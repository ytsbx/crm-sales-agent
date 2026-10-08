"""钉钉发起幂等 + 状态机回归（第七批 7.7/7.8）。

跑法（需要 PostgreSQL；**不连钉钉、不发任何真实请求**）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_dingtalk_idempotency.py

## 为什么必须用假客户端

这条路径唯一"真实"的验证方式是往钉钉发一张审批单——那会打扰 3 位同事
（发起人 + 两位审批人）。所以这里把 `service.get_client` 换成假的：
**记录调用次数、可模拟失败**，于是"会不会重复建单"这件事可以本地断言，
对外零请求。

## 锁住的口径

7.7「状态 → 允许动作」：
1. 第一次发起：调外部一次，拿到实例号；
2. **同一轮重复发起：不再调外部**（复用已有行）——"网络重试不重复建单"的落点；
3. **待审批 / 已通过不许重提**：重提不是"再发一次"而是"另建一张单"，
   那两种状态下钉钉那边正有一张活单，另建就是重复实例；
4. 驳回后重提（resubmit）：新的一轮，允许再调一次——业务主动重提，不是重复；
5. **三种后果分开**：结果未知（超时）→ `needs_review` 且不自动重发；
   明确失败（4xx）→ `failed`，同轮可以重试（**不是死路**）。

7.8「结果未知的核定」：
6. 人工 `resend` 才真的重发；**同一请求键回放不再调外部**；
7. 认领（adopt）先核实模板/发起人/来源需求，**对不上就拒绝采纳**；
8. 接管僵死占用（上次核定中断）时**不盲目再建**，明确报冲突。

夹具带 CHKIDEM 前缀，跑完即清。
"""

import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from _test_support import require_isolated_db

require_isolated_db()

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.user.model import User

FAILURES = []
PREFIX = 'CHKIDEM'

#: 核定占用的僵死阈值（与 service.RESOLVE_CLAIM_STUCK_AFTER 同量级，这里取 30 分钟
#: 只是为了"明显已经死了"，不依赖实现里的具体常量值）
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


class FakeClient:
    """假钉钉客户端：只记调用次数。**不连网、不建任何单。**"""

    def __init__(self) -> None:
        self.calls = 0
        #: 下一次发起要抛的异常（None = 成功）。用异常对象而不是 bool，
        #: 因为 7.7 的关键就是"不同异常 → 不同后果"，布尔表达不了
        self.fail: BaseException | None = None
        #: 认领要核实的实例内容（按 instance_id 预置，没有就报 404）
        self.instances: dict[str, dict] = {}

    async def create_process_instance(self, **_kwargs) -> str:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return f'FAKE-{self.calls}'

    async def get_process_instance(self, instance_id: str) -> dict:
        from app.modules.dingtalk.client import DingTalkError

        if instance_id not in self.instances:
            raise DingTalkError(
                '查询不到这张审批单', api='workflow/processInstances:get', http_status=404
            )
        return self.instances[instance_id]


async def cleanup():
    async with SessionLocal() as s:
        await s.execute(text(
            "delete from oa_instances where inquiry_id in "
            "(select id from custom_inquiries where inquiry_no like :p)"
        ), {'p': f'{PREFIX}%'})
        await s.execute(text('delete from custom_inquiries where inquiry_no like :p'),
                        {'p': f'{PREFIX}%'})
        await s.execute(text('delete from customers where name like :p'), {'p': f'{PREFIX}%'})
        await s.commit()


async def main() -> int:
    from app.core.config import settings
    from app.core.errors import AppError, ErrorCode
    from app.modules.customer.model import Customer
    from app.modules.dingtalk import service as dt
    from app.modules.dingtalk.client import DingTalkError, DingTalkUnknownOutcome
    from app.modules.dingtalk.model import OaInstance
    from app.modules.inquiry.model import CustomInquiry

    fake = FakeClient()
    # 打桩：service 模块里 `get_client` 是模块级引用，所以要替换它的名字
    dt.get_client = lambda: fake
    # 总闸只在本进程内打开——**因为客户端是假的，不会有任何真实请求**
    settings.dingtalk_push_off = False

    stamp = int(time.time())
    await cleanup()

    async with SessionLocal() as s:
        admin = (await s.execute(select(User).where(User.username == 'admin'))).scalars().one()
        user = CurrentUser(admin, permissions=set(), roles=[], data_scope='all')
        customer = Customer(name=f'{PREFIX}客户-{stamp}', level='A', status='active',
                            pool_status='private', owner_id=admin.id)
        s.add(customer)
        await s.flush()
        inquiry = CustomInquiry(inquiry_no=f'{PREFIX}{stamp}', title='幂等回归', version=1,
                                quantity=Decimal('1'), status='open',
                                customer_id=customer.id, created_by=admin.id)
        s.add(inquiry)
        await s.commit()

        def submit(**overrides):
            kwargs = dict(
                user=user, inquiry_id=inquiry.id, inquiry_version=1,
                customer_id=customer.id, process_code='PROC-FAKE',
                originator_user_id='fake-user', field_map={'t': 'v'},
            )
            kwargs.update(overrides)
            return dt.create_inquiry_instance(s, **kwargs)

        async def rows_in_round(version: int) -> int:
            return int((await s.execute(
                text('select count(*) from oa_instances where inquiry_id = :i and inquiry_version = :v'),
                {'i': inquiry.id, 'v': version},
            )).scalar() or 0)

        def extra_row(version: int, **overrides) -> OaInstance:
            """直接造一行"待人工核对"的记录（模拟历史遗留/并发入口留下的行）。"""
            key = f'{inquiry.id}:{version}:inquiry:1'
            fields = dict(
                customer_id=customer.id, inquiry_id=inquiry.id, inquiry_version=version,
                oa_type='inquiry', idempotency_key=key, submit_round=1,
                process_code='PROC-FAKE', originator_user_id='fake-user',
                form_snapshot={'formComponentValues': []}, status='needs_review',
                created_by=admin.id, created_at=datetime.now(UTC),
                last_attempt_at=datetime.now(UTC),
            )
            fields.update(overrides)
            return OaInstance(**fields)

        print('=== 1. 第一次发起 ===')
        first = await submit()
        check('外部调用次数', fake.calls, 1)
        check('状态', first.status, 'pending')
        check_true('拿到实例号', bool(first.instance_id), f'instance_id={first.instance_id}')
        check('尝试次数', first.attempt_count, 1)
        check_true('请求号已留痕', bool(first.idempotency_key), first.idempotency_key)

        print('=== 2. 同一轮重复发起：不该再调外部（防重复建单）===')
        again = await submit()
        check('外部调用次数仍是 1', fake.calls, 1)
        check('复用同一行', again.id, first.id)

        print('=== 3. 待审批重复点 + 重提：必须被拒（7.7 的核心）===')
        calls_before = fake.calls
        try:
            await submit(resubmit=True)
        except AppError as exc:
            check('待审批重提被拒', exc.code, ErrorCode.STATUS_NOT_ALLOWED)
            check_true('报错说清了为什么不能重提', '重提会在钉钉里再建一张单' in exc.message,
                       exc.message[:80])
            check_true('报错同时给了现在能做什么', '当前可以做的操作' in exc.message,
                       exc.message[:80])
        else:
            check_true('待审批重提必须被拒', False, '重提会在钉钉里另建一张单')
        check('被拒的重提没有调外部', fake.calls, calls_before)
        check('也没有悄悄多落一行', await rows_in_round(1), 1)

        print('=== 4. 驳回后重提：换一轮，允许再发一次 ===')
        first.status = 'rejected'
        await s.commit()
        resubmitted = await submit(resubmit=True)
        check('外部调用次数', fake.calls, calls_before + 1)
        check('轮次', resubmitted.submit_round, 2)
        check_true('是新的一行（旧轮次保留）', resubmitted.id != first.id,
                   f'{first.id} vs {resubmitted.id}')
        check('旧轮状态没被改写', first.status, 'rejected')

        print('=== 5. 结果未知（客户端超时）：不自动重发，转人工 ===')
        fake.fail = DingTalkUnknownOutcome('模拟超时：响应没回来')
        unknown = await submit(inquiry_version=2)
        calls_at_unknown = fake.calls
        check('状态是"结果待人工核对"而不是"发起失败"', unknown.status, 'needs_review')
        check_true('说明点明可能已建单', '可能已经建了审批单' in (unknown.error or ''),
                   str(unknown.error)[:70])
        retried_unknown = await submit(inquiry_version=2)
        check('结果未知时不自动重发（外部调用次数没变）', fake.calls, calls_at_unknown)
        check('复用的是那行 needs_review', retried_unknown.id, unknown.id)

        print('=== 6. 明确失败（4xx）：不是死路，同轮可以重试 ===')
        fake.fail = DingTalkError('模板不存在', api='workflow/processInstances',
                                  http_status=400)
        failed = await submit(inquiry_version=3)
        calls_at_failure = fake.calls
        check('状态', failed.status, 'failed')
        check_true('错误已落痕', bool(failed.error), str(failed.error)[:50])
        fake.fail = None
        retried_failed = await submit(inquiry_version=3)
        check('明确失败后同轮重试：真的再发一次', fake.calls, calls_at_failure + 1)
        check('状态回到 pending', retried_failed.status, 'pending')
        check('复用同一行（不另建实例）', retried_failed.id, failed.id)
        check('尝试次数累加', retried_failed.attempt_count, 2)

        print('=== 7. 人工核定：resend 才真的重发；同请求键回放不再调外部 ===')
        fake.fail = DingTalkUnknownOutcome('模拟超时')
        needs = await submit(inquiry_version=4)
        fake.fail = None
        calls_before_resend = fake.calls
        resent = await dt.resolve_reviewed_instance(
            s, needs, action='resend', request_key='CHKIDEM-RESEND'
        )
        check('人工 resend 才真的重发（外部调用 +1）', fake.calls, calls_before_resend + 1)
        check('resend 后回到 pending', resent.status, 'pending')
        check('resend 复用同一行', resent.id, needs.id)
        replayed = await dt.resolve_reviewed_instance(
            s, needs, action='resend', request_key='CHKIDEM-RESEND'
        )
        check('同一请求键回放：不再调外部', fake.calls, calls_before_resend + 1)
        check('回放的是同一行', replayed.id, needs.id)

        print('=== 8. 认领：先核实模板/发起人/来源需求，对不上不采纳 ===')
        adopt_row = extra_row(5)
        s.add(adopt_row)
        await s.flush()
        fake.instances['DT-ADOPT-1'] = {
            'processCode': 'PROC-FAKE',
            'originatorUserId': 'fake-user',
            'businessId': str(inquiry.id),
            'title': '定制询价审批',
            'status': 'RUNNING',
        }
        adopted = await dt.resolve_reviewed_instance(
            s, adopt_row, action='adopt', instance_id='DT-ADOPT-1',
            request_key='CHKIDEM-ADOPT',
        )
        check('adopt 后状态回 pending', adopted.status, 'pending')
        check('adopt 接住了钉钉实例号', adopted.instance_id, 'DT-ADOPT-1')
        check('adopt 不再调外部创建', fake.calls, calls_before_resend + 1)

        wrong_row = extra_row(6)
        s.add(wrong_row)
        await s.flush()
        fake.instances['DT-WRONG'] = {
            **fake.instances['DT-ADOPT-1'], 'processCode': 'PROC-OTHER'
        }
        try:
            await dt.resolve_reviewed_instance(
                s, wrong_row, action='adopt', instance_id='DT-WRONG',
                request_key='CHKIDEM-ADOPT2',
            )
        except AppError as exc:
            check('错误模板不得采纳', exc.http_status, 409)
            check_true('报错点明模板对不上', '审批模板对不上' in exc.message, exc.message[:80])
        else:
            check_true('错误模板必须拒绝采纳', False)
        check('拒绝采纳后没有实例号', wrong_row.instance_id, None)
        check('拒绝采纳后仍是"结果待人工核对"', wrong_row.status, 'needs_review')
        check('拒绝采纳后占用已放掉', wrong_row.resolve_state, 'idle')

        print('=== 9. 同一实例不能被两条记录同时认领 ===')
        try:
            await dt.resolve_reviewed_instance(
                s, wrong_row, action='adopt', instance_id='DT-ADOPT-1',
                request_key='CHKIDEM-ADOPT3',
            )
        except AppError as exc:
            check('已关联别的需求的实例不得采纳', exc.code, ErrorCode.DUPLICATE)
            check_true('报错指出已关联哪条需求', str(inquiry.id) in exc.message, exc.message[:80])
        else:
            check_true('已关联的实例必须拒绝', False)
        check('本地仍然没有实例号', wrong_row.instance_id, None)

        print('=== 10. 僵死占用：接管后不盲目再建（7.8）===')
        stuck = extra_row(
            7,
            resolve_state='processing',
            resolve_request_key='CHKIDEM-DEAD',
            resolve_claimed_at=datetime.now(UTC) - timedelta(minutes=STALE_MINUTES),
        )
        s.add(stuck)
        await s.commit()
        calls_before_stuck = fake.calls
        try:
            await dt.resolve_reviewed_instance(
                s, stuck, action='resend', request_key='CHKIDEM-STUCK'
            )
        except AppError as exc:
            check('僵死占用被接管时拒绝盲目重发', exc.http_status, 409)
            check_true('要求先到钉钉核对', '先到钉钉' in exc.message, exc.message[:80])
        else:
            check_true('僵死占用必须拒绝盲目重发', False)
        check('没有偷偷再调外部', fake.calls, calls_before_stuck)
        check('状态保持"结果未知"', stuck.status, 'needs_review')
        check('占用已放掉（否则永远核不了）', stuck.resolve_state, 'idle')

        print('=== 11. 关闸时落的 skipped：开闸后能真正发出 ===')
        settings.dingtalk_push_off = True
        blocked = await submit(inquiry_version=8)
        check('关闸时落 skipped', blocked.status, 'skipped')
        calls_before_open = fake.calls
        settings.dingtalk_push_off = False
        sent = await submit(inquiry_version=8)
        check('开闸后再发起：真的发给钉钉了（外部调用 +1）', fake.calls,
              calls_before_open + 1)
        check('状态 pending', sent.status, 'pending')

        print('=== 12. 卡在 submitting 的行：转人工，不自动重发 ===')
        stuck_submit = await submit(inquiry_version=9)
        calls_before_stuck_submit = fake.calls
        # 就在**同一个会话**里把它改成"20 分钟前卡在 submitting"：
        # 模拟"进程在占业务键与调外部之间被杀"。这里刻意不用第二个连接改库
        # ——异步会话的 identity map 里还是旧对象，读到的是改之前的状态；
        # 而 expire_all() 会让后续属性访问触发同步 IO（MissingGreenlet）直接崩。
        stuck_submit.status = 'submitting'
        stuck_submit.created_at = datetime.now(UTC) - timedelta(minutes=20)
        original_created_at = stuck_submit.created_at
        stuck_submit.last_attempt_at = stuck_submit.created_at
        await s.commit()
        needs_review = await submit(inquiry_version=9)
        check('超时的 submitting 转人工（不自动重发）', needs_review.status, 'needs_review')
        check('没有偷偷再调外部', fake.calls, calls_before_stuck_submit)
        check('复用的是同一行', needs_review.id, stuck_submit.id)
        check('created_at 没被改写', needs_review.created_at, original_created_at)
        check_true('错误说明指向人工核对', '人工' in (needs_review.error or ''),
                   str(needs_review.error)[:50])

    await cleanup()
    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('钉钉发起幂等 + 状态机回归 全部通过（全程未连钉钉）')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
