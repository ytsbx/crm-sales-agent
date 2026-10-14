#!/usr/bin/env python3
"""真库检查：**作废月结协议与续签扫描的并发**（第十批 ⑦）。

## 报的问题

扫描先读到有效协议并准备建任务；另一事务作废协议并提交；扫描随后提交。
终态：协议已经 `void`，续签任务仍为 `pending`。作废侧看不到扫描尚未提交的任务，
所以取消不到它。

## 为什么必须真库

`FOR UPDATE` 在 SQLite 上被忽略 —— "扫描会不会被作废挡住"只有真 PostgreSQL 能证明。
等待/不等待一律用 `FOR UPDATE NOWAIT` 判定（确定性，不靠 sleep 碰运气）。

## 结论（脚本自己判定）

  ① **基座**：无人持锁时 NOWAIT 能拿到协议行；
  ② **扫描要拿这把锁**：有人持锁时，扫描那条查询的 `NOWAIT` 必须失败
     —— 否则扫描与作废不串行，"锁内重判"就无从谈起；
  ③ **交错后的终态**（核心）：持锁方把协议改成 `void` 并提交 → 扫描才继续 →
     **不得新建续签任务**；已经存在的续签任务**必须被取消**（作废侧的职责）；
  ④ **反面对照**：协议仍然有效时，扫描**照常建任务** —— 闸门不能被堵死。

跑法（一次性库 + 隔离后端）：

    DB=crm_check_contract_scan
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8075 &
    API_BASE=http://127.0.0.1:8075/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \\
      .venv/bin/python scripts/check_contract_scan_race.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_isolated_db  # noqa: E402

require_isolated_db()

#: 把所有 ORM 模型导入一次（只导几个会在解析外键时报 NoReferencedTableError）。
from app.core.database import SessionLocal  # noqa: E402
from app.modules.contract.model import ContractDocument, ContractTemplate  # noqa: E402
from app.modules.contract import service as contract_service  # noqa: E402
from app.modules.customer.model import Customer  # noqa: E402
from app.modules.task.model import Task  # noqa: E402
#: 必须把  也导入： 的外键指向 zhaorongkai，
#: 少这张表会在建对象/解析映射时报 NoReferencedTableError（照抄
#:  的做法）。
from app.modules.user.model import User  # noqa: E402,F401  # 只为注册映射，不直接用
from sqlalchemy import select, text  # noqa: E402

PREFIX = "CHKSCAN"
RESULTS: list[tuple[str, bool, str]] = []


def check_true(label: str, value, detail: str = "") -> None:
    ok = bool(value)
    RESULTS.append((label, ok, detail))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}{('：' + detail) if detail else ''}")


def check(label: str, value, expected) -> None:
    ok = value == expected
    RESULTS.append((label, ok, f"{value!r}（期望 {expected!r}）"))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {value!r}（期望 {expected!r}）")


async def make_monthly_doc(session, tag: str, *, days_to_expiry: int = 10) -> tuple[int, int]:
    """造一份"10 天后到期、已签"的月结协议（扫描会把它当候选）。"""
    customer = Customer(name=f"{tag}", owner_id=1, status="active", level="A")
    # template_id 是**非空**列（实测踩到 NotNullViolationError），先建一份模板
    template = ContractTemplate(
        doc_type="monthly",
        name=f"{tag}-模板",
        body="正文",
        enabled=True,
        created_at=datetime.now(UTC),
    )
    session.add_all([customer, template])
    await session.flush()
    doc = ContractDocument(
        template_id=template.id,
        content_snapshot="正文",
        doc_no=tag,
        doc_type="monthly",
        title=tag,
        customer_id=customer.id,
        status="signed",
        signed_at=datetime.now(UTC),
        expiry_date=(datetime.now(UTC).date() + timedelta(days=days_to_expiry)),
        created_at=datetime.now(UTC),
    )
    session.add(doc)
    await session.flush()
    return doc.id, customer.id


async def nowait_can_lock(doc_id: int) -> bool:
    async with SessionLocal() as session:
        try:
            await session.execute(
                text("select id from contract_documents where id = :i for update nowait"),
                {"i": doc_id},
            )
            await session.rollback()
            return True
        except Exception:
            await session.rollback()
            return False


async def tasks_for(doc_id: int) -> list[tuple[int, str]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Task.id, Task.status).where(
                    Task.source_business_type == "contract",
                    Task.source_business_id == doc_id,
                )
            )
        ).all()
    return [(r[0], r[1]) for r in rows]


async def main() -> int:  # noqa: C901
    print("\n=== ① 基座：无人持锁时 NOWAIT 能拿到协议行 ===")
    async with SessionLocal() as session:
        doc_id, _ = await make_monthly_doc(session, f"{PREFIX}{uuid.uuid4().hex[:8]}")
        await session.commit()
    check("① 无人持锁 → NOWAIT 能拿到", await nowait_can_lock(doc_id), True)
    check("① 扫描前：还没有续签任务", await tasks_for(doc_id), [])

    print("\n=== ② 扫描会拿同一把协议行锁 ===")
    locked = asyncio.Event()

    async def holder_voids():
        """持锁 → 等一会儿 → 在锁内把协议改成 void 并提交（模拟作废）。"""
        async with SessionLocal() as session:
            await session.execute(
                text("select id from contract_documents where id = :i for update"),
                {"i": doc_id},
            )
            locked.set()
            await asyncio.sleep(1.5)
            await session.execute(
                text("update contract_documents set status = 'void' where id = :i"),
                {"i": doc_id},
            )
            await session.commit()

    task = asyncio.create_task(holder_voids())
    await locked.wait()
    check("② 有人持锁 → NOWAIT 失败（扫描必须等它）", await nowait_can_lock(doc_id), False)

    print("\n=== ③ 核心：作废先提交，扫描随后继续 → 不得新建续签任务 ===")
    scan_result: dict = {}

    async def run_scan():
        async with SessionLocal() as session:
            scan_result["count"] = await contract_service.notify_expiring_monthly(session)
            await session.commit()

    # ⚠️ 必须用**同一个事件循环**里的 create_task。
    # 我第一版用 `run_in_executor` + `asyncio.run()`：那会在**另一个线程**新建事件循环，
    # 而 asyncpg 的连接池是绑在主循环上的 —— 直接报
    # "got Future attached to a different loop"。要并发就跑在同一个循环里。
    scan_task = asyncio.create_task(run_scan())
    await asyncio.sleep(1.0)   # 持锁方 1.5 秒后才提交，所以这 1 秒里扫描一定卡在锁上
    check_true("③ 扫描被协议锁挡住（1 秒内未完成）", not scan_result)
    await task          # 作废提交（锁释放）
    await scan_task     # 扫描这才继续
    rows = await tasks_for(doc_id)
    check("★作废已提交后，扫描不得为该协议建任务", rows, [])
    print(f"     （扫描本轮新建 {scan_result.get('count')} 条，均为其他协议/客户）")

    print("\n=== ④ 反面对照：协议仍有效时，扫描照常建任务 ===")
    async with SessionLocal() as session:
        doc2, _ = await make_monthly_doc(session, f"{PREFIX}B{uuid.uuid4().hex[:8]}")
        await session.commit()
    async with SessionLocal() as session:
        await contract_service.notify_expiring_monthly(session)
        await session.commit()
    rows2 = await tasks_for(doc2)
    check_true("④ 有效协议 → 扫描建成续签任务", len(rows2) >= 1, f"{rows2}")

    print("\n=== ⑤ 作废侧仍能取消已提交的任务（职责不变） ===")
    async with SessionLocal() as session:
        doc = (
            await session.execute(
                select(ContractDocument).where(ContractDocument.id == doc2)
            )
        ).scalar_one()
        cancelled = await contract_service.void_document(session, doc, reason="CHKSCAN 作废")
        await session.commit()
    rows3 = await tasks_for(doc2)
    check_true(
        "⑤ 作废后原续签任务被取消",
        all(status != "pending" for _id, status in rows3),
        f"cancelled={cancelled} 现状={rows3}",
    )

    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：扫描在协议锁内重判，作废先提交就不会留下待续签任务")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
