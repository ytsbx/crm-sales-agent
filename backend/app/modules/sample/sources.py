"""询价/报价版本 → 打样：仅复制已知资料，来源快照与本次差异分开保存。"""
import hashlib
import json
from decimal import Decimal

from sqlalchemy import select

from app.core.audit import write_audit
from app.core.errors import AppError, ErrorCode
from app.modules.customer import service as customer_service
from app.modules.followup.service import record_and_notify
from app.modules.inquiry import service as inquiry_service
from app.modules.inquiry.model import CustomInquiry
from app.modules.opportunity import service as opportunity_service
from app.modules.quote import service as quote_service
from app.modules.sample import service
from app.modules.sample.model import SampleItem, SampleRequest


async def load_source(session, user, *, quote_version_id=None, inquiry_id=None):
    if 'admin' not in user.roles and not user.has('quote:view'):
        raise AppError(ErrorCode.FORBIDDEN, '读取打样来源需要报价/询价查看权限', 403)
    if quote_version_id:
        version = await quote_service.get_visible_version(session, user, quote_version_id, for_update=True)
        quote = await quote_service.get_visible_quote(session, user, version.quote_id)
        context = {'type': 'quote_version', 'id': version.id, 'quote_id': quote.id,
                   'no': quote.quote_no, 'version': version.version_no,
                   'approval_status': version.approval_status,
                   'is_historical': quote.current_version_id != version.id,
                   'delivery_terms': version.delivery_terms}
        customer_id, opportunity_id, contact_id = quote.customer_id, quote.opportunity_id, quote.contact_id
        items = [{
            'source_item_id': item.id, 'sku_id': item.sku_id, 'inquiry_id': item.inquiry_id,
            'inquiry_no': item.inquiry_no_snapshot, 'sku_code': item.sku_code_snapshot,
            'name': item.sku_name_snapshot or item.inquiry_no_snapshot or '未命名明细',
            'specification': item.spec_snapshot, 'original_quantity': str(item.quantity),
            'remark': item.remark,
        } for item in await quote_service.version_items(session, version.id)]
    else:
        inquiry = await inquiry_service.get_visible_or_404(session, user, inquiry_id)
        await session.execute(select(CustomInquiry).where(
            CustomInquiry.id == (inquiry.root_id or inquiry.id)
        ).with_for_update())
        await session.refresh(inquiry)
        context = {'type': 'inquiry', 'id': inquiry.id, 'no': inquiry.inquiry_no,
                   'version': inquiry.version or 1, 'is_historical': inquiry.superseded_at is not None}
        customer_id, opportunity_id, contact_id = inquiry.customer_id, inquiry.opportunity_id, inquiry.contact_id
        items = [{
            'source_item_id': inquiry.id, 'sku_id': None, 'inquiry_id': inquiry.id,
            'inquiry_no': inquiry.inquiry_no, 'sku_code': None, 'name': inquiry.title,
            'specification': inquiry.description,
            'original_quantity': str(inquiry.quantity) if inquiry.quantity is not None else None,
            'remark': inquiry.remark,
        }]
    if not customer_id:
        raise AppError(ErrorCode.PARAM_ERROR, '来源尚未关联客户，请先补客户再打样', 422)
    customer = await customer_service.get_visible_customer(session, user, customer_id)
    opportunity = (await opportunity_service.get_visible_opportunity(session, user, opportunity_id)
                   if opportunity_id else None)
    if opportunity and opportunity.customer_id != customer_id:
        raise AppError(ErrorCode.PARAM_ERROR, '来源商机与客户不一致，请先核对来源', 422)
    await opportunity_service.validate_contact(session, customer_id=customer_id, contact_id=contact_id)
    return {'source': context, 'customer_id': customer_id, 'customer_name': customer.name,
            'opportunity_id': opportunity_id, 'contact_id': contact_id,
            'opportunity_title': opportunity.title if opportunity else None,
            'owner_id': (opportunity.owner_id if opportunity else customer.owner_id) or user.id,
            'items': items}


async def create_from_source(session, user, payload, *, ip=None):
    key = str(payload.request_key)
    digest = hashlib.sha256(json.dumps(payload.model_dump(mode='json', exclude={'request_key'}),
                            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # 同 key 串行化；不用外部锁，事务失败时一并释放。
    from sqlalchemy import text
    await session.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                          {'key': int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)})
    existing = (await session.execute(select(SampleRequest).where(SampleRequest.request_key == key))).scalar_one_or_none()
    if existing:
        if existing.created_by != user.id:
            raise AppError(ErrorCode.FORBIDDEN, '该请求编号不可使用', 403)
        if existing.request_hash != digest:
            raise AppError(ErrorCode.PARAM_ERROR, '该请求编号已用于其他打样内容，请重新发起', 409)
        await service.get_visible_or_404(session, user, existing.id)
        return existing, True
    source = await load_source(session, user, quote_version_id=payload.quote_version_id, inquiry_id=payload.inquiry_id)
    available = {row['source_item_id']: row for row in source['items']}
    if any(row.source_item_id not in available for row in payload.items):
        raise AppError(ErrorCode.PARAM_ERROR, '所选明细不属于该来源版本', 422)
    sample = SampleRequest(customer_id=source['customer_id'], opportunity_id=source['opportunity_id'],
                           contact_id=source['contact_id'], owner_id=source['owner_id'],
                           status='pending', requested_at=service.now(), created_at=service.now(), remark=payload.remark, source_context=source['source'],
                           request_key=key, request_hash=digest, created_by=user.id)
    session.add(sample); await session.flush()
    for selected in payload.items:
        original = available[selected.source_item_id]
        session.add(SampleItem(sample_request_id=sample.id, sku_id=original['sku_id'],
                    inquiry_id=original['inquiry_id'], inquiry_no_snapshot=original['inquiry_no'],
                    item_name=original['name'], source_snapshot=original,
                    original_quantity=Decimal(original['original_quantity']) if original['original_quantity'] is not None else None, quantity=selected.quantity,
                    specification=selected.specification if selected.specification is not None else original['specification'],
                    remark=selected.remark if selected.remark is not None else original['remark']))
    await session.flush()
    await write_audit(session, operator_id=user.id, action='create', business_type='sample',
                      business_id=sample.id, after={'source': source['source'], 'item_count': len(payload.items)}, ip=ip)
    await record_and_notify(session, customer_id=sample.customer_id, owner_id=sample.owner_id,
        operator_id=user.id, exclude_user_id=user.id, title='打样申请已创建',
        content=f"样品申请 #{sample.id} 来源 {source['source']['no'] or ''} V{source['source']['version']}（{len(payload.items)} 条明细）",
        business_type='sample', business_id=sample.id, sample_id=sample.id,
        opportunity_id=sample.opportunity_id, event_key=f'sample:create:{sample.id}')
    await customer_service.touch_progress(session, sample.customer_id)
    return sample, False
