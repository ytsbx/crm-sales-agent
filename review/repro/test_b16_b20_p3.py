"""B16、B18–B20 的 P3 逐条证伪（22 条）。"""

from __future__ import annotations

import inspect

from app.modules.analytics import router as analytics_router
from app.modules.cases import router as case_router, schema as case_schema, service as case_service
from app.modules import contact_util
from app.modules.followup import schema as followup_schema, service as followup_service
from app.modules.inquiry import router as inquiry_router, schema as inquiry_schema
from app.modules.lead import router as lead_router, schema as lead_schema, service as lead_service
from app.modules.opportunity import router as opp_router, schema as opp_schema
from app.modules.product import master as product_master, service as product_service, router as product_router
from app.modules.quote import model as quote_model
from app.modules.search import router as search_router


# ---------- B16 ----------


def test_b16_07_search_like_should_escape_wildcards_and_count_total():
    src = inspect.getsource(search_router.global_search)
    assert "escape" in src or "replace(\"%\"" in src, "搜索关键词未转义 %/_，用户可做宽匹配"
    assert "returned_total" in src or "count(" in src, "total 是各组 limit 截断后的条数之和，不是全量"


# ---------- B18 ----------


def test_b18_05_superseded_by_should_order_by_latest():
    src = inspect.getsource(case_service.list_cases)
    block = src[src.index("child_of.setdefault"):]
    assert "order_by" in block, "superseded_by 先到先得，可能指向 withdrawn/rejected 修订稿"


def test_b18_06_timing_report_should_have_upper_bounds():
    fields = analytics_router.TimingReport.model_fields
    checks = {
        "business_type": ("max_length", 32),
        "business_id": ("le", 2**63 - 1),
        "typed_fields": ("le", 2**31 - 1),
        "rework_count": ("le", 2**31 - 1),
    }
    missing = []
    for name, (attr, limit) in checks.items():
        if not any(getattr(m, attr, None) == limit for m in fields[name].metadata):
            missing.append(name)
    assert not missing, f"耗时接口字段无上限，超大值到 DB 才 500：{missing}"


def test_b18_07_sales_target_year_should_have_bounds():
    src = inspect.getsource(analytics_router.list_sales_targets)
    assert "ge=" in src and "le=" in src, "year 无上下界，GET /sales-targets/bases 可对任意历史年份惰性冻结"


def test_b18_08_case_audit_should_capture_before_after():
    create_src = inspect.getsource(case_router.create_case)
    update_src = inspect.getsource(case_router.update_case)
    assert "evidence_ids" in create_src, "案例创建审计只记 title，不记客户/证据"
    assert "before=" in update_src, "案例更新审计只有 action/business_id"


def test_b18_09_case_inputs_should_reject_zero_and_blank():
    create_src = inspect.getsource(case_router.create_case)
    assert "if payload.customer_id is not None" in create_src, "customer_id=0 被 if payload.customer_id 跳过校验"
    field = case_schema.CaseUpdate.model_fields["title"]
    assert any(getattr(m, "min_length", None) == 1 for m in field.metadata), "更新标题可空"
    submit_src = inspect.getsource(case_service.submit_case)
    assert "case.title.strip()" in submit_src, "空 格标题可通过提交"


def test_b18_10_contact_dedupe_like_should_escape_wildcards():
    src = inspect.getsource(contact_util)
    assert 'ilike(f"%{' not in src or "escape" in src, "客户/联系人查重 LIKE 未转义，%/_ 会放大候选范围"


# ---------- B19 ----------


def test_b19_06_followup_type_should_have_enum_and_length():
    fields = followup_schema.FollowUpCreate.model_fields
    assert any(getattr(m, "max_length", None) == 32 for m in fields["followup_type"].metadata), (
        "followup_type 无 max_length，33 字符到 DB 才 500"
    )


def test_b19_07_lead_fields_should_have_lengths_and_name_min():
    fields = lead_schema.LeadUpdate.model_fields
    assert any(getattr(m, "min_length", None) == 1 for m in fields["name"].metadata), "LeadUpdate.name 可空"
    batch = lead_schema.LeadBatchAssign.model_fields["lead_ids"]
    assert any(getattr(m, "max_length", None) for m in batch.metadata), "批量 lead_ids 无上限"


def test_b19_08_opportunity_fields_should_have_lengths_and_limits():
    fields = opp_schema.OpportunityUpdate.model_fields
    assert any(getattr(m, "min_length", None) == 1 for m in fields["title"].metadata), "OpportunityUpdate.title 可空"
    call = opp_schema.RecommendProductsRequest.model_fields["limit"]
    assert any(getattr(m, "le", None) for m in call.metadata), "推荐 limit 无上下界"


def test_b19_09_lead_inquiry_create_should_have_request_key_and_status_guard():
    lead_src = inspect.getsource(lead_router.create_lead)
    inquiry_src = inspect.getsource(inquiry_router.create_inquiry)
    assert "request_key" in lead_src, "创建线索无 request_key，双击重复"
    assert "request_key" in inquiry_src, "创建询价无 request_key，双击重复"
    upd = inspect.getsource(inquiry_router.update_inquiry)
    assert 'data["status"] is not None' not in upd or "status 不能为空" in upd, "询价 status=null 会落空"


def test_b19_10_lead_inquiry_audit_should_have_before():
    inq = inspect.getsource(inquiry_router.update_inquiry)
    lead = inspect.getsource(lead_router.release_lead)
    assert "before=" in inq, "询价更新审计只有 after，目标价改前值丢失"
    assert "before=" in lead, "线索释放审计无 before/after，归属变化不可复算"


def test_b19_11_lead_opportunity_like_should_escape_wildcards():
    src = inspect.getsource(lead_service.build_lead_stmt)
    assert "escape" in src, "线索/商机关键词 LIKE 未转义 %/_"


def test_b19_12_business_event_duplicate_should_be_caught():
    src = inspect.getsource(followup_service.record_progress_event)
    assert "IntegrityError" in src, "BusinessEvent 并发重放撞唯一约束直接 500"


# ---------- B20 ----------


def test_b20_07_product_physical_values_should_expose_exact():
    src = inspect.getsource(product_service)
    assert "_exact" in src, "SKU 物理量序列化只有 float，无 exact"
    master_src = inspect.getsource(product_master)
    assert "_json_value" not in master_src or "str(value)" in master_src, "主数据确认快照存 float"


def test_b20_08_stage_status_should_be_enum_and_reorder_locked():
    src = inspect.getsource(opp_schema.StageUpdate)
    assert "Literal" in src, "阶段 status 是自由字符串"
    reorder_src = inspect.getsource(opp_router.reorder_stages)
    assert "lock_stages" in reorder_src, "阶段重排无锁，与新增阶段交叉 sequence 重复"


def test_b20_09_opportunity_create_should_have_request_key():
    src = inspect.getsource(opp_router.create_opportunity)
    assert "request_key" in src, "创建商机/明细/克隆无 request_key，双击重复"


def test_b20_10_reopen_should_clear_loss_remark_and_audit_before():
    src = inspect.getsource(opp_router.reopen_opportunity)
    assert "loss_remark = None" in src, "重开不清 loss_remark，旧失单理由残留"
    assert "before=" in src, "重开审计无 before，状态/原因不可复算"


def test_b20_11_single_update_should_take_row_lock():
    src = inspect.getsource(opp_router.update_opportunity)
    assert "for_update=True" in src, "商机单条更新无行锁，并发最后写入覆盖"
    item_src = inspect.getsource(opp_router.update_item)
    assert "for_update=True" in item_src, "明细单条更新无行锁"


def test_b20_12_replace_copy_audit_should_have_item_details():
    replace_src = inspect.getsource(opp_router.replace_items)
    block = replace_src[replace_src.index('action="replace_items"'):replace_src.index("ip=client_ip")]
    assert "before" in block, "整批替换审计只记 count，无原明细"
    copy_src = inspect.getsource(opp_router.copy_opportunity_items)
    assert "source_items" in copy_src or "before" in copy_src, "复制审计只记计数，无逐条 SKU"


def test_b20_13_ingest_external_sku_should_not_commit_internally():
    src = inspect.getsource(product_master.ingest_external_sku)
    assert "await session.commit()" not in src, "ingest 自己提交，调用方审计可能落空"


def test_b20_14_delete_item_should_check_quote_reference():
    src = inspect.getsource(opp_router.delete_item)
    assert "QuoteItem" in src or "quote_item" in src, "硬删商机明细不查报价引用，opportunity_item_id 悬空"
    assert quote_model.QuoteItem.__table__.columns["opportunity_item_id"].foreign_keys, (
        "QuoteItem.opportunity_item_id 无外键，商机明细硬删后报价溯源悬空"
    )
