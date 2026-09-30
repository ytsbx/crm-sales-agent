"""新品洞察接口（§3.3 第三类：运营选品 → 评审 → 转询价线索）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.inquiry.model import CustomInquiry
from app.modules.product_insight.model import INSIGHT_STATUS_LABEL, ProductInsight
from app.modules.product_insight.schema import InsightCreate, InsightReview, InsightUpdate
from app.modules.user.model import User

router = APIRouter(tags=["ProductInsight"])

#: 评审人：主管/管理员（与案例库同一口径）
REVIEWER_ROLES = ("sales_manager", "admin")


def _is_reviewer(user) -> bool:
    return any(role in REVIEWER_ROLES for role in user.roles) or user.has("settings:manage")


def _serialize(row: ProductInsight, *, owner_name: str | None = None) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "source": row.source,
        "target_customer": row.target_customer,
        "direction": row.direction,
        "selling_points": row.selling_points,
        "price_assumption": (
            float(row.price_assumption) if row.price_assumption is not None else None
        ),
        "conclusion": row.conclusion,
        "owner_id": row.owner_id,
        "owner_name": owner_name,
        "status": row.status,
        "status_label": INSIGHT_STATUS_LABEL.get(row.status, row.status),
        "reviewer_id": row.reviewer_id,
        "reviewed_at": row.reviewed_at,
        "review_note": row.review_note,
        "converted_inquiry_id": row.converted_inquiry_id,
        "created_at": row.created_at,
    }


async def _owner_names(session: AsyncSession, rows: list[ProductInsight]) -> dict[int, str]:
    ids = {row.owner_id for row in rows if row.owner_id}
    if not ids:
        return {}
    return {
        int(uid): name
        for uid, name in (await session.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
    }


async def _get_visible(session: AsyncSession, user, insight_id: int) -> ProductInsight:
    row = await session.get(ProductInsight, insight_id)
    if row is None or row.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "洞察记录不存在", 404)
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None and row.owner_id not in owner_ids:
        raise AppError(ErrorCode.FORBIDDEN, "不在你的数据范围内")
    return row


@router.get("/product-insights")
async def list_insights(
    status: str | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(ProductInsight).where(ProductInsight.deleted_at.is_(None))
    if status:
        stmt = stmt.where(ProductInsight.status == status)
    if keyword:
        stmt = stmt.where(ProductInsight.title.ilike(f"%{keyword}%"))
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(ProductInsight.owner_id.in_(owner_ids))
    stmt = stmt.order_by(ProductInsight.id.desc())
    rows, total = await paginate(session, stmt, page, page_size)
    names = await _owner_names(session, rows)
    return ok(page_data([_serialize(r, owner_name=names.get(r.owner_id)) for r in rows], total, page, page_size))


@router.get("/product-insights/{insight_id}")
async def get_insight(
    insight_id: int,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    row = await _get_visible(session, user, insight_id)
    names = await _owner_names(session, [row])
    return ok(_serialize(row, owner_name=names.get(row.owner_id)))


@router.post("/product-insights")
async def create_insight(
    payload: InsightCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    # 默认负责人 = 创建人。**必须用 `if not data.get(...)` 而不是 setdefault**：
    # model_dump() 会把 owner_id=None 这个键也带出来，setdefault 永远不生效
    # （客户模块踩过同一个坑：所有人新建的客户都掉进公海）。
    # 不写这一行的话，主管/产品岗建完洞察立刻从列表里消失，点进去还报"不在你的范围内"——
    # 而列表与详情都按 owner_id 过滤，前端又没传这个字段，于是只能由后端兜住。
    if not data.get("owner_id"):
        data["owner_id"] = user.id
    row = ProductInsight(
        **data,
        status="draft",
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="product_insight",
        business_id=row.id,
        after={"title": row.title},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize(row), "洞察已记录")


@router.patch("/product-insights/{insight_id}")
async def update_insight(
    insight_id: int,
    payload: InsightUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _get_visible(session, user, insight_id)
    if row.status == "converted":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已转询价线索的记录不可再修改")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, field, value)
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="product_insight",
        business_id=row.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize(row), "已保存")


@router.post("/product-insights/{insight_id}/submit")
async def submit_insight(
    insight_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _get_visible(session, user, insight_id)
    if row.status not in ("draft", "rejected"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "当前状态不能提交评审")
    row.status = "under_review"
    await write_audit(
        session, operator_id=user.id, action="submit",
        business_type="product_insight", business_id=row.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize(row), "已提交评审")


@router.post("/product-insights/{insight_id}/review")
async def review_insight(
    insight_id: int,
    payload: InsightReview,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    if not _is_reviewer(user):
        raise AppError(ErrorCode.FORBIDDEN, "只有主管/管理员能评审新品洞察")
    row = await _get_visible(session, user, insight_id)
    if row.status != "under_review":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该记录不在待评审状态")
    row.status = "approved" if payload.approve else "rejected"
    row.reviewer_id = user.id
    row.reviewed_at = datetime.now(UTC)
    row.review_note = payload.note
    await write_audit(
        session, operator_id=user.id, action="review", business_type="product_insight",
        business_id=row.id, after={"approve": payload.approve, "note": payload.note},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize(row), "评审完成")


@router.post("/product-insights/{insight_id}/convert")
async def convert_insight(
    insight_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """评审通过后转成定制询价线索（§3.3 三类内容的转换关系）。

    价格假设只作为线索里的参考文字，**不写入价格规则**（未经确认不成为指导价）。
    """
    row = await _get_visible(session, user, insight_id)
    if row.status != "approved":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "只有评审通过的洞察才能转询价线索")
    description_parts = []
    if row.direction:
        description_parts.append(f"产品方向：{row.direction}")
    if row.selling_points:
        description_parts.append(f"假设卖点：{row.selling_points}")
    if row.price_assumption is not None:
        description_parts.append(f"价格假设（未确认，仅供内部参考）：{float(row.price_assumption)}")
    if row.conclusion:
        description_parts.append(f"评估结论：{row.conclusion}")
    inquiry = CustomInquiry(
        title=row.title,
        description="\n".join(description_parts) or None,
        status="open",
        remark=f"由新品洞察 #{row.id} 转入",
        created_by=user.id,
    )
    session.add(inquiry)
    await session.flush()
    row.status = "converted"
    row.converted_inquiry_id = inquiry.id
    await write_audit(
        session, operator_id=user.id, action="convert", business_type="product_insight",
        business_id=row.id, after={"inquiry_id": inquiry.id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"insight_id": row.id, "inquiry_id": inquiry.id}, "已转为定制询价线索")


@router.delete("/product-insights/{insight_id}")
async def delete_insight(
    insight_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _get_visible(session, user, insight_id)
    row.deleted_at = datetime.now(UTC)
    await write_audit(
        session, operator_id=user.id, action="delete",
        business_type="product_insight", business_id=row.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已删除")
