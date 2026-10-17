"""钉钉询价图片必须复用普通附件预览的安全类型判据。"""
import hashlib
import hmac
import time

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.modules.dingtalk.service import first_image_attachment
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.file.router import public_inquiry_image
from tests.conftest import SyncSessionAsAsync


@pytest.fixture
def image_db(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_root", str(tmp_path))
    monkeypatch.setattr(settings, "jwt_secret", "isolated-inquiry-image-test-secret")
    for model in (FileRecord, BusinessFile):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    return db_session, tmp_path


def add_image(image_db, file_id, suffix, mime):
    db, root = image_db
    key = f"proof-{file_id}{suffix}"
    content = (b'<svg xmlns="http://www.w3.org/2000/svg" onload="document.title=123"/>'
               if suffix == ".svg" else b"isolated-image-content")
    (root / key).write_bytes(content)
    db.add(FileRecord(id=file_id, file_name=key, object_key=key, mime_type=mime))
    db.add(BusinessFile(file_id=file_id, business_type="inquiry", business_id=1))
    db.flush()


def signed_args(file_id=1):
    expires = int(time.time()) + 60
    sig = hmac.new(settings.jwt_secret.encode(), f"{file_id}:{expires}".encode(), hashlib.sha256).hexdigest()
    return expires, sig


@pytest.mark.anyio
@pytest.mark.parametrize("suffix,mime", [
    (".svg", "image/svg+xml"),
    (".png", "image/svg+xml; charset=utf-8"),
    (".svg", "image/png"),
    (".html", "image/png"),
])
async def test_public_url_rejects_executable_formats_even_with_valid_signature(image_db, suffix, mime):
    add_image(image_db, 1, suffix, mime)
    expires, sig = signed_args()
    with pytest.raises(AppError) as error:
        await public_inquiry_image(1, expires, sig, SyncSessionAsAsync(image_db[0]))
    assert error.value.http_status == 404


@pytest.mark.anyio
@pytest.mark.parametrize("suffix,mime", [
    (".svg", "image/svg+xml"),
    (".svg", "image/png"),
    (".html", "image/png"),
    (".pdf", None),
])
async def test_dingtalk_selection_skips_unsafe_or_non_image_files(image_db, suffix, mime):
    add_image(image_db, 1, suffix, mime)
    add_image(image_db, 2, ".png", "image/png")
    found = await first_image_attachment(SyncSessionAsAsync(image_db[0]), business_type="inquiry", business_id=1)
    assert found[0] == 2


@pytest.mark.anyio
@pytest.mark.parametrize("suffix,mime,expected", [
    (".png", "image/png", "image/png"),
    (".jpg", "Image/JPEG; charset=utf-8", "image/jpeg"),
    (".png", None, "image/png"),
])
async def test_safe_images_remain_selectable_and_publicly_readable(image_db, suffix, mime, expected):
    add_image(image_db, 1, suffix, mime)
    db = SyncSessionAsAsync(image_db[0])
    assert (await first_image_attachment(db, business_type="inquiry", business_id=1))[0] == 1
    expires, sig = signed_args()
    response = await public_inquiry_image(1, expires, sig, db)
    assert response.media_type == expected
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.anyio
async def test_signature_still_required_for_safe_images(image_db):
    add_image(image_db, 1, ".png", "image/png")
    expires, _ = signed_args()
    with pytest.raises(AppError) as error:
        await public_inquiry_image(1, expires, "invalid", SyncSessionAsAsync(image_db[0]))
    assert error.value.http_status == 403
