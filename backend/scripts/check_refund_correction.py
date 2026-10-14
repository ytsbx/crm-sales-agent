#!/usr/bin/env python3
"""真库端到端：**同一笔外部退款"先核定 100、再更正为 200"**（第十批 ①）。

## 报的问题

同一外部退款单，先核定 100 元，再更正为 200 元并重新核定，生成两条 `pending`
台账：100 + 200 = **300 元**。目前只按差异 ID 去重，更正产生新差异后就绕过去了。

## 为什么会绕过

差异指纹 `build_diff_key` **把金额与期间也算进哈希** —— 这是差异队列的正确设计
（值变了必须出新的一条，否则"上次已核定"会把新问题一起吞掉）。于是"把 100 更正为
200"必然生成**新差异**，`source_diff_id` 换了，只按它建的唯一约束就绕过去了。

差异保留版本历史是对的，但**台账不能按差异版本各记一笔**。

## 修法

`financial_adjustments` 另加 `source_key`（系统+店铺+对象类型+来源单，**不含金额与期间**）
+ **部分唯一索引**（只管 `status='pending'`）：
  · 同来源**还没处理** → **更新那一条**的金额并写明更正痕迹（不新增）；
  · 同来源**已处理过** → 不占位，允许新增（那是真正的差额）。

## 结论（脚本自己判定）

  ① 第一次核定 100 → 一条 pending、金额 100；
  ② **把金额更正为 200 并重新核定 → 仍然只有一条 pending、金额变成 200**
     （不是 100 + 200 = 300）；且台账带更正痕迹；
  ③ 该台账被财务处理掉之后，再来一次更正（250）→ **允许新增**一条 pending，
     金额是新的 250（那是差额，需要财务单独处理）；
  ④ 差异记录本身**保留了两条**（历史不该被抹掉）—— 与"台账只有一条"并不矛盾。

跑法（一次性库 + 隔离后端）：

    DB=crm_check_refund_correction
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8077 &
    API_BASE=http://127.0.0.1:8077/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \\
      .venv/bin/python scripts/check_refund_correction.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_isolated_db  # noqa: E402

require_isolated_db()

from app.core.database import SessionLocal  # noqa: E402
from app.modules.erp import reconcile as rec  # noqa: E402
from app.modules.integration.model import (  # noqa: E402
    ExternalRecord,
    FinancialAdjustment,
    IntegrationDiff,
)
from app.modules.integration.vocab import (  # noqa: E402
    OBJECT_AFTERSALE,
    OBJECT_AFTERSALE_ITEM,
)
from app.modules.customer.model import Customer  # noqa: E402
from app.modules.user.model import User  # noqa: E402,F401  # 注册映射用
from sqlalchemy import delete, select  # noqa: E402

PREFIX = "CHKREFUND"
SYSTEM = "JIANDAOYUN"
RESULTS: list[tuple[str, bool, str]] = []


def check_true(label: str, value, detail: str = "") -> None:
    ok = bool(value)
    RESULTS.append((label, ok, detail))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}{('：' + detail) if detail else ''}")


def check(label: str, value, expected) -> None:
    ok = value == expected
    RESULTS.append((label, ok, f"{value!r}（期望 {expected!r}）"))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {value!r}（期望 {expected!r}）")


async def upsert_aftersale(session, *, shop: str, code: str, amount: str, order_no: str) -> None:
    """写一条"外部退款"事实（售后单头 + 明细），采集适配器未接通时用它造数。

    与 `check_erp_external_sync.py` 同一手法：直接写 `external_records`，
    再跑 `run_reconciliation` 让它按业务规则出差异。
    """
    # ⚠️ **必须先有一条外部订单**：`load_period_facts` 是先从 `object_type=order`
    # 的记录算出 `order_keys`，再用它去捞 shipment / aftersale 单头
    # （`if order_keys:` 才查）。少了订单，售后记录压根不会被读到 ——
    # 实测第一步"售后差异已生成"一直是 0 条，就是这里。
    now0 = datetime.now(UTC)
    order_row = (
        await session.execute(
            select(ExternalRecord).where(
                ExternalRecord.system_type == SYSTEM,
                ExternalRecord.shop_id == shop,
                ExternalRecord.object_type == "order",
                ExternalRecord.dedupe_key == order_no,
            )
        )
    ).scalar_one_or_none()
    if order_row is None:
        session.add(
            ExternalRecord(
                system_type=SYSTEM,
                shop_id=shop,
                object_type="order",
                dedupe_key=order_no,
                external_id=order_no,
                amount=Decimal("500"),
                currency="CNY",
                raw_digest=hashlib.sha256(order_no.encode()).hexdigest(),
                occurred_at=now0,
                first_seen_at=now0,
                last_seen_at=now0,
                collected_count=1,
            )
        )
        await session.flush()

    head_key = f"{code}"
    existing = (
        await session.execute(
            select(ExternalRecord).where(
                ExternalRecord.system_type == SYSTEM,
                ExternalRecord.shop_id == shop,
                ExternalRecord.object_type == OBJECT_AFTERSALE,
                ExternalRecord.dedupe_key == head_key,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is None:
        session.add(
            ExternalRecord(
                system_type=SYSTEM,
                shop_id=shop,
                object_type=OBJECT_AFTERSALE,
                dedupe_key=head_key,
                external_id=code,
                external_code=order_no,
                #: `order_key` 指向**外部订单号**（reconcile 用它找本地单）。
                #: 售后差异的生成会读 `head.order_key` 或明细行的 order_key；
                #: 不带的话差异压根不会生成（实测第一步就是 0 条）。
                order_key=order_no,
                #: `raw_digest` 非空列（实测踩到）；按 collection 的口径取内容摘要，
                #: 让"金额更正"在摘要上也能体现出来。
                raw_digest=hashlib.sha256(f"{code}|{amount}".encode()).hexdigest(),
                #: 售后单头没有 kind（差异逻辑只对**明细行**读 kind：
                #: return / exchange，见 reconcile.py 的 line.kind 分支）。
                amount=Decimal(amount),
                currency="CNY",
                occurred_at=now,
                first_seen_at=now,
                last_seen_at=now,
                collected_count=1,
            )
        )
    else:
        # ⚠️ 更正金额 = 同一条外部事实改了值（真实系统里就是单据被改了）
        existing.amount = Decimal(amount)
        existing.last_seen_at = now
        existing.collected_count = (existing.collected_count or 1) + 1
    # 明细行：售后差异的 `order_keys` 是从**明细行**取的，所以每条售后都要配一行。
    # `kind` 用 "return"（退货）—— reconcile 对 return/exchange 有分支。
    line = (
        await session.execute(
            select(ExternalRecord).where(
                ExternalRecord.system_type == SYSTEM,
                ExternalRecord.shop_id == shop,
                ExternalRecord.object_type == OBJECT_AFTERSALE_ITEM,
                ExternalRecord.line_key == f"{head_key}:L1",
            )
        )
    ).scalar_one_or_none()
    if line is None:
        session.add(
            ExternalRecord(
                system_type=SYSTEM,
                shop_id=shop,
                object_type=OBJECT_AFTERSALE_ITEM,
                dedupe_key=f"{head_key}:L1",
                parent_key=head_key,
                order_key=order_no,
                line_key=f"{head_key}:L1",
                kind="return",
                external_id=f"{code}-L1",
                quantity=Decimal("1"),
                amount=Decimal(amount),
                currency="CNY",
                raw_digest=hashlib.sha256(f"{code}-L1|{amount}".encode()).hexdigest(),
                occurred_at=now,
                first_seen_at=now,
                last_seen_at=now,
                collected_count=1,
            )
        )
    else:
        line.amount = Decimal(amount)
        line.raw_digest = hashlib.sha256(f"{code}-L1|{amount}".encode()).hexdigest()
        line.last_seen_at = now
    await session.flush()


async def open_diffs(session, *, shop: str) -> list:
    rows = (
        await session.execute(
            select(IntegrationDiff)
            .where(
                IntegrationDiff.system_type == SYSTEM,
                IntegrationDiff.shop_id == shop,
                IntegrationDiff.diff_type == rec.DIFF_AFTERSALE_REVIEW,
            )
            .order_by(IntegrationDiff.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def pending_adjustments(session, *, source_key_like: str) -> list[FinancialAdjustment]:
    rows = (
        await session.execute(
            select(FinancialAdjustment)
            .where(
                FinancialAdjustment.status == "pending",
                FinancialAdjustment.reason.like(f"%{source_key_like}%"),
            )
            .order_by(FinancialAdjustment.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def main() -> int:  # noqa: C901
    shop = f"{PREFIX}SHOP"
    code = f"{PREFIX}-AS-{uuid.uuid4().hex[:6]}"
    today = date.today()
    period_start = today.replace(day=1)
    period_end = date(today.year + 1, 1, 1) if today.month == 12 else date(
        today.year, today.month + 1, 1
    )

    async with SessionLocal() as session:
        customer = Customer(
            name=f"{PREFIX}客户{uuid.uuid4().hex[:6]}", owner_id=1, status="active", level="A"
        )
        session.add(customer)
        await session.flush()
        customer_id = customer.id
        await session.commit()

    print("\n=== ① 第一次：外部退款 100 → 核定 → 一条 pending 台账 ===")
    async with SessionLocal() as session:
        await upsert_aftersale(session, shop=shop, code=code, amount="100", order_no="SO-REFUND-E2E")
        await session.commit()
    async with SessionLocal() as session:
        await rec.run_reconciliation(
            session, period_start=period_start, period_end=period_end,
            shop_id=shop, system_type=SYSTEM,
        )
        await session.commit()
    async with SessionLocal() as session:
        diffs = await open_diffs(session, shop=shop)
    check_true("① 售后差异已生成", len(diffs) >= 1, f"{len(diffs)} 条")
    if not diffs:
        return 1
    # 差异里挑"待核定来源与时点"那条（售后复核）
    target = diffs[0]

    async with SessionLocal() as session:
        diff = await session.get(IntegrationDiff, target.id)
        await rec.confirm_diff(
            session, diff, resolution="manual", note=f"人工核定售后 {code}",
            financial_adjustment_type="refund", financial_adjustment_amount=Decimal("100"),
            operator_id=1,
        )
        await session.commit()
    async with SessionLocal() as session:
        pending1 = [
            row
            for row in (
                await session.execute(
                    select(FinancialAdjustment).where(
                        FinancialAdjustment.status == "pending",
                        FinancialAdjustment.order_id.is_(None)
                        | (FinancialAdjustment.order_id == diff.internal_id),
                    )
                )
            ).scalars().all()
        ]
    check("① 第一次核定后有且只有一条 pending", len(pending1), 1)
    check("① 台账金额", str(pending1[0].amount), "100.00")
    first_key = pending1[0].source_key
    check_true("① 台账带稳定业务身份（source_key）", bool(first_key), f"{first_key}")
    first_id = pending1[0].id

    print("\n=== ② 更正为 200 并重新核定 → **仍只有一条**、金额变 200（不是 300）===")
    async with SessionLocal() as session:
        await upsert_aftersale(session, shop=shop, code=code, amount="200", order_no="SO-REFUND-E2E")
        await session.commit()
    async with SessionLocal() as session:
        await rec.run_reconciliation(
            session, period_start=period_start, period_end=period_end,
            shop_id=shop, system_type=SYSTEM,
        )
        await session.commit()
    async with SessionLocal() as session:
        diffs2 = await open_diffs(session, shop=shop)
    check_true("② 更正后差异记录**保留了两条**（历史不抹）", len(diffs2) >= 2, f"{len(diffs2)} 条")
    # 核定**新的**那条差异（模拟"更正后重新核定"）
    new_diff = [d for d in diffs2 if d.id != target.id][-1]
    async with SessionLocal() as session:
        diff = await session.get(IntegrationDiff, new_diff.id)
        await rec.confirm_diff(
            session, diff, resolution="manual", note=f"人工核定售后 {code} 更正为 200",
            financial_adjustment_type="refund", financial_adjustment_amount=Decimal("200"),
            operator_id=1,
        )
        await session.commit()
    async with SessionLocal() as session:
        pending2 = (
            await session.execute(
                select(FinancialAdjustment).where(
                    FinancialAdjustment.source_key == first_key
                )
            )
        ).scalars().all()
    pending_now = [row for row in pending2 if row.status == "pending"]
    check("★同来源仍只有一条 pending（不是 100 + 200）", len(pending_now), 1)
    check("★台账金额被更正为", str(pending_now[0].amount), "200.00")
    check("★台账 id 未变（是更新，不是新增）", pending_now[0].id, first_id)
    check_true(
        "★说明里留了更正痕迹",
        "更正" in (pending_now[0].reason or ""),
        f"{pending_now[0].reason[:60]}",
    )
    total = sum(Decimal(str(row.amount or 0)) for row in pending_now)
    check("★待处理金额合计（修前是 300）", str(total), "200.00")

    print("\n=== ③ 台账已被财务处理掉之后，再更正 → 允许新增一条 ===")
    async with SessionLocal() as session:
        row = await session.get(FinancialAdjustment, first_id)
        row.status = "processed"
        await session.commit()
    async with SessionLocal() as session:
        await upsert_aftersale(session, shop=shop, code=code, amount="250", order_no="SO-REFUND-E2E")
        await session.commit()
    async with SessionLocal() as session:
        await rec.run_reconciliation(
            session, period_start=period_start, period_end=period_end,
            shop_id=shop, system_type=SYSTEM,
        )
        await session.commit()
    async with SessionLocal() as session:
        diffs3 = await open_diffs(session, shop=shop)
    newest = [d for d in diffs3 if d.id not in {target.id, new_diff.id}][-1]
    async with SessionLocal() as session:
        diff = await session.get(IntegrationDiff, newest.id)
        await rec.confirm_diff(
            session, diff, resolution="manual", note=f"人工核定售后 {code} 更正为 250",
            financial_adjustment_type="refund", financial_adjustment_amount=Decimal("250"),
            operator_id=1,
        )
        await session.commit()
    async with SessionLocal() as session:
        all_rows = (
            await session.execute(
                select(FinancialAdjustment)
                .where(FinancialAdjustment.source_key == first_key)
                .order_by(FinancialAdjustment.id.asc())
            )
        ).scalars().all()
    pending3 = [row for row in all_rows if row.status == "pending"]
    check("③ 已处理后允许新增一条 pending", len(pending3), 1)
    check("③ 新台账金额", str(pending3[0].amount), "250.00")
    check("③ 该来源共两条台账（一已处理 + 一待处理）", len(all_rows), 2)
    check("③ 第一条仍是 processed（历史不改）", all_rows[0].status, "processed")

    # 清理本套件造的数据（订单/客户/差异/台账/外部记录）
    async with SessionLocal() as session:
        for row in all_rows:
            await session.delete(row)
        for diff in diffs3:
            await session.delete(await session.get(IntegrationDiff, diff.id))
        await session.execute(
            delete(ExternalRecord).where(
                ExternalRecord.shop_id == shop, ExternalRecord.system_type == SYSTEM
            )
        )
        await session.execute(delete(Customer).where(Customer.id == customer_id))
        await session.commit()

    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：退款更正只更新同一条待处理台账，"
          "差异历史仍保留；已处理之后才允许新增差额")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
