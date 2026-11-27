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
from app.modules.customer.model import Contact, Customer, CustomerFollower
from app.modules.user.model import User
from app.modules.wecom.model import WeComExternalContact, WeComFollowRelationship
from app.modules.wecom.service import finish_job, start_job

#: 导入扣住原因：多人跟进且分别对应不同负责人（待确认列表要优先展示这一类）
HOLD_MULTI_OWNER = '多名跟进员工对应不同负责人'
HOLD_UNMAPPED_FOLLOWER = '有跟进员工没有对应的 CRM 账号'
HOLD_NO_FOLLOWER = '没有跟进员工，无法确定负责人'
HOLD_OWNER_UNRESOLVED = '负责人未唯一匹配，需绑定或分配'


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
    customer_followers = (
        await session.execute(
            select(CustomerFollower).order_by(CustomerFollower.id)
        )
    ).scalars().all()
    return rows, users, customers, contacts, followers, customer_followers


def _plan(snapshot, policy):
    if policy != 'hold':
        raise AppError(ErrorCode.PARAM_ERROR, '未匹配负责人策略无效', 422)
    rows, users, customers, contacts, followers, customer_followers = snapshot
    mapped = defaultdict(set)
    for u in users:
        if u.status == 'active' and u.wecom_userid:
            mapped[u.wecom_userid].add(u.id)
    follows = defaultdict(set)
    follow_add_time = {}
    for r in followers:
        follows[r.external_contact_id].add(r.wecom_userid)
        follow_add_time[(r.external_contact_id, r.wecom_userid)] = getattr(r, 'add_time', None)
    existing = defaultdict(list)
    for c in customers:
        existing[_key(c.name)].append(c)
    linked = {c.external_userid for c in contacts}  # 包括已删除联系人，不能悄悄重建关联。
    groups = {}
    held = []
    # 同公司多联系人一起判定归属；名单上要能告诉用户「这条自己是单人跟进，
    # 是同公司的其它联系人由别人跟进」，否则共用一个原因会看起来自相矛盾。
    peer_map: dict[int, list[str]] = {}
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
        group = groups.setdefault(group_key, {
            'name': name, 'kind': kind, 'target': target, 'rows': [],
            'follower_ids': set(), 'follower_times': {},
            'has_followers': False, 'has_unmapped': False,
        })
        row_followers: set[int] = set()
        row_times: dict[int, datetime | None] = {}
        if follows[r.id]:
            group['has_followers'] = True
        for uid in follows[r.id]:
            if len(mapped[uid]) != 1:
                group['has_unmapped'] = True
            else:
                crm_user_id = next(iter(mapped[uid]))
                row_followers.add(crm_user_id)
                row_times[crm_user_id] = follow_add_time.get((r.id, uid))
        group['follower_ids'].update(row_followers)
        for crm_user_id, add_time in row_times.items():
            old = group['follower_times'].get(crm_user_id)
            if crm_user_id not in group['follower_times'] or (
                add_time is not None and (old is None or add_time < old)
            ):
                group['follower_times'][crm_user_id] = add_time
        group['rows'].append((r, row_followers, row_times))

    def _earliest(group) -> int | None:
        """默认负责人：加得最早的人（add_time），其次 user id 最小。"""
        follower_ids = group['follower_ids']
        if not follower_ids:
            return None
        times = group['follower_times']

        def sort_key(uid: int):
            value = times.get(uid)
            return (value is None, value or datetime.min.replace(tzinfo=UTC), uid)

        return sorted(follower_ids, key=sort_key)[0]

    ready = []
    for group in groups.values():
        follower_ids = group['follower_ids']
        if not follower_ids:
            reason = HOLD_UNMAPPED_FOLLOWER if group['has_followers'] else HOLD_NO_FOLLOWER
            peers = [(_name(rr.name) or '未命名') for rr, _, _ in group['rows']]
            if len(group['rows']) > 1:
                for row_index, (rr, _, _) in enumerate(group['rows']):
                    peer_map[rr.id] = [name for index, name in enumerate(peers) if index != row_index]
            held.extend((r.id, reason) for r, _, _ in group['rows'])
            continue
        target = group['target']
        if target is not None and target.owner_id is not None:
            # 既有私海客户：默认负责人保持不变；如果它不是跟进人，执行时补进去。
            owner_id = target.owner_id
        else:
            owner_id = _earliest(group)
        group['owner_id'] = owner_id
        group['rows'] = [
            (r, next(iter(row_followers)) if len(row_followers) == 1 else owner_id)
            for r, row_followers, _ in group['rows']
        ]
        ready.append(group)
    # 绑定、公司名、负责人、关联及跟进关系变化都会让旧预览失效。
    signature = {
        'policy': policy,
        'rows': [[r.id, r.external_userid, r.name, r.corp_name, r.raw_data, r.last_sync_at.isoformat() if r.last_sync_at else None] for r in rows],
        'users': [[u.id, u.wecom_userid, u.status] for u in users],
        'customers': [[c.id, c.name, c.customer_type, c.status, c.owner_id, bool(c.deleted_at)] for c in customers],
        'contacts': [[c.id, c.external_userid, c.customer_id, bool(c.deleted_at)] for c in contacts],
        'followers': [[f.id, f.external_contact_id, f.wecom_userid, f.status] for f in followers],
        'customer_followers': [[f.customer_id, f.user_id, bool(f.deleted_at)] for f in customer_followers],
    }
    digest = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    reasons = Counter(reason for _, reason in held)
    held_map = dict(held)
    summary = {
        'digest': digest, 'pending_contacts': len(rows),
        'import_contacts': sum(len(g['rows']) for g in ready),
        'new_company_customers': sum(g['target'] is None and g['kind'] == '企业' for g in ready),
        'new_personal_customers': sum(g['target'] is None and g['kind'] == '个人' for g in ready),
        'existing_customer_contacts': sum(len(g['rows']) for g in ready if g['target'] is not None),
        'import_followers': sum(len(g['follower_ids']) for g in ready),
        'new_unassigned_customers': sum(g['target'] is None and g['owner_id'] is None for g in ready),
        'held_contacts': len(held), 'held_reasons': dict(reasons),
    }
    return summary, ready, held_map, peer_map


async def preview_import(session: AsyncSession, *, policy='hold'):
    summary, _, _, _ = _plan(await _snapshot(session), policy)
    return summary


async def import_hold_details(session: AsyncSession) -> tuple[dict[int, str], dict[int, list[str]]]:
    """待确认列表用：与「预览导入」同一套判定，给出每条不能自动导入的原因，
    以及同公司其它联系人的名字（个人客户没有同组）。"""
    summary = _plan(await _snapshot(session), 'hold')
    return summary[2], summary[3]


async def import_hold_reasons(session: AsyncSession) -> dict[int, str]:
    """只取扣住原因（预览/测试用）。"""
    return _plan(await _snapshot(session), 'hold')[2]


async def execute_import(session: AsyncSession, *, digest: str, policy='hold', operator_id=None, source='WEB'):
    # 同批导入串行；单条归一与本入口同样先锁企微行，再锁客户行。
    try:
        dialect = session.get_bind().dialect.name
    except AttributeError:
        dialect = 'sqlite'
    if dialect == 'postgresql':
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('crm:wecom-customer-import'))"))
    summary, groups, _, _ = _plan(await _snapshot(session, lock=True), policy)
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
            # 既有客户：只补跟进人，不改负责人、等级、状态或已有联系人。
            customer = (await session.execute(select(Customer).where(Customer.id == customer.id)
                .with_for_update().execution_options(populate_existing=True))).scalars().one()
            if customer.deleted_at is not None or customer.status != 'active':
                raise AppError(ErrorCode.VERSION_CONFLICT, '客户状态已变化，请重新预览', 409)
        from app.modules.customer.followers import add_followers

        follower_ids = set(group['follower_ids'])
        if customer.owner_id is not None:
            follower_ids.add(customer.owner_id)
        await add_followers(
            session,
            customer.id,
            follower_ids,
            source='wecom_import',
            created_by=operator_id,
        )
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
