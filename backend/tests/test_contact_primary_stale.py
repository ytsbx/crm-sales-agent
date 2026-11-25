"""主位写入不得因等锁前ORM旧值为True而被省略；实际PG并发另有探针。"""
import asyncio

import pytest
from sqlalchemy import select, update, text
from sqlalchemy.orm import Session

from conftest import make_engine, SyncSessionAsAsync
from app.modules.customer.model import Customer, Contact
from app.modules.customer.service import set_primary_contact


@pytest.mark.parametrize('stale_true', [False, True])
def test_explicit_primary_selection_persists_after_other_selection(stale_true):
    async def run():
        engine=make_engine()
        try:
            # 模型仅声明postgresql_where；SQLite测试中显式复现相同部分唯一索引。
            with engine.begin() as connection:
                connection.execute(text('DROP INDEX uq_contacts_primary_per_customer'))
                connection.execute(text('CREATE UNIQUE INDEX uq_contacts_primary_per_customer ON contacts(customer_id) WHERE is_primary=1 AND deleted_at IS NULL'))
            with Session(engine,expire_on_commit=False) as raw:
                raw.add(Customer(id=1,name='隔离主联系人客户'))
                a=Contact(id=1,customer_id=1,name='A',is_primary=stale_true)
                b=Contact(id=2,customer_id=1,name='B',is_primary=not stale_true)
                raw.add_all([a,b]);raw.commit()
                if stale_true:
                    # 模拟另一个事务在请求已读A后切到B；本请求identity map仍是A=True。
                    raw.execute(update(Contact).where(Contact.id==1).values(is_primary=False).execution_options(synchronize_session=False))
                    raw.execute(update(Contact).where(Contact.id==2).values(is_primary=True).execution_options(synchronize_session=False))
                    raw.commit()
                    assert a.is_primary is True
                s=SyncSessionAsAsync(raw)
                for _ in range(2):
                    await set_primary_contact(s,a)
                    await s.flush()
                    actual=raw.execute(select(Contact.id,Contact.is_primary).order_by(Contact.id)).all()
                    assert actual==[(1,True),(2,False)],actual
                raw.rollback()
        finally:engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize('deleted', [False, True])
def test_changed_or_deleted_contact_is_rejected_before_clearing_primaries(deleted):
    from datetime import UTC, datetime
    from app.core.errors import AppError

    async def run():
        engine=make_engine()
        try:
            with engine.begin() as connection:
                connection.execute(text('DROP INDEX uq_contacts_primary_per_customer'))
                connection.execute(text('CREATE UNIQUE INDEX uq_contacts_primary_per_customer ON contacts(customer_id) WHERE is_primary=1 AND deleted_at IS NULL'))
            with Session(engine,expire_on_commit=False) as raw:
                raw.add_all([Customer(id=1,name='甲'),Customer(id=2,name='乙')])
                moving=Contact(id=1,customer_id=1,name='改挂人',is_primary=False)
                raw.add_all([moving,Contact(id=2,customer_id=1,name='甲主',is_primary=True),Contact(id=3,customer_id=2,name='乙主',is_primary=True)])
                raw.commit()
                values={'deleted_at':datetime.now(UTC)} if deleted else {'customer_id':2}
                raw.execute(update(Contact).where(Contact.id==1).values(**values).execution_options(synchronize_session=False))
                raw.commit()
                before=raw.execute(select(Contact.id,Contact.customer_id,Contact.is_primary,Contact.deleted_at).order_by(Contact.id)).all()
                assert moving.customer_id==1 and moving.deleted_at is None
                with pytest.raises(AppError) as error:
                    await set_primary_contact(SyncSessionAsAsync(raw),moving)
                assert error.value.http_status==(404 if deleted else 409)
                if not deleted:assert '已改挂' in error.value.message
                assert raw.execute(select(Contact.id,Contact.customer_id,Contact.is_primary,Contact.deleted_at).order_by(Contact.id)).all()==before
                raw.rollback()
        finally:engine.dispose()
    asyncio.run(run())
