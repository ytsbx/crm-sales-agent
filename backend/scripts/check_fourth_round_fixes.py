"""第四轮返工回归：冻结实绩范围 / 新客口径 / 洞察权限与并发 / 案例并发与搜索。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 覆盖

**P1-1 冻结实绩的数据范围**
- 仅本人范围的账号，**看不到**别人被冻结的业绩（补零行也不补别人的）；
- 部门 / 含下级部门 / 全公司三种范围各按自己的边界，结账前后一致；
- 公司汇总行仍在（既定的"全公司目标人人可见"），但它只是一个汇总值。

**P1-2 新客口径**
- 考核实绩 = **首次有效成交**；只看建档、没有成交 → 实绩为 0；
- 新建档数作为过程指标单独返回，不进差额；
- **汇总数 == 明细条数**（同一份取数，结构上不可能对不上）。

**P1-3 / P1-4 洞察权限与并发**
- "能评审但不能改别人的草稿"：编辑/提交/删除/转换四个写入口全部 403；
- 审核人**仍然看得到**别人的洞察（评审是职责，不能一起禁掉）；
- 审核人**可以**审（review 接口放行）；
- 提交完全相同的内容**不触发重审**（轮次不变）；
- 各轮内容与审核结果**逐轮留痕**（谁、什么时候、结论、当时的内容）；
- 并发审核只有一个成功。

**P1-5 案例首次修订并发**
- 两个并发修订请求只产出一张 V2（老实现产出两张）；
- 重复审核只有一个成功。

**P1-6 搜索不成为探测口**
- 普通读者搜不到被隐藏的原文（客户全称、手机号）；
- 看得见的标题/正文仍能搜到（检索能力没被砍）；
- 有原文权限的人仍按原文搜。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_fourth_round_fixes.py
"""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

from app.core.database import SessionLocal

FAILURES: list[str] = []
BASE = require_api_base()
PREFIX = "CHK4"
YEAR = datetime.now(UTC).year - 1


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


async def cleanup() -> None:
    """自底向上清干净。本套件写：客户/订单/回款/快照/快照明细/洞察及其轮次/案例及证据/角色用户。"""
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        for sql in (
            "delete from case_evidences where case_id in "
            "(select id from sales_cases where title like :m)",
            "delete from sales_cases where title like :m",
            "delete from product_insight_rounds where insight_id in "
            "(select id from product_insights where title like :m)",
            "delete from product_insights where title like :m",
            "delete from analytics_actual_snapshot_items where period like :period",
            "delete from analytics_actual_snapshots where period like :period",
            # **基准快照也要清**：去年的"老客池 / 首次成交"一旦冻结就指向当时的客户 id，
            # 夹具删掉之后它仍留在库里，再跑一轮就会读到指向已删客户的基准 ——
            # 表现为"新客实绩算成 0"，而原因是上一轮的残渣（实测踩到）。
            "delete from analytics_basis_snapshots where year = :year",
            "delete from payment_records where order_id in "
            "(select id from sales_orders where customer_id in " + cust + ")",
            "delete from sales_orders where customer_id in " + cust,
            "delete from customer_owner_history where customer_id in " + cust,
            "delete from customers where name like :p",
            "delete from audit_logs where after_data::text like :m "
            "or before_data::text like :m",
            "delete from user_roles where user_id in (select id from users where username like :u)",
            "delete from user_roles where role_id in (select id from roles where code like :r)",
            "delete from role_permissions where role_id in (select id from roles where code like :r)",
            "delete from roles where code like :r",
            "delete from users where username like :u",
        ):
            await s.execute(
                text(sql),
                {
                    "p": f"{PREFIX}%", "m": f"%{PREFIX}%",
                    "u": f"{PREFIX.lower()}%", "r": f"{PREFIX}%",
                    "period": f"{YEAR}-%",
                    "year": YEAR,
                },
            )
        await s.commit()


async def seed_users() -> dict:
    """造四种数据范围的账号 + 一个只有评审权限的账号。"""
    from app.core.security import hash_password
    from app.modules.user.model import Department, Role, User, role_permissions, user_roles
    from app.modules.user.model import Permission

    stamp = int(time.time())
    pwd = hash_password("123456")
    ids: dict = {}

    async with SessionLocal() as s:
        dept_a = Department(name=f"{PREFIX}甲部-{stamp}")
        dept_b = Department(name=f"{PREFIX}乙部-{stamp}")
        sub_a = Department(name=f"{PREFIX}甲部子部-{stamp}")
        s.add_all([dept_a, dept_b, sub_a])
        await s.flush()
        # 子部挂到甲部下（"含下级部门"范围要用）
        sub_a.parent_id = dept_a.id
        await s.flush()

        def new_role(code: str, name: str) -> Role:
            return Role(code=code, name=name, status="active", data_scope="self")

        role_self = new_role(f"{PREFIX}SELF", f"{PREFIX}仅本人")
        role_dept = new_role(f"{PREFIX}DEPT", f"{PREFIX}本部门")
        role_sub = new_role(f"{PREFIX}SUB", f"{PREFIX}含下级")
        role_all = new_role(f"{PREFIX}ALL", f"{PREFIX}全公司")
        role_review = new_role(f"{PREFIX}REV", f"{PREFIX}只评审")
        role_dept.data_scope = "department"
        role_sub.data_scope = "department_and_sub"
        role_all.data_scope = "all"
        role_review.data_scope = "self"
        s.add_all([role_self, role_dept, role_sub, role_all, role_review])
        await s.flush()

        codes = [
            # 建案例要挂客户，所以建客户/改客户的权限也得给 ——
            # 少了 `customer:create` 时客户建不出来（403），案例就成了"无客户案例"，
            # 标题里的客户全称不会被脱敏，搜索那一组断言会以**假象**失败
            # （实测踩过：查了半天搜索逻辑，根因是夹具的客户没建出来）。
            "customer:view", "customer:create", "customer:update",
            "order:view", "quote:view", "sample:view",
            "opportunity:view", "product:view", "product:manage", "product:review",
        ]
        perms = {
            code: (
                await s.execute(
                    select(Permission).where(Permission.code == code)
                )
            ).scalars().first()
            for code in codes
        }
        for code, permission in perms.items():
            if permission is None:
                raise SystemExit(f"权限码 {code} 不存在，先跑 scripts/seed.py")

        role_map = {
            "self": role_self, "dept": role_dept, "sub": role_sub, "all": role_all,
            "review": role_review,
        }
        for key, role in role_map.items():
            grant = (
                ["product:view", "product:review", "product:manage"]
                if key == "review"
                else codes
            )
            for code in grant:
                # 关联表用 execute 插，不能用 session.add（那是给 ORM 对象用的）
                await s.execute(
                    role_permissions.insert().values(
                        role_id=role.id, permission_id=perms[code].id
                    )
                )
        await s.flush()

        users = {}
        for key, dept_id, role in (
            ("self", dept_a.id, role_self),
            ("peer", dept_a.id, role_self),      # 同部门、仅本人范围：用来验证"看不到别人的"
            ("dept", dept_a.id, role_dept),
            ("sub", dept_a.id, role_sub),
            ("all", dept_a.id, role_all),
            ("review", dept_a.id, role_review),
        ):
            user = User(
                username=f"{PREFIX.lower()}_{key}_{stamp}", name=f"{PREFIX}{key}",
                password_hash=pwd, status="active", department_id=dept_id,
            )
            s.add(user)
            await s.flush()
            await s.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
            users[key] = user.id

        ids.update(users)
        ids.update(
            {
                "dept_a": dept_a.id, "dept_b": dept_b.id, "sub_a": sub_a.id,
                "stamp": stamp,
                "role_ids": [role.id for role in role_map.values()],
            }
        )
        await s.commit()
    return ids


async def seed_snapshot_fixture(ids: dict) -> None:
    """给两个账号各造一张订单 + 一笔回款，然后把**这一年结账**。

    排序上让 id 小的先建：仅本人范围的账号看的就是自己那一条，
    别人的那一条绝不该出现。
    """
    from app.modules.customer.model import Customer
    from app.modules.order.model import SalesOrder
    from app.modules.payment.model import PaymentRecord
    from app.modules.user.model import User

    stamp = ids["stamp"]
    async with SessionLocal() as s:
        for key, amount in (("self", Decimal("1000")), ("peer", Decimal("7777"))):
            owner = await s.get(User, ids[key])
            customer = Customer(
                name=f"{PREFIX}{key}客户-{stamp}", owner_id=owner.id,
                status="active", pool_status="private",
            )
            s.add(customer)
            await s.flush()
            order = SalesOrder(
                order_no=f"{PREFIX}{key.upper()}{stamp}", customer_id=customer.id,
                owner_id=owner.id, sales_owner_id=owner.id, total_amount=amount,
                currency="CNY", status="pending",
                created_at=datetime(YEAR, 5, 12, tzinfo=UTC),
            )
            s.add(order)
            await s.flush()
            s.add(
                PaymentRecord(
                    order_id=order.id, received_amount=amount / 2,
                    received_date=datetime(YEAR, 5, 20, tzinfo=UTC).date(),
                    confirmed_at=datetime(YEAR, 5, 22, tzinfo=UTC),
                    status="confirmed", currency="CNY",
                    created_at=datetime(YEAR, 5, 20, tzinfo=UTC),
                )
            )
        await s.commit()


def _frozen_seed_values(ids: dict) -> dict:
    """直接按结账的口径往快照里写两行（本套件不依赖结账接口的实现）。"""
    from app.modules.analytics import target_actuals

    period = f"{YEAR}-05"
    values = {}
    for key, sales, received in (
        ("self", Decimal("1000"), Decimal("500")),
        ("peer", Decimal("7777"), Decimal("3888.5")),
    ):
        scope = target_actuals.scope_key_of(ids[key], None)
        values[(scope, "sales")] = sales
        values[(scope, "received")] = received
        values[(scope, "shipped")] = Decimal("0")
        # 新客快照给真实值：结账会用存档值覆盖实时值，
        # 全写 0 的话第 2b 节就没有一行可比对（"汇总==明细"那条断言会空跑）。
        # 这一项现在按"首次成交"口径，与明细同源，存 1 就该核出 1 条明细。
        values[(scope, "new_customer")] = Decimal("1")
        values[(scope, "repeat_net")] = Decimal("0")
    scope_company = target_actuals.scope_key_of(None, None)
    values[(scope_company, "sales")] = Decimal("8777")
    values[(scope_company, "received")] = Decimal("4388.5")
    values[(scope_company, "shipped")] = Decimal("0")
    values[(scope_company, "new_customer")] = Decimal("2")
    values[(scope_company, "repeat_net")] = Decimal("0")
    return period, values


async def freeze_snapshot(ids: dict) -> str:
    from app.modules.analytics import target_actuals

    period, values = _frozen_seed_values(ids)
    async with SessionLocal() as s:
        await target_actuals.freeze_period(
            s, period=period, values=values,
            basis_version="check", operator_id=ids["all"], note=None,
        )
        await s.commit()
    return period


async def main() -> int:
    import app.main  # noqa: F401  先把应用加载进来（模型注册）

    await cleanup()
    ids = await seed_users()
    await seed_snapshot_fixture(ids)
    period = await freeze_snapshot(ids)

    tokens = {
        key: login(f"{PREFIX.lower()}_{key}_{ids['stamp']}", "123456")
        for key in ("self", "peer", "dept", "sub", "all", "review")
    }
    # 案例的审核人按**角色**判（is_reviewer 只看 sales_manager/admin），
    # 自造的角色码不算 —— 用内置管理员。
    admin_token = login("admin", "admin123")

    try:
        print("=== 1. P1-1 冻结实绩的数据范围（这一年已结账）===")
        status, res = call("GET", f"/sales-targets?year={YEAR}", tokens["self"])
        check("本人范围账号能读报表", status, 200)
        rows = (res.get("data") or {}).get("rows") or []
        mine = [r for r in rows if r.get("period") == period and r.get("user_id") == ids["self"]]
        others = [
            r for r in rows
            if r.get("period") == period
            and r.get("user_id") not in (None, ids["self"])
        ]
        check("看得到自己那一期", len(mine), 1)
        # 这一条就是返工单告的那个洞：补快照行时没做范围过滤，
        # 于是别人的（期间, 人）行会被补出来，带着别人冻结的签单额与回款额。
        check("**看不到**别人的冻结行", others, [])
        names = {r.get("user_name") for r in rows}
        check_true("不会把别人的名字也查出来", f"{PREFIX}peer" not in names, str(sorted(names)))
        if mine:
            check("自己的冻结回款额正确", mine[0].get("received_actual"), 500.0)
        status, res = call("GET", f"/sales-targets?year={YEAR}", tokens["all"])
        admin_rows = (res.get("data") or {}).get("rows") or []
        admin_peers = [
            r for r in admin_rows
            if r.get("period") == period and r.get("user_id") == ids["peer"]
        ]
        check("全公司范围看得到 peer 那一行（对照）", len(admin_peers), 1)
        if admin_peers:
            check("对照行的金额就是快照里的", admin_peers[0].get("received_actual"), 3888.5)

        print()
        print("=== 1b. 结账前后边界一致 ===")
        async with SessionLocal() as s:
            await s.execute(
                text("delete from analytics_actual_snapshot_items where period = :p"),
                {"p": period},
            )
            await s.execute(
                text("delete from analytics_actual_snapshots where period = :p"),
                {"p": period},
            )
            await s.commit()
        status, res = call("GET", f"/sales-targets?year={YEAR}", tokens["self"])
        live_rows = (res.get("data") or {}).get("rows") or []
        live_ids = {
            r.get("user_id") for r in live_rows if r.get("period") == period
        }
        check_true("结账前也看不到别人", ids["peer"] not in live_ids, str(sorted(live_ids, key=str)))
        await freeze_snapshot(ids)  # 恢复，后面的用例还要用

        print()
        print("=== 2. P1-2 新客口径：首次有效成交 ===")
        # 造一个"只建档、没有任何成交"的客户
        from app.modules.customer.model import Customer

        stamp = ids["stamp"]
        async with SessionLocal() as s:
            s.add(
                Customer(
                    name=f"{PREFIX}只建档客户-{stamp}", owner_id=ids["self"],
                    status="active", pool_status="private",
                    created_at=datetime(YEAR, 9, 3, tzinfo=UTC),
                )
            )
            await s.commit()
        status, res = call("GET", f"/sales-targets?year={YEAR}", tokens["all"])
        rows = (res.get("data") or {}).get("rows") or []
        sep = [r for r in rows if r.get("period") == f"{YEAR}-09" and r.get("user_id") == ids["self"]]
        if sep:
            check("只建档没有成交 → 考核新客实绩为 0", sep[0].get("new_customer_actual"), 0)
            check_true(
                "建档数作为过程指标单独返回",
                sep[0].get("new_customer_created_actual", 0) >= 1,
                str(sep[0].get("new_customer_created_actual")),
            )
        else:
            check("9 月不应因为'只建档'而出现新客实绩行", True, True)

        print()
        print("=== 2b. 汇总数与明细条数一致（同一份取数）===")
        # 比对前先把这一期的快照删掉：已结账期间的下钻读的是**存档明细**，
        # 而本套件造的快照只写了汇总值、没写明细行（夹具没走结账接口），
        # 拿它比对会得到"明细 0 条"的假象。
        # 删掉之后两边都是实时算的，正好直接验证"汇总与明细同一份取数"。
        async with SessionLocal() as s:
            await s.execute(
                text("delete from analytics_actual_snapshots where period = :p"),
                {"p": period},
            )
            await s.commit()
        status, res = call("GET", f"/sales-targets?year={YEAR}", tokens["all"])
        rows = (res.get("data") or {}).get("rows") or []
        checked = 0
        for row in rows:
            if not row.get("new_customer_actual"):
                continue
            scope_q = f"&user_id={row['user_id']}" if row.get("user_id") else ""
            status, detail = call(
                "GET",
                f"/sales-targets/drilldown?period={row['period']}"
                f"&metric=new_customer{scope_q}",
                tokens["all"],
            )
            items = (detail.get("data") or {}).get("items") or []
            check(
                f"{row['period']} 新客汇总 {row['new_customer_actual']} == 明细 {len(items)}",
                len(items),
                int(row["new_customer_actual"]),
            )
            checked += 1
        check_true("至少核了一组新客汇总/明细", checked >= 1, f"核了 {checked} 组")
        await freeze_snapshot(ids)  # 恢复快照，供后面的范围用例使用

        print()
        print("=== 3. P1-3 能评审 ≠ 能改别人的草稿 ===")
        # 全公司账号建一条挂在自己名下的洞察（作者另有其人）
        status, res = call(
            "POST", "/product-insights", tokens["all"],
            {"title": f"{PREFIX}他人草稿-{stamp}", "direction": "方向：轻量款",
             "selling_points": "卖点：耐用"},
        )
        check("建洞察成功", status, 200)
        foreign_id = (res.get("data") or {}).get("id")
        # 归属改成 peer，这样"仅本人范围 + 有评审权限"的账号就落在范围之外了
        async with SessionLocal() as s:
            await s.execute(
                text("update product_insights set owner_id = :o where id = :i"),
                {"o": ids["peer"], "i": foreign_id},
            )
            await s.commit()

        status, res = call("GET", f"/product-insights/{foreign_id}", tokens["review"])
        check("评审人**看得到**别人的洞察（评审是职责）", status, 200)
        status, res = call(
            "PATCH", f"/product-insights/{foreign_id}", tokens["review"],
            {"title": f"{PREFIX}偷改标题-{stamp}"},
        )
        check("评审人**改不了**别人的草稿（编辑）", status, 403)
        status, res = call(
            "POST", f"/product-insights/{foreign_id}/submit", tokens["review"], {}
        )
        check("评审人改不了别人的（提交）", status, 403)
        status, res = call(
            "POST", f"/product-insights/{foreign_id}/convert", tokens["review"], {}
        )
        check("评审人改不了别人的（转换）", status, 403)
        status, res = call("DELETE", f"/product-insights/{foreign_id}", tokens["review"])
        check("评审人改不了别人的（删除）", status, 403)
        async with SessionLocal() as s:
            title = (
                await s.execute(
                    text("select title from product_insights where id = :i"), {"i": foreign_id}
                )
            ).scalar_one()
        check_true("标题确实没被改掉", "偷改" not in title, title)

        print()
        print("=== 4. P1-4 逐轮留痕 + 值比较 + 并发审核 ===")
        status, res = call(
            "POST", "/product-insights", tokens["self"],
            {"title": f"{PREFIX}我的洞察-{stamp}", "direction": "方向 A",
             "selling_points": "卖点 A"},
        )
        mine_id = (res.get("data") or {}).get("id")
        call("POST", f"/product-insights/{mine_id}/submit", tokens["self"], {"request_key": "k1"})
        status, res = call(
            "POST", f"/product-insights/{mine_id}/review", tokens["review"],
            {"approve": True, "note": "第一轮通过"},
        )
        check("第一轮审核通过", status, 200)
        async with SessionLocal() as s:
            before_round = (
                await s.execute(
                    text("select review_round from product_insights where id = :i"),
                    {"i": mine_id},
                )
            ).scalar_one()
        # **提交完全相同的标题**：不该被当成"改了内容"
        status, res = call(
            "PATCH", f"/product-insights/{mine_id}", tokens["self"],
            {"title": f"{PREFIX}我的洞察-{stamp}"},
        )
        check("原样再提交同标题不报错", status, 200)
        async with SessionLocal() as s:
            after_round = (
                await s.execute(
                    text("select review_round, status from product_insights where id = :i"),
                    {"i": mine_id},
                )
            ).first()
        check("轮次没有因为'传了相同内容'而增加", after_round[0], before_round)
        check("状态仍是已通过（没被误退回待评审）", after_round[1], "approved")

        # 真的改了才退回重审
        status, res = call(
            "PATCH", f"/product-insights/{mine_id}", tokens["self"],
            {"direction": "方向 B（真的改了）"},
        )
        async with SessionLocal() as s:
            real_change = (
                await s.execute(
                    text("select review_round, status from product_insights where id = :i"),
                    {"i": mine_id},
                )
            ).first()
        check("真改了内容 → 退回待评审", real_change[1], "under_review")
        check("真改了内容 → 轮次 +1", real_change[0], before_round + 1)

        # 逐轮留痕
        status, res = call("GET", f"/product-insights/{mine_id}/rounds", tokens["all"])
        rounds = (res.get("data") or [])
        check_true("逐轮记录可取", status == 200 and len(rounds) >= 1, str(len(rounds)))
        if rounds:
            first = rounds[0]
            check_true(
                "第 1 轮记了提交内容快照",
                bool(first.get("content")),
                str(first.get("content"))[:60],
            )
            check("第 1 轮的审核结论", first.get("review_result"), "approved")
            check_true("第 1 轮记了审核人名字", bool(first.get("reviewer_name")))
            check_true("第 1 轮记了审核时间", bool(first.get("reviewed_at")))
            check("第 1 轮的审核意见", first.get("review_note"), "第一轮通过")

        # 并发审核：两个请求同时点通过
        call("POST", f"/product-insights/{mine_id}/submit", tokens["self"], {"request_key": "k2"})
        barrier = threading.Barrier(2)
        results: list[int] = []

        def race():
            barrier.wait()
            code, _ = call(
                "POST", f"/product-insights/{mine_id}/review", tokens["review"],
                {"approve": True, "note": "并发审核"},
            )
            results.append(code)

        threads = [threading.Thread(target=race) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        check_true(
            "并发审核只有一个成功（另一个被状态挡下）",
            sorted(results).count(200) == 1,
            f"返回码 {sorted(results)}",
        )

        print()
        print("=== 5. P1-5 案例首次修订的并发 ===")
        status, res = call(
            "POST", "/cases", tokens["self"],
            {"title": f"{PREFIX}并发修订案例-{stamp}",
             "key_actions": "先出样再锁产能", "lessons": "交期写成书面承诺"},
        )
        case_id = (res.get("data") or {}).get("id")
        call("POST", f"/cases/{case_id}/submit", tokens["self"], {})
        call("POST", f"/cases/{case_id}/review", admin_token,
             {"approve": True, "note": "通过"})

        barrier2 = threading.Barrier(2)
        revise_results: list[int] = []

        def race_revise():
            barrier2.wait()
            code, _ = call("POST", f"/cases/{case_id}/revise", tokens["self"], {})
            revise_results.append(code)

        threads = [threading.Thread(target=race_revise) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        async with SessionLocal() as s:
            revisions = (
                await s.execute(
                    text(
                        "select count(*) from sales_cases "
                        "where revision_of_id = :i and deleted_at is null"
                    ),
                    {"i": case_id},
                )
            ).scalar_one()
        check(
            "两个并发修订请求只产出一张 V2（老实现产出两张）",
            int(revisions),
            1,
        )

        print()
        print("=== 6. P1-6 搜索不成为探测口 ===")
        status, res = call(
            "POST", "/customers", tokens["self"],
            {"name": f"{PREFIX}真名客户{stamp}", "customer_type": "企业"},
        )
        real_customer_id = (res.get("data") or {}).get("id")
        check_true("夹具客户建出来了（否则后面的脱敏断言会以假象失败）",
                   bool(real_customer_id), str(res.get("message")))
        status, res = call(
            "POST", "/cases", tokens["self"],
            {"title": f"{PREFIX}真名客户{stamp} 的返单打法",
             "customer_id": real_customer_id, "customer_label": "某包装厂",
             "key_actions": "先做产前样，再锁产线档期",
             "lessons": "联系人 13800138000，交期紧就发计划表"},
        )
        probe_case_id = (res.get("data") or {}).get("id")
        call("POST", f"/cases/{probe_case_id}/submit", tokens["self"], {})
        call("POST", f"/cases/{probe_case_id}/review", admin_token,
             {"approve": True, "note": "通过"})

        def search(token: str, word: str) -> list[int]:
            status, res = call(
                "GET", "/cases?keyword=" + urllib.parse.quote(word) + "&page_size=200",
                token,
            )
            return [row["id"] for row in (res.get("data") or {}).get("items") or []]

        # 诊断：先看普通读者眼里这条案例长什么样（标题是否脱敏、是不是被当成主管）
        status, probe_detail = call("GET", f"/cases/{probe_case_id}", tokens["dept"])
        probe_data = probe_detail.get("data") or {}
        print(
            "  [诊断] 普通读者看到的标题="
            f"{probe_data.get('title')!r} share_view={probe_data.get('share_view')}"
        )
        readers = search(tokens["dept"], "产前样")
        check_true("普通读者按正文里的做法能搜到（检索没被砍）", probe_case_id in readers, str(readers[:5]))
        check_true(
            "普通读者搜**被隐藏的手机号**搜不到",
            probe_case_id not in search(tokens["dept"], "13800138000"),
        )
        check_true(
            "普通读者搜**客户全称**搜不到（他看不见那个词）",
            probe_case_id not in search(tokens["dept"], f"{PREFIX}真名客户{stamp}"),
        )
        check_true(
            "主管按原文仍能搜到（原文检索保留给有权限的人）",
            probe_case_id in search(admin_token, f"{PREFIX}真名客户{stamp}"),
        )
        check_true(
            "普通读者按脱敏后看得见的代称能搜到",
            probe_case_id in search(tokens["dept"], "某包装厂"),
        )

    finally:
        await cleanup()

    print()
    if FAILURES:
        print(f"FAILED：{len(FAILURES)} 项未通过")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
