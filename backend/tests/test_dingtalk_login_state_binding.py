"""B11-01：钉钉扫码 state 必须绑定发起登录的浏览器。

两层证据：
1. service 层：同一个 state 换浏览器绑定值即拒；同一浏览器可并行多个 state。
2. 路由层：`/authorize` 下发 HttpOnly + SameSite=Lax cookie；callback 没带或带错
   cookie 时直接回登录页错误，不会进入身份交换。
"""
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import register_exception_handlers
from app.modules.auth.router import router as auth_router
from app.modules.dingtalk import service


@pytest.fixture(autouse=True)
def _clear_login_states():
    service._login_states.clear()
    yield
    service._login_states.clear()


def test_state_is_bound_to_the_browser_that_started_login():
    browser = service.ensure_login_browser_token(None)
    state = service.issue_login_state(browser)

    assert service.consume_login_state(state, browser) is True
    assert service.consume_login_state(state, browser) is False  # 一次性


def test_same_state_from_another_browser_is_rejected():
    browser = service.ensure_login_browser_token(None)
    state = service.issue_login_state(browser)

    assert service.consume_login_state(state, service.ensure_login_browser_token(None)) is False


def test_missing_browser_binding_is_rejected():
    state = service.issue_login_state(service.ensure_login_browser_token(None))

    assert service.consume_login_state(state, None) is False


def test_one_browser_can_start_multiple_login_tabs():
    browser = service.ensure_login_browser_token(None)
    first = service.issue_login_state(browser)
    second = service.issue_login_state(browser)

    assert service.consume_login_state(first, browser) is True
    assert service.consume_login_state(second, browser) is True


def test_ensure_login_browser_token_replaces_malformed_cookie():
    malformed = "not a token!"
    token = service.ensure_login_browser_token(malformed)

    assert token != malformed
    assert 32 <= len(token) <= 128


def _make_client(monkeypatch):
    app = FastAPI()
    app.include_router(auth_router, prefix=settings.api_prefix)
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    monkeypatch.setattr(settings, "dingtalk_app_key", "test-key")
    monkeypatch.setattr(settings, "dingtalk_app_secret", "test-secret")
    monkeypatch.setattr(settings, "dingtalk_login_off", False)

    class FakeClient:
        def build_login_url(self, *, redirect_uri: str, state: str) -> str:
            assert redirect_uri.endswith("/api/v1/auth/dingtalk/callback")
            return f"https://dingtalk.example/authorize?state={state}"

    monkeypatch.setattr(service, "get_client", lambda: FakeClient())
    return TestClient(app)


def _authorize(client):
    response = client.get(f"{settings.api_prefix}/auth/dingtalk/authorize")
    assert response.status_code == 200
    body = response.json()
    state = parse_qs(urlsplit(body["data"]["authorize_url"]).query)["state"][0]
    return response, state


def test_authorize_sets_httponly_samesite_cookie_and_callback_accepts_same_browser(monkeypatch):
    client = _make_client(monkeypatch)
    response, state = _authorize(client)
    cookie = response.headers["set-cookie"]

    assert service.LOGIN_STATE_COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert f"Path={service.LOGIN_STATE_COOKIE_PATH}" in cookie

    resolve = AsyncMock(return_value=(None, "not_bound"))
    monkeypatch.setattr(service, "resolve_login_user", resolve)
    callback = client.get(
        f"{settings.api_prefix}/auth/dingtalk/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 303
    error = parse_qs(urlsplit(callback.headers["location"]).query)["dingtalk_error"][0]
    assert unquote(error) == service.LOGIN_REASON_LABEL["not_bound"]
    resolve.assert_awaited_once()


def test_callback_without_browser_cookie_is_rejected_before_identity_exchange(monkeypatch):
    client = _make_client(monkeypatch)
    _response, state = _authorize(client)
    attacker = TestClient(client.app)

    resolve = AsyncMock(return_value=(None, "not_bound"))
    monkeypatch.setattr(service, "resolve_login_user", resolve)
    callback = attacker.get(
        f"{settings.api_prefix}/auth/dingtalk/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 303
    error = parse_qs(urlsplit(callback.headers["location"]).query)["dingtalk_error"][0]
    assert "登录凭据已失效" in unquote(error)
    resolve.assert_not_awaited()


def test_callback_with_wrong_browser_cookie_is_rejected_before_identity_exchange(monkeypatch):
    client = _make_client(monkeypatch)
    _response, state = _authorize(client)
    attacker = TestClient(client.app)
    attacker.cookies.set(
        service.LOGIN_STATE_COOKIE_NAME,
        "x" * 43,
        domain="testserver",
        path=service.LOGIN_STATE_COOKIE_PATH,
    )

    resolve = AsyncMock(return_value=(None, "not_bound"))
    monkeypatch.setattr(service, "resolve_login_user", resolve)
    callback = attacker.get(
        f"{settings.api_prefix}/auth/dingtalk/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 303
    error = parse_qs(urlsplit(callback.headers["location"]).query)["dingtalk_error"][0]
    assert "登录凭据已失效" in unquote(error)
    resolve.assert_not_awaited()
