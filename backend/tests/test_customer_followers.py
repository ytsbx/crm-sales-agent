"""客户多跟进人：可见性、增删、最后一位保护、转交与公海（无默认负责人）。"""

import pytest
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.customer import followers as follower_service
from app.modules.customer.model import Customer, CustomerFollower, CustomerOwnerHistory
from app.modules.customer.service import (
    assert_customer_visible,
    transfer_customer,
)
from app.modules.user.model import User
from app.modules.task.model import Task
from conftest import SyncSessionAsAsync, make_engine, make_user


@pytest.mark.anyio
async def test_follower_can_see_customer_and_outsider_cannot():
    engine = make_engine()
    try:
        with Session(engine) as raw:
            raw.add(Customer(id=1, name="多人客户"))
            raw.add(CustomerFollower(customer_id=1, user_id=1, source="manual"))
            raw.add(CustomerFollower(customer_id=1, user_id=2, source="manual"))
            raw.flush()
            session = SyncSessionAsAsync(raw)
            customer = raw.get(Customer, 1)
            follower = make_user(1, permissions=("customer:view",), data_scope="self")
            outsider = make_user(3, permissions=("customer:view",), data_scope="self")
            await assert_customer_visible(session, follower, customer)
            with pytest.raises(AppError) as caught:
                await assert_customer_visible(session, outsider, customer)
            assert caught.value.http_status == 403
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_add_remove_last_follower_guard_and_replace():
    engine = make_engine()
    try:
        with Session(engine) as raw:
            raw.add(Customer(id=1, name="客户"))
            raw.flush()
            customer = raw.get(Customer, 1)
            session = SyncSessionAsAsync(raw)
            await follower_service.add_followers(
                session, 1, [1, 2, 2], source="manual", created_by=1
            )
            assert await follower_service.active_follower_ids(session, 1) == [1, 2]
            await follower_service.remove_follower(session, customer, 2)
            assert await follower_service.active_follower_ids(session, 1) == [1]
            # 最后一位跟进人不能直接移除
            with pytest.raises(AppError):
                await follower_service.remove_follower(session, customer, 1)
            # 转交：接手人加入、被替换人移除
            await follower_service.add_followers(session, 1, [2], source="manual")
            await follower_service.replace_follower(
                session, customer, 1, 2, source="transfer"
            )
            assert await follower_service.active_follower_ids(session, 1) == [2]
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_transfer_replaces_follower_and_pool_clears_followers():
    engine = make_engine()
    Task.__table__.create(engine, checkfirst=True)
    CustomerOwnerHistory.__table__.create(engine, checkfirst=True)
    try:
        with Session(engine) as raw:
            raw.add(Customer(id=1, name="客户"))
            raw.add(User(id=3, name="接手人", username="taker", password_hash="!", status="active"))
            raw.add(CustomerFollower(customer_id=1, user_id=1, source="manual"))
            raw.add(CustomerFollower(customer_id=1, user_id=2, source="manual"))
            raw.flush()
            session = SyncSessionAsAsync(raw)
            customer = raw.get(Customer, 1)
            # 操作人 1 本人是跟进人：转交 = 用 3 替换 1
            user = make_user(
                1, permissions=("customer:assign", "customer:view"), data_scope="all"
            )
            await transfer_customer(
                session, user, customer, 3, "转给三", move_documents=False
            )
            assert set(await follower_service.active_follower_ids(session, 1)) == {2, 3}
            # 放回公海：公海 = 没有跟进人（全部清空），pool_status=public
            await transfer_customer(
                session, user, customer, None, "放公海", move_documents=False
            )
            assert await follower_service.active_follower_ids(session, 1) == []
            assert customer.pool_status == "public"
    finally:
        engine.dispose()
