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


async def save_bytes(data: bytes, suffix: str = ".pdf") -> tuple[str, int, str]:
    """保存系统自己产出的一段字节（合同生成稿、报表 PDF 等）。

    与 `save_upload` 的分工：那个走流式读上传、带体积上限，是「用户传进来的」；
    这里是「系统生成的」，体积可控，一次写盘并把 sha256 一并算出来。

    校验值必须落库：它是事后证明"这份原件没被换过 / 和当初一模一样"的唯一依据。
    合同这类对外文件，光有文件不够，得能自证。
    """
    today = datetime.now(UTC).strftime("%Y/%m")
    object_key = f"{today}/{uuid4().hex}{suffix[:16]}"
    target = absolute_path(object_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return object_key, len(data), hashlib.sha256(data).hexdigest()


def delete_object(object_key: str) -> None:
    absolute_path(object_key).unlink(missing_ok=True)


def copy_to(source_key: str, target_path: Path) -> None:
    shutil.copyfile(absolute_path(source_key), target_path)
