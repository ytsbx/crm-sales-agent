"""P2 产品主数据：B03-04 同日重复成本；B03-07 软删 SKU 仍可建价格规则。"""
import asyncio
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine, make_user  # noqa: E402
from app.core.audit import AuditLog  # noqa: E402
from app.modules.pricing import router as pricing_router  # noqa: E402
from app.modules.pricing.model import PriceRule, ProductCost  # noqa: E402
from app.modules.pricing.schema import CostCreate, PriceRuleCreate  # noqa: E402
from app.modules.product.model import Product, Sku  # noqa: E402

NOW = datetime.now(UTC)
REQ = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})


async def main():
    engine = make_engine()
    for table in (Product, Sku, ProductCost, PriceRule, AuditLog):
        table.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Product(id=1, name="P2产品", status="active"))
        raw.add_all([
            Sku(id=1, product_id=1, sku_code="P2-SKU-ACTIVE", name="P2正常SKU",
                status="active", deleted_at=None),
            Sku(id=2, product_id=1, sku_code="P2-SKU-DELETED", name="P2软删SKU",
                status="active", deleted_at=NOW),
        ])
        raw.commit()
        session = SyncSessionAsAsync(raw)
        user = make_user(permissions=("price:manage",), roles=("admin",))

        # B03-04：同 SKU、同 effective_from 连建两条成本。
        for amount in (Decimal("10.0000"), Decimal("12.0000")):
            await pricing_router.create_cost(
                sku_id=1,
                payload=CostCreate(purchase_cost=amount, effective_from=date(2026, 11, 23)),
                request=REQ, user=user, session=session,
            )
        count = len(raw.execute(select(ProductCost).where(ProductCost.sku_id == 1)).scalars().all())
        print(f"[B03-04] 同 SKU 同生效日成本条数={count}（期望 1，实际 2）")

        # B03-07：软删 SKU 仍可建价格规则。
        await pricing_router.create_price_rule(
            payload=PriceRuleCreate(
                sku_id=2, standard_price=Decimal("20.0000"),
                effective_from=date(2026, 11, 23),
            ),
            request=REQ, user=user, session=session,
        )
        rules = raw.execute(select(PriceRule).where(PriceRule.sku_id == 2)).scalars().all()
        print(f"[B03-07] 软删 SKU 建价格规则数={len(rules)} "
              f"status={[r.status for r in rules]} sku.deleted_at={raw.get(Sku, 2).deleted_at}")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
