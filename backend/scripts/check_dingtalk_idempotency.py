"""钉钉发起幂等回归（P1 修复）。

跑法（需要 PostgreSQL；**不连钉钉、不发任何真实请求**）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_dingtalk_idempotency.py

## 为什么必须用假客户端

这条路径唯一"真实"的验证方式是往钉钉发一张审批单——那会打扰 3 位同事
（发起人 + 两位审批人）。所以这里把 `service.get_client` 换成假的：
**记录调用次数、可模拟失败**，于是"会不会重复建单"这件事可以本地断言，
对外零请求。

## 锁住的四条（对应 P1 修复）

1. 第一次发起：调外部一次，拿到实例号；
2. **同一轮重复发起：不再调外部**（复用已有行）——这是"网络重试不重复建单"的落点；
3. 驳回后重提（resubmit）：**新的一轮**，会再调一次——这是业务主动重提，不是重复；
4. 外部失败/超时：留一行 failed 且写明原因；**同轮再发起不会再调外部**
   （业务确认的口径：宁可让人点一下，也不要自动重试造成重复）。

夹具带 CHKIDEM 前缀，跑完即清。
"""

import asyncio
import sys
import time
from decimal import Decimal

from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.user.model import User

FAILURES = []
PREFIX = 'CHKIDEM'


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
        self.fail = False

    async def create_process_instance(self, **_kwargs) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError('模拟网络超时')
        return f'FAKE-{self.calls}'


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
    from app.modules.customer.model import Customer
    from app.modules.dingtalk import service as dt
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

        print('=== 1. 第一次发起 ===')
        first = await submit()
        check('外部调用次数', fake.calls, 1)
        check('状态', first.status, 'pending')
        check_true('拿到实例号', bool(first.instance_id), f'instance_id={first.instance_id}')

        print('=== 2. 同一轮重复发起：不该再调外部（防重复建单）===')
        again = await submit()
        check('外部调用次数仍是 1', fake.calls, 1)
        check('复用同一行', again.id, first.id)

        print('=== 3. 驳回后重提：是新的一轮，允许再发一次 ===')
        resubmitted = await submit(resubmit=True)
        check('外部调用次数', fake.calls, 2)
        check('轮次', resubmitted.submit_round, 2)
        check_true('是新的一行（旧轮次保留）', resubmitted.id != first.id,
                   f'{first.id} vs {resubmitted.id}')

        print('=== 4. 外部失败：落 failed，且同轮不再自动重试 ===')
        fake.fail = True
        failed = await submit(resubmit=True)
        calls_at_failure = fake.calls
        check('状态', failed.status, 'failed')
        check_true('错误已落痕', bool(failed.error), str(failed.error)[:40])
        retried = await submit()
        check('同轮再发起：外部调用次数没变', fake.calls, calls_at_failure)
        check('复用的还是那行 failed（等人工决定）', retried.id, failed.id)

    await cleanup()
    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('钉钉发起幂等回归 全部通过（全程未连钉钉）')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
