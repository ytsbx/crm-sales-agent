"""企微 SSO 通道关闭：不得用前端自报身份换令牌（24 号交接说明 §8.1）。

修前复现（2026-10-06 实测，直接调用路由函数 + 假 session，不连库不连网）：
把 `wecom_contact_ready` 需要的两项设置造出来后，请求体只要带
`{"wecom_userid": "zhangsan", "state": "forged-state"}` 就能拿到 access_token；
同一个 state 原样再发一次**又**签发一次；审计里留下 action='sso_login' 的
成功记录——即"报一个工号 = 登录成那个人"，且事后与真实登录无法区分。

本文件把 §8.1 的验收固化成离线断言：已配环境下任意 userid、伪造/重放 state、
失效 code 均不得签发；已停用/未绑定用户不得登录；链路未接通必须明确报错。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.modules.auth import router as auth_router
from app.modules.auth.schema import WeComSsoCallback

CALLBACK = auth_router.wecom_sso_callback


def _configured(monkeypatch, ready: bool):
    """造出/撤掉 `settings.wecom_contact_ready` 依赖的两项配置（等价 WECOM_* 环境变量）。"""
    monkeypatch.setattr(settings, "wecom_corp_id", "ww-fake-corp" if ready else "")
    monkeypatch.setattr(
        settings, "wecom_contact_secret", "fake-contact-secret" if ready else ""
    )
    assert settings.wecom_contact_ready is ready


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="10.0.0.9"), headers={})


def _forbid_signing(monkeypatch):
    """让"签发令牌"和"记一条成功登录审计"本身抛错。

    拒绝路径必须在这两个动作之前就结束：如果实现又走回了签发/审计，
    这里抛的是 AssertionError（不是 AppError），测试会直接失败——不会被
    `pytest.raises(AppError)` 误当成"拒绝成功"。
    """

    def boom(*args, **kwargs):
        raise AssertionError("关闭的企微 SSO 通道不得签发令牌或写成功审计")

    monkeypatch.setattr(auth_router, "create_access_token", boom)
    monkeypatch.setattr(auth_router, "write_audit", boom)


def _call(payload: WeComSsoCallback) -> AppError:
    with pytest.raises(AppError) as caught:
        asyncio.run(CALLBACK(payload=payload, request=_request()))
    return caught.value


# ---------------------------------------------------------------- 不得签发令牌


def test_configured_environment_refuses_self_reported_userid(monkeypatch):
    """已配环境：只报一个 wecom_userid（含别人的、从没绑定过的）也拿不到令牌。"""
    _configured(monkeypatch, ready=True)
    _forbid_signing(monkeypatch)
    payload = WeComSsoCallback.model_validate(
        {"wecom_userid": "zhangsan", "state": "forged-state"}
    )

    error = _call(payload)

    assert error.code == ErrorCode.EXTERNAL_ERROR
    assert error.http_status == 503
    assert "未配置" in error.message, error.message
    assert "wecom_userid" in error.message, error.message
    # 报错必须能让人定位缺什么，而不是笼统的"登录失败"
    assert "WECOM_CORP_ID" in error.message or "授权 code" in error.message


@pytest.mark.parametrize(
    "body",
    [
        {"wecom_userid": "any-colleague"},
        {"wecom_userid": "disabled-user", "state": "forged"},
        {"wecom_userid": "never-bound-user", "code": "expired-or-fake-code"},
        {"code": "expired-or-fake-code", "state": "forged-state"},
        {"code": "replayed-code", "state": "replayed-state"},
        {},
    ],
)
def test_configured_environment_never_issues_token(monkeypatch, body):
    """已配环境下这些入参组合（自报 userid / 伪造 state / 失效 code / 空体）都不得签发。"""
    _configured(monkeypatch, ready=True)
    _forbid_signing(monkeypatch)

    error = _call(WeComSsoCallback.model_validate(body))

    assert error.http_status == 503
    assert error.code == ErrorCode.EXTERNAL_ERROR


def test_replayed_state_is_refused_again(monkeypatch):
    """同一 state 重放第二次同样拒绝：关闭期间不存在"第一次过了、第二次也能过"。"""
    _configured(monkeypatch, ready=True)
    _forbid_signing(monkeypatch)
    payload = WeComSsoCallback.model_validate({"code": "c", "state": "same-state"})

    first = _call(payload)
    second = _call(payload)

    assert first.http_status == second.http_status == 503


def test_disabled_or_unbound_userid_cannot_login(monkeypatch):
    """已停用/未绑定的员工标识不得登录（关闭期间连"查绑定关系"这一步都不该发生）。"""
    _configured(monkeypatch, ready=True)
    _forbid_signing(monkeypatch)

    for userid in ("disabled-user", "never-bound-user"):
        error = _call(WeComSsoCallback.model_validate({"wecom_userid": userid}))
        assert error.http_status == 503, userid


def test_callback_does_not_need_database(monkeypatch):
    """拒绝路径不得依赖数据库：没有 DB 也要能明确报错，而不是 500。"""
    _configured(monkeypatch, ready=True)
    _forbid_signing(monkeypatch)
    # 没有任何 session/db 可传：签名里若还留着数据库依赖，本用例会直接 TypeError
    error = _call(WeComSsoCallback.model_validate({"wecom_userid": "zhangsan"}))
    assert error.message


# ---------------------------------------------------------------- 接入状态如实


def test_unconfigured_environment_reports_missing_credentials(monkeypatch):
    """未配凭据：报错指名缺哪个配置，不假装能登录。"""
    _configured(monkeypatch, ready=False)
    _forbid_signing(monkeypatch)

    error = _call(WeComSsoCallback.model_validate({"wecom_userid": "x"}))

    assert error.http_status == 503
    assert "WECOM_CORP_ID" in error.message


def test_legacy_body_message_keeps_saying_not_configured(monkeypatch):
    """兼容既有回归：`check_lead_auth_role_api` 发 {'wecom_userid': 'x'} 时，
    仍要求报错里出现"未配置"，且必须是业务错误码而不是参数校验错误。"""
    _configured(monkeypatch, ready=False)
    _forbid_signing(monkeypatch)

    error = _call(WeComSsoCallback.model_validate({"wecom_userid": "x"}))

    assert "未配置" in error.message
    assert error.code != ErrorCode.PARAM_ERROR


def test_schema_no_longer_declares_self_reported_identity():
    """入参契约里不再有 wecom_userid：它从字段层面就不是身份来源了。"""
    assert "wecom_userid" not in WeComSsoCallback.model_fields
    assert {"code", "state"} <= set(WeComSsoCallback.model_fields)


# ---------------------------------------------------------------- 既有行为保留


def test_other_auth_endpoints_unchanged():
    """账号密码登录、refresh、logout、/me、/permissions 的入口保持原样。"""
    paths = {route.path for route in auth_router.router.routes}

    assert {
        "/auth/login",
        "/auth/refresh",
        "/auth/logout",
        "/auth/me",
        "/auth/permissions",
        "/auth/sso/wecom/callback",
    } <= paths
