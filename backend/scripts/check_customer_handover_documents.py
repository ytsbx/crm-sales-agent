"""交接之后历史单据给不给新人看：把这条口径钉成断言。

**只在隔离库跑**：必须显式给 `DATABASE_URL`（库名以 `crm_iso` / `crm_check` 开头）
和 `API_BASE`（默认的 8000 是开发后端）。本套件会真的建客户与单据、真的改归属。

## 这条口径（2026-10-07 业务拍板）

    客户的负责人从 A 换成 B 时，**原本挂在 A 名下、属于这个客户的单据一起改成 B**。

改之前不是这样：只有**离职交接**会把单据搬过去，日常的「转移负责人／主管分配／
批量转移／撞单裁定／公海指派」**只改客户和没办完的待办**，单据原地不动 ——
于是"客户给了新人，新人打开这个客户，订单/报价标签是空的"（子资源接口按
**单据自己的负责人**过滤数据范围），而 AI 的「客户全貌」按"客户可见即资料可见"
又把它们讲了出来，两条路口径打架。

## 为什么自己造三个账号

要验的是"**新负责人是个只管自己的业务员**时，他能不能看到搬过来的历史单据"。
seed 里的张三/李四不合用：李四是销售主管（本部门都看得见，验不出单据有没有搬），
张三在交接后连客户都看不到了（那是客户级可见性）。所以三个账号都用**业务员**角色
（数据范围 self），造完自己清掉。

## 这个套件钉住 15 件事

1. 转移后，原负责人名下的**订单**跟着到新负责人名下
2. **商机、报价、打样**同样跟着走（打样的两个责任字段都要看）
3. 没办完的**待办**跟着走；**已完成的不动**（处理人与完成时间要留在档案里）
4. **别的在职同事负责的单子不动**（第六批审查第 4 条那条口径）
5. 订单的**业绩归属**（`sales_owner_id`）不变 —— "交接后保留历史业绩归属"
6. 单据的**历史创建人**（`created_by`）不变
7. 归属历史里多了一条变更记录
8. **新负责人**用接口只看得到搬过来的那一张（同事那张仍看不到）
9. **旧负责人**用订单明细接口已经看不到它（403）
10. **放进公海不搬**：单据留在最后经手人名下
11. **有人从公海接走时，按归属历史把单据一并接过来**
12. 接过来时业绩归属仍然不变
13. 两条交接路共用的类别名单不漂移（`DOCUMENT_KINDS` 对得上离职交接的名单）

跑法：

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_customer_handover_documents.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

import app.main  # noqa: F401  保证所有模型都注册进 metadata

from sqlalchemy import func, select, text

from app.core.database import SessionLocal, engine
from app.core.security import hash_password
from app.modules.customer.documents import DOCUMENT_KINDS
from app.modules.user.model import User, user_roles
from app.modules.wecom.model import TRANSFER_KIND_LABEL

FAILURES: list[str] = []
PREFIX = "CHKHANDOVER"
STAMP = str(int(time.time()))
BASE = os.environ.get("API_BASE", "")
PASSWORD = "CHKhandover123"


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def require_isolated_db() -> str:
    """显式要求一次性隔离库：本套件会真的建客户、建账号、改归属。"""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit(
            "必须显式设置 DATABASE_URL（一次性隔离库，库名以 crm_iso / crm_check 开头）"
        )
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.startswith(("crm_iso", "crm_check")):
        raise SystemExit(f"拒绝执行：DATABASE_URL 指向 {name!r}，不是一次性隔离库")
    if not BASE:
        raise SystemExit(
            "必须显式设置 API_BASE（默认的 8000 是开发后端）。"
            "例：API_BASE=http://127.0.0.1:8001/api/v1"
        )
    return name


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


def items_of(res: dict) -> list[dict]:
    """列表接口的 items；出错时 data 可能是 None，别让解析把套件崩掉。"""
    return (res.get("data") or {}).get("items") or []


async def cleanup() -> None:
    """自底向上清干净。

    本套件用**直接写库**造夹具（跟着 `check_wecom_handover` 的做法：字段可控、
    不依赖界面流程），所以依赖链很短；但**顺序不能乱**：业务行都引用 users，
    所以用户必须最后删；报价那三张表也要先删，别让外键把清理打断
    （清理一中断，夹具就留在库里，最后被守门套件抓出来）。
    """
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        quote_ids = f"(select id from quotes where customer_id in {cust})"
        version_ids = f"(select id from quote_versions where quote_id in {quote_ids})"
        for sql in (
            f"delete from quote_items where quote_version_id in {version_ids}",
            f"delete from quote_charges where quote_version_id in {version_ids}",
            f"delete from quote_versions where quote_id in {quote_ids}",
            f"delete from quotes where customer_id in {cust}",
            f"delete from sample_requests where customer_id in {cust}",
            f"delete from sales_orders where customer_id in {cust}",
            f"delete from opportunities where customer_id in {cust}",
            f"delete from tasks where customer_id in {cust}",
            f"delete from order_drafts where customer_id in {cust}",
            f"delete from customer_owner_history where customer_id in {cust}",
            "delete from customers where name like :p",
            "delete from user_roles where user_id in "
            "(select id from users where username like :u)",
            "delete from users where username like :u",
        ):
            await s.execute(text(sql), {"p": f"{PREFIX}%", "u": f"{PREFIX.lower()}_%"})
        await s.commit()
    # 显式收池：async 引擎的连接池绑在创建它的那个事件循环上，
    # 留着不关容易在别的脚本里报 "attached to a different loop"。
    await engine.dispose()


async def read_state(ids: dict) -> dict:
    """把要断言的行重新读一遍（避免拿着会话里的旧对象）。"""
    from app.modules.customer.model import Customer, CustomerOwnerHistory
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest
    from app.modules.task.model import Task

    async with SessionLocal() as s:
        order_mine = await s.get(SalesOrder, ids["order_mine"])
        order_colleague = await s.get(SalesOrder, ids["order_colleague"])
        sample = await s.get(SampleRequest, ids["sample"])
        return {
            "customer_owner": (await s.get(Customer, ids["customer"])).owner_id,
            "opportunity_owner": (await s.get(Opportunity, ids["opportunity"])).owner_id,
            "quote_owner": (await s.get(Quote, ids["quote"])).owner_id,
            "order_mine_owner": order_mine.owner_id,
            "order_mine_sales_owner": order_mine.sales_owner_id,
            "order_mine_created_by": order_mine.created_by,
            "order_colleague_owner": order_colleague.owner_id,
            "sample_owner": sample.owner_id,
            "sample_production_owner": sample.production_owner_id,
            "open_task_owner": (await s.get(Task, ids["open_task"])).owner_id,
            "done_task_owner": (await s.get(Task, ids["done_task"])).owner_id,
            "history_count": (
                await s.execute(
                    select(func.count()).select_from(CustomerOwnerHistory).where(
                        CustomerOwnerHistory.customer_id == ids["customer"]
                    )
                )
            ).scalar_one(),
        }


async def seed_fixtures() -> dict:
    """一个客户 + 各类单据 + 三个**业务员**账号（原负责人／新负责人／在职同事）。"""
    from app.modules.customer.model import Customer
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest
    from app.modules.task.model import Task

    ids: dict = {}
    async with SessionLocal() as s:
        admin = (
            await s.execute(select(User).where(User.username == "admin"))
        ).scalars().first()
        if admin is None:
            raise SystemExit("库里没有 admin 账号，先跑 scripts/seed.py")
        ids["admin"] = admin.id
        dept_id = (
            await s.execute(text("select id from departments order by id limit 1"))
        ).scalar_one_or_none()
        role_id = (
            await s.execute(text("select id from roles where code = 'salesperson'"))
        ).scalar_one()
        stage_id = (
            await s.execute(text("select id from opportunity_stages order by id limit 1"))
        ).scalar_one()

        def make_user(tag: str, label: str) -> User:
            return User(
                username=f"{PREFIX.lower()}_{tag}_{STAMP}", name=f"{PREFIX}{label}-{STAMP}",
                password_hash=hash_password(PASSWORD), status="active",
                department_id=dept_id,
            )

        from_owner = make_user("from", "原负责人")
        to_owner = make_user("to", "新负责人")
        colleague = make_user("col", "在职同事")
        s.add_all([from_owner, to_owner, colleague])
        await s.flush()
        for user in (from_owner, to_owner, colleague):
            await s.execute(user_roles.insert().values(user_id=user.id, role_id=role_id))
        ids["from"], ids["to"], ids["colleague"] = (
            from_owner.id, to_owner.id, colleague.id,
        )
        ids["from_username"], ids["to_username"] = from_owner.username, to_owner.username

        customer = Customer(
            name=f"{PREFIX}交接客户-{STAMP}", owner_id=from_owner.id,
            status="active", pool_status="private", level="A",
        )
        s.add(customer)
        await s.flush()
        ids["customer"] = customer.id

        # 订单的"业绩归属"与"历史创建人"刻意写成**第三个人**：
        # 这样"搬负责人时不许动这两个字段"才验得出来。
        order_mine = SalesOrder(
            order_no=f"{PREFIX}O1{STAMP}", customer_id=customer.id, total_amount=1000,
            currency="CNY", status="pending", owner_id=from_owner.id,
            sales_owner_id=colleague.id, created_by=colleague.id,
        )
        # 在职同事负责的订单：客户交接时**不该**被搬走
        order_colleague = SalesOrder(
            order_no=f"{PREFIX}O2{STAMP}", customer_id=customer.id, total_amount=2000,
            currency="CNY", status="pending", owner_id=colleague.id,
            sales_owner_id=colleague.id, created_by=colleague.id,
        )
        s.add_all([
            Opportunity(
                customer_id=customer.id, title=f"{PREFIX}商机-{STAMP}", stage_id=stage_id,
                currency="CNY", status="active", owner_id=from_owner.id,
            ),
            Quote(
                quote_no=f"{PREFIX}Q{STAMP}", customer_id=customer.id,
                owner_id=from_owner.id, status="draft", created_by=from_owner.id,
            ),
            SampleRequest(
                customer_id=customer.id, owner_id=from_owner.id,
                production_owner_id=from_owner.id, status="approved",
                requested_at=datetime.now(UTC),
            ),
            Task(
                customer_id=customer.id, title=f"{PREFIX}没办完的待办-{STAMP}",
                owner_id=from_owner.id, priority="normal", status="pending", source="manual",
            ),
            Task(
                customer_id=customer.id, title=f"{PREFIX}已完成的待办-{STAMP}",
                owner_id=from_owner.id, priority="normal", status="done", source="manual",
            ),
            order_mine,
            order_colleague,
        ])
        await s.flush()
        ids["order_mine"], ids["order_colleague"] = order_mine.id, order_colleague.id
        ids["opportunity"] = (
            await s.execute(
                select(Opportunity.id).where(Opportunity.customer_id == customer.id)
            )
        ).scalar_one()
        ids["quote"] = (
            await s.execute(select(Quote.id).where(Quote.customer_id == customer.id))
        ).scalar_one()
        ids["sample"] = (
            await s.execute(
                select(SampleRequest.id).where(SampleRequest.customer_id == customer.id)
            )
        ).scalar_one()
        task_rows = (
            await s.execute(
                select(Task.id, Task.status).where(Task.customer_id == customer.id)
            )
        ).all()
        ids["open_task"] = next(r[0] for r in task_rows if r[1] == "pending")
        ids["done_task"] = next(r[0] for r in task_rows if r[1] == "done")
        # 必须提交：接口那边是**另一个会话**，只 flush 的话会话一关就全回滚，
        # 接口那边只会回 404（这个坑第一版就踩了）。
        await s.commit()
    return ids


async def main() -> None:
    db_name = require_isolated_db()
    print(f"隔离库：{db_name}")
    await cleanup()

    admin_token = login("admin", "admin123")
    ids = await seed_fixtures()
    cid = ids["customer"]
    print(f"夹具：客户 #{cid}；订单 #{ids['order_mine']}（原负责人）/ "
          f"#{ids['order_colleague']}（在职同事负责）；"
          f"账号 {ids['from_username']} / {ids['to_username']}")

    # ── 1) 转移：原负责人 → 新负责人（两个都是"只管自己"的业务员）────────
    status, res = call(
        "POST", f"/customers/{cid}/transfer", admin_token,
        {"owner_id": ids["to"], "reason": "CHK 交接搬单据断言"},
    )
    check_true("转移接口成功", status == 200 and res.get("code") == 0,
               f"HTTP {status} {res.get('message')}")
    st = await read_state(ids)

    check("客户负责人已变", st["customer_owner"], ids["to"])
    check("原负责人名下的订单跟着到新负责人", st["order_mine_owner"], ids["to"])
    check("商机跟着走", st["opportunity_owner"], ids["to"])
    check("报价跟着走", st["quote_owner"], ids["to"])
    check("打样（跟单责任）跟着走", st["sample_owner"], ids["to"])
    check("打样（生产责任）跟着走", st["sample_production_owner"], ids["to"])
    check("没办完的待办跟着走", st["open_task_owner"], ids["to"])
    check("已完成的待办不动（历史记录要留档）", st["done_task_owner"], ids["from"])
    check("在职同事负责的订单不动", st["order_colleague_owner"], ids["colleague"])
    check("业绩归属不变", st["order_mine_sales_owner"], ids["colleague"])
    check("历史创建人不变", st["order_mine_created_by"], ids["colleague"])
    check_true("归属历史记了一条", st["history_count"] >= 1, f"{st['history_count']} 条")

    # ── 2) 新负责人用接口看：只看到搬过来的那一张 ────────────────────────
    to_token = login(ids["to_username"], PASSWORD)
    status, res = call("GET", f"/customers/{cid}/orders", to_token)
    check("新负责人只看到搬过来的那一张（同事那张仍看不到）",
          sorted(row["id"] for row in items_of(res)), [ids["order_mine"]])
    check_true("（上面这条接口确实通了）", status == 200, f"HTTP {status}")

    # ── 3) 旧负责人用订单明细接口看：已经不是他的了 ──────────────────────
    from_token = login(ids["from_username"], PASSWORD)
    status, _ = call("GET", f"/orders/{ids['order_mine']}", from_token)
    check("旧负责人看不到已经交出去的订单（订单明细按自己的负责人判范围）", status, 403)

    # ── 4) 放进公海：不搬 ───────────────────────────────────────────────
    status, res = call(
        "POST", f"/customers/{cid}/release-to-pool", admin_token,
        {"reason": "CHK 交接搬单据断言：放入公海"},
    )
    check_true("放入公海接口成功", status == 200 and res.get("code") == 0,
               f"HTTP {status} {res.get('message')}")
    st = await read_state(ids)
    check("放进公海后客户无负责人", st["customer_owner"], None)
    check("放进公海**不搬**：订单仍挂在最后经手人名下", st["order_mine_owner"], ids["to"])

    # ── 5) 从公海接走：按归属历史一并接过来 ─────────────────────────────
    status, res = call("POST", f"/public-pool/customers/{cid}/claim", from_token)
    check_true("公海领取接口成功", status == 200 and res.get("code") == 0,
               f"HTTP {status} {res.get('message')}")
    st = await read_state(ids)
    check("领取后客户归领取人", st["customer_owner"], ids["from"])
    check("领取时把历史单据一并接过来（取归属历史里的上一位负责人）",
          st["order_mine_owner"], ids["from"])
    check("接过来时业绩归属仍然不变", st["order_mine_sales_owner"], ids["colleague"])
    check("在职同事那一张还是不动", st["order_colleague_owner"], ids["colleague"])

    # ── 6) 两条交接路共用的类别名单不漂移 ───────────────────────────────
    missing = [kind for kind in DOCUMENT_KINDS if kind not in TRANSFER_KIND_LABEL]
    check("DOCUMENT_KINDS 里的类别都在离职交接的名单里", missing, [])

    await cleanup()

    print()
    if FAILURES:
        print(f"❌ 交接搬单据回归失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"   - {item}")
        raise SystemExit(1)
    print("✅ 交接搬单据回归通过")


if __name__ == "__main__":
    asyncio.run(main())
