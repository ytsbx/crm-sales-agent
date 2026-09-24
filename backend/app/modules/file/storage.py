"""存储适配器：本地磁盘实现。"""

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import AppError, ErrorCode


def file_root() -> Path:
    root = Path(settings.file_root)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[3] / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def absolute_path(object_key: str) -> Path:
    path = (file_root() / object_key).resolve()
    root = file_root().resolve()
    if not str(path).startswith(str(root)):
        # 防目录穿越：object_key 只能落在存储根目录内
        raise AppError(ErrorCode.PARAM_ERROR, "非法的文件路径")
    return path


async def save_upload(file: UploadFile) -> tuple[str, int, str]:
    """保存上传文件，返回 (object_key, size, checksum)。"""
    limit = settings.max_upload_mb * 1024 * 1024
    today = datetime.now(UTC).strftime("%Y/%m")
    suffix = Path(file.filename or "").suffix[:16]
    object_key = f"{today}/{uuid4().hex}{suffix}"
    target = absolute_path(object_key)
    target.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                buffer.close()
                target.unlink(missing_ok=True)
                raise AppError(
                    ErrorCode.PARAM_ERROR, f"文件超过 {settings.max_upload_mb} MB 限制"
                )
            digest.update(chunk)
            buffer.write(chunk)
    return object_key, size, digest.hexdigest()


def delete_object(object_key: str) -> None:
    absolute_path(object_key).unlink(missing_ok=True)


def copy_to(source_key: str, target_path: Path) -> None:
    shutil.copyfile(absolute_path(source_key), target_path)
