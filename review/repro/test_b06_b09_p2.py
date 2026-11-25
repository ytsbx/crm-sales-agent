"""
B06–B09 P2 逐条复现（证伪用）。

运行：
    cd backend
    PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b06_b09_p2.py -q

断言写“修复后应有的行为”。本文件只覆盖 P2，P3 不混入。
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------- B06-06
def test_b06_06_erp_order_id_should_be_unique():
    from app.modules.order.model import SalesOrder

    assert SalesOrder.__table__.c.erp_order_id.unique is True, (
        "sales_orders.erp_order_id 没有唯一约束；历史重复会让回读/匹配的 .first() 挂错单"
    )


# ---------------------------------------------------------------- B06-07
def test_b06_07_missing_currency_should_not_default_to_cny():
    from app.modules.erp.collection import normalize_item

    item = normalize_item({"external_id": "SO-NO-CURRENCY"}, object_type="order", position=1)
    assert item["currency"] is None, (
        f"缺币种被默认成 {item['currency']!r}；外币缺失会静默按人民币入账"
    )


# ---------------------------------------------------------------- B06-08
def test_b06_08_replay_should_lock_rows():
    from app.modules.erp import collection

    source = inspect.getsource(collection.replay_pending_mappings)
    assert "with_for_update" in source, (
        "重放只 select 不加锁；并发人工匹配提交后会被旧 pending 结果覆盖"
    )


# ---------------------------------------------------------------- B06-09
def test_b06_09_unmapped_status_should_not_be_success():
    from app.modules.erp import service as erp_service

    source = inspect.getsource(erp_service.apply_status_webhook)
    assert "if new_status is None" in source, (
        "未知 raw_status 没有单独分支，仍落 success/processed；补映射后同一事件不会重放"
    )


# ---------------------------------------------------------------- B06-10
def test_b06_10_collection_status_should_accept_scope():
    from app.modules.erp.collection import collection_status

    params = inspect.signature(collection_status).parameters
    assert "owner_ids" in params or "shop_ids" in params, (
        "collection_status 不接收范围；order:view 能看到全公司水位/来源证据/计数"
    )


# ---------------------------------------------------------------- B07-05
def test_b07_05_manual_diff_should_require_amount():
    from app.modules.erp.schema import DiffConfirmRequest

    assert DiffConfirmRequest.model_fields["financial_adjustment_amount"].is_required(), (
        "售后人工核定金额可选；不填会生成 amount=NULL 的待处理退款台账"
    )


# ---------------------------------------------------------------- B07-06
def test_b07_06_confirm_audit_should_include_adjustment():
    from app.modules.erp import router as erp_router

    source = inspect.getsource(erp_router.confirm_reconcile_diff)
    assert '"financial_adjustment"' in source, (
        "核定审计 after 只有 status/resolution/note，没有生成的调整台账 id/金额/类型"
    )


# ---------------------------------------------------------------- B07-07
def test_b07_07_reconcile_runs_should_accept_scope():
    from app.modules.erp.reconcile import list_runs

    params = inspect.signature(list_runs).parameters
    assert "owner_ids" in params or "shop_ids" in params, (
        "对账批次列表只按 system_type 过滤；order:view 能看到全公司批次计数"
    )


# ---------------------------------------------------------------- B07-08
def test_b07_08_null_occurred_at_should_not_be_missing_external():
    from app.modules.erp import reconcile

    source = inspect.getsource(reconcile.run_reconciliation)
    assert "occurred_at.is_(None)" in source, (
        "seen_external 只收本期 occurred_at 的订单；外部单缺时间会被判成本期缺失且不会自动关"
    )


# ---------------------------------------------------------------- B07-09
def test_b07_09_confirm_should_recheck_scope_inside_lock():
    from app.modules.erp.reconcile import confirm_diff

    params = inspect.signature(confirm_diff).parameters
    assert "owner_ids" in params or "user" in params, (
        "confirm_diff 锁内不复查范围；路由检查后 internal_id 可被并发改写"
    )


# ---------------------------------------------------------------- B07-10
def test_b07_10_manual_sync_should_write_audit():
    from app.modules.contract import customer_flow

    source = inspect.getsource(customer_flow.sync_approval)
    assert "write_audit" in source, (
        "手工同步 OA 审批不写审计；审批从 pending 变 approved 无操作人/前后状态"
    )


# ---------------------------------------------------------------- B07-15
def test_b07_15_sign_audit_should_include_amount_and_oa():
    from app.modules.contract import router as contract_router

    source = inspect.getsource(contract_router.sign_document)
    for key in ("amount", "quote_version_id", "oa_instance_id"):
        assert key in source, f"签署审计缺 {key}，证据链无法回答“签的是哪一版、多少钱”"


# ---------------------------------------------------------------- B08-01
def test_b08_01_customer_level_should_not_be_a_template_token():
    from app.modules.bizdoc.tokens import SOURCE_FIELDS

    assert "level" not in SOURCE_FIELDS["customer"], (
        "模板白名单仍允许 customer.level，内部评级可被印到对客单据"
    )


# ---------------------------------------------------------------- B08-02（控制项：应为已修复）
def test_b08_02_pdf_footer_should_escape_gaps():
    from app.modules.bizdoc import pdf

    source = inspect.getsource(pdf)
    assert "_esc(gap.get('label'))" in source and "_esc(gap.get('display'))" in source, (
        "未留存项页脚没有转义，SKU 名称含 <& 时 PDF 500"
    )


# ---------------------------------------------------------------- B08-03
def test_b08_03_template_remark_should_be_length_limited():
    from app.modules.bizdoc.router import TemplateCreate

    with pytest.raises(ValidationError):
        TemplateCreate(doc_type="quote", name="t", remark="x" * 256)


# ---------------------------------------------------------------- B09-03
def test_b09_03_definition_update_should_be_length_limited():
    from app.modules.approval.schema import ApprovalDefinitionUpdate

    field = ApprovalDefinitionUpdate.model_fields["name"]
    assert any(getattr(m, "max_length", None) == 128 for m in field.metadata), (
        "ApprovalDefinitionUpdate.name 没有 128 长度约束，129 字会到数据库才 500"
    )


# ---------------------------------------------------------------- B09-05
def test_b09_05_approval_amount_should_not_be_float():
    from app.modules.approval import router as approval_router

    source = inspect.getsource(approval_router)
    assert '"total_amount": float(version.total_amount)' not in source, (
        "审批列表/详情金额仍转 float；审批确认入口的金额精度会丢"
    )
