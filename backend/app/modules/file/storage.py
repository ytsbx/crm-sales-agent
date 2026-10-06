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
    """把 object_key 解析成磁盘路径，并确保它**真的**落在存储根目录内。

    为什么不是字符串 `startswith`（2026-10-06 修，§8.6）：根目录取
    `/tmp/crm-proof-files` 时，键 `../crm-proof-files-other/secret.txt` 会解析成
    `/tmp/crm-proof-files-other/secret.txt` —— 字符串确实以根目录开头，于是**同前缀的
    兄弟目录**被当成了根目录内。字符串前缀不是目录归属，只有解析成真实路径、
    按父子关系比（`Path.is_relative_to`）才是。
    `resolve()` 同时把符号链接展开，所以"根目录内的链接指向根外"也一并被拒。
    """
    root = file_root().resolve()
    path = (root / object_key).resolve() if object_key else root
    # `path == root` 也要拒：那个"路径"是目录本身，不是文件（对目录 unlink/read 都是错的）。
    if not object_key or path == root or not path.is_relative_to(root):
        # 防目录穿越：object_key 只能落在存储根目录内的**文件**上
        raise AppError(ErrorCode.PARAM_ERROR, "非法的文件路径")
    return path


def _discard_partial(target: Path) -> None:
    """删掉本次刚写下的半成品文件。

    只按本次的 object_key 精确删，不扫目录、不按前缀删：清理一旦"顺手多删"，
    迟早把历史原件（已签合同、系统生成稿）一起带走。删不掉也不能盖掉原始报错——
    调用方正要抛的那个异常（读取中断/超限）才是要定位的问题。
    """
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


async def save_upload(file: UploadFile) -> tuple[str, int, str]:
    """保存上传文件，返回 (object_key, size, checksum)。

    任何异常路径（读取中断、超限、写盘失败）都必须清掉本次的半成品：
    原来只有"超限"这一条删了文件，弱网断连留下的半份会一直躺在盘上，
    事后既分不清它是垃圾还是原件，下载时才发现内容不全。
    """
    limit = settings.max_upload_mb * 1024 * 1024
    today = datetime.now(UTC).strftime("%Y/%m")
    suffix = Path(file.filename or "").suffix[:16]
    object_key = f"{today}/{uuid4().hex}{suffix}"
    target = absolute_path(object_key)
    target.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size = 0
    try:
        # 删除放在 `with` **外面**：文件句柄没关就 unlink，在 Windows 上会失败
        # （共享冲突），那样半文件反而留在盘上。
        with target.open("wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise AppError(
                        ErrorCode.PARAM_ERROR,
                        f"文件超过 {settings.max_upload_mb} MB 限制",
                    )
                digest.update(chunk)
                buffer.write(chunk)
    except BaseException:
        _discard_partial(target)
        raise
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
    try:
        target.write_bytes(data)
    except BaseException:
        # 与 save_upload 同一条纪律：写盘失败留下的是半份原件，比没有更坏
        # （下载得到一份截断的合同，看起来却像真的）。
        _discard_partial(target)
        raise
    return object_key, len(data), hashlib.sha256(data).hexdigest()


def delete_object(object_key: str) -> None:
    absolute_path(object_key).unlink(missing_ok=True)


def copy_to(source_key: str, target_path: Path) -> None:
    shutil.copyfile(absolute_path(source_key), target_path)
