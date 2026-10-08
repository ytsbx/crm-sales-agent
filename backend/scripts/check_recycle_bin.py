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
- **客户的权限边界**（2026-10-08 复审 RB01/RB02/RB04 补）：
  合并来源按**合并前快照里的负责人**判范围（不是清空后的字段）；
  快照里没有负责人的只给管理员看并标"待核实"；
  合并**目标**单独鉴权（不在范围内时连名字都不下发）；
  A→B→C 要保留 A→B 的历史、同时给出最终 C 的入口。
- **并发**（2026-10-08 复审 RB03 补）：产品"正在被删"（事务已写未提交）时，
  恢复 SKU / 新增 SKU 都必须**等**产品行锁，不能读着旧状态抢先落库 —— 否则
  产品一提交就留下挂在已删产品下的有效 SKU。
- **权限**：能看列表 ≠ 能恢复。业务员（有 `*:view`）列表 200、恢复 403。

跑法（需要后端在跑，且**不能用 8000**）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_recycle_bin.py
"""

import asyncio
import os
import threading
import time
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

        # 权限用例要用到"别人的客户"：张三（业务员）的数据范围是 self，
        # 李四（销售主管）是 department_and_sub —— 张三看不到李四的客户。
        zhangsan = (
            await s.execute(select(User).where(User.username == "zhangsan"))
        ).scalars().first()
        lisi = (await s.execute(select(User).where(User.username == "lisi"))).scalars().first()
        if zhangsan is None or lisi is None:
            raise SystemExit("库里缺 zhangsan / lisi 账号，先跑 scripts/seed.py")
        ids["zhangsan"] = zhangsan.id
        ids["lisi"] = lisi.id

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
        # 别人的客户（都属于李四）：张三不该在回收站看到被合并掉的那条
        other_src = Customer(name=f"{PREFIX}他组来源", owner_id=lisi.id, status="active",
                             pool_status="private")
        other_tgt = Customer(name=f"{PREFIX}他组目标", owner_id=lisi.id, status="active",
                             pool_status="private")
        # 合并留痕里**没有**负责人快照的老数据（后面用 SQL 把 owner_id 从快照里摘掉）
        nosnap_src = Customer(name=f"{PREFIX}缺快照来源", owner_id=lisi.id, status="active",
                              pool_status="private")
        nosnap_tgt = Customer(name=f"{PREFIX}缺快照目标", owner_id=lisi.id, status="active",
                              pool_status="private")
        # 来源归张三（他看得到）、目标归李四（他看不到）—— 验"来源可见 ≠ 目标可见"
        mixed_src = Customer(name=f"{PREFIX}混合来源", owner_id=zhangsan.id, status="active",
                             pool_status="private")
        mixed_tgt = Customer(name=f"{PREFIX}混合目标", owner_id=lisi.id, status="active",
                             pool_status="private")
        # A→B→C：三条都归张三，他全程有权看
        chain_a = Customer(name=f"{PREFIX}链条A", owner_id=zhangsan.id, status="active",
                           pool_status="private")
        chain_b = Customer(name=f"{PREFIX}链条B", owner_id=zhangsan.id, status="active",
                           pool_status="private")
        chain_c = Customer(name=f"{PREFIX}链条C", owner_id=zhangsan.id, status="active",
                           pool_status="private")
        s.add_all([
            src, tgt, direct, other_src, other_tgt, nosnap_src, nosnap_tgt,
            mixed_src, mixed_tgt, chain_a, chain_b, chain_c,
        ])
        await s.flush()
        ids["cust_src"] = src.id
        ids["cust_tgt"] = tgt.id
        ids["cust_direct"] = direct.id
        ids["cust_other_src"] = other_src.id
        ids["cust_other_tgt"] = other_tgt.id
        ids["cust_nosnap_src"] = nosnap_src.id
        ids["cust_nosnap_tgt"] = nosnap_tgt.id
        ids["cust_mixed_src"] = mixed_src.id
        ids["cust_mixed_tgt"] = mixed_tgt.id
        ids["cust_chain_a"] = chain_a.id
        ids["cust_chain_b"] = chain_b.id
        ids["cust_chain_c"] = chain_c.id

        # 产品 D：产品有效、其中一个 SKU 已被单独删 —— 用来验"删产品 vs 恢复 SKU"的并发
        product_d = Product(name=f"{PREFIX}产品D", created_by=admin.id)
        s.add(product_d)
        await s.flush()
        sku_d1 = Sku(product_id=product_d.id, sku_code=f"{PREFIX}-D1", name="D1")
        s.add(sku_d1)
        await s.flush()
        ids["product_d"] = product_d.id
        ids["sku_d1"] = sku_d1.id

        # 产品 E：没有 SKU —— 用来验"删产品 vs 新增 SKU"的并发
        product_e = Product(name=f"{PREFIX}产品E", created_by=admin.id)
        s.add(product_e)
        await s.flush()
        ids["product_e"] = product_e.id

        await s.commit()
    return ids


async def _merge(source_id: int, target_id: int, token: str) -> None:
    """走真实接口合并两个客户（夹具用）。"""
    status, body = call(
        "POST", "/customers/merge", token=token,
        body={"source_customer_id": source_id, "target_customer_id": target_id},
    )
    if status != 200:
        raise SystemExit(f"合并夹具失败：HTTP {status} {body}")


async def _strip_owner_from_snapshot(source_id: int) -> None:
    """把合并留痕里的负责人快照摘掉 —— 模拟"上线前的老数据没有这条快照"。"""
    async with SessionLocal() as s:
        await s.execute(
            text(
                "update customer_merge_logs set merge_snapshot = merge_snapshot - 'owner_id'"
                " where source_customer_id = :p"
            ),
            {"p": source_id},
        )
        await s.commit()


async def assert_merged_customer_scope(ids: dict, admin: str, sales: str) -> None:
    """RB01：合并来源客户按**合并前**的负责人判数据范围，不能被当成公海。"""
    print()
    print("=== 11. 客户：合并来源按【合并前】负责人判范围 ===")
    await _merge(ids["cust_other_src"], ids["cust_other_tgt"], admin)

    status, body = call("GET", "/recycle-bin/customers?page_size=200", token=sales)
    check("业务员能看客户回收站", status, 200)
    check_true(
        "业务员看不到别人团队被合并掉的客户",
        ids["cust_other_src"] not in ids_of(body),
        str(sorted(ids_of(body))),
    )

    status, body = call("GET", "/recycle-bin/customers?page_size=200", token=admin)
    check_true("管理员看得到（对照）", ids["cust_other_src"] in ids_of(body))
    row = row_of(body, ids["cust_other_src"]) or {}
    # 合并会把来源的 owner_id 清空；显示必须回到**快照**里的那位，而不是"未分配"
    check("原负责人取的是合并前的快照", row.get("owner_name"), "李四")
    check_true("这条不是『待核实』", row.get("owner_pending") is False, repr(row.get("owner_pending")))
    check("去向指向合并目标", (row.get("merged_into") or {}).get("id"), ids["cust_other_tgt"])


async def assert_missing_snapshot_is_admin_only(ids: dict, admin: str, sales: str) -> None:
    """RB01 的边界：快照里没有负责人时，既不能公开给所有人，也不该无声消失。"""
    print()
    print("=== 12. 客户：留痕缺负责人快照 → 只给管理员，标『待核实』 ===")
    await _merge(ids["cust_nosnap_src"], ids["cust_nosnap_tgt"], admin)
    await _strip_owner_from_snapshot(ids["cust_nosnap_src"])

    status, body = call("GET", "/recycle-bin/customers?page_size=200", token=sales)
    check_true(
        "缺快照时不放行给业务员（不能因为字段缺失就公开）",
        ids["cust_nosnap_src"] not in ids_of(body),
    )

    status, body = call("GET", "/recycle-bin/customers?page_size=200", token=admin)
    row = row_of(body, ids["cust_nosnap_src"])
    check_true("管理员仍能查到这条（不是无声消失）", row is not None)
    check("标成『待核实』", (row or {}).get("owner_pending"), True)
    check("不再显示成『未分配』", (row or {}).get("owner_name"), None)


async def assert_target_needs_its_own_permission(ids: dict, admin: str, sales: str) -> None:
    """RB02：看得到来源，不代表看得到合并目标。"""
    print()
    print("=== 13. 客户：来源能看 ≠ 目标能看 ===")
    await _merge(ids["cust_mixed_src"], ids["cust_mixed_tgt"], admin)

    status, body = call("GET", "/recycle-bin/customers?page_size=200", token=sales)
    row = row_of(body, ids["cust_mixed_src"])
    check_true("业务员看得到自己那条被合并的记录", row is not None)
    ref = (row or {}).get("merged_into") or {}
    check("目标不给名字", ref.get("name"), None)
    check("目标不给 id（前端就没有可点的入口）", ref.get("id"), None)
    check("目标标成无查看权限", ref.get("state"), "forbidden")
    status, _ = call("GET", f"/customers/{ids['cust_mixed_tgt']}", token=sales)
    check_true("目标详情本来也打不开（对照）", status == 403, f"实际 {status}")


async def assert_merge_chain_resolves(ids: dict, admin: str, sales: str) -> None:
    """RB04：A→B→C 之后，A 既要保留"并入 B"的历史，也要给出最终 C 的入口。"""
    print()
    print("=== 14. 客户：多级合并 A→B→C ===")
    await _merge(ids["cust_chain_a"], ids["cust_chain_b"], admin)
    await _merge(ids["cust_chain_b"], ids["cust_chain_c"], admin)

    status, body = call("GET", "/recycle-bin/customers?page_size=200", token=sales)
    row = row_of(body, ids["cust_chain_a"])
    check_true("链条起点在回收站里", row is not None)
    ref = (row or {}).get("merged_into") or {}
    check("保留『并入 B』的历史", ref.get("name"), f"{PREFIX}链条B")
    check("B 已被并走，不给 id", ref.get("id"), None)
    check("B 标成已不存在", ref.get("state"), "gone")

    fin = (row or {}).get("final_target") or {}
    check("给出最终有效客户 C", fin.get("id"), ids["cust_chain_c"])
    check("C 的名字", fin.get("name"), f"{PREFIX}链条C")
    status, _ = call("GET", f"/customers/{ids['cust_chain_c']}", token=sales)
    check_true("C 的详情能正常打开（给的是能用的入口）", status == 200, f"实际 {status}")


def _call_later(method: str, path: str, *, token: str, body=None) -> tuple[threading.Thread, dict]:
    """把一次真实请求放到后台线程发，用来观察它"有没有卡住"。"""
    slot: dict = {}

    def run() -> None:
        slot["result"] = call(method, path, token=token, body=body)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, slot


class _ProductDeleteInFlight:
    """在**独立事务**里锁住产品行并把它标成"已删"、但先不提交。

    这就是"产品正在被删"的确定性复现（比靠 sleep 拼时序稳得多）：
    进入时产品行的锁就攥在手里，`commit()` 才让这次删除真正落库。
    期间任何"先锁产品"的请求都会卡住 —— 这正是我们要断言的行为。
    """

    def __init__(self, product_id: int) -> None:
        self._product_id = product_id
        self._session = None

    async def __aenter__(self) -> "_ProductDeleteInFlight":
        self._session = SessionLocal()
        await self._session.execute(
            text("select id from products where id = :p for update"), {"p": self._product_id}
        )
        await self._session.execute(
            text("update products set deleted_at = now() where id = :p"), {"p": self._product_id}
        )
        return self

    async def commit(self) -> None:
        await self._session.commit()

    async def __aexit__(self, *exc) -> None:
        await self._session.close()


async def assert_restore_sku_waits_for_product_lock(ids: dict, admin: str) -> None:
    """RB03：删产品（未提交）时，恢复 SKU 必须**等**，不能读着旧状态抢先改。

    没有产品行锁的旧实现里，恢复 SKU 只看 SKU 自己 + 读一眼产品（读到的还是
    删之前的状态），于是会立刻 200 并把它改成有效；产品那边一提交，
    就留下一个挂在已删产品下的有效 SKU（孤儿）。
    """
    print()
    print("=== 15. 并发：删产品进行中，恢复 SKU 必须等（RB03）===")
    status, _ = call("DELETE", f"/skus/{ids['sku_d1']}", token=admin)
    check("先把该 SKU 单独删掉（夹具）", status, 200)

    async with _ProductDeleteInFlight(ids["product_d"]) as in_flight:
        thread, slot = _call_later(
            "POST", f"/skus/{ids['sku_d1']}/restore", token=admin
        )
        time.sleep(1.5)
        check_true(
            "产品删除未落库时，恢复 SKU 会等（而不是抢先改）",
            "result" not in slot,
            f"实际已经返回：{slot.get('result')}",
        )
        await in_flight.commit()
        thread.join(timeout=20)

    status = (slot.get("result") or (None, None))[0]
    check_true("产品删除落库后，恢复 SKU 被正确拒绝", status == 400, f"实际 {status}")

    async with SessionLocal() as s:
        still_deleted = (
            await s.execute(
                text("select (deleted_at is not null) from skus where id = :p"),
                {"p": ids["sku_d1"]},
            )
        ).scalar_one()
    check_true("该 SKU 仍是已删状态（没被并发恢复成孤儿）", bool(still_deleted))


async def assert_create_sku_waits_for_product_lock(ids: dict, admin: str) -> None:
    """RB03 的镜像面：删产品（未提交）时，新增 SKU 也不能抢先插进去。"""
    print()
    print("=== 16. 并发：删产品进行中，新增 SKU 必须等（RB03）===")
    new_code = f"{PREFIX}-E1"

    async with _ProductDeleteInFlight(ids["product_e"]) as in_flight:
        thread, slot = _call_later(
            "POST", f"/products/{ids['product_e']}/skus", token=admin,
            body={"sku_code": new_code, "name": "E1"},
        )
        time.sleep(1.5)
        check_true(
            "产品删除未落库时，新增 SKU 会等",
            "result" not in slot,
            f"实际已经返回：{slot.get('result')}",
        )
        await in_flight.commit()
        thread.join(timeout=20)

    status = (slot.get("result") or (None, None))[0]
    check_true("产品删除落库后，新增 SKU 被拒 404", status == 404, f"实际 {status}")

    async with SessionLocal() as s:
        count = (
            await s.execute(
                text("select count(*) from skus where sku_code = :c"), {"c": new_code}
            )
        ).scalar_one()
    check("没有插进孤儿 SKU", int(count), 0)


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

        # ============================================================ 复审补的边界
        await assert_merged_customer_scope(ids, admin, sales)
        await assert_missing_snapshot_is_admin_only(ids, admin, sales)
        await assert_target_needs_its_own_permission(ids, admin, sales)
        await assert_merge_chain_resolves(ids, admin, sales)
        await assert_restore_sku_waits_for_product_lock(ids, admin)
        await assert_create_sku_waits_for_product_lock(ids, admin)

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
