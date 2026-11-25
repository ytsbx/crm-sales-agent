"""CSV 导入读取大小边界。"""

import asyncio
import io

import pytest
from fastapi import UploadFile

from app.core.config import settings
from app.core.csvio import read_upload_bytes
from app.core.errors import AppError


def test_csv_upload_rejects_declared_oversize_before_reading(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    upload = UploadFile(file=io.BytesIO(b"small"), filename="too-large.csv", size=2 * 1024 * 1024)

    with pytest.raises(AppError) as caught:
        asyncio.run(read_upload_bytes(upload))

    assert caught.value.http_status == 422
    assert "超过 1 MB" in caught.value.message


def test_csv_upload_rejects_streamed_oversize(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    upload = UploadFile(file=io.BytesIO(b"x" * (1024 * 1024 + 1)), filename="too-large.csv")

    with pytest.raises(AppError):
        asyncio.run(read_upload_bytes(upload))
