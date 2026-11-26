"""现有产品寄样审批（2026-11-26 主人拍板补上）。

定制样品的寄样凭原询价审批；现有产品没有定制审批可依，走 CRM 内：
业务员提交承运商/运费/说明 → 主管批准 → 才能登记寄样；批准同时把样品单批成已批准。
"""
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.sample import router
from app.modules.sample.model import SampleRequest
from app.modules.sample.schema import SampleShipApprovalApply, SampleShipApprovalDecision


def request() -> Request:
    return Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


def sample(**changes) -> SampleRequest:
    values = dict(
        id=7, status='pending', version=1, customer_id=1, owner_id=1,
        opportunity_id=None, made_at=None, shipped_at=None,
        source_context={'dingtalk_oa': {'mode': 'standalone', 'fields': {}}},
    )
    return SampleRequest(**(values | changes))


def _setup(monkeypatch, row, *, custom=False):
    session = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock())
    audit = AsyncMock()
    monkeypatch.setattr(router.svc, 'get_visible_or_404', AsyncMock(return_value=row))
    monkeypatch.setattr(router.svc, 'detail', AsyncMock(return_value={'id': row.id}))
    monkeypatch.setattr(router.svc, 'record_opportunity_touch', AsyncMock())
    monkeypatch.setattr(router.svc, 'sample_is_custom', AsyncMock(return_value=custom))
    monkeypatch.setattr(router, 'write_audit', audit)
    return session, audit


def manager():
    return SimpleNamespace(id=9, roles=['sales_manager'], has=lambda code: False)


def salesperson():
    return SimpleNamespace(id=1, roles=['salesperson'], has=lambda code: False)


@pytest.mark.anyio
async def test_existing_product_apply_sets_pending(monkeypatch):
    row = sample()
    session, audit = _setup(monkeypatch, row)
    await router.apply_ship_approval(
        7, SampleShipApprovalApply(carrier='顺丰', shipping_fee=Decimal('25.50'), note='客户急要'),
        request(), salesperson(), session,
    )
    approval = row.source_context['ship_approval']
    assert approval['status'] == 'pending'
    assert approval['applicant_id'] == 1
    assert approval['shipping_fee'] == '25.50'
    assert row.status == 'pending'
    assert audit.call_args.kwargs['action'] == 'sample_ship_apply'
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_custom_sample_does_not_use_internal_ship_approval(monkeypatch):
    row = sample()
    session, _ = _setup(monkeypatch, row, custom=True)
    with pytest.raises(AppError) as caught:
        await router.apply_ship_approval(
            7, SampleShipApprovalApply(shipping_fee=Decimal('10')),
            request(), salesperson(), session,
        )
    assert '原询价审批' in caught.value.message
    assert 'ship_approval' not in (row.source_context or {})


@pytest.mark.anyio
async def test_manager_approval_marks_sample_approved(monkeypatch):
    row = sample()
    updated = manager()  # 申请人不是主管本人
    row.source_context['ship_approval'] = {
        'status': 'pending', 'applicant_id': 1, 'shipping_fee': '25.50',
        'carrier': '顺丰', 'applied_at': datetime.now(UTC).isoformat(),
    }
    session, audit = _setup(monkeypatch, row)
    await router.approve_ship_approval(
        7, SampleShipApprovalDecision(comment='同意'), request(), updated, session,
    )
    approval = row.source_context['ship_approval']
    assert approval['status'] == 'approved'
    assert approval['approver_id'] == 9
    assert row.status == 'approved'
    assert row.approved_at is not None
    assert audit.call_args.kwargs['action'] == 'sample_ship_approve'


@pytest.mark.anyio
async def test_non_manager_cannot_approve(monkeypatch):
    row = sample()
    row.source_context['ship_approval'] = {
        'status': 'pending', 'applicant_id': 1, 'shipping_fee': '0',
    }
    session, _ = _setup(monkeypatch, row)
    with pytest.raises(AppError) as caught:
        await router.approve_ship_approval(
            7, SampleShipApprovalDecision(), request(), salesperson(), session,
        )
    assert caught.value.http_status == 403
    assert row.source_context['ship_approval']['status'] == 'pending'


@pytest.mark.anyio
async def test_applicant_cannot_approve_own_request(monkeypatch):
    row = sample()
    row.source_context['ship_approval'] = {
        'status': 'pending', 'applicant_id': 9, 'shipping_fee': '0',
    }
    session, _ = _setup(monkeypatch, row)
    with pytest.raises(AppError) as caught:
        await router.approve_ship_approval(
            7, SampleShipApprovalDecision(), request(), manager(), session,
        )
    assert '不能审批自己' in caught.value.message


@pytest.mark.anyio
async def test_reject_requires_reason_and_keeps_sample_pending(monkeypatch):
    row = sample()
    row.source_context['ship_approval'] = {
        'status': 'pending', 'applicant_id': 1, 'shipping_fee': '0',
    }
    session, audit = _setup(monkeypatch, row)
    with pytest.raises(AppError) as caught:
        await router.reject_ship_approval(
            7, SampleShipApprovalDecision(comment='  '), request(), manager(), session,
        )
    assert '驳回原因' in caught.value.message
    await router.reject_ship_approval(
        7, SampleShipApprovalDecision(comment='运费偏高'), request(), manager(), session,
    )
    approval = row.source_context['ship_approval']
    assert approval['status'] == 'rejected'
    assert approval['comment'] == '运费偏高'
    assert row.status == 'pending'
    assert audit.call_args.kwargs['action'] == 'sample_ship_reject'


@pytest.mark.anyio
async def test_ship_requires_approved_for_existing_product(monkeypatch):
    row = sample(status='approved')
    monkeypatch.setattr(router.svc, 'sample_is_custom', AsyncMock(return_value=False))
    with pytest.raises(AppError) as caught:
        await router._require_ship_approved(SimpleNamespace(), row)
    assert '主管审批' in caught.value.message

    row.source_context['ship_approval'] = {'status': 'approved', 'shipping_fee': '25.50'}
    approval = await router._require_ship_approved(SimpleNamespace(), row)
    assert approval['shipping_fee'] == '25.50'


@pytest.mark.anyio
async def test_custom_ship_still_uses_inquiry_approval(monkeypatch):
    from app.modules.oa import business_gates

    gate = AsyncMock(return_value=SimpleNamespace(status='approved'))
    monkeypatch.setattr(business_gates, 'require_sample_approved', gate)
    monkeypatch.setattr(router.svc, 'sample_is_custom', AsyncMock(return_value=True))
    row = sample(status='approved')
    assert await router._require_ship_approved(SimpleNamespace(), row) is None
    gate.assert_awaited_once()


@pytest.mark.anyio
async def test_list_filter_finds_pending_ship_approvals(db_session):
    """主管能按「寄样待审批」筛出待批的单子（JSON 状态筛选在 SQL 层生效）。"""
    from conftest import SyncSessionAsAsync, make_user
    from app.modules.sample.model import SampleItem, SampleShipment

    for model in (SampleRequest, SampleItem, SampleShipment):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([
        SampleRequest(id=21, owner_id=1, status='pending',
                      source_context={'ship_approval': {'status': 'pending'}}),
        SampleRequest(id=22, owner_id=1, status='pending',
                      source_context={'ship_approval': {'status': 'approved'}}),
    ])
    db_session.commit()
    user = make_user(1, permissions=('sample:view', 'sample:manage'), data_scope='all')
    result = await router.list_samples(
        keyword=None, status=None, customer_id=None, opportunity_id=None, owner_id=None,
        include_history=False, include_deleted=False, ship_approval_status='pending',
        page=1, page_size=20, user=user, session=SyncSessionAsAsync(db_session),
    )
    assert [row['id'] for row in result['data']['items']] == [21]
