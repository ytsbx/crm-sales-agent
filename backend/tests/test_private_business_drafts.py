"""Private drafts are scoped by the creating account even for global users."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.draft_visibility import draft_scope, ensure_draft_access
from app.core.errors import AppError
from app.modules.audit_router import _draft_audit_scope
from app.modules.bizdoc.model import BizDoc
from app.modules.cases.model import SalesCase
from app.modules.contract.model import ContractDocument
from app.modules.dingtalk.model import OaInstance
from app.modules.inquiry.model import CustomInquiry
from app.modules.order.model import OrderDraft
from app.modules.quote.model import Quote, QuoteCharge, QuoteItem
from app.modules.sample.model import SampleItem, SampleRequest
from tests.conftest import SyncSessionAsAsync


@pytest.mark.anyio
async def test_drafts_creator_and_other_account_with_real_list_and_direct_predicates(db_session):
    bind = db_session.get_bind()
    for model in (AuditLog, BizDoc, ContractDocument, SalesCase, CustomInquiry, OaInstance,
                  OrderDraft, QuoteItem, QuoteCharge, SampleItem, SampleRequest):
        model.__table__.create(bind, checkfirst=True)
    now = datetime.now(UTC)
    quote = Quote(id=1, quote_no="Q-DRAFT", customer_id=1, owner_id=202, created_by=101, status="draft")
    order = OrderDraft(id=1, customer_id=1, source_context={}, owner_id=202, created_by=101,
                       request_key="private-draft-test", request_hash="test", status="draft")
    inquiry = CustomInquiry(id=1, title="需求", created_by=101, extra={"oa_draft": {"fields": {}}})
    sample = SampleRequest(id=1, owner_id=202, created_by=101, status="pending",
                           source_context={"dingtalk_oa": {"mode": "standalone", "fields": {}}})
    contract = ContractDocument(id=1, doc_no="C-DRAFT", title="合同", customer_id=1,
                                content_snapshot="隔离内容", created_by=101, status="draft", created_at=now)
    case = SalesCase(id=1, author_id=101, title="案例草稿", status="draft", created_at=now)
    db_session.add_all([quote, order, inquiry, sample, contract, case,
                        AuditLog(operator_id=101, business_type="sample", business_id=1, action="create")])
    db_session.add_all([
        AuditLog(operator_id=101, business_type=kind, business_id=inquiry.id, action="update")
        for kind in ("inquiry", "custom_inquiry")
    ])
    db_session.flush()
    session = SyncSessionAsAsync(db_session)
    for row in (quote, order, inquiry, sample, contract, case):
        model = type(row)
        for actor in (202, 999):
            assert (await session.scalar(select(model.id).where(draft_scope(model, actor)))) is None
            with pytest.raises(AppError) as blocked:
                await ensure_draft_access(session, model, row.id, actor)
            assert blocked.value.http_status == 404
        assert await session.scalar(select(model.id).where(draft_scope(model, 101))) == row.id
        assert await ensure_draft_access(session, model, row.id, 101)

    assert await session.scalar(select(AuditLog.id).where(_draft_audit_scope(202))) is None
    assert set(await session.scalars(select(AuditLog.business_type).where(_draft_audit_scope(101)))) == {
        "sample", "inquiry", "custom_inquiry",
    }
    quote.status = "pending_approval"
    case.status = "pending_review"
    order.status = "converted"
    sample.source_context = {"dingtalk_oa": {"mode": "standalone", "submitted": True}}
    for kind in ("inquiry", "contract"):
        db_session.add(OaInstance(oa_type=kind, inquiry_id=1, inquiry_version=1, status="pending",
                                  idempotency_key=f"privacy-{kind}", submit_round=1,
                                  instance_id=f"real-oa-{kind}", created_at=now))
    db_session.flush()
    for row in (quote, order, inquiry, sample, contract, case):
        model = type(row)
        assert await session.scalar(select(model.id).where(draft_scope(model, 202))) == row.id
        assert not await ensure_draft_access(session, model, row.id, 202)
