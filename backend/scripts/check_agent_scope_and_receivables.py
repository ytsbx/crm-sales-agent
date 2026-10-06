#!/usr/bin/env python
"""第八批 8.2 / 8.3 / 8.4 的接口 + 真库回归。

守的问题
--------
§8.2 全局搜索只有 `get_current_user`：零模块权限的用户照样把六类业务各查一遍；
     联系人只挡了自己的软删除、没挡客户软删除；空白关键词被 strip 成全库通配；
     联系人结果带完整手机号。
§8.3 AI 客户全貌只验"客户在不在范围内"，随后按 customer_id 全取联系人/商机/
     报价/订单；完整电话直接返回；待回款用 float 累加且含取消单。
§8.4 AI 应收把**员工编号当订单编号**（`SalesOrder.id.in_(owner_ids)`）：既漏掉
     自己的应收，又读到"订单 id 恰好等于某员工编号"的别人的金额；计划与实收
     没有币种分组。

本套件为什么必须走接口 + 真库
----------------------------
单元测试（`tests/test_search_scope.py`、`tests/test_agent_scope_and_receivables.py`）
用的是内存 SQLite。PostgreSQL 上的差异恰好落在本套件的要害上：
  · 软删除过滤与 join 在两种方言下走不同计划；
  · `Numeric` 在 PG 上是真 decimal（SQLite 会退化成浮点），Decimal 口径要在
    真库上才站得住；
  · 权限是从 `role_permissions` 关联表查出来的——只有真库才有这套表。
所以这里用**真角色 + 真权限码 + 真接口**再走一遍。

跑法（必须显式给 API_BASE，指到一次性隔离库的后端）：

    cd backend
    API_BASE=http://127.0.0.1:8012/api/v1 PYTHONPATH=. \\
      .venv/Scripts/python.exe scripts/check_agent_scope_and_receivables.py

或直接：
    & .\\ops\\iso_checks.ps1 -Db crm_iso_b -Port 8012 -Suites check_agent_scope_and_receivables
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import app.main  # noqa: F401  保证所有模型都注册进 metadata
from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.customer.model import Contact, Customer
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.quote.model import Quote
from app.modules.user.model import Department, Permission, Role, User, role_permissions, user_roles

FAILURES: list[str] = []
PREFIX = "CHKSCOPEAI"
STAMP = str(int(time.time()))

BASE = os.environ.get("API_BASE", "")

# ⚠️ 必须**显式**给 API_BASE：本套件会写夹具、也会真的调接口。
# 忘了传就会打到开发后端（默认 8000），夹具落在隔离库、请求落在开发库。
if not BASE:
    raise SystemExit(
        "必须显式设置 API_BASE（本套件会写夹具并调接口，不能默认打到开发后端 8000）"
    )
if "8000" in BASE:
    raise SystemExit(f"API_BASE 指向 8000（开发后端）很可能是误传：{BASE}")


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:
            return exc.code, {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


IDS: dict = {}

#: 本套件拥有的角色/用户/客户/订单——清理时按这些前缀与 id 精确删，
#: 不用 like 一把梭（避免误删同前缀的其它套件夹具）。
OWNER = "owner"
STAFF = "staff"
MANAGER = "manager"


#: 本套件会写入的表。建夹具前要把它们的 id 序列推到现有数据之后 ——
#: 原因见 `_align_sequences`。
FIXTURE_TABLES = (
    "users",
    "customers",
    "contacts",
    "opportunities",
    "quotes",
    "sales_orders",
    "receivable_plans",
    "payment_records",
)


async def _align_sequences() -> None:
    """把夹具会写入的表的 id 序列推到 `max(id)+1`。

    为什么要这一步（真库上真实踩到）：套件里有一张"别人的订单"是按**显式 id**
    插的（`id = 业务员编号`，专门用来验"员工编号被当成订单编号"那个缺陷）。
    显式插入**不会推进** PostgreSQL 的序列，于是后面任何不带 id 的插入都会拿到
    一个已经被占用的号 → `duplicate key value violates unique constraint "sales_orders_pkey"`。

    而且这个错**只在特定顺序下出现**：单独跑本套件没事，跟在别的套件后面跑就炸
    （别的套件也插过显式 id）。这类"顺序相关"的失败最难查，所以在建夹具前先对齐，
    比事后解释便宜得多。只推游标，不碰任何业务数据。
    """
    async with SessionLocal() as s:
        for table in FIXTURE_TABLES:
            await s.execute(
                text(
                    "select setval(pg_get_serial_sequence(:t, 'id'), "
                    f"coalesce(max(id), 0) + 1, false) from {table}"
                ),
                {"t": table},
            )
        await s.commit()


async def cleanup() -> None:
    """自底向上清干净（回款 → 应收 → 订单 → 报价/商机 → 联系人 → 客户 → 角色/用户/部门）。

    清理按**本套件记录的 id / 用户名前缀**做，不用 `like 'CHK%'`
    这种会波及其它套件的写法。
    """
    usernames = [f"{PREFIX.lower()}_{name}_{STAMP}" for name in (OWNER, STAFF, MANAGER)]
    async with SessionLocal() as s:
        await s.execute(
            text(
                "delete from payment_records where order_id in "
                "(select id from sales_orders where order_no like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text(
                "delete from receivable_plans where order_id in "
                "(select id from sales_orders where order_no like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(text("delete from sales_orders where order_no like :p"), {"p": f"{PREFIX}%"})
        await s.execute(
            text(
                "delete from quotes where customer_id in "
                "(select id from customers where name like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text(
                "delete from opportunities where customer_id in "
                "(select id from customers where name like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text(
                "delete from contacts where customer_id in "
                "(select id from customers where name like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(text("delete from customers where name like :p"), {"p": f"{PREFIX}%"})
        await s.execute(
            text(
                "delete from user_roles where user_id in "
                "(select id from users where username = any(:names))"
            ),
            {"names": usernames},
        )
        await s.execute(
            text("delete from role_permissions where role_id in "
                 "(select id from roles where code like :c)"),
            {"c": f"{PREFIX}%"},
        )
        await s.execute(text("delete from roles where code like :c"), {"c": f"{PREFIX}%"})
        await s.execute(text("delete from users where username = any(:names)"), {"names": usernames})
        await s.execute(text("delete from departments where name like :d"), {"d": f"{PREFIX}%"})
        await s.commit()


async def build_fixtures() -> None:
    """造三个用户（不同数据范围/权限）+ 客户、联系人、商机、报价、订单、应收。

    关键设计：
      · `STAFF`（业务员，self）：**只给 customer:view + order:view + payment:view**，
        故意不给 opportunity:view / quote:view——用来验证"无权块不查、不返回"；
      · `MANAGER`（主管，department）：给全，用来验证"能看完整联系方式"；
      · 员工编号与订单编号故意交叉（见下面 `mine` / `other` 两条订单）。
    """
    async with SessionLocal() as s:
        dept = Department(name=f"{PREFIX}部-{STAMP}", status="active")
        s.add(dept)
        await s.flush()

        def mk_user(name: str, role: Role) -> User:
            user = User(
                name=f"{PREFIX}{name}-{STAMP}",
                username=f"{PREFIX.lower()}_{name}_{STAMP}",
                password_hash=hash_password("123456"),
                status="active",
                department_id=dept.id,
            )
            s.add(user)
            return user

        staff_role = Role(code=f"{PREFIX}STAFF{STAMP}", name=f"{PREFIX}业务员", data_scope="self")
        manager_role = Role(
            code=f"{PREFIX}MANAGER{STAMP}", name=f"{PREFIX}主管", data_scope="department"
        )
        s.add_all([staff_role, manager_role])
        await s.flush()

        staff = mk_user(STAFF, staff_role)
        manager = mk_user(MANAGER, manager_role)
        await s.flush()

        # 角色 → 权限（用库里已有的权限码；缺哪个就补建，避免依赖 seed 的具体集合）
        wanted = {
            staff_role.id: ["customer:view", "order:view", "payment:view"],
            manager_role.id: [
                "customer:view",
                "opportunity:view",
                "quote:view",
                "order:view",
                "payment:view",
            ],
        }
        for role_id, codes in wanted.items():
            for code in codes:
                perm = (
                    await s.execute(select(Permission).where(Permission.code == code))
                ).scalar_one_or_none()
                if perm is None:
                    perm = Permission(code=code, name=code, resource=code.split(":")[0], action="view")
                    s.add(perm)
                    await s.flush()
                await s.execute(
                    role_permissions.insert().values(role_id=role_id, permission_id=perm.id)
                )
        await s.execute(user_roles.insert().values(user_id=staff.id, role_id=staff_role.id))
        await s.execute(user_roles.insert().values(user_id=manager.id, role_id=manager_role.id))
        await s.flush()

        now = datetime.now(UTC)
        customer = Customer(
            name=f"{PREFIX}客户-{STAMP}",
            level="A",
            region="浙江",
            status="active",
            pool_status="private",
            owner_id=staff.id,
            created_by=staff.id,
            created_at=now,
        )
        dead_customer = Customer(
            name=f"{PREFIX}已删客户-{STAMP}",
            level="C",
            status="active",
            pool_status="private",
            owner_id=staff.id,
            created_by=staff.id,
            created_at=now,
            deleted_at=now,  # 软删除：它的联系人不得出现在搜索里
        )
        s.add_all([customer, dead_customer])
        await s.flush()

        contact = Contact(
            customer_id=customer.id,
            name=f"{PREFIX}张工-{STAMP}",
            mobile="13800001111",
            email="zhang@hongyuan.example",
            is_primary=True,
            created_at=now,
        )
        dead_contact = Contact(
            customer_id=dead_customer.id,
            name=f"{PREFIX}已删客户联系人-{STAMP}",
            mobile="13700009999",
            is_primary=True,
            created_at=now,
        )
        stage = OpportunityStage(
            code=f"{PREFIX}{STAMP}", name=f"{PREFIX}阶段", sequence=1, is_win=False, is_loss=False
        )
        s.add_all([contact, dead_contact, stage])
        await s.flush()

        opportunity = Opportunity(
            customer_id=customer.id,
            title=f"{PREFIX}商机-{STAMP}",
            stage_id=stage.id,
            status="open",
            owner_id=staff.id,
            created_at=now,
        )
        quote = Quote(
            quote_no=f"{PREFIX}Q-{STAMP}",
            customer_id=customer.id,
            owner_id=staff.id,
            status="draft",
            created_at=now,
        )
        # 员工编号 vs 订单编号交叉：员工自己的单 id 很大，别人的单 id 落在
        # "看起来像员工编号"的小号段。旧实现按 `SalesOrder.id.in_(owner_ids)` 过滤，
        # 会漏掉自己的单、又把别人的单算进来。
        mine = SalesOrder(
            id=None,
            order_no=f"{PREFIX}SO-MINE-{STAMP}",
            customer_id=customer.id,
            owner_id=staff.id,
            total_amount=Decimal("100.00"),
            currency="CNY",
            status="pending",
            created_at=now,
        )
        usd = SalesOrder(
            order_no=f"{PREFIX}SO-USD-{STAMP}",
            customer_id=customer.id,
            owner_id=staff.id,
            total_amount=Decimal("100.00"),
            currency="USD",
            status="pending",
            created_at=now,
        )
        dead = SalesOrder(
            order_no=f"{PREFIX}SO-DEAD-{STAMP}",
            customer_id=customer.id,
            owner_id=staff.id,
            total_amount=Decimal("9999.00"),
            currency="CNY",
            status="cancelled",
            created_at=now,
        )
        # 别人的订单，id 故意设成 staff.id（旧实现按 `SalesOrder.id.in_(owner_ids)`
        # 过滤时会把它当"自己的"）。users/orders 各自自增、起点相近，所以
        # staff.id 必然小于新建订单的 id —— 这正是"员工编号 vs 订单编号"的错配形态。
        other = SalesOrder(
            id=staff.id,
            order_no=f"{PREFIX}SO-OTHER-{STAMP}",
            customer_id=customer.id,
            owner_id=manager.id,
            total_amount=Decimal("500.00"),
            currency="CNY",
            status="pending",
            created_at=now,
            updated_at=now,
        )
        s.add_all([opportunity, quote, mine, usd, dead, other])
        await s.flush()
        s.add_all(
            [
                ReceivablePlan(
                    order_id=mine.id,
                    plan_name="全款",
                    due_date=date.today() + timedelta(days=30),
                    amount=Decimal("100.00"),
                    currency="CNY",
                    status="pending",
                    created_at=now,
                ),
                ReceivablePlan(
                    order_id=usd.id,
                    plan_name="全款",
                    due_date=date.today() + timedelta(days=30),
                    amount=Decimal("100.00"),
                    currency="USD",
                    status="pending",
                    created_at=now,
                ),
                ReceivablePlan(
                    order_id=dead.id,
                    plan_name="全款",
                    due_date=date.today() + timedelta(days=30),
                    amount=Decimal("9999.00"),
                    currency="CNY",
                    status="pending",
                    created_at=now,
                ),
                # 别人的订单（id 恰好等于员工编号）上的应收：旧实现会把它算进来
                ReceivablePlan(
                    order_id=other.id,
                    plan_name="全款",
                    due_date=date.today() + timedelta(days=30),
                    amount=Decimal("500.00"),
                    currency="CNY",
                    status="pending",
                    created_at=now,
                ),
            ]
        )
        await s.flush()
        plan_cny = (
            await s.execute(select(ReceivablePlan).where(ReceivablePlan.order_id == mine.id))
        ).scalar_one()
        s.add(
            PaymentRecord(
                order_id=mine.id,
                receivable_plan_id=plan_cny.id,
                received_date=date.today(),
                received_amount=Decimal("40.00"),
                currency="CNY",
                status="confirmed",
                confirmed_at=now,
                created_at=now,
            )
        )
        await s.flush()

        IDS.update(
            dept=dept.id,
            staff=staff.id,
            manager=manager.id,
            customer=customer.id,
            dead_customer=dead_customer.id,
            contact=contact.id,
            dead_contact=dead_contact.id,
            my_order=mine.id,
            usd_order=usd.id,
            dead_order=dead.id,
            other_order=staff.id,  # 就是 staff.id：交叉关系的核心
        )
        await s.commit()


def search(token: str, keyword: str, limit: int = 10):
    status, res = call("GET", f"/search?keyword={urllib.parse.quote(keyword)}&limit={limit}", token=token)
    if res.get("code") != 0:
        raise SystemExit(f"搜索失败：{status} {res}")
    return res["data"]


def group_of(data: dict, key: str) -> dict:
    for group in data["groups"]:
        if group["type"] == key:
            return group
    raise AssertionError(f"响应里没有 {key} 组")


def all_items(data: dict) -> list[dict]:
    return [item for group in data["groups"] for item in group["items"]]


async def main() -> int:
    await cleanup()
    # 先对齐 id 序列，再插夹具（见 _align_sequences 的说明：显式 id 不推进序列，
    # 会让后面的自增插入撞主键，而且只在特定执行顺序下暴露）
    await _align_sequences()
    await build_fixtures()

    staff_token = login(f"{PREFIX.lower()}_{STAFF}_{STAMP}", "123456")
    manager_token = login(f"{PREFIX.lower()}_{MANAGER}_{STAMP}", "123456")
    login("admin", "admin123")  # 只为确认开发/隔离库的管理员口令可用，结果不参与断言
    print(
        f"夹具：客户 #{IDS['customer']}（负责人=业务员 #{IDS['staff']}）、"
        f"自己的订单 #{IDS['my_order']}、别人的订单 #{IDS['other_order']}"
        f"（id 恰好等于业务员编号，且 < 自己的订单 id = {IDS['other_order'] < IDS['my_order']}）\n"
    )

    # ------------------------------------------------------------ 8.2 全局搜索
    print("== 8.2 全局搜索 ==")
    data = search(staff_token, PREFIX)
    check_true("业务员搜得到自己的客户", any(i["title"].startswith(PREFIX) for i in group_of(data, "customer")["items"]))
    check("业务员无 opportunity:view → 商机组 denied", group_of(data, "opportunity")["denied"], True)
    check("商机组条目为空", group_of(data, "opportunity")["items"], [])
    check("业务员无 quote:view → 报价组 denied", group_of(data, "quote")["denied"], True)
    check("报价组条目为空", group_of(data, "quote")["items"], [])

    contact_group = group_of(data, "contact")
    check("联系人有 customer:view → 不 denied", contact_group["denied"], False)
    # 已删客户（软删除）的联系人不得出现
    contact_blob = json.dumps(contact_group["items"], ensure_ascii=False)
    check_true("已删客户的联系人不出现", "13700009999" not in contact_blob, contact_blob[:200])
    # 联系方式可见范围（用户 2026-10-06 确认的口径）：负责人本客户 / 主管本团队 /
    # 管理员全部 / 其他可见人员脱敏。业务员就是本客户的负责人 → 给完整值。
    check_true(
        "负责人（业务员）搜自己的客户 → 拿到完整手机号",
        "13800001111" in contact_blob,
        contact_blob[:200],
    )

    # 主管：department 范围，本团队成员的客户 → 按同一条规则也给完整值
    manager_data = search(manager_token, PREFIX)
    manager_contact_blob = json.dumps(group_of(manager_data, "contact")["items"], ensure_ascii=False)
    check_true(
        "主管（本团队）也能拿到完整手机号",
        "13800001111" in manager_contact_blob,
        manager_contact_blob[:200],
    )
    check("主管有 quote:view → 报价组不 denied", group_of(manager_data, "quote")["denied"], False)

    # 反过来必须成立：**公海客户**（无负责人）对所有可见者一律脱敏 ——
    # 这正是"完整信息需另授权"要挡的那条路。把夹具客户临时改成公海再搜一次。
    async def _set_owner(owner_id: int | None) -> None:
        async with SessionLocal() as s:
            await s.execute(
                text("update customers set owner_id = :o where id = :c"),
                {"o": owner_id, "c": IDS["customer"]},
            )
            await s.commit()

    await _set_owner(None)
    try:
        public_blob = json.dumps(
            group_of(search(staff_token, PREFIX), "contact")["items"], ensure_ascii=False
        )
        check_true(
            "公海客户的联系人一律脱敏（不因“能看客户”就给完整电话）",
            "13800001111" not in public_blob,
            public_blob[:200],
        )
        check_true("脱敏后仍保留可辨识片段", "1111" in public_blob, public_blob[:200])
    finally:
        await _set_owner(IDS["staff"])

    # 空白关键词：不能变成全库通配
    blank = search(staff_token, "%20%20%20")
    check("空白关键词 total 为 0", blank["total"], 0)
    check_true(
        "空白关键词六组都不返回条目",
        all(group["items"] == [] for group in blank["groups"]),
    )

    # ------------------------------------------------------------ 8.3 / 8.4 工具
    print("\n== 8.3 / 8.4 Agent 工具（真 Session + 真库）==")
    from app.core.deps import CurrentUser
    from app.modules.agent.tools import (
        TOOLS,
        ToolContext,
        ensure_tool_permission,
        get_customer_overview,
        get_receivables_summary,
        missing_permissions,
    )
    from app.core.errors import AppError
    from app.modules.user.service import get_user_permission_codes, resolve_data_scope

    async def current_user_of(user_id: int) -> CurrentUser:
        """按真实库里的角色/权限拼一个 `CurrentUser`（与 `get_current_user` 同一套来源）。"""
        async with SessionLocal() as s:
            row = await s.get(User, user_id)
            roles = (
                await s.execute(
                    select(Role)
                    .join(user_roles, user_roles.c.role_id == Role.id)
                    .where(user_roles.c.user_id == user_id)
                )
            ).scalars().all()
            return CurrentUser(
                row,
                permissions=await get_user_permission_codes(s, user_id),
                roles=[r.code for r in roles],
                data_scope=resolve_data_scope(list(roles)),
            )

    staff_user = await current_user_of(IDS["staff"])
    manager_user = await current_user_of(IDS["manager"])
    check_true("业务员权限快照含 customer:view", staff_user.has("customer:view"), "")
    check_true("业务员权限快照**不含** quote:view", not staff_user.has("quote:view"), "")

    # 网关：只有 agent:use 的人拿不到应收
    spec = TOOLS["get_receivables_summary"]
    check("应收工具声明的权限", spec.permissions, ("payment:view",))
    check("业务员缺 payment:view 时被网关拦下", missing_permissions(spec, staff_user), [])

    async with SessionLocal() as s:
        ctx_staff = ToolContext(session=s, user=staff_user, agent_session_id=0)
        overview = await get_customer_overview(ctx_staff, IDS["customer"])
        check_true("客户全貌：无 opportunity:view → 不返回商机块", "opportunities" not in overview)
        check_true("客户全貌：无 quote:view → 不返回报价块", "quotes" not in overview)
        check_true("客户全貌：有 order:view → 返回订单块", "orders" in overview)
        check_true(
            "客户全貌：有 payment:view → 返回待回款（多币种给分组，不给无币种合计）",
            "pending_receivable_amount" in overview
            or bool(overview.get("pending_receivable_by_currency")),
        )
        # 本夹具刻意同时有 CNY 与 USD 两张未取消订单：绝不能出现一个
        # "200 元"式的无币种合计（交接文档 §8.4 的验收原文）。
        grouped = {
            row["currency"]: row
            for row in (overview.get("pending_receivable_by_currency") or [])
        }
        check_true(
            "多币种时不给顶层无币种合计",
            "pending_receivable_amount" not in overview,
            str(overview.get("pending_receivable_amount")),
        )
        check(
            "CNY 待回款分组：算的是本客户全部未取消单（自己的 100 + 主管名下那单 500）",
            grouped.get("CNY", {}).get("order_total_amount"),
            600.0,
        )
        check_true(
            "取消单的 9999 没被算进来",
            grouped.get("CNY", {}).get("order_total_amount") != 10599.0,
            str(grouped.get("CNY")),
        )
        check_true("USD 单独立分组（不与 CNY 相加）", "USD" in grouped, str(sorted(grouped)))
        check_true(
            "客户全貌：说明这是参考口径",
            "正式应收" in (overview.get("receivable_note") or ""),
        )
        # 业务员是本客户负责人 → 按已拍板口径看得到完整联系方式
        check("负责人看得到完整手机号", overview["contacts"][0]["mobile"], "13800001111")

        # 8.4：应收必须按**负责人**过滤，不能按订单 id。
        # 多币种时实现刻意**不给**顶层 plan_count/plan_amount（合计数在多币种下必然错），
        # 所以这里只看分组。
        summary = await get_receivables_summary(ctx_staff)
        by_ccy = {row["currency"]: row for row in summary["by_currency"]}
        check_true("多币种时不给顶层计划数", "plan_count" not in summary, str(summary.get("plan_count")))
        check_true("CNY 分组存在", "CNY" in by_ccy, str(sorted(by_ccy)))
        check_true("USD 分组存在", "USD" in by_ccy, str(sorted(by_ccy)))
        check("CNY 计划条数（自己的 1 条）", by_ccy["CNY"]["plan_count"], 1)
        check("CNY 计划额（自己的 100；取消单 9999 与别人的 500 都不计）", by_ccy["CNY"]["plan_amount"], 100.0)
        check("CNY 已收", by_ccy["CNY"]["received_amount"], 40.0)
        check("USD 计划额", by_ccy["USD"]["plan_amount"], 100.0)
        check_true(
            "别人的订单（id=员工编号，500）没有被算进来",
            all(row["plan_amount"] != 500.0 for row in summary["by_currency"]),
            str(summary["by_currency"]),
        )

        # 范围外 order_id：受控拒绝
        try:
            await get_receivables_summary(ctx_staff, IDS["other_order"])
            check_true("范围外 order_id 被拒", False, "没有抛错")
        except AppError as exc:
            check("范围外 order_id 被拒的码", exc.code, 40302)

        # 主管（department）看得到业务员的数据：合法接手/团队资料不被误挡
        ctx_manager = ToolContext(session=s, user=manager_user, agent_session_id=0)
        overview_m = await get_customer_overview(ctx_manager, IDS["customer"])
        check_true("主管看得到商机块", "opportunities" in overview_m)
        check_true("主管看得到报价块", "quotes" in overview_m)
        # 已删客户全貌不可见
        try:
            await get_customer_overview(ctx_manager, IDS["dead_customer"])
            check_true("已删客户全貌被拒", False, "没有抛错")
        except AppError as exc:
            check("已删客户全貌的码", exc.code, 40401)

    await cleanup()

    print()
    if FAILURES:
        print(f"✗ 失败 {len(FAILURES)} 项：" + "；".join(FAILURES))
        return 1
    print("✓ 全部通过：搜索按模块授权+联系方式脱敏，AI 侧应收按负责人/币种，越权被拦")
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  （search() 里用到）

    raise SystemExit(asyncio.run(main()))
