"""客户软删除 / 恢复的授权边界（2026-11-27 起按跟进人）。

业务员（`customer:delete_own`）只能删/恢复**只有自己一位跟进人**的客户；
多人跟进或公海客户需要主管/管理员（`customer:delete`）。
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError
from app.modules.customer import router as customer_router
from app.modules.customer import service as customer_service
from app.modules.customer.model import Customer, CustomerFollower
from app.modules.user.model import User
from conftest import SyncSessionAsAsync, make_engine


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


def _seed(follower_ids: tuple[int, ...], *, pool_status: str = "private"):
    engine = make_engine()
    with Session(engine) as raw:
        raw.add(Customer(id=7, name="测试客户", pool_status=pool_status))
        for uid in follower_ids:
            raw.add(CustomerFollower(customer_id=7, user_id=uid, source="manual"))
        raw.commit()
        customer = raw.get(Customer, 7)
        return SyncSessionAsAsync(raw), engine, customer


# ---------------------------------------------------------------- 删除规则


def test_sole_follower_can_delete_own_customer():
    session, engine, customer = _seed((1,))
    try:
        asyncio.run(
            customer_service.ensure_can_delete_customer(
                session, _current(permissions=("customer:delete_own",)), customer
            )
        )
    finally:
        engine.dispose()


def test_own_permission_cannot_delete_shared_customer():
    session, engine, customer = _seed((1, 2))
    try:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                customer_service.ensure_can_delete_customer(
                    session, _current(permissions=("customer:delete_own",)), customer
                )
            )
        assert caught.value.http_status == 403
        assert "只能删除自己单独跟进的客户" in caught.value.message
    finally:
        engine.dispose()


def test_own_permission_cannot_delete_public_pool_customer():
    session, engine, customer = _seed((), pool_status="public")
    try:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                customer_service.ensure_can_delete_customer(
                    session, _current(permissions=("customer:delete_own",)), customer
                )
            )
        assert caught.value.http_status == 403
    finally:
        engine.dispose()


def test_full_delete_permission_keeps_scope_wide_behavior():
    session, engine, customer = _seed((2, 3))
    try:
        asyncio.run(
            customer_service.ensure_can_delete_customer(
                session,
                _current(permissions=("customer:delete",), roles=("sales_manager",)),
                customer,
            )
        )
    finally:
        engine.dispose()


def test_admin_does_not_need_the_permission_codes():
    session, engine, customer = _seed((2, 3))
    try:
        asyncio.run(
            customer_service.ensure_can_delete_customer(
                session, _current(permissions=(), roles=("admin",)), customer
            )
        )
    finally:
        engine.dispose()


def test_no_delete_permission_is_rejected():
    session, engine, customer = _seed((1,))
    try:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                customer_service.ensure_can_delete_customer(session, _current(), customer)
            )
        assert caught.value.http_status == 403
        assert "customer:delete" in caught.value.message
    finally:
        engine.dispose()


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


def test_delete_route_rejects_shared_customer_before_writing(monkeypatch):
    session, engine, customer = _seed((1, 2))
    try:
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
                    session,
                )
            )
        assert caught.value.http_status == 403
        assert "只能删除自己单独跟进的客户" in caught.value.message
    finally:
        engine.dispose()


def test_delete_route_allows_sole_follower_and_writes_audit(monkeypatch):
    session, engine, customer = _seed((1,))
    deleted: list[int] = []
    audits: list[dict] = []
    fake_session = _Session()
    try:
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
        assert session._session is not None
        assert len(audits) == 1
        assert audits[0]["action"] == "delete"
        assert audits[0]["business_type"] == "customer"
        assert audits[0]["business_id"] == 7
        assert result["message"] == "客户已删除，2 条未完成待办已取消"
        assert result["data"]["cancelled_task_count"] == 2
    finally:
        engine.dispose()


# ---------------------------------------------------------------- 恢复：同一档规则


def test_sole_follower_can_restore_own_customer():
    session, engine, customer = _seed((1,))
    try:
        asyncio.run(
            customer_service.ensure_can_restore_customer(
                session, _current(permissions=("customer:delete_own",)), customer
            )
        )
    finally:
        engine.dispose()


def test_own_permission_cannot_restore_shared_customer():
    session, engine, customer = _seed((1, 2))
    try:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                customer_service.ensure_can_restore_customer(
                    session, _current(permissions=("customer:delete_own",)), customer
                )
            )
        assert caught.value.http_status == 403
        assert "只能恢复自己单独跟进的客户" in caught.value.message
    finally:
        engine.dispose()


def test_own_permission_cannot_restore_public_pool_customer():
    session, engine, customer = _seed((), pool_status="public")
    try:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                customer_service.ensure_can_restore_customer(
                    session, _current(permissions=("customer:delete_own",)), customer
                )
            )
        assert caught.value.http_status == 403
    finally:
        engine.dispose()


def test_full_delete_permission_can_restore_scope_wide_customer():
    session, engine, customer = _seed((2, 3))
    try:
        asyncio.run(
            customer_service.ensure_can_restore_customer(
                session,
                _current(permissions=("customer:delete",), roles=("sales_manager",)),
                customer,
            )
        )
    finally:
        engine.dispose()


def test_restore_route_rejects_shared_customer_before_restoring(monkeypatch):
    session, engine, customer = _seed((1, 2))
    try:
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
                    session,
                    None,
                )
            )
        assert caught.value.http_status == 403
    finally:
        engine.dispose()


def test_restore_route_allows_sole_follower_and_writes_audit(monkeypatch):
    session, engine, customer = _seed((1,))
    restored: list[tuple[int, int | None]] = []
    audits: list[dict] = []
    try:
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
        assert len(audits) == 1
        assert audits[0]["action"] == "restore"
        assert audits[0]["business_type"] == "customer"
        assert audits[0]["business_id"] == 7
        assert result["message"] == "客户已恢复"
    finally:
        engine.dispose()
