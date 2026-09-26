"""审计日志查询（对齐 03-API §33）。"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data
from app.modules.user.model import User

router = APIRouter(tags=["Audit"])


@router.get("/audit-logs")
async def list_audit_logs(
    business_type: str | None = None,
    action: str | None = None,
    operator_id: int | None = None,
    keyword: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("audit:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(AuditLog, User.name).outerjoin(User, User.id == AuditLog.operator_id)
    if business_type:
        stmt = stmt.where(AuditLog.business_type == business_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if operator_id:
        stmt = stmt.where(AuditLog.operator_id == operator_id)
    if start:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end:
        stmt = stmt.where(AuditLog.created_at <= end)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            AuditLog.action.ilike(like) | AuditLog.business_type.ilike(like)
        )

    count_stmt = stmt.order_by(None)
    from sqlalchemy import func

    total = (
        await session.execute(
            select(func.count()).select_from(count_stmt.order_by(None).subquery())
        )
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        {
            "id": row.id,
            "operator_id": row.operator_id,
            "operator_name": name,
            "source": row.source,
            "business_type": row.business_type,
            "business_id": row.business_id,
            "action": row.action,
            "before_data": row.before_data,
            "after_data": row.after_data,
            "ip": row.ip,
            "created_at": row.created_at,
        }
        for row, name in rows
    ]
    return ok(page_data(items, int(total), page, page_size))


@router.get("/audit-logs/{log_id}")
async def get_audit_log(
    log_id: int,
    _: CurrentUser = Depends(require_permission("audit:view")),
    session: AsyncSession = Depends(get_db),
):
    """单条审计详情（API §33）。

    列表接口按设计只给摘要，改前 / 改后的完整 JSON 在这一条里给全 ——
    排查"这单为什么变成这样"时看的就是这两个字段。
    """
    row = (
        await session.execute(
            select(AuditLog, User.name)
            .outerjoin(User, User.id == AuditLog.operator_id)
            .where(AuditLog.id == log_id)
        )
    ).first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "审计日志不存在", 404)
    log, name = row
    return ok(
        {
            "id": log.id,
            "operator_id": log.operator_id,
            "operator_name": name,
            "source": log.source,
            "business_type": log.business_type,
            "business_id": log.business_id,
            "action": log.action,
            "before_data": log.before_data,
            "after_data": log.after_data,
            "ip": log.ip,
            "created_at": log.created_at,
        }
    )


@router.get("/business/{business_type}/{business_id}/audit-logs")
async def list_business_audit_logs(
    business_type: str,
    business_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("audit:view")),
    session: AsyncSession = Depends(get_db),
):
    """某个业务对象的操作历史（API §33）。

    与列表接口分开是因为这是查详情页「日志」标签用的：
    必须按业务对象精确过滤，不能靠关键字模糊匹配。
    """
    stmt = (
        select(AuditLog, User.name)
        .outerjoin(User, User.id == AuditLog.operator_id)
        .where(
            AuditLog.business_type == business_type,
            AuditLog.business_id == business_id,
        )
    )
    count_stmt = select(AuditLog).where(
        AuditLog.business_type == business_type,
        AuditLog.business_id == business_id,
    )
    total = (
        await session.execute(
            select(func.count()).select_from(count_stmt.subquery())
        )
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        {
            "id": log.id,
            "operator_id": log.operator_id,
            "operator_name": name,
            "source": log.source,
            "business_type": log.business_type,
            "business_id": log.business_id,
            "action": log.action,
            "before_data": log.before_data,
            "after_data": log.after_data,
            "ip": log.ip,
            "created_at": log.created_at,
        }
        for log, name in rows
    ]
    return ok(page_data(items, int(total), page, page_size))
