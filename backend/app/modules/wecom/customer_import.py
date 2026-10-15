"""企微镜像 → 客户中心。先预览后确认；来源关联是重试幂等依据。"""
from collections import Counter, defaultdict
from datetime import UTC, datetime
import hashlib
import json

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import AppError, ErrorCode
from app.modules.contact_util import create_contact_for_customer
from app.modules.customer.model import Contact, Customer
from app.modules.user.model import User
from app.modules.wecom.model import WeComExternalContact, WeComFollowRelationship
from app.modules.wecom.service import finish_job, start_job


def _name(value) -> str:
    return str(value or '').strip()


def _key(value) -> str:
    return _name(value).casefold()


async def _snapshot(session: AsyncSession, *, lock=False):
    query = select(WeComExternalContact).where(
        WeComExternalContact.normalize_status == 'pending',
        WeComExternalContact.crm_contact_id.is_(None),
        WeComExternalContact.crm_customer_id.is_(None),
    ).order_by(WeComExternalContact.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    rows = (await session.execute(query)).scalars().all()
    users_query = select(User).order_by(User.id)
    if lock:
        users_query = users_query.with_for_update().execution_options(populate_existing=True)
    users = (await session.execute(users_query)).scalars().all()
    customers = (await session.execute(select(Customer).order_by(Customer.id))).scalars().all()
    contacts = (await session.execute(select(Contact).where(Contact.external_userid.is_not(None)).order_by(Contact.id))).scalars().all()
    followers = (await session.execute(select(WeComFollowRelationship).where(
        WeComFollowRelationship.external_contact_id.in_([r.id for r in rows]),
        WeComFollowRelationship.status == 'active',
    ).order_by(WeComFollowRelationship.id))).scalars().all() if rows else []
    return rows, users, customers, contacts, followers


def _plan(snapshot, policy):
    if policy != 'hold':
        raise AppError(ErrorCode.PARAM_ERROR, '未匹配负责人策略无效', 422)
    rows, users, customers, contacts, followers = snapshot
    mapped = defaultdict(set)
    for u in users:
        if u.status == 'active' and u.wecom_userid:
            mapped[u.wecom_userid].add(u.id)
    follows = defaultdict(set)
    for r in followers:
        follows[r.external_contact_id].add(r.wecom_userid)
    existing = defaultdict(list)
    for c in customers:
        existing[_key(c.name)].append(c)
    linked = {c.external_userid for c in contacts}  # 包括已删除联系人，不能悄悄重建关联。
    groups = {}
    held = []
    for r in rows:
        raw = r.raw_data if isinstance(r.raw_data, dict) else {}
        full = _name(raw.get('corp_full_name'))
        short = _name(r.corp_name)
        personal = _name(r.name)
        reason = None
        if not personal or len(personal) > 64:
            reason = '联系人姓名为空或超长'
        elif r.external_userid in linked:
            reason = '已有同来源联系人，请核对关联'
        elif not full and short:
            reason = '只有公司简称，需确认公司全称'
        kind = '企业' if full else '个人'
        name = full or personal
        if len(name) > 200:
            reason = '客户名称超长'
        matches = existing.get(_key(name), [])
        target = None
        if not reason and matches:
            if kind == '企业' and len(matches) == 1 and matches[0].deleted_at is None and matches[0].status == 'active' and matches[0].customer_type == '企业':
                target = matches[0]
            else:
                reason = '存在同名或不可用客户，需人工确认'
        if reason:
            held.append((r.id, reason))
            continue
        group_key = ('企业', _key(full)) if full else ('个人', r.external_userid)
        group = groups.setdefault(group_key, {'name': name, 'kind': kind, 'target': target, 'rows': [], 'owners': set(), 'owner_unknown': False})
        owner_ids = set()
        unresolved = not follows[r.id]
        for uid in follows[r.id]:
            if len(mapped[uid]) != 1:
                unresolved = True
            else:
                owner_ids.update(mapped[uid])
        owner = next(iter(owner_ids)) if not unresolved and len(owner_ids) == 1 else None
        group['rows'].append((r, owner))
        group['owners'].update(owner_ids)
        group['owner_unknown'] |= unresolved or owner is None
    ready = []
    for group in groups.values():
        owner = next(iter(group['owners'])) if not group['owner_unknown'] and len(group['owners']) == 1 else None
        group['owner_id'] = owner
        if owner is None:
            held.extend((r.id, '负责人未唯一匹配，需绑定或分配') for r, _ in group['rows'])
        elif group['target'] is not None and group['target'].owner_id != owner:
            held.extend((r.id, '已有客户负责人不同，需人工确认') for r, _ in group['rows'])
        else:
            ready.append(group)
    # 绑定、公司名、负责人、关联及跟进关系变化都会让旧预览失效。
    signature = {
        'policy': policy,
        'rows': [[r.id, r.external_userid, r.name, r.corp_name, r.raw_data, r.last_sync_at.isoformat() if r.last_sync_at else None] for r in rows],
        'users': [[u.id, u.wecom_userid, u.status] for u in users],
        'customers': [[c.id, c.name, c.customer_type, c.status, c.owner_id, bool(c.deleted_at)] for c in customers],
        'contacts': [[c.id, c.external_userid, c.customer_id, bool(c.deleted_at)] for c in contacts],
        'followers': [[f.id, f.external_contact_id, f.wecom_userid, f.status] for f in followers],
    }
    digest = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    reasons = Counter(reason for _, reason in held)
    summary = {
        'digest': digest, 'pending_contacts': len(rows),
        'import_contacts': sum(len(g['rows']) for g in ready),
        'new_company_customers': sum(g['target'] is None and g['kind'] == '企业' for g in ready),
        'new_personal_customers': sum(g['target'] is None and g['kind'] == '个人' for g in ready),
        'existing_customer_contacts': sum(len(g['rows']) for g in ready if g['target'] is not None),
        'new_unassigned_customers': sum(g['target'] is None and g['owner_id'] is None for g in ready),
        'held_contacts': len(held), 'held_reasons': dict(reasons),
    }
    return summary, ready


async def preview_import(session: AsyncSession, *, policy='hold'):
    summary, _ = _plan(await _snapshot(session), policy)
    return summary


async def execute_import(session: AsyncSession, *, digest: str, policy='hold', operator_id=None, source='WEB'):
    # 同批导入串行；单条归一与本入口同样先锁企微行，再锁客户行。
    try:
        dialect = session.get_bind().dialect.name
    except AttributeError:
        dialect = 'sqlite'
    if dialect == 'postgresql':
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('crm:wecom-customer-import'))"))
    summary, groups = _plan(await _snapshot(session, lock=True), policy)
    if summary['digest'] != digest:
        raise AppError(ErrorCode.VERSION_CONFLICT, '客户或负责人数据已变化，请重新预览后导入', 409)
    job = await start_job(session, job_type='customer_import', operator_id=operator_id)
    now = datetime.now(UTC)
    for group in groups:
        customer = group['target']
        created = customer is None
        if created:
            customer = Customer(name=group['name'], customer_type=group['kind'], country='中国',
                source='企业微信', level=None, owner_id=group['owner_id'],
                pool_status='private',
                created_by=operator_id, last_contact_unknown=True,
                remark='企业微信客户导入；原始跟进关系保留在企业微信模块。')
            session.add(customer)
            await session.flush()
        else:
            # 既有客户不改负责人、等级、状态或已有联系人。
            customer = (await session.execute(select(Customer).where(Customer.id == customer.id)
                .with_for_update().execution_options(populate_existing=True))).scalars().one()
            if customer.deleted_at is not None or customer.status != 'active' or customer.owner_id != group['owner_id']:
                raise AppError(ErrorCode.VERSION_CONFLICT, '客户状态已变化，请重新预览', 409)
        for row, owner in group['rows']:
            contact = await create_contact_for_customer(session, customer_id=customer.id,
                name=_name(row.name), owner_id=owner, source='企业微信')
            contact.external_userid = row.external_userid
            row.crm_customer_id = customer.id
            row.crm_contact_id = contact.id
            row.normalize_status = 'created' if created else 'bound'
            row.normalized_by = operator_id
            row.normalized_at = now
    finish_job(job, success=summary['import_contacts'], fail=summary['held_contacts'], detail=summary)
    result = {**summary, 'job_id': job.id}
    await write_audit(session, operator_id=operator_id, source=source,
        action='wecom_import_customers', business_type='integration', business_id=job.id, after=result)
    await session.flush()
    return result
