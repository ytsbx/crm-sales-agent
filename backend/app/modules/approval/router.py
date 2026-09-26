"""审批中心接口（对齐 03-API §23）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.approval.model import ApprovalInstance, ApprovalRecord
from app.modules.customer.model import Customer
from app.modules.pricing import service as pricing_service
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.notification import service as notification_service
from app.modules.user.model import User

router = APIRouter(tags=["Approval"])

STATUS_LABEL = {
    "pending": "待审批",
    "approved": "已通过",
    "rejected": "已拒绝",
    "withdrawn": "已撤回",
}


async def _context(session: AsyncSession, instances: list[ApprovalInstance]) -> dict:
    version_ids = [i.business_id for i in instances if i.business_type == "quote_version"]
    versions = {
        v.id: v
        for v in (
            await session.execute(select(QuoteVersion).where(QuoteVersion.id.in_(version_ids)))
        ).scalars().all()
    } if version_ids else {}
    quote_ids = {v.quote_id for v in versions.values()}
    quotes = {
        q.id: q
        for q in (
            await session.execute(select(Quote).where(Quote.id.in_(quote_ids)))
        ).scalars().all()
    } if quote_ids else {}
    customer_ids = {q.customer_id for q in quotes.values()}
    customers = {
        int(cid): name
        for cid, name in (
            await session.execute(select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids)))
        ).all()
    } if customer_ids else {}
    user_ids = {i.applicant_id for i in instances if i.applicant_id}
    users = {
        int(uid): name
        for uid, name in (
            await session.execute(select(User.id, User.name).where(User.id.in_(user_ids)))
        ).all()
    } if user_ids else {}
    return {"versions": versions, "quotes": quotes, "customers": customers, "users": users}


def _serialize(instance: ApprovalInstance, ctx: dict) -> dict:
    version = ctx["versions"].get(instance.business_id)
    quote = ctx["quotes"].get(version.quote_id) if version else None
    return {
        "id": instance.id,
        "business_type": instance.business_type,
        "business_id": instance.business_id,
        "status": instance.status,
        "status_label": STATUS_LABEL.get(instance.status, instance.status),
        "current_node": instance.current_node,
        "applicant_id": instance.applicant_id,
        "applicant_name": ctx["users"].get(instance.applicant_id) if instance.applicant_id else None,
        "summary": instance.summary,
        "created_at": instance.created_at,
        "finished_at": instance.finished_at,
        "quote_id": quote.id if quote else None,
        "quote_no": quote.quote_no if quote else None,
        "version_id": version.id if version else None,
        "version_no": version.version_no if version else None,
        "total_amount": float(version.total_amount) if version else None,
        "customer_name": ctx["customers"].get(quote.customer_id) if quote else None,
    }


@router.get("/approvals")
async def list_approvals(
    status: str | None = "pending",
    mine: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(ApprovalInstance)
    if status:
        stmt = stmt.where(ApprovalInstance.status == status)
    if mine:
        stmt = stmt.where(ApprovalInstance.applicant_id == user.id)
    rows, total = await paginate(session, stmt.order_by(ApprovalInstance.id.desc()), page, page_size)
    ctx = await _context(session, rows)
    return ok(page_data([_serialize(row, ctx) for row in rows], total, page, page_size))


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: int,
    _: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    instance = await session.get(ApprovalInstance, approval_id)
    if instance is None:
        raise AppError(ErrorCode.NOT_FOUND, "审批单不存在", 404)
    ctx = await _context(session, [instance])
    records = (
        await session.execute(
            select(ApprovalRecord, User.name)
            .outerjoin(User, User.id == ApprovalRecord.approver_id)
            .where(ApprovalRecord.approval_instance_id == approval_id)
            .order_by(ApprovalRecord.id.asc())
        )
    ).all()
    return ok(
        {
            **_serialize(instance, ctx),
            "records": [
                {
                    "id": record.id,
                    "action": record.action,
                    "comment": record.comment,
                    "approver_name": name,
                    "created_at": record.created_at,
                }
                for record, name in records
            ],
        }
    )


async def _load_pending(session: AsyncSession, approval_id: int) -> ApprovalInstance:
    instance = await session.get(ApprovalInstance, approval_id)
    if instance is None:
        raise AppError(ErrorCode.NOT_FOUND, "审批单不存在", 404)
    if instance.status != "pending":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该审批单已处理")
    return instance


async def _assert_can_approve(session: AsyncSession, user: CurrentUser, instance: ApprovalInstance) -> None:
    _, can_approve = await pricing_service.resolve_min_margin(session, user.roles)
    if not can_approve and "admin" not in user.roles:
        raise AppError(ErrorCode.FORBIDDEN, "你的角色没有审批低价报价的权限", 403)
    # 分级审批：这一级只允许指定角色处理（管理员例外）
    summary = instance.summary or {}
    allowed_roles = summary.get("node_role_codes") or []
    if allowed_roles and "admin" not in user.roles:
        if not (set(user.roles) & set(allowed_roles)):
            raise AppError(
                ErrorCode.FORBIDDEN,
                f"该审批需要「{summary.get('node_label', '上级')}」处理，你的角色无权批准",
                403,
            )
    if instance.applicant_id == user.id and "admin" not in user.roles:
        raise AppError(ErrorCode.FORBIDDEN, "不能审批自己提交的报价", 403)


@router.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:approve")),
    session: AsyncSession = Depends(get_db),
):
    instance = await _load_pending(session, approval_id)
    await _assert_can_approve(session, user, instance)

    version = await session.get(QuoteVersion, instance.business_id)
    quote = await session.get(Quote, version.quote_id) if version else None
    instance.status = "approved"
    instance.finished_at = datetime.now(UTC)
    instance.current_node = None
    session.add(
        ApprovalRecord(
            approval_instance_id=instance.id,
            node_code="manager",
            approver_id=user.id,
            action="approve",
        )
    )
    if version:
        version.approval_status = "approved"
        version.approved_at = datetime.now(UTC)
    if quote:
        quote.status = "approved"
    if instance.applicant_id:
        await notification_service.notify(
            session,
            user_id=instance.applicant_id,
            type_="approval",
            title="报价审批已通过",
            content=f"{quote.quote_no if quote else ''} 审批通过，现在可以发给客户了",
            business_type="quote",
            business_id=quote.id if quote else instance.business_id,
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="approve",
        business_type="quote",
        business_id=quote.id if quote else instance.id,
        after={"approval_id": instance.id},
        ip=client_ip(request),
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(None, "已通过，业务员可以发送该报价")


@router.post("/approvals/{approval_id}/reject")
async def reject(
    approval_id: int,
    request: Request,
    comment: str | None = None,
    user: CurrentUser = Depends(require_permission("quote:approve")),
    session: AsyncSession = Depends(get_db),
):
    instance = await _load_pending(session, approval_id)
    await _assert_can_approve(session, user, instance)

    version = await session.get(QuoteVersion, instance.business_id)
    quote = await session.get(Quote, version.quote_id) if version else None
    instance.status = "rejected"
    instance.finished_at = datetime.now(UTC)
    instance.current_node = None
    session.add(
        ApprovalRecord(
            approval_instance_id=instance.id,
            node_code="manager",
            approver_id=user.id,
            action="reject",
            comment=comment,
        )
    )
    if version:
        version.approval_status = "rejected"
    if quote:
        quote.status = "approval_rejected"
    if instance.applicant_id:
        await notification_service.notify(
            session,
            user_id=instance.applicant_id,
            type_="approval",
            title="报价审批被拒绝",
            content=(comment or "请调整价格后重新提交"),
            business_type="quote",
            business_id=quote.id if quote else instance.business_id,
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="reject",
        business_type="quote",
        business_id=quote.id if quote else instance.id,
        after={"comment": comment},
        ip=client_ip(request),
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(None, "已拒绝，业务员需要调整价格后重新提交")


@router.get("/approvals/{approval_id}/records")
async def records(
    approval_id: int,
    _: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(
            select(ApprovalRecord, User.name)
            .outerjoin(User, User.id == ApprovalRecord.approver_id)
            .where(ApprovalRecord.approval_instance_id == approval_id)
            .order_by(ApprovalRecord.id.asc())
        )
    ).all()
    return ok(
        [
            {
                "id": record.id,
                "action": record.action,
                "comment": record.comment,
                "approver_name": name,
                "created_at": record.created_at,
            }
            for record, name in rows
        ]
    )
