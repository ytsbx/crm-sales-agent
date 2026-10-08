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
- **负责人字段成对下发**（2026-10-08 复审 RB05 补）：`owner_id/owner_name` 是客户**当前**
  的负责人（合并来源被清空 → 两个都是 null），`original_owner_id/original_owner_name`
  才是**原**负责人（合并前快照 / 直接删除的取当时字段）。从前只下发前者之一 + 后者之一，
  两个字段说的不是同一件事。
- **合并链的三种结局**（2026-10-08 复审 RB03 补）：追链有 20 层上限。
  正常走到终点 → 给可点入口；成环 → `loop`；**追满上限还没到头 → `truncated`**，
  界面说「合并链过长，最终去向待核实」。**关键是不能把停下来的那个中间客户当成终点**
  —— 那会给用户一个"目标已不存在"的错误结论，而真正的最终客户还在。
  边界也要钉住：正好追满上限的链**不算**没追完。
- **并发**（2026-10-08 复审 RB03 补）：产品"正在被删"（事务已写未提交）时，
  恢复 SKU / 新增 SKU 都必须**等**产品行锁，不能读着旧状态抢先落库 —— 否则
  产品一提交就留下挂在已删产品下的有效 SKU。
- **谁删的 / 怎么没的**（2026-10-08 复审 RB06 补）：四个列表都下发删除操作人；
  SKU 还要说清是**单独删**还是**随产品删**（后者给的是**删产品的那个人**）；
  客户要说清是**直接删除**还是**被合并移除**，并带上合并原因。
  **删 → 恢复 → 再删** 之后显示的是**本次**那个人，不是第一次那条旧留痕；
  留痕缺失时如实标「历史操作人待核实」，**不拿负责人顶替**。
- **不许张冠李戴**（2026-10-08 复审 RB07 补，第三轮收尾）：判"这条 SKU 是怎么没的、
  谁删的"必须**确认是同一次操作**。从前只比"哪条留痕的时间更近"，于是**一个月前**
  就删掉、但没有自己删除记录的 SKU，会被算到**今天删产品的人**头上。
  现在：随产品删也**各写一条 SKU 自己的**留痕（带 `via` 与当时的 `deleted_at`）；
  判"是不是本次"**只认留痕里钉下的那个 `deleted_at` 是否完全相等** —— 新留痕本来就
  记准了，"1 秒内"代替"同一次"是把判据放松了（另一次删除只差 0.5 秒也会被认错）；
  老留痕只有审计时间、证明不了对应本次 → 一律「待核实」，**不再靠时间容差猜**。
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
from datetime import UTC, datetime, timedelta
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
            # ⚠️ 审计必须排在**删 skus / products 之前**：SKU / 产品的删除留痕是按
            # `business_id` 反查的，源行删掉了就再也找不着它们（回收站正是靠这些
            # 留痕判断"谁删的、怎么没的"，残下来的会被守门套件逮住）
            "delete from audit_logs where business_type = 'sku' and business_id in "
            "(select id from skus where sku_code like :p)",
            "delete from audit_logs where business_type = 'product' and business_id in "
            "(select id from products where name like :p)",
            "delete from audit_logs where business_type = 'customer' and business_id in "
            + cust,
            "delete from audit_logs where business_type = 'lead' and business_id in "
            "(select id from leads where name like :p)",
            "delete from customers where name like :p",
            "delete from skus where sku_code like :p",
            "delete from products where name like :p",
            "delete from lead_assignments where lead_id in "
            "(select id from leads where name like :p)",
            "delete from leads where name like :p",
            # 内容里还带着夹具名的，兜底再扫一遍（删除/恢复/合并都会写）
            "delete from audit_logs where business_type in ('lead', 'product', 'sku', 'customer')"
            " and (coalesce(before_data::text, '') like :m or coalesce(after_data::text, '') like :m)",
            # 权限用例自造的账号与角色（第 8 段）。**必须最后删**：角色与用户
            # 被关联表指着，而且此时业务行已经清完，不会再撞外键。
            "delete from user_roles where user_id in "
            "(select id from users where username like :u)",
            "delete from users where username like :u",
            "delete from role_permissions where role_id in "
            "(select id from roles where code like :p)",
            "delete from roles where code like :p",
        ):
            await s.execute(
                text(sql),
                {"p": f"{PREFIX}%", "m": f"%{PREFIX}%", "u": f"{PREFIX.lower()}%"},
            )
        await s.commit()


async def seed_fixtures() -> dict:
    """夹具：线索两条、产品三组、客户三个。

    "孤儿线索"的负责人故意设成一个**不存在的用户 id** —— 它不属于任何人的数据范围，
    于是"没数据范围的人看不到、管理员看得到"这条能被稳定验出来，
    不必依赖某个角色恰好是 self 还是 department。
    """
    from app.core.security import hash_password
    from app.modules.customer.model import Customer
    from app.modules.lead.model import Lead
    from app.modules.product.model import Product, Sku
    from app.modules.user.model import (
        Permission,
        Role,
        User,
        role_permissions,
        user_roles,
    )

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

        # 权限两档要用一个"能删能恢复、但**不能**改归属"的账号。
        # seed 里没有这一档：业务员两样都没有、销售主管两样都有 —— 所以自造一个，
        # 只授 `customer:view` + `customer:delete`。跑完在 `cleanup()` 里连角色一起删。
        role = Role(
            code=f"{PREFIX}DELONLY",
            name=f"{PREFIX}只删不改归属",
            status="active",
            data_scope="all",
        )
        s.add(role)
        await s.flush()
        perm_ids = (
            await s.execute(
                select(Permission.id).where(
                    Permission.code.in_(["customer:view", "customer:delete"])
                )
            )
        ).scalars().all()
        if len(perm_ids) != 2:
            raise SystemExit(
                "权限码 customer:view / customer:delete 不全，先跑 scripts/seed.py"
            )
        for perm_id in perm_ids:
            await s.execute(
                role_permissions.insert().values(role_id=role.id, permission_id=perm_id)
            )
        delonly = User(
            username=f"{PREFIX.lower()}delonly",
            name=f"{PREFIX}只删不改归属",
            password_hash=hash_password("123456"),
            status="active",
            department_id=admin.department_id,
        )
        s.add(delonly)
        await s.flush()
        await s.execute(user_roles.insert().values(user_id=delonly.id, role_id=role.id))
        ids["delonly"] = delonly.id
        ids["delonly_username"] = delonly.username

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

        # ---- 合并链的长度边界（2026-10-08 复审 RB03）----
        # 追链上限取自后端那个常量本身，链长按它推 —— 谁改了上限，这里跟着走，
        # 不会出现"上限改了、用例还在验旧层数"的假绿。
        #   长链：比上限**多追一步**才到头 → 必须标"没追完"
        #   边界链：正好**追满上限**就到底 → 必须照常给终点（这一条防误判）
        # 链条之外的环由 `_add_reverse_merge_log` 手工补（合并接口自己造不出环）。
        from app.modules.recycle.service import _MERGE_CHAIN_MAX_HOPS

        ids["chain_hops"] = _MERGE_CHAIN_MAX_HOPS
        long_nodes = _MERGE_CHAIN_MAX_HOPS + 3
        cap_nodes = _MERGE_CHAIN_MAX_HOPS + 2
        long_chain = [
            Customer(name=f"{PREFIX}长链{n:02d}", owner_id=admin.id,
                     status="active", pool_status="private")
            for n in range(1, long_nodes + 1)
        ]
        cap_chain = [
            Customer(name=f"{PREFIX}边界链{n:02d}", owner_id=admin.id,
                     status="active", pool_status="private")
            for n in range(1, cap_nodes + 1)
        ]
        cyc_a = Customer(name=f"{PREFIX}环A", owner_id=admin.id, status="active",
                         pool_status="private")
        cyc_b = Customer(name=f"{PREFIX}环B", owner_id=admin.id, status="active",
                         pool_status="private")
        s.add_all([*long_chain, *cap_chain, cyc_a, cyc_b])
        await s.flush()
        ids["long_chain"] = [c.id for c in long_chain]
        ids["cap_chain"] = [c.id for c in cap_chain]
        ids["cyc_a"] = cyc_a.id
        ids["cyc_b"] = cyc_b.id
        # 名字也带着走：断言里要拿它核对"终点是谁"，别在用例里现拼字符串
        ids["long_second_name"] = long_chain[1].name
        ids["long_tail_name"] = long_chain[-1].name
        ids["cap_tail_name"] = cap_chain[-1].name

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
    # 合并会把来源的 owner_id 清空；"原负责人"必须回到**快照**里的那位，而不是"未分配"
    check("原负责人取的是合并前的快照", row.get("original_owner_name"), "李四")
    check("原负责人的 id 也来自快照（成对下发）", row.get("original_owner_id"), ids["lisi"])
    check("『当前负责人』确实已被清空，别跟它混着看", row.get("owner_id"), None)
    check("『当前负责人』姓名同样为空", row.get("owner_name"), None)
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
    check("不再显示成『未分配』（原负责人没名字）", (row or {}).get("original_owner_name"), None)
    check("原负责人 id 也为空（成对）", (row or {}).get("original_owner_id"), None)


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


async def _add_reverse_merge_log(source_id: int, target_id: int) -> None:
    """手工补一条合并留痕，造出 A→B 且 B→A 的环。

    走接口是造不出环的（来源合并完就被软删，不可能再当一次来源），所以这只能是
    "脏数据" —— 但列表接口必须扛得住：既不无限循环，也不给一个错的链接。
    """
    async with SessionLocal() as s:
        await s.execute(
            text(
                "insert into customer_merge_logs"
                " (source_customer_id, target_customer_id, operator_id,"
                "  merge_snapshot, moved, conflicts, reason, created_at)"
                " values (:s, :t, null, null, null, null, :r, now())"
            ),
            {"s": source_id, "t": target_id, "r": "夹具：手工造环"},
        )
        await s.commit()


async def _build_merge_chains(ids: dict, admin: str) -> None:
    """把长链、边界链合并出来；环手工补一条反向留痕。"""
    for chain in (ids["long_chain"], ids["cap_chain"]):
        for source, target in zip(chain, chain[1:]):
            await _merge(source, target, admin)
    # 边界链的尾节点**直接删掉**（不是被合并）：用来分辨
    # "终点已删（gone，给名字不给链接）"和"没追完（truncated）"这两种"没有链接"。
    status, _ = call("DELETE", f"/customers/{ids['cap_chain'][-1]}", token=admin)
    if status != 200:
        raise SystemExit(f"删除边界链尾节点失败：HTTP {status}")
    await _merge(ids["cyc_a"], ids["cyc_b"], admin)
    await _add_reverse_merge_log(ids["cyc_b"], ids["cyc_a"])


async def _customer_rows_by_id(
    cust_ids: list[int], token: str, page_size: int
) -> dict[int, dict]:
    """**按同一种分页**把这几条记录一次找齐。

    列表里还有本套件前面几节留下的夹具，目标行不一定在第一页 —— 所以必须翻页。
    一次走完、把要的几条都收下，别为每条单独再翻一遍。
    """
    wanted = set(cust_ids)
    found: dict[int, dict] = {}
    for page in range(1, 81):
        status, body = call(
            "GET",
            f"/recycle-bin/customers?page={page}&page_size={page_size}",
            token=token,
        )
        if status != 200:
            break
        rows = items_of(body)
        if not rows:
            break
        for row in rows:
            if row.get("id") in wanted:
                found[row["id"]] = row
        if len(found) == len(wanted):
            break
    return found


async def assert_merge_chain_over_cap(ids: dict, admin: str) -> None:
    """RB03：链条追到上限还不到头时**不给链接**，更不能把中间客户当成终点。"""
    hops = ids["chain_hops"]
    head = ids["long_chain"][0]
    cap_head = ids["cap_chain"][0]
    print()
    print(f"=== 17. 客户：合并链超过追踪上限（{hops} 层）→ 不给错链接 ===")

    # 关键：**一页一行**。后端是"按当页要追的那些客户逐层批量展开"的，把整条链摆在
    # 同一页上会一口气展开完，反而看不出上限 —— 真实场景里正是"这条链的其他客户
    # 落在别的页上"才追不完，一页一行就是那个场景的确定版。
    one = await _customer_rows_by_id([head, cap_head, ids["cyc_a"]], admin, 1)

    # ---- 长链：从起点的"直接目标"起要追 21 跳，超出上限 ----
    row = one.get(head)
    check_true("长链起点在回收站里", row is not None)
    check("直接历史照常给（并入了第 2 个）",
          ((row or {}).get("merged_into") or {}).get("name"), ids["long_second_name"])
    fin = (row or {}).get("final_target") or {}
    # 从前这里会把"停下来的那个中间客户"当成终点发出去，用户看到"目标已不存在"
    check("追不完 → 标成 truncated", fin.get("state"), "truncated")
    check("追不完 → 不给 id（不给错的链接）", fin.get("id"), None)
    check("追不完 → 不给名字（免得被当成确定的去向）", fin.get("name"), None)

    # ---- 边界链：正好追满上限就到底，**不能**被当成"没追完" ----
    # 这条同时盯着"追满上限后补的那一次探边"：少了它，正好卡上限的链会被误判成没追完。
    row = one.get(cap_head)
    check_true("边界链起点在回收站里", row is not None)
    fin = (row or {}).get("final_target") or {}
    check("正好追满上限也不算『没追完』", fin.get("state"), "gone")
    check("终点就是链尾那个客户", fin.get("name"), ids["cap_tail_name"])
    check("终点已被直接删除 → 不给 id", fin.get("id"), None)

    # ---- 环：A↔B ----
    row = one.get(ids["cyc_a"])
    check_true("成环那条在回收站里", row is not None)
    fin = (row or {}).get("final_target") or {}
    check("成环 → 标成 loop", fin.get("state"), "loop")
    check("成环不给 id", fin.get("id"), None)

    # ---- 换个分页：展开范围会变（结论允许在"给得出"和"待核实"之间变），
    #      但**永远不能把链条中间的某个客户说成最终去向** ----
    for page_size in (200, 10):
        found = await _customer_rows_by_id([head], admin, page_size)
        fin = (found.get(head) or {}).get("final_target") or {}
        check_true(
            f"分页 {page_size}：终点要么是链尾、要么说『待核实』，不能是中间客户",
            fin.get("id") in (None, ids["long_chain"][-1])
            and fin.get("name") in (None, ids["long_tail_name"]),
            repr(fin),
        )


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


async def assert_removed_by(ids: dict, admin: str) -> None:
    """RB06（2026-10-08 复审）：「谁把他删了」和「他是怎么没的」。

    操作人**一直都记着** —— 删除记在 `audit_logs`（`business_type` / `business_id` /
    `operator_id`），合并另有一份更精确的留痕（`customer_merge_logs.operator_id`，
    记的正是"谁把这条并进了哪条"）。缺的只是"没往外接"。这一节守五件事：

    1. 四个列表都下发「谁删的」；
    2. **SKU 要说清"单独删"还是"随产品删"**，而且随产品删时给的是
       **删产品的那个人** —— 正是"谁通过删产品把它带走的"；
    3. 客户要说清"直接删除"还是"被合并移除"，并带上合并原因；
    4. **删 → 恢复 → 再删 之后显示的是本次那个人**，不是第一次那条旧留痕；
    5. 留痕缺失时如实标「待核实」，**不拿负责人顶替**。

    第 4 条得手工构造：有权限删线索的只有管理员一个人，光走接口造不出
    "换个人删"的局面 —— 所以把**第一次**那条审计的操作人改成李四，再看第二次
    删完显示的是谁。实现若取"最早一条"，这里必定报红。
    """
    print()
    print("=== 18. 回收站：谁删的 / 怎么没的（复审 RB06）===")

    tag = uuid4().hex[:6]
    async with SessionLocal() as s:
        from app.modules.customer.model import Customer
        from app.modules.lead.model import Lead
        from app.modules.product.model import Product, Sku

        lead = Lead(name=f"{PREFIX}删人{tag}", owner_id=ids["zhangsan"],
                    status="assigned", created_by=ids["admin"])
        no_trace = Lead(name=f"{PREFIX}无痕{tag}", owner_id=ids["zhangsan"],
                        status="assigned", created_by=ids["admin"])
        p_with = Product(name=f"{PREFIX}连带{tag}", created_by=ids["admin"])
        p_alone = Product(name=f"{PREFIX}单独{tag}", created_by=ids["admin"])
        c_direct = Customer(name=f"{PREFIX}直删{tag}", owner_id=ids["admin"],
                            status="active", pool_status="private")
        c_src = Customer(name=f"{PREFIX}被并{tag}", owner_id=ids["admin"],
                         status="active", pool_status="private")
        c_tgt = Customer(name=f"{PREFIX}去处{tag}", owner_id=ids["admin"],
                         status="active", pool_status="private")
        s.add_all([lead, no_trace, p_with, p_alone, c_direct, c_src, c_tgt])
        await s.flush()
        sk_with = Sku(product_id=p_with.id, sku_code=f"{PREFIX}-W{tag}", name="随产品删")
        sk_alone = Sku(product_id=p_alone.id, sku_code=f"{PREFIX}-S{tag}", name="单独删")
        s.add_all([sk_with, sk_alone])
        await s.flush()
        lead_id, no_trace_id = lead.id, no_trace.id
        prod_with_id = p_with.id
        sku_with_id, sku_alone_id = sk_with.id, sk_alone.id
        cust_direct_id, cust_src_id, cust_tgt_id = c_direct.id, c_src.id, c_tgt.id
        await s.commit()

    def sku_row(rid: int) -> dict:
        _, body = call("GET", "/recycle-bin/skus?page_size=200", token=admin)
        return row_of(body, rid) or {}

    # ---- SKU：单独删 ----
    check("删掉一个 SKU（单独删）", call("DELETE", f"/skus/{sku_alone_id}", token=admin)[0], 200)
    row = sku_row(sku_alone_id)
    check("单独删的标成 direct", row.get("removed_via"), "direct")
    check("记下了是谁删的", row.get("deleted_by_id"), ids["admin"])
    check_true("也给了名字，不是只有一串编号", bool(row.get("deleted_by_name")),
               repr(row.get("deleted_by_name")))
    check("有留痕就不是「待核实」", row.get("deleted_by_pending"), False)

    # ---- SKU：随产品删 ----
    check("删掉产品（连带删它名下的 SKU）",
          call("DELETE", f"/products/{prod_with_id}", token=admin)[0], 200)
    row = sku_row(sku_with_id)
    check("随产品删的标成 with_product", row.get("removed_via"), "with_product")
    check("给的是**删产品的那个人**（谁把它带走的）", row.get("deleted_by_id"), ids["admin"])
    check("有留痕就不是「待核实」", row.get("deleted_by_pending"), False)
    async with SessionLocal() as s:
        own_trace = [
            r[0]
            for r in (
                await s.execute(
                    text("select after_data from audit_logs where business_type = 'sku'"
                         " and business_id = :b and action = 'delete'"),
                    {"b": sku_with_id},
                )
            ).all()
        ]
    # RB07 之后：连坐删也**各写一条 SKU 自己的留痕**。从前只写产品那一条，
    # 回收站只能拿"产品留痕的时间"去和 SKU 的删除时间比谁近 —— 那会张冠李戴。
    check("这条 SKU 自己**有一条**删除留痕（RB07 补的）", len(own_trace), 1)
    check("留痕里写明「随产品删」",
          (own_trace[0] or {}).get("via") if own_trace else None, "product_delete")

    # ---- SKU：先单独删过、恢复过，再被产品连坐删 → 仍要说成"随产品删" ----
    # 这一条才真正考验判据：库里**既有它自己的旧留痕、又有产品那条**，
    # 只有"与本次删除时刻同一次"的那条才对得上。若只看"有没有自己的留痕"，
    # 会认成"单独删"—— 可那条留痕是上一次的，早就不作数了。
    async with SessionLocal() as s:
        p_mix = Product(name=f"{PREFIX}混合{tag}", created_by=ids["admin"])
        s.add(p_mix)
        await s.flush()
        sk_mix = Sku(product_id=p_mix.id, sku_code=f"{PREFIX}-M{tag}", name="先单独删过")
        s.add(sk_mix)
        await s.flush()
        p_mix_id, sk_mix_id = p_mix.id, sk_mix.id
        await s.commit()

    check("（混合用例）先单独删一次", call("DELETE", f"/skus/{sk_mix_id}", token=admin)[0], 200)
    check("（混合用例）把它恢复", call("POST", f"/skus/{sk_mix_id}/restore", token=admin)[0], 200)
    check("（混合用例）再删产品，它被连坐",
          call("DELETE", f"/products/{p_mix_id}", token=admin)[0], 200)
    row = sku_row(sk_mix_id)
    check("恢复过又被产品连坐删 → 仍然说「随产品删」（没翻出上次那条旧留痕）",
          row.get("removed_via"), "with_product")

    # 反过来：先随产品删 → 恢复产品（SKU 一起回来）→ 再单独删这个 SKU
    async with SessionLocal() as s:
        p_rev = Product(name=f"{PREFIX}反向{tag}", created_by=ids["admin"])
        s.add(p_rev)
        await s.flush()
        sk_rev = Sku(product_id=p_rev.id, sku_code=f"{PREFIX}-R{tag}", name="先随产品删过")
        s.add(sk_rev)
        await s.flush()
        p_rev_id, sk_rev_id = p_rev.id, sk_rev.id
        await s.commit()

    check("（反向用例）先删产品（它被连坐）",
          call("DELETE", f"/products/{p_rev_id}", token=admin)[0], 200)
    check("（反向用例）恢复产品，SKU 一起回来",
          call("POST", f"/products/{p_rev_id}/restore", token=admin)[0], 200)
    check("（反向用例）再单独删这个 SKU",
          call("DELETE", f"/skus/{sk_rev_id}", token=admin)[0], 200)
    row = sku_row(sk_rev_id)
    check("产品恢复过、又单独删 → 说「单独删除」（没翻出产品那条旧留痕）",
          row.get("removed_via"), "direct")

    # ---- 产品列表 ----
    _, body = call("GET", "/recycle-bin/products?page_size=200", token=admin)
    row = row_of(body, prod_with_id) or {}
    check("产品列表也记下了谁删的", row.get("deleted_by_id"), ids["admin"])

    # ---- 线索 ----
    check("删掉一条线索", call("DELETE", f"/leads/{lead_id}", token=admin)[0], 200)
    _, body = call("GET", "/recycle-bin/leads?page_size=200", token=admin)
    row = row_of(body, lead_id) or {}
    check("线索列表记下了谁删的", row.get("deleted_by_id"), ids["admin"])
    check_true("线索也给了名字", bool(row.get("deleted_by_name")), repr(row.get("deleted_by_name")))

    # ---- 客户：直接删 ----
    check("删掉一个客户（直接删）",
          call("DELETE", f"/customers/{cust_direct_id}", token=admin)[0], 200)
    _, body = call("GET", "/recycle-bin/customers?page_size=200", token=admin)
    row = row_of(body, cust_direct_id) or {}
    check("客户直接删标成 direct", row.get("removed_via"), "direct")
    check("客户直接删也记下了谁删的", row.get("deleted_by_id"), ids["admin"])
    check("直接删的没有合并原因（不显示一个空的「原因」）", row.get("merge_reason"), None)

    # ---- 客户：被合并掉 ----
    reason = f"{PREFIX}同一个客户重复建档{tag}"
    status, body = call("POST", "/customers/merge", token=admin, body={
        "source_customer_id": cust_src_id, "target_customer_id": cust_tgt_id, "reason": reason,
    })
    check("合并两个客户", status, 200)
    _, body = call("GET", "/recycle-bin/customers?page_size=200", token=admin)
    row = row_of(body, cust_src_id) or {}
    check("被合并掉的标成 merged", row.get("removed_via"), "merged")
    check("合并的操作人也记下来了（取合并留痕，不是流水账）",
          row.get("deleted_by_id"), ids["admin"])
    check("合并原因一起下发", row.get("merge_reason"), reason)

    # ---- 删 → 恢复 → 再删：要显示**本次**那个人 ----
    check("先把那条线索恢复", call("POST", f"/leads/{lead_id}/restore", token=admin)[0], 200)
    async with SessionLocal() as s:
        await s.execute(
            text("update audit_logs set operator_id = :who where business_type = 'lead'"
                 " and business_id = :b and action = 'delete'"),
            {"who": ids["lisi"], "b": lead_id},
        )
        await s.commit()
    check("再删一次（本次是管理员删的）",
          call("DELETE", f"/leads/{lead_id}", token=admin)[0], 200)
    _, body = call("GET", "/recycle-bin/leads?page_size=200", token=admin)
    row = row_of(body, lead_id) or {}
    check("删了又恢复再删：给的是**本次**那个人",
          row.get("deleted_by_id"), ids["admin"])
    check_true("没有翻出第一次那条旧留痕（李四）",
               row.get("deleted_by_id") != ids["lisi"], repr(row))

    # ---- 留痕缺失：如实说"待核实"，**不拿负责人顶替** ----
    check("删掉另一条线索", call("DELETE", f"/leads/{no_trace_id}", token=admin)[0], 200)
    async with SessionLocal() as s:
        await s.execute(
            text("delete from audit_logs where business_type = 'lead'"
                 " and business_id = :b and action = 'delete'"),
            {"b": no_trace_id},
        )
        await s.commit()
    _, body = call("GET", "/recycle-bin/leads?page_size=200", token=admin)
    row = row_of(body, no_trace_id) or {}
    check("留痕没了 → 标成「待核实」", row.get("deleted_by_pending"), True)
    check("这时候不给名字（不拿负责人顶替）", row.get("deleted_by_name"), None)
    check("负责人那一栏照旧有值 —— 两件事分开，不能互相顶替",
          row.get("owner_id"), ids["zhangsan"])


async def assert_stale_sku_not_blamed(ids: dict, admin: str) -> None:
    """RB07（2026-10-08 复审）：历史 SKU 的删除人**不许**被算到后来删产品的人头上。

    ## 复审复现的那个错

    一条 SKU 在**一个月前**就被删了、但没有留下自己的删除记录；今天管理员删掉了它
    所属的产品（这次删产品**根本没碰它** —— 它早就是"已删"状态）。回收站却显示它是
    "随产品删除"、删除人是**今天**这位管理员，还标成"无需核实"。
    等于：今天删产品的人，白背了一个月前那笔账。

    根因是判据只认"时间谁更近"：把 SKU 自己的留痕和它所属产品的留痕都当候选，
    **没有"是不是同一次操作"这道判定**，差一个月也照认。

    ## 现在的口径（第三轮把最后那点容差也去掉了）

    - 「随产品删除」有 **SKU 自己的**留痕（`delete_product` 给每个被连坐删的 SKU
      各写一条，带 `via=product_delete` 与当时的 `deleted_at`）；
    - 判"是不是本次"**只认留痕里钉下的那个 `deleted_at` 与当前值完全相等** ——
      新留痕本来就记准了，"1 秒内"代替"同一次"是把判据放松了；
    - 老留痕只有审计时间、证明不了对应本次 → 一律 **`removed_via=None` + 待核实**，
      **不再退回任何时间容差**（拿 60 秒、20 秒去贴，本质还是猜）；
    - 拿不到自己的留痕、或者对不上，同样一律待核实，不猜。

    下面七个场景逐条对应复审给的验收清单；场景 6 / 7 是第三轮补的"本次留痕缺失"
    两种形态（**只差 0.5 秒**的新格式留痕、**只差 20 秒**的老格式留痕）。
    """
    print()
    print("=== 19. SKU 的删除人：不许张冠李戴（复审 RB07）===")

    tag = uuid4().hex[:6]
    now = datetime.now(UTC)
    long_ago = now - timedelta(days=30)

    def sku_row(rid: int) -> dict:
        # ⚠️ `page_size` 上限是 200（`Query(20, ge=1, le=200)`）：写 300 会被 422 挡回，
        # 拿到空 body、每条断言都变成"查不到"—— 那会是一片假红，白找半天。
        _, body = call("GET", "/recycle-bin/skus?page_size=200", token=admin)
        return row_of(body, rid) or {}

    async def new_product_and_sku(suffix: str, *, deleted_at=None):
        """建一个产品 + 一条 SKU（可选：直接置成"已删"，模拟历史数据）。"""
        async with SessionLocal() as s:
            from app.modules.product.model import Product, Sku

            p = Product(name=f"{PREFIX}RB07{suffix}{tag}", created_by=ids["admin"])
            s.add(p)
            await s.flush()
            sk = Sku(
                product_id=p.id,
                sku_code=f"{PREFIX}-{suffix}{tag}",
                name=f"RB07{suffix}",
                deleted_at=deleted_at,
            )
            s.add(sk)
            await s.flush()
            pid, sid = p.id, sk.id
            await s.commit()
        return pid, sid

    async def add_delete_trace(bid: int, *, operator_id: int, created_at, after=None):
        """直接往流水账里插一条删除留痕（模拟"本次留痕缺失、只剩旧的那一条"）。

        走 ORM 而不是拼 SQL：`after_data` 是 JSONB 列，用 `text()` 传字符串会报
        "column after_data is of type jsonb but expression is of type character varying"。
        """
        from app.core.audit import AuditLog

        async with SessionLocal() as s:
            s.add(
                AuditLog(
                    operator_id=operator_id,
                    source="WEB",
                    business_type="sku",
                    business_id=bid,
                    action="delete",
                    after_data=after,
                    created_at=created_at,
                )
            )
            await s.commit()

    # ---- 场景 1：早就删掉、没有自己的留痕，后来删产品 → 待核实，不许归给删产品的人
    p1, sk1 = await new_product_and_sku("OLD", deleted_at=long_ago)
    check(
        "场景1：一个月前就删掉的 SKU（且没留下自己的删除记录），今天删它所属产品",
        call("DELETE", f"/products/{p1}", token=admin)[0],
        200,
    )
    row = sku_row(sk1)
    check("场景1：删除方式判不出来 → 不给一个假答案", row.get("removed_via"), None)
    check("场景1：**不许**归给今天删产品的人", row.get("deleted_by_id"), None)
    check("场景1：如实标成「待核实」", row.get("deleted_by_pending"), True)

    # ---- 场景 2：真正随产品删的 SKU → 给删产品的那个人
    p2, sk2 = await new_product_and_sku("WITH")
    check(
        "场景2：删产品（这条 SKU 是活的，会被连坐）",
        call("DELETE", f"/products/{p2}", token=admin)[0],
        200,
    )
    row = sku_row(sk2)
    check("场景2：说成「随产品删」", row.get("removed_via"), "with_product")
    check("场景2：给的是**删产品的那个人**", row.get("deleted_by_id"), ids["admin"])
    check("场景2：不算「待核实」", row.get("deleted_by_pending"), False)

    # ---- 场景 3：单独删 → 恢复 → 随产品删 → 说"随产品删"（本次操作）
    p3, sk3 = await new_product_and_sku("MIX")
    check("场景3：先单独删一次", call("DELETE", f"/skus/{sk3}", token=admin)[0], 200)
    check("场景3：恢复它", call("POST", f"/skus/{sk3}/restore", token=admin)[0], 200)
    check("场景3：再删产品，它被连坐", call("DELETE", f"/products/{p3}", token=admin)[0], 200)
    row = sku_row(sk3)
    check(
        "场景3：恢复过又被产品连坐删 → 说「随产品删」（没翻出上次那条旧留痕）",
        row.get("removed_via"),
        "with_product",
    )
    check("场景3：操作人仍是本次这位", row.get("deleted_by_id"), ids["admin"])

    # ---- 场景 4：随产品删 → 恢复 → 单独删 → 说"单独删"（本次操作）
    p4, sk4 = await new_product_and_sku("REV")
    check("场景4：先删产品（它被连坐）", call("DELETE", f"/products/{p4}", token=admin)[0], 200)
    check("场景4：恢复产品，SKU 一起回来",
          call("POST", f"/products/{p4}/restore", token=admin)[0], 200)
    check("场景4：再单独删这个 SKU", call("DELETE", f"/skus/{sk4}", token=admin)[0], 200)
    row = sku_row(sk4)
    check(
        "场景4：产品恢复过、又单独删 → 说「单独删除」（没翻出产品那条旧留痕）",
        row.get("removed_via"),
        "direct",
    )
    check("场景4：操作人仍是本次这位", row.get("deleted_by_id"), ids["admin"])

    # ---- 场景 5：只有一条**无关**的旧留痕 → 待核实，不许取旧人顶替
    p5, sk5 = await new_product_and_sku("STALE", deleted_at=now)
    # 老格式的留痕：没记 `deleted_at`，而且时间在 30 天前 —— 与本次删除无关
    await add_delete_trace(sk5, operator_id=ids["lisi"], created_at=long_ago)
    row = sku_row(sk5)
    check("场景5：库里只有一条无关旧留痕 → **不取那个人**顶替",
          row.get("deleted_by_id"), None)
    check_true("场景5：也不能把李四认成删它的人",
               row.get("deleted_by_id") != ids["lisi"], repr(row))
    check("场景5：删除方式同样判不出来", row.get("removed_via"), None)
    check("场景5：如实标成「待核实」", row.get("deleted_by_pending"), True)

    # ---- 场景 6（第三轮补）：本次留痕缺失，只剩"上一次删除"的留痕，而且只差 0.5 秒
    #
    # 这是"历史留痕不完整时的边界"：**新格式**留痕记着当时的 `deleted_at`，
    # 但它与本次删除只差 0.5 秒。从前那道判据给新格式留了"1 秒内"的容差，
    # 于是这**另一次**操作照样被认成本次 —— 旧操作人被算到今天这次头上，
    # 还不标「待核实」。这是**把判据放松**（新留痕本来就钉得住，不必留容差）。
    p6, sk6 = await new_product_and_sku("NEAR", deleted_at=now)
    near = now - timedelta(seconds=0.5)
    await add_delete_trace(
        sk6,
        operator_id=ids["lisi"],
        created_at=near,
        after={"via": "direct", "deleted_at": near.isoformat()},
    )
    row = sku_row(sk6)
    check("场景6：只差 0.5 秒的**另一次**删除 → 不许认成本次", row.get("deleted_by_id"), None)
    check_true("场景6：也不能把李四算成删它的人",
               row.get("deleted_by_id") != ids["lisi"], repr(row))
    check("场景6：删除方式同样判不出来", row.get("removed_via"), None)
    check("场景6：如实标成「待核实」", row.get("deleted_by_pending"), True)

    # ---- 场景 7（第三轮补）：老格式留痕、只差 20 秒 → 同样不认
    #
    # 老留痕只有审计时间，**证明不了它对应本次删除**。从前靠"60 秒内"去贴，
    # 差 20 秒就认了 —— 那还是猜，而且猜错的方向正是"把旧操作人算到今天头上"。
    p7, sk7 = await new_product_and_sku("OLD20", deleted_at=now)
    await add_delete_trace(
        sk7,
        operator_id=ids["lisi"],
        created_at=now - timedelta(seconds=20),
    )
    row = sku_row(sk7)
    check("场景7：老格式留痕只差 20 秒 → **不认**（证明不了是同一次）",
          row.get("deleted_by_id"), None)
    check("场景7：删除方式同样判不出来", row.get("removed_via"), None)
    check("场景7：如实标成「待核实」", row.get("deleted_by_pending"), True)


async def assert_customer_restore(ids: dict, admin: str, sales: str) -> None:
    """客户恢复（2026-10-08 主人拍板；03-API §42.2）。

    口径（都是"核实过代码"才这么定的；回收站复审第三轮补齐）：

    - **只有"直接删除"的客户能恢复**。`delete_customer` 只把客户本人的
      `deleted_at` 置上 —— 名下联系人/商机/报价/订单**一条都没删**、也没改挂。
      所以**不换人**的恢复就是把标记去掉，那些东西**自动就回来了**。
    - **被合并掉的不能恢复**（400）：它名下已经被改挂到目标客户，恢复只会得到一个
      空壳，还会把"它已经被并进某某了"这个事实盖掉 —— 用户会以为数据丢了。
    - **原负责人已停用 / 账号已没**时，用 `REQUIRED_FIELD_MISSING`（**40003**）+ 422
      拒掉 —— **调用方认这个错误码，不许去匹配提示文字**。带上 `owner_id` 指定一位
      在职的人选即可；否则恢复出来是一条"挂在停用账号下、谁都看不到"的脏数据。
    - **给了 `owner_id` 就是一次改派**（第三轮修）：不再只改客户负责人，归属历史、
      未完成待办、名下单据、公海/私海标记都要一起办完。**"责任有没有真的搬过去"
      由 `check_customer_handover_documents` 第 11 段钉住**（那边才有各类单据的夹具）；
      这里只验接口层面：要不要权限、要不要选人、公海私海、归属历史。
    - **权限两档**：恢复给原负责人只要 `customer:delete`（谁删的谁能拾回来）；
      **把客户交给别人额外要 `customer:assign`** —— 否则只有"删除客户"权限的人
      借恢复这个入口就能改归属，等于绕开分配权限。业务员两样都没有 → 403。
    """
    print()
    print("=== 20. 客户恢复（只有「直接删除」的能恢复）===")

    tag = uuid4().hex[:6]
    async with SessionLocal() as s:
        from app.modules.customer.model import Contact, Customer

        plain = Customer(name=f"{PREFIX}待恢复{tag}", owner_id=ids["lisi"],
                         status="active", pool_status="private")
        with_data = Customer(name=f"{PREFIX}带数据{tag}", owner_id=ids["lisi"],
                             status="active", pool_status="private")
        src = Customer(name=f"{PREFIX}并入{tag}", owner_id=ids["lisi"],
                       status="active", pool_status="private")
        tgt = Customer(name=f"{PREFIX}去处{tag}", owner_id=ids["lisi"],
                       status="active", pool_status="private")
        alive = Customer(name=f"{PREFIX}没删{tag}", owner_id=ids["lisi"],
                         status="active", pool_status="private")
        stale = Customer(name=f"{PREFIX}停用负责人{tag}", owner_id=ids["lisi"],
                         status="active", pool_status="private")
        s.add_all([plain, with_data, src, tgt, alive, stale])
        await s.flush()
        # 给"带数据"那条挂一个联系人：用来证明"删客户没动名下的东西"
        s.add(
            Contact(
                customer_id=with_data.id,
                name=f"{PREFIX}联系人{tag}",
                mobile="13900000001",
            )
        )
        await s.flush()
        plain_id, with_data_id = plain.id, with_data.id
        src_id, tgt_id, alive_id, stale_id = src.id, tgt.id, alive.id, stale.id
        await s.commit()

    async def owner_of(cid: int) -> int | None:
        async with SessionLocal() as s:
            return (
                await s.execute(
                    text("select owner_id from customers where id = :i"), {"i": cid}
                )
            ).scalar_one()

    async def contact_count(cid: int) -> int:
        async with SessionLocal() as s:
            return int(
                (
                    await s.execute(
                        text("select count(*) from contacts where customer_id = :i"),
                        {"i": cid},
                    )
                ).scalar_one()
            )

    def customer_listed(cid: int) -> bool:
        _, body = call("GET", f"/customers?keyword={PREFIX}&page_size=200", token=admin)
        return cid in ids_of(body)

    # ---- 1. 直接删除 → 可以恢复，归属还原 ----
    check("（1）删掉一条客户", call("DELETE", f"/customers/{plain_id}", token=admin)[0], 200)
    check_true("删完它就从客户列表里消失了", not customer_listed(plain_id))
    check(
        "（1）恢复它",
        call("POST", f"/customers/{plain_id}/restore", token=admin, body={})[0],
        200,
    )
    check_true("恢复后回到客户列表", customer_listed(plain_id))
    check("（1）归属还原给原负责人（删除时没有清空过）", await owner_of(plain_id), ids["lisi"])
    async with SessionLocal() as s:
        restored_trace = int(
            (
                await s.execute(
                    text(
                        "select count(*) from audit_logs where business_type = 'customer'"
                        " and business_id = :b and action = 'restore'"
                    ),
                    {"b": plain_id},
                )
            ).scalar_one()
        )
    check("（1）恢复写了留痕（谁在什么时候恢复的）", restored_trace, 1)

    # ---- 2. 删客户**不动**名下的数据（所以恢复才这么轻）----
    before_contacts = await contact_count(with_data_id)
    check("（2）前提：这条客户名下挂着 1 个联系人", before_contacts, 1)
    check("（2）删掉这条客户", call("DELETE", f"/customers/{with_data_id}", token=admin)[0], 200)
    check("（2）删客户**没有**连带删它名下的联系人", await contact_count(with_data_id), 1)
    check(
        "（2）恢复它",
        call("POST", f"/customers/{with_data_id}/restore", token=admin, body={})[0],
        200,
    )
    check("（2）恢复后联系人仍在（本来就没被动过）", await contact_count(with_data_id), 1)

    # ---- 3. 被合并掉的客户：不给恢复 ----
    check(
        "（3）把一条客户合并进另一条",
        call("POST", "/customers/merge", token=admin, body={
            "source_customer_id": src_id, "target_customer_id": tgt_id,
            "reason": f"{PREFIX}恢复用例{tag}",
        })[0],
        200,
    )
    status, body = call(
        "POST", f"/customers/{src_id}/restore", token=admin, body={}
    )
    check("（3）恢复被合并掉的客户 → 400（空壳，不能给假希望）", status, 400)
    check_true(
        "（3）理由说清是「被合并掉的」、并指路到合并后那个客户",
        "合并" in (body.get("message") or ""),
        body.get("message"),
    )

    # ---- 4. 没被删的客户：谈不上恢复 ----
    check(
        "（4）恢复一条没被删的客户 → 404",
        call("POST", f"/customers/{alive_id}/restore", token=admin, body={})[0],
        404,
    )

    # ---- 5. 原负责人已停用：422，可指定新人选 ----
    check("（5）删掉一条（负责人是李四）", call("DELETE", f"/customers/{stale_id}", token=admin)[0], 200)
    async with SessionLocal() as s:
        await s.execute(
            text("update users set status = 'inactive' where id = :i"), {"i": ids["lisi"]}
        )
        await s.commit()
    try:
        status, body = call("POST", f"/customers/{stale_id}/restore", token=admin, body={})
        check("（5）原负责人已停用 → 422（不制造挂在停用账号下的脏数据）", status, 422)
        check(
            "（5）错误**码**要能认出「得先指定新负责人」（调用方不靠文案判断）",
            body.get("code"),
            40003,
        )
        check_true(
            "（5）理由指路了「指定新负责人」",
            "负责人" in (body.get("message") or ""),
            body.get("message"),
        )
        check(
            "（5）恢复时指定新负责人 → 200",
            call("POST", f"/customers/{stale_id}/restore", token=admin,
                 body={"owner_id": ids["zhangsan"]})[0],
            200,
        )
        check("（5）负责人换成了指定那位", await owner_of(stale_id), ids["zhangsan"])
    finally:
        # 李四的状态**必须改回来**：后面还有别的断言按"在职"来
        async with SessionLocal() as s:
            await s.execute(
                text("update users set status = 'active' where id = :i"), {"i": ids["lisi"]}
            )
            await s.commit()

    # ---- 6. 权限：恢复与删除共用同一把钥匙 ----
    check("（6）前提：业务员能看回收站", call("GET", "/recycle-bin/customers", token=sales)[0], 200)
    check("（6）再删一条给业务员试", call("DELETE", f"/customers/{plain_id}", token=admin)[0], 200)
    check(
        "（6）业务员（无 customer:delete）恢复 → 403",
        call("POST", f"/customers/{plain_id}/restore", token=sales, body={})[0],
        403,
    )
    check(
        "（6）把这条恢复回去，别留给守门套件",
        call("POST", f"/customers/{plain_id}/restore", token=admin, body={})[0],
        200,
    )

    # ---- 7. 公海客户：指定新人 → 同步设成私海；不指定 → 保留公海 ----
    #
    # 复审里那条：原来在公海的已删除客户，指定 B 恢复后负责人变成了 B，
    # 但 `pool_status` 还是 `public`——它照样出现在公海筛选里，自相矛盾。
    async def pool_of(cid: int) -> str | None:
        async with SessionLocal() as s:
            return (
                await s.execute(
                    text("select pool_status from customers where id = :i"), {"i": cid}
                )
            ).scalar_one()

    async with SessionLocal() as s:
        pub = Customer(
            name=f"{PREFIX}公海待恢复{tag}", owner_id=None,
            status="active", pool_status="public",
        )
        s.add(pub)
        await s.flush()
        pub_id = pub.id
        await s.commit()

    check("（7）删掉这条公海客户", call("DELETE", f"/customers/{pub_id}", token=admin)[0], 200)
    check(
        "（7）不指定负责人恢复 → 200（本来没负责人，照原样回公海）",
        call("POST", f"/customers/{pub_id}/restore", token=admin, body={})[0],
        200,
    )
    check("（7）负责人仍是空的", await owner_of(pub_id), None)
    check("（7）公海标记不变（原本在公海就留在公海）", await pool_of(pub_id), "public")

    check("（7）再删一次", call("DELETE", f"/customers/{pub_id}", token=admin)[0], 200)
    check(
        "（7）指定负责人恢复 → 200",
        call("POST", f"/customers/{pub_id}/restore", token=admin,
             body={"owner_id": ids["zhangsan"]})[0],
        200,
    )
    check("（7）负责人换成了指定那位", await owner_of(pub_id), ids["zhangsan"])
    check("（7）**同步设为私海**（否则还挂在公海筛选里）", await pool_of(pub_id), "private")

    # ---- 8. 权限两档：归还原负责人用删除权限；换给别人额外要分配权限 ----
    #
    # 主人口径（2026-10-08）：不给"改归属"另加一道闸，就有人能借「恢复」把客户
    # 交到别人名下 —— 那是分配权限该管的事。夹具账号 `delonly` 只有
    # `customer:view` + `customer:delete`，正是这一档。
    delonly_token = login(ids["delonly_username"], "123456")
    async with SessionLocal() as s:
        perm = Customer(
            name=f"{PREFIX}权限客户{tag}", owner_id=ids["lisi"],
            status="active", pool_status="private",
        )
        s.add(perm)
        await s.flush()
        perm_id = perm.id
        await s.commit()

    check(
        "（8）前提：这个账号能看回收站",
        call("GET", "/recycle-bin/customers", token=delonly_token)[0],
        200,
    )
    check("（8）删掉这条客户", call("DELETE", f"/customers/{perm_id}", token=admin)[0], 200)
    check(
        "（8）只有「删除客户」权限：**归还原负责人** → 200",
        call("POST", f"/customers/{perm_id}/restore", token=delonly_token, body={})[0],
        200,
    )
    check("（8）再删一次", call("DELETE", f"/customers/{perm_id}", token=admin)[0], 200)
    status, body = call(
        "POST", f"/customers/{perm_id}/restore", token=delonly_token,
        body={"owner_id": ids["zhangsan"]},
    )
    check("（8）同一个人要**把客户交给别人** → 403（改归属归「分配客户」管）", status, 403)
    check_true(
        "（8）拒绝时点名缺的是哪一项权限",
        "customer:assign" in (body.get("message") or ""),
        body.get("message"),
    )
    check_true(
        "（8）被拒之后客户**仍在回收站**（一个字段都没动）",
        not customer_listed(perm_id),
    )
    check(
        "（8）有「分配客户」权限的人去换人 → 200",
        call("POST", f"/customers/{perm_id}/restore", token=login("lisi", "123456"),
             body={"owner_id": ids["zhangsan"]})[0],
        200,
    )
    check("（8）负责人换成了指定那位", await owner_of(perm_id), ids["zhangsan"])
    async with SessionLocal() as s:
        perm_history = int(
            (
                await s.execute(
                    text(
                        "select count(*) from customer_owner_history where customer_id = :i"
                    ),
                    {"i": perm_id},
                )
            ).scalar_one()
        )
    check(
        "（8）换人写了一条归属历史（不换人的那两次一个字都没写）",
        perm_history,
        1,
    )


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
        # 直接删除的没有"合并清空"这一步：两对负责人字段应该是同一套人
        check("直接删的：原负责人就是当时那位（id）",
              (direct_row or {}).get("original_owner_id"), ids["admin"])
        check_true("直接删的：原负责人姓名不为空（不是『待核实』）",
                   bool((direct_row or {}).get("original_owner_name")),
                   repr((direct_row or {}).get("original_owner_name")))
        check("直接删的：当前负责人没被清空（与合并来源不同）",
              (direct_row or {}).get("owner_id"), ids["admin"])

        print()
        print("=== 9. 客户：只有『直接删除』的能恢复（2026-10-08 起）===")
        # 这一段在 2026-10-07 第一版是"客户只读、没有恢复接口"；主人 2026-10-08
        # 拍板给**直接删除**的客户加恢复（被合并掉的不给，见第 20 节）。
        status, _ = call("POST", f"/customers/{ids['cust_src']}/restore", token=admin, body={})
        check("被合并掉的客户不给恢复 → 400", status, 400)
        status, _ = call("POST", f"/customers/{ids['cust_direct']}/restore", token=admin, body={})
        check("直接删除的客户可以恢复 → 200", status, 200)

        print()
        print("=== 10. 匿名访问被拒 ===")
        status, _ = call("GET", "/recycle-bin/leads")
        check("匿名访问回收站", status, 401)

        # ============================================================ 复审补的边界
        await assert_merged_customer_scope(ids, admin, sales)
        await assert_missing_snapshot_is_admin_only(ids, admin, sales)
        await assert_target_needs_its_own_permission(ids, admin, sales)
        await assert_merge_chain_resolves(ids, admin, sales)
        await _build_merge_chains(ids, admin)
        await assert_merge_chain_over_cap(ids, admin)
        await assert_restore_sku_waits_for_product_lock(ids, admin)
        await assert_create_sku_waits_for_product_lock(ids, admin)
        await assert_removed_by(ids, admin)
        await assert_stale_sku_not_blamed(ids, admin)
        await assert_customer_restore(ids, admin, sales)

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
