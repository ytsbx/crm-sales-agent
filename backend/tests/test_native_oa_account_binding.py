from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.dingtalk import router
from app.modules.oa import client
from app.modules.user.model import Department, Role, User, user_roles
from conftest import SyncSessionAsAsync, make_user


OA_ID = "6f4fe113-97c0-44b5-8797-da90644ca39d"
REQUEST = Request({"type": "http", "headers": []})


@pytest.mark.anyio
async def test_bind_native_oa_person_without_dingtalk_and_reject_duplicate(db_session, monkeypatch):
    AuditLog.__table__.create(db_session.bind)
    db_session.add_all([
        User(id=11, name="员工一", username="one", password_hash="test", dingtalk_userid="scan-only"),
        User(id=12, name="员工二", username="two", password_hash="test"),
    ])
    db_session.commit()
    monkeypatch.setattr(client, "list_directory", AsyncMock(return_value={"users": [
        {"id": OA_ID, "name": "同序员工", "department_name": "销售部", "dingtalk_user_id": None},
    ]}))
    session = SyncSessionAsAsync(db_session)
    admin = make_user(101, roles=("admin",))
    payload = router.OaUserBinding(oa_user_id=OA_ID)
    members = (await router.list_oa_members(admin, session))["data"]
    assert members[0]["id"] == OA_ID and members[0]["crm_user_id"] is None
    await router.set_oa_binding(11, payload, REQUEST, admin, session)
    assert db_session.get(User, 11).dingtalk_userid == "scan-only"
    assert client.applicant_fields(client.applicant_identity(db_session.get(User, 11))) == {
        "applicant_oa_user_id": OA_ID,
    }
    with pytest.raises(AppError, match="另一个 CRM 账号"):
        await router.set_oa_binding(12, payload, REQUEST, admin, session)
    with pytest.raises(AppError, match="没有这位在职人员"):
        await router.set_oa_binding(
            12, router.OaUserBinding(oa_user_id=UUID(int=1)), REQUEST, admin, session
        )
    assert db_session.query(AuditLog).filter_by(action="oa_account_binding").one().after_data == {
        "oa_user_id": OA_ID,
    }


@pytest.mark.anyio
async def test_oa_binding_requires_administrator():
    with pytest.raises(AppError) as error:
        await router._require_oa_account_admin(make_user(101, permissions=("user:manage", "settings:manage")))
    assert error.value.http_status == 403


@pytest.mark.anyio
async def test_create_crm_account_from_native_directory(db_session, monkeypatch):
    AuditLog.__table__.create(db_session.bind)
    Role.__table__.create(db_session.bind)
    user_roles.create(db_session.bind)
    department = "c27a8fac-ba37-4dae-a372-914ed6382838"
    monkeypatch.setattr(client, "list_directory", AsyncMock(return_value={
        "departments": [{"id": department, "name": "同序销售部"}],
        "users": [{"id": OA_ID, "name": "同序员工", "department_id": department, "dingtalk_user_id": None}],
    }))
    session = SyncSessionAsAsync(db_session)
    result = await router.provision_oa_user(
        router.OaUserCreate(oa_user_id=OA_ID, username="oa-sales", password="testing-only"),
        REQUEST, make_user(101, roles=("admin",)), session,
    )
    account = db_session.get(User, result["data"]["id"])
    assert account.oa_user_id == OA_ID
    assert account.dingtalk_userid is None
    assert db_session.get(Department, account.department_id).oa_department_id == department
