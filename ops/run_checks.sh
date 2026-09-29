#!/usr/bin/env bash
# 本地一键全量验证（与 CI 同一份清单）：静态检查 + 接口回归。
# 套件清单来自 ops/check_suites.txt（唯一真源），CI 读的是同一个文件。
# 前提：后端已在 127.0.0.1:8000 运行（没跑就先起后端，脚本会提示）。
# UI 冒烟默认跳过，加 --ui 一起跑（需要本机 Chrome/Edge + 前端 5173 在跑）。
#
# 用法：
#   bash ops/run_checks.sh          # 静态 + 接口回归
#   bash ops/run_checks.sh --ui     # 再加 UI 冒烟

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUITES_FILE="$SCRIPT_DIR/check_suites.txt"
cd "$SCRIPT_DIR/../backend"

FAILED=()

if [[ ! -f "$SUITES_FILE" ]]; then
  echo "缺少套件清单：$SUITES_FILE"
  exit 1
fi

echo "== 0. 后端可达性 =="
if ! curl -sf -o /dev/null http://127.0.0.1:8000/docs; then
  echo "后端没在 127.0.0.1:8000 运行。先起后端："
  echo "  cd backend && PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000"
  exit 1
fi
echo "backend ready"

echo
echo "== 1. 静态检查 =="
.venv/bin/python -m pyflakes app && echo "OK  pyflakes" || FAILED+=("pyflakes")
PYTHONPATH=. .venv/bin/python scripts/check_model_attrs.py  >/dev/null && echo "OK  模型列"   || FAILED+=("check_model_attrs")
PYTHONPATH=. .venv/bin/python scripts/check_permissions.py >/dev/null && echo "OK  权限码"   || FAILED+=("check_permissions")

echo
echo "== 1.5 单元测试（pytest，纯函数，不连库不连网）=="
PYTHONPATH=. .venv/bin/python -m pytest || FAILED+=("pytest")

echo
echo "== 2. 接口回归（顺序跑）=="
while read -r suite; do
  case "$suite" in '' | '#'*) continue ;; esac
  if [[ ! -f "scripts/$suite.py" ]]; then
    # 变量必须写成 ${suite}：后面紧跟的是全角括号，bash 会把多字节字符
    # 当成变量名的一部分，在 set -u 下报 "unbound variable" 并直接退出——
    # 结果是"套件失败"变成"脚本崩了"，连是哪个套件失败都看不到（真踩过）。
    echo "FAIL ${suite}（清单里有，但 scripts/${suite}.py 不存在）"
    FAILED+=("$suite")
    continue
  fi
  if PYTHONPATH=. .venv/bin/python "scripts/$suite.py" >/tmp/crm-check-$suite.log 2>&1; then
    echo "OK  $suite"
  else
    echo "FAIL ${suite}（详情：/tmp/crm-check-${suite}.log）"
    FAILED+=("$suite")
  fi
done < "$SUITES_FILE"

if [[ "${1:-}" == "--ui" ]]; then
  echo
  echo "== 3. UI 冒烟 =="
  cd ..
  if node ops/smoke_ui.mjs; then echo "OK  smoke_ui"; else echo "FAIL smoke_ui"; FAILED+=("smoke_ui"); fi
  cd backend
fi

echo
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "FAILED: ${FAILED[*]}"
  exit 1
fi
echo "全部通过 ✓"
