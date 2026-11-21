"""上传补偿在持续取消下有界完成，只删除已确认未登记的本次文件。"""

from types import SimpleNamespace

import anyio
import pytest

from app.core.config import settings
from app.modules.file import service


class CleanupSession:
    def __init__(self, *, registered=False, hang=None, fail_query=False):
        self.registered = registered
        self.hang = hang
        self.fail_query = fail_query
        self.rollbacks = 0

    async def rollback(self):
        self.rollbacks += 1
        if self.hang == "rollback":
            await anyio.sleep_forever()
        await anyio.sleep(0)

    async def execute(self, statement):
        if self.hang == "query":
            await anyio.sleep_forever()
        await anyio.sleep(0)
        if self.fail_query:
            raise RuntimeError("isolated registration lookup failed")
        return SimpleNamespace(first=lambda: (1,) if self.registered else None)


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_root", str(tmp_path))
    original = tmp_path / "unrelated-original.txt"
    original.write_bytes(b"original")
    upload = tmp_path / "this-upload.txt"
    upload.write_bytes(b"upload")
    return original, upload


async def cancel_and_cleanup(session):
    with anyio.CancelScope() as scope:
        scope.cancel()
        try:
            await anyio.sleep(0)
        except anyio.get_cancelled_exc_class():
            await service.discard_unregistered_upload(session, "this-upload.txt")
            raise
    assert scope.cancelled_caught, "原取消必须继续传播"


@pytest.mark.anyio
@pytest.mark.parametrize("registered", [False, True])
async def test_cleanup_survives_persistent_cancellation(files, registered):
    original, upload = files
    session = CleanupSession(registered=registered)
    await cancel_and_cleanup(session)
    assert upload.exists() is registered
    if registered:
        assert upload.read_bytes() == b"upload"
    assert original.read_bytes() == b"original"


@pytest.mark.anyio
@pytest.mark.parametrize("hang", ["rollback", "query"])
async def test_cleanup_timeout_keeps_unverified_file_and_original_cancel(
    files, monkeypatch, caplog, hang,
):
    original, upload = files
    monkeypatch.setattr(service, "UPLOAD_CLEANUP_TIMEOUT_SECONDS", 0.02)
    with anyio.fail_after(1):
        await cancel_and_cleanup(CleanupSession(hang=hang))
    assert upload.read_bytes() == b"upload"
    assert original.read_bytes() == b"original"
    assert "清理超时" in caplog.text


@pytest.mark.anyio
async def test_cleanup_query_error_keeps_file_and_original_cancel(files, caplog):
    original, upload = files
    await cancel_and_cleanup(CleanupSession(fail_query=True))
    assert upload.read_bytes() == b"upload"
    assert original.read_bytes() == b"original"
    assert "临时文件清理失败" in caplog.text
