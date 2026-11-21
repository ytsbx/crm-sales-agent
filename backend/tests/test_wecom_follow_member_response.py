"""成员清单未知响应不能当作明确的空清单。HTTP只走MockTransport。"""
import httpx
import pytest

from app.core.config import settings
from app.modules.wecom import client


@pytest.fixture
def isolated_config(monkeypatch):
    monkeypatch.setattr(settings, 'wecom_corp_id', 'isolated-follow-members')
    monkeypatch.setattr(settings, 'wecom_external_contact_secret', 'isolated-secret')
    monkeypatch.setattr(client, '_token_cache', {})


async def read_members(payload):
    def respond(request):
        if request.url.path.endswith('/gettoken'):
            return httpx.Response(200, json={'access_token':'isolated-token'})
        assert request.url.path.endswith('/externalcontact/get_follow_user_list')
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url='https://mock.invalid') as http:
        return await client.WeComClient(http=http).get_follow_user_ids()


@pytest.mark.anyio
@pytest.mark.parametrize('payload', [
    {}, {'follow_user':None}, {'follow_user':{}}, {'follow_user':'abc'},
    {'follow_user':[1]}, {'follow_user':[None]}, {'follow_user':['']},
    {'follow_user':[' ']}, {'follow_user':[True]}, {'follow_user':[{}]},
])
async def test_malformed_member_list_rejected(isolated_config, payload):
    with pytest.raises(client.WeComResponseError):
        await read_members(payload)


@pytest.mark.anyio
@pytest.mark.parametrize('members', [[], ['member-a'], ['member-a', 'member-b']])
async def test_explicit_empty_and_normal_members_preserved(isolated_config, members):
    assert await read_members({'follow_user':members}) == members
