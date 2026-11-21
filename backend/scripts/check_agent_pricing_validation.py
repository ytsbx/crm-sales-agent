"""Agent 核价工具必须与普通接口同一套数值校验（issue #14）。

审查实测的反例（我复现过）：

    真实工具函数 `calculate_price`
      数量 0   → **放行**（算出 0 元的"建议价"，看着像正常结果）
      数量 -1  → **放行**
      数量 1.5 → 抛 `Decimal × float` 的 **TypeError**
    而普通接口 `POST /pricing/calculate` 对同样输入一律 40001「数量必须大于 0」。

根因：工具把模型给的参数**直接**传给计算服务，没走接口那一份 Pydantic 校验。
JSON Schema 与 Python 类型注解都不能代替运行时校验 —— schema 里是
`"type": "number"`，模型完全可能给 0、负数或小数。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_ag \
    PYTHONPATH=. .venv/bin/python scripts/check_agent_pricing_validation.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.getenv("API_BASE", "http://127.0.0.1:8000/api/v1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _test_support import require_isolated_db  # noqa: E402

require_isolated_db()

passed = 0
failed: list[str] = []


def check_true(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  OK   {label} {detail}")
    else:
        failed.append(label)
        print(f"  FAIL {label} {detail}")


def _api(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + urllib.parse.quote(path, safe="/?&=%"), data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode() or "{}")


#: (说明, 工具入参, 普通接口是否放行)。**两条路径必须给出同一结论**。
CASES: tuple[tuple[str, dict, bool], ...] = (
    ("数量 0 必须拒", {"sku_id": 1, "quantity": 0}, False),
    ("数量 -1 必须拒", {"sku_id": 1, "quantity": -1}, False),
    ("拟报价 0 必须拒", {"sku_id": 1, "quantity": 1, "quoted_price": 0}, False),
    ("拟报价 -5 必须拒", {"sku_id": 1, "quantity": 1, "quoted_price": -5}, False),
    # 1.5 是**合法**数量（接口本来就放行它）。从前工具在这里抛
    # `Decimal × float` TypeError —— 现在两条路径都返回正常结果，这才叫一致。
    ("数量 1.5 应放行（接口也放行）", {"sku_id": 1, "quantity": 1.5}, True),
    ("数量 1 正常放行", {"sku_id": 1, "quantity": 1}, True),
    ("数量 1 + 拟报价 100 放行", {"sku_id": 1, "quantity": 1, "quoted_price": 100}, True),
)


async def _tool_results() -> list[tuple[str, bool, str]]:
    from app.core.database import SessionLocal
    from app.core.deps import CurrentUser
    from app.modules.agent.tools import TOOLS, ToolContext
    from app.modules.user.model import User

    out: list[tuple[str, bool, str]] = []
    async with SessionLocal() as s:
        me = await s.get(User, 1)
        if me is None:
            return [("登录用户不存在", False, "id=1")]
        user = CurrentUser(me, {"price:manage", "product:view"}, ["admin"], "all")
        spec = TOOLS["calculate_price"]
        for label, args, _want in CASES:
            ctx = ToolContext(session=s, user=user, agent_session_id=None, action_id=None)
            try:
                res = await spec.handler(ctx, **args)
                await s.rollback()
                out.append((label, True, f"建议价={res.get('recommended_price')}"))
            except Exception as exc:  # noqa: BLE001
                await s.rollback()
                out.append((label, False, f"{type(exc).__name__}: {str(exc)[:48]}"))
    return out


def main() -> int:
    res = _api("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1

    print("\n=== ① Agent 工具：非法数量/金额必须拒，合法必须放行 ===")
    tool_rows = asyncio.run(_tool_results())
    for (label, _args, want), (_l, allowed, detail) in zip(CASES, tool_rows, strict=True):
        check_true(f"工具：{label}", allowed == want, detail)

    print('\n=== ② 普通接口给出同一结论（这才是 issue 要的一致）===')
    for label, args, want in CASES:
        body = {k: v for k, v in args.items()}
        d = _api("POST", "/pricing/calculate", admin, body)
        api_ok = d.get("code") == 0
        check_true(
            f"接口：{label}",
            api_ok == want,
            f"code={d.get('code')} {str(d.get('message'))[:34]}",
        )

    print("\n=== ③ 被拒时给的是**可读中文**，不是 Pydantic 英文原文 ===")
    tool_rows_bad = [(l, detail) for l, allowed, detail in tool_rows if not allowed]
    for label, detail in tool_rows_bad:
        check_true(
            f"{label} → 文案可读",
            "需要大于 0" in detail and "Input should be" not in detail,
            detail[:52],
        )

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"Agent 核价与普通接口同一套校验：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
