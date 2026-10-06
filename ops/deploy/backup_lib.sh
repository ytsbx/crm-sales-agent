#!/usr/bin/env bash
# CRM 备份/恢复公用函数库。被同目录的 backup_db.sh / verify_file_tree.sh /
# restore_drill.sh 用 `. "$(dirname "$0")/backup_lib.sh"` 引入，不单独执行。
#
# 为什么要有这个文件（而不是每个脚本各写一遍）：
#   8.16 要求"一致恢复清单"——数据库归档、文件归档、清单三者必须用同一套
#   口径计算校验值和解析配置。三份复制粘贴的实现迟早会漂移：某一处改了
#   掩码规则或 sha256 工具回退顺序，就会出现"清单说成功、恢复对不上"的假象。
#
# 这里只放纯函数，不放副作用（不改环境、不创建文件、不退出进程）：
#   1. 配置解析：DATABASE_URL / FILE_ROOT 只从环境来的值解析，**不猜默认库**；
#   2. 凭据掩码：任何要打印的连接信息先过 mask_url；
#   3. 校验值：sha256 工具三选一（sha256sum → shasum -a 256 → openssl）；
#   4. 对象清单读取：从 files 表导出 object_key<TAB>checksum 的只读查询。
# 语法检查：bash -n ops/deploy/backup_lib.sh

# ---------------------------------------------------------------- 校验值工具 --
# 为什么先探测再选：备份脚本要能在最小化安装的 Debian（无 shasum）和 macOS
# （无 sha256sum）上都跑。三个都没有就明确报错退出，绝不"跳过校验继续备份"——
# 没有校验值的备份等于没有备份。
SHA256_TOOL=""

detect_sha256_tool() {
  if [[ -n "$SHA256_TOOL" ]]; then
    printf '%s\n' "$SHA256_TOOL"
    return 0
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    SHA256_TOOL="sha256sum"
  elif command -v shasum >/dev/null 2>&1; then
    SHA256_TOOL="shasum -a 256"
  elif command -v openssl >/dev/null 2>&1; then
    SHA256_TOOL="openssl_sha256"
  else
    echo "ERROR 找不到 sha256 工具（sha256sum / shasum / openssl 都没有）：无法生成校验值，拒绝继续" >&2
    return 1
  fi
  printf '%s\n' "$SHA256_TOOL"
}

# file_sha256 <文件路径> → stdout 输出 64 位十六进制摘要
file_sha256() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "ERROR file_sha256 目标不是普通文件：$path（原件缺失/损坏，备份不完整）" >&2
    return 1
  fi
  case "$(detect_sha256_tool)" in
    sha256sum) sha256sum "$path" | awk '{print $1}' ;;
    "shasum -a 256") shasum -a 256 "$path" | awk '{print $1}' ;;
    openssl_sha256) openssl dgst -sha256 "$path" | awk '{print $NF}' ;;
    *) echo "ERROR file_sha256 内部错误：未知校验工具" >&2; return 1 ;;
  esac
}

# write_sha256_file <文件> <摘要输出文件>：写成 `摘要  文件名` 的 sha256sum 格式，
# 便于以后直接用 `sha256sum -c` 复验（也给人工核对留标准格式）。
write_sha256_file() {
  local path="$1" out="$2" digest
  digest="$(file_sha256 "$path")" || return 1
  printf '%s  %s\n' "$digest" "$(basename "$path")" > "$out"
}

# --------------------------------------------------------------- 凭据掩码 ----
# 输出里绝不能出现口令：日志会进 journald/企业微信告警群/工单附件。
# 覆盖 postgresql+asyncpg://user:pass@host/db、postgres://、以及带查询串的形态。
mask_url() {
  sed -e 's#\(://[^:/@][^:/@]*\):[^@]*@#\1:***@#g' \
      -e 's#\([?&][Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd]=\)[^&]*#\1***#g' \
      -e 's#\([?&][Pp][Ww][Dd]=\)[^&]*#\1***#g'
}

# --------------------------------------------------------------- 配置解析 ----
# 解析连接串：**只认环境里给的值**。
#
# 为什么不再保留默认值：原脚本 `DB_URL="${DATABASE_URL:-postgresql://crm:...@127.0.0.1:5432/crm_sales_agent}"`
# 在 DATABASE_URL 缺失时会静默去连 127.0.0.1 的 crm_sales_agent，并且把口令硬编码在
# 仓库里。备份任务最危险的失败模式是"看起来成功了，其实备的是另一台机器/另一个库"，
# 或者"每周把错误环境的库覆盖成正式归档"。这里改为：没配置就报出该配哪个变量并退出。
#
# 同时剥掉 SQLAlchemy 方言后缀（pg_dump/psql 不认 postgresql+asyncpg）。
resolve_db_url() {
  local raw="${DATABASE_URL:-}"
  if [[ -z "$raw" ]]; then
    echo "ERROR 未配置 DATABASE_URL：备份连接绝不回退到默认库。请在 /opt/crm/backend/.env 或 systemd EnvironmentFile 里显式给出 postgresql://用户:口令@主机:端口/库名" >&2
    return 1
  fi
  raw="${raw//+asyncpg/}"
  raw="${raw//+psycopg/}"
  if [[ "$raw" != postgresql://* && "$raw" != postgres://* ]]; then
    echo "ERROR DATABASE_URL 不是 PostgreSQL 连接串（掩码后：$(printf '%s' "$raw" | mask_url)）：pg_dump 无法使用，请检查 .env" >&2
    return 1
  fi
  printf '%s\n' "$raw"
}

# 解析文件存储根目录（对应 backend/app/modules/file/storage.py 的 settings.file_root）。
# 口径与后端保持一致：绝对路径直接用；相对路径挂到 FILE_ROOT_BASE_DIR 下
# （部署里就是 /opt/crm/backend，即后端的 WorkingDirectory），不挂到当前目录——
# systemd 的 WorkingDirectory 与 cron 的 CWD 不同，否则会备份到"另一棵空目录"。
resolve_file_root() {
  local raw="${FILE_ROOT:-}"
  if [[ -z "$raw" ]]; then
    echo "ERROR 未配置 FILE_ROOT：附件/合同/生成原件不在数据库里，不知道目录就无法建立一致恢复清单。请在 .env 里显式给出（生产一般为 /opt/crm/backend/data/files）" >&2
    return 1
  fi
  if [[ "$raw" = /* ]]; then
    printf '%s\n' "$raw"
    return 0
  fi
  local base="${FILE_ROOT_BASE_DIR:-/opt/crm/backend}"
  printf '%s\n' "${base%/}/$raw"
}

# 解析业务单据目录根（合同签署件/生成件）。这些文件由 docs.*_dir 配置，默认相对后端目录。
# 为什么单独列：它们不在 FILE_ROOT 下，只备 FILE_ROOT 会漏掉历史合同扫描件/生成件，
# 恢复后"单据台账有、原件没有"。不存在就跳过（开发机常见），但会把结论写进清单。
resolve_biz_docs_root() {
  local raw="${BIZ_DOCS_ROOT:-}"
  if [[ -n "$raw" ]]; then
    if [[ "$raw" = /* ]]; then printf '%s\n' "$raw"; else printf '%s\n' "${FILE_ROOT_BASE_DIR:-/opt/crm/backend}/${raw}"; fi
    return 0
  fi
  local base="${FILE_ROOT_BASE_DIR:-/opt/crm/backend}"
  printf '%s\n' "${base%/}/docs"
}

# ------------------------------------------------- PostgreSQL 客户端路径 ----
# PGCLIENT_BIN（环境变量，可选）：把所有 PostgreSQL 客户端命令钉到一个目录。
# 为什么需要：客户端大版本比服务端新时，pg_restore 会发服务端不认的 SET
# （实测客户端 18 + 服务端 15：`SET transaction_timeout = 0` 直接报错，
#  归档备得出来、却恢复不回去）。Debian/Ubuntu 的客户端在
# /usr/lib/postgresql/<版本>/bin，服务器上按服务端版本钉住即可。
# 空值 = 走 PATH（默认行为，保持与原来一致）。
PG_BIN="${PGCLIENT_BIN:+${PGCLIENT_BIN%/}/}"

# --------------------------------------------------------- 数据库只读导出 ----
# 下面这些查询是**只读**的：备份流程从不对业务库做任何写操作。
# 统一 `-X -q -A -t`：不读 ~/.psqlrc（避免个人配置改变输出格式污染清单）。
psql_ro() {
  local db_url="$1" sql="$2"
  ${PG_BIN}psql -X -q -A -t -v ON_ERROR_STOP=1 -d "$db_url" -c "$sql"
}

# 迁移版本：数据库里 alembic_version 的真实值。恢复后要拿它和清单对，
# 这是"恢复出来的库到底是不是备份当时那一版结构"的唯一证据。
fetch_alembic_revision() {
  local db_url="$1"
  psql_ro "$db_url" "SELECT version_num FROM alembic_version ORDER BY version_num" \
    | paste -sd, -
}

# 文件登记清单（object_key<TAB>checksum）：files 表里登记的本地对象。
# storage_provider<>'local' 说明原件在对象存储里，不在本地目录，明确排除而不是
# 当成"缺文件"报错（否则一旦将来接 MinIO，备份每天都会假告警）。
# checksum 为空的旧行仍然导出，走 verify_file_tree.sh 的 UNRECORDED 分支处理。
dump_file_registry() {
  local db_url="$1" out="$2"
  psql_ro "$db_url" "SELECT object_key || E'\t' || COALESCE(checksum, '') FROM files WHERE storage_provider = 'local' AND object_key IS NOT NULL ORDER BY object_key" > "$out"
}

# 历史对外单据（合同/报价/打样/下单）抽样：演练要能"取出历史原件且 hash 一致"。
# 逐类取最近 N 条，附带台账里的登记校验值，恢复后逐条核对。
#
# 注意两处不能想当然：
#   1. contract_documents 的单据号列历史上叫 contract_no、后来叫 doc_no（见
#      backend/app/modules/contract/model.py）。这里**查一次数据字典再拼 SQL**，
#      而不是赌某个名字——赌错的表现是备份直接失败（本项目实测踩过）。
#   2. 表/列不存在（裁剪部署或旧版本）不能让整套备份失败：查询失败时输出空清单，
#      演练那边会把"没有可抽查引用"明确显示为 SKIP，而不是假装抽查过。
dump_doc_references() {
  local db_url="$1" out="$2" limit="${3:-20}" contract_no_col=""
  contract_no_col="$(psql_ro "$db_url" "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'contract_documents' AND column_name IN ('doc_no','contract_no') ORDER BY CASE column_name WHEN 'doc_no' THEN 0 ELSE 1 END LIMIT 1" 2>/dev/null || true)"
  [[ -n "$contract_no_col" ]] || contract_no_col="doc_no"
  if ! psql_ro "$db_url" "
WITH refs AS (
  SELECT 'contract' AS kind, cd.id AS doc_id, cd.${contract_no_col} AS doc_no, f.object_key, f.checksum
    FROM contract_documents cd JOIN files f ON f.id = cd.generated_file_id
   WHERE cd.generated_file_id IS NOT NULL
  UNION ALL
  SELECT 'bizdoc:' || bd.doc_type, bd.id, bd.doc_no, f.object_key, f.checksum
    FROM biz_docs bd
    JOIN business_files bf ON bf.business_type = 'biz_doc' AND bf.business_id = bd.id
    JOIN files f ON f.id = bf.file_id
), ranked AS (
  SELECT *, row_number() OVER (PARTITION BY kind ORDER BY doc_id DESC) AS rn FROM refs
)
SELECT kind || E'\t' || doc_id || E'\t' || COALESCE(doc_no, '') || E'\t' || object_key || E'\t' || COALESCE(checksum, '')
  FROM ranked WHERE rn <= ${limit} ORDER BY kind, doc_id" > "$out" 2>/dev/null; then
    echo "WARN 历史单据引用抽样失败（contract_documents/biz_docs 结构与本版本不一致）：清单里该段为空，恢复演练将跳过历史单据抽查" >&2
    : > "$out"
  fi
}

# 关键台账行数：恢复后逐项对，能发现"归档能读但恢复少表/少行"。
# 表不存在（旧版本/裁剪部署）时输出 -1，而不是让整个备份失败——
# 备份的可用性优先于清单的完整性，缺项在清单里是明确可见的。
count_table() {
  local db_url="$1" table="$2" n
  n="$(psql_ro "$db_url" "SELECT count(*) FROM ${table}" 2>/dev/null)" || { printf '%s\n' "-1"; return 0; }
  printf '%s\n' "${n:-0}"
}

# 模板清单（doc_type → 各版本 + 启用状态 + body 摘要）：模板正文是文件的输入源，
# 恢复后要能证明"当时这套模板还在"。条目数很少，整份进清单不算大。
dump_template_registry() {
  local db_url="$1" out="$2"
  psql_ro "$db_url" "SELECT doc_type || E'\t' || version || E'\t' || enabled || E'\t' || COALESCE(md5(body), '') FROM biz_doc_templates ORDER BY doc_type, version" > "$out" 2>/dev/null || : > "$out"
}

# 字体/渲染依赖验证信息。
#
# 为什么不能只写"字体已确认"：报价单/打样单 PDF 用 reportlab 的 CID 字体
# STSong-Light（见 backend/app/modules/bizdoc/pdf.py），它依赖的解释器、reportlab
# 版本、CID 资源目录缺一个就会在**生成文件时**才炸，而备份/恢复看起来一切正常。
# 这里把"哪套解释器、reportlab 版本、CID 字体文件、模板入口文件"的 sha256 都记下来，
# 恢复环境用 verify_render_bundle 复验，把"能不能再渲染出同样的文件"变成可核对项。
dump_render_env() {
  local out="$1" backend_dir="$2" py_bin="$3"
  local line ver
  if [[ -z "$py_bin" && -x "${backend_dir}/.venv/bin/python" ]]; then
    py_bin="${backend_dir}/.venv/bin/python"
  fi
  if [[ -z "$py_bin" ]] && command -v python3 >/dev/null 2>&1; then
    py_bin="$(command -v python3)"
  fi
  : > "$out"
  if [[ -n "$py_bin" && -x "$py_bin" ]]; then
    ver="$(PYTHONPATH="${backend_dir}:${PYTHONPATH:-}" "$py_bin" - <<'PY' 2>/dev/null || true
import sys
try:
    import reportlab
    print("reportlab=%s" % reportlab.Version)
except Exception:
    print("reportlab=absent")
if sys.version_info >= (3, 8):
    from importlib.metadata import version as _v
    for mod in ("pydantic", "alembic", "sqlalchemy"):
        try:
            print("%s=%s" % (mod, _v(mod)))
        except Exception:
            print("%s=absent" % mod)
PY
)"
    [[ -n "$ver" ]] && printf 'python\t%s\t%s\n' "$py_bin" "$(printf '%s' "$ver" | paste -sd, -)" >> "$out"
    # CID 字体来源：reportlab 内置的 STSong-Light 资源；找不到就记 missing，
    # 恢复后复验时能立刻看出"这台机器渲染不出中文 PDF"。
    local cid
    cid="$("$py_bin" - <<'PY' 2>/dev/null || true
import os, reportlab
base = os.path.dirname(reportlab.__file__)
hit = os.path.join(base, "fonts", "STSong-Light")
print(hit if os.path.exists(hit) else "")
PY
)"
    if [[ -n "$cid" ]]; then
      # 目录内每个字体文件一行摘要，按路径排序后拼成逗号串；这样"字体资源被换掉"
      # 与"字体资源缺失"是两种可区分的结论。
      printf 'cid_font_dir\t%s\t%s\n' "$cid" "$(find "$cid" -type f 2>/dev/null | sort | xargs -r sha256sum 2>/dev/null | awk '{print $1}' | paste -sd, -)" >> "$out"
    else
      printf 'cid_font_dir\tmissing\tSTSong-Light 资源未找到：恢复后无法渲染中文 PDF，需重装 reportlab 对应版本\n' >> "$out"
    fi
  else
    printf 'python\tabsent\t未找到可用解释器；字体/渲染依赖未验证\n' >> "$out"
  fi
  # 渲染入口文件本身的摘要：升级渲染器而不更新清单的情况要能被发现。
  local f
  for f in "${backend_dir}/app/modules/bizdoc/pdf.py" "${backend_dir}/app/modules/bizdoc/xlsx.py" "${backend_dir}/app/modules/bizdoc/service.py"; do
    if [[ -f "$f" ]]; then
      printf 'renderer\t%s\t%s\n' "$f" "$(file_sha256 "$f")" >> "$out"
    fi
  done
}

# --------------------------------------------------------------- 清单结构 ----
# JSON 清单由各脚本各自拼装（字段随能力演进），这里只放共用的转义与字段片段，
# 保证三个脚本写出来的 JSON 形状一致、可被同一段解析逻辑读。
json_escape() {
  # 反斜杠、双引号转义；换行统一成 \n。清单会被 grep/python 读，绝不能出现裸换行。
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n' ' '
}

# 读清单里的一个顶层字符串字段：manifest_field <清单> <字段名>
# 用 grep+sed 而不是依赖 python/jq：监控脚本要在最小化系统上也能跑，
# 少一个依赖就少一处"监控自己先挂了"。
manifest_field() {
  local manifest="$1" key="$2"
  grep -m1 "\"${key}\"" "$manifest" 2>/dev/null | sed -e 's/.*: *"//' -e 's/".*//'
}
