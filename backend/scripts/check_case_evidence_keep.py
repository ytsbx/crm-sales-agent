"""案例证据完整性（返修 R04）：修订稿要带走全部证据、局部改旧字段不截别类、脏数据整笔拒绝。

**只在隔离库跑**：库名必须含 test（或 CI=true）。

## 这条修的是什么

案例的证据从"四个单值字段"改成了"一张 `case_evidences` 表"（一条案例可挂多张
单据）。三条与新结构配套的逻辑没跟上：

1. **开修订稿时不复制证据** —— 修订稿只继承了四个旧字段（每类顶多一条），新表里的
   多条一条都不过去。而修订稿是要**替换**原版的，批准之后原版那批引用就等于被
   一次性抹掉了 —— 修订的初衷只是改内容。
2. **改某个旧字段会重写整份证据列表** —— 改一个 `order_id`，`quote` / `sample` /
   `opportunity` 三类也被一起重写成"每类第一条"，同类里第二、第三条就这么没了。
3. **脏数据不被拒绝，反而把证据清空** —— `validate_evidences` 只**过滤**掉不认识
   的 `kind`，过滤完为空就直接放行；而 `sync_evidences` 是"先删干净、再逐条插入"，
   于是**一次带脏数据的提交就把整份证据清空了**。

## 怎么验

真造两份报价、两张订单、一条案例，按"挂证据 → 塞脏数据 → 改旧字段 → 开修订稿"
的顺序走一遍，每一步都数一数证据还剩几条。判据是**条数**，不是"返回 200"：

- 塞脏数据后：应 422，且证据**一条没少**（旧写法：200 且清零）；
- 改 `order_id` 后：`quote` 类的**每一条都还在**（旧写法：只剩第一条）；
- 开修订稿后：新草稿的证据条数与原版**相等**（旧写法：少了）。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_case_evidence_keep.py
"""

import asyncio
import os
import time
from urllib.parse import urlparse

from sqlalchemy import select, text
from _test_support import require_isolated_db

require_isolated_db()

from app.core.config import settings
from app.core.database import SessionLocal
from scripts.check_review_regressions import BASE, call, login

MARKER = f"CHKEVID{int(time.time())}"
FAILURES: list[str] = []
CREATED: dict[str, list[int]] = {"quotes": [], "orders": []}


def check(label: str, condition: object, expected: object = True) -> None:
    ok = condition == expected
    print(f'  {"OK  " if ok else "FAIL"} {label}：{condition!r}' + ("" if ok else f"（应为 {expected!r}）"))
    if not ok:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def kinds_of(detail: dict) -> dict[str, int]:
    """详情里 `evidences` 按 kind 计数 —— 断言比"有几条"更能说清少了哪一类。"""
    counted: dict[str, int] = {}
    for item in detail.get("evidences") or []:
        counted[item["kind"]] = counted.get(item["kind"], 0) + 1
    return counted


async def cleanup() -> None:
    q = ",".join(str(x) for x in CREATED["quotes"]) or "0"
    o = ",".join(str(x) for x in CREATED["orders"]) or "0"
    m = f"{MARKER}%"
    statements = [
        # 报价这一棵（先删引用者，父表最后退场）
        f"delete from quote_send_logs where quote_version_id in (select id from quote_versions where quote_id in ({q}))",
        f"delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in ({q}))",
        f"delete from quote_charges where quote_version_id in (select id from quote_versions where quote_id in ({q}))",
        f"update quotes set current_version_id = null where id in ({q})",
        f"delete from quote_versions where quote_id in ({q})",
        f"delete from quotes where id in ({q})",
        # 订单这一棵
        f"delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in ({o}))",
        f"delete from order_shipment_batches where order_id in ({o})",
        f"delete from sales_order_items where order_id in ({o})",
        f"delete from receivable_plans where order_id in ({o})",
        f"delete from order_status_history where order_id in ({o})",
        f"delete from order_milestones where order_id in ({o})",
        f"delete from sales_orders where id in ({o})",
        # 案例这一棵
        f"delete from case_evidences where case_id in (select id from sales_cases where title like '{m}')",
        f"delete from sales_cases where title like '{m}'",
        f"delete from audit_logs where after_data::text like '{m}'",
    ]
    async with SessionLocal() as session:
        for sql in statements:
            try:
                await session.execute(text(sql))
            except Exception as error:  # noqa: BLE001 - 清理要尽力而为，别让一句失败挡住后面
                print(f"    （清理跳过一句：{type(error).__name__}: {error}）")
                await session.rollback()
        await session.commit()


async def main() -> int:
    import app.main
    _ = app.main

    assert urlparse(BASE).hostname in {"127.0.0.1", "localhost", "::1"}, BASE
    db = urlparse(settings.database_url)
    assert db.hostname in {"127.0.0.1", "localhost", "::1"}, db
    assert "test" in db.path.lower() or os.getenv("CI") == "true", db

    admin = login("admin", "admin123")

    from app.modules.customer.model import Customer
    from app.modules.opportunity.model import Opportunity

    async with SessionLocal() as session:
        pair = (
            await session.execute(
                select(Customer, Opportunity)
                .join(Opportunity, Opportunity.customer_id == Customer.id)
                .where(
                    Customer.deleted_at.is_(None),
                    Opportunity.deleted_at.is_(None),
                )
                .order_by(Customer.id, Opportunity.id)
                .limit(1)
            )
        ).first()
    assert pair is not None, "隔离库要先跑 scripts/seed.py（需要同一客户下的商机）"
    customer, opportunity = pair

    try:
        print("=== 0. 造夹具：两张报价 + 两张订单（同一客户）===")
        status, res = call("GET", "/pricing/sku-options", token=admin)
        sku_id = res["data"][0]["id"]

        # 报价**必须挂商机**（接口明确拒了"只给客户"的报价），且证据必须与案例同客户。
        opportunity_id = opportunity.id

        quotes: list[int] = []
        for _ in range(3):
            status, res = call(
                "POST", "/quotes", token=admin,
                body={"opportunity_id": opportunity_id, "currency": "CNY"},
            )
            if status == 200:
                qid = (res.get("data") or {}).get("quote_id")
                # 同一商机重复建会给同一张报价开新版本，那种情况返回的是同一个
                # `quote_id` —— 只收不同的单，别把"版本"当"另一张报价"。
                if qid and qid not in quotes:
                    quotes.append(qid)
                    version_id = (res.get("data") or {}).get("version_id")
                    if version_id:
                        # 正式发送口径要求报价版本显式确认物流费用；证据套件本身不测金额，
                        # 用明确的 0 元物流费用让夹具保持可发送状态。
                        charge_status, charge_res = call(
                            "POST", f"/quote-versions/{version_id}/charges", token=admin,
                            body={"charge_type": "logistics", "description": "测试夹具零运费", "amount": 0},
                        )
                        assert charge_status == 200, charge_res
            if len(quotes) >= 2:
                break
        orders: list[int] = []
        for _ in range(2):
            status, res = call(
                "POST", "/orders", token=admin,
                body={
                    "customer_id": customer.id,
                    "delivery_date": "2026-12-31",
                    "items": [{"sku_id": sku_id, "quantity": 100, "unit_price": 50}],
                },
            )
            assert status == 200, res
            orders.append(res["data"]["order_id"])
        CREATED["quotes"] = quotes
        CREATED["orders"] = orders
        print(f"    客户 {customer.name}（id={customer.id}）· 报价 {quotes} · 订单 {orders}")
        check_true("造到了两张报价单", len(quotes) >= 2, str(quotes))
        check_true("造到了两张销售订单", len(orders) == 2, str(orders))
        if len(quotes) < 2:
            # 造不出两张就照实说，后面的断言按实际张数走 —— 不假装覆盖到了
            print("    ⚠️ 只造出一张报价单，「同类多条」这一层由订单那两条顶着")

        status, res = call(
            "POST", "/cases", token=admin,
            body={
                "title": f"{MARKER} 证据完整性",
                "customer_id": customer.id,
                "customer_label": "某机械厂",
                "key_actions": "占位",
                "lessons": "占位",
            },
        )
        assert status == 200, res
        case_id = res["data"]["id"]

        payload = [
            {"kind": "quote", "business_id": qid, "label": f"报价 {qid}（作者写的说明）"}
            for qid in quotes
        ] + [
            {"kind": "order", "business_id": oid, "label": f"订单 {oid}（作者写的说明）"}
            for oid in orders
        ]
        total = len(payload)

        print("=== 1. 一次挂上多条证据 ===")
        status, res = call("PATCH", f"/cases/{case_id}", token=admin, body={"evidences": payload})
        check("挂证据成功", status, 200)
        counted = kinds_of(res["data"])
        check("证据条数 = 挂上去的条数", sum(counted.values()), total)
        check("报价类条数正确", counted.get("quote", 0), len(quotes))
        check("订单类条数正确", counted.get("order", 0), len(orders))

        print("=== 2. 脏数据必须整笔拒绝，且不能把已有证据清空（R04-c）===")
        status, res = call(
            "PATCH", f"/cases/{case_id}", token=admin,
            body={"evidences": [{"kind": "合同", "business_id": 1}]},
        )
        check("不认识的证据类型 → 422（旧写法返回 200）", status, 422)
        status, res = call("GET", f"/cases/{case_id}", token=admin)
        check("被拒之后证据**一条没少**（旧写法这里会清零）",
              sum(kinds_of(res["data"]).values()), total)

        status, res = call(
            "PATCH", f"/cases/{case_id}", token=admin,
            body={"evidences": [{"kind": "order", "business_id": None}]},
        )
        # 这一条由入参模型先拦（400），到不了服务层的 422 —— 两种都算"被拒"。
        # 真正由本次修复把关的是上一条（类型不认识）。
        check_true("缺单据编号 → 被拒（400 或 422）", status in (400, 422), f"HTTP {status}")

        print("=== 3. 改一个旧字段，别类的多条不能被截掉（R04-b）===")
        # 老调用方只认识 `order_id` 这类单值字段。它只该管自己那一类。
        status, res = call("PATCH", f"/cases/{case_id}", token=admin,
                           body={"order_id": orders[1]})
        check("改旧字段成功", status, 200)
        counted = kinds_of(res["data"])
        check("报价类**每一条都还在**（旧写法只剩第一条）", counted.get("quote", 0), len(quotes))
        check("订单类被换成指定的那一条", counted.get("order", 0), 1)
        check("总条数 = 报价全部 + 新的那一条订单",
              sum(counted.values()), len(quotes) + 1)

        print("=== 4. 修订稿要把全部证据带走（R04-a）===")
        before = sum(kinds_of(res["data"]).values())
        call("POST", f"/cases/{case_id}/submit", token=admin, body={})
        status, res = call("POST", f"/cases/{case_id}/review", token=admin,
                           body={"approve": True, "note": "通过"})
        check("案例已发布", res["data"]["status"], "published")

        status, res = call("POST", f"/cases/{case_id}/revise", token=admin)
        check("开出修订稿", status, 200)
        revision_id = res["data"]["id"]
        status, res = call("GET", f"/cases/{revision_id}", token=admin)
        after = sum(kinds_of(res["data"]).values())
        check(f"修订稿带走了全部证据（原版 {before} 条）", after, before)
        check("修订稿的报价类条数与原版一致",
              kinds_of(res["data"]).get("quote", 0), len(quotes))
        labels = [item.get("label") for item in (res["data"].get("evidences") or [])]
        check_true("作者写在证据上的说明也一并带过来了",
                   any(label and "作者写的说明" in label for label in labels), str(labels))

    finally:
        await cleanup()
        print()
        print(f"（已清理夹具：案例前缀 {MARKER}、报价 {CREATED['quotes']}、订单 {CREATED['orders']}）")

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("OK 案例证据：修订带走全部、改旧字段不截别类、脏数据整笔拒绝")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
