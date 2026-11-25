"""错误签名中的非 ASCII 字符应按校验失败处理，不能抛 TypeError。"""
import pytest
from starlette.requests import Request

from app.core.config import settings
from app.core.errors import AppError
from app.core.importing import make_preview_token, read_preview_token
from app.modules.erp.router import _verify_webhook_secret
from app.modules.wecom.callback import WeComCallbackError, compute_signature, verify_signature


@pytest.fixture(autouse=True)
def isolated_keys(monkeypatch):
    monkeypatch.setattr(settings, 'jwt_secret', 'isolated-signature-test-key')
    monkeypatch.setattr(settings, 'erp_webhook_secret', 'isolated-erp-key')
    monkeypatch.setattr(settings, 'wecom_callback_token', 'isolated-wecom-token')


@pytest.mark.parametrize('signature', ['invalid', '签名', 'é', '😀'])
def test_import_snapshot_bad_signature_uses_existing_validation_error(signature):
    token = make_preview_token('product', 'isolated-file-sha', {'2': 'created'})
    malformed = token.split('.', 1)[0] + '.' + signature
    with pytest.raises(AppError) as error:
        read_preview_token(malformed, 'product')
    assert error.value.http_status == 422
    assert read_preview_token(token, 'product') == ('isolated-file-sha', {'2': 'created'}, {})


@pytest.mark.parametrize('signature', ['invalid', '签名', 'é', '😀'])
def test_wecom_bad_signature_uses_existing_validation_error(signature):
    args = {'timestamp': '123', 'nonce': 'isolated-nonce', 'encrypt': 'isolated-payload'}
    with pytest.raises(WeComCallbackError):
        verify_signature(signature=signature, **args)
    valid = compute_signature(token=settings.wecom_callback_token, **args)
    assert verify_signature(signature=valid, **args) is None


@pytest.mark.parametrize('raw_secret', [b'wrong', b'\xff'])
def test_erp_bad_header_uses_existing_forbidden_error(raw_secret):
    def request(value):
        return Request({'type': 'http', 'headers': [(b'x-erp-secret', value)]})
    with pytest.raises(AppError) as error:
        _verify_webhook_secret(request(raw_secret))
    assert error.value.http_status == 403
    assert _verify_webhook_secret(request(b'isolated-erp-key')) is None
