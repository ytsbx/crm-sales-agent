"""
B10–B14 P2 复现（证伪用，静态断言为主）。

运行：
    cd backend
    PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b10_b14_p2.py -q

本批多数缺陷在权限/并发路径上，无法纯函数复现；断言以“修复后源码/模型应满足”为准。
B10-04 的 userid_by_unionid 是控制项，验证报告把已包装的函数也算进裸解析。
"""
from __future__ import annotations

import inspect


# ---------------------------------------------------------------- B10-02
def test_b10_02_self_oa_attachments_should_use_config_business_type():
    from app.modules.oa import inquiry

    source = inspect.getsource(inquiry._attachments)
    assert "inquiry_file_business_type" in source, (
        "自研 OA 附件仍写死 business_type='inquiry'，配置成别的类型就取不到附件"
    )


# ---------------------------------------------------------------- B10-04
def test_b10_04_dept_reads_should_wrap_httpx_and_json():
    from app.modules.dingtalk.client import DingTalkClient

    source = inspect.getsource(DingTalkClient.get_user_dept_ids)
    assert "except (httpx.HTTPError" in source or "except httpx.HTTPError" in source, (
        "get_user_dept_ids 没包 httpx/JSON 异常，超时或 HTML 错误页会 500"
    )


def test_b10_04_control_userid_by_unionid_is_wrapped():
    from app.modules.dingtalk.client import DingTalkClient

    source = inspect.getsource(DingTalkClient.userid_by_unionid)
    assert "钉钉成员查询响应不是合法 JSON" in source, (
        "userid_by_unionid 其实已经包了 httpx/JSON；报告把它算进“裸解析”是子项误报"
    )


# ---------------------------------------------------------------- B10-05
def test_b10_05_image_url_should_use_configured_base():
    from app.modules.dingtalk import router as dingtalk_router

    source = inspect.getsource(dingtalk_router)
    assert "settings.public_base_url" in source or "public_base_url" in source, (
        "产品参考图片 URL 仍写死 crm.mandla.cn，换环境必现钉钉取不到图"
    )


# ---------------------------------------------------------------- B10-07
def test_b10_07_single_candidate_without_name_match_should_be_none():
    from app.modules.dingtalk.client import DingTalkClient

    source = inspect.getsource(DingTalkClient.search_user_id_by_name)
    assert "if user_name is None and len(candidates) == 1" in source, (
        "无精确姓名匹配时仍返回唯一候选；姓名不同也会绑错人"
    )


# ---------------------------------------------------------------- B11-04
def test_b11_04_disable_should_revoke_sessions():
    from app.modules.user import service as user_service

    source = inspect.getsource(user_service.set_user_status)
    assert "revoke_user_sessions" in source, (
        "停用账号不吊销会话；重新启用后旧 token 直接复活"
    )


# ---------------------------------------------------------------- B11-06
def test_b11_06_department_create_should_lock_or_unique():
    from app.modules.user import service as user_service
    from app.modules.user.model import Department

    source = inspect.getsource(user_service.create_department)
    has_unique = any(
        getattr(idx, "unique", False) for idx in Department.__table__.indexes
    )
    assert "with_for_update" in source or has_unique, (
        "部门创建先查后插无锁、表上也无 (parent_id,name) 唯一约束，并发会出同名同级部门"
    )


# ---------------------------------------------------------------- B11-11
def test_b11_11_external_base_should_use_config_host():
    from app.modules.auth import router as auth_router

    source = inspect.getsource(auth_router._external_base)
    assert "settings.public_base_url" in source or "allowed_hosts" in source, (
        "_external_base 直接信任 Host；恶意 Host 会把一次性 ticket 303 到攻击者域名"
    )


# ---------------------------------------------------------------- B12-02
def test_b12_02_validation_error_should_mask_secrets():
    from app.core import errors

    source = inspect.getsource(errors._validation_error)
    assert "password" in source and ("***" in source or "redact" in source), (
        "参数校验错误仍回显原始 input；密码超长时响应体里有密码原文"
    )


# ---------------------------------------------------------------- B12-04
def test_b12_04_delete_should_recheck_scope_after_lock():
    from app.modules.file import router as file_router

    source = inspect.getsource(file_router.delete_file)
    assert source.count("await can_access_file") >= 2, (
        "删除文件只在取锁前判可见，锁后不复查；并发改派后可删范围外文件"
    )


# ---------------------------------------------------------------- B13-03
def test_b13_03_merge_should_handle_duplicate_cases():
    from app.modules.customer import tags

    assert any("CustomerDuplicateCase" in str(item) for item in tags.MERGE_TARGETS), (
        "合并 MERGE_TARGETS 不含 CustomerDuplicateCase；来源软删后 pending 案件永远裁不了"
    )


# ---------------------------------------------------------------- B13-04
def test_b13_04_merge_should_check_inherited_owner_active():
    from app.modules.customer import tags

    source = inspect.getsource(tags.merge_customers)
    assert 'status == "active"' in source or "ensure_owner_active" in source, (
        "合并继承来源负责人不校验在职，目标客户会挂到停用账号"
    )


# ---------------------------------------------------------------- B14-03
def test_b14_03_merge_preview_should_require_price_permission():
    from app.modules.customer import tags_router

    source = inspect.getsource(tags_router.merge_preview)
    assert "price:manage" in source, (
        "merge-preview 只要 customer:view，却把专属价具体价格明文返回"
    )


# ---------------------------------------------------------------- B14-04
def test_b14_04_model_should_match_migration_unique_index():
    from app.modules.wecom.model import WeComSyncJob

    names = {idx.name for idx in WeComSyncJob.__table__.indexes}
    assert "uq_wecom_sync_event_fingerprint" in names, (
        "模型没有 event_fingerprint 唯一索引，只有迁移有；create_all 建库会丢并发保护"
    )
