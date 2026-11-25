"""P2 审计字段：B02-14 改价格权限只写 after、不写 before。"""
import asyncio
import sys
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
from app.modules.pricing.model import PricePermission  # noqa: E402
from app.modules.pricing.schema import PricePermissionUpdate  # noqa: E402
from app.modules.user.model import Role  # noqa: E402


async def main():
    engine = make_engine()
    for table in (Role, PricePermission, AuditLog):
        table.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Role(id=1, code="salesperson", name="业务员", data_scope="self", status="active"))
        raw.add(PricePermission(
            id=1, role_id=1, minimum_margin=Decimal("0.20"),
            discount_limit=Decimal("0.1000"), can_approve=False, status="active",
        ))
        raw.commit()
        session = SyncSessionAsAsync(raw)
        user = make_user(permissions=("price:manage",), roles=("admin",))
        req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
        await pricing_router.upsert_price_permission(
            role_id=1,
            payload=PricePermissionUpdate(
                minimum_margin=Decimal("0.10"), discount_limit=Decimal("0.0500"),
                can_approve=False, remark="P2 降权限",
            ),
            request=req, user=user, session=session,
        )
        row = raw.execute(
            select(AuditLog).where(
                AuditLog.business_type == "price_permission",
                AuditLog.business_id == 1,
                AuditLog.action == "update",
            )
        ).scalars().first()
        after = row.after_data if row else {}
        print(f"[B02-14] 审计 after keys={sorted(after.keys())} before={row.before_data if row else None}")
        print(f"[B02-14] 新值 minimum_margin={after.get('minimum_margin')} discount_limit={after.get('discount_limit')}")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
