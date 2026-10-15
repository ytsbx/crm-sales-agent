"""CRM填写提交→生产部钉钉处理；外部全替身，不发真实审批。"""
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.dingtalk import router as routes
from app.modules.dingtalk import client as client_module
from app.modules.inquiry import service as inquiries
from app.modules.settings import service as settings_service
from app.modules.user.model import User


@pytest.fixture
def setup(monkeypatch):
    inquiry=Obj(id=7,title='需要定制',description='尺寸与材质',quantity=10,target_price=20,
                inquiry_no='INQ-TEST',version=1,customer_id=None,oa_quote_user_id=None)
    cfg={'process_code':'PROC-TEST','field_map':{'title':'title-component'},
         'quote_owner_component':'quote-component','quote_owners':[{'user_id':'ding-quoter','name':'报价员甲'}],
         'originator_component':'originator-component'}
    session=Obj(get=AsyncMock(return_value=Obj(status='active',dingtalk_userid='bound-id')),
                commit=AsyncMock())
    user=Obj(id=1,name='真实姓名（昵称）')
    client=Obj(search_user_id_by_name=AsyncMock(return_value='searched-id'),get_user_dept_ids=AsyncMock(return_value=['dept']),get_process_schema=AsyncMock(return_value={'schemaContent':{'items':[]}}))
    monkeypatch.setattr(inquiries,'get_visible_or_404',AsyncMock(return_value=inquiry))
    monkeypatch.setattr(settings_service,'get_setting',AsyncMock(return_value=cfg))
    monkeypatch.setattr(client_module,'get_client',lambda:client)
    monkeypatch.setattr(routes.app_settings,'dingtalk_push_off',False)
    created=Obj(id=9,status='pending',instance_id='instance-test',submit_round=1,error=None)
    create=AsyncMock(return_value=created)
    monkeypatch.setattr(routes.svc,'create_inquiry_instance',create)
    monkeypatch.setattr(routes.svc,'serialize',lambda r:{'id':r.id,'status':r.status})
    monkeypatch.setattr(routes,'write_audit',AsyncMock())
    return inquiry,cfg,session,user,client,create


def request():
    return Request({'type':'http','headers':[],'client':('127.0.0.1',10)})


@pytest.mark.anyio
async def test_preparation_reads_only_no_external_or_approval_creation(setup):
    inquiry,cfg,session,user,client,create=setup
    result=await routes.inquiry_submission_options(7,user=user,session=session)
    data=result['data']
    assert data['quote_owner_required'] and data['configured'] and data['enabled']
    assert data['quote_owners']==[{'user_id':'ding-quoter','name':'报价员甲'}]
    assert 'process_code' not in data and 'field_map' not in data
    create.assert_not_awaited();client.search_user_id_by_name.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_submission_uses_bound_identity_and_selected_configured_quoter(setup):
    inquiry,cfg,session,user,client,create=setup
    result=await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter'),request(),user,session)
    assert result['data']['status']=='pending'
    client.search_user_id_by_name.assert_not_awaited()
    session.get.assert_awaited_once_with(User,1)
    values=create.await_args.kwargs
    assert values['originator_user_id']=='bound-id'
    assert values['field_map']['originator-component']=='bound-id'
    assert values['field_map']['quote-component']=='ding-quoter'
    assert values['field_map']['title-component']=='需要定制'
    assert values['inquiry_id']==7 and values['inquiry_version']==1


@pytest.mark.anyio
async def test_unbound_legacy_account_keeps_existing_lookup(setup):
    inquiry,cfg,session,user,client,create=setup
    session.get.return_value=Obj(status='active',dingtalk_userid=None)
    await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter'),request(),user,session)
    client.search_user_id_by_name.assert_awaited_once_with(user.name)
    assert create.await_args.kwargs['originator_user_id']=='searched-id'


@pytest.mark.anyio
@pytest.mark.parametrize('owner',[None,'arbitrary-user'])
async def test_missing_or_forged_quoter_never_submits(setup,owner):
    inquiry,cfg,session,user,client,create=setup
    with pytest.raises(AppError):
        await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id=owner),request(),user,session)
    create.assert_not_awaited()


@pytest.mark.anyio
async def test_closed_gate_does_not_lookup_identity_or_send(setup,monkeypatch):
    inquiry,cfg,session,user,client,create=setup
    monkeypatch.setattr(routes.app_settings,'dingtalk_push_off',True)
    await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter'),request(),user,session)
    client.search_user_id_by_name.assert_not_awaited()
    session.get.assert_not_awaited()
    assert create.await_args.kwargs['originator_user_id']==''


@pytest.mark.anyio
async def test_no_visible_inquiry_cannot_prepare(setup,monkeypatch):
    inquiry,cfg,session,user,client,create=setup
    monkeypatch.setattr(inquiries,'get_visible_or_404',AsyncMock(side_effect=AppError(40401,'不存在',404)))
    with pytest.raises(AppError):await routes.inquiry_submission_options(7,user=user,session=session)
    create.assert_not_awaited()


@pytest.mark.anyio
async def test_supplementary_required_fields_validate_before_external_submit(setup):
    inquiry,cfg,session,user,client,create=setup
    client.get_process_schema.return_value={'schemaContent':{'items':[
        {'componentName':'DDSelectField','props':{'id':'provided','label':'是否提供样品','required':True,'options':['{"value":"是"}','{"value":"否"}']}},
        {'componentName':'DDDateField','props':{'id':'deadline','label':'期望回复时间','required':True}},
        {'componentName':'TextField','props':{'id':'production','label':'打样师傅','required':True}},
    ]}}
    options=await routes.inquiry_submission_options(7,user=user,session=session)
    assert len(options['data']['fields'])==2  # 生产部后续节点不让销售填。
    for fields in ({},{'provided':'乱填','deadline':'2026-10-20 12:00'}, {'provided':'否','deadline':'错误日期'}):
        with pytest.raises(AppError):
            await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter',extra_fields=fields),request(),user,session)
    create.assert_not_awaited()
    await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter',extra_fields={'provided':'否','deadline':'2026-10-20 12:00'}),request(),user,session)
    assert create.await_args.kwargs['field_map']['provided']=='否'


@pytest.mark.anyio
async def test_extra_fields_cannot_impersonate_initiator(setup):
    inquiry,cfg,session,user,client,create=setup
    with pytest.raises(AppError):
        await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter',extra_fields={'originator-component':'other-person'}),request(),user,session)
    create.assert_not_awaited()

@pytest.mark.anyio
async def test_optional_fields_are_offered_and_optional(setup):
    inquiry,cfg,session,user,client,create=setup
    client.get_process_schema.return_value={'schemaContent':{'items':[
        {'componentName':'TextField','props':{'id':'delivery','label':'大货交付时间','required':False}},
        {'componentName':'TextareaField','props':{'id':'note','label':'特别说明','required':False}},
        {'componentName':'DDAttachment','props':{'id':'attachment','label':'附件','required':False}}]}}
    result=await routes.inquiry_submission_options(7,user=user,session=session)
    assert [f['label'] for f in result['data']['fields']]==['大货交付时间','特别说明','附件']
    await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter'),request(),user,session)
    assert 'delivery' not in create.await_args.kwargs['field_map']
    await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter',extra_fields={'delivery':'确认样品后20天','note':'防水包装'}),request(),user,session)
    assert create.await_args.kwargs['field_map']['delivery']=='确认样品后20天'
    assert create.await_args.kwargs['field_map']['note']=='防水包装'

@pytest.mark.anyio
async def test_attachment_scope_and_payload(setup,monkeypatch,tmp_path):
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.modules.file.model import FileRecord,BusinessFile
    from app.core import deps
    from app.modules.dingtalk import attachments
    inquiry,cfg,session,user,client,create=setup
    client.get_process_schema.return_value={'schemaContent':{'items':[
        {'componentName':'DDAttachment','props':{'id':'DDAttachment_test','label':'附件','required':False}}]}}
    engine=create_engine('sqlite://')
    FileRecord.__table__.create(engine);BusinessFile.__table__.create(engine)
    with Session(engine) as sync:
        sync.add_all([FileRecord(id=1,file_name='own.pdf',object_key='own.pdf',size=3),FileRecord(id=2,file_name='other.pdf',object_key='other.pdf',size=3),BusinessFile(id=1,business_type='inquiry',business_id=7,file_id=1),BusinessFile(id=2,business_type='inquiry',business_id=8,file_id=2)])
        sync.commit()
        session.execute=AsyncMock(side_effect=lambda stmt:sync.execute(stmt))
        monkeypatch.setattr(deps,'ensure_permission',lambda *_:None)
        monkeypatch.setattr(routes.app_settings,'file_root',str(tmp_path))
        (tmp_path/'own.pdf').write_bytes(b'abc')
        uploaded=AsyncMock(return_value={'spaceId':'123','fileId':'file-own','fileName':'own.pdf','fileSize':3,'fileType':'pdf'})
        monkeypatch.setattr(attachments,'upload_approval_attachment',uploaded)
        with pytest.raises(AppError):await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter',attachment_file_ids=[2]),request(),user,session)
        uploaded.assert_not_awaited();create.assert_not_awaited()
        await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter',attachment_file_ids=[1,1]),request(),user,session)
        uploaded.assert_awaited_once()
        value=create.await_args.kwargs['field_map']['DDAttachment_test']
        assert isinstance(value,str) and json.loads(value)[0]['fileId']=='file-own'
        create.reset_mock()
        with pytest.raises(AppError):await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter',extra_fields={'DDAttachment_test':'forged'}),request(),user,session)
        create.assert_not_awaited()
    engine.dispose()

@pytest.mark.anyio
async def test_missing_department_stops_before_approval(setup):
    inquiry,cfg,session,user,client,create=setup
    cfg['dept_component']='dept-component'
    client.get_user_dept_ids.return_value=[]
    with pytest.raises(AppError,match='提报部门'):
        await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter'),request(),user,session)
    create.assert_not_awaited()
    client.get_user_dept_ids.return_value=[10]
    await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter'),request(),user,session)
    assert create.await_args.kwargs['field_map']['dept-component']==10
