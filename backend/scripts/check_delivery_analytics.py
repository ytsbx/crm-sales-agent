"""交期履约分析回归（需要 PostgreSQL 在跑；不依赖网络）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_delivery_analytics.py

## 为什么有这条

跟单里程碑与发货批次此前**只写不读**：跟单员登记了实际日期，分析层一个字段都没用，
「有多少单是按期交的」系统答不出来（交期整维在 analytics 里是空的）。

## 锁住的口径（都是算错就会误导业务的地方）

1. 准时 = **首批发货日期 ≤ 客户交期**；延迟天数 = 首批发货 − 交期；
   一单多批次时取**最早**的实际发货日（与里程碑 first_shipment 同源），
   计划中（未发货）的批次不参与；
2. **没填交期的已发货单不进准时率分母**——判不了，既不算准时也不算延迟，
   单独报 `undated_delivered_count`（否则要么虚高要么虚低）；
3. 已取消订单完全不参与（分子、分母、风险、逾期节点都不算）；
4. 在跟风险 = 未完成未取消 + **尚无任何已发货批次**：超期为风险单、7 天内为临近；
   已经发过首批货的单不再算交期风险（该催的是还没发的）；
5. 逾期节点与每日逾期提醒**同口径**：计划日已过 + 实际日期未登记；
   实际日期已登记、哪怕晚于计划日也算完成（不是"逾期未完成"）；
6. 数据范围按**当前负责人**：别人的单一律看不到（本项目在数据范围上翻过车）；
7. 趋势是近 12 个月、按月补齐 12 行。

夹具用 CHKDEL 前缀的独立用户/客户/订单，跑完即清，不碰演示数据。
"""

import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from _test_support import require_isolated_db

require_isolated_db()

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.user.model import User

FAILURES = []
PREFIX = 'CHKDEL'


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
    """按前缀清理夹具；开头与结尾各跑一次，容忍上次中途失败留下的残留。"""
    async with SessionLocal() as s:
        order_scope = '(select id from sales_orders where order_no like :p)'
        await s.execute(text(
            f'delete from order_milestones where order_id in {order_scope}'
        ), {'p': f'{PREFIX}%'})
        await s.execute(text(
            f'delete from order_shipment_batch_items where batch_id in '
            f'(select id from order_shipment_batches where order_id in {order_scope})'
        ), {'p': f'{PREFIX}%'})
        await s.execute(text(
            f'delete from order_shipment_batches where order_id in {order_scope}'
        ), {'p': f'{PREFIX}%'})
        await s.execute(text(
            f'delete from order_status_history where order_id in {order_scope}'
        ), {'p': f'{PREFIX}%'})
        await s.execute(text(
            'delete from sales_orders where order_no like :p'
        ), {'p': f'{PREFIX}%'})
        await s.execute(text(
            'delete from customers where name like :p'
        ), {'p': f'{PREFIX}%'})
        await s.execute(text(
            'delete from users where username like :p'
        ), {'p': f'{PREFIX.lower()}%'})
        await s.commit()


async def main():
    from app.modules.analytics import service as analytics_service
    from app.modules.customer.model import Customer
    from app.modules.order.model import OrderMilestone, OrderShipmentBatch, SalesOrder

    stamp = int(time.time())
    today = datetime.now(UTC).date()
    now = datetime.now(UTC)

    await cleanup()

    async with SessionLocal() as s:
        owner = User(
            name=f'{PREFIX}销售-{stamp}',
            username=f'{PREFIX.lower()}_a_{stamp}',
            password_hash='x',
            status='active',
        )
        other = User(
            name=f'{PREFIX}其他人-{stamp}',
            username=f'{PREFIX.lower()}_b_{stamp}',
            password_hash='x',
            status='active',
        )
        s.add_all([owner, other])
        await s.flush()
        owner_id = owner.id

        customer = Customer(
            name=f'{PREFIX}客户-{stamp}', level='A', status='active',
            pool_status='private', owner_id=owner_id, source='回归',
            customer_type='企业', country='中国',
        )
        s.add(customer)
        await s.flush()
        customer_id = customer.id

        def make_order(suffix, due, status):
            order = SalesOrder(
                order_no=f'{PREFIX}{stamp}{suffix}',
                customer_id=customer_id,
                owner_id=owner_id,
                sales_owner_id=owner_id,
                total_amount=Decimal('1000.00'),
                currency='CNY',
                status=status,
                delivery_date=due,
                delivery_kind="shipping", transit_days=0,
                created_by=owner_id,
            )
            s.add(order)
            return order

        def make_batch(order, batch_no, status, actual):
            batch = OrderShipmentBatch(
                order_id=order.id, batch_no=batch_no, status=status,
                actual_ship_date=actual, created_by=owner_id, created_at=now,
            )
            s.add(batch)
            return batch

        def make_milestone(order, node, planned, actual):
            s.add(OrderMilestone(
                order_id=order.id, node=node, planned_date=planned, actual_date=actual,
                created_by=owner_id, created_at=now,
            ))

        # 1) 准时：交期前 2 天发首批货
        on_time = make_order('A', today - timedelta(days=10), 'completed')
        # 2) 延迟 7 天
        late = make_order('B', today - timedelta(days=10), 'completed')
        # 3) 已过交期仍未发货 → 风险
        risk = make_order('C', today - timedelta(days=5), 'in_production')
        # 4) 3 天内到期、未发货 → 临近
        make_order('D', today + timedelta(days=3), 'pending')
        # 5) 在跟但没填交期
        make_order('E', None, 'in_production')
        # 6) 已取消：一律不参与
        make_order('F', today - timedelta(days=10), 'cancelled')
        # 7) 多批次：取**最早**的实际发货日（若误用最晚/任意批次会判成延迟）
        multi = make_order('G', today - timedelta(days=20), 'completed')
        # 8) 已发货但没填交期：不进准时率分母
        undated_delivered = make_order('H', None, 'completed')
        await s.flush()

        make_batch(on_time, 1, 'shipped', today - timedelta(days=12))

        make_batch(late, 1, 'shipped', today - timedelta(days=3))

        make_batch(multi, 1, 'shipped', today - timedelta(days=25))
        make_batch(multi, 2, 'shipped', today - timedelta(days=18))
        # 计划中的批次没有实际发货日，不能被当成交付事实
        make_batch(multi, 3, 'planned', None)

        make_batch(undated_delivered, 1, 'shipped', today - timedelta(days=4))

        # 逾期节点（与每日逾期提醒同口径）
        make_milestone(risk, 'contract', today - timedelta(days=20), None)      # 逾期
        make_milestone(risk, 'deposit', today + timedelta(days=5), None)        # 未到期
        make_milestone(risk, 'first_shipment', today - timedelta(days=5),       # 已登记即算完成
                       today - timedelta(days=4))
        make_milestone(on_time, 'contract', today - timedelta(days=40), None)   # 已发货单也照算
        await s.commit()

        # 用 self 范围而不是 all：all **不加任何过滤**，会把演示数据的订单一起算进来，
        # 断言就不再只覆盖夹具。self 范围同时正好压到 scoped_owner_ids 那条路径上。
        owner_user = CurrentUser(owner, permissions=set(), roles=[], data_scope='self')
        data = await analytics_service.delivery_stats(s, owner_user)
        summary = data['summary']

        print('=== 1. 准时 / 延迟（首批发货 vs 客户交期）===')
        check('已交付订单数', summary['delivered_order_count'], 3)
        check('准时单数', summary['on_time_count'], 2)
        check('延迟单数', summary['late_count'], 1)
        check('准时率', summary['on_time_rate'], round(2 / 3, 4))
        check('平均延迟天数', summary['average_delay_days'], 7.0)
        check('最大延迟天数', summary['max_delay_days'], 7)

        print('=== 2. 没填交期的已发货单不进分母 ===')
        check('未填交期的已交付单数', summary['undated_delivered_count'], 1)

        print('=== 3. 已取消订单不参与 ===')
        check_true(
            '取消单没有进交付口径',
            summary['delivered_order_count'] + summary['undated_delivered_count'] == 4,
            f"交付+无交期={summary['delivered_order_count'] + summary['undated_delivered_count']}（夹具共 8 单，应为 4）",
        )

        print('=== 4. 在跟风险 ===')
        check('在跟订单数', summary['open_order_count'], 3)
        check('超期未发货单数', summary['risk_order_count'], 1)
        check('7 天内到期单数', summary['due_soon_order_count'], 1)
        check('在跟但未填交期单数', summary['no_due_date_open_count'], 1)
        check_true(
            '风险单是那张超期未发的单、且带超期天数',
            len(data['risk_orders']) == 1
            and data['risk_orders'][0]['order_no'] == f'{PREFIX}{stamp}C'
            and data['risk_orders'][0]['days_overdue'] == 5,
            f"{[r['order_no'] for r in data['risk_orders']]}",
        )
        check_true(
            '已发过首批货的单不算交期风险',
            all(r['order_no'] not in {f'{PREFIX}{stamp}A', f'{PREFIX}{stamp}B'} for r in data['risk_orders']),
        )

        print('=== 5. 逾期节点（与每日逾期提醒同口径）===')
        check('逾期节点总数', summary['overdue_node_count'], 2)
        check('涉及订单数', summary['overdue_order_count'], 2)
        check(
            '逾期节点分布',
            data['overdue_nodes'],
            [{'name': '签订合同', 'value': 2}],
        )

        print('=== 6. 按负责人 ===')
        check('负责人行数', len(data['by_owner']), 1)
        check('负责人交付单数', data['by_owner'][0]['order_count'], 3)
        check('负责人准时单数', data['by_owner'][0]['on_time_count'], 2)
        check('负责人平均延迟', data['by_owner'][0]['average_delay_days'], 7.0)

        print('=== 7. 趋势（近 12 个月，按月补齐）===')
        check('趋势行数', len(data['trend']), 12)
        check('趋势准时合计', sum(r['on_time'] for r in data['trend']), 2)
        check('趋势延迟合计', sum(r['late'] for r in data['trend']), 1)

        print('=== 8. 数据范围：别人的单看不到 ===')
        other_user = CurrentUser(other, permissions=set(), roles=[], data_scope='self')
        other_data = await analytics_service.delivery_stats(s, other_user)
        check('他人视图-在跟单数', other_data['summary']['open_order_count'], 0)
        check('他人视图-交付单数', other_data['summary']['delivered_order_count'], 0)
        check('他人视图-风险单', other_data['risk_orders'], [])
        check('他人视图-负责人行', other_data['by_owner'], [])
        check('他人视图-趋势行数仍为 12', len(other_data['trend']), 12)

    await cleanup()

    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('交期履约分析回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
