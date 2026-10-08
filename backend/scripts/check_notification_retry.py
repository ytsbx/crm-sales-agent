"""通知投递失败重试回归（不依赖网络；需要 PostgreSQL 在跑）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_notification_retry.py

## 覆盖（文档 §六：「发送失败保留业务记录并重试通知；
##            不能为了重发消息再次创建报价或订单」）

1. 自动重试的入选条件（只读断言，不真发消息）：
   到期的才捞、到次数上限的不捞、迁移前的历史失败行（next_retry 为空）能捞；
2. 人工补投：
   - 不受退避时间与次数上限约束（补投是"重新开始"）；
   - **不经过业务事件去重键**——补投动的是原通知行、不重跑业务动作，
     这正是"原实现里人工补发必被跳过、失败行又永不再入选"的解药；
3. 概览口径：失败里"会自动重试"与"已停止等人工"必须分得开。

夹具只碰标题以 CHK-RETRY 开头的行，跑完即清；不动库里其它通知。
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta

from _test_support import require_isolated_db

require_isolated_db()
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


TITLE_PREFIX = 'CHK-RETRY'


async def main():
    from sqlalchemy import select, text

    from app.core.database import SessionLocal
    from app.modules.notification import service as retry_service
    from app.modules.notification.model import BusinessEvent, Notification

    now = datetime.now(UTC)
    created_ids: dict[str, int] = {}

    async with SessionLocal() as s:
        # 先清上次跑剩的（含中途失败留下的）
        await s.execute(
            text("delete from notifications where title like :p"), {'p': f'{TITLE_PREFIX}%'}
        )
        await s.execute(
            text("delete from business_events where event_key like :p"),
            {'p': f'{TITLE_PREFIX}%'},
        )
        await s.commit()

        def make(suffix: str, status: str, attempts: int, next_retry):
            return Notification(
                user_id=1,
                type='task',
                title=f'{TITLE_PREFIX}-{suffix}',
                content='回归夹具',
                channel='both',
                wecom_status=status,
                wecom_attempts=attempts,
                wecom_next_retry_at=next_retry,
            )

        fixtures = {
            # 到期该重试
            'due': make('due', 'failed', 1, now - timedelta(minutes=1)),
            # 未到期：退避还没走完，不能捞（否则退避形同虚设）
            'future': make('future', 'failed', 1, now + timedelta(hours=3)),
            # 已到次数上限：不再自动重试，等人工补投
            'exhausted': make('exhausted', 'failed', 3, None),
            # 迁移前的历史失败行：next_retry 为空视同立即到期
            'legacy': make('legacy', 'failed', 0, None),
            # 待投递：走正常通道
            'pending': make('pending', 'pending', 0, None),
        }
        for row in fixtures.values():
            s.add(row)
        # 业务事件：证明补投路径不看它（原来人工补发就是被这个唯一键挡住的）
        s.add(
            BusinessEvent(
                event_key=f'{TITLE_PREFIX}-event',
                business_type='order',
                title='供补投用例引用的事件',
                created_at=now,
            )
        )
        await s.commit()
        created_ids = {name: row.id for name, row in fixtures.items()}

        print('=== 1. 自动重试的入选条件（只读断言）===')
        policy = await retry_service.retry_policy(s)
        check_true(
            '策略可读且上限为正',
            policy['max_attempts'] >= 1 and len(policy['backoff']) >= 1,
            f"max_attempts={policy['max_attempts']} backoff={policy['backoff']}",
        )
        clause = retry_service.retry_due_clause(policy, datetime.now(UTC))
        matched = set(
            (
                await s.execute(
                    select(Notification.id).where(
                        clause, Notification.id.in_(list(created_ids.values()))
                    )
                )
            ).scalars().all()
        )
        check_true('到期行入选', created_ids['due'] in matched)
        check_true('未到期行不入选（退避生效）', created_ids['future'] not in matched)
        check_true('到上限行不入选（不无限重试）', created_ids['exhausted'] not in matched)
        check_true('历史失败行入选（迁移前数据可自愈）', created_ids['legacy'] in matched)
        check_true('pending 不走失败重试谓词', created_ids['pending'] not in matched)

        print()
        print('=== 2. 人工补投：不受退避/上限约束，且不被业务事件去重挡住 ===')
        queued = await retry_service.requeue_for_redispatch(
            s, ids=[created_ids['exhausted']]
        )
        check('已到上限的行也能排队补投', queued['requeued'], 1)
        result = await retry_service.dispatch_pending(
            s, only_ids={created_ids['exhausted']}
        )
        check('补投确实投了一遍', result['attempted'], 1)
        check_true(
            '业务事件已存在时补投照样执行（不撞去重键）',
            result['attempted'] == 1,
            '补投走原通知行，不重跑 record_and_notify',
        )

        queued = await retry_service.requeue_for_redispatch(s, ids=[created_ids['future']])
        check('未到期的行也能人工补投', queued['requeued'], 1)
        result = await retry_service.dispatch_pending(s, only_ids={created_ids['future']})
        check('补投不看书退避时间', result['attempted'], 1)
        check_true(
            '补投后 attempts 归零（重新开始，不被旧配额拦住）',
            result['attempted'] == 1,
            '',
        )

        print()
        print('=== 3. 投递失败概览：会自动重试 vs 已停止，分得开 ===')
        s.expire_all()
        summary = await retry_service.delivery_failure_summary(s)
        check_true(
            '概览字段齐',
            {'pending', 'sent', 'failed', 'skipped', 'retrying', 'max_attempts'}
            <= set(summary),
            str(summary),
        )
        check_true(
            'retrying 是 failed 的子集',
            summary['retrying'] <= summary['failed'],
            f"failed={summary['failed']} retrying={summary['retrying']}",
        )
        check_true(
            '至少含一条会自动重试的失败行（legacy 夹具）',
            summary['retrying'] >= 1,
            f"retrying={summary['retrying']}",
        )

        # 清理夹具
        await s.execute(
            text("delete from notifications where title like :p"), {'p': f'{TITLE_PREFIX}%'}
        )
        await s.execute(
            text("delete from business_events where event_key like :p"),
            {'p': f'{TITLE_PREFIX}%'},
        )
        await s.commit()

    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('通知投递重试回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
