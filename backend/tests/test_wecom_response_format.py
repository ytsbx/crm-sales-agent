"""企微响应解析边界：纯HTTP替身，无真实第三方调用。"""
import httpx
import pytest

from app.modules.wecom.client import WeComClient, WeComError


@pytest.mark.anyio
@pytest.mark.parametrize('content', [
    '<html>secret-response</html>', '', 'null', '[]', '42',
    '{"errcode":null}', '{"errcode":"secret-response"}',
    '{"errcode":[]}', '{"errcode":{}}', '{"errcode":0.5}',
])
async def test_invalid_response_has_safe_explicit_error(content):
    def handle(request):
        return httpx.Response(200, content=content)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://mock.invalid') as http:
        with pytest.raises(RuntimeError, match='响应格式异常') as caught:
            await WeComClient(http=http)._call('department/list')
        assert 'secret-response' not in str(caught.value)
        assert not isinstance(caught.value, WeComError)


@pytest.mark.anyio
@pytest.mark.parametrize('code', [0, '0'])
async def test_valid_success_is_unchanged(code):
    payload = {'errcode': code, 'department': []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)), base_url='http://mock.invalid') as http:
        assert await WeComClient(http=http)._call('department/list') == payload


@pytest.mark.anyio
async def test_business_error_is_unchanged():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'errcode': 60020, 'errmsg': 'test denied'})), base_url='http://mock.invalid') as http:
        with pytest.raises(WeComError) as caught:
            await WeComClient(http=http)._call('department/list')
        assert caught.value.errcode == 60020


@pytest.mark.anyio
@pytest.mark.parametrize('payload', [
    {'errcode': 0}, {'external_contact': None}, {'external_contact': {}},
    {'external_contact': []}, {'external_contact': 'invalid'},
    {'external_contact': {'name': 'no identity'}},
    {'external_contact': {'external_userid': 'someone-else', 'name': 'wrong person'}},
])
async def test_invalid_contact_detail_cannot_be_used_as_empty_or_wrong_person(monkeypatch, payload):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'wecom_corp_id', 'isolated-test')
    monkeypatch.setattr(settings, 'wecom_external_contact_secret', 'isolated-secret')
    def handle(request):
        if request.url.path.endswith('/gettoken'):
            return httpx.Response(200, json={'access_token': 'isolated-token'})
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://mock.invalid') as http:
        with pytest.raises(RuntimeError, match='响应格式异常'):
            await WeComClient(http=http).get_external_contact('expected-contact')


@pytest.mark.anyio
async def test_valid_contact_detail_keeps_optional_empty_fields(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'wecom_corp_id', 'isolated-test')
    monkeypatch.setattr(settings, 'wecom_external_contact_secret', 'isolated-secret')
    # 校验对象身份，不把合法的可选字段留空一概拒绝。
    detail = {'external_userid': 'expected-contact', 'name': '', 'corp_name': None}
    def handle(request):
        return httpx.Response(200, json=({'access_token': 'isolated-token'} if request.url.path.endswith('/gettoken') else {'external_contact': detail}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://mock.invalid') as http:
        assert await WeComClient(http=http).get_external_contact('expected-contact') == detail


@pytest.mark.anyio
@pytest.mark.parametrize('method,key', [('list_departments','department'), ('list_department_users','userlist')])
@pytest.mark.parametrize('shape', ['missing', 'null', 'object', 'string', 'scalar', 'bad_item', 'no_identity'])
async def test_directory_lists_require_explicit_objects_and_identity(monkeypatch, method, key, shape):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'wecom_corp_id', 'isolated-directory')
    monkeypatch.setattr(settings, 'wecom_contact_secret', 'isolated-secret')
    values = {'null': None, 'object': {}, 'string': 'bad', 'scalar': 3, 'bad_item': [None], 'no_identity': [{}]}
    payload = {} if shape == 'missing' else {key: values[shape]}
    def handle(request):
        return httpx.Response(200, json=({'access_token':'isolated-token'} if request.url.path.endswith('/gettoken') else payload))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://mock.invalid') as http:
        client = WeComClient(http=http)
        with pytest.raises(RuntimeError, match='响应格式异常'):
            if method == 'list_departments': await client.list_departments()
            else: await client.list_department_users(1)


@pytest.mark.anyio
@pytest.mark.parametrize('method,key,rows', [
    ('list_departments','department',[]),
    ('list_departments','department',[{'id':1,'name':'部门'}]),
    ('list_department_users','userlist',[]),
    ('list_department_users','userlist',[{'userid':'employee-1','name':'成员'}]),
])
async def test_valid_directory_empty_and_populated_lists_unchanged(monkeypatch, method, key, rows):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'wecom_corp_id', 'isolated-directory')
    monkeypatch.setattr(settings, 'wecom_contact_secret', 'isolated-secret')
    def handle(request):
        return httpx.Response(200,json=({'access_token':'isolated-token'} if request.url.path.endswith('/gettoken') else {key:rows}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://mock.invalid') as http:
        client=WeComClient(http=http)
        actual=await client.list_departments() if method=='list_departments' else await client.list_department_users(1)
        assert actual==rows


@pytest.mark.anyio
@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('payload', [{}, {'access_token':None}, {'access_token':''}, {'access_token':'   '}, {'access_token':123}, {'access_token':False}, {'access_token':{}}, {'access_token':[]}])
async def test_invalid_token_is_not_cached_and_retry_recovers(monkeypatch, external, payload):
    from app.core.config import settings
    from app.modules.wecom import client as adapter
    monkeypatch.setattr(settings, 'wecom_corp_id', 'isolated-token-test')
    monkeypatch.setattr(settings, 'wecom_contact_secret', 'isolated-secret')
    monkeypatch.setattr(settings, 'wecom_external_contact_secret', 'isolated-secret')
    monkeypatch.setattr(adapter, '_token_cache', {})
    calls = 0
    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200,json=payload if calls==1 else {'access_token':'valid-test-token'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://mock.invalid') as http:
        client=WeComClient(http=http)
        with pytest.raises(RuntimeError,match='响应格式异常'):
            await client.access_token(external=external)
        assert adapter._token_cache == {}
        assert await client.access_token(external=external)=='valid-test-token'
        assert await client.access_token(external=external)=='valid-test-token'
        assert calls==2  # 有效令牌仍复用缓存，不增添每次取令牌的请求。
