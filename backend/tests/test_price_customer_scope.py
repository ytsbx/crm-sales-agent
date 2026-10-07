"""客户专属价的**范围判据**：直接钉住 `ensure_customer_in_scope_for_price`。

为什么不靠端到端套件（审查 2026-10-07 第二轮）：本项目 seed 里**所有有改价权的
账号都在同一个部门**（admin / 李四 / 张三 的 `department_id` 都是 1），
而李四是"本部门及下级"范围 —— "有权限、但看不到这个客户"的组合在真实数据里
根本造不出来，所以端到端只能验到"被拒"（那可能是权限不够挡的）。

判据是**一处写、四处用**（新增 / 修改 / 删除 / 列表），所以这里直接测它。
上一轮就是"新增修改查了、删除和列表漏了"，才出的越权删除。
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine

from app.core.base import Base
from app.core.errors import AppError
from app.modules.customer.model import Customer
from app.modules.pricing import service as pricing
from app.modules.product.model import Product, Sku
from app.modules.user.model import User


def _engine():
    engine = make_engine()
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Customer.__table__, Product.__table__, Sku.__table__],
    )
    return engine


def _session():
    return SyncSessionAsAsync(Session(_engine()))


def _add_customer(session, name: str, owner_id: int | None) -> int:
    row = Customer(name=name, owner_id=owner_id)
    session.add(row)
    asyncio.run(session.flush())
    return row.id


def _user(user_id: int, *, scope: str = "self", roles: list[str] | None = None):
    return SimpleNamespace(
        id=user_id, roles=roles or ["salesperson"], data_scope=scope, permissions=set()
    )


def test_scope_denies_other_owner():
    """别人负责的客户 → 拒绝（这正是端到端造不出来的那一档）。"""
    session = _session()
    customer_id = _add_customer(session, "别人的客户", 9)

    with pytest.raises(AppError) as caught:
        asyncio.run(
            pricing.ensure_customer_in_scope_for_price(
                session, user=_user(1), customer_id=customer_id
            )
        )
    assert caught.value.code == 40302


def test_scope_allows_own_pool_and_admin():
    """自己负责的、无负责人的（公海）、以及管理员 → 放行。

    公海必须放行：它与"客户列表里看得到公海"同一口径，否则界面能建、接口 403。
    """
    session = _session()
    mine = _add_customer(session, "我的客户", 1)
    pool = _add_customer(session, "公海客户", None)
    others = _add_customer(session, "别人的客户", 9)

    asyncio.run(
        pricing.ensure_customer_in_scope_for_price(session, user=_user(1), customer_id=mine)
    )
    asyncio.run(
        pricing.ensure_customer_in_scope_for_price(session, user=_user(1), customer_id=pool)
    )
    asyncio.run(
        pricing.ensure_customer_in_scope_for_price(
            session, user=_user(2, scope="all", roles=["admin"]), customer_id=others
        )
    )


def test_scope_rejects_missing_or_deleted_customer():
    """客户不存在 / 已软删 → 404，不能拿它当"范围外"混过去。"""
    from datetime import UTC, datetime

    session = _session()
    gone = _add_customer(session, "已删除的客户", 1)
    row = asyncio.run(session.get(Customer, gone))
    row.deleted_at = datetime.now(UTC)
    asyncio.run(session.flush())

    with pytest.raises(AppError) as caught:
        asyncio.run(
            pricing.ensure_customer_in_scope_for_price(
                session, user=_user(1), customer_id=gone
            )
        )
    assert caught.value.code == 40401
