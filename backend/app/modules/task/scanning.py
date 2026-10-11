"""定时与手动扫描共用的事务锁，避免查询去重后并发创建两份待办。"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_task_scan(session: AsyncSession) -> None:
    # SQLite（内存测试/本地开发）没有 PostgreSQL advisory lock；扫描在单写者
    # 环境下无需这把跨进程锁，生产 PostgreSQL 才启用它防止重复扫描。
    try:
        dialect = session.get_bind().dialect.name
    except Exception:  # noqa: BLE001
        dialect = ""
    if dialect == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('crm:task-scan', 0))"))
