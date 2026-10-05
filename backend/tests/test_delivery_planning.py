from datetime import date, datetime, UTC
import pytest
from pydantic import ValidationError
from app.core.errors import AppError
from app.modules.order.model import SalesOrder, OrderMilestone
from app.modules.order.schedule import planning_parameters, shipment_date
from app.modules.order.milestones import row_status
from app.modules.order.schema import ScheduleChangeCreate


def test_arrival_subtracts_transport_and_keeps_customer_date():
    order = SalesOrder(delivery_kind=None, transit_days=None, plan_offsets=None)
    p = planning_parameters(order, date(2026, 10, 30), delivery_kind='arrival', transit_days=3)
    assert p['delivery_date'] == '2026-10-30'
    assert shipment_date(p) == date(2026, 10, 27)
    assert p['plan_offsets']['payment'] == -15


@pytest.mark.parametrize('kwargs', [{}, {'delivery_kind': 'arrival'}, {'delivery_kind': 'arrival', 'transit_days': -1}, {'delivery_kind': 'shipping', 'plan_offsets': {'contract': 30}}])
def test_incomplete_or_invalid_policy_is_not_inferred(kwargs):
    with pytest.raises(AppError):
        planning_parameters(SalesOrder(), date(2026, 10, 30), **kwargs)


@pytest.mark.parametrize('value', [True, 1.5, -366])
def test_offsets_are_integer_days_with_bounds(value):
    payload = dict(new_delivery_date='2026-10-30', delivery_kind='arrival', transit_days=3,
                   plan_offsets={'contract': value})
    if value == -366:
        with pytest.raises(AppError):
            planning_parameters(SalesOrder(), date(2026, 10, 30), delivery_kind='arrival', transit_days=3, plan_offsets=payload['plan_offsets'])
    else:
        with pytest.raises(ValidationError):
            ScheduleChangeCreate(**payload)


def test_skipped_is_neither_overdue_nor_completed():
    node = OrderMilestone(planned_date=date(2020, 1, 1), skipped_at=datetime.now(UTC))
    assert row_status(node, date(2026, 10, 5)) == 'skipped'
    node.skipped_at = None
    assert row_status(node, date(2026, 10, 5)) == 'overdue'
    node.actual_date = date(2020, 1, 2)
    assert row_status(node, date(2026, 10, 5)) == 'done'
