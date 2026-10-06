"""第八批 8.15（quote 侧）：`POST /quotes` 的请求幂等接线。

守什么：弱网下"服务端已提交、客户端没收到响应"的重试只建一条报价，
而**两次真实报价必须能是两条**——不能按"内容相同"否定合法的新报价。
另有两条边界：同键不同内容报冲突；失败后带同一把键改内容重试要能过
（服务端失败时释放了占位）。

共用设施（`app.core.idempotency`）自己的三条语义已有
`tests/test_request_idempotency.py` 守着，这里只验**路由接线**：
- 键从 body 字段或 `X-Request-Key` 头取；
- 回放时**不再调用业务创建**（否则一次点击仍会建两条）；
- 业务异常时释放占位；
- 没带键时照常创建，但响应文案要说清"这次没有幂等保护"。

接口级的真库回归（含并发与真实报价插入）在隔离库脚本里跑：
`ops/iso_checks.ps1 -Suites check_quote_api,check_quote_lifecycle`。
"""

import asyncio
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import RequestKey
from app.modules.quote import router as quote_router
from app.modules.quote.schema import QuoteCreate
from app.modules.user.model import User


class _FakeSession:
    """只包出路由与幂等助手真正用到的几个方法（其余一律 AttributeError）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, stmt, *args, **kwargs):
        return self._session.execute(stmt, *args, **kwargs)

    async def get(self, entity, ident, *args, **kwargs):
        return self._session.get(entity, ident, *args, **kwargs)

    async def delete(self, obj) -> None:
        self._session.delete(obj)

    def add(self, obj) -> None:
        self._session.add(obj)

    async def flush(self) -> None:
        self._session.flush()

    async def commit(self) -> None:
        self._session.commit()

    async def rollback(self) -> None:
        self._session.rollback()


def _request(headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/quotes",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


def _user(user_id: int = 501) -> CurrentUser:
    return CurrentUser(
        User(
            id=user_id,
            name="幂等回归销售",
            username=f"idem{user_id}",
            password_hash="x",
            status="active",
        ),
        permissions={"quote:manage"},
        roles=[],
        data_scope="self",
    )


def _fake_created(quote_id: int) -> dict:
    """`svc.create_quote` 的最小返回值形状（路由只读这几个键）。"""
    quote = SimpleNamespace(id=quote_id, customer_id=1)
    version = SimpleNamespace(id=quote_id * 10, currency="CNY")
    return {
        "_quote": quote,
        "_version": version,
        "exchange_rate_snapshot": None,
        "warnings": [],
    }


@contextmanager
def _patched(create):
    """替换路由用到的东西。

    序列化也换成最小替身：它读的是完整 ORM 实例（含 created_at 等），
    本文件只验幂等接线，不重复 ser 的用例。
    """
    with patch.object(quote_router.svc, "create_quote", create), patch.object(
        quote_router.svc, "serialize_quote", lambda *args, **kwargs: {"quote_id": "fake"}
    ), patch.object(
        quote_router.customer_service, "touch_progress", AsyncMock()
    ), patch.object(quote_router, "write_audit", AsyncMock()):
        yield


def _run(callback):
    """每个用例一个全新的内存库（幂等表是唯一有状态的东西）。"""
    # SQLite 只对 INTEGER PRIMARY KEY 自增，本仓库主键统一 BigInteger；
    # 不注册这个方言改写，request_keys.id 一插就 NOT NULL 失败。
    from tests.conftest import _register_sqlite_bigint_as_integer

    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _no_autocommit(dbapi_connection, _record):  # noqa: ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _explicit_begin(conn):
        conn.exec_driver_sql("BEGIN")

    RequestKey.__table__.create(engine)
    with Session(engine) as raw:
        session = _FakeSession(raw)
        try:
            return asyncio.run(callback(session))
        finally:
            raw.rollback()
    engine.dispose()


def _count_keys(session: _FakeSession) -> int:
    return len(session._session.execute(select(RequestKey)).scalars().all())


def test_same_key_and_content_replays_without_creating_again():
    """同键同内容：回放第一次的响应，**业务创建不再被调用**（否则仍会重复建）。"""

    async def _case(session):
        calls = []

        async def fake_create(session_, **kwargs):
            calls.append(kwargs)
            return _fake_created(700 + len(calls))

        with _patched(fake_create):
            first = await quote_router.create_quote(
                payload=QuoteCreate(customer_id=1, remark="首次提交", request_key="rk-quote-1"),
                request=_request(),
                user=_user(),
                session=session,
            )
            second = await quote_router.create_quote(
                payload=QuoteCreate(customer_id=1, remark="首次提交", request_key="rk-quote-1"),
                request=_request(),
                user=_user(),
                session=session,
            )

        assert first["data"]["quote_id"] == 701
        assert second["data"] == first["data"], "同键同内容必须回放同一份结果"
        assert "没有重复创建" in second["message"]
        assert len(calls) == 1, "回放时不得再走一遍业务创建"
        assert _count_keys(session) == 1

    _run(_case)


def test_same_key_different_content_is_a_conflict():
    """同键不同内容：报冲突，不静默按新内容再建一条。"""

    async def _case(session):
        async def fake_create(session_, **kwargs):
            return _fake_created(710)

        with _patched(fake_create):
            await quote_router.create_quote(
                payload=QuoteCreate(customer_id=1, remark="第一次内容", request_key="rk-quote-2"),
                request=_request(),
                user=_user(),
                session=session,
            )
            try:
                await quote_router.create_quote(
                    payload=QuoteCreate(
                        customer_id=1, remark="改过的内容", request_key="rk-quote-2"
                    ),
                    request=_request(),
                    user=_user(),
                    session=session,
                )
                raise AssertionError("同键不同内容没有被拦下")
            except AppError as exc:
                assert exc.http_status == 409
                assert "不一致" in str(exc.message)

    _run(_case)


def test_two_real_quotes_with_different_keys_are_two_rows():
    """两次真实报价：不同键就是**两条**，不能按"内容相同"否定合法业务。"""

    async def _case(session):
        calls = []

        async def fake_create(session_, **kwargs):
            calls.append(kwargs)
            return _fake_created(720 + len(calls))

        with _patched(fake_create):
            first = await quote_router.create_quote(
                payload=QuoteCreate(customer_id=1, remark="同一份需求", request_key="rk-quote-3a"),
                request=_request(),
                user=_user(),
                session=session,
            )
            second = await quote_router.create_quote(
                payload=QuoteCreate(customer_id=1, remark="同一份需求", request_key="rk-quote-3b"),
                request=_request(),
                user=_user(),
                session=session,
            )

        assert first["data"]["quote_id"] == 721
        assert second["data"]["quote_id"] == 722, "内容相同但键不同 → 必须是两次真实报价"
        assert len(calls) == 2
        assert _count_keys(session) == 2

    _run(_case)


def test_failed_submit_releases_the_key_so_retry_can_change_content():
    """失败后同键改内容重试必须能过：服务端失败时释放了占位。"""

    async def _case(session):
        async def failing_create(session_, **kwargs):
            raise AppError(ErrorCode.PARAM_ERROR, "缺少适用售价，请先维护指导价", 422)

        with _patched(failing_create):
            try:
                await quote_router.create_quote(
                    payload=QuoteCreate(
                        customer_id=1, remark="第一次内容", request_key="rk-quote-4"
                    ),
                    request=_request(),
                    user=_user(),
                    session=session,
                )
                raise AssertionError("业务失败没有被抛出")
            except AppError as exc:
                assert exc.http_status == 422
        assert _count_keys(session) == 0, "失败必须释放占位"

        async def ok_create(session_, **kwargs):
            return _fake_created(730)

        with _patched(ok_create):
            retried = await quote_router.create_quote(
                payload=QuoteCreate(customer_id=1, remark="改过的内容", request_key="rk-quote-4"),
                request=_request(),
                user=_user(),
                session=session,
            )
        assert retried["data"]["quote_id"] == 730

    _run(_case)


def test_key_can_come_from_the_request_header_and_absence_is_reported():
    """键也能走 `X-Request-Key`；没带键时照常创建但要说清"没有幂等保护"。"""

    async def _case(session):
        async def fake_create(session_, **kwargs):
            return _fake_created(740)

        with _patched(fake_create):
            from_header = await quote_router.create_quote(
                payload=QuoteCreate(customer_id=1, remark="头部带键"),
                request=_request({"X-Request-Key": "rk-quote-5"}),
                user=_user(),
                session=session,
            )
            without_key = await quote_router.create_quote(
                payload=QuoteCreate(customer_id=1, remark="不带键"),
                request=_request(),
                user=_user(),
                session=session,
            )

        assert _count_keys(session) == 1, "只有带键的那次留下幂等记录"
        assert "没有重复创建" not in from_header["message"]
        assert "未带请求键" in without_key["message"], without_key["message"]

    _run(_case)


def test_keys_from_different_users_do_not_collide():
    """键按（用户 + 动作）分区：两个销售用同一个字符串互不影响。"""

    async def _case(session):
        async def fake_create(session_, **kwargs):
            return _fake_created(750)

        with _patched(fake_create):
            for user_id in (501, 502):
                await quote_router.create_quote(
                    payload=QuoteCreate(
                        customer_id=1, remark="同一个表单字符串", request_key="rk-shared"
                    ),
                    request=_request(),
                    user=_user(user_id),
                    session=session,
                )
        assert _count_keys(session) == 2

    _run(_case)


def test_request_key_field_defaults_to_none_and_keeps_other_fields():
    """schema 层：老客户端不传键也不报错（不能因为加幂等把老调用方打断）。"""
    from uuid import uuid4

    assert QuoteCreate(customer_id=1).request_key is None
    key = str(uuid4())
    assert QuoteCreate(customer_id=1, request_key=key).request_key == key
    payload = QuoteCreate(customer_id=1, currency="USD", exchange_rate=Decimal("7.1"))
    assert payload.currency == "USD"
    assert payload.exchange_rate == Decimal("7.1")
