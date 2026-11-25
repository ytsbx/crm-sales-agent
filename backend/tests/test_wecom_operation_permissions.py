"""Company-wide WeCom operations require explicit permissions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core.jsonio import DecimalJSONResponse

from app.core import config
from app.core.audit import AuditLog
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.modules.customer.model import Customer
from app.modules.wecom import router as routes
from app.modules.wecom.model import WeComExternalContact
from conftest import SyncSessionAsAsync, make_user


@pytest.mark.anyio
async def test_wecom_customer_actions_follow_crm_permissions_and_scope(db_session):
    for model in (AuditLog, WeComExternalContact):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([
        Customer(id=11, name="本人的客户", owner_id=101),
        Customer(id=12, name="同事的客户", owner_id=102),
        WeComExternalContact(id=21, external_userid="bind-contact", name="要关联的联系人"),
        WeComExternalContact(id=22, external_userid="create-contact", name="待新建的联系人"),
    ])
    db_session.commit()
    actor = make_user(101, permissions=("wecom:view", "wecom:manage"))
    session = SyncSessionAsAsync(db_session)
    app = FastAPI(default_response_class=DecimalJSONResponse)
    register_exception_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_db] = lambda: session
    base = "/integrations/wecom/unbound-contacts"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"{base}/21/candidates")).status_code == 403
        assert (await client.post(f"{base}/21/bind-customer", json={"customer_id": 11})).status_code == 403
        assert (await client.post(f"{base}/22/create-customer", json={"name": "新客户"})).status_code == 403
        actor.permissions.add("customer:update")
        assert (await client.post(f"{base}/21/bind-customer", json={"customer_id": 12})).status_code == 403
        assert db_session.get(WeComExternalContact, 21).crm_customer_id is None
        bound = await client.post(f"{base}/21/bind-customer", json={"customer_id": 11})
        assert bound.status_code == 200
        assert bound.json()["data"]["customer_id"] == 11
        actor.permissions.add("customer:create")
        created = await client.post(f"{base}/22/create-customer", json={"name": "新客户"})
        assert created.status_code == 200
        assert db_session.get(Customer, created.json()["data"]["customer_id"]).owner_id == actor.id


@pytest.mark.anyio
async def test_operations_require_separate_permissions_and_transfer_department(monkeypatch):
    app = FastAPI(default_response_class=DecimalJSONResponse)
    register_exception_handlers(app)
    app.include_router(routes.router)
    permissions = {"wecom:view", "wecom:manage", "wecom:transfer_preview"}
    actor = SimpleNamespace(id=1, roles=[], department_id=10, has=permissions.__contains__)
    session = SimpleNamespace(scalars=AsyncMock(return_value=[2]))
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_db] = lambda: session
    preview = AsyncMock(return_value={"sections": []})
    monkeypatch.setattr(routes.svc, "preview_transfer", preview)
    monkeypatch.setattr(config.settings, "wecom_transfer_enabled", True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/integrations/wecom/sync-users")).status_code == 403
        assert (await client.post("/integrations/wecom/sync-follow-relations")).status_code == 403
        body = {"handover_user_id": 2, "takeover_user_id": 3}
        assert (await client.get("/integrations/wecom/transfer-preview", params=body)).status_code == 403
        preview.assert_not_awaited()
        assert (await client.post("/integrations/wecom/transfer", json=body)).status_code == 403

        session.scalars.return_value = [2, 3]
        assert (await client.get("/integrations/wecom/transfer-preview", params=body)).status_code == 200
        preview.assert_awaited_once()
        permissions.add("wecom:transfer")
        outside_department = {**body, "item_assignees": {"customer:9": 4}}
        assert (await client.post("/integrations/wecom/transfer", json=outside_department)).status_code == 403

        permissions.remove("wecom:view")
        assert (await client.get("/integrations/wecom/transfer-preview", params=body)).status_code == 403
