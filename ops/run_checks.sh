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
API_BASE="${API_BASE:-http://127.0.0.1:8000/api/v1}"
API_ORIGIN="${API_BASE%/api/v1}"
export API_BASE
cd "$SCRIPT_DIR/../backend"

# 本轮起点：收尾清扫（见下面 2.5）靠它划出"这轮跑出来的数据"，之前的一律不动
TEST_RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
export TEST_RUN_STARTED_AT

FAILED=()

if [[ ! -f "$SUITES_FILE" ]]; then
  echo "缺少套件清单：$SUITES_FILE"
  exit 1
fi

echo "== 0. 后端可达性 =="
if ! curl -sf -o /dev/null "$API_ORIGIN/docs"; then
  echo "后端没在 $API_ORIGIN 运行。先启动后端，或设置 API_BASE："
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
echo "== 1.8 复位业务口径（trade_mode）=="
# 有套件要造外币数据，得临时把口径放开成「国内与出口都做」（那是业务方的真实
# 做法，见 backend/scripts/_fx_scope.py），跑完自己收回。万一某个套件被硬杀、
# 没来得及收，**后面所有套件都会以为可以写外币** —— 外币断言会莫名其妙地绿。
# 这里统一兜底一次；本来就已经复位时它什么都不改（不产生多余审计）。
if PYTHONPATH=. .venv/bin/python scripts/reset_trade_mode.py; then
  echo "OK  业务口径=domestic"
else
  echo "FAIL 业务口径复位"
  FAILED+=("reset_trade_mode")
fi

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

echo
echo "== 2.5 清扫本轮通知与系统留痕 =="
# 各套件跑真实流程会生成站内通知/客户时间线留痕，它们没有 CHK 前缀、判据又常挂在
# 套件自己刚删掉的父表上，逐个清容易漏（实测总会剩几条"孤儿"）。这里按本轮时间窗
# 统一兜底；脚本没有时间窗就拒绝执行，不会误删业务数据。
if PYTHONPATH=. .venv/bin/python scripts/clean_test_run_leftovers.py; then
  echo "OK  clean_test_run_leftovers"
else
  echo "FAIL clean_test_run_leftovers"
  FAILED+=("clean_test_run_leftovers")
fi

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
