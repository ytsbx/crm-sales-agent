"""客户创建时指定负责人必须经过分配权限。"""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.core import idempotency
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.modules.customer import router as customer_router
from app.modules.customer.schema import CustomerCreate
from app.modules.user.model import User


def _request() -> Request:
    return Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})


def test_create_customer_requires_assign_permission_for_explicit_owner(monkeypatch):
    user = User(id=1, username="creator", name="创建人", status="active")
    current = CurrentUser(user, {"customer:create"}, ["salesperson"], "self")

    async def no_dictionary_check(*_args, **_kwargs):
        return None

    async def should_not_create(*_args, **_kwargs):
        raise AssertionError("权限检查前不应创建客户")

    monkeypatch.setattr(customer_router.settings_service, "ensure_dictionary_value", no_dictionary_check)
    monkeypatch.setattr(customer_router.svc, "create_customer", should_not_create)
    monkeypatch.setattr(idempotency, "request_key_from", lambda *_args: None)

    with pytest.raises(AppError) as caught:
        asyncio.run(
            customer_router.create_customer(
                CustomerCreate(name="越权客户", owner_id=2),
                _request(),
                current,
                SimpleNamespace(),
            )
        )

    assert caught.value.http_status == 403
    assert "customer:assign" in caught.value.message
