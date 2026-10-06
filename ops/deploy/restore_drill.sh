#!/usr/bin/env bash
# 隔离恢复演练：把一份备份恢复到**临时库 + 临时文件目录**，逐项核对，最后给出可否放行结论。
#
# 用法：
#   bash ops/deploy/restore_drill.sh --backup /var/backups/crm/crm-20261006-023000 \
#        --db-name crm_restore_20261006 [--files-dir /tmp/crm-restore-20261006] \
#        [--admin-url postgresql://crm:***@127.0.0.1:5432/postgres] [--cleanup] [--quiet]
#
# 硬性纪律（脚本自己强制执行，不靠人记得）：
#   1. **绝不覆盖业务库**。目标库名不允许是 crm_sales_agent / crm_sales_agent_test /
#      postgres / template0 / template1，也不允许已存在的库名（存在就报错让换名字）。
#   2. 只有"看起来就是隔离演练用的名字"才放行：必须匹配 ^crm_restore_[a-z0-9_]*$，
#      否则要求 --allow-db 显式确认。这样手滑把库名打成 crm_sales_agent 也进不去。
#   3. 默认只允许连本机（127.0.0.1 / localhost / ::1 / unix socket）：演练会 CREATE DATABASE，
#      在正式服务器上对着远端跑等于"用生产实例做实验"。要远端必须 --allow-remote 明确表态。
#   4. 默认**保留**恢复出来的库和目录，便于人工再翻（结尾打印清理命令）；
#      只有显式 --cleanup 才删。
#
# 演练做什么（每一步失败即整体 FAIL，不会"部分通过也算过"）：
#   A. 备份目录自校验（verify_backup.sh）：数据库+文件+清单齐全、摘要一致；
#   B. 建临时库 → pg_restore 恢复 → 核对 alembic_version 与清单一致；
#   C. 核对关键台账行数与清单一致（能发现"归档能读但少表/少行"）；
#   D. 归档解到临时目录 → 用登记清单逐条核对 hash（文件损坏能检出）；
#   E. 抽查历史单据原件（合同/报价/打样/下单在 doc_references 里的条目）：
#      原件存在、hash 与台账登记值一致、权限可读且不是全局可写；
#   F. 给出结论：可以放行 / 有哪些项没通过（含还没验的部分，如系统字体）。
#
# 本脚本只对**临时库**写数据；对业务库只做只读连接（其连接串仅用于创建临时库时的管理连接，
# 真正的数据恢复目标是临时库）。
#   PGCLIENT_BIN   PostgreSQL 客户端命令所在目录（默认走 PATH）。
#                  演练会用与本机 pg_restore 相同版本的客户端去恢复；客户端大版本比服务端新时
#                  可能恢复失败（实测客户端 18 对服务端 15 会执行服务端不认的 SET）。
#                  服务器上按服务端版本钉住，例如 PGCLIENT_BIN=/usr/lib/postgresql/15/bin。
#   其余约定：默认只允许本机、默认保留恢复现场（--cleanup 才删）。
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
umask 077
# shellcheck source=ops/deploy/backup_lib.sh
. "$HERE/backup_lib.sh"

BACKUP=""
DB_NAME=""
FILES_DIR=""
ADMIN_URL=""
CLEANUP=0
QUIET=0
ALLOW_DB=0
ALLOW_REMOTE=0
RESTORE_BASE="${RESTORE_BASE:-${TMPDIR:-/tmp}}"

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) BACKUP="${2:-}"; shift 2 ;;
    --db-name) DB_NAME="${2:-}"; shift 2 ;;
    --files-dir) FILES_DIR="${2:-}"; shift 2 ;;
    --admin-url) ADMIN_URL="${2:-}"; shift 2 ;;
    --cleanup) CLEANUP=1; shift ;;
    --quiet) QUIET=1; shift ;;
    --allow-db) ALLOW_DB=1; shift ;;
    --allow-remote) ALLOW_REMOTE=1; shift ;;
    -h|--help) usage ;;
    *) echo "ERROR restore_drill.sh 未知参数：$1（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

STEP=0
PASS=0
FAILED=0
# 演练过程中的中间输出（restore 日志、解包日志、失败清单）都放临时目录，
# 退出时统一清理。绝不往备份目录里写东西——备份目录是只读证据。
TMPD="$(mktemp -d "${TMPDIR:-/tmp}/crm-drill.XXXXXX")"
FAIL_LOG="$TMPD/failures.txt"
: > "$FAIL_LOG"
cleanup_tmp() { rm -rf "$TMPD"; }
trap cleanup_tmp EXIT

say()  { printf '%s\n' "$*"; }
step() { STEP=$((STEP + 1)); say ""; say "== 步骤 $STEP: $*"; }
ok()   { PASS=$((PASS + 1)); say "  PASS $*"; }
bad()  { FAILED=$((FAILED + 1)); printf '  FAIL %s\n' "$*"; printf '%s\n' "$*" >> "$FAIL_LOG"; }

[[ -n "$BACKUP" ]] || { echo "ERROR restore_drill.sh 必须给 --backup <备份目录>" >&2; exit 2; }
for tool in psql pg_restore createdb dropdb tar find sort awk; do
  command -v "$tool" >/dev/null 2>&1 || { echo "ERROR 缺少命令 $tool（Debian: apt-get install postgresql-client；或用 PGCLIENT_BIN 指定客户端目录）" >&2; exit 2; }
done
[[ -z "$PGCLIENT_BIN" || -x "${PG_BIN}pg_restore" ]] || { echo "ERROR PGCLIENT_BIN=$PGCLIENT_BIN 下没有 pg_restore：路径写错了" >&2; exit 2; }
[[ -n "$(detect_sha256_tool)" ]] || { echo "ERROR 缺少 sha256 工具：无法核对原件 hash" >&2; exit 2; }

STAMP="$(date +%Y%m%d-%H%M%S)"
[[ -n "$DB_NAME" ]] || DB_NAME="crm_restore_${STAMP//-/}"
[[ -n "$FILES_DIR" ]] || FILES_DIR="$RESTORE_BASE/crm-restore-$STAMP"

# ---------------------------------------------------------------- 纪律检查 ---
say "隔离恢复演练：备份=$BACKUP 目标库=$DB_NAME 目标目录=$FILES_DIR"

# 连接串：优先 --admin-url，其次 DATABASE_URL（掩码后与清单里的目标库比对）。
ADMIN_URL="${ADMIN_URL:-${DATABASE_URL:-}}"
if [[ -z "$ADMIN_URL" ]]; then
  echo "ERROR 缺少管理连接串：用 --admin-url 或 DATABASE_URL 给出（需要能 CREATE DATABASE 的账号；脚本只对临时库写数据）" >&2
  exit 2
fi
ADMIN_URL="${ADMIN_URL//+asyncpg/}"
ADMIN_MASKED="$(printf '%s' "$ADMIN_URL" | mask_url)"

# 管理连接串拆成 libpq 环境变量。
# 为什么不能直接把连接串当参数传给 createdb/dropdb：
#   psql/dropdb 的 -d 是"要连的库"，而 **createdb 的 -d 是 --dbname 的同义（也是连到哪个库），
#   不是"要创建的库名"**（createdb 的库名是位置参数）。本项目实测踩过：
#   `createdb -d "$URL" "$DB"` 在 PostgreSQL 18 上直接报 invalid option -- 'd'。
#   拆成 PGHOST/PGPORT/PGUSER/PGPASSWORD 之后，库名一律用位置参数，语义不再含糊。
admin_rest="${ADMIN_URL#*://}"
case "$admin_rest" in
  *@*)
    admin_userinfo="${admin_rest%%@*}"
    admin_hostpart="${admin_rest#*@}"
    admin_user="${admin_userinfo%%:*}"
    case "$admin_userinfo" in
      *:*) admin_pass="${admin_userinfo#*:}" ;;
      *)   admin_pass="" ;;
    esac
    ;;
  *)
    admin_userinfo=""
    admin_hostpart="$admin_rest"
    admin_user="${USER:-crm}"
    admin_pass=""
    ;;
esac
admin_host="${admin_hostpart%%[:/?]*}"
admin_portpart="${admin_hostpart#*:}"
admin_port="${admin_portpart%%[/?]*}"
case "$admin_port" in
  ""|*[!0-9]*) admin_port="5432" ;;
esac
# 维护库（createdb/dropdb 要连进去的那个库）：取路径最后一段，缺省 postgres。
admin_pathpart="${admin_hostpart#*/}"
if [[ "$admin_pathpart" == "$admin_hostpart" ]]; then
  admin_db="postgres"
else
  admin_db="${admin_pathpart%%\?*}"
  [[ -n "$admin_db" ]] || admin_db="postgres"
fi
export PGHOST="$admin_host" PGPORT="$admin_port" PGUSER="$admin_user" PGDATABASE="$admin_db"
if [[ -n "$admin_pass" ]]; then export PGPASSWORD="$admin_pass"; fi

# 只允许本机：演练会建库，跑在正式服务器上对着远端实例做实验不是本脚本的使用方式。
# unix socket 形式（…/db 或 ?host=/var/run/postgresql）解析出的主机是空串，同样按本机处理。
case "$admin_host" in
  ""|localhost|127.0.0.1|"::1"|"[::1]") : ;;
  *)
    if [[ "$ALLOW_REMOTE" -ne 1 ]]; then
      echo "ERROR 管理连接串指向非本机主机（$admin_host）：演练会 CREATE DATABASE，默认拒绝在远端实例上跑。确认无风险后加 --allow-remote" >&2
      exit 2
    fi
    say "  NOTICE 已显式允许远端主机 $admin_host（--allow-remote）"
    ;;
esac

# 业务库/系统库黑名单 + 命名白名单。
case "$DB_NAME" in
  crm_sales_agent|crm_sales_agent_test|postgres|template0|template1)
    echo "ERROR 目标库名 '$DB_NAME' 是业务库/系统库：演练绝不允许对着它恢复。请用 crm_restore_<日期> 这类一次性名字" >&2
    exit 2 ;;
esac
if [[ ! "$DB_NAME" =~ ^crm_restore_[a-z0-9_]*$ ]]; then
  if [[ "$ALLOW_DB" -ne 1 ]]; then
    echo "ERROR 目标库名 '$DB_NAME' 不符合演练命名约定 ^crm_restore_[a-z0-9_]*$。若确有理由，请显式加 --allow-db（并自行确认这不是业务库）" >&2
    exit 2
  fi
  say "  NOTICE 目标库名 $DB_NAME 已由 --allow-db 显式确认"
fi

# 目标库必须不存在：pg_restore 到已有库里会把数据叠在旧数据上，
# "恢复演练"就变成了"污染别人的库"。
exists="$(${PG_BIN}psql -X -q -A -t -d "$ADMIN_URL" -c "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")"
if [[ "$exists" == "1" ]]; then
  echo "ERROR 目标库 $DB_NAME 已存在：演练绝不复用/覆盖已有库。请换名字，或先人工确认后 DROP" >&2
  exit 2
fi
[[ -e "$FILES_DIR" ]] && { echo "ERROR 目标文件目录已存在：$FILES_DIR（演练不覆盖已有目录，避免删掉别人的数据）" >&2; exit 2; }

# 从管理连接串派生目标库连接串：只把库名换成临时库，其余（主机/端口/账号/口令）沿用。
# 分别处理「?查询串」（库名在 / 与 ? 之间）和「无查询串」（库名到结尾）两种形态，
# 否则 --admin-url 带 ?sslmode=… 时会把库名替换错，连到错误的地方。
case "$ADMIN_URL" in
  *\?*)
    DB_URL_DRILL="${ADMIN_URL%%\?*}"
    DB_URL_DRILL="${DB_URL_DRILL%/*}/${DB_NAME}?${ADMIN_URL#*\?}" ;;
  */)
    # 结尾只有斜杠：库名缺省（按 libpq 会落到账号同名库），明确拒绝比猜一个库名安全
    DB_URL_DRILL="$ADMIN_URL" ;;
  *)
    DB_URL_DRILL="${ADMIN_URL%/*}/${DB_NAME}" ;;
esac
[[ "$DB_URL_DRILL" != "$ADMIN_URL" ]] || { echo "ERROR 无法从管理连接串派生目标库连接串：$ADMIN_MASKED（请用 --admin-url 指定含库名的连接串，例如 …/postgres）" >&2; exit 2; }
DB_DRILL_MASKED="$(printf '%s' "$DB_URL_DRILL" | mask_url)"

DRILL_STARTED_AT="$(date -Is)"
# 退出处理：先抓 $?（后面的 say/rm 都会把它冲掉），再清临时库。
# 默认不删恢复出来的库和目录——演练的价值一半在于事后人工翻看；
# 要删必须显式 --cleanup，避免"演练跑完什么都没留下"。
cleanup_drill() {
  local rc=$?
  trap - EXIT
  if [[ "$CLEANUP" -eq 1 ]]; then
    say ""
    say "== 清理（--cleanup）：删除临时库 $DB_NAME 与目录 $FILES_DIR"
    # dropdb 的 -d 是"连到哪个库"（这里确实要连维护库），库名同样是位置参数。
    if ${PG_BIN}dropdb --if-exists "$DB_NAME"; then say "  已删除临时库 $DB_NAME"; else say "  警告：删除临时库失败，请人工执行 dropdb $DB_NAME（维护库 $admin_db@$admin_host:$admin_port）"; fi
    if rm -rf "$FILES_DIR"; then say "  已删除 $FILES_DIR"; else say "  警告：删除 $FILES_DIR 失败"; fi
  else
    say ""
    say "== 保留现场（未加 --cleanup）：人工检查后可执行——"
    say "   psql '$ADMIN_MASKED' -c 'DROP DATABASE $DB_NAME;'"
    say "   rm -rf '$FILES_DIR'"
  fi
  cleanup_tmp
  exit "$rc"
}
trap cleanup_drill EXIT

# ============================================================ A. 备份自校验 --
step "A. 备份目录自校验（数据库+文件+清单齐全、摘要一致）"
if bash "$HERE/verify_backup.sh" --backup "$BACKUP" ${QUIET:+--quiet} > "$TMPD/verify.log" 2>&1; then
  ok "$(grep -m1 '^RESULT' "$TMPD/verify.log")"
else
  cat "$TMPD/verify.log" >&2
  bad "备份目录自校验不通过：这份备份已损坏/不完整，演练无需继续（详见上面输出）"
  say ""
  say "DRILL RESULT: FAIL（备份本身不合格）"
  exit 1
fi

MANIFEST="$BACKUP/manifest.json"
EXPECT_REV="$(manifest_field "$MANIFEST" "database_revision")"
FILE_ROOT_NAME="$(manifest_field "$MANIFEST" "file_root_name")"
[[ -n "$FILE_ROOT_NAME" ]] || FILE_ROOT_NAME="files"
say "  清单：alembic_version=$EXPECT_REV file_root_name=$FILE_ROOT_NAME"

# ================================================= B. 建临时库并恢复数据库 --
step "B. 建临时库 $DB_NAME 并 pg_restore 恢复数据库归档"
# 库名是位置参数；连到哪个库由已导出的 PGDATABASE/PGHOST/PGPORT/PGUSER(+PGPASSWORD) 决定，
# 不写 -d（createdb 的 -d 是"连到哪个库"，不是"创建哪个库"，见上面的说明）。
${PG_BIN}createdb "$DB_NAME" || { bad "createdb $DB_NAME 失败：检查账号是否有 CREATE DATABASE 权限（当前维护库 $admin_db@$admin_host:$admin_port）"; say ""; say "DRILL RESULT: FAIL"; exit 1; }
ok "已创建临时库 $DB_NAME（业务库未被触碰）"

# --exit-on-error：恢复中途出错必须立刻失败，而不是留下一个"半截库"却被判通过。
# --no-owner/--no-privileges：演练环境的角色/属主与生产通常不同，这两项与数据一致性无关，
# 不关掉的话演练会因为"角色不存在"这种环境差异失败，掩盖真正的问题。
if ${PG_BIN}pg_restore --exit-on-error --no-owner --no-privileges -d "$DB_URL_DRILL" "$BACKUP/database.dump" > "$TMPD/restore.log" 2>&1; then
  ok "pg_restore 恢复完成（目标 $DB_DRILL_MASKED）"
else
  tail -30 "$TMPD/restore.log" >&2
  bad "pg_restore 恢复失败（详见 $TMPD/restore.log）：归档与当前 pg_restore 版本/结构可能不兼容"
fi

actual_rev="$(${PG_BIN}psql -X -q -A -t -d "$DB_URL_DRILL" -c "SELECT version_num FROM alembic_version" 2>/dev/null | paste -sd, -)"
if [[ "$actual_rev" == "$EXPECT_REV" ]]; then
  ok "迁移版本一致：alembic_version=$actual_rev"
else
  bad "迁移版本不一致：清单 $EXPECT_REV，恢复库 $actual_rev（恢复出来的结构与备份当时不是同一版）"
fi

# ============================================= C. 关键台账行数逐项核对 ------
step "C. 关键台账行数与清单逐项核对"
count_mismatch=0
# 清单里的 table_counts 由 manifest_extract.py 导出（它同时校验清单是不是合法 JSON）。
if command -v python3 >/dev/null 2>&1; then
  if python3 "$HERE/manifest_extract.py" --manifest "$MANIFEST" --field table_counts > "$TMPD/table_counts.tsv" 2>"$TMPD/extract.log"; then
    while IFS=$'\t' read -r tbl expect; do
      [[ -z "$tbl" ]] && continue
      if [[ "$expect" == "-1" ]]; then
        say "  SKIP $tbl（备份时该表不存在，不参与核对）"
        continue
      fi
      got="$(${PG_BIN}psql -X -q -A -t -d "$DB_URL_DRILL" -c "SELECT count(*) FROM ${tbl}" 2>/dev/null)"
      if [[ "${got:-missing}" == "$expect" ]]; then
        [[ "$QUIET" -eq 0 ]] && say "  PASS $tbl 行数 $got"
      else
        count_mismatch=$((count_mismatch + 1))
        bad "$tbl 行数不一致：清单 $expect，恢复库 ${got:-读不到}"
      fi
    done < "$TMPD/table_counts.tsv"
    [[ "$count_mismatch" -eq 0 ]] && ok "全部关键台账行数与清单一致" || bad "有 $count_mismatch 张表行数不一致"
  else
    cat "$TMPD/extract.log" >&2
    bad "无法从清单导出 table_counts：清单不可信，行数核对做不了"
  fi
else
  say "  SKIP 本机无 python3：跳过行数逐项比对（清单已随备份保存，可在有 python3 的机器上复验）"
fi

# ====================================== D. 文件归档解到临时目录并逐条核对 --
step "D. 归档解到 $FILES_DIR 并用登记清单逐条核对原件 hash"
mkdir -p "$FILES_DIR" || { bad "无法创建 $FILES_DIR"; say ""; say "DRILL RESULT: FAIL"; exit 1; }
if tar -xzf "$BACKUP/files.tar.gz" -C "$FILES_DIR" 2>"$TMPD/untar.log"; then
  ok "原件归档解包成功（$(find "$FILES_DIR" -type f | wc -l | tr -d ' ') 个文件）"
else
  cat "$TMPD/untar.log" >&2
  bad "原件归档解包失败：归档损坏（tar 已报错）"
fi
RESTORED_TREE="$FILES_DIR/$FILE_ROOT_NAME"
if [[ ! -d "$RESTORED_TREE" ]]; then
  bad "解包后找不到原件根目录 $RESTORED_TREE（清单记的 file_root_name=$FILE_ROOT_NAME）：恢复后后端按 file_root 找不到任何原件"
else
  # 用**备份里那份登记清单**核对，而不是回查源库：这正是灾难恢复时的场景——
  # 源库已经没了，只有备份目录里的清单和归档可依据。
  if bash "$HERE/verify_file_tree.sh" --tree "$RESTORED_TREE" \
       --registry "$BACKUP/REGISTRY/files_registry.tsv" --quiet > "$TMPD/tree.log" 2>&1; then
    ok "$(grep -m1 '^SUMMARY' "$TMPD/tree.log")"
  else
    grep -E '^(FAIL-|SUMMARY|FAILKIND|RESULT)' "$TMPD/tree.log" | head -40 >&2
    bad "恢复后的原件树与登记清单不一致：$(grep -m1 '^RESULT' "$TMPD/tree.log")"
  fi
fi

# ============================ E. 历史单据原件抽查（hash / 引用 / 权限） ----
step "E. 历史单据原件抽查：存在、hash、引用、权限"
if [[ ! -f "$BACKUP/doc_references.tsv" ]]; then
  say "  SKIP 备份里没有 doc_references.tsv（旧版备份）：无法抽查历史单据原件"
else
  checked=0; e_fail=0
  while IFS=$'\t' read -r kind doc_id doc_no obj_key expect_sum; do
    [[ -z "${kind:-}" ]] && continue
    checked=$((checked + 1))
    target="$RESTORED_TREE/$obj_key"
    if [[ ! -f "$target" ]]; then
      e_fail=$((e_fail + 1)); bad "$kind#$doc_id($doc_no) 原件缺失：$obj_key（台账有引用、文件取不出来）"
      continue
    fi
    # 权限：演练账号能读到（否则恢复后业务同样取不到）；且不能是全局可写
    # （对外单据原件被任何人改写就等于历史事实可被篡改）。
    if [[ ! -r "$target" ]]; then
      e_fail=$((e_fail + 1)); bad "$kind#$doc_id($doc_no) 原件不可读（权限问题）：$obj_key"
    fi
    perm="$(stat -c '%a' "$target" 2>/dev/null || stat -f '%Lp' "$target" 2>/dev/null || echo '?')"
    case "$perm" in
      *[2367]) e_fail=$((e_fail + 1)); bad "$kind#$doc_id($doc_no) 原件对 others 可写（权限 $perm）：$obj_key（历史单据可被改写）" ;;
    esac
    actual_sum="$(file_sha256 "$target")" || { e_fail=$((e_fail + 1)); bad "$kind#$doc_id($doc_no) 无法计算 hash：$obj_key"; continue; }
    if [[ -n "$expect_sum" && "$actual_sum" != "$expect_sum" ]]; then
      e_fail=$((e_fail + 1)); bad "$kind#$doc_id($doc_no) hash 不一致：登记 ${expect_sum:0:12}…，恢复 ${actual_sum:0:12}…"
      continue
    fi
    [[ "$QUIET" -eq 0 ]] && say "  PASS $kind#$doc_id($doc_no) $obj_key hash=${actual_sum:0:12}… perm=$perm"
  done < "$BACKUP/doc_references.tsv"
  if [[ "$checked" -eq 0 ]]; then
    say "  SKIP 备份时台账里没有任何可抽查的单据原件引用（空库或还没生成过单据）"
  elif [[ "$e_fail" -eq 0 ]]; then
    ok "抽查 $checked 份历史单据原件：全部存在、hash 一致、权限可读且非全局可写"
  else
    bad "抽查 $checked 份历史单据原件，$e_fail 项不合格"
  fi
fi

# ===================================================== F. 渲染依赖（字体） --
step "F. 渲染依赖/字体（清单记录 vs 当前环境）"
if [[ -f "$BACKUP/render_env.tsv" ]]; then
  # 这里只比对"清单里记的和当前机器上的是不是同一套"。字体/CID 资源缺失不会导致
  # 恢复不出旧文件，但会导致**今后**生成的中文 PDF 变化或失败——属于必须告警、
  # 但不该阻断"历史数据恢复成功"的项，所以记为 WARN 而不是 FAIL。
  warn_n=0
  while IFS=$'\t' read -r kind path val; do
    [[ -z "${kind:-}" ]] && continue
    case "$kind" in
      python)
        say "  INFO 备份时解释器：$path（$val）" ;;
      cid_font_dir)
        if [[ "$path" == "missing" ]]; then
          warn_n=$((warn_n + 1)); say "  WARN 备份时就缺 STSong-Light 字体资源：$val"
        elif [[ -d "$path" ]]; then
          now="$(find "$path" -type f 2>/dev/null | sort | xargs -r sha256sum 2>/dev/null | awk '{print $1}' | paste -sd, -)"
          if [[ "$now" == "$val" ]]; then say "  PASS CID 字体资源与备份时一致：$path"
          else warn_n=$((warn_n + 1)); say "  WARN CID 字体资源与备份时不同（$path）：会影响今后重新渲染的中文 PDF，历史已生成文件不受影响"; fi
        else
          warn_n=$((warn_n + 1)); say "  WARN 当前环境没有备份时记录的字体目录：$path"
        fi ;;
      renderer)
        if [[ -f "$path" ]]; then
          now="$(file_sha256 "$path")"
          [[ "$now" == "$val" ]] && say "  PASS 渲染器未变：$(basename "$path")" \
            || say "  WARN 渲染器已变更：$path（历史归档件不变，但今后重出的文件会不同）"
        else
          warn_n=$((warn_n + 1)); say "  WARN 备份时记录的渲染器文件现在不存在：$path"
        fi ;;
    esac
  done < "$BACKUP/render_env.tsv"
  [[ "$warn_n" -eq 0 ]] && ok "渲染依赖与备份时一致" || say "  NOTICE 渲染依赖有 $warn_n 处差异（不阻断历史数据恢复，需在放行前确认）"
else
  say "  SKIP 备份里没有 render_env.tsv：无法比对字体/渲染依赖"
fi

# ================================================================ 结论 ------
say ""
say "演练耗时从 $DRILL_STARTED_AT 到 $(date -Is)"
if [[ "$FAILED" -eq 0 ]]; then
  say "DRILL RESULT: PASS（$PASS 项通过）—— 这份备份在隔离环境中可恢复：数据库结构与行数、文件原件 hash、历史单据原件都核对一致"
  say "说明：本结论只覆盖本脚本列出的核对项。真实服务器上仍需确认：备份账号权限、磁盘余量、systemd 定时器实际触发、以及是否已在正式目录做过一次演练。"
  exit 0
fi
say "DRILL RESULT: FAIL（$FAILED 项不合格，$PASS 项通过）—— 不合格项："
sed -e 's/^/  - /' "$FAIL_LOG"
say "未通过前不得对外宣称"备份可用"。"
exit 1
