import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
import pytest
from sqlalchemy.orm import Session
from app.core.errors import AppError
from app.modules.oa import business_gates as gates, client
from app.modules.dingtalk.model import OaInstance
from app.modules.inquiry import service
from tests.test_oa_sample_inquiry_flow import _engine
from tests.conftest import SyncSessionAsAsync
from test_batch7_cost_lifecycle import case  # noqa: F401


@pytest.mark.parametrize('overall,milestone,allowed',[
    ('pending','approved',True),('approved','approved',True),('pending','pending',False),
    ('approved','rejected',False),('withdrawn','approved',False),('rejected','approved',False),
    ('submitting','approved',False),('needs_review','approved',False),
    ('approved',None,False),('pending',None,False)])
def test_formal_quote_uses_pricing_phase_not_whole_request(monkeypatch,overall,milestone,allowed):
    monkeypatch.setattr(client,'configured',lambda:False)
    with Session(_engine()) as raw:
        result={'business_stages':{'quotation':{'status':milestone}}} if milestone else {}
        raw.add(OaInstance(oa_type='inquiry',inquiry_id=1,inquiry_version=1,idempotency_key='stage',submit_round=1,
            status=overall,result=result,created_at=datetime.now(UTC)))
        raw.commit()
        action=gates.require_inquiry_items_approved(SyncSessionAsAsync(raw),[Obj(inquiry_id=1)])
        if allowed:asyncio.run(action)
        else:
            with pytest.raises(AppError,match='核价/报价复核'):asyncio.run(action)


def test_existing_quote_blocks_second_creation_before_business_writes(monkeypatch):
    inquiry=Obj(id=1,root_id=None,opportunity_id=None,deleted_at=None,status="open",superseded_at=None)
    session=Obj(execute=AsyncMock(return_value=Obj(scalar_one=lambda:inquiry)), refresh=AsyncMock())
    monkeypatch.setattr(service,'linked_quotes_for',AsyncMock(return_value={1:[{'id':8,'quote_no':'Q-8'}]}))
    validate=AsyncMock()
    monkeypatch.setattr(service,'validate_links',validate)
    with pytest.raises(AppError,match='Q-8'):
        asyncio.run(service.create_quote_from_inquiry(session,inquiry=inquiry,user=None,unit_cost=1,quoted_price=2))
    validate.assert_not_awaited()


def test_draft_preparation_does_not_wait_for_oa(monkeypatch):
    inquiry=Obj(id=1,root_id=None,opportunity_id=None,customer_id=1,contact_id=None,
                deleted_at=None,status="open",superseded_at=None,version=1)
    session=Obj(execute=AsyncMock(side_effect=[Obj(scalar_one=lambda:inquiry),Obj(first=lambda:None),
                Obj(scalars=lambda:Obj(all=lambda:[inquiry]))]),refresh=AsyncMock())
    monkeypatch.setattr(service,'linked_quotes_for',AsyncMock(return_value={}))
    validate=AsyncMock(side_effect=RuntimeError('reached existing draft validation'))
    monkeypatch.setattr(service,'validate_links',validate)
    with pytest.raises(RuntimeError,match='existing draft validation'):
        asyncio.run(service.create_quote_from_inquiry(session,inquiry=inquiry,user=None,unit_cost=1,quoted_price=2))
    validate.assert_awaited_once()

@pytest.mark.anyio
async def test_quote_links_include_history_once_and_ignore_deleted_quotes(case):
    from test_batch7_cost_lifecycle import build
    from app.modules.quote import service as quotes
    raw, session, quote, version = case
    await build(raw, session, version, cost='complete')
    item = (await quotes.version_items(session, version.id))[0]
    item.inquiry_id = 42
    await session.flush()
    links = await service.linked_quotes_for(session, [42])
    assert links[42] == [{'id':quote.id, 'quote_no':quote.quote_no}]
    quote.deleted_at = datetime.now(UTC)
    await session.flush()
    assert await service.linked_quotes_for(session, [42]) == {}


@pytest.mark.anyio
async def test_unreviewed_quote_document_is_watermarked_draft(monkeypatch):
    from app.modules.bizdoc import service as docs
    from app.modules.quote import service as quotes
    from app.core.errors import ErrorCode
    version=Obj(approval_status='approved')
    session=Obj(get=AsyncMock(return_value=version))
    monkeypatch.setattr(docs,'build_quote_doc',AsyncMock(return_value={'owner_id':1,'source':{}}))
    monkeypatch.setattr(docs,'ensure_in_scope',AsyncMock())
    monkeypatch.setattr(docs,'current_template',AsyncMock(return_value=Obj(id=1)))
    monkeypatch.setattr(quotes,'ensure_items_cost_complete',AsyncMock())
    monkeypatch.setattr(quotes,'version_items',AsyncMock(return_value=[Obj(inquiry_id=1)]))
    monkeypatch.setattr(quotes,'master_confirmation_problems',AsyncMock(return_value=[]))
    monkeypatch.setattr(quotes,'version_charges',AsyncMock(return_value=[]))
    monkeypatch.setattr(quotes,'quote_freight_unconfirmed_reason',lambda _:None)
    monkeypatch.setattr(gates,'require_inquiry_items_approved',AsyncMock(side_effect=AppError(ErrorCode.STATUS_NOT_ALLOWED,'报价阶段未通过',422)))
    persist=AsyncMock(return_value=Obj(status='draft'))
    monkeypatch.setattr(docs,'_persist',persist)
    result=await docs.generate_quote_doc(session,quote_version_id=1,user=Obj(id=1))
    assert result.status=='draft'
    assert '报价阶段未通过' in persist.call_args.kwargs['draft_reason']
    quotes.ensure_items_cost_complete.assert_awaited_once_with(session,version_id=1,check_inquiry_approval=False)

@pytest.mark.anyio
async def test_actual_quote_draft_can_be_created_without_oa_and_not_duplicated(case):
    from decimal import Decimal
    from app.modules.inquiry.model import CustomInquiry
    from test_batch7_cost_lifecycle import USER
    raw, session, old_quote, old_version = case
    from app.modules.opportunity.model import OpportunityItem
    OpportunityItem.__table__.create(raw.get_bind(), checkfirst=True)
    CustomInquiry.__table__.create(raw.get_bind(), checkfirst=True)
    inquiry=CustomInquiry(id=42,title='无需先走OA的内部草稿',inquiry_no='TEST-XQ',
        description='内部准备',quantity=10,customer_id=1,opportunity_id=1,created_by=1)
    raw.add(inquiry)
    await session.flush()
    result=await service.create_quote_from_inquiry(session,inquiry=inquiry,user=USER,
        unit_cost=Decimal('5'),quoted_price=Decimal('10'))
    assert result['quote'].status=='draft'
    assert result['version'].approval_status=='not_submitted'
    assert result['item'].inquiry_id==42
    with pytest.raises(AppError,match='已关联报价'):
        await service.create_quote_from_inquiry(session,inquiry=inquiry,user=USER,
            unit_cost=Decimal('6'),quoted_price=Decimal('12'))
    inquiry.superseded_at = datetime.now(UTC)
    await session.flush()
    with pytest.raises(AppError, match="最新询价"):
        await service.lock_quote_source(session, inquiry, quote_id=old_quote.id)
    assert (await service.lock_quote_source(session, inquiry, quote_id=result["quote"].id)).id == inquiry.id
    inquiry.status = "archived"
    await session.flush()
    with pytest.raises(AppError, match="已归档"):
        await service.lock_quote_source(session, inquiry)
