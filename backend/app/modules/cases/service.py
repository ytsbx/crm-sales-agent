"""案例库业务逻辑（§3.7/场景15）：检索、脱敏、审核流。"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.cases import redaction
from app.modules.cases.model import CASE_STATUS_LABEL, SalesCase
from app.modules.user.model import User

#: 有权审核与看全量（含真实客户身份）的角色
REVIEWER_ROLES = ("sales_manager", "admin")


def is_reviewer(user) -> bool:
    return any(role in REVIEWER_ROLES for role in user.roles) or user.has("settings:manage")


def serialize_case(
    case: SalesCase,
    *,
    author_name: str | None = None,
    customer_name: str | None = None,
    reviewer_name: str | None = None,
    reveal_customer: bool = False,
    evidence_scope: set[str] | None = None,
) -> dict:
    """序列化。

    `reveal_customer=False` 即「分享版」（场景15）：客户身份只留代称，**同时**
    对正文做价格/联系方式脱敏、并清掉读者无权查看的单据引用（§3.7：分享版对
    客户电话、合同、成本、特殊价做权限控制或脱敏，原单据仍按业务权限访问）。
    作者与主管看原文——他们要看的原单据本来就在自己权限内。

    `redaction_summary` 两种视角都返回，用途不同：审核人据此知道"这条案例分享
    出去会被抹掉哪些片段、要不要先改正文"，培训读者据此知道"这里为什么少了
    一个数字"。二者都不是可有可无的装饰——少了它，脱敏就从"可解释"变成"神秘消失"。
    """
    share_view = not reveal_customer
    narrative: dict[str, str | None] = {}
    counters: dict[str, int] = {}
    for field in redaction.NARRATIVE_FIELDS:
        raw = getattr(case, field)
        masked, hits = redaction.mask_text(raw)
        for label, count in hits.items():
            counters[label] = counters.get(label, 0) + count
        # 分享版给脱敏文本；作者/主管给原文，但同样回报命中数（发布前自查用）
        narrative[field] = masked if share_view else raw

    evidence: dict[str, int | None] = {
        field: getattr(case, field) for field in redaction.EVIDENCE_PERMISSIONS
    }
    hidden_evidence: list[str] = []
    if share_view and evidence_scope is not None:
        for field, value in evidence.items():
            if value is not None and field not in evidence_scope:
                evidence[field] = None
                hidden_evidence.append(field)

    return {
        "id": case.id,
        "title": case.title,
        "author_id": case.author_id,
        "author_name": author_name,
        # 受限字段：非授权视角只给代称
        "customer_id": case.customer_id if reveal_customer else None,
        "customer_name": customer_name if reveal_customer else None,
        "customer_label": case.customer_label or ("某客户" if case.customer_id else None),
        "industry": case.industry,
        "product_line": case.product_line,
        "stage_reached": case.stage_reached,
        "problem_tags": (case.problem_tags or {}).get("tags", []) if isinstance(case.problem_tags, dict) else (case.problem_tags or []),
        **narrative,
        **evidence,
        "status": case.status,
        "status_label": CASE_STATUS_LABEL.get(case.status, case.status),
        "reviewer_id": case.reviewer_id,
        "reviewer_name": reviewer_name,
        "reviewed_at": case.reviewed_at,
        "review_note": case.review_note,
        "created_at": case.created_at,
        # 脱敏可解释（§3.7）：抹了哪几类、各几处；以及哪些单据引用对读者不可见
        "redaction_summary": redaction.summarize(counters),
        "hidden_evidence": hidden_evidence,
        "share_view": share_view,
    }


def _apply_update(case: SalesCase, payload) -> None:
    for field in (
        "title", "customer_id", "customer_label", "industry", "product_line",
        "stage_reached", "problem_tags", "background", "goal", "key_actions",
        "objection_handling", "process", "result", "lessons",
        "quote_id", "order_id", "sample_id", "opportunity_id",
    ):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(case, field, value)
    if isinstance(case.problem_tags, list):
        case.problem_tags = {"tags": case.problem_tags}


async def get_case_or_404(session: AsyncSession, case_id: int) -> SalesCase:
    case = await session.get(SalesCase, case_id)
    if case is None or case.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "案例不存在", 404)
    return case


async def list_cases(
    session: AsyncSession, *, user, status: str | None, industry: str | None,
    product_line: str | None, stage: str | None, keyword: str | None,
) -> list[dict]:
    """列表：已发布人尽可读（脱敏）；未发布的只有作者自己；主管看全量。

    可见范围必须与详情一致（get_case_detail：非 published 仅作者与主管）——
    此前把 pending_review 漏给了全员，未审案例人人可见。
    """
    reviewer = is_reviewer(user)
    stmt = select(SalesCase).where(SalesCase.deleted_at.is_(None))
    if not reviewer:
        stmt = stmt.where(
            (SalesCase.status == "published") | (SalesCase.author_id == user.id)
        )
    if status:
        stmt = stmt.where(SalesCase.status == status)
    if industry:
        stmt = stmt.where(SalesCase.industry == industry)
    if product_line:
        stmt = stmt.where(SalesCase.product_line == product_line)
    if stage:
        stmt = stmt.where(SalesCase.stage_reached == stage)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            (SalesCase.title.like(like))
            | (SalesCase.lessons.like(like))
            | (SalesCase.key_actions.like(like))
        )
    rows = (
        await session.execute(stmt.order_by(SalesCase.created_at.desc()).limit(200))
    ).scalars().all()

    author_ids = {row.author_id for row in rows}
    reviewer_ids = {row.reviewer_id for row in rows if row.reviewer_id}
    users = {
        int(uid): name
        for uid, name in (
            await session.execute(select(User.id, User.name).where(User.id.in_(author_ids | reviewer_ids)))
        ).all()
    } if (author_ids | reviewer_ids) else {}
    customer_ids = {row.customer_id for row in rows if row.customer_id and reviewer}
    customers: dict[int, str] = {}
    if customer_ids:
        from app.modules.customer.model import Customer

        customers = {
            int(cid): name
            for cid, name in (
                await session.execute(
                    select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
                )
            ).all()
        }
    scope = redaction.evidence_scope_for(user)
    return [
        serialize_case(
            row,
            author_name=users.get(row.author_id),
            reviewer_name=users.get(row.reviewer_id),
            customer_name=customers.get(row.customer_id) if row.customer_id else None,
            reveal_customer=reviewer or row.author_id == user.id,
            evidence_scope=scope,
        )
        for row in rows
    ]


async def get_case_detail(session: AsyncSession, *, case: SalesCase, user) -> dict:
    reviewer = is_reviewer(user)
    if case.status != "published" and not (reviewer or case.author_id == user.id):
        raise AppError(ErrorCode.FORBIDDEN, "该案例未发布，仅作者与主管可见")
    reveal = reviewer or case.author_id == user.id
    customer_name = None
    if case.customer_id and reveal:
        from app.modules.customer.model import Customer

        customer = await session.get(Customer, case.customer_id)
        customer_name = customer.name if customer else None
    author = await session.get(User, case.author_id)
    reviewer_user = await session.get(User, case.reviewer_id) if case.reviewer_id else None
    return serialize_case(
        case,
        author_name=author.name if author else None,
        reviewer_name=reviewer_user.name if reviewer_user else None,
        customer_name=customer_name,
        reveal_customer=reveal,
        evidence_scope=redaction.evidence_scope_for(user),
    )


async def submit_case(session: AsyncSession, *, case: SalesCase, user) -> None:
    """提交审核。发布不制造业绩或跟进记录——这里刻意只改状态。"""
    if case.author_id != user.id and not is_reviewer(user):
        raise AppError(ErrorCode.FORBIDDEN, "只有作者能提交审核")
    if case.status not in ("draft", "rejected"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, f"当前状态（{CASE_STATUS_LABEL.get(case.status, case.status)}）不能提交审核")
    if not case.title or not (case.lessons or case.key_actions):
        raise AppError(ErrorCode.PARAM_ERROR, "提交前至少填写「标题」和「关键动作/可复用做法」")
    case.status = "pending_review"
    await session.flush()


async def review_case(session: AsyncSession, *, case: SalesCase, user, approve: bool, note: str | None) -> None:
    if not is_reviewer(user):
        raise AppError(ErrorCode.FORBIDDEN, "只有销售主管/管理员能审核案例")
    if case.status != "pending_review":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该案例不在待审核状态")
    case.status = "published" if approve else "rejected"
    case.reviewer_id = user.id
    case.reviewed_at = datetime.now(UTC)
    case.review_note = note
    await session.flush()
