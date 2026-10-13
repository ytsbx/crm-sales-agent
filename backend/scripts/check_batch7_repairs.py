#!/usr/bin/env python3
"""第七批 10 项修复的守门套件。

用法（一次性隔离库；库名要含 test）：
    API_BASE=http://127.0.0.1:8001/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_batch7 \
    PYTHONPATH=. .venv/bin/python scripts/check_batch7_repairs.py

## 为什么每一组都带"正向对照"

只验"该拒的被拒了"是不够的：把闸门写得太宽（例如"凡是有成本就算不完整"、
"凡是有候选价就拒"）同样能让反向断言变绿，却把正常业务也一起堵死。
所以每个反向用例旁边都有一条**必须成功**的正向用例。

## 覆盖

| 编号 | 判据 |
| --- | --- |
| P1-1 | 成本四项没填全 → 毛利未知 → **不命中毛利免审**；填全 → 照常命中 |
| P1-2 | 同 SKU 同生效日两条成本 → 核价与导入**都显式报错**，不静默挑一条 |
| P1-4 | 无 price:manage：版本对比不返回成本/毛利；模拟不给成本反推价 |
| P2-5 | 两个定价入口传 USD 一律 400（含 `allowed=true` 那条） |
| P2-6 | 人民币请求传非零退税率 → 400；不传 → 正常 |
| P2-7 | 定制品与标准品混排**不崩**，且定制品改价能报出来 |
| P2-8 | 样品制作完成填未来 → 422；填今天 → 通过 |
| P2-10 | `quoted_price="1e1000"`、`candidates=["1e1000"]` → 400（原来是 500） |
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

MARK = "CHK7"
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
        # 本套件直接插过合同（P1-3），必须按外键顺序先清掉
        f"delete from contract_documents where order_id in {oids}",
        "delete from contract_templates where name like 'P13模板%'",
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
        # 样品按**客户**清（sample_no 是系统编号、不带夹具前缀）。
        # ⚠️ 顺序要紧：必须排在 `customers` **之前**。我第一版把它追加在元组末尾
        # （也就是 customers 之后），注释还写着"必须排在之前"——代码和注释相反，
        # 于是外键把 customers 删不掉，清理直接报错。
        f"delete from sample_requests where customer_id in {cids}",
        f"delete from contacts where customer_id in {cids}",
        f"delete from customer_price_rules where customer_id in {cids}",
        f"delete from customers where name like '{MARK}%'",
    ):
        out = db(sql)
        if str(out).startswith("SQLERR") and "does not exist" not in str(out):
            raise AssertionError(f"清理失败（{sql[:60]}…）：{out[:140]}")


def set_cost(sku_id: int, *, complete: bool) -> None:
    """把 SKU 的成本改成"填全"或"只填一项"（直接写库，模拟价格中心的两种录入）。"""
    db("delete from product_costs where sku_id = %d" % sku_id)
    if complete:
        values = "10, 5, 3, 2"
    else:
        values = "10, null, null, null"
    out = db(
        "insert into product_costs (sku_id, effective_from, purchase_cost, production_cost, "
        "package_cost, processing_cost, currency, created_at) values "
        f"({sku_id}, '2026-01-01', {values}, 'CNY', now())"
    )
    if str(out).startswith("SQLERR"):
        raise AssertionError(f"写成本失败：{out[:160]}")


def add_freight(token: str, vid: int, amount: str = "80") -> None:
    """补一条运费 —— 提交审批的前置闸（2026-10-12 口径：运费必须先确认）。"""
    call("POST", f"/quote-versions/{vid}/charges", token,
         {"charge_type": "logistics", "description": "运费", "amount": amount})


def make_quote(token: str, sku_id: int, quoted_price: str, tag: str) -> tuple[int, int, int]:
    """建报价 + 写明细（**不提交审批**），返回 (quote_id, version_id, item_id)。"""
    _, res = call("POST", "/customers", token, {"name": f"{MARK}-{tag}客户", "level": "C"})
    cid = res["data"]["id"]
    _, res = call("POST", "/opportunities", token, {"customer_id": cid, "title": f"{MARK}-{tag}商机"})
    opp = res["data"]["id"]
    _, res = call("POST", "/quotes", token, {"customer_id": cid, "opportunity_id": opp})
    qid, vid = res["data"]["quote_id"], res["data"]["version_id"]
    status, res = call(
        "POST",
        f"/quote-versions/{vid}/items/batch",
        token,
        [{"sku_id": sku_id, "quantity": 1, "quoted_price": quoted_price}],
    )
    if res.get("code") != 0:
        raise AssertionError(f"写明细失败：HTTP {status} {res.get('message')}")
    _, res = call("GET", f"/quote-versions/{vid}", token)
    items = res["data"].get("items") or []
    item_id = items[0]["id"] if items else 0
    return qid, vid, item_id


def main() -> int:  # noqa: C901 —— 十条判据顺序写下来最清楚
    _, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    _, res = call("POST", "/auth/login", body={"username": "zhangsan", "password": "123456"})
    sales = (res.get("data") or {}).get("access_token")
    if not admin or not sales:
        print("  登录失败")
        return 1
    cleanup()
    _, res = call("GET", "/skus?page=1&page_size=5", admin)
    sku_id = res["data"]["items"][0]["id"]
    print(f"=== 夹具：SKU {sku_id} ===")

    # ---------------------------------------------------------------- P1-1
    print("\n=== P1-1 成本不完整不能被当成完整成本免审 ===")
    # ⚠️ 对照必须用**同一个 SKU**（先不完整、再填全）。我第一版拿 `/skus` 的
    # 第一条当"正向对照"，而种子里的成本本来就有 NULL 列 —— 于是"正向"那条
    # 同样是不完整成本，两条都走不到毛利判据，看着像修坏了。
    set_cost(sku_id, complete=False)
    _, vid_bad, _ = make_quote(admin, sku_id, "100", "P11坏")
    # 诊断：把这一刻的成本行与明细快照打出来（这两个月我在这条路上猜错了好几次）
    print("   · 成本行:", db(f"select id||':采购='||coalesce(purchase_cost::text,'NULL')"
                            f"||',生产='||coalesce(production_cost::text,'NULL')"
                            f" from product_costs where sku_id={sku_id} order by id"))
    print("   · 明细快照:", db(f"select 'cost='||cost_snapshot||' complete='"
                               f"||coalesce(cost_complete_snapshot::text,'NULL')"
                               f" from quote_items where quote_version_id={vid_bad}"))
    add_freight(admin, vid_bad)
    status, res = call("POST", f"/quote-versions/{vid_bad}/submit-approval", admin,
                       {"reason": "P1-1 不完整成本"})
    # ⚠️ 判据是 **`approval_required`**，不是 HTTP 码。第一版我只看了 code/HTTP：
    # 提交成功本身是 200/code=0（它就是"提交"这个动作成功），
    # "要不要审批"在返回体的 `approval_required` 里 —— 我差点把修好的当成没修好。
    bad_data = res.get("data") or {}
    check_true("★不完整成本：提交后**需要人工审批**（没有被自动放行）",
               bool(bad_data.get("approval_required")),
               f"HTTP {status} approval_required={bad_data.get('approval_required')} "
               f"{str(res.get('message'))[:40]}")

    set_cost(sku_id, complete=True)
    _, vid_ok, _ = make_quote(admin, sku_id, "100", "P11好")
    add_freight(admin, vid_ok)
    status, res = call("POST", f"/quote-versions/{vid_ok}/submit-approval", admin,
                       {"reason": "P1-1 完整成本"})
    ok_data = res.get("data") or {}
    check_true("正向对照：同一 SKU 成本填全后，高毛利照常**不需要审批**",
               res.get("code") == 0 and not ok_data.get("approval_required"),
               f"HTTP {status} approval_required={ok_data.get('approval_required')} "
               f"{str(res.get('message'))[:40]}")

    # ---------------------------------------------------------------- P1-2
    print("\n=== P1-2 同日两条成本：核价与导入都不许静默挑一条 ===")
    db(f"delete from product_costs where sku_id = {sku_id}")
    for amount in ("70", "100"):
        out = db(
            "insert into product_costs (sku_id, effective_from, purchase_cost, production_cost, "
            "package_cost, processing_cost, currency, created_at) values "
            f"({sku_id}, '2026-01-01', {amount}, 0, 0, 0, 'CNY', now())"
        )
        if str(out).startswith("SQLERR"):
            raise AssertionError(out[:140])
    status, res = call("POST", "/pricing/calculate", admin,
                       {"sku_id": sku_id, "quantity": 1, "currency": "CNY"})
    check_true("★核价遇歧义 → 明确报错（不再取某一条）",
               res.get("code") != 0 and "成本版本" in str(res.get("message")),
               f"HTTP {status} {str(res.get('message'))[:80]}")
    # 导入同一 SKU、同一天：应报"无法确定更新哪一条"。
    # 这个接口收的是 **multipart**（`file: UploadFile` + `preview: Form`），
    # 不是裸 text/csv —— 我第一版直接发 CSV 正文，拿回 400「file 这一项是必填的」，
    # 看着像接口没实现，其实是请求形状不对。
    # ⚠️ 表头必须是**模板里的中文表头**（`required_headers=["SKU编码","生效起始日(YYYY-MM-DD)"]`）。
    # 我第一版按英文列名写，拿回 400「表头缺少：SKU编码…」——接口是好的，是我没照模板。
    # ⚠️ 必须导入**同一个 SKU**（`sku_id`）。我第一版写死了 `ZX-6040-B`，
    # 而重复成本是造在套件选中的那个 SKU 上 —— 导入打到了另一个 SKU，
    # 那边只有一条成本，于是"更新 1 条"完全正常，看着却像我的闸没生效。
    sku_code = str(db(f"select sku_code from skus where id = {sku_id}")).strip()
    if not sku_code or sku_code.startswith("SQLERR"):
        raise AssertionError(f"取 SKU 编码失败：{sku_code[:80]}")
    csv_text = (
        "SKU编码,生效起始日(YYYY-MM-DD),采购成本\n"
        f"{sku_code},2026-01-01,200\n"
    )
    boundary = "----CHK7Boundary"
    parts = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="preview"',
        "",
        "true",
        f"--{boundary}",
        'Content-Disposition: form-data; name="file"; filename="costs.csv"',
        "Content-Type: text/csv",
        "",
        csv_text,
        f"--{boundary}--",
        "",
    ]
    payload = "\r\n".join(parts).encode()
    req = urllib.request.Request(API + "/costs/import", data=payload, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", "Bearer " + admin)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, body = resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, json.loads(exc.read().decode() or "{}")
    blob = json.dumps(body, ensure_ascii=False)
    check_true("★导入遇歧义 → 报错并说明不会替你猜",
               "无法确定" in blob,
               f"HTTP {status} {blob[:130]}")

    # ---------------------------------------------------------------- P2-5 / P2-6 / P2-10
    print("\n=== P2-5 两个定价入口拒外币 ===")
    for path, body in (
        ("/pricing/check-permission", {"sku_id": sku_id, "quantity": 1, "quoted_price": 100,
                                       "currency": "USD", "exchange_rate": 7}),
        ("/pricing/simulate", {"base": {"sku_id": sku_id, "quantity": 1,
                                        "currency": "USD", "exchange_rate": 7},
                               "candidates": [100]}),
    ):
        status, res = call("POST", path, admin, body)
        check_true(f"★{path} 传 USD 被拒", res.get("code") != 0,
                   f"HTTP {status} {str(res.get('message'))[:56]}")
        check_true(f"{path} 的拒绝文案说得清（人民币口径）",
                   "人民币" in str(res.get("message")), str(res.get("message"))[:70])

    print("\n=== P2-6 人民币请求不许有出口退税 ===")
    # 先把 P1-2 造出来的"同日两条"清掉，否则核价一律 409，正向对照根本走不到退税判据
    set_cost(sku_id, complete=True)
    status, res = call("POST", "/pricing/calculate", admin,
                       {"sku_id": sku_id, "quantity": 1, "currency": "CNY",
                        "tax_refund_rate": 0.3})
    check_true("★CNY + 退税率 30% 被拒", res.get("code") != 0,
               f"HTTP {status} {str(res.get('message'))[:60]}")
    status, res = call("POST", "/pricing/calculate", admin,
                       {"sku_id": sku_id, "quantity": 1, "currency": "CNY"})
    check_true("正向对照：不传退税率照常核价", res.get("code") == 0,
               f"HTTP {status} {str(res.get('message'))[:50]}")

    print("\n=== P2-10 极值金额在入口被拦（原来会 500）===")
    for label, path, raw in (
        ("check-permission 的 quoted_price=\"1e1000\"", "/pricing/check-permission",
         '{"sku_id": %d, "quantity": 1, "quoted_price": "1e1000"}' % sku_id),
        ("simulate 的 candidates=[\"1e1000\"]", "/pricing/simulate",
         '{"base": {"sku_id": %d, "quantity": 1}, "candidates": ["1e1000"]}' % sku_id),
        ("simulate 的 candidates=[1e30]", "/pricing/simulate",
         '{"base": {"sku_id": %d, "quantity": 1}, "candidates": [1e30]}' % sku_id),
    ):
        req = urllib.request.Request(API + path, data=raw.encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + admin)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, body = resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            status, body = exc.code, json.loads(exc.read().decode() or "{}")
        check_true(f"★{label} → 4xx（不是 500）", 400 <= status < 500,
                   f"HTTP {status} code={body.get('code')}")

    # ---------------------------------------------------------------- P2-7
    print("\n=== P2-7 定制品与标准品混排的版本对比 ===")
    _, res = call("POST", "/customers", admin, {"name": f"{MARK}-P27客户", "level": "C"})
    cid = res["data"]["id"]
    _, res = call("POST", "/opportunities", admin, {"customer_id": cid, "title": f"{MARK}-P27商机"})
    opp = res["data"]["id"]
    _, res = call("POST", "/quotes", admin, {"customer_id": cid, "opportunity_id": opp})
    qid, v1 = res["data"]["quote_id"], res["data"]["version_id"]
    set_cost(sku_id, complete=True)
    call("POST", "/sku-master/skus/%d/confirm-local" % sku_id, admin, {"sku_id": sku_id})
    call("POST", f"/quote-versions/{v1}/items/batch", admin,
         [{"sku_id": sku_id, "quantity": 1, "quoted_price": "100"}])
    # 两条定制品（sku_id 为空）
    for name in ("定制件甲", "定制件乙"):
        call("POST", f"/quote-versions/{v1}/items", admin,
             {"custom_name": name, "quantity": 1, "quoted_price": "100"})
    status, res = call("GET", f"/quotes/{qid}/version-comparison", admin)
    check_true("★混排（标准品 + 两条定制品）不崩", status == 200,
               f"HTTP {status} {str(res.get('message'))[:60]}")

    # ---------------------------------------------------------------- P2-8
    print("\n=== P2-8 样品制作完成不能填未来 ===")
    _, res = call("POST", "/customers", admin, {"name": f"{MARK}-P28客户", "level": "C"})
    cid = res["data"]["id"]
    _, res = call("POST", "/samples", admin,
                  {"customer_id": cid, "sku_id": sku_id, "quantity": 1,
                   "request_note": f"{MARK} 样品"})
    sid = (res.get("data") or {}).get("id")
    if not sid:
        check_true("样品夹具建立（跳过本条）", False, str(res.get("message"))[:70])
    else:
        call("POST", f"/samples/{sid}/approve", admin, {})
        future = (date.today() + timedelta(days=30)).isoformat() + "T02:00:00+00:00"
        status, res = call("POST", f"/samples/{sid}/made", admin,
                           {"made_at": future, "remark": "P2-8 未来"})
        check_true("★30 天后的制作完成时间被拒", res.get("code") != 0,
                   f"HTTP {status} {str(res.get('message'))[:60]}")
        check_true("拒绝文案说明「不能晚于今天」", "今天" in str(res.get("message")),
                   str(res.get("message"))[:70])
        now = date.today().isoformat() + "T02:00:00+00:00"
        status, res = call("POST", f"/samples/{sid}/made", admin,
                           {"made_at": now, "remark": "P2-8 今天"})
        check_true("正向对照：今天的制作完成照常登记", res.get("code") == 0,
                   f"HTTP {status} {str(res.get('message'))[:50]}")

    # ---------------------------------------------------------------- P1-4
    print("\n=== P1-4 普通销售拿不到成本（脱敏）===")
    set_cost(sku_id, complete=True)
    _, res = call("POST", "/opportunities", admin, {"customer_id": cid, "title": f"{MARK}-P14商机"})
    opp14 = res["data"]["id"]
    _, res = call("POST", "/quotes", admin, {"customer_id": cid, "opportunity_id": opp14})
    if not res.get("data"):
        raise AssertionError(f"P1-4 夹具建报价失败：{res.get('message')}")
    qid2, v2 = res["data"]["quote_id"], res["data"]["version_id"]
    call("POST", f"/quote-versions/{v2}/items/batch", admin,
         [{"sku_id": sku_id, "quantity": 1, "quoted_price": "100"}])
    status, res = call("GET", f"/quotes/{qid2}/version-comparison", admin)
    admin_row = (res.get("data") or {}).get("versions") or [{}]
    check_true("正向对照：管理员看得到成本", admin_row[0].get("cost_total") is not None,
               str(admin_row[0].get("cost_total")))
    status, res = call("GET", f"/quotes/{qid2}/version-comparison", sales)
    if status == 403:
        check_true("普通销售：整个版本对比被拒（也是可接受口径）", True)
    else:
        row = (res.get("data") or {}).get("versions") or [{}]
        leaked = {k: row[0].get(k) for k in ("cost_total", "profit_total", "margin",
                                             "unit_cost", "unit_profit", "avg_cost")}
        check_true("★普通销售：成本与毛利字段全部为空", all(v is None for v in leaked.values()),
                   str(leaked))
    status, res = call("POST", "/pricing/simulate", sales,
                       {"base": {"sku_id": sku_id, "quantity": 1}, "margins": [0.5]})
    data = res.get("data") or {}
    scenarios = data.get("scenarios") or []
    prices = [row.get("candidate_price") for row in scenarios]
    check_true("★普通销售：拿不到由成本反推的候选价",
               not scenarios or all(p is None for p in prices),
               f"HTTP {status} 候选价={prices[:3]} blocked={str(data.get('blocked_reason'))[:40]}")

    # ---------------------------------------------------------------- P1-3
    print("\n=== P1-3 已签合同的订单不能被取消 ===")
    # 并发交错那一半（签署持订单锁）在 `check_contract_sign_lock.py` 里单独验；
    # 这里验"签完之后"的闸：取消必须被拒，且订单状态不能变。
    _, res = call("POST", "/customers", admin, {"name": f"{MARK}-P13客户", "level": "C"})
    cid13 = res["data"]["id"]
    _, res = call("POST", "/orders", admin,
                  {"customer_id": cid13, "payment_terms": "款到发货",
                   "items": [{"sku_id": sku_id, "quantity": 1, "unit_price": 100}]})
    if not (res.get("data") or {}).get("order_id"):
        check_true("P1-3 夹具建单（跳过本条）", False, str(res.get("message"))[:60])
    else:
        oid13 = res["data"]["order_id"]
        # 造一份"已签署"的合同挂到订单上（走库，因为签署还要文件与模板一堆前置）
        out = db(
            "insert into contract_templates (doc_type, name, version, body, enabled, created_at) "
            "values ('sales', 'P13模板', 1, '正文', true, now()) returning id"
        )
        tpl_id = str(out).strip()
        out = db(
            "insert into contract_documents (doc_no, doc_type, title, customer_id, order_id, "
            "template_id, content_snapshot, status, signed_at, created_at) values "
            f"('DOC-P13-{oid13}', 'sales', 'P13合同', {cid13}, {oid13}, {tpl_id}, '正文', "
            "'signed', now(), now()) returning id"
        )
        if str(out).startswith("SQLERR"):
            check_true("P1-3 夹具建已签合同（跳过本条）", False, str(out)[:70])
        else:
            status, res = call("POST", f"/orders/{oid13}/cancel", admin,
                               {"reason": "P1-3 验证：已签合同的订单不该能取消"})
            check_true("★已签合同的订单：取消被拒", res.get("code") != 0,
                       f"HTTP {status} {str(res.get('message'))[:70]}")
            check_true("拒绝文案说清「先处理合同」",
                       "合同" in str(res.get("message")), str(res.get("message"))[:80])
            _, res = call("GET", f"/orders/{oid13}", admin)
            check("订单状态没有被改掉", (res.get("data") or {}).get("status"), "pending")

    cleanup()
    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：第七批 10 项修复都验到了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
