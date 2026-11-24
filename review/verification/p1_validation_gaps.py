"""P1 隔离复现：0 成本、0 物理量、交接候选人范围。

运行：backend/.venv/bin/python review/verification/p1_validation_gaps.py
"""
import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine, make_user  # noqa: E402
from app.modules.product.model import Sku  # noqa: E402
from app.modules.product.schema import SkuCreate  # noqa: E402
from app.modules.pricing import logistics  # noqa: E402
from app.modules.quote.model import QuoteItem  # noqa: E402
from app.modules.quote.service import item_cost_problems  # noqa: E402
from app.modules.settings.model import SystemSetting  # noqa: E402
from app.modules.task import router as task_router  # noqa: E402
from app.modules.user.model import User  # noqa: E402

NOW = datetime.now(UTC)


async def main():
    # 1) F11-02：成本完整且金额为 0 的明细，完整性闸门是否放行。
    item = QuoteItem(
        id=1, quote_version_id=1, sku_name_snapshot="P0成本0",
        quantity=Decimal("1"), quoted_price=Decimal("100"),
        cost_snapshot=Decimal("0"), cost_present_snapshot=True, cost_complete_snapshot=True,
    )
    problems = item_cost_problems([item])
    print(f"[F11-02] 成本=0 的完整明细问题数={len(problems)}（0 表示放行）")

    # 2) F12-02：SKU 单重=0 能否过入参、能否静默算出 0 运费重量。
    payload = SkuCreate(
        sku_code="P1-ZERO", name="零重量SKU",
        length=Decimal("100"), width=Decimal("100"), height=Decimal("100"),
        weight=Decimal("0"),
    )
    print(f"[F12-02] SkuCreate(weight=0) 通过，weight={payload.weight}")

    engine = make_engine()
    SystemSetting.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        session = SyncSessionAsAsync(raw)
        sku = Sku(id=1, product_id=1, sku_code="P1-ZERO", weight=Decimal("0"),
                  length=Decimal("100"), width=Decimal("100"), height=Decimal("100"))
        measures = await logistics.compute_weight_and_volume(session, sku=sku, quantity=Decimal("1"))
        print(f"[F12-02] SKU weight=0 试算：actual_weight={measures['actual_weight']} "
              f"warnings={measures['warnings']}")
        print("[F12-02] 对照 logistics.py:437 手填 weight_override<=0 被拒；SKU 存 0 却放行")

        # 3) F10-05：销售主管的交接候选人是否只筛在岗、不筛部门/数据范围。
        raw.add_all([
            User(id=101, name="主管", username="mgr", password_hash="x",
                 department_id=1, status="active"),
            User(id=201, name="外部门员工", username="outsider", password_hash="x",
                 department_id=99, status="active"),
        ])
        raw.commit()
        result = await task_router.handover_candidates(
            keyword="", page=1, page_size=20,
            user=make_user(permissions=("task:manage",), roles=("sales_manager",), data_scope="self"),
            session=session,
        )
        print(f"[F10-05] 主管 data_scope=self；候选人={[(x['id'], x['name']) for x in result['data']['items']]}")
        print("[F10-05] 候选人无 department_id / data_scope 过滤，外部门 active 用户也在列")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
