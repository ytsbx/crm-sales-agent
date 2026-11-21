import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core import database, scheduler
from app.core.base import Base
from app.core.config import settings
from app.core.csvio import csv_bytes
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.main import app, lifespan
from app.modules.dingtalk.oa_stream import oa_stream
from app.modules.erp import adapter
from app.modules.file.model import BusinessFile
from app.modules.file.service import inspect_file_usage
from app.modules.lead.io_router import import_leads
from app.modules.lead.model import Lead
from app.modules.user import service as users
from app.modules.user.model import Permission, Role, role_permissions, user_roles
from app.modules.wecom import client
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer, make_user


@pytest.mark.anyio
async def test_sample_cannot_replace_explicitly_empty_making_basis(monkeypatch):
    from app.modules.oa import business_gates
    from app.modules.sample import router
    from app.modules.sample.schema import SampleMade

    sample = SimpleNamespace(
        id=1, status="approved", made_at=None, shipped_at=None, signed_at=None,
        made_events=[{"key": "first"}], basis_files=[],
    )
    monkeypatch.setattr(router.svc, "get_visible_or_404", AsyncMock(return_value=sample))
    monkeypatch.setattr(business_gates, "require_sample_approved", AsyncMock())
    execute = AsyncMock(return_value=Mock(scalars=lambda: Mock(all=lambda: [])))
    session = SimpleNamespace(execute=execute)
    with pytest.raises(AppError, match="已经登记过制作依据"):
        await router.register_sample_made(
            1, SampleMade(request_key="second", basis_file_ids=[2]),
            Request({"type": "http", "headers": []}), SimpleNamespace(id=1), session,
        )
    execute.assert_not_awaited()
    assert sample.basis_files == []
    assert sample.made_events == [{"key": "first"}]
    assert sample.made_at is None


@pytest.mark.anyio
async def test_lead_conversion_rate_respects_scope_without_returning_duration(db_session):
    from app.modules.analytics.service import lead_stats

    def day(value):
        return datetime(2026, 11, value, tzinfo=UTC)

    for identity in range(1, 13):
        db_session.add(Lead(
            id=identity, name=f"线索 {identity}", created_at=day(2), updated_at=day(15),
            status="converted" if identity <= 3 or identity >= 11 else "pending",
            owner_id=999 if identity == 11 else 101, deleted_at=day(18) if identity == 12 else None,
        ))
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    result = await lead_stats(session, make_user())
    assert (result["total"], result["converted_count"], result["conversion_rate"]) == (10, 3, 0.3)
    assert "average_conversion_days" not in result

    empty = await lead_stats(session, make_user(102))
    assert (empty["total"], empty["converted_count"], empty["conversion_rate"]) == (0, 0, 0)
    assert "average_conversion_days" not in empty


@pytest.mark.anyio
@pytest.mark.parametrize("failure_point", ["start", "stop"])
async def test_lifespan_cleans_other_services_when_stream_fails(monkeypatch, failure_point):
    start_stream = Mock(side_effect=RuntimeError("stream failed") if failure_point == "start" else None)
    stop_stream = AsyncMock(side_effect=RuntimeError("stream failed") if failure_point == "stop" else None)
    stop_scheduler = Mock()
    reset_adapters = AsyncMock()
    reset_client = AsyncMock()
    monkeypatch.setattr(scheduler, "start_scheduler", Mock())
    monkeypatch.setattr(scheduler, "stop_scheduler", stop_scheduler)
    monkeypatch.setattr(oa_stream, "start", start_stream)
    monkeypatch.setattr(oa_stream, "stop", stop_stream)
    monkeypatch.setattr(adapter, "reset_adapters", reset_adapters)
    monkeypatch.setattr(client, "reset_client", reset_client)

    with pytest.raises(RuntimeError, match="stream failed"):
        async with lifespan(app):
            assert failure_point == "stop"

    stop_stream.assert_awaited_once()
    stop_scheduler.assert_called_once()
    reset_adapters.assert_awaited_once()
    reset_client.assert_awaited_once()


@pytest.mark.anyio
async def test_oa_health_uses_existing_settings_permission(monkeypatch):
    class HealthSession:
        async def __aenter__(self):
            return SimpleNamespace(execute=AsyncMock(), rollback=AsyncMock())

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(database, "SessionLocal", HealthSession)
    url = f"{settings.api_prefix}/dingtalk/oa-health"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/health")).json()["data"] == {
            "status": "up", "app": settings.app_name, "database": "ok",
        }
        assert (await http.get(url)).status_code == 401
        allowed = False
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            roles=[], has=lambda code: allowed and code == "settings:manage",
        )
        try:
            assert (await http.get(url)).status_code == 403
            allowed = True
            response = await http.get(url)
            assert response.status_code == 200
            assert "last_event_at" in response.json()["data"]
        finally:
            app.dependency_overrides.pop(get_current_user)


@pytest.mark.anyio
async def test_joined_access_query_preserves_roles_without_permissions():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Role.__table__, Permission.__table__, user_roles, role_permissions]
    )
    with Session(engine) as raw:
        raw.add_all([
            Role(id=1, code="seller", name="销售"),
            Role(id=2, code="lead", name="主管"),
            Role(id=3, code="admin", name="管理员", status="disabled", data_scope="all"),
            Role(id=4, code="department", name="部门范围", data_scope="department"),
            Permission(id=1, code="quote:view", name="查看报价"),
            Permission(id=2, code="settings:manage", name="管理设置"),
        ])
        raw.flush()
        raw.execute(user_roles.insert(), [{"user_id": 10, "role_id": rid} for rid in range(1, 5)])
        raw.execute(role_permissions.insert(), [
            {"role_id": 1, "permission_id": 1},
            {"role_id": 2, "permission_id": 1},
            {"role_id": 3, "permission_id": 2},
        ])
        session = SyncSessionAsAsync(raw)
        roles, permissions = await users.get_user_roles_and_permissions(session, 10)
        assert {role.code for role in roles} == {"seller", "lead", "department"}
        assert permissions == {"quote:view"}
        assert users.resolve_data_scope(roles) == "department"
        assert await users.get_user_roles_and_permissions(session, 11) == ([], set())


@pytest.mark.anyio
async def test_lead_csv_round_trip_keeps_phone_and_checks_duplicate(monkeypatch):
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite://")
    Lead.__table__.create(engine)
    monkeypatch.setattr("app.modules.lead.io_router.finalize", AsyncMock())
    raw_csv = csv_bytes(
        [["电话号码", "+86 123-4567", "=备注"], ["重复号码", "+86 123-4567", ""]],
        ["线索名称", "手机号", "备注"],
    )
    assert "'+86 123-4567" in raw_csv.decode("utf-8-sig")

    class ImportSession(SyncSessionAsAsync):
        async def begin_nested(self):
            transaction = self._session.begin_nested()

            async def commit():
                transaction.commit()

            async def rollback():
                transaction.rollback()

            return SimpleNamespace(commit=commit, rollback=rollback)

        @property
        def new(self):
            return self._session.new

    with Session(engine) as raw:
        await import_leads(
            Request({"type": "http", "headers": []}), UploadFile(io.BytesIO(raw_csv)),
            False, None, SimpleNamespace(id=1), ImportSession(raw),
        )
        row = raw.scalars(select(Lead)).one()
        assert row.mobile == "+86 123-4567"
        assert row.remark == "'=备注"


@pytest.mark.anyio
async def test_file_usage_tolerates_old_sample_json_and_keeps_real_references():
    engine = create_engine("sqlite://")
    BusinessFile.__table__.create(engine)
    with Session(engine) as raw:
        for ddl in (
            "contract_documents (id int, generated_file_id int)",
            "biz_docs (id int, file_id int)",
            "payment_records (id int, voucher_file_id int)",
            "sample_requests (basis_files json)",
        ):
            raw.execute(text(f"create table {ddl}"))
        raw.execute(text("insert into sample_requests values (:basis)"), [
            {"basis": basis} for basis in (
                "null", '"旧资料"', '[null, "旧资料", {"file_id": "未知"}]',
                '[{"file_id": 101}]', '[{"file_id": "102"}]', '{"file_id": 103}',
            )
        ])

        class BoundSession(SyncSessionAsAsync):
            def get_bind(self):
                return raw.get_bind()

        session = BoundSession(raw)
        for file_id in (101, 102, 103):
            assert "打样的制作依据" in (await inspect_file_usage(session, file_id)).other_holders
        assert (await inspect_file_usage(session, 104)).other_holders == []
