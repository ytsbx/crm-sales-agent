#!/usr/bin/env bash
# 本地一键全量验证（与 CI 同一份清单）：静态检查 + 接口回归 8 套件。
# 前提：后端已在 127.0.0.1:8000 运行（没跑就先起后端，脚本会提示）。
# UI 冒烟默认跳过，加 --ui 一起跑（需要本机 Chrome/Edge + 前端 5173 在跑）。
#
# 用法：
#   bash ops/run_checks.sh          # 静态 + 接口回归
#   bash ops/run_checks.sh --ui     # 再加 UI 冒烟

set -uo pipefail
cd "$(dirname "$0")/../backend"

FAILED=()

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
for suite in check_quote_api check_reference_integrity check_order_payment_api \
             check_customer_contact_api check_lead_auth_role_api \
             check_product_pricing_api check_agent_api \
             check_approval_rules check_agent_stream check_scheduler; do
  if PYTHONPATH=. .venv/bin/python "scripts/$suite.py" >/tmp/crm-check-$suite.log 2>&1; then
    echo "OK  $suite"
  else
    echo "FAIL $suite（详情：/tmp/crm-check-$suite.log）"
    FAILED+=("$suite")
  fi
done

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
