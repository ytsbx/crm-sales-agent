"""B01–B05 剩余 P2 证伪复现（22 条）。

失败 = 缺陷存在；通过 = 已被现状兜住。
"""

from __future__ import annotations

import inspect

from app.modules.order import router as order_router
from app.modules.order import schedule as order_schedule
from app.modules.payment import router as payment_router
from app.modules.payment import service as payment_service
from app.modules.pricing import logistics
from app.modules.pricing import logistics_schema
from app.modules.pricing import router as pricing_router
from app.modules.pricing import service as pricing_service
from app.modules.quote import router as quote_router
from app.modules.quote import schema as quote_schema
from app.modules.quote import service as quote_service
from app.modules.erp import adapter as erp_adapter


# ---------- B01 ----------


def test_b01_02_quote_serialize_should_expose_exact_for_components():
    src = inspect.getsource(quote_service.serialize_version)
    assert "subtotal_amount_exact" in src and "charge_amount_exact" in src, (
        "报价版本只有 total_amount_exact，分项金额仍只有 float"
    )


def test_b01_03_money_quantize_should_specify_rounding():
    src = inspect.getsource(quote_service)
    assert 'quantize(Decimal("0.01"))' not in src, "金额量化用默认 HALF_EVEN，与 HALF_UP 口径不一致"


# ---------- B02 ----------


def test_b02_03_update_quote_should_validate_contact():
    src = inspect.getsource(quote_router.update_quote)
    assert "contact.customer_id" in src or "Contact" in src, "改联系人只 setattr，不校验存在/归属"


def test_b02_04_delete_quote_should_block_sent_or_accepted():
    src = inspect.getsource(quote_router.delete_quote)
    assert "sent_at" in src or "accepted_at" in src, "已发送/已接受报价仍可软删"


def test_b02_05_role_permissions_should_use_one_direction():
    discount_src = inspect.getsource(pricing_service.resolve_discount_limit)
    margin_src = inspect.getsource(pricing_service.resolve_min_margin)
    # 折扣上限 min = 最严；利润率 min = 最松。两项方向相反。
    assert not ("return min(rows)" in discount_src and "margin = min(" in margin_src), (
        "折扣上限取 min、利润率也取 min，角色叠加方向相反"
    )


def test_b02_07_foreign_without_fx_should_not_compare():
    src = inspect.getsource(pricing_service)
    block = src[src.index("below_protection ="):src.index("below_authorized =")]
    assert "fx is None" in block or "not is_foreign" in block, (
        "外币无汇率仍拿人民币底价与外币报价比较"
    )


def test_b02_12_quote_valid_until_null_should_be_rejected():
    src = inspect.getsource(quote_router.update_quote)
    assert "有效期不能为空" in src or "valid_until is None" in src, (
        "PATCH /quotes 可把有效期显式置空，报价从此永不失效"
    )


# ---------- B03 ----------


def test_b03_04_cost_create_should_check_same_effective_day():
    src = inspect.getsource(pricing_router)
    assert "same_day" in src or "effective_from ==" in src, "成本新增不查同日重复，核价时才 409"


def test_b03_05_logistics_amount_should_not_roundtrip_float():
    src = inspect.getsource(logistics)
    assert "Decimal(str(option[\"amount\"]))" not in src, "运费金额 float 往返后落库"


def test_b03_06_logistics_quantity_schema_should_match_column():
    src = inspect.getsource(logistics_schema)
    assert "decimal_places=3" in src, "试算数量不限制 3 位，落库 3 位但金额按原数量算"


def test_b03_07_price_rule_should_reject_deleted_or_inactive_sku():
    src = inspect.getsource(pricing_router)
    assert "sku.deleted_at" in src and "sku.status" in src, "软删/停用 SKU 仍可建价格规则"


# ---------- B04 ----------


def test_b04_04_order_assign_should_check_target_scope():
    src = inspect.getsource(order_router.update_order)
    assert "ensure_in_scope" in src, "转移订单负责人只查 active，不查数据范围"


def test_b04_05_completed_order_should_not_change_delivery():
    src = inspect.getsource(order_schedule)
    assert 'status == "completed"' in src, "已完成订单仍可走交期变更，历史履约基准被改写"


def test_b04_06_refresh_status_should_require_manage():
    src = inspect.getsource(order_router.refresh_status)
    assert "order:manage" in src, "只有 order:view 的用户可通过 ERP 刷新改订单状态"


def test_b04_07_partial_shipment_should_not_default_to_full():
    from app.modules.order import service as order_service

    src = inspect.getsource(order_service.ship_shipment_batch)
    assert "planned_qty" not in src[src.index("ship_inputs.get"):], (
        "未传的实发行默认按计划量全发"
    )


def test_b04_08_delivery_change_should_shift_batches_when_old_date_missing():
    src = inspect.getsource(order_schedule)
    assert "if batch.planned_date is None or delta is None" not in src, (
        "旧交期为空时批次计划日被静默跳过"
    )


# ---------- B05 ----------


def test_b05_06_fetch_order_status_should_match_external_id():
    src = inspect.getsource(erp_adapter.ErpAdapter.fetch_order_status)
    assert 'first.get("o_id") == external_id' in src or "o_id" in src and "external_id" in src, (
        "ERP 回读取 orders[0]，不校验 o_id 与请求一致"
    )


def test_b05_07_idempotency_in_flight_should_have_timeout():
    from app.core import idempotency

    src = inspect.getsource(idempotency._judge)
    assert "created_at" in src or "timeout" in src, "in_flight 占位无超时，进程崩溃后同键永久 409"


def test_b05_08_cancelled_receivable_should_not_be_updated():
    src = inspect.getsource(payment_router.update_receivable)
    assert 'plan.status == "cancelled"' in src or "已取消" in src, (
        "已取消应收节点仍可改金额/到期日"
    )


def test_b05_09_delete_receivable_should_ignore_rejected_payments():
    src = inspect.getsource(payment_router.delete_receivable)
    assert "PaymentRecord.status" in src or "rejected" in src, "已驳回回款永久阻止删除应收节点"


def test_b05_10_plan_payments_should_filter_order_id():
    src = inspect.getsource(payment_router.plan_payments)
    assert "PaymentRecord.order_id == plan.order_id" in src, "期次回款列表未过滤 order_id，脏数据可跨订单展示"


def test_b05_12_manual_allocation_should_not_exceed_payment():
    src = inspect.getsource(payment_service.allocate_payment)
    block = src[src.index('record.allocation_mode == "manual"'):]
    block = block[: block.index("# 历史已有人工行")] if "# 历史已有人工行" in block else block
    assert "received_amount" in block, "人工分配不校验合计 ≤ 回款金额"


# ---------- 补充审查 B01-B05 P2 ----------


def test_b01_10_update_item_audit_should_capture_quantity_and_cost():
    src = inspect.getsource(quote_router.update_item)
    assert "quantity" in src[src.index("before={"):src.index("ip=client_ip")], (
        "改价审计只记 item_id/quoted_price，数量/成本变化无法复算"
    )


def test_b02_14_price_permission_audit_should_have_before():
    src = inspect.getsource(pricing_router.upsert_price_permission)
    assert "before=" in src, "价格权限修改审计无 before，旧利润率/折扣/审批权无法复算"


def test_b02_15_accept_decline_audit_should_capture_amount():
    from app.modules.quote import lifecycle

    src = inspect.getsource(lifecycle._record)
    assert "total_amount" in src, "客户接受/拒绝审计只记版本号，不记金额/币种"


def test_b03_19_import_commit_should_not_echo_raw_exception():
    src = inspect.getsource(importing.finalize)
    assert "str(exc)[:120]" not in src, "整批导入 commit 失败把数据库异常原文回显"


def test_b03_20_import_audit_should_record_row_changes():
    src = inspect.getsource(importing.finalize)
    block = src[src.index('action="import"'):]
    assert "rows" in block or "changed" in block, "价格/成本导入审计只记文件与计数，不记每行旧值/新值"


def test_b03_21_logistics_input_should_have_bounds():
    from app.modules.pricing import logistics_schema

    fields = logistics_schema.LogisticsCalculateRequest.model_fields
    for name in ("quantity", "weight_override", "volume_override"):
        meta = fields[name].metadata
        assert any(getattr(m, "max_digits", None) for m in meta), (
            f"{name} 无 max_digits/decimal_places，超大值到 Numeric 列才溢出"
        )


def test_b04_19_cancel_audit_should_capture_money_and_plans():
    src = inspect.getsource(order_router.cancel_order)
    block = src[src.index('action="cancel"'):src.index('ip=client_ip')]
    assert "total_amount" in block or "amount_exact" in block, (
        "取消订单审计只记数量，不记订单金额/回款/被取消期次明细"
    )


def test_b04_20_shipment_request_key_should_be_required():
    from app.modules.order import schema as order_schema

    field = order_schema.ShipmentBatchCreate.model_fields["request_key"]
    assert field.is_required(), "排批次 request_key 可选，无键双击会生成两条计划批次"


def test_b05_19_payment_confirm_audit_should_capture_amount():
    src = inspect.getsource(payment_router)
    confirm_src = src[src.index('async def confirm_payment'):src.index('async def reject_payment')]
    assert "received_amount" in confirm_src[confirm_src.index("write_audit"):], (
        "确认回款审计只记 comment，不记金额/币种/订单/期次"
    )

