import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.core.errors import AppError
from app.core.config import settings
from app.modules.oa import contract_form as form


def test_active_template_uses_latest_version(monkeypatch):
    monkeypatch.setattr(form.service, '_template_config', lambda: ('', 'contract'))
    monkeypatch.setattr(form.client, 'list_templates', AsyncMock(return_value=[
        {'id':'old','code':'contract','version':1}, {'id':'latest','code':'contract','version':2}]))
    result = asyncio.run(form.active_template(SimpleNamespace(dingtalk_userid='bound')))
    assert result['id'] == 'latest'


@pytest.mark.parametrize('key', ['contract_amount','applicant_name','contract_files','foreign_field'])
def test_original_contract_facts_cannot_be_overwritten(monkeypatch, key):
    monkeypatch.setattr(form, 'active_template', AsyncMock(return_value={'fields':[{'id':'contract_amount','type':'text'}]}))
    payload={'fields':{},'attachment_ids':[]}
    with pytest.raises(AppError, match='不可修改'):
        asyncio.run(form.apply_extra_fields(None, None, None, payload, {key:'spoofed'}))
    assert payload == {'fields':{},'attachment_ids':[]}


def test_contract_choices_dates_and_person_ids_are_forwarded(monkeypatch):
    fields=[{'id':'contract_sign_mode','type':'choice'}, {'id':'contract_sign_date','type':'date'},
            {'id':'contract_seal_type','type':'multi_choice'}, {'id':'contract_archive','type':'user'}]
    monkeypatch.setattr(form, 'active_template', AsyncMock(return_value={'fields':fields}))
    values={'contract_sign_mode':'纸质签署','contract_sign_date':'2026-11-16','contract_seal_type':['公章'], 'contract_archive':'oa-user'}
    payload={'fields':{},'attachment_ids':[]}
    asyncio.run(form.apply_extra_fields(None,None,None,payload,values))
    assert payload['fields'] == values


@pytest.mark.parametrize('value',[['fake-id'], '1', [True], list(range(31))])
def test_contract_attachments_reject_invalid_ids_before_query(monkeypatch, value):
    monkeypatch.setattr(form, 'active_template', AsyncMock(return_value={'fields':[{'id':'contract_attachments','type':'attachment'}]}))
    with pytest.raises(AppError, match='附件选择无效'):
        asyncio.run(form.apply_extra_fields(None,None,SimpleNamespace(id=1),{'fields':{},'attachment_ids':[]},{'contract_attachments':value}))


def test_attachment_from_another_contract_is_rejected(monkeypatch):
    from sqlalchemy.orm import Session
    from tests.test_oa_sample_inquiry_flow import _engine
    from tests.conftest import SyncSessionAsAsync
    from app.modules.file.model import FileRecord, BusinessFile
    monkeypatch.setattr(form, 'active_template', AsyncMock(return_value={'fields':[{'id':'contract_attachments','type':'attachment'}]}))
    upload = AsyncMock()
    monkeypatch.setattr(form.client, 'upload_file', upload)
    with Session(_engine()) as raw:
        raw.add(FileRecord(id=10, object_key='other.pdf', file_name='other.pdf', size=1))
        raw.add(BusinessFile(file_id=10, business_type='contract', business_id=99))
        raw.commit()
        with pytest.raises(AppError, match='不属于当前合同'):
            asyncio.run(form.apply_extra_fields(SyncSessionAsAsync(raw),None,SimpleNamespace(id=1),{'fields':{},'attachment_ids':[]},{'contract_attachments':[10]}))
    upload.assert_not_awaited()


def test_complete_contract_payload_keeps_identity_and_extra_fields(monkeypatch):
    user = SimpleNamespace(id=1, name='提报同事', department_id=None, dingtalk_userid='bound')
    doc = SimpleNamespace(id=2, customer_id=3, filled_data={}, title='合同名称', doc_no='CT-2',
                          doc_type='contract', content_snapshot='合同条款', effective_date=None, expiry_date=None)
    record = SimpleNamespace(file_name='contract.pdf', mime_type='application/pdf')
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(deleted_at=None)))
    monkeypatch.setattr(form.service, '_template_config', lambda: ('template', ''))
    monkeypatch.setattr(form.service, '_load_contract_file', AsyncMock(return_value=(record,b'%PDF')))
    monkeypatch.setattr(form.service, '_contract_amount', AsyncMock(return_value=None))
    monkeypatch.setattr(form.client, 'upload_file', AsyncMock(return_value={'id':'oa-file'}))
    monkeypatch.setattr(form, 'active_template', AsyncMock(return_value={
        'fields': [{'id':'contract_sign_mode','type':'choice'}, {'id':'contract_sign_note','type':'text'},
                   {'id':'applicant_name','type':'text'}, {'id':'department','type':'text'}],
        'applicant': {'department_name': '同序销售部'},
    }))
    payload = asyncio.run(form.service._build_payload(session,doc,user,'ref',our_company='我方',counterparty_name='对方',
        extra_fields={'contract_sign_mode':'电子签署','contract_sign_note':'核对签署要求'}))
    assert payload['fields']['applicant_name']=='提报同事'
    # 归属部门取同序申请人资料：CRM 这边 department_id 为空也不影响。
    assert payload['fields']['department']=='同序销售部'
    assert payload['fields']['contract_sign_mode']=='电子签署'
    assert payload['fields']['contract_sign_note']=='核对签署要求'
    assert payload['fields']['contract_files']==['oa-file']


def test_saved_upload_fields_are_sent_even_without_extra_argument(monkeypatch):
    """上传时选的文件分类等 `_oa_fields`，发起审批时即使前端没重传也要带上。"""
    user = SimpleNamespace(id=1, name='提报同事', department_id=None, dingtalk_userid='bound')
    doc = SimpleNamespace(id=2, customer_id=3, filled_data={'_oa_fields': {'contract_sign_mode': '电子签署'}},
                          title='合同名称', doc_no='CT-2', doc_type='contract', content_snapshot='条款',
                          effective_date=None, expiry_date=None)
    record = SimpleNamespace(file_name='contract.pdf', mime_type='application/pdf')
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(deleted_at=None)))
    monkeypatch.setattr(form.service, '_template_config', lambda: ('template', ''))
    monkeypatch.setattr(form.service, '_load_contract_file', AsyncMock(return_value=(record, b'%PDF')))
    monkeypatch.setattr(form.service, '_contract_amount', AsyncMock(return_value=None))
    monkeypatch.setattr(form.client, 'upload_file', AsyncMock(return_value={'id': 'oa-file'}))
    monkeypatch.setattr(form, 'active_template', AsyncMock(return_value={'fields': [
        {'id': 'contract_sign_mode', 'type': 'choice'}]}))
    payload = asyncio.run(form.service._build_payload(
        session, doc, user, 'ref', our_company='我方', counterparty_name='对方'))
    assert payload['fields']['contract_sign_mode'] == '电子签署'
