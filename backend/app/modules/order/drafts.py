"""订单准备资料。只有客户确认且与草稿逐项一致的报价才能转正式订单。"""
from collections import Counter
from decimal import Decimal
import hashlib
import json

from sqlalchemy import select, text
from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope
from app.core.errors import AppError, ErrorCode
from app.core.trade_mode import ensure_currency_allowed
from app.modules.inquiry.model import CustomInquiry
from app.modules.order.model import OrderDraft, OrderDraftItem
from app.modules.order import service as order_service
from app.modules.quote import service as quote_service
from app.modules.sample import sources


async def preview(session, user, **ref):
    source = await sources.load_source(session, user, **ref)
    if ref.get('quote_version_id'):
        version = await quote_service.get_visible_version(session, user, ref['quote_version_id'])
        prices = {i.id: i.quoted_price for i in await quote_service.version_items(session, version.id)}
        source['currency'] = version.currency
        source['payment_terms'] = version.payment_terms
        for row in source['items']:
            row['unit_price'] = str(prices[row['source_item_id']])
    else:
        source['currency'] = 'CNY'
        source['payment_terms'] = None
        for row in source['items']:
            row['unit_price'] = None  # 客户目标价不是核定成交价
    return source


async def get_visible(session, user, draft_id, *, lock=False):
    row = (await session.execute(select(OrderDraft).where(OrderDraft.id == draft_id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none() if lock else await session.get(OrderDraft, draft_id)
    if not row:
        raise AppError(ErrorCode.NOT_FOUND, '订单草稿不存在', 404)
    await ensure_in_scope(session, user, owner_id=row.owner_id, label='订单草稿')
    return row


async def items(session, draft_id):
    return list((await session.execute(select(OrderDraftItem).where(OrderDraftItem.draft_id == draft_id)
        .order_by(OrderDraftItem.id))).scalars())


async def detail(session, draft):
    return {'id': draft.id, 'customer_id': draft.customer_id, 'opportunity_id': draft.opportunity_id,
        'source_context': draft.source_context, 'status': draft.status, 'order_id': draft.order_id,
        'currency': draft.currency, 'delivery_date': draft.delivery_date, 'payment_terms': draft.payment_terms,
        'remark': draft.remark, 'revision': draft.revision, 'created_at': draft.created_at,
        'items': [{'id': i.id, 'source_item_id': i.source_snapshot['source_item_id'], 'name': i.name,
            'source_snapshot': i.source_snapshot, 'quantity': float(i.quantity),
            'unit_price': float(i.unit_price) if i.unit_price is not None else None,
            'specification': i.specification, 'remark': i.remark} for i in await items(session, draft.id)]}


def require_editable(draft, revision):
    if draft.status != 'draft':
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '该草稿已转正式订单，不可再修改', 422)
    if draft.revision != revision:
        raise AppError(ErrorCode.VERSION_CONFLICT, '草稿已被其他人修改，请刷新后核对', 409)


async def create(session, user, payload):
    digest = hashlib.sha256(json.dumps(payload.model_dump(mode='json', exclude={'request_key'}), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = str(payload.request_key)
    await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int.from_bytes(hashlib.sha256(('order-draft:'+key).encode()).digest()[:8], 'big', signed=True)})
    existing = (await session.execute(select(OrderDraft).where(OrderDraft.request_key == key))).scalar_one_or_none()
    if existing:
        await ensure_in_scope(session, user, owner_id=existing.owner_id, label='订单草稿')
        if existing.created_by != user.id:
            raise AppError(ErrorCode.FORBIDDEN, '该请求编号不可使用', 403)
        if existing.request_hash != digest:
            raise AppError(ErrorCode.PARAM_ERROR, '该请求编号已用于不同草稿内容', 409)
        return existing
    data = await preview(session, user, quote_version_id=payload.quote_version_id, inquiry_id=payload.inquiry_id)
    available = {i['source_item_id']: i for i in data['items']}
    if any(i.source_item_id not in available for i in payload.items):
        raise AppError(ErrorCode.PARAM_ERROR, '所选明细不属于该来源版本', 422)
    draft = OrderDraft(customer_id=data['customer_id'], opportunity_id=data['opportunity_id'], owner_id=data['owner_id'],
        source_context=data['source'], currency=data['currency'], payment_terms=payload.payment_terms if payload.payment_terms is not None else data['payment_terms'],
        delivery_date=payload.delivery_date, remark=payload.remark, request_key=key, request_hash=digest, created_by=user.id)
    session.add(draft); await session.flush()
    for selected in payload.items:
        original = available[selected.source_item_id]
        price = selected.unit_price if 'unit_price' in selected.model_fields_set else (Decimal(original['unit_price']) if original['unit_price'] is not None else None)
        session.add(OrderDraftItem(draft_id=draft.id, source_snapshot=original, name=original['name'],
            quantity=selected.quantity, unit_price=price,
            specification=selected.specification if selected.specification is not None else original['specification'],
            remark=selected.remark if selected.remark is not None else original['remark']))
    await session.flush()
    await write_audit(session, operator_id=user.id, action='create', business_type='order_draft', business_id=draft.id,
                      after=await detail(session, draft))
    return draft


async def update(session, user, draft_id, payload):
    draft = await get_visible(session, user, draft_id, lock=True)
    require_editable(draft, payload.revision)
    existing = {i.source_snapshot['source_item_id']: i for i in await items(session, draft.id)}
    ids = [i.source_item_id for i in payload.items]
    if len(set(ids)) != len(ids) or any(i not in existing for i in ids):
        raise AppError(ErrorCode.PARAM_ERROR, '明细重复或不属于该草稿', 422)
    before = await detail(session, draft)
    for selected in payload.items:
        item = existing[selected.source_item_id]
        item.quantity, item.unit_price = selected.quantity, selected.unit_price
        item.specification, item.remark = selected.specification, selected.remark
    for key, item in existing.items():
        if key not in ids:
            await session.delete(item)
    # 业务口径闸（2026-10-08）：这一行是**界面上唯一还能改币种的地方**
    # （草稿页那个输入框此前不看 trade_mode）。不传 = 不改，所以只在给了值时才判。
    if payload.currency is not None:
        draft.currency = await ensure_currency_allowed(
            session, payload.currency, label="订单币种"
        )
    draft.delivery_date, draft.payment_terms, draft.remark = payload.delivery_date, payload.payment_terms, payload.remark
    draft.revision += 1
    await session.flush()
    await write_audit(session, operator_id=user.id, action='update', business_type='order_draft', business_id=draft.id,
                      before=before, after=await detail(session, draft))
    return draft


async def confirm(session, user, draft_id, payload):
    draft = await get_visible(session, user, draft_id)
    if draft.status == 'converted':
        order = await order_service.get_visible_order(session, user, draft.order_id)
        if order.quote_version_id != payload.quote_version_id:
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '该草稿已用于另一报价版本的订单', 409)
        return order
    from app.modules.customer.service import get_visible_customer
    await get_visible_customer(session, user, draft.customer_id)
    # 与正式转单/新建版本统一锁顺序：商机→报价→版本→草稿。
    version = await quote_service.get_visible_version(session, user, payload.quote_version_id, for_update=True)
    quote = await quote_service.get_visible_quote(session, user, version.quote_id)
    draft = await get_visible(session, user, draft_id, lock=True)
    if draft.status == 'converted':
        order = await order_service.get_visible_order(session, user, draft.order_id)
        if order.quote_version_id != version.id:
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '该草稿已转另一版本订单', 409)
        return order
    require_editable(draft, payload.revision)
    if draft.opportunity_id is None and draft.source_context.get('type') == 'inquiry':
        source_inquiry = await session.get(CustomInquiry, draft.source_context['id'])
        if source_inquiry and source_inquiry.opportunity_id == quote.opportunity_id:
            draft.opportunity_id = quote.opportunity_id
    if quote.customer_id != draft.customer_id or quote.opportunity_id != draft.opportunity_id:
        raise AppError(ErrorCode.PARAM_ERROR, '确认报价必须属于该客户和同一商机需求', 422)
    if not version.accepted_at or quote.status != 'accepted':
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '正式下单须先记录客户接受该报价', 422)
    rows = await quote_service.version_items(session, version.id)
    prepared = await items(session, draft.id)
    # 对重复 SKU 使用包含规格/数量/价格/备注的多重集，逐行保留重数，不能用字典覆盖。
    inquiry_ids = {i.inquiry_id for i in rows if i.inquiry_id} | {i.source_snapshot.get('inquiry_id') for i in prepared if i.source_snapshot.get('inquiry_id')}
    roots = {i.id: i.root_id or i.id for i in (await session.execute(select(CustomInquiry).where(CustomInquiry.id.in_(inquiry_ids)))).scalars()}
    def identity(sku_id, inquiry_id):
        return ('sku', sku_id) if sku_id else ('inquiry', roots.get(inquiry_id, inquiry_id))
    expected = Counter((identity(i.sku_id, i.inquiry_id), i.spec_snapshot or '', i.quantity, i.quoted_price, i.remark or '') for i in rows)
    actual = Counter((identity(i.source_snapshot.get('sku_id'), i.source_snapshot.get('inquiry_id')),
                      i.specification or '', i.quantity, i.unit_price, i.remark or '') for i in prepared)
    if expected != actual or draft.currency != version.currency or (draft.payment_terms or '') != (version.payment_terms or ''):
        raise AppError(ErrorCode.PARAM_ERROR, '草稿明细、币种或付款条件与客户确认报价不一致；请先修订报价并取得客户确认，再核对草稿。正式订单须包含该确认版本的全部明细。', 422)
    order = await order_service.create_order_from_quote(session, version=version, user_id=user.id,
                                                       delivery_date=draft.delivery_date, remark=draft.remark)
    draft.status, draft.order_id = 'converted', order.id
    draft.revision += 1
    await write_audit(session, operator_id=user.id, action='confirm', business_type='order_draft', business_id=draft.id,
                      after={'order_id': order.id, 'quote_version_id': version.id})
    return order


async def generate_document(session, user, draft_id, request_key=None):
    """给订单草稿出一份下单文件（§8.9 补幂等键）。

    双击 / 弱网重试在这条路径上同样会多出一份：原来每次请求都新插一行，
    两份内容一模一样、版本号却连着，事后分不清哪份是"要发出去的那份"。
    带上 request_key 后同一把键重试返回原文件；要明确再出一版就换新键
    （哪怕内容没变也照出——不能按内容去重，那会挡掉合法的新版）。
    """
    from app.modules.bizdoc import service as docs
    from app.modules.customer.model import Customer

    async def _build():
        draft = await get_visible(session, user, draft_id, lock=True)
        rows = await items(session, draft_id)
        current = [{'source_item_id': i.source_snapshot['source_item_id'], 'name': i.name,
                    'spec': i.specification, 'quantity': i.quantity, 'unit_price': i.unit_price,
                    'amount': i.quantity*i.unit_price if i.unit_price is not None else None, 'remark': i.remark} for i in rows]
        original = [{'source_item_id': i.source_snapshot['source_item_id'], 'name': i.source_snapshot['name'],
                     'spec': i.source_snapshot.get('specification'), 'quantity': i.source_snapshot.get('original_quantity'),
                     'remark': i.source_snapshot.get('remark'), 'unit_price': i.source_snapshot.get('unit_price')} for i in rows]
        built = {'order_draft_id': draft.id, 'customer_id': draft.customer_id,
            'customer': await session.get(Customer, draft.customer_id), 'owner_id': draft.owner_id,
            'source': draft.source_context, 'items': current, 'diffs': docs._diff_lines(current, original, 'source_item_id'),
            'title_suffix': f'#{draft.id}', 'sections': [
                {'label': '单据性质', 'value': '订单草稿，仅供核对准备，不代表正式下单或生产指令'},
                {'label': '草稿修订', 'value': str(draft.revision)},
                {'label': '币种', 'value': draft.currency},
                {'label': '计划交期', 'value': str(draft.delivery_date or '')},
                {'label': '付款条件', 'value': draft.payment_terms or ''}, {'label': '备注', 'value': draft.remark or ''}]}
        template = await docs.current_template(session, 'order_sheet', None)
        return await docs._persist(session, built=built, doc_type='order_sheet', template=template,
                                   user_id=user.id, extra_fields=None, source_ref=draft.source_context)

    doc, replayed = await docs.run_idempotent_generation(
        session, user_id=user.id, action='bizdoc:order_draft_sheet',
        request_key=request_key, payload={'order_draft_id': draft_id}, generate=_build)
    await write_audit(session, operator_id=user.id, action='generate_replay' if replayed else 'generate',
                      business_type='order_draft', business_id=draft_id,
                      after={'document_id': doc.id, 'document_version': doc.version, 'replayed': replayed})
    return doc
