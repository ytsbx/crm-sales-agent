"""签单归属回归：离职交接后业绩不改写（文档 §3.8 / :61）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_sales_attribution.py

## 这条业务规则

文档 :61 同时要求两件事，用一列做不到：
  a) 「交接后保留……**历史业绩归属**」——钱算签单的人；
  b) 「逐项分配接手人」——接手人必须看得到这些单子（订单列表按 owner_id 过滤）。

所以订单拆了两列：`owner_id`（当前负责人，随交接变）与 `sales_owner_id`
（签单归属，创建时写死）。这个脚本锁住三件事：

1. 建单时 sales_owner_id 写上签单的人；
2. 交接**只动 owner_id**：接手人能看到单子，sales_owner_id 一个字不动；
3. 业绩按签单归属算：交接后原销售的成交额不缩水、接手人的成交额不虚增；
   目标页的实际销售额同理。

夹具全部带 CHK 前缀、且是**独立的新用户**——交接只会搬动这个用户名下的东西，
不会碰到库里的真实/演示数据（交接是全量搬移，拿真账号跑会污染演示数据）。
跑完即清。
"""

import asyncio
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.user.model import User

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


async def cleanup(ids):
    async with SessionLocal() as s:
        for cid in ids.get('customers', []):
            await s.execute(text('delete from biz_docs where order_draft_id in (select id from order_drafts where customer_id=:c)'), {'c':cid})
            await s.execute(text("delete from audit_logs where business_type='order_draft' and business_id in (select id from order_drafts where customer_id=:c)"), {'c':cid})
            await s.execute(text('delete from order_draft_items where draft_id in (select id from order_drafts where customer_id=:c)'), {'c':cid})
            await s.execute(text('delete from order_drafts where customer_id=:c'), {'c':cid})
            await s.execute(text('delete from custom_inquiries where customer_id=:c'), {'c':cid})
        for oid in ids.get('orders', []):
            await s.execute(text('delete from sales_order_items where order_id = :o'), {'o': oid})
            await s.execute(text('delete from order_status_history where order_id = :o'), {'o': oid})
            await s.execute(text('delete from sales_orders where id = :o'), {'o': oid})
            await s.execute(text(
                "delete from audit_logs where business_type = 'order' and business_id = :o"
            ), {'o': oid})
        for cid in ids.get('customers', []):
            await s.execute(text('delete from customer_owner_history where customer_id = :c'), {'c': cid})
            await s.execute(text('delete from customers where id = :c'), {'c': cid})
        for uid in ids.get('users', []):
            await s.execute(text('delete from user_roles where user_id = :u'), {'u': uid})
            await s.execute(text('delete from users where id = :u'), {'u': uid})
        await s.execute(text(
            "delete from wecom_sync_jobs where job_type = 'transfer' and detail::text like '%CHK%'"
        ))
        await s.commit()


async def main():
    from app.modules.analytics import service as analytics_service
    from app.modules.analytics import targets as targets_service
    from app.modules.customer.model import Customer
    from app.modules.order import service as order_service
    from app.modules.order.model import SalesOrder
    from app.modules.product.model import Sku
    from app.modules.wecom import service as wecom_service

    stamp = int(time.time())
    ids = {'orders': [], 'customers': [], 'users': []}

    async with SessionLocal() as s:
        admin = (await s.execute(select(User).where(User.username == 'admin'))).scalars().one()
        takeover = (await s.execute(select(User).where(User.username == 'wangwu'))).scalars().one()
        sku = (await s.execute(select(Sku).limit(1))).scalars().one()
        admin_user = CurrentUser(admin, permissions=set(), roles=[], data_scope='all')

        print('=== 1. 建单时把签单归属写死 ===')
        leaver = User(
            name=f'CHK离职销售-{stamp}',
            username=f'chk_leaver_{stamp}',
            password_hash='x',
            status='active',
        )
        s.add(leaver)
        await s.flush()
        leaver_id = leaver.id
        ids['users'].append(leaver_id)

        customer = Customer(
            name=f'CHK归属客户-{stamp}', level='A', status='active', pool_status='private',
            owner_id=leaver_id, source='回归', customer_type='企业', country='中国',
        )
        s.add(customer)
        await s.flush()
        customer_id = customer.id
        ids['customers'].append(customer_id)

        order = await order_service.create_order(
            s,
            user_id=leaver_id,
            customer_id=customer_id,
            items=[SimpleNamespace(
                sku_id=sku.id, quantity=Decimal(10), unit_price=Decimal(100),
                specification=None, remark=None,
            )],
        )
        order_id = order.id
        order_owner = order.owner_id
        order_sales_owner = order.sales_owner_id
        order_total = float(order.total_amount)
        ids['orders'].append(order_id)
        await s.commit()
        # commit 后 ORM 属性会过期，惰性访问会触发同步 IO（MissingGreenlet），
        # 所以上面先把要断言的几个值取成普通变量
        check('建单：当前负责人是签单的人', order_owner, leaver_id)
        check('建单：签单归属 = 签单的人', order_sales_owner, leaver_id)
        check_true('成交额按明细算出', order_total == 1000.0, str(order_total))

        async def stat_of(user_id: int) -> dict:
            rows = await analytics_service.sales_user_stats(s, admin_user, limit=200)
            return next((r for r in rows if r['user_id'] == user_id), {})

        from app.modules.inquiry.model import CustomInquiry
        from app.modules.order import drafts as draft_service
        from app.modules.order.schema import OrderDraftCreate
        from app.modules.order.model import OrderDraft
        from app.modules.bizdoc.model import BizDoc
        from uuid import uuid4
        inquiry=CustomInquiry(customer_id=order.customer_id, title='CHK交接草稿需求', inquiry_no='CHK交接', quantity=5)
        s.add(inquiry); await s.flush()
        draft_user=CurrentUser(leaver, permissions={'quote:view','order:manage'}, roles=[], data_scope='self')
        draft=await draft_service.create(s,draft_user,OrderDraftCreate(inquiry_id=inquiry.id,request_key=uuid4(),items=[{'source_item_id':inquiry.id,'quantity':5}]))
        document=await draft_service.generate_document(s,draft_user,draft.id)
        draft_id,doc_id,doc_hash=draft.id,document.id,document.content_sha256
        original_source=dict(draft.source_context)
        await s.commit()
        takeover_id = takeover.id
        before_leaver = await stat_of(leaver_id)
        before_takeover = await stat_of(takeover_id)
        check('交接前：离职销售签单额 1000', before_leaver.get('order_amount'), 1000.0)

        def target_actual(rows: list[dict], user_id: int) -> float:
            return sum(
                r['sales_actual'] for r in rows
                if r['user_id'] == user_id and r['period'] == datetime.now(UTC).strftime('%Y-%m')
            )

        year = datetime.now(UTC).year
        targets_before = await targets_service.targets_with_actuals(s, admin_user, year)
        check('交接前：目标页实际销售额含这 1000',
              target_actual(targets_before['rows'], leaver_id), 1000.0)

        print()
        print('=== 2. 离职交接：只动当前负责人 ===')
        job = await wecom_service.transfer_relations(
            s,
            user=admin_user,
            handover_user_id=leaver_id,
            takeover_user_id=takeover_id,
            transfer_wecom=False,  # 不碰企微（夹具没有企微关系）
        )
        # transfer_relations 不提交（由调用方收口，接口层是这么做的），
        # 所以这里要自己 commit；commit 后 ORM 属性会过期，job.detail 先取出来
        job_detail = dict(job.detail or {})
        await s.commit()
        fresh = await s.get(SalesOrder, order_id)
        check('交接：订单当前负责人转给接手人', fresh.owner_id, takeover_id)
        check('交接：签单归属一个字没动', fresh.sales_owner_id, leaver_id)
        check_true('交接：明细里报了订单条数', job_detail.get('orders') == 1,
                   str(job_detail.get('orders')))

        print()
        moved_draft=await s.get(OrderDraft,draft_id)
        moved_doc=await s.get(BizDoc,doc_id)
        check('交接：草稿负责人转给接手人',moved_draft.owner_id,takeover_id)
        check('交接：草稿作者保留',moved_draft.created_by,leaver_id)
        check('交接：草稿来源不改写',moved_draft.source_context,original_source)
        check('交接：草稿文件随授权交接',moved_doc.owner_id,takeover_id)
        check('交接：历史文件校验值不改写',moved_doc.content_sha256,doc_hash)
        print('=== 3. 业绩仍算签单的人（钱不跟着交接走）===')
        after_leaver = await stat_of(leaver_id)
        after_takeover = await stat_of(takeover_id)
        check('交接后：离职销售签单额仍是 1000', after_leaver.get('order_amount'), 1000.0)
        check('交接后：接手人签单额没有虚增',
              after_takeover.get('order_amount'), before_takeover.get('order_amount'))

        targets_after = await targets_service.targets_with_actuals(s, admin_user, year)
        check('交接后：目标页实际销售额仍算离职销售',
              target_actual(targets_after['rows'], leaver_id), 1000.0)

        print()
        print('=== 4. 接手人看得到这张单（数据范围按当前负责人）===')
        visible = (
            await s.execute(
                select(SalesOrder.id).where(SalesOrder.owner_id == takeover_id)
            )
        ).scalars().all()
        check_true('接手人的订单列表包含这张单', order_id in list(visible),
                   f'owner_id={takeover_id} 名下 {len(visible)} 单')

    await cleanup(ids)
    print()
    print('（夹具已清理）')
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('签单归属回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
