"""来源地址解析：客户端不能靠伪造 X-Forwarded-For 绕过登录限流。"""

from starlette.requests import Request

from app.core.config import settings
from app.core.deps import client_ip


def request_with(*, peer: str, forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (peer, 12345),
            "query_string": b"",
        }
    )


def test_untrusted_peer_cannot_spoof_forwarded_for(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "")
    request = request_with(peer="10.0.0.8", forwarded="1.2.3.4")
    assert client_ip(request) == "10.0.0.8"


def test_trusted_proxy_uses_nearest_untrusted_forwarded_address(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.0/8")
    request = request_with(peer="10.0.0.8", forwarded="198.51.100.7, 10.1.0.2")
    assert client_ip(request) == "198.51.100.7"


def test_malformed_forwarded_for_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.0/8")
    request = request_with(peer="10.0.0.8", forwarded="not-an-ip")
    assert client_ip(request) == "10.0.0.8"
