#!/usr/bin/env bash
# CRM 每日一致备份：数据库归档 + 原件文件树 + 模板/字体/迁移版本 → 一份可校验的恢复清单。
#
# 用法（手动）：
#   bash ops/deploy/backup_db.sh
# 生产（Linux）：由 crm-backup.timer 每天 02:30 自动跑（见同目录 timer）。
#
# 必填环境（systemd 从 /opt/crm/backend/.env 的 EnvironmentFile 继承）：
#   DATABASE_URL   postgresql://用户:口令@主机:端口/库名（或 postgresql+asyncpg://…，本脚本会剥掉方言后缀）
#   FILE_ROOT      附件/合同/生成原件所在目录，与后端 settings.file_root 同一口径
# 可选：
#   BACKUP_DIR     备份根目录，默认 /var/backups/crm
#   KEEP_DAYS      保留天数，默认 14
#   FILE_ROOT_BASE_DIR  相对 FILE_ROOT 的锚点，默认 /opt/crm/backend（等于后端 WorkingDirectory）
#   BIZ_DOCS_ROOT  业务单据原件目录（默认 ${FILE_ROOT_BASE_DIR}/docs），不存在则跳过并在清单里记 skipped
#   WORK_DIR       临时工作目录，默认 $TMPDIR
#   PGCLIENT_BIN   PostgreSQL 客户端命令所在目录（默认走 PATH）。为什么要这个开关：
#                  Debian/Ubuntu 把 pg_dump/pg_restore 装在 /usr/lib/postgresql/<版本>/bin，
#                  而客户端大版本比服务端**新**时（例如客户端 18 对服务端 15），
#                  pg_restore 会执行服务端不认的 SET（实测 transaction_timeout 直接报错），
#                  归档能备、却恢复不了。钉住目录即可与服务端同版本。
#
# 与本项（8.16）要求对应的设计取舍：
#   1. **不再有默认连接串**。原来 DATABASE_URL 缺失时会去连 127.0.0.1:5432/crm_sales_agent，
#      还把口令硬编码进仓库。备份最坏的失败不是"没备成"，而是"备了别的库/别的机器还报成功"。
#      现在缺配置直接失败，并说明该配哪个变量。
#   2. **数据库和文件必须一起成功**。只备数据库会在恢复后出现"files 表有登记、磁盘上没有原件"，
#      对外单据和合同扫描件全部打不开。任一侧失败 → 整个备份失败（退出码非 0），
#      监控看到的是一次失败，而不是"成功了一半"。
#   3. **先备到临时目录、校验通过后再发布**。备份目录最终形态是
#        crm-<时间戳>/{database.dump, files.tar.gz, manifest.json, *.sha256, REGISTRY/, .complete}
#      全部内容先写到 .crm-<时间戳>.partial/，最后一步才 `mv` 成正式名字。
#      监控只把"有 manifest.json + .complete"的目录算成功备份，所以半个备份永远不会被当成好备份。
#   4. **删除旧备份前先确认新备份成功**：本脚本只有在发布成功后才会进入清理阶段，
#      且清理阶段自己再复验一次新备份（归档可读 + 校验值一致）才动手删。
#   5. **输出不泄露凭据**：所有打印的连接信息先过 mask_url。
#   6. 打包后会**把归档重新解到临时目录再逐文件校验 hash**，证明"归档里真的是原件"，
#      而不只是"归档文件能打开"（后者是原 pg_restore --list 的水平）。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# 备份目录里是数据库全量 + 全部原件（可能含客户合同扫描件），默认只有属主可读写。
umask 077
# shellcheck source=ops/deploy/backup_lib.sh
. "$HERE/backup_lib.sh"

# 三个配套脚本必须都在：缺任何一个都意味着"校验口径不完整"。
# 明确报出缺哪个文件，比让它跑到一半用空函数糊过去要好得多。
for helper in backup_lib.sh verify_file_tree.sh verify_backup.sh manifest_extract.py; do
  [[ -r "$HERE/$helper" ]] || {
    echo "ERROR 缺少配套脚本 $HERE/$helper：备份的校验逻辑不完整，拒绝执行（请确认 ops/deploy/ 完整同步）" >&2
    exit 1
  }
done

BACKUP_DIR="${BACKUP_DIR:-/var/backups/crm}"
KEEP_DAYS="${KEEP_DAYS:-14}"
BACKEND_DIR="${FILE_ROOT_BASE_DIR:-/opt/crm/backend}"
WORK_DIR="${WORK_DIR:-${TMPDIR:-/tmp}}"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
fail() { printf '[%s] FAILED %s\n' "$(date '+%F %T')" "$*" >&2; exit 1; }

# 只保留 DATABASE_URL 里的凭据：显式清掉 libpq 的单变量口令/用户/主机，
# 避免"环境里残留的 PGPASSWORD/PGUSER 把连接悄悄引到另一个库"，也避免值出现在 psql 报错里。
unset PGPASSWORD PGUSER PGHOST PGPORT PGDATABASE 2>/dev/null || true

for tool in pg_dump pg_restore psql tar awk sed sort find mktemp; do
  command -v "$tool" >/dev/null 2>&1 || fail "缺少命令 $tool：备份环境不完整（Debian: apt-get install postgresql-client；或用 PGCLIENT_BIN 指定客户端目录）"
done
# PGCLIENT_BIN 非空时把所有 PostgreSQL 客户端调用改指该目录（见文件头的版本兼容说明）
PG_BIN="${PGCLIENT_BIN:+${PGCLIENT_BIN%/}/}"
[[ -z "$PGCLIENT_BIN" || -x "${PG_BIN}pg_dump" ]] || fail "PGCLIENT_BIN=$PGCLIENT_BIN 下没有 pg_dump：路径写错了"
[[ -n "$(detect_sha256_tool)" ]] || fail "缺少 sha256 工具：无法生成校验值，拒绝继续"

DB_URL="$(resolve_db_url)" || exit 1
FILE_ROOT_ABS="$(resolve_file_root)" || exit 1
BIZ_DOCS_ROOT_ABS="$(resolve_biz_docs_root)"
DB_MASKED="$(printf '%s' "$DB_URL" | mask_url)"

[[ -d "$FILE_ROOT_ABS" ]] || fail "FILE_ROOT 目录不存在：$FILE_ROOT_ABS（原件不在数据库里，目录错等于没备份文件）"

mkdir -p "$BACKUP_DIR" || fail "无法创建备份目录 $BACKUP_DIR（检查权限与磁盘）"
[[ -w "$BACKUP_DIR" ]] || fail "备份目录不可写：$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
FINAL="$BACKUP_DIR/crm-$STAMP"
STAGE="$BACKUP_DIR/.crm-$STAMP.partial"
rm -rf "$STAGE"
mkdir -p "$STAGE" || fail "无法创建临时备份目录 $STAGE"
cleanup_partial() {
  # 失败时保留 .partial 目录（便于定位"哪一步没成"），但绝不留成正式备份名。
  if [[ -d "$STAGE" ]]; then
    log "本次未发布：失败现场保留在 $STAGE（不会被监控当成有效备份，可人工检查后删除）"
  fi
}
trap cleanup_partial EXIT

log "备份开始 目标库=$DB_MASKED 文件根=$FILE_ROOT_ABS 输出=$FINAL"

# ------------------------------------------------------------ 1. 数据库归档 --
DUMPSH="$STAGE/database.sha256"
DUMP="$STAGE/database.dump"
log "步骤 1/7 pg_dump -Fc → $(basename "$DUMP")"
${PG_BIN}pg_dump "$DB_URL" -Fc -f "$DUMP" || fail "pg_dump 失败（目标库=$DB_MASKED）：连接/权限/磁盘空间需人工确认"
[[ -s "$DUMP" ]] || fail "pg_dump 产出了空归档：目标库可能不是预期的库"

# -Fc 归档可读性：这是"最小验证"（原来只有这一步），现在只是后续步骤的前提。
${PG_BIN}pg_restore --list "$DUMP" > "$STAGE/database.toc.txt" 2>/dev/null \
  || fail "pg_restore --list 无法读取归档 $DUMP：归档已损坏"
toc_tables=$(grep -c 'TABLE DATA' "$STAGE/database.toc.txt" || true)
[[ "${toc_tables:-0}" -gt 0 ]] || fail "归档里没有任何表数据（TABLE DATA 条目为 0）：备份内容不可信"
log "  归档可读，TABLE DATA 条目 $toc_tables"

# 迁移版本与台账行数：恢复后要能证明"恢复到的是备份当时那一版结构/规模"。
REV="$(fetch_alembic_revision "$DB_URL")"
[[ -n "$REV" ]] || fail "读不到 alembic_version（数据库=$DB_MASKED）：这个库不是本应用迁移过的库，或连接到了错误的库"
log "  迁移版本 alembic_version=$REV"

declare -a COUNT_TABLES=(users customers contacts products skus quotes quote_versions
  orders sales_orders payments receivables files business_files biz_docs biz_doc_templates
  contract_documents sample_requests inquiries)
{
  for t in "${COUNT_TABLES[@]}"; do
    printf '%s\t%s\n' "$t" "$(count_table "$DB_URL" "$t")"
  done
} > "$STAGE/table_counts.tsv"
log "  关键表行数已记录（$(wc -l < "$STAGE/table_counts.tsv" | tr -d ' ') 张，缺表记 -1）"

dump_template_registry "$DB_URL" "$STAGE/template_registry.tsv"
dump_doc_references "$DB_URL" "$STAGE/doc_references.tsv" 20
dump_render_env "$STAGE/render_env.tsv" "$BACKEND_DIR" "${PYTHON_BIN:-}"
log "  模板 $(wc -l < "$STAGE/template_registry.tsv" | tr -d ' ') 条、抽检单据引用 $(wc -l < "$STAGE/doc_references.tsv" | tr -d ' ') 条、渲染环境 $(wc -l < "$STAGE/render_env.tsv" | tr -d ' ') 条"

# ------------------------------------------- 2. 文件登记清单 + 备份前核对 ----
REGISTRY_DIR="$STAGE/REGISTRY"
mkdir -p "$REGISTRY_DIR"
dump_file_registry "$DB_URL" "$REGISTRY_DIR/files_registry.tsv"
registry_count=$(grep -c . "$REGISTRY_DIR/files_registry.tsv" || true)
log "步骤 2/7 files 表登记对象 $registry_count 条（只读查询）"

log "步骤 3/7 备份前核对原件：登记清单 ↔ $FILE_ROOT_ABS"
if ! bash "$HERE/verify_file_tree.sh" --tree "$FILE_ROOT_ABS" --registry "$REGISTRY_DIR/files_registry.tsv" --quiet \
     > "$STAGE/verify_before.log" 2>&1; then
  # 注意不要写成 `verify | tee 日志`：管道的退出码是 tee 的，校验失败会被吞掉，
  # 于是把已知损坏的文件静默收进归档。这里先落盘、再判退出码、失败时回显。
  cat "$STAGE/verify_before.log" >&2
  fail "备份前文件核对不通过：磁盘原件与数据库登记已经不一致（详见 $STAGE/verify_before.log）。先修原件再备份，不要把已知损坏收进归档"
fi
log "  $(grep -m1 '^SUMMARY' "$STAGE/verify_before.log" || echo 'SUMMARY 缺失')"

# ------------------------------------------------------------ 3. 文件归档 ---
ARCHIVE="$STAGE/files.tar.gz"
log "步骤 4/7 归档原件树 → $(basename "$ARCHIVE")"
# 归档路径用「根目录名 + 内容」的形状：恢复时 tar -xzf -C <目标> 得到 <根目录名>/…，
# 层次与后端 file_root() 解析出来的目录一致，不需要人肉搬位置。
FILE_ROOT_NAME="$(basename "${FILE_ROOT_ABS%/}")"
tar -czf "$ARCHIVE" -C "$(dirname "${FILE_ROOT_ABS%/}")" "$FILE_ROOT_NAME" \
  || fail "tar 打包原件目录失败（$FILE_ROOT_ABS）：磁盘空间或文件权限需人工确认"
[[ -s "$ARCHIVE" ]] || fail "原件归档为空：$FILE_ROOT_ABS 下没有文件（目录配错了吗）"

BIZ_DOCS_STATUS="skipped"
if [[ -d "$BIZ_DOCS_ROOT_ABS" ]]; then
  BIZ_NAME="$(basename "${BIZ_DOCS_ROOT_ABS%/}")"
  tar -czf "$STAGE/biz_docs.tar.gz" -C "$(dirname "${BIZ_DOCS_ROOT_ABS%/}")" "$BIZ_NAME" \
    && BIZ_DOCS_STATUS="archived:${BIZ_DOCS_ROOT_ABS}" \
    || fail "tar 打包业务单据目录失败（$BIZ_DOCS_ROOT_ABS）"
else
  log "  业务单据目录不存在（$BIZ_DOCS_ROOT_ABS）：清单中记为 skipped（不静默当成已备）"
fi

# ------------------------------- 4. 把归档解回临时目录逐条复核（关键一步） ----
# pg_restore --list 只能说明"归档文件能打开"。要证明"归档里真的是那些原件"，
# 必须反向解包再按登记校验值逐个比对。这一步就是"文件损坏能检出"的落点。
RELIST="$STAGE/verify_after.log"
RELIST_DIR="$WORK_DIR/crm-backup-unpack.$$"
rm -rf "$RELIST_DIR"; mkdir -p "$RELIST_DIR"
trap 'rm -rf "$RELIST_DIR"; cleanup_partial' EXIT
log "步骤 5/7 解包复核：归档 → $RELIST_DIR 再逐文件校验"
tar -xzf "$ARCHIVE" -C "$RELIST_DIR" || fail "解包复核失败：归档解不开（$ARCHIVE）"
if ! bash "$HERE/verify_file_tree.sh" --tree "$RELIST_DIR/$FILE_ROOT_NAME" \
     --registry "$REGISTRY_DIR/files_registry.tsv" --quiet > "$RELIST" 2>&1; then
  cat "$RELIST" >&2
  fail "解包复核不通过：归档内容与数据库登记不一致（详见 $RELIST）。归档未发布，也不删除任何旧备份"
fi
rm -rf "$RELIST_DIR"
log "  $(grep -m1 '^SUMMARY' "$RELIST" || echo 'SUMMARY 缺失')"

# ------------------------------------------------------- 5. 校验值与清单 ----
log "步骤 6/7 生成校验值与恢复清单"
PUBLISHED_AT="$(date -Is)"   # 发布时刻：只进日志/文件名，不改写已定稿的清单（清单在发布前已定稿）
write_sha256_file "$DUMP" "$DUMPSH" || fail "无法计算数据库归档摘要"
ARCHSH="$STAGE/files.sha256"
write_sha256_file "$ARCHIVE" "$ARCHSH" || fail "无法计算文件归档摘要"
DUMP_SHA="$(awk '{print $1}' "$DUMPSH")"
ARCH_SHA="$(awk '{print $1}' "$ARCHSH")"
DUMP_SIZE="$(wc -c < "$DUMP" | tr -d ' ')"
ARCH_SIZE="$(wc -c < "$ARCHIVE" | tr -d ' ')"
PG_VERSION="$(${PG_BIN}pg_dump --version | head -1)"
HOSTNAME_S="$(hostname 2>/dev/null || echo unknown)"
GENERATED_AT="$(date -Is)"

# table_counts.tsv / template_registry.tsv / doc_references.tsv 以 TSV 片段进入清单：
# 清单必须自解释，恢复演练时只靠这一个文件就能核对，不必回查备份当天的日志。
json_tsv_array() { # json_tsv_array <文件> <列数> → 形如 [["a","1"],...]
  awk -F'\t' -v n="$2" '
    BEGIN { printf "[" }
    NF > 0 {
      printf "%s[", (NR > 1 ? "," : "")
      for (i = 1; i <= n; i++) {
        v = $i; gsub(/\\/, "\\\\", v); gsub(/"/, "\\\"", v)
        printf "%s\"%s\"", (i > 1 ? "," : ""), v
      }
      printf "]"
    }
    END { printf "]" }
  ' "$1"
}

MANIFEST="$STAGE/manifest.json"
{
  printf '{\n'
  printf '  "manifest_version": 2,\n'
  printf '  "generated_at": "%s",\n' "$(json_escape "$GENERATED_AT")"
  printf '  "backup_name": "%s",\n' "$(json_escape "crm-$STAMP")"
  printf '  "host": "%s",\n' "$(json_escape "$HOSTNAME_S")"
  printf '  "database_url_masked": "%s",\n' "$(json_escape "$DB_MASKED")"
  printf '  "database_revision": "%s",\n' "$(json_escape "$REV")"
  printf '  "pg_dump_version": "%s",\n' "$(json_escape "$PG_VERSION")"
  printf '  "database_dump": {"file": "database.dump", "sha256": "%s", "bytes": %s, "table_data_entries": %s},\n' \
    "$DUMP_SHA" "$DUMP_SIZE" "$toc_tables"
  printf '  "file_root": "%s",\n' "$(json_escape "$FILE_ROOT_ABS")"
  printf '  "file_root_name": "%s",\n' "$(json_escape "$FILE_ROOT_NAME")"
  printf '  "file_tree": {"file": "files.tar.gz", "sha256": "%s", "bytes": %s, "registered_objects": %s},\n' \
    "$ARCH_SHA" "$ARCH_SIZE" "$registry_count"
  if [[ "$BIZ_DOCS_STATUS" == archived:* ]]; then
    printf '  "biz_docs": {"status": "archived", "file": "biz_docs.tar.gz", "root": "%s"},\n' \
      "$(json_escape "${BIZ_DOCS_STATUS#archived:}")"
  else
    printf '  "biz_docs": {"status": "skipped", "root": "%s", "reason": "目录不存在：本环境未用 docs 目录存放单据原件"},\n' \
      "$(json_escape "$BIZ_DOCS_ROOT_ABS")"
  fi
  printf '  "table_counts": %s,\n' "$(json_tsv_array "$STAGE/table_counts.tsv" 2)"
  printf '  "templates": %s,\n' "$(json_tsv_array "$STAGE/template_registry.tsv" 4)"
  printf '  "doc_references": %s,\n' "$(json_tsv_array "$STAGE/doc_references.tsv" 5)"
  printf '  "render_env": %s,\n' "$(json_tsv_array "$STAGE/render_env.tsv" 3)"
  printf '  "files": {\n'
  printf '    "registry": "REGISTRY/files_registry.tsv",\n'
  printf '    "registry_sha256": "%s",\n' "$(file_sha256 "$REGISTRY_DIR/files_registry.tsv")"
  printf '    "verify_before_log": "verify_before.log",\n'
  printf '    "verify_after_log": "verify_after.log"\n'
  printf '  },\n'
  printf '  "keep_days": %s,\n' "$KEEP_DAYS"
  printf '  "generator": "ops/deploy/backup_db.sh"\n'
  printf '}\n'
} > "$MANIFEST"

# 清单在**发布之前**就要自证合格：用与监控、演练完全同一个 verify_backup.sh。
# 这样"清单+双归档"的判定口径只有一处实现，不存在"备份时算通过、监控时算失败"。
# 此刻还缺 .complete（下一步才写），所以先写标记再校验；校验不通过就不发布。
printf 'manifest.json 与双归档即将通过 verify_backup.sh 校验；本标记表示发布流程已走完。\n' > "$STAGE/.complete"
if ! bash "$HERE/verify_backup.sh" --backup "$STAGE" --quiet > "$STAGE/self_check.log" 2>&1; then
  cat "$STAGE/self_check.log" >&2
  fail "发布前自检不通过：备份目录未发布（详见 $STAGE/self_check.log），也不删除任何旧备份"
fi
log "  $(grep -m1 '^RESULT' "$STAGE/self_check.log" || echo '自检结果缺失')"

# ----------------------------------------------- 6. 发布（原子改名）+ 标记 --
# 发布用 mv（同一文件系统内是原子操作）：监控要么看到完整的 partial，
# 要么看到完整的正式目录，不会读到"目录在、内容不全"的中间态。
log "步骤 7/7 发布备份目录（临时 → 正式）"
[[ ! -e "$FINAL" ]] || fail "目标备份目录已存在：$FINAL（时间戳冲突，请人工检查）"
mv "$STAGE" "$FINAL" || fail "发布备份目录失败：$STAGE → $FINAL"
trap - EXIT
rm -rf "$RELIST_DIR" 2>/dev/null || true
rmdir "$REGISTRY_DIR" 2>/dev/null || true

log "已发布 $FINAL（数据库 ${DUMP_SIZE}B、原件归档 ${ARCH_SIZE}B，发布时间 ${PUBLISHED_AT}）"
log "清单：$FINAL/manifest.json（database_sha256=$DUMP_SHA files_sha256=$ARCH_SHA）"

# --------------------------------------------- 7. 仅在新备份确认后清理旧备份 --
# 清理阶段自证：新备份此刻还能通过 verify_backup.sh 才允许删旧的。
# 这样"删旧备份前确认新备份成功"不是靠流程纪律，而是脚本里的一道门。
if [[ "$KEEP_DAYS" =~ ^[0-9]+$ ]] && [[ "$KEEP_DAYS" -gt 0 ]]; then
  VERIFY_NEW_LOG="$WORK_DIR/crm-backup-verifynew.$$.log"
  if bash "$HERE/verify_backup.sh" --backup "$FINAL" --quiet > "$VERIFY_NEW_LOG" 2>&1; then
    log "清理：新备份已复验通过，开始删除 $KEEP_DAYS 天前的旧备份"
    # 只删"完整备份目录"和失败留下的 .partial 目录；绝不按文件名前缀乱删。
    # 同一时间戳既不能被删除阶段选中，也不能误删正在发布的 partial。
    deleted=0
    while IFS= read -r old; do
      [[ -z "$old" ]] && continue
      [[ "$(basename "$old")" == "$(basename "$FINAL")" ]] && continue
      rm -rf "$old" && deleted=$((deleted + 1))
    done < <(find "$BACKUP_DIR" -maxdepth 1 -type d \
               \( -name 'crm-*' -o -name '.crm-*.partial' \) -mtime +"$KEEP_DAYS" 2>/dev/null | sort)
    log "清理：删除 $deleted 个过期备份目录（保留 $KEEP_DAYS 天）"
  else
    # 注意这里不是 fail：备份已经发布成功，只是清理被跳过。
    # 但必须把原因说出来——否则磁盘会被旧备份悄悄填满。
    cat "$VERIFY_NEW_LOG" >&2
    log "清理跳过：新备份复验未通过（见上），本轮不删除任何旧备份（宁可占磁盘，不留唯一好备份被删的风险）"
  fi
  rm -f "$VERIFY_NEW_LOG" 2>/dev/null || true
else
  log "清理跳过：KEEP_DAYS=$KEEP_DAYS 不是正整数（0 表示不自动删除）"
fi

log "备份完成：$FINAL"
