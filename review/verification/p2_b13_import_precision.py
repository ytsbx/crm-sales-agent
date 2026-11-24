"""P2/B13-06、B13-07：客户导入无幂等键；金额序列化走 float。"""
import inspect
import sys
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.modules.customer.io_router import import_customers  # noqa: E402
from app.modules.customer.service import _f  # noqa: E402

params = list(inspect.signature(import_customers).parameters)
print(f"[B13-06] import_customers 参数={params}")
print(f"[B13-06] 是否含 request_key={'request_key' in params}")

amount = Decimal("123456789.1234")
print(f"[B13-07] _f(Decimal('123456789.1234'))={_f(amount)!r} type={type(_f(amount)).__name__}")
