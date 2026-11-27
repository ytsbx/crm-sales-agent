"""B21-13：手动打样创建同请求键幂等（双击/弱网重试不重复建）。"""
import asyncio

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.audit import AuditLog
from app.modules.approval.model import ApprovalInstance
from app.modules.customer.model import Customer, CustomerFollower
from app.modules.sample import router as sample_router
from app.modules.sample.model import SampleItem, SampleRequest, SampleShipment
from app.modules.sample.schema import SampleCreate
from app.modules.user.model import Permission, Role, User, role_permissions, user_roles
from tests.conftest import (
    SyncSessionAsAsync,
    _register_sqlite_bigint_as_integer,
    make_user,
)


def test_manual_sample_create_same_request_key_is_idempotent():
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _prep(dbapi_connection, _record):  # noqa: ARG001
        dbapi_connection.isolation_level = None
        dbapi_connection.create_function("LEAST", 2, lambda a, b: min(a, b), deterministic=True)
        dbapi_connection.create_function("GREATEST", 2, lambda a, b: max(a, b), deterministic=True)

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    for table in (
        User.__table__, Customer.__table__, CustomerFollower.__table__, SampleRequest.__table__, SampleItem.__table__,
        SampleShipment.__table__, ApprovalInstance.__table__, AuditLog.__table__,
        Role.__table__, Permission.__table__,
        user_roles, role_permissions,
    ):
        table.create(engine, checkfirst=True)

    manager = make_user(101, permissions=("sample:manage", "sample:view"))
    request = Request({"type": "http", "headers": []})
    with Session(engine, expire_on_commit=False) as raw:
        raw.add_all([
            User(id=101, name="负责人", username="owner", password_hash="fixture"),
            Customer(id=1, name="客户", owner_id=101),
        ])
        raw.flush()
        raw.add(CustomerFollower(customer_id=1, user_id=101, source="manual"))
        raw.commit()
        session = SyncSessionAsAsync(raw)

        async def _run():
            first = await sample_router.create_sample(
                SampleCreate(customer_id=1, request_key="idem-key-1"), request, manager, session
            )
            second = await sample_router.create_sample(
                SampleCreate(customer_id=1, request_key="idem-key-1"), request, manager, session
            )
            return first, second

        first, second = asyncio.run(_run())
        assert first["data"]["id"] == second["data"]["id"]
        assert raw.scalar(select(func.count()).select_from(SampleRequest)) == 1
    engine.dispose()