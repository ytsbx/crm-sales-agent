"""结账Numeric(16,2)边界：明确拒绝且在任何存档改写之前退出。"""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.modules.analytics import target_actuals


@pytest.mark.parametrize("amount", ["0", "3.03", "99999999999999.99", "-99999999999999.99", "99999999999999.994", "-99999999999999.994"])
def test_representable_amounts_keep_postgres_rounding(amount):
    target_actuals._check_snapshot_amount("2025-01", "sales", "汇总", Decimal(amount))


@pytest.mark.parametrize("amount", ["99999999999999.995", "-99999999999999.995", "100000000000000", "NaN", "Infinity"])
def test_overflow_has_clear_period_and_metric(amount):
    with pytest.raises(AppError) as error:
        target_actuals._check_snapshot_amount("2025-01", "sales", "汇总", Decimal(amount))
    assert error.value.http_status == 422
    assert "2025-01" in error.value.message and "签单额汇总金额超出可保存范围" in error.value.message


@pytest.mark.parametrize("replace, bad_detail", [(False, False), (False, True), (True, False), (True, True)])
def test_whole_batch_is_checked_before_any_snapshot_mutation(monkeypatch, replace, bad_detail):
    async def scenario():
        session = AsyncMock()
        monkeypatch.setattr(target_actuals, "lock_period", AsyncMock())
        existing = {("company", "sales"): Decimal("8.88")} if replace else {}
        monkeypatch.setattr(target_actuals, "frozen_for", AsyncMock(return_value=existing))
        values = {("user:1", "sales"): Decimal("1.01"), ("company", "sales"): Decimal("100000000000000") if not bad_detail else Decimal("1.01")}
        items = [{"metric": "signed", "amount": Decimal("1.01")}, {"metric": "signed", "amount": Decimal("100000000000000")}] if bad_detail else []
        with pytest.raises(AppError) as error:
            await target_actuals.freeze_period(session, period="2025-01", values=values,
                                              basis_version="test", operator_id=1,
                                              replace=replace, note="隔离重算原因" if replace else None, items=items)
        assert error.value.http_status == 422
        assert "明细" in error.value.message if bad_detail else "汇总" in error.value.message
        session.get.assert_not_called()
        session.add.assert_not_called()
        session.delete.assert_not_called()
        session.execute.assert_not_called()
        session.flush.assert_not_called()
    asyncio.run(scenario())
