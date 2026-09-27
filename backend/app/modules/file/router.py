"""文件中心接口（对齐 03-API §31）。"""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.config import settings
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.file import access, storage
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.user.model import User

router = APIRouter(tags=["File"])


def serialize_file(record: FileRecord, uploader: str | None = None) -> dict:
    return {
        "id": record.id,
        "file_name": record.file_name,
        "mime_type": record.mime_type,
        "size": record.size,
        "storage_provider": record.storage_provider,
        # 前端据此决定显示"预览"还是"下载"
        "previewable": is_previewable(record),
        "uploaded_by": record.uploaded_by,
        "uploader_name": uploader,
        "created_at": record.created_at,
    }


@router.post("/files/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    business_type: str | None = Form(default=None),
    business_id: int | None = Form(default=None),
    category: str | None = Form(default=None),
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    object_key, size, checksum = await storage.save_upload(file)
    record = FileRecord(
        storage_provider=settings.storage_provider,
        object_key=object_key,
        file_name=file.filename or "未命名文件",
        mime_type=file.content_type,
        size=size,
        checksum=checksum,
        uploaded_by=user.id,
    )
    session.add(record)
    await session.flush()

    if business_type and business_id:
        session.add(
            BusinessFile(
                business_type=business_type,
                business_id=business_id,
                file_id=record.id,
                category=category,
            )
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="upload",
        business_type="file",
        business_id=record.id,
        after={"file_name": record.file_name, "size": size, "target": business_type},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_file(record, user.name), "上传成功")


@router.get("/files/{file_id}")
async def get_file(
    file_id: int,
    user: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    if not await access.can_access_file(session, user, file_id):
        raise AppError(ErrorCode.FORBIDDEN, "该文件所在的业务对象不在你的数据范围内", 403)
    return ok(serialize_file(record))


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: int,
    user: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    # 附件挂在业务对象上，必须反查可见性；只校验 file:view 会让任何人按 id 取走别人的文件
    if not await access.can_access_file(session, user, file_id):
        raise AppError(ErrorCode.FORBIDDEN, "该文件所在的业务对象不在你的数据范围内", 403)
    path = storage.absolute_path(record.object_key)
    if not path.exists():
        raise AppError(ErrorCode.NOT_FOUND, "文件内容已丢失", 404)
    return FileResponse(
        path,
        media_type=record.mime_type or "application/octet-stream",
        filename=record.file_name,
        headers={
            # 中文文件名要走 RFC 5987，否则浏览器会乱码
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(record.file_name)}"
        },
    )


# 能内联预览的类型。刻意只放"浏览器原生渲染且不容易执行脚本"的。
PREVIEWABLE_MIME_PREFIXES = ("image/",)
PREVIEWABLE_MIME_EXACT = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/markdown",
}

# 显式拒绝内联的类型。前缀匹配（image/*）会漏掉下面这些，
# 所以必须先按"危险清单"挡一道：
#   svg  : 是图片但能内嵌 <script>，内联即 XSS
#   html/xml: 同理
# 这类一律降级为下载（浏览器下载后本地打开的风险由用户自己承担，
# 至少不会以我们站点的身份执行）。
DANGEROUS_MIME_EXACT = {
    "image/svg+xml",
    "text/html",
    "application/xhtml+xml",
    "text/xml",
    "application/xml",
}
DANGEROUS_SUFFIXES = {".svg", ".svgz", ".html", ".htm", ".xhtml", ".xml"}

PREVIEWABLE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".pdf",
    ".txt",
    ".csv",
    ".md",
    ".log",
}


def is_previewable(record: FileRecord) -> bool:
    mime = (record.mime_type or "").lower()
    suffix = Path(record.file_name or "").suffix.lower()
    # 危险清单优先于一切：mime 说是图片也不行
    if mime in DANGEROUS_MIME_EXACT or suffix in DANGEROUS_SUFFIXES:
        return False
    if mime.startswith(PREVIEWABLE_MIME_PREFIXES) or mime in PREVIEWABLE_MIME_EXACT:
        return True
    return suffix in PREVIEWABLE_SUFFIXES


@router.get("/files/{file_id}/preview")
async def preview_file(
    file_id: int,
    user: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    """内联预览（PRD §25 的「预览」）。

    与 download 的区别只在于 `Content-Disposition: inline`：让浏览器直接渲染，
    而不是弹下载框。**不是**把文件内容转成 HTML —— 那样等于自己造 XSS 通道。
    不可预览的类型返回 `inline=false` 并让前端走下载，不做静默降级。
    """
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    if not await access.can_access_file(session, user, file_id):
        raise AppError(ErrorCode.FORBIDDEN, "该文件所在的业务对象不在你的数据范围内", 403)
    path = storage.absolute_path(record.object_key)
    if not path.exists():
        raise AppError(ErrorCode.NOT_FOUND, "文件内容已丢失", 404)

    if not is_previewable(record):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{record.file_name}」的类型不支持在线预览，请下载后查看",
            422,
        )

    return FileResponse(
        path,
        media_type=record.mime_type or "application/octet-stream",
        filename=record.file_name,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(record.file_name)}",
            # 防嗅探：浏览器不得把内容当成别的类型执行
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/files/{file_id}")
async def delete_file(
    file_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    links = (
        await session.execute(select(BusinessFile).where(BusinessFile.file_id == file_id))
    ).scalars().all()
    for link in links:
        await session.delete(link)
    storage.delete_object(record.object_key)
    await session.delete(record)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="file",
        business_id=file_id,
        before={"file_name": record.file_name},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "文件已删除")


@router.get("/business/{business_type}/{business_id}/files")
async def list_business_files(
    business_type: str,
    business_id: int,
    user: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    # 列附件会暴露文件名/上传人等元数据，与下载同一条可见性规则：
    # 业务对象本身不在数据范围内，附件清单也不给看（防按 id 枚举）。
    if not await access.visible_object(
        session, user, business_type=business_type, business_id=business_id
    ):
        raise AppError(ErrorCode.FORBIDDEN, "该业务对象不在你的数据范围内", 403)
    rows = (
        await session.execute(
            select(BusinessFile, FileRecord, User.name)
            .join(FileRecord, FileRecord.id == BusinessFile.file_id)
            .outerjoin(User, User.id == FileRecord.uploaded_by)
            .where(
                BusinessFile.business_type == business_type,
                BusinessFile.business_id == business_id,
            )
            .order_by(BusinessFile.id.desc())
        )
    ).all()
    return ok(
        [
            {
                **serialize_file(record, uploader),
                "business_file_id": link.id,
                "category": link.category,
                "remark": link.remark,
            }
            for link, record, uploader in rows
        ]
    )


@router.post("/business/{business_type}/{business_id}/files")
async def attach_file(
    business_type: str,
    business_id: int,
    request: Request,
    file_id: int = Query(...),
    category: str | None = None,
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    link = BusinessFile(
        business_type=business_type,
        business_id=business_id,
        file_id=file_id,
        category=category,
    )
    session.add(link)
    await session.flush()
    # 附件挂载属于业务对象的内容变更，要留痕（谁能给客户/商机加附件）
    await write_audit(
        session,
        operator_id=user.id,
        action="attach",
        business_type=business_type,
        business_id=business_id,
        after={"file_id": file_id, "file_name": record.file_name, "category": category},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"business_file_id": link.id}, "已关联")


@router.delete("/business-files/{business_file_id}")
async def unlink_file(
    business_file_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    link = await session.get(BusinessFile, business_file_id)
    if link is None:
        raise AppError(ErrorCode.NOT_FOUND, "关联不存在", 404)
    before = {
        "business_type": link.business_type,
        "business_id": link.business_id,
        "file_id": link.file_id,
    }
    await session.delete(link)
    await write_audit(
        session,
        operator_id=user.id,
        action="detach",
        business_type=link.business_type,
        business_id=link.business_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已取消关联（文件本身保留）")


__all__ = ["Path"]
