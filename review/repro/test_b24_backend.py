"""
B24 复核用复现（只读业务代码，测试放在 review/repro）。

运行：
    cd backend
    PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b24_backend.py -q

断言写的是“修复后应有的行为”，当前代码上失败即为证据。
不连接数据库、不写业务库。
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.similarity import normalize_name


# ---------------------------------------------------------------- B24-01
def _cors_app(origins: list[str]):
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def test_b24_01_wildcard_plus_credentials_should_not_reflect_any_origin():
    """CORS_ORIGINS=* 时，不应把任意 Origin 回显并允许凭据。

    审查员说“浏览器会拒绝（登录失败）”；Starlette 1.7 的实际行为是先写
    `Access-Control-Allow-Origin: *`，再因为 allow_credentials=True 用请求 Origin
    覆盖成任意来源。这个测试直接断言安全行为，当前实现必然失败。
    """
    client = TestClient(_cors_app(["*"]))
    resp = client.get("/ping", headers={"Origin": "https://evil.example"})
    allow_origin = resp.headers.get("access-control-allow-origin")
    allow_cred = resp.headers.get("access-control-allow-credentials")
    print(f"\n[实际响应] ACAO={allow_origin!r} ACAC={allow_cred!r}")
    assert allow_origin != "https://evil.example", "任意 Origin 被回显（带上凭据许可）"
    assert allow_cred != "true", "通配来源不应同时允许凭据"


def test_b24_01_preflight_also_reflects_evil_origin():
    client = TestClient(_cors_app(["*"]))
    resp = client.options(
        "/ping",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    allow_origin = resp.headers.get("access-control-allow-origin")
    print(f"\n[预检响应] status={resp.status_code} ACAO={allow_origin!r}")
    assert allow_origin != "https://evil.example", "预检也回显了任意 Origin"


def test_b24_01_config_should_reject_wildcard_with_credentials():
    """配置层应在启动时拒绝 `*`（当前只有 startswith 的拆分，没有校验）。"""
    from app.core.config import Settings

    with pytest.raises(Exception):
        Settings(
            cors_origins="*",
            jwt_secret="x" * 32,
            _env_file=None,
        )


# ---------------------------------------------------------------- B24-02
def test_b24_02_health_logs_db_failure(monkeypatch, caplog):
    from app.main import health

    class BoomSession:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: BoomSession())

    with caplog.at_level(logging.WARNING):
        import asyncio

        payload = asyncio.run(health())

    print(f"\n[health 响应] {payload}")
    print(f"[捕获日志] {caplog.text!r}")
    assert "health db check failed" in caplog.text.lower(), "DB 失败没有留下任何日志"


def test_b24_02_health_db_check_has_short_timeout():
    """探活不应被默认 30s 的池等待拖住。"""
    from app.core.database import engine

    timeout = getattr(engine.pool, "_timeout", None)
    print(f"\n[连接池超时] {timeout!r}s")
    assert timeout is not None and timeout <= 3, f"pool_timeout={timeout}s，探活可能挂 30s"


# ---------------------------------------------------------------- B24-03
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("宁波\t宏远", "宁波 宏远"),
        ("宁波\n宏远", "宁波 宏远"),
        ("宁波\r\n宏远", "宁波 宏远"),
    ],
)
def test_b24_03_normalize_name_collapses_control_whitespace(raw, expected):
    left = normalize_name(raw)
    right = normalize_name(expected)
    print(f"\n[归一化] {raw!r} -> {left!r}；{expected!r} -> {right!r}")
    assert left == right, f"{raw!r} 与 {expected!r} 没有被归一成同一个名字"


def test_b24_03_customer_schema_allows_internal_tab():
    """客户名 schema 只限长度，内部制表符可以进库。"""
    from app.modules.customer.schema import CustomerCreate

    customer = CustomerCreate(name="宁波\t宏远")
    print(f"\n[schema 接受] {customer.name!r}")
    assert "\t" in customer.name


# ---------------------------------------------------------------- 漏项：NFKC / 标点
DEFAULT_WEIGHTS = {
    "weight_name_exact": 70,
    "weight_name_contains": 55,
    "weight_mobile": 45,
    "weight_tax_no": 60,
    "weight_domain": 30,
    "weight_address": 15,
}


@pytest.mark.parametrize(
    "left,right",
    [
        ("宁波宏远（集团）有限公司", "宁波宏远(集团)有限公司"),
        ("ＮＢＡ贸易有限公司", "NBA贸易有限公司"),
        ("宁波·宏远包装有限公司", "宁波宏远包装有限公司"),
    ],
)
def test_b24_missed_unicode_normalization_should_hit_threshold(left, right):
    """同一个公司名只差全角/半角括号、全角字母或间隔点，查重分应 ≥ 50。"""
    from app.core.similarity import similarity_score

    score, reasons = similarity_score(
        left={"name": left}, right={"name": right}, weights=DEFAULT_WEIGHTS
    )
    print(f"\n[漏项评分] {left!r} vs {right!r} -> {score} {reasons}")
    assert score >= 50, f"同一家公司只差全半角/标点，得分只有 {score}，不会出现在疑似重复里"
