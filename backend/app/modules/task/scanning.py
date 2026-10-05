"""定时与手动扫描共用的事务锁，避免查询去重后并发创建两份待办。"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_task_scan(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('crm:task-scan', 0))"))
