"""返工 A1 回归锁：B14-01 转移锁内复查操作者数据范围。

路由/批量入口的可见性预检查在锁外；并发转手后客户可能已落到操作者范围外的人手里。
transfer_customer 拿锁重读后必须再 assert_customer_visible（automatic 系统改派除外）。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.customer.model import Customer, CustomerOwnerHistory
from app.modules.customer.service import transfer_customer
from app.modules.user.model import User
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer, make_user


@pytest.fixture()
def raw():
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite://")
    # 转移连带待办迁移只摸 Task；全建会撞个别模型的 SQLite 不兼容表达式，逐张建
    from app.modules.task.model import Task

    for table in (User.__table__, Customer.__table__, CustomerOwnerHistory.__table__, Task.__table__):
        table.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as session:
        session.add_all([
            User(id=101, name="操作者", username="op", password_hash="fixture"),
            User(id=999, name="范围外同事", username="out", password_hash="fixture"),
        ])
        session.commit()
        yield session


@pytest.mark.anyio
async def test_transfer_rechecks_scope_inside_lock(raw):
    # 锁外预检查时客户还归操作者；走到锁内时已被范围外同事接走 → 必须 403
    raw.add(Customer(id=1, name="并发客户", owner_id=999))
    raw.commit()
    customer = raw.get(Customer, 1)
    user = make_user(101, permissions=("customer:assign",), data_scope="self")
    with pytest.raises(AppError) as error:
        await transfer_customer(
            SyncSessionAsAsync(raw), user, customer, 101, "返工回归", move_documents=False
        )
    assert error.value.http_status == 403
    assert raw.get(Customer, 1).owner_id == 999  # 一个字段都没动


@pytest.mark.anyio
async def test_transfer_in_scope_still_works(raw):
    raw.add(Customer(id=2, name="范围内客户", owner_id=101))
    raw.commit()
    customer = raw.get(Customer, 2)
    user = make_user(101, permissions=("customer:assign",), data_scope="self")
    await transfer_customer(
        SyncSessionAsAsync(raw), user, customer, 999, "返工回归", move_documents=False
    )
    assert raw.get(Customer, 2).owner_id == 999