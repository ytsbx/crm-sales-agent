"""官方创建契约回归；所有 HTTP 使用 MockTransport，不连接真实钉钉。"""
import asyncio
import json

import httpx
import pytest

from app.core.config import settings
from app.modules.dingtalk import client as api
from app.modules.dingtalk.service import build_form_component_values


def test_schema_labels_and_single_contact_array_preserve_entire_application():
    fields = [
        {'id': 'person', 'label': '申请人员', 'type': 'InnerContactField'},
        {'id': 'department', 'label': '归属部门', 'type': 'DepartmentField'},
        {'id': 'attachment', 'label': '样品明细明细表', 'type': 'DDAttachment'},
        {'id': 'points', 'label': '本次打样积分', 'type': 'NumberField'},
    ]
    attachments = [{'spaceId': 'space', 'fileId': 'file', 'fileName': '原申请表.xlsx', 'fileSize': 10, 'fileType': 'xlsx'}]
    actual = build_form_component_values(
        {'person': '021003563141679555', 'department': 1115706176,
         'attachment': attachments, 'points': 0}, field_definitions=fields,
    )
    by_name = {c['name']: c for c in actual}
    assert len(actual) == 4
    assert json.loads(by_name['申请人员']['value']) == ['021003563141679555']
    assert by_name['归属部门']['value'] == '1115706176'
    assert json.loads(by_name['样品明细明细表']['value']) == attachments
    assert by_name['本次打样积分']['value'] == '0'
    assert all(isinstance(c['value'], str) for c in actual)


@pytest.mark.parametrize('contact', ['u1', '["u1"]', ['u1']])
def test_contact_encoding_is_idempotent(contact):
    result = build_form_component_values({'InnerContactField_A': contact})
    assert json.loads(result[0]['value']) == ['u1']


def test_creation_preserves_error_code_and_vendor_request_id(monkeypatch):
    monkeypatch.setattr(settings, 'dingtalk_push_off', False)
    monkeypatch.setattr(settings, 'dingtalk_app_key', 'fake-key')
    monkeypatch.setattr(settings, 'dingtalk_app_secret', 'fake-secret')
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={
            'code': 'sysErrror', 'message': '创建审批实例系统异常', 'requestid': 'vendor-trace-1',
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(api.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    client = api.DingTalkClient()
    client._token = 'fake-token'
    client._token_expires_at = float('inf')
    with pytest.raises(api.DingTalkError) as caught:
        asyncio.run(client.create_process_instance(
            process_code='PROC-TEST', originator_user_id='u1', dept_id=20,
            form_component_values=[{'name': '说明', 'value': '原内容'}],
        ))
    assert caught.value.code == 'sysErrror'
    assert caught.value.request_id == 'vendor-trace-1'
    assert 'vendor-trace-1' in str(caught.value)
    assert len(calls) == 1
    assert calls[0]['deptId'] == 20
    assert 'originatorDeptId' not in calls[0]
