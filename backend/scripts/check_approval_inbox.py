"""审批收件箱与转交回归：资格、范围、分页及拒绝转交无副作用。仅允许本机测试库。"""

import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password

BASE = os.getenv('API_BASE', 'http://127.0.0.1:8000/api/v1').rstrip('/')
PREFIX = f'CHKAIN{uuid4().hex[:8]}'
USERS = {}
CASES = {}


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f'PASS {label}')


def call(method, path, token, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


async def setup():
    import app.main  # noqa: F401
    _ = app.main
    from app.modules.approval.model import ApprovalDefinition, ApprovalInstance
    from app.modules.customer.model import Customer
    from app.modules.pricing.model import PricePermission
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.user.model import Department, Permission, Role, User, role_permissions, user_roles

    async with SessionLocal() as session:
        departments = {key: Department(name=f'{PREFIX}{key}') for key in ('A', 'B')}
        roles = {key: Role(code=f'{PREFIX}{key}', name=f'{PREFIX}{key}', data_scope=scope)
                 for key, scope in (('mgr', 'department'), ('viewer', 'department'),
                                    ('finance', 'all'), ('off', 'department'),
                                    ('wrongrole', 'department'), ('noview', 'department'))}
        session.add_all([*departments.values(), *roles.values()])
        await session.flush()
        permissions = dict((await session.execute(
            select(Permission.code, Permission.id)
        )).all())
        for key, role in roles.items():
            codes = ([] if key == 'noview' else ['quote:view'])
            if key in ('mgr', 'off', 'wrongrole', 'noview'):
                codes.append('quote:approve')
            for code in codes:
                await session.execute(role_permissions.insert().values(
                    role_id=role.id, permission_id=permissions[code]))
            session.add(PricePermission(role_id=role.id,
                                       can_approve=key in ('mgr', 'wrongrole', 'noview'), status='active'))
        password = hash_password('123456')
        users = {}
        for key, role_key, dept_key in (
            ('m1', 'mgr', 'A'), ('m2', 'mgr', 'A'), ('m3', 'mgr', 'A'), ('app', 'viewer', 'A'),
            ('outside', 'mgr', 'B'), ('finance', 'finance', 'A'), ('off', 'off', 'A'),
            ('viewer', 'viewer', 'A'), ('wrongrole', 'wrongrole', 'A'),
            ('noview', 'noview', 'A'), ('disabled', 'mgr', 'A'),
        ):
            row = User(name=f'{PREFIX}{key}', username=f'{PREFIX}{key}', password_hash=password,
                       status='disabled' if key == 'disabled' else 'active',
                       department_id=departments[dept_key].id)
            session.add(row)
            await session.flush()
            await session.execute(user_roles.insert().values(user_id=row.id, role_id=roles[role_key].id))
            users[key] = row
            USERS[key] = row.id
        users['admin'] = (await session.execute(select(User).where(User.username == 'admin'))).scalar_one()
        customers = {key: Customer(name=f'{PREFIX}{key}', owner_id=users[key].id,
                                  pool_status='private', status='active') for key in ('app', 'outside')}
        definition = ApprovalDefinition(code=PREFIX, name=PREFIX, business_type='quote_version',
                                        config_json={}, status='active')
        session.add_all([*customers.values(), definition])
        await session.flush()

        async def create_case(key, *, applicant='app', owner='app', status='pending', node='manager', extra=None):
            quote = Quote(quote_no=f'{PREFIX}{key}', customer_id=customers[owner].id,
                          owner_id=users[owner].id, status='pending_approval', created_by=users[applicant].id)
            session.add(quote)
            await session.flush()
            version = QuoteVersion(quote_id=quote.id, version_no=1, currency='CNY',
                                   total_amount=100, approval_status=status, created_at=datetime.now(UTC))
            session.add(version)
            await session.flush()
            instance = ApprovalInstance(
                definition_id=definition.id, business_type='quote_version', business_id=version.id,
                applicant_id=users[applicant].id, status=status, current_node=node,
                summary={'node_role_codes': [roles['mgr'].code], 'quote_no': quote.quote_no, **(extra or {})},
            )
            session.add(instance)
            await session.flush()
            CASES[key] = instance.id
            return instance

        normal = await create_case('normal')
        await create_case('to_m1', extra={'current_assignee_id': USERS['m1']})
        await create_case('to_m2', extra={'current_assignee_id': USERS['m2']})
        await create_case('self_m1', applicant='m1')
        await create_case('self_m2', applicant='m2')
        await create_case('co_sign', node='co_sign', extra={
            'co_sign': {'role_codes': [roles['finance'].code], 'label': '财务会签', 'status': 'pending'},
        })
        await create_case('approved', applicant='m1', status='approved', node=None)
        await create_case('out_of_scope', applicant='m1', owner='outside')
        await create_case('admin_self', applicant='admin', extra={'node_role_codes': ['unavailable_role']})
        # 新记录排在可处理单据前，跨过服务端读取批次，守住“先筛资格、后分页”。
        for _ in range(210):
            session.add(ApprovalInstance(
                definition_id=definition.id, business_type='quote_version', business_id=normal.business_id,
                applicant_id=USERS['app'], status='pending', current_node='manager',
                summary={'node_role_codes': ['unavailable_role']},
            ))
        await session.commit()


async def cleanup():
    q = 'select id from quotes where quote_no like :p'
    v = f'select id from quote_versions where quote_id in ({q})'
    a = f'select id from approval_instances where business_id in ({v}) and business_type=\'quote_version\''
    u = 'select id from users where username like :p'
    r = 'select id from roles where code like :p'
    async with SessionLocal() as session:
        for sql in (
            f"delete from notifications where business_type='quote' and business_id in ({q} union {v})",
            f"delete from audit_logs where (business_type='quote' and business_id in ({q})) "
            f"or (business_type='approval' and business_id in ({a}))",
            f'delete from approval_records where approval_instance_id in ({a})',
            f'delete from approval_instances where id in ({a})',
            f'update quotes set current_version_id=null where id in ({q})',
            f'delete from quote_versions where quote_id in ({q})',
            f'delete from quotes where id in ({q})',
            'delete from customers where name like :p',
            f'delete from user_roles where user_id in ({u})',
            f'delete from users where id in ({u})',
            f'delete from price_permissions where role_id in ({r})',
            f'delete from role_permissions where role_id in ({r})',
            f'delete from roles where id in ({r})',
            'delete from approval_definitions where code like :p',
            'delete from departments where name like :p',
        ):
            await session.execute(text(sql), {'p': f'{PREFIX}%'})
        await session.commit()


def inbox(token, *, page=1, size=200):
    status, result = call('GET', f'/approvals?pending_for_me=true&page={page}&page_size={size}', token)
    check('inbox is available', status == 200)
    return result['data']


async def transfer_snapshot(case):
    """完整比较审批、关联报价、处理记录、通知和审计；失败不应写入任何一项。"""
    from app.core.audit import AuditLog
    from app.modules.approval.model import ApprovalInstance, ApprovalRecord
    from app.modules.notification.model import Notification
    from app.modules.quote.model import Quote, QuoteVersion

    async with SessionLocal() as session:
        instance = await session.get(ApprovalInstance, CASES[case])
        version = await session.get(QuoteVersion, instance.business_id)
        quote = await session.get(Quote, version.quote_id)
        return {
            'instance': (instance.status, instance.current_node, instance.summary, instance.finished_at),
            'version': (version.approval_status, version.approved_at, version.submitted_at),
            'quote': quote.status,
            'records': (await session.execute(select(ApprovalRecord.__table__).where(
                ApprovalRecord.approval_instance_id == instance.id
            ).order_by(ApprovalRecord.id))).all(),
            'notifications': (await session.execute(select(Notification.__table__).order_by(Notification.id))).all(),
            'audits': (await session.execute(select(AuditLog.__table__).order_by(AuditLog.id))).all(),
        }


async def main():
    hosts = {'127.0.0.1', 'localhost', '::1'}
    db = urlparse(settings.database_url)
    if urlparse(BASE).hostname not in hosts or db.hostname not in hosts or (
        'test' not in db.path.lower() and os.getenv('CI', '').lower() != 'true'
    ):
        raise SystemExit('只允许本机 API 和一次性 test 数据库')
    if not settings.dingtalk_push_off or not settings.wecom_push_off or settings.scheduler_enabled:
        raise SystemExit('请关闭钉钉/企微推送和定时调度')
    try:
        await setup()
        tokens = {}
        for key in USERS:
            if key == 'disabled':
                continue
            status, result = call('POST', '/auth/login', '', {'username': f'{PREFIX}{key}', 'password': '123456'})
            check(f'login {key}', status == 200)
            tokens[key] = result['data']['access_token']
        status, result = call('POST', '/auth/login', '', {'username': 'admin', 'password': 'admin123'})
        check('login admin', status == 200)
        tokens['admin'] = result['data']['access_token']
        expected = {'m1': {'normal', 'to_m1', 'self_m2'}, 'm2': {'normal', 'to_m2', 'self_m1'},
                    'finance': {'co_sign'}, 'off': set(), 'app': set()}
        for key, names in expected.items():
            data = inbox(tokens[key])
            check(f'{key}: exact eligible approvals and total',
                  {row['id'] for row in data['items']} == {CASES[name] for name in names}
                  and data['total'] == len(names))
            check(f'{key}: returned approvals expose action eligibility',
                  all(row['can_approve'] for row in data['items']))
        first, second = inbox(tokens['m1'], size=2), inbox(tokens['m1'], page=2, size=2)
        expected_order = sorted([CASES[name] for name in expected['m1']], reverse=True)
        check('pagination happens after eligibility filtering',
              [row['id'] for row in first['items']] == expected_order[:2]
              and [row['id'] for row in second['items']] == expected_order[2:]
              and first['total'] == second['total'] == 3)
        check('out-of-range page is empty but retains filtered total',
              inbox(tokens['m1'], page=3, size=2) == {'items': [], 'total': 3, 'page': 3, 'page_size': 2})
        admin_rows = inbox(tokens['admin'], page=2)['items']
        check('administrator fallback appears even for own submission and restricted node',
              any(row['id'] == CASES['admin_self'] and row['can_approve'] for row in admin_rows))
        status, _ = call('POST', f"/approvals/{CASES['admin_self']}/approve", tokens['admin'])
        check('administrator fallback matches actual operation', status == 200)
        status, result = call('GET', '/approvals?mine=true&status=&page_size=200', tokens['m1'])
        check('submitted list includes completed approvals but respects data scope', status == 200
              and {row['id'] for row in result['data']['items']} == {CASES['self_m1'], CASES['approved']})
        for path, method, body in (
            ('', 'GET', None), ('/records', 'GET', None), ('/approve', 'POST', None),
            ('/reject', 'POST', None), ('/transfer', 'POST', {'to_user_id': USERS['m2']}),
            ('/withdraw', 'POST', {}),
        ):
            status, result = call(method, f"/approvals/{CASES['out_of_scope']}{path}", tokens['m1'], body)
            check(f'cross-scope {path or "detail"} is denied', status == 403 and result['code'] == 40302)
        for user, case in (('m2', 'to_m1'), ('m1', 'self_m1'), ('off', 'normal'), ('finance', 'normal')):
            status, _ = call('POST', f"/approvals/{CASES[case]}/approve", tokens[user])
            check(f'inbox exclusion matches operation denial: {user}/{case}', status == 403)
        for label, case, target, reason in (
            ('no approval permission', 'to_m1', 'viewer', '没有审批'),
            ('price approval disabled', 'to_m1', 'off', '没有审批'),
            ('wrong node role', 'to_m1', 'wrongrole', '角色无权'),
            ('no quote view permission', 'to_m1', 'noview', '报价查看权限'),
            ('outside quote scope', 'to_m1', 'outside', '数据范围'),
            ('applicant cannot self-approve', 'self_m2', 'm2', '不能审批自己'),
            ('inactive recipient', 'to_m1', 'disabled', '已停用'),
            ('transfer to self', 'to_m1', 'm1', '不能转交给自己'),
        ):
            before = await transfer_snapshot(case)
            status, result = call('POST', f"/approvals/{CASES[case]}/transfer", tokens['m1'],
                                  {'to_user_id': USERS[target]})
            check(f'transfer rejected with reason: {label}', status == 422
                  and result['code'] == 40001 and reason in result['message'])
            check(f'rejected transfer has no side effects: {label}',
                  await transfer_snapshot(case) == before)
        status, _ = call('POST', f"/approvals/{CASES['normal']}/transfer", tokens['m1'],
                         {'to_user_id': USERS['m2']})
        check('valid transfer succeeds', status == 200)
        check('transferred approval leaves original inbox',
              CASES['normal'] not in {row['id'] for row in inbox(tokens['m1'])['items']})
        check('transferred approval reaches recipient inbox',
              CASES['normal'] in {row['id'] for row in inbox(tokens['m2'])['items']})
        status, _ = call('POST', f"/approvals/{CASES['normal']}/approve", tokens['m1'])
        check('original handler cannot approve after transfer', status == 403)
        status, _ = call('POST', f"/approvals/{CASES['normal']}/transfer", tokens['m2'],
                         {'to_user_id': USERS['m3']})
        check('recipient can transfer again despite existing assignment', status == 200)
        check('second transfer updates both inboxes',
              CASES['normal'] not in {row['id'] for row in inbox(tokens['m2'])['items']}
              and CASES['normal'] in {row['id'] for row in inbox(tokens['m3'])['items']})
        status, detail = call('GET', f"/approvals/{CASES['normal']}", tokens['m3'])
        check('two successful transfers retain ordered responsibility history', status == 200
              and [(row['from_user_id'], row['to_user_id'])
                   for row in detail['data']['summary']['transfer_history']]
              == [(USERS['m1'], USERS['m2']), (USERS['m2'], USERS['m3'])])
        status, _ = call('POST', f"/approvals/{CASES['normal']}/approve", tokens['m2'])
        check('previous recipient cannot approve after second transfer', status == 403)
        status, _ = call('POST', f"/approvals/{CASES['normal']}/approve", tokens['m3'])
        check('recipient can approve and processed item leaves inbox', status == 200
              and CASES['normal'] not in {row['id'] for row in inbox(tokens['m3'])['items']})
        status, detail = call('GET', f"/approvals/{CASES['co_sign']}", tokens['finance'])
        check('finance co-sign exposes action eligibility without ordinary approve permission',
              status == 200 and detail['data']['can_approve'])
        status, _ = call('POST', f"/approvals/{CASES['co_sign']}/approve", tokens['finance'])
        check('finance can complete co-sign and item leaves inbox', status == 200
              and inbox(tokens['finance'])['total'] == 0)
    finally:
        await cleanup()
    print('Approval inbox regressions passed.')


if __name__ == '__main__':
    asyncio.run(main())
