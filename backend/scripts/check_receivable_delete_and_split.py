#!/usr/bin/env python3
"""应收计划：删节点、按比例生成、三期以上补齐名称/到期日。

用法（一次性隔离库；库名要含 test）：
    API_BASE=http://127.0.0.1:8001/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_ar \
    PYTHONPATH=. .venv/bin/python scripts/check_receivable_delete_and_split.py

## 为什么写这一套

2026-10-13 主人卡在"删不掉自动生成的「全款」→ 「生成应收计划」按钮不出现 → 分期根本进不去"
（那个按钮的显示条件是**零应收**）。改动是给应收节点加「删除」入口，
并把生成弹窗从写死的 30/70 两期改成可编辑。

**这些改动全靠"顺序与前置条件"成立**，单测证明不了：

- 删掉全款之后，生成按钮**真的**会出现（判据在后端之外的界面条件里）；
- 有回款的节点**真的**删不掉（后端硬拦，不是前端藏起来就算数）；
- 三期以上：先生成、再用 PATCH 逐条补名称与到期日 ——
  **后端一点没改**，所以更要钉住"补完之后合计仍等于订单总额"。

## 判据来源

- 删除：`DELETE /receivables/{id}`（后端带锁、拦有回款的）
- 生成：`POST /orders/{id}/receivables/generate`（1～24 期、合计=1、每期>0、末期补差）
- 补齐：`PATCH /receivables/{id}`（`plan_name` / `due_date`）

## 反向验证（本套件就是那两条的落地）

1. 有回款的节点删除 → 必须被拒（不是"删了但没报错"）；
2. 比例合计 ≠ 1 → 必须被拒（90+20）；
并且两条都要求**库里状态没被改坏**（节点还在、没有多余应收）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

API = os.getenv("API_BASE", "http://127.0.0.1:8000/api/v1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _test_support  # noqa: E402

DB_NAME = _test_support.require_isolated_db()

from _db_helper import db  # noqa: E402

MARK = "CHKAR"
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


def receivables(token: str, order_id: int) -> list[dict]:
    _, res = call("GET", f"/receivables?order_id={order_id}&page_size=50", token)
    data = res.get("data")
    return (data.get("items") if isinstance(data, dict) else data) or []


def cleanup() -> None:
    """按外键顺序清干净（清不掉直接报错，别把残留留给守门套件）。"""
    # ⚠️ 订单号是**系统自动编号**（SO2026…），不带夹具前缀 ——
    # 按 order_no like 清会漏掉它们，最后卡在"客户被订单引用"上（实测踩过）。
    # 按客户关联清才对。
    cids = f"(select id from customers where name like '{MARK}%')"
    ids = f"(select id from sales_orders where customer_id in {cids})"
    qids = f"(select id from quotes where customer_id in {cids})"
    vids = f"(select id from quote_versions where quote_id in {qids})"
    for sql in (
        f"delete from payment_records where order_id in {ids}",
        f"delete from receivable_plans where order_id in {ids}",
        f"delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in {ids})",
        f"delete from order_shipment_batches where order_id in {ids}",
        f"delete from order_milestones where order_id in {ids}",
        f"delete from order_status_history where order_id in {ids}",
        f"delete from sales_order_items where order_id in {ids}",
        f"delete from sales_orders where id in {ids}",
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


def make_order(token: str) -> tuple[int, float]:
    """手工建单（POST /orders）——本套件要的是"订单"，不需要走报价链路。"""
    _, res = call("POST", "/customers", token, {"name": f"{MARK}-客户", "level": "C"})
    cid = res["data"]["id"]
    status, res = call(
        "POST",
        "/orders",
        token,
        {
            "customer_id": cid,
            "items": [{"sku_id": 1, "quantity": 10, "unit_price": 1000}],
            "remark": f"{MARK} 应收删改核对",
            "payment_terms": "款到发货",
        },
    )
    if res.get("code") != 0:
        raise AssertionError(f"建单失败：HTTP {status} {res.get('message')}")
    return int(res["data"]["order_id"]), float(res["data"]["total_amount"])


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1
    cleanup()
    order_id, total = make_order(admin)
    print(f"=== 夹具：订单 #{order_id}，总额 {total} ===")

    print("\n=== 1. 按自定义比例生成 30/70 ===")
    today = date.today()
    status, res = call(
        "POST",
        f"/orders/{order_id}/receivables/generate",
        admin,
        {
            "ratios": [0.3, 0.7],
            "first_due_date": str(today + timedelta(days=30)),
            "second_due_date": str(today + timedelta(days=60)),
            "first_name": "定金",
            "second_name": "尾款",
        },
    )
    check("生成成功", res.get("code"), 0)
    rows = receivables(admin, order_id)
    check("两期", len(rows), 2)
    check_true("名称按填的写", [r["plan_name"] for r in rows] == ["定金", "尾款"], str([r["plan_name"] for r in rows]))
    check_true(
        "各期合计 = 订单总额",
        abs(sum(float(r["amount"]) for r in rows) - total) < 0.01,
        f"{sum(float(r['amount']) for r in rows)} vs {total}",
    )
    check_true("两期到期日不同（各用各的槽位）", str(rows[0]["due_date"]) != str(rows[1]["due_date"]),
               f"{rows[0]['due_date']} / {rows[1]['due_date']}")

    print("\n=== 2. ★反向：比例合计 ≠ 1 必须被拒，且不改库 ===")
    before = len(rows)
    status, res = call(
        "POST",
        f"/orders/{order_id}/receivables/generate",
        admin,
        {"ratios": [0.9, 0.2], "first_due_date": str(today)},
    )
    check_true("90+20 被拒", res.get("code") != 0, f"HTTP {status} {str(res.get('message'))[:50]}")
    check("库里应收条数没变", len(receivables(admin, order_id)), before)

    print("\n=== 3. ★反向：有回款的节点删不掉 ===")
    target = rows[0]
    status, res = call(
        "POST",
        "/payments",
        admin,
        {
            "receivable_plan_id": target["id"],
            "received_amount": str(float(target["amount"]) / 2),
            "received_date": str(today),
            "payment_method": "银行转账",
        },
    )
    check("先登记一笔回款", res.get("code"), 0)
    status, res = call("DELETE", f"/receivables/{target['id']}", admin)
    check_true("有回款的删除被拒", res.get("code") != 0, f"HTTP {status} {str(res.get('message'))[:60]}")
    check_true("被拒后节点还在", any(r["id"] == target["id"] for r in receivables(admin, order_id)))

    print("\n=== 4. 没有回款的节点能删掉（这是解开死循环的那把钥匙）===")
    other = [r for r in receivables(admin, order_id) if r["id"] != target["id"]][0]
    status, res = call("DELETE", f"/receivables/{other['id']}", admin)
    check("删除成功", res.get("code"), 0)
    check("应收少了一条", len(receivables(admin, order_id)), before - 1)

    print("\n=== 5. 三期以上：先生成、再用 PATCH 补名称/到期日（后端零改动）===")
    # 清场：先删回款、再删节点（顺序反了会被"已有回款记录"挡住 —— 那正是第 3 条的判据）
    mid = db(f"select id from payment_records where order_id={order_id} limit 1")
    if mid and not str(mid).startswith("SQLERR"):
        db(f"delete from payment_records where order_id={order_id}")
    for row in receivables(admin, order_id):
        call("DELETE", f"/receivables/{row['id']}", admin)
    check("清场后应收归零", len(receivables(admin, order_id)), 0)

    status, res = call(
        "POST",
        f"/orders/{order_id}/receivables/generate",
        admin,
        {
            "ratios": [0.2, 0.3, 0.5],
            "first_due_date": str(today),
            "first_name": "首款",
            "second_name": "二期款",
        },
    )
    check("三期生成成功", res.get("code"), 0)
    rows = receivables(admin, order_id)
    check("三期", len(rows), 3)
    check_true(
        "第 3 期自动命名（后端只有两个名称槽位）",
        rows[2]["plan_name"].startswith("第 3"),
        rows[2]["plan_name"],
    )
    check_true(
        "第 3 期到期日回落到第一期（现状的局限，所以前端要补）",
        str(rows[2]["due_date"]) == str(rows[0]["due_date"]),
        f"{rows[2]['due_date']} vs {rows[0]['due_date']}",
    )
    third = rows[2]
    new_due = str(today + timedelta(days=120))
    status, res = call("PATCH", f"/receivables/{third['id']}", admin,
                       {"plan_name": "验收款", "due_date": new_due})
    check("PATCH 补名称/日期成功", res.get("code"), 0)
    rows = receivables(admin, order_id)
    fixed = [r for r in rows if r["id"] == third["id"]][0]
    check_true(
        "补完名称与日期正确",
        fixed["plan_name"] == "验收款" and str(fixed["due_date"]) == new_due,
        f"{fixed['plan_name']} / {fixed['due_date']}",
    )
    check_true(
        "★补完各期合计仍 = 订单总额",
        abs(sum(float(r["amount"]) for r in rows) - total) < 0.01,
        f"{sum(float(r['amount']) for r in rows)} vs {total}",
    )

    cleanup()
    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：删节点、按比例生成、三期补齐都对得上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
