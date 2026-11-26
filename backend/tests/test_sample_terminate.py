"""K3：打样终止。

口径（主人 2026-11-25 拍板）：
- 业务员（sample:manage）就可以终止，前端二次确认；
- 只改状态 + 原因 + 审计，不生成费用/损失记录；
- 已寄样/已签收也允许终止，但制作与寄送事实原样保留。
"""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.sample import router
from app.modules.sample.model import SampleRequest
from app.modules.sample.schema import SampleTerminate
from app.modules.sample.service import ensure_transition as _ensure_transition


def request() -> Request:
    return Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


def sample(**changes) -> SampleRequest:
    values = dict(
        id=7, status='approved', version=1, customer_id=1, owner_id=1,
        made_at=datetime(2026, 11, 20, tzinfo=UTC),
        shipped_at=None, signed_at=None,
        source_context={'dingtalk_oa': {'submitted': True}},
    )
    return SampleRequest(**(values | changes))


def _setup(monkeypatch, row):
    session = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock())
    audit = AsyncMock()
    monkeypatch.setattr(router.svc, 'get_visible_or_404', AsyncMock(return_value=row))
    monkeypatch.setattr(router.svc, 'detail', AsyncMock(return_value={'id': row.id}))
    monkeypatch.setattr(router.svc, 'record_opportunity_touch', AsyncMock())
    monkeypatch.setattr(router, 'write_audit', audit)
    return session, audit


@pytest.mark.anyio
async def test_terminate_keeps_made_and_shipping_facts(monkeypatch):
    row = sample(status='shipped', shipped_at=datetime(2026, 11, 21, tzinfo=UTC))
    session, audit = _setup(monkeypatch, row)

    result = await router.terminate_sample(
        7, SampleTerminate(reason='  客户项目取消  '), request(),
        SimpleNamespace(id=1), session,
    )

    assert result['data'] == {'id': 7}
    assert row.status == 'terminated'
    assert row.source_context['termination']['reason'] == '客户项目取消'
    assert row.source_context['termination']['from_status'] == 'shipped'
    # 制作与寄送事实不动
    assert row.made_at == datetime(2026, 11, 20, tzinfo=UTC)
    assert row.shipped_at == datetime(2026, 11, 21, tzinfo=UTC)
    assert audit.call_args.kwargs['action'] == 'terminate'
    assert audit.call_args.kwargs['before'] == {'status': 'shipped'}
    session.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize('status', ['pending', 'rejected', 'terminated'])
async def test_only_approved_shipped_signed_can_be_terminated(monkeypatch, status):
    row = sample(status=status, made_at=None)
    session, audit = _setup(monkeypatch, row)
    with pytest.raises(AppError) as caught:
        await router.terminate_sample(
            7, SampleTerminate(reason='客户取消'), request(), SimpleNamespace(id=1), session,
        )
    assert caught.value.http_status == 422
    assert '不能终止' in caught.value.message
    assert row.status == status
    audit.assert_not_awaited()


@pytest.mark.anyio
async def test_blank_reason_is_rejected(monkeypatch):
    row = sample()
    session, _ = _setup(monkeypatch, row)
    with pytest.raises(AppError) as caught:
        await router.terminate_sample(
            7, SampleTerminate(reason='   '), request(), SimpleNamespace(id=1), session,
        )
    assert '原因' in caught.value.message
    assert row.status == 'approved'


def test_terminated_is_terminal():
    with pytest.raises(AppError):
        _ensure_transition('terminated', 'shipped')
    # 允许的三个入口
    for status in ('approved', 'shipped', 'signed'):
        _ensure_transition(status, 'terminated')


def test_terminated_sample_is_closed_to_edits():
    from app.modules.file.access import sample_write_lock_label_for

    row = sample(status='terminated')
    with pytest.raises(AppError) as caught:
        router._reject_terminated(row)
    assert '不能再修改' in caught.value.message
    assert sample_write_lock_label_for(row) is not None
