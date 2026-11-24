"""P2/B11-11、B12-01、B12-02、B14-04：外部基址、审计金额、错误回显、企微任务索引。"""
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from starlette.requests import Request  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402

from app.core.audit import json_safe  # noqa: E402
from app.core.errors import register_exception_handlers  # noqa: E402
from app.modules.auth.router import _external_base  # noqa: E402
from app.modules.auth.schema import LoginRequest  # noqa: E402
from app.modules.wecom.model import WeComSyncJob  # noqa: E402


def fake_request(host: str, proto: str = "http"):
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": proto,
        "path": "/", "raw_path": b"/", "query_string": b"", "root_path": "",
        "headers": [(b"host", host.encode()), (b"x-forwarded-proto", proto.encode())],
        "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1234),
    })


def main():
    print(f"[B11-11] Host=evil.example.com -> {_external_base(fake_request('evil.example.com', 'https'))}")

    value = json_safe({"amount": Decimal("12.30")})
    print(f"[B12-01] Decimal 12.30 -> {value['amount']!r} type={type(value['amount']).__name__}")

    long_password = "p" * 200
    try:
        LoginRequest(username="u", password=long_password)
    except ValidationError as exc:
        wrapped = RequestValidationError(exc.errors())
        handler = register_exception_handlers.__globals__["_validation_error"] if False else None
        from app.main import app
        handler = app.exception_handlers[RequestValidationError]
        resp = asyncio.run(handler(None, wrapped))
        body = json.loads(resp.body)
        leaked = long_password in resp.body.decode()
        print(f"[B12-02] status={resp.status_code} 原文是否回显={leaked} input 长度={len(body['data'][0]['input'])}")

    idx = [(i.name, i.unique, [c.name for c in i.columns]) for i in WeComSyncJob.__table__.indexes]
    print(f"[B14-04] WeComSyncJob indexes={idx}")


if __name__ == "__main__":
    main()
