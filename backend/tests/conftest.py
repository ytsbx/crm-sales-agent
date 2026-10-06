"""测试共用夹具：**同步内存 SQLite** 冒充 `AsyncSession`。

## 为什么需要它

本仓库约定「单元测试只测纯函数，不连数据库」（backend/pytest.ini），
但第八批要守的缺陷（8.2 搜索越权、8.3 客户全貌越权、8.4 员工编号当订单编号）
都长在**真函数 + 真 SQL**里：把 session 换成 mock 就等于自己把 SQL 重写一遍，
测不出「过滤条件写错列」这类错。交接文档 §6 给的办法正是
「实际函数 + 内存 SQLite」。

`.venv` 里没有 `aiosqlite`，所以这里用**同步 sqlite 引擎**，再把
`execute/get/scalar` 包成协程——被测函数只 `await session.execute(...)`，
拿回来的是真正的 SQLAlchemy `Result`，`.scalars().all()/.all()` 都照常工作。

本文件只放「测试替身」和「建表」两件事；用例自己的数据在各自 test_*.py 里造。
"""

from datetime import UTC, datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_BIGINT_PATCHED = False


def _register_sqlite_bigint_as_integer() -> None:
    """让 SQLite 把 `BigInteger` 建成 `INTEGER`，否则自增主键插不进去。

    SQLite 只对 `INTEGER PRIMARY KEY` 做 rowid 自增；`BIGINT PRIMARY KEY`
    插 NULL 会直接 NOT NULL 失败（本仓库主键统一 `BigInteger`）。
    这是**测试专用**的方言改写，生产 DDL 仍由 Alembic 出。
    """
    global _BIGINT_PATCHED
    if _BIGINT_PATCHED:
        return
    from sqlalchemy import BigInteger
    from sqlalchemy.ext.compiler import compiles

    @compiles(BigInteger, "sqlite")
    def _bigint_as_integer(type_, compiler, **kw):  # noqa: ARG001
        return "INTEGER"

    _BIGINT_PATCHED = True


@pytest.fixture
def anyio_backend():
    """异步用例只跑 asyncio：`pytest-asyncio` 没装，用 anyio 的 pytest 插件。

    替身会话背后是**同步** sqlite，不存在跨事件循环复用连接的问题，
    所以后端固定 asyncio 而不是让它同时跑 trio（trio 也没装）。
    """
    return "asyncio"


class SyncSessionAsAsync:
    """把同步 `Session` 包成异步会话的最小替身。

    只实现被测代码真正用到的那几个方法；**故意不实现 `add/commit/flush`**
    之外的东西——只读工具不需要，缺了会立刻 AttributeError，
    比"静默什么都没做"更容易发现替身不够用。
    """

    def __init__(self, session):
        self._session = session

    async def execute(self, stmt, *args, **kwargs):
        return self._session.execute(stmt, *args, **kwargs)

    async def get(self, entity, ident, *args, **kwargs):
        return self._session.get(entity, ident, *args, **kwargs)

    async def scalar(self, stmt, *args, **kwargs):
        return self._session.scalar(stmt, *args, **kwargs)

    async def scalars(self, stmt, *args, **kwargs):
        return self._session.scalars(stmt, *args, **kwargs)

    def add(self, obj):
        self._session.add(obj)

    async def flush(self):
        self._session.flush()

    async def commit(self):
        self._session.commit()

    async def refresh(self, obj):
        self._session.refresh(obj)


def make_engine():
    """建一个建好业务表的内存 SQLite 引擎（每调用一次就是一个全新库）。"""
    from sqlalchemy import create_engine

    from app.core.base import Base
    from app.core.idempotency import RequestKey
    from app.modules.customer.model import Contact, Customer
    from app.modules.lead.model import Lead
    from app.modules.opportunity.model import Opportunity, OpportunityStage
    from app.modules.order.model import SalesOrder
    from app.modules.payment.model import PaymentRecord, ReceivablePlan
    from app.modules.pricing.model import ExchangeRate
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.user.model import Department, User

    _register_sqlite_bigint_as_integer()

    # 只建这套用例真正用到的表：`Base.metadata.create_all` 全建会带出
    # PostgreSQL 专有索引（customer_duplicate_cases 的表达式索引）在 SQLite 上炸掉。
    tables = [
        model.__table__
        for model in (
            Department,
            User,
            Customer,
            Contact,
            Lead,
            OpportunityStage,
            Opportunity,
            Quote,
            QuoteVersion,
            SalesOrder,
            ReceivablePlan,
            PaymentRecord,
            ExchangeRate,
            # 通用请求幂等（第七批 7.9 回款登记 / 8.15 各模块重复提交）：
            # 走 router 的用例需要这张表，缺了会直接 "no such table: request_keys"。
            RequestKey,
        )
    ]
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=tables)
    return engine


@pytest.fixture()
def db_session():
    """一个全新的内存库 + 替身会话（同步用，用例里直接 `db_session.add(...)`）。"""
    from sqlalchemy.orm import Session

    engine = make_engine()
    with Session(engine) as session:
        yield session


def make_user(user_id: int = 101, *, permissions=(), roles=("salesperson",), data_scope="self"):
    """造一个真 `User` ORM 实例并包成 `CurrentUser`。

    用真实例而不是 SimpleNamespace：`CurrentUser.__init__` 读的是
    `user.id/.name/.username/.department_id`，属性名写错在替身上是静默的。
    """
    from app.core.deps import CurrentUser
    from app.modules.user.model import User

    row = User(
        id=user_id,
        name=f"测试用户{user_id}",
        username=f"tester{user_id}",
        password_hash="x",
        department_id=None,
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    return CurrentUser(
        row, permissions=set(permissions), roles=list(roles), data_scope=data_scope
    )
