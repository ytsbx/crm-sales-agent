"""B11-11：登录回调域名白名单。

钉钉扫码登录的 redirect_uri / 跳回地址是用请求 Host 拼的；Host 可伪造。
生产反代目前只转 crm.mandla.cn，但那是部署配置而非代码约束 —— 这一批把
白名单挪进应用配置，并用测试钉住"伪造 Host 进不了会拼地址的两个入口"。
"""

import json

import pytest
from starlette.requests import Request

from app.core import auth_host_guard as guard
from app.core.config import settings
from app.core.errors import ErrorCode


def _request(path: str, host: str | None) -> Request:
    headers = [(b"host", host.encode())] if host is not None else []
    return Request({"type": "http", "method": "GET", "path": path, "headers": headers})


async def _called(_request):
    return "passed"


def test_normalize_host_accepts_scheme_port_and_case():
    assert guard.normalize_host("crm.mandla.cn") == "crm.mandla.cn"
    assert guard.normalize_host("https://crm.mandla.cn/login") == "crm.mandla.cn"
    assert guard.normalize_host("CRM.MANDLA.CN:443") == "crm.mandla.cn"
    assert guard.normalize_host("") == ""


@pytest.mark.anyio
async def test_empty_allowlist_disables_guard(monkeypatch):
    monkeypatch.setattr(settings, "auth_callback_hosts", "")
    request = _request("/api/v1/auth/dingtalk/authorize", "evil.example")
    assert await guard.auth_callback_host_guard(request, _called) == "passed"


@pytest.mark.anyio
@pytest.mark.parametrize("path", [
    "/api/v1/auth/dingtalk/authorize",
    "/api/v1/auth/dingtalk/callback",
])
async def test_spoofed_host_is_rejected_before_building_redirect(monkeypatch, path):
    monkeypatch.setattr(settings, "auth_callback_hosts", "crm.mandla.cn")
    response = await guard.auth_callback_host_guard(_request(path, "evil.example"), _called)
    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["code"] == ErrorCode.PARAM_ERROR
    assert "白名单" in body["message"]


@pytest.mark.anyio
async def test_allowed_host_with_port_passes(monkeypatch):
    monkeypatch.setattr(settings, "auth_callback_hosts", "https://crm.mandla.cn")
    response = await guard.auth_callback_host_guard(
        _request("/api/v1/auth/dingtalk/callback", "crm.mandla.cn:443"), _called
    )
    assert response == "passed"


@pytest.mark.anyio
async def test_other_paths_are_not_affected(monkeypatch):
    monkeypatch.setattr(settings, "auth_callback_hosts", "crm.mandla.cn")
    response = await guard.auth_callback_host_guard(
        _request("/api/v1/orders", "evil.example"), _called
    )
    assert response == "passed"


def test_middleware_is_wired_into_app(monkeypatch):
    """接线也要钉住：守卫写得再对，没挂到应用上等于没有。"""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "auth_callback_hosts", "crm.mandla.cn")
    client = TestClient(app)
    response = client.get(
        "/api/v1/auth/dingtalk/authorize", headers={"host": "evil.example"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == ErrorCode.PARAM_ERROR
