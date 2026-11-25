"""B10-04：钉钉读接口的非 JSON/异常结构响应转成人话错误，不冒 500。"""

import pytest

from app.modules.dingtalk.client import DingTalkError, _read_json


class _Resp:
    def __init__(self, content: bytes, payload=None, status: int = 502):
        self.content = content
        self._payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def test_invalid_json_is_translated():
    resp = _Resp(b"<html>bad gateway</html>", ValueError("not json"))
    with pytest.raises(DingTalkError) as caught:
        _read_json(resp, api="forms/schemas/processCodes")
    assert caught.value.http_status == 502
    assert "无法解析" in str(caught.value)


def test_non_object_payload_is_translated():
    resp = _Resp(b"[]", [])
    with pytest.raises(DingTalkError) as caught:
        _read_json(resp, api="media/upload")
    assert "响应结构异常" in str(caught.value)


def test_empty_body_is_empty_dict():
    assert _read_json(_Resp(b"", None), api="x") == {}
