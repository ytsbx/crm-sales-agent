"""「确认了主数据，但报价接不上」的那座桥（主人 2026-10-10 指出的交互断层）。

问题是什么：确认动作在**产品**那边做，引用刷新在**报价**这边生效，
中间没有桥。黄条说"没有可引用的已确认主数据版本"，用户去确认完回来点
「刷新主数据」，**黄条还在** —— 因为刷新复用的是「重新核价」，
而它在下面两种情况下直接跳过整条明细：

    `item.price_source is None`   （手工定价）
    `lookup["status"] != "ok"`    （没有价格规则 / 规则被停用 / 查不到价）

于是**「接主数据」被「查不到价」挡住了**。这两件事本来就不该绑在一起：
主数据版本号回答的是"这一行对着哪一版名称/规格/单位"，与"这一行卖多少钱"无关。

修法：`POST /quote-versions/{id}/link-master` —— 只重建对客三字段 + 钉版本号，
**价格/成本/利润一个字不动**。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_br \
    PYTHONPATH=. .venv/bin/python scripts/check_master_link_bridge.py
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
MARK = "CHKBR"


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
    skus = f"(select id from skus where sku_code like '{MARK}%')"
    for sql in (
        f"delete from quote_charges where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
        f"delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
        f"delete from quote_versions where quote_id in (select id from quotes where customer_id in {custs})",
        f"delete from quotes where customer_id in {custs}",
        f"delete from opportunity_stage_history where opportunity_id in (select id from opportunities where title like '{MARK}%')",
        f"delete from opportunity_items where opportunity_id in (select id from opportunities where title like '{MARK}%')",
        f"delete from opportunities where title like '{MARK}%'",
        f"delete from contacts where customer_id in {custs}",
        f"delete from customers where name like '{MARK}%'",
        f"delete from sku_field_authorities where sku_id in {skus}",
        f"delete from sku_master_versions where sku_id in {skus}",
        f"delete from price_rules where sku_id in {skus}",
        f"delete from skus where sku_code like '{MARK}%'",
        f"delete from products where name like '{MARK}%'",
    ):
        db(sql)


def _make_quote(admin: str, sku: int) -> tuple[int, int, int]:
    """建客户/商机/需求/报价，返回 (报价, 版本, 明细)。"""
    call("POST", "/customers", admin, {"name": f"{MARK}-客户", "level": "C"})
    cid = int(db(f"select id from customers where name='{MARK}-客户' order by id desc limit 1"))
    call("POST", "/opportunities", admin,
         {"title": f"{MARK}-商机", "customer_id": cid, "expected_amount": 100})
    opp = int(db(f"select id from opportunities where title='{MARK}-商机' order by id desc limit 1"))
    call("POST", f"/opportunities/{opp}/items", admin,
         {"sku_id": sku, "quantity": 1, "target_price": 100})
    call("POST", "/quotes", admin, {"opportunity_id": opp})
    qid = int(db(f"select id from quotes where opportunity_id={opp} order by id desc limit 1"))
    _, res = call("GET", f"/quotes/{qid}", admin)
    vid = (res.get("data") or {}).get("current_version_id")
    iid = int(db(f"select id from quote_items where quote_version_id={vid} order by id desc limit 1") or 0)
    return qid, int(vid), iid


def _warnings(admin: str, vid: int) -> list[str]:
    _, res = call("GET", f"/quote-versions/{vid}", admin)
    return (res.get("data") or {}).get("master_warnings") or []


def _make_sku(admin: str, code: str) -> int:
    call("POST", "/products", admin,
         {"name": f"{MARK}-{code}", "product_line": "x", "category": "包装"})
    pid = db(f"select id from products where name='{MARK}-{code}' order by id desc limit 1")
    call("POST", f"/products/{pid}/skus", admin,
         {"sku_code": f"{MARK}-{code}", "name": "本地件", "specification": "100×200", "unit": "个"})
    return int(db(f"select id from skus where sku_code='{MARK}-{code}'"))


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1
    _cleanup()

    # 夹具：SKU 先有价格规则（这样报价明细能带价生成），主数据**没确认**
    sku = _make_sku(admin, "001")
    call("POST", "/price-rules", admin,
         {"sku_id": sku, "min_qty": 0, "standard_price": 120, "guide_price": 110,
          "minimum_price": 90, "effective_from": "2026-01-01"})
    _qid, vid, iid = _make_quote(admin, sku)
    check_true("夹具：报价明细建出来了", iid > 0, f"明细={iid}")
    check("生成时没有可引用的已确认版本（复现断层）",
          db(f"select coalesce(master_version_no::text,'NULL') from quote_items where id={iid}"), "NULL")
    check_true("黄条出现", bool(_warnings(admin, vid)), str(_warnings(admin, vid))[:60])

    print("\n=== ① 去产品中心确认后：能接上、黄条消失 ===")
    call("POST", f"/sku-master/skus/{sku}/confirm-local", admin, {"sku_id": sku})
    _, res = call("POST", f"/quote-versions/{vid}/link-master", admin, {})
    d = res.get("data") or {}
    check_true("接入成功", res.get("code") == 0, f"code={res.get('code')}")
    check("接上了 1 条", d.get("linked"), 1)
    check("没有缺确认的", d.get("missing_count"), 0)
    check_true("明细钉上了版本号",
               db(f"select coalesce(master_version_no::text,'NULL') from quote_items where id={iid}") != "NULL",
               db(f"select coalesce(master_version_no::text,'NULL') from quote_items where id={iid}"))
    check("黄条消失", _warnings(admin, vid), [])

    print("\n=== ② 关键：**查不到价**时也能接上（断层就在这里）===")
    # 停用价格规则 → 重新核价会跳过这条明细；接主数据不该被它挡住
    rid = db(f"select id from price_rules where sku_id={sku} order by id desc limit 1")
    call("PATCH", f"/price-rules/{rid}", admin, {"status": "disabled"})
    db(f"update quote_items set master_version_no=null where id={iid}")
    check_true("先把黄条造回来", bool(_warnings(admin, vid)), str(_warnings(admin, vid))[:50])
    _, res = call("POST", f"/quote-versions/{vid}/price-refresh", admin, {})
    d = res.get("data") or {}
    check_true("（对照）**刷新价格**确实会跳过它", d.get("skipped", 0) >= 1, f"{d}")
    check("（对照）所以刷新后版本号还是挂不上",
          db(f"select coalesce(master_version_no::text,'NULL') from quote_items where id={iid}"), "NULL")
    _, res = call("POST", f"/quote-versions/{vid}/link-master", admin, {})
    d = res.get("data") or {}
    check("接主数据不受影响，接上了", d.get("linked"), 1)
    check("黄条消失", _warnings(admin, vid), [])

    print("\n=== ③ 价格与成本快照**一个字不动**（它只管主数据）===")
    before = db(
        f"select coalesce(quoted_price::text,'-')||'|'||coalesce(cost_snapshot::text,'-')"
        f"||'|'||coalesce(profit_snapshot::text,'-') from quote_items where id={iid}"
    )
    db(f"update quote_items set master_version_no=null where id={iid}")
    _, res = call("POST", f"/quote-versions/{vid}/link-master", admin, {})
    after = db(
        f"select coalesce(quoted_price::text,'-')||'|'||coalesce(cost_snapshot::text,'-')"
        f"||'|'||coalesce(profit_snapshot::text,'-') from quote_items where id={iid}"
    )
    check("拟报价/成本/利润快照完全没变", after, before)

    print("\n=== ④ 缺确认时**明确报缺什么**（不静默跳过）===")
    sku2 = _make_sku(admin, "002")
    call("POST", "/price-rules", admin,
         {"sku_id": sku2, "min_qty": 0, "standard_price": 120, "guide_price": 110,
          "minimum_price": 90, "effective_from": "2026-01-01"})
    _q2, vid2, iid2 = _make_quote(admin, sku2)
    _, res = call("POST", f"/quote-versions/{vid2}/link-master", admin, {})
    d = res.get("data") or {}
    check("一条也没接上", d.get("linked"), 0)
    check("明确报了缺几条", d.get("missing_count"), 1)
    check_true("且列出缺哪些字段（**只有对客三字段**，不是全部 13 个）",
               sorted((d.get("missing") or [{}])[0].get("missing_labels") or [])
               == ["单位", "名称", "规格"],
               str((d.get("missing") or [{}])[0].get("missing_labels")))
    check_true("文案指路去产品中心",
               "产品中心" in str(d.get("message")), str(d.get("message"))[:60])

    print("\n=== ⑤ 幂等：已接上的再来一次不重复计数 ===")
    call("POST", f"/sku-master/skus/{sku}/confirm-local", admin, {"sku_id": sku})
    call("POST", f"/quote-versions/{vid}/link-master", admin, {})
    _, res = call("POST", f"/quote-versions/{vid}/link-master", admin, {})
    d = res.get("data") or {}
    check("第二次：linked=0 且 already_linked=1", (d.get("linked"), d.get("already_linked")), (0, 1))

    print("\n=== ⑥ 已发送 / 已审批的版本不给接（内容是对客承诺）===")
    db(f"update quote_versions set sent_at = now() where id={vid}")
    _, res = call("POST", f"/quote-versions/{vid}/link-master", admin, {})
    check_true("已发送被拒", res.get("code") != 0, f"code={res.get('code')}")
    check_true("  并引导新建版本", "新建版本" in str(res.get("message")),
               str(res.get("message"))[:50])
    db(f"update quote_versions set sent_at = null where id={vid}")

    print("\n=== ⑦ 无权限拒绝 ===")
    _, res = call("POST", "/auth/login", body={"username": "zhangsan", "password": "123456"})
    zs = (res.get("data") or {}).get("access_token")
    _, res = call("POST", f"/quote-versions/{vid}/link-master", zs, {})
    check_true("业务员（无 quote:manage）被拒", res.get("code") != 0, f"code={res.get('code')}")

    _cleanup()

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"主数据接入的桥：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
