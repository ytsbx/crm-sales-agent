"""§8.5 通用附件入口必须跟随来源模块的查看/写入授权（离线 mock，不连库、不连网）。

守的问题
--------
1. `visible_object(product, 任意编号)` 原来**既不查产品、也不查 `product:view`**，
   命中 `NO_OWNER_TYPES` 就直接 `return True`。于是给不存在/已删除的产品编号挂附件
   会被放行，脏关联落库后谁都不知道它挂在哪。
2. 文件路由只守 `file:view` / `file:manage`：客户/报价/订单等分支不看来源模块权限。
   有 `file:view` 而**没有** `quote:view` 的人，猜一个 file_id 就能从通用入口拿走
   报价附件；反过来只有报价权限的人也能挂/拿订单附件。
3. 已软删的对象没有一致过滤，删掉的报价上的附件照样能下载。

这里全部用**真实函数 + 假会话**（不连数据库）：权限不够时连一次库都不该查，
所以假会话把 `execute` 的次数记下来当断言。

跑法：
    cd backend
    PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -m pytest -q tests/test_file_access_authorization.py
"""

import asyncio
from datetime import UTC, datetime

from app.core.deps import CurrentUser
from app.modules.file.access import can_access_file, visible_object
from app.modules.user.model import User


class _Result:
    """够用的 SQLAlchemy 结果替身：`.first()` / `.scalars().all()`。"""

    def __init__(self, *, row=None, rows=None) -> None:
        self._row = row
        self._rows = list(rows or [])

    def first(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Link:
    """`business_files` 的一行替身。"""

    def __init__(self, business_type: str, business_id: int) -> None:
        self.business_type = business_type
        self.business_id = business_id


class _FakeSession:
    """假会话：`execute` 按排队顺序返回结果，`get` 返回预先放好的对象。

    `executed` / `gets` 记录查库次数：**权限不够时一次库都不该查**。
    查了就等于把"有没有权限"和"这个编号存不存在"混在一起，
    越权的人也能靠响应差异（403 还是 404、快还是慢）探测数据。
    """

    def __init__(self, *, results=(), get_result=None) -> None:
        self._results = list(results)
        self._get_result = get_result
        self.executed = 0
        self.gets = 0
        self.statements: list = []

    async def execute(self, statement):
        self.executed += 1
        self.statements.append(statement)
        return self._results.pop(0) if self._results else _Result(row=(1,))

    async def get(self, _model, _pk):
        self.gets += 1
        return self._get_result


class _Product:
    """产品替身：这一层只关心 `deleted_at`。"""

    def __init__(self, deleted_at=None) -> None:
        self.deleted_at = deleted_at


def _user(*permissions: str, roles=(), scope: str = "self") -> CurrentUser:
    return CurrentUser(
        User(name="测试用户", username="tester", status="active"),
        set(permissions),
        list(roles),
        scope,
    )


# ------------------------------------------------- 产品：查真实对象 + product:view


def test_nonexistent_product_object_is_denied():
    """不存在的产品编号：修前直接放行（任意编号都能挂/读附件）。"""
    session = _FakeSession(get_result=None)

    allowed = asyncio.run(
        visible_object(
            session, _user("file:view", "product:view"),
            business_type="product", business_id=987654321,
        )
    )

    assert allowed is False
    assert session.gets == 1, "必须真的查一次产品，不能凭类型字符串就放行"


def test_soft_deleted_product_object_is_denied():
    """已删除的产品与不存在同等待遇：它的附件不再可见，也不能再往上挂新的。"""
    session = _FakeSession(get_result=_Product(deleted_at=datetime.now(UTC)))

    allowed = asyncio.run(
        visible_object(
            session, _user("file:view", "product:view"),
            business_type="product", business_id=1,
        )
    )

    assert allowed is False


def test_product_requires_product_view_permission():
    """有 file:view 没有 product:view：产品附件不是"有文件权限就能看"。"""
    session = _FakeSession(get_result=_Product())

    allowed = asyncio.run(
        visible_object(session, _user("file:view", "file:manage"),
                       business_type="product", business_id=1)
    )

    assert allowed is False
    assert session.gets == 0 and session.executed == 0, "权限不够就不该去查数据"


def test_product_visible_with_product_view():
    """对照：有 product:view 且产品在，仍要放行（别修成谁都看不了）。"""
    session = _FakeSession(get_result=_Product())

    allowed = asyncio.run(
        visible_object(session, _user("file:view", "product:view"),
                       business_type="product", business_id=1)
    )

    assert allowed is True


def test_admin_bypasses_module_permission():
    """管理员口径与 `require_permission` 一致（管理员角色默认放行）。"""
    session = _FakeSession(get_result=_Product())

    allowed = asyncio.run(
        visible_object(session, _user(roles=["admin"]),
                       business_type="product", business_id=1)
    )

    assert allowed is True


# ------------------------------------------------- 客户/报价：来源模块权限 + 范围 + 未删除


def test_quote_requires_quote_view_permission():
    """有 file:view 没有 quote:view：不能从通用附件入口取报价附件。"""
    session = _FakeSession()

    allowed = asyncio.run(
        visible_object(session, _user("file:view"),
                       business_type="quote", business_id=1)
    )

    assert allowed is False
    assert session.executed == 0, "权限不够就不该去查数据"


def test_quote_read_keeps_owner_scope():
    """有 quote:view 但在数据范围外：仍然拒绝（范围过滤不能倒退）。"""
    session = _FakeSession(results=[_Result(row=None)])

    allowed = asyncio.run(
        visible_object(session, _user("file:view", "quote:view"),
                       business_type="quote", business_id=1)
    )

    assert allowed is False


def test_quote_read_inside_scope_is_allowed_and_filters_deleted():
    """范围内的报价放行，且查询里**带上了**未删除条件（修前没有这个条件）。"""
    session = _FakeSession(results=[_Result(row=(1,))])

    allowed = asyncio.run(
        visible_object(session, _user("file:view", "quote:view"),
                       business_type="quote", business_id=1)
    )

    assert allowed is True
    assert "deleted_at IS NULL" in str(session.statements[0])


def test_quote_write_requires_quote_manage():
    """挂载/解绑是写入：只有 quote:view 的人不能往报价上挂附件。"""
    session = _FakeSession()

    allowed = asyncio.run(
        visible_object(session, _user("file:manage", "quote:view"),
                       business_type="quote", business_id=1, write=True)
    )

    assert allowed is False
    assert session.executed == 0


def test_quote_write_allowed_with_quote_manage():
    """对照：有 quote:manage 且报价在范围内，写入判定放行。"""
    session = _FakeSession(results=[_Result(row=(1,))])

    allowed = asyncio.run(
        visible_object(session, _user("file:manage", "quote:manage"),
                       business_type="quote", business_id=1, write=True)
    )

    assert allowed is True


def test_unknown_business_type_defaults_to_denied():
    """没登记的类型默认拒绝（不能因为"看起来无害"就默默放行）。"""
    session = _FakeSession()

    allowed = asyncio.run(
        visible_object(session, _user("file:view", "file:manage"),
                       business_type="sku", business_id=1)
    )

    assert allowed is False


def test_product_write_gate_stays_on_file_manage():
    """产品附件的**写入**授权仍由文件中心的 file:manage 承担。

    产品字段维护是 product:manage，但产品附件的两个入口（通用入口与
    `POST /products/{id}/files`）一直只要求 file:manage；收紧到 product:manage
    属于改口径，得连守门套件 check_data_scope 的对照用例一起改，本轮先不动。
    """
    session = _FakeSession(get_result=_Product())

    allowed = asyncio.run(
        visible_object(session, _user("file:view", "file:manage", "product:view"),
                       business_type="product", business_id=1, write=True)
    )

    assert allowed is True


# ------------------------------------------------- 详情/下载：单独守门，不是只靠列表


def test_can_access_file_follows_product_permission():
    """文件挂在一个"看得见"的产品上、但用户没有 product:view：详情/下载必须被拒。

    这是 §8.5 里"只靠列表过滤不够"的那一半——列表过滤挡不住猜 file_id。
    """
    session = _FakeSession(
        results=[_Result(rows=[_Link("product", 1)])], get_result=_Product()
    )

    allowed = asyncio.run(
        can_access_file(session, _user("file:view", "file:manage"), 5)
    )

    assert allowed is False


def test_can_access_file_keeps_any_visible_link_rule():
    """一个文件挂多个对象时，**任一关联可见即可见**的老口径不能倒退。"""
    session = _FakeSession(
        results=[
            _Result(rows=[_Link("product", 1), _Link("quote", 2)]),
            _Result(row=(1,)),
        ],
        get_result=_Product(),
    )

    allowed = asyncio.run(
        can_access_file(session, _user("file:view", "quote:view"), 5)
    )

    assert allowed is True
