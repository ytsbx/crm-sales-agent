"""客户原稿合同：身份核验、作废撤回、文件真实性、通知去重的回归。无真实钉钉写入。"""
from datetime import UTC, datetime
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
import hashlib

import pytest

from app.core.errors import AppError
from app.modules.contract import customer_flow as flow
from app.modules.contract.schema import DocumentCreate


def instance(**changes):
    data = {"processCode": flow.CONTRACT_PROCESS, "originatorUserId": "ding-actor", "status": "RUNNING",
            "formComponentValues": [
                {"name": "合同编号", "value": "CT-001"}, {"name": "合同名称", "value": "采购合同"},
                {"name": "合同正文", "value": '[{"fileId":"file-a"}]'},
            ]}
    return {**data, **changes}


def check(data):
    flow.verify_instance(data, cfg={"process_code": flow.CONTRACT_PROCESS}, doc=Obj(doc_no="CT-001", title="采购合同"), user_id="ding-actor")


def test_uploaded_contract_does_not_require_a_template():
    p = DocumentCreate(customer_id=1, source_type="customer_drafted", original_file_id=3)
    assert p.template_id is None and p.original_file_id == 3


def test_association_verifies_contract_number_name_actor_and_file():
    check(instance())
    with pytest.raises(AppError):
        check(instance(processCode="PROC-OTHER"))
    with pytest.raises(AppError):
        check(instance(originatorUserId="another-person"))
    for index in range(3):
        data = instance()
        data["formComponentValues"][index]["value"] = ""
        with pytest.raises(AppError):
            check(data)


def test_real_instance_aliases_are_supported():
    data = instance()
    data["originatorUserid"] = data.pop("originatorUserId")
    data["formValueVOS"] = data.pop("formComponentValues")
    check(data)


@pytest.mark.parametrize('data,status', [
    ({"status":"COMPLETED","processInstanceResult":"refuse"}, "rejected"),
    ({"status":"COMPLETED","result":"agree"}, "approved"),
    ({"status":"COMPLETED"}, "needs_review"),
    ({"status":"TERMINATED"}, "withdrawn"),
    ({"status":"RUNNING"}, "pending"),
    ({"status":"SOMETHING_UNKNOWN"}, "needs_review"),
])
def test_unknown_completed_result_is_not_approval(data,status):
    assert flow.status_of(data) == status


@pytest.mark.anyio
async def test_suite_never_reaches_generic_create():
    client = Obj(get_process_schema=AsyncMock(return_value={"schemaContent": '{"items":[{"componentName":"DDBizSuite"}]}'}))
    with pytest.raises(AppError):
        await flow.reject_suite_submission(client, flow.CONTRACT_PROCESS)
    client.get_process_schema.assert_not_called()
    with pytest.raises(AppError):
        await flow.reject_suite_submission(client, "OTHER-SUITE")
    await flow.reject_suite_submission(Obj(get_process_schema=AsyncMock(return_value={"items": [{"componentName": "DDAttachment"}]})), "ORDINARY")


def test_original_bytes_must_exist_and_match_registry(monkeypatch,tmp_path):
    path=tmp_path / 'contract.pdf'
    path.write_bytes(b'%PDF-real-original')
    monkeypatch.setattr(flow, 'absolute_path', lambda _: path)
    row=Obj(object_key='original',size=path.stat().st_size,checksum=hashlib.sha256(path.read_bytes()).hexdigest())
    assert flow.verified_content(row)==path.read_bytes()
    path.write_bytes(b'%PDF-changed-other')
    with pytest.raises(AppError): flow.verified_content(row)
    path.unlink()
    with pytest.raises(AppError): flow.verified_content(row)


def setup_withdraw(monkeypatch, *, read_states=None, status='pending'):
    row=Obj(id=1,status=status,instance_id='INST-1',originator_user_id='ding-actor')
    monkeypatch.setattr(flow.oa,'get_by_business_key',AsyncMock(return_value=row))
    monkeypatch.setattr(flow.settings,'dingtalk_push_off',False)
    client=Obj(get_process_instance=AsyncMock(side_effect=read_states or [instance(), {'status':'TERMINATED'}]), terminate_process_instance=AsyncMock())
    monkeypatch.setattr(flow,'get_client',lambda:client)
    monkeypatch.setattr(flow,'store_snapshot',AsyncMock())
    session=Obj(get=AsyncMock(return_value=Obj(dingtalk_userid='ding-actor')))
    return row,client,session


@pytest.mark.anyio
async def test_void_waits_for_confirmed_external_withdrawal(monkeypatch):
    row, client, session=setup_withdraw(monkeypatch)
    await flow.withdraw_before_void(session,Obj(id=1),Obj(id=2))
    client.terminate_process_instance.assert_awaited_once()
    assert client.get_process_instance.await_count==2
    flow.store_snapshot.assert_awaited_once()


@pytest.mark.anyio
async def test_unknown_or_failed_withdrawal_never_marks_local_void(monkeypatch):
    row,client,session=setup_withdraw(monkeypatch,read_states=[instance(),instance()])
    with pytest.raises(AppError): await flow.withdraw_before_void(session,Obj(id=1),Obj(id=2))
    flow.store_snapshot.assert_not_awaited()
    row,client,session=setup_withdraw(monkeypatch,status='needs_review')
    with pytest.raises(AppError): await flow.withdraw_before_void(session,Obj(id=1),Obj(id=2))
    client.terminate_process_instance.assert_not_awaited()


@pytest.mark.anyio
async def test_cannot_withdraw_other_persons_approval(monkeypatch):
    row,client,session=setup_withdraw(monkeypatch)
    row.originator_user_id='another-person'
    with pytest.raises(AppError): await flow.withdraw_before_void(session,Obj(id=1),Obj(id=2))
    client.terminate_process_instance.assert_not_awaited()


@pytest.mark.anyio
async def test_approval_refresh_notifies_only_once_per_round(monkeypatch):
    monkeypatch.setattr(flow.oa,'_notify_contract_review_outcome',AsyncMock())
    doc=Obj(filled_data={})
    row=Obj(id=10,instance_id='INST-A')
    data={'status':'COMPLETED','result':'agree','tasks':[{'status':'COMPLETED'}]}
    await flow.store_snapshot(None,doc,row,data)
    await flow.store_snapshot(None,doc,row,data)
    flow.oa._notify_contract_review_outcome.assert_awaited_once()
    assert doc.filled_data['_dingtalk_oa']['result']['payload']['tasks']
    row.id=11
    await flow.store_snapshot(None,doc,row,data)
    assert flow.oa._notify_contract_review_outcome.await_count==2


@pytest.fixture
def sql_session(db_session):
    from conftest import SyncSessionAsAsync
    from app.core.base import Base
    from app.modules.contract.model import ContractDocument, ContractTemplate
    from app.modules.file.model import FileRecord, BusinessFile
    from app.modules.dingtalk.model import OaInstance
    from app.modules.task.model import Task
    from app.modules.settings.model import SystemSetting
    Base.metadata.create_all(db_session.get_bind(), tables=[m.__table__ for m in (ContractTemplate, ContractDocument, FileRecord, BusinessFile, OaInstance, Task, SystemSetting)])
    return SyncSessionAsAsync(db_session)


@pytest.mark.anyio
async def test_original_contract_has_no_generated_template_or_content(sql_session,monkeypatch):
    from app.modules.customer.model import Customer
    from app.modules.contract import service
    from app.modules.settings import numbering
    sql_session.add(Customer(id=9,name='客户甲',owner_id=1))
    await sql_session.flush()
    monkeypatch.setattr(numbering,'generate_for',AsyncMock(return_value='CT-TEST'))
    p=DocumentCreate(customer_id=9,source_type='customer_drafted',original_file_id=88,title='原稿合同')
    doc,replayed=await service.generate_document(sql_session,payload=p,user_id=1)
    assert doc.template_id is None and doc.generated_file_id is None
    assert doc.filled_data['_original_file_id']==88
    assert '上传的合同原件' in doc.content_snapshot
    assert not replayed


@pytest.mark.anyio
async def test_customer_source_without_original_is_rejected(sql_session):
    from app.modules.contract import service
    with pytest.raises(AppError):
        await service.generate_document(sql_session,payload=DocumentCreate(customer_id=9,source_type='customer_drafted'),user_id=1)


@pytest.mark.anyio
async def test_overdue_reminder_is_daily_and_stops_after_archive(sql_session,monkeypatch):
    from app.modules.contract.model import ContractDocument
    from app.modules.task.model import Task
    from app.modules.notification import service as notices
    from datetime import timedelta
    doc=ContractDocument(id=19,doc_no='CT-019',doc_type='contract',title='原稿',customer_id=9,template_id=None,content_snapshot='',filled_data={'_source_type':'customer_drafted'},status='draft',created_at=datetime.now(UTC))
    task=Task(id=20,title='回签',owner_id=1,status='pending',priority='high',source='system',task_type='contract_signed_upload',source_key='contract:signed-upload:19',due_at=datetime.now(UTC)-timedelta(days=1))
    sql_session.add(doc); sql_session.add(task); await sql_session.flush()
    notice=AsyncMock();monkeypatch.setattr(notices,'notify',notice)
    assert await flow.remind_countersigns(sql_session)==1
    assert await flow.remind_countersigns(sql_session)==0
    doc.filled_data={**doc.filled_data,'_return_reminded_day':'previous-day'}
    assert await flow.remind_countersigns(sql_session)==1
    doc.status='signed'
    assert await flow.remind_countersigns(sql_session)==0
    assert notice.await_count==2


@pytest.mark.anyio
async def test_pending_or_rejected_approval_cannot_archive(sql_session,monkeypatch):
    from app.modules.contract import service
    from app.modules.contract.model import ContractDocument
    monkeypatch.setattr(service,'_ensure_signable_source',AsyncMock())
    doc=ContractDocument(id=30,doc_no='CT-030',doc_type='contract',title='原稿',customer_id=9,content_snapshot='',filled_data={'_original_file_id':88},status='draft',created_at=datetime.now(UTC))
    sql_session.add(doc);await sql_session.flush()
    for state in ('pending','rejected','needs_review'):
        monkeypatch.setattr(flow.oa,'get_by_business_key',AsyncMock(return_value=Obj(status=state)))
        with pytest.raises(AppError):await service.sign_document(sql_session,doc,file_id=99,note=None)
        assert doc.status=='draft'
    monkeypatch.setattr(flow.oa,'get_by_business_key',AsyncMock(return_value=Obj(status='approved')))
    with pytest.raises(AppError):await service.sign_document(sql_session,doc,file_id=88,note=None)


@pytest.mark.anyio
async def test_approved_contract_archive_completes_real_return_task(sql_session,monkeypatch,tmp_path):
    from app.modules.contract import service
    from app.modules.contract.model import ContractDocument
    from app.modules.file.model import FileRecord
    from app.modules.task.model import Task
    monkeypatch.setattr(service,'_ensure_signable_source',AsyncMock())
    monkeypatch.setattr(flow.oa,'get_by_business_key',AsyncMock(return_value=Obj(status='approved')))
    doc=ContractDocument(id=31,doc_no='CT-031',doc_type='contract',title='原稿',customer_id=9,content_snapshot='',filled_data={'_original_file_id':88},status='draft',created_at=datetime.now(UTC))
    path=tmp_path/'signed.pdf';path.write_bytes(b'%PDF-signed')
    monkeypatch.setattr(flow,'absolute_path',lambda _:path)
    signed=FileRecord(id=99,storage_provider='local',object_key='signed',file_name='签回.pdf',size=path.stat().st_size,uploaded_by=1)
    task=Task(id=21,title='回签',owner_id=1,status='pending',source='system',task_type='contract_signed_upload',source_business_type='contract',source_business_id=31,source_key='contract:signed-upload:31')
    for row in (doc,signed,task):sql_session.add(row)
    await sql_session.flush()
    await service.sign_document(sql_session,doc,file_id=99,note=None)
    assert doc.status=='signed'
    assert task.status=='done'


@pytest.mark.parametrize('value',[None,'',[],"[]",'not-an-attachment','[{}]'])
def test_association_rejects_empty_or_malformed_attachments(value):
    data=instance()
    data['formComponentValues'][2]['value']=value
    with pytest.raises(AppError):check(data)


def test_pasted_approval_link_is_parsed_without_executing_it():
    assert flow.LinkInput(instance_id='https://applink.dingtalk.com/approval?procInstId=instance-123').instance_id=='instance-123'
    with pytest.raises(ValueError):flow.LinkInput(instance_id='https://example.com/unknown')


@pytest.mark.anyio
async def test_withdraw_timeout_explains_uncertainty_and_does_not_close_contract(monkeypatch):
    from app.modules.dingtalk.client import DingTalkUnknownOutcome
    row,client,session=setup_withdraw(monkeypatch)
    client.terminate_process_instance.side_effect=DingTalkUnknownOutcome('timeout')
    with pytest.raises(AppError,match='本次未作废合同'):
        await flow.withdraw_before_void(session,Obj(id=1),Obj(id=2))
    flow.store_snapshot.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('payload',[
    DocumentCreate(customer_id=1,template_id=2),
    DocumentCreate(customer_id=1,source_type='customer_drafted'),
    DocumentCreate(customer_id=1,source_type='customer_drafted',original_file_id=5,template_id=2),
])
async def test_contract_entry_accepts_uploads_only(payload):
    from app.modules.contract import router
    with pytest.raises(AppError,match='系统模板生成已停用'):
        await router.generate_document(payload,Obj(),Obj(),Obj())


def test_template_creation_is_no_longer_a_public_action():
    from app.modules.contract.router import router
    assert not any(getattr(route,'path','')=='/contract-templates' and 'POST' in getattr(route,'methods',set()) for route in router.routes)


@pytest.mark.anyio
async def test_upload_requires_explicit_contract_origin():
    from app.modules.contract import router
    p=DocumentCreate(customer_id=1,source_type='customer_drafted',original_file_id=5)
    with pytest.raises(AppError,match='请选择合同来源'):
        await router.generate_document(p,Obj(),Obj(),Obj())


@pytest.mark.anyio
@pytest.mark.parametrize('origin,label',[('company','我方合同'),('customer','客户合同')])
async def test_uploaded_contract_origin_is_frozen_and_displayed(sql_session,monkeypatch,origin,label):
    from app.modules.customer.model import Customer
    from app.modules.contract import service
    from app.modules.settings import numbering
    sql_session.add(Customer(id=9,name='客户甲',owner_id=1))
    await sql_session.flush()
    monkeypatch.setattr(numbering,'generate_for',AsyncMock(return_value='CT-ORIGIN'))
    p=DocumentCreate(customer_id=9,source_type='customer_drafted',original_file_id=88,title='上传合同',contract_origin=origin)
    doc,_=await service.generate_document(sql_session,payload=p,user_id=1)
    result=service.serialize_document(doc)
    assert result['contract_origin']==origin and result['contract_origin_label']==label
    assert doc.template_id is None and doc.generated_file_id is None


def test_unknown_history_is_not_reclassified_as_customer():
    from app.modules.contract import service
    from app.modules.contract.model import ContractDocument
    doc=ContractDocument(id=99,doc_no='CT-OLD',doc_type='contract',title='旧合同',customer_id=1,status='draft',content_snapshot='',filled_data={'_source_type':'customer_drafted'},created_at=datetime.now(UTC))
    result=service.serialize_document(doc)
    assert result['contract_origin'] is None and result['contract_origin_label']=='历史未标注'


def test_origin_is_part_of_idempotency_and_unknown_values_are_rejected():
    from app.modules.contract.service import _request_fingerprint
    company=DocumentCreate(customer_id=1,source_type='customer_drafted',original_file_id=5,contract_origin='company')
    customer=company.model_copy(update={'contract_origin':'customer'})
    assert _request_fingerprint(company)!=_request_fingerprint(customer)
    with pytest.raises(ValueError):DocumentCreate(customer_id=1,contract_origin='unknown')


@pytest.mark.anyio
@pytest.mark.parametrize('title',[None,'','   '])
async def test_upload_requires_contract_name_with_chinese_message(title):
    from app.modules.contract import router
    p=DocumentCreate(customer_id=1,source_type='customer_drafted',original_file_id=5,contract_origin='company',title=title)
    with pytest.raises(AppError,match='请填写合同名称'):
        await router.generate_document(p,Obj(),Obj(),Obj())


def test_upload_schema_does_not_require_template_and_errors_use_chinese():
    from app.core.errors import _field_label
    assert not DocumentCreate.model_fields['template_id'].is_required()
    assert _field_label('original_file_id','DocumentCreate')=='合同原文件'
    assert _field_label('template_id','DocumentCreate')=='合同模板'
    assert _field_label('title','DocumentCreate')=='合同名称'
