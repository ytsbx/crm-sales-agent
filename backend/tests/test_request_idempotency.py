"""第八批 8.15：请求幂等的三条语义（同键同内容 / 同键不同内容 / 同键并发）。

为什么要单独守这三条：弱网重试是**真实场景**（服务端已成功、响应没回到客户端），
而"重复提交"的三种结局都必须有确定行为，不能靠"应该不会有人连点两次"：

- 同键同内容 → 回放原结果，库里只有一条；
- 同键不同内容 → 报冲突。**不能**静默按新内容再建一条：那等于把"重复提交"
  变成"悄悄改了内容"，用户看到两条数据还不知道哪条是真的；
- 同键并发 → 只有一个拿到占位，另一个明确报"正在处理中"。

失败要释放占位：用户改完表单会带**同一把键**重试，内容必然不同，
不释放就会把"改错重填"误判成冲突。
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer

from app.core.errors import AppError
from app.core.idempotency import (
    RequestKey,
    complete,
    payload_hash,
    release,
    request_key_from,
    reserve,
)


class _NestedTxn:
    def __init__(self, nested) -> None:
        self._nested = nested

    async def commit(self) -> None:
        self._nested.commit()

    async def rollback(self) -> None:
        self._nested.rollback()


class FakeSession(SyncSessionAsAsync):
    """补上幂等助手真正用到的方法（rollback / delete）。"""

    async def rollback(self) -> None:
        self._session.rollback()

    async def delete(self, obj) -> None:
        self._session.delete(obj)

    def add(self, obj) -> None:
        self._session.add(obj)


@pytest.fixture()
def session():
    from sqlalchemy import create_engine

    from app.core.base import Base

    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _prepare(dbapi_connection, _record):  # noqa: ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _explicit_begin(conn):
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine, tables=[RequestKey.__table__])
    with Session(engine) as raw_session:
        yield FakeSession(raw_session)
    engine.dispose()


PAYLOAD = {"name": "宏远包装", "level": "A"}


@pytest.mark.anyio
async def test_same_key_same_payload_replays(session):
    first = await reserve(
        session, user_id=1, action="customer:create", request_key="k-1", payload=PAYLOAD
    )
    assert first.should_replay is False
    await complete(session, first, result_payload={"id": 7, "name": "宏远包装"}, result_id=7)

    second = await reserve(
        session, user_id=1, action="customer:create", request_key="k-1", payload=PAYLOAD
    )
    assert second.should_replay is True
    assert second.replay_payload == {"id": 7, "name": "宏远包装"}
    assert second.replay_id == 7


@pytest.mark.anyio
async def test_same_key_different_payload_is_a_conflict(session):
    first = await reserve(
        session, user_id=1, action="customer:create", request_key="k-2", payload=PAYLOAD
    )
    await complete(session, first, result_payload={"id": 8}, result_id=8)

    with pytest.raises(AppError) as excinfo:
        await reserve(
            session,
            user_id=1,
            action="customer:create",
            request_key="k-2",
            payload={"name": "另一家客户", "level": "A"},
        )
    assert "不一致" in excinfo.value.message


@pytest.mark.anyio
async def test_same_key_still_in_flight_is_refused_not_duplicated(session):
    """并发：另一个请求已经占位但还没完成 → 明确报"正在处理中"，不建第二条。"""
    await reserve(
        session, user_id=1, action="customer:create", request_key="k-3", payload=PAYLOAD
    )
    with pytest.raises(AppError) as excinfo:
        await reserve(
            session, user_id=1, action="customer:create", request_key="k-3", payload=PAYLOAD
        )
    assert "正在处理中" in excinfo.value.message


@pytest.mark.anyio
async def test_release_lets_the_same_key_be_reused_after_a_failure(session):
    """失败后改内容重试：同一把键必须能再用（否则"改错重填"会被误判成冲突）。"""
    first = await reserve(
        session, user_id=1, action="customer:create", request_key="k-4", payload=PAYLOAD
    )
    await release(session, first)

    retry = await reserve(
        session,
        user_id=1,
        action="customer:create",
        request_key="k-4",
        payload={"name": "宏远包装（改过）", "level": "A"},
    )
    assert retry.should_replay is False


@pytest.mark.anyio
async def test_keys_are_scoped_by_user_and_action(session):
    """不同用户 / 不同接口用同一个字符串互不影响（前端 uuid 不必全局唯一）。"""
    for user_id, action in ((1, "customer:create"), (2, "customer:create"), (1, "quote:create")):
        reservation = await reserve(
            session, user_id=user_id, action=action, request_key="same-key", payload=PAYLOAD
        )
        assert reservation.should_replay is False
    rows = (session._session.execute(select(RequestKey))).scalars().all()
    assert len(rows) == 3


def test_payload_hash_ignores_field_order():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})


def test_request_key_comes_from_body_then_header():
    class Req:
        headers = {"x-request-key": "header-key"}

    assert request_key_from(Req(), "body-key") == "body-key"
    assert request_key_from(Req(), None) == "header-key"
    assert request_key_from(Req(), "   ") == "header-key"
    assert request_key_from(None, None) is None


@pytest.mark.anyio
async def test_complete_makes_the_payload_json_safe(session):
    """回放的响应体里天然有 datetime/Decimal：底座必须先转成 JSON 能存的形状。

    不转的后果很具体：PostgreSQL 的 JSON 列在 `commit` 阶段抛
    `Object of type datetime is not JSON serializable` —— 业务记录**已经建好
    又整体回滚**，用户看到 500，重试也拿不到回放（那一行同样被回滚了）。
    """
    import json
    from decimal import Decimal

    created_at = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    reservation = await reserve(
        session, user_id=1, action="customer:create", request_key="k-json", payload=PAYLOAD
    )
    await complete(
        session,
        reservation,
        result_payload={
            "id": 9,
            "name": "宏远包装",
            "created_at": created_at,
            "amount": Decimal("12.50"),
            "tags": [{"at": created_at}],
        },
        result_id=9,
    )
    stored = reservation.row.result_payload
    json.dumps(stored)  # JSON 列的要求：必须可直接序列化
    assert stored["created_at"] == created_at.isoformat()
    assert stored["amount"] == 12.5
    assert stored["tags"][0]["at"] == created_at.isoformat()

    replay = await reserve(
        session, user_id=1, action="customer:create", request_key="k-json", payload=PAYLOAD
    )
    assert replay.should_replay is True
    json.dumps(replay.replay_payload)


@pytest.mark.anyio
async def test_empty_key_is_refused(session):
    with pytest.raises(AppError):
        await reserve(
            session, user_id=1, action="customer:create", request_key="  ", payload=PAYLOAD
        )
