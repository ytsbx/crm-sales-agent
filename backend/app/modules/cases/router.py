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
from app.modules.cases.model import (
    CASE_STATUS_LABEL,
    LEGACY_EVIDENCE_FIELDS,
    SalesCase,
)
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
    customer_type: str | None = Query(
        None, description="客户类型（企业/个人），取自关联客户，不是案例上的「行业」"
    ),
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

    筛选维度按方案 §3.7：**客户类型、产品线、阶段及问题**。
    这里有个曾经写错的注释说"客户类型后端本来就支持"—— 实际不支持，
    前端拿「行业」顶替了它。两者的区别是：行业是案例自己填的自由文本
    （"食品""机械设备"），客户类型是客户档案上的企业/个人（一个企业客户
    可以属于任何行业）。所以这条要查客户表，不能拿 industry 充当。
    """
    items, total = await svc.list_cases(
        session, user=user, status=status, industry=industry,
        product_line=product_line, stage=stage, keyword=keyword,
        problem_tags=problem_tags, customer_type=customer_type,
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
    # 多条版：一次校验一批（含"同一条不许挂两遍"），见 `validate_evidences`。
    evidence_items = [
        item if isinstance(item, dict) else item.model_dump()
        for item in (
            payload.evidences
            or [
                {"kind": kind, "business_id": value}
                for kind, value in (
                    ("quote", payload.quote_id),
                    ("order", payload.order_id),
                    ("sample", payload.sample_id),
                    ("opportunity", payload.opportunity_id),
                )
                if value is not None
            ]
        )
    ]
    await svc.validate_evidences(
        session, user=user, customer_id=payload.customer_id, evidences=evidence_items
    )
    fields = payload.model_dump()
    tags = fields.pop("problem_tags") or []
    # 证据不进案例表（那是独立的表），从构造参数里摘掉
    fields.pop("evidences", None)
    case = SalesCase(
        **fields,
        author_id=user.id,
        problem_tags={"tags": tags} if tags else None,
        created_at=datetime.now(UTC),
    )
    session.add(case)
    await session.flush()
    if evidence_items:
        # 落证据表并同步旧列（旧列仍在下发，两者必须一致）
        await svc.sync_evidences(
            session, case=case, user=user, evidences=evidence_items
        )
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
    # 行锁 + 锁内重读（R02，2026-10-06）：并发审核可能刚好把这条发布掉。
    # 此前这里是 `get_case_or_404`（无锁），状态判完就直接写 —— 编辑请求停留期间
    # 主管把它审过发了，旧编辑仍会覆盖已发布正文（`_apply_update` 只写叙述列、
    # 不写 status，"已发布"这个状态保留着，正文却已经换了）。
    # 与 submit / review 用同一把锁：**凡是会改案例的动作都从同一个加锁入口进**。
    case = await svc._lock_case(session, case_id)
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
    changed_fields = payload.model_dump(exclude_unset=True)
    svc._apply_update(case, payload)
    # 用**改完之后的**关联值再校验一遍（§5.1.3）：换客户的同时可能把旧客户的
    # 报价/订单留在身上，或者新挂了不属于该客户的证据——两种情况都要拦住。
    if payload.evidences is not None:
        # 传了 `evidences` 就**整体替换**证据列表（界面提交的是"这一版挂了哪几张单"）
        await svc.sync_evidences(
            session,
            case=case,
            user=user,
            evidences=[
                item if isinstance(item, dict) else item.model_dump()
                for item in payload.evidences
            ],
        )
    elif any(field in changed_fields for field in LEGACY_EVIDENCE_FIELDS.values()):
        # 老调用方直接改 `quote_id` 这类旧字段：把它们当成证据列表同步一次。
        # **只在旧字段真的被改时**才同步 —— 否则一次改标题的 PATCH 会把
        # 通过新接口挂上的多条证据截断成"每类第一条"。
        await svc.sync_evidences(
            session,
            case=case,
            user=user,
            evidences=[
                {"kind": kind, "business_id": getattr(case, field)}
                for kind, field in LEGACY_EVIDENCE_FIELDS.items()
                if getattr(case, field) is not None
            ],
        )
    else:
        # 证据没动，但仍要校验**当前**的这一批：换了客户之后，
        # 原来那几张单据可能已经不属于新客户了
        await svc.validate_evidences(
            session,
            user=user,
            customer_id=case.customer_id,
            evidences=[
                {"kind": row.kind, "business_id": row.business_id}
                for row in (await svc.load_evidences(session, [case.id])).get(case.id, [])
            ],
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
