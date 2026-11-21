#!/usr/bin/env python3
"""转单时的收款方式：不传=全款（兼容），传了=按比例分期。

用法（一次性隔离库；库名要含 test）：
    API_BASE=http://127.0.0.1:8001/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_conv \
    PYTHONPATH=. .venv/bin/python scripts/check_convert_with_payment_plan.py

## 为什么写这一套（2026-10-13 改动三）

从前转单**写死一条「全款」**，想拆定金/尾款得转完单再回订单页删掉重建
（主人：「转单时直接选付款方式」）。现在 `OrderFromQuote.receivable_plan`
可以让转单那一刻就定下收款计划。

这套东西有三处**单测证明不了**的风险，所以钉成接口套件：

1. **向后兼容**：老调用方不传 `receivable_plan` 时，必须还是一条「全款」
   —— 这条一旦坏了，所有既有入口（订单草稿、外部脚本）都会静默变成不建应收。
2. **金额对得上**：各期之和必须**严格等于订单总额**（末期补差额）。
   比例是用户手填的，四舍五入必然有零头，靠"比例打得刚好"是不行的。
3. **非法比例必须在参数阶段就拒**、且**不留订单壳**：
   如果先建了订单再在生成应收时炸，事务回滚与否就成了另一回事（这里实测过是回滚的，
   但要一直钉住）。

判据与 `POST /orders/{id}/receivables/generate` 共用同一个校验模型
（`ReceivableGenerate`），所以两处不会漂移。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.getenv("API_BASE", "http://127.0.0.1:8000/api/v1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _test_support  # noqa: E402

DB_NAME = _test_support.require_isolated_db()

from _db_helper import db  # noqa: E402

MARK = "CHKCV"
RESULTS: list[tuple[str, bool, str]] = []


def call(method: str, path: str, token: str | None = None, body=None):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode() if body is not None else None, method=method
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:
            return exc.code, {}


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    RESULTS.append((label, ok, f"{actual!r}（期望 {expected!r}）"))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {actual!r}（期望 {expected!r}）")


def check_true(label: str, value, detail: str = "") -> None:
    RESULTS.append((label, bool(value), detail))
    print(f"  {'OK  ' if value else 'FAIL'} {label}{('：' + detail) if detail else ''}")


def cleanup() -> None:
    cids = f"(select id from customers where name like '{MARK}%')"
    qids = f"(select id from quotes where customer_id in {cids})"
    vids = f"(select id from quote_versions where quote_id in {qids})"
    oids = f"(select id from sales_orders where customer_id in {cids})"
    for sql in (
        f"delete from payment_records where order_id in {oids}",
        f"delete from receivable_plans where order_id in {oids}",
        f"delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in {oids})",
        f"delete from order_shipment_batches where order_id in {oids}",
        f"delete from order_milestones where order_id in {oids}",
        f"delete from order_status_history where order_id in {oids}",
        f"delete from sales_order_items where order_id in {oids}",
        f"delete from sales_orders where id in {oids}",
        f"delete from quote_send_logs where quote_version_id in {vids}",
        f"delete from quote_charges where quote_version_id in {vids}",
        f"delete from quote_items where quote_version_id in {vids}",
        "delete from approval_records where approval_instance_id in (select id from approval_instances where business_type='quote_version' and business_id in "
        + vids
        + ")",
        "delete from approval_instances where business_type='quote_version' and business_id in " + vids,
        f"delete from quote_versions where quote_id in {qids}",
        f"delete from quotes where id in {qids}",
        f"delete from opportunity_items where opportunity_id in (select id from opportunities where title like '{MARK}%')",
        f"delete from opportunity_stage_history where opportunity_id in (select id from opportunities where title like '{MARK}%')",
        f"delete from opportunities where title like '{MARK}%'",
        f"delete from contacts where customer_id in {cids}",
        f"delete from customer_price_rules where customer_id in {cids}",
        f"delete from customers where name like '{MARK}%'",
    ):
        out = db(sql)
        if str(out).startswith("SQLERR"):
            raise AssertionError(f"清理失败（{sql[:70]}…）：{out[:160]}")


def accepted_version(token: str, tag: str) -> int:
    """造一张「已接受、可转单」的报价版本（转单要求：审批通过 + 已发送 + 客户已接受）。"""
    _, res = call("POST", "/customers", token, {"name": f"{MARK}-{tag}客户", "level": "C"})
    cid = res["data"]["id"]
    _, res = call("POST", "/opportunities", token, {"customer_id": cid, "title": f"{MARK}-{tag}商机"})
    opp = res["data"]["id"]
    _, res = call("POST", "/quotes", token, {"customer_id": cid, "opportunity_id": opp})
    vid = res["data"]["version_id"]
    # ⚠️ 顺序要紧：主数据必须先确认，**再**建明细（明细会快照当时的确认状态）
    call("POST", "/sku-master/skus/1/confirm-local", token, {"sku_id": 1})
    call("POST", f"/quote-versions/{vid}/items/batch", token,
         [{"sku_id": 1, "quantity": 10, "quoted_price": 1000, "unit_cost": 600}])
    # 运费闸：提交审批前必须先确认运费
    call("POST", f"/quote-versions/{vid}/charges", token,
         {"charge_type": "logistics", "description": "运费", "amount": "80"})
    call("POST", f"/quote-versions/{vid}/submit-approval", token, {"reason": f"{MARK} 转单核对"})
    call("POST", f"/quote-versions/{vid}/mark-sent", token,
         {"channel": "企业微信", "receiver": f"{MARK} 王经理"})
    call("POST", f"/quote-versions/{vid}/accept", token, {})
    return vid


def receivables(token: str, order_id: int) -> list[dict]:
    _, res = call("GET", f"/receivables?order_id={order_id}&page_size=50", token)
    data = res.get("data")
    return (data.get("items") if isinstance(data, dict) else data) or []


def main() -> int:
    _, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1
    cleanup()

    print("\n=== 1. ★向后兼容：不传收款计划 → 仍是一条「全款」===")
    vid = accepted_version(admin, "全款")
    status, res = call("POST", f"/quote-versions/{vid}/convert-to-order?confirm=true", admin, {})
    check("转单成功", res.get("code"), 0)
    oid = res["data"]["order_id"]
    _, res = call("GET", f"/orders/{oid}", admin)
    total = float(res["data"]["total_amount"])
    rows = receivables(admin, oid)
    check("只有一条应收", len(rows), 1)
    check("名称是「全款」", rows[0]["plan_name"], "全款")
    check_true("金额 = 订单总额", abs(float(rows[0]["amount"]) - total) < 0.01,
               f"{rows[0]['amount']} vs {total}")

    print("\n=== 2. 传分期计划 → 按计划建多条，名称/到期日各就各位 ===")
    vid2 = accepted_version(admin, "分期")
    plan = {
        "ratios": [0.3, 0.7],
        "names": ["定金", "尾款"],
        "due_dates": ["2026-11-20", "2026-12-20"],
    }
    status, res = call("POST", f"/quote-versions/{vid2}/convert-to-order", admin,
                       {"receivable_plan": plan})
    msg = str(res.get("message") or "")
    check_true("确认文案说清期数与名称",
               "将按你选的分期生成 2 条应收" in msg and "定金 30" in msg and "尾款 70" in msg,
               msg[:110])
    status, res = call("POST", f"/quote-versions/{vid2}/convert-to-order?confirm=true", admin,
                       {"receivable_plan": plan})
    check("转单成功", res.get("code"), 0)
    oid2 = res["data"]["order_id"]
    _, res = call("GET", f"/orders/{oid2}", admin)
    total2 = float(res["data"]["total_amount"])
    rows2 = receivables(admin, oid2)
    check("两条应收", len(rows2), 2)
    check("名称按传入的写", [r["plan_name"] for r in rows2], ["定金", "尾款"])
    check_true("到期日按传入的写", str(rows2[1]["due_date"]) == "2026-12-20", str(rows2[1]["due_date"]))
    check_true("★各期合计 = 订单总额",
               abs(sum(float(r["amount"]) for r in rows2) - total2) < 0.01,
               f"{sum(float(r['amount']) for r in rows2)} vs {total2}")

    print("\n=== 3. 三期：转单时就能分三期（后端零额外改动）===")
    vid3 = accepted_version(admin, "三期")
    plan3 = {
        "ratios": [0.2, 0.3, 0.5],
        "names": ["首款", "二期款", "验收款"],
        "due_dates": ["2026-11-01", "2026-12-01", "2027-01-15"],
    }
    status, res = call("POST", f"/quote-versions/{vid3}/convert-to-order?confirm=true", admin,
                       {"receivable_plan": plan3})
    check("转单成功", res.get("code"), 0)
    oid3 = res["data"]["order_id"]
    _, res = call("GET", f"/orders/{oid3}", admin)
    total3 = float(res["data"]["total_amount"])
    rows3 = receivables(admin, oid3)
    check("三条应收", len(rows3), 3)
    check_true("名称与到期日各就各位",
               [r["plan_name"] for r in rows3] == ["首款", "二期款", "验收款"]
               and str(rows3[2]["due_date"]) == "2027-01-15")
    check_true("各期合计 = 订单总额",
               abs(sum(float(r["amount"]) for r in rows3) - total3) < 0.01,
               f"{sum(float(r['amount']) for r in rows3)} vs {total3}")

    print("\n=== 4. ★反向：非法比例在参数阶段就拒，且不留订单壳 ===")
    vid4 = accepted_version(admin, "非法")
    for label, bad in (
        ("比例合计≠1（90+20）", {"ratios": [0.9, 0.2]}),
        ("有比例为 0", {"ratios": [1.0, 0.0]}),
        ("超过 24 期", {"ratios": [1 / 25] * 25}),
        ("名称个数对不上", {"ratios": [0.5, 0.5], "names": ["只给一个"]}),
    ):
        status, res = call("POST", f"/quote-versions/{vid4}/convert-to-order?confirm=true", admin,
                           {"receivable_plan": bad})
        check_true(f"{label} 被拒", res.get("code") != 0,
                   f"HTTP {status} {str(res.get('message'))[:50]}")
    _, res = call("GET", "/orders?page=1&page_size=100", admin)
    items = (res.get("data") or {})
    items = items.get("items") if isinstance(items, dict) else items
    leaked = [o for o in (items or []) if o.get("quote_version_id") == vid4]
    check("没有留下订单壳（整单回滚）", len(leaked), 0)

    cleanup()
    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：转单时的收款方式与金额都对得上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
