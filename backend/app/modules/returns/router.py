"销售退货接口：登记、取消、填退款金额、退款登记、退款附件、作废。"

import asyncio
import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.file import service as file_service
from app.modules.file import storage
from app.modules.file.model import FileRecord
from app.modules.order.service import get_visible_order
from app.modules.returns import service as svc
from app.modules.returns.schema import (
    ReturnCancel,
    ReturnCreate,
    ReturnRefund,
    ReturnReview,
    ReturnSettle,
    ReturnVoid,
)

router = APIRouter(tags=["销售退货"])
logger = logging.getLogger(__name__)

# 退款附件只收常见凭证格式；前端 accept 是体验，后端后缀是实际闸门。
_REFUND_ATTACHMENT_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif",
    ".pdf", ".ofd", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".zip",
}


async def _lock_return_with_order(session, user, return_id: int):
    """统一锁序：先订单、后退货单。"""
    row = await svc.get_visible_return(session, user, return_id)
    await get_visible_order(session, user, row.order_id, for_update=True)
    return await svc.get_visible_return(session, user, return_id, for_update=True)


@router.get("/orders/{order_id}/returns")
async def list_order_returns(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    await get_visible_order(session, user, order_id)
    return ok(await svc.list_returns_for_order(session, order_id))


@router.get("/orders/{order_id}/returnable")
async def returnable(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    await get_visible_order(session, user, order_id)
    return ok(await svc.returnable_overview(session, order_id))


@router.post("/orders/{order_id}/returns")
async def create_return(
    order_id: int,
    payload: ReturnCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    order = await get_visible_order(session, user, order_id, for_update=True)
    row = await svc.create_return(
        session, user, order, payload, ip=client_ip(request)
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    return ok(data, f"退货单 {row.return_no} 已登记，等待主管复核")


@router.get("/returns")
async def list_returns(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view", "payment:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.list_returns(session, user, status=status, limit=limit))


@router.get("/returns/{return_id}")
async def get_return(
    return_id: int,
    user: CurrentUser = Depends(require_permission("order:view", "payment:view")),
    session: AsyncSession = Depends(get_db),
):
    row = await svc.get_visible_return(session, user, return_id)
    return ok(await svc.serialize_return(session, row))


@router.post("/returns/{return_id}/review")
async def review_return(
    return_id: int,
    payload: ReturnReview,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:assign")),
    session: AsyncSession = Depends(get_db),
):
    """主管复核退货：`order:assign` 是主管级权限，财务/业务员没有。"""
    row = await _lock_return_with_order(session, user, return_id)
    await svc.review_return(
        session,
        user,
        row,
        decision=payload.decision,
        note=payload.note,
        ip=client_ip(request),
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    message = (
        f"退货单 {row.return_no} 已通过复核，等待填退款金额"
        if row.status == "pending"
        else f"退货单 {row.return_no} 已驳回，可退数量已释放"
    )
    return ok(data, message)


@router.post("/returns/{return_id}/cancel")
async def cancel_return(
    return_id: int,
    payload: ReturnCancel,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _lock_return_with_order(session, user, return_id)
    await svc.cancel_return(
        session, user, row, payload.reason, ip=client_ip(request)
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    return ok(data, f"退货单 {row.return_no} 已取消")


@router.post("/returns/{return_id}/settle")
async def settle_return(
    return_id: int,
    payload: ReturnSettle,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage", "payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _lock_return_with_order(session, user, return_id)
    await svc.settle_return(
        session,
        user,
        row,
        settled_goods_amount=payload.settled_goods_amount,
        freight_refund_amount=payload.freight_refund_amount,
        settlement_note=payload.settlement_note,
        ip=client_ip(request),
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    message = (
        f"退货单 {row.return_no} 已确认退款总额 {row.settled_goods_amount:.2f} 元，"
        f"抵扣客户未付款 {row.offset_amount:.2f} 元"
        + (f"，退还客户 {row.refund_amount:.2f} 元" if row.refund_amount > 0 else "，无需向客户退款")
    )
    return ok(data, message)


@router.post("/returns/{return_id}/refund")
async def refund_return(
    return_id: int,
    payload: ReturnRefund,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _lock_return_with_order(session, user, return_id)
    await svc.refund_return(
        session, user, row, payload.refund_reference, ip=client_ip(request)
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    return ok(data, f"退货单 {row.return_no} 已登记退款")


@router.post("/returns/{return_id}/refund-attachment")
async def upload_refund_attachment(
    return_id: int,
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """财务登记实际退款前，上传银行回单/转账截图等退款凭证。

    只允许对「已确认退款总额、且确实需要退钱」的退货单上传；已退款或已作废的单
    不能再改凭证。一个退货单只保留一份附件，换附件要先删除旧的。
    """
    row = await _lock_return_with_order(session, user, return_id)
    if row.status != "completed" or row.refund_amount <= 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "只有已确认退款总额、且需要退还客户的退货单才能上传退款附件",
            422,
        )
    if row.refund_attachment_file_id:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "已有退款附件，请先删除旧附件再上传", 422
        )
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _REFUND_ATTACHMENT_SUFFIXES:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "退款附件只支持图片、PDF、OFD、Word、Excel、CSV、TXT 或 ZIP",
            422,
        )

    object_key, size, checksum = await storage.save_upload(file)
    attachment = FileRecord(
        storage_provider=settings.storage_provider,
        object_key=object_key,
        file_name=file.filename or "退款附件",
        mime_type=file.content_type,
        size=size,
        checksum=checksum,
        uploaded_by=user.id,
    )
    try:
        session.add(attachment)
        await session.flush()
        row.refund_attachment_file_id = attachment.id
        await write_audit(
            session,
            operator_id=user.id,
            action="attach_refund_attachment",
            business_type="sales_return",
            business_id=row.id,
            after={
                "refund_attachment_file_id": attachment.id,
                "file_name": attachment.file_name,
                "size": size,
            },
            ip=client_ip(request),
        )
        data = await svc.serialize_return(session, row)
        await session.commit()
    except Exception:
        # 登记失败时事务回滚，但盘上刚写的文件还在；与通用上传共用同一份补偿。
        await file_service.discard_unregistered_upload(session, object_key)
        raise
    except asyncio.CancelledError:
        # CancelledError 是 BaseException，上一段捕不到；客户端断开也要清掉半份原件。
        await file_service.discard_unregistered_upload(session, object_key)
        raise
    return ok(data, "退款附件已上传")


@router.get("/returns/{return_id}/refund-attachment")
async def download_refund_attachment(
    return_id: int,
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    """下载退款附件；权限与应收/回款查看权限一致，并按订单数据范围校验。"""
    row = await svc.get_visible_return(session, user, return_id)
    if not row.refund_attachment_file_id:
        raise AppError(ErrorCode.NOT_FOUND, "该退货单还没有退款附件", 404)
    attachment = await session.get(FileRecord, row.refund_attachment_file_id)
    if attachment is None:
        raise AppError(ErrorCode.NOT_FOUND, "退款附件文件不存在", 404)
    path = storage.absolute_path(attachment.object_key)
    if not path.exists():
        raise AppError(ErrorCode.NOT_FOUND, "退款附件文件内容已丢失", 404)
    return FileResponse(
        path,
        media_type=attachment.mime_type or "application/octet-stream",
        filename=attachment.file_name,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.file_name)}"
        },
    )


@router.delete("/returns/{return_id}/refund-attachment")
async def delete_refund_attachment(
    return_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """删除尚未随退款一起提交的退款附件；文件本体仅在无人引用时删除。"""
    row = await _lock_return_with_order(session, user, return_id)
    if row.status != "completed":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "退款已登记或退货单已结束，不能再修改退款附件", 422
        )
    if not row.refund_attachment_file_id:
        raise AppError(ErrorCode.NOT_FOUND, "该退货单还没有退款附件", 404)

    old_file_id = row.refund_attachment_file_id
    attachment = await file_service.lock_file_row(session, old_file_id)
    usage = await file_service.inspect_file_usage(
        session, old_file_id, exclude_sales_return_id=row.id
    )
    if usage.protected is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"这份文件同时是{usage.protected}，不能删除；请先解除那一处关联",
            422,
        )

    object_key = attachment.object_key if attachment is not None else None
    row.refund_attachment_file_id = None
    file_removed = not usage.shared
    if file_removed and attachment is not None:
        await session.delete(attachment)
    await write_audit(
        session,
        operator_id=user.id,
        action="remove_refund_attachment",
        business_type="sales_return",
        business_id=row.id,
        before={
            "refund_attachment_file_id": old_file_id,
            "file_name": attachment.file_name if attachment else None,
            "file_removed": file_removed,
            "still_used_by": usage.other_holders,
        },
        ip=client_ip(request),
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    if file_removed and object_key is not None:
        # 库已提交；磁盘清理失败只能留孤儿文件，不能把成功的删除返回成 500。
        try:
            storage.delete_object(object_key)
        except Exception:  # noqa: BLE001
            logger.warning(
                "退货 %s 的退款附件 %s 已从库中删除，但磁盘原件清理失败",
                row.id,
                old_file_id,
                exc_info=True,
            )
    return ok(data, "退款附件已删除" if file_removed else "已解除退款附件关联；文件仍被其他业务使用")


@router.post("/returns/{return_id}/void")
async def void_return(
    return_id: int,
    payload: ReturnVoid,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _lock_return_with_order(session, user, return_id)
    await svc.void_return(
        session, user, row, payload.reason, ip=client_ip(request)
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    return ok(data, f"退货单 {row.return_no} 已作废，原抵扣的客户未付款已恢复")
