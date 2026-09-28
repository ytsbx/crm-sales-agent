"""进程内定时任务调度器（APScheduler AsyncIOScheduler）。

背景：公海回收（run_public_pool_recycle）与自动任务规则（run_auto_tasks）
的执行引擎早就有，但只有设置页的手动按钮能触发——没人点就永远不跑，
配了的规则形同虚设。这里把"手点按钮"换成"每天到点自动点"。

设计取舍：
- 用 AsyncIOScheduler 把任务挂到应用自己的事件循环上，任务里直接用
  应用的 SessionLocal——不要在 sync job 里 asyncio.run 另起事件循环，
  那会踩"连接池绑定主循环"的坑（10 号文档第八节 / check 脚本注释都有记录）；
- 任务函数就是 settings/service 里那两个实现本身（不写第二份实现），
  operator_id 传 None、source 传 SCHEDULER，审计里能看出"这是系统自动跑的"；
- 多实例部署时只能让一台跑：scheduler_enabled=False 整体关掉（部署脚本/环境变量控制）。

加新周期任务的姿势：写一个 async job 函数 → start_scheduler 里 add_job 一行。
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.settings import service as settings_service

logger = logging.getLogger("crm.scheduler")

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def run_public_pool_recycle_job() -> None:
    """每天定时：按公海回收规则把长期未跟进客户释放回公海。"""
    async with SessionLocal() as session:
        result = await settings_service.run_public_pool_recycle(
            session, operator_id=None, source="SCHEDULER"
        )
    logger.info(
        "定时公海回收完成：释放 %s 个客户（规则 %s 条命中）",
        result.get("released_count"),
        len(result.get("rules_hit") or result.get("customers") or []),
    )


async def run_auto_tasks_job() -> None:
    """每天定时：按自动任务规则生成跟进任务（同一对象不会重复生成）。"""
    async with SessionLocal() as session:
        result = await settings_service.run_auto_tasks(
            session, operator_id=None, source="SCHEDULER"
        )
    logger.info("定时自动任务完成：生成 %s 条任务", result.get("created_count"))


async def run_milestone_overdue_job() -> None:
    """每天定时：扫描逾期未完成的跟单节点，推负责人与业务主管（每节点只推一次）。"""
    from app.modules.notification import service as notification_service
    from app.modules.order import milestones as milestones_svc

    async with SessionLocal() as session:
        count = await milestones_svc.notify_overdue_milestones(session)
        await session.commit()
        await notification_service.dispatch_pending(session)
    logger.info("跟单逾期扫描完成：推送 %s 个逾期节点", count)


def start_scheduler() -> None:
    """应用启动时调用：注册周期任务并启动调度器。

    max_instances=1：上一轮还没跑完不允许并发第二轮；coalesce：错过多次
    触发只补跑一次（比如机器停了一天，开机后不会连环补跑）。
    """
    if not settings.scheduler_enabled:
        logger.info("定时任务调度已关闭（SCHEDULER_ENABLED=false），公海回收/自动任务需手动触发")
        return

    scheduler.add_job(
        run_public_pool_recycle_job,
        "cron",
        hour=settings.scheduler_recycle_hour,
        minute=0,
        id="public_pool_recycle",
        name="公海回收（每日）",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_auto_tasks_job,
        "cron",
        hour=settings.scheduler_task_rules_hour,
        minute=10,
        id="auto_task_rules",
        name="自动任务规则（每日）",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_milestone_overdue_job,
        "cron",
        hour=settings.scheduler_task_rules_hour,
        minute=20,
        id="milestone_overdue",
        name="跟单逾期提醒（每日）",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info(
        "定时任务调度已启动：公海回收每天 %02d:00、自动任务规则每天 %02d:10（Asia/Shanghai）",
        settings.scheduler_recycle_hour,
        settings.scheduler_task_rules_hour,
    )


def stop_scheduler() -> None:
    """应用关闭时调用，避免残留线程。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("定时任务调度已停止")
