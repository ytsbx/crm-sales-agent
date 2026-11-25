"""B01–B05 的 P3 证伪复现（37 条）。

失败 = 缺陷/观察成立；通过 = 现状已兜住或报告不成立。
"""

from __future__ import annotations

import inspect

from app.core import confirmation
from app.modules.order import drafts, milestones, model as order_model, router as order_router, schema as order_schema, service as order_service
from app.modules.payment import model as payment_model, router as payment_router, service as payment_service, schema as payment_schema
from app.modules.pricing import logistics, logistics_router, model as pricing_model, router as pricing_router, schema as pricing_schema, service as pricing_service
from app.modules.pricing import io_router as pricing_io_router
from app.modules.quote import router as quote_router, service as quote_service


# ---------- B01 ----------


def test_b01_04_probe_log_should_be_removed():
    src = inspect.getsource(quote_service)
    assert 'getLogger("probe")' not in src, "生产代码仍向 probe 打底价派生值"


def test_b01_05_approval_reason_should_say_divide_not_multiply():
    src = inspect.getsource(quote_service)
    assert "×(1+" not in src, "审批理由把成本÷(1-r)写成成本×(1+r)，误导审批人"


def test_b01_06_margin_delta_should_not_use_float_round():
    src = inspect.getsource(quote_service)
    assert 'round(current["margin"] - previous["margin"], 6)' not in src, (
        "版本对比毛利率差额用 float round，和 exact 口径不一致"
    )


def test_b01_09_create_version_should_verify_source_visibility():
    src = inspect.getsource(quote_service.create_version)
    assert "ensure_draft_access" in src or "private_draft" in src, (
        "service 内不复核源版本可见性，未来新调用方可复制他人私密草稿"
    )


def test_b01_11_quote_audit_should_not_write_float_money():
    src = inspect.getsource(quote_router.update_item)
    assert "float(item.quoted_price)" not in src, "改价审计写 float，复算会差 1 分"


# ---------- B02 ----------


def test_b02_08_refund_and_freight_quantize_should_specify_rounding():
    src = inspect.getsource(pricing_service)
    assert 'quantize(Decimal("0.0001"))' not in src, "退税/运费单价量化仍用默认 HALF_EVEN"


def test_b02_09_historical_version_should_not_fill_current_fields():
    src = inspect.getsource(quote_service.serialize_quote)
    assert '"current_version_no": version.version_no' not in src, (
        "查看历史版本时 current_version_no/amount 填的是被查看版本"
    )


def test_b02_10_send_email_log_should_be_idempotent():
    src = inspect.getsource(quote_router.send_email)
    assert "request_key" in src, "邮件发送记录无请求键，重复点击会多登记"


def test_b02_11_router_all_should_be_removed():
    src = inspect.getsource(quote_router)
    assert '__all__ = ["date", "QuoteItem"]' not in src, "路由文件尾部残留错误 __all__"


def test_b02_13_product_cost_should_have_same_day_unique_constraint():
    constraints = [c for c in pricing_model.ProductCost.__table__.constraints if type(c).__name__ == "UniqueConstraint"]
    names = {tuple(col.name for col in c.columns) for c in constraints}
    assert ("sku_id", "effective_from") in names, "同日成本只能等核价时 409"


def test_b02_16_session_dialect_should_not_swallow_all_exceptions():
    src = inspect.getsource(pricing_service._session_dialect)
    assert "except Exception" not in src, "get_bind 异常被吞后返回空串，会静默跳过 advisory 锁"


def test_b02_17_create_quote_release_failure_should_not_mask_original():
    src = inspect.getsource(quote_router.create_quote)
    block = src[src.index("except Exception"):]
    assert "await idempotency.release" not in block, "release 自身失败会替换原始异常"


# ---------- B03 ----------


def test_b03_08_draft_confirm_should_check_source_version_id():
    src = inspect.getsource(drafts.confirm_draft)
    assert "source_context" in src and "quote_version_id" in src and "!=" in src, (
        "草稿确认不校验来源版本号，可挂到另一版本"
    )


def test_b03_09_logistics_quantize_should_specify_rounding():
    src = inspect.getsource(logistics)
    assert 'quantize(Decimal("0.01"))' not in src or "rounding" in src, (
        "物流金额量化仍用默认 HALF_EVEN"
    )


def test_b03_10_exchange_rate_should_require_cny_base():
    src = inspect.getsource(pricing_router.create_exchange_rate)
    assert 'if base != "CNY"' in src or "base_currency" == "CNY" in src, (
        "汇率接口允许任意基准币种，USD→EUR 可入库"
    )


def test_b03_11_logistics_rate_strings_should_have_max_length():
    fields = pricing_schema.LogisticsRateCreate.model_fields
    checks = {"provider": 64, "remark": 255}
    missing = [n for n, limit in checks.items() if not any(getattr(m, "max_length", None) == limit for m in fields[n].metadata)]
    assert not missing, f"费率字符串无 max_length，超长到 DB 才 500：{missing}"


def test_b03_12_price_rule_customer_level_should_be_literal():
    src = inspect.getsource(pricing_schema.PriceRuleCreate)
    assert "Literal" in src, "客户等级/状态是自由字符串，E/whatever 可静默不生效"


def test_b03_13_batch_calculate_should_have_item_limit():
    src = inspect.getsource(pricing_router.batch_calculate)
    assert "max_length" in src, "批量核价 items 无条数上限，可单请求打满负载"


def test_b03_14_logistics_calculate_should_have_request_key():
    src = inspect.getsource(logistics_router.calculate)
    assert "request_key" in src, "物流试算保存无幂等键，双击多留一条"


def test_b03_15_ratios_should_be_decimal():
    src = inspect.getsource(payment_schema)
    assert "list[float]" not in src, "收款比例用 float 且拆分默认 HALF_EVEN"


def test_b03_16_logistics_quote_unit_price_should_match_basis():
    src = inspect.getsource(logistics.save_quote)
    block = src[src.index("unit_price="):src.index("eta_days")]
    assert "unit_price_per_volume" in block, "体积计价仍存元/kg 单价，留痕语义错"


def test_b03_17_delete_rate_should_take_advisory_lock():
    src = inspect.getsource(pricing_router.delete_logistics_rate)
    assert "_lock_logistics_rate_changes" in src, "删除费率绕过增改锁，可与修改并发 500"


def test_b03_18_erp_order_id_should_be_unique():
    col = order_model.SalesOrder.__table__.columns["erp_order_id"]
    assert col.unique is True, "SalesOrder.erp_order_id 无唯一约束"


def test_b03_22_pricing_import_should_log_unexpected_exception():
    src = inspect.getsource(pricing_io_router)
    assert "logger.exception" in src, "逐行 except Exception 把编程错误降级成行失败且无堆栈"


# ---------- B04 ----------


def test_b04_09_cancel_should_accept_reason():
    params = inspect.signature(order_router.cancel_order).parameters
    assert "reason" in params or "remark" in params, "取消接口无理由入参，completed 无法取消"


def test_b04_10_cancel_should_drop_batch_nodes():
    src = inspect.getsource(order_router.cancel_order)
    assert "drop_batch_node" in src, "取消订单不撤动态批次节点"


def test_b04_11_manual_order_items_should_have_limit():
    field = order_schema.OrderCreate.model_fields["items"]
    assert any(getattr(m, "max_length", None) for m in field.metadata), "手工建单明细无条数上限"


def test_b04_12_manual_order_payment_milestone_should_complete():
    src = inspect.getsource(milestones.sync_payment_milestones)
    assert "planned > 0" not in src, "手工单无应收计划，收款节点永远不完成"


def test_b04_13_convert_retry_should_replay_existing_order():
    src = inspect.getsource(order_service.create_order_from_quote)
    assert "return existing" in src, "转单重试返回 409，不是幂等回放"


def test_b04_14_receivable_should_have_unique_and_fk():
    constraints = [c for c in payment_model.ReceivablePlan.__table__.constraints if type(c).__name__ == "UniqueConstraint"]
    names = {tuple(col.name for col in c.columns) for c in constraints}
    assert ("order_id", "plan_name") in names, "应收计划无 (order_id, plan_name) 唯一约束"
    assert payment_model.PaymentRecord.__table__.columns["receivable_plan_id"].foreign_keys, (
        "payment_records.receivable_plan_id 无外键，可挂到不存在的期"
    )


def test_b04_16_convert_integrity_error_should_not_rollback_outer_tx():
    src = inspect.getsource(order_service.create_order_from_quote)
    block = src[src.index("except IntegrityError"):]
    assert "session.rollback()" not in block, "并发兜底回滚整个外层事务"


def test_b04_21_status_audit_should_capture_before_and_remark():
    src = inspect.getsource(order_router.change_status)
    block = src[src.index('action="change_status"'):src.index("ip=client_ip")]
    assert "before=" in block, "状态变更审计只写新状态，旧状态/理由不在通用审计"


# ---------- B05 ----------


def test_b05_11_finance_summary_should_zero_cancelled_unreceived():
    src = inspect.getsource(payment_service.order_finance_summary)
    assert 'status == "cancelled"' in src, "取消订单仍返回未收金额，超收显示负数"


def test_b05_13_auto_allocation_should_check_currency():
    src = inspect.getsource(payment_service.allocate_payment)
    assert "plan.currency != record.currency" in src, "自动分配不校验应收节点与回款币种"


def test_b05_14_require_confirmation_should_be_used():
    src = inspect.getsource(confirmation.require_confirmation)
    assert "CONFIRM_REQUIRED" in src
    # 路由各自拼文案，函数没有调用点
    assert "require_confirmation(" in inspect.getsource(order_router), (
        "require_confirmation 是死代码，各路由自行拼确认文案"
    )


def test_b05_15_voucher_upload_should_not_hold_lock_during_disk_io():
    src = inspect.getsource(payment_router.upload_payment_voucher)
    lock_idx = src.index("for_update=True")
    save_idx = src.index("save_upload")
    assert not (lock_idx < save_idx), "持订单/节点/回款锁做磁盘写入，慢存储会卡住财务操作"


def test_b05_20_overdue_refresh_should_record_progress():
    src = inspect.getsource(payment_service.refresh_overdue_plans)
    assert "commit_per_order" not in src or "last_order_id" in src, (
        "逐笔 commit 中途失败留下半批次，无断点/进度"
    )
