"""第十三批返修：N01~N06 六条确认缺陷的**反例**回归。

这个套件专门复现"原测试全绿、缺陷仍在"的那几条 —— 每一条都先打**反例**，
再打**正向对照**，确保修的是行为而不是把功能一起关掉：

- **N01** 报价数量：负/零/超三位小数必须拒绝；合法数量下
  「明细金额之和 == 版本货款」「明细金额 == 数量×单价」（从前合计拿未舍入原值、
  明细拿落库舍入值，同一张单两个数）。新增 / 编辑 / 批量三个入口同一把尺子。
- **N02** 应收终态：已结清不能被标逾期；随订单取消的节点改备注后仍是取消。
- **N03** 取消订单不得再产生应收（手工 / 订单路径 / 按比例三个入口）。
- **N04** 工作台待回款随订单取消**减少相应金额**（从前取消的节点仍被计入）。
- **N05** 工作台逾期数**按数据范围**（与应收列表同一口径；从前是全公司数）。
- **N06** 回款必填字段显式传 null → 参数错误（从前 500）；不传保持原值；
  可空字段仍能清空。

只允许一次性隔离库及本机独立 API（防呆见 `_guard`）。
用法：
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/crm_iso_test \\
    API_BASE=http://127.0.0.1:8001/api/v1 \\
    PYTHONPATH=. .venv/bin/python scripts/check_thirteenth_round_repairs.py
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

BASE = os.environ.get("API_BASE", "").rstrip("/")
MARKER = "CHK13R"
FAILURES: list[str] = []
FIX: dict[str, object] = {}

#: 反例里"该被拒绝"的状态码
BAD_REQUEST = 400
UNPROCESSABLE = 422


def _guard() -> None:
    """显式要求一次性隔离库：本套件会真的建订单/应收/回款。"""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit(
            "必须显式设置 DATABASE_URL（一次性隔离库，库名以 crm_iso / crm_check 开头）"
        )
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not (name.startswith("crm_iso") or name.startswith("crm_check")):
        raise SystemExit(f"拒绝执行：DATABASE_URL 指向 {name!r}，不是一次性隔离库")
    if not BASE:
        raise SystemExit(
            "必须显式设置 API_BASE（默认的 8000 是开发后端）。"
            "例：API_BASE=http://127.0.0.1:8001/api/v1"
        )


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + urllib.parse.quote(path, safe="/?&="), data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            return exc.code, {"raw": raw[:300]}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录 {username} 失败：{res.get('message')}")
    return res["data"]["access_token"]


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  OK   {label}：{actual!r}")
    else:
        print(f"  FAIL {label}：{actual!r}（期望 {expected!r}）")
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}{('：' + detail) if detail else ''}")
    else:
        print(f"  FAIL {label}{('：' + detail) if detail else ''}")
        FAILURES.append(label)


# ---------------------------------------------------------------- 直连库（读真值）


def db(sql: str) -> str:
    """直连数据库读一个标量，用来核对"接口说成功了，库里到底写成什么"。"""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.environ["DATABASE_URL"]

    async def go() -> str:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                row = (await conn.execute(text(sql))).first()
                return "" if row is None else str(row[0])
        finally:
            await engine.dispose()

    return asyncio.run(go())


def db_exec(sql: str) -> None:
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.environ["DATABASE_URL"]

    async def go() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
        finally:
            await engine.dispose()

    asyncio.run(go())


# ---------------------------------------------------------------- 夹具


def build_fixtures(admin: str) -> None:
    """建一个专属客户 + SKU，避免碰演示数据。"""
    _, res = call("POST", "/customers", admin, {"name": f"{MARKER}客户"})
    if res.get("code") != 0:
        raise SystemExit(f"建客户失败：{res.get('message')}")
    FIX["cust"] = res["data"]["id"]
    _, res = call("GET", "/pricing/sku-options", admin)
    FIX["sku"] = res["data"][0]["id"]
    _, res = call("GET", "/opportunities?page_size=1", admin)
    items = res["data"]["items"]
    FIX["opp"] = items[0]["id"] if items else None
    print(f"  夹具：客户 {FIX['cust']}、SKU {FIX['sku']}")


def new_quote(admin: str) -> int:
    body = {"customer_id": FIX["cust"], "currency": "CNY"}
    if FIX.get("opp"):
        body["opportunity_id"] = FIX["opp"]
    _, res = call("POST", "/quotes", admin, body)
    if res.get("code") != 0:
        raise SystemExit(f"建报价失败：{res.get('message')}")
    return res["data"]["version_id"]


def new_order(admin: str, suffix: str, amount: str = "4321.00") -> int:
    """建一张手工订单（金额由明细算出），返回 order_id。"""
    _, res = call("POST", "/orders", admin, {
        "customer_id": FIX["cust"],
        "items": [{"sku_id": FIX["sku"], "quantity": 1, "unit_price": amount}],
    })
    if res.get("code") != 0:
        raise SystemExit(f"建订单 {suffix} 失败：{res.get('message')}")
    return res["data"]["order_id"]


def _delete_customers(where: str, params: dict) -> int:
    """删掉符合条件的客户**及其全部下游子行**（按外键目录递归，自底向上）。

    为什么不手工列表：`customers` 被 16 张表引用（联系人、客户价格规则、商机、
    样品、合同、物流报价……），手工列必然漏；漏了就是"清理报 IntegrityError、
    夹具越积越多"，而残留下来的行会改变后续用例的读数（本套件自己就被上轮残留
    撞过一次 order_no 唯一键）。所以照 `seed_freight_split_demo.py` 的做法，
    从 `information_schema` 现场读外键目录，一次事务里递归删干净。
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    FK_SQL = (
        "select ccu.table_name as parent, tc.table_name as child, kcu.column_name as col "
        "from information_schema.table_constraints tc "
        "join information_schema.key_column_usage kcu "
        "  on kcu.constraint_name = tc.constraint_name "
        "join information_schema.constraint_column_usage ccu "
        "  on ccu.constraint_name = tc.constraint_name "
        "where tc.constraint_type = 'FOREIGN KEY'"
    )

    async def go() -> int:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with engine.begin() as conn:
                graph: dict[str, list[tuple[str, str]]] = {}
                for parent, child, col in (await conn.execute(text(FK_SQL))).all():
                    graph.setdefault(parent, []).append((child, col))
                has_id: dict[str, bool] = {}

                async def cols_of(table: str) -> set[str]:
                    rows = (await conn.execute(text(
                        "select column_name from information_schema.columns "
                        "where table_name = :t"), {"t": table})).all()
                    return {r[0] for r in rows}

                async def del_ids(table: str, ids: list[int]) -> None:
                    if not ids:
                        return
                    if table not in has_id:
                        has_id[table] = "id" in await cols_of(table)
                    for child, col in graph.get(table, []):
                        if child not in has_id:
                            has_id[child] = "id" in await cols_of(child)
                        if has_id[child]:
                            child_ids = [
                                r[0] for r in (await conn.execute(text(
                                    f"select id from {child} where {col} = any(:ids)"),
                                    {"ids": ids})).all()
                            ]
                            await del_ids(child, child_ids)
                        else:
                            # 连接表（复合主键，无 id）：直接按外键列删
                            await conn.execute(text(
                                f"delete from {child} where {col} = any(:ids)"),
                                {"ids": ids})
                    await conn.execute(text(f"delete from {table} where id = any(:ids)"),
                                       {"ids": ids})

                ids = [
                    r[0] for r in (await conn.execute(
                        text(f"select id from customers where {where}"), params)).all()
                ]
                await del_ids("customers", ids)
                return len(ids)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def cleanup() -> None:
    """按前缀收：客户（连带其下全部子行）。"""
    try:
        removed = _delete_customers("name like :p", {"p": f"{MARKER}%"})
        left_c = db(f"select count(*) from customers where name like '{MARKER}%'")
        # N05 的夹具挂在演示客户名下、不在上面这棵树里；它自己每次"先删后建"，
        # 所以不算残留（否则提示永远显示 1，看起来像没清干净）。
        left_o = db(
            "select count(*) from sales_orders "
            f"where order_no like '{MARKER}%' and order_no <> '{MARKER}-N05'"
        )
        print(f"  已清理 {removed} 个夹具客户（残留客户 {left_c}、残留订单 {left_o}）")
    except Exception as exc:  # noqa: BLE001
        print(f"  清理失败（不影响结论）：{exc.__class__.__name__}: {exc}")


# ---------------------------------------------------------------- N01


def n01_quantity(admin: str) -> None:
    print("\n=== N01 报价数量：非法值拒绝 + 合法值三处一致 ===")
    vid = new_quote(admin)
    for qty in (-1, 0, 1.23456, 0.0001):
        status, res = call("POST", f"/quote-versions/{vid}/items", admin,
                           {"sku_id": FIX["sku"], "quantity": qty, "quoted_price": 150})
        check(f"新增：数量 {qty} 被拒", status, BAD_REQUEST)
        check_true(f"新增：数量 {qty} 的提示点名字段", "数量" in str(res.get("message")))

    # 三个入口同一把尺子：编辑与批量也不能放行
    _, res = call("POST", f"/quote-versions/{vid}/items", admin,
                  {"sku_id": FIX["sku"], "quantity": 2, "quoted_price": 150})
    item_id = res["data"]["id"]
    for qty in (-1, 0, 1.23456):
        status, _ = call("PATCH", f"/quote-items/{item_id}", admin, {"quantity": qty})
        check(f"编辑：数量 {qty} 被拒", status, BAD_REQUEST)
    for qty in (-1, 0, 1.23456):
        status, _ = call("POST", f"/quote-versions/{vid}/items/batch", admin,
                         [{"sku_id": FIX["sku"], "quantity": qty, "quoted_price": 150}])
        check(f"批量：数量 {qty} 被拒", status, BAD_REQUEST)
    # 被拒之后原值一个字没动
    _, detail = call("GET", f"/quote-versions/{vid}", admin)
    mine = [x for x in detail["data"]["items"] if x["id"] == item_id][0]
    check("被拒后原数量未被改坏", mine["quantity"], 2.0)

    # 正向对照：合法数量下"同一张单只有一个数"
    vid2 = new_quote(admin)
    for qty in ("1.235", "0.5", "1000"):
        _, res = call("POST", f"/quote-versions/{vid2}/items", admin,
                      {"sku_id": FIX["sku"], "quantity": qty, "quoted_price": 150})
        check(f"合法数量 {qty} 可保存", res.get("code"), 0)
    _, detail = call("GET", f"/quote-versions/{vid2}", admin)
    items = detail["data"]["items"]
    summary = detail["data"]["summary"]
    line_sum = sum((Decimal(str(i["amount"])) for i in items), Decimal(0))
    check_true(
        "明细金额之和 == 版本货款（两个数同源）",
        abs(line_sum - Decimal(str(summary["goods_amount"]))) < Decimal("0.005"),
        f"明细和={line_sum} 货款={summary['goods_amount']}",
    )
    per_item_ok = all(
        abs(Decimal(str(i["quantity"])) * Decimal(str(i["quoted_price"]))
            - Decimal(str(i["amount"]))) < Decimal("0.005")
        for i in items
    )
    check_true("每条明细：金额 == 数量 × 单价", per_item_ok)
    check_true(
        "落库数量未被静默舍入（都 ≤ 3 位小数）",
        all(Decimal(str(i["quantity"])) == Decimal(str(i["quantity"])).quantize(Decimal("0.001"))
            for i in items),
    )


# ---------------------------------------------------------------- N02


def n02_terminal_status(admin: str) -> None:
    print("\n=== N02 应收终态：已结清与已取消都不许被覆盖 ===")
    order_id = new_order(admin, "N02")
    _, res = call("POST", f"/orders/{order_id}/receivables", admin,
                  {"plan_name": f"{MARKER}-N02", "due_date": "2026-01-01", "amount": 700})
    plan_id = res["data"]["id"]

    # 已结清 -> 不许标逾期，也不许被重算退回待收
    #
    # ⚠️ 必须**真实收齐**，不能用 SQL 硬改状态（2026-10-09 修，C4-01 连带发现）。
    # 原来写的是 `db_exec("update receivable_plans set status='paid' ...")` ——
    # 一行 SQL 造出"已结清"，但这条应收**一分钱都没收**（amount 700、回款 0）。
    # 那种假状态在 C4-01 之前"能过"，只是因为 `paid` 被 `recalc_plan` 整个跳过；
    # C4-01 让 `paid` 也参与重算之后，它一算就变 `overdue` —— 而**这是对的**
    # （没收钱 + 已过期）。所以这条用例原先**测不到真实场景**，只是被跳过逻辑掩盖了。
    # 现在走正常业务路径收齐 700，再验"改备注不会把它算回去"。
    status, _ = call("POST", "/payments", admin,
                     {"receivable_plan_id": plan_id, "received_date": "2026-10-09",
                      "received_amount": 700})
    check("N02 前置：登记回款", status, 200)
    payment_id = db("select id from payment_records order by id desc limit 1")
    status, _ = call("POST", f"/payments/{payment_id}/confirm", admin, {})
    check("N02 前置：确认回款", status, 200)
    check("N02 前置：收齐后是 paid",
          db(f"select status from receivable_plans where id={plan_id}"), "paid")

    status, res = call("POST", f"/receivables/{plan_id}/mark-overdue", admin, {})
    check("已结清标逾期被拒", status, UNPROCESSABLE)
    check("已结清状态仍是 paid", db(f"select status from receivable_plans where id={plan_id}"), "paid")
    status, _ = call("PATCH", f"/receivables/{plan_id}", admin, {"remark": "改备注"})
    check("已结清改备注放行", status, 200)
    check("已结清改备注后仍是 paid（真实收齐的那种）",
          db(f"select status from receivable_plans where id={plan_id}"), "paid")

    # 已取消 -> 改备注不许复活
    db_exec(
        "update receivable_plans set status='cancelled', due_date=CURRENT_DATE - 10 "
        f"where id={plan_id}"
    )
    status, _ = call("PATCH", f"/receivables/{plan_id}", admin, {"remark": "只改备注"})
    check("取消节点改备注放行", status, 200)
    check("取消节点改备注后仍是 cancelled",
          db(f"select status from receivable_plans where id={plan_id}"), "cancelled")
    status, _ = call("POST", f"/receivables/{plan_id}/mark-overdue", admin, {})
    check("取消节点标逾期被拒", status, UNPROCESSABLE)
    check("取消节点标逾期后仍是 cancelled",
          db(f"select status from receivable_plans where id={plan_id}"), "cancelled")

    # 正向对照：普通待收节点仍能正常标逾期、改备注
    _, res = call("POST", f"/orders/{order_id}/receivables", admin,
                  {"plan_name": f"{MARKER}-N02b", "due_date": "2026-01-01", "amount": 10})
    live_id = res["data"]["id"]
    status, _ = call("POST", f"/receivables/{live_id}/mark-overdue", admin, {})
    check("普通节点仍可标逾期", status, 200)
    check("普通节点标逾期后是 overdue",
          db(f"select status from receivable_plans where id={live_id}"), "overdue")


# ---------------------------------------------------------------- N03


def n03_cancelled_order(admin: str) -> None:
    print("\n=== N03 取消订单：三个入口都不许再产生应收 ===")
    order_id = new_order(admin, "N03")
    status, res = call("POST", f"/orders/{order_id}/cancel", admin, {"reason": f"{MARKER}验证"})
    check("订单已取消", res.get("code"), 0)
    check("订单状态是 cancelled", db(f"select status from sales_orders where id={order_id}"),
          "cancelled")

    before = db(f"select count(*) from receivable_plans where order_id={order_id}")
    status, res = call("POST", "/receivables", admin, {
        "order_id": order_id, "plan_name": f"{MARKER}-N03a",
        "due_date": "2026-01-01", "amount": 100,
    })
    check("手工新增被拒", status, UNPROCESSABLE)
    check_true("手工新增的提示说清原因", "已取消" in str(res.get("message")))
    status, _ = call("POST", f"/orders/{order_id}/receivables", admin, {
        "plan_name": f"{MARKER}-N03b", "due_date": "2026-01-01", "amount": 50,
    })
    check("订单路径新增被拒", status, UNPROCESSABLE)
    status, _ = call("POST", f"/orders/{order_id}/receivables/generate", admin, {
        "ratios": [0.3, 0.7], "first_due_date": "2026-01-01", "second_due_date": "2026-02-01",
    })
    check("按比例生成被拒", status, UNPROCESSABLE)
    after = db(f"select count(*) from receivable_plans where order_id={order_id}")
    check("被拒后一条应收都没多出来", after, before)

    # 正向对照：未取消订单照常能建
    live_order = new_order(admin, "N03-live")
    status, _ = call("POST", f"/orders/{live_order}/receivables", admin, {
        "plan_name": f"{MARKER}-N03c", "due_date": "2026-03-01", "amount": 20,
    })
    check("未取消订单仍可建应收", status, 200)


# ---------------------------------------------------------------- N04 / N05


def workbench(token: str) -> tuple[float, int]:
    _, res = call("GET", "/dashboard/summary", token)
    data = res["data"]
    return float(data["pending_receivable_amount"]), int(data["overdue_receivable_count"])


def n04_pending_amount(admin: str) -> None:
    print("\n=== N04 工作台待回款：取消订单后必须减少相应金额 ===")
    amount = "4321.00"
    order_id = new_order(admin, "N04", amount)
    _, res = call("POST", f"/orders/{order_id}/receivables", admin,
                  {"plan_name": f"{MARKER}-N04", "due_date": "2026-06-01", "amount": amount})
    plan_id = res["data"]["id"]
    before, _ = workbench(admin)
    status, _ = call("POST", f"/orders/{order_id}/cancel", admin, {"reason": f"{MARKER}N04"})
    check("订单已取消", status, 200)
    check("节点随订单置为 cancelled",
          db(f"select status from receivable_plans where id={plan_id}"), "cancelled")
    after, _ = workbench(admin)
    check_true(
        "待回款减少了这笔金额",
        abs(before - after - float(amount)) < 0.01,
        f"取消前={before} 取消后={after} 差={before - after:.2f}",
    )


def n05_scope(admin: str, zhangsan: str) -> None:
    print("\n=== N05 工作台逾期数：按数据范围，与应收列表同口径 ===")
    # 先清掉可能的上轮残留（夹具要能反复跑，否则撞 order_no 唯一键）
    leftover = db(f"select id from sales_orders where order_no='{MARKER}-N05'")
    if leftover:
        db_exec(f"delete from receivable_plans where order_id={leftover}")
        db_exec(f"delete from sales_orders where id={leftover}")
    # 造一条**别人名下**的逾期节点：admin 的两次读数都不能被它影响
    owner_id = db("select id from users where username='zhangsan'")
    cust = db("select customer_id from sales_orders order by id limit 1")
    db_exec(
        "insert into sales_orders (order_no, customer_id, owner_id, sales_owner_id, "
        "total_amount, currency, status, created_by, created_at, updated_at) "
        f"values ('{MARKER}-N05', {cust}, {owner_id}, {owner_id}, 500.00, 'CNY', "
        "'pending', 1, now(), now())"
    )
    order_id = db(f"select id from sales_orders where order_no='{MARKER}-N05'")
    db_exec(
        "insert into receivable_plans (order_id, plan_name, due_date, amount, currency, "
        f"status, created_at) values ({order_id}, '{MARKER}-N05', "
        "CURRENT_DATE - 15, 500.00, 'CNY', 'overdue', now())"
    )

    def counts(token: str) -> tuple[int, int]:
        _, res = call("GET", "/receivables?status=overdue&page_size=200", token)
        data = res["data"]
        items = data.get("items") if isinstance(data, dict) else data
        return len(items), workbench(token)[1]

    admin_list, admin_wb = counts(admin)
    check("admin：工作台 == 列表", admin_wb, admin_list)
    zs_list, zs_wb = counts(zhangsan)
    check("张三：工作台 == 自己可见的列表", zs_wb, zs_list)
    company = int(db("select count(*) from receivable_plans where status='overdue'"))
    check_true(
        "张三（非管理员）拿到的不是全公司数",
        zs_wb != company or zs_list == company,
        f"张三工作台={zs_wb} 张三列表={zs_list} 全公司={company}",
    )
    check_true("全公司的逾期确实多于张三可见的", company > zs_list,
               f"全公司={company} > 张三={zs_list}")


# ---------------------------------------------------------------- N06


def n06_payment_null(admin: str) -> None:
    print("\n=== N06 回款更新：必填字段传 null 是参数错误，不是 500 ===")
    order_id = new_order(admin, "N06")
    # 回款必须挂在应收节点上（`POST /payments` 要的是 receivable_plan_id）
    _, res = call("POST", f"/orders/{order_id}/receivables", admin,
                  {"plan_name": f"{MARKER}-N06", "due_date": "2026-06-01", "amount": 500})
    plan_id = res["data"]["id"]
    _, res = call("POST", "/payments", admin, {
        "receivable_plan_id": plan_id, "received_date": "2026-01-05",
        "received_amount": 111, "payment_method": "电汇",
    })
    if res.get("code") != 0:
        raise SystemExit(f"建回款失败：{res.get('message')}")
    payment_id = res["data"]["id"]

    for field, label in (("received_amount", "金额"), ("received_date", "日期")):
        status, res = call("PATCH", f"/payments/{payment_id}", admin, {field: None})
        check(f"{label}传 null → 参数错误（不是 500）", status, BAD_REQUEST)
        check_true(f"{label}的提示点名字段", label in str(res.get("message")))

    status, res = call("PATCH", f"/payments/{payment_id}", admin, {})
    check("不传字段照常放行", status, 200)
    check("不传字段保持原值", res["data"]["received_amount"], 111.0)
    # 可空字段仍能清空（别把校验做成"什么都拦"）
    status, _ = call("PATCH", f"/payments/{payment_id}", admin, {"voucher_note": None})
    check("可空字段（凭证说明）仍可清空", status, 200)
    status, res = call("PATCH", f"/payments/{payment_id}", admin, {"received_amount": 222})
    check("正常改金额仍可用", status, 200)
    check("金额确实改了", res["data"]["received_amount"], 222.0)


# ---------------------------------------------------------------- main


def main() -> int:
    _guard()
    admin = login("admin", "admin123")
    zhangsan = login("zhangsan", "123456")
    cleanup()
    try:
        build_fixtures(admin)
        n01_quantity(admin)
        n02_terminal_status(admin)
        n03_cancelled_order(admin)
        n04_pending_amount(admin)
        n05_scope(admin, zhangsan)
        n06_payment_null(admin)
    finally:
        cleanup()
    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：{FAILURES}")
        return 1
    print("第十三批返修（N01~N06）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
