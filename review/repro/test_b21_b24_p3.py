"""B21–B24 的 P3 逐条证伪（24 条：21 + B24 3）。"""

from __future__ import annotations

import inspect

from app.core import importing, similarity
import app.main as main_module
from app.modules.product import io_router as product_io_router, router as product_router
from app.modules.product_insight import router as insight_router, schema as insight_schema
from app.modules.sample import model as sample_model, router as sample_router, schema as sample_schema, service as sample_service, sources as sample_sources
from app.modules.settings import numbering, router as settings_router, schema as settings_schema, service as settings_service
from app.modules.task import router as task_router, schema as task_schema, scanning


# ---------- B21 ----------


def test_b21_05_product_export_should_use_schema_and_limit():
    src = inspect.getsource(product_io_router.export_products_filtered)
    assert "payload: dict" not in src, "产品导出用裸 dict，keyword 非 str 会 500"
    assert "limit" in src or "stream" in src, "导出整表 scalars().all()，无分页/上限"


def test_b21_06_insight_review_request_key_should_be_used():
    src = inspect.getsource(insight_router.review_insight)
    assert "request_key" in src, "InsightReview.request_key 定义了但评审不使用，重试返回 422"


def test_b21_07_insight_create_should_have_request_key():
    fields = insight_schema.InsightCreate.model_fields
    assert "request_key" in fields, "洞察创建无 request_key，双击生成重复记录"


def test_b21_08_sku_audit_should_have_before_after():
    src = inspect.getsource(product_router.enable_sku)
    assert "before=" in src and "after=" in src, "SKU 启用审计只有 action，无 before/after"


def test_b21_09_sample_insight_like_should_escape_wildcards():
    src = inspect.getsource(sample_service.build_list_stmt)
    assert "escape" in src, "打样列表 LIKE 未转义 %/_"


def test_b21_10_sample_source_should_validate_owner():
    src = inspect.getsource(sample_sources.create_from_source)
    assert "_assert_assignee" in src or "scoped_owner_ids" in src, "打样来源建单不校验 owner 在职/范围"


def test_b21_11_product_attachment_should_have_length_limits():
    src = inspect.getsource(product_router.attach_product_file)
    assert "max_length=32" in src and "max_length=255" in src, "产品附件 category/remark 无列宽前置校验"


def test_b21_12_insight_images_should_have_limits():
    fields = insight_schema.InsightCreate.model_fields
    meta = fields["images"].metadata
    assert any(getattr(m, "max_length", None) for m in meta), "洞察 images 无条数/单条长度上限"


def test_b21_13_manual_sample_create_should_have_request_key():
    fields = sample_schema.SampleCreate.model_fields
    assert "request_key" in fields, "手动打样创建无 request_key，双击生成重复申请单"


# ---------- B22 ----------


def test_b22_07_revision_should_have_request_key_and_unique():
    src = inspect.getsource(sample_router.revise_sample)
    assert "request_key" in src, "修订版无 request_key，双击出两份 V2"
    constraints = [c for c in sample_model.SampleRequest.__table__.constraints if type(c).__name__ == "UniqueConstraint"]
    names = {tuple(col.name for col in c.columns) for c in constraints}
    assert ("parent_id", "version") in names, "修订版无 (parent_id, version) 唯一约束"


def test_b22_08_task_inputs_should_be_enum_bounded_and_stripped():
    priority_src = inspect.getsource(task_schema.TaskCreate)
    assert "Literal" in priority_src, "任务 priority 是自由字符串"
    batch = task_schema.TaskBatchComplete.model_fields["task_ids"]
    assert any(getattr(m, "max_length", None) for m in batch.metadata), "批量完成 task_ids 无上限"
    title = task_schema.TaskCreate.model_fields["title"]
    assert any(getattr(m, "min_length", None) == 1 for m in title.metadata)


def test_b22_09_task_audit_should_have_before():
    src = inspect.getsource(task_router)
    complete_src = src[src.index("async def complete_task"):src.index("async def cancel_task")]
    assert "before=" in complete_src, "任务完成审计只有 action，无 before 状态"


def test_b22_10_sample_audit_should_have_before():
    src = inspect.getsource(sample_router.approve_sample)
    assert "before=" in src, "打样审批/寄样审计只有 after，无 before 状态/金额"


def test_b22_11_numbering_prefix_should_be_whitelisted():
    src = inspect.getsource(numbering.period_max_number)
    assert "escape_like" in src or "escape=" in src, "编号前缀参与 LIKE 探测未转义 %/_"


def test_b22_12_scanning_dialect_should_not_swallow_all():
    src = inspect.getsource(scanning)
    assert "except Exception" not in src, "扫描锁方言探测吞异常，静默跳过 advisory lock"


# ---------- B23 ----------


def test_b23_04_settings_strings_should_have_lengths():
    checks = [
        (settings_schema.SettingInput, "key", 64),
        (settings_schema.SettingInput, "description", 255),
        (settings_schema.DictionaryItemCreate, "type", 32),
        (settings_schema.DictionaryItemCreate, "label", 128),
        (settings_schema.CustomerLevelCreate, "code", 64),
        (settings_schema.CustomerLevelCreate, "name", 128),
    ]
    missing = [f"{m.__name__}.{n}" for m, n, limit in checks
               if not any(getattr(x, "max_length", None) == limit for x in m.model_fields[n].metadata)]
    assert not missing, f"设置/字典/等级字段无 max_length，超长到 DB 才 500：{missing}"


def test_b23_05_approval_levels_should_lock_row():
    src = inspect.getsource(settings_router)
    idx = src.index("approval_levels")
    block = src[idx:idx + 4000]
    assert "for_update" in block, "approval_levels 保存不锁行，并发后写覆盖先写"


def test_b23_06_auto_task_scan_should_not_write_next_followup_without_lock():
    src = inspect.getsource(settings_service.run_auto_tasks)
    assert "next_followup_at" not in src or "lock_customer_row" in src, (
        "扫描直接写 next_followup_at 不加客户锁，可能覆盖用户新约定"
    )


def test_b23_07_protection_detail_should_pass_customer_id():
    src = inspect.getsource(settings_service.decide_candidate)
    assert "protection_detail(session, customer_id=" in src, "单客户复核扫全库 protection_detail"


def test_b23_08_batch_candidate_ids_should_be_bounded():
    fields = settings_router.BatchCandidateDecision.model_fields
    field = fields["candidate_ids"]
    assert any(getattr(m, "max_length", None) for m in field.metadata), "批量复核 candidate_ids 无上限"


def test_b23_09_preview_token_decompress_should_have_max_length():
    src = inspect.getsource(importing)
    block = src[src.index("zlib.decompress"):]
    assert "max_length" in block[:200], "preview_token 解压无上限，可构造内存/CPU 消耗"


# ---------- B24 ----------


def test_b24_01_cors_wildcard_should_conflict_with_credentials():
    src = inspect.getsource(main_module)
    assert "allow_credentials=False" in src or 'if "*" in' in src, "CORS 允许 * 且 allow_credentials=True，无启动校验"


def test_b24_02_health_should_log_db_failure():
    src = inspect.getsource(main_module)
    block = src[src.index('database": "ok"'):]
    assert "logger.exception" in block or "logger.error" in block, "健康检查吞 DB 异常不打日志"


def test_b24_03_normalize_name_should_strip_all_whitespace():
    src = inspect.getsource(similarity.normalize_name)
    assert "\\t" in src or "split()" in src or "\\s" in src, "公司名归一化只清普通空格，制表符/换行会漏判"
