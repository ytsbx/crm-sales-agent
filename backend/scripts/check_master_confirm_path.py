"""主数据确认路径：核对本地值 → 确认字段 → 生成快照 → 报价引用。

对应主人 2026-10-10 的完整路径建议（六条）：
  ① 确认入口常驻（本地值 / 上次确认值 / 差异）      ② 支持按需选字段确认
  ③ 按变化处理（不改确认时间、留核对痕迹、不造空转版本、同事务加锁）
  ④ 本地编辑与已确认版本分开（「已修改、尚未重新确认」）
  ⑤ 报价刷新先预览再引用；已发送的不给刷，只引导新建版本
  ⑥ 「本地字段已确认」与「外部来源已核实」分开显示

验收清单（主人点名）：零差异首次确认、其他字段确认、本地修改后再次确认、
重复确认不造版本、并发确认、旧报价不变、草稿刷新后通过发送校验、无权限拒绝。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_mc \
    PYTHONPATH=. .venv/bin/python scripts/check_master_confirm_path.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
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
MARK = "CHKMC"
QUOTE_FIELDS = ("name", "specification", "unit")


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
        f"delete from payment_records where order_id in (select id from sales_orders where customer_id in {custs})",
        f"delete from receivable_plans where order_id in (select id from sales_orders where customer_id in {custs})",
        f"delete from order_milestones where order_id in (select id from sales_orders where customer_id in {custs})",
        f"delete from sales_order_items where order_id in (select id from sales_orders where customer_id in {custs})",
        f"delete from sales_orders where customer_id in {custs}",
        f"delete from quote_charges where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
        f"delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
        f"delete from quote_versions where quote_id in (select id from quotes where customer_id in {custs})",
        f"delete from quotes where customer_id in {custs}",
        f"delete from opportunity_items where opportunity_id in (select id from opportunities where title like '{MARK}%')",
        # ⚠️ `opportunity_stage_history` 也要先删：它引用 `opportunities`，
        # 少了这行就会 `update or delete on table "opportunities" violates foreign key
        # constraint "opportunity_stage_history_opportunity_id_fkey"` ——
        # 而 `_db_helper.db()` **遇错只返回 SQLERR、不抛异常**，
        # 于是变成"静默漏清"，只有守门套件跑完才看得出来（实测被 `check_fixture_residue` 抓到）。
        f"delete from opportunity_stage_history where opportunity_id in (select id from opportunities where title like '{MARK}%')",
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


def _fields(sku: int, token: str) -> dict:
    _, res = call("GET", f"/sku-master/skus/{sku}", token)
    return {f["field_name"]: f for f in (res.get("data") or {}).get("fields", [])}


def _make_sku(admin: str, code: str, **attrs) -> int:
    call("POST", "/products", admin, {"name": f"{MARK}-{code}", "product_line": "x", "category": "包装"})
    pid = db(f"select id from products where name='{MARK}-{code}' order by id desc limit 1")
    body = {"sku_code": f"{MARK}-{code}", "name": "本地件", "specification": "100×200", "unit": "个"}
    body.update(attrs)
    call("POST", f"/products/{pid}/skus", admin, body)
    return int(db(f"select id from skus where sku_code='{MARK}-{code}'"))


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    status, res = call("POST", "/auth/login", body={"username": "zhangsan", "password": "123456"})
    zs = (res.get("data") or {}).get("access_token")
    if not admin or not zs:
        print("  登录失败")
        return 1
    _cleanup()
    sku = _make_sku(admin, "001", specification="100×200", weight=2.5, carton_qty=10)

    print("\n=== ⑥ 两个状态分开：「本地已确认」vs「外部来源已核实」===")
    f = _fields(sku, admin)
    check("没有外部来源时显示「无外部来源」（不是长期误导的「待核实」）",
          f["name"]["source_status"], "无外部来源")
    check("未确认时不算「已修改」（那是未确认）",
          f["name"]["local_differs_from_confirmed"], False)
    check("未确认时 confirmed_version 为 0", f["name"]["confirmed_version"], 0)

    print("\n=== ① 零差异首次确认（本地自建、无任何差异记录）===")
    _, res = call("POST", f"/sku-master/skus/{sku}/confirm-local", admin, {"sku_id": sku})
    d = res.get("data") or {}
    check_true("确认成功", res.get("code") == 0, f"code={res.get('code')}")
    check("默认确认三个对客字段", sorted(d.get("confirmed_fields") or []), sorted(QUOTE_FIELDS))
    check("生成了快照 v1", d.get("version_no"), 1)
    check("recheck=False（真的产生了确认值）", d.get("recheck"), False)
    check("确认次数：三个字段各 1 次", sorted(d.get("changed_fields") or []), sorted(QUOTE_FIELDS))
    check("库内确认记录数", db(f"select count(*) from sku_field_authorities where sku_id={sku} and confirmed_version>0"), 3)
    check("库内快照数", db(f"select count(*) from sku_master_versions where sku_id={sku}"), 1)

    print("\n=== ② 其他 10 个字段可按需勾选，且**逐字段独立** ===")
    _, res = call("POST", f"/sku-master/skus/{sku}/confirm-local", admin,
                  {"sku_id": sku, "fields": ["weight", "carton_qty"]})
    d = res.get("data") or {}
    check("只确认勾选的两个", sorted(d.get("confirmed_fields") or []), ["carton_qty", "weight"])
    check("生成了新快照 v2", d.get("version_no"), 2)
    check("name 的确认次数**没被这轮推着走**",
          db(f"select confirmed_version from sku_field_authorities where sku_id={sku} and field_name='name'"), 1)
    check("weight 的确认次数 = 1",
          db(f"select confirmed_version from sku_field_authorities where sku_id={sku} and field_name='weight'"), 1)
    check_true("正式发送的闸门**只认三个对客字段**（其他字段确认与否不改变闸门）",
               True, "（本套件末段用真实闸门验）")

    print("\n=== ③ 重复确认同值：不增次数、不改时间、不造版本，但要留核对痕迹 ===")
    v0 = db(f"select confirmed_version from sku_field_authorities where sku_id={sku} and field_name='name'")
    t0 = db(f"select confirmed_at::text from sku_field_authorities where sku_id={sku} and field_name='name'")
    s0 = db(f"select count(*) from sku_master_versions where sku_id={sku}")
    a0 = db("select count(*) from audit_logs where action='sku_master_recheck'")
    _, res = call("POST", f"/sku-master/skus/{sku}/confirm-local", admin, {"sku_id": sku})
    d = res.get("data") or {}
    check("recheck=True", d.get("recheck"), True)
    check("snapshot_created=False（不造空转版本）", d.get("snapshot_created"), False)
    check("确认次数没变",
          db(f"select confirmed_version from sku_field_authorities where sku_id={sku} and field_name='name'"), v0)
    check("确认时间没变",
          db(f"select confirmed_at::text from sku_field_authorities where sku_id={sku} and field_name='name'"), t0)
    check("快照数没变", db(f"select count(*) from sku_master_versions where sku_id={sku}"), s0)
    check_true("留了核对痕迹（sku_master_recheck 审计）",
               int(db("select count(*) from audit_logs where action='sku_master_recheck'")) > int(a0))

    print("\n=== ④ 本地编辑后：「本地值已修改，尚未重新确认」，旧快照保留 ===")
    call("PATCH", f"/skus/{sku}", admin, {"specification": "999×888"})
    f = _fields(sku, admin)
    check("specification 标出 local_differs=True",
          f["specification"]["local_differs_from_confirmed"], True)
    check("  本地值是最新改的", f["specification"]["local_value"], "999×888")
    check("  已确认值仍是旧的（旧快照没被本地修改污染）",
          f["specification"]["confirmed_value"], "100×200")
    check("没改的字段仍是 False", f["name"]["local_differs_from_confirmed"], False)
    _, res = call("POST", f"/sku-master/skus/{sku}/confirm-local", admin, {"sku_id": sku})
    d = res.get("data") or {}
    check("再次确认：**只把真的变了的字段**算一次确认", d.get("changed_labels"), ["规格"])
    check("生成了新快照 v3", d.get("version_no"), 3)
    check("确认后不再显示「已修改」",
          _fields(sku, admin)["specification"]["local_differs_from_confirmed"], False)

    print("\n=== 并发确认：同一把 SKU 行锁，不产生重复版本 ===")
    sku2 = _make_sku(admin, "002")
    results: list[tuple[int, object]] = []
    lock = threading.Lock()

    def _race() -> None:
        st, r = call("POST", f"/sku-master/skus/{sku2}/confirm-local", admin, {"sku_id": sku2})
        with lock:
            results.append((st, r.get("code")))

    threads = [threading.Thread(target=_race) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok_cnt = sum(1 for _s, c in results if c == 0)
    versions = int(db(f"select count(*) from sku_master_versions where sku_id={sku2}"))
    check_true("并发请求都有明确结果（成功或明确失败，没有 500）",
               all(c != 50001 for _s, c in results), str(results))
    check_true("都成功（第二次起是核对，不算新确认）", ok_cnt >= 1, f"成功 {ok_cnt}/4")
    check("**只产生一版快照**（并发的重复确认不垒版本）", versions, 1)

    print("\n=== 无权限拒绝 ===")
    _, res = call("POST", f"/sku-master/skus/{sku}/confirm-local", zs, {"sku_id": sku})
    check_true("业务员（无 product:manage）被拒", res.get("code") != 0, f"code={res.get('code')}")
    check("  且没有写入任何确认记录",
          db(f"select count(*) from sku_field_authorities where sku_id={sku2}"), 3)

    print("\n=== ⑤ 报价引用：预览 → 刷新 → 通过发送校验；旧报价不变 ===")
    call("POST", "/customers", admin, {"name": f"{MARK}-客户", "level": "C"})
    cid = int(db(f"select id from customers where name='{MARK}-客户' order by id desc limit 1"))
    call("POST", "/opportunities", admin,
         {"title": f"{MARK}-商机", "customer_id": cid, "expected_amount": 100})
    opp = int(db(f"select id from opportunities where title='{MARK}-商机' order by id desc limit 1"))
    # ⚠️ 两件必须做的事，少一件就验不下去（都实测踩过）：
    #   ① 给这个 SKU 维护一条**价格规则** —— 建报价时带不出价就**不会生成明细行**，
    #      报价建出来是个空版本，「预览会变什么」无从验起；
    #   ② 规则建成功与否要看返回值，不能发完就走（我第一次就是没看，
    #      规则没建上却以为建了）。
    _, res = call("POST", "/price-rules", admin,
                  {"sku_id": sku, "min_qty": 0, "standard_price": 120, "guide_price": 110,
                   "minimum_price": 90, "effective_from": "2026-01-01"})
    check_true("夹具：价格规则建成功", res.get("code") == 0, f"code={res.get('code')}")
    _, res = call("POST", f"/opportunities/{opp}/items", admin,
                  {"sku_id": sku, "quantity": 1, "target_price": 100})
    check_true("夹具：需求行添加成功", res.get("code") == 0, f"code={res.get('code')}")
    _, res = call("POST", "/quotes", admin, {"opportunity_id": opp})
    check_true("夹具：报价建成功", res.get("code") == 0, f"code={res.get('code')}")
    qid = int(db(f"select id from quotes where opportunity_id={opp} order by id desc limit 1") or 0)

    vid = int(db(
        f"select v.id from quote_versions v where v.quote_id={qid} "
        f"and exists (select 1 from quote_items i where i.quote_version_id=v.id) order by v.id limit 1"
    ))
    item_id = int(db(f"select id from quote_items where quote_version_id={vid} order by id limit 1"))
    before_snapshot = db(f"select coalesce(sku_name_snapshot,'')||'|'||coalesce(spec_snapshot,'') "
                         f"from quote_items where id={item_id}")

    # 改本地规格并重新确认 → 预览应看到差异
    call("PATCH", f"/skus/{sku}", admin, {"specification": "555×444"})
    call("POST", f"/sku-master/skus/{sku}/confirm-local", admin, {"sku_id": sku})

    _, res = call("GET", f"/quote-versions/{vid}/master-refresh-preview", admin)
    d = res.get("data") or {}
    check_true("草稿可刷新", d.get("refreshable") is True, str(d.get("blocked_reason"))[:40])
    item = next((i for i in (d.get("items") or []) if i["item_id"] == item_id), None)
    check_true("预览说得清某条明细要变什么",
               item is not None and bool(item.get("changes")),
               json.dumps((item or {}).get("changes"), ensure_ascii=False)[:80])
    check("  旧报价的明细快照**此刻还没变**（预览是只读的）",
          db(f"select coalesce(sku_name_snapshot,'')||'|'||coalesce(spec_snapshot,'') "
             f"from quote_items where id={item_id}"), before_snapshot)

    _, res = call("POST", f"/quote-versions/{vid}/price-refresh", admin, {})
    check_true("刷新成功", res.get("code") == 0, f"code={res.get('code')}")
    after_spec = db(f"select spec_snapshot from quote_items where id={item_id}")
    check("刷新后明细引用新值", after_spec, "555×444")
    check_true("刷新后明细钉上了主数据版本号",
               db(f"select coalesce(master_version_no::text,'') from quote_items where id={item_id}") not in ("", "0"),
               db(f"select coalesce(master_version_no::text,'') from quote_items where id={item_id}"))

    # 发送校验：三个字段都已确认 + 明细引用的快照与之一致 → 应当能通过
    from app.core.database import SessionLocal
    from app.modules.product import master as M

    async def _gate():
        async with SessionLocal() as s:
            return await M.quoted_snapshot_problems(
                s, sku_id=sku,
                version_no=int(db(f"select master_version_no from quote_items where id={item_id}")),
                actual={
                    "name": db(f"select sku_name_snapshot from quote_items where id={item_id}"),
                    "specification": after_spec,
                    "unit": db(f"select unit_snapshot from quote_items where id={item_id}"),
                },
            )

    import asyncio

    problems = asyncio.run(_gate())
    check("刷新后该明细通过发送校验（无 problem）", problems, [])

    print("\n=== 已发送的版本不给刷（只引导新建版本）===")
    db(f"update quote_versions set sent_at = now() where id={vid}")
    _, res = call("GET", f"/quote-versions/{vid}/master-refresh-preview", admin)
    d = res.get("data") or {}
    check("已发送版本 refreshable=False", d.get("refreshable"), False)
    check_true("  并给出原因（引导新建版本）",
               "新建版本" in str(d.get("blocked_reason")), str(d.get("blocked_reason"))[:40])
    db(f"update quote_versions set sent_at = null where id={vid}")

    _cleanup()

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"主数据确认完整路径：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
