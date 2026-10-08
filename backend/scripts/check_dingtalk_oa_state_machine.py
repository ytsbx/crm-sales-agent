"""OA 结果未知的并发核定（第七批 7.7/7.8）—— **真 PostgreSQL** 回归。

跑法（必须显式给一次性隔离库；脚本自己拒绝默认库/开发库）：
    cd backend
    $env:DATABASE_URL = 'postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/crm_iso_oa'
    $env:PYTHONPATH = '.'
    .\\.venv\\Scripts\\python.exe scripts/check_dingtalk_oa_state_machine.py

前置：先在**这个库**上跑 `alembic upgrade head`（要 e6a7b8c9d0e1 的列与两个唯一索引）。
外部调用一律打桩（`service.get_client` 换成假客户端），**全程不连钉钉、不发真实请求**。

## 为什么这条必须用真库

SQLite（单元测试）只能证明"逻辑上做了比较再写"，证明不了 PostgreSQL 的行锁与
唯一索引：两个连接的 `SELECT ... FOR UPDATE` 会阻塞到对方提交、然后读到最新版本，
这才是"并发核定只有一个能建外部实例"的真正保证。所以这里用**两个独立连接**跑同一行。

锁住的四件事：
1. 双连接并发核定：外部创建只发生一次，另一个拿到明确的 409；
2. 中途重启可恢复：僵死占用被接管时**不盲目再建**，放掉占用后下一次才能真正重发；
3. 请求键表里的死占位（进程被杀留下的 in_flight）能被判定并清掉，
   而"真的在处理中"的占位绝不能被误清；
4. 一个外部实例只能关联一行——由数据库唯一索引兜底，不是靠应用层判断。
"""

import asyncio
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

FAILURES: list[str] = []
PREFIX = 'CHKOA'

#: 核定占用的僵死阈值比实现里的 10 分钟更长：这里只表达"明显已经死了"
STALE_MINUTES = 30


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=''):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)
from _test_support import require_isolated_db

require_isolated_db()


def require_isolated_db() -> str:
    """显式要求一次性隔离库。

    这条回归会**真的建表数据、并发写库**，所以绝不能落在默认库/开发库上：
    与 `ops/iso_checks.ps1` 同一套判据（库名必须以 crm_iso / crm_check 开头），
    而且**没有 DATABASE_URL 就直接退出**，不给"悄悄连到默认库"留机会。
    """
    url = (os.environ.get('DATABASE_URL') or '').strip()
    if not url:
        raise SystemExit(
            '必须显式设置 DATABASE_URL（一次性隔离库，库名以 crm_iso / crm_check 开头）。'
            '例：postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/crm_iso_oa'
        )
    name = url.rsplit('/', 1)[-1].split('?')[0]
    if not name.startswith(('crm_iso', 'crm_check')):
        raise SystemExit(f'拒绝执行：DATABASE_URL 指向 {name!r}，不是一次性隔离库')
    return name


class FakeClient:
    """假钉钉客户端：只记调用次数，并可把"调外部"这一段撑开，暴露并发窗口。"""

    def __init__(self) -> None:
        self.calls = 0
        #: 撑开并发窗口用（真实环境里是网络往返的几十~几百毫秒）
        self.delay = 0.0

    async def create_process_instance(self, **_kwargs) -> str:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return f'FAKE-{self.calls}'


def fake_request():
    """够 `client_ip()` / `request_key_from()` 用的最小 Request。"""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/dingtalk/oa-instances/0/resolve",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )


async def cleanup():
    """自底向上清扫：先子表后父表，避免外键/残留。"""
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        # 审计先删（它按 oa_instances.id 关联，父表删掉后就找不到这些行了）
        await s.execute(
            text(
                "delete from audit_logs where business_type = 'oa_instance' "
                "and business_id in (select id from oa_instances where inquiry_id in "
                "(select id from custom_inquiries where inquiry_no like :p))"
            ),
            {'p': f'{PREFIX}%'},
        )
        await s.execute(
            text(
                "delete from request_keys where request_key like :p"
            ),
            {'p': f'{PREFIX}%'},
        )
        await s.execute(
            text(
                "delete from oa_instances where inquiry_id in "
                "(select id from custom_inquiries where inquiry_no like :p)"
            ),
            {'p': f'{PREFIX}%'},
        )
        await s.execute(text('delete from custom_inquiries where inquiry_no like :p'),
                        {'p': f'{PREFIX}%'})
        await s.execute(text('delete from customers where name like :p'), {'p': f'{PREFIX}%'})
        await s.commit()


async def preflight(session) -> bool:
    """先确认迁移真的跑过：列与唯一索引都在，否则并发结论没有意义。"""
    from sqlalchemy import text

    columns = set(
        (
            await session.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_name = 'oa_instances' and column_name in "
                    "('resolve_state', 'resolve_request_key', 'resolve_claimed_at', "
                    "'resolved_at', 'resolved_action', 'attempt_count')"
                )
            )
        ).scalars().all()
    )
    expected_columns = {
        'resolve_state',
        'resolve_request_key',
        'resolve_claimed_at',
        'resolved_at',
        'resolved_action',
        'attempt_count',
    }
    missing_columns = expected_columns - columns
    check_true(
        '迁移已跑（7.8 的核定占用列都在）',
        not missing_columns,
        f'缺 {sorted(missing_columns)}' if missing_columns else '',
    )

    indexes = set(
        (
            await session.execute(
                text(
                    "select indexname from pg_indexes where tablename = 'oa_instances' "
                    "and indexname in ('uq_oa_instance_instance_id', "
                    "'uq_oa_instance_resolve_key')"
                )
            )
        ).scalars().all()
    )
    expected_indexes = {'uq_oa_instance_instance_id', 'uq_oa_instance_resolve_key'}
    missing_indexes = expected_indexes - indexes
    check_true(
        '迁移已跑（实例唯一 / 核定请求键唯一索引都在）',
        not missing_indexes,
        f'缺 {sorted(missing_indexes)}；未被唯一索引保护的并发是测不出来的'
        if missing_indexes
        else '',
    )
    return not missing_columns and not missing_indexes


async def main() -> int:
    db_name = require_isolated_db()
    print(f'目标隔离库：{db_name}')

    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.core.deps import CurrentUser
    from app.core.errors import AppError, ErrorCode
    from app.core.idempotency import RequestKey
    from app.modules.customer.model import Customer
    from app.modules.dingtalk import service as dt
    from app.modules.dingtalk import router as dt_router
    from app.modules.dingtalk.model import OaInstance
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.user.model import User

    fake = FakeClient()
    # 打桩：service 模块里 `get_client` 是模块级引用，要替换它的名字
    real_get_client = dt.get_client
    dt.get_client = lambda: fake
    # 总闸只在本进程内打开——**客户端是假的，不会有任何真实请求**
    settings.dingtalk_push_off = False

    stamp = int(time.time())
    await cleanup()

    admin = None
    inquiry = None
    async with SessionLocal() as s:
        if not await preflight(s):
            print()
            print('FAILED 迁移前置不满足：先 alembic upgrade head，再跑本脚本')
            return 1
        admin = (await s.execute(select(User).where(User.username == 'admin'))).scalars().one()
        user = CurrentUser(admin, permissions=set(), roles=[], data_scope='all')
        customer = Customer(name=f'{PREFIX}客户-{stamp}', level='A', status='active',
                            pool_status='private', owner_id=admin.id)
        s.add(customer)
        await s.flush()
        inquiry = CustomInquiry(inquiry_no=f'{PREFIX}{stamp}', title='并发核定回归',
                                version=1, quantity=Decimal('1'), status='open',
                                customer_id=customer.id, created_by=admin.id)
        s.add(inquiry)
        await s.commit()
        inquiry_id = inquiry.id

    async def make_review_row(version: int, **overrides) -> int:
        """造一行"结果待人工核对"的记录（本轮用一个版本号隔离场景）。"""
        async with SessionLocal() as s:
            now = datetime.now(UTC)
            fields = dict(
                customer_id=None,
                inquiry_id=inquiry_id,
                inquiry_version=version,
                oa_type='inquiry',
                idempotency_key=f'{inquiry_id}:{version}:inquiry:1',
                submit_round=1,
                process_code='PROC-FAKE',
                originator_user_id='fake-user',
                form_snapshot={'formComponentValues': []},
                status='needs_review',
                created_by=admin.id,
                created_at=now,
                last_attempt_at=now,
            )
            fields.update(overrides)
            row = OaInstance(**fields)
            s.add(row)
            await s.commit()
            return row.id

    async def read(oa_id: int) -> OaInstance:
        async with SessionLocal() as s:
            return await s.get(OaInstance, oa_id)

    try:
        print('=== 1. 双连接并发核定：外部创建只能有一次（7.8 命门）===')
        oa_id = await make_review_row(1)
        fake.delay = 0.3  # 撑开窗口：旧实现里两个请求都会走到这一步
        s1, s2 = SessionLocal(), SessionLocal()

        async def one(session, key):
            row = await session.get(OaInstance, oa_id)
            # 两个请求"几乎同时到达"：先让对手也把 needs_review 读出来
            await asyncio.sleep(0)
            try:
                return await dt.resolve_reviewed_instance(
                    session, row, action='resend', request_key=key
                )
            except AppError as exc:
                return exc

        calls_before = fake.calls
        try:
            results = await asyncio.gather(
                one(s1, f'{PREFIX}-K-A'), one(s2, f'{PREFIX}-K-B')
            )
        finally:
            await s1.close()
            await s2.close()
        check('两个并发核定只产生一次外部创建', fake.calls - calls_before, 1)
        conflicts = [r for r in results if isinstance(r, AppError)]
        check('另一个请求拿到 409 冲突', len(conflicts), 1)
        if conflicts:
            check('冲突错误码', conflicts[0].code, ErrorCode.VERSION_CONFLICT)
        after = await read(oa_id)
        check('落库状态', after.status, 'pending')
        check('只关联了一个实例号', after.attempt_count, 1)
        check('占用已放掉', after.resolve_state, 'idle')

        print('=== 2. 中途重启：僵死占用接管不盲目再建，之后才允许重发 ===')
        stuck_id = await make_review_row(
            2,
            resolve_state='processing',
            resolve_request_key=f'{PREFIX}-DEAD',
            resolve_claimed_at=datetime.now(UTC) - timedelta(minutes=STALE_MINUTES),
        )
        fake.delay = 0.0
        calls_before = fake.calls
        async with SessionLocal() as s:
            row = await s.get(OaInstance, stuck_id)
            try:
                await dt.resolve_reviewed_instance(
                    s, row, action='resend', request_key=f'{PREFIX}-TAKEOVER-1'
                )
            except AppError as exc:
                check('僵死占用被接管时拒绝盲目重发', exc.http_status, 409)
                check_true('要求先到钉钉核实', '先到钉钉' in exc.message, exc.message[:80])
            else:
                check_true('僵死占用必须拒绝盲目重发', False)
        check('没有偷偷调外部', fake.calls, calls_before)
        after = await read(stuck_id)
        check('状态保持"结果未知"', after.status, 'needs_review')
        check('占用已放掉（否则永远核不了）', after.resolve_state, 'idle')

        # 第二次是**明确的人工重发**：占用已空闲，这次应当真的发出去
        async with SessionLocal() as s:
            row = await s.get(OaInstance, stuck_id)
            recovered = await dt.resolve_reviewed_instance(
                s, row, action='resend', request_key=f'{PREFIX}-TAKEOVER-2'
            )
        check('重新核定后真的重发（外部调用 +1）', fake.calls, calls_before + 1)
        check('恢复后状态 pending', recovered.status, 'pending')

        print('=== 3. 请求键表的死占位：能判定、能清掉，但不误清在处理中的 ===')
        key_idle = f'{PREFIX}-RESV-IDLE'
        key_live = f'{PREFIX}-RESV-LIVE'
        live_id = await make_review_row(
            3,
            resolve_state='processing',
            resolve_request_key=key_live,
            resolve_claimed_at=datetime.now(UTC),
        )
        async with SessionLocal() as s:
            s.add(RequestKey(user_id=admin.id, action=dt_router.RESOLVE_ACTION,
                             request_key=key_idle, request_hash='x', status='in_flight',
                             created_at=datetime.now(UTC)))
            s.add(RequestKey(user_id=admin.id, action=dt_router.RESOLVE_ACTION,
                             request_key=key_live, request_hash='x', status='in_flight',
                             created_at=datetime.now(UTC)))
            await s.commit()

            idle_row = await s.get(OaInstance, stuck_id)  # 这一行的占用已经空闲
            live_row = await s.get(OaInstance, live_id)  # 这一行正在处理中（未僵死）
            dropped = await dt_router._drop_stale_reservation(
                s, user_id=admin.id, key=key_idle, row=idle_row
            )
            check('空闲行的死占位被清掉（重启后可恢复）', dropped, True)
            kept = await dt_router._drop_stale_reservation(
                s, user_id=admin.id, key=key_live, row=live_row
            )
            check('处理中的占位绝不能被误清', kept, False)
            left = set(
                (
                    await s.execute(
                        select(RequestKey.request_key).where(
                            RequestKey.request_key.in_([key_idle, key_live])
                        )
                    )
                ).scalars().all()
            )
            check('被清掉的键确实不在了', key_idle in left, False)
            check('在处理中的键还在', key_live in left, True)

        print('=== 4. 一个外部实例只能关联一行（数据库唯一索引兜底）===')
        first_holder = await make_review_row(4)
        second_holder = await make_review_row(5)
        async with SessionLocal() as s:
            row = await s.get(OaInstance, first_holder)
            row.instance_id = f'{PREFIX}-SAME-INSTANCE'
            row.status = 'pending'
            await s.commit()
        duplicated = False
        async with SessionLocal() as s:
            row = await s.get(OaInstance, second_holder)
            row.instance_id = f'{PREFIX}-SAME-INSTANCE'
            row.status = 'pending'
            try:
                await s.commit()
            except Exception as exc:  # noqa: BLE001 —— 这里就是要看它炸
                duplicated = True
                print(f'  （预期的唯一约束冲突：{type(exc).__name__}）')
                await s.rollback()
        check('同一个钉钉实例被第二行关联时数据库拒绝', duplicated, True)
        after = await read(second_holder)
        check('被拒的那一行没有实例号', after.instance_id, None)

        print('=== 5. 关闸时核定必须拒绝且不改记录 ===')
        settings.dingtalk_push_off = True
        try:
            gate_id = await make_review_row(6)
            before = await read(gate_id)
            snapshot = (before.status, before.error, before.instance_id,
                        before.resolve_state, before.attempt_count)
            calls_before = fake.calls
            async with SessionLocal() as s:
                row = await s.get(OaInstance, gate_id)
                try:
                    await dt.resolve_reviewed_instance(
                        s, row, action='resend', request_key=f'{PREFIX}-GATE'
                    )
                except AppError as exc:
                    check('关闸重发返回 403', exc.http_status, 403)
                    check('关闸重发错误码', exc.code, ErrorCode.FORBIDDEN)
                else:
                    check_true('关闸重发必须拒绝', False)
            check('关闸重发不调外部', fake.calls, calls_before)
            after = await read(gate_id)
            check(
                '关闸重发不改结果未知的原记录',
                (after.status, after.error, after.instance_id,
                 after.resolve_state, after.attempt_count),
                snapshot,
            )
        finally:
            settings.dingtalk_push_off = False

        print('=== 6. 接口层：同一请求键重放不重复创建 ===')
        replay_id = await make_review_row(7)

        async def call_resolve(session, oa_id, key, action='resend', instance_id=None):
            return await dt_router.resolve_oa_instance(
                oa_id=oa_id,
                payload=dt_router.ResolveOa(action=action, instance_id=instance_id,
                                            request_key=key),
                request=fake_request(),
                user=user,
                session=session,
            )

        fake.delay = 0.0
        calls_before = fake.calls
        async with SessionLocal() as s:
            first = await call_resolve(s, replay_id, f'{PREFIX}-API-K')
        async with SessionLocal() as s:
            second = await call_resolve(s, replay_id, f'{PREFIX}-API-K')
        check('接口层同键只产生一次外部创建', fake.calls - calls_before, 1)
        check_true('第二次是回放（提示里说明未再发起）',
                   '重放' in (second.get('message') or ''), second.get('message'))
        check('回放返回同一份结果',
              second['data']['instance_id'], first['data']['instance_id'])

        print('=== 7. 接口层：双连接并发核定只有一个能建外部实例 ===')
        race_id = await make_review_row(8)
        fake.delay = 0.3
        s1, s2 = SessionLocal(), SessionLocal()
        calls_before = fake.calls
        try:
            outcomes = await asyncio.gather(
                call_resolve(s1, race_id, f'{PREFIX}-API-RACE-A'),
                call_resolve(s2, race_id, f'{PREFIX}-API-RACE-B'),
                return_exceptions=True,
            )
        finally:
            await s1.close()
            await s2.close()
        check('接口层并发：外部创建只有一次', fake.calls - calls_before, 1)
        rejected = [o for o in outcomes if isinstance(o, AppError)]
        check('另一个请求收到明确冲突', len(rejected), 1)
        if rejected:
            check('冲突用 409 表达', rejected[0].http_status, 409)
        after = await read(race_id)
        check('落库只有一条实例关联', after.attempt_count, 1)
        check('状态 pending', after.status, 'pending')

    finally:
        dt.get_client = real_get_client  # 还原替身，避免影响同进程后续调用
        await cleanup()

    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print(f'OA 并发核定回归 全部通过（真库 {db_name}，全程未连钉钉）')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
