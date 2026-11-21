"""第十三批：真实采集/查询/核定 + 隔离 SQLite，包含正反向对照。"""
import asyncio

import pytest
from sqlalchemy import select

import test_erp_external_sync as fx
from app.core.errors import AppError
from app.modules.erp import collection as col, reconcile as rec
from app.modules.integration.model import ExternalObjectMapping, ExternalSyncWatermark, FinancialAdjustment, IntegrationDiff


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


def test_cross_shop_numbers_use_selected_shop_orders_and_the_same_period():
    s = collect([fx._order_record("1001", "CRM-A1"), fx._order_record("1002", "CRM-A2")])
    older = fx._order_record("1001", "CRM-B1")
    older["occurred_at"] = "2026-08-01T09:00:00"
    collect([older, fx._order_record("1002", "CRM-B2"), fx._order_record("2001", "CRM-B3")],
            shop="SHOP-B", session=s)
    collect([fx._order_record("2001", "CRM-C1")], shop="SHOP-C", session=s)

    def count(shop, start=fx.PERIOD_START.date(), end=fx.PERIOD_END.date()):
        result = asyncio.run(rec.run_reconciliation(
            s, period_start=start, period_end=end, shop_id=shop, system_type=fx.ADAPTER_SYSTEM,
        ))
        return result["counters"]["cross_shop_same_number"]

    criteria = IntegrationDiff.diff_type == rec.DIFF_CROSS_SHOP_NUMBER
    assert count("SHOP-A") == 1
    assert count("SHOP-UNKNOWN") == 0
    september = rows_of(s, IntegrationDiff, criteria)
    assert len(september) == 1 and september[0].external_id == "1002"
    assert september[0].incoming_value["shops"] == ["SHOP-A", "SHOP-B"]
    assert {key["shop_id"] for key in september[0].evidence["record_keys"]} == {"SHOP-A", "SHOP-B"}

    assert count("*") == 2
    assert count("SHOP-B") == 2
    assert len(rows_of(s, IntegrationDiff, criteria)) == 2
    # 同号差异也要按对账期间分开留痕。
    assert count("SHOP-A", end=fx.PERIOD_START.date().replace(day=25)) == 1
    assert september[0].period == rec.period_label(fx.PERIOD_START.date(), fx.PERIOD_END.date())
    assert len(rows_of(s, IntegrationDiff, criteria)) == 3


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


def test_reconciliation_closes_proven_correction_and_preserves_recurrence_history():
    def batch(qty, session=None):
        session = collect([fx._order_record("O1", "CRM1"),
                           fx._order_item("O1", "1", "A", "1")], session=session)
        fx._collect(session, fx.FakeCollectAdapter([[
            fx._shipment("S1", "O1"), fx._shipment_item("S1", "1", "O1", "A", qty),
        ]]), object_type="shipment")
        return session

    s = batch("2")
    assert run(s)["counters"]["over_shipped"] == 1
    criteria = IntegrationDiff.diff_type == "over_shipped"
    old = rows_of(s, IntegrationDiff, criteria)[0]
    assert old.status == "open"

    batch(None, session=s)
    assert run(s)["counters"]["diffs_auto_closed"] == 0
    assert old.status == "open"
    batch("1", session=s)
    cursor = rows_of(s, ExternalSyncWatermark)[0]
    for status, page_token in [("failed", None), ("idle", "more-pages")]:
        cursor.status, cursor.page_token = status, page_token
        asyncio.run(s.commit())
        assert run(s)["counters"]["diffs_auto_closed"] == 0
        assert old.status == "open"
    cursor.status, cursor.page_token = "idle", None
    asyncio.run(s.commit())
    assert run(s)["counters"]["diffs_auto_closed"] == 1
    assert old.status == "resolved" and old.resolution == "auto_reconciled"
    assert old.incoming_value["shipped"] == "2.0000"
    assert old.evidence["auto_resolution"]["values"]["shipped"] == "1.0000"
    assert run(s)["counters"]["diffs_auto_closed"] == 0

    for expected_count in (2, 3):
        batch("2", session=s)
        assert run(s)["counters"]["over_shipped"] == 1
        run(s)
        rows = rows_of(s, IntegrationDiff, criteria)
        assert len(rows) == expected_count
        assert sum(row.status == "open" for row in rows) == 1
        batch("1", session=s)
        assert run(s)["counters"]["diffs_auto_closed"] == 1
        assert all(row.status == "resolved" for row in rows)


def test_amount_auto_close_keeps_manual_aftersale_for_review():
    s = collect([fx._order_record("O1", "CRM1", "120"),
                 fx._aftersale("AS1", "O1", "return", amount="25")])
    s.add(fx._order("CRM1", amount="100"))
    asyncio.run(s.commit())
    assert run(s)["counters"]["amount_mismatch"] == 1

    collect([fx._order_record("O1", "CRM1", "100")], session=s)
    assert run(s)["counters"]["diffs_auto_closed"] == 1
    by_type = {row.diff_type: row for row in rows_of(s, IntegrationDiff)}
    assert by_type["amount_mismatch"].resolution == "auto_reconciled"
    assert by_type["aftersale_needs_review"].status == "open"


def test_amount_cannot_close_using_newer_shipment_watermark():
    session = collect([fx._order_record("O1", "CRM1", "110")])
    local = fx._order("CRM1", amount="100")
    session.add(local)
    asyncio.run(session.commit())
    assert run(session)["counters"]["amount_mismatch"] == 1
    local.total_amount = 110
    asyncio.run(session.commit())

    fx._collect(session, fx.FakeCollectAdapter([[]]), object_type="shipment")
    assert run(session)["counters"]["diffs_auto_closed"] == 0
    [diff] = rows_of(session, IntegrationDiff, IntegrationDiff.diff_type == "amount_mismatch")
    assert diff.status == "open"

    collect([fx._order_record("O1", "CRM1", "110")], session=session)
    assert run(session)["counters"]["diffs_auto_closed"] == 1
    assert set(diff.evidence["auto_resolution"]["collection_finished_by_source"]) == {"order"}


def test_over_return_cannot_close_until_both_shipment_and_aftersale_are_current():
    session = collect([fx._order_record("O1", "CRM1"),
                       fx._order_item("O1", "1", "SKU-A", "10")])

    def shipment(quantity):
        fx._collect(session, fx.FakeCollectAdapter([[
            fx._shipment("S1", "O1"), fx._shipment_item("S1", "1", "O1", "SKU-A", quantity),
        ]]), object_type="shipment")

    def aftersale():
        fx._collect(session, fx.FakeCollectAdapter([[
            fx._aftersale("AS1", "O1", "return"), fx._aftersale_item("AS1", "1", "O1", "return", "2"),
        ]]), object_type="aftersale")

    shipment("1")
    aftersale()
    assert run(session)["counters"]["over_returned"] == 1
    shipment("2")
    assert run(session)["counters"]["diffs_auto_closed"] == 0
    [diff] = rows_of(session, IntegrationDiff, IntegrationDiff.diff_type == "over_returned")
    assert diff.status == "open"
    aftersale()
    assert run(session)["counters"]["diffs_auto_closed"] == 1
    assert set(diff.evidence["auto_resolution"]["collection_finished_by_source"]) == {"shipment", "aftersale"}


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


def test_unmatched_order_line_evidence_survives_hashed_mapping_key_and_rerun():
    order_key = "O" * 120
    line_key = "L" * 12
    line = fx._order_item(order_key, line_key, "UNKNOWN", "1", external_id="LINE-REF")
    session = collect([fx._order_record(order_key, "CRM-LONG"), line])
    collect([fx._order_record(order_key, "OTHER"), line], shop="SHOP-B", session=session)
    run(session)
    criteria = (IntegrationDiff.diff_type == rec.DIFF_UNMATCHED_SKU, IntegrationDiff.shop_id == "SHOP-A")
    diff = rows_of(session, IntegrationDiff, *criteria)[0]
    assert diff.external_id.startswith("line-sha256:")
    evidence = asyncio.run(rec.diff_evidence(session, diff))
    expected_key = f"{order_key}#{line_key}"
    assert [(record["shop_id"], record["dedupe_key"]) for record in evidence["records"]] == [
        ("SHOP-A", expected_key),
    ]
    assert not evidence["missing_records"]

    diff.evidence = rec.build_evidence([])
    asyncio.run(session.commit())
    diff_id = diff.id
    run(session)
    refreshed = rows_of(session, IntegrationDiff, *criteria)
    assert [row.id for row in refreshed] == [diff_id]
    assert refreshed[0].evidence["record_keys"][0]["dedupe_key"] == expected_key


def test_missing_local_diff_auto_closes_on_verified_late_match():
    session = collect([fx._order_record("N01", "CRM-N01"), fx._order_item("N01", "1", "UNKNOWN", "1")])
    assert run(session)["counters"]["missing_local"] == 1
    diff = rows_of(session, IntegrationDiff, IntegrationDiff.diff_type == rec.DIFF_MISSING_LOCAL)[0]
    original_keys = diff.evidence["record_keys"]
    local = fx._order("CRM-N01")
    session.add(local)
    asyncio.run(session.commit())
    result = run(session)
    assert result["counters"]["missing_local"] == 0
    assert result["counters"]["diffs_auto_closed"] == 1
    assert diff.status == rec.DIFF_RESOLVED
    assert diff.evidence["record_keys"] == original_keys
    assert diff.evidence["auto_resolution"]["values"] == {"order_id": local.id, "order_no": local.order_no}
    assert run(session)["counters"]["diffs_auto_closed"] == 0


def test_pending_sku_uses_source_date_or_parent_period():
    session = collect([
        fx._order_record("SEPT", "CRM-SEPT"),
        fx._order_item("SEPT", "1", "IN-PERIOD", "1", occurred_at=None),
        fx._order_item("SEPT", "2", "LATER", "1", occurred_at="2026-10-15T10:00:00"),
        {"object_type": "sku", "external_id": "UNDATED", "external_code": "UNMATCHED-UNDATED"},
        {"object_type": "sku", "external_id": "OCT", "external_code": "UNMATCHED-OCT", "occurred_at": "2026-10-15T10:00:00"},
    ])
    assert run(session)["counters"]["unmatched_sku"] == 1
    diffs = rows_of(session, IntegrationDiff, IntegrationDiff.diff_type == rec.DIFF_UNMATCHED_SKU)
    assert [diff.external_id for diff in diffs] == ["SEPT#1"]
    assert diffs[0].evidence["record_keys"][0]["dedupe_key"] == "SEPT#1"


def test_pending_sku_no_longer_in_period_closes_old_diff_on_rerun():
    row = {"object_type": "sku", "external_id": "SKU-1", "external_code": "UNMATCHED",
           "occurred_at": "2026-09-15T10:00:00"}
    session = collect([row])
    assert run(session)["counters"]["unmatched_sku"] == 1
    diff = rows_of(session, IntegrationDiff, IntegrationDiff.diff_type == rec.DIFF_UNMATCHED_SKU)[0]
    collect([{**row, "occurred_at": "2026-10-15T10:00:00"}], session=session)
    result = run(session)
    assert result["counters"]["unmatched_sku"] == 0
    assert diff.status == rec.DIFF_RESOLVED
    assert diff.evidence["record_keys"] and diff.evidence["auto_resolution"]["record_keys"]


def test_pending_customers_follow_linked_orders_not_collection_dates_or_other_shops():
    def customer(key, **extra):
        return {"object_type": "customer", "external_id": key, "dedupe_key": f"customer:{key}", **extra}

    september = {**fx._order_record("SEPT", "CRM-SEPT"), "payload": {"customer_external_id": "C-SEP"}}
    october = {**fx._order_record("OCT", "CRM-OCT"), "occurred_at": "2026-10-04T09:00:00",
               "payload": {"customer_external_id": "C-OCT"}}
    session = collect([
        september,
        {**september, "dedupe_key": "SEPT2", "external_id": "SEPT2"},
        october,
        fx._order_record("LINKED", "CRM-LINKED"),
        customer("C-SEP", occurred_at="2026-10-07T09:00:00"),
        customer("C-OCT", occurred_at="2026-09-01T09:00:00"),
        customer("C-LINK", order_key="LINKED"),
        customer("C-UNLINKED", occurred_at="2026-09-01T09:00:00"),
        customer("C-CONFLICT", order_key="SEPT"),
    ])
    collect([september, customer("C-OCT", order_key="SEPT")], shop="SHOP-B", session=session)
    result = run(session)
    assert result["counters"]["unmatched_customer"] == 2
    criteria = IntegrationDiff.diff_type == rec.DIFF_UNMATCHED_CUSTOMER
    diffs = rows_of(session, IntegrationDiff, criteria)
    assert {(row.shop_id, row.external_id) for row in diffs} == {("SHOP-A", "C-SEP"), ("SHOP-A", "C-LINK")}
    sep = next(row for row in diffs if row.external_id == "C-SEP")
    assert {entry["dedupe_key"] for entry in sep.evidence["record_keys"]} == {"customer:C-SEP", "SEPT", "SEPT2"}
    next_period = asyncio.run(rec.run_reconciliation(
        session, period_start=fx.PERIOD_END.date(), period_end=fx.PERIOD_END.date().replace(month=11),
        system_type=fx.ADAPTER_SYSTEM,
    ))
    assert next_period["counters"]["unmatched_customer"] == 1
    assert {row.external_id for row in rows_of(session, ExternalObjectMapping,
        ExternalObjectMapping.object_type == "customer", ExternalObjectMapping.match_status == "pending")} == {
        "C-SEP", "C-OCT", "C-LINK", "C-UNLINKED", "C-CONFLICT",
    }


def test_pending_customer_no_longer_linked_to_period_closes_diff_and_keeps_mapping():
    order = {**fx._order_record("SEPT", "CRM-SEPT"), "payload": {"customer_external_id": "C-1"}}
    customer = {"object_type": "customer", "external_id": "C-1"}
    session = collect([order, customer])
    assert run(session)["counters"]["unmatched_customer"] == 1
    diff = rows_of(session, IntegrationDiff, IntegrationDiff.diff_type == rec.DIFF_UNMATCHED_CUSTOMER)[0]
    original = diff.evidence["record_keys"]
    collect([{**order, "occurred_at": "2026-10-04T09:00:00"}], session=session)
    result = run(session)
    assert result["counters"]["unmatched_customer"] == 0
    assert result["counters"]["diffs_auto_closed"] >= 1
    assert diff.status == rec.DIFF_RESOLVED
    assert diff.evidence["record_keys"] == original
    [mapping] = rows_of(session, ExternalObjectMapping, ExternalObjectMapping.object_type == "customer")
    assert mapping.match_status == "pending"
