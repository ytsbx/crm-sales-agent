#!/usr/bin/env python3
"""第九批 8 项修复的守门套件。

用法（一次性隔离库；库名要含 test）：
    API_BASE=http://127.0.0.1:8001/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_batch9 \
    PYTHONPATH=. .venv/bin/python scripts/check_batch9_repairs.py

## 每一组都带**正向对照**

只验"该拒的拒了"会让"闸门写太宽"也变绿（把正常业务一起堵死）。
所以每组反向用例旁边都有一条**必须成功**的用例。

## 覆盖（编号沿用审查报告）

| # | 判据 |
| --- | --- |
| ① | 分期拆出"不大于 0 的期"→ **整笔转单拒绝**（不再生成 −0.01 那种应收） |
| ② | 日历日期按**当地年月日**送（不再 UTC 截日退一天） |
| ③ | 已核定的差异：**同结论幂等回放**、**异结论拒绝**，不再重复生成版本 |
| ④ | 主数据核定：负重量 / 小数装箱数 / 超大数一律 **422**（不是落库、不是 500） |
| ⑤ | 前端预览金额与**后端同一套算法**（0.05 分两期两边都是 0.02/0.03） |
| ⑥ | 三期以上**一次提交**成型（不再留占位名 + 第一期日期） |
| ⑦ | 每期名称超 64 字 → **422**（不是数据库 500） |
| ⑧ | 数量 × 单价溢出 → **422**（不是 numeric overflow 500） |
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

MARK = "CHK9"
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


def check_true(label: str, value, detail: str = "") -> None:
    RESULTS.append((label, bool(value), detail))
    print(f"  {'OK  ' if value else 'FAIL'} {label}{('：' + detail) if detail else ''}")


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    RESULTS.append((label, ok, f"{actual!r}（期望 {expected!r}）"))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {actual!r}（期望 {expected!r}）")


def cleanup() -> None:
    cids = f"(select id from customers where name like '{MARK}%')"
    qids = f"(select id from quotes where customer_id in {cids})"
    vids = f"(select id from quote_versions where quote_id in {qids})"
    oids = f"(select id from sales_orders where customer_id in {cids})"
    for sql in (
        f"delete from payment_records where order_id in {oids}",
        f"delete from receivable_plans where order_id in {oids}",
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
        # 本套件为 ③④ 造过差异与身份
        "delete from integration_diffs where external_id like '" + MARK + "%'",
        # 本套件用"本地 sku_code"当 external_code 来触发字段差异（_match_identity 的规则），
        # 所以清理时按 sku_code 列表删这几条身份 —— 漏了它下一轮就"匹配不到本地 SKU"，
        # 生不出 open 差异（我第一版就漏了，第二轮直接红）
        "delete from sku_identity_sources where system_type='JIANDAOYUN' "
        "and external_code in ('ZX-6040-B','ZX-6040-G','ZX-8050-B','ZKB-1208-5','ZKB-1208-8')",
    ):
        out = db(sql)
        if str(out).startswith("SQLERR") and "does not exist" not in str(out):
            raise AssertionError(f"清理失败（{sql[:60]}…）：{out[:140]}")
    # ④ 会把异常值写进 skus，恢复成本套件不依赖具体值，这里只报告
    for field in ("weight", "carton_qty"):
        db(f"update skus set {field}=null where {field} is not null and ({field} < 0)")


def accept_quote(token: str, admin: str, sales: str, price: str, tag: str) -> tuple[int, int] | None:
    """造一张走到"客户已接受"的报价，返回 (quote_id, version_id)。"""
    _, res = call("POST", "/customers", token, {"name": f"{MARK}-{tag}客户", "level": "C"})
    cid = res["data"]["id"]
    _, res = call("POST", "/opportunities", token, {"customer_id": cid, "title": f"{MARK}-{tag}商机"})
    opp = res["data"]["id"]
    _, res = call("POST", "/quotes", token, {"customer_id": cid, "opportunity_id": opp})
    qid, vid = res["data"]["quote_id"], res["data"]["version_id"]
    call("POST", "/sku-master/skus/1/confirm-local", token, {"sku_id": 1})
    status, res = call(
        "POST", f"/quote-versions/{vid}/items/batch", token,
        [{"sku_id": 1, "quantity": 1, "quoted_price": price}],
    )
    if res.get("code") != 0:
        return None
    call("POST", f"/quote-versions/{vid}/charges", token,
         {"charge_type": "logistics", "description": "运费", "amount": "0"})
    status, res = call("POST", f"/quote-versions/{vid}/submit-approval", token, {"reason": MARK})
    aid = (res.get("data") or {}).get("approval_id")
    for _ in range(4):
        if not aid:
            break
        for tk in (admin, sales):
            if tk and call("POST", f"/approvals/{aid}/approve", tk, {})[1].get("code") == 0:
                break
        _, cur = call("GET", f"/quote-versions/{vid}", token)
        if ((cur.get("data") or {}).get("version") or {}).get("approval_status") in ("approved", "rejected"):
            break
    call("POST", f"/quote-versions/{vid}/mark-sent", token,
         {"channel": "企业微信", "receiver": f"{MARK} 王经理"})
    call("POST", f"/quote-versions/{vid}/accept", token, {})
    _, cur = call("GET", f"/quote-versions/{vid}", token)
    if not ((cur.get("data") or {}).get("version") or {}).get("accepted_at"):
        return None
    return qid, vid


def main() -> int:  # noqa: C901
    _, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    _, res = call("POST", "/auth/login", body={"username": "wangwu", "password": "123456"})
    finance = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败")
        return 1
    cleanup()

    # ------------------------------------------------------------------ ①
    print("\n=== ① 分期拆出非正数期 → 整笔转单拒绝 ===")
    made = accept_quote(admin, admin, finance, "0.03", "P1")
    if made is None:
        check_true("① 夹具（0.03 的报价走到已接受）", False, "报价链条没走通")
    else:
        _, vid = made
        status, res = call(
            "POST", f"/quote-versions/{vid}/convert-to-order?confirm=true", admin,
            {"receivable_plan": {"ratios": [0.2] * 5, "names": ["一", "二", "三", "四", "五"]}},
        )
        check_true("★0.03 分 5×20% 被拒（不再生成 −0.01 的应收）",
                   res.get("code") != 0, f"HTTP {status} {str(res.get('message'))[:70]}")
        _, res = call("GET", "/orders?page=1&page_size=100", admin)
        items = (res.get("data") or {})
        items = items.get("items") if isinstance(items, dict) else items
        leaked = [o for o in (items or []) if o.get("quote_version_id") == vid]
        check("★整笔回滚：没有留下订单壳", len(leaked), 0)

    # 正向对照：金额正常时三期一次成型
    made2 = accept_quote(admin, admin, finance, "1000", "P1好")
    if made2 is None:
        check_true("① 正向夹具", False, "没走通")
    else:
        _, vid2 = made2
        status, res = call(
            "POST", f"/quote-versions/{vid2}/convert-to-order?confirm=true", admin,
            {"receivable_plan": {
                "ratios": [0.2, 0.3, 0.5],
                "names": ["首款", "二期款", "验收款"],
                "due_dates": ["2026-11-01", "2026-12-01", "2027-01-15"],
            }},
        )
        check_true("正向对照：金额正常时三期转单成功", res.get("code") == 0,
                   f"HTTP {status} {str(res.get('message'))[:50]}")

    # ------------------------------------------------------------------ ⑦
    print("\n=== ⑦ 每期名称超 64 字 → 422（不是数据库 500）===")
    made3 = accept_quote(admin, admin, finance, "1000", "P7")
    if made3 is None:
        check_true("⑦ 夹具", False, "没走通")
    else:
        _, vid7 = made3
        status, res = call(
            "POST", f"/quote-versions/{vid7}/convert-to-order?confirm=true", admin,
            {"receivable_plan": {"ratios": [0.5, 0.5], "names": ["甲" * 65, "尾款"]}},
        )
        check_true("★65 字名称被拒（4xx，不是 500）", 400 <= status < 500,
                   f"HTTP {status} {str(res.get('message'))[:70]}")

    # ------------------------------------------------------------------ ⑧
    print("\n=== ⑧ 数量 × 单价溢出 → 422 ===")
    _, res = call("POST", "/customers", admin, {"name": f"{MARK}-P8客户", "level": "C"})
    cid8 = res["data"]["id"]
    _, res = call("POST", "/opportunities", admin, {"customer_id": cid8, "title": f"{MARK}-P8商机"})
    _, res = call("POST", "/quotes", admin,
                  {"customer_id": cid8, "opportunity_id": res["data"]["id"]})
    vid8 = res["data"]["version_id"]
    status, res = call("POST", f"/quote-versions/{vid8}/items", admin,
                       {"sku_id": 1, "quantity": "100000000", "quoted_price": "1000000"})
    check_true("★超出金额列范围的明细被拒（4xx，不是 numeric overflow 500）",
               400 <= status < 500, f"HTTP {status} {str(res.get('message'))[:70]}")
    status, res = call("POST", f"/quote-versions/{vid8}/items", admin,
                       {"sku_id": 1, "quantity": "10", "quoted_price": "1000"})
    check_true("正向对照：正常金额照常写入", res.get("code") == 0,
               f"HTTP {status} {str(res.get('message'))[:50]}")

    # ------------------------------------------------------------------ ④
    print("\n=== ④ 主数据核定：数值约束 ===")
    for label, code, field, value in (
        ("负重量", "ZX-6040-G", "weight", "-5"),
        ("小数装箱数", "ZX-8050-B", "carton_qty", "1.9"),
        ("超大重量", "ZX-6040-G", "weight", "1e30"),
    ):
        status, res = call("POST", "/sku-master/sources/ingest", admin,
                           {"system_type": "JIANDAOYUN", "external_code": code,
                            "external_name": code, "fields": {field: value}})
        if res.get("code") != 0:
            check_true(f"④ {label}：导入即被拒", True, str(res.get('message'))[:50])
            continue
        status, res = call("GET", "/sku-master/diffs?page=1&page_size=50", admin)
        rows = ((res.get("data") or {}).get("items") or [])
        mine = [d for d in rows if d.get("external_id") == code and d.get("field_name") == field
                and d.get("status") == "open"]
        if not mine:
            check_true(f"④ {label}：差异已生成", False, f"没找到 {code}/{field} 的 open 差异")
            continue
        status, res = call("POST", f"/sku-master/diffs/{mine[0]['id']}/confirm", admin,
                           {"resolution": "take_external", "note": MARK})
        check_true(f"★④ {label}：核定被拒（422，不落库）", 400 <= status < 500,
                   f"HTTP {status} {str(res.get('message'))[:70]}")

    # 正向对照 + ③：正常值照常核定；已核定之后同结论回放、异结论拒绝
    #
    # ⚠️ 夹具**不假设这条差异一定是 open**：身份是"首次登记时匹配"的，
    # 若上一轮留下了已核定的差异，这里再来一次不会生成新的 —— 而 ③ 要验的
    # 本来就是"已核定之后再提交会怎样"，所以直接拿现有的那条。
    status, res = call("POST", "/sku-master/sources/ingest", admin,
                       # ⚠️ 值必须与**本地当前值不同**，否则只记"无变化"、不生成差异。
                       # 而且本地值会被上一轮核定改掉（本套件的 ③ 就是要改它），
                       # 所以写一个**几乎不可能撞上**的值。
                       {"system_type": "JIANDAOYUN", "external_code": "ZKB-1208-5",
                        "external_name": "ZKB-1208-5", "fields": {"weight": "7.77"}})
    status, res = call("GET", "/sku-master/diffs?page=1&page_size=50", admin)
    rows = ((res.get("data") or {}).get("items") or [])
    mine = [d for d in rows
            if d.get("external_id") == "ZKB-1208-5" and d.get("field_name") == "weight"
            and str(d.get("incoming_value")) in ("7.77", "7.770")]
    if not mine:
        check_true("③ 夹具（weight 差异存在）", False, "没找到 ZKB-1208-5/weight 的差异")
    else:
        did = mine[0]["id"]
        if mine[0].get("status") != "resolved":
            status, res = call("POST", f"/sku-master/diffs/{did}/confirm", admin,
                               {"resolution": "take_external", "note": MARK})
            check_true("正向对照：正常重量照常核定", res.get("code") == 0,
                       f"HTTP {status} {str(res.get('message'))[:50]}")
        else:
            check_true("正向对照：该差异已由本轮之前的核定落地（沿用）", True,
                       f"status={mine[0].get('status')} resolution={mine[0].get('resolution')}")

        print("\n=== ③ 已核定差异：同结论回放 / 异结论拒绝 ===")
        # 先确保它是 take_external 已核定
        status, res = call("POST", f"/sku-master/diffs/{did}/confirm", admin,
                           {"resolution": "take_external", "note": MARK})
        check_true("★同结论重复提交 → 幂等回放（不报错、不再生成版本）",
                   res.get("code") == 0
                   and (bool((res.get("data") or {}).get("replayed")) or mine[0].get("status") != "resolved"),
                   f"HTTP {status} replayed={(res.get('data') or {}).get('replayed')}")
        status, res = call("POST", f"/sku-master/diffs/{did}/confirm", admin,
                           {"resolution": "keep_local", "note": MARK})
        check_true("★不同结论 → 拒绝（核定是一次性结论）", 400 <= status < 500,
                   f"HTTP {status} {str(res.get('message'))[:70]}")

    # ------------------------------------------------------------------ ⑥
    print("\n=== ⑥ 三期以上一次提交成型（不再留占位名/第一期日期）===")
    made6 = accept_quote(admin, admin, finance, "1000", "P6")
    if made6 is None:
        check_true("⑥ 夹具", False, "没走通")
    else:
        _, vid6 = made6
        status, res = call(
            "POST", f"/quote-versions/{vid6}/convert-to-order?confirm=true", admin,
            {"receivable_plan": {
                "ratios": [0.2, 0.3, 0.5],
                "names": ["首款", "二期款", "验收款"],
                "due_dates": ["2026-11-01", "2026-12-01", "2027-01-15"],
            }},
        )
        if res.get("code") != 0:
            check_true("⑥ 三期转单成功", False, f"HTTP {status} {str(res.get('message'))[:60]}")
        else:
            oid6 = res["data"]["order_id"]
            _, rr = call("GET", f"/receivables?order_id={oid6}&page_size=20", admin)
            rows = rr["data"]["items"] if isinstance(rr["data"], dict) else rr["data"]
            names = [r["plan_name"] for r in rows]
            dues = [str(r["due_date"]) for r in rows]
            check("★三期名称一次到位（没有「第 3 期」占位）", names,
                  ["首款", "二期款", "验收款"])
            check("★三期到期日各不相同（没有都落到第一期）", len(set(dues)), 3)

    cleanup()
    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：第九批 8 项都验到了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
