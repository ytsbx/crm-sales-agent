"""客户多跟进人：可见性、增删、默认负责人与转交/公海。"""

import pytest
from sqlalchemy import select
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
            raw.add(Customer(id=1, name="多人客户", owner_id=2))
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
async def test_add_remove_and_set_primary_followers():
    engine = make_engine()
    try:
        with Session(engine) as raw:
            raw.add(Customer(id=1, name="客户", owner_id=1))
            raw.flush()
            customer = raw.get(Customer, 1)
            session = SyncSessionAsAsync(raw)
            await follower_service.add_followers(
                session, 1, [1, 2, 2], source="manual", created_by=1
            )
            assert await follower_service.active_follower_ids(session, 1) == [1, 2]
            # 默认负责人不能直接移除
            with pytest.raises(AppError):
                await follower_service.remove_follower(session, customer, 1)
            await follower_service.remove_follower(session, customer, 2)
            assert await follower_service.active_follower_ids(session, 1) == [1]
            # 非跟进人不能设为默认
            with pytest.raises(AppError):
                await follower_service.set_default_follower(session, customer, 2)
            await follower_service.add_followers(session, 1, [2], source="manual")
            await follower_service.set_default_follower(session, customer, 2)
            assert customer.owner_id == 2
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_transfer_moves_default_but_keeps_other_followers_and_pool_keeps_all():
    engine = make_engine()
    Task.__table__.create(engine, checkfirst=True)
    CustomerOwnerHistory.__table__.create(engine, checkfirst=True)
    try:
        with Session(engine) as raw:
            raw.add(Customer(id=1, name="客户", owner_id=1))
            raw.add(User(id=3, name="接手人", username="taker", password_hash="!", status="active"))
            raw.add(CustomerFollower(customer_id=1, user_id=1, source="manual"))
            raw.add(CustomerFollower(customer_id=1, user_id=2, source="manual"))
            raw.flush()
            session = SyncSessionAsAsync(raw)
            customer = raw.get(Customer, 1)
            user = make_user(
                9, permissions=("customer:assign", "customer:view"), data_scope="all"
            )
            await transfer_customer(
                session, user, customer, 3, "转给三", move_documents=False
            )
            assert customer.owner_id == 3
            assert set(await follower_service.active_follower_ids(session, 1)) == {2, 3}
            # 放回公海：默认负责人清空，跟进人全部保留
            await transfer_customer(
                session, user, customer, None, "放公海", move_documents=False
            )
            assert customer.owner_id is None
            assert set(await follower_service.active_follower_ids(session, 1)) == {2, 3}
    finally:
        engine.dispose()
