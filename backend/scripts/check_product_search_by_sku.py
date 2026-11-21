"""产品中心：按 SKU 编码 / SKU 名称也能搜到产品（主人 2026-10-10 提的口径）。

## 为什么要这条

产品中心那页是**按产品**呈现的，但业务员手里拿到的往往是 SKU 编码
（`TP-1210-ST`）或 SKU 名（"田字塑料托盘 1200×1000 黑色"）。
从前 `build_product_stmt` 只匹配 产品名/产品线/品牌 —— 拿编码搜是**空结果**，
明明库里有。现在补上 SKU 维度。

## 断言写的是"容易改错的地方"

- 按 SKU 编码 / SKU 名称能搜到**所属产品**；
- **不能把产品查重**：一个产品下有 N 个匹配的 SKU 时，`total` 必须是 1 而不是 N，
  列表里产品也只能出现一次 —— 这正是"用 join 而不是 exists"会踩的坑；
- 「SKU 数」列仍显示**该产品的全部 SKU 数**，不是"只算匹配上的那几个"；
- 软删的 SKU 不算数（它已不在 SKU 列表里，拿它把产品搜出来会误导）；
- 原有的 产品名/产品线/品牌 三个维度**没退化**。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_ps \
    PYTHONPATH=. .venv/bin/python scripts/check_product_search_by_sku.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.getenv("API_BASE", "http://127.0.0.1:8000/api/v1")

# 夹具走项目引擎（吃 DATABASE_URL），过一次性库防呆
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _test_support import require_isolated_db  # noqa: E402

require_isolated_db()

from sqlalchemy import text  # noqa: E402

passed = 0
failed: list[str] = []

#: 固定前缀：本套件自己造、自己清，跑几遍都不留残留
MARK = "CHKPS"


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


async def seed() -> dict:
    """直接建夹具：1 个产品 + 3 个 SKU（外加 2 个别的产品干扰搜索结果）。

    产品中心本来就能建，但走接口要一堆字段校验；这里要验的是**搜索**，
    所以夹具走 SQL，干净直接。
    """
    from app.core.database import SessionLocal

    ids = {}
    async with SessionLocal() as s:
        # ⚠️ 顺序不能反：SKU 的编码是 `ZX-6040-BL` 这种**业务编码**，不以 MARK 开头，
        # 所以清理要**按 product_id 删 SKU**，不能按 `sku_code like 'CHKPS%'`
        # （那样一个都删不掉，随后删产品会被 `skus_product_id_fkey` 挡回来 —— 实测）。
        await s.execute(
            text(
                "delete from skus where product_id in "
                f"(select id from products where name like '{MARK}%')"
            )
        )
        await s.execute(text(f"delete from products where name like '{MARK}%'"))
        for tag, name, line, brand in (
            ("A", f"{MARK}-周转箱", "塑料制品", "宏远"),
            ("B", f"{MARK}-托盘", "仓储", "恒达"),
        ):
            pid = (
                await s.execute(
                    text(
                        "insert into products (name, product_line, category, brand, status, "
                        "created_at, updated_at) values (:n, :l, '包装', :b, 'active', now(), now()) "
                        "returning id"
                    ),
                    {"n": name, "l": line, "b": brand},
                )
            ).scalar_one()
            ids[tag] = int(pid)
        # A 产品下 3 个 SKU（其中 2 个名字里带同一个词，用来验"不会把产品查重"）
        for code, nm in (
            ("ZX-6040-BL", "折叠周转箱 600×400 蓝色"),
            ("ZX-6040-GY", "折叠周转箱 600×400 灰色"),
            ("ZX-9999-XX", None),  # 没名字的 SKU：按名字搜不到，按编码能搜到
        ):
            await s.execute(
                text(
                    "insert into skus (product_id, sku_code, name, specification, status, "
                    "created_at, updated_at) values (:p, :c, :n, '600×400×300mm', 'active', now(), now())"
                ),
                {"p": ids["A"], "c": code, "n": nm},
            )
        await s.commit()
    return ids


async def cleanup() -> None:
    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        await s.execute(
            text(
                "delete from skus where product_id in "
                f"(select id from products where name like '{MARK}%')"
            )
        )
        await s.execute(text(f"delete from products where name like '{MARK}%'"))
        await s.commit()


def search(token: str, keyword: str) -> tuple[int, list[tuple[int, str, int]]]:
    status, res = call(
        "GET",
        f"/products?page=1&page_size=50&keyword={urllib.parse.quote(keyword)}",
        token,
    )
    data = res.get("data") or {}
    items = [(p["id"], p["name"], p.get("sku_count")) for p in (data.get("items") or [])]
    return int(data.get("total") or 0), items


async def main() -> int:
    """⚠️ 全程**一个事件循环**：`app.core.database` 的 engine 把连接池绑在
    首次使用它的事件循环上，多次 `asyncio.run()` 会报
    `got Future attached to a different loop`（实测踩到）。
    所以这里只有一个 async 入口，`asyncio.run` 在文件末尾只调一次。
    """
    await seed()
    try:
        _, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
        admin = (res.get("data") or {}).get("access_token")
        if not admin:
            print("  登录失败:", res)
            return 1

        print("\n=== ① 按 SKU 编码搜到所属产品 ===")
        total, items = search(admin, "ZX-6040-BL")
        check("精确编码 → total", total, 1)
        check_true("命中 A 产品", bool(items) and items[0][1] == f"{MARK}-周转箱", items)

        print("\n=== ② 按 SKU 名称搜到所属产品 ===")
        total, items = search(admin, "折叠周转箱")
        check("按 SKU 名 → total", total, 1)
        check_true("命中 A 产品", bool(items) and items[0][1] == f"{MARK}-周转箱", items)

        print("\n=== ③ 不能被 SKU 数撑成重复行（join 会踩，exists 不会）===")
        # A 产品下 2 个 SKU 都含「折叠周转箱」
        total, items = search(admin, "折叠周转箱 600×400")
        check("2 个 SKU 匹配 → total 仍是 1", total, 1)
        check_true("列表里 A 产品只出现一次", len(items) == 1, items)

        print("\n=== ④ SKU 数仍显示全部（不是只算匹配上的）===")
        _, items = search(admin, "ZX-6040-BL")
        check("A 产品 sku_count（它有 3 个 SKU）", items[0][2], 3)

        print("\n=== ⑤ 没名字的 SKU：按编码搜得到 ===")
        total, items = search(admin, "ZX-9999")
        check("无名字 SKU 的编码 → total", total, 1)
        check_true("命中 A 产品", bool(items) and items[0][1] == f"{MARK}-周转箱", items)

        print("\n=== ⑥ 原有三个维度没退化 ===")
        for kw, want in (
            (f"{MARK}-托盘", f"{MARK}-托盘"),
            ("塑料制品", f"{MARK}-周转箱"),
            ("恒达", f"{MARK}-托盘"),
        ):
            total, items = search(admin, kw)
            check_true(f"按「{kw}」搜到", total >= 1 and any(i[1] == want for i in items), items)
        total, _ = search(admin, "绝对不存在的关键词ZZZ")
        check("无匹配时 total", total, 0)

        print("\n=== ⑦ 软删的 SKU 不该把产品搜出来 ===")
        from sqlalchemy import text as _t

        from app.core.database import SessionLocal

        async def _soft_delete(code: str, on: bool) -> None:
            async with SessionLocal() as s2:
                await s2.execute(
                    _t(
                        "update skus set deleted_at = "
                        + ("now()" if on else "null")
                        + " where sku_code = :c"
                    ),
                    {"c": code},
                )
                await s2.commit()

        await _soft_delete("ZX-9999-XX", True)
        total, _ = search(admin, "ZX-9999")
        check("软删 SKU 的编码搜不到产品", total, 0)
        total, items = search(admin, f"{MARK}-周转箱")
        check("但产品本身按名字仍搜得到", total, 1)
        await _soft_delete("ZX-9999-XX", False)
    finally:
        await cleanup()

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"产品中心按 SKU 搜索：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
