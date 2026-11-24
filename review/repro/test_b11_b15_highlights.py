"""
B11–B15 高严重度复核复现（证伪用）。

运行：
    cd backend
    PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b11_b15_highlights.py -q

本批多数缺陷长在路由权限与并发窗口上，无法用纯函数复现；断言以
“修复后源码应满足的结构条件”为主，另加一条 httpx 异常原文的控制项。
"""
from __future__ import annotations

import inspect

import httpx
import pytest


# ---------------------------------------------------------------- B11-01
def test_b11_01_login_state_should_bind_browser():
    """state 只存在进程字典里，不绑定发起登录的浏览器 → 可被登录 CSRF 利用。"""
    from app.modules.dingtalk import service as dingtalk

    params = inspect.signature(dingtalk.consume_login_state).parameters
    assert "browser_nonce" in params or "binding" in params, (
        "consume_login_state 没有浏览器绑定参数；任何拿到合法 state 的人都能把回调发给受害者"
    )


# ---------------------------------------------------------------- B11-02
def test_b11_02_pending_password_should_block_business_api():
    """初始密码 123456 后，后端没有任何“未改密不能访问业务接口”的闸门。"""
    from app.core import deps

    source = inspect.getsource(deps.get_current_user)
    assert "initial_password_pending" in source, (
        "get_current_user 不检查 initial_password_pending，前端弹窗挡不住直接调 API"
    )


# ---------------------------------------------------------------- B12-03
def test_b12_03_upload_should_reject_protected_category():
    """通用上传/挂载直接接收客户端 category，可伪造 signed/generated。"""
    from app.modules.file import router as file_router

    source = inspect.getsource(file_router.upload_file)
    assert "PROTECTED_CATEGORIES" in source or "category" not in source, (
        "通用上传仍原样落客户端 category；可把普通附件标成 signed，绕过 sign_document"
    )


# ---------------------------------------------------------------- B13-01
def test_b13_01_customer_360_amount_should_exclude_cancelled_orders():
    from app.modules.customer import service as customer_service

    source = inspect.getsource(customer_service.customer_overview)
    assert 'SalesOrder.status != "cancelled"' in source, (
        "客户 360 的订单金额汇总没有排除取消单，累计成交会虚高"
    )


# ---------------------------------------------------------------- B13-02
def test_b13_02_merge_should_whitelist_tax_no_resolution():
    """税号冲突只判 resolutions 非空，传任意字符串即可合并两家不同主体。"""
    from app.modules.customer import tags

    source = inspect.getsource(tags.merge_customers)
    assert '"tax_no"' in source and '"confirm"' in source, (
        "merge_customers 没有校验 tax_no 冲突的取值；任意非空字符串都能通过"
    )


# ---------------------------------------------------------------- B14-01
def test_b14_01_transfer_should_recheck_scope_inside_lock():
    """路由先判可见，服务拿锁后不再 assert_customer_visible。"""
    from app.modules.customer import service as customer_service

    source = inspect.getsource(customer_service.transfer_customer)
    assert "assert_customer_visible" in source, (
        "transfer_customer 锁内没有复查数据范围；并发转手后可越权改归属"
    )


# ---------------------------------------------------------------- B14-02
def test_b14_02_merge_should_recheck_price_permission_inside_service():
    """价格权限预检查在锁外，锁内新增价格冲突后服务不再复查权限。"""
    from app.modules.customer import tags

    params = inspect.signature(tags.merge_customers).parameters
    assert "can_manage_price" in params or "user" in params, (
        "merge_customers 不接收调用者/价格权限，锁外预检查过期后仍会执行价格裁决"
    )


# ---------------------------------------------------------------- B15-01
def test_b15_01_bind_customer_should_require_customer_scope():
    """bind-customer 只要求 wecom:manage，且用 get_customer_or_404 而非可见范围。"""
    from app.modules.wecom import router as wecom_router

    source = inspect.getsource(wecom_router.bind_customer)
    assert "customer:view" in source or "customer:assign" in source, (
        "bind-customer 没有客户权限/数据范围依赖，可把联系人挂到别人的私有客户"
    )


# ---------------------------------------------------------------- B15-02
def test_b15_02_candidates_should_apply_data_scope():
    """待归一候选按全库匹配，只要求 wecom:view。"""
    from app.modules.wecom import service as wecom_service

    source = inspect.getsource(wecom_service.unbound_candidates)
    assert "owner_ids=" in source, (
        "unbound_candidates 不传 owner_ids，候选会把全公司客户暴露给 wecom:view"
    )


# ---------------------------------------------------------------- B15-03
def test_b15_03_control_httpx_error_leaks_token():
    """控制项：HTTPStatusError 的字符串确实带 URL query，证明泄露不是臆造。"""
    request = httpx.Request(
        "GET",
        "https://qyapi.weixin.qq.com/cgi-bin/user/list?access_token=SECRET-TOKEN",
    )
    response = httpx.Response(500, request=request)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        response.raise_for_status()
    assert "SECRET-TOKEN" in str(caught.value)


def test_b15_03_single_item_error_should_be_redacted():
    from app.modules.wecom import service as wecom_service

    source = inspect.getsource(wecom_service.sync_follow_relations)
    assert "redact" in source or "access_token" not in source and "str(error)" not in source, (
        "单条失败仍把 str(error) 原样写进 job.error_message，可能带 access_token"
    )
