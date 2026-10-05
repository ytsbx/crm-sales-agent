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
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.order.model import (
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.payment.model import PaymentRecord
# 只为了让 SQLAlchemy 认得 `sales_order_items.sku_id` 指向的 `skus` 表：
# 建夹具时 mapper 要解析外键，目标表的模型没导入就报 NoReferencedTableError
# （10-交接文档 第八节第 7 类坑）。
from app.modules.product.model import Sku  # noqa: F401
from app.modules.user.model import Department, User

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
        batch = f"(select id from order_shipment_batches where order_id in {order})"
        for sql in (
            # 子表先删：批次明细有外键指向批次（§4.1.4 的夹具会建明细）
            f"delete from order_shipment_batch_items where batch_id in {batch}",
            f"delete from order_shipment_batches where order_id in {order}",
            f"delete from payment_records where order_id in {order}",
            # 订单明细也是子表（§4.1.4 的分摊夹具要建它才有金额可算）
            f"delete from sales_order_items where order_id in {order}",
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
        # 存成普通变量：`commit()` 会让 ORM 属性过期，异步会话里再读 `veteran.id`
        # 会触发懒加载并报 MissingGreenlet（10-交接文档 第八节第 3 条）
        veteran_id = veteran.id

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
        # 发货口径现在是**按批次分摊**（§4.1.4）：批次必须带明细，否则算不出金额。
        # 新客那单：7 件 × 100 元 = 700，一批发完。
        rookie_item = SalesOrderItem(
            order_id=rookie_this_year.id, quantity=Decimal('7'),
            unit_price=Decimal('100'), amount=Decimal('700'),
        )
        s.add(rookie_item)
        await s.flush()
        # 新客那单发了货、也回了款；老客 3 月那单只签单，用来区分三个口径
        rookie_batch = OrderShipmentBatch(
            order_id=rookie_this_year.id, batch_no=1, status='shipped',
            actual_ship_date=date(YEAR, 4, 20),
            created_at=datetime(YEAR, 4, 20, tzinfo=UTC),
        )
        s.add(rookie_batch)
        await s.flush()
        s.add_all([
            OrderShipmentBatchItem(
                batch_id=rookie_batch.id, order_item_id=rookie_item.id,
                planned_qty=Decimal('7'), shipped_qty=Decimal('7'),
            ),
            PaymentRecord(
                order_id=rookie_this_year.id, received_amount=Decimal('700'),
                received_date=date(YEAR, 5, 6),
                status='confirmed', created_at=datetime(YEAR, 5, 6, tzinfo=UTC),
            ),
        ])

        # §4.1.4 的场景夹具：一张 1000 元的单，**10 月发 10 件、11 月发 90 件**。
        # 旧实现按首批把整单算进 10 月，分批发货直接错期。
        split_customer = Customer(
            name=f'{PREFIX}分批客-{stamp}', owner_id=owner_id, status='active',
            pool_status='private', created_at=datetime(YEAR, 1, 5, tzinfo=UTC),
        )
        s.add(split_customer)
        await s.flush()
        split_order = SalesOrder(
            order_no=f'{PREFIX}{stamp}SPLIT', customer_id=split_customer.id,
            owner_id=owner_id, sales_owner_id=owner_id, total_amount=Decimal('1000'),
            currency='CNY', status='completed',
            # 签单月放在 8 月：发货月由批次决定（10/11 月），签单月不掺进 9 月的团队目标用例
            created_at=datetime(YEAR, 8, 1, tzinfo=UTC),
        )
        s.add(split_order)
        await s.flush()
        split_item = SalesOrderItem(
            order_id=split_order.id, quantity=Decimal('100'),
            unit_price=Decimal('10'), amount=Decimal('1000'),
        )
        s.add(split_item)
        await s.flush()
        for seq, (qty, month_no, day) in enumerate(
            ((Decimal('10'), 10, 15), (Decimal('90'), 11, 20)), start=1
        ):
            batch = OrderShipmentBatch(
                order_id=split_order.id, batch_no=seq, status='shipped',
                actual_ship_date=date(YEAR, month_no, day),
                created_at=datetime(YEAR, month_no, day, tzinfo=UTC),
            )
            s.add(batch)
            await s.flush()
            s.add(
                OrderShipmentBatchItem(
                    batch_id=batch.id, order_item_id=split_item.id,
                    planned_qty=qty, shipped_qty=qty,
                )
            )
        await s.commit()

        data = await target_bases.annual_bases(s, user, YEAR)
        month = lambda key, m: next(x['value'] for x in data[key] if x['month'] == m)  # noqa: E731

        print('=== 1. 三个销售额口径真的分开 ===')
        check('3 月签单含老客那单', month('signed', '03'), 400.0)
        check('4 月签单含新客那单', month('signed', '04'), 700.0)
        check('发货口径按批次分摊到 4 月', month('shipped', '04'), 700.0)
        check('发货口径不在签单月重复计一次', month('shipped', '03'), 0.0)
        check('回款口径按财务确认日归到 5 月', month('received', '05'), 700.0)
        check('回款口径与签单月不同（三口径确实分开）', month('received', '04'), 0.0)

        print('=== 1.1 分批发货按批次分摊，不按首批把整单归一个月（§4.1.4）===')
        # 一张 1000 元的单：10 月发 10 件（100 元）、11 月发 90 件（900 元）。
        # 旧实现取"首批实际发货日"，整单 1000 全落在 10 月——11 月的业绩凭空少 900。
        check('10 月只算那一批（10 件 × 10 元）', month('shipped', '10'), 100.0)
        check('11 月算另一批（90 件 × 10 元）', month('shipped', '11'), 900.0)
        check('两批合计 = 整单金额', month('shipped', '10') + month('shipped', '11'), 1000.0)
        check_true(
            '整单没有被整笔算进首批那个月（旧实现这里是 1000）',
            month('shipped', '10') != 1000.0,
            f"10月={month('shipped', '10')}",
        )

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
        # 考核主口径 = **确认回款**（已确认 2026-10-05）。3 月那单的钱 5 月才确认，
        # 所以 3 月的考核值是 0；签单额 400 照常展示但**不进差额**（§4.3 明确要求）。
        check('考核口径是确认回款', result['assess_basis'], 'received')
        check('考核口径标签', result['assess_basis_label'], '确认回款')
        check('3 月考核值 = 回款 0（钱 5 月才确认）', with_target['assess_actual'], 0.0)
        check('差额 = 考核值(回款) − 目标', with_target['sales_variance'], -1000.0)
        check('达成率 = 考核值 / 目标', with_target['sales_achievement'], 0.0)
        check('签单额照常展示（只是不进差额）', with_target['sales_actual'], 400.0)
        check_true(
            '差额确实不是用签单额算的（否则会是 -600）',
            with_target['sales_variance'] != -600.0,
            f"variance={with_target['sales_variance']}",
        )
        check('新客差额', with_target['new_customer_variance'], -2)
        # §4.3：目标值、指标定义版本、实际值、差额、数据来源、计算时间都要能拿到
        check_true('有指标定义版本', bool(result.get('metric_basis_version')),
                   str(result.get('metric_basis_version')))
        check_true('有计算时间', bool(result.get('computed_at')), str(result.get('computed_at')))
        check_true('有数据来源清单', bool(result.get('sources')),
                   str(list((result.get('sources') or {}).keys()))[:60])
        check_true('有归属口径说明', bool(result.get('attribution_note')),
                   str(result.get('attribution_note'))[:40])
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

        # === 7. 团队目标与个人目标不混算（§4.1.1）===
        print('=== 7. 团队目标与个人目标不混算 ===')
        # 旧实现的过滤是 `or_(user_id == 我, user_id IS NULL)`：
        #   ① `user_id IS NULL` 一行全收 → **别的部门**的团队目标也进了我的列表；
        #   ② 只比 `我` → **同团队同事**的个人目标全看不到。
        dept_a = Department(name=f'{PREFIX}甲部-{stamp}')
        dept_b = Department(name=f'{PREFIX}乙部-{stamp}')
        s.add_all([dept_a, dept_b])
        await s.flush()
        # 全部存成局部 id：后面有 commit，ORM 属性会过期、异步会话里再读就 MissingGreenlet
        dept_a_id, dept_b_id = dept_a.id, dept_b.id
        peer = User(name=f'{PREFIX}同事-{stamp}', username=f'{PREFIX.lower()}_peer_{stamp}',
                    password_hash='x', status='active', department_id=dept_a_id)
        outsider = User(name=f'{PREFIX}外部门-{stamp}',
                        username=f'{PREFIX.lower()}_out_{stamp}',
                        password_hash='x', status='active', department_id=dept_b_id)
        s.add_all([peer, outsider])
        await s.flush()
        peer_id, outsider_id = peer.id, outsider.id
        boss = await s.get(User, owner_id)
        boss.department_id = dept_a_id

        target_month = f'{YEAR}-09'
        s.add_all([
            SalesTarget(period=target_month, user_id=None, department_id=dept_a_id,
                        sales_target=Decimal('5000'), created_at=datetime.now(UTC)),
            SalesTarget(period=target_month, user_id=peer_id,
                        sales_target=Decimal('300'), created_at=datetime.now(UTC)),
            SalesTarget(period=target_month, user_id=None, department_id=dept_b_id,
                        sales_target=Decimal('9999'), created_at=datetime.now(UTC)),
        ])
        # 团队目标的实绩只算**本部门成员**：甲部主管本人的 500 要算，乙部那单 1111 不能算
        s.add_all([
            SalesOrder(order_no=f'{PREFIX}{stamp}D9A', customer_id=veteran_id,
                       owner_id=owner_id, sales_owner_id=owner_id,
                       total_amount=Decimal('500'), currency='CNY', status='completed',
                       created_at=datetime(YEAR, 9, 12, tzinfo=UTC)),
            SalesOrder(order_no=f'{PREFIX}{stamp}D9B', customer_id=veteran_id,
                       owner_id=outsider_id, sales_owner_id=outsider_id,
                       total_amount=Decimal('1111'), currency='CNY', status='completed',
                       created_at=datetime(YEAR, 9, 20, tzinfo=UTC)),
        ])
        await s.commit()

        # ① 可见性：甲部主管（department 范围）
        manager = CurrentUser(boss, permissions=set(), roles=[], data_scope='department')
        mgr_rows = (await targets_svc.targets_with_actuals(s, manager, YEAR))['rows']
        seen_depts = {r.get('department_id') for r in mgr_rows if r.get('department_id')}
        seen_users = {r.get('user_id') for r in mgr_rows if r.get('user_id')}
        check('本部门的团队目标能看到', dept_a_id in seen_depts, True)
        check('**别的部门**的团队目标不该出现（旧实现会带进来）',
              dept_b_id in seen_depts, False)
        check('同团队同事的个人目标能看到（旧实现只看自己）', peer_id in seen_users, True)
        check('外部门同事的个人目标不该出现', outsider_id in seen_users, False)

        # ② 实绩口径：团队目标只算本部门成员。用 all 范围看，避免数据范围先把外部门过滤掉，
        #    否则"算错也没人发现"（这正是这条断言要防的）。
        admin = CurrentUser(boss, permissions=set(), roles=[], data_scope='all')
        admin_rows = (await targets_svc.targets_with_actuals(s, admin, YEAR))['rows']
        team_a_row = next(r for r in admin_rows if r.get('department_id') == dept_a_id)
        check('团队目标实绩只算本部门成员（500，不含外部门 1111）',
              team_a_row['sales_actual'], 500.0)

        # 收干净：users 有外键指向 departments，先摘引用再删部门
        await s.execute(
            text('update users set department_id = null where department_id in (:a, :b)'),
            {'a': dept_a_id, 'b': dept_b_id},
        )
        await s.execute(
            text('delete from sales_targets where period = :p'), {'p': target_month}
        )
        await s.execute(
            text('delete from departments where id in (:a, :b)'),
            {'a': dept_a_id, 'b': dept_b_id},
        )
        await s.commit()

        # === 8. 历史口径冻结：事后取消旧单不再改写去年已出的数（§4.1.5）===
        print('=== 8. 历史口径冻结（老客池 / 首次成交按年冻结）===')
        past_year = YEAR - 1
        # 先清掉这一年可能残留的快照：本段要自己造基准，否则会读到上一轮的快照
        await s.execute(
            text('delete from analytics_basis_snapshots where year = :y'), {'y': past_year}
        )
        await s.commit()
        # 两个客户，别混成一个：**同一客户不可能既是一年的期初老客、又在这一年首次成交**
        # ① 老客池用：past_year-1 就有成交，past_year 又成交一次
        frozen_customer = Customer(
            name=f'{PREFIX}冻结客-{stamp}', owner_id=owner_id, status='active',
            pool_status='private', created_at=datetime(past_year - 1, 6, 1, tzinfo=UTC),
        )
        # ② 首次成交用：past_year 之前一单都没有，首单落在 past_year 的 4 月
        rookie_past = Customer(
            name=f'{PREFIX}去年新客-{stamp}', owner_id=owner_id, status='active',
            pool_status='private', created_at=datetime(past_year, 4, 1, tzinfo=UTC),
        )
        s.add_all([frozen_customer, rookie_past])
        await s.flush()
        frozen_id, rookie_past_id = frozen_customer.id, rookie_past.id
        s.add_all([
            # 往年（past_year - 1）成交 → 该客户是 past_year 的期初老客
            SalesOrder(order_no=f'{PREFIX}{stamp}PRE', customer_id=frozen_id,
                       owner_id=owner_id, sales_owner_id=owner_id,
                       total_amount=Decimal('800'), currency='CNY', status='completed',
                       created_at=datetime(past_year - 1, 11, 5, tzinfo=UTC)),
            # past_year 老客池里的那一单
            SalesOrder(order_no=f'{PREFIX}{stamp}M1', customer_id=frozen_id,
                       owner_id=owner_id, sales_owner_id=owner_id,
                       total_amount=Decimal('200'), currency='CNY', status='completed',
                       created_at=datetime(past_year, 3, 8, tzinfo=UTC)),
            # past_year 的首次成交（4 月）
            SalesOrder(order_no=f'{PREFIX}{stamp}R1', customer_id=rookie_past_id,
                       owner_id=owner_id, sales_owner_id=owner_id,
                       total_amount=Decimal('300'), currency='CNY', status='completed',
                       created_at=datetime(past_year, 4, 6, tzinfo=UTC)),
        ])
        await s.commit()

        before_freeze = await target_bases.annual_bases(s, user, past_year)
        fb = lambda key, m: next(  # noqa: E731
            x['value'] for x in before_freeze[key] if x['month'] == m
        )
        check('过去年份会自动冻结口径基准', before_freeze['basis_frozen'], True)
        check('冻结时记下口径版本', before_freeze['basis_version'],
              target_bases.BASIS_VERSION)
        check_true('冻结时间非空', bool(before_freeze['basis_frozen_at']),
                   str(before_freeze['basis_frozen_at']))
        check('冻结时：老客 3 月净额含那单', fb('repeat_net', '03'), 200.0)
        check('冻结时：首次成交记在 4 月', fb('new_by_first_deal', '04'), 1)

        # 事后改动①：取消**往年**那单 —— 现算的话客户会掉出老客池，
        # 去年整年的老客净额跟着变 0（这正是"历史指标会漂移"）
        await s.execute(
            text("update sales_orders set status='cancelled' where order_no = :n"),
            {'n': f'{PREFIX}{stamp}PRE'},
        )
        await s.commit()
        after_pool = await target_bases.annual_bases(s, user, past_year)
        ap = lambda key, m: next(  # noqa: E731
            x['value'] for x in after_pool[key] if x['month'] == m
        )
        check('取消往年订单后，冻结的老客池不变（现算会掉出去 -> 0）',
              ap('repeat_net', '03'), 200.0)

        # 事后改动②：取消人家的首单，再补一张 7 月的 —— 现算的话首次成交月会跳到 7 月
        await s.execute(
            text("update sales_orders set status='cancelled' where order_no = :n"),
            {'n': f'{PREFIX}{stamp}R1'},
        )
        s.add(SalesOrder(
            order_no=f'{PREFIX}{stamp}R2', customer_id=rookie_past_id,
            owner_id=owner_id, sales_owner_id=owner_id,
            total_amount=Decimal('300'), currency='CNY', status='completed',
            created_at=datetime(past_year, 7, 9, tzinfo=UTC),
        ))
        await s.commit()
        after_deal = await target_bases.annual_bases(s, user, past_year)
        ad = lambda key, m: next(  # noqa: E731
            x['value'] for x in after_deal[key] if x['month'] == m
        )
        check('取消首单后，冻结的首次成交月不变（现算会跳到 7 月）',
              ad('new_by_first_deal', '04'), 1)
        check('首次成交没有挪到 7 月', ad('new_by_first_deal', '07'), 0)

        # **已知边界**（写进断言，免得以为已经全冻住了）：**金额**仍按订单**当前状态**算
        # ——取消首单后那一期的签单额就变 0 了，而"首次成交"这个**基准**不变。
        # 冻结的是"客户集合与首次成交基准"（§4.1.5 前半句）；"每个期间的实绩落库"
        # （实际值快照）还没做，那属于 §4.3 的另一半。
        check('已知边界：金额随订单当前状态变 0，但基准不变', ad('signed', '04'), 0.0)
        snapshot_rows = (await s.execute(text(
            'select metric_basis_version, computed_at from analytics_basis_snapshots '
            'where year = :y'
        ), {'y': past_year})).all()
        check('快照确实落库（一行一年）', len(snapshot_rows), 1)
        check('快照里的口径版本一致', snapshot_rows[0][0], target_bases.BASIS_VERSION)

        # 收干净：本段自己造的快照与订单（订单按 CHKTGT 前缀由 cleanup 收）
        await s.execute(
            text('delete from analytics_basis_snapshots where year = :y'), {'y': past_year}
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
