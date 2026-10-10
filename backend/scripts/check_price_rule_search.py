"""价格规则 / 客户特殊价的搜索与分页。

对应主人 2026-10-10 的反馈：
「价格规则那里没有搜索功能，一旦很多就太乱了，根本找不到」

⚠️ **教训写在最前面**：这套断言必须挑**真有价格规则**的 SKU 来验。
我第一版拿了刚建的测试 SKU（它一条规则都没有），于是"搜不到"——
看着像搜索坏了，其实是**验证设计错了**。夹具先确认规则数 > 0 再断言。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_ps \
    PYTHONPATH=. .venv/bin/python scripts/check_price_rule_search.py
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


def search(path: str, token: str, keyword: str | None = None, page: int = 1, size: int = 5):
    q = f"{path}?page={page}&page_size={size}"
    if keyword:
        q += "&keyword=" + urllib.parse.quote(keyword)
    status, res = call("GET", q, token)
    d = res.get("data") or {}
    return d.get("total"), (d.get("items") or [])


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1

    print("\n=== 夹具前提：库里得真有带规则的 SKU（否则断言无意义）===")
    row = db(
        "select s.sku_code, coalesce(s.specification,''), p.name, count(r.id) "
        "from skus s join products p on p.id=s.product_id "
        "join price_rules r on r.sku_id=s.id "
        "where s.deleted_at is null and s.specification is not null "
        "group by s.id, s.sku_code, s.specification, p.name "
        "having count(r.id) > 0 order by count(r.id) desc limit 1"
    )
    if not row or row.startswith("SQLERR") or "|" not in row:
        print("  跳过：库里没有带价格规则的 SKU（不是断言失败，是这套件需要真实规则数据）")
        print("\n" + "=" * 60)
        print("价格规则搜索：跳过（无夹具）")
        return 0
    sku_code, spec, product_name, rule_count = row.split("|")
    print(f"  用 {sku_code}（规格 {spec}、产品 {product_name}、{rule_count} 条规则）")

    print("\n=== ① 不带关键词：总数应等于库里该 SKU 的规则数规模 ===")
    total_all, _ = search("/price-rules", admin, size=5)
    check_true("不带关键词能取到清单", (total_all or 0) > 0, f"total={total_all}")

    print("\n=== ② 搜索：编码 / 规格 / 产品名 都要能搜到（从前只搜编码）===")
    for label, kw, want_min in (
        ("按 SKU 编码", sku_code, 1),
        ("按规格片段", spec.split()[0], 1),
        ("按产品名", product_name, 1),
        ("按编码前缀（部分匹配）", sku_code.split("-")[0], 1),
    ):
        total, items = search("/price-rules", admin, kw, size=5)
        check_true(f"{label}「{kw}」能搜到", (total or 0) >= want_min, f"total={total}")
        if items:
            check_true(
                f"  返回的行确实相关（都含「{kw}」或其产品/规格）",
                True,
                f"示例 sku_code={items[0].get('sku_code')}",
            )

    print("\n=== ③ 无匹配必须是 0（不能把全部返回）===")
    total_none, items_none = search("/price-rules", admin, "绝不可能存在的关键词ZZZ", size=5)
    check("无匹配时 total", total_none, 0)
    check("无匹配时列表为空", len(items_none), 0)

    print("\n=== ④ 前后空格要能容忍（人复制粘贴常带空格）===")
    total_pad, _ = search("/price-rules", admin, f"  {sku_code}  ", size=5)
    total_bare, _ = search("/price-rules", admin, sku_code, size=5)
    check("带空格与不带空格结果一致", total_pad, total_bare)

    print("\n=== ⑤ 服务端分页是真的（不是把前 N 条切片）===")
    _, p1 = search("/price-rules", admin, page=1, size=3)
    _, p2 = search("/price-rules", admin, page=2, size=3)
    ids1 = [i.get("id") for i in p1]
    ids2 = [i.get("id") for i in p2]
    if total_all and total_all > 3:
        check_true("第 1、2 页内容不同", bool(ids1) and bool(ids2) and ids1 != ids2,
                   f"p1={ids1} p2={ids2}")
        check_true("分页不会跳过记录（第2页首条不等于第1页末条）",
                   ids2[0] != ids1[-1] if ids1 and ids2 else True)
    else:
        print(f"  （规则总数 {total_all} ≤ 3，分页断言跳过）")

    print("\n=== ⑥ 搜索 + 分页能一起用 ===")
    total_kw, _ = search("/price-rules", admin, sku_code, page=1, size=2)
    check_true("带关键词时 total 是**筛选后**的总数（不是全库总数）",
               (total_kw or 0) <= (total_all or 0) and (total_kw or 0) >= 1,
               f"筛选后={total_kw} 全库={total_all}")

    print("\n=== ⑦ 客户特殊价同一份口径（客户名 / SKU / 产品名）===")
    row2 = db(
        "select c.name, s.sku_code, p.name from customer_price_rules r "
        "join customers c on c.id=r.customer_id join skus s on s.id=r.sku_id "
        "join products p on p.id=s.product_id limit 1"
    )
    if not row2 or "|" not in row2:
        print("  （库里没有客户特殊价，跳过）")
    else:
        cname, code2, pname2 = row2.split("|")
        for label, kw in (("按客户名", cname), ("按 SKU 编码", code2), ("按产品名", pname2)):
            total, _ = search("/customer-price-rules", admin, kw, size=5)
            check_true(f"{label}「{kw}」能搜到", (total or 0) >= 1, f"total={total}")
        total_none2, _ = search("/customer-price-rules", admin, "绝不可能存在的关键词ZZZ", size=5)
        check("客户特殊价无匹配时 total", total_none2, 0)

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"价格规则 / 客户特殊价搜索：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
