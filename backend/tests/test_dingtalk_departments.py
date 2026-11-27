"""同序/钉钉部门按需落库：只建真实用到的部门，同名不并错。"""
import asyncio
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.base import Base
from app.core.errors import AppError
from app.modules.dingtalk import router as routes
from app.modules.dingtalk import service as svc
from app.modules.user.model import Department, User
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer


def _session():
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Department.__table__, User.__table__])
    return SyncSessionAsAsync(Session(engine)), engine


def test_member_department_uses_first_named_department():
    departments = {"10": {"id": "10", "name": "", "parent_id": "1"},
                   "11": {"id": "11", "name": "AI部门", "parent_id": "1"}}
    assert svc._member_department({"dept_id_list": [10, 11]}, departments) == ("11", "AI部门")


def test_ensure_creates_only_the_department_in_use():
    session, _engine = _session()
    departments = {"10": {"id": "10", "name": "AI部门", "parent_id": "1"}}
    dept, created = asyncio.run(svc.ensure_dingtalk_department(session, departments, "10"))
    assert created is True
    assert (dept.name, dept.dingtalk_department_id, dept.parent_id) == ("AI部门", "10", None)
    again, created_again = asyncio.run(svc.ensure_dingtalk_department(session, departments, "10"))
    assert created_again is False and again.id == dept.id


def test_ensure_reuses_top_level_same_name_department():
    session, engine = _session()
    with Session(engine) as raw:
        raw.add(Department(name="财务部", status="active"))
        raw.commit()
    departments = {"20": {"id": "20", "name": "财务部", "parent_id": "1"}}
    dept, created = asyncio.run(svc.ensure_dingtalk_department(session, departments, "20"))
    assert created is False
    assert dept.name == "财务部" and dept.dingtalk_department_id == "20"


def test_ensure_does_not_merge_into_child_department_when_top_level_exists():
    session, engine = _session()
    with Session(engine) as raw:
        top = Department(name="财务部", status="active")
        raw.add(top)
        raw.flush()
        raw.add(Department(name="财务部", parent_id=top.id, status="active"))
        raw.commit()
    departments = {"30": {"id": "30", "name": "财务部", "parent_id": "1"}}
    dept, created = asyncio.run(svc.ensure_dingtalk_department(session, departments, "30"))
    assert created is False
    assert dept.parent_id is None
    assert dept.dingtalk_department_id == "30"


def test_login_binding_does_not_fill_blank_department(monkeypatch):
    session, engine = _session()
    with Session(engine) as raw:
        raw.add(User(id=1, name="甲", username="jia", password_hash="x",
                     dingtalk_userid=None, status="active", department_id=None))
        raw.commit()
    members = [{"userid": "u1", "name": "甲", "mobile": "", "dept_id_list": [10]}]
    departments = {"10": {"id": "10", "name": "AI部门", "parent_id": "1"}}
    monkeypatch.setattr(svc, "fetch_directory", AsyncMock(return_value=(members, departments)))
    monkeypatch.setattr(svc, "get_client", lambda: Obj())
    monkeypatch.setattr(svc.app_settings, "dingtalk_login_off", False)
    result = asyncio.run(svc.sync_users(session))
    assert result["bound"] == 1
    asyncio.run(session.commit())
    with Session(engine) as raw:
        user = raw.get(User, 1)
        assert user.dingtalk_userid == "u1"
        assert user.department_id is None
        assert raw.query(Department).count() == 0


@pytest.mark.anyio
async def test_oa_directory_error_never_falls_back_to_dingtalk(monkeypatch):
    from app.modules.oa import client as oa_client

    session, _engine = _session()
    monkeypatch.setattr(oa_client, "list_directory", AsyncMock(side_effect=oa_client.OaNotConfigured("offline")))
    fetch = AsyncMock()
    monkeypatch.setattr(svc, "fetch_directory", fetch)
    with pytest.raises(AppError, match="无法读取同序人员目录"):
        await routes.list_dingtalk_members(user=Obj(id=99), session=session)
    payload = routes.ProvisionDingTalkUser(
        dingtalk_userid="u1", username="jia", password="secret1", role_ids=[],
    )
    request = Request({"type": "http", "headers": []})
    with pytest.raises(AppError, match="无法读取同序人员目录"):
        await routes.provision_dingtalk_user(payload, request, user=Obj(id=99), session=session)
    with pytest.raises(AppError, match="无法读取同序人员目录"):
        await svc.align_departments(session)
    fetch.assert_not_awaited()


def test_same_name_department_already_mapped_to_another_identity_is_not_stolen():
    session, engine = _session()
    with Session(engine) as raw:
        raw.add(Department(name="财务部", status="active", dingtalk_department_id="20"))
        raw.commit()
    departments = {"30": {"id": "30", "name": "财务部", "parent_id": "1"}}
    dept, created = asyncio.run(svc.ensure_dingtalk_department(session, departments, "30"))
    assert created is True
    assert dept.dingtalk_department_id == "30"
    asyncio.run(session.commit())
    with Session(engine) as raw:
        assert raw.query(Department).filter_by(dingtalk_department_id="20").count() == 1


def test_provision_user_prefers_oa_directory(monkeypatch):
    from app.modules.oa import client as oa_client

    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Department.__table__, User.__table__, AuditLog.__table__])
    session = SyncSessionAsAsync(Session(engine))
    monkeypatch.setattr(oa_client, "configured", lambda: True)
    monkeypatch.setattr(oa_client, "list_directory", AsyncMock(return_value={
        "organization": {"id": "org-1", "name": "同序"},
        "departments": [{"id": "oa-dept-1", "name": "AI部门", "parent_id": None, "restricted": False}],
        "users": [{"id": "oa-user-1", "name": "同序甲", "department_id": "oa-dept-1",
                   "department_name": "AI部门", "dingtalk_user_id": "u-oa"}],
    }))
    payload = routes.ProvisionDingTalkUser(
        dingtalk_userid="u-oa", username="tongxu", password="secret1", role_ids=[],
    )
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
    result = asyncio.run(routes.provision_dingtalk_user(payload, request, user=Obj(id=99), session=session))
    assert result["data"]["department"] == "AI部门"
    asyncio.run(session.commit())
    with Session(engine) as raw:
        dept = raw.query(Department).filter_by(oa_department_id="oa-dept-1").one()
        user = raw.query(User).filter_by(username="tongxu").one()
        assert dept.name == "AI部门" and dept.dingtalk_department_id is None
        assert user.dingtalk_userid == "u-oa" and user.department_id == dept.id


def test_align_departments_moves_existing_user_to_oa_department(monkeypatch):
    from app.modules.oa import client as oa_client

    session, engine = _session()
    with Session(engine) as raw:
        old = Department(name="销售二部", status="active")
        raw.add(old); raw.flush()
        raw.add(User(id=1, name="刘涛", username="liutao", password_hash="x",
                     dingtalk_userid="u-liu", status="active", department_id=old.id))
        raw.commit()
        old_id = old.id
    monkeypatch.setattr(oa_client, "configured", lambda: True)
    monkeypatch.setattr(oa_client, "list_directory", AsyncMock(return_value={
        "organization": {"id": "org-1", "name": "同序"},
        "departments": [{"id": "oa-ai", "name": "AI部门", "parent_id": None, "restricted": False}],
        "users": [{"id": "oa-u", "name": "刘涛", "department_id": "oa-ai",
                   "department_name": "AI部门", "dingtalk_user_id": "u-liu"}],
    }))
    result = asyncio.run(svc.align_departments(session))
    assert result["source"] == "oa" and result["matched"] == 1
    assert result["changes"][0]["current_department"] == "销售二部"
    assert result["changes"][0]["dingtalk_department"] == "AI部门"
    asyncio.run(session.commit())
    with Session(engine) as raw:
        user = raw.get(User, 1)
        dept = raw.query(Department).filter_by(oa_department_id="oa-ai").one()
        assert user.department_id == dept.id != old_id


# ---- 同序 OA 部门：上级不存在时递归补建 ----

def test_oa_ensure_creates_missing_ancestors():
    """只建子部门会让它落到根上，主管的「部门及下级」就看不到下级。"""
    session, engine = _session()
    departments = {
        "root": {"id": "root", "name": "职能部门", "parent_id": None},
        "mid": {"id": "mid", "name": "总经办", "parent_id": "root"},
        "leaf": {"id": "leaf", "name": "财务部", "parent_id": "mid"},
    }
    dept, created = asyncio.run(svc.ensure_oa_department(session, departments, "leaf"))
    assert created is True
    with Session(engine) as raw:
        rows = {row.oa_department_id: row for row in raw.scalars(select(Department)).all()}
    assert set(rows) == {"root", "mid", "leaf"}
    assert rows["leaf"].parent_id == rows["mid"].id
    assert rows["mid"].parent_id == rows["root"].id
    assert dept.oa_department_id == "leaf"


def test_oa_ensure_rejects_department_cycle():
    session, _engine = _session()
    departments = {
        "a": {"id": "a", "name": "甲部", "parent_id": "b"},
        "b": {"id": "b", "name": "乙部", "parent_id": "a"},
    }
    with pytest.raises(AppError):
        asyncio.run(svc.ensure_oa_department(session, departments, "a"))
