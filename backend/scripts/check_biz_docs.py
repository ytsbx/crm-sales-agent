"""对外单据生成回归：打样需求单 / 下单文件（文档 §3.5、场景12）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_biz_docs.py

## 场景12 的原话

| 12 | 勾选旧询价生成打样和下单文件 | **原询价不变**，新单保留**来源、差异**和**可下载文件** |

逐条落成断言：

1. 从打样申请生成 → 来源询价号与版本进文件行、明细数量如实、差异能看出改了什么；
2. **原单不变**：生成前后来源询价的标题/数量/版本、打样申请的状态与明细一个字不动；
3. **旧文件不被覆盖**：再生成一次得到 V2（parent 指向 V1），V1 的快照与校验值不变，
   两份都下载得到 PDF；
4. 下单文件同口径，来源是它转单时用的那一版报价（version_no）；
5. 已取消的订单不出单（422），别人的业务对象不能拿来出单（403）；
6. 校验值可重算——同一份快照算两次必须一样（否则它没法用来判断"文件被改过没有"）；
7. 模板加了一版之后，历史文件仍指向它们当时用的那一版。

夹具全部用 CHKBD 前缀，跑完即清。
"""

import asyncio
import copy
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.user.model import User

FAILURES = []
PREFIX = 'CHKBD'


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
        sample = "(select id from sample_requests where customer_id in %s)" % cust
        order = "(select id from sales_orders where customer_id in %s)" % cust
        quote = "(select id from quotes where customer_id in %s)" % cust
        for sql in (
            # 先删引用方，再删被引用方（外键顺序）
            f'delete from biz_docs where customer_id in {cust}',
            'delete from biz_doc_templates where name like :p '
            'or (remark is not null and remark like :p)',
            f'delete from sample_items where sample_request_id in {sample}',
            f'delete from sample_requests where customer_id in {cust}',
            f'delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in {quote})',
            f'delete from quote_versions where quote_id in {quote}',
            f'delete from quotes where customer_id in {cust}',
            f'delete from sales_order_items where order_id in {order}',
            f'delete from order_status_history where order_id in {order}',
            f'delete from sales_orders where customer_id in {cust}',
            f'delete from custom_inquiries where customer_id in {cust}',
            'delete from customers where name like :p',
            'delete from users where username like :u',
        ):
            params = {'p': f'{PREFIX}%', 'u': f'{PREFIX.lower()}%'}
            await s.execute(text(sql), params)
        await s.commit()


async def main():
    # 先把全部模型注册进 metadata：只 import 用到的几个模型时，
    # sample_requests.opportunity_id 这类外键会因为目标表没注册而在 flush 时报
    # NoReferencedTableError（应用里走 main.py 才不会有这个问题）。
    import app.main  # noqa: F401
    _ = app.main  # 显式引用一次：导入的副作用就是我们要的，别让静态检查报未使用

    from app.modules.bizdoc import service as bizdoc
    from app.modules.bizdoc.model import BizDocTemplate
    from app.modules.customer.model import Customer
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.order.model import SalesOrder, SalesOrderItem
    from app.modules.product.model import Sku
    from app.modules.quote.model import Quote, QuoteItem, QuoteVersion
    from app.modules.sample.model import SampleItem, SampleRequest

    stamp = int(time.time())
    now = datetime.now(UTC)
    today = now.date()

    await cleanup()

    async with SessionLocal() as s:
        owner = User(
            name=f'{PREFIX}销售-{stamp}', username=f'{PREFIX.lower()}_a_{stamp}',
            password_hash='x', status='active',
        )
        outsider = User(
            name=f'{PREFIX}其他人-{stamp}', username=f'{PREFIX.lower()}_b_{stamp}',
            password_hash='x', status='active',
        )
        s.add_all([owner, outsider])
        await s.flush()
        owner_user = CurrentUser(owner, permissions=set(), roles=[], data_scope='self')
        outsider_user = CurrentUser(outsider, permissions=set(), roles=[], data_scope='self')

        sku = (await s.execute(select(Sku).limit(1))).scalars().one()
        customer = Customer(
            name=f'{PREFIX}客户-{stamp}', level='A', status='active', pool_status='private',
            owner_id=owner.id, source='回归', customer_type='企业', country='中国',
        )
        s.add(customer)
        await s.flush()

        # 来源：一条定制需求（数量 10）
        inquiry = CustomInquiry(
            inquiry_no=f'XQ{stamp}', title=f'{PREFIX}定制法兰', description='客户原始要求：304 不锈钢',
            version=1, customer_id=customer.id, quantity=Decimal('10'), status='open',
            created_by=owner.id,
        )
        s.add(inquiry)
        await s.flush()

        # 打样申请：勾了这条需求，本次数量改成 12（差异就该是 10 → 12）
        sample = SampleRequest(
            customer_id=customer.id, owner_id=owner.id, status='approved',
            remark='打样要求：材质 304，表面拉丝', created_by=owner.id, requested_at=now,
        )
        s.add(sample)
        await s.flush()
        s.add_all([
            SampleItem(
                sample_request_id=sample.id, inquiry_id=inquiry.id,
                inquiry_no_snapshot=inquiry.inquiry_no, item_name=f'{PREFIX}定制法兰',
                quantity=Decimal('12'), remark='本次改为 12 件',
            ),
            # 来源之外新增的一行：差异里应记成"新增"
            SampleItem(
                sample_request_id=sample.id, sku_id=sku.id, item_name=None,
                quantity=Decimal('1'), remark='随样寄一份标准件',
            ),
        ])
        await s.commit()

        print('=== 1. 打样需求单：来源与差异 ===')
        sample_before = (sample.status, sample.remark)
        doc1 = await bizdoc.generate_sample_request_doc(
            s, sample_request_id=sample.id, user=owner_user
        )
        await s.commit()
        check('单据类型', doc1.doc_type, 'sample_request')
        check('来源询价号', doc1.source_no, inquiry.inquiry_no)
        check('来源版本', doc1.source_version, 1)
        check('第一份的版本号', doc1.version, 1)
        check('第一份没有前一版', doc1.parent_id, None)
        check('单号前缀', doc1.doc_no[:2], 'DY')
        items = doc1.input_snapshot['items']
        check('明细行数', len(items), 2)
        check('明细数量如实（12）', str(items[0]['quantity']), '12')
        diffs = doc1.input_snapshot['diffs']
        check_true(
            '差异里有"数量 10 → 12"',
            any(d['field'] == '数量' and d['before'] == '10' and d['after'] == '12' for d in diffs),
            f'{diffs}',
        )
        check_true(
            '来源之外的那一行记成新增',
            any(d['field'] == '明细' and (d.get('after') or "").startswith('新增') for d in diffs),
            f'{diffs}',
        )

        print('=== 2. 原单不变 ===')
        await s.refresh(inquiry)
        await s.refresh(sample)
        sample_item = (
            await s.execute(
                select(SampleItem).where(
                    SampleItem.sample_request_id == sample.id, SampleItem.inquiry_id == inquiry.id
                )
            )
        ).scalars().one()
        check('来源询价标题未变', inquiry.title, f'{PREFIX}定制法兰')
        # 用 Decimal 数值比较：库里的 Numeric(16,3) 读出来是 Decimal('10.000')，
        # 拿 str() 比 '10' 会假报失败（10.000 与 10 是同一个数）
        check('来源询价数量未变', inquiry.quantity, Decimal('10'))
        check('来源询价版本未变', inquiry.version, 1)
        check('打样申请状态未变', (sample.status, sample.remark), sample_before)
        check('打样明细数量未变', sample_item.quantity, Decimal('12'))

        print('=== 3. 旧文件不被覆盖 ===')
        snapshot_v1 = copy.deepcopy(doc1.input_snapshot)
        hash_v1 = doc1.content_sha256
        doc1_id = doc1.id
        doc2 = await bizdoc.generate_sample_request_doc(
            s, sample_request_id=sample.id, user=owner_user
        )
        await s.commit()
        check('第二份版本号', doc2.version, 2)
        check('第二份指向第一份', doc2.parent_id, doc1_id)
        reloaded = await bizdoc.get_doc_or_404(s, doc1_id)
        check('第一份快照没被改动', reloaded.input_snapshot, snapshot_v1)
        check('第一份校验值没被改动', reloaded.content_sha256, hash_v1)
        check_true('两份单号不同', doc2.doc_no != doc1.doc_no, f'{doc1.doc_no} / {doc2.doc_no}')
        from app.modules.bizdoc.pdf import render_biz_doc_pdf

        for label, target in (('V1', reloaded), ('V2', doc2)):
            pdf = render_biz_doc_pdf(await bizdoc.doc_pdf_data(s, target))
            check_true(f'{label} 下载得到 PDF', pdf[:4] == b'%PDF', f'前 4 字节 {pdf[:4]!r}')

        print('=== 4. 下单文件：来源报价与差异 ===')
        quote = Quote(
            quote_no=f'Q{stamp}', customer_id=customer.id, owner_id=owner.id,
            status='accepted', created_by=owner.id,
        )
        s.add(quote)
        await s.flush()
        qv = QuoteVersion(
            quote_id=quote.id, version_no=3, subtotal_amount=Decimal('500'),
            total_amount=Decimal('500'), currency='CNY', created_at=now,
        )
        s.add(qv)
        await s.flush()
        quote.current_version_id = qv.id
        s.add(QuoteItem(
            quote_version_id=qv.id, sku_id=sku.id, sku_name_snapshot='ZX-6040-B',
            quantity=Decimal('5'), quoted_price=Decimal('100'),
        ))
        order = SalesOrder(
            order_no=f'{PREFIX}{stamp}', customer_id=customer.id, owner_id=owner.id,
            sales_owner_id=owner.id, quote_id=quote.id, quote_version_id=qv.id,
            total_amount=Decimal('800'), currency='CNY', status='pending',
            delivery_date=today + timedelta(days=10), payment_terms='30% 预付',
            created_by=owner.id,
        )
        s.add(order)
        await s.flush()
        s.add(SalesOrderItem(
            order_id=order.id, sku_id=sku.id, sku_snapshot='ZX-6040-B',
            specification='600*400*300mm', quantity=Decimal('8'),
            unit_price=Decimal('100'), amount=Decimal('800'),
        ))
        await s.commit()

        order_doc = await bizdoc.generate_order_sheet_doc(s, order_id=order.id, user=owner_user)
        await s.commit()
        check('单据类型', order_doc.doc_type, 'order_sheet')
        check('单号前缀', order_doc.doc_no[:2], 'XD')
        check('来源报价号', order_doc.source_no, quote.quote_no)
        check('来源报价版本', order_doc.source_version, 3)
        order_diffs = order_doc.input_snapshot['diffs']
        check_true(
            '差异里有"数量 5 → 8"',
            any(
                d['field'] == '数量' and d['before'] == '5' and d['after'] == '8'
                for d in order_diffs
            ),
            f'{order_diffs}',
        )
        check(
            '下单文件带上了客户交期',
            any(
                sec['label'] == '客户交期' and sec['value'] == (today + timedelta(days=10)).isoformat()
                for sec in order_doc.input_snapshot['sections']
            ),
            True,
        )

        print('=== 4b. 对客 Excel 报价单：金额与报价版本逐项一致 ===')
        from app.modules.bizdoc.model import DOC_TYPE_FORMAT
        from app.modules.bizdoc.xlsx import render_quote_xlsx

        check('报价单出的格式是 Excel', DOC_TYPE_FORMAT.get('quote_sheet'), 'xlsx')
        quote_doc = await bizdoc.generate_quote_doc(
            s, quote_version_id=qv.id, user=owner_user
        )
        await s.commit()
        check('单据类型', quote_doc.doc_type, 'quote_sheet')
        check('单号前缀', quote_doc.doc_no[:2], 'BJ')
        check('来源报价号', quote_doc.source_no, quote.quote_no)
        check('来源报价版本', quote_doc.source_version, 3)
        snap_items = quote_doc.input_snapshot['items']
        check('明细行数', len(snap_items), 1)
        # 金额取自版本快照的 quoted_price × quantity，两处必须逐项一致
        check('明细单价与报价版本一致', str(snap_items[0]['unit_price']), '100')
        check('明细金额 = 数量 × 单价', str(snap_items[0]['amount']), '500')
        check('合计取自报价版本', str(quote_doc.input_snapshot['total_amount']), '500')

        # 改当前价格规则不能影响已出的表：报价快照是唯一数据源
        quote_item_row = (
            await s.execute(select(QuoteItem).where(QuoteItem.quote_version_id == qv.id))
        ).scalars().one()
        quote_item_row.quoted_price = Decimal('999')
        await s.commit()
        reloaded_doc = await bizdoc.get_doc_or_404(s, quote_doc.id)
        check(
            '报价版本事后被改，已出的表不动',
            str(reloaded_doc.input_snapshot['items'][0]['unit_price']),
            '100',
        )
        quote_item_row.quoted_price = Decimal('100')
        await s.commit()

        xlsx_bytes = render_quote_xlsx(await bizdoc.doc_pdf_data(s, quote_doc))
        check_true(
            '报价单下载得到 xlsx（zip 魔数 PK）',
            xlsx_bytes[:2] == b'PK',
            f'前 2 字节 {xlsx_bytes[:2]!r}',
        )
        quote_doc2 = await bizdoc.generate_quote_doc(
            s, quote_version_id=qv.id, user=owner_user
        )
        await s.commit()
        check('重新生成是新增一版', quote_doc2.version, 2)
        check('新版指向前一版', quote_doc2.parent_id, quote_doc.id)

        print('=== 5. 取消的订单不出单 / 越权出单被拒 ===')
        cancelled = SalesOrder(
            order_no=f'{PREFIX}{stamp}C', customer_id=customer.id, owner_id=owner.id,
            sales_owner_id=owner.id, total_amount=Decimal('1'), currency='CNY',
            status='cancelled', created_by=owner.id,
        )
        s.add(cancelled)
        await s.commit()
        from app.core.errors import AppError

        try:
            await bizdoc.generate_order_sheet_doc(s, order_id=cancelled.id, user=owner_user)
            check_true('取消的订单被拒', False, '没有抛异常')
        except AppError as exc:
            check('取消的订单被拒（422）', exc.http_status, 422)
        await s.rollback()

        try:
            await bizdoc.generate_sample_request_doc(
                s, sample_request_id=sample.id, user=outsider_user
            )
            check_true('别人的打样申请被拒', False, '没有抛异常')
        except AppError as exc:
            check('别人的打样申请被拒（403）', exc.http_status, 403)
        await s.rollback()

        print('=== 6. 校验值可重算 ===')
        again = bizdoc._content_hash(doc1.doc_no, doc1.template_version, snapshot_v1)
        check('同一份快照算两次一致', again, hash_v1)
        changed = copy.deepcopy(snapshot_v1)
        changed['items'][0]['quantity'] = '999'
        check_true(
            '快照改了校验值就变',
            bizdoc._content_hash(doc1.doc_no, doc1.template_version, changed) != hash_v1,
        )

        print('=== 7. 模板加一版后，历史文件仍指旧版 ===')
        new_version = await bizdoc.next_template_version(s, 'sample_request')
        s.add(BizDocTemplate(
            doc_type='sample_request', name=f'{PREFIX}正式模板', version=new_version,
            body='1. 按上表打样。', enabled=True, created_by=owner.id, created_at=now,
        ))
        await s.commit()
        doc3 = await bizdoc.generate_sample_request_doc(
            s, sample_request_id=sample.id, user=owner_user
        )
        await s.commit()
        check('新文件用新模板版本', doc3.template_version, new_version)
        reloaded_v1 = await bizdoc.get_doc_or_404(s, doc1_id)
        check('V1 仍指向它当时用的模板版本', reloaded_v1.template_version, 1)
        check('V1 校验值仍未变', reloaded_v1.content_sha256, hash_v1)

    await cleanup()

    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('对外单据生成回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
