"""案例库接口（§3.7/场景15）。读=quote:view（全员内部培训）；写=作者本人；审=主管。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.cases import service as svc
from app.modules.cases.model import SalesCase
from app.modules.cases.schema import CaseCreate, CaseReview, CaseUpdate
from app.modules.customer import service as customer_service

router = APIRouter(tags=["Cases"])


def _forbid(message: str) -> None:
    raise AppError(ErrorCode.FORBIDDEN, message)


@router.get("/cases")
async def list_cases(
    status: str | None = Query(None),
    industry: str | None = Query(None),
    product_line: str | None = Query(None),
    stage: str | None = Query(None),
    keyword: str | None = Query(None),
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(
        await svc.list_cases(
            session, user=user, status=status, industry=industry,
            product_line=product_line, stage=stage, keyword=keyword,
        )
    )


@router.get("/cases/{case_id}")
async def get_case(
    case_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    case = await svc.get_case_or_404(session, case_id)
    return ok(await svc.get_case_detail(session, case=case, user=user))


@router.post("/cases")
async def create_case(
    payload: CaseCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    if payload.customer_id:
        await customer_service.get_visible_customer(session, user, payload.customer_id)
    fields = payload.model_dump()
    tags = fields.pop("problem_tags") or []
    case = SalesCase(
        **fields,
        author_id=user.id,
        problem_tags={"tags": tags} if tags else None,
        created_at=datetime.now(UTC),
    )
    session.add(case)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="case",
        business_id=case.id,
        after={"title": case.title},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_case(case, author_name=user.name, reveal_customer=True), "案例草稿已保存")


@router.patch("/cases/{case_id}")
async def update_case(
    case_id: int,
    payload: CaseUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    case = await svc.get_case_or_404(session, case_id)
    is_author = case.author_id == user.id
    if not is_author and not svc.is_reviewer(user):
        _forbid("只有作者或主管能修改案例")
    if case.status not in ("draft", "rejected") and not svc.is_reviewer(user):
        _forbid("已提交的案例只有主管能修改")
    # 与创建同一纪律：改挂客户必须校验该客户在当前用户数据范围内——
    # 否则作者可以把别人的客户挂上来，再借详情页读到客户名（场景15 脱敏漏口）
    if payload.customer_id is not None and payload.customer_id != case.customer_id:
        await customer_service.get_visible_customer(session, user, payload.customer_id)
    svc._apply_update(case, payload)
    case.updated_at = datetime.now(UTC)
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="case",
        business_id=case.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.get_case_detail(session, case=case, user=user))


@router.post("/cases/{case_id}/submit")
async def submit_case(
    case_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    case = await svc.get_case_or_404(session, case_id)
    await svc.submit_case(session, case=case, user=user)
    await write_audit(
        session,
        operator_id=user.id,
        action="submit",
        business_type="case",
        business_id=case.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_case(case, reveal_customer=True), "已提交审核")


@router.post("/cases/{case_id}/review")
async def review_case(
    case_id: int,
    payload: CaseReview,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    case = await svc.get_case_or_404(session, case_id)
    await svc.review_case(session, case=case, user=user, approve=payload.approve, note=payload.note)
    await write_audit(
        session,
        operator_id=user.id,
        action="review",
        business_type="case",
        business_id=case.id,
        after={"approve": payload.approve, "note": payload.note},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_case(case, reveal_customer=True), "审核完成")


@router.delete("/cases/{case_id}")
async def delete_case(
    case_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    case = await svc.get_case_or_404(session, case_id)
    if case.author_id != user.id and not svc.is_reviewer(user):
        _forbid("只有作者或主管能删除案例")
    case.deleted_at = datetime.now(UTC)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="case",
        business_id=case.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "案例已删除")
