"""第四批返修的反例断言（审查 C4-01 …）。

断言写的是**当时复现出来的那个反例**，不是"改完长什么样"——退回去改坏了一定会红。

用法：
    API_BASE=http://127.0.0.1:8010/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_c4 \
    PYTHONPATH=. .venv/bin/python scripts/check_c4_round_repairs.py

库名**只认 `DATABASE_URL`**（与夹具同一个真源），并过 `require_isolated_db()` 防呆。
CI 的后端 job 只设 `DATABASE_URL`，这里从前另读 `DB_NAME` → 会落到开发库默认值。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.getenv("API_BASE", "http://127.0.0.1:8010/api/v1")
MARK = "CHKC4"

# ---------------------------------------------------------------------------
# 库名**只认 `DATABASE_URL` 这一个真源**（2026-10-10 修，为加入 CI 清单）。
#
# 从前这里读的是一个独立变量 `DB_NAME`，于是有**两个真源**：
#   - 夹具走 `app.core.database`（吃 `DATABASE_URL`）→ 写到 A 库；
#   - 本套件的 psql 子进程走 `DB`（吃 `DB_NAME`）→ 查 B 库。
# CI 的后端 job **只设了 `DATABASE_URL`、没设 `DB_NAME`**，`DB` 就落到默认值
# （一个开发库名）—— 断言会在**开发库**上查，而夹具写进隔离库：
# 要么查不到（假红），要么去动开发库（更糟）。
# 现在从 `DATABASE_URL` 里取出库名，两边必然一致；本地/CI/容器三种部署都成立。
# ---------------------------------------------------------------------------
if os.getenv("DATABASE_URL") is None:
    os.environ["DATABASE_URL"] = (
        f"postgresql+asyncpg://{os.getenv('PG_USER', 'crm')}:"
        f"{os.getenv('PGPASSWORD', 'crm123456')}@"
        f"{os.getenv('PG_HOST', '127.0.0.1')}:{os.getenv('PG_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'crm_check_test_c4')}"
    )

import sys as _sys

_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _test_support import require_isolated_db  # noqa: E402

#: 与 `DATABASE_URL` 同源，**不再是第二个变量**（必须在上面 import 之前就位：
#: `require_isolated_db` 要求"设置好 DATABASE_URL 之后、import app.* 之前"调用）。
DB = require_isolated_db()

# 夹具（`_fixture_sku_master`）用 `app.core.database`，连的是**这个进程**的
# `settings.database_url`。上面的 `DATABASE_URL` 已经设好（且与 `DB` 同源），
# 所以夹具和 psql 断言必然打同一个库 —— 不再需要对第二块对齐代码。

# ---------------------------------------------------------------------------
# 同步 SQL：用项目自己的引擎（`app.core.database`，吃 DATABASE_URL）。
#
# 从前这里用 `subprocess` 调 **psql**，有两个坑（2026-10-11 加入 CI 时实测踩到）：
#   ① `PSQL_BIN` 默认写死 macOS 的 `/opt/homebrew/opt/postgresql@15/bin/psql`，
#      Ubuntu（CI）上不存在 → `subprocess.run` 抛 FileNotFoundError；
#      清单里另外 91 个套件**没有一个**依赖 psql，这是唯一的例外。
#   ② psql 用 `-d DB`、夹具用 `DATABASE_URL` —— 两个库名真源，CI 只设后者，
#      两边会指向不同的库。
# 现在统一走 `scripts/_db_helper.py`：内部是异步引擎 + 专属事件循环线程，
# 所以**同步代码和 async def 里都能直接调**（本套件有 8 处在 async 里调），
# 返回值仍是 `psql -tAc` 的形状。
# ---------------------------------------------------------------------------
from _db_helper import db  # noqa: E402

passed = 0
failed: list[str] = []


def call(method, path, token=None, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + urllib.parse.quote(path, safe="/?&="), data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")
    except Exception as exc:  # 超时/连接失败也算一种结果
        return "TIMEOUT", {"message": str(exc)[:60]}


def check(label, got, want):
    global passed
    if str(got) == str(want):
        passed += 1
        print(f"  OK   {label}: {got!r}")
    else:
        failed.append(label)
        print(f"  FAIL {label}: {got!r}（期望 {want!r}）")


def check_true(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  OK   {label} {detail}")
    else:
        failed.append(label)
        print(f"  FAIL {label} {detail}")


# =====================================================================
# C4-01：已结清的应收改大金额后，状态必须如实退回
# =====================================================================
def sec_c401(admin: str) -> None:
    """复现出来的反例：应收 100 已收齐（paid），PATCH 改成 200 后
    **余额 100 却仍是 paid** —— 对账时说不清这条算不算收完。

    根因：`recalc_plan()` 里 `paid` 在终态集合里被直接跳过。那条保护是为 N02 加的
    （防"取消的节点被算回 overdue"），但它前提写错了 —— 以为 `paid` 无法合法退回，
    漏算了"改应收金额"这条路径。现在终态只留 `cancelled`。
    """
    print("\n=== C4-01 已结清应收改大金额 ===")
    db("delete from payment_records")
    db("delete from receivable_plans")
    db("delete from sales_order_items")
    db("delete from sales_orders")

    def fresh_order(amount=100) -> str:
        call("POST", "/orders", admin, {
            "customer_id": 1, "currency": "CNY",
            "items": [{"sku_id": 1, "quantity": 1, "unit_price": amount}]})
        return db("select id from sales_orders order by id desc limit 1")

    def add_plan(oid: str, amt) -> str:
        call("POST", f"/orders/{oid}/receivables", admin,
             {"plan_name": f"{MARK}-测试", "due_date": "2026-11-01", "amount": amt})
        return db("select id from receivable_plans order by id desc limit 1")

    def pay(pid: str, amt) -> None:
        call("POST", "/payments", admin, {
            "receivable_plan_id": int(pid), "received_date": "2026-10-09", "received_amount": amt})
        rec = db("select id from payment_records order by id desc limit 1")
        call("POST", f"/payments/{rec}/confirm", admin, {})

    def st(pid: str) -> str:
        return db(f"select status from receivable_plans where id={pid}")

    # ① 正题
    oid = fresh_order(100)
    pid = add_plan(oid, 100)
    pay(pid, 100)
    check("① 收齐后状态", st(pid), "paid")
    call("PATCH", f"/receivables/{pid}", admin, {"amount": 200})
    check("① 改成 200 后如实退回 partial（从前仍是 paid）", st(pid), "partial")
    _, res = call("GET", f"/receivables/{pid}", admin)
    d = res.get("data") or {}
    if isinstance(d, list):
        d = d[0] if d else {}
    check("① 余额与状态一致（余额 100 且不是已结清）",
          f"{d.get('remaining_amount')}/{d.get('status')}", "100.0/partial")
    # ② 改回去能回 paid
    call("PATCH", f"/receivables/{pid}", admin, {"amount": 100})
    check("② 金额改回 100 后回到 paid", st(pid), "paid")

    # ③ N02 回归：取消的节点不能被重算复活
    oid2 = fresh_order(500)
    pid2 = add_plan(oid2, 500)
    db(f"update receivable_plans set due_date='2026-01-01' where id={pid2}")
    call("PATCH", f"/receivables/{pid2}", admin, {"remark": "触发重算"})
    check("③ 过期未收 → overdue", st(pid2), "overdue")
    call("POST", f"/orders/{oid2}/cancel", admin, {"reason": "测试取消"})
    if st(pid2) == "cancelled":
        call("PATCH", f"/receivables/{pid2}", admin, {"remark": "再触发一次重算"})
        check("③ 取消后再重算仍是 cancelled（N02 未被弄坏）", st(pid2), "cancelled")
    else:
        print(f"  --   订单取消未连带节点取消（当前 {st(pid2)}），跳过 N02 断言")

    # ④ 边界：收过一部分 + 已过期 → partial 而不是 overdue
    oid3 = fresh_order(1000)
    pid3 = add_plan(oid3, 1000)
    db(f"update receivable_plans set due_date='2026-01-01' where id={pid3}")
    pay(pid3, 300)
    call("PATCH", f"/receivables/{pid3}", admin, {"remark": "触发重算"})
    check("④ 收过 300/1000 且已过期 → partial（不是 overdue）", st(pid3), "partial")

    # ⑤ 边界：零回款 + 未过期 → pending
    oid4 = fresh_order(100)
    pid4 = add_plan(oid4, 100)
    db(f"update receivable_plans set due_date='2099-01-01' where id={pid4}")
    call("PATCH", f"/receivables/{pid4}", admin, {"remark": "触发重算"})
    check("⑤ 未收且未过期 → pending", st(pid4), "pending")

    # ⑥ 「标记逾期」仍要拒绝已结清的节点（那条保护不能被我改没）
    oid5 = fresh_order(100)
    pid5 = add_plan(oid5, 100)
    pay(pid5, 100)
    check("⑥ 前置：收齐", st(pid5), "paid")
    code = call("POST", f"/receivables/{pid5}/mark-overdue", admin, {})[1].get("code")
    check_true("⑥ 「标记逾期」仍拒绝已结清的节点", code != 0, f"code={code}")

    # 收尾
    db("delete from payment_records")
    db("delete from receivable_plans")
    db("delete from sales_order_items")
    db("delete from sales_orders")


# =====================================================================
# C4-01 之外：报价可以挂已删除联系人 —— 放行但**必须显性提醒**
# =====================================================================
def sec_quote_deleted_contact(admin: str) -> None:
    """口径（主人 2026-10-09 拍板）：**放行 + warnings 提醒**，不硬拦。

    为什么不硬拦：联系人不只由人手工选，还会从**定制需求**自动带过来
    （`inquiry.contact_id`）。那个人离职后被删，硬拦会让"从这条需求建报价"
    这条正常业务卡死，出路只有把已删联系人重新加回来（留假数据）。
    与"主数据未确认"同一口径：默认放行 + 如实提示，不静默。

    这条断言守两件事：① 确实放行（别被谁改成硬拦）；② 提醒确实出现
    （别变成"静默放行"）。
    """
    print("\n=== 报价挂已删除联系人：放行 + 提醒 ===")
    db(f"delete from contacts where name like '{MARK}-联系人%'")
    cust = db("select id from customers order by id limit 1")

    # 造一个联系人并删掉
    call("POST", f"/customers/{cust}/contacts", admin,
         {"name": f"{MARK}-联系人甲", "mobile": "13900001111"})
    ct = db("select id from contacts order by id desc limit 1")
    call("DELETE", f"/contacts/{ct}", admin)
    check_true("前置：联系人已软删除",
               db(f"select deleted_at is not null from contacts where id={ct}") == "t")

    # 建商机（报价必须挂商机）
    call("POST", "/opportunities", admin,
         {"title": f"{MARK}-商机", "customer_id": int(cust), "expected_amount": 100})
    opp = db("select id from opportunities order by id desc limit 1")

    # ① 带已删除联系人建报价 → 放行，且必须有提醒
    _, res = call("POST", "/quotes", admin, {"opportunity_id": int(opp), "contact_id": int(ct)})
    check("① 放行（不是硬拦）", res.get("code"), 0)
    warns = (res.get("data") or {}).get("warnings") or []
    check_true("① warnings 里说清联系人已被删除",
               any("已被删除" in w for w in warns),
               f"warnings={warns}")

    # ② 对照：有效联系人不应出现这条提醒（别把提醒写成无条件的）
    call("POST", f"/customers/{cust}/contacts", admin,
         {"name": f"{MARK}-联系人乙", "mobile": "13900002222"})
    ct2 = db("select id from contacts order by id desc limit 1")
    _, res2 = call("POST", "/quotes", admin, {"opportunity_id": int(opp), "contact_id": int(ct2)})
    check("② 有效联系人可以正常建报价", res2.get("code"), 0)
    warns2 = (res2.get("data") or {}).get("warnings") or []
    check_true("② 有效联系人没有这条提醒",
               not any("已被删除" in w for w in warns2),
               f"warnings={warns2}")

    # 收尾
    db(f"delete from contacts where name like '{MARK}-联系人%'")
    db(f"delete from opportunities where title = '{MARK}-商机'")


# =====================================================================
# C4-02：合同依据必须与订单依据**同一版**报价
# =====================================================================
async def _sku_master_fixture(sku_id: int, *, restore: dict | None = None) -> dict:
    """进/出 SKU 主数据夹具，**同一个事件循环里完成**。

    ⚠️ 不要把这个拆成多个 `asyncio.run()`：`app.core.database` 的 engine 在
    第一次使用时把连接池绑到当时的事件循环，第二次 `asyncio.run()` 换了个循环，
    实测报 `Future ... attached to a different loop`。所以进和出各只调一次。

    - `restore=None` → **进入**：先记下原有状态并返回它，再写入"全部已确认"夹具
      （报价要过"正式发送"闸门，必须有已确认的主数据 + 整版快照）；
    - `restore=<快照>` → **恢复**：删掉本套件写的行，再把快照里的行原样插回。

    **为什么必须恢复**：夹具把字段权威标成"已确认"，而 `check_quote_api` 有一条
    断言依赖"主数据**未确认**时只提示不阻断"。我第一版漏了恢复，实测污染了同一个库
    （`check_quote_api` 单独跑也红）。
    """
    import json as _json

    from sqlalchemy import text as _text

    from app.core.database import SessionLocal
    from app.modules.product.master import MASTER_FIELDS

    async with SessionLocal() as s:
        if restore is not None:
            await s.execute(_text(
                "delete from sku_master_versions where sku_id = :sku and diff_key like :k"),
                {"sku": sku_id, "k": f"{MARK}:%"})
            await s.execute(_text("delete from sku_field_authorities where sku_id = :sku"),
                            {"sku": sku_id})
            for f in restore["fields"]:
                await s.execute(_text(
                    "insert into sku_field_authorities "
                    "(sku_id, field_name, confirmed_version, confirmed_value, confirmed_by, "
                    " status, source_verified, created_at, updated_at) "
                    "values (:sku, :f, :v, cast(:val as jsonb), :by, :st, :sv, :ca, now())"),
                    {"sku": sku_id, "f": f[0], "v": f[1], "val": f[2], "by": f[3],
                     "st": f[4], "sv": f[5], "ca": f[6]})
            await s.commit()
            return {}

        # —— 进入：快照 ——
        fields = (await s.execute(_text(
            "select field_name, confirmed_version, confirmed_value::text, confirmed_by, status, "
            "       source_verified, created_at "
            "from sku_field_authorities where sku_id = :sku"), {"sku": sku_id})).all()
        before = {"fields": [tuple(r) for r in fields]}

        # —— 写入夹具 ——
        values = {f: f"{MARK}-{f}" for f in MASTER_FIELDS}
        values["name"] = f"{MARK} 测试品名"
        values["specification"] = f"{MARK} 规格"
        values["unit"] = "个"
        for field in MASTER_FIELDS:
            await s.execute(_text(
                "insert into sku_field_authorities "
                "(sku_id, field_name, confirmed_version, confirmed_value, confirmed_by, "
                " status, source_verified, created_at, updated_at) "
                "values (:sku, :f, 1, cast(:val as jsonb), 1, 'confirmed', false, now(), now()) "
                "on conflict (sku_id, field_name) do update set "
                "confirmed_version = 1, confirmed_value = cast(:val as jsonb), "
                "status = 'confirmed', updated_at = now()"),
                {"sku": sku_id, "f": field, "val": _json.dumps(values[field])})
        await s.execute(_text(
            "insert into sku_master_versions "
            "(sku_id, version_no, values, source_summary, confirmed_by, confirmed_at, "
            " note, diff_key, created_at, updated_at) "
            "values (:sku, 1, cast(:vals as jsonb), cast(:src as jsonb), 1, now(), "
            " :note, :key, now(), now()) "
            "on conflict (sku_id, version_no) do update set values = cast(:vals as jsonb), "
            "updated_at = now()"),
            {"sku": sku_id, "vals": _json.dumps(values),
             "src": _json.dumps({"note": MARK}), "note": f"{MARK} 第 1 版",
             "key": f"{MARK}:v1"})
        await s.commit()
        return before


def sec_c402(admin: str) -> None:
    """复现出来的反例：订单依据 V1、生成合同时显式选 V2 —— 原校验只比**整份报价单**
    （`order.quote_id` vs `quote.id`），不比**版本**，于是合同照样生成；签署闸门
    （`_ensure_signable_source`）看到合同挂了未取消的订单就**直接 return**，
    于是能一路签下去。同一份合同上"订单号"与"报价版本"指向两个不同依据，而它是要
    签字盖章的对外文件。

    口径（主人 2026-10-09 拍板 A）：**直接拦住**，不让人选不一致的版本。
    不传版本的路径不受影响（生成时"跟订单走"会自动带出订单依据的那一版）。
    """
    print("\n=== C4-02 合同依据必须与订单依据同一版 ===")
    import asyncio

    sku = int(db("select id from skus order by id limit 1"))
    async def _run() -> None:
        """**同一个事件循环**里"进夹具 → 跑完全部步骤 → 恢复"。

        为什么必须包在一起：`app.core.database` 的 engine 第一次使用时把连接池
        绑到当时的事件循环；`asyncio.run()` 每调一次就新建一个循环，第二次必报
        `Future ... attached to a different loop`（实测踩到）。
        其他套件都是单个 `async def main()` 全程一个循环，这里照同一结构。
        """
        before = await _sku_master_fixture(sku)
        try:
            # 模板 + 客户 + 商机 + 报价
            call("POST", "/contract-templates", admin,
                 {"doc_type": "contract", "name": f"{MARK}-合同模板",
                  "body": "金额 {{order_amount}} 客户 {{customer_name}}"})
            tid = db("select id from contract_templates order by id desc limit 1")
            call("POST", "/customers", admin, {"name": f"{MARK}-合同客户"})
            cust = db("select id from customers order by id desc limit 1")
            call("POST", "/opportunities", admin,
                 {"title": f"{MARK}-合同商机", "customer_id": int(cust), "expected_amount": 100})
            opp = db("select id from opportunities order by id desc limit 1")
            call("POST", "/quotes", admin, {"opportunity_id": int(opp)})
            qid = db(f"select id from quotes where customer_id={cust} order by id desc limit 1")
            v1 = db(f"select id from quote_versions where quote_id={qid} order by id limit 1")

            # 明细 + 运费（提交审批前必须填，提交后版本就锁了）
            call("POST", f"/quote-versions/{v1}/items", admin,
                 {"sku_id": sku, "quantity": 1, "unit_price": 100})
            call("POST", f"/quote-versions/{v1}/charges", admin,
                 {"charge_type": "logistics", "amount": 0})
            call("POST", f"/quote-versions/{v1}/recalculate", admin, {})

            # 报价生命周期：审批 → 发送 → 客户接受
            call("POST", f"/quote-versions/{v1}/submit-approval", admin, {})
            call("POST", f"/quote-versions/{v1}/mark-sent", admin, {"channel": "email"})
            call("POST", f"/quote-versions/{v1}/accept", admin, {})

            # 成交转单：订单记下依据版本（**只有这条路会写 quote_version_id**）
            status, res = call("POST", f"/quote-versions/{v1}/convert-to-order", admin, {})
            oid = db(f"select id from sales_orders where customer_id={cust} order by id desc limit 1")
            check("前置：成交转单成功", status, 200)
            order_version = db(f"select coalesce(quote_version_id::text,'N') from sales_orders where id={oid}")
            check("前置：订单记下了依据版本（=V1）", order_version, str(v1))

            # 报价再出一版 V2 —— 造出"订单依据 V1、报价已有 V2"的局面
            call("POST", f"/quotes/{qid}/versions", admin, {})
            v2 = db(f"select id from quote_versions where quote_id={qid} order by id desc limit 1")
            check_true("前置：V2 与 V1 不同", v2 != v1, f"v1={v1} v2={v2}")

            # ① 显式选 V2（与订单依据不一致）→ 必须拒
            status, res = call("POST", "/contract-documents", admin, {
                "template_id": int(tid), "customer_id": int(cust), "order_id": int(oid),
                "quote_id": int(qid), "quote_version_id": int(v2), "title": f"{MARK}-冲突合同"})
            check_true("① 依据与订单不一致被拒（从前 200 并生成）",
                       res.get("code") != 0, f"code={res.get('code')} {str(res.get('message'))[:56]}")

            # ② 对照：显式选订单依据的那一版 → 放行
            status, res = call("POST", "/contract-documents", admin, {
                "template_id": int(tid), "customer_id": int(cust), "order_id": int(oid),
                "quote_id": int(qid), "quote_version_id": int(v1), "title": f"{MARK}-一致合同"})
            check("② 选订单依据的那一版放行（没误伤）", res.get("code"), 0)

            # ③ 对照：不传版本（生成时"跟订单走"）→ 放行
            status, res = call("POST", "/contract-documents", admin, {
                "template_id": int(tid), "customer_id": int(cust), "order_id": int(oid),
                "title": f"{MARK}-自动带版本"})
            check("③ 不传版本时不误伤（自动跟随订单依据）", res.get("code"), 0)

        finally:
                # 必须恢复：夹具把主数据标成"已确认"，而 `check_quote_api` 有一条断言
                # 依赖"主数据未确认时只提示不阻断"。我第一版漏了，实测污染了同一个库。
                await _sku_master_fixture(sku, restore=before)

    asyncio.run(_run())
    db(f"delete from contract_documents where title like '{MARK}-%'")
    db("delete from contract_templates where name = "
       f"'{MARK}-合同模板'")
    db(f"delete from contacts where name like '{MARK}-%'")
    # C4-02 段自建的**客户与商机**也要收（2026-10-10 修，为加入 CI 清单）。
    # 从前只清了合同/模板/联系人，客户与商机留在库里 —— 被 `check_fixture_residue`
    # 抓到（实测残留：客户 'CHKC4-合同客户'、商机 'CHKC4-商机' 与 'CHKC4-合同商机'）。
    # 这一段自建的**全部**夹具按依赖顺序清。
    #
    # ⚠️ 为什么必须**一次排全**：`db()` 遇到错误只返回 `SQLERR:` 前缀、**不抛异常**，
    # 所以任何一条被外键挡回来都是**静默漏清** —— 套件自己报"全部通过"，
    # 库里却留着东西，只有 `check_fixture_residue` 能发现。
    # 实测就是被这里连挡了四次才收敛的（每一条都是真跑出来的报错）：
    #   quotes_customer_id_fkey → sales_orders_customer_id_fkey
    #   → receivable_plans_order_id_fkey → payment_records 那一层
    #
    # 依赖方向（照 information_schema 的 FK 图谱排的，不是猜的）：
    #   合同（引 客户/订单/报价版本）→ 报价叶子/版本/报价单
    #   → 回款记录 → 应收节点 → 订单叶子/批次/订单
    #   → 商机叶子/商机 → 客户叶子 → 客户
    _cust = f"(select id from customers where name like '{MARK}-%')"
    _so = f"(select id from sales_orders where customer_id in {_cust})"
    _rp = f"(select id from receivable_plans where order_id in {_so})"
    _ver = (
        "(select id from quote_versions where quote_id in "
        f"(select id from quotes where customer_id in {_cust}))"
    )
    _opp = f"(select id from opportunities where title like '{MARK}-%')"
    for sql in (
        # 合同：同时引 客户 / 订单 / 报价版本，必须最先
        f"delete from contract_documents where customer_id in {_cust}",
        # 报价叶子 → 版本 → 报价单
        f"delete from quote_charges where quote_version_id in {_ver}",
        f"delete from quote_send_logs where quote_version_id in {_ver}",
        f"delete from quote_items where quote_version_id in {_ver}",
        f"delete from quote_versions where quote_id in (select id from quotes where customer_id in {_cust})",
        f"delete from quotes where customer_id in {_cust}",
        # 回款记录 → 应收节点 → 订单
        f"delete from payment_records where receivable_plan_id in {_rp}",
        f"delete from receivable_plans where order_id in {_so}",
        f"delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in {_so})",
        f"delete from order_shipment_batches where order_id in {_so}",
        f"delete from order_status_history where order_id in {_so}",
        f"delete from order_milestones where order_id in {_so}",
        f"delete from order_drafts where order_id in {_so}",
        f"delete from sales_order_items where order_id in {_so}",
        f"delete from sales_orders where customer_id in {_cust}",
        # 商机叶子 → 商机
        f"delete from opportunity_stage_history where opportunity_id in {_opp}",
        f"delete from opportunity_items where opportunity_id in {_opp}",
        f"delete from opportunities where title like '{MARK}-%'",
        # 客户叶子 → 客户
        f"delete from customer_price_rules where customer_id in {_cust}",
        f"delete from customer_tags where customer_id in {_cust}",
        f"delete from sales_cases where customer_id in {_cust}",
        f"delete from contacts where customer_id in {_cust}",
        f"delete from customers where name like '{MARK}-%'",
    ):
        db(sql)


# =====================================================================
# SKU 图片：文件中心原先不认识 `sku`，图片只能挂在"概念"上、挂不到"实物"上
# =====================================================================
def sec_sku_images(admin: str) -> None:
    """产品与 SKU 两级都要图片（主人 2026-10-10 拍板 B 口径）。

    修之前：文件中心的 `BUSINESS_PERMISSIONS` **没有 `sku`**，于是给 SKU 上传图片
    走兜底拒成 403「它不在你的数据范围内，或你没有该模块的维护权限」—— 措辞像权限
    问题，真实原因是"这个类型根本没登记"。

    而真实可售、客户真正要看图的是**具体型号**：`products` 只是概念/系列
    （名称、产品线、品牌、描述，**没有物理属性**），`skus` 才是实物
    （编码、规格、颜色、材质、长宽高、重量、装箱数、MOQ）。
    """
    print("\n=== SKU 图片（两级图片）===")
    sku = int(db("select id from skus order by id limit 1"))
    # 清掉本套件可能留下的关联，避免断言互相干扰
    db(f"delete from business_files where business_type='sku' and business_id={sku}")

    # ① 上传（从前 403）
    png = _tiny_png()
    status, res = _upload(png, "sku", sku, admin)
    check_true("① 给 SKU 上传图片（从前 403「不是有效的业务对象」）",
               res.get("code") == 0, f"code={res.get('code')} {str(res.get('message'))[:40]}")
    file_id = (res.get("data") or {}).get("id")

    # ② 读列表
    _, res2 = call("GET", f"/business/sku/{sku}/files", admin)
    rows = res2.get("data") or []
    check_true("② 能读到该 SKU 的附件列表", isinstance(rows, list) and len(rows) >= 1,
               f"附件数={len(rows) if isinstance(rows, list) else rows}")
    if isinstance(rows, list) and rows:
        check("② 读到的就是刚传的图",
              (rows[0].get("mime_type") or "").startswith("image/"),
              True)

    # ③ 不存在的 SKU 照样拒（不能因为"登记了 sku"就变成来者不拒）
    _, res3 = _upload(png, "sku", 999999, admin)
    check_true("③ 不存在的 SKU 仍被拒（没有放开成来者不拒）",
               res3.get("code") != 0, f"code={res3.get('code')}")

    # ④ 权限反例：zhangsan(salesperson) 有 file:manage + product:view，
    #    但**没有 product:manage** —— 看得见图，不能传图。
    #    这条守的是"附件跟随业务对象"的口径：上传是**写入**，要目标模块的写权限。
    zs = login_as("zhangsan", "123456")
    if zs:
        _, res4 = _upload(png, "sku", sku, zs)
        check_true("④ 有文件权但无产品写权限的人不能给 SKU 传图",
                   res4.get("code") != 0, f"code={res4.get('code')}")
        _, res5 = call("GET", f"/business/sku/{sku}/files", zs)
        check("④ 但他看得见（读取只要 product:view）", res5.get("code"), 0)
    else:
        print("  --   zhangsan 登录失败，跳过权限反例")

    # ⑤ 批量接口（SKU 列表显示缩略图要靠它，逐行查就是 N+1）
    skus = db("select string_agg(id::text, ',') from (select id from skus order by id limit 3) t")
    _, resb = call("GET", f"/business/sku/files/batch?business_ids={skus}", admin)
    grouped = resb.get("data") or {}
    ids = [x for x in str(skus).split(",") if x]
    check("⑤ 批量接口可用", resb.get("code"), 0)
    check_true("⑤ 返回覆盖请求的每个对象（含空数组，不是只返回有附件的）",
               isinstance(grouped, dict) and all(k in grouped for k in ids),
               f"请求={ids} 返回={sorted(grouped.keys()) if isinstance(grouped, dict) else grouped}")
    counts = {k: len(v or []) for k, v in (grouped or {}).items()}
    check_true("⑤ 刚传的那张图出现在对应对象下",
               any(n >= 1 for n in counts.values()),
               f"各对象附件数={counts}")

    # 参数校验：非数字要拒（否则会被当成枚举通道）
    code_bad = call("GET", "/business/sku/files/batch?business_ids=abc", admin)[1].get("code")
    check_true("⑤ 非数字 business_ids 被拒", code_bad != 0, f"code={code_bad}")
    _, rese = call("GET", "/business/sku/files/batch?business_ids=", admin)
    check("⑤ 空列表返回空对象（不是报错）", rese.get("code"), 0)

    # 收尾：把测试图删掉，别在库里留垃圾
    if file_id:
        call("DELETE", f"/files/{file_id}", admin)


def _tiny_png() -> bytes:
    """一张 1×1 的合法 PNG（不引第三方库，直接拼字节）。"""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"\x00" + b"\xff\x00\x00"  # 一行，一个红色像素
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def login_as(username: str, password: str) -> str | None:
    """登录另一个账号，拿它的 token（用来做权限反例）。"""
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    return (res.get("data") or {}).get("access_token")


def _upload(content: bytes, business_type: str, business_id: int, token: str, name: str = "chkc4.png"):
    """按 multipart 上传一个文件（`urllib` 手写 body，不引 requests）。"""
    boundary = "----CHKC4Boundary7d01"
    parts: list[bytes] = []
    for field, value in (("business_type", business_type), ("business_id", str(business_id))):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(API + "/files/upload", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")
    except Exception as exc:  # noqa: BLE001
        return "ERR", {"message": str(exc)[:60]}


# =====================================================================
# C4-05：批次明细的数量必须"按批次状态"解读（前端据此显示）
# =====================================================================
def sec_c405_batch_qty(admin: str) -> None:
    """`planned_qty` / `shipped_qty` 的语义随**批次状态**变，接口契约要守住。

    起因（C4-05 复核）：前端原来写 `shipped_qty ?? planned_qty`。
    `shipped_qty` 后端**总是返回数字**（未发货时是 0），而 `??` 只对 null/undefined
    兜底 —— **0 不触发回退**：

        待发批次  planned=10, shipped=0 → 显示「×0」❌（应为计划量 10）
        已发批次  planned=10, shipped=0 → 显示「×0」✅（这一行确实没发）

    **同一个 0 在两种状态下意思相反**，所以判据只能是 `batch.status`。
    这条断言守的是接口那份契约（状态 + 两个数量都得在），前端按它选值。
    """
    print("\n=== C4-05 批次明细的数量语义 ===")
    db("delete from order_shipment_batch_items")
    db("delete from order_shipment_batches")
    db("delete from sales_order_items")
    db("delete from sales_orders")

    call("POST", "/orders", admin, {
        "customer_id": 1, "currency": "CNY",
        "items": [{"sku_id": 1, "quantity": 10, "unit_price": 100},
                  {"sku_id": 2, "quantity": 5, "unit_price": 100}]})
    oid = db("select id from sales_orders order by id desc limit 1")
    items = db(f"select string_agg(id::text, ',' order by id) from sales_order_items where order_id={oid}")
    i1, i2 = (int(x) for x in str(items).split(","))

    status, res = call("POST", f"/orders/{oid}/shipments", admin, {
        "planned_date": "2026-11-01",
        "items": [{"order_item_id": i1, "planned_qty": 10},
                  {"order_item_id": i2, "planned_qty": 5}]})
    check("建批次放行", res.get("code"), 0)
    bid = db(f"select id from order_shipment_batches where order_id={oid} order by id desc limit 1")

    def batch():
        _, r = call("GET", f"/orders/{oid}/shipments", admin)
        for b in ((r.get("data") or {}).get("batches") or []):
            if b.get("id") == int(bid):
                return b
        return {}

    # ① 待发批次：planned=10 / shipped=0 —— 前端必须按"计划量"读，不能显示 0
    b = batch()
    check("① 待发批次状态", b.get("status"), "planned")
    it = next((x for x in (b.get("items") or []) if x.get("order_item_id") == i1), {})
    check("① 待发批次 planned_qty（应显示这个）", it.get("planned_qty"), "10.0")
    check("① 待发批次 shipped_qty 是 0（**不是 null** —— `??` 不会回退）",
          it.get("shipped_qty"), "0.0")

    # ② 已发货批次：一行零实发、一行正常实发 —— 零要保留（那是事实）
    status, res = call("POST", f"/orders/{oid}/shipments/{bid}/ship", admin, {
        "actual_ship_date": "2026-11-02",
        "items": [{"order_item_id": i1, "shipped_qty": 0},
                  {"order_item_id": i2, "shipped_qty": 5}]})
    check("② 混合批次可发货（单行允许 0）", res.get("code"), 0)
    b = batch()
    check("② 已发货批次状态", b.get("status"), "shipped")
    by_id = {x.get("order_item_id"): x for x in (b.get("items") or [])}
    check("② 零实发那行：shipped_qty 仍是 0（不能回退成 10）", by_id.get(i1, {}).get("shipped_qty"), "0.0")
    check("② 零实发那行的 planned_qty 原样保留（历史不美化）", by_id.get(i1, {}).get("planned_qty"), "10.0")
    check("② 正常实发那行", by_id.get(i2, {}).get("shipped_qty"), "5.0")

    db("delete from order_shipment_batch_items")
    db("delete from order_shipment_batches")
    db("delete from sales_order_items")
    db("delete from sales_orders")


# =====================================================================
# C4-06：旧合同（无请求指纹）也要能比出日期差异
# =====================================================================
def _strip_fp(doc_id) -> bool:
    """去掉请求指纹，模拟升级前就存在的旧合同。返回是否真的去掉了。"""
    db(f"update contract_documents set filled_data = filled_data - '_request_fingerprint' "
       f"where id={doc_id}")
    return db(f"select (filled_data ? '_request_fingerprint')::text from contract_documents "
              f"where id={doc_id}") == "false"


def sec_c406_legacy_fingerprint(admin: str) -> None:
    """升级前的旧合同没有指纹，只能逐字段比 —— 此前**漏了到期日与生效日**。

    后果（C4-06 复核）：同一把请求编号、只把到期日从 11-01 改成 12-01 →
    判不出差异 → **回放成旧合同**，返回的到期日还是 11-01。生效日同理。

    **阴性对照**：把日期比较撤掉重跑，① 会得到 code=0（回放）；恢复后是 40001。
    所以这条断言确实钉住了修复，不是"改完长什么样"。
    """
    print("\n=== C4-06 旧合同（无指纹）的日期比较 ===")

    def mk(tag):
        call("POST", "/contract-templates", admin,
             {"doc_type": "contract", "name": f"{MARK}-{tag}", "body": "客户 {{customer_name}}"})
        tid = int(db("select id from contract_templates order by id desc limit 1"))
        key = f"{MARK}-{tag}"
        base = {"template_id": tid, "customer_id": 1, "title": f"{MARK}-{tag}",
                "expiry_date": "2026-11-01", "effective_date": "2026-10-15", "request_key": key}
        call("POST", "/contract-documents", admin, dict(base))
        did = int(db(f"select id from contract_documents where request_key='{key}' and deleted_at is null"))
        return base, did

    strip_fp = _strip_fp

    # ① 旧合同：只改到期日 → 拒
    base, did = mk("L1")
    check_true("① 前置：成功模拟出旧合同", strip_fp(did))
    code = call("POST", "/contract-documents", admin, {**base, "expiry_date": "2026-12-01"})[1].get("code")
    check_true("① 只改到期日被拒（从前回放成 11-01）", code != 0, f"code={code}")
    check("① 原到期日未变", db(f"select expiry_date::text from contract_documents where id={did}"),
          "2026-11-01")

    # ② 旧合同：只改生效日 → 拒
    base2, did2 = mk("L2")
    check_true("② 前置：成功模拟出旧合同", strip_fp(did2))
    code = call("POST", "/contract-documents", admin, {**base2, "effective_date": "2026-11-20"})[1].get("code")
    check_true("② 只改生效日被拒", code != 0, f"code={code}")
    check("② 原生效日未变", db(f"select effective_date::text from contract_documents where id={did2}"),
          "2026-10-15")

    # ③ 旧合同：内容完全相同 → 幂等命中（不能误伤）
    base3, did3 = mk("L3")
    check_true("③ 前置：成功模拟出旧合同", strip_fp(did3))
    _, res = call("POST", "/contract-documents", admin, dict(base3))
    d = res.get("data") or {}
    check_true("③ 内容相同 → 幂等命中、不误伤",
               res.get("code") == 0 and (d.get("id") == did3 or d.get("duplicated") is True),
               f"code={res.get('code')} id={d.get('id')}")
    check("③ 没多建一份",
          db(f"select count(*) from contract_documents where request_key='{base3['request_key']}' "
             f"and deleted_at is null"), "1")

    # ⑤ 空值边界：显式把字段改成 null 也必须拒（C4-06 补边界，2026-10-10 复核指出）
    #
    # 反例（复核复现）：旧合同到期日 2026-11-01，同一把请求编号、显式传
    # `expiry_date: null` → 从前**回放原合同**（200「返回的是同一份」），到期日仍是 11-01。
    # 根因：判据写的是 `payload.expiry_date is not None`，把"**没传这个字段**"
    # 和"**显式传了 null**"混成一件事。前者不该比（默认值反推请求内容会误判），
    # 后者是**一次真实的修改意图**、必须比。改成 `model_fields_set` 后才分得开。
    base5, did5 = mk("L5")
    check_true("⑤ 前置：成功模拟出旧合同", strip_fp(did5))
    code = call("POST", "/contract-documents", admin, {**base5, "expiry_date": None})[1].get("code")
    check_true("⑤ 显式传 expiry_date=null 被拒（从前会回放）", code != 0, f"code={code}")
    check("⑤ 原到期日未变", db(f"select expiry_date::text from contract_documents where id={did5}"),
          "2026-11-01")

    base6, did6 = mk("L6")
    check_true("⑤ 前置：成功模拟出旧合同", strip_fp(did6))
    code = call("POST", "/contract-documents", admin, {**base6, "effective_date": None})[1].get("code")
    check_true("⑤ 显式传 effective_date=null 被拒", code != 0, f"code={code}")

    # 对照①：不传这些字段的裸重放仍要幂等 —— 修空值边界不能把"没传"也判成冲突
    base7, did7 = mk("L7")
    check_true("⑤ 前置：成功模拟出旧合同", strip_fp(did7))
    _, res = call("POST", "/contract-documents", admin, dict(base7))
    d = res.get("data") or {}
    check_true("⑤ 对照：完整重放仍幂等（没把「没传」误判成冲突）",
               res.get("code") == 0 and (d.get("id") == did7 or d.get("duplicated") is True),
               f"code={res.get('code')}")

    # 对照②：原值本来就是 null + 显式传 null → 值相同，不该判冲突
    call("POST", "/contract-templates", admin,
         {"doc_type": "contract", "name": f"{MARK}-L8", "body": "客户 {{customer_name}}"})
    tid8 = int(db("select id from contract_templates order by id desc limit 1"))
    key8 = f"{MARK}-L8"
    base8 = {"template_id": tid8, "customer_id": 1, "title": f"{MARK}-L8", "request_key": key8}
    call("POST", "/contract-documents", admin, dict(base8))
    did8 = int(db(f"select id from contract_documents where request_key='{key8}' and deleted_at is null"))
    strip_fp(did8)
    _, res = call("POST", "/contract-documents", admin,
                  {**base8, "expiry_date": None, "effective_date": None})
    d = res.get("data") or {}
    check_true("⑤ 对照：原值本就是 null + 传 null → 回放（值相同不算冲突）",
               res.get("code") == 0 and (d.get("id") == did8 or d.get("duplicated") is True),
               f"code={res.get('code')}")

    # ⑥ 自动补全字段不能被误判成冲突（复核 2026-10-10 第二轮抓到的回归）
    #
    # 反例：原请求**只选订单**，`quote_id` / `quote_version_id` 传 null ——
    # 后端会由订单带出真实版本（`generate_document` 里那段"只选了订单、没指定版本：
    # 跟订单走"）。`title` 传 null 也会生成「销售合同-客户名」默认标题。
    # 于是**落库的是补全后的值**。旧合同只能拿请求现值比库里值，
    # 内容完全相同的幂等重试就会 `None != 补全后的版本 id` → **409 误拦**（实测）。
    #
    # 客户端**分不清**"传 null 让后端补全"与"不传"，所以这两个请求必须同等对待。
    # 修法：生成时把请求原值存进 `_request_echo`，比较走同一把尺子；
    # 更早的行（连回显都没有）退化成"跳过会被自动补全的字段"。
    # ⚠️ `contract_documents.request_key` 上有**唯一约束**，而 `MARK` 是固定前缀、
    # 不随轮次变 —— 上一轮留下的同名键会让这一轮的插入**静默失败**，
    # 随后按 request_key 查到的其实是**上一轮那份**（实测：跨轮误红，且报错
    # 指向"内容冲突"，看起来像业务缺陷、实际是夹具没清）。
    # 所以进这一段先把上一轮的残留按 key 前缀清掉。
    db(f"delete from contract_documents where request_key like '{MARK}-autofill-%'")

    # ⚠️ 夹具必须**自建且自洽**：以前这里用
    # `select id from sales_orders/quotes order by id desc limit 1` 去捡"最后一行"，
    # 结果捡到的是**别的段落留下的行** —— 库状态随运行轮次变化，第二轮就红了
    # （实测：第一轮过、第二轮 409）。按 mark 精确取自己刚建的那一条。
    # 自建客户 + 商机 + 报价（不用 seed 里的，避免依赖别的段落留没留数据）
    call("POST", "/customers", admin, {"name": f"{MARK}-自动补全客户"})
    cid6 = db(f"select id from customers where name = '{MARK}-自动补全客户' order by id desc limit 1")
    call("POST", "/opportunities", admin,
         {"title": f"{MARK}-自动补全商机", "customer_id": int(cid6), "expected_amount": 100})
    opp6 = db(f"select id from opportunities where title = '{MARK}-自动补全商机' order by id desc limit 1")
    call("POST", "/quotes", admin, {"opportunity_id": int(opp6)})
    qid6 = db(f"select id from quotes where opportunity_id={opp6} order by id desc limit 1")
    vid6 = db(f"select id from quote_versions where quote_id={qid6} order by id limit 1")
    call("POST", "/orders", admin, {
        "customer_id": int(cid6), "currency": "CNY",
        "items": [{"sku_id": 1, "quantity": 1, "unit_price": 100}]})
    oid6 = db(f"select id from sales_orders where customer_id={cid6} order by id desc limit 1")
    # 让订单"从某个报价版本转来"（那正是自动补全的依据）。
    # 两者必须指向**同一个报价**，否则生成时的自动补全与订单不一致。
    db(f"update sales_orders set quote_id={qid6}, quote_version_id={vid6} where id={oid6}")
    check("⑥ 前置：订单挂在自建报价上",
          db(f"select quote_id::text from sales_orders where id={oid6}"), str(qid6))

    call("POST", "/contract-templates", admin,
         {"doc_type": "contract", "name": f"{MARK}-自动补全模板", "body": "客户 {{customer_name}}"})
    tid6 = int(db("select id from contract_templates order by id desc limit 1"))

    key6 = f"{MARK}-autofill-1"
    base6 = {"template_id": tid6, "customer_id": int(cid6), "order_id": int(oid6),
             "quote_id": None, "quote_version_id": None, "title": None, "request_key": key6}
    call("POST", "/contract-documents", admin, dict(base6))
    did6 = int(db(f"select id from contract_documents where request_key='{key6}' and deleted_at is null"))
    check("⑥ 前置：报价/版本被自动补全",
          db(f"select quote_version_id is not null from contract_documents where id={did6}"), "t")
    check_true("⑥ 前置：已模拟旧合同（只去指纹，保留回显）", _strip_fp(did6))
    _, res = call("POST", "/contract-documents", admin, dict(base6))
    d = res.get("data") or {}
    check_true("⑥ null 让后端补全 → 相同重试必须回放（从前 409）",
               res.get("code") == 0 and (d.get("id") == did6 or d.get("duplicated") is True),
               f"code={res.get('code')}")

    # 更早的行：连回显都没有（本轮修复之前生成的）→ 跳过会被补全的字段
    key7 = f"{MARK}-autofill-2"
    base7 = {**base6, "request_key": key7}
    call("POST", "/contract-documents", admin, dict(base7))
    did7 = int(db(f"select id from contract_documents where request_key='{key7}' and deleted_at is null"))
    db(f"update contract_documents set filled_data = filled_data - '_request_fingerprint' "
       f"- '_request_echo' where id={did7}")
    _, res = call("POST", "/contract-documents", admin, dict(base7))
    d = res.get("data") or {}
    check_true("⑥ 无指纹也无回显的最老行 → 仍不能误拦",
               res.get("code") == 0 and (d.get("id") == did7 or d.get("duplicated") is True),
               f"code={res.get('code')}")

    # 对照：真实修改仍然要拒（修自动补全不能把真正的冲突也放过）
    _, res = call("POST", "/contract-documents", admin, {**base6, "title": f"{MARK}-换标题"})
    check_true("⑥ 对照：真改标题仍拒", res.get("code") != 0, f"code={res.get('code')}")

    # ④ 新合同（有指纹）：改日期同样要拒
    base4, did4 = mk("N1")
    check("④ 前置：新合同有指纹",
          db(f"select (filled_data ? '_request_fingerprint')::text from contract_documents where id={did4}"),
          "true")
    code = call("POST", "/contract-documents", admin, {**base4, "expiry_date": "2026-12-01"})[1].get("code")
    check_true("④ 改到期日被拒（指纹分支）", code != 0, f"code={code}")
    _, res = call("POST", "/contract-documents", admin, dict(base4))
    d = res.get("data") or {}
    check_true("④ 相同内容仍幂等", res.get("code") == 0 and (d.get("id") == did4 or d.get("duplicated") is True),
               f"code={res.get('code')}")

    # ⑥ 段自建的夹具：request_key 不体现在 title 上，**必须按 key 清**，
    # 否则跨轮撞唯一约束（这一段的客户叫 `{MARK}-自动补全客户`，
    # 而上面按 `title like` 的清理只覆盖合同标题）。
    db(f"delete from contract_documents where request_key like '{MARK}-autofill-%'")
    db(f"delete from contract_documents where title like '{MARK}-%'")
    db(f"delete from contract_templates where name like '{MARK}-%'")
    # ⑥ 那套客户/商机/报价/订单（挂在自建客户下）
    _c6 = f"(select id from customers where name like '{MARK}-自动补全%')"
    _o6 = f"(select id from opportunities where title like '{MARK}-自动补全%')"
    db(f"delete from quote_charges where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where opportunity_id in {_o6}))")
    db(f"delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where opportunity_id in {_o6}))")
    db(f"delete from quote_versions where quote_id in (select id from quotes where opportunity_id in {_o6})")
    db(f"delete from quotes where opportunity_id in {_o6}")
    db(f"delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in (select id from sales_orders where customer_id in {_c6}))")
    db(f"delete from order_shipment_batches where order_id in (select id from sales_orders where customer_id in {_c6})")
    db(f"delete from order_status_history where order_id in (select id from sales_orders where customer_id in {_c6})")
    db(f"delete from order_milestones where order_id in (select id from sales_orders where customer_id in {_c6})")
    db(f"delete from sales_order_items where order_id in (select id from sales_orders where customer_id in {_c6})")
    db(f"delete from sales_orders where customer_id in {_c6}")
    db(f"delete from opportunity_stage_history where opportunity_id in {_o6}")
    db(f"delete from opportunity_items where opportunity_id in {_o6}")
    db(f"delete from opportunities where title like '{MARK}-自动补全%'")
    db(f"delete from contacts where customer_id in {_c6}")
    db(f"delete from customers where name like '{MARK}-自动补全%'")


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    if res.get("code") != 0:
        print(f"登录失败：{status} {res.get('message')}")
        return 1
    admin = res["data"]["access_token"]

    sec_c401(admin)
    sec_quote_deleted_contact(admin)
    sec_c402(admin)
    sec_sku_images(admin)
    sec_c405_batch_qty(admin)
    sec_c406_legacy_fingerprint(admin)

    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）:")
        for name in failed:
            print("  -", name)
        return 1
    print(f"第四批返修：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
