"""客户原稿合同：身份核验、作废撤回、文件真实性、通知去重的回归。无真实钉钉写入。"""
from datetime import UTC, datetime
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
import hashlib

import pytest

from app.core.errors import AppError
from app.modules.contract import support as flow
from app.modules.contract.schema import DocumentCreate


def test_uploaded_contract_does_not_require_a_template():
    p = DocumentCreate(customer_id=1, source_type="customer_drafted", original_file_id=3)
    assert p.template_id is None and p.original_file_id == 3


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


def setup_withdraw(monkeypatch, *, created_by=1, status='pending', provider='self_oa'):
    row=Obj(id=1,status=status,instance_id='INST-1',provider=provider,created_by=created_by,result={})
    monkeypatch.setattr(flow.oa,'get_by_business_key',AsyncMock(return_value=row))
    monkeypatch.setattr(flow.oa,'lock_oa_business_then_instance',AsyncMock(return_value=row))
    monkeypatch.setattr(flow,'visible_doc',AsyncMock())
    withdraw=AsyncMock()
    monkeypatch.setattr('app.modules.oa.service.withdraw_contract_row',withdraw)
    session=Obj(get=AsyncMock(return_value=Obj(id=1)),commit=AsyncMock())
    return row,withdraw,session


@pytest.mark.anyio
async def test_void_withdraws_self_oa_before_local_void(monkeypatch):
    row,withdraw,session=setup_withdraw(monkeypatch)
    await flow.withdraw_before_void(session,Obj(id=1),Obj(id=2))
    withdraw.assert_awaited_once()


@pytest.mark.anyio
async def test_unknown_withdrawal_never_marks_local_void(monkeypatch):
    from app.core.errors import ErrorCode
    row,withdraw,session=setup_withdraw(monkeypatch)
    withdraw.side_effect=AppError(ErrorCode.EXTERNAL_ERROR,'OA 未确认',502)
    with pytest.raises(AppError,match='本次未作废合同'):
        await flow.withdraw_before_void(session,Obj(id=1),Obj(id=2))


@pytest.mark.anyio
async def test_cannot_withdraw_other_persons_approval(monkeypatch):
    row,withdraw,session=setup_withdraw(monkeypatch,created_by=2)
    with pytest.raises(AppError,match='发起人'):
        await flow.withdraw_before_void(session,Obj(id=1),Obj(id=2))
    withdraw.assert_not_awaited()


@pytest.mark.anyio
async def test_historical_dingtalk_approval_is_not_touched(monkeypatch):
    row,withdraw,session=setup_withdraw(monkeypatch,provider='dingtalk')
    with pytest.raises(AppError,match='历史钉钉审批'):
        await flow.withdraw_before_void(session,Obj(id=1),Obj(id=2))
    withdraw.assert_not_awaited()


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
    # B07-03：无金额的原稿合同也要留下冻结键，后续不能因为订单补了金额就漂。
    assert doc.filled_data['_amount'] is None
    assert doc.filled_data['_currency']=='CNY'
    assert doc.filled_data['_amount_source']=='none'
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
    from app.modules.oa import service as self_oa
    monkeypatch.setattr(self_oa, 'latest_contract_row', AsyncMock(return_value=Obj(status='approved')))
    with pytest.raises(AppError):await service.sign_document(sql_session,doc,file_id=88,note=None)


@pytest.mark.anyio
async def test_contract_upload_and_attach_leave_files_unconfirmed(sql_session, monkeypatch):
    from starlette.datastructures import UploadFile
    from starlette.requests import Request
    from io import BytesIO
    from sqlalchemy import select
    from app.modules.contract import service
    from app.modules.contract.model import ContractDocument
    from app.modules.file import router as files
    from app.modules.file.model import BusinessFile

    doc = ContractDocument(id=31, doc_no="CT-031", doc_type="contract", title="合同",
                           customer_id=9, content_snapshot="", status="draft", created_at=datetime.now(UTC))
    sql_session.add(doc)
    await sql_session.flush()
    monkeypatch.setattr(files, "visible_object", AsyncMock(return_value=True))
    monkeypatch.setattr(files, "can_access_file", AsyncMock(return_value=True))
    monkeypatch.setattr(files.storage, "save_upload", AsyncMock(return_value=("file", 3, None)))
    monkeypatch.setattr(files, "write_audit", AsyncMock())
    request = Request({"type": "http", "headers": []})
    user = Obj(id=1, name="隔离测试用户")
    result = await files.upload_file(
        request, UploadFile(BytesIO(b"pdf"), filename="回签.pdf"),
        "contract", doc.id, "signed", user=user, session=sql_session,
    )
    file_id = result["data"]["id"]
    assert await service.list_signed_files(sql_session, doc.id) == []
    await files.attach_file("contract", doc.id, request, file_id, "signed", user, sql_session)
    links = (await sql_session.execute(
        select(BusinessFile).where(BusinessFile.file_id == file_id)
    )).scalars().all()
    assert all(link.category == "pending_signature" for link in links)
    assert doc.status == "draft"
    # 兼容旧数据：草稿曾被误贴签署标签时，详情仍不能当作正式回签展示。
    links[0].category = "signed"
    await sql_session.flush()
    assert await service.list_signed_files(sql_session, doc.id) == []


@pytest.mark.anyio
async def test_approved_contract_archive_completes_real_return_task(sql_session,monkeypatch,tmp_path):
    from app.modules.contract import service
    from app.modules.contract.model import ContractDocument
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.task.model import Task
    monkeypatch.setattr(service,'_ensure_signable_source',AsyncMock())
    monkeypatch.setattr(flow.oa,'get_by_business_key',AsyncMock(return_value=Obj(status='approved')))
    from app.modules.oa import service as self_oa
    monkeypatch.setattr(self_oa, 'latest_contract_row', AsyncMock(return_value=Obj(status='approved')))
    doc=ContractDocument(id=31,doc_no='CT-031',doc_type='contract',title='原稿',customer_id=9,content_snapshot='',filled_data={'_original_file_id':88},status='draft',created_at=datetime.now(UTC))
    path=tmp_path/'signed.pdf';path.write_bytes(b'%PDF-signed')
    monkeypatch.setattr(flow,'absolute_path',lambda _:path)
    signed=FileRecord(id=99,storage_provider='local',object_key='signed',file_name='签回.pdf',size=path.stat().st_size,uploaded_by=1)
    staged=BusinessFile(business_type="contract", business_id=31, file_id=99, category="pending_signature")
    other = FileRecord(id=100, storage_provider="local", object_key="other", file_name="误标.pdf", size=1, uploaded_by=1)
    historical = BusinessFile(business_type="contract", business_id=31, file_id=100, category="signed")
    task=Task(id=21,title='回签',owner_id=1,status='pending',source='system',task_type='contract_signed_upload',source_business_type='contract',source_business_id=31,source_key='contract:signed-upload:31')
    for row in (doc,signed,task,staged,other,historical):sql_session.add(row)
    await sql_session.flush()
    await service.sign_document(sql_session,doc,file_id=99,note=None)
    assert doc.status=='signed'
    assert task.status=='done'
    assert staged.category == "signed"
    assert [row["file_id"] for row in await service.list_signed_files(sql_session, doc.id)] == [99]



@pytest.mark.anyio
@pytest.mark.parametrize("separate_upload", [False, True])
async def test_reusing_a_signed_contract_file_requires_a_reason(sql_session, monkeypatch, separate_upload):
    from app.core.errors import ErrorCode
    from app.modules.contract import service
    from app.modules.contract.model import ContractDocument
    from app.modules.file.model import BusinessFile, FileRecord

    monkeypatch.setattr(service, "_ensure_signable_source", AsyncMock())
    now = datetime.now(UTC)
    first = ContractDocument(id=1, doc_no="CT-A", doc_type="contract", title="合同甲",
                             customer_id=1, content_snapshot="", status="signed", created_at=now)
    second = ContractDocument(id=2, doc_no="CT-B", doc_type="contract", title="合同乙",
                              customer_id=2, content_snapshot="", status="draft", created_at=now)
    file_ids = [10, 11] if separate_upload else [10]
    for file_id in file_ids:
        sql_session.add(FileRecord(id=file_id, storage_provider="local", object_key=str(file_id),
                                  file_name="合扫.pdf", size=10, checksum="a" * 64, uploaded_by=1))
    sql_session.add(first)
    sql_session.add(second)
    sql_session.add(BusinessFile(business_type="contract", business_id=1, file_id=10, category="signed"))
    await sql_session.flush()

    for reason in (None, "  "):
        with pytest.raises(AppError, match="请核对是否选错") as caught:
            await service.sign_document(sql_session, second, file_id=file_ids[-1], note=None, reuse_reason=reason)
        assert caught.value.code == ErrorCode.CONFIRM_REQUIRED
        assert second.status == "draft"

    await service.sign_document(sql_session, second, file_id=file_ids[-1], note=None,
                                reuse_reason=" 两份合同合扫在一份 PDF ")
    assert second.status == "signed"
    assert second.filled_data["_signed_file_reuse_reason"] == "两份合同合扫在一份 PDF"



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


def test_template_entry_only_accepts_an_uploaded_file():
    """2026-11-25：文档管理加了「合同模板」存档入口。

    但系统模板生成**保持停用**：入口只收"类型/名称/已上传文件"，
    没有正文、没有启用开关，生成合同仍只能上传原文件。
    """
    from app.modules.contract.router import router
    from app.modules.contract.schema import TemplateFileCreate

    posts = [
        route for route in router.routes
        if getattr(route, 'path', '') == '/contract-templates'
        and 'POST' in getattr(route, 'methods', set())
    ]
    assert posts, "合同模板存档入口应当存在"
    assert set(TemplateFileCreate.model_fields) == {'doc_type', 'name', 'file_id'}


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
