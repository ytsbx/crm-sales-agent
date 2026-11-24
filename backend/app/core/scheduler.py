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
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.audit import write_audit
from app.modules.settings import service as settings_service

logger = logging.getLogger("crm.scheduler")

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def run_public_pool_recycle_job() -> None:
    """每天定时：按公海回收规则生成**回收预告**（不直接回收，返工单 6.3）。

    老实现是扫到就直接清空负责人，不可逆。现在只提名，
    等主管在「回收待复核」里批了才真的回收 —— 而且批准时还会再检查一遍
    这期间有没有新的履约事项。

    ⚠️ 与自动任务一样靠 `lock_task_scan` 防重入：同一进程里两个扫描同时跑
    会互相抢连接、也可能重复提名（虽然有唯一索引兜底）。
    """
    async with SessionLocal() as session:
        result = await settings_service.run_public_pool_recycle(
            session, operator_id=None, source="SCHEDULER"
        )
    logger.info(
        "定时公海回收扫描完成：提名 %s 个待复核（履约中豁免 %s 个）",
        result.get("nominated_count"),
        result.get("protected_count"),
    )


async def run_auto_tasks_job() -> None:
    """每日扫描分步提交，单项失败时继续处理其他提醒。"""
    from app.modules.contract import service as contract_service
    from app.modules.contract.customer_flow import remind_countersigns
    from app.modules.notification import service as notification_service
    from app.modules.payment import service as payment_service
    from app.modules.quote import service as quote_service
    from app.modules.task.scanning import lock_task_scan

    failed_steps: list[str] = []

    async def run_step(name, action, /, *, lock_scan=False, **kwargs):
        try:
            async with SessionLocal() as session:
                if lock_scan:
                    await lock_task_scan(session)
                result = await action(session, **kwargs)
                await session.commit()
                return result
        except Exception:
            failed_steps.append(name)
            logger.exception("每日自动任务步骤失败：%s", name)
            return None

    refreshed = await run_step("应收逾期刷新", payment_service.refresh_overdue_plans, commit_per_order=True)
    result = await run_step("生成自动任务", settings_service.run_auto_tasks, operator_id=None, source="SCHEDULER")
    if result and result.get("failed_rule_count"):
        logger.warning("自动任务规则配置错误，已跳过：%s", result.get("rule_errors"))

    countersigns = await run_step("合同回签提醒", remind_countersigns, lock_scan=True)
    expired = await run_step("月结到期提醒", contract_service.notify_expiring_monthly, lock_scan=True)
    expired_quotes = await run_step("报价到期提醒", quote_service.notify_expired_quotes)
    due_followups = await run_step("约定跟进提醒", settings_service.notify_due_followups)
    await run_step(
        "每日扫描审计", write_audit,
        operator_id=None, source="SCHEDULER", action="run_followup_deadlines",
        business_type="task_rule", business_id=None,
        after={"expired_quotes_count": expired_quotes, "due_followups_count": due_followups,
               "monthly_expiring_count": expired, "countersign_due_count": countersigns,
               "overdue_plans_refreshed": refreshed, "failed_steps": list(failed_steps)},
    )
    await run_step("通知投递", notification_service.dispatch_pending)
    logger.info(
        "定时自动任务结束：刷新逾期应收 %s 个节点，生成 %s 条任务（约定跟进豁免 %s 个客户），"
        "合同回签提醒 %s 条，月结到期提醒 %s 条，报价到期提醒 %s 条，约定跟进到期提醒 %s 条，失败步骤：%s",
        refreshed, result.get("created_count") if result is not None else None,
        result.get("agreed_skipped_count") if result is not None else None,
        countersigns, expired, expired_quotes, due_followups, failed_steps,
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
    """每 N 分钟：补发待投递的即时企微通知，并按策略重试到期的失败通知。

    日报级待投递通知由日报任务处理。已跳过或重试到达上限的行走人工补投。
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
    logger.info(
        "OA 审批状态同步完成：检查 %s 条，状态变更 %s 条", result["checked"], result["changed"]
    )


def _register_oa_sync_job() -> None:
    scheduler.add_job(
        run_oa_sync_job,
        "interval",
        minutes=settings.scheduler_oa_sync_minutes,
        id="oa_sync",
        name="OA 审批状态同步（周期）",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        next_run_time=datetime.now(UTC),
    )


def start_scheduler() -> None:
    """应用启动时调用：注册周期任务并启动调度器。

    max_instances=1：上一轮还没跑完不允许并发第二轮；coalesce：错过多次
    触发只补跑一次（比如机器停了一天，开机后不会连环补跑）。
    """
    from app.modules.wecom import client as wecom_client, service as wecom_service

    wecom = wecom_client.get_client()
    wecom_events_enabled = wecom.callback_ready() and wecom.external_ready()
    if wecom_events_enabled:
        async def sync_wecom_events() -> None:
            async with SessionLocal() as session:
                await wecom_service.sync_contact_events(session)

        scheduler.add_job(sync_wecom_events, "interval", minutes=1, id="wecom_contact_events",
                          name="企微联系人变更同步", max_instances=1, coalesce=True)

    if not settings.scheduler_enabled:
        if settings.scheduler_oa_sync_enabled:
            _register_oa_sync_job()
        if settings.scheduler_oa_sync_enabled or wecom_events_enabled:
            scheduler.start()
        logger.info("通用定时任务已关闭；OA 自动同步：%s；企微变更同步：%s",
                    settings.scheduler_oa_sync_enabled, wecom_events_enabled)
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
    _register_oa_sync_job()
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
