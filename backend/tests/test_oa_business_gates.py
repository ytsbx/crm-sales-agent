import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy.orm import Session
from app.core.errors import AppError
from app.core.config import settings
from app.modules.oa import business_gates as gates, client
from app.modules.dingtalk.model import OaInstance
from app.modules.quote import service as quotes
from app.modules.contract import service as contracts
from app.modules.oa import service as oa_service
from tests.test_oa_sample_inquiry_flow import _engine
from tests.conftest import SyncSessionAsAsync


@pytest.mark.parametrize('kind', ['inquiry','sample','contract'])
@pytest.mark.parametrize('status', [None,'pending','submitting','needs_review','rejected','withdrawn','approved'])
def test_latest_approval_required_for_formal_actions(kind,status):
    with Session(_engine()) as raw:
        if status:
            raw.add(OaInstance(oa_type=kind,inquiry_id=77,inquiry_version=1,status=status,idempotency_key='test-'+kind,
                              submit_round=1,created_at=datetime.now(UTC)))
            raw.commit()
        action=gates.require_latest_approved(SyncSessionAsAsync(raw),kind=kind,business_id=77)
        if status=='approved':assert asyncio.run(action).status=='approved'
        else:
            with pytest.raises(AppError,match='审批尚未通过'):asyncio.run(action)


def test_quote_guard_is_called_by_shared_formal_cost_boundary(monkeypatch):
    items=[SimpleNamespace(inquiry_id=77)]
    monkeypatch.setattr(quotes,'version_items',AsyncMock(return_value=items))
    guard=AsyncMock(side_effect=AppError(40901,'核价未通过',422))
    monkeypatch.setattr(gates,'require_inquiry_items_approved',guard)
    with pytest.raises(AppError,match='核价未通过'):
        asyncio.run(quotes.ensure_items_cost_complete(None,version_id=7))
    guard.assert_awaited_once_with(None,items)


def test_standard_sku_does_not_need_inquiry_approval():
    session=SimpleNamespace(execute=AsyncMock())
    asyncio.run(gates.require_inquiry_items_approved(session,[SimpleNamespace(inquiry_id=None)]))
    session.execute.assert_not_awaited()


def test_latest_pending_round_overrides_old_approved_round():
    with Session(_engine()) as raw:
        for round_no,status in [(1,'approved'),(2,'pending')]:
            raw.add(OaInstance(oa_type='sample',inquiry_id=77,inquiry_version=1,status=status,idempotency_key='round-'+str(round_no),submit_round=round_no,created_at=datetime.now(UTC)))
        raw.commit()
        with pytest.raises(AppError):
            asyncio.run(gates.require_sample_approved(SyncSessionAsAsync(raw),SimpleNamespace(id=77,version=1,source_context={'dingtalk_oa':{'mode':'standalone'}})))


def test_contract_without_approval_cannot_sign(monkeypatch):
    doc=SimpleNamespace(id=7,status='draft',filled_data={'_original_file_id':1})
    monkeypatch.setattr(contracts,'get_doc_or_404',AsyncMock(return_value=doc))
    monkeypatch.setattr(contracts,'_ensure_signable_source',AsyncMock())
    monkeypatch.setattr(oa_service,'latest_contract_row',AsyncMock(return_value=None))
    with pytest.raises(AppError,match='审批通过'):
        asyncio.run(contracts.sign_document(None,doc,file_id=2,note=None))


def test_unsubmitted_sample_is_blocked_when_self_oa_is_configured(monkeypatch):
    monkeypatch.setattr(client, 'configured', lambda: True)
    monkeypatch.setattr(settings, 'oa_sample_template_code', 'test-sample')
    with Session(_engine()) as raw:
        sample = SimpleNamespace(id=77, version=1, source_context=None)
        with pytest.raises(AppError, match='打样审批尚未通过'):
            asyncio.run(gates.require_sample_approved(SyncSessionAsAsync(raw), sample))


def test_approval_of_old_sample_version_does_not_unlock_new_version():
    with Session(_engine()) as raw:
        raw.add(OaInstance(oa_type='sample', inquiry_id=77, inquiry_version=1,
                          status='approved', idempotency_key='old-version', submit_round=1,
                          created_at=datetime.now(UTC)))
        raw.commit()
        sample = SimpleNamespace(id=77, version=2, source_context={'dingtalk_oa': {'mode': 'standalone', 'status': 'approved'}})
        with pytest.raises(AppError):
            asyncio.run(gates.require_sample_approved(SyncSessionAsAsync(raw), sample))


@pytest.mark.parametrize('overall,milestone,allowed', [
    ('pending','approved',True), ('approved','pending',False),
    ('pending','pending',False), ('withdrawn','approved',False), ('rejected','approved',False),
])
def test_sample_actions_use_review_milestone_not_whole_approval(overall, milestone, allowed):
    with Session(_engine()) as raw:
        raw.add(OaInstance(oa_type='sample', inquiry_id=77, inquiry_version=1, status=overall,
            result={'business_stages': {'sample': {'status': milestone}}},
            idempotency_key='sample-stage', submit_round=1, created_at=datetime.now(UTC)))
        raw.commit()
        sample = SimpleNamespace(id=77, version=1, source_context={'dingtalk_oa': {'mode':'standalone'}})
        action = gates.require_sample_approved(SyncSessionAsAsync(raw), sample)
        if allowed:
            assert asyncio.run(action).status == 'pending'
        else:
            with pytest.raises(AppError): asyncio.run(action)
