"""会签权限的真实 HTTP 回归，只准隔离库运行。"""
import asyncio
import os
from uuid import uuid4
from datetime import UTC,datetime
import httpx
from sqlalchemy import select,delete
from _test_support import require_isolated_db
require_isolated_db()


async def main():
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.core.audit import AuditLog
    from app.modules.auth.model import LoginSession
    from app.modules.user.model import User,Role,Permission,user_roles,role_permissions
    from app.modules.customer.model import Customer
    from app.modules.quote.model import Quote,QuoteVersion
    from app.modules.approval.model import ApprovalDefinition,ApprovalInstance,ApprovalRecord
    from app.modules.pricing.model import PricePermission
    from app.modules.notification.model import Notification
    prefix='cosign_'+uuid4().hex[:8]
    users={};roles={};case_ids=[];quote_id=None;customer_id=None;definition_id=None;finance_id=None;permission_id=None;removed=False
    try:
        async with SessionLocal() as s:
            permissions=dict((await s.execute(select(Permission.code,Permission.id))).all())
            permission_id=permissions['quote:finance_approve']
            finance_id=(await s.execute(select(Role.id).where(Role.code=='finance'))).scalar_one()
            assert (await s.execute(select(role_permissions).where(role_permissions.c.role_id==finance_id,role_permissions.c.permission_id==permission_id))).first()
            await s.execute(role_permissions.delete().where(role_permissions.c.role_id==finance_id,role_permissions.c.permission_id==permission_id));removed=True
            for name,scope in [('reviewer','all'),('manager','all'),('self','self')]:
                role=Role(code=prefix+'_'+name,name=name,data_scope=scope,status='active');s.add(role);await s.flush();roles[name]=role.id
                codes=['quote:view','quote:approve'] if name=='manager' else ['quote:view','quote:finance_approve']
                for code in codes: await s.execute(role_permissions.insert().values(role_id=role.id,permission_id=permissions[code]))
            s.add(PricePermission(role_id=roles['manager'],can_approve=True,status='active'))
            for name,role_name in [('applicant','reviewer'),('one','reviewer'),('two','reviewer'),('manager','manager'),('outside','self'),('legacy_finance',None)]:
                user=User(name=name,username=prefix+'_'+name,password_hash=hash_password('123456'),status='active');s.add(user);await s.flush();users[name]=user.id
                await s.execute(user_roles.insert().values(user_id=user.id,role_id=roles[role_name] if role_name else finance_id))
            c=Customer(name=prefix,owner_id=users['applicant']);s.add(c);await s.flush();customer_id=c.id
            q=Quote(quote_no=prefix,customer_id=c.id,owner_id=users['applicant'],status='pending');s.add(q);await s.flush();quote_id=q.id
            v=QuoteVersion(quote_id=q.id,version_no=1,created_at=datetime.now(UTC),approval_status='pending',total_amount=100,currency='CNY');s.add(v);await s.flush();q.current_version_id=v.id
            d=ApprovalDefinition(code=prefix,name=prefix,business_type='quote_version');s.add(d);await s.flush();definition_id=d.id
            node=ApprovalInstance(definition_id=d.id,business_type='quote_version',business_id=v.id,applicant_id=users['applicant'],status='pending',current_node='manager',summary={'node_role_codes':[prefix+'_manager'],'co_sign':{'role_codes':['finance'],'label':'财务会签','status':'pending'}})
            s.add(node);await s.flush();case_ids.append(node.id);await s.commit()
        async with httpx.AsyncClient(base_url=os.environ['API_BASE']+'/',timeout=30) as api:
            headers={}
            for name in users:
                res=await api.post('auth/login',json={'username':prefix+'_'+name,'password':'123456'})
                assert res.status_code==200,res.text
                headers[name]={'Authorization':'Bearer '+res.json()['data']['access_token']}
            nid=case_ids[0]
            res=await api.post(f'approvals/{nid}/approve',headers=headers['manager'])
            assert res.status_code==200 and res.json()['data']['current_node']=='co_sign',res.text
            async with SessionLocal() as s:
                notices=(await s.execute(select(Notification.user_id).where(Notification.business_type=='quote',Notification.business_id==quote_id,Notification.user_id.in_(users.values()),Notification.title.like('%待会签%')))).scalars().all()
                assert set(notices)=={users['one'],users['two']},notices
            for name in ('legacy_finance','manager','applicant'):
                res=await api.post(f'approvals/{nid}/approve',headers=headers[name]);assert res.status_code==403,(name,res.text)
            res=await api.get(f'approvals/{nid}',headers=headers['outside']);assert res.status_code==403
            info=await api.get(f'approvals/{nid}',headers=headers['one']);assert info.json()['data']['can_approve'] is True
            inbox=await api.get('approvals',headers=headers['one'],params={'pending_for_me':'true','status':'pending','page_size':200})
            assert nid in [x['id'] for x in inbox.json()['data']['items']]
            choices=await api.get(f'approvals/{nid}/transfer-candidates',headers=headers['one']);assert choices.status_code==200,choices.text
            ids={u['id'] for u in choices.json()['data']}
            assert users['two'] in ids and not ids.intersection({users['outside'],users['legacy_finance'],users['applicant']})
            res=await api.post(f'approvals/{nid}/transfer',headers=headers['one'],json={'to_user_id':users['two']});assert res.status_code==200,res.text
            assert (await api.post(f'approvals/{nid}/approve',headers=headers['one'])).status_code==403
            res=await api.post(f'approvals/{nid}/approve',headers=headers['two']);assert res.status_code==200,res.text
            print('PASS 非财务角色凭权限会签/通知/待办/转交/通过；仅角色或普通审批权均不足；自审和范围仍拦截')
            async with SessionLocal() as s:
                version=await s.get(QuoteVersion,v.id);version.approval_status='pending'
                node=ApprovalInstance(definition_id=definition_id,business_type='quote_version',business_id=v.id,applicant_id=users['applicant'],status='pending',current_node='co_sign',summary={'co_sign':{'role_codes':['old-role'],'status':'pending'}})
                s.add(node);await s.flush();case_ids.append(node.id);await s.commit()
            res=await api.post(f'approvals/{case_ids[-1]}/reject',headers=headers['two'],params={'comment':'财务否决'});assert res.status_code==200,res.text
            async with SessionLocal() as s:
                assert (await s.get(ApprovalInstance,case_ids[-1])).status=='rejected'
                assert (await s.execute(select(ApprovalRecord.comment).where(ApprovalRecord.approval_instance_id==case_ids[-1],ApprovalRecord.action=='reject'))).scalar_one()=='财务否决'
                version=await s.get(QuoteVersion,v.id);version.approval_status='pending'
                node=ApprovalInstance(definition_id=definition_id,business_type='quote_version',business_id=v.id,applicant_id=users['applicant'],status='pending',current_node='co_sign',summary={'co_sign':{'status':'pending'}})
                s.add(node);await s.flush();case_ids.append(node.id)
                await s.execute(role_permissions.delete().where(role_permissions.c.role_id==roles['reviewer'],role_permissions.c.permission_id==permission_id));await s.commit()
            assert (await api.post(f'approvals/{case_ids[-1]}/approve',headers=headers['two'])).status_code==403
            assert (await api.get(f'approvals/{case_ids[-1]}',headers=headers['two'])).json()['data']['can_approve'] is False
            print('PASS 会签一票否决及撤销权限后列表/操作同步失效')
    finally:
        async with SessionLocal() as s:
            if removed:
                from sqlalchemy.dialects.postgresql import insert
                await s.execute(insert(role_permissions).values(role_id=finance_id,permission_id=permission_id).on_conflict_do_nothing())
            uids=list(users.values());rids=list(roles.values())
            if quote_id:
                await s.execute(delete(Notification).where(Notification.business_type=='quote',Notification.business_id==quote_id))
                q=await s.get(Quote,quote_id)
                if q:q.current_version_id=None;await s.flush()
            if case_ids:
                await s.execute(delete(ApprovalRecord).where(ApprovalRecord.approval_instance_id.in_(case_ids)))
                await s.execute(delete(ApprovalInstance).where(ApprovalInstance.id.in_(case_ids)))
            if quote_id:
                await s.execute(delete(QuoteVersion).where(QuoteVersion.quote_id==quote_id));await s.execute(delete(Quote).where(Quote.id==quote_id))
            if customer_id:await s.execute(delete(Customer).where(Customer.id==customer_id))
            if definition_id:await s.execute(delete(ApprovalDefinition).where(ApprovalDefinition.id==definition_id))
            if uids:
                await s.execute(delete(LoginSession).where(LoginSession.user_id.in_(uids)))
                await s.execute(delete(AuditLog).where(AuditLog.operator_id.in_(uids)))
                await s.execute(user_roles.delete().where(user_roles.c.user_id.in_(uids)))
                await s.execute(delete(User).where(User.id.in_(uids)))
            if rids:
                await s.execute(delete(PricePermission).where(PricePermission.role_id.in_(rids)))
                await s.execute(role_permissions.delete().where(role_permissions.c.role_id.in_(rids)))
                await s.execute(delete(Role).where(Role.id.in_(rids)))
            await s.commit()


if __name__=='__main__':asyncio.run(main())
