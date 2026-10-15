"""审批附件 HTTP 全替身，不产生外部文件或审批。"""
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
import httpx
import pytest
from app.modules.dingtalk import attachments
from app.modules.dingtalk.client import DingTalkError, DingTalkDisabled

@pytest.mark.anyio
@pytest.mark.parametrize('failure',[None,'space','url','put','commit'])
async def test_attachment_protocol(monkeypatch,failure):
    requests=[]
    def handle(req):
        requests.append(req)
        if req.url.path=='/topapi/v2/user/get':return httpx.Response(200,json={'result':{'unionid':'union-test'}})
        if req.url.path.endswith('/spaces/infos/query'):return httpx.Response(200,json={'result':{} if failure=='space' else {'spaceId':123}})
        if req.url.path.endswith('/uploadInfos/query'):
            return httpx.Response(200,json={'uploadKey':'key','headerSignatureInfo':{'resourceUrls':['https://evil.example/file' if failure=='url' else 'https://bucket.oss-cn-hangzhou.aliyuncs.com/file'],'headers':{'x-oss-date':'date'}}})
        if req.method=='PUT':
            assert 'x-acs-dingtalk-access-token' not in req.headers
            assert 'authorization' not in req.headers
            assert req.content==b'abc'
            return httpx.Response(500 if failure=='put' else 200)
        if req.url.path.endswith('/commit'):return httpx.Response(200,json={'dentry':{'id':'file-1','spaceId':'123','name':'drawing.pdf','extension':'pdf','size':999 if failure=='commit' else 3}})
        pytest.fail(f'unexpected {req.url.path}')
    original=httpx.AsyncClient
    monkeypatch.setattr(attachments,'httpx',Obj(AsyncClient=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)))
    client=Obj(_require=lambda:None,access_token=AsyncMock(return_value='fake-token'))
    if failure:
        with pytest.raises(DingTalkError):await attachments.upload_approval_attachment(client,user_id='ding-user',filename='drawing.pdf',content=b'abc')
        if failure in ('space','url','put'):assert not any(r.url.path.endswith('/commit') for r in requests)
    else:
        result=await attachments.upload_approval_attachment(client,user_id='ding-user',filename='drawing.pdf',content=b'abc')
        assert result=={'spaceId':'123','fileId':'file-1','fileName':'drawing.pdf','fileSize':3,'fileType':'pdf'}
        assert len(requests)==5

@pytest.mark.anyio
async def test_attachment_gate_stops_before_token():
    def disabled():raise DingTalkDisabled()
    client=Obj(_require=disabled,access_token=AsyncMock())
    with pytest.raises(DingTalkDisabled):await attachments.upload_approval_attachment(client,user_id='x',filename='x.pdf',content=b'x')
    client.access_token.assert_not_awaited()
