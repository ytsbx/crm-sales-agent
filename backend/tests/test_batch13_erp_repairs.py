"""第十三批：真实采集/查询/核定 + 隔离 SQLite，包含正反向对照。"""
import asyncio

import pytest
from sqlalchemy import select

import test_erp_external_sync as fx
from app.core.errors import AppError
from app.modules.erp import collection as col, reconcile as rec
from app.modules.integration.model import FinancialAdjustment, IntegrationDiff


def collect(rows, *, shop='SHOP-A', session=None):
    session = session or fx._session()
    fx._collect(session, fx.FakeCollectAdapter([rows]), shop_id=shop)
    return session


def run(session):
    return asyncio.run(rec.run_reconciliation(
        session, period_start=fx.PERIOD_START.date(), period_end=fx.PERIOD_END.date(),
        system_type=fx.ADAPTER_SYSTEM,
    ))


def facts(session):
    return asyncio.run(rec.load_period_facts(session, system_type=fx.ADAPTER_SYSTEM,
        shop_id='*', start=fx.PERIOD_START, end=fx.PERIOD_END))


def rows_of(session, cls, *conditions):
    return asyncio.run(session.execute(select(cls).where(*conditions))).scalars().all()


def manual(session, row):
    return asyncio.run(rec.confirm_diff(session, row, resolution='manual',
        note='测试：凭原始售后资料人工核定', operator_id=None))


@pytest.mark.parametrize('qty,expected', [('10',0),('11',1)])
def test_parent_only_order_item_is_loaded(qty, expected):
    line = fx._order_item('O1','1','A','10'); line.pop('order_key')
    s=collect([fx._order_record('O1','CRM1'),line,fx._shipment('S1','O1'),
               fx._shipment_item('S1','1','O1','A',qty)])
    assert {x.dedupe_key for x in facts(s)['lines']} == {'O1#1','S1#1'}
    assert run(s)['counters']['over_shipped'] == expected


def test_merge_head_from_lines_enters_queue_and_keeps_all_orders():
    head=fx._aftersale('AS1','O1','return',amount='50');head.pop('order_key');head.pop('parent_key')
    s=collect([fx._order_record('O1','CRM1'),fx._order_item('O1','1','SKU-A','10'),
               head,fx._aftersale_item('AS1','1','O1','return','1'),
               fx._aftersale_item('AS1','2','OTHER-PERIOD','return','1')])
    s.add(fx._order('CRM1'));asyncio.run(s.commit())
    result=run(s)
    assert result['counters']['aftersale_pending_review']==1
    diff=rows_of(s,IntegrationDiff,IntegrationDiff.diff_type=='aftersale_needs_review')[0]
    assert diff.internal_id is None, '合单跨订单的整单金额不得全归本期订单'
    assert {x['dedupe_key'] for x in diff.evidence['records']} == {'AS1','AS1#1','AS1#2'}
    rerun=run(s)
    assert rerun['counters']['aftersale_pending_review']==1
    assert len(rows_of(s,IntegrationDiff,IntegrationDiff.diff_type=='aftersale_needs_review'))==1


def test_head_link_uses_shop_and_type_and_period_identity():
    h=fx._aftersale('SAME','O1','return',amount='50');h.pop('order_key');h.pop('parent_key')
    s=collect([fx._order_record('O1','CRM1'),h,fx._aftersale_item('SAME','1','O1','return','1')])
    # 同父单号不同类型；同订单号但另一店不在本期，均不能串入。
    collect([fx._shipment('SAME','OTHER')],session=s)
    h2=fx._aftersale('SAME','O1','return',amount='70')
    collect([h2,fx._aftersale_item('SAME','1','O1','return','2')],shop='SHOP-B',session=s)
    f=facts(s)
    assert [(h.shop_id,h.object_type,h.dedupe_key) for h in f['heads']]==[('SHOP-A','aftersale','SAME')]
    assert all(x.shop_id=='SHOP-A' for x in f['lines'])


@pytest.mark.parametrize('a,b,expected',[('10','10',0),('20','0',1)])
def test_sku_ship_quantities_cannot_offset(a,b,expected):
    s=collect([fx._order_record('O1','CRM1'),fx._order_item('O1','1','A','10'),
        fx._order_item('O1','2','B','10'),fx._shipment('S1','O1'),
        fx._shipment_item('S1','1','O1','A',a),fx._shipment_item('S1','2','O1','B',b)])
    assert run(s)['counters']['over_shipped']==expected
    diffs=rows_of(s,IntegrationDiff,IntegrationDiff.diff_type=='over_shipped')
    if expected:
        assert diffs[0].incoming_value=={'sku':'A','shipped':'20.0000'}
        assert {x['external_code'] for x in diffs[0].evidence['records'] if x['object_type'].endswith('_item')}=={'A'}


@pytest.mark.parametrize('sku,expected',[('SKU-A',0),('B',1)])
def test_sku_return_quantities_cannot_offset(sku,expected):
    line=fx._aftersale_item('AS1','1','O1','return','2');line['external_code']=sku
    s=collect([fx._order_record('O1','CRM1'),fx._order_item('O1','1','SKU-A','10'),
        fx._shipment('S1','O1'),fx._shipment_item('S1','1','O1','SKU-A','10'),
        fx._aftersale('AS1','O1','return'),line])
    assert run(s)['counters']['over_returned']==expected


def test_each_sku_gets_distinct_idempotent_difference():
    s=collect([fx._order_record('O1','CRM1'),fx._order_item('O1','1','A','1'),
        fx._order_item('O1','2','B','1'),fx._shipment('S1','O1'),
        fx._shipment_item('S1','1','O1','A','2'),fx._shipment_item('S1','2','O1','B','3')])
    assert run(s)['counters']['over_shipped']==2
    assert run(s)['counters']['over_shipped']==2
    ds=rows_of(s,IntegrationDiff,IntegrationDiff.diff_type=='over_shipped')
    assert len(ds)==2 and len({d.diff_key for d in ds})==2


@pytest.mark.parametrize('currency',['USD','usd','EUR'])
def test_foreign_collection_rejected(currency):
    row=fx._aftersale('AS1','O1','return',amount='50',currency=currency)
    with pytest.raises(AppError,match='人民币'):
        col.normalize_item(row,object_type='aftersale',position=2)


@pytest.mark.parametrize('currency',[None,'',' cny '])
def test_domestic_collection_normalized(currency):
    row=fx._aftersale('AS1','O1','return',currency=currency)
    assert col.normalize_item(row,object_type='aftersale',position=1)['currency']=='CNY'


def test_historical_foreign_diff_is_rejected_before_status_change():
    s=fx._session()
    d=IntegrationDiff(diff_key='historical-foreign',domain='order',system_type='ERP',
        shop_id='SHOP-A',object_type='aftersale',external_id='AS1',
        diff_type='aftersale_needs_review',status='open',incoming_value={'amount':'50','currency':'USD'})
    s.add(d);asyncio.run(s.commit())
    with pytest.raises(AppError,match='币种'):
        manual(s,d)
    assert d.status=='open' and not rows_of(s,FinancialAdjustment)


def test_late_order_match_updates_single_pending_ledger_with_trace():
    s=collect([fx._order_record('O1','CRM1'),fx._aftersale('AS1','O1','return',amount='50')])
    run(s);old=rows_of(s,IntegrationDiff,IntegrationDiff.diff_type=='aftersale_needs_review')[0]
    first=manual(s,old)['financial_adjustment'];assert first['order_id'] is None
    local=fx._order('CRM1');s.add(local);asyncio.run(s.commit())
    run(s)
    new=rows_of(s,IntegrationDiff,IntegrationDiff.diff_type=='aftersale_needs_review',IntegrationDiff.status=='open')[0]
    assert new.internal_id==local.id
    updated=manual(s,new)['financial_adjustment']
    assert updated['id']==first['id'] and updated['order_id']==local.id
    assert updated['amount']=='50.00' and '订单归属更正' in updated['reason']
    assert manual(s,new)['financial_adjustment']['id']==first['id']
    assert len(rows_of(s,FinancialAdjustment))==1
    assert old.internal_id is None, '原核定证据不能跟着更正'
