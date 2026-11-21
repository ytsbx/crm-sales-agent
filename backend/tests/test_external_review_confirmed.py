from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.csvio import csv_bytes, parse_csv_bytes
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.analytics.service import pricing_stats, team_summary
from app.modules.approval.model import ApprovalInstance
from app.modules.cases.model import CaseEvidence, SalesCase
from app.modules.cases.router import delete_case
from app.modules.cases.service import get_case_detail, list_cases, review_case
from app.modules.customer.model import Customer
from app.modules.erp.collection import resolve_mapping
from app.modules.inquiry.model import CustomInquiry
from app.modules.inquiry.router import delete_inquiry, list_inquiries, update_inquiry
from app.modules.inquiry.schema import CustomInquiryUpdate
from app.modules.integration.model import ExternalObjectMapping
from app.modules.integration.vocab import MATCH_CONFLICT, MATCH_MATCHED
from app.modules.lead.io_router import restore_lead
from app.modules.lead.model import Lead, LeadAssignment
from app.modules.lead.router import discard_lead
from app.modules.lead.schema import LeadDiscard
from app.modules.opportunity.model import Opportunity, OpportunityItem, OpportunityStage, OpportunityStageHistory
from app.modules.opportunity.router import list_opportunities
from app.modules.opportunity.service import recommend_products
from app.modules.order.model import SalesOrder, SalesOrderItem
from app.modules.order.schema import MilestoneUpdate
from app.modules.payment.model import ReceivablePlan
from app.modules.product.model import Product, Sku
from app.modules.product.schema import ProductUpdate, SkuUpdate
from app.modules.product_insight.model import ProductInsight, ProductInsightRound
from app.modules.product_insight.router import convert_insight, get_insight, list_insights, review_insight
from app.modules.product_insight.schema import InsightReview
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion
from app.modules.sample.oa_content import validated_sample_details
from app.modules.sample.schema import SampleUpdate
from app.modules.settings.model import SystemSetting, TaskRule
from app.modules.settings.router import upsert_setting
from app.modules.settings.schema import NumberingRuleCreate, PublicPoolRuleInput, SettingInput, TaskRuleInput
from app.modules.settings.service import run_auto_tasks, validate_setting_value, validate_task_rule
from app.modules.task.model import HANDOVER_SOURCE, Task
from app.modules.task.router import batch_handover_tasks, get_task, handover_candidates, list_tasks
from app.modules.task.schema import TaskBatchHandover
from app.modules.user.model import User
from tests.conftest import SyncSessionAsAsync, make_user


@pytest.mark.anyio
async def test_quote_opportunity_search_finds_old_open_matches_within_data_scope(db_session):
    OpportunityItem.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([
        Customer(id=1, name="远处客户"),
        Customer(id=2, name="普通客户"),
        OpportunityStage(id=1, code="initial", name="初始"),
    ])
    db_session.flush()
    db_session.add_all([
        Opportunity(id=i, customer_id=1 if i <= 3 else 2, title=f"商机 {i}", stage_id=1,
                    owner_id=102 if i == 2 else 101, status="loss" if i == 3 else "open")
        for i in range(1, 105)
    ])
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    user = make_user(101, permissions={"opportunity:view"})
    params = {"user": user, "session": session, "status": "open", "page_size": 20, "page": 1}
    first_page = (await list_opportunities(**params))["data"]
    assert first_page["total"] == 102
    assert all(item["id"] != 1 for item in first_page["items"])
    assert (await list_opportunities(keyword="远处客户", **params))["data"]["total"] == 0
    matches = (await list_opportunities(keyword="远处客户", search_customer_name=True, **params))["data"]
    assert matches["total"] == 1
    assert [item["id"] for item in matches["items"]] == [1]


@pytest.mark.anyio
async def test_insight_original_inquiry_is_archived_and_remains_linked(db_session):
    from app.modules.dingtalk.model import OaInstance

    for model in (AuditLog, CustomInquiry, ProductInsight, QuoteItem, OaInstance):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([
        ProductInsight(id=1, owner_id=101, title="已转洞察", status="converted", converted_inquiry_id=10),
        CustomInquiry(id=10, source_insight_id=1, created_by=101, title="洞察原询价"),
        CustomInquiry(id=11, created_by=101, title="其他询价"),
    ])
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    user = make_user(permissions=("quote:view", "quote:manage", "product:manage"))
    request = Request({"type": "http", "headers": []})

    with pytest.raises(AppError, match="归档") as blocked:
        await delete_inquiry(10, request, user, session)
    assert blocked.value.http_status == 409
    archived = await update_inquiry(10, CustomInquiryUpdate(status="archived"), request, user, session)
    assert archived["data"]["status"] == "archived"
    rows = await list_inquiries(page=1, page_size=20, user=user, session=session, inquiry_id=10)
    assert [(row["id"], row["source_insight_id"]) for row in rows["data"]["items"]] == [(10, 1)]
    assert (await convert_insight(1, request, user=user, session=session))["data"]["inquiry_id"] == 10
    await delete_inquiry(11, request, user, session)
    assert db_session.get(CustomInquiry, 11).deleted_at is not None

    # 存量历史已删时不给成功回执。
    db_session.get(CustomInquiry, 10).deleted_at = datetime.now(UTC)
    db_session.commit()
    with pytest.raises(AppError, match="原询价已删除"):
        await convert_insight(1, request, user=user, session=session)


@pytest.mark.anyio
async def test_insight_edit_flags_match_existing_public_pool_write_scope(db_session):
    for model in (ProductInsight, ProductInsightRound):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([
        ProductInsight(id=1, title="公海洞察", created_by=202),
        ProductInsight(id=2, title="本人洞察", created_by=101, owner_id=101),
        ProductInsight(id=3, title="别人的洞察", created_by=202, owner_id=202),
    ])
    db_session.flush()
    session = SyncSessionAsAsync(db_session)
    reviewer = make_user(permissions=("product:view", "product:review", "product:manage"))
    rows = await list_insights(page=1, page_size=20, user=reviewer, session=session)
    assert {item["id"]: item["can_edit"] for item in rows["data"]["items"]} == {1: True, 2: True, 3: False}
    assert (await get_insight(1, reviewer, session))["data"]["can_edit"] is True
    assert (await get_insight(3, reviewer, session))["data"]["can_edit"] is False


@pytest.mark.anyio
async def test_team_summary_reports_disabled_employee_subtotal_in_scope(db_session):
    for model in (ApprovalInstance, Task, SystemSetting, OpportunityStageHistory):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    now = datetime.now(UTC)
    db_session.add_all([
        User(id=101, name="在岗", username="active", password_hash="fixture", department_id=1),
        User(id=102, name="停用", username="disabled", password_hash="fixture", department_id=1, status="disabled"),
        User(id=103, name="其他部门", username="other", password_hash="fixture", department_id=2, status="disabled"),
        Task(title="在岗待办", owner_id=101),
        Task(title="需交接的逾期待办", owner_id=102, due_at=now - timedelta(days=1)),
        Task(title="已完成无需交接", owner_id=102, status="done"),
        Task(title="范围外待办", owner_id=103),
        Task(title="无负责人待办"),
        OpportunityStage(id=1, code="win", name="赢单", is_win=True),
        Opportunity(id=1, title="本月成交", customer_id=1, stage_id=1, owner_id=102,
                    expected_amount=Decimal("100.25"), status="win"),
        OpportunityStageHistory(opportunity_id=1, to_stage_id=1, entered_at=now),
    ])
    db_session.flush()
    session = SyncSessionAsAsync(db_session)
    user = make_user(data_scope="department")
    user.department_id = 1
    result = await team_summary(session, user)
    assert result["member_count"] == 1
    assert result["team_task_count"] == 2 and result["team_overdue_count"] == 1
    assert result["team_won_amount_this_month"] == 100.25
    assert result["disabled_members"] == {
        "todo_count": 1, "overdue_count": 1, "won_amount_this_month": 100.25,
    }
    company_result = await team_summary(session, make_user(data_scope="all"))
    assert company_result["team_task_count"] == 4
    assert company_result["disabled_members"]["todo_count"] == 2

    db_session.get(User, 102).status = "active"
    result = await team_summary(session, user)
    assert result["disabled_members"] is None
    assert result["team_task_count"] == 2
    assert (await team_summary(session, make_user()))["is_team_view"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("assigned", [True, False])
async def test_discard_and_restore_keep_the_lead_owner_without_recording_a_release(db_session, assigned):
    for model in (LeadAssignment, AuditLog):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    user = make_user(permissions=("lead:assign",))
    owner_id = user.id if assigned else None
    initial_status = "assigned" if assigned else "pending"
    lead = Lead(id=1, name="待废弃线索", owner_id=owner_id, status=initial_status)
    db_session.add_all([lead, User(
        id=user.id, username="lead-recovery", name="线索经办人", password_hash="test",
    )])
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    request = Request({"type": "http", "headers": []})

    await discard_lead(lead.id, LeadDiscard(reason="重复线索"), request, user, session)
    assert lead.deleted_at is not None
    assert lead.owner_id == owner_id
    assert db_session.scalar(select(LeadAssignment)) is None
    discard_audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "discard"))
    assert discard_audit.after_data == {"reason": "重复线索", "owner_id": owner_id}

    restored = await restore_lead(lead.id, request, user, session)
    assert lead.deleted_at is None
    assert restored["data"]["owner_id"] == owner_id
    assert restored["data"]["status"] == initial_status
    assert restored["data"]["invalid_reason"] is None
    restore_audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "restore"))
    assert restore_audit.before_data["invalid_reason"] == "重复线索"

    if not assigned:
        from app.modules.lead.service import claim_lead
        row, changed = await claim_lead(session, user, lead.id)
        assert changed and row.owner_id == user.id and row.status == "assigned"


@pytest.mark.anyio
async def test_setting_patches_preserve_other_values_and_allow_switching_off(db_session):
    for model in (SystemSetting, AuditLog):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    session = SyncSessionAsAsync(db_session)
    request = Request({"type": "http", "headers": []})

    async def save(key, value):
        result = await upsert_setting(SettingInput(key=key, value=value), request, make_user(), session)
        return result["data"]["value"]

    await save("hard_floor", {"mode": "cost_markup", "markup_ratio": 0.2})
    assert await save("hard_floor", {"markup_ratio": 0.3}) == {"mode": "cost_markup", "markup_ratio": 0.3}
    with pytest.raises(AppError, match="绝对底价模式无效"):
        await save("hard_floor", {"mode": "costs"})
    await session.rollback()
    assert (await save("hard_floor", {"markup_ratio": 0.4}))["mode"] == "cost_markup"
    await save("export", {"limit": 120, "alert_rows": 75})
    assert (await save("export", {"alert_window_hours": "8"})) == {
        "limit": 120, "alert_rows": 75, "alert_window_hours": 8,
    }
    await save("logistics_volumetric_ratio", {"number": 167})
    assert await save("logistics_volumetric_ratio", {"number": "0"}) == {"number": 0}
    await save("dedup_scoring", {"weight_mobile": 77})
    scoring = await save("dedup_scoring", {"threshold": 60})
    assert scoring["weight_mobile"] == 77 and scoring["threshold"] == 60
    await save("notification_retry", {"backoff_minutes": [9, 45], "max_attempts": 5})
    assert await save("notification_retry", {"enabled": False}) == {
        "enabled": False, "max_attempts": 5, "backoff_minutes": [9, 45],
    }


@pytest.mark.anyio
async def test_tax_matching_demands_a_single_active_customer(db_session):
    db_session.add_all([
        Customer(id=1, name="甲", tax_no="TAX-A"),
        Customer(id=2, name="乙", tax_no="TAX-A"),
    ])
    db_session.flush()
    mapping = ExternalObjectMapping(object_type="customer", external_code="TAX-A")
    session = SyncSessionAsAsync(db_session)
    assert (await resolve_mapping(session, mapping))[::3] == (None, MATCH_CONFLICT)
    db_session.get(Customer, 1).deleted_at = datetime.now(UTC)
    db_session.flush()
    assert await resolve_mapping(session, mapping) == (2, "tax_no", None, MATCH_MATCHED)


@pytest.mark.anyio
async def test_price_analysis_only_counts_live_quotes(db_session):
    QuoteItem.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([
        Quote(id=1, quote_no="Q-A", customer_id=1, owner_id=101),
        Quote(id=2, quote_no="Q-B", customer_id=1, owner_id=101, deleted_at=datetime.now(UTC)),
        QuoteVersion(id=1, quote_id=1, created_at=datetime.now(UTC)),
        QuoteVersion(id=2, quote_id=2, created_at=datetime.now(UTC)),
        QuoteItem(id=1, quote_version_id=1, quoted_price=95, recommended_price_snapshot=100),
        QuoteItem(id=2, quote_version_id=2, quoted_price=50, recommended_price_snapshot=100, approval_required=True),
    ])
    db_session.flush()
    result = await pricing_stats(SyncSessionAsAsync(db_session), make_user())
    assert result["item_count"] == 1
    assert result["approval_required_count"] == 0
    assert result["average_discount_rate"] == 0.05


@pytest.mark.anyio
async def test_one_receivable_task_per_order_with_each_installment(db_session):
    for model in (AuditLog, Task, TaskRule):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    day = today_business()
    db_session.add_all([
        SalesOrder(id=1, order_no="SO-A", customer_id=1, owner_id=101),
        TaskRule(id=1, code="due-a", name="催收", trigger_type="receivable_due"),
        TaskRule(id=2, code="due-b", name="额外催收规则", trigger_type="receivable_due"),
        ReceivablePlan(id=1, order_id=1, plan_name="首款", due_date=day,
                       amount=100, created_at=datetime.now(UTC)),
        ReceivablePlan(id=2, order_id=1, plan_name="尾款", due_date=day + timedelta(days=1),
                       amount=200, created_at=datetime.now(UTC)),
    ])
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    assert (await run_auto_tasks(session, 101))["created_count"] == 1
    assert (await run_auto_tasks(session, 101))["created_count"] == 0
    task = db_session.query(Task).one()
    data = (await get_task(task.id, make_user(permissions={"payment:view"}), session))["data"]
    assert [row["plan_name"] for row in data["receivable_plans"]] == ["首款", "尾款"]
    assert [row["remaining_amount_exact"] for row in data["receivable_plans"]] == ["100.00", "200.00"]
    assert "receivable_plans" not in (await get_task(task.id, make_user(), session))["data"]


@pytest.mark.anyio
async def test_disabled_staff_tasks_wait_for_scoped_batch_handover(db_session, monkeypatch):
    for model in (AuditLog, Task, TaskRule):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    monkeypatch.setattr("app.modules.task.router.notification_service.notify", AsyncMock())
    db_session.add_all([
        User(id=5, name="停用员工", username="disabled", password_hash="x", status="disabled", department_id=1),
        User(id=6, name="在职接手人", username="active", password_hash="x", department_id=1),
        User(id=7, name="其他部门员工", username="other", password_hash="x", status="disabled", department_id=2),
        Task(id=1, title="历史在办任务", owner_id=5),
        Task(id=2, title="完成历史", owner_id=5, status="done"),
        Task(id=3, title="其他部门任务", owner_id=7),
        TaskRule(id=1, code="due", name="催收", trigger_type="receivable_due"),
        SalesOrder(id=1, order_no="SO-DISABLED", customer_id=1, owner_id=5),
        SalesOrder(id=2, order_no="SO-NEW", customer_id=1, owner_id=6),
        ReceivablePlan(id=1, order_id=1, plan_name="首款", due_date=today_business(),
                       amount=100, created_at=datetime.now(UTC)),
        ReceivablePlan(id=2, order_id=2, plan_name="首款", due_date=today_business(),
                       amount=100, created_at=datetime.now(UTC)),
    ])
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    assert (await run_auto_tasks(session, 101))["created_count"] == 2
    generated = db_session.query(Task).filter_by(order_id=1).one()
    assert generated.owner_id is None and generated.source_business_type == HANDOVER_SOURCE
    assert db_session.query(Task).filter_by(order_id=2).one().owner_id == 6
    manager = make_user(101, roles=("sales_manager",), permissions={"task:manage"}, data_scope="department")
    manager.department_id = 1
    candidates = (await handover_candidates("接手", 1, 20, manager, session))["data"]
    assert candidates["items"] == [{"id": 6, "name": "在职接手人", "username": "active"}]
    assert (await handover_candidates("disabled", 1, 20, manager, session))["data"]["total"] == 0
    with pytest.raises(AppError, match="待办交接仅限"):
        await handover_candidates("", 1, 20, make_user(6, permissions={"task:manage"}), session)
    tasks = (await list_tasks(page=1, page_size=20, user=manager, session=session, handover=True))["data"]
    assert {row["id"] for row in tasks["items"]} == {1, generated.id}
    admin = make_user(101, roles=("admin",), data_scope="all")
    assert (await list_tasks(page=1, page_size=20, user=admin, session=session, handover=True))["data"]["total"] == 3
    request = Request({"type": "http", "headers": []})
    with pytest.raises(AppError):
        await batch_handover_tasks(TaskBatchHandover(task_ids=[3], owner_id=6), request, manager, session)
    await session.rollback()
    await batch_handover_tasks(TaskBatchHandover(task_ids=[1, generated.id], owner_id=6), request, manager, session)
    assert db_session.get(Task, 1).owner_id == generated.owner_id == 6
    assert db_session.get(Task, 2).owner_id == 5
    assert generated.source_business_type is None
    assert (await run_auto_tasks(session, 101))["created_count"] == 0


@pytest.mark.anyio
async def test_case_author_can_review_and_admin_can_self_review_insight_but_other_reviewer_cannot(db_session):
    for model in (SalesCase, ProductInsight, ProductInsightRound, AuditLog):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    case = SalesCase(id=1, author_id=101, title="待审核案例", status="pending_review",
                     created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    insight = ProductInsight(id=1, created_by=101, owner_id=101, title="待评审洞察", status="under_review")
    manager_insight = ProductInsight(id=2, created_by=103, owner_id=103, title="主管自审洞察", status="under_review")
    db_session.add_all([case, insight, manager_insight])
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    author = make_user(101, roles=("admin",), permissions={"product:review"})
    manager_author = make_user(103, roles=("sales_manager",), permissions={"product:review"})
    author_without_review = make_user(101, roles=("salesperson",), permissions={"quote:view"})
    reviewer = make_user(102, roles=("admin",), permissions={"product:review"})
    request = Request({"type": "http", "headers": []})
    with pytest.raises(AppError, match="只有销售主管/管理员能审核案例"):
        await review_case(session, case=case, user=author_without_review, approve=True, note=None)
    # 非管理员评审人仍然不能自审（管理员自审见下）
    with pytest.raises(AppError, match="不能审核自己"):
        await review_insight(2, InsightReview(approve=True), request, manager_author, session)
    assert case.status == "pending_review" and manager_insight.status == "under_review"
    await review_case(session, case=case, user=author, approve=True, note=None)
    # 管理员可以审核自己提交的新品洞察（2026-11-21 用户确认）
    await review_insight(1, InsightReview(approve=True), request, author, session)
    await review_insight(2, InsightReview(approve=True), request, reviewer, session)
    assert case.status == "published" and case.reviewer_id == 101
    assert insight.status == "approved" and insight.reviewer_id == 101
    assert manager_insight.status == "approved" and manager_insight.reviewer_id == 102


@pytest.mark.anyio
async def test_shared_case_links_only_visible_quote_evidence(db_session):
    for model in (SalesCase, CaseEvidence):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    case = SalesCase(id=1, author_id=999, title="培训案例", status="published", quote_id=2, customer_id=1,
                     created_at=datetime.now(UTC))
    note = "用纸样解释封口工艺，远山集团 13800001234，成本 12 元"
    db_session.add_all([
        case,
        Customer(id=1, name="远山集团"),
        Quote(id=1, quote_no="Q-A", customer_id=1, owner_id=101),
        Quote(id=2, quote_no="Q-B", customer_id=1, owner_id=999),
        CaseEvidence(case_id=1, kind="quote", business_id=1, label="可查看", note=note,
                     created_at=datetime.now(UTC)),
        CaseEvidence(case_id=1, kind="quote", business_id=2, label="范围外", note="范围外备注",
                     created_at=datetime.now(UTC)),
    ])
    db_session.flush()
    payload = await get_case_detail(SyncSessionAsAsync(db_session), case=case,
                                    user=make_user(permissions={"quote:view"}))
    assert payload["quote_id"] is None
    assert [item["business_id"] for item in payload["evidences"]] == [1]
    assert "用纸样解释封口工艺" in payload["evidences"][0]["note"]
    assert "远山集团" not in payload["evidences"][0]["note"]
    assert "13800001234" not in payload["evidences"][0]["note"]
    assert "12 元" not in payload["evidences"][0]["note"]
    assert "范围外" not in str(payload)
    author_payload = await get_case_detail(SyncSessionAsAsync(db_session), case=case, user=make_user(999))
    assert next(item["note"] for item in author_payload["evidences"] if item["business_id"] == 1) == note


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["draft", "rejected"])
async def test_author_can_soft_delete_editable_case_from_list(db_session, status):
    for model in (SalesCase, CaseEvidence, AuditLog):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    case = SalesCase(id=1, author_id=101, title="待删除测试案例", status=status,
                     created_at=datetime.now(UTC))
    db_session.add(case)
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    request = Request({"type": "http", "headers": []})
    with pytest.raises(AppError, match="案例不存在"):
        await delete_case(1, request, make_user(103, roles=("admin",)), session, draft_only=True)
    assert case.deleted_at is None
    result = await delete_case(1, request, make_user(101), session, draft_only=True)
    assert result["message"] == "案例已删除"
    assert case.deleted_at is not None
    assert db_session.query(AuditLog).one().action == "delete"


@pytest.mark.anyio
async def test_only_reviewer_can_withdraw_a_published_case_and_history_survives(db_session):
    for model in (SalesCase, CaseEvidence, AuditLog):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    case = SalesCase(id=1, author_id=101, title="培训案例", status="published",
                     review_history=[{"round": 1, "approve": True}], created_at=datetime.now(UTC))
    db_session.add(case)
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    request = Request({"type": "http", "headers": []})
    reviewer = make_user(102, roles=("sales_manager",))
    author = make_user(101)
    with pytest.raises(AppError, match="审核权限"):
        await delete_case(1, request, author, session)
    assert case.status == "published"
    with pytest.raises(AppError, match="状态已变化"):
        await delete_case(1, request, reviewer, session, draft_only=True)
    assert case.status == "published" and case.deleted_at is None
    result = await delete_case(1, request, reviewer, session)
    assert "已撤下" in result["message"]
    assert case.status == "withdrawn" and case.deleted_at is None
    with pytest.raises(AppError):
        await get_case_detail(session, case=case, user=make_user(103))
    history = await get_case_detail(session, case=case, user=author)
    assert history["review_history"][0]["approve"] is True
    filters = dict(status=None, industry=None, product_line=None, stage=None, keyword=None)
    assert (await list_cases(session, user=reviewer, **filters))[1] == 0
    assert (await list_cases(session, user=reviewer, include_history=True, **filters))[1] == 1
    assert db_session.query(AuditLog).one().after_data == {"status": "withdrawn"}


@pytest.mark.anyio
async def test_recommendation_uses_price_range_from_latest_valid_order(db_session):
    for model in (Product, Sku, SalesOrderItem):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    oldest = datetime.now(UTC) - timedelta(days=10)
    db_session.add_all([
        Product(id=1, name="箱子"),
        Sku(id=1, product_id=1, name="大箱", sku_code="BOX"),
        Sku(id=2, product_id=1, name="停用款", sku_code="OFF", status="disabled"),
        SalesOrder(id=1, order_no="SO-OLD", customer_id=1, created_at=oldest),
        SalesOrder(id=2, order_no="SO-LAST", customer_id=1, created_at=oldest + timedelta(days=5)),
        SalesOrder(id=3, order_no="SO-CANCEL", customer_id=1, status="cancelled", created_at=datetime.now(UTC)),
        SalesOrder(id=4, order_no="SO-USD", customer_id=1, currency="USD", created_at=datetime.now(UTC)),
    ])
    db_session.flush()
    db_session.add_all([SalesOrderItem(order_id=order_id, sku_id=sku_id, unit_price=price)
                        for order_id, sku_id, price in [(1, 1, 90), (2, 1, 8), (2, 1, 10),
                                                        (2, 2, 2), (3, 1, 2), (4, 1, 999)]])
    db_session.flush()
    rows = await recommend_products(SyncSessionAsAsync(db_session), opportunity=Opportunity(customer_id=1))
    assert len(rows) == 1 and rows[0]["order_times"] == 2
    assert rows[0]["last_unit_price"] is None
    assert rows[0]["last_unit_price_min_exact"] == "8.0000"
    assert rows[0]["last_unit_price_max_exact"] == "10.0000"


@pytest.mark.parametrize(("trigger_type", "config"), [
    ("unknown", {}), ("receivable_due", {"days": -1}), ("receivable_due", {"days": True}),
    ("customer_silent", {"levels": "A"}), ("quote_no_followup", {"days": 10**12}),
])
def test_rejects_automatic_task_rules_that_cannot_run(trigger_type, config):
    with pytest.raises(AppError):
        validate_task_rule(TaskRule(trigger_type=trigger_type, trigger_config=config))


@pytest.mark.anyio
@pytest.mark.parametrize("value", [{"threshold": 101}, {"weight_mobile": -1}, {"weight_tax_no": "无效"}])
async def test_dedup_settings_reject_invalid_threshold_or_weight(value):
    with pytest.raises(AppError, match="客户查重"):
        await validate_setting_value(None, "dedup_scoring", value)
    await validate_setting_value(None, "dedup_scoring", {"threshold": 50, "weight_tax_no": "60"})


@pytest.mark.parametrize("headers", ["客户名称,客户名称", "客户名称, 客户 名称 ", "客户名称,单价（元）,单价(元)"])
def test_csv_rejects_colliding_headers(headers):
    with pytest.raises(AppError, match="表头.*重复"):
        parse_csv_bytes(headers.encode(), required_headers=["客户名称"])


def test_csv_protects_text_with_leading_spaces():
    csv_text = csv_bytes([["  =1+1", " 普通备注", -10]], ["一", "二", "三"])
    assert "'  =1+1, 普通备注,-10" in csv_text.decode("utf-8-sig")


@pytest.mark.parametrize("invalid", ["-1", "NaN", "Infinity", "0.001", "100000000000000"])
def test_dynamic_sample_fee_uses_same_validation_as_sample_update(invalid):
    with pytest.raises(AppError, match="打样费用"):
        validated_sample_details({"_sample_fee": invalid})
    assert validated_sample_details({"_sample_fee": " 0 ", "_sample_maker": "  "}) == {
        "sample_fee": Decimal(0), "sample_maker": None,
    }


@pytest.mark.parametrize(("schema", "payload"), [
    (SampleUpdate, {"purpose": "x" * 256}),
    (MilestoneUpdate, {"remark": "x" * 256}),
    (NumberingRuleCreate, {"code": "order", "name": "订单", "prefix": "x" * 17}),
    (TaskRuleInput, {"code": "x" * 65, "name": "任务", "trigger_type": "receivable_due"}),
    (PublicPoolRuleInput, {"level": "A", "days": 1, "remark": "x" * 256}),
    (ProductUpdate, {"status": "unknown"}),
    (SkuUpdate, {"status": "unknown"}),
])
def test_known_database_fields_and_status_are_validated(schema, payload):
    with pytest.raises(ValidationError):
        schema.model_validate(payload)
