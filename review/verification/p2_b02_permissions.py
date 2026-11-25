"""P2 价格权限方向：B02-05 折扣取最严、最低毛利反而取最松。"""
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine  # noqa: E402
from app.modules.pricing import service as pricing_service  # noqa: E402
from app.modules.pricing.model import PricePermission  # noqa: E402
from app.modules.settings.model import SystemSetting  # noqa: E402
from app.modules.user.model import Role  # noqa: E402


async def main():
    engine = make_engine()
    for table in (Role, PricePermission, SystemSetting):
        table.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add_all([
            Role(id=1, code="salesperson", name="业务员", data_scope="self", status="active"),
            Role(id=2, code="sales_manager", name="销售主管", data_scope="department", status="active"),
        ])
        raw.add_all([
            PricePermission(id=1, role_id=1, minimum_margin=Decimal("0.10"),
                            discount_limit=Decimal("5.0000"), can_approve=False, status="active"),
            PricePermission(id=2, role_id=2, minimum_margin=Decimal("0.20"),
                            discount_limit=Decimal("10.0000"), can_approve=True, status="active"),
        ])
        raw.commit()
        session = SyncSessionAsAsync(raw)
        roles = ["salesperson", "sales_manager"]
        discount = await pricing_service.resolve_discount_limit(session, roles)
        margin, can_approve = await pricing_service.resolve_min_margin(session, roles)
        print(f"[B02-05] 角色并集 discount_limit={discount}（5 比 10 严，取 min 正确）")
        print(f"[B02-05] 角色并集 minimum_margin={margin} can_approve={can_approve}（0.10 比 0.20 松，取 min 反而放宽）")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
