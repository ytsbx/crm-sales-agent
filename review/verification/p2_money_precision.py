"""P2 金额舍入与物流精度：B01-03 / B03-05 / B03-06。"""
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.modules.payment.service import split_amounts  # noqa: E402
from app.modules.pricing.logistics import quote_rate  # noqa: E402
from app.modules.pricing.logistics_schema import LogisticsCalculateRequest  # noqa: E402
from app.modules.pricing.model import LogisticsRate  # noqa: E402


def main():
    # B01-03：split_amounts 文档说“四舍五入”，代码量化的默认却是 HALF_EVEN。
    parts = split_amounts(Decimal("0.05"), [Decimal("0.5"), Decimal("0.5")])
    expected = [(Decimal("0.05") * Decimal("0.5")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)]
    expected.append(Decimal("0.05") - expected[0])
    print(f"[B01-03] split_amounts(0.05, 50/50)={parts}；HALF_UP 应为 {expected}")

    # B03-05：quote_rate 出参 amount 是 float；同一个 1.005 先按 HALF_EVEN 成 1.00。
    rate = LogisticsRate(
        id=1, provider="P2承运商", shipping_method="陆运",
        unit_price_per_kg=Decimal("1.0000"), unit_price_per_volume=None,
        min_charge=Decimal("0.00"), status="active",
    )
    result = quote_rate(rate, chargeable_weight=Decimal("1.005"), volume=Decimal("0"))
    print(f"[B03-05] quote_rate(1.005kg, 1元/kg) amount={result['amount']!r} "
          f"type={type(result['amount']).__name__}；HALF_UP 应为 1.01")

    # B03-06：试算 quantity 入参无位数/上限，落库列是 Numeric(16,3)。
    payload = LogisticsCalculateRequest(sku_id=1, quantity=Decimal("1.2345"))
    print(f"[B03-06] LogisticsCalculateRequest 接受 quantity={payload.quantity}（4 位小数仍可通过）")
    print(f"[B03-06] 同值落到 Numeric(16,3) 会变 {payload.quantity.quantize(Decimal('0.001'))}")
    huge = LogisticsCalculateRequest(sku_id=1, quantity=Decimal("9999999999999999"))
    print(f"[B03-06] 超大 quantity 也通过：{huge.quantity}")


if __name__ == "__main__":
    main()
