from copy import deepcopy
from datetime import UTC,datetime
from decimal import Decimal as D
from io import BytesIO
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.datastructures import UploadFile
from conftest import make_engine,make_user,SyncSessionAsAsync
from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.user.model import User,Role
from app.modules.customer.model import Customer,Contact
from app.modules.customer.contacts_router import contact_followups
from app.modules.followup.model import FollowUp
from app.modules.sample.model import SampleRequest
from app.modules.lead.model import Lead,LeadAssignment
from app.modules.lead import service as leads,io_router
from app.modules.quote.model import Quote,QuoteVersion,QuoteItem,QuoteCharge
from app.modules.quote import service as quotes,router as quote_router
from app.modules.approval.model import ApprovalInstance,ApprovalRecord
from app.modules.approval import router as approvals
from app.modules.pricing.model import PricePermission
from app.modules.settings.model import SystemSetting
from app.modules.order.schema import ShipmentBatchShip
from app.modules.contract.model import ContractDocument
from app.modules.contract.service import build_pdf_data
NOW=datetime.now(UTC)
REQ=Request({'type':'http','headers':[],'client':('127.0.0.1',1)})
class Nest:
    def __init__(self,tx): self.tx=tx
    def __await__(self):
        async def ready(): return self
        return ready().__await__()
    async def commit(self): self.tx.commit()
    async def rollback(self): self.tx.rollback()
class DB(SyncSessionAsAsync):
    def begin_nested(self): return Nest(self._session.begin_nested())
    def __getattr__(self,name): return getattr(self._session,name)
@pytest.fixture
def case():
    e=make_engine()
    for model in (AuditLog,LeadAssignment,FollowUp,SampleRequest,QuoteItem,QuoteCharge,ApprovalInstance,ApprovalRecord,PricePermission,SystemSetting,Role):
        model.__table__.create(e,checkfirst=True)
    with Session(e,expire_on_commit=False) as raw:
        raw.add_all([User(id=i,username=f'user{i}',name=f'用户{i}',password_hash='isolated',status='inactive' if i==3 else 'active') for i in (1,2,3)])
        raw.add(Customer(id=1,name='客户',owner_id=1));raw.add(Contact(id=1,customer_id=1,name='联系人'))
        raw.add(Quote(id=1,quote_no='REVIEW',customer_id=1,owner_id=1,current_version_id=2))
        raw.add_all([QuoteVersion(id=i,quote_id=1,version_no=i,currency='CNY',created_at=NOW,approval_status='pending',total_amount=D(100)) for i in (1,2)])
        raw.commit();yield raw,DB(raw)
    e.dispose()
def sensitive_summary():
    return {'offending':[{'sku_code':'SKU','quoted_price':10,'minimum_price':100,'profit_rate':-1}],
            'rule_trace':{'rule_name':'成本条件','context':{'gross_margin':-100,'min_item_margin':-100,'total_amount':10},
             'matched_conditions':[{'field':'gross_margin','actual':-100,'actual_label':'-100%','value':20}]}}
@pytest.mark.anyio
async def test_approval_read_exits_and_stored_evidence(case):
    raw,s=case;summary=sensitive_summary();original=deepcopy(summary)
    a=ApprovalInstance(id=1,definition_id=1,business_type='quote_version',business_id=2,applicant_id=1,current_node='manager',status='pending',summary=summary)
    raw.add(a);raw.commit()
    user=make_user(1,permissions=('quote:view',),roles=('finance',),data_scope='all')
    payloads=[(await approvals.get_approval(1,user,s))['data'],
              (await approvals.list_approvals('pending',False,False,1,20,user,s))['data']['items'][0],
              (await quote_router.quote_approval_history(1,user,s))['data'][0]['instance'],
              (await quote_router.get_version(2,user,s))['data']['approval']]
    for payload in payloads:
        safe=payload['summary']
        assert safe['offending'][0]['minimum_price'] is None and safe['offending'][0]['profit_rate'] is None
        assert safe['rule_trace']['context']['gross_margin'] is None and safe['rule_trace']['context']['min_item_margin'] is None
        assert safe['rule_trace']['matched_conditions'][0]['actual'] is None
        assert safe['rule_trace']['matched_conditions'][0]['actual_label']=='无权限查看'
        assert safe['offending'][0]['quoted_price']==10
    privileged=make_user(1,permissions=('quote:view','price:manage'),roles=('finance',),data_scope='all')
    assert (await approvals.get_approval(1,privileged,s))['data']['summary']==original
    raw.refresh(a);assert a.summary==original
@pytest.mark.parametrize('source',['general','custom_manual'])
def test_cost_reason(source):
    item=QuoteItem(quantity=D(1),quoted_price=D(10),price_source=source,cost_snapshot=D(20),approval_required=True,
                   approval_reason='报价 ¥10.00 低于最低允许价 ¥100.00，利润率 -100.00%')
    assert quotes.serialize_item(item,can_see_cost=False)['approval_reason']=='报价超出价格权限，需要审批'
    assert quotes.serialize_item(item,can_see_cost=True)['approval_reason']==item.approval_reason
    item.approval_reason='折扣 20% 超过角色上限 10%'
    assert quotes.serialize_item(item,can_see_cost=False)['approval_reason']==item.approval_reason
@pytest.mark.anyio
@pytest.mark.parametrize('login,error',[('typo','不存在'),('user3','已停用')])
async def test_owner_resolver(case,login,error):
    raw,s=case
    with pytest.raises(AppError) as exc: await leads.resolve_owner(s,login,1)
    assert error in exc.value.message
    assert await leads.resolve_owner(s,' ',1)==1
    assert await leads.resolve_owner(s,' user2 ',1)==2
@pytest.mark.anyio
@pytest.mark.parametrize('login,permission,created,failed',[('user2',False,0,1),('',False,1,0),('user2',True,1,0),('typo',True,0,1),('user3',True,0,1)])
async def test_import_permissions_and_assignment(case,login,permission,created,failed):
    raw,s=case
    user=make_user(1,permissions=('lead:create','lead:assign') if permission else ('lead:create',),roles=('salesperson',),data_scope='self')
    csv=f'线索名称,负责人登录名\n隔离线索,{login}\n'.encode('utf-8-sig')
    result=await io_router.import_leads(REQ,UploadFile(file=BytesIO(csv),filename='owners.csv'),False,None,user,s)
    assert result['data']['created_count']==created and result['data']['failed_count']==failed
    assert raw.query(Lead).count()==created
    if created:
        assert raw.query(Lead).one().owner_id==(2 if login else 1)
        assert raw.query(LeadAssignment).count()==(1 if login else 0)
        if login:assert raw.query(LeadAssignment).one().to_user_id==2
@pytest.mark.anyio
async def test_comparison_mixed_and_repeated_custom(case):
    raw,s=case
    for vid in (1,2):
        for index,(sku,inquiry) in enumerate([(3,None),(None,101),(None,102),(None,102)]):
            raw.add(QuoteItem(quote_version_id=vid,sku_id=sku,inquiry_id=inquiry,sku_name_snapshot=f'行{index}',quantity=D(1),quoted_price=D(10 if vid==1 else 20),cost_snapshot=D(5)))
    raw.commit();result=await quotes.version_comparison(s,1)
    changes=[c for c in result['diffs'][0]['changes'] if c['field']=='quoted_price']
    assert len(changes)==4 and {c['sku_name'] for c in changes}=={'行0','行1','行2','行3'}
    assert [c['sku_id'] for c in changes].count(None)==3
@pytest.mark.anyio
@pytest.mark.parametrize('hour,day',[(23,'2026-10-14'),(0,'2026-10-13'),(16,'2026-10-14')])
async def test_contract_beijing_day(hour,day):
    doc=ContractDocument(doc_no='ISOLATED',doc_type='sales_contract',status='draft',created_at=datetime(2026,10,13,hour,tzinfo=UTC),filled_data={'_header':{'company_name':'公司','customer_name':'客户','quote_no':'Q','order_no':'S'}})
    assert (await build_pdf_data(None,doc))['created_date']==day
@pytest.mark.parametrize('field',['logistics_company','tracking_no'])
def test_shipping_length(field):
    assert getattr(ShipmentBatchShip(**{field:'中'*64}),field)=='中'*64
    with pytest.raises(ValidationError):ShipmentBatchShip(**{field:'中'*65})
    assert getattr(ShipmentBatchShip(**{field:None}),field) is None
@pytest.mark.anyio
async def test_contact_scope_and_collaboration(case):
    raw,s=case
    raw.add_all([FollowUp(contact_id=1,customer_id=1,owner_id=2,followup_type='电话',content='同事正常协作'),FollowUp(contact_id=1,customer_id=1,owner_id=2,quote_id=1,followup_type='系统',content='无报价权限的系统事实')]);raw.commit()
    missing=make_user(1,permissions=('customer:view',),roles=('salesperson',),data_scope='self')
    with pytest.raises(AppError) as exc:await contact_followups(1,1,20,missing,s)
    assert exc.value.http_status==403
    user=make_user(1,permissions=('customer:view','followup:view'),roles=('salesperson',),data_scope='self')
    rows=(await contact_followups(1,1,20,user,s))['data']
    assert rows['total']==1 and rows['items'][0]['content']=='同事正常协作'

@pytest.mark.anyio
async def test_import_bad_owner_row_does_not_break_valid_rows(case):
    raw,s=case
    user=make_user(1,permissions=('lead:create','lead:assign'),roles=('salesperson',),data_scope='self')
    content='线索名称,负责人登录名\n错别字,typo\n正常分配,user2\n默认本人,\n停用,user3\n'.encode('utf-8-sig')
    result=(await io_router.import_leads(REQ,UploadFile(file=BytesIO(content),filename='mixed.csv'),False,None,user,s))['data']
    assert result['created_count']==2 and result['failed_count']==2
    assert {x.name:x.owner_id for x in raw.query(Lead).all()}=={'正常分配':2,'默认本人':1}
    assert '不存在' in str(result['failed']) and '已停用' in str(result['failed'])
    assert raw.query(LeadAssignment).count()==1

@pytest.mark.anyio
async def test_comparison_add_remove_unlinked_and_custom_lines(case):
    raw,s=case
    raw.add(QuoteItem(quote_version_id=1,sku_id=None,inquiry_id=9,sku_name_snapshot='删除定制',quantity=D(1),quoted_price=D(5)))
    raw.add(QuoteItem(quote_version_id=2,sku_id=None,inquiry_id=10,sku_name_snapshot='新增定制',quantity=D(1),quoted_price=D(6)))
    raw.add(QuoteItem(quote_version_id=2,sku_id=None,inquiry_id=None,sku_name_snapshot='无来源历史项',quantity=D(1),quoted_price=D(7)))
    raw.commit();changes=(await quotes.version_comparison(s,1))['diffs'][0]['changes']
    actual={(x['sku_name'],x['field']) for x in changes if x['field'] in ('added','removed')}
    assert actual=={('删除定制','removed'),('新增定制','added'),('无来源历史项','added')}

@pytest.mark.anyio
async def test_changed_sku_not_hidden_by_same_opportunity_source(case):
    raw,s=case
    for vid,sku in [(1,3),(2,4)]:
        raw.add(QuoteItem(quote_version_id=vid,sku_id=sku,opportunity_item_id=99,sku_name_snapshot=f'SKU{sku}',quantity=D(1),quoted_price=D(10)))
    raw.commit();changes=(await quotes.version_comparison(s,1))['diffs'][0]['changes']
    assert {(c['sku_id'],c['field']) for c in changes if c['field'] in ('added','removed')}=={(3,'removed'),(4,'added')}
