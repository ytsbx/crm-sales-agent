"""P2/B18-02、B19-03：目标快照与商机金额出口的 float 精度。"""
import sys
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.modules.analytics.targets import target_snapshot  # noqa: E402
from app.modules.analytics.model import SalesTarget  # noqa: E402
from app.modules.opportunity.service import _number  # noqa: E402

row = SalesTarget(
    id=1, period="2026-11", sales_target=Decimal("123456789.123456789"),
    repeat_customer_target=Decimal("987654321.987654321"), new_customer_target=3,
)
snap = target_snapshot(row)
print(f"[B18-02] sales_target={snap['sales_target']!r} type={type(snap['sales_target']).__name__}")
print(f"[B18-02] repeat_customer_target={snap['repeat_customer_target']!r} type={type(snap['repeat_customer_target']).__name__}")

print(f"[B19-03] _number(Decimal('123456789.123456789'))={_number(Decimal('123456789.123456789'))!r} type={type(_number(Decimal('123456789.123456789'))).__name__}")
