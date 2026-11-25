import pytest
from sqlalchemy import CheckConstraint, MetaData, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.base import Base
from app.core.errors import AppError
from app.modules.auth.model import LoginSession
from app.modules.customer.model import Customer
from app.modules.user import router
from app.modules.user.model import Role, User, user_roles
from conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer, make_user


class AccountSession(SyncSessionAsAsync):
    async def delete(self, row):
        self._session.delete(row)


@pytest.fixture
def accounts():
    from app.main import app  # noqa: F401 — load every CRM model checked by account deletion

    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    schema = MetaData()
    for table in Base.metadata.sorted_tables:
        local_table = table.to_metadata(schema)
        for constraint in tuple(local_table.constraints):
            if isinstance(constraint, CheckConstraint):
                local_table.constraints.remove(constraint)
    with engine.begin() as connection:
        for table in schema.sorted_tables:
            connection.execute(CreateTable(table))
    with Session(engine) as raw:
        raw.add_all([User(id=1, name="管理员", username="admin", password_hash="x"),
                     User(id=2, name="员工", username="staff", password_hash="x", status="disabled")])
        raw.commit()
        yield raw, AccountSession(raw)
    engine.dispose()


REQUEST = Request({"type": "http", "headers": []})
ADMIN = make_user(1, roles=("admin",))


@pytest.mark.anyio
async def test_delete_unused_disabled_account_removes_only_account_and_roles(accounts):
    raw, session = accounts
    raw.add(Role(id=4, code="sales", name="销售"))
    raw.flush()
    raw.execute(user_roles.insert().values(user_id=2, role_id=4))
    raw.commit()

    await router.delete_unused_user(2, REQUEST, ADMIN, session)

    assert raw.get(User, 2) is None
    assert raw.get(Role, 4) is not None
    assert raw.query(AuditLog).filter_by(action="delete", business_id=2).one().operator_id == 1


@pytest.mark.anyio
@pytest.mark.parametrize("history", ["login", "business", "audit", "binding", "active"])
async def test_delete_rejects_account_already_in_use(accounts, history):
    raw, session = accounts
    if history == "login":
        raw.add(LoginSession(id=6, sid="test", user_id=2))
    elif history == "business":
        raw.add(Customer(id=6, name="已有客户", owner_id=2, created_by=1))
    elif history == "audit":
        raw.add(AuditLog(id=6, operator_id=2, action="login"))
    elif history == "binding":
        raw.get(User, 2).dingtalk_userid = "scan"
    else:
        raw.get(User, 2).status = "active"
    raw.commit()

    with pytest.raises(AppError):
        await router.delete_unused_user(2, REQUEST, ADMIN, session)
    assert raw.get(User, 2) is not None


@pytest.mark.anyio
async def test_archive_keeps_history_and_revokes_login(accounts):
    raw, session = accounts
    raw.get(User, 2).status = "active"
    raw.add_all([LoginSession(id=6, sid="test", user_id=2),
                 Customer(id=6, name="历史客户", owner_id=2, created_by=1)])
    raw.commit()

    await router.archive_user(2, REQUEST, ADMIN, session)

    assert raw.get(User, 2).status == "archived"
    assert raw.get(LoginSession, 6).status == "revoked"
    assert raw.get(Customer, 6).owner_id == 2
    default_list = await router.list_users(omit_archived=True, page=1, page_size=20, user=ADMIN, session=session)
    archived_list = await router.list_users(status="archived", omit_archived=True, page=1, page_size=20,
                                           user=ADMIN, session=session)
    assert [row["id"] for row in default_list["data"]["items"]] == [1]
    assert [row["id"] for row in archived_list["data"]["items"]] == [2]
    with pytest.raises(AppError, match="不能直接启用"):
        await router.enable_user(2, REQUEST, ADMIN, session)


@pytest.mark.anyio
async def test_only_admin_can_archive_or_delete(accounts):
    _, session = accounts
    manager = make_user(3, permissions=("user:manage",))
    for endpoint in (router.archive_user, router.delete_unused_user):
        with pytest.raises(AppError) as error:
            await endpoint(2, REQUEST, manager, session)
        assert error.value.http_status == 403
