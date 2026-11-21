"""审计日志查询（对齐 03-API §33）。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.audit_search import action_codes, audit_type_variants, business_type_codes
from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.draft_visibility import draft_creator, private_draft
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data
from app.modules.user.model import User

router = APIRouter(tags=["Audit"])


def _draft_audit_scope(user_id):
    from app.modules.bizdoc.model import BizDoc
    from app.modules.cases.model import SalesCase
    from app.modules.contract.model import ContractDocument
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.order.model import OrderDraft
    from app.modules.quote.model import Quote, QuoteCharge, QuoteItem, QuoteVersion
    from app.modules.sample.model import SampleItem, SampleRequest

    checks = []
    for kind, model in (
        ("quote", Quote), ("order_draft", OrderDraft), ("sample", SampleRequest),
        ("custom_inquiry", CustomInquiry), ("contract", ContractDocument),
        ("case", SalesCase), ("biz_doc", BizDoc),
    ):
        hidden = select(model.id).where(
            model.id == AuditLog.business_id, private_draft(model),
            draft_creator(model).is_distinct_from(user_id),
        ).correlate(AuditLog).exists()
        checks.append(and_(AuditLog.business_type.in_(audit_type_variants(kind)), hidden))

    version = select(QuoteVersion.id).join(Quote, Quote.id == QuoteVersion.quote_id).where(or_(
        private_draft(Quote) & draft_creator(Quote).is_distinct_from(user_id),
        private_draft(QuoteVersion) & func.coalesce(
            QuoteVersion.created_by, Quote.created_by, Quote.owner_id,
        ).is_distinct_from(user_id),
    )).correlate(AuditLog)
    checks.append(and_(AuditLog.business_type == "quote_version", version.where(
        QuoteVersion.id == AuditLog.business_id,
    ).exists()))
    def ids_from_item(item, source, key):
        current = select(cast(source, String)).where(item.id == AuditLog.business_id).correlate(AuditLog)
        return func.coalesce(current.scalar_subquery(), AuditLog.after_data[key].as_string(),
                             AuditLog.before_data[key].as_string())

    for kind, item in (("quote_item", QuoteItem), ("quote_charge", QuoteCharge)):
        source_id = ids_from_item(item, item.quote_version_id, "quote_version_id")
        checks.append(and_(AuditLog.business_type == kind, version.where(
            cast(QuoteVersion.id, String) == source_id,
        ).exists()))
    sample_source_id = ids_from_item(SampleItem, SampleItem.sample_request_id, "sample_request_id")
    sample_item = select(SampleRequest.id).where(
        cast(SampleRequest.id, String) == sample_source_id, private_draft(SampleRequest),
        draft_creator(SampleRequest).is_distinct_from(user_id),
    ).correlate(AuditLog).exists()
    checks.append(and_(AuditLog.business_type == "sample_item", sample_item))
    return ~or_(*checks)


@router.get("/audit-logs")
async def list_audit_logs(
    business_type: str | None = None,
    action: str | None = None,
    exclude_actions: Annotated[str | None, Query(max_length=1000, description="排除的动作，多个值用英文逗号分隔，例如 login,logout,refresh")] = None,
    operator_id: int | None = None,
    keyword: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("audit:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(AuditLog, User.name).outerjoin(User, User.id == AuditLog.operator_id).where(_draft_audit_scope(user.id))
    if business_type:
        stmt = stmt.where(AuditLog.business_type.in_(audit_type_variants(business_type)))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if exclude_actions:
        excluded = {value.strip() for value in exclude_actions.split(",") if value.strip()}
        if excluded:
            stmt = stmt.where(AuditLog.action.not_in(excluded))
    if operator_id:
        stmt = stmt.where(AuditLog.operator_id == operator_id)
    if start:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end:
        stmt = stmt.where(AuditLog.created_at <= end)
    if keyword:
        # 搜中文也要搜得到（2026-10-15，主人「没有搜索功能」）：
        # 界面上显示的是中文（「定制需求」「修改状态」），库里存的是英文码
        # （`custom_inquiry`、`update_status`）—— 原来只 `ilike` 这两个码字段，
        # 于是用户打中文**一个都搜不到**。这里先把中文反查成码，再一起搜。
        #
        # 同时补上**操作人姓名**：查"某某人干了什么"是最常见的用法之一，
        # 而原来只在码字段里找，按人名搜同样是空的。
        #
        # 编号也认（`#12` 或 `12`）：取证时经常是"这条记录被谁改过"，
        # 手上只有编号。纯数字才当编号，避免"2026"这种把结果搞脏。
        text = keyword.strip()
        like = f"%{text}%"
        type_codes = business_type_codes(text)
        act_codes = action_codes(text)

        conditions = [
            AuditLog.action.ilike(like),
            AuditLog.business_type.ilike(like),
            User.name.ilike(like),
        ]
        if type_codes:
            conditions.append(AuditLog.business_type.in_(type_codes))
        if act_codes:
            conditions.append(AuditLog.action.in_(act_codes))
        digits = text.lstrip("#")
        if digits.isdigit() and len(digits) <= 18:
            conditions.append(AuditLog.business_id == int(digits))

        stmt = stmt.where(or_(*conditions))

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
    user: CurrentUser = Depends(require_permission("audit:view")),
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
            .where(AuditLog.id == log_id, _draft_audit_scope(user.id))
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
    user: CurrentUser = Depends(require_permission("audit:view")),
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
            AuditLog.business_type.in_(audit_type_variants(business_type)),
            AuditLog.business_id == business_id,
            _draft_audit_scope(user.id),
        )
    )
    count_stmt = select(AuditLog).where(
        AuditLog.business_type.in_(audit_type_variants(business_type)),
        AuditLog.business_id == business_id,
        _draft_audit_scope(user.id),
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
