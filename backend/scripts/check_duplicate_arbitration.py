"""撞单裁定回归（文档 §11.4 验收 20 / §11.5 :279）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_duplicate_arbitration.py

## 文档给的流程

:279「确认客户身份 → 看原归属与有效业务关系 → **争议冻结自动改派** → 主管裁定并留痕」

合成四条断言：

1. 疑似重复能开成待裁定单，且证据是**当时**的快照；
2. **争议期间自动改派被冻结**：离职交接与公海回收都动不了这两个客户；
3. 人工转移不受冻结影响（人做的决定不该被系统拦住）；
4. 裁定按人写的结论执行并留痕：`keep_both` 不动归属，`assign_*` 改归属且写归属历史；
   裁定后解冻。

另外锁一条"不依建档先后"：**裁定之前，两个客户的归属一直是原样**——
系统不会因为谁先建档就把人改掉。
"""

import asyncio
import sys
import time
from datetime import UTC, datetime

from sqlalchemy import select, text
from _test_support import require_isolated_db

require_isolated_db()

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.user.model import User

FAILURES = []
PREFIX = 'CHKDA'


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=''):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


async def cleanup():
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        await s.execute(text(f"delete from customer_duplicate_cases where customer_id in {cust} "
                             f"or candidate_id in {cust}"), {'p': f'{PREFIX}%'})
        await s.execute(text(f"delete from customer_owner_history where customer_id in {cust}"), {'p': f'{PREFIX}%'})
        await s.execute(text("delete from customers where name like :p"), {'p': f'{PREFIX}%'})
        await s.execute(text("delete from users where username like :u"), {'u': f'{PREFIX.lower()}%'})
        await s.commit()


async def main():
    from app.modules.customer import duplicates as dup
    from app.modules.customer.model import Customer, CustomerDuplicateCase
    from app.modules.customer.service import transfer_customer
    from app.core.errors import AppError

    stamp = int(time.time())
    await cleanup()

    async with SessionLocal() as s:
        a = User(name=f'{PREFIX}销售甲-{stamp}', username=f'{PREFIX.lower()}_a_{stamp}',
                 password_hash='x', status='active')
        b = User(name=f'{PREFIX}销售乙-{stamp}', username=f'{PREFIX.lower()}_b_{stamp}',
                 password_hash='x', status='active')
        s.add_all([a, b])
        await s.flush()
        # 同理：commit/rollback 之后 a.id 也会触发同步刷新，先把 id 固定下来
        a_id, b_id = a.id, b.id
        admin = (await s.execute(select(User).where(User.username == 'admin'))).scalars().one()
        admin_id = admin.id
        actor = CurrentUser(admin, permissions=set(), roles=[], data_scope='all')

        # 同名同域名的两家「公司」：疑似撞单，但归属分别是甲、乙
        existing = Customer(name=f'{PREFIX}宏远贸易', domain='chkda.example.com',
                            owner_id=a_id, status='active', pool_status='private')
        incoming = Customer(name=f'{PREFIX}宏远贸易有限公司', domain='chkda.example.com',
                            owner_id=b_id, status='active', pool_status='private')
        s.add_all([existing, incoming])
        await s.flush()  # 先落库拿到 id：add_all 之后、flush 之前 id 还是 None
        # id 提前取成普通整数：commit/rollback 会过期 ORM 属性，
        # 之后再用 existing.id 会触发同步刷新 → MissingGreenlet
        existing_id, incoming_id = existing.id, incoming.id
        await s.commit()

        print('=== 1. 疑似重复 → 开待裁定单（带当时证据）===')
        cases = await dup.open_cases_for_customer(
            s, customer=incoming, source='import', actor_id=admin_id
        )
        # commit 会过期所有 ORM 属性，之后再读就会触发同步刷新 →
        # 异步会话里报 MissingGreenlet。所以**提交前**先把 id 取出来，
        # 提交后用 await s.get() 异步取回（这个坑本项目文档里记过一次）。
        case_id = cases[0].id
        await s.commit()
        check('开出了裁定单', len(cases) > 0, True)
        case = await s.get(CustomerDuplicateCase, case_id)
        check('状态是待裁定', case.status, 'pending')
        check('来源记为导入', case.source, 'import')
        check_true('证据里留了命中的理由', bool((case.evidence or {}).get('reasons')),
                   f"{case.evidence}")
        again = await dup.open_cases_for_customer(
            s, customer=incoming, source='import', actor_id=admin_id
        )
        await s.commit()
        check('重复导入不会堆出第二条待办（幂等）', [c.id for c in again], [case.id])
        reverse = await dup.open_cases_for_customer(
            s, customer=existing, source='manual', actor_id=admin_id
        )
        await s.commit()
        check('反向检查同一对客户复用待裁定记录', [c.id for c in reverse], [case.id])
        check_true('有争议时 is_disputed 为真', await dup.is_disputed(s, existing_id))
        check_true('新客户也在争议里', await dup.is_disputed(s, incoming_id))

        print('=== 2. 争议冻结自动改派 ===')
        check('裁定前：已有客户归属没变', (await s.get(Customer, existing_id)).owner_id, a_id)
        check('裁定前：新客户归属没变（不依建档先后）',
              (await s.get(Customer, incoming_id)).owner_id, b_id)

        try:
            await transfer_customer(s, actor, await s.get(Customer, existing_id), b_id,
                                    '离职继承', automatic=True)
            check_true('离职交接被冻结拦下', False, '居然放行了')
        except AppError as exc:
            check('离职交接被冻结拦下（422）', exc.http_status, 422)
        await s.rollback()
        check('被拦下后归属仍是原样', (await s.get(Customer, existing_id)).owner_id, a_id)

        # 人工转移不受冻结影响
        await transfer_customer(s, actor, await s.get(Customer, incoming_id), a_id,
                                '人工调整', automatic=False)
        await s.commit()
        check('人工转移不被冻结拦住', (await s.get(Customer, incoming_id)).owner_id, a_id)

        print('=== 3. 主管裁定并留痕 ===')
        # 上面每次 commit/rollback 都会把 case 标记过期，进 resolve_case 前
        # 必须重新取一份活的——否则里面读 case.status 就触发同步刷新
        case = await s.get(CustomerDuplicateCase, case_id)
        await dup.resolve_case(s, case=case, decision='assign_new', owner_id=b_id,
                               remark='两边都是老客户，判给乙', actor=actor)
        await s.commit()
        case = await s.get(CustomerDuplicateCase, case_id)
        check('裁定已结案', case.status, 'resolved')
        check('裁定结论', case.decision, 'assign_new')
        check('归属按人写的结论改', (await s.get(Customer, existing_id)).owner_id, b_id)
        history = (await s.execute(text(
            "select count(*) from customer_owner_history where customer_id = :c "
            "and reason like '%撞单裁定%'"), {'c': existing_id})).scalar_one()
        check_true('归属变更留了痕', history > 0, f'匹配 {history} 条')
        check_true('裁定后解冻', not await dup.is_disputed(s, existing_id))

        print('=== 4. keep_both：判为两家不同，各自保留、不合并 ===')
        third = Customer(name=f'{PREFIX}另一家', domain='chkda.example.com',
                         owner_id=b_id, status='active', pool_status='private')
        s.add(third)
        await s.commit()
        cases2 = await dup.open_cases_for_customer(s, customer=third, source='import',
                                                   actor_id=admin_id)
        case2_id = cases2[0].id
        await s.commit()
        case2 = await s.get(CustomerDuplicateCase, case2_id)
        before_pair = (
            (await s.get(Customer, case2.customer_id)).owner_id,
            (await s.get(Customer, case2.candidate_id)).owner_id,
        )
        case2 = await s.get(CustomerDuplicateCase, case2_id)
        await dup.resolve_case(s, case=case2, decision='keep_both', owner_id=None,
                               remark='不是同一家', actor=actor)
        await s.commit()
        after_pair = (
            (await s.get(Customer, case2.customer_id)).owner_id,
            (await s.get(Customer, case2.candidate_id)).owner_id,
        )
        check('keep_both 不动归属', after_pair, before_pair)
        still = (await s.execute(text(
            "select count(*) from customers where id in (:a, :b) and deleted_at is null"),
            {'a': case2.customer_id, 'b': case2.candidate_id})).scalar_one()
        check('两条客户都还在（不误合并/误删）', still, 2)

        print('=== 5. 沿用已有客户负责人：无需另传 ID，公海归属同步切换 ===')
        public = Customer(name=f'{PREFIX}公海归属对照', owner_id=None,
                          status='active', pool_status='public')
        s.add(public)
        await s.flush()
        public_id = public.id
        existing_case = CustomerDuplicateCase(
            customer_id=public_id, candidate_id=existing_id,
            source='manual', status='pending', created_at=datetime.now(UTC),
        )
        s.add(existing_case)
        await s.flush()
        existing_case_id = existing_case.id
        await s.commit()

        # 已有客户的负责人也必须在职；失败不能结案或改派公海客户。
        existing_owner = await s.get(User, b_id)
        existing_owner.status = 'disabled'
        await s.flush()
        try:
            await dup.resolve_case(s, case=await s.get(CustomerDuplicateCase, existing_case_id),
                                   decision='assign_existing', owner_id=None,
                                   remark='停用负责人对照', actor=actor)
            check_true('已有客户的停用负责人不能接收客户', False)
        except AppError as exc:
            check('已有客户的停用负责人被拒', exc.http_status, 422)
        await s.rollback()
        check('拒绝后案件仍待裁定',
              (await s.get(CustomerDuplicateCase, existing_case_id)).status, 'pending')
        check('拒绝后公海客户仍无负责人', (await s.get(Customer, public_id)).owner_id, None)

        await dup.resolve_case(s, case=await s.get(CustomerDuplicateCase, existing_case_id),
                               decision='assign_existing', owner_id=None,
                               remark='沿用已有客户负责人', actor=actor)
        await s.commit()
        assigned_public = await s.get(Customer, public_id)
        check('无需传负责人 ID，也能沿用已有归属', assigned_public.owner_id, b_id)
        check('分配后的公海客户转为私海', assigned_public.pool_status, 'private')
        check('裁定记录保存实际负责人',
              (await s.get(CustomerDuplicateCase, existing_case_id)).resolved_owner_id, b_id)

    await cleanup()
    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('撞单裁定回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
