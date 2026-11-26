"""现有产品寄样审批（2026-11-26 主人拍板）。

定制样品凭原询价审批寄样；现有产品走审批中心：
业务员提交承运商/运费/说明 → 主管批准 → 才能登记寄样；批准同时把现货样品单批成已批准。
审批状态只有一处真源：`ApprovalInstance(business_type='sample_shipment')`。
"""
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.approval import router as approval_router
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord
from app.modules.sample import router as sample_router
from app.modules.sample.model import SampleItem, SampleRequest, SampleShipment
from app.modules.sample.schema import SampleShipApprovalApply
from conftest import SyncSessionAsAsync, make_user


def request() -> Request:
    return Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


def manager():
    return SimpleNamespace(id=9, roles=['sales_manager'], has=lambda code: False)


def salesperson():
    return SimpleNamespace(id=1, roles=['salesperson'], has=lambda code: False)


def sample(**changes) -> SampleRequest:
    values = dict(
        id=7, status='pending', version=1, customer_id=None, owner_id=1,
        opportunity_id=None, made_at=None, shipped_at=None, source_context={},
    )
    return SampleRequest(**(values | changes))


@pytest.fixture()
def approval_db(db_session):
    for model in (SampleRequest, SampleItem, SampleShipment,
                  ApprovalDefinition, ApprovalInstance, ApprovalRecord, AuditLog):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    return db_session


def _mock_sample_for(monkeypatch, row):
    monkeypatch.setattr(sample_router.svc, 'get_visible_or_404', AsyncMock(return_value=row))
    monkeypatch.setattr(approval_router.sample_service, 'get_visible_or_404', AsyncMock(return_value=row))
    monkeypatch.setattr(sample_router.svc, 'detail', AsyncMock(return_value={'id': row.id}))
    monkeypatch.setattr(sample_router.svc, 'sample_is_custom', AsyncMock(return_value=False))
    monkeypatch.setattr(approval_router.sample_service, 'record_opportunity_touch', AsyncMock())
    monkeypatch.setattr(approval_router, '_context', AsyncMock(return_value={
        'samples': {row.id: row}, 'customers': {}, 'users': {},
        'versions': {}, 'quotes': {},
    }))


@pytest.mark.anyio
async def test_apply_creates_pending_approval_in_center(approval_db, monkeypatch):
    row = sample()
    _mock_sample_for(monkeypatch, row)
    audit = AsyncMock()
    monkeypatch.setattr(sample_router, 'write_audit', audit)

    await sample_router.apply_ship_approval(
        7, SampleShipApprovalApply(carrier='顺丰', shipping_fee=Decimal('25.50'), note='客户急要'),
        request(), make_user(1, permissions=('sample:view', 'sample:manage'), data_scope='all'),
        SyncSessionAsAsync(approval_db),
    )

    instance = (
        approval_db.query(ApprovalInstance)
        .filter_by(business_type='sample_shipment', business_id=7)
        .one()
    )
    assert instance.status == 'pending'
    assert instance.applicant_id == 1
    assert instance.summary['shipping_fee'] == '25.50'
    assert instance.summary['carrier'] == '顺丰'
    assert instance.definition_id is not None
    assert audit.call_args.kwargs['action'] == 'sample_ship_apply'


@pytest.mark.anyio
async def test_custom_sample_does_not_use_internal_ship_approval(approval_db, monkeypatch):
    row = sample()
    _mock_sample_for(monkeypatch, row)
    monkeypatch.setattr(sample_router.svc, 'sample_is_custom', AsyncMock(return_value=True))
    with pytest.raises(AppError) as caught:
        await sample_router.apply_ship_approval(
            7, SampleShipApprovalApply(shipping_fee=Decimal('10')), request(),
            make_user(1, permissions=('sample:manage',), data_scope='all'),
            SyncSessionAsAsync(approval_db),
        )
    assert '原询价审批' in caught.value.message
    assert approval_db.query(ApprovalInstance).count() == 0


def _pending_instance(db, applicant_id=1) -> ApprovalInstance:
    definition = ApprovalDefinition(
        id=1, code='sample_shipment', name='寄样审批', business_type='sample_shipment'
    )
    db.add(definition)
    instance = ApprovalInstance(
        id=5, definition_id=1, business_type='sample_shipment', business_id=7,
        applicant_id=applicant_id, status='pending', current_node='manager',
        summary={'carrier': '顺丰', 'shipping_fee': '25.50', 'note': None},
        created_at=datetime.now(UTC),
    )
    db.add(instance)
    db.commit()
    return instance


@pytest.mark.anyio
async def test_manager_approves_in_center_and_sample_becomes_approved(approval_db, monkeypatch):
    row = sample()
    _mock_sample_for(monkeypatch, row)
    audit = AsyncMock()
    monkeypatch.setattr(approval_router, 'write_audit', audit)
    instance = _pending_instance(approval_db)

    await approval_router.approve(
        instance.id, request(), manager(), SyncSessionAsAsync(approval_db)
    )

    approval_db.refresh(instance)
    assert instance.status == 'approved'
    assert instance.summary['approver_id'] == 9
    assert row.status == 'approved' and row.approved_at is not None
    record = approval_db.query(ApprovalRecord).filter_by(approval_instance_id=instance.id).one()
    assert record.action == 'approve'
    assert audit.call_args.kwargs['action'] == 'sample_ship_approve'


@pytest.mark.anyio
async def test_reject_requires_reason_and_keeps_sample_pending(approval_db, monkeypatch):
    row = sample()
    _mock_sample_for(monkeypatch, row)
    monkeypatch.setattr(approval_router, 'write_audit', AsyncMock())
    instance = _pending_instance(approval_db)

    with pytest.raises(AppError) as caught:
        await approval_router.reject(
            instance.id, request(), comment='  ', user=manager(),
            session=SyncSessionAsAsync(approval_db),
        )
    assert '驳回原因' in caught.value.message
    assert instance.status == 'pending'

    await approval_router.reject(
        instance.id, request(), comment='运费偏高', user=manager(),
        session=SyncSessionAsAsync(approval_db),
    )
    approval_db.refresh(instance)
    assert instance.status == 'rejected'
    assert instance.summary['comment'] == '运费偏高'
    assert row.status == 'pending'


@pytest.mark.anyio
async def test_non_manager_and_self_approval_are_refused(approval_db, monkeypatch):
    row = sample()
    _mock_sample_for(monkeypatch, row)
    instance = _pending_instance(approval_db, applicant_id=9)
    with pytest.raises(AppError) as caught:
        await approval_router.approve(
            instance.id, request(), salesperson(), SyncSessionAsAsync(approval_db)
        )
    assert caught.value.http_status == 403
    with pytest.raises(AppError) as caught:
        # 申请人 9 就是主管自己：不能自批
        await approval_router.approve(
            instance.id, request(), manager(), SyncSessionAsAsync(approval_db)
        )
    assert '不能审批自己' in caught.value.message or '只有主管' in caught.value.message


@pytest.mark.anyio
async def test_applicant_can_withdraw_ship_application(approval_db, monkeypatch):
    row = sample()
    _mock_sample_for(monkeypatch, row)
    monkeypatch.setattr(approval_router, 'write_audit', AsyncMock())
    instance = _pending_instance(approval_db)
    applicant = make_user(1, permissions=('sample:manage',), data_scope='all')

    await approval_router.withdraw(
        instance.id, approval_router.ApprovalWithdraw(comment='先不寄了'),
        request(), applicant, SyncSessionAsAsync(approval_db),
    )
    approval_db.refresh(instance)
    assert instance.status == 'withdrawn'
    assert row.status == 'pending'


@pytest.mark.anyio
async def test_ship_requires_approved_instance_and_freezes_fee(approval_db, monkeypatch):
    row = sample(status='approved')
    monkeypatch.setattr(sample_router.svc, 'sample_is_custom', AsyncMock(return_value=False))
    with pytest.raises(AppError) as caught:
        await sample_router._require_ship_approved(SyncSessionAsAsync(approval_db), row)
    assert '主管审批' in caught.value.message

    definition = ApprovalDefinition(id=2, code='sample_shipment', name='寄样审批',
                                    business_type='sample_shipment')
    approval_db.add(definition)
    approval_db.add(ApprovalInstance(
        definition_id=2, business_type='sample_shipment', business_id=7,
        applicant_id=1, status='approved', summary={'shipping_fee': '25.50'},
        created_at=datetime.now(UTC),
    ))
    approval_db.commit()
    approval = await sample_router._require_ship_approved(SyncSessionAsAsync(approval_db), row)
    assert approval['shipping_fee'] == '25.50'


@pytest.mark.anyio
async def test_custom_ship_still_uses_inquiry_approval(monkeypatch):
    from app.modules.oa import business_gates

    gate = AsyncMock(return_value=SimpleNamespace(status='approved'))
    monkeypatch.setattr(business_gates, 'require_sample_approved', gate)
    monkeypatch.setattr(sample_router.svc, 'sample_is_custom', AsyncMock(return_value=True))
    row = sample(status='approved')
    assert await sample_router._require_ship_approved(SimpleNamespace(), row) is None
    gate.assert_awaited_once()


@pytest.mark.anyio
async def test_sample_list_filter_uses_approval_instances(approval_db):
    from conftest import make_user
    from app.modules.sample import router as sr

    approval_db.add_all([
        SampleRequest(id=21, owner_id=1, status='pending'),
        SampleRequest(id=22, owner_id=1, status='pending'),
    ])
    definition = ApprovalDefinition(id=3, code='sample_shipment', name='寄样审批',
                                    business_type='sample_shipment')
    approval_db.add(definition)
    approval_db.add_all([
        ApprovalInstance(definition_id=3, business_type='sample_shipment', business_id=21,
                         applicant_id=1, status='pending', summary={}, created_at=datetime.now(UTC)),
        ApprovalInstance(definition_id=3, business_type='sample_shipment', business_id=22,
                         applicant_id=1, status='approved', summary={}, created_at=datetime.now(UTC)),
    ])
    approval_db.commit()
    user = make_user(1, permissions=('sample:view', 'sample:manage'), data_scope='all')
    result = await sr.list_samples(
        keyword=None, status=None, customer_id=None, opportunity_id=None, owner_id=None,
        include_history=False, include_deleted=False, ship_approval_status='pending',
        page=1, page_size=20, user=user, session=SyncSessionAsAsync(approval_db),
    )
    assert [row['id'] for row in result['data']['items']] == [21]


@pytest.mark.anyio
async def test_approval_center_lists_sample_ship_with_category(approval_db):
    """审批中心能按类型列出寄样审批，并给出看样品所需的字段。"""
    from conftest import make_user

    approval_db.add(SampleRequest(id=21, owner_id=1, status='pending'))
    approval_db.add(ApprovalInstance(
        id=30, definition_id=1, business_type='sample_shipment', business_id=21,
        applicant_id=1, status='pending', current_node='manager',
        summary={'carrier': '顺丰', 'shipping_fee': '25.50', 'note': '客户急要'},
        created_at=datetime.now(UTC),
    ))
    approval_db.commit()
    approver = make_user(9, permissions=('sample:view',), roles=('sales_manager',), data_scope='all')
    result = await approval_router.list_approvals(
        status='pending', mine=False, pending_for_me=False, page=1, page_size=20,
        business_type='sample_shipment', user=approver, session=SyncSessionAsAsync(approval_db),
    )
    row = result['data']['items'][0]
    assert row['business_type'] == 'sample_shipment'
    assert row['business_type_label'] == '寄样审批'
    assert row['sample_no'] == '打样单编号 21'
    assert row['carrier'] == '顺丰'
    assert row['shipping_fee'] == '25.50'
    assert row['can_approve'] is True
