#!/usr/bin/env bash
# 单个备份目录的自校验：DB 归档摘要 + 原件归档摘要 + 清单结构 + 发布完整性。
#
# 用法：
#   bash ops/deploy/verify_backup.sh --backup <备份目录> [--quiet] [--legacy]
#   退出码 0 = 这份备份可以当"好备份"用；1 = 不完整/被改；2 = 用法错误。
#
# 为什么需要它（而不是每个调用点各写一段 if）：
#   四个地方要问同一个问题"这份备份算不算完整"——
#     backup_db.sh 发布前后、restore_drill.sh 演练前、monitor.sh 巡检时、人手工核。
#   口径必须完全一样，否则会出现"监控说正常、演练说损坏"的互相矛盾。
#
# 判定口径：
#   - 目录存在，且存在 manifest.json 与 .complete（.complete 是发布完成的标记，
#     backup_db.sh 的原子改名保证它出现时目录内容已经齐全）；
#   - database.dump / files.tar.gz 都存在、非空；
#   - 两个归档的 sha256 与清单里的值一致，也和随附的 *.sha256 文件一致；
#   - manifest.json 能被严格解析（有 python3 时）。
#   - 数据库和文件**两个都**必须通过：只有一侧成功的备份在这里一律判失败，
#     正是"仅库成功/仅文件成功不能报整套成功"的落点。
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ops/deploy/backup_lib.sh
. "$HERE/backup_lib.sh"

BACKUP=""
QUIET=0
LEGACY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) BACKUP="${2:-}"; shift 2 ;;
    --quiet) QUIET=1; shift ;;
    --legacy) LEGACY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "ERROR verify_backup.sh 未知参数：$1" >&2; exit 2 ;;
  esac
done
[[ -n "$BACKUP" ]] || { echo "ERROR verify_backup.sh 必须给 --backup <备份目录>" >&2; exit 2; }
[[ -d "$BACKUP" ]] || { echo "ERROR 备份目录不存在：$BACKUP" >&2; exit 1; }

PROBLEMS=0
note() { printf '%s\n' "$*"; }
prob() { PROBLEMS=$((PROBLEMS + 1)); printf 'PROBLEM %s\n' "$*"; }

MANIFEST="$BACKUP/manifest.json"
if [[ ! -f "$MANIFEST" ]]; then
  if [[ "$LEGACY" -eq 1 ]]; then
    # 交接前的旧备份只有 crm-<戳>.dump，没有清单也没有文件归档。
    # 明确报"不构成一致恢复清单"，但允许只看数据库归档是否可读，
    # 避免巡检对历史文件天天误报。
    dump="$(find "$BACKUP" -maxdepth 1 -name '*.dump' -type f | head -1)"
    [[ -n "$dump" ]] || { echo "RESULT FAIL 旧式备份目录里没有 .dump 文件：$BACKUP"; exit 1; }
    ${PG_BIN}pg_restore --list "$dump" >/dev/null 2>&1 || { echo "RESULT FAIL 旧式备份归档不可读：$dump"; exit 1; }
    echo "LEGACY 无 manifest.json（第七批之前的备份）：仅确认数据库归档可读，不具备文件/模板一致性清单"
    echo "RESULT OK-LEGACY $BACKUP"
    exit 0
  fi
  echo "RESULT FAIL 缺少 manifest.json：这不是一次完整备份（可能只有数据库或只有文件，或发布被中断）"
  exit 1
fi
if [[ ! -f "$BACKUP/.complete" ]]; then
  prob "缺少 .complete 发布标记：备份未走完发布流程，不能当成好备份"
fi

# 清单必须是合法 JSON：清单自己坏了，后面所有"按清单核对"都没有意义。
JSON_OK=0
if command -v python3 >/dev/null 2>&1; then
  if python3 -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$MANIFEST" 2>/dev/null; then
    JSON_OK=1
  else
    prob "manifest.json 不是合法 JSON（清单已损坏，无法据此核对任何内容）"
  fi
else
  note "NOTICE 本机无 python3：manifest.json 未做严格 JSON 解析，只做字段存在性检查"
fi

sha_of() { file_sha256 "$1" 2>/dev/null; }

check_pair() { # check_pair <归档文件> <清单字段名> <说明>
  local file="$1" key="$2" label="$3" manifest_sha side_sha actual
  if [[ ! -s "$file" ]]; then
    prob "${label} 归档缺失或为空：$(basename "$file")"
    return 1
  fi
  actual="$(sha_of "$file")" || { prob "${label} 归档无法计算摘要：$(basename "$file")"; return 1; }
  manifest_sha="$(tr -d '\n' < "$MANIFEST" | grep -o "\"${key}\"[^}]*\"sha256\": *\"[0-9a-f]\{64\}\"" | head -1 | grep -o '[0-9a-f]\{64\}' | head -1)"
  if [[ -z "$manifest_sha" ]]; then
    prob "${label} 在清单里没有 sha256 字段：清单不完整，无法证明归档未被改动"
  elif [[ "$manifest_sha" != "$actual" ]]; then
    prob "${label} 归档 sha256 与清单不一致：清单 $manifest_sha，实际 $actual（归档被截断/替换/磁盘损坏）"
  fi
  local side=""
  case "$file" in
    *.tar.gz) side="${file%.tar.gz}.sha256" ;;
    *.*)      side="${file%.*}.sha256" ;;
  esac
  if [[ -n "$side" && -f "$side" ]]; then
    side_sha="$(awk '{print $1}' "$side")"
    [[ "$side_sha" == "$actual" ]] || prob "${label} 随附 $(basename "$side") 与实际摘要不一致（$side_sha vs $actual）"
  else
    note "NOTICE ${label} 缺少随附 $(basename "$side")（不影响清单核对）"
  fi
  [[ "$QUIET" -eq 0 ]] && note "OK ${label} sha256=$actual size=$(wc -c < "$file" | tr -d ' ')"
  return 0
}

check_pair "$BACKUP/database.dump" "database_dump" "数据库归档"
check_pair "$BACKUP/files.tar.gz" "file_tree" "原件归档"

# 数据库归档还要证明确实是 pg_dump -Fc 且带表数据：只看摘要无法区分
# "我们的归档" 和 "摘要一致但内容其实不是数据库的字节"。
if [[ -s "$BACKUP/database.dump" ]]; then
  if ${PG_BIN}pg_restore --list "$BACKUP/database.dump" >/dev/null 2>&1; then
    [[ "$QUIET" -eq 0 ]] && note "OK 数据库归档可被 pg_restore 读取"
  else
    prob "database.dump 无法被 pg_restore 读取：归档损坏，恢复演练必然失败"
  fi
fi

# 清单里的关键字段必须齐：缺了就等于"没有可核对的口径"。
for field in database_revision file_root registered_objects; do
  case "$field" in
    registered_objects)
      grep -q '"registered_objects"' "$MANIFEST" || prob "清单缺少 file_tree.registered_objects（无法知道应有多少原件）" ;;
    *)
      v="$(manifest_field "$MANIFEST" "$field")"
      [[ -n "$v" ]] || prob "清单缺少字段 $field（恢复时无法核对）" ;;
  esac
done
rev="$(manifest_field "$MANIFEST" "database_revision")"
fr="$(manifest_field "$MANIFEST" "file_root")"
[[ "$QUIET" -eq 0 ]] && note "清单：alembic_version=$rev file_root=$fr"

# 客户端版本提醒。两条经验规则（本项目在真机上踩过第二条）：
#   - **客户端比服务端新**：pg_restore 会执行服务端不认的 SET，恢复直接失败
#     （实测客户端 18 恢复进服务端 15：SET transaction_timeout 报错）；
#   - 客户端比服务端旧：pg_restore 会拒绝该归档（"archive was created by a newer version"）。
# 所以演练机上应当把 PGCLIENT_BIN 钉到与服务端同版本。
# 这里只提示不判失败：确认服务端版本要连库，而校验脚本设计成离线可跑。
dump_ver="$(manifest_field "$MANIFEST" "pg_dump_version")"
if [[ -n "$dump_ver" && "$dump_ver" != "pg_dump: command not found" ]]; then
  dump_major="$(printf '%s' "$dump_ver" | grep -o '[0-9]\+\(\.[0-9]\+\)*' | head -1 | cut -d. -f1)"
  if [[ -n "$dump_major" ]] && command -v ${PG_BIN}pg_restore >/dev/null 2>&1; then
    local_major="$(${PG_BIN}pg_restore --version | grep -o '[0-9]\+\(\.[0-9]\+\)*' | head -1 | cut -d. -f1)"
    if [[ -n "$local_major" && "$local_major" != "$dump_major" ]]; then
      note "NOTICE 备份用的是 pg_dump 大版本 $dump_major，本机 pg_restore 大版本 $local_major：演练前请把 PGCLIENT_BIN 指向与服务端同版本的客户端，否则恢复可能因版本差异失败"
    fi
  fi
fi

# 登记清单本身也在备份目录里：演练要拿它逐条比对，丢了就没法证明原件齐不齐。
if [[ -f "$BACKUP/REGISTRY/files_registry.tsv" ]]; then
  declared="$(manifest_field "$MANIFEST" "registry_sha256")"
  if [[ -n "$declared" ]]; then
    actual_r="$(sha_of "$BACKUP/REGISTRY/files_registry.tsv")"
    [[ "$declared" == "$actual_r" ]] || prob "REGISTRY/files_registry.tsv 与清单登记的摘要不一致（登记清单被改，核对结论不可信）"
  fi
  [[ "$QUIET" -eq 0 ]] && note "OK 文件登记清单 $(wc -l < "$BACKUP/REGISTRY/files_registry.tsv" | tr -d ' ') 条"
else
  prob "缺少 REGISTRY/files_registry.tsv：无法逐条核对原件"
fi

if [[ "$PROBLEMS" -gt 0 ]]; then
  echo "RESULT FAIL $BACKUP 有 $PROBLEMS 处问题（见上）：这份备份不能当成可用备份"
  exit 1
fi
echo "RESULT OK $BACKUP 数据库+文件+清单齐全且校验一致"
exit 0
