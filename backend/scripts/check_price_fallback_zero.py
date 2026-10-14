#!/usr/bin/env python3
"""算价的**回退链**守卫：明确填写的 0 不能被当成"没填"。

## 这个套件守什么（2026-10-14 审查报的第 21 条）

`pricing/service.py::calculate_price` 里原来用 `or` 串起回退链：

    standard_price = (rule.standard_price if rule else None) or (base_cost / (1 - margin))
    recommended = (customer_rule.agreed_price if customer_rule else None) \
        or (rule.guide_price if rule and rule.guide_price else None) \
        or (base_cost / (1 - margin) if has_cost else None)
    margin = target_margin or rule.target_margin or 默认值

而这三个价格字段在**接口 schema 上的约束是 `ge=0`**（`pricing/schema.py:77/80/83`）
—— 也就是 **0 是合法值**（免费样品、赠品、打包附赠等真实场景）。
但 `0 or X` 得到的是 `X`：库里写 `standard_price = 0`，算价返回的是成本反推价。

实测（修前）：规则里 `standard_price=0`、`guide_price=0` →
返回 `standard_price = 28.571428571428573`、`recommended_price = 28.571428571428573`。
**用户明明填了 0，系统给了另一个数，而且没有任何提示。**

`margin` 那条更宽：`margin >= 1` 只挡上界，**下界（0、负数）从来没守卫**，
而全局默认值 `default_target_margin` 是**自由文本**写进系统设置的
（`settings/service.py::validate_setting_value` 只校验两个"天数"键），
所以负数真能落到 `base_cost / (1 - margin)` 里。函数契约是 `(0, 1)`。

## 判据（直读产品函数，不走接口）

不走 HTTP 的原因见 `check_quote_center_acceptance.py` 里 A16 的注释：
接口层有脱敏与权限，读回的价格字段可能被隐藏，断言会假红。
这里直接用隔离库 + `calculate_price`，测的就是回退链本身。

## 跑法

    DB=crm_iso_test_pricezero
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend
    export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. DATABASE_URL=$DATABASE_URL .venv/bin/python scripts/check_price_fallback_zero.py
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

# 注册全部模型后再取会话（`import app.modules.user.model` 只为副作用，
# pyflakes 不认 `# noqa`，所以用一个真实引用让它成为"被使用"）
from app.modules.user.model import User  # noqa: E402

from app.core.database import SessionLocal, engine  # noqa: E402
from app.modules.pricing import service as pricing_service  # noqa: E402
from app.modules.pricing.model import PriceRule  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(f"  {'OK  ' if passed else 'FAIL'} {name}" + (f"  —— {detail}" if detail else ""))


async def _prepare(session) -> tuple[int, list[tuple[int, Decimal | None]]]:
    """给第一个 SKU 造一条**明确的 0 价**规则（覆盖它原有的档位），返回原值以便还原。"""
    sku_id = (
        await session.execute(select(PriceRule.sku_id).order_by(PriceRule.sku_id.asc()).limit(1))
    ).scalar_one()
    rows = (
        await session.execute(select(PriceRule).where(PriceRule.sku_id == sku_id))
    ).scalars().all()
    snapshot = [(row.id, row.standard_price) for row in rows]
    for row in rows:
        row.standard_price = Decimal("0")
        row.guide_price = Decimal("0")
        # 档位放开，保证这条规则一定能被数量 1 命中
        row.min_qty = Decimal("0")
        row.max_qty = None
        row.customer_level = None
        row.status = "active"
    await session.commit()
    return int(sku_id), snapshot


async def _restore(session, snapshot) -> None:
    for rule_id, value in snapshot:
        row = await session.get(PriceRule, rule_id)
        if row is not None:
            row.standard_price = value
    await session.commit()


async def _run() -> int:
    """整个套件跑在**同一个事件循环**里，结束时在同一个循环内 dispose。

    连接池绑在创建它的事件循环上（同 `read_margin_ctx` 的注释）：
    在循环外再 `asyncio.run(engine.dispose())` 会报 "Event loop is closed"。
    """
    try:
        return await _body()
    finally:
        await engine.dispose()


async def _body() -> int:
    async with SessionLocal() as session:
        sku_id, snapshot = await _prepare(session)
        try:
            base = await pricing_service.calculate_price(
                session,
                sku_id=sku_id,
                quantity=Decimal("1"),
                customer_id=None,
                quoted_price=None,
                role_codes=["salesperson"],
            )
            # ① 明确填写的 0 必须被尊重
            check(
                "规则里 standard_price=0 → 返回的 standard_price 就是 0",
                base.get("standard_price") in (0, Decimal("0")),
                f"实际 {base.get('standard_price')!r}"
                "（修前会被 `or` 跳过、返回成本反推价）",
            )
            check(
                "规则里 guide_price=0 → 返回的 recommended_price 就是 0",
                base.get("recommended_price") in (0, Decimal("0")),
                f"实际 {base.get('recommended_price')!r}",
            )

            # ② margin 下界必须有守卫（0 与负数都不能静默算下去）
            for raw in ("0", "-1"):
                raised = ""
                try:
                    await pricing_service.calculate_price(
                        session,
                        sku_id=sku_id,
                        quantity=Decimal("1"),
                        customer_id=None,
                        quoted_price=None,
                        role_codes=["salesperson"],
                        target_margin=Decimal(raw),
                    )
                except Exception as error:  # noqa: BLE001 —— 这里就是要看它有没有抛
                    raised = f"{type(error).__name__}: {str(error)[:40]}"
                check(
                    f"target_margin={raw} → 拒绝（而不是静默按别的利润率算）",
                    bool(raised),
                    raised or "**没有抛错**，说明下界仍然裸奔",
                )

            check(
                "库里有用户（夹具就绪）",
                (await session.execute(select(User).limit(1))).scalar_one_or_none() is not None,
            )

            # ③ 正常值不能被这条守卫误伤
            ok_value = await pricing_service.calculate_price(
                session,
                sku_id=sku_id,
                quantity=Decimal("1"),
                customer_id=None,
                quoted_price=None,
                role_codes=["salesperson"],
                target_margin=Decimal("0.3"),
            )
            check(
                "target_margin=0.3 仍然算得出价（守卫没误伤正常值）",
                # 顺带确认模型层可用（User 是这里唯一的直接引用）
                ok_value.get("recommended_price") is not None,
                f"建议价 {ok_value.get('recommended_price')}",
            )
        finally:
            await _restore(session, snapshot)

    failed = [name for name, passed, _ in RESULTS if not passed]
    print()
    if failed:
        print(f"算价回退链守卫：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过；失败：{failed}")
        return 1
    print(f"算价回退链守卫：{len(RESULTS)}/{len(RESULTS)} 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
