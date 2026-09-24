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
from app.modules.file import storage
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
    _: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    return ok(serialize_file(record))


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: int,
    _: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
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
    _: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
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
    await session.commit()
    return ok({"business_file_id": link.id}, "已关联")


@router.delete("/business-files/{business_file_id}")
async def unlink_file(
    business_file_id: int,
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    link = await session.get(BusinessFile, business_file_id)
    if link is None:
        raise AppError(ErrorCode.NOT_FOUND, "关联不存在", 404)
    await session.delete(link)
    await session.commit()
    return ok(None, "已取消关联（文件本身保留）")


__all__ = ["Path"]
