"""Company-wide WeCom operations require explicit permissions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core import config
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.modules.wecom import router as routes


@pytest.mark.anyio
async def test_operations_require_separate_permissions_and_transfer_department(monkeypatch):
    app = FastAPI()
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
