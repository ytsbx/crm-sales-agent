#!/usr/bin/env bash
# 文件原件树校验：把「数据库 files 表登记的对象清单」与「磁盘上的原件目录」逐条对齐。
#
# 用法：
#   bash ops/deploy/verify_file_tree.sh --tree <原件目录> --registry <登记清单文件> [--quiet] [--emit-missing]
#
#   --registry 是两列 TSV：`object_key<TAB>checksum`（由 backup_lib.sh 的
#              dump_file_registry 从 files 表只读导出，checksum 可为空）。
#   --quiet    只打印汇总行（备份流程里每步都刷几百行没人看）。
#   --emit-missing 额外把缺失对象写成 `object_key<TAB>checksum`（前有 MISSING-LIST 段头），
#              供调用方决定是补文件还是阻断发布。
#
# 退出码：0 = 全部登记对象存在且校验一致；1 = 有 FAIL（缺失/被改/不可读/符号链接/空文件）；
#         2 = 用法或环境错误（目录不存在、找不到校验工具）。
#
# 为什么单独成脚本（而不是塞进 backup_db.sh）：
#   同一个校验要在三个时刻跑，语义必须完全一致——
#     备份前（磁盘原件是否本来就已经烂了）、
#     打包后（tar 归档是否真的收全了原件，而不是把坏文件静默收进去）、
#     隔离恢复后（演练里取出来的历史原件是否和当初一致）。
#   校验逻辑一旦复制成三份，就可能出现"备份时判通过、恢复时判不一致但两边依据不同"，
#   那时就无法证明任何一件事。
#
# 判定口径（每一条都要能定位问题）：
#   OK          登记对象在树上存在、非符号链接、非空、sha256 与登记值一致
#   OK-NOHASH   存在且非空，但登记行本身没有 checksum（历史数据）→ 只证明"文件在"
#   FAIL-MISSING    登记了但树上没有 → 恢复后正是"有登记、原件不存在"
#   FAIL-HASH       存在但 sha256 与登记值不同 → 文件被改/被截断/被同名覆盖
#   FAIL-EMPTY      存在但 0 字节 → 上传中途失败留下的半文件
#   FAIL-SYMLINK    是指向别处的符号链接 → 归档后恢复出来可能取不到内容
#   FAIL-UNREADABLE 读不了（权限/损坏）
#   FAIL-ESCAPE     对象键逃出目录根（.. 或绝对路径）→ 拒绝按该路径读取
#   ORPHAN      树上多出来的文件（数据库没登记）→ 只提示不判失败，正常上传过程中会有
set -uo pipefail  # 故意不 set -e：校验要跑完全部条目再汇总，中途退出等于漏报

TREE=""
REGISTRY=""
QUIET=0
EMIT_MISSING=0

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tree) TREE="${2:-}"; shift 2 ;;
    --registry) REGISTRY="${2:-}"; shift 2 ;;
    --quiet) QUIET=1; shift ;;
    --emit-missing) EMIT_MISSING=1; shift ;;
    -h|--help) usage ;;
    *) echo "ERROR verify_file_tree.sh 未知参数：$1（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

if [[ -z "$TREE" || -z "$REGISTRY" ]]; then
  echo "ERROR verify_file_tree.sh 必须同时给 --tree 和 --registry（缺一个都无法对齐登记与原件）" >&2
  exit 2
fi
if [[ ! -d "$TREE" ]]; then
  echo "ERROR verify_file_tree.sh 原件目录不存在：$TREE（配置错目录等于没备份文件）" >&2
  exit 2
fi
if [[ ! -f "$REGISTRY" ]]; then
  echo "ERROR verify_file_tree.sh 登记清单不存在：$REGISTRY（先用 backup_lib.sh 的 dump_file_registry 生成）" >&2
  exit 2
fi

TREE_ABS="$(cd "$TREE" && pwd -P)"

# sha256 工具与 backup_lib.sh 共用，保证两处对同一文件算出的摘要完全一样。
if [[ -f "$(dirname "$0")/backup_lib.sh" ]]; then
  # shellcheck source=ops/deploy/backup_lib.sh
  . "$(dirname "$0")/backup_lib.sh"
else
  echo "ERROR verify_file_tree.sh 找不到同目录的 backup_lib.sh：无法保证与备份流程用同一套校验口径" >&2
  exit 2
fi
if ! detect_sha256_tool >/dev/null; then
  exit 2
fi

# 不用关联数组/数组下标统计：目标是 RHEL/Debian 的 bash 4+，但保持兼容写法
# （老 macOS 的 bash 3.2 也能跑）——运维脚本在别人的笔记本上跑不动最耽误事。
work="$(mktemp -d "${TMPDIR:-/tmp}/crm-verifytree.XXXXXX")"
trap 'rm -rf "$work"' EXIT
FAILED_KEYS="$work/failed_keys"
SEEN_KEYS="$work/seen_keys"
: > "$FAILED_KEYS"
: > "$SEEN_KEYS"

n_ok=0; n_nohash=0; n_fail=0
n_missing=0; n_hash=0; n_empty=0; n_symlink=0; n_unreadable=0; n_escape=0
FAILED_KEYS="$FAILED_KEYS"

report() { # report <状态> <对象键> <说明>
  printf '%s\t%s\t%s\n' "$1" "$2" "$3"
}

bump_fail() { # bump_fail <状态> <对象键> <说明>
  report "$1" "$2" "$3"
  printf '%s\n' "$2" >> "$FAILED_KEYS"
  n_fail=$((n_fail + 1))
  case "$1" in
    FAIL-MISSING)    n_missing=$((n_missing + 1)) ;;
    FAIL-HASH)       n_hash=$((n_hash + 1)) ;;
    FAIL-EMPTY)      n_empty=$((n_empty + 1)) ;;
    FAIL-SYMLINK)    n_symlink=$((n_symlink + 1)) ;;
    FAIL-UNREADABLE) n_unreadable=$((n_unreadable + 1)) ;;
    FAIL-ESCAPE)     n_escape=$((n_escape + 1)) ;;
  esac
}

while IFS=$'\t' read -r key checksum || [[ -n "${key:-}" ]]; do
  [[ -z "${key:-}" ]] && continue
  key="${key%$'\r'}"            # 登记文件若被 Windows 编辑器碰过会带 CR
  checksum="${checksum%$'\r'}"

  # 对象键必须是安全相对路径：绝对路径、含 .. 的、含反斜杠的一律拒绝读取。
  # 报 FAIL-ESCAPE 而不是跳过：登记里出现越界键本身就是要处理的事故。
  case "$key" in
    /*|*..*|*\\*)
      bump_fail "FAIL-ESCAPE" "$key" "对象键不是安全的相对路径，拒绝在原件目录外读取"
      continue ;;
  esac

  target="$TREE_ABS/$key"
  printf '%s\n' "$key" >> "$SEEN_KEYS"

  if [[ -L "$target" ]]; then
    bump_fail "FAIL-SYMLINK" "$key" "是符号链接（→ $(readlink "$target" 2>/dev/null || echo '?')）：归档恢复后可能取不到原件内容"
    continue
  fi
  if [[ ! -e "$target" ]]; then
    bump_fail "FAIL-MISSING" "$key" "数据库有登记但原件不存在：只备数据库时正是这个结果"
    continue
  fi
  if [[ ! -f "$target" ]]; then
    bump_fail "FAIL-MISSING" "$key" "路径存在但不是普通文件（目录/设备等），不是可恢复的原件"
    continue
  fi
  if [[ ! -r "$target" ]]; then
    bump_fail "FAIL-UNREADABLE" "$key" "文件不可读（权限不足）：备份账号需要读权限"
    continue
  fi
  size="$(wc -c < "$target" 2>/dev/null | tr -d ' ')"
  if [[ -z "${size:-}" ]]; then
    bump_fail "FAIL-UNREADABLE" "$key" "读不到文件大小（磁盘错误？）"
    continue
  fi
  if [[ "$size" -eq 0 ]]; then
    bump_fail "FAIL-EMPTY" "$key" "0 字节：上传中断留下的半文件，恢复出来也是空的"
    continue
  fi

  if [[ -z "$checksum" ]]; then
    [[ "$QUIET" -eq 0 ]] && report "OK-NOHASH" "$key" "存在且非空（${size} 字节），但 files.checksum 为空：只能证明文件在，不能证明内容一致"
    n_nohash=$((n_nohash + 1))
    continue
  fi

  actual="$(file_sha256 "$target")" || {
    bump_fail "FAIL-UNREADABLE" "$key" "计算 sha256 失败"
    continue
  }
  if [[ "$actual" != "$checksum" ]]; then
    bump_fail "FAIL-HASH" "$key" "sha256 不一致：登记 $checksum，实际 $actual（${size} 字节）"
    continue
  fi
  [[ "$QUIET" -eq 0 ]] && report "OK" "$key" "$actual ${size} 字节"
  n_ok=$((n_ok + 1))
done < "$REGISTRY"

# 树上多出来的文件：正常上传过程中的临时文件会落到这里，不算失败，
# 但数量是"目录被别的进程写过"的信号，写进汇总以便判断能否信任这次快照。
sort -u "$SEEN_KEYS" > "$work/seen_sorted"
( cd "$TREE_ABS" && find . -type f 2>/dev/null | sed 's#^\./##' ) | sort -u > "$work/tree_sorted"
comm -13 "$work/seen_sorted" "$work/tree_sorted" > "$work/orphans"
n_orphan=$(wc -l < "$work/orphans" | tr -d ' ')
if [[ "$QUIET" -eq 0 && "$n_orphan" -gt 0 ]]; then
  while IFS= read -r o; do
    [[ -n "$o" ]] && report "ORPHAN" "$o" "原件目录里有、数据库没有登记（可能正在上传或已被清理）"
  done < "$work/orphans"
fi
n_registered=$(sort -u "$SEEN_KEYS" | wc -l | tr -d ' ')

if [[ "$EMIT_MISSING" -eq 1 && -s "$FAILED_KEYS" ]]; then
  echo "MISSING-LIST"
  # 输出对象键 + 登记校验值，调用方可直接拿去做"待补文件"工单
  awk -F'\t' 'NR==FNR{k[$1]=1;next} ($1 in k){print}' "$FAILED_KEYS" "$REGISTRY" | sort -u
fi

echo "SUMMARY registered=${n_registered} ok=${n_ok} ok_nohash=${n_nohash} fail=${n_fail} orphan=${n_orphan}"
if [[ "$n_fail" -gt 0 ]]; then
  echo "FAILKIND FAIL-MISSING=${n_missing} FAIL-HASH=${n_hash} FAIL-EMPTY=${n_empty} FAIL-SYMLINK=${n_symlink} FAIL-UNREADABLE=${n_unreadable} FAIL-ESCAPE=${n_escape}"
  echo "RESULT FAIL 文件原件树与数据库登记不一致：$n_fail 条（详见上面 FAIL-* 行）"
  exit 1
fi
echo "RESULT OK 全部登记对象存在且校验一致（其中 $n_nohash 条无登记校验值，仅证明存在）"
exit 0
