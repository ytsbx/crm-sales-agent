"""打样两条路径；外部审批与文件上传全部替身，不写业务库。"""
from datetime import UTC, datetime
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.requests import Request
from app.core.errors import AppError
from app.modules.sample import dingtalk as routes
from app.modules.sample.model import SampleRequest, SampleItem


def request():return Request({'type':'http','headers':[],'client':('127.0.0.1',1)})

@pytest.fixture
def setup(monkeypatch):
    sample=SampleRequest(id=7,customer_id=2,owner_id=1,status='pending',version=1,source_context={},made_at=None)
    account=Obj(status='active',dingtalk_userid='ding-1')
    session=Obj(get=AsyncMock(return_value=account),flush=AsyncMock(),commit=AsyncMock())
    user=Obj(id=1,name='业务员甲',has=lambda _:True)
    client=Obj(get_user_dept_ids=AsyncMock(return_value=[20]))
    monkeypatch.setattr(routes.service,'get_visible_or_404',AsyncMock(return_value=sample))
    monkeypatch.setattr(routes,'get_client',lambda:client)
    monkeypatch.setattr(routes,'inquiry_candidates',AsyncMock(return_value=[{'id':8,'label':'来源审批'}]))
    monkeypatch.setattr(routes,'write_audit',AsyncMock())
    create=AsyncMock(return_value=Obj(status='pending',instance_id='fake-instance'))
    monkeypatch.setattr(routes.oa,'create_inquiry_instance',create)
    monkeypatch.setattr(routes.oa,'get_by_business_key',AsyncMock(return_value=None))
    monkeypatch.setattr(routes.oa,'serialize',lambda row:{'status':row.status,'instance_id':row.instance_id})
    # `save()` / `save_draft()` 要按**控件标签**找「要求完成日期」（那个日期得落进
    # `target_completion_date` 列 —— Excel 的「交样时间」取的就是它）。这里 stub 掉模板读取：
    # 本文件用的是假 session，而读设置会走 `session.execute`（连库）。
    # 返回一个**含该控件**的最小模板 —— 既让"落库"那条路走通，也不碰数据库。
    monkeypatch.setattr(routes,'template',AsyncMock(return_value=(
        {'configured':True,'enabled':True,'fields':[
            {'id':'DDDateField_DUE','label':'要求完成日期','type':'DDDateField',
             'required':True,'system':False,'options':[],'format':'yyyy-MM-dd'},
        ]}, None)))
    return sample,session,user,client,create

@pytest.mark.anyio
async def test_draft_does_not_send(setup):
    sample,session,user,client,create=setup
    await routes.save_draft(7,routes.Draft(mode='standalone',fields={'note':'草稿'}),request(),user,session)
    assert routes.metadata(sample)['fields']=={'note':'草稿'}
    assert not routes.metadata(sample).get('submitted')
    create.assert_not_awaited();client.get_user_dept_ids.assert_not_awaited()

@pytest.mark.anyio
async def test_linked_inquiry_sends_nothing_and_cannot_switch_modes(setup):
    sample,session,user,client,create=setup
    await routes.submit(7,routes.Draft(mode='inquiry',source_oa_id=8),request(),user,session)
    assert routes.metadata(sample)['submitted']
    create.assert_not_awaited();client.get_user_dept_ids.assert_not_awaited()
    # 同一次关联重复点击，仍不发送。
    await routes.submit(7,routes.Draft(mode='inquiry',source_oa_id=8),request(),user,session)
    with pytest.raises(AppError):await routes.submit(7,routes.Draft(mode='standalone'),request(),user,session)
    create.assert_not_awaited()

@pytest.mark.anyio
@pytest.mark.parametrize('source',[None,999])
async def test_bad_source_cannot_link(setup,source):
    sample,session,user,client,create=setup
    with pytest.raises(AppError):await routes.submit(7,routes.Draft(mode='inquiry',source_oa_id=source),request(),user,session)
    create.assert_not_awaited()

@pytest.mark.anyio
async def test_disabled_template_does_not_send(setup,monkeypatch):
    sample,session,user,client,create=setup
    monkeypatch.setattr(routes,'template',AsyncMock(return_value=({'configured':False,'enabled':False,'fields':[]},None)))
    with pytest.raises(AppError):await routes.submit(7,routes.Draft(mode='standalone'),request(),user,session)
    create.assert_not_awaited();client.get_user_dept_ids.assert_not_awaited()

@pytest.mark.anyio
async def test_unknown_outcome_is_not_resent(setup,monkeypatch):
    sample,session,user,client,create=setup
    sample.source_context={'dingtalk_oa':{'mode':'standalone','fields':{},'submitted':True}}
    monkeypatch.setattr(routes.oa,'get_by_business_key',AsyncMock(return_value=Obj(status='needs_review',instance_id=None)))
    await routes.submit(7,routes.Draft(mode='standalone'),request(),user,session)
    create.assert_not_awaited();client.get_user_dept_ids.assert_not_awaited()

@pytest.mark.anyio
async def test_standalone_uses_true_template_identity_and_excel(setup,monkeypatch):
    from app.modules.sample import application_form
    from app.modules.dingtalk import attachments
    sample,session,user,client,create=setup
    labels=[('打样类型','DDSelectField',['销售客户打样']),('打样产品大类','DDSelectField',['高频类产品']),('样品制作单位','DDSelectField',['义乌打样部']),('要求完成日期','DDDateField',[]),('本次打样积分','NumberField',[]),('打样需求说明','TextareaField',[]),('申请人员','InnerContactField',[]),('归属部门','DepartmentField',[]),('样品明细明细表','DDAttachment',[])]
    fields=[{'id':f'id{i}','label':label,'type':kind,'options':options,'required':True,'system':label in routes.SYSTEM_LABELS} for i,(label,kind,options) in enumerate(labels)]
    values=['销售客户打样','高频类产品','义乌打样部','2026-10-20','30','纸质盒打样']
    monkeypatch.setattr(routes,'template',AsyncMock(return_value=({'configured':True,'enabled':True,'fields':fields},'PROC-SAMPLE')))
    session.scalars=AsyncMock(side_effect=[Obj(all=lambda:[SampleItem(id=1,sample_request_id=7,quantity=1,item_name='产品')]),Obj(unique=lambda:Obj(all=lambda:[]))])
    generated=Mock(return_value=b'xlsx-data')
    monkeypatch.setattr(application_form,'render_application_form',generated)
    upload=AsyncMock(return_value={'spaceId':'space','fileId':'file','fileName':'sample.xlsx','fileSize':9,'fileType':'xlsx'})
    monkeypatch.setattr(attachments,'upload_approval_attachment',upload)
    payload=routes.Draft(mode='standalone',fields={**{f'id{i}':v for i,v in enumerate(values)},'_product_name':'参考产品'})
    await routes.submit(7,payload,request(),user,session)
    sent=create.await_args.kwargs
    assert sent['oa_type']=='sample' and sent['process_code']=='PROC-SAMPLE'
    assert sent['originator_user_id']=='ding-1'
    assert sent['field_map']['id6']=='ding-1' and sent['field_map']['id7']==20
    assert 'fileId' in sent['field_map']['id8']
    assert generated.call_args.args[0].product_name=='参考产品'
    assert generated.call_args.args[0].estimated_hours==3
    upload.assert_awaited_once()
    assert routes.metadata(sample)['submitted']

@pytest.mark.parametrize('submitted',[False,True])
def test_local_review_and_mutation_guards(submitted):
    sample=Obj(source_context={'dingtalk_oa':{'mode':'standalone','submitted':submitted}})
    with pytest.raises(AppError):routes.ensure_local_review(sample)
    if submitted:
        with pytest.raises(AppError):routes.ensure_editable(sample)
    else:routes.ensure_editable(sample)
    routes.ensure_editable(Obj(source_context=None))
    routes.ensure_local_review(Obj(source_context=None))

@pytest.mark.anyio
async def test_rejected_allows_new_draft_but_pending_does_not(setup,monkeypatch):
    sample,session,user,client,create=setup
    sample.status='rejected'
    sample.source_context={'dingtalk_oa':{'mode':'standalone','fields':{},'submitted':True}}
    monkeypatch.setattr(routes.oa,'get_by_business_key',AsyncMock(return_value=Obj(status='pending')))
    with pytest.raises(AppError):await routes.save_draft(7,routes.Draft(mode='standalone',resubmit=True),request(),user,session)
    monkeypatch.setattr(routes.oa,'get_by_business_key',AsyncMock(return_value=Obj(status='rejected')))
    await routes.save_draft(7,routes.Draft(mode='standalone',resubmit=True,fields={'note':'改好'}),request(),user,session)
    assert not routes.metadata(sample).get('submitted')
    assert routes.metadata(sample)['resubmit']
    create.assert_not_awaited()

@pytest.mark.anyio
async def test_polling_only_maps_standalone_sample_terminal_result(monkeypatch):
    from app.core.config import settings
    from app.modules.dingtalk import service as service
    from app.modules.dingtalk.model import OaInstance
    monkeypatch.setattr(settings,'dingtalk_push_off',False)
    own=Obj(id=7,version=1,status='pending',deleted_at=None,source_context={'dingtalk_oa':{'mode':'standalone','submitted':True}})
    original=Obj(id=8,version=1,status='pending',deleted_at=None,source_context={'dingtalk_oa':{'mode':'inquiry','submitted':True}})
    rows=[OaInstance(id=1,inquiry_id=7,inquiry_version=1,oa_type='sample',instance_id='A',status='pending'),
          OaInstance(id=2,inquiry_id=8,inquiry_version=1,oa_type='inquiry',instance_id='B',status='pending')]
    async def get(model,pk,**_):return own if pk==7 else original
    session=Obj(execute=AsyncMock(return_value=Obj(scalars=lambda:Obj(all=lambda:rows))),get=get,flush=AsyncMock())
    monkeypatch.setattr(service,'get_client',lambda:Obj(get_process_instance=AsyncMock(return_value={'status':'COMPLETED','result':'agree','tasks':[]})))
    await service.sync_pending_instances(session)
    assert own.status=='approved'
    assert original.status=='pending'  # 原询价整体通过不能当打样通过。
    assert rows[0].result['payload']['result']=='agree'

@pytest.mark.anyio
async def test_same_numeric_id_cannot_mix_inquiry_and_sample_approvals(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.modules.dingtalk.model import OaInstance
    from app.modules.dingtalk import router as base_routes
    from app.modules.inquiry import service as inquiries
    from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer
    _register_sqlite_bigint_as_integer()
    engine=create_engine('sqlite://')
    OaInstance.__table__.create(engine)
    with Session(engine) as raw:
        raw.add_all([OaInstance(id=1,inquiry_id=7,inquiry_version=1,oa_type='inquiry',idempotency_key='inquiry-7',instance_id='inquiry-7',status='pending',created_at=datetime.now(UTC)),
                     OaInstance(id=2,inquiry_id=7,inquiry_version=1,oa_type='sample',idempotency_key='sample-7',instance_id='sample-7',status='pending',created_at=datetime.now(UTC))])
        raw.commit()
        monkeypatch.setattr(inquiries,'get_visible_or_404',AsyncMock(return_value=Obj(id=7)))
        response=await base_routes.list_inquiry_approvals(7,user=Obj(id=1),session=SyncSessionAsAsync(raw))
        assert [r['instance_id'] for r in response['data']]==['inquiry-7']
    engine.dispose()
