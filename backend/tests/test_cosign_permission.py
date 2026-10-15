"""会签只认独立权限，角色不再作为资格条件。"""
import pytest
from conftest import make_user
from app.core.errors import AppError
from app.modules.approval.access import approval_denial
from app.modules.approval.model import ApprovalInstance
from app.modules.approval.rules_router import RulePayload, _validate_payload


def instance(**summary):
    return ApprovalInstance(applicant_id=1,current_node='co_sign',summary={'co_sign':{'role_codes':['finance'],'label':'财务会签'},**summary})


@pytest.mark.parametrize('roles',[('finance',),('sales_manager',),('custom_reviewer',),()])
def test_any_role_with_permission_can_cosign(roles):
    user=make_user(2,roles=roles,permissions=('quote:finance_approve',))
    assert approval_denial(user,instance(),price_can_approve=False) is None


@pytest.mark.parametrize('roles',[('finance',),('sales_manager',),('custom_reviewer',)])
def test_role_without_permission_is_denied(roles):
    user=make_user(2,roles=roles,permissions=('quote:approve','price:manage'))
    assert '财务会签权限' in approval_denial(user,instance(),price_can_approve=True)


def test_applicant_and_current_assignee_restrictions_remain():
    user=make_user(1,roles=('finance',),permissions=('quote:finance_approve',))
    assert '不能审批自己' in approval_denial(user,instance(),price_can_approve=False)
    user=make_user(2,roles=('custom',),permissions=('quote:finance_approve',))
    assigned=instance(current_assignee_id=3,current_assignee_name='他人')
    assert '已转交' in approval_denial(user,assigned,price_can_approve=False)
    assert approval_denial(user,assigned,price_can_approve=False,check_assignee=False) is None


def test_admin_existing_exception_preserved():
    user=make_user(1,roles=('admin',),permissions=())
    assert approval_denial(user,instance(current_assignee_id=3),price_can_approve=False) is None


def test_finance_permission_does_not_grant_normal_approval():
    node=instance();node.current_node='manager'
    user=make_user(2,roles=('finance',),permissions=('quote:finance_approve',))
    assert approval_denial(user,node,price_can_approve=False) is not None


def test_new_snapshot_without_role_works():
    node=instance();node.summary={'co_sign':{'permission_code':'quote:finance_approve','label':'财务会签'}}
    user=make_user(2,roles=('unrelated',),permissions=('quote:finance_approve',))
    assert approval_denial(user,node,price_can_approve=False) is None


def test_rules_do_not_require_roles_and_reject_wrong_permission():
    base={'name':'会签','kind':'exception_route','conditions':[{'field':'gross_margin','op':'lte','value':0}]}
    _validate_payload(RulePayload(**base,action={'add_node_permission':'quote:finance_approve'}))
    _validate_payload(RulePayload(**base,action={'add_node_role_codes':['old_custom_role']}))
    with pytest.raises(AppError) as err:
        _validate_payload(RulePayload(**base,action={'add_node_permission':'customer:view'}))
    assert '财务会签权限' in str(err.value)


@pytest.mark.anyio
async def test_notifications_and_transfer_candidates_use_permission_and_scope():
    from datetime import UTC, datetime
    from sqlalchemy.orm import Session
    from conftest import make_engine, SyncSessionAsAsync
    from app.core.base import Base
    from app.modules.approval import router as approval
    from app.modules.approval.model import ApprovalDefinition, ApprovalRecord
    from app.modules.customer.model import Customer
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.pricing.model import PricePermission
    from app.modules.settings.model import SystemSetting
    from app.modules.user.model import User, Role, Permission, user_roles, role_permissions
    engine=make_engine()
    Base.metadata.create_all(engine,tables=[x.__table__ for x in (Role,Permission,PricePermission,SystemSetting,ApprovalDefinition,ApprovalInstance,ApprovalRecord)]+[user_roles,role_permissions])
    with Session(engine,expire_on_commit=False) as raw:
        for uid in range(1,9):
            raw.add(User(id=uid,name=f'人员{uid}',username=f'user{uid}',password_hash='x',status='disabled' if uid==5 else 'active'))
        for rid,code,scope,status in [(1,'finance','all','active'),(2,'reviewer','all','active'),(3,'self_reviewer','self','active'),(4,'off_reviewer','all','disabled'),(5,'noview_reviewer','all','active'),(6,'admin','all','active')]:
            raw.add(Role(id=rid,code=code,name=code,data_scope=scope,status=status))
        raw.add(Permission(id=1,code='quote:view',name='查看报价'))
        raw.add(Permission(id=2,code='quote:finance_approve',name='财务会签'))
        raw.flush()
        for uid,rid in [(1,2),(2,1),(3,2),(4,3),(5,2),(6,4),(7,5),(8,6)]:
            raw.execute(user_roles.insert().values(user_id=uid,role_id=rid))
        for rid in (1,2,3,4): raw.execute(role_permissions.insert().values(role_id=rid,permission_id=1))
        for rid in (2,3,4,5): raw.execute(role_permissions.insert().values(role_id=rid,permission_id=2))
        raw.add(Customer(id=1,name='客户',owner_id=1))
        raw.add(Quote(id=1,quote_no='Q-COSIGN',customer_id=1,owner_id=1,current_version_id=1))
        raw.add(QuoteVersion(id=1,quote_id=1,version_no=1,created_at=datetime.now(UTC),approval_status='pending'))
        raw.add(ApprovalDefinition(id=1,code='quote_low_price',name='低价',business_type='quote_version'))
        node=instance();node.id=1;node.definition_id=1;node.business_type='quote_version';node.business_id=1;node.status='pending'
        raw.add(node);raw.flush()
        session=SyncSessionAsAsync(raw)
        assert await approval._co_sign_recipient_ids(session,node)==[3]
        user=make_user(3,roles=('reviewer',),permissions=('quote:view','quote:finance_approve'),data_scope='all')
        choices=await approval.transfer_candidates(1,user,session)
        assert choices['data']==[{'id':8,'name':'人员8'}]
        user=make_user(2,roles=('finance',),permissions=('quote:view',),data_scope='all')
        with pytest.raises(AppError): await approval.transfer_candidates(1,user,session)
    engine.dispose()
