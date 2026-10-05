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
from app.core.audit import write_audit
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
        if result.get("failed_rule_count"):
            logger.warning("自动任务规则配置错误，已跳过：%s", result.get("rule_errors"))
        # 月结协议到期提醒（§3.6）+ 报价有效期届满提醒（§3.4）：
        # 都挂在同一个每日任务里，不新增调度项
        from app.modules.contract import service as contract_service
        from app.modules.quote import service as quote_service
        from app.modules.task.scanning import lock_task_scan

        await lock_task_scan(session)
        expired = await contract_service.notify_expiring_monthly(session)
        expired_quotes = await quote_service.notify_expired_quotes(session)
        # 第三个时钟（§2.3）：约定的下次跟进时间到了却没联系 → 推负责人一次
        due_followups = await settings_service.notify_due_followups(session)
        await write_audit(
            session, operator_id=None, source="SCHEDULER", action="run_followup_deadlines",
            business_type="task_rule", business_id=None,
            after={"expired_quotes_count": expired_quotes, "due_followups_count": due_followups,
                   "monthly_expiring_count": expired},
        )
        await session.commit()
        from app.modules.notification import service as notification_service
        await notification_service.dispatch_pending(session)
    logger.info(
        "定时自动任务完成：生成 %s 条任务（其中 %s 个客户因\"已约定下次跟进\"豁免），"
        "月结到期提醒 %s 条，报价到期提醒 %s 条，约定跟进到期提醒 %s 条",
        result.get("created_count"),
        result.get("agreed_skipped_count"),
        expired,
        expired_quotes,
        due_followups,
    )


async def run_milestone_overdue_job() -> None:
    """每天定时：扫逾期未完成的**跟单节点**与**发货批次**，推负责人与业务主管。

    两者都在这里跑，但去重凭证各自一张表（milestone.overdue_notified_at /
    batch.overdue_notified_at）：节点与批次是两套对象，混用一个凭证会让
    "节点推了、批次就不推了"这种错沉默地发生。
    """
    from app.modules.notification import service as notification_service
    from app.modules.order import milestones as milestones_svc

    async with SessionLocal() as session:
        count = await milestones_svc.notify_overdue_milestones(session)
        batch_count = await milestones_svc.notify_overdue_batches(session)
        await session.commit()
        await notification_service.dispatch_pending(session)
    logger.info(
        "跟单逾期扫描完成：推送 %s 个逾期节点、%s 个逾期批次", count, batch_count
    )


async def run_notification_retry_job() -> None:
    """每 N 分钟：把到期该重试的企微投递失败通知再发一次（文档 §六）。

    只捞"failed 且未超上限且已到退避时间"的行——pending 由业务动作实时投递，
    skipped（没绑企微）不自动重试。到上限的行停着等设置页的人工补投，
    这里不碰，避免把确定性失败打成长期噪声。
    """
    from app.modules.notification import service as notification_service

    async with SessionLocal() as session:
        result = await notification_service.dispatch_pending(
            session, include_failed=True, limit=100
        )
    if result["attempted"]:
        logger.info(
            "通知失败重投完成：尝试 %s、成功 %s、仍失败 %s",
            result["attempted"],
            result["sent"],
            result["failed"],
        )


async def run_notification_digest_job() -> None:
    """每天一次：把 digest 级通知按收件人合成一条日报发出去（验收 24）。

    与"通知失败重投"的分工：重投管即时通道的失败补发，这里只管攒着的日报。
    没配分级策略时库里不会有 digest 行，这个任务就是一次空查询。
    """
    from app.modules.notification import service as notification_service

    result = await notification_service.send_digest()
    if result["messages"]:
        logger.info(
            "通知日报已投递：%s 人 %s 条消息，覆盖 %s 条事件（成功 %s / 未投递 %s / 失败 %s）",
            result["users"],
            result["messages"],
            result["items"],
            result["sent"],
            result["skipped"],
            result["failed"],
        )


async def run_oa_sync_job() -> None:
    """每 N 分钟：把还在审批中的 OA 实例状态拉回来（场景11 的结果回收）。

    没有实例在审批中时就是一次空查询；单个实例查询失败只记在那一行上，
    不影响其它实例，下一轮还会再试——轮询天然自愈。
    """
    from app.modules.dingtalk import service as dingtalk_service

    async with SessionLocal() as session:
        result = await dingtalk_service.sync_pending_instances(session)
        await session.commit()
    if result["checked"]:
        logger.info(
            "OA 审批状态同步：检查 %s 条，状态变更 %s 条", result["checked"], result["changed"]
        )


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
    scheduler.add_job(
        run_notification_retry_job,
        "interval",
        minutes=settings.scheduler_retry_minutes,
        id="notification_retry",
        name="通知失败重投（周期）",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        run_notification_digest_job,
        "cron",
        hour=settings.scheduler_digest_hour,
        minute=settings.scheduler_digest_minute,
        id="notification_digest",
        name="通知日报（每日）",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_oa_sync_job,
        "interval",
        minutes=settings.scheduler_oa_sync_minutes,
        id="oa_sync",
        name="OA 审批状态同步（周期）",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.start()
    logger.info(
        "定时任务调度已启动：公海回收每天 %02d:00、自动任务规则每天 %02d:10、"
        "跟单逾期提醒每天 %02d:20、通知失败重投每 %s 分钟、"
        "通知日报每天 %02d:%02d（Asia/Shanghai）",
        settings.scheduler_recycle_hour,
        settings.scheduler_task_rules_hour,
        settings.scheduler_task_rules_hour,
        settings.scheduler_retry_minutes,
        settings.scheduler_digest_hour,
        settings.scheduler_digest_minute,
    )


def stop_scheduler() -> None:
    """应用关闭时调用，避免残留线程。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("定时任务调度已停止")
