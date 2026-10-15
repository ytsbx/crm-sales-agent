"""钉钉开发总闸：底层所有入口及服务重发/同步必须在任何副作用前拦截。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.modules.dingtalk import client as client_module
from app.modules.dingtalk import service
from app.modules.dingtalk.model import OaInstance


METHODS = [
    ('access_token', {}, []),
    ('create_process_instance', {'process_code': 'FAKE', 'form_component_values': [],
                                 'originator_user_id': 'FAKE-USER'}, []),
    ('get_process_instance', {}, ['FAKE-INST']),
    ('get_process_schema', {}, ['FAKE-PROC']),
    ('upload_media', {'content': b'FAKE', 'filename': 'fake.png'}, []),
    ('search_user_id_by_name', {}, ['虚构姓名']),
    ('get_user_dept_ids', {}, ['FAKE-USER']),
]


@pytest.mark.parametrize('method,kwargs,args', METHODS)
@pytest.mark.parametrize('configured', [False, True])
def test_closed_client_never_constructs_http_client(monkeypatch, method, kwargs, args, configured):
    calls = []

    def forbidden_http(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('关闸时不得创建任何 HTTP 客户端')

    monkeypatch.setattr(settings, 'dingtalk_push_off', True)
    monkeypatch.setattr(settings, 'dingtalk_app_key', 'FAKE-KEY' if configured else '')
    monkeypatch.setattr(settings, 'dingtalk_app_secret', 'FAKE-SECRET' if configured else '')
    monkeypatch.setattr(client_module, 'httpx', SimpleNamespace(AsyncClient=forbidden_http))
    client = client_module.DingTalkClient()
    client._token = 'FAKE-CACHED-TOKEN'
    client._token_expires_at = float('inf')
    with pytest.raises(client_module.DingTalkDisabled, match='开发阶段未执行'):
        asyncio.run(getattr(client, method)(*args, **kwargs))
    assert calls == []


class NoDatabaseCalls:
    def __getattr__(self, name):
        raise AssertionError(f'关闸时不得访问数据库：{name}')


def test_closed_resend_preserves_unknown_record(monkeypatch):
    monkeypatch.setattr(settings, 'dingtalk_push_off', True)
    row = OaInstance(status='needs_review', error='上次结果不明', instance_id='FAKE-OLD',
                     last_attempt_at=datetime(2026, 1, 1, tzinfo=UTC),
                     form_snapshot={'formComponentValues': []})
    before = (row.status, row.error, row.instance_id, row.last_attempt_at, row.form_snapshot)
    with pytest.raises(AppError) as caught:
        asyncio.run(service.resolve_reviewed_instance(NoDatabaseCalls(), row, action='resend'))
    assert caught.value.http_status == 403
    assert caught.value.code == ErrorCode.FORBIDDEN
    assert (row.status, row.error, row.instance_id, row.last_attempt_at, row.form_snapshot) == before


def test_closed_sync_explicitly_reports_not_executed(monkeypatch):
    monkeypatch.setattr(settings, 'dingtalk_push_off', True)
    result = asyncio.run(service.sync_pending_instances(NoDatabaseCalls()))
    assert result['checked'] == result['changed'] == 0
    assert result['disabled'] is True
    assert '开发阶段未执行' in result['message']


def test_search_user_id_extracts_userid_from_dingtalk_user_objects(monkeypatch):
    """姓名搜索返回对象时，审批必须拿到 userid，而不是对象的 repr。"""

    class Response:
        status_code = 200
        content = b"{}"
        text = ""

        def json(self):
            return {
                "list": [
                    {"userid": "u-other", "name": "张三（华东）"},
                    {"userid": "u-target", "name": "张三"},
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(settings, 'dingtalk_push_off', False)
    monkeypatch.setattr(settings, 'dingtalk_app_key', 'FAKE-KEY')
    monkeypatch.setattr(settings, 'dingtalk_app_secret', 'FAKE-SECRET')
    monkeypatch.setattr(client_module.httpx, 'AsyncClient', lambda **_kwargs: Client())
    client = client_module.DingTalkClient()
    monkeypatch.setattr(client, 'access_token', lambda: asyncio.sleep(0, result='TOKEN'))

    assert asyncio.run(client.search_user_id_by_name('张三')) == 'u-target'


def test_search_user_id_accepts_wrapped_result_and_string_users(monkeypatch):
    class Response:
        status_code = 200
        content = b"{}"
        text = ""

        def json(self):
            return {"result": {"list": ["u-only"]}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(settings, 'dingtalk_push_off', False)
    monkeypatch.setattr(settings, 'dingtalk_app_key', 'FAKE-KEY')
    monkeypatch.setattr(settings, 'dingtalk_app_secret', 'FAKE-SECRET')
    monkeypatch.setattr(client_module.httpx, 'AsyncClient', lambda **_kwargs: Client())
    client = client_module.DingTalkClient()
    monkeypatch.setattr(client, 'access_token', lambda: asyncio.sleep(0, result='TOKEN'))

    assert asyncio.run(client.search_user_id_by_name('任意')) == 'u-only'
