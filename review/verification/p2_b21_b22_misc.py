"""P2/B21-04、B22-05。"""
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.modules.product_insight.model import ProductInsight  # noqa: E402
from app.modules.product_insight.router import _serialize  # noqa: E402
from app.modules.task.router import _active_owner  # noqa: E402
from app.modules.user.model import User  # noqa: E402

row = ProductInsight(id=1, title="t", price_assumption=Decimal("123456789.12"), review_round=1)
data = _serialize(row)
print(f"[B21-04] price_assumption={data['price_assumption']!r} type={type(data['price_assumption']).__name__}")

class FakeSession:
    async def get(self, model, key):
        return User(id=202, name="外部门员工", username="u202", password_hash="x", status="active", department_id=20)

async def main():
    target = await _active_owner(FakeSession(), 202)
    print(f"[B22-05] _active_owner(202) -> id={target.id} name={target.name} dept={target.department_id} status={target.status}")

asyncio.run(main())
