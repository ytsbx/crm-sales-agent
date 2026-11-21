"""财务概览精确输出不得先经过float；真实PG链另由HTTP专项覆盖。"""
import asyncio
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.timebase import today_business
from app.modules.payment import service


@pytest.mark.parametrize('text', ['0.00', '123.45', '99999999999999.99'])
def test_summary_exact_amounts_from_decimal(monkeypatch, text):
    async def run():
        amount = Decimal(text)
        plans = [] if not amount else [SimpleNamespace(id=1,amount=amount,status='paid',due_date=today_business()+timedelta(days=1))]
        session = AsyncMock()
        session.get.return_value = SimpleNamespace(total_amount=amount)
        plan_result = Mock()
        plan_result.scalars.return_value.all.return_value = plans
        received_result, pending_result = Mock(), Mock()
        received_result.scalar_one.return_value = amount * 2
        pending_result.scalar_one.return_value = amount
        session.execute.side_effect = [plan_result, received_result, pending_result]
        monkeypatch.setattr(service,'confirmed_amounts',AsyncMock(return_value={1:amount} if plans else {}))
        monkeypatch.setattr(service,'allocation_totals',AsyncMock(return_value={1:{'allocated_amount':amount,'unallocated_amount':amount}}))
        result = await service.order_finance_summary(session,1)
        expected = {key:amount for key in ('order_amount','planned_amount','allocated_amount','unallocated_amount','pending_confirm_amount')}
        expected.update(received_amount=amount*2,unreceived_amount=-amount,unsettled_plan_amount=Decimal(0))
        for key,value in expected.items():
            assert result[key+'_exact'] == format(value,'.2f')
            assert result[key] == float(value)
        assert result['plan_count'] == len(plans) and result['overdue_count'] == 0
    asyncio.run(run())
