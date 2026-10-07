"""第九批复验返修（8 个缺口）：把审查给的反例变成断言。

**只在隔离库跑**：必须显式给 `DATABASE_URL`（库名以 `crm_iso` / `crm_check` 开头）
与 `API_BASE`（默认的 8000 是开发后端）。

## 这个套件钉住什么

**① 9.1 AI 摘要的待办要按负责人收范围**（P1）
`/agent/customer-summary`、`/agent/followup-suggestion`、`/agent/opportunity-analysis`
原来只在 SQL 里判 `task:view`、**没按负责人过滤** —— 只管自己数据的销售，
能通过 AI 读到**别人负责**的待办标题，而同一个客户的概览页却什么也不给
（同一份系统里两个答案）。

**② 9.3 终态任务的普通编辑**（P2）
`PATCH /tasks/{id}` 的终态判断只覆盖状态与负责人，`due_at` 从来没参与 ——
`/postpone` 会拦、普通编辑却 200 而且真的改了（**清空也算**）。另外显式传
`{"status": null}` 会撞 NOT NULL 变 500，标题、优先级同样。

**③ 9.8 整单没发完不给最终偏差**（P2）
订购 10、已发 6、剩 4 件又**没排新批次**时：`all_shipped=false`、`remaining=4`、
`pending_batch_count=0`，但仍然给出了最终偏差。`pending_batch_count=0`
**不能**代表"整单发完"。

**④ 9.10 业务时区**（P1）
首次成交归月、报价过期等按日判断原来用 UTC —— 北京时间元旦凌晨的首单会被
归到上一年；凌晨那 8 小时里"今天到期"会被算成"昨天到期"。

**⑤ 9.9 / 9.11 是纯前端**：由 `tsc` + 完整 UI 冒烟覆盖；本套件只保证接口把
前端要用的字段都给全了（整单偏差、批次偏差、待发批次数）。

跑法：

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_ninth_round_gaps.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import app.main  # noqa: F401  保证所有模型都注册进 metadata
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.core.timebase import BUSINESS_TZ, today_business
from app.modules.analytics import target_bases
from app.modules.quote import service as quote_service
from app.modules.user.model import Role, User

FAILURES: list[str] = []
PREFIX = "CHK9GAP"
STAMP = str(int(time.time()))
BASE = os.environ.get("API_BASE", "")
PASSWORD = "CHK9gap123"
#: 被拦的动作：STATUS_NOT_ALLOWED(40002) 在本项目一律是 **400**，参数错误是 422
REJECTED = (400, 422)
STARTED_AT = datetime.now(UTC)


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_in(label: str, actual, expected: tuple) -> None:
    good = actual in expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望属于 {expected}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def require_isolated_db() -> None:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("必须显式设置 DATABASE_URL（一次性隔离库）")
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.startswith(("crm_iso", "crm_check")):
        raise SystemExit(f"拒绝执行：DATABASE_URL 指向 {name!r}，不是一次性隔离库")
    if not BASE:
        raise SystemExit("必须显式设置 API_BASE（默认的 8000 是开发后端）")


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


async def grant_role(session, role_id: int, codes: list[str]) -> None:
    for code in codes:
        pid = (
            await session.execute(
                text("select id from permissions where code = :c"), {"c": code}
            )
        ).scalar_one_or_none()
        if pid is None:
            raise SystemExit(f"权限码不存在：{code}")
        await session.execute(
            text(
                "insert into role_permissions (role_id, permission_id) "
                "values (:r, :p) on conflict do nothing"
            ),
            {"r": role_id, "p": pid},
        )


async def make_self_scope_user(ids: dict, tag: str, codes: list[str]) -> tuple[int, str]:
    """造一个 data_scope=self 的业务员，返回 (id, 用户名)。

    **必须用 self 范围**：范围是 all / 部门的话，"看不看得到别人的待办"验不出来
    （seed 里的李四是主管，本部门都看得见）—— 这是上面几批踩过的坑。
    """
    async with SessionLocal() as s:
        role = Role(code=f"{PREFIX}{tag}{STAMP}", name=f"{PREFIX}{tag}", data_scope="self")
        s.add(role)
        await s.flush()
        await grant_role(s, role.id, codes)
        ids["roles"].append(role.id)
        user = User(
            name=f"{PREFIX}{tag}-{STAMP}",
            username=f"{PREFIX.lower()}_{tag}_{STAMP}",
            password_hash=hash_password(PASSWORD),
            status="active",
        )
        s.add(user)
        await s.flush()
        # `UserRole` 是 Table 别名（不是 ORM 类），走 SQL 插
        await s.execute(
            text("insert into user_roles (user_id, role_id) values (:u, :r)"),
            {"u": user.id, "r": role.id},
        )
        ids["users"].append(user.id)
        await s.commit()
        return user.id, user.username


async def cleanup(ids: dict) -> None:
    async with SessionLocal() as s:
        tasks = ids.get("tasks") or []
        if tasks:
            # `followups` 没有 business_type / business_id 这两列（踩过），
            # 任务下的跟进挂在 customer_id 上，随客户一起清（见下面那段）
            await s.execute(text("delete from tasks where id = any(:t)"), {"t": tasks})
        orders = ids.get("orders") or []
        if orders:
            await s.execute(
                text(
                    "delete from order_shipment_batch_items where batch_id in "
                    "(select id from order_shipment_batches where order_id = any(:o))"
                ),
                {"o": orders},
            )
            for sql in (
                "delete from order_schedule_changes where order_id = any(:o)",
                "delete from order_milestones where order_id = any(:o)",
                "delete from order_shipment_batches where order_id = any(:o)",
                "delete from order_status_history where order_id = any(:o)",
                "delete from sales_order_items where order_id = any(:o)",
                "delete from sales_orders where id = any(:o)",
            ):
                await s.execute(text(sql), {"o": orders})
        quotes = ids.get("quotes") or []
        if quotes:
            await s.execute(
                text("delete from quote_items where quote_version_id in "
                     "(select id from quote_versions where quote_id = any(:q))"),
                {"q": quotes},
            )
            await s.execute(
                text("delete from quote_versions where quote_id = any(:q)"), {"q": quotes}
            )
            await s.execute(text("delete from quotes where id = any(:q)"), {"q": quotes})
        customers = ids.get("customers") or []
        if customers:
            await s.execute(
                text("delete from followups where customer_id = any(:c)"), {"c": customers}
            )
            await s.execute(
                text("delete from customer_owner_history where customer_id = any(:c)"),
                {"c": customers},
            )
            await s.execute(text("delete from customers where id = any(:c)"), {"c": customers})
        users = ids.get("users") or []
        if users:
            await s.execute(text("delete from user_roles where user_id = any(:u)"), {"u": users})
            await s.execute(text("delete from users where id = any(:u)"), {"u": users})
        roles = ids.get("roles") or []
        if roles:
            await s.execute(
                text("delete from role_permissions where role_id = any(:r)"), {"r": roles}
            )
            await s.execute(text("delete from roles where id = any(:r)"), {"r": roles})
        # 过程留痕/通知：按本次启动时间窗清，只删本次运行产生的
        for sql in (
            "delete from notifications where created_at > :ts",
            "delete from followups where followup_type='系统' and created_at > :ts",
        ):
            await s.execute(text(sql), {"ts": STARTED_AT})
        await s.commit()


async def main() -> None:
    require_isolated_db()
    admin = login("admin", "admin123")
    async with SessionLocal() as s:
        admin_row = await s.get(User, 1)
        admin_id = admin_row.id
    ids: dict = {
        "users": [], "roles": [], "customers": [], "tasks": [], "orders": [], "quotes": []
    }
    try:
        # ==================================================================
        print()
        print("=== ① 9.1 AI 摘要的待办必须按负责人收范围 ===")
        user_a_id, uname_a = await make_self_scope_user(
            ids, "A", ["customer:view", "task:view", "agent:use"]
        )
        user_b_id, _uname_b = await make_self_scope_user(
            ids, "B", ["customer:view", "task:view", "agent:use"]
        )
        user_n_id, uname_n = await make_self_scope_user(
            ids, "N", ["customer:view", "agent:use"]  # 没有 task:view
        )
        token_a = login(uname_a, PASSWORD)
        token_n = login(uname_n, PASSWORD)

        _, res = call(
            "POST", "/customers", admin,
            body={"name": f"{PREFIX}客户-{STAMP}", "level": "A", "owner_id": user_a_id},
        )
        customer_id = res["data"]["id"]
        ids["customers"].append(customer_id)

        # 一条**属于 B** 的待办，挂在 A 负责的客户上
        _, res = call(
            "POST", "/tasks", admin,
            body={
                "title": f"{PREFIX}B 的待办-{STAMP}",
                "customer_id": customer_id,
                "owner_id": user_b_id,
                "priority": "normal",
            },
        )
        if res.get("code") != 0:
            raise SystemExit(f"建待办失败：{res.get('message')}")
        other_task_id = res["data"]["id"]
        ids["tasks"].append(other_task_id)

        _, res = call("POST", "/agent/customer-summary", token_a, body={"customer_id": customer_id})
        payload = res.get("data") or {}
        check("客户摘要里看不到别人负责的待办", payload.get("open_tasks") or [], [])
        _, res = call("GET", f"/customers/{customer_id}/tasks", token_a)
        check("客户待办接口同样看不到（与概览口径一致）", (res.get("data") or {}).get("items") or [], [])
        _, res = call(
            "POST", "/agent/followup-suggestion", token_a, body={"customer_id": customer_id}
        )
        check("跟进建议里也没有别人负责的待办", (res.get("data") or {}).get("open_task_count", 0), 0)

        # 把客户改到"没有 task:view"的那个人名下：这时是"看不到"（None），
        # 不是"一条都没有"（[]）—— 两者不能混
        async with SessionLocal() as s:
            await s.execute(
                text("update customers set owner_id = :u where id = :c"),
                {"u": user_n_id, "c": customer_id},
            )
            await s.commit()
        _, res = call("POST", "/agent/customer-summary", token_n, body={"customer_id": customer_id})
        payload = res.get("data") or {}
        check(
            "没有 task:view 时 open_tasks 是 None（看不到 ≠ 一条都没有）",
            payload.get("open_tasks", "（字段缺失）"),
            None,
        )

        # ==================================================================
        print()
        print("=== ② 9.3 终态任务的普通编辑：不许改期；显式传空要 422 ===")
        _, res = call(
            "POST", "/tasks", admin,
            body={"title": f"{PREFIX}终态-{STAMP}", "customer_id": customer_id,
                  "owner_id": admin_id, "priority": "normal",
                  "due_at": "2026-12-01T10:00:00"},
        )
        task_id = res["data"]["id"]
        ids["tasks"].append(task_id)
        _, res = call(
            "POST", f"/tasks/{task_id}/complete", admin,
            body={"completion_note": f"{PREFIX} 套件置为完成"},
        )
        check("先把任务置为已完成", res.get("code"), 0)
        _, before = call("GET", f"/tasks/{task_id}", admin)
        due_before = before["data"].get("due_at")
        owner_before = before["data"].get("owner_id")
        done_before = before["data"].get("completed_at")

        status, res = call(
            "POST", f"/tasks/{task_id}/postpone", admin, body={"due_at": "2026-12-20T10:00:00"}
        )
        check_in("延期接口拦住已完成任务（原有行为）", status, REJECTED)

        status, res = call(
            "PATCH", f"/tasks/{task_id}", admin, body={"due_at": "2026-12-25T10:00:00"}
        )
        check_in("普通编辑改期同样被拦（原来 200 且真改了）", status, REJECTED)

        status, res = call("PATCH", f"/tasks/{task_id}", admin, body={"due_at": None})
        check_in("清空截止时间也被拦（原来也能过）", status, REJECTED)

        status, res = call("PATCH", f"/tasks/{task_id}", admin, body={"status": None})
        check_in("显式传空状态 → 参数错误（原来 500）", status, REJECTED)

        status, res = call("PATCH", f"/tasks/{task_id}", admin, body={"title": None})
        check_in("显式传空标题 → 参数错误（原来 500）", status, REJECTED)

        status, res = call("PATCH", f"/tasks/{task_id}", admin, body={"priority": None})
        check_in("显式传空优先级 → 参数错误（原来 500）", status, REJECTED)

        status, res = call("PATCH", f"/tasks/{task_id}", admin, body={"status": "not_a_status"})
        check_in("未知状态仍是 422", status, REJECTED)

        _, after = call("GET", f"/tasks/{task_id}", admin)
        check("被拒之后截止时间没被改动", after["data"].get("due_at"), due_before)
        check("被拒之后负责人没被改动", after["data"].get("owner_id"), owner_before)
        check("被拒之后完成时间没被改动", after["data"].get("completed_at"), done_before)

        # 正常路径不受影响：在办任务的普通编辑照常
        _, res = call(
            "POST", "/tasks", admin,
            body={"title": f"{PREFIX}在办-{STAMP}", "customer_id": customer_id,
                  "owner_id": admin_id, "priority": "normal"},
        )
        open_task = res["data"]["id"]
        ids["tasks"].append(open_task)
        status, res = call(
            "PATCH", f"/tasks/{open_task}", admin, body={"title": f"{PREFIX}在办改-{STAMP}"}
        )
        check("在办任务的普通编辑照常可用", res.get("code"), 0)

        # ==================================================================
        print()
        print("=== ③ 9.8 整单没发完不给最终偏差 ===")
        sku = call("GET", "/pricing/sku-options", admin)[1]["data"][0]["id"]
        _, res = call(
            "POST", "/orders", admin,
            body={
                "customer_id": customer_id,
                "delivery_date": "2026-10-20",
                "items": [{"sku_id": sku, "quantity": 10, "unit_price": 10}],
            },
        )
        order_id = res["data"]["order_id"]
        ids["orders"].append(order_id)
        item_id = call("GET", f"/orders/{order_id}/items", admin)[1]["data"][0]["id"]
        # 到货类交期 + 运输 7 天 → 建议发货日 = 10-20 − 7 = 10-13
        # （`POST /orders` 不收这两个字段，照既有套件的做法直接改库）
        async with SessionLocal() as s:
            await s.execute(
                text(
                    "update sales_orders set delivery_kind = 'arrival', transit_days = 7 "
                    "where id = :i"
                ),
                {"i": order_id},
            )
            await s.commit()

        _, res = call(
            "POST", f"/orders/{order_id}/shipments", admin,
            body={"planned_date": "2026-10-13",
                  "items": [{"order_item_id": item_id, "planned_qty": 10}]},
        )
        batch1 = res["data"]["batch_id"]
        call(
            "POST", f"/orders/{order_id}/shipments/{batch1}/ship", admin,
            body={"actual_ship_date": "2026-10-17",
                  "items": [{"order_item_id": item_id, "shipped_qty": 6}]},
        )
        _, res = call("GET", f"/orders/{order_id}/shipments", admin)
        summary = res["data"]["summary"]
        check("还剩 4 件未发", summary["remaining"], 4)
        check("整单未发完", summary["all_shipped"], False)
        check("没排新批次 → 待发批次数是 0", summary["pending_batch_count"], 0)
        check(
            "**不能**因为待发批次是 0 就给最终偏差（保持空值）",
            summary["last_batch_vs_delivery_days"],
            None,
        )
        check("建议发货日照常给出", summary["suggested_ship_date"], "2026-10-13")
        check_true(
            "批次自己的偏差仍保留（批次结果 ≠ 整单结论）",
            res["data"]["batches"][0].get("deviation_days") is not None,
            str(res["data"]["batches"][0].get("deviation_days")),
        )

        # 补完剩下的 4 件 → 整单发完，**这时才**给最终结论
        _, res = call(
            "POST", f"/orders/{order_id}/shipments", admin,
            body={"planned_date": "2026-10-13",
                  "items": [{"order_item_id": item_id, "planned_qty": 4}]},
        )
        batch2 = res["data"]["batch_id"]
        call(
            "POST", f"/orders/{order_id}/shipments/{batch2}/ship", admin,
            body={"actual_ship_date": "2026-10-16",
                  "items": [{"order_item_id": item_id, "shipped_qty": 4}]},
        )
        _, res = call("GET", f"/orders/{order_id}/shipments", admin)
        summary = res["data"]["summary"]
        check("整单已发完", summary["all_shipped"], True)
        # 第 1 批 10-17、第 2 批（编号更大）10-16 —— 要按**实际最晚发货日**取 10-17，
        # 而不是"编号最大的那批"。减建议发货日 10-13 → +4
        check(
            "发完之后才给最终偏差（按实际最晚发货日 10-17）：+4",
            summary["last_batch_vs_delivery_days"],
            4,
        )

        # ==================================================================
        print()
        print("=== ④ 9.10 业务时区：归月与「今天」 ===")
        _, res = call(
            "POST", "/customers", admin,
            body={"name": f"{PREFIX}跨年客户-{STAMP}", "level": "A"},
        )
        cross_customer = res["data"]["id"]
        ids["customers"].append(cross_customer)
        _, res = call(
            "POST", "/orders", admin,
            body={"customer_id": cross_customer,
                  "items": [{"sku_id": sku, "quantity": 1, "unit_price": 100}]},
        )
        cross_order = res["data"]["order_id"]
        ids["orders"].append(cross_order)
        # 北京时间 2026-01-01 01:00 == UTC 2025-12-31 17:00
        cross_utc = datetime(2026, 1, 1, 1, 0, tzinfo=BUSINESS_TZ).astimezone(UTC)
        async with SessionLocal() as s:
            await s.execute(
                text("update sales_orders set created_at = :t where id = :i"),
                {"t": cross_utc, "i": cross_order},
            )
            await s.commit()
            _veterans, first_deal_month, _detail = await target_bases._build_basis(s, 2026)
        check(
            "北京时间元旦凌晨的首单归到 2026-01（原来写成 2025-12）",
            first_deal_month.get(cross_customer),
            "2026-01",
        )

        today = today_business()
        check("有效期到昨天 → 已过期", quote_service.quote_is_expired(today - timedelta(days=1)), True)
        check("有效期到今天 → 未过期（含当天）", quote_service.quote_is_expired(today), False)
        check("有效期到明天 → 未过期", quote_service.quote_is_expired(today + timedelta(days=1)), False)

        # ==================================================================
        print()
        print("=== ⑤ 接口把前端要用的字段给全（9.9/9.11 由 tsc + UI 冒烟覆盖） ===")
        _, res = call("GET", f"/orders/{order_id}/shipments", admin)
        summary = res["data"]["summary"]
        for field in (
            "last_batch_vs_delivery_days",
            "last_batch_vs_delivery_basis",
            "suggested_ship_date",
            "pending_batch_count",
            "all_shipped",
            "remaining",
        ):
            check_true(f"发货概览带 {field}", field in summary)
        batch = res["data"]["batches"][0]
        for field in ("deviation_days", "late"):
            check_true(f"批次行带 {field}", field in batch)
    finally:
        await cleanup(ids)

    print()
    if FAILURES:
        print(f"❌ 第九批复验返修回归失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"   - {item}")
        raise SystemExit(1)
    print("✅ 第九批复验返修回归全部通过")


if __name__ == "__main__":
    asyncio.run(main())
