"""第四批返修的反例断言（审查 C4-01 …）。

断言写的是**当时复现出来的那个反例**，不是"改完长什么样"——退回去改坏了一定会红。

用法：
    API_BASE=http://127.0.0.1:8010/api/v1 DB_NAME=crm_check_test_c4 \
    PYTHONPATH=. .venv/bin/python scripts/check_c4_round_repairs.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.getenv("API_BASE", "http://127.0.0.1:8010/api/v1")
DB = os.getenv("DB_NAME", "crm_check_test_c4")
MARK = "CHKC4"

#: 本地原生 PostgreSQL（crm_prod / crm_sales_agent 在 5432 上）
_PG = os.getenv("PSQL_BIN", "/opt/homebrew/opt/postgresql@15/bin/psql")
_PG_PORT = os.getenv("PG_PORT", "5432")
_USE_DOCKER: bool | None = None

passed = 0
failed: list[str] = []


def call(method, path, token=None, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + urllib.parse.quote(path, safe="/?&="), data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")
    except Exception as exc:
        return "TIMEOUT", {"message": str(exc)[:60]}


def _probe_docker() -> bool:
    global _USE_DOCKER
    if _USE_DOCKER is None:
        r = subprocess.run(["docker", "exec", "crm-postgres", "psql", "-U", "crm", "-d", DB,
                            "-tAc", "select 1"], capture_output=True, text=True, timeout=30)
        _USE_DOCKER = r.returncode == 0
    return _USE_DOCKER


def db(sql: str):
    """执行 SQL。**自动适配**：库在容器（5433）还是本机原生（5432）。"""
    if _probe_docker():
        r = subprocess.run(["docker", "exec", "crm-postgres", "psql", "-U", "crm", "-d", DB,
                            "-tAc", sql], capture_output=True, text=True)
    else:
        env = dict(os.environ, PGPASSWORD=os.getenv("PGPASSWORD", "crm123456"))
        r = subprocess.run([_PG, "-h", os.getenv("PG_HOST", "127.0.0.1"), "-p", _PG_PORT,
                            "-U", os.getenv("PG_USER", "crm"), "-d", DB, "-tAc", sql],
                           capture_output=True, text=True, env=env)
    if r.returncode != 0:
        return "SQLERR:" + r.stderr.strip().split("\n")[-1][:70]
    return r.stdout.strip()


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


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    if res.get("code") != 0:
        print(f"登录失败：{status} {res.get('message')}")
        return 1
    admin = res["data"]["access_token"]

    sec_c401(admin)
    sec_quote_deleted_contact(admin)

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
