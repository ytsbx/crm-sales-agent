"""回收站回归：只看被删的 + 把它捡回来（2026-10-07 第一版）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送与调度全关。

## 覆盖的口径

- **线索**：删掉后出现在回收站（且限数据范围）；恢复后回到线索列表。
- **产品**：删产品会连带软删它名下的 SKU；**恢复产品时那些 SKU 一起回来**
  （第一版口径：库里没记"哪些 SKU 是被产品连坐删的"，只能整体恢复）。
- **SKU**：产品还在回收站里时单独恢复被拒（400，提示先恢复产品）；
  产品恢复后 SKU 已随之回来，再点一次提示"无需恢复"。
- **客户**：只读。被合并掉的出现在回收站并带上 `merged_into`；直接删的没有。
  **没有恢复接口** —— 打过去必须 404/405（这条是"只做看"的硬约束）。
- **权限**：能看列表 ≠ 能恢复。业务员（有 `*:view`）列表 200、恢复 403。

跑法（需要后端在跑，且**不能用 8000**）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_recycle_bin.py
"""

import asyncio
import os
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import SessionLocal
from scripts.check_review_regressions import BASE, call, login

FAILURES: list[str] = []
PREFIX = "CHKRECYCLE" + uuid4().hex[:6]


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def items_of(body: dict) -> list:
    """取响应里的列表：分页接口在 `data.items`，纯列表接口 `data` 本身就是数组。"""
    data = body.get("data")
    if isinstance(data, dict):
        return data.get("items") or []
    return data or []


def ids_of(body: dict) -> set:
    return {row.get("id") for row in items_of(body)}


def row_of(body: dict, row_id: int) -> dict | None:
    return next((r for r in items_of(body) if r.get("id") == row_id), None)


async def cleanup() -> None:
    """清干净本套件写下的东西（客户及其关联 / 线索 / 产品 / SKU / 审计）。"""
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        for sql in (
            "delete from customer_merge_logs where source_customer_id in " + cust
            + " or target_customer_id in " + cust,
            "delete from contacts where customer_id in " + cust,
            "delete from customer_owner_history where customer_id in " + cust,
            "delete from customers where name like :p",
            "delete from skus where sku_code like :p",
            "delete from products where name like :p",
            "delete from lead_assignments where lead_id in "
            "(select id from leads where name like :p)",
            "delete from leads where name like :p",
            # 回收站的删除/恢复都会写审计，内容里带着夹具名，按内容兜底清
            "delete from audit_logs where business_type in ('lead', 'product', 'sku', 'customer')"
            " and (coalesce(before_data::text, '') like :m or coalesce(after_data::text, '') like :m)",
        ):
            await s.execute(text(sql), {"p": f"{PREFIX}%", "m": f"%{PREFIX}%"})
        await s.commit()


async def seed_fixtures() -> dict:
    """夹具：线索两条、产品三组、客户三个。

    "孤儿线索"的负责人故意设成一个**不存在的用户 id** —— 它不属于任何人的数据范围，
    于是"没数据范围的人看不到、管理员看得到"这条能被稳定验出来，
    不必依赖某个角色恰好是 self 还是 department。
    """
    from app.modules.customer.model import Customer
    from app.modules.lead.model import Lead
    from app.modules.product.model import Product, Sku
    from app.modules.user.model import User

    ids: dict = {}
    async with SessionLocal() as s:
        admin = (
            await s.execute(select(User).where(User.username == "admin"))
        ).scalars().first()
        if admin is None:
            raise SystemExit("库里没有 admin 账号，先跑 scripts/seed.py")
        ids["admin"] = admin.id

        mine = Lead(
            name=f"{PREFIX}我的线索", company_name=f"{PREFIX}公司",
            owner_id=admin.id, status="assigned", created_by=admin.id,
        )
        orphan = Lead(
            name=f"{PREFIX}孤儿线索", company_name=f"{PREFIX}公司",
            owner_id=999999, status="assigned", created_by=admin.id,
        )
        s.add_all([mine, orphan])
        await s.flush()
        ids["lead_mine"] = mine.id
        ids["lead_orphan"] = orphan.id

        # 产品 A：连同 2 个 SKU 一起删 → 恢复时 SKU 应一起回来
        product_a = Product(name=f"{PREFIX}产品A", created_by=admin.id)
        s.add(product_a)
        await s.flush()
        s.add_all([
            Sku(product_id=product_a.id, sku_code=f"{PREFIX}-A1", name="A1"),
            Sku(product_id=product_a.id, sku_code=f"{PREFIX}-A2", name="A2"),
        ])
        ids["product_a"] = product_a.id

        # 产品 B：只单独删它下面的一个 SKU
        product_b = Product(name=f"{PREFIX}产品B", created_by=admin.id)
        s.add(product_b)
        await s.flush()
        sku_b1 = Sku(product_id=product_b.id, sku_code=f"{PREFIX}-B1", name="B1")
        s.add(sku_b1)
        await s.flush()
        ids["product_b"] = product_b.id
        ids["sku_b1"] = sku_b1.id

        # 产品 C：删产品（连带删 SKU）后，单独恢复 SKU 应被拒
        product_c = Product(name=f"{PREFIX}产品C", created_by=admin.id)
        s.add(product_c)
        await s.flush()
        sku_c1 = Sku(product_id=product_c.id, sku_code=f"{PREFIX}-C1", name="C1")
        s.add(sku_c1)
        await s.flush()
        ids["product_c"] = product_c.id
        ids["sku_c1"] = sku_c1.id

        src = Customer(name=f"{PREFIX}合并来源", owner_id=admin.id, status="active",
                       pool_status="private")
        tgt = Customer(name=f"{PREFIX}合并目标", owner_id=admin.id, status="active",
                       pool_status="private")
        direct = Customer(name=f"{PREFIX}直接删除", owner_id=admin.id, status="active",
                          pool_status="private")
        s.add_all([src, tgt, direct])
        await s.flush()
        ids["cust_src"] = src.id
        ids["cust_tgt"] = tgt.id
        ids["cust_direct"] = direct.id

        await s.commit()
    return ids


async def main() -> int:
    import app.main  # noqa: F401  触发模型注册（不启调度器）
    _ = app.main

    loopback = {"127.0.0.1", "localhost", "::1"}
    db_url = urlparse(settings.database_url)
    assert urlparse(BASE).hostname in loopback, BASE
    assert db_url.hostname in loopback, db_url
    assert "test" in (db_url.path or "").lower() or os.getenv("CI", "").lower() == "true", db_url
    assert settings.dingtalk_push_off and settings.wecom_push_off
    assert not settings.scheduler_enabled and not settings.wecom_transfer_enabled

    ids = await seed_fixtures()
    print(f"夹具就绪：{ids}")

    try:
        admin = login("admin", "admin123")
        sales = login("zhangsan", "123456")

        # ============================================================ 线索
        print()
        print("=== 1. 线索：删了能在回收站看到，恢复后回到列表 ===")
        status, _ = call("DELETE", f"/leads/{ids['lead_mine']}", token=admin)
        check("删除线索", status, 200)

        status, body = call("GET", "/recycle-bin/leads?page_size=200", token=admin)
        check("回收站列表可访问", status, 200)
        check_true("被删的线索出现在回收站", ids["lead_mine"] in ids_of(body), str(ids_of(body)))

        status, _ = call("POST", f"/leads/{ids['lead_mine']}/restore", token=admin)
        check("恢复线索", status, 200)
        status, body = call("GET", "/recycle-bin/leads?page_size=200", token=admin)
        check_true("恢复后不再出现在回收站", ids["lead_mine"] not in ids_of(body))
        status, body = call("GET", "/leads?page_size=200", token=admin)
        check_true("恢复后回到线索列表", ids["lead_mine"] in ids_of(body))

        print()
        print("=== 2. 线索：数据范围（没范围的人看不到别人范围内的）===")
        status, _ = call("DELETE", f"/leads/{ids['lead_orphan']}", token=admin)
        check("删除孤儿线索", status, 200)
        status, body = call("GET", "/recycle-bin/leads?page_size=200", token=sales)
        check("业务员能看回收站列表", status, 200)
        check_true("业务员看不到不在自己范围内的已删线索",
                   ids["lead_orphan"] not in ids_of(body), str(ids_of(body)))
        status, body = call("GET", "/recycle-bin/leads?page_size=200", token=admin)
        check_true("管理员看得到（对照）", ids["lead_orphan"] in ids_of(body))

        print()
        print("=== 3. 线索：能看 ≠ 能恢复 ===")
        status, _ = call("POST", f"/leads/{ids['lead_orphan']}/restore", token=sales)
        check_true("业务员（无 lead:assign）恢复被拒", status == 403, f"实际 {status}")

        # ============================================================ 产品 / SKU
        print()
        print("=== 4. 产品：删产品连带删 SKU，恢复时 SKU 一起回来 ===")
        status, _ = call("DELETE", f"/products/{ids['product_a']}", token=admin)
        check("删除产品", status, 200)

        status, body = call("GET", "/recycle-bin/products?page_size=200", token=admin)
        check("产品回收站可访问", status, 200)
        row = row_of(body, ids["product_a"])
        check_true("被删的产品在回收站里", row is not None)
        check("该产品带出的 SKU 数", (row or {}).get("deleted_sku_count"), 2)

        status, body = call("GET", "/recycle-bin/skus?page_size=200", token=admin)
        codes = {r["sku_code"] for r in items_of(body)}
        check_true("它名下的 SKU 也在回收站里",
                   {f"{PREFIX}-A1", f"{PREFIX}-A2"} <= codes, str(sorted(codes)))

        status, body = call("POST", f"/products/{ids['product_a']}/restore", token=admin)
        check("恢复产品", status, 200)
        check("连带恢复了 SKU 数", len((body.get("data") or {}).get("restored_skus") or []), 2)
        check("没有 SKU 被跳过", len((body.get("data") or {}).get("skipped_skus") or []), 0)

        status, body = call("GET", f"/products/{ids['product_a']}/skus", token=admin)
        check("恢复后产品名下 SKU 数", len(items_of(body)), 2)

        print()
        print("=== 5. SKU：产品还在回收站时，不能单独恢复它 ===")
        status, _ = call("DELETE", f"/products/{ids['product_c']}", token=admin)
        check("删除产品C（连带删 SKU）", status, 200)
        status, _ = call("POST", f"/skus/{ids['sku_c1']}/restore", token=admin)
        check("产品还在回收站 -> 单独恢复 SKU 被拒", status, 400)

        status, _ = call("POST", f"/products/{ids['product_c']}/restore", token=admin)
        check("先恢复产品", status, 200)
        status, body = call("GET", f"/products/{ids['product_c']}/skus", token=admin)
        check_true("该 SKU 已随产品一起回来", ids["sku_c1"] in ids_of(body))
        status, _ = call("POST", f"/skus/{ids['sku_c1']}/restore", token=admin)
        check("它现在没被删，再点恢复提示『无需恢复』", status, 400)

        print()
        print("=== 6. SKU：产品还在、SKU 被单独删 -> 能单独恢复 ===")
        status, _ = call("DELETE", f"/skus/{ids['sku_b1']}", token=admin)
        check("单独删除 SKU", status, 200)
        status, body = call("GET", "/recycle-bin/skus?page_size=200", token=admin)
        row = row_of(body, ids["sku_b1"])
        check_true("它在回收站里且标注了『可单独恢复』",
                   row is not None and row["product_deleted"] is False, str(row))
        status, _ = call("POST", f"/skus/{ids['sku_b1']}/restore", token=admin)
        check("单独恢复 SKU", status, 200)
        status, body = call("GET", f"/products/{ids['product_b']}/skus", token=admin)
        check_true("恢复后回到产品名下", ids["sku_b1"] in ids_of(body))

        print()
        print("=== 7. 产品：能看 ≠ 能恢复 ===")
        status, _ = call("POST", f"/products/{ids['product_b']}/restore", token=sales)
        check_true("业务员（无 product:manage）恢复产品被拒", status == 403, f"实际 {status}")
        status, _ = call("POST", f"/skus/{ids['sku_b1']}/restore", token=sales)
        check_true("业务员恢复 SKU 也被拒", status == 403, f"实际 {status}")

        # ============================================================ 客户（只读）
        print()
        print("=== 8. 客户：被合并的标出『已并入谁』，直接删的没有 ===")
        status, body = call(
            "POST", "/customers/merge", token=admin,
            body={"source_customer_id": ids["cust_src"], "target_customer_id": ids["cust_tgt"]},
        )
        check("合并客户", status, 200)
        status, _ = call("DELETE", f"/customers/{ids['cust_direct']}", token=admin)
        check("直接删除客户", status, 200)

        status, body = call("GET", "/recycle-bin/customers?page_size=200", token=admin)
        check("客户回收站可访问", status, 200)
        src_row = row_of(body, ids["cust_src"])
        direct_row = row_of(body, ids["cust_direct"])
        check_true("被合并的客户在回收站里", src_row is not None)
        merged_into = (src_row or {}).get("merged_into") or {}
        check("它标出了并入了谁", merged_into.get("id"), ids["cust_tgt"])
        check_true("直接删的客户在回收站里", direct_row is not None)
        check("直接删的没有『并入了谁』", (direct_row or {}).get("merged_into"), None)

        print()
        print("=== 9. 客户：只读 —— 没有恢复接口 ===")
        status, _ = call("POST", f"/customers/{ids['cust_src']}/restore", token=admin)
        check_true("客户恢复接口不存在（404/405）", status in (404, 405), f"实际 {status}")

        print()
        print("=== 10. 匿名访问被拒 ===")
        status, _ = call("GET", "/recycle-bin/leads")
        check("匿名访问回收站", status, 401)

    finally:
        await cleanup()

    print()
    if FAILURES:
        print(f"FAILED：{len(FAILURES)} 项未通过")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
