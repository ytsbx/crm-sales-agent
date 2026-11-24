"""B11–B15 的 P3 逐条证伪（9 条）。"""

from __future__ import annotations

import inspect

from app.modules.customer import router as customer_router, tags as customer_tags, tags_router
from app.modules.file import router as file_router
from app.modules.notification import service as notification_service, visibility
from app.modules.user import schema as user_schema, service as user_service
from app.modules.wecom import client as wecom_client, schema as wecom_schema, service as wecom_service


# ---------- B11 ----------


def test_b11_12_role_scope_and_status_should_be_enum():
    create_src = inspect.getsource(user_schema.RoleCreate)
    update_src = inspect.getsource(user_schema.RoleUpdate)
    assert "Literal" in create_src and "Literal" in update_src, (
        "RoleCreate/RoleUpdate 的 data_scope/status 只是字符串，非法值静默退回 self"
    )


# ---------- B12 ----------


def test_b12_06_upload_should_validate_metadata_before_disk_write():
    src = inspect.getsource(file_router.upload_file)
    save_idx = src.index("save_upload")
    head = src[:save_idx]
    assert "len(file.filename" in head or "max_length" in head, (
        "上传文件名/MIME/category 未按列宽前置校验，先写盘再 500"
    )


# ---------- B13 ----------


def test_b13_10_single_customer_create_should_require_request_key():
    src = inspect.getsource(customer_router.create_customer)
    assert "if not request_key" in src or "request_key 不能为空" in src, (
        "单建客户允许不带 request_key，双击仍会产生重复客户"
    )


def test_b13_11_tag_write_should_use_savepoint_not_full_rollback():
    src = inspect.getsource(customer_tags.create_tag)
    block = src[src.index("except IntegrityError"):]
    assert "session.rollback()" not in block, "标签撞唯一约束回滚整个 session"


# ---------- B14 ----------


def test_b14_06_wecom_create_customer_should_validate_lengths():
    fields = wecom_schema.CreateCustomerRequest.model_fields
    checks = {"name": 200, "short_name": 100, "region": 64, "address": 255, "level": 8, "source": 32}
    missing = [n for n, limit in checks.items()
               if not any(getattr(m, "max_length", None) == limit for m in fields[n].metadata)]
    assert not missing, f"企微建客户字段无 max_length，超长到 DB 才 500：{missing}"


def test_b14_07_token_cache_should_be_locked_and_secret_aware():
    src = inspect.getsource(wecom_client.WeComClient.access_token)
    assert "asyncio.Lock" in src or "lock" in src, "token 缓存无锁，并发首次取 token 会重复请求"
    assert "secret" in src.lower() and ("hash" in src or "fingerprint" in src), (
        "缓存键不含密钥版本，轮换 secret 后仍返回旧 token"
    )


def test_b14_08_batch_tag_replace_audit_should_capture_before():
    src = inspect.getsource(tags_router.batch_tag)
    block = src[src.index('action=f"batch_tag_'):src.index("ip=client_ip")]
    assert "before" in block, "批量 replace 审计只记目标 tag_ids，不记变更前标签集合"


# ---------- B15 ----------


def test_b15_07_contact_sync_success_should_not_override_partial():
    src = inspect.getsource(wecom_service.sync_contact_events)
    assert 'job.status = "success"' not in src, (
        "成功分支强制 status=success，但 fail_count 仍保留，任务成功与失败次数矛盾"
    )


def test_b15_08_notification_materialize_should_filter_all_deleted_sources():
    src = inspect.getsource(notification_service.materialize_business_notifications)
    assert "source_type == 'quote' and source.deleted_at" not in src, (
        "materialize 只对 quote 判 deleted_at，其他已删单据仍发通知"
    )
    vis = inspect.getsource(visibility)
    assert vis.count("deleted_at.is_(None)") >= 6, "过程通知权限 SQL 只对 quote 过滤软删"
