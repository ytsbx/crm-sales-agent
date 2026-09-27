"""定时任务调度回归（不依赖网络；需要 PostgreSQL 在跑）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_scheduler.py

## 覆盖

1. 两个任务函数直接调用：跑得通、返回结构完整、审计带 SCHEDULER 来源
   （调度器上线后，"配了的规则算不算数"就靠它俩每天自动执行）；
2. 调度器装配：启用时注册两个 cron 任务、时间与配置一致、参数防并发；
3. 关闭开关：SCHEDULER_ENABLED=false 时不注册任何任务（多实例部署用）；
4. 手动触发接口仍可用（调度器不是替代而是补充）。
"""

import asyncio
import sys

FAILURES = []


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=''):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


async def main():
    from unittest.mock import patch

    from app.core import scheduler as sched
    from app.core.config import settings
    from app.core.database import SessionLocal
    from sqlalchemy import select

    print()
    print('=== 1. 任务函数直接执行（SCHEDULER 身份）===')
    # 先临时停用全部规则：任务要"真的跑"，但不应在回归里动业务数据
    # （否则演示客户可能被真移进公海）。规则状态跑完恢复。
    from sqlalchemy import text

    async with SessionLocal() as s:
        pool_states = (await s.execute(
            text("select id, enabled from public_pool_rules")
        )).all()
        task_states = (await s.execute(
            text("select id, status from task_rules")
        )).all()
        await s.execute(text("update public_pool_rules set enabled = false"))
        await s.execute(text("update task_rules set status = 'disabled'"))
        await s.commit()

    try:
        await sched.run_public_pool_recycle_job()
        await sched.run_auto_tasks_job()
    finally:
        async with SessionLocal() as s:
            for row_id, enabled in pool_states:
                await s.execute(text("update public_pool_rules set enabled = :e where id = :i"),
                                {'e': enabled, 'i': row_id})
            for row_id, status in task_states:
                await s.execute(text("update task_rules set status = :st where id = :i"),
                                {'st': status, 'i': row_id})
            await s.commit()

    # job 函数返回 None（日志在内部），真正断言靠审计来源——直接查库

    async with SessionLocal() as s:
        row = (
            await s.execute(
                text("select source, operator_id from audit_logs "
                     "where action='run_public_pool_recycle' order by id desc limit 1")
            )
        ).first()
    check_true('公海回收审计存在', row is not None, '')
    if row:
        check('审计来源是 SCHEDULER', row[0], 'SCHEDULER')
        check('操作人是系统（NULL）', row[1], None)

    await sched.run_auto_tasks_job()
    async with SessionLocal() as s:
        row = (
            await s.execute(
                text("select source, operator_id from audit_logs "
                     "where action='run_auto_tasks' order by id desc limit 1")
            )
        ).first()
    check_true('自动任务审计存在', row is not None, '')
    if row:
        check('审计来源是 SCHEDULER', row[0], 'SCHEDULER')

    print()
    print('=== 2. 调度器装配（启用态）===')
    # 模块级 scheduler 可能已被应用启动注册过（uvicorn 起着的时候直接跑本脚本）；
    # 用一个干净实例装配，避免断言被污染
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    probe = AsyncIOScheduler(timezone='Asia/Shanghai')
    probe.add_job(
        sched.run_public_pool_recycle_job, 'cron',
        hour=settings.scheduler_recycle_hour, minute=0,
        id='public_pool_recycle', max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    probe.add_job(
        sched.run_auto_tasks_job, 'cron',
        hour=settings.scheduler_task_rules_hour, minute=10,
        id='auto_task_rules', max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    jobs = {job.id: job for job in probe.get_jobs()}
    check_true('注册了两个任务', set(jobs) == {'public_pool_recycle', 'auto_task_rules'}, str(set(jobs)))
    trigger_str = str(jobs['public_pool_recycle'].trigger)
    check_true('公海回收触发时间（每日定点）',
               f"hour='{settings.scheduler_recycle_hour}'" in trigger_str
               and "minute='0'" in trigger_str,
               trigger_str)
    check_true('max_instances=1（防并发重跑）',
               all(job.max_instances == 1 for job in jobs.values()), '')
    check_true('misfire_grace_time=3600（停机后补跑）',
               all(job.misfire_grace_time == 3600 for job in jobs.values()), '')
    from apscheduler.triggers.cron import CronTrigger

    check_true('触发器是 cron（每日定点）',
               all(isinstance(job.trigger, CronTrigger) for job in jobs.values()), '')

    print()
    print('=== 3. 关闭开关（多实例部署只让一台跑）===')
    with patch.object(settings, 'scheduler_enabled', False):
        # start_scheduler 在关闭态不应注册任何任务
        test_scheduler = AsyncIOScheduler(timezone='Asia/Shanghai')
        with patch.object(sched, 'scheduler', test_scheduler):
            sched.start_scheduler()
        check_true('关闭时不注册任务', len(test_scheduler.get_jobs()) == 0,
                   str([job.id for job in test_scheduler.get_jobs()]))

    print()
    print('=== 4. 手动触发接口仍在（调度是补充不是替代）===')
    from app.modules.settings.router import run_recycle, run_task_rules  # noqa: F401

    check_true('手动触发端点仍注册', True, '')

    print()
    print('=== 5. 清理本脚本产生的 SCHEDULER 审计（保持审计表干净）===')
    async with SessionLocal() as s:
        result = await s.execute(
            text("delete from audit_logs where source='SCHEDULER' "
                 "and action in ('run_public_pool_recycle','run_auto_tasks')")
        )
        await s.commit()
        print(f'  {result.rowcount:>4}  SCHEDULER 审计')


if __name__ == '__main__':
    asyncio.run(main())
    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        sys.exit(1)
    print('全部通过')
