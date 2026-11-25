"""P2/B15-06、B16-02、B17-02、B17-07。"""
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.modules.integration.model import IntegrationLog  # noqa: E402
from app.modules.agent.insights import _f as insights_f  # noqa: E402
from app.modules.agent.tools import _money  # noqa: E402
from app.modules.pricing.logistics import compute_weight_and_volume  # noqa: E402
from app.modules.product.model import Sku  # noqa: E402

idx = [(i.name, i.unique, [c.name for c in i.columns]) for i in IntegrationLog.__table__.indexes]
print(f"[B15-06] IntegrationLog indexes={idx}")

print(f"[B16-02] insights._f(1.005)={insights_f(1.005)!r}")
print(f"[B17-02] tools._money(Decimal('1.005'))={_money(Decimal('1.005'))!r}")

async def main():
    sku = Sku(weight=Decimal('1.2000'), carton_volume=Decimal('0.0100'), carton_qty=10)
    try:
        await compute_weight_and_volume(None, sku=sku, quantity=2.5)
        print('[B17-07] float quantity 通过（不应发生）')
    except Exception as exc:
        print(f"[B17-07] float quantity -> {type(exc).__name__}: {exc}")

asyncio.run(main())
