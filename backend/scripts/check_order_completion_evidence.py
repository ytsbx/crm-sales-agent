"""订单「已完成」必须有履约依据；回退要留理由（issue #10）。

审查实测的反例（我复现过）：
    待生产、**没有任何发货批次**的订单，直接 POST status=completed → 成功；
    再 POST status=in_production（已完成改回生产中）→ 也成功。
    发货概览同时显示 shipped=0、remaining=1、all_shipped=false。

根因在 `order.service.change_status`：判据写成 `if overview["batches"]:` ——
**没建批次就整段跳过**，于是绕过方式就是"什么都不建"。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_ord \
    PYTHONPATH=. .venv/bin/python scripts/check_order_completion_evidence.py
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
MARK = "CHKORD"


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
        f"delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in {orders})",
        f"delete from order_shipment_batches where order_id in {orders}",
        f"delete from order_status_history where order_id in {orders}",
        f"delete from order_milestones where order_id in {orders}",
        f"delete from sales_order_items where order_id in {orders}",
        f"delete from sales_orders where customer_id in {custs}",
        f"delete from customers where name like '{MARK}%'",
    ):
        db(sql)


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
          "items": [{"sku_id": sku, "quantity": 1, "unit_price": 200}]})
    oid = int(db(f"select id from sales_orders where customer_id={cid} order by id desc limit 1"))

    print("\n=== ① 复现的反例：零发货记录 → 不能完成 ===")
    summary = (call("GET", f"/orders/{oid}/shipments", admin)[1].get("data") or {}).get("summary") or {}
    check("前提：订单订了 1、实发 0、没有批次",
          (summary.get("ordered"), summary.get("shipped"), summary.get("batch_count")),
          (1.0, 0.0, 0))
    _, res = call("POST", f"/orders/{oid}/status", admin, {"status": "completed"})
    check("库内状态没变成 completed",
          db(f"select status from sales_orders where id={oid}"), "pending")
    check_true("被拒", res.get("code") != 0, f"code={res.get('code')}")
    check_true("  文案说清是「没有发货记录」", "发货记录" in str(res.get("message")),
               str(res.get("message"))[:56])

    print("\n=== ② 建了批次但没发出 → 也不能完成 ===")
    oit = int(db(f"select id from sales_order_items where order_id={oid} limit 1"))
    call("POST", f"/orders/{oid}/shipments", admin,
         {"items": [{"order_item_id": oit, "planned_qty": 1}]})
    batch = int(db(f"select id from order_shipment_batches where order_id={oid} order by id desc limit 1"))
    _, res = call("POST", f"/orders/{oid}/status", admin, {"status": "completed"})
    check("库内状态仍是 pending",
          db(f"select status from sales_orders where id={oid}"), "pending")
    check_true("  文案说清还差多少", "还差" in str(res.get("message")),
               str(res.get("message"))[:56])

    print("\n=== ③ 批次真的发出、发够了 → 可以完成 ===")
    _, res = call("POST", f"/orders/{oid}/shipments/{batch}/ship", admin, {})
    check_true("登记发货成功", res.get("code") == 0, f"code={res.get('code')}")
    summary = (call("GET", f"/orders/{oid}/shipments", admin)[1].get("data") or {}).get("summary") or {}
    check("发货概览：shipped=ordered、all_shipped",
          (summary.get("shipped"), summary.get("all_shipped")), (1.0, True))
    _, res = call("POST", f"/orders/{oid}/status", admin, {"status": "completed"})
    check("发够了可以完成", db(f"select status from sales_orders where id={oid}"), "completed")

    print("\n=== ④ 已完成回退必须写理由（纠错留痕）===")
    _, res = call("POST", f"/orders/{oid}/status", admin, {"status": "in_production"})
    check("不写理由：状态保持 completed",
          db(f"select status from sales_orders where id={oid}"), "completed")
    check_true("  文案要求填理由", "理由" in str(res.get("message")),
               str(res.get("message"))[:60])
    _, res = call("POST", f"/orders/{oid}/status", admin,
                  {"status": "in_production", "remark": "客户退回重做包装"})
    check("写了理由：可以回退", db(f"select status from sales_orders where id={oid}"), "in_production")
    check_true("  理由进了状态变更留痕",
               "退回重做包装" in db(
                   f"select coalesce(remark,'') from order_status_history "
                   f"where order_id={oid} order by id desc limit 1"
               ),
               "")

    print("\n=== ⑤ 非完成态之间的正常流转不受影响（不能把闸门做成全拦）===")
    _, res = call("POST", f"/orders/{oid}/status", admin, {"status": "shipped"})
    check_true("生产中→已发货 不需要理由", res.get("code") == 0, f"code={res.get('code')}")
    _, res = call("POST", f"/orders/{oid}/status", admin, {"status": "delivered"})
    check_true("已发货→已签收 不需要理由", res.get("code") == 0, f"code={res.get('code')}")

    print("\n=== ⑥ 已取消的订单仍然不能改状态（既有口径没被改坏）===")
    _, res = call("POST", f"/orders/{oid}/cancel", admin, {"reason": "套件：验证取消后不可改"})
    if res.get("code") == 0:
        _, res = call("POST", f"/orders/{oid}/status", admin, {"status": "in_production"})
        check_true("已取消订单改状态被拒", res.get("code") != 0, f"code={res.get('code')}")
    else:
        print(f"  （取消接口未成功，跳过：{str(res.get('message'))[:40]}）")

    _cleanup()

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"订单完成的履约依据：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
