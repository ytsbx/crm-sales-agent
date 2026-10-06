"""案例库接口（§3.7/场景15）。读=quote:view（全员内部培训）；写=作者本人；审=主管。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data
from app.modules.cases import service as svc
from app.modules.cases.model import CASE_STATUS_LABEL, SalesCase
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
    problem_tags: str | None = Query(
        None, description="按问题标签筛：命中的案例标签里包含它"
    ),
    keyword: str | None = Query(None),
    include_history: bool = Query(
        False, description="是否包含已被修订版取代的历史版本（默认只列当前版本）"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """案例列表（真分页，第四批 §5.1.6）。

    这几步筛选（客户类型/产品线/阶段）后端**本来就支持**，缺的是分页与前端联动；
    改成返回 `items/page/page_size/total` 之后，前端才能知道"还有多少没看到"。
    """
    items, total = await svc.list_cases(
        session, user=user, status=status, industry=industry,
        product_line=product_line, stage=stage, keyword=keyword,
        problem_tags=problem_tags,
        include_history=include_history, page=page, page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


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
    # 证据单据必须**存在、同客户、在数据范围内**（§5.1.3）：
    # 前端的候选筛选只是方便，直接调 API 就能挂上别人的单子——
    # 挂上以后详情页的"证据单据"就是一条越权读入口。
    await svc.validate_evidence(
        session, user=user, customer_id=payload.customer_id,
        quote_id=payload.quote_id, order_id=payload.order_id,
        sample_id=payload.sample_id, opportunity_id=payload.opportunity_id,
    )
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
    # **已发布 / 已被取代的版本只读**（§5.1.5，已确认口径＝修订稿）：
    # 审核批的是"这一版内容"，改完内容还挂着"已发布"，等于复用了一个对不上号的
    # 审核结论。连主管也不能原地改——要改就开修订稿重新走审核。
    if case.status in ("published", "superseded"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该案例是「{CASE_STATUS_LABEL.get(case.status, case.status)}」，不能原地修改"
            f"（审核批的是这一版内容）。请开修订稿：POST /cases/{case.id}/revise，"
            f"重新审核通过后替换当前发布版；当前版本继续可供培训。",
            422,
        )
    if case.status not in ("draft", "rejected") and not svc.is_reviewer(user):
        _forbid("已提交的案例只有主管能修改")
    # 与创建同一纪律：改挂客户必须校验该客户在当前用户数据范围内——
    # 否则作者可以把别人的客户挂上来，再借详情页读到客户名（场景15 脱敏漏口）
    if payload.customer_id is not None and payload.customer_id != case.customer_id:
        await customer_service.get_visible_customer(session, user, payload.customer_id)
    svc._apply_update(case, payload)
    # 用**改完之后的**关联值再校验一遍（§5.1.3）：换客户的同时可能把旧客户的
    # 报价/订单留在身上，或者新挂了不属于该客户的证据——两种情况都要拦住
    await svc.validate_evidence(
        session, user=user, customer_id=case.customer_id,
        quote_id=case.quote_id, order_id=case.order_id,
        sample_id=case.sample_id, opportunity_id=case.opportunity_id,
    )
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


@router.post("/cases/{case_id}/revise")
async def revise_case(
    case_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """从已发布的案例开一份**修订稿**（§5.1.5，已确认口径＝修订稿）。

    已发布版继续可供培训（不改动、不断档），修订稿是一份新的草稿：
    改完 → 提交审核 → 主管批准 → **替换当前发布版**，原版转「已被修订版取代」。
    审核结论不继承——修订稿还没被批过。
    """
    case = await svc.get_case_or_404(session, case_id)
    if case.author_id != user.id and not svc.is_reviewer(user):
        _forbid("只有作者或主管能开修订稿")
    revision = await svc.revise_case(session, case=case, user=user)
    await write_audit(
        session,
        operator_id=user.id,
        action="revise",
        business_type="case",
        business_id=revision.id,
        before={"revision_of_id": case.id, "version": case.version or 1},
        after={"id": revision.id, "version": revision.version},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        await svc.get_case_detail(session, case=revision, user=user),
        f"已开第 {revision.version} 版修订稿（草稿），原版继续可供培训",
    )


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
