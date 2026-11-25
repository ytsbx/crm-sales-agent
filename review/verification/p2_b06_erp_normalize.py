"""P2/B06-07：采集记录缺币种被当 CNY；B06-06：erp_order_id 无唯一约束。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.modules.erp.collection import normalize_item  # noqa: E402
from app.modules.order.model import SalesOrder  # noqa: E402

item = normalize_item(
    {"external_id": "SO-1", "amount": "10.00", "payload": {}},
    object_type="order", position=1,
)
print(f"[B06-07] 不传 currency -> currency={item['currency']}")

try:
    normalize_item({"external_id": "SO-2", "amount": "10", "currency": "USD"}, object_type="order", position=2)
    print("[B06-07] USD 被放行（不应发生）")
except Exception as exc:
    print(f"[B06-07] USD 被拒 -> {type(exc).__name__}: {exc}")

col = SalesOrder.__table__.c.erp_order_id
indexes = [(i.name, i.unique, [c.name for c in i.columns]) for i in SalesOrder.__table__.indexes]
print(f"[B06-06] erp_order_id unique={col.unique} indexes={indexes}")
