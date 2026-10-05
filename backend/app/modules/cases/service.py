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
    label = case.customer_label or "某客户"

    def _replace_customer_name(text: str) -> str:
        """把**客户全称**换成代称并计数（分享版专用）。

        只在分享版调；调用方必须已经按 id 反查到 `customer_name`（它只用于替换，不下发）。
        之前只有标题做了这件事，**正文里的全称照旧下发**——正文是自由文本，
        销售张口就会写"某某集团"，漏了这里等于没脱敏（§5.1.1/§5.1.2）。
        """
        if not customer_name or customer_name not in text:
            return text
        hits = text.count(customer_name)
        counters["客户名"] = counters.get("客户名", 0) + hits
        return text.replace(customer_name, label)

    narrative: dict[str, str | None] = {}
    counters: dict[str, int] = {}
    for field in redaction.NARRATIVE_FIELDS:
        raw = getattr(case, field)
        masked, hits = redaction.mask_text(raw)
        for hit_label, count in hits.items():
            counters[hit_label] = counters.get(hit_label, 0) + count
        if share_view and masked:
            masked = _replace_customer_name(masked)
        # 分享版给脱敏文本；作者/主管给原文，但同样回报命中数（发布前自查用）
        narrative[field] = masked if share_view else raw

    # **标题也要脱敏**：它此前不在 NARRATIVE_FIELDS 里，两个视角都原样返回，
    # 于是分享版的客户名被换成代称、标题里却写着全称，搜索也还能按它命中。
    # 先按同一套规则抹掉标题里的金额/联系方式，再把**客户全称**换成代称
    # （名字由调用方按 id 反查，只用于替换，不下发）。
    title = case.title
    if share_view:
        title, title_hits = redaction.mask_text(title)
        for hit_label, count in title_hits.items():
            counters[hit_label] = counters.get(hit_label, 0) + count
        if title:
            title = _replace_customer_name(title)

    # **审核意见**同样走脱敏（§5.1.2）：它是自由文本，审核人顺手就会写
    # "电话 138…"、"合同 HT2024…"。此前只在正文上脱敏，审核意见原样下发。
    review_note = case.review_note
    if share_view and review_note:
        review_note, note_hits = redaction.mask_text(review_note)
        for hit_label, count in note_hits.items():
            counters[hit_label] = counters.get(hit_label, 0) + count
        review_note = _replace_customer_name(review_note)

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
        "title": title,
        "author_id": case.author_id,
        "author_name": author_name,
        # 受限字段：非授权视角只给代称
        "customer_id": case.customer_id if reveal_customer else None,
        "customer_name": customer_name if reveal_customer else None,
        "customer_label": label,
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
        "review_note": review_note,
        "created_at": case.created_at,
        # 脱敏可解释（§3.7）：抹了哪几类、各几处；以及哪些单据引用对读者不可见
        "redaction_summary": redaction.summarize(counters),
        "hidden_evidence": hidden_evidence,
        "share_view": share_view,
    }


def _apply_update(case: SalesCase, payload) -> None:
    """按 PATCH 语义更新：**显式传 null 就是"清空"**，没传的字段才不动（§5.1.4）。

    原来是 `if value is not None` 一律跳过，于是报价/订单/打样/商机的关联**解除不了**：
    传 `null` 表达"解除关联"被当成"没改"，换客户时旧客户的证据会一直残留、
    越挂越多。用 `exclude_unset=True` 才能区分"没传"和"传了 null"。
    """
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if field == "problem_tags":
            tags = value or []
            case.problem_tags = {"tags": tags} if tags else None
        else:
            setattr(case, field, value)


async def validate_evidence(
    session: AsyncSession,
    *,
    user,
    customer_id: int | None,
    quote_id: int | None = None,
    order_id: int | None = None,
    sample_id: int | None = None,
    opportunity_id: int | None = None,
) -> None:
    """校验案例引用的证据单据：**存在、同客户、在操作者数据范围内**（§5.1.3）。

    为什么必须在后端做：前端的候选筛选只是"方便"，直接调 API 就能挂上别人的单子
    ——挂上以后详情页的"证据单据"就是一条越权读入口。所以每条引用都要
    ① 走对应模块的**可见性取单**（不存在 / 不在范围 → 404 或 403），
    ② 再核对**与本案例是同一个客户**（否则等于把 A 客户的单子挂到 B 客户的案例上）。

    没选客户时不允许挂证据：没有客户就无从判断"同客户"，与其放过不如要求先选客户。
    """
    refs = {
        "quote_id": (quote_id, "报价单"),
        "order_id": (order_id, "订单"),
        "sample_id": (sample_id, "打样单"),
        "opportunity_id": (opportunity_id, "商机"),
    }
    given = {name: value for name, (value, _label) in refs.items() if value is not None}
    if not given:
        return
    if customer_id is None:
        raise AppError(
            ErrorCode.PARAM_ERROR, "挂了证据单据就必须先选定客户（否则无法核对是否同一客户）", 422
        )

    from app.modules.opportunity import service as opportunity_service
    from app.modules.order import service as order_service
    from app.modules.quote import service as quote_service
    from app.modules.sample import service as sample_service

    actual_customer: dict[str, int | None] = {}
    if quote_id is not None:
        actual_customer["quote_id"] = (await quote_service.get_visible_quote(
            session, user, quote_id
        )).customer_id
    if order_id is not None:
        actual_customer["order_id"] = (await order_service.get_visible_order(
            session, user, order_id
        )).customer_id
    if sample_id is not None:
        actual_customer["sample_id"] = (await sample_service.get_visible_or_404(
            session, user, sample_id
        )).customer_id
    if opportunity_id is not None:
        actual_customer["opportunity_id"] = (await opportunity_service.get_visible_opportunity(
            session, user, opportunity_id
        )).customer_id

    for name, (value, label) in refs.items():
        if value is None:
            continue
        if actual_customer.get(name) != customer_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"{label} #{value} 不属于本案例的客户，不能作为证据挂上来",
                422,
            )


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
    # **客户名要按 id 反查给所有人**（不只是主管）：分享版靠它把标题与正文里的
    # 客户全称换成代称。原先这里带 `and reviewer`，普通读者的列表拿不到名字，
    # 标题里的全称原样下发——列表脱敏比详情松，正是 §5.1.1 说的不一致。
    # 名字只用于替换，`customer_name` 字段仍按 reveal 决定是否下发。
    customer_ids = {row.customer_id for row in rows if row.customer_id}
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
    # **分享版也要按 id 反查客户名**：只用于把标题里的客户全称换成代称，
    # 不随响应下发（customer_id / customer_name 仍按 reveal 决定是否返回）。
    # 原先这里带 `and reveal`，分享视角拿不到名字 → 标题原样带着全称，
    # 而正文与客户字段都已脱敏。
    customer_name = None
    if case.customer_id:
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
