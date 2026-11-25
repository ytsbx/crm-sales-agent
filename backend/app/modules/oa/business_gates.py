"""Formal business actions require the latest approval, never a cached UI status."""
from sqlalchemy import select
from app.core.errors import AppError, ErrorCode
from app.modules.dingtalk.model import OaInstance


async def require_latest_approved(session, *, kind, business_id, version=None, release_stage=None, provider=None):
    query = select(OaInstance).where(OaInstance.oa_type == kind, OaInstance.inquiry_id == business_id)
    if version is not None:
        query = query.where(OaInstance.inquiry_version == version)
    if provider is not None:
        query = query.where(OaInstance.provider == provider)
    row = (await session.execute(query.order_by(OaInstance.submit_round.desc(), OaInstance.id.desc()).limit(1))).scalars().first()
    label = {'inquiry': '定制询价', 'sample': '打样', 'contract': '合同'}[kind]
    if row and release_stage:
        # 实测结论（2026-11-25）：现行同序模板是单节点/两节点流程，不产出
        # business_stages 摘要（无 sample_review 节点、"是否通过+报价方案"字段），
        # OA 侧代码固定。整单 approved 就是该阶段真实走完 → 无阶段摘要时按整单判断。
        stages = (row.result or {}).get('business_stages')
        if not isinstance(stages, dict) or not stages:
            allowed = row.status == 'approved'
        else:
            # 有阶段摘要却缺所需阶段：半截/异常回包，不放行（防呆）。
            milestone = stages.get(release_stage)
            allowed = (row.status in {'pending', 'approved'}
                       and isinstance(milestone, dict) and milestone.get('status') == 'approved')
    else:
        allowed = bool(row and row.status == 'approved')
    if not allowed:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED,
            f'{label}审批尚未通过，不能执行此正式动作。请先在同序 OA 完成审批，并在 CRM 同步最新结果', 422)
    return row


async def require_inquiry_items_approved(session, items):
    from app.core.config import settings
    from app.modules.oa import client
    # Ordinary SKU quotes do not require a custom inquiry approval.
    ids = {item.inquiry_id for item in items if getattr(item, 'inquiry_id', None)}
    if not ids:
        return
    for identity in ids:
        existing = (await session.execute(select(OaInstance.id).where(
            OaInstance.oa_type == 'inquiry', OaInstance.inquiry_id == identity).limit(1))).scalar_one_or_none()
        if existing is not None or (client.configured() and (settings.oa_inquiry_template_id or settings.oa_inquiry_template_code)):
            row = (await session.execute(select(OaInstance).where(
                OaInstance.oa_type == 'inquiry', OaInstance.inquiry_id == identity
            ).order_by(OaInstance.submit_round.desc(), OaInstance.id.desc()).limit(1))).scalars().first()
            stages = (row.result or {}).get('business_stages') if row else None
            if not isinstance(stages, dict) or not stages:
                # 现行模板无阶段摘要：整单通过即可（2026-11-25 实测）。
                approved = bool(row and row.status == 'approved')
            else:
                quotation = stages.get('quotation')
                # 有摘要却缺报价段：异常回包，不放行（防呆）。
                approved = (row.status in {'pending', 'approved'}
                            and isinstance(quotation, dict) and quotation.get('status') == 'approved')
            if not approved:
                raise AppError(ErrorCode.STATUS_NOT_ALLOWED,
                    '询价的核价/报价复核尚未通过，暂不能正式报价。可先保存内部草稿；请在同序完成报价阶段并在 CRM 同步结果，无需等待打样或备货结束', 422)


async def require_sample_approved(session, sample):
    meta = (sample.source_context or {}).get('dingtalk_oa') or {}
    if meta.get('requires_self_oa'):
        return await require_latest_approved(
            session, kind='sample', business_id=sample.id, version=int(sample.version or 1),
            release_stage='sample', provider='self_oa',
        )
    if not meta:
        from app.core.config import settings
        from app.modules.oa import client
        if not (client.configured() and (settings.oa_sample_template_id or settings.oa_sample_template_code)):
            return
        return await require_latest_approved(session, kind='sample', business_id=sample.id, version=int(sample.version or 1), release_stage='sample')
    if meta.get('mode') == 'inquiry':
        row = await session.get(OaInstance, meta.get('source_oa_id')) if meta.get('source_oa_id') else None
        if row is None or row.oa_type != 'inquiry':
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '关联询价审批不存在，请核对原审批', 422)
        return await require_latest_approved(session, kind='inquiry', business_id=row.inquiry_id)
    return await require_latest_approved(session, kind='sample', business_id=sample.id, version=int(sample.version or 1), release_stage='sample')
