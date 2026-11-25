"""停用/删除的 SKU 必须贯穿所有新业务入口（issue #7）。

审查实测的反例：SKU 停用之后 ——
    查价仍返回 `status=ok` 与有效适用价；
    仍能加进报价明细；
    手工建单也成功；
    软删后查价同样 200。
四处入口各自只判 `deleted_at`、谁都没看 `status`，所以"停售"对销售完全失效。

本套件断言的是**修好之后**的口径，并且守一条容易被改坏的边界：
**历史单据不受影响** —— 已发出去的报价、已建的订单照常能打开、能看。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_sku \
    PYTHONPATH=. .venv/bin/python scripts/check_sku_availability.py
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
MARK = "CHKSKU"


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
    opps = f"(select id from opportunities where title like '{MARK}%')"
    for sql in (
        f"delete from contract_documents where customer_id in {custs}",
        f"delete from quote_charges where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
        f"delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
        f"delete from quote_versions where quote_id in (select id from quotes where customer_id in {custs})",
        f"delete from quotes where customer_id in {custs}",
        f"delete from sales_order_items where order_id in (select id from sales_orders where customer_id in {custs})",
        f"delete from order_status_history where order_id in (select id from sales_orders where customer_id in {custs})",
        f"delete from order_milestones where order_id in (select id from sales_orders where customer_id in {custs})",
        f"delete from sales_orders where customer_id in {custs}",
        f"delete from opportunity_stage_history where opportunity_id in {opps}",
        f"delete from opportunity_items where opportunity_id in {opps}",
        f"delete from opportunities where title like '{MARK}%'",
        f"delete from contacts where customer_id in {custs}",
        f"delete from customers where name like '{MARK}%'",
    ):
        db(sql)


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1

    row = db("select id, sku_code from skus where deleted_at is null order by id limit 1")
    if not row or "|" not in row:
        print("  跳过：库里没有可用 SKU")
        return 0
    sku_raw, sku_code = row.split("|")
    sku = int(sku_raw)
    # 前置：确保它是启用的（上一轮可能把它停用了，套件必须自身可重复跑）
    call("POST", f"/skus/{sku}/enable", admin)
    _cleanup()

    print(f"\n=== 用 SKU {sku}（{sku_code}）===")
    print("\n=== ① 停用前：查价正常（否则后面的断言没有对照）===")
    _, res = call("GET", f"/pricing/lookup?customer_id=1&sku_id={sku}&quantity=1", admin)
    check_true("停用前查价放行", res.get("code") == 0, f"code={res.get('code')}")

    print("\n=== ② 停用后：五个新业务入口全部被拒 ===")
    call("POST", f"/skus/{sku}/disable", admin)
    check("库内 status", db(f"select status from skus where id={sku}"), "disabled")

    _, res = call("GET", f"/pricing/lookup?customer_id=1&sku_id={sku}&quantity=1", admin)
    check_true("查价被拒", res.get("code") != 0, f"code={res.get('code')}")
    check_true("  且原因是「已停用」而不是「不存在」",
               "停用" in str(res.get("message")), str(res.get("message"))[:44])

    call("POST", "/customers", admin, {"name": f"{MARK}-客户", "level": "C"})
    cid = db(f"select id from customers where name='{MARK}-客户' order by id desc limit 1")
    call("POST", "/opportunities", admin,
         {"title": f"{MARK}-商机", "customer_id": int(cid), "expected_amount": 100})
    opp = db(f"select id from opportunities where title='{MARK}-商机' order by id desc limit 1")

    _, res = call("POST", f"/opportunities/{opp}/items", admin,
                  {"sku_id": sku, "quantity": 1, "target_price": 100})
    check_true("加需求行被拒", res.get("code") != 0, f"code={res.get('code')}")

    _, res = call("POST", "/quotes", admin, {"opportunity_id": int(opp)})
    qid = db(f"select id from quotes where opportunity_id={opp} order by id desc limit 1")
    vid = db(f"select id from quote_versions where quote_id={qid} order by id limit 1")
    _, res = call("POST", f"/quote-versions/{vid}/items", admin,
                  {"sku_id": sku, "quantity": 1, "unit_price": 100})
    check_true("加入报价被拒", res.get("code") != 0, f"code={res.get('code')}")

    _, res = call("POST", "/orders", admin,
                  {"customer_id": int(cid), "currency": "CNY",
                   "items": [{"sku_id": sku, "quantity": 1, "unit_price": 100}]})
    check_true("手工建单被拒", res.get("code") != 0, f"code={res.get('code')}")

    _, res = call("POST", "/pricing/calculate", admin,
                  {"sku_id": sku, "quantity": 1, "logistics_cost": 0})
    check_true("核价被拒", res.get("code") != 0, f"code={res.get('code')}")

    print("\n=== ③ 软删后同样被拒（判据不只 status）===")
    db(f"update skus set status='active' where id={sku}")
    call("DELETE", f"/skus/{sku}", admin)
    _, res = call("GET", f"/pricing/lookup?customer_id=1&sku_id={sku}&quantity=1", admin)
    check_true("软删后查价被拒", res.get("code") != 0, f"code={res.get('code')}")
    check_true("  且原因是「已删除」", "删除" in str(res.get("message")),
               str(res.get("message"))[:40])
    db(f"update skus set deleted_at=null, status='active' where id={sku}")

    print("\n=== ④ 恢复启用后重新放行（停用必须是可逆的）===")
    call("POST", f"/skus/{sku}/enable", admin)
    _, res = call("GET", f"/pricing/lookup?customer_id=1&sku_id={sku}&quantity=1", admin)
    check_true("恢复后查价放行", res.get("code") == 0 and (res.get("data") or {}).get("status") == "ok",
               f"status={(res.get('data') or {}).get('status')}")
    _, res = call("POST", f"/quote-versions/{vid}/items", admin,
                  {"sku_id": sku, "quantity": 1, "unit_price": 100})
    check_true("恢复后能加入报价", res.get("code") == 0, f"code={res.get('code')}")

    print("\n=== ⑤ 历史单据不受影响（最容易改坏的边界）===")
    call("POST", f"/skus/{sku}/disable", admin)
    _, res = call("GET", f"/quote-versions/{vid}", admin)
    check_true("停用后，已建的报价详情仍能读", res.get("code") == 0, f"code={res.get('code')}")
    items = ((res.get("data") or {}).get("items") or [])
    check_true("  且明细还在（不是空白）", len(items) >= 1, f"{len(items)} 条")
    _, res = call("GET", f"/quotes/{qid}", admin)
    check_true("停用后，报价单本身仍能读", res.get("code") == 0, f"code={res.get('code')}")

    # 收尾：把 SKU 恢复启用（不污染后续套件）
    call("POST", f"/skus/{sku}/enable", admin)
    _cleanup()

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"停用/删除 SKU 贯穿新业务：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
