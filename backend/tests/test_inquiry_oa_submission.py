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
                inquiry_no='INQ-TEST',version=1,customer_id=1,oa_quote_user_id=None)
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
    with pytest.raises(AppError, match="尚未配置同序 OA 询价审批"):
        await routes.start_inquiry_approval(7,routes.StartApproval(quote_owner_user_id='ding-quoter'),request(),user,session)
    client.search_user_id_by_name.assert_not_awaited()
    session.get.assert_not_awaited()
    create.assert_not_awaited()



@pytest.mark.anyio
async def test_no_visible_inquiry_cannot_prepare(setup,monkeypatch):
    inquiry,cfg,session,user,client,create=setup
    monkeypatch.setattr(inquiries,'get_visible_or_404',AsyncMock(side_effect=AppError(40401,'不存在',404)))
    with pytest.raises(AppError):await routes.inquiry_submission_options(7,user=user,session=session)
    create.assert_not_awaited()


@pytest.mark.anyio
async def test_oa_config_switches_options_and_start(setup, monkeypatch):
    inquiry, cfg, session, user, client, create = setup
    monkeypatch.setattr(routes.app_settings, "oa_inquiry_template_id", "")
    monkeypatch.setattr(routes.app_settings, "oa_inquiry_template_code", routes.oa_inquiry.NATIVE_INQUIRY_CODE)
    monkeypatch.setattr(routes.oa_client, "configured", lambda: True)
    monkeypatch.setattr(routes.oa_client, "list_templates", AsyncMock(return_value=[{
        "id":"tpl-oa", "code":routes.oa_inquiry.NATIVE_INQUIRY_CODE, "version":1,
        "applicant":{"id":"owner","name":"同序提报人","department_id":"dep","department_name":"同序部门"},
        "fields":[{"id":"quoter","user_scope":["a","b"]},
                  {"id":"quote_scope","type":"text","label":"原模板报价范围","required":False}],
        "people":[{"id":"a","name":"宋桂香"},{"id":"b","name":"子木"},{"id":"outside","name":"范围外"}]}]))
    inquiry.oa_quote_user_id = 'outside'
    data = (await routes.inquiry_submission_options(7, user=user, session=session))["data"]
    assert data["provider"] == "self_oa" and data["enabled"]
    assert data["quote_owner_required"]
    assert [p["name"] for p in data["quote_owners"]] == ["宋桂香", "子木"]
    assert data["selected_quote_owner"] is None
    inquiry.extra = {"oa_draft": {"quote_owner_user_id": "b"}}
    data = (await routes.inquiry_submission_options(7, user=user, session=session))["data"]
    assert data["selected_quote_owner"] == "b"
    inquiry.extra["oa_draft"]["quote_owner_user_id"] = None
    data = (await routes.inquiry_submission_options(7, user=user, session=session))["data"]
    assert data["selected_quote_owner"] is None
    assert data["fields"] == [{"id":"quote_scope","type":"TextField","label":"原模板报价范围",
                               "required":False,"options":[]}]

    row = Obj(id=1, status="pending", instance_id="oa-1")
    start = AsyncMock(return_value=(row, "已发起 OA 定制询价审批"))
    monkeypatch.setattr(routes.oa_inquiry, "start_inquiry_approval", start)
    result = await routes.start_inquiry_approval(
        7, routes.StartApproval(extra_fields={"quote_scope": "全系列"}), request(), user, session,
    )
    assert result["data"]["status"] == "pending"
    start.assert_awaited_once()
    create.assert_not_awaited()

@pytest.mark.anyio
async def test_non_native_oa_inquiry_does_not_fall_back_to_crm_roster(setup, monkeypatch):
    inquiry, cfg, session, user, client, create = setup
    monkeypatch.setattr(routes.app_settings, "oa_inquiry_template_id", "old-template")
    monkeypatch.setattr(routes.oa_client, "configured", lambda: True)
    read = AsyncMock()
    monkeypatch.setattr(routes.oa_client, "list_templates", read)
    options = (await routes.inquiry_submission_options(7, user=user, session=session))["data"]
    assert options["quote_owners"] == []
    assert not options["enabled"]
    with pytest.raises(AppError, match="同序已发布"):
        await routes.start_inquiry_approval(7, routes.StartApproval(), request(), user, session)
    read.assert_not_awaited()
    create.assert_not_awaited()
