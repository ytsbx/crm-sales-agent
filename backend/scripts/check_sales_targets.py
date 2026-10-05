"""目标口径回归（文档 §六 :121 / 场景17）。纯库操作，不依赖后端。

    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_sales_targets.py

## 为什么这条最重要

目标数字错了不会报错，只会让考核口径失真——**而且很难事后发现**。
所以这里锁的是"口径本身"，不是"页面能不能打开"：

1. **三个销售额口径必须真的分开**：发货口径按首批实际发货日归月，
   回款口径只认财务确认；它们不能是签单额换个名字（这是最容易糊弄过去的地方）；
2. **老客池期初固定**（业务已拍板）：只有"年初之前已成交"的客户才算老客，
   本期才成交的客户**不进老客池**，否则新客一成交就变成老客、数字虚高；
3. **两种新客口径要能区分**：建档月 vs 首次成交月（建档后三个月才成交的客户，
   两者落在不同月份）；
4. 口径说明与数据来源随结果返回——文档要求"分别保存计算口径与数据来源"。

夹具带 CHKTGT 前缀，跑完即清。
"""

import asyncio
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.order.model import OrderShipmentBatch, SalesOrder
from app.modules.payment.model import PaymentRecord
from app.modules.user.model import User

FAILURES = []
PREFIX = 'CHKTGT'
YEAR = datetime.now(UTC).year


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
        order = f"(select id from sales_orders where customer_id in {cust})"
        for sql in (
            f"delete from order_shipment_batches where order_id in {order}",
            f"delete from payment_records where order_id in {order}",
            f"delete from sales_orders where customer_id in {cust}",
            "delete from customers where name like :p",
            "delete from users where username like :u",
        ):
            await s.execute(text(sql), {'p': f'{PREFIX}%', 'u': f'{PREFIX.lower()}%'})
        await s.commit()


async def main():
    from app.modules.analytics import target_bases
    from app.modules.customer.model import Customer

    stamp = int(time.time())
    await cleanup()

    async with SessionLocal() as s:
        owner = User(name=f'{PREFIX}销售-{stamp}', username=f'{PREFIX.lower()}_{stamp}',
                     password_hash='x', status='active')
        s.add(owner)
        await s.flush()
        owner_id = owner.id
        user = CurrentUser(owner, permissions=set(), roles=[], data_scope='self')

        # 老客：去年 12 月就有成交（进期初池）；新客：今年才有成交（不进池）
        veteran = Customer(name=f'{PREFIX}老客-{stamp}', owner_id=owner_id, status='active',
                           pool_status='private', created_at=datetime(YEAR - 1, 3, 1, tzinfo=UTC))
        rookie = Customer(name=f'{PREFIX}新客-{stamp}', owner_id=owner_id, status='active',
                          pool_status='private', created_at=datetime(YEAR, 2, 1, tzinfo=UTC))
        s.add_all([veteran, rookie])
        await s.flush()

        def order(customer, amount, created_at, seq):
            # order_no 必须唯一：早先按 customer_id 拼，同一个客户两单直接撞唯一约束
            row = SalesOrder(
                order_no=f'{PREFIX}{stamp}{seq}', customer_id=customer.id,
                owner_id=owner_id, sales_owner_id=owner_id,
                total_amount=Decimal(amount), currency='CNY', status='completed',
                created_at=created_at,
            )
            s.add(row)
            return row

        order(veteran, '1000', datetime(YEAR - 1, 12, 20, tzinfo=UTC), 'A')
        order(veteran, '400', datetime(YEAR, 3, 10, tzinfo=UTC), 'B')
        rookie_this_year = order(rookie, '700', datetime(YEAR, 4, 5, tzinfo=UTC), 'C')
        await s.flush()
        # 新客那单发了货、也回了款；老客 3 月那单只签单，用来区分三个口径
        s.add_all([
            OrderShipmentBatch(
                order_id=rookie_this_year.id, batch_no=1, status='shipped',
                actual_ship_date=datetime(YEAR, 4, 20, tzinfo=UTC).date(),
                created_at=datetime(YEAR, 4, 20, tzinfo=UTC),
            ),
            PaymentRecord(
                order_id=rookie_this_year.id, received_amount=Decimal('700'),
                received_date=datetime(YEAR, 5, 6, tzinfo=UTC).date(),
                status='confirmed', created_at=datetime(YEAR, 5, 6, tzinfo=UTC),
            ),
        ])
        await s.commit()

        data = await target_bases.annual_bases(s, user, YEAR)
        month = lambda key, m: next(x['value'] for x in data[key] if x['month'] == m)  # noqa: E731

        print('=== 1. 三个销售额口径真的分开 ===')
        check('3 月签单含老客那单', month('signed', '03'), 400.0)
        check('4 月签单含新客那单', month('signed', '04'), 700.0)
        check('发货口径按"首批实际发货日"归到 4 月', month('shipped', '04'), 700.0)
        check('发货口径不在签单月重复计一次', month('shipped', '03'), 0.0)
        check('回款口径按财务确认日归到 5 月', month('received', '05'), 700.0)
        check('回款口径与签单月不同（三口径确实分开）', month('received', '04'), 0.0)

        print('=== 2. 老客池期初固定：本期新客不进池 ===')
        check('3 月老客净额 = 老客那单', month('repeat_net', '03'), 400.0)
        check('4 月新客的单不计入老客净额', month('repeat_net', '04'), 0.0)
        check('去年那单不在本期（去年 12 月）', month('repeat_net', '12'), 0.0)

        print('=== 3. 两种新客口径能区分 ===')
        check('建档口径：新客 2 月建档', month('new_by_created', '02'), 1)
        check('首单口径：该新客 4 月才首单', month('new_by_first_deal', '04'), 1)
        check('建档月不等于首单月（两者没有互相顶替）', month('new_by_first_deal', '02'), 0)

        print('=== 4. 口径与来源随结果返回 ===')
        check('口径说明条数', len(data['basis_note']), 6)
        check_true('数据来源非空', bool(data['source_note']), data['source_note'][:20])
        check('月度序列长度', len(data['signed']), 12)

        print('=== 5. 差额与达成率；零基期不产生错误增长率 ===')
        from app.modules.analytics.model import SalesTarget
        from app.modules.analytics import targets as targets_svc

        month = f'{YEAR}-03'
        s.add(
            SalesTarget(
                period=month, user_id=owner_id,
                new_customer_target=2, sales_target=Decimal('1000'),
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()
        result = await targets_svc.targets_with_actuals(s, user, YEAR)
        rows = result['rows'] if isinstance(result, dict) else result
        with_target = next(r for r in rows if r.get('sales_target') == 1000.0)
        check('差额 = 实际 − 目标', with_target['sales_variance'], -600.0)
        check('达成率 = 实际 / 目标', with_target['sales_achievement'], 0.4)
        check('新客差额', with_target['new_customer_variance'], -2)
        zero_row = next(r for r in rows if not r.get('sales_target'))
        check_true(
            '零基期不给百分比（文档场景17 明确要求）',
            zero_row['sales_achievement'] is None,
            f"achievement={zero_row['sales_achievement']}",
        )
        check_true('零基期给出说明而不是空白', bool(zero_row['achievement_note']),
                   str(zero_row['achievement_note']))
        await s.execute(text('delete from sales_targets where period = :p'), {'p': month})
        await s.commit()

        # === 6. 目标约束：期间标准化、非法组合、唯一与乐观并发（§4.1.2 / §4.1.6）===
        print('=== 6. 目标约束：期间标准化、非法组合、唯一与乐观并发 ===')
        from app.core.errors import AppError, ErrorCode

        # ① 期间标准化：`2026-1` 与 `2026-01` 不能再并存。
        #    旧实现只过 strptime('%Y-%m')，它是**宽容**的，`2026-1` 照样存库；
        #    实际值按 `2026-01` 生成，两边永远对不上，那行目标变成"设了但达成为 0"。
        check('2026-1 标准化成 2026-01', targets_svc.normalize_period('2026-1'), '2026-01')
        check('标准期间原样返回', targets_svc.normalize_period('2026-10'), '2026-10')
        for bad in ('2026-13', '2026-00', '2026-1-1', 'abc', ''):
            try:
                targets_svc.normalize_period(bad)
                rejected = False
            except AppError:
                rejected = True
            check(f'非法期间被拒：{bad!r}', rejected, True)

        async def err_code(coro):
            try:
                await coro
            except AppError as exc:
                return exc.code
            return None

        # ② 个人目标与团队目标互斥：同时给就说不清这条到底算谁的
        check(
            '人员与部门同时指定被拒',
            await err_code(targets_svc.upsert_target(
                s, user=user, period=f'{YEAR}-07', user_id=owner_id, department_id=1,
                new_customer_target=1, sales_target=10)),
            ErrorCode.PARAM_ERROR,
        )
        # ③ 目标值不得为负
        check(
            '负目标被拒',
            await err_code(targets_svc.upsert_target(
                s, user=user, period=f'{YEAR}-07', user_id=owner_id,
                new_customer_target=-1, sales_target=10)),
            ErrorCode.PARAM_ERROR,
        )

        # ④ 同一作用域只留一条：第二次 upsert 是**更新**，不是再插一行
        row1, created1, before1 = await targets_svc.upsert_target(
            s, user=user, period=f'{YEAR}-7', user_id=owner_id,
            new_customer_target=1, sales_target=100)
        await s.commit()
        check('首次 upsert 是新建', created1, True)
        check('新建时没有改前快照', before1, None)
        check('期间已标准化入库', row1.period, f'{YEAR}-07')
        row2, created2, before2 = await targets_svc.upsert_target(
            s, user=user, period=f'{YEAR}-07', user_id=owner_id,
            new_customer_target=3, sales_target=300)
        await s.commit()
        check('第二次 upsert 是更新（不是再插一行）', created2, False)
        check('两次拿到同一行', row2.id, row1.id)
        check('改前快照记着旧目标值（审计要能回答被谁改成了什么）',
              (before2 or {}).get('sales_target'), 100.0)
        count = (await s.execute(text(
            'select count(*) from sales_targets where period = :p and user_id = :u '
            'and department_id is null and deleted_at is null'),
            {'p': f'{YEAR}-07', 'u': owner_id})).scalar_one()
        check('库里只有一条活行', count, 1)

        # ⑤ 乐观并发：带着读到的时间戳改 → 成功；带旧时间戳改 → 409，不覆盖别人的改动
        row3, _, _ = await targets_svc.upsert_target(
            s, user=user, period=f'{YEAR}-07', user_id=owner_id,
            new_customer_target=3, sales_target=350,
            expected_updated_at=row2.updated_at)
        await s.commit()
        check('带最新时间戳可以改', float(row3.sales_target), 350.0)
        # 注意：不能用 row1.updated_at 当"过期时间戳"——row1/row2/row3 在同一个 session 里
        # 是**同一个对象**（identity map），它的 updated_at 会被后面的更新刷成最新值，
        # 那样测出来的是"没冲突"。这里用一个真正过期的时间戳。
        check(
            '带过期时间戳被拒（不静默覆盖别人）',
            await err_code(targets_svc.upsert_target(
                s, user=user, period=f'{YEAR}-07', user_id=owner_id,
                new_customer_target=9, sales_target=999,
                expected_updated_at=datetime(2000, 1, 1, tzinfo=UTC))),
            ErrorCode.VERSION_CONFLICT,
        )

        # ⑥ 库层唯一索引确实存在（不是只写在模型里）：直接插重复行必须被挡
        dup_blocked = True
        try:
            await s.execute(text(
                'insert into sales_targets (period, user_id, department_id, '
                'new_customer_target, sales_target, repeat_customer_target, created_at) '
                'values (:p, :u, null, 1, 1, 0, now())'), {'p': f'{YEAR}-07', 'u': owner_id})
            dup_blocked = False
        except Exception:
            dup_blocked = True
        await s.rollback()
        check('库层唯一索引挡住重复插入', dup_blocked, True)

        # ⑦ 团队目标被编辑后不能丢 department_id（§4.1.2 的原缺陷：
        #    一编辑就从团队目标变成全局目标，主管以为设的是部门目标、其实全公司都在用）
        dept_id = (await s.execute(text('select id from departments order by id limit 1'))).scalar_one_or_none()
        if dept_id is None:
            print('  -    跳过：库里没有部门可供建团队目标')
        else:
            team1, _, _ = await targets_svc.upsert_target(
                s, user=user, period=f'{YEAR}-08', user_id=None, department_id=dept_id,
                new_customer_target=1, sales_target=500)
            await s.commit()
            team2, _, _ = await targets_svc.upsert_target(
                s, user=user, period=f'{YEAR}-08', user_id=None, department_id=dept_id,
                new_customer_target=2, sales_target=800)
            await s.commit()
            check('团队目标更新后 department_id 没丢', team2.department_id, dept_id)
            check('团队目标没变成全公司目标', team2.user_id, None)
            check('更新的是同一行（没有多出一条）', team2.id, team1.id)

        # 本套件自己在库里造的目标行，跑完收干净（默认库**不要**跑这条套件，见文档 §4.1.7）
        await s.execute(
            text('delete from sales_targets where period in (:p1, :p2)'),
            {'p1': f'{YEAR}-07', 'p2': f'{YEAR}-08'},
        )
        await s.commit()

    await cleanup()
    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('目标口径回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
