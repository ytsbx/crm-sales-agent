"""现有产品寄样审批：由审批中心（`ApprovalInstance`）承载。

口径（主人 2026-11-26）：定制样品的寄样凭原询价审批；现有产品没有定制审批可依，
补一道 CRM 内寄样审批——业务员提交承运商/运费/说明 → 主管批准 → 才能登记寄样；
批准同时把现货样品单从「待审批」改为「已批准」。

为什么用 `ApprovalInstance` 而不是在样品上再存一份 JSON：审批中心要能**统一列出**
所有待办并按类型分类；状态、申请人、处理记录只能有一个真源，两处存必然分叉。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.approval.model import ApprovalDefinition, ApprovalInstance
from app.modules.sample.model import SHIP_APPROVER_ROLES, SampleRequest

BUSINESS_TYPE = "sample_shipment"
DEFINITION_CODE = "sample_shipment"
TYPE_LABEL = "寄样审批"

#: 审批中心里这一类的分类名（列表按类型分组/筛选用）。
BUSINESS_TYPE_LABEL = {
    "quote_version": "报价审批",
    BUSINESS_TYPE: TYPE_LABEL,
}


async def ensure_definition(session: AsyncSession) -> ApprovalDefinition:
    row = (
        await session.execute(
            select(ApprovalDefinition).where(ApprovalDefinition.code == DEFINITION_CODE)
        )
    ).scalar_one_or_none()
    if row is None:
        row = ApprovalDefinition(
            code=DEFINITION_CODE,
            name=TYPE_LABEL,
            business_type=BUSINESS_TYPE,
            status="active",
        )
        session.add(row)
        await session.flush()
    return row


async def latest(
    session: AsyncSession, sample_id: int, *, for_update: bool = False
) -> ApprovalInstance | None:
    stmt = (
        select(ApprovalInstance)
        .where(
            ApprovalInstance.business_type == BUSINESS_TYPE,
            ApprovalInstance.business_id == sample_id,
        )
        .order_by(ApprovalInstance.id.desc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalars().first()


async def latest_map(
    session: AsyncSession, sample_ids: list[int]
) -> dict[int, ApprovalInstance]:
    """批量取每张样品单最新一条寄样审批（列表装配用，避免 N 次查询）。"""
    if not sample_ids:
        return {}
    rows = (
        await session.execute(
            select(ApprovalInstance)
            .where(
                ApprovalInstance.business_type == BUSINESS_TYPE,
                ApprovalInstance.business_id.in_(sample_ids),
            )
            .order_by(ApprovalInstance.business_id, ApprovalInstance.id.desc())
        )
    ).scalars().all()
    result: dict[int, ApprovalInstance] = {}
    for row in rows:
        result.setdefault(row.business_id, row)
    return result


def serialize(instance: ApprovalInstance | None) -> dict | None:
    if instance is None:
        return None
    summary = instance.summary or {}
    return {
        "instance_id": instance.id,
        "status": instance.status,
        "applicant_id": instance.applicant_id,
        "applied_at": instance.created_at.isoformat() if instance.created_at else None,
        "carrier": summary.get("carrier"),
        "shipping_fee": summary.get("shipping_fee"),
        "note": summary.get("note"),
        "approver_id": summary.get("approver_id"),
        "decided_at": summary.get("decided_at"),
        "comment": summary.get("comment"),
    }


def can_approve(user, instance: ApprovalInstance) -> bool:
    """主管 / 管理员可批；不能批自己的申请（管理员除外）。"""
    if instance.status != "pending":
        return False
    if not any(role in SHIP_APPROVER_ROLES for role in user.roles) and not user.has(
        "settings:manage"
    ):
        return False
    return instance.applicant_id != user.id or "admin" in user.roles


async def apply(
    session: AsyncSession,
    sample: SampleRequest,
    user,
    *,
    carrier: str | None,
    shipping_fee: Decimal,
    note: str | None,
) -> ApprovalInstance:
    current = await latest(session, sample.id, for_update=True)
    if current is not None and current.status == "approved":
        from app.core.errors import AppError, ErrorCode

        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "寄样已批准，直接登记寄样即可", 409)
    if current is not None and current.status == "pending":
        from app.core.errors import AppError, ErrorCode

        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "寄样申请正在审批中，请等待结果", 409)
    definition = await ensure_definition(session)
    instance = ApprovalInstance(
        definition_id=definition.id,
        business_type=BUSINESS_TYPE,
        business_id=sample.id,
        applicant_id=user.id,
        status="pending",
        current_node="manager",
        summary={
            "carrier": carrier,
            "shipping_fee": str(shipping_fee),
            "note": note,
            "customer_id": sample.customer_id,
            "owner_id": sample.owner_id,
        },
        created_at=datetime.now(UTC),
    )
    session.add(instance)
    await session.flush()
    return instance


def decide(
    instance: ApprovalInstance,
    user,
    *,
    approved: bool,
    comment: str | None,
) -> None:
    """写审批结论与处理记录；样品状态由调用方按业务动作更新。"""
    now = datetime.now(UTC)
    instance.status = "approved" if approved else "rejected"
    instance.finished_at = now
    instance.summary = {
        **(instance.summary or {}),
        "approver_id": user.id,
        "decided_at": now.isoformat(),
        "comment": comment,
    }
