"""跟单节点不能脱离真实发货/回款而手填完成（issue #11）。

审查实测的反例（我复现过）：
    普通销售把「首批发货」与「收款」的实际日期直接填上，节点立刻显示完成；
    而同一张订单**实发数量仍是 0、已确认到账也是 0**。
    主管看到全绿，货和钱其实都没发生。

「第 N 批发货」这类动态节点早就堵住了（只能由批次改），
漏的是固定节点里的 `first_shipment` / `deposit` / `payment`
—— 它们同样是真实事实的投影。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_ms \
    PYTHONPATH=. .venv/bin/python scripts/check_milestone_facts.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.getenv("API_BASE", "http://127.0.0.1:8000/api/v1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _test_support import require_isolated_db  # noqa: E402

require_isolated_db()

from _db_helper import db  # noqa: E402

passed = 0
failed: list[str] = []
MARK = "CHKMS"


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


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + urllib.parse.quote(path, safe="/?&=%"), data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def _cleanup() -> None:
    custs = f"(select id from customers where name like '{MARK}%')"
    orders = f"(select id from sales_orders where customer_id in {custs})"
    for sql in (
        f"delete from payment_records where order_id in {orders}",
        f"delete from receivable_plans where order_id in {orders}",
        f"delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in {orders})",
        f"delete from order_shipment_batches where order_id in {orders}",
        f"delete from order_milestones where order_id in {orders}",
        f"delete from order_status_history where order_id in {orders}",
        f"delete from sales_order_items where order_id in {orders}",
        f"delete from sales_orders where customer_id in {custs}",
        f"delete from customers where name like '{MARK}%'",
    ):
        db(sql)


def _milestone(order_id: int, node: str) -> dict:
    return {
        "id": int(db(f"select id from order_milestones where order_id={order_id} and node='{node}'") or 0),
        "actual": db(
            f"select coalesce(actual_date::text,'-') from order_milestones "
            f"where order_id={order_id} and node='{node}'"
        ),
        "evidence": db(
            f"select coalesce(evidence,'') from order_milestones "
            f"where order_id={order_id} and node='{node}'"
        ),
    }


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1
    _cleanup()

    row = db("select id from skus where deleted_at is null and status='active' order by id limit 1")
    if not row or row.startswith("SQLERR"):
        print("  跳过：库里没有可用 SKU")
        return 0
    sku = int(row)

    call("POST", "/customers", admin, {"name": f"{MARK}-客户", "level": "C"})
    cid = int(db(f"select id from customers where name='{MARK}-客户' order by id desc limit 1"))
    call("POST", "/orders", admin,
         {"customer_id": cid, "currency": "CNY",
          "items": [{"sku_id": sku, "quantity": 10, "unit_price": 100}]})
    oid = int(db(f"select id from sales_orders where customer_id={cid} order by id desc limit 1"))
    call("GET", f"/orders/{oid}/milestones", admin)  # 打开跟单 Tab 初始化节点

    print("\n=== ① 反例：派生节点不能手填实际日期 ===")
    for node, hint in (("first_shipment", "发货"), ("deposit", "收款"), ("payment", "收款")):
        ms = _milestone(oid, node)
        if not ms["id"]:
            print(f"  （没有 {node} 节点，跳过）")
            continue
        _, res = call("PATCH", f"/orders/{oid}/milestones/{ms['id']}", admin,
                      {"actual_date": "2026-10-10"})
        check(f"{node} 手填被拒且没落库", (res.get("code") != 0, _milestone(oid, node)["actual"]),
              (True, "-"))
        check_true(f"  文案指向「{hint}」这个正确入口", hint in str(res.get("message")),
                   str(res.get("message"))[:52])

    print("\n=== ② 反例成立的前提：真实事实确实都是 0 ===")
    summary = (call("GET", f"/orders/{oid}/shipments", admin)[1].get("data") or {}).get("summary") or {}
    confirmed = int(db(f"select count(*) from payment_records where order_id={oid} and status='confirmed'"))
    check("实发 0、无批次、已确认回款 0",
          (summary.get("shipped"), summary.get("batch_count"), confirmed), (0.0, 0, 0))

    print("\n=== ③ 派生节点的计划日仍可调（不能把整个节点锁死）===")
    ms = _milestone(oid, "first_shipment")
    if ms["id"]:
        _, res = call("PATCH", f"/orders/{oid}/milestones/{ms['id']}", admin,
                      {"planned_date": "2026-10-20"})
        check_true("计划日可以改", res.get("code") == 0, f"code={res.get('code')}")
        check("计划日落库",
              db(f"select planned_date::text from order_milestones where id={ms['id']}"),
              "2026-10-20")

    print("\n=== ④ 人工节点（签约）可以登记，但必须标明是人工声明 ===")
    ms = _milestone(oid, "contract")
    if ms["id"]:
        _, res = call("PATCH", f"/orders/{oid}/milestones/{ms['id']}", admin,
                      {"actual_date": "2026-10-10"})
        check_true("签约可以人工登记", res.get("code") == 0, f"code={res.get('code')}")
        check_true("  且标明是人工声明（不与已验证事实混为一类）",
                   "人工声明" in _milestone(oid, "contract")["evidence"],
                   _milestone(oid, "contract")["evidence"])

    print("\n=== ⑤ 真发货 → 首批发货节点自动完成 ===")
    oit = int(db(f"select id from sales_order_items where order_id={oid} limit 1"))
    call("POST", f"/orders/{oid}/shipments", admin,
         {"items": [{"order_item_id": oit, "planned_qty": 10}]})
    batch = int(db(f"select id from order_shipment_batches where order_id={oid} order by id desc limit 1"))
    _, res = call("POST", f"/orders/{oid}/shipments/{batch}/ship", admin, {})
    check_true("登记发货成功", res.get("code") == 0, f"code={res.get('code')}")
    check_true("首批发货节点自动填上实际日",
               _milestone(oid, "first_shipment")["actual"] != "-",
               _milestone(oid, "first_shipment")["actual"])

    print("\n=== ⑥ 确认收款 → 付定金 / 收款节点自动完成 ===")
    call("POST", f"/orders/{oid}/receivables", admin,
         {"plan_name": "全额", "amount": 1000, "due_date": "2026-11-01"})
    plan = int(db(f"select id from receivable_plans where order_id={oid} order by id desc limit 1"))
    call("POST", "/payments", admin,
         {"receivable_plan_id": plan, "received_amount": 1000, "received_date": "2026-10-09"})
    pid = int(db(f"select id from payment_records where order_id={oid} order by id desc limit 1"))
    _, res = call("POST", f"/payments/{pid}/confirm", admin, {})
    check_true("确认收款成功", res.get("code") == 0, f"code={res.get('code')}")
    check("付定金节点自动填上（最早到账日）", _milestone(oid, "deposit")["actual"], "2026-10-09")
    check("收款节点自动填上（收齐才标）", _milestone(oid, "payment")["actual"], "2026-10-09")

    print("\n=== ⑦ 没确认到账时不标完成（宁可不标，也不假完成）===")
    call("POST", "/customers", admin, {"name": f"{MARK}-客户2", "level": "C"})
    cid2 = int(db(f"select id from customers where name='{MARK}-客户2' order by id desc limit 1"))
    call("POST", "/orders", admin,
         {"customer_id": cid2, "currency": "CNY",
          "items": [{"sku_id": sku, "quantity": 1, "unit_price": 100}]})
    oid2 = int(db(f"select id from sales_orders where customer_id={cid2} order by id desc limit 1"))
    call("GET", f"/orders/{oid2}/milestones", admin)
    call("POST", f"/orders/{oid2}/receivables", admin,
         {"plan_name": "全额", "amount": 500, "due_date": "2026-11-01"})
    plan2 = int(db(f"select id from receivable_plans where order_id={oid2} order by id desc limit 1"))
    call("POST", "/payments", admin,
         {"receivable_plan_id": plan2, "received_amount": 500, "received_date": "2026-10-08"})
    check("只登记回款、未确认 → 收款节点仍是空的",
          _milestone(oid2, "payment")["actual"], "-")

    _cleanup()

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"跟单节点与真实事实：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
