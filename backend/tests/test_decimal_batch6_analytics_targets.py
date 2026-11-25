"""float→Decimal 专项批6 边界回归：分析/目标金额不得经 float。

覆盖：analytics _f 总闸 Decimal 原值 + 线路逐位精确；目标快照 sales_target /
repeat_customer_target Decimal + exact 同源；存档明细金额 Decimal 两位舍入
（精确半偶，修掉 round(float,2) 的 double 伪影边界）。
"""
import json
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.jsonio import exact_json_dumps
from app.modules.analytics import service as analytics_svc
from app.modules.analytics import target_actuals, targets
from app.modules.analytics.model import SalesTarget

BIG = D("99999999999999.99")


def _wire_exact(payload: dict, key: str, expected: D):
    back = json.loads(exact_json_dumps(payload), parse_float=D, parse_int=D)
    assert back[key] == expected, (key, back[key], expected)


def test_analytics_f_gate_keeps_decimal_and_wire_exact():
    assert analytics_svc._f(BIG) == BIG
    assert analytics_svc._f(None) == D(0)
    _wire_exact({"v": analytics_svc._f(BIG)}, "v", BIG)


def test_target_snapshot_keeps_decimal_and_exact():
    row = SalesTarget(period="2026-11", user_id=1, sales_target=BIG,
                      repeat_customer_target=D("1234.56"), new_customer_target=3)
    out = targets.target_snapshot(row)
    assert isinstance(out["sales_target"], D) and out["sales_target"] == BIG
    assert isinstance(out["repeat_customer_target"], D)
    assert out["sales_target_exact"] == format(BIG, "f")
    _wire_exact(out, "sales_target", BIG)


@pytest.mark.anyio
async def test_frozen_items_amount_decimal_rounding():
    rows = [SimpleNamespace(record_type="payment", record_id=1, label="回款",
                            owner_id=1, amount=D("2.675"), at_text="2026-11-01"),
            SimpleNamespace(record_type="payment", record_id=2, label="回款",
                            owner_id=1, amount=BIG, at_text="2026-11-02")]
    result = Mock()
    result.scalars.return_value.all.return_value = rows
    session = AsyncMock()
    session.execute.return_value = result
    out = await target_actuals.frozen_items(session, "2026-11", "received")
    # 精确十进制半偶舍入：2.675 → 2.68（旧 round(float,2) 因 double 伪影得 2.67）
    assert out[0]["amount"] == D("2.68")
    assert out[0]["amount_exact"] == "2.675"
    assert isinstance(out[1]["amount"], D) and out[1]["amount"] == BIG
    _wire_exact(out[1], "amount", BIG)