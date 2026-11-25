"""定时与手动扫描共用的事务锁，避免查询去重后并发创建两份待办。"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def _advisory_scan_lock(session: AsyncSession, key: str) -> None:
    # SQLite（内存测试/本地开发）没有 PostgreSQL advisory lock；扫描在单写者
    # 环境下无需这把跨进程锁，生产 PostgreSQL 才启用它防止重复扫描。
    try:
        dialect = session.get_bind().dialect.name
    except Exception:  # noqa: BLE001
        # B22-12：探测失败不能静默——PG 上跳过锁可能重复建待办，留日志便于排查
        logger.warning("task-scan 方言探测失败，跳过 advisory lock", exc_info=True)
        dialect = ""
    if dialect == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )


async def lock_task_scan(session: AsyncSession) -> None:
    await _advisory_scan_lock(session, "crm:task-scan")


async def lock_recycle_scan(session: AsyncSession) -> None:
    """B23-P1：公海回收扫描专用防重入锁（与任务扫描互不阻塞）。"""
    await _advisory_scan_lock(session, "crm:pool-recycle")
