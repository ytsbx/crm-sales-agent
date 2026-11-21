"""Cutover must preserve old approval routing and source form facts."""
import asyncio
import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.config import settings
from app.core.errors import AppError
from app.modules.oa import client, contract_form


def test_old_instance_uses_old_server_and_secret_but_new_submissions_use_new(monkeypatch):
    for name,value in {'oa_base_url':'http://new-oa.test','oa_integration_secret':'new-secret',
                       'oa_legacy_base_url':'http://old-oa.test','oa_legacy_integration_secret':'old-secret',
                       'oa_legacy_instance_ids':'old-id, second-id'}.items():
        monkeypatch.setattr(settings,name,value)
    requests=[]
    def handler(request):
        requests.append(request)
        secret='old-secret' if request.url.host=='old-oa.test' else 'new-secret'
        timestamp=request.headers['X-CRM-Timestamp']
        canonical='\n'.join([timestamp,request.method,request.url.raw_path.decode(),hashlib.sha256(request.content).hexdigest()])
        assert request.headers['X-CRM-Signature']==hmac.new(secret.encode(),canonical.encode(),hashlib.sha256).hexdigest()
        return httpx.Response(200,json={'status':'pending'})
    real_client=httpx.AsyncClient
    monkeypatch.setattr(client.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    asyncio.run(client.get_approval('old-id'))
    asyncio.run(client.withdraw_approval('second-id','撤回'))
    asyncio.run(client.get_approval('new-id'))
    asyncio.run(client.create_approval({'external_ref':'new-request'}))
    assert [r.url.host for r in requests]==['old-oa.test','old-oa.test','new-oa.test','new-oa.test']


def test_missing_historical_secret_fails_closed(monkeypatch):
    monkeypatch.setattr(settings,'oa_legacy_instance_ids','old-id')
    monkeypatch.setattr(settings,'oa_legacy_integration_secret','')
    with pytest.raises(client.OaNotConfigured):
        asyncio.run(client.get_approval('old-id'))


def test_source_contract_form_maps_business_defaults_and_locks_them():
    template={'code':contract_form.SOURCE_CODE,'fields':[{'id':identity} for identity in
        ['file_category','manual_number','amount_type','contract_amount','our_parties','other_parties','sign_method']]}
    template['fields'][0]['options'] = ['合同 / 销售合同']
    assert 'file_category' not in contract_form.source_defaults(template, {'contract_classify_cascade': '月结协议'})
    defaults=contract_form.source_defaults(template,{'contract_classify_cascade':'销售合同',
        'contract_number':'CT-1','contract_amount_type':'收入','contract_amount':'12.34',
        'contract_mine_info':[{'mine_company_name':'我方'}],
        'contract_opposite_info':[{'opposite_company_name':'对方'}], 'contract_sign_mode':'纸质签署'})
    assert defaults['file_category']=='合同 / 销售合同'
    assert defaults['manual_number']=='CT-1'
    assert defaults['our_parties']==[{'name':'我方','contact':''}]
    assert {'manual_number','amount_type','contract_amount','our_parties','other_parties'} <= contract_form.locked_fields(template)
    assert 'sign_method' not in contract_form.locked_fields(template)


def test_source_contract_cannot_override_locked_business_amount(monkeypatch):
    monkeypatch.setattr(contract_form,'active_template',AsyncMock(return_value={
        'code':contract_form.SOURCE_CODE,'fields':[{'id':'contract_amount','type':'money'}]}))
    with pytest.raises(AppError,match='不可修改'):
        asyncio.run(contract_form.apply_extra_fields(None,None,None,{'fields':{}},{'contract_amount':'0'}))


def test_paused_inquiry_cannot_submit_or_load_an_external_template(monkeypatch):
    from app.modules.oa import inquiry
    from app.modules.dingtalk import router
    monkeypatch.setattr(settings,'oa_inquiry_submission_enabled',False)
    with pytest.raises(AppError,match='暂未启用'):
        inquiry._template_id()
    options=asyncio.run(router._submission_options(None))
    assert options['data']['enabled'] is False
    assert options['data']['provider']=='self_oa'
