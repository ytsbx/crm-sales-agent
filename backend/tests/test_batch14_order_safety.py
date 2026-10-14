"""第十四批：真实入参边界和排期计算；数值/并发的真 PostgreSQL 见专项脚本。"""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.modules.order import schedule, milestones
from app.modules.order.schema import OrderCreate, OrderUpdate, OrderItemInput, ShipmentBatchCreate, ShipmentBatchShip
from app.modules.payment.schema import ReceivableCreate, ReceivableUpdate, PaymentCreate, PaymentUpdate
from app.modules.contract.schema import DocumentCreate


CASES = [
    (OrderCreate, {'customer_id':1,'items':[{'sku_id':1,'quantity':1,'unit_price':1}]}, 'payment_terms',128),
    (OrderUpdate, {}, 'payment_terms',128),
    (OrderItemInput, {'sku_id':1,'quantity':1,'unit_price':1}, 'specification',200),
    (ShipmentBatchCreate, {'items':[{'order_item_id':1,'planned_qty':1}]}, 'remark',255),
    (ShipmentBatchShip, {}, 'remark',255),
    (ReceivableCreate, {'plan_name':'全款','due_date':'2026-10-14','amount':1}, 'remark',255),
    (ReceivableUpdate, {}, 'remark',255),
    (PaymentCreate, {'received_date':'2026-10-14','received_amount':1}, 'payment_method',32),
    (PaymentUpdate, {}, 'payment_method',32),
    (PaymentCreate, {'received_date':'2026-10-14','received_amount':1}, 'voucher_note',255),
    (PaymentUpdate, {}, 'voucher_note',255),
    (DocumentCreate, {'template_id':1,'customer_id':1}, 'title',200),
]


@pytest.mark.parametrize('model,payload,field,limit',CASES)
def test_bounded_financial_text_accepts_limit_rejects_next(model,payload,field,limit):
    assert getattr(model(**payload,**{field:'中'*limit}),field)=='中'*limit
    with pytest.raises(ValidationError):
        model(**payload,**{field:'中'*(limit+1)})


@pytest.mark.parametrize('delivery,kind,days',[(date.min,'arrival',1),(date.min,'arrival',365)])
def test_arrival_calendar_underflow_is_business_error(delivery,kind,days):
    with pytest.raises(AppError,match='日期超出有效范围'):
        schedule.suggested_ship_date(delivery,kind,days)
    assert schedule.suggested_ship_date(date(2026,10,20),'arrival',7)==date(2026,10,13)


class EmptyPlanningSession:
    async def execute(self, stmt):
        return SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[]))


@pytest.mark.parametrize('delivery', [date.min,date.max])
def test_planning_offsets_calendar_overflow_is_business_error(delivery):
    offsets={key:(30 if delivery==date.min else -30) for key,_,_ in milestones.MILESTONE_NODES}
    order=SimpleNamespace(id=1,order_no='BOUNDARY',delivery_date=None,
        delivery_kind='shipping',transit_days=0,plan_offsets=None)
    with pytest.raises(AppError,match='日期超出有效范围'):
        asyncio.run(schedule.preview(EmptyPlanningSession(),order,delivery,
            delivery_kind='shipping',transit_days=0,plan_offsets=offsets))
    result=asyncio.run(schedule.preview(EmptyPlanningSession(),order,date(2026,10,20),
        delivery_kind='shipping',transit_days=0,plan_offsets={key:1 for key in offsets}))
    assert len(result['nodes'])==6
    assert all(row['after']=='2026-10-19' for row in result['nodes'])


def test_default_node_plan_has_same_date_guard():
    with pytest.raises(AppError,match='日期超出有效范围'):
        milestones.default_plan(date.min)
    assert milestones.default_plan(date(2026,10,20))['contract']==date(2026,9,20)
