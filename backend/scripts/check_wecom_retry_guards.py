"""离职交接「重试」三处缺口的回归（R10）。

**只在隔离库跑**：库名必须含 test（或 CI=true）。

## 覆盖（对应审查单 R10 的 (a)(b)(c)）

**(a) 接手人在职校验没覆盖全部 CRM 类别**：首次执行时校验过，但重试可能发生在
几天之后。此前只有客户类（走 `transfer_customer`，它内部会拦）和企微关系类是安全的，
**待办/商机/打样/订单/草稿这 5 类会把活直接转给一个已停用的人**。
这里把接手人停用后重试，断言该项失败、且待办的负责人**没有**被改走。

**(b) 只锁交接任务，不锁实际业务记录**：那两张表挡不住"别人同时在待办/订单模块里
改派"。用**持锁量耗时**的确定性做法验：另起线程在独立事务里锁住那条待办，
主线程此刻发起重试 —— 修复后被挡 ≈ 持锁时长，修复前几乎立刻返回。

**(c) 外部已成功的事实没有及时落库**：企微那边一旦转过去就收不回来，而重试路径
此前全程只有一个外层 commit。这里用假企微让外部调用成功、然后**故意把外层事务
回滚**：修复后该项仍是 transferred（那次落库已经独立提交），修复前会被一起回滚掉。

跑法（需要后端在跑；本脚本只在进程内调服务层，不依赖 HTTP）：
    cd backend
    DATABASE_URL=...crm_sales_agent_test PYTHONPATH=. \\
      .venv/bin/python scripts/check_wecom_retry_guards.py

⚠️ **安全提示（第一版在这里翻过车）**：本脚本会走到企微转接分支，所以**每一处**
服务层调用都必须先把 `wecom_client._client` 换成 `FakeWeComClient` ——
本项目 `.env` 里配着**真实凭据**，不换就会真的打到 qyapi.weixin.qq.com
（实测日志里能看到 gettoken / transfer 的真实请求）。改这个脚本时别把这层替换漏掉。
"""

import asyncio
import os
import threading
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from _test_support import require_isolated_db

require_isolated_db()

from app.core.config import settings
from app.core.database import SessionLocal
from scripts.check_wecom_handover import FakeWeComClient

PREFIX = "CHKRTG"
HOLD_SECONDS = 3.0
FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def _hold_and_reassign(
    task_id: int, new_owner: int, hold_seconds: float, ready, result: dict
) -> None:
    """在独立线程 + 独立引擎里：锁住那条待办 → 持有一段时间 → **把它改派给别人** → 提交。

    模拟"重试进行到一半，别人先一步把这条待办接走了"。

    ⚠️ 必须用独立引擎（`poolclass=NullPool`）：应用那个全局 engine 是在主事件循环
    里建的，拿到子线程的 `asyncio.run()` 里用会挂在"连接绑定到另一个 loop"，
    而且**异常是在子线程里抛的、完全静默**（这个坑本项目踩过）。
    """

    async def _run() -> None:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("select id from tasks where id = :i for update"), {"i": task_id}
                )
                ready.set()
                await asyncio.sleep(hold_seconds)
                await conn.execute(
                    text("update tasks set owner_id = :o where id = :i"),
                    {"o": new_owner, "i": task_id},
                )
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as error:  # noqa: BLE001
        result["error"] = f"{type(error).__name__}: {error}"


def _system_user(user):
    from app.core.deps import CurrentUser

    return CurrentUser(user, {"wecom:manage"}, ["admin"], "all")


async def run_retry(job_id: int, admin_id: int):
    from app.modules.user.model import User
    from app.modules.wecom import client as wecom_client
    from app.modules.wecom import service as wecom_service

    # ⚠️ **必须换成假客户端**：这条回归会走到企微转接分支（`wecom_relation` 那项），
    # 用真客户端会**真的发出外部调用** —— 本项目 `.env` 里有真实凭据，
    # 实测直接打到了 qyapi.weixin.qq.com（第一版忘了换，日志里能看到）。
    # 任何涉及交接重试的脚本都得在这里把客户端换掉。
    original = wecom_client._client
    wecom_client._client = FakeWeComClient()
    try:
        async with SessionLocal() as session:
            admin = await session.get(User, admin_id)
            result = await wecom_service.retry_transfer(
                session, user=_system_user(admin), job_id=job_id
            )
            await session.commit()
        return result
    finally:
        wecom_client._client = original


async def seed_fixtures() -> dict:
    from app.modules.customer.model import Customer
    from app.modules.task.model import Task
    from app.modules.user.model import User
    from app.modules.wecom.model import (
        WeComExternalContact,
        WeComFollowRelationship,
        WeComSyncJob,
        WeComTransferItem,
    )

    stamp = int(time.time())
    async with SessionLocal() as session:
        admin = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalars().first()
        assert admin is not None, "隔离库要先跑 scripts/seed.py"

        handover = User(
            username=f"{PREFIX.lower()}_ho_{stamp}", name=f"{PREFIX}离职-{stamp}",
            password_hash="x", status="active", wecom_userid=f"{PREFIX}HO{stamp}",
        )
        takeover = User(
            username=f"{PREFIX.lower()}_to_{stamp}", name=f"{PREFIX}接手-{stamp}",
            password_hash="x", status="active", wecom_userid=f"{PREFIX}TO{stamp}",
        )
        # 第三方同事：给"并发改派"那一段用（模拟别人先把这条待办接走了）
        colleague = User(
            username=f"{PREFIX.lower()}_col_{stamp}", name=f"{PREFIX}同事-{stamp}",
            password_hash="x", status="active", wecom_userid=f"{PREFIX}COL{stamp}",
        )
        session.add_all([handover, takeover, colleague])
        await session.flush()

        customer = Customer(
            name=f"{PREFIX}客户-{stamp}", owner_id=handover.id,
            status="active", pool_status="private",
        )
        session.add(customer)
        await session.flush()
        task = Task(
            customer_id=customer.id, title=f"{PREFIX}待办-{stamp}", owner_id=handover.id,
            priority="normal", status="pending", source="manual",
        )
        session.add(task)
        await session.flush()

        job = WeComSyncJob(
            job_type="transfer", status="partial", operator_id=admin.id,
            detail={"handover_id": handover.id, "takeover_id": takeover.id},
        )
        session.add(job)
        await session.flush()

        task_item = WeComTransferItem(
            job_id=job.id, kind="task", business_id=task.id, label=f"{PREFIX}待办",
            from_owner_id=handover.id, to_owner_id=takeover.id,
            from_owner_name=handover.name, to_owner_name=takeover.name,
            crm_status="pending", wecom_status="not_applicable",
        )
        session.add(task_item)

        contact = WeComExternalContact(
            external_userid=f"{PREFIX}EXT-{stamp}", crm_customer_id=customer.id,
            name=f"{PREFIX}外部",
        )
        session.add(contact)
        await session.flush()
        relation = WeComFollowRelationship(
            external_contact_id=contact.id, wecom_userid=handover.wecom_userid,
            status="active", add_time=datetime.now(UTC), last_sync_at=datetime.now(UTC),
        )
        session.add(relation)
        await session.flush()
        rel_item = WeComTransferItem(
            job_id=job.id, kind="wecom_relation", business_id=relation.id,
            label=contact.external_userid,
            from_owner_id=handover.id, to_owner_id=takeover.id,
            from_owner_name=handover.name, to_owner_name=takeover.name,
            crm_status="not_applicable", wecom_status="pending",
        )
        session.add(rel_item)
        await session.commit()

        return {
            "admin": admin.id, "handover": handover.id, "takeover": takeover.id,
            "colleague": colleague.id,
            "customer": customer.id, "task": task.id, "job": job.id,
            "task_item": task_item.id, "rel_item": rel_item.id,
        }


async def cleanup() -> None:
    async with SessionLocal() as session:
        for sql in (
            "delete from wecom_transfer_items where job_id in "
            "(select id from wecom_sync_jobs where detail::text like :m)",
            "delete from wecom_follow_relationships where external_contact_id in "
            "(select id from wecom_external_contacts where external_userid like :p)",
            "delete from wecom_external_contacts where external_userid like :p",
            "delete from wecom_sync_jobs where detail::text like :m",
            "delete from tasks where customer_id in (select id from customers where name like :p)",
            "delete from customers where name like :p",
            "delete from users where username like :u",
        ):
            await session.execute(
                text(sql), {"p": f"{PREFIX}%", "m": f"%{PREFIX}%", "u": f"{PREFIX.lower()}%"}
            )
        await session.commit()


async def main() -> int:
    import app.main
    _ = app.main

    db = urlparse(settings.database_url)
    assert "test" in db.path.lower() or os.getenv("CI") == "true", db

    from app.modules.task.model import Task
    from app.modules.user.model import User
    from app.modules.wecom.model import WeComTransferItem

    ids = await seed_fixtures()
    print(f"夹具就绪：离职={ids['handover']} 接手={ids['takeover']} 待办={ids['task']}")

    try:
        # ---------------- (b) 重试要锁住实际业务记录 ----------------
        print()
        print("=== R10(b). 重试期间别人改了派：必须发现，不能覆盖掉 ===")
        ready = threading.Event()
        holder: dict = {}
        thread = threading.Thread(
            target=_hold_and_reassign,
            args=(ids["task"], ids["colleague"], HOLD_SECONDS, ready, holder),
            daemon=True,
        )
        thread.start()
        check_true("持锁线程已锁住那条待办", ready.wait(timeout=10))

        started = time.monotonic()
        await run_retry(ids["job"], ids["admin"])
        elapsed = time.monotonic() - started
        thread.join(timeout=10)

        check_true("持锁线程没报错", "error" not in holder, holder.get("error", ""))
        # ⚠️ 这一条是**装置自检**，不是修复的判据：数据库层的行锁本来就挡住写，
        # 应用层加不加锁都一样慢（反向验证时它照样绿，实测踩到）。
        check_true(
            "装置自检：重试确实撞上了行锁",
            elapsed >= HOLD_SECONDS * 0.66,
            f"耗时 {elapsed:.2f}s，持锁 {HOLD_SECONDS}s",
        )

        async with SessionLocal() as session:
            task_item = await session.get(WeComTransferItem, ids["task_item"])
            task = await session.get(Task, ids["task"])
        # 下面两条才是**能区分修复与否**的判据：
        # 无锁 → 读到的是进入时的旧责任人（离职人），于是把同事刚接走的活覆盖掉；
        # 加锁 + 锁内重读 → 发现责任已变，报冲突、不覆盖。
        check_true(
            "发现责任已变（别人先接走了）→ 这一项报冲突/失败",
            task_item.crm_status == "failed",
            f"crm_status={task_item.crm_status}；error={task_item.crm_error}",
        )
        check("同事刚接手的活没有被覆盖掉", task.owner_id, ids["colleague"])

        # ---------------- (a) 接手人停用必须拒绝 ----------------
        print()
        print("=== R10(a). 接手人已停用：重试必须拒绝，不能把活转给停用的人 ===")
        async with SessionLocal() as session:
            # 重置：让这一项重新回到"待办"状态，并把负责人放回离职人
            await session.execute(
                text("update wecom_transfer_items set crm_status='pending', crm_error=null"
                     " where id = :i"),
                {"i": ids["task_item"]},
            )
            await session.execute(
                text("update tasks set owner_id = :h where id = :i"),
                {"h": ids["handover"], "i": ids["task"]},
            )
            takeover = await session.get(User, ids["takeover"])
            takeover.status = "disabled"
            await session.commit()

        await run_retry(ids["job"], ids["admin"])

        async with SessionLocal() as session:
            task_item = await session.get(WeComTransferItem, ids["task_item"])
            task = await session.get(Task, ids["task"])
        check("该项重试后是 failed", task_item.crm_status, "failed")
        check_true(
            "原因说清了是接手人停用", "停用" in (task_item.crm_error or ""),
            task_item.crm_error or "",
        )
        check("待办**没有**被转给已停用的人", task.owner_id, ids["handover"])

        # ---------------- (c) 外部已成功的事实要落库 ----------------
        print()
        print("=== R10(c). 外部已转成功：外层事务回滚也不能把它抹掉 ===")
        from app.modules.wecom import client as wecom_client
        from app.modules.wecom import service as wecom_service

        async with SessionLocal() as session:
            # 让待办那一项先"办完"，把这次实验隔离到企微关系那一项上
            await session.execute(
                text("update wecom_transfer_items set crm_status='moved', crm_error=null"
                     " where id = :i"),
                {"i": ids["task_item"]},
            )
            # ⚠️ 把企微那一项**重置成"还没转"**：前面的段落已经把它办成 transferred 了，
            # 不重置这一段会被直接跳过、断言读到的是上一段的结果（假绿，实测踩到）。
            await session.execute(
                text("update wecom_transfer_items set wecom_status='pending', wecom_error=null"
                     " where id = :i"),
                {"i": ids["rel_item"]},
            )
            await session.execute(
                text("update wecom_follow_relationships set status='active' where id ="
                     " (select business_id from wecom_transfer_items where id = :i)"),
                {"i": ids["rel_item"]},
            )
            takeover = await session.get(User, ids["takeover"])
            takeover.status = "active"          # 接手人恢复在职
            await session.commit()

        admin = None
        async with SessionLocal() as session:
            admin = await session.get(User, ids["admin"])
            original = wecom_client._client
            wecom_client._client = FakeWeComClient()     # 外部调用这次会成功
            try:
                await wecom_service.retry_transfer(
                    session, user=_system_user(admin), job_id=ids["job"]
                )
                # ⚠️ 故意回滚**外层**事务：模拟"外部成功后本地后续步骤失败"
                await session.rollback()
            finally:
                wecom_client._client = original

        async with SessionLocal() as session:
            rel_item = await session.get(WeComTransferItem, ids["rel_item"])
        check(
            "外部已成功 → 状态已独立落库，不被外层回滚抹掉",
            rel_item.wecom_status, "transferred",
        )

    finally:
        await cleanup()
        print()
        print(f"（已清理夹具：{PREFIX} 前缀的用户/客户/待办/交接记录）")

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("离职交接重试三处缺口回归 全部通过")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
