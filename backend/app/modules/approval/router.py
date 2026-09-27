"""审批中心接口（对齐 03-API §23）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_current_user, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord
from app.modules.approval.schema import (
    ApprovalDefinitionCreate,
    ApprovalDefinitionUpdate,
    ApprovalTransfer,
    ApprovalWithdraw,
)
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
                    "node_code": record.node_code,
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
    summary = instance.summary or {}
    co_sign = summary.get("co_sign")
    if instance.current_node == "co_sign" and co_sign:
        # 会签节点：只看会签角色（财务默认没有 quote:approve 权限，
        # 这里按角色放行而不是按权限码，否则会签就没人能批了）。管理员保留兜底。
        allowed = list(co_sign.get("role_codes") or [])
        if "admin" not in user.roles and not (set(user.roles) & set(allowed)):
            raise AppError(
                ErrorCode.FORBIDDEN,
                f"该审批处于「{co_sign.get('label', '会签')}」节点，需要 {'、'.join(allowed)} 处理",
                403,
            )
        if instance.applicant_id == user.id and "admin" not in user.roles:
            raise AppError(ErrorCode.FORBIDDEN, "不能审批自己提交的报价", 403)
        return
    # 普通节点：仍然要求 quote:approve 权限（端点上不再依赖权限码，这里显式判）
    if "admin" not in user.roles and not user.has("quote:approve"):
        raise AppError(ErrorCode.FORBIDDEN, "你的角色没有审批低价报价的权限", 403)
    _, can_approve = await pricing_service.resolve_min_margin(session, user.roles)
    if not can_approve and "admin" not in user.roles:
        raise AppError(ErrorCode.FORBIDDEN, "你的角色没有审批低价报价的权限", 403)
    # 分级审批：这一级只允许指定角色处理（管理员例外）
    allowed_roles = summary.get("node_role_codes") or []
    if allowed_roles and "admin" not in user.roles:
        if not (set(user.roles) & set(allowed_roles)):
            raise AppError(
                ErrorCode.FORBIDDEN,
                f"该审批需要「{summary.get('node_label', '上级')}」处理，你的角色无权批准",
                403,
            )
    # 被转交过的审批只由当前处理人处理，否则"转交"只是写了个名字：
    # 转出去以后原审批人照样能批，责任就说不清了。管理员保留兜底处理权
    # （有人离职又没人接手时要能收尾）。
    assignee_id = summary.get("current_assignee_id")
    if assignee_id and "admin" not in user.roles and int(assignee_id) != user.id:
        raise AppError(
            ErrorCode.FORBIDDEN,
            f"该审批已转交给「{summary.get('current_assignee_name', '他人')}」处理",
            403,
        )
    if instance.applicant_id == user.id and "admin" not in user.roles:
        raise AppError(ErrorCode.FORBIDDEN, "不能审批自己提交的报价", 403)


async def _user_ids_by_roles(session: AsyncSession, role_codes: list[str]) -> list[int]:
    """按角色编码找在职用户（会签节点的通知对象）。"""
    from app.modules.user.model import Role, user_roles

    if not role_codes:
        return []
    rows = (
        await session.execute(
            select(User.id)
            .join(user_roles, user_roles.c.user_id == User.id)
            .join(Role, Role.id == user_roles.c.role_id)
            .where(Role.code.in_(role_codes), User.status == "active")
        )
    ).scalars().all()
    return list(rows)


@router.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    instance = await _load_pending(session, approval_id)
    await _assert_can_approve(session, user, instance)

    version = await session.get(QuoteVersion, instance.business_id)
    quote = await session.get(Quote, version.quote_id) if version else None
    summary = dict(instance.summary or {})

    if instance.current_node != "co_sign":
        co_sign = summary.get("co_sign")
        if co_sign and co_sign.get("status") != "approved":
            # 本级通过但还有会签节点：审批单保持 pending，切到会签人
            session.add(
                ApprovalRecord(
                    approval_instance_id=instance.id,
                    node_code=instance.current_node,
                    approver_id=user.id,
                    action="approve",
                    comment=f"本级通过，待「{co_sign.get('label', '会签')}」",
                )
            )
            instance.current_node = "co_sign"
            summary["co_sign"] = {**co_sign, "status": "pending"}
            instance.summary = summary
            await session.flush()
            co_sign_user_ids = await _user_ids_by_roles(
                session, list(co_sign.get("role_codes") or [])
            )
            for uid in co_sign_user_ids:
                if uid == instance.applicant_id:
                    continue
                await notification_service.notify(
                    session,
                    user_id=uid,
                    type_="approval",
                    title=f"报价 {summary.get('quote_no', '')} 待会签",
                    content=(
                        f"V{summary.get('version_no', '')} 已过业务审批，"
                        f"需要「{co_sign.get('label', '会签')}」确认（一票否决）"
                    ),
                    business_type="quote",
                    business_id=quote.id if quote else instance.business_id,
                )
            await write_audit(
                session,
                operator_id=user.id,
                action="approve",
                business_type="approval",
                business_id=instance.id,
                after={"node": "co_sign", "by": user.id},
                ip=client_ip(request),
            )
            await session.commit()
            await notification_service.dispatch_pending(session)
            return ok({"current_node": "co_sign"}, f"本级已通过，进入「{co_sign.get('label', '会签')}」")

    node_code = "co_sign" if instance.current_node == "co_sign" else "manager"
    instance.status = "approved"
    instance.finished_at = datetime.now(UTC)
    instance.current_node = None
    if summary.get("co_sign"):
        summary["co_sign"] = {**summary["co_sign"], "status": "approved"}
        instance.summary = summary
    session.add(
        ApprovalRecord(
            approval_instance_id=instance.id,
            node_code=node_code,
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
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    instance = await _load_pending(session, approval_id)
    await _assert_can_approve(session, user, instance)

    version = await session.get(QuoteVersion, instance.business_id)
    quote = await session.get(Quote, version.quote_id) if version else None
    # 会签节点上拒绝 = 一票否决，整单终止；记录里写清是哪个节点否的
    node_code = "co_sign" if instance.current_node == "co_sign" else "manager"
    instance.status = "rejected"
    instance.finished_at = datetime.now(UTC)
    instance.current_node = None
    session.add(
        ApprovalRecord(
            approval_instance_id=instance.id,
            node_code=node_code,
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
                "node_code": record.node_code,
                "action": record.action,
                "comment": record.comment,
                "approver_name": name,
                "created_at": record.created_at,
            }
            for record, name in rows
        ]
    )


# ------------------------------------------------- 03-API §23 新增的接口


@router.post("/approvals/{approval_id}/transfer")
async def transfer(
    approval_id: int,
    payload: ApprovalTransfer,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:approve")),
    session: AsyncSession = Depends(get_db),
):
    """把待审批单转交给别人处理。

    转交的是**这一单的处理权**，不是把自己的审批权限给出去，
    所以只换 `current_node` 上的处理人记录，不碰角色与权限。
    转交后本人不再能批（除非被转回来），避免"转出去又自己批了"。
    """
    instance = await _load_pending(session, approval_id)
    await _assert_can_approve(session, user, instance)

    target = await session.get(User, payload.to_user_id)
    if target is None:
        raise AppError(ErrorCode.NOT_FOUND, f"接收人 id={payload.to_user_id} 不存在", 404)
    if target.status != "active":
        raise AppError(
            ErrorCode.PARAM_ERROR, f"接收人「{target.name}」已停用，不能接收审批", 422
        )
    if target.id == user.id:
        raise AppError(ErrorCode.PARAM_ERROR, "不能转交给自己", 422)

    # 把处理人记进 summary：审批链上没有独立的"当前处理人"表，
    # 用 node_assignee 表达最轻量，且序列化时能直接看到。
    summary = dict(instance.summary or {})
    history = list(summary.get("transfer_history") or [])
    history.append(
        {
            "from_user_id": user.id,
            "from_name": user.name,
            "to_user_id": target.id,
            "to_name": target.name,
            "comment": payload.comment,
            "at": datetime.now(UTC).isoformat(),
        }
    )
    summary["transfer_history"] = history
    summary["current_assignee_id"] = target.id
    summary["current_assignee_name"] = target.name
    instance.summary = summary

    session.add(
        ApprovalRecord(
            approval_instance_id=instance.id,
            node_code=instance.current_node,
            approver_id=user.id,
            action="transfer",
            comment=f"转交给 {target.name}：{payload.comment or ''}".strip("："),
        )
    )
    # 接收人要收到提醒，否则转交了也没人知道
    await notification_service.notify(
        session,
        user_id=target.id,
        type_="approval",
        title="有审批单转交给你处理",
        content=f"{summary.get('quote_no') or ''} {summary.get('reason') or ''}".strip(),
        business_type="quote",
        business_id=instance.business_id,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="transfer",
        business_type="approval",
        business_id=instance.id,
        after={"to_user_id": target.id, "comment": payload.comment},
        ip=client_ip(request),
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    ctx = await _context(session, [instance])
    return ok(_serialize(instance, ctx), f"已转交给「{target.name}」")


@router.post("/approvals/{approval_id}/withdraw")
async def withdraw(
    approval_id: int,
    payload: ApprovalWithdraw,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """申请人撤回自己提交的审批（03-API §23）。

    只有申请人本人（或管理员）能撤回；撤回后报价版本回到"未提交"，
    业务员可以改价重新提交 —— 这与 `withdraw-approval` 的区别是：
    那个从报价版本侧发起，这个从审批单侧发起，两者落到同一结果。
    """
    instance = await _load_pending(session, approval_id)
    if instance.applicant_id != user.id and "admin" not in user.roles:
        raise AppError(ErrorCode.FORBIDDEN, "只能撤回自己提交的审批", 403)

    instance.status = "withdrawn"
    instance.finished_at = datetime.now(UTC)
    instance.current_node = None

    version = await session.get(QuoteVersion, instance.business_id)
    quote = await session.get(Quote, version.quote_id) if version else None
    if version is not None:
        version.approval_status = "not_submitted"
        version.submitted_at = None
    if quote is not None:
        quote.status = "draft"

    session.add(
        ApprovalRecord(
            approval_instance_id=instance.id,
            node_code=instance.current_node,
            approver_id=user.id,
            action="withdraw",
            comment=payload.comment or "申请人撤回",
        )
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="withdraw",
        business_type="approval",
        business_id=instance.id,
        after={"comment": payload.comment},
        ip=client_ip(request),
    )
    await session.commit()
    ctx = await _context(session, [instance])
    return ok(_serialize(instance, ctx), "已撤回，报价回到草稿状态可以重新编辑")


# ---- 审批定义（03-API §23）------------------------------------------------


def _serialize_definition(row: ApprovalDefinition) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "business_type": row.business_type,
        "status": row.status,
        "config_json": row.config_json,
        "created_at": row.created_at,
    }


@router.get("/approval-definitions")
async def list_definitions(
    business_type: str | None = None,
    _: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """审批定义列表。只读给业务看；增改要 settings:manage。"""
    stmt = select(ApprovalDefinition)
    if business_type:
        stmt = stmt.where(ApprovalDefinition.business_type == business_type)
    rows = (
        await session.execute(stmt.order_by(ApprovalDefinition.id.asc()))
    ).scalars().all()
    return ok([_serialize_definition(row) for row in rows])


@router.post("/approval-definitions")
async def create_definition(
    payload: ApprovalDefinitionCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    existing = (
        await session.execute(
            select(ApprovalDefinition).where(ApprovalDefinition.code == payload.code)
        )
    ).scalars().first()
    if existing is not None:
        raise AppError(ErrorCode.DUPLICATE, f"审批定义 {payload.code} 已存在", 409)
    row = ApprovalDefinition(**payload.model_dump())
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="approval_definition",
        business_id=row.id,
        after=_serialize_definition(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize_definition(row), "审批定义已创建")


@router.patch("/approval-definitions/{definition_id}")
async def update_definition(
    definition_id: int,
    payload: ApprovalDefinitionUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(ApprovalDefinition, definition_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "审批定义不存在", 404)
    before = _serialize_definition(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="approval_definition",
        business_id=row.id,
        before=before,
        after=_serialize_definition(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize_definition(row), "已保存")
