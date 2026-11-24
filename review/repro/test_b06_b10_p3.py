"""B06–B10 的 P3 逐条证伪（15 条）。"""

from __future__ import annotations

import inspect

from app.modules.approval import model as approval_model
from app.modules.erp import collection, reconcile, router as erp_router, service as erp_service
from app.modules.contract import customer_flow, model as contract_model, service as contract_service
from app.modules.oa import sample as oa_sample, service as oa_service


# ---------- B06 ----------


def test_b06_11_serialize_record_should_expose_exact():
    src = inspect.getsource(collection.serialize_record)
    assert "amount_exact" in src and "quantity_exact" in src, "外部记录只出 float，无 exact"


def test_b06_12_exception_queue_should_include_failed():
    assert "FAILED" in erp_service.EXCEPTION_QUEUE_STATUS, "被状态守卫拦下的 failed 事件不进异常队列"


def test_b06_13_push_payload_shop_id_should_not_be_hardcoded_none():
    src = inspect.getsource(erp_service._build_push_payload)
    assert '"shop_id": None' not in src, "推单 shop_id 固定 None，多店铺无法区分"


def test_b06_14_list_mappings_should_show_scoped_unmatched():
    src = inspect.getsource(collection.list_mappings)
    assert "internal_id is None" in src or "external_code" in src, "有限范围用户看不到范围内待匹配订单"


def test_b06_17_fail_quietly_should_log_when_failure_persist_fails():
    src = inspect.getsource(collection._fail_quietly)
    assert "logger.exception" in src or "logger.error" in src, "失败落盘再失败被 pass 吞掉，无日志/告警"


def test_b06_18_push_success_audit_should_be_same_transaction():
    src = inspect.getsource(erp_router.push_order)
    service_src = inspect.getsource(erp_service.push_order)
    assert "await session.commit()" not in service_src or "write_audit" in service_src, (
        "服务先 commit，路由再审计，审计失败留下无审计的已推单"
    )


# ---------- B07 ----------


def test_b07_11_reconcile_audit_should_be_same_transaction():
    src = inspect.getsource(reconcile.run_reconciliation)
    assert "await session.commit()" not in src or "write_audit" in src, (
        "对账服务先 commit，路由后审计，失败留下无审计批次"
    )


def test_b07_12_contract_request_key_should_have_orm_unique():
    constraints = [c for c in contract_model.ContractDocument.__table__.constraints if type(c).__name__ == "UniqueConstraint"]
    names = {tuple(col.name for col in c.columns) for c in constraints}
    assert ("request_key",) in names, "合同 request_key 唯一约束只在迁移，ORM/测试建表没有"


def test_b07_13_erp_webhook_should_use_hmac_timestamp():
    src = inspect.getsource(erp_router)
    assert "hmac" in src.lower() or "X-ERP-Timestamp" in src, "ERP 回调只共享密钥，无 HMAC/时间戳/事件签名"


def test_b07_14_sync_approval_should_lock_document_before_snapshot():
    src = inspect.getsource(customer_flow.sync_approval)
    assert "for_update=True" in src, "同步审批先读文档不加锁，可能写已签/已作废合同"


# ---------- B08 ----------


def test_b08_04_cancel_safe_should_not_reshield_same_coroutine():
    src = inspect.getsource(contract_service._cancel_safe)
    assert "asyncio.shield(coro)" not in src or "factory" in src or "task" in src, (
        "_cancel_safe 重试复用同一个 coroutine，第二次 shield 必 RuntimeError"
    )


# ---------- B09 ----------


def test_b09_04_rule_version_should_have_unique_constraint():
    constraints = [c for c in approval_model.ApprovalRuleVersion.__table__.constraints if type(c).__name__ == "UniqueConstraint"]
    names = {tuple(col.name for col in c.columns) for c in constraints}
    assert ("rule_id", "version_no") in names, "规则发布快照无唯一约束，只靠咨询锁防重"


# ---------- B10 ----------


def test_b10_08_uploaded_attachments_should_be_cleaned_on_state_change():
    src = inspect.getsource(oa_service.send_prepared_approval)
    block = src[src.index("uploads.fill"):]
    assert "delete_approval" in block or "cleanup" in block, "附件先上传后状态变化，OA 侧孤儿文件无清理"


def test_b10_09_sample_status_should_follow_oa_return():
    src = inspect.getsource(oa_sample.start_sample_approval)
    assert 'summary["status"]' in src and "approved" in src, (
        "OA 创建返回 approved/rejected 时本地打样单仍置 pending"
    )


def test_b10_10_send_should_catch_unexpected_and_mark_failure():
    src = inspect.getsource(oa_service.send_prepared_approval)
    assert "except Exception" in src, "只捕 OaError，意外异常留下 submitting 且失败无审计"
