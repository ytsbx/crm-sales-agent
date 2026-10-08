"""第九批返修回归（9.1~9.9）：概览权限 / 待办校验 / 发货批次 / 交期 / 币种。

**只在隔离库跑**：API_BASE 必须指向本机，DATABASE_URL 库名必须含 test。

## 这个套件钉住什么

**9.1 客户概览不再绕过模块权限**
`GET /customers/{id}/overview` 此前只查 `customer:view`，随后把该客户名下的
订单金额、报价单号、跟进内容一次性交出去 —— 有客户查看权、没有订单权的人
（例如财务）能从这一个入口看到订单板块。现在逐板块判权限：没权限的板块
**不查、不返回**（counts 给 None、列表为空），板块名进 `restricted`。
断言：受限账号拿到 `restricted`、`counts.orders is None`，且直接调
`/customers/{id}/orders` 被 403；管理员拿到的不是受限结果。

**9.2 待办创建的关联校验**
此前只判"存在"，于是 ①能给别人的客户建待办；②一张待办能同时挂"客户 B"
与"客户 A 的订单"。断言：跨客户组合被拒（422）；只传单据时**落库的客户
由单据确定**。

**9.3 待办状态与改派约束**
`status` 改成枚举（未知值 422）；终态任务不能被普通编辑改派/改状态；
编辑改成 done 要留下完成时间。

**9.5 少发之后剩余量能继续排批次**
计划 10、实发 6 之后，`unplanned` 应当是 4（释放未实发部分），能再排一批。

**9.6 批次号不复用**
取号看**全部批次（含已取消）**：取消编号最大的一批后，新批次号继续递增。
另用两个并发请求验证不会超排（应用层锁 + 库层唯一约束兜底）。

**9.7 明细校验**
实发明细必须属于本批次（错误明细号被拒），计划/实发明细都不能重复
（重复要 422，不是 500）。

**9.8 交期偏差按"建议发货日"**
到货类交期（运输 7 天）时，实际发货日应和"到货日 − 7 天"比。
客户要 10-20 到货 → 建议 10-13 发货；实际 10-17 发 → 应为 +4（原来是 -3）。

**9.9 金额带币种、不跨币种合并**
客户概览按币种分组返回订单金额（不再把 CNY 与 USD 直接相加）。

跑法（后端已在 8001 跑隔离库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_ninth_round_fixes.py
"""

import asyncio

# 外币夹具：造美元订单前要先把业务口径放开，跑完收回（见 scripts/_fx_scope.py）
from _fx_scope import open_export, restore_domestic
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata（缺表会 NoReferencedTableError）
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.user.model import Role, User

FAILURES: list[str] = []
BASE = require_api_base()
PREFIX = "CHK9TH"
STAMP = str(int(time.time()))
DENIED = (403, 404)
REJECTED = (400, 422)


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def check_in(label: str, actual, expected: tuple) -> None:
    good = actual in expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望属于 {expected}）')
    if not good:
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


async def setup() -> dict:
    """建夹具：一个"只有客户查看权"的账号 + 两个客户 + 两条订单 + 一条待办。"""
    ids: dict = {}
    async with SessionLocal() as s:
        role = Role(
            code=f"{PREFIX}VIEW{STAMP}",
            name=f"{PREFIX}只看客户",
            data_scope="all",  # 能看所有客户，但**没有任何模块权限**
        )
        s.add(role)
        await s.flush()
        await grant_role(s, role.id, ["customer:view"])
        ids["role"] = role.id

        viewer = User(
            name=f"{PREFIX}只读-{STAMP}",
            username=f"{PREFIX.lower()}_viewer_{STAMP}",
            password_hash=hash_password("123456"),
            status="active",
        )
        s.add(viewer)
        await s.flush()
        # `UserRole` 是 Table 别名（不是 ORM 类），走 SQL 插
        await s.execute(
            text("insert into user_roles (user_id, role_id) values (:u, :r)"),
            {"u": viewer.id, "r": role.id},
        )
        ids["viewer"] = viewer.id
        ids["viewer_username"] = viewer.username
        await s.commit()

    admin_token = login("admin", "admin123")
    sku = call("GET", "/pricing/sku-options", admin_token)[1]["data"][0]["id"]
    ids["sku"] = sku

    for tag in ("A", "B"):
        _, res = call(
            "POST", "/customers", admin_token,
            body={"name": f"{PREFIX}客户{tag}-{STAMP}", "level": "A"},
        )
        if res.get("code") != 0:
            raise SystemExit(f"建客户失败：{res.get('message')}")
        ids[f"customer_{tag}"] = res["data"]["id"]

    # 客户 A 名下一条订单（10 件 × 10 元）
    _, res = call(
        "POST", "/orders", admin_token,
        body={
            "customer_id": ids["customer_A"],
            "delivery_date": "2026-10-20",
            "items": [{"sku_id": sku, "quantity": 10, "unit_price": 10}],
        },
    )
    if res.get("code") != 0:
        raise SystemExit(f"建订单失败：{res.get('message')}")
    ids["order"] = res["data"]["order_id"]
    detail = call("GET", f"/orders/{ids['order']}/items", admin_token)[1]["data"]
    ids["order_item"] = detail[0]["id"]

    # 再一条订单：并发排批次用（同样 10 件）
    _, res = call(
        "POST", "/orders", admin_token,
        body={
            "customer_id": ids["customer_A"],
            "items": [{"sku_id": sku, "quantity": 10, "unit_price": 10}],
        },
    )
    ids["order_race"] = res["data"]["order_id"]
    race_detail = call("GET", f"/orders/{ids['order_race']}/items", admin_token)[1]["data"]
    ids["order_race_item"] = race_detail[0]["id"]

    ids["admin_token"] = admin_token
    return ids


async def cleanup(ids: dict) -> None:
    """自底向上清干净（订单的从属表必须先删，否则外键会挡住）。"""
    async with SessionLocal() as s:
        params = {
            "p": f"{PREFIX}%",
            "u": f"{PREFIX.lower()}_%",
            "r": f"{PREFIX}%",
        }
        orders = (
            "(select id from sales_orders where customer_id in "
            "(select id from customers where name like :p))"
        )
        customers = "(select id from customers where name like :p)"
        for sql in (
            # 订单的从属表（顺序按外键依赖）
            f"delete from order_schedule_changes where order_id in {orders}",
            f"delete from order_milestones where order_id in {orders}",
            "delete from order_shipment_batch_items where batch_id in "
            f"(select id from order_shipment_batches where order_id in {orders})",
            f"delete from order_shipment_batches where order_id in {orders}",
            f"delete from order_status_history where order_id in {orders}",
            f"delete from followups where order_id in {orders}",
            f"delete from sales_order_items where order_id in {orders}",
            "delete from notifications where business_type = 'order' "
            f"and business_id in {orders}",
            f"delete from tasks where customer_id in {customers}",
            f"delete from followups where customer_id in {customers}",
            f"delete from sales_orders where customer_id in {customers}",
            f"delete from customers where name like :p",
            # 账号与角色
            "delete from user_roles where user_id in "
            "(select id from users where username like :u)",
            "delete from users where username like :u",
            "delete from role_permissions where role_id in "
            "(select id from roles where code like :r)",
            "delete from roles where code like :r",
        ):
            await s.execute(text(sql), params)
        await s.commit()


async def main() -> None:
    host = urlparse(BASE).hostname
    assert host in ("127.0.0.1", "localhost"), f"只能在本地跑，当前 {BASE}"
    assert "test" in os.environ.get("DATABASE_URL", ""), "只能在隔离库跑"

    ids = await setup()
    try:
        admin = ids["admin_token"]
        customer_a = ids["customer_A"]
        order = ids["order"]
        item_id = ids["order_item"]

        # ------------------------------------------------------------------
        print()
        print("=== 9.1 客户概览逐板块判权限 ===")
        viewer_token = login(ids["viewer_username"], "123456")

        status, res = call("GET", f"/customers/{customer_a}/overview", viewer_token)
        check("受限账号能取概览（客户本身可见）", status, 200)
        data = res["data"]
        restricted = set(data.get("restricted") or [])
        check_true(
            "没权限的板块进入 restricted",
            {"orders", "quotes", "opportunities", "followups", "tasks"} <= restricted,
            str(sorted(restricted)),
        )
        check("无权限时订单计数是 None（不是 0）", data["counts"]["orders"], None)
        check("无权限时订单列表为空", data["orders"], [])
        check_true(
            "订单摘要里没有金额泄露",
            all("total_amount" not in row for row in (data.get("orders") or [])),
        )

        status, res = call("GET", f"/customers/{customer_a}/orders", viewer_token)
        check("直接调客户下的订单列表被拒（403）", status, 403)
        status, res = call("GET", f"/customers/{customer_a}/followups", viewer_token)
        check("直接调客户下的跟进列表被拒（403）", status, 403)

        status, res = call("GET", f"/customers/{customer_a}/overview", admin)
        check("管理员取概览", status, 200)
        check("管理员不受限", res["data"].get("restricted"), [])
        check_true(
            "管理员能看到订单金额（按币种分组）",
            isinstance(res["data"]["counts"].get("order_amounts"), list),
        )

        # ------------------------------------------------------------------
        print()
        print("=== 9.2 待办创建的关联校验 ===")
        # 跨客户组合：客户 B + 客户 A 的订单
        status, res = call(
            "POST", "/tasks", admin,
            body={
                "title": f"{PREFIX}跨客户待办",
                "customer_id": ids["customer_B"],
                "order_id": order,
            },
        )
        check("跨客户组合被拒（422）", status, 422)

        # 只传单据：客户应当由单据确定下来
        status, res = call(
            "POST", "/tasks", admin,
            body={"title": f"{PREFIX}由单据定客户", "order_id": order},
        )
        check("只传单据可以建待办", res.get("code"), 0)
        task_from_order = res["data"]["id"]
        check("落库的客户由单据确定", res["data"]["customer_id"], customer_a)

        # 别人的客户（业务员建 admin 的客户的待办）
        zhangsan = login("zhangsan", "123456")
        status, res = call(
            "POST", "/tasks", zhangsan,
            body={"title": f"{PREFIX}越权待办", "customer_id": customer_a},
        )
        check("范围外客户被拒（403）", status, 403)

        # ------------------------------------------------------------------
        print()
        print("=== 9.3 待办状态与改派约束 ===")
        status, res = call(
            "PATCH", f"/tasks/{task_from_order}", admin, body={"status": "whatever"}
        )
        # 项目把入参校验统一成 code=40001 / HTTP 400（不是 FastAPI 默认的 422）
        check("非法状态被拒（40001 参数校验失败）", res.get("code"), 40001)
        check_true(
            "报错里列出了允许的状态",
            "pending" in json.dumps(res.get("data") or []),
            json.dumps(res.get("data") or [])[:120],
        )

        status, res = call("POST", f"/tasks/{task_from_order}/complete", admin, body={})
        check("任务可以完成", res.get("code"), 0)
        check_true("完成时间已登记", res["data"].get("completed_at") is not None)

        # 改派给**别人**（1 是 admin 自己，传它等于没改）
        status, res = call(
            "PATCH", f"/tasks/{task_from_order}", admin, body={"owner_id": 2}
        )
        check_in("已完成任务不能通过普通编辑改派", res.get("code"), (40002,))

        status, res = call(
            "PATCH", f"/tasks/{task_from_order}", admin, body={"status": "pending"}
        )
        check("已完成任务不能靠普通编辑改回进行中（400）", status, 400)

        # 编辑直接改成 done：要留下完成时间
        status, res = call(
            "POST", "/tasks", admin, body={"title": f"{PREFIX}编辑完成", "customer_id": customer_a}
        )
        edit_task = res["data"]["id"]
        status, res = call("PATCH", f"/tasks/{edit_task}", admin, body={"status": "done"})
        check("编辑改成已完成", res.get("code"), 0)
        check_true(
            "编辑路径也登记了完成时间",
            res["data"].get("completed_at") is not None,
            str(res["data"].get("completed_at")),
        )

        # ------------------------------------------------------------------
        print()
        print("=== 9.5 少发之后剩余量能继续排批次 ===")
        status, res = call(
            "POST", f"/orders/{order}/shipments", admin,
            body={"planned_date": "2026-10-13", "items": [{"order_item_id": item_id, "planned_qty": 10}]},
        )
        check("排第 1 批（计划 10）", res.get("code"), 0)
        batch1 = res["data"]["batch_id"]
        check("第 1 批编号为 1", res["data"]["batch_no"], 1)

        status, res = call(
            "POST", f"/orders/{order}/shipments/{batch1}/ship", admin,
            body={"actual_ship_date": "2026-10-17", "items": [{"order_item_id": item_id, "shipped_qty": 6}]},
        )
        check("实发 6 件", res.get("code"), 0)

        status, res = call("GET", f"/orders/{order}/shipments", admin)
        row = next(r for r in res["data"]["items"] if r["order_item_id"] == item_id)
        check("未发量 4", row["remaining"], 4)
        check("可继续安排的也是 4（少发的部分已释放）", row["unplanned"], 4)
        check("原计划 10 的事实保留", res["data"]["batches"][0]["items"][0]["planned_qty"], 10)
        check("实际发货 6 的事实保留", res["data"]["batches"][0]["items"][0]["shipped_qty"], 6)

        status, res = call(
            "POST", f"/orders/{order}/shipments", admin,
            body={"planned_date": "2026-10-25", "items": [{"order_item_id": item_id, "planned_qty": 4}]},
        )
        check("剩余 4 件能排新批次", res.get("code"), 0)
        batch2 = res["data"]["batch_id"]
        check("新批次编号为 2", res["data"]["batch_no"], 2)

        # ------------------------------------------------------------------
        print()
        print("=== 9.7 明细校验 ===")
        status, res = call(
            "POST", f"/orders/{order}/shipments/{batch2}/ship", admin,
            body={"items": [{"order_item_id": 99999999, "shipped_qty": 1}]},
        )
        check("不属于本批的实发明细被拒（422）", status, 422)

        status, res = call(
            "POST", f"/orders/{order}/shipments", admin,
            body={"items": [{"order_item_id": item_id, "planned_qty": 1},
                            {"order_item_id": item_id, "planned_qty": 1}]},
        )
        check("计划明细重复被拒（40001，不是 500）", res.get("code"), 40001)

        status, res = call("GET", f"/orders/{order}/shipments", admin)
        check(
            "被拒之后没有多出批次",
            len([b for b in res["data"]["batches"] if b["status"] != "cancelled"]),
            2,
        )

        # ------------------------------------------------------------------
        print()
        print("=== 9.6 批次号不复用 ===")
        status, res = call("DELETE", f"/orders/{order}/shipments/{batch2}", admin)
        check("取消第 2 批", res.get("code"), 0)

        status, res = call(
            "POST", f"/orders/{order}/shipments", admin,
            body={"items": [{"order_item_id": item_id, "planned_qty": 2}]},
        )
        check("取消后仍能排批次", res.get("code"), 0)
        check("批次号继续递增（不复用被取消的 2）", res["data"]["batch_no"], 3)

        # 并发：同一订单两个请求各排 8 件（订购 10）→ 只能成功一个
        race_order = ids["order_race"]
        race_item = ids["order_race_item"]
        body = {"items": [{"order_item_id": race_item, "planned_qty": 8}]}
        results = await asyncio.gather(
            *[
                asyncio.to_thread(
                    call, "POST", f"/orders/{race_order}/shipments", token=admin, body=body
                )
                for _ in range(2)
            ]
        )
        codes = sorted(r[0] for r in results)
        check_true(
            "并发排批次只有一个成功",
            codes == [200, 400] or codes == [200, 422],
            str(codes),
        )
        status, res = call("GET", f"/orders/{race_order}/shipments", admin)
        total_planned = sum(
            b_item["planned_qty"]
            for b in res["data"]["batches"]
            for b_item in b["items"]
        )
        check_true("并发之后没有超排", total_planned <= 10, str(total_planned))
        check("并发之后批次数是 1", len([b for b in res["data"]["batches"] if b["status"] != "cancelled"]), 1)

        # ------------------------------------------------------------------
        # 审查方点名的另外两个并发场景（§9.6 验收）：只"同时排 8+8"不够，
        # 还要证明**订单取消不会和排批次互相穿透**、**取消批次不会和实发打架**。
        def make_order(quantity: int = 10) -> tuple[int, int]:
            """建一张测试订单，返回 (订单 id, 明细 id)。"""
            _, created = call(
                "POST", "/orders", admin,
                body={
                    "customer_id": customer_a,
                    "items": [{"sku_id": ids["sku"], "quantity": quantity, "unit_price": 10}],
                },
            )
            new_order = created["data"]["order_id"]
            new_items = call("GET", f"/orders/{new_order}/items", admin)[1]["data"]
            return new_order, new_items[0]["id"]

        print()
        print("=== 9.6 并发：排批次 与 取消订单 ===")
        cancel_order_id, cancel_item = make_order()
        await asyncio.gather(
            asyncio.to_thread(
                call, "POST", f"/orders/{cancel_order_id}/shipments", token=admin,
                body={"items": [{"order_item_id": cancel_item, "planned_qty": 10}]},
            ),
            asyncio.to_thread(call, "POST", f"/orders/{cancel_order_id}/cancel", token=admin),
        )
        final_order = call("GET", f"/orders/{cancel_order_id}", admin)[1]["data"]
        if final_order["status"] == "cancelled":
            # 关键判据：订单取消之后**不能还挂着一张有效待发批次** ——
            # 那意味着排批次穿过了取消动作。取消订单本身会把 planned 批次级联置取消，
            # 所以"排批次先成功"是允许的，不允许的是**取消之后新增**。
            leftover = call("GET", f"/orders/{cancel_order_id}/shipments", admin)[1]["data"]
            planned = [b for b in leftover["batches"] if b["status"] == "planned"]
            check("订单已取消时不留下有效待发批次", planned, [])
            check_true(
                "取消订单后不能继续排批次",
                call(
                    "POST", f"/orders/{cancel_order_id}/shipments", admin,
                    body={"items": [{"order_item_id": cancel_item, "planned_qty": 1}]},
                )[0] in (400, 422),
            )
        else:
            check_true("要么取消成功、要么订单仍在（状态可解释）", final_order["status"] != "cancelled", final_order["status"])

        print()
        print("=== 9.6 并发：取消批次 与 登记实发 ===")
        ship_order_id, ship_item = make_order()
        _, batch_res = call(
            "POST", f"/orders/{ship_order_id}/shipments", admin,
            body={"items": [{"order_item_id": ship_item, "planned_qty": 10}]},
        )
        race_batch = batch_res["data"]["batch_id"]
        await asyncio.gather(
            asyncio.to_thread(
                call, "POST", f"/orders/{ship_order_id}/shipments/{race_batch}/ship", token=admin,
                body={"actual_ship_date": "2026-10-06"},
            ),
            asyncio.to_thread(call, "DELETE", f"/orders/{ship_order_id}/shipments/{race_batch}", token=admin),
        )
        # 直接查库（列表接口会滤掉已取消的批次，看不到终态）
        async with SessionLocal() as s:
            batch_row = (
                await s.execute(
                    text(
                        "select b.status, coalesce(sum(bi.shipped_qty), 0) "
                        "from order_shipment_batches b "
                        "left join order_shipment_batch_items bi on bi.batch_id = b.id "
                        "where b.id = :b group by b.status"
                    ),
                    {"b": race_batch},
                )
            ).one()
        batch_status, batch_shipped = batch_row[0], float(batch_row[1])
        check_true(
            "批次只可能是已发货或已取消（没有第三种残留状态）",
            batch_status in ("shipped", "cancelled"),
            batch_status,
        )
        if batch_status == "cancelled":
            check("取消掉的批次没有留下实发数量", batch_shipped, 0.0)
        else:
            check("已发货的批次实发量就是计划量", batch_shipped, 10.0)

        # 并发跑出来的是哪一个分支要看时序，所以**两个方向各再顺序走一遍** ——
        # 这样"取消后不能实发""实发后不能取消"这两条规则不依赖运气也钉得住。
        seq_order, seq_item = make_order()
        _, seq_res = call(
            "POST", f"/orders/{seq_order}/shipments", admin,
            body={"items": [{"order_item_id": seq_item, "planned_qty": 10}]},
        )
        seq_batch = seq_res["data"]["batch_id"]
        call("DELETE", f"/orders/{seq_order}/shipments/{seq_batch}", admin)
        check_true(
            "已取消的批次不能登记实发",
            call(
                "POST", f"/orders/{seq_order}/shipments/{seq_batch}/ship", admin,
                body={"actual_ship_date": "2026-10-06"},
            )[0] in (400, 422),
        )

        seq_order2, seq_item2 = make_order()
        _, seq_res2 = call(
            "POST", f"/orders/{seq_order2}/shipments", admin,
            body={"items": [{"order_item_id": seq_item2, "planned_qty": 10}]},
        )
        seq_batch2 = seq_res2["data"]["batch_id"]
        call(
            "POST", f"/orders/{seq_order2}/shipments/{seq_batch2}/ship", admin,
            body={"actual_ship_date": "2026-10-06"},
        )
        check_true(
            "已发货的批次不能取消",
            call("DELETE", f"/orders/{seq_order2}/shipments/{seq_batch2}", admin)[0] in (400, 422),
        )

        # ------------------------------------------------------------------
        print()
        print("=== 9.8 交期偏差按建议发货日算 ===")
        async with SessionLocal() as s:
            await s.execute(
                text(
                    "update sales_orders set delivery_kind = 'arrival', transit_days = 7, "
                    "delivery_date = '2026-10-20' where id = :i"
                ),
                {"i": order},
            )
            await s.commit()
        status, res = call("GET", f"/orders/{order}/shipments", admin)
        summary = res["data"]["summary"]
        # §9.8 复审（第九批复验）：这时第 2 批还**没发**（`all_shipped=False`），
        # 所以不该给最终结论 —— 原来这里会算出一个 "+4" 来，读起来像"这单晚了 4 天"，
        # 其实它根本没发完。业务 2026-10-07 拍板：发完才给最终数字。
        check_true(
            "未发完时不给最终偏差（保持空值）",
            summary["last_batch_vs_delivery_days"] is None,
            str(summary["last_batch_vs_delivery_days"]),
        )
        check("整单未发完（逐明细按数量判）", summary["all_shipped"], False)
        check("剩余未发数量照实给出", summary["remaining"], 4)
        check("标明比较的是发货", summary["last_batch_vs_delivery_basis"], "shipping")
        check("建议发货日照常给出（前端拿它解释还剩几天）", summary["suggested_ship_date"], "2026-10-13")
        check("未发完的批次数单独给出", summary["pending_batch_count"], 1)

        # 「发完之后才给结论」**另起一张干净订单**验：上面那张的批次在 9.6 段
        # 被取消/重整过，已经不是"发了一半"的状态（照原样复用 `batch2` 会踩坑）。
        paid_order, paid_item = make_order(quantity=4)
        # 不用单独登记：本套件的 cleanup 是按客户删订单的，新订单会跟着清掉
        async with SessionLocal() as s:
            await s.execute(
                text(
                    "update sales_orders set delivery_kind = 'arrival', transit_days = 7, "
                    "delivery_date = '2026-10-20' where id = :i"
                ),
                {"i": paid_order},
            )
            await s.commit()
        _, res = call(
            "POST", f"/orders/{paid_order}/shipments", admin,
            body={"planned_date": "2026-10-13",
                  "items": [{"order_item_id": paid_item, "planned_qty": 4}]},
        )
        paid_batch = res["data"]["batch_id"]
        status, res = call(
            "POST", f"/orders/{paid_order}/shipments/{paid_batch}/ship", admin,
            body={"actual_ship_date": "2026-10-17",
                  "items": [{"order_item_id": paid_item, "shipped_qty": 4}]},
        )
        check("这张单一次发完", res.get("code"), 0)
        status, res = call("GET", f"/orders/{paid_order}/shipments", admin)
        summary = res["data"]["summary"]
        check("整单已发完", summary["all_shipped"], True)
        # 建议发货日 = 10-20 − 7 = 10-13；实际 10-17 发 → 晚 4 天（这时才给数字）
        check("发完之后才给最终偏差：+4", summary["last_batch_vs_delivery_days"], 4)

        # ------------------------------------------------------------------
        print()
        # 这笔是美元单：要先把业务口径放开（业务方也是先改口径、再报外币价）。
        # `finally` 一定要收回：收不回去，后面所有套件都会以为可以写外币。
        open_export(admin)
        try:
            print("=== 9.9 客户概览按币种分组 ===")
            # 客户 B 名下：一笔 USD 100、一笔 CNY 100 —— 合并相加会变成 200
            call(
                "POST", "/orders", admin,
                body={
                    "customer_id": ids["customer_B"],
                    "currency": "USD",
                    "items": [{"sku_id": ids["sku"], "quantity": 1, "unit_price": 100}],
                },
            )
            call(
                "POST", "/orders", admin,
                body={
                    "customer_id": ids["customer_B"],
                    "items": [{"sku_id": ids["sku"], "quantity": 1, "unit_price": 100}],
                },
            )
            status, res = call("GET", f"/customers/{ids['customer_B']}/overview", admin)
            amounts = res["data"]["counts"]["order_amounts"]
            currencies = {row["currency"] for row in amounts}
            check_true(
                "两个币种分别列出（没有相加）",
                {"CNY", "USD"} <= currencies,
                str(amounts),
            )
            check_true(
                "每个币种的金额各自独立",
                all(row["amount"] == 100 for row in amounts if row["currency"] in ("CNY", "USD")),
                str(amounts),
            )
            detail = res["data"]["orders"][0]
            check_true("订单摘要带币种", "currency" in detail, str(detail))
        finally:
            restore_domestic(admin)
    finally:
        await cleanup(ids)

    print()
    if FAILURES:
        print(f"❌ 第九批回归失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"   - {item}")
        raise SystemExit(1)
    print("✅ 第九批回归全部通过")


if __name__ == "__main__":
    asyncio.run(main())
