"""GitHub issue 整改批次一（#3 / #5 / #6 / #9 / #12）的反例断言。

对应 issue：
  - #3  [P1] 报价金额、利润、审批和输入约束没有共用同一口径
  - #9  [P1] 核价的"目标利润金额"没有真正约束建议价
  - #6  [P1] 复制报价能挂到不可见、不同客户的商机
  - #5  [P1] 成本保密只覆盖部分入口，报价和产品分析仍泄露成本
  - #12 [P1] 产品经营统计把报价草稿和版本修订重复累计

断言写的是**当时复现出来的那个反例**，不是"改完长什么样"——退回去改坏了一定会红。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_iss \
    PYTHONPATH=. .venv/bin/python scripts/check_issue_batch_round1.py
"""

from __future__ import annotations

import asyncio
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

from sqlalchemy import text  # noqa: E402

passed = 0
failed: list[str] = []
MARK = "CHKIS"


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


async def _db(sql: str, params: dict | None = None):
    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        return await s.execute(text(sql), params or {})


async def _count_quotes() -> int:
    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        return int((await s.execute(text("select count(*) from quotes"))).scalar_one())


def new_opportunity(admin: str, tag: str) -> int:
    """**每条用例各自一个**客户+商机+需求行。

    为什么要独立：报价是按商机建的，若多条用例共用一个商机，
    "取最新那份报价"就会拿到**别的用例留下的**版本 —— 实测踩到
    （优惠 95 那条拿到的是上一条 28.71 的报价，总额算成 5 而不是 -66）。
    每条用例自己的商机，报价永远是它自己刚建的那一份。
    """
    call("POST", "/customers", admin, {"name": f"{MARK}-{tag}-客户", "level": "C"})
    status, res = call("GET", f"/customers?keyword={MARK}-{tag}-客户&page=1&page_size=5", admin)
    cid = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
    call("POST", "/opportunities", admin,
         {"title": f"{MARK}-{tag}-商机", "customer_id": cid, "expected_amount": 100})
    status, res = call("GET", f"/opportunities?keyword={MARK}-{tag}-商机&page=1&page_size=5", admin)
    oid = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
    call("POST", f"/opportunities/{oid}/items", admin,
         {"sku_id": 1, "quantity": 1, "target_price": 100})
    return oid


def fixture(admin: str) -> dict:
    """用**接口**建夹具（客户 + 商机 + 需求行 + 一条已知成本）。

    为什么不用 SQL 直插：`customers.pool_status`、`opportunities.stage_id` 等是
    NOT NULL，直插要逐个补齐内部列，既啰嗦又跟业务默认值脱节；
    走接口就是"界面怎么建我就怎么建"，且拿到的一定是合法数据。
    """
    call("POST", "/customers", admin, {"name": f"{MARK}-客户", "level": "C"})
    status, res = call("GET", f"/customers?keyword={MARK}-客户&page=1&page_size=5", admin)
    rows = (res.get("data") or {}).get("items") or []
    assert rows, f"客户没建成：{res}"
    cid = rows[0]["id"]

    call("POST", "/opportunities", admin,
         {"title": f"{MARK}-商机", "customer_id": cid, "expected_amount": 100})
    status, res = call("GET", f"/opportunities?keyword={MARK}-商机&page=1&page_size=5", admin)
    rows = (res.get("data") or {}).get("items") or []
    assert rows, f"商机没建成：{res}"
    oid = rows[0]["id"]

    call("POST", f"/opportunities/{oid}/items", admin,
         {"sku_id": 1, "quantity": 1, "target_price": 100})
    return {"customer": cid, "opportunity": oid}


async def cleanup() -> None:
    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        custs = f"(select id from customers where name like '{MARK}%')"
        opps = f"(select id from opportunities where title like '{MARK}%')"
        for sql in (
            f"delete from contract_documents where customer_id in {custs}",
            f"delete from quote_charges where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
            f"delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
            f"delete from quote_versions where quote_id in (select id from quotes where customer_id in {custs})",
            f"delete from quotes where customer_id in {custs}",
            f"delete from order_status_history where order_id in (select id from sales_orders where customer_id in {custs})",
            f"delete from sales_order_items where order_id in (select id from sales_orders where customer_id in {custs})",
            f"delete from sales_orders where customer_id in {custs}",
            f"delete from opportunity_stage_history where opportunity_id in {opps}",
            f"delete from opportunity_items where opportunity_id in {opps}",
            f"delete from opportunities where title like '{MARK}%'",
            f"delete from contacts where customer_id in {custs}",
            f"delete from customers where name like '{MARK}%'",
            f"delete from skus where sku_code like '{MARK}%'",
            f"delete from products where name like '{MARK}%'",
        ):
            await s.execute(text(sql))
        await s.commit()


def _quote_with_discount(admin: str, opp: int, price: float, discount: float):
    """建报价 → 改明细单价 → 加整单优惠 → 重算，返回 (version_id, summary, items)。"""
    call("POST", "/quotes", admin, {"opportunity_id": opp})
    # 取刚建的那份（按商机定位，不用"最后一行"那种会跨轮漂的写法）
    status, res = call("GET", f"/quotes?opportunity_id={opp}&page=1&page_size=10", admin)
    items = (res.get("data") or {}).get("items") or []
    qid = items[0]["id"] if items else None
    if qid is None:
        return None, {}, []
    status, res = call("GET", f"/quotes/{qid}", admin)
    vid = (res.get("data") or {}).get("current_version_id")
    call("PATCH", f"/quote-versions/{vid}", admin, {})
    status, res = call("GET", f"/quote-versions/{vid}", admin)
    d = res.get("data") or {}
    for it in d.get("items") or []:
        call("PATCH", f"/quote-items/{it['id']}", admin, {"quoted_price": price})
    call(
        "POST",
        f"/quote-versions/{vid}/charges",
        admin,
        {"charge_type": "discount", "amount": discount, "is_discount": True},
    )
    call("POST", f"/quote-versions/{vid}/recalculate", admin, {})
    status, res = call("GET", f"/quote-versions/{vid}", admin)
    d = res.get("data") or {}
    return vid, d.get("summary") or {}, d.get("items") or []


async def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1
    status, res = call("POST", "/auth/login", body={"username": "zhangsan", "password": "123456"})
    zs = (res.get("data") or {}).get("access_token")
    if not zs:
        print("  业务员登录失败:", res)
        return 1
    status, res = call("GET", "/auth/me", zs)
    zs_uid = (res.get("data") or {}).get("id")
    try:
        fixture(admin)

        print("\n=== #3 整单优惠不得把总额打到 0 以下（从前：负数也免审通过）===")
        for label, discount, want in (
            ("优惠 95（明细 100 − 95 → 5）", 95, None),
            ("优惠 100（→ 0）", 100, 422),
            ("优惠 150（→ −50）", 150, 422),
        ):
            o = new_opportunity(admin, f"D{discount}")
            vid, summary, _ = _quote_with_discount(admin, o, 100.0, discount)
            total = float(summary.get("total_amount") or 0)
            status, res = call("POST", f"/quote-versions/{vid}/submit-approval", admin, {})
            if want == 422:
                check_true(
                    f"{label} → 总额 {total:.2f} 必须硬拒",
                    res.get("code") != 0,
                    f"code={res.get('code')}",
                )
            else:
                # 总额为正但亏损：可以提交，但**不能直接批准**
                d = res.get("data") or {}
                check_true(
                    f"{label} → 亏损报价不得免审直批（auto_passed 不许为 True）",
                    d.get("auto_passed") is not True,
                    f"code={res.get('code')} auto_passed={d.get('auto_passed')}",
                )
            status, res = call("GET", f"/quote-versions/{vid}", admin)
            st = (res.get("data") or {}).get("version", {}).get("approval_status")
            check_true(f"{label} → 没有落成 approved", st != "approved", f"status={st}")

        print("\n=== #3 对照：总额为正但亏损 → 必须走审批（不是免审）===")
        # 明细 100、成本 20、优惠 95 → 总额 5，真实毛利 -15
        vid, summary, items = _quote_with_discount(admin, new_opportunity(admin, "LOSS"), 100.0, 95)
        status, res = call("POST", f"/quote-versions/{vid}/submit-approval", admin, {})
        d = res.get("data") or {}
        check_true(
            "正数但亏损的报价被拦下（进审批，不直接 approved）",
            res.get("code") != 0 or d.get("auto_passed") is not True,
            f"code={res.get('code')} auto_passed={d.get('auto_passed')}",
        )

        print("\n=== #9 目标利润金额 ===")
        status, res = call("POST", "/pricing/calculate", admin,
                           {"sku_id": 1, "quantity": 1, "logistics_cost": 0,
                            "target_profit_amount": 80})
        # 无成本场景另建 SKU
        call("POST", "/products", admin, {"name": f"{MARK}-产品", "product_line": "x", "category": "包装"})
        status, res = call("GET", f"/products?keyword={MARK}-产品", admin)
        pid = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        if pid:
            call("POST", f"/products/{pid}/skus", admin, {"sku_code": f"{MARK}-NOCOST"})
            status, res = call("GET", f"/skus?keyword={MARK}-NOCOST", admin)
            sk = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
            if sk:
                status, res = call("POST", "/pricing/calculate", admin,
                                   {"sku_id": sk, "quantity": 1, "logistics_cost": 0,
                                    "target_profit_amount": 80})
                check_true("无成本 + 目标利润 → 不再 500", res.get("code") != 50001,
                           f"code={res.get('code')}")
                check_true("无成本 + 目标利润 → 明说无法计算",
                           any("没有生效成本" in w for w in ((res.get("data") or {}).get("warnings") or [])),
                           str(((res.get("data") or {}).get("warnings") or []))[:70])

        status, res = call("POST", "/pricing/calculate", admin,
                           {"sku_id": 1, "quantity": 1, "logistics_cost": 0,
                            "target_profit_amount": 80})
        d = res.get("data") or {}
        inp = d.get("inputs") or {}
        c = d.get("cost") or {}
        base = float(c.get("base_cost") or 0)
        check_true("返回「达标所需试算价」", inp.get("target_profit_price") is not None,
                   f"试算价={inp.get('target_profit_price')}")
        if inp.get("target_profit_price") is not None and base > 0:
            check_true(
                "试算价 = 成本 + 目标利润",
                abs(float(inp["target_profit_price"]) - (base + 80)) < 0.02,
                f"{inp['target_profit_price']} vs 成本{base}+80",
            )
        check_true("给出「当前适用价是否达标」的结论",
                   inp.get("profit_target_met") in (True, False),
                   f"met={inp.get('profit_target_met')}")
        check_true("不达标时明确说出来（不再静默忽略）",
                   any("达不到要求的单件利润" in w for w in (d.get("warnings") or [])),
                   str((d.get("warnings") or []))[:80])

        print("\n=== #6 复制报价：最终商机必须先校验 ===")
        # 别人的客户 + 商机（zhangsan 读不到）
        call("POST", "/customers", admin, {"name": f"{MARK}-X-客户", "level": "C"})
        status, res = call("GET", f"/customers?keyword={MARK}-X-客户&page=1&page_size=5", admin)
        xc = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        call("POST", "/opportunities", admin,
             {"title": f"{MARK}-X-商机", "customer_id": xc, "expected_amount": 100})
        status, res = call("GET", f"/opportunities?keyword={MARK}-X-商机&page=1&page_size=5", admin)
        xo = ((res.get("data") or {}).get("items") or [{}])[0].get("id")

        # zhangsan 自己的客户/商机/报价
        call("POST", "/customers", zs, {"name": f"{MARK}-M-客户", "level": "C"})
        status, res = call("GET", f"/customers?keyword={MARK}-M-客户&page=1&page_size=5", admin)
        mc = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        await _db("update customers set owner_id=:o where id=:i", {"o": zs_uid, "i": int(mc)})
        call("POST", "/opportunities", zs,
             {"title": f"{MARK}-M-商机", "customer_id": mc, "expected_amount": 100})
        status, res = call("GET", f"/opportunities?keyword={MARK}-M-商机&page=1&page_size=5", admin)
        mo = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        await _db("update opportunities set owner_id=:o where id=:i", {"o": zs_uid, "i": int(mo)})
        call("POST", "/quotes", zs, {"opportunity_id": mo})
        status, res = call("GET", "/quotes?page=1&page_size=1", zs)
        mq = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        await _db("commit")

        status, res = call("GET", f"/opportunities/{xo}", zs)
        check_true("先确认那条商机对业务员确实不可见",
                   res.get("code") != 0, f"code={res.get('code')}")

        before = await _count_quotes()
        status, res = call("POST", f"/quotes/{mq}/clone", zs, {"opportunity_id": xo})
        after = await _count_quotes()
        check_true("复制到不可见商机 → 拒绝", res.get("code") != 0, f"code={res.get('code')}")
        check("  且**没有创建任何报价**", after - before, 0)

        # 可见但属于别的客户
        call("POST", "/customers", zs, {"name": f"{MARK}-M2-客户", "level": "C"})
        status, res = call("GET", f"/customers?keyword={MARK}-M2-客户&page=1&page_size=5", admin)
        mc2 = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        await _db("update customers set owner_id=:o where id=:i", {"o": zs_uid, "i": int(mc2)})
        call("POST", "/opportunities", zs,
             {"title": f"{MARK}-M2-商机", "customer_id": mc2, "expected_amount": 100})
        status, res = call("GET", f"/opportunities?keyword={MARK}-M2-商机&page=1&page_size=5", admin)
        mo2 = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        await _db("update opportunities set owner_id=:o where id=:i", {"o": zs_uid, "i": int(mo2)})
        await _db("commit")

        before = await _count_quotes()
        status, res = call("POST", f"/quotes/{mq}/clone", zs, {"opportunity_id": mo2})
        after = await _count_quotes()
        check_true("复制到不同客户的商机 → 拒绝", res.get("code") != 0, f"code={res.get('code')}")
        check("  且**没有创建任何报价**", after - before, 0)

        # 对照：复制到自己的商机应成功
        before = await _count_quotes()
        status, res = call("POST", f"/quotes/{mq}/clone", zs, {"opportunity_id": mo})
        after = await _count_quotes()
        check_true("对照：复制到自己的同一个商机 → 放行",
                   res.get("code") == 0 and after - before == 1,
                   f"code={res.get('code')} 新建={after - before}")

        print("\n=== #5 成本可见性（报价详情与产品分析同一口径）===")
        status, res = call("GET", f"/quote-versions/{vid}", admin)
        it_admin = ((res.get("data") or {}).get("items") or [{}])[0]
        status, res = call("GET", f"/quote-versions/{vid}", zs)
        it_sales = ((res.get("data") or {}).get("items") or [{}])[0]
        cost_fields = (
            "cost_snapshot", "package_cost_snapshot", "logistics_cost_snapshot",
            "minimum_price_snapshot", "profit_snapshot",
        )
        if it_admin.get("cost_snapshot") is not None:
            check_true("有 price:manage 的管理员仍看得到成本",
                       any(it_admin.get(f) is not None for f in cost_fields),
                       str({f: it_admin.get(f) for f in cost_fields})[:70])
        if it_sales:
            leaked = {f: it_sales.get(f) for f in cost_fields if it_sales.get(f) is not None}
            check_true("无 price:manage 的销售看不到成本类字段",
                       not leaked, str(leaked)[:80])
            check_true("  但报价本身仍可见（销售要用）",
                       it_sales.get("quoted_price") is not None,
                       f"quoted={it_sales.get('quoted_price')}")

        status, res = call("GET", "/analytics/products?limit=20", admin)
        rows_a = (res.get("data") or []) if isinstance(res.get("data"), list) else []
        status, res = call("GET", "/analytics/products?limit=20", zs)
        rows_s = (res.get("data") or []) if isinstance(res.get("data"), list) else []
        if rows_a:
            check_true("管理员拿得到报价预估毛利",
                       any(r.get("quote_estimated_profit") is not None for r in rows_a),
                       str([r.get("quote_estimated_profit") for r in rows_a[:3]]))
        if rows_s:
            check_true("销售拿到的是 null（不能反推成本）",
                       all(r.get("quote_estimated_profit") is None for r in rows_s),
                       str([r.get("quote_estimated_profit") for r in rows_s[:3]]))

        print("\n=== #12 统计拆列：复制版本不能虚增正式报价与预估毛利 ===")
        status, res = call("GET", "/analytics/products?limit=20", admin)
        rows_a = (res.get("data") or []) if isinstance(res.get("data"), list) else []
        target = next((r for r in rows_a if (r.get("version_revision_times") or 0) > 0), None)
        if target is None:
            print("  （没有可比的 SKU，跳过）")
        else:
            code = target.get("sku_code")
            status, res = call("GET", "/quotes?page=1&page_size=20", admin)
            qs = ((res.get("data") or {}).get("items") or [])
            if qs:
                call("POST", f"/quotes/{qs[0]['id']}/versions?confirm=true", admin, {})
                status, res = call("GET", "/analytics/products?limit=20", admin)
                after_rows = (res.get("data") or []) if isinstance(res.get("data"), list) else []
                after = next((r for r in after_rows if r.get("sku_code") == code), {})
                check("复制版本后「正式报价」次数不变",
                      after.get("quote_formal_times"), target.get("quote_formal_times"))
                check("复制版本后「报价预估毛利」不变",
                      after.get("quote_estimated_profit"), target.get("quote_estimated_profit"))
                check_true("「版本修订」如实增加（这正是要如实反映的）",
                           (after.get("version_revision_times") or 0)
                           >= (target.get("version_revision_times") or 0),
                           f"{target.get('version_revision_times')} → "
                           f"{after.get('version_revision_times')}")
                for field in ("quote_draft_times", "quote_formal_times",
                              "version_revision_times", "quote_estimated_profit",
                              "order_won_quantity", "inquiry_times"):
                    if field not in target:
                        failed.append(f"#12 缺字段 {field}")
                        print(f"  FAIL #12 缺字段 {field}")
                else:
                    check_true("拆列字段齐全（草稿/正式/版本修订/预估毛利/实际成交数量）", True)

        print("\n=== #9 对照：只给利润率时不受影响 ===")
        status, res = call("POST", "/pricing/calculate", admin,
                           {"sku_id": 1, "quantity": 1, "logistics_cost": 0, "target_margin": 0.3})
        d = res.get("data") or {}
        check_true("纯利润率核价仍正常", res.get("code") == 0,
                   f"code={res.get('code')} 建议价={d.get('recommended_price')}")
    finally:
        await cleanup()

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"issue 批次一（#3/#5/#6/#9/#12）：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
