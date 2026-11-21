"""客户分页必须给出可识别清单，不能把未知响应当空页。"""
import httpx
import pytest

from app.core.config import settings
from app.modules.wecom import client


@pytest.fixture
def isolated_config(monkeypatch):
    monkeypatch.setattr(settings, 'wecom_corp_id', 'isolated-customer-page')
    monkeypatch.setattr(settings, 'wecom_external_contact_secret', 'isolated-secret')
    monkeypatch.setattr(client, '_token_cache', {})


async def read_page(payload):
    def respond(request):
        if request.url.path.endswith('/gettoken'):
            return httpx.Response(200, json={'access_token':'isolated-token'})
        assert request.url.path.endswith('/externalcontact/list')
        assert request.url.params['userid'] == 'member-a'
        assert request.url.params['cursor'] == 'second'
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url='https://mock.invalid') as http:
        return await client.WeComClient(http=http).list_external_contacts(userid='member-a', cursor='second')


@pytest.mark.anyio
@pytest.mark.parametrize('payload', [
    {}, {'external_userid':None}, {'external_userid':'customer-id'},
    {'external_userid':[None]}, {'external_userid':[' ']}, {'external_userid':[1]},
    {'external_contact_list':{}}, {'external_contact_list':['customer-id']},
    {'external_contact_list':[{}]}, {'external_contact_list':[{'external_contact':None}]},
    {'external_contact_list':[{'external_contact':{'external_userid':''}}]},
])
async def test_malformed_customer_page_rejected(isolated_config, payload):
    with pytest.raises(client.WeComResponseError):
        await read_page(payload)


@pytest.mark.anyio
@pytest.mark.parametrize('payload', [
    {'external_userid':[]}, {'external_contact_list':[]},
    {'external_userid':['a','b'], 'next_cursor':'third'},
    {'external_contact_list':[{'external_contact':{'external_userid':'a'}}]},
    {'external_contact_list':[], 'external_userid':['a']},
])
async def test_both_page_shapes_and_empty_pages_preserved(isolated_config, payload):
    assert await read_page(payload) == payload
