"""钉钉已拒绝申请可回收，但审批留痕和制作中的单据仍受保护。"""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.sample import router
from app.modules.sample.dingtalk import ensure_editable
from app.modules.sample.model import SampleRequest, can_delete_sample


def sample(**changes):
    values = dict(id=7, status='rejected', deleted_at=None, made_at=None, shipped_at=None,
                  source_context={'dingtalk_oa': {'submitted': True, 'instance_id': 'original-approval'}})
    return SampleRequest(**(values | changes))


@pytest.mark.parametrize('status,submitted,allowed', [
    ('pending', False, True), ('pending', True, False),
    ('rejected', False, True), ('rejected', True, True),
    ('approved', True, False), ('shipped', True, False), ('signed', True, False),
])
def test_delete_policy(status, submitted, allowed):
    row = sample(status=status, source_context={'dingtalk_oa': {'submitted': submitted}})
    assert can_delete_sample(row) is allowed


@pytest.mark.parametrize('field', ['deleted_at', 'made_at', 'shipped_at'])
def test_rejected_with_business_history_stays_protected(field):
    assert not can_delete_sample(sample(**{field: datetime.now(UTC)}))


@pytest.mark.anyio
async def test_rejected_submitted_soft_delete_keeps_approval(monkeypatch):
    row = sample()
    original = row.source_context.copy()
    session = SimpleNamespace(commit=AsyncMock())
    audit = AsyncMock()
    monkeypatch.setattr(router.svc, 'get_visible_or_404', AsyncMock(return_value=row))
    monkeypatch.setattr(router.svc, 'detail', AsyncMock(return_value={'id': row.id}))
    monkeypatch.setattr(router, 'write_audit', audit)
    request = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})
    await router.delete_sample(row.id, request, SimpleNamespace(id=1), session)
    assert row.deleted_at is not None
    assert row.source_context == original
    assert row.status == 'rejected'
    assert audit.call_args.kwargs['action'] == 'delete'
    session.commit.assert_awaited_once()
    # 允许回收不会放开对已提交审批内容的编辑。
    with pytest.raises(AppError):
        ensure_editable(row)


@pytest.mark.anyio
async def test_pending_submitted_delete_is_rejected(monkeypatch):
    row = sample(status='pending')
    session = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(router.svc, 'get_visible_or_404', AsyncMock(return_value=row))
    with pytest.raises(AppError, match='不能删除'):
        await router.delete_sample(row.id, None, SimpleNamespace(id=1), session)
    assert row.deleted_at is None
    session.commit.assert_not_awaited()
