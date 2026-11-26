"""P1：原询价审批结果必须回写到关联样品单；无 SKU 的新品必须按定制处理。

1. 「沿用原询价流程」原先只记关联、不回写状态：原询价通过后样品单仍是 pending，
   不能制作、不能编辑、不能删除。现在关联时已通过就直接批准，后续同步也会回写。
2. 只填产品名称、没有 SKU 的新品原先被判成现有产品，绕过同序 OA 审批直接走寄样。
"""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.oa import inquiry as inquiry_oa
from app.modules.sample import dingtalk as sample_approval
from app.modules.sample import service as sample_service
from app.modules.sample.model import SampleItem, SampleRequest
from conftest import SyncSessionAsAsync, make_user


def request() -> Request:
    return Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


def _tables(db):
    for model in (SampleRequest, SampleItem, AuditLog):
        model.__table__.create(db.get_bind(), checkfirst=True)


def _sample(status='pending', **meta):
    return SampleRequest(
        id=7, status=status, owner_id=1, version=1, customer_id=None, opportunity_id=None,
        made_at=None, shipped_at=None,
        source_context={'dingtalk_oa': {'mode': 'inquiry', 'source_oa_id': 5,
                                       'submitted': True, **meta}},
        created_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_inquiry_approved_writeback_approves_pending_sample(db_session):
    _tables(db_session)
    sample = _sample()
    db_session.add(sample)
    db_session.commit()

    await inquiry_oa._sync_linked_samples(
        SyncSessionAsAsync(db_session), SimpleNamespace(id=5, status='approved')
    )

    assert sample.status == 'approved'
    assert sample.approved_at is not None
    audit = db_session.query(AuditLog).filter_by(action='sample_oa_status', business_id=7).one()
    assert audit.before_data == {'status': 'pending'}
    assert audit.after_data['status'] == 'approved'


@pytest.mark.anyio
async def test_inquiry_rejected_writeback_reopens_sample(db_session):
    _tables(db_session)
    sample = _sample()
    db_session.add(sample)
    db_session.commit()

    await inquiry_oa._sync_linked_samples(
        SyncSessionAsAsync(db_session), SimpleNamespace(id=5, status='rejected')
    )

    assert sample.status == 'rejected'
    assert '原询价审批已拒绝' in sample.reject_reason
    # 清掉 submitted，才能修改资料或重新关联审批
    assert sample.source_context['dingtalk_oa']['submitted'] is False


@pytest.mark.anyio
async def test_linked_approved_inquiry_approves_sample_immediately(monkeypatch):
    sample = _sample(status='pending', submitted=False)
    session = SimpleNamespace(
        commit=AsyncMock(), flush=AsyncMock(),
        get=AsyncMock(return_value=SimpleNamespace(id=5, oa_type='inquiry', status='approved')),
    )
    monkeypatch.setattr(sample_service, 'get_visible_or_404', AsyncMock(return_value=sample))
    monkeypatch.setattr(sample_approval, 'inquiry_candidates', AsyncMock(return_value=[{'id': 5}]))
    monkeypatch.setattr(sample_approval, 'self_oa_available', lambda: False)
    monkeypatch.setattr(sample_approval, 'write_audit', AsyncMock())
    monkeypatch.setattr(sample_service, 'record_opportunity_touch', AsyncMock())

    await sample_approval.submit(
        7, sample_approval.Draft(mode='inquiry', source_oa_id=5),
        request(), make_user(1, permissions=('sample:manage',)), session,
    )

    assert sample.status == 'approved'
    assert sample.approved_at is not None


@pytest.mark.anyio
async def test_cannot_link_a_rejected_inquiry(monkeypatch):
    sample = _sample(status='pending', submitted=False)
    session = SimpleNamespace(
        commit=AsyncMock(), flush=AsyncMock(),
        get=AsyncMock(return_value=SimpleNamespace(id=5, oa_type='inquiry', status='rejected')),
    )
    monkeypatch.setattr(sample_service, 'get_visible_or_404', AsyncMock(return_value=sample))
    monkeypatch.setattr(sample_approval, 'inquiry_candidates', AsyncMock(return_value=[{'id': 5}]))
    monkeypatch.setattr(sample_approval, 'self_oa_available', lambda: False)
    monkeypatch.setattr(sample_approval, 'write_audit', AsyncMock())

    with pytest.raises(AppError) as caught:
        await sample_approval.submit(
            7, sample_approval.Draft(mode='inquiry', source_oa_id=5),
            request(), make_user(1, permissions=('sample:manage',)), session,
        )
    assert '未通过' in caught.value.message
    assert sample.status == 'pending'


@pytest.mark.anyio
async def test_item_without_sku_is_treated_as_custom(db_session):
    _tables(db_session)
    sample = SampleRequest(id=7, status='pending', owner_id=1, created_at=datetime.now(UTC))
    db_session.add(sample)
    db_session.add(SampleItem(
        id=1, sample_request_id=7, sku_id=None, inquiry_id=None, item_name='全新产品', quantity=1,
    ))
    db_session.commit()
    assert await sample_service.sample_is_custom(SyncSessionAsAsync(db_session), sample) is True

    item = db_session.get(SampleItem, 1)
    item.sku_id = 3
    db_session.commit()
    assert await sample_service.sample_is_custom(SyncSessionAsAsync(db_session), sample) is False
