"""客户软删除 / 恢复的授权边界（2026-11-13 用户确认）。

业务员只能删、恢复**自己负责**的客户；主管/管理员的 `customer:delete` 仍按数据范围，
公海客户（无负责人）不在业务员那一档里 —— 否则"看得见公海"就能删/恢复公海。
"""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError
from app.modules.customer import router as customer_router
from app.modules.customer import service as customer_service
from app.modules.customer.model import Customer
from app.modules.user.model import User


def _request() -> Request:
    return Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})


def _user(user_id: int = 1) -> User:
    return User(id=user_id, username=f"u{user_id}", name=f"用户{user_id}", status="active")


def _current(
    user: User | None = None,
    permissions: tuple[str, ...] = (),
    roles: tuple[str, ...] = ("salesperson",),
) -> CurrentUser:
    return CurrentUser(user or _user(), set(permissions), list(roles), "self")


def _customer(owner_id: int | None) -> Customer:
    return Customer(id=7, name="测试客户", owner_id=owner_id, pool_status="private")


# ---------------------------------------------------------------- 纯规则


def test_own_permission_can_delete_own_customer():
    customer_service.ensure_can_delete_customer(
        _current(permissions=("customer:delete_own",)), _customer(owner_id=1)
    )


def test_own_permission_cannot_delete_other_owner():
    with pytest.raises(AppError) as caught:
        customer_service.ensure_can_delete_customer(
            _current(permissions=("customer:delete_own",)), _customer(owner_id=2)
        )
    assert caught.value.http_status == 403
    assert caught.value.message == "只能删除自己负责的客户"


def test_own_permission_cannot_delete_public_pool_customer():
    """公海客户没有负责人：业务员看得见，但不是"自己负责"，不能删。"""
    with pytest.raises(AppError) as caught:
        customer_service.ensure_can_delete_customer(
            _current(permissions=("customer:delete_own",)), _customer(owner_id=None)
        )
    assert caught.value.http_status == 403


def test_full_delete_permission_keeps_scope_wide_behavior():
    customer_service.ensure_can_delete_customer(
        _current(permissions=("customer:delete",), roles=("sales_manager",)),
        _customer(owner_id=2),
    )


def test_admin_does_not_need_the_permission_codes():
    customer_service.ensure_can_delete_customer(
        _current(permissions=(), roles=("admin",)), _customer(owner_id=2)
    )


def test_no_delete_permission_is_rejected():
    with pytest.raises(AppError) as caught:
        customer_service.ensure_can_delete_customer(_current(), _customer(owner_id=1))
    assert caught.value.http_status == 403
    assert "customer:delete" in caught.value.message


def test_route_permission_accepts_either_delete_key():
    """路由依赖是"任一满足"：主管的 `customer:delete` 与业务员的 `delete_own` 都放行。"""
    dependency = require_permission("customer:delete", "customer:delete_own")

    asyncio.run(dependency(user=_current(permissions=("customer:delete_own",))))
    asyncio.run(dependency(user=_current(permissions=("customer:delete",))))

    with pytest.raises(AppError) as caught:
        asyncio.run(dependency(user=_current()))
    assert caught.value.http_status == 403
    assert "customer:delete_own" in caught.value.message


# ---------------------------------------------------------------- 路由接线


class _Session:
    """只够本用例用：删除分支最终会 commit 一次。"""

    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


def test_delete_route_rejects_other_owner_before_writing(monkeypatch):
    customer = _customer(owner_id=2)

    async def fake_visible(*_args, **_kwargs):
        return customer

    async def should_not_delete(*_args, **_kwargs):
        raise AssertionError("越权删除不应走到 service.delete_customer")

    monkeypatch.setattr(customer_router.svc, "get_visible_customer", fake_visible)
    monkeypatch.setattr(customer_router.svc, "delete_customer", should_not_delete)

    with pytest.raises(AppError) as caught:
        asyncio.run(
            customer_router.delete_customer(
                7,
                _request(),
                _current(permissions=("customer:delete_own",)),
                SimpleNamespace(),
            )
        )

    assert caught.value.http_status == 403
    assert caught.value.message == "只能删除自己负责的客户"


def test_delete_route_allows_own_customer_and_writes_audit(monkeypatch):
    customer = _customer(owner_id=1)
    deleted: list[int] = []
    audits: list[dict] = []
    session = _Session()

    async def fake_visible(*_args, **_kwargs):
        return customer

    async def fake_delete(_session, row):
        deleted.append(row.id)
        return 2

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(customer_router.svc, "get_visible_customer", fake_visible)
    monkeypatch.setattr(customer_router.svc, "delete_customer", fake_delete)
    monkeypatch.setattr(customer_router, "write_audit", fake_audit)

    result = asyncio.run(
        customer_router.delete_customer(
            7,
            _request(),
            _current(permissions=("customer:delete_own",)),
            session,
        )
    )

    assert deleted == [7]
    assert session.committed is True
    assert len(audits) == 1
    assert audits[0]["action"] == "delete"
    assert audits[0]["business_type"] == "customer"
    assert audits[0]["business_id"] == 7
    assert result["message"] == "客户已删除，2 条未完成待办已取消"
    assert result["data"]["cancelled_task_count"] == 2


# ---------------------------------------------------------------- 恢复：同一档归属规则


def test_own_permission_can_restore_own_customer():
    customer_service.ensure_can_restore_customer(
        _current(permissions=("customer:delete_own",)), _customer(owner_id=1)
    )


def test_own_permission_cannot_restore_other_owner():
    with pytest.raises(AppError) as caught:
        customer_service.ensure_can_restore_customer(
            _current(permissions=("customer:delete_own",)), _customer(owner_id=2)
        )
    assert caught.value.http_status == 403
    assert caught.value.message == "只能恢复自己负责的客户"


def test_own_permission_cannot_restore_public_pool_customer():
    with pytest.raises(AppError) as caught:
        customer_service.ensure_can_restore_customer(
            _current(permissions=("customer:delete_own",)), _customer(owner_id=None)
        )
    assert caught.value.http_status == 403
    assert caught.value.message == "只能恢复自己负责的客户"


def test_full_delete_permission_can_restore_scope_wide_customer():
    customer_service.ensure_can_restore_customer(
        _current(permissions=("customer:delete",), roles=("sales_manager",)),
        _customer(owner_id=2),
    )


def test_restore_route_rejects_other_owner_before_restoring(monkeypatch):
    customer = _customer(owner_id=2)

    async def fake_lock(*_args, **_kwargs):
        return customer

    async def should_not_restore(*_args, **_kwargs):
        raise AssertionError("越权恢复不应走到 service.restore_customer")

    monkeypatch.setattr(customer_router.svc, "lock_deleted_customer", fake_lock)
    monkeypatch.setattr(customer_router.svc, "restore_customer", should_not_restore)

    with pytest.raises(AppError) as caught:
        asyncio.run(
            customer_router.restore_customer(
                7,
                _request(),
                _current(permissions=("customer:delete_own",)),
                SimpleNamespace(),
                None,
            )
        )

    assert caught.value.http_status == 403
    assert caught.value.message == "只能恢复自己负责的客户"


def test_restore_route_allows_own_customer_and_writes_audit(monkeypatch):
    customer = _customer(owner_id=1)
    restored: list[tuple[int, int | None]] = []
    audits: list[dict] = []
    session = _Session()

    async def fake_lock(*_args, **_kwargs):
        return customer

    async def fake_restore(_session, _user, row, *, new_owner_id=None):
        restored.append((row.id, new_owner_id))
        return {}

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(customer_router.svc, "lock_deleted_customer", fake_lock)
    monkeypatch.setattr(customer_router.svc, "restore_customer", fake_restore)
    monkeypatch.setattr(customer_router, "write_audit", fake_audit)

    result = asyncio.run(
        customer_router.restore_customer(
            7,
            _request(),
            _current(permissions=("customer:delete_own",)),
            session,
            None,
        )
    )

    assert restored == [(7, None)]
    assert session.committed is True
    assert len(audits) == 1
    assert audits[0]["action"] == "restore"
    assert audits[0]["business_type"] == "customer"
    assert audits[0]["business_id"] == 7
    assert result["message"] == "客户已恢复"
