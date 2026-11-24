"""B20–B23 第二轮（P2）证伪复现：15 条。

失败 = 缺陷存在；通过 = 该条被现状兜住。全部只读源码/构造模型字段，不写库。
"""

from __future__ import annotations

import inspect

from app.core import importing, scheduler
from app.modules.opportunity import router as opp_router
from app.modules.opportunity import schema as opp_schema
from app.modules.product import io, master
from app.modules.product import io_router
from app.modules.product_insight import router as insight_router
from app.modules.sample import router as sample_router
from app.modules.sample import schema as sample_schema
from app.modules.sample import service as sample_service
from app.modules.settings import router as settings_router
from app.modules.settings import service as settings_service
from app.modules.task import router as task_router


# ---------- B20 商机/产品 ----------


def test_b20_01_funnel_should_expose_exact_amount():
    src = inspect.getsource(opp_router)
    assert "amount_exact" in src, "漏斗/看板/360 概览只有 float 金额，无 exact"


def test_b20_02_delete_opportunity_should_check_live_orders():
    src = inspect.getsource(opp_router.delete_opportunity)
    assert "SalesOrder" in src or "live_orders" in src, "删除商机不查未取消订单，成交商机可直接软删"


def test_b20_03_assign_schema_should_require_owner():
    field = opp_schema.OpportunityAssign.model_fields["owner_id"]
    assert field.is_required(), "owner_id 可空，改派入口能把商机清成无主"


def test_b20_04_copy_items_should_require_same_customer():
    src = inspect.getsource(opp_router)
    idx = src.index("async def copy_opportunity_items")
    block = src[idx : idx + 2000]
    assert "customer_id" in block and "==" in block, "跨客户复制需求不校验客户"


def test_b20_05_sku_import_decimal_should_apply_field_rules():
    src = inspect.getsource(io._decimal)
    assert "max_digits" in src or "MAX_DIGITS" in src, "CSV 物理量不判 Numeric 位数，超限到 DB 才失败"


def test_b20_06_master_text_should_have_max_length():
    src = inspect.getsource(master.normalize_field_value)
    block = src[src.index("if field in _TEXT_FIELDS"):]
    block = block[: block.index("\n    if field in _INT_FIELDS")] if "\n    if field in _INT_FIELDS" in block else block
    assert "max_length" in block or "MAX_LENGTH" in block, "主数据文本确认不按列宽校验，超长 DB 500"


# ---------- B21 产品接口/新品/打样 ----------


def test_b21_01_sample_money_should_expose_exact():
    src = inspect.getsource(sample_service)
    assert "sample_fee_exact" in src or "shipping_fee_exact" in src, "打样费用/运费只有 float 出口"


def test_b21_02_sample_text_inputs_should_have_max_length():
    checks = [
        (sample_schema.SampleItemInput, "item_name", 200),
        (sample_schema.SampleConfirm, "remark", 255),
        (sample_schema.SampleApprove, "reject_reason", 255),
        (sample_schema.SampleShip, "carrier", 64),
    ]
    missing = []
    for model, name, limit in checks:
        field = model.model_fields[name]
        if not any(getattr(meta, "max_length", None) == limit for meta in field.metadata):
            missing.append(f"{model.__name__}.{name}")
    assert not missing, f"这些入参没有 max_length，超长到 DB 才 500：{missing}"


def test_b21_03_product_import_should_not_echo_raw_exception():
    src = inspect.getsource(io_router)
    assert "str(exc)[:160]" not in src, "产品/SKU 导入把 DB 异常原文回显到失败清单"


def test_b21_04_insight_price_should_keep_exact():
    src = inspect.getsource(insight_router.serialize)
    assert "price_assumption_exact" in src, "新品洞察价格假设/评审快照 Decimal 转 float"


# ---------- B22 打样路由/任务 ----------


def test_b22_04_revision_should_copy_application_form_fields():
    src = inspect.getsource(sample_router.revise_sample)
    for field in ("sample_type", "product_category", "maker_unit", "estimated_hours", "deliver_date", "sample_maker"):
        assert f"{field}=parent.{field}" in src, f"修订版漏复制 {field}"


def test_b22_05_task_assignee_should_be_in_scope():
    src = inspect.getsource(task_router._active_owner)
    assert "scoped_owner_ids" in src or "ensure_in_scope" in src, "任务可改派给数据范围外用户"


def test_b22_06_batch_handover_should_dispatch_notification():
    src = inspect.getsource(task_router.batch_handover_tasks)
    assert "dispatch_after_commit" in src or "dispatch_pending" in src, "批量交接只落 pending 通知，不投递"


# ---------- B23 设置/导入 ----------


def test_b23_02_recycle_should_dispatch_notification():
    route_src = inspect.getsource(settings_router.run_recycle)
    job_src = inspect.getsource(scheduler.run_public_pool_recycle_job)
    assert "dispatch" in route_src or "dispatch" in job_src, "公海回收只落 pending 通知，不投递"


def test_b23_03_import_commit_should_not_echo_raw_exception():
    src = inspect.getsource(importing.finalize)
    assert "str(exc)[:120]" not in src, "导入 commit 失败把数据库异常原文回显给前端"
