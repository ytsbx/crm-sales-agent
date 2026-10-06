"""案例库业务逻辑（§3.7/场景15）：检索、脱敏、审核流。"""

import json
from datetime import UTC, datetime

from sqlalchemy import String, cast, delete, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.response import paginate
from app.modules.cases import redaction
from app.modules.cases.model import (
    CASE_EVIDENCE_KINDS,
    CASE_STATUS_LABEL,
    LEGACY_EVIDENCE_FIELDS,
    CaseEvidence,
    SalesCase,
)
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
    superseded_by: int | None = None,
    evidences: list | None = None,
    reviewer_names: dict[int, str] | None = None,
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

    # **审核历史要逐条脱敏**（2026-10-06 修，此前是个绕过口）。
    # `review_note` 只留最新一条，历史在 `review_history` 里逐条追加；
    # 上面只脱敏了 `review_note`，于是界面呈现为：
    #     最新意见：某客户 电话〔手机号〕
    #     历史意见：客户甲 电话13800138000      ← 真名 + 真号码照样在里面
    # 普通培训读者的列表/详情都能拿到 `review_history`，翻一次就绕过去了。
    # 规则与其它自由文本完全相同——**不能只处理"最后一条"**。
    review_history: list[dict] = []
    for entry in case.review_history or []:
        item = dict(entry) if isinstance(entry, dict) else {"note": str(entry)}
        raw_note = item.get("note")
        if share_view and raw_note:
            masked, hits = redaction.mask_text(raw_note)
            for hit_label, count in hits.items():
                counters[hit_label] = counters.get(hit_label, 0) + count
            item["note"] = _replace_customer_name(masked)
        # 老数据里只存了 `reviewer_id`，没存名字 —— 用调用方给的映射补上，
        # 免得前端拿到历史却显示不出"谁批的"（这正是这一条返工要修的东西）。
        if not item.get("reviewer_name") and item.get("reviewer_id") and reviewer_names:
            item["reviewer_name"] = reviewer_names.get(item["reviewer_id"])
        review_history.append(item)

    evidence: dict[str, int | None] = {
        field: getattr(case, field) for field in redaction.EVIDENCE_PERMISSIONS
    }
    hidden_evidence: list[str] = []
    if share_view and evidence_scope is not None:
        for field, value in evidence.items():
            if value is not None and field not in evidence_scope:
                evidence[field] = None
                hidden_evidence.append(field)

    # ---- 证据列表（多条，§3.7）----
    # 权限过滤与旧字段**同一套判据**：按类别要对应的查看权限（`quote:view` 等），
    # 没有权限的那几条不下发 —— 但要在 `hidden_evidence` 里留个记号，
    # 让读者知道"这里少了东西"，而不是以为作者没挂证据。
    evidence_items: list[dict] = []
    for row in evidences or []:
        kind = row.kind
        field = LEGACY_EVIDENCE_FIELDS.get(kind)
        if kind not in CASE_EVIDENCE_KINDS or row.business_id is None:
            continue
        if share_view and evidence_scope is not None and field and field not in evidence_scope:
            if kind not in hidden_evidence:
                hidden_evidence.append(kind)
            continue
        # **证据标签也要脱敏**（2026-10-06 修 R01）：标签是作者手写的自由文本，
        # 写「某某集团 138… 成本 12 元」是常事。此前只有正文/标题/审核意见走脱敏，
        # 标签原样下发——正文遮得严严实实，证据那一行的客户全名照样露着。
        # 规则与其它自由文本完全相同：先抹金额/联系方式，再把客户全称换成代称。
        item_label = row.label
        if share_view and item_label:
            item_label, label_hits = redaction.mask_text(item_label)
            for hit_label, count in label_hits.items():
                counters[hit_label] = counters.get(hit_label, 0) + count
            item_label = _replace_customer_name(item_label)
        evidence_items.append(
            {
                "kind": kind,
                "kind_label": CASE_EVIDENCE_KINDS[kind],
                "business_id": row.business_id,
                "label": item_label,
            }
        )

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
        # 修订版（§5.1.5）：第几版、取代了谁、又被谁取代（superseded_by 有值即只读）
        "version": case.version or 1,
        "revision_of_id": case.revision_of_id,
        "superseded_by": superseded_by,
        # 逐条审核历史（原来只有 review_note 一个单值，下一次审核就覆盖）。
        # 走上面脱敏过的 review_history，**不要**在这里再用 case.review_history——
        # 那等于把刚脱掉的原文又放回去了。
        "review_history": review_history,
        "reviewer_id": case.reviewer_id,
        "reviewer_name": reviewer_name,
        "reviewed_at": case.reviewed_at,
        "review_note": review_note,
        "created_at": case.created_at,
        # 脱敏可解释（§3.7）：抹了哪几类、各几处；以及哪些单据引用对读者不可见
        "redaction_summary": redaction.summarize(counters),
        "hidden_evidence": hidden_evidence,
        # 证据单据**列表**（§3.7：一个案例常常是多张单据支撑的）。
        # 旧的那四个 `*_id` 字段保留在 `evidence` 里继续下发，是为了兼容——
        # 新界面读 `evidences`。
        "evidences": evidence_items,
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
        if field == "evidences":
            # 证据走 `case_evidences` 表（一个案例可以挂多条），
            # 不是案例表上的列 —— 这里跳过，由 `sync_evidences` 处理
            continue
        if field == "problem_tags":
            tags = value or []
            case.problem_tags = {"tags": tags} if tags else None
        else:
            setattr(case, field, value)


async def load_evidences(
    session: AsyncSession, case_ids: list[int]
) -> dict[int, list[CaseEvidence]]:
    """批量取证据（列表页一次查完，别在循环里逐条查）。

    同时把**旧字段上的数据当兜底**：迁移会把老数据搬进新表，
    但万一有环境没跑迁移，这里仍能读出来 —— 证据是决策依据，不能因为
    一次部署顺序问题就"看不见了"。
    """
    if not case_ids:
        return {}
    rows = (
        await session.execute(
            select(CaseEvidence)
            .where(CaseEvidence.case_id.in_(case_ids))
            .order_by(CaseEvidence.kind, CaseEvidence.id)
        )
    ).scalars().all()
    grouped: dict[int, list[CaseEvidence]] = {}
    for row in rows:
        grouped.setdefault(row.case_id, []).append(row)
    # 旧列兜底：新表里没有的 kind，从案例行上补
    legacy = (
        await session.execute(select(SalesCase).where(SalesCase.id.in_(case_ids)))
    ).scalars().all()
    for case in legacy:
        existing_kinds = {row.kind for row in grouped.get(case.id, [])}
        for kind, field in LEGACY_EVIDENCE_FIELDS.items():
            value = getattr(case, field, None)
            if value is None or kind in existing_kinds:
                continue
            # 只在库里确实没有这类证据时，才用旧字段补一条（不写库，只用于展示）
            grouped.setdefault(case.id, []).append(
                CaseEvidence(
                    case_id=case.id, kind=kind, business_id=value,
                    label=None, created_at=datetime.now(UTC),
                )
            )
    return grouped


async def sync_evidences(
    session: AsyncSession, *, case: SalesCase, user, evidences: list[dict]
) -> None:
    """把案例的证据列表整体替换成 `evidences`（返回体里的那种格式）。

    整体替换而不是逐条增删：界面提交的就是"这一版案例挂了哪几张单据"，
    逐条差异比对反而容易漏（少传一条会被当成"没动"）。
    同时同步旧的四个字段，让仍读旧字段的地方（老查询、报表）不落空。
    """
    await validate_evidences(session, user=user, customer_id=case.customer_id, evidences=evidences)

    await session.execute(delete(CaseEvidence).where(CaseEvidence.case_id == case.id))
    seen: set[tuple[str, int]] = set()
    by_kind: dict[str, int] = {}
    for item in evidences:
        kind = str(item.get("kind") or "").strip()
        business_id = item.get("business_id")
        if kind not in CASE_EVIDENCE_KINDS or business_id is None:
            continue
        key = (kind, int(business_id))
        if key in seen:
            continue
        seen.add(key)
        session.add(
            CaseEvidence(
                case_id=case.id,
                kind=kind,
                business_id=int(business_id),
                label=(item.get("label") or None),
                note=(item.get("note") or None),
                created_at=datetime.now(UTC),
            )
        )
        by_kind.setdefault(kind, int(business_id))
    # 旧列保持与证据表一致（取每类的第一条）：它们仍在序列化里下发，
    # 不一致会让两个读法给出不同的证据
    for kind, field in LEGACY_EVIDENCE_FIELDS.items():
        setattr(case, field, by_kind.get(kind))
    await session.flush()


async def validate_evidences(
    session: AsyncSession,
    *,
    user,
    customer_id: int | None,
    evidences: list[dict],
) -> None:
    """校验一批证据引用：**存在、同客户、在操作者数据范围内**（§5.1.3，多条版）。

    逐条走对应模块的**可见性取单**（不存在/不在范围 → 404 或 403），
    再核对与本案例是同一个客户。多条之后多一件事：**同一条不许挂两遍**。
    """
    valid = [
        item
        for item in evidences
        if item.get("kind") in CASE_EVIDENCE_KINDS and item.get("business_id") is not None
    ]
    if not valid:
        return
    if customer_id is None:
        raise AppError(
            ErrorCode.PARAM_ERROR, "挂了证据单据就必须先选定客户（否则无法核对是否同一客户）", 422
        )

    from app.modules.opportunity import service as opportunity_service
    from app.modules.order import service as order_service
    from app.modules.quote import service as quote_service
    from app.modules.sample import service as sample_service

    loaders = {
        "quote": quote_service.get_visible_quote,
        "order": order_service.get_visible_order,
        "sample": sample_service.get_visible_or_404,
        "opportunity": opportunity_service.get_visible_opportunity,
    }
    seen: set[tuple[str, int]] = set()
    for item in valid:
        kind = str(item["kind"])
        business_id = int(item["business_id"])
        key = (kind, business_id)
        if key in seen:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"{CASE_EVIDENCE_KINDS[kind]} #{business_id} 重复挂了两次",
                422,
            )
        seen.add(key)
        record = await loaders[kind](session, user, business_id)
        if getattr(record, "customer_id", None) != customer_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"{CASE_EVIDENCE_KINDS[kind]} #{business_id} 不属于本案例的客户，"
                f"不能作为证据挂上来",
                422,
            )


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


def visible_text_for_search(
    case: SalesCase, *, share_view: bool, customer_name: str | None
) -> str:
    """某个视角下"**看得见的那些文字**"，用于搜索匹配（返工单 P1-6）。

    为什么搜索要单独算一遍可见文字：搜索必须与"列表/详情给读者看的东西"一致。
    原来 DB 层直接按**原文**匹配标题，可标题在分享视角是脱敏后展示的 ——
    读者看见"某客户 电话〔手机号〕"，拿被隐藏的完整手机号一搜就命中。
    内容没看到，"这条案例里有这个号码"这个事实却泄露了：**搜索变成了探测接口**。

    规则与 `serialize_case` 完全同一套（同一份 `mask_text`、
    同一个"客户全称换代称"判据），不另立一套 —— 两套判据必然漂移，
    而这里漂移的后果就是漏一个字段出去。
    """
    if not share_view:
        parts = [case.title or ""]
        parts += [getattr(case, field) or "" for field in redaction.NARRATIVE_FIELDS]
        return "\n".join(parts)

    label = case.customer_label or "某客户"

    def replace_name(text: str) -> str:
        if not customer_name or customer_name not in text:
            return text
        return text.replace(customer_name, label)

    parts = [replace_name(redaction.mask_text(case.title or "")[0])]
    for field in redaction.NARRATIVE_FIELDS:
        raw = getattr(case, field) or ""
        parts.append(replace_name(redaction.mask_text(raw)[0]))
    return "\n".join(parts)


async def get_case_or_404(session: AsyncSession, case_id: int) -> SalesCase:
    case = await session.get(SalesCase, case_id)
    if case is None or case.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "案例不存在", 404)
    return case


async def list_cases(
    session: AsyncSession, *, user, status: str | None, industry: str | None,
    product_line: str | None, stage: str | None, keyword: str | None,
    problem_tags: str | None = None,
    customer_type: str | None = None,
    include_history: bool = False,
    page: int = 1, page_size: int = 20,
) -> tuple[list[dict], int]:
    """列表：已发布人尽可读（脱敏）；未发布的只有作者自己；主管看全量。

    可见范围必须与详情一致（get_case_detail：非 published 仅作者与主管）——
    此前把 pending_review 漏给了全员，未审案例人人可见。

    分页（第四批 §5.1.6）：原来是 `.limit(200)` 硬顶——第 201 条案例在页面上
    永远不出现，而且没有任何提示，用户只会以为"一共就这么些"。
    改成真分页并返回总数，前端才可能知道自己少看了多少。
    """
    reviewer = is_reviewer(user)
    stmt = select(SalesCase).where(SalesCase.deleted_at.is_(None))
    if not reviewer:
        # 已发布 + **被修订版取代的旧版**都人尽可读：旧版只是不能再改，
        # 它还是培训资料。修订稿上线就把旧版从读者视野里抹掉，等于把资料弄丢了
        # （返工单第 6 条）。
        stmt = stmt.where(
            SalesCase.status.in_(("published", "superseded"))
            | (SalesCase.author_id == user.id)
        )
    if status:
        stmt = stmt.where(SalesCase.status == status)
    if industry:
        # 模糊匹配：行业与产品线是**自由文本**（不是字典项），精确匹配等于要求
        # 用户手打出完整字段名——"机械设备"筛不到"机械设备制造"。
        stmt = stmt.where(SalesCase.industry.ilike(f"%{industry}%"))
    if product_line:
        stmt = stmt.where(SalesCase.product_line.ilike(f"%{product_line}%"))
    if stage:
        stmt = stmt.where(SalesCase.stage_reached == stage)
    if customer_type:
        # **客户类型**（方案 §3.7：按客户类型、产品线、阶段及问题检索）。
        # 它取的是客户档案上的「企业 / 个人」，**不是**案例上的「行业」——
        # 行业是案例自己填的自由文本，两者会被混为一谈（"食品"是行业，
        # "企业"才是类型；一个企业客户也可以属于任何行业）。
        # 所以要 join/子查询到客户表去筛，而不是拿 industry 顶替。
        from app.modules.customer.model import Customer

        stmt = stmt.where(
            SalesCase.customer_id.in_(
                select(Customer.id).where(
                    Customer.customer_type == customer_type,
                    Customer.deleted_at.is_(None),
                )
            )
        )
    if problem_tags:
        # 问题标签在库里存的是 **`{"tags": [...]}`**，不是裸数组（见 router 的创建路径
        # 与 `_apply_update`：两边都包了这一层）。比对必须照这个形状来——
        # 拿裸数组去比永远匹配不上（`{"tags":["x"]} @> ["x"]` 是 false）。
        #
        # 三点必须这样写，少一点就是 500 或静默查不到：
        # 1. 用 `.op("@>")` **显式**给操作符。不能用 `.contains()`：那是 SQLAlchemy 给
        #    JSON（非 jsonb）准备的，会编译成 `LIKE '%' || ... || '%'` 的字符串匹配，
        #    语义完全不对（"交期"能匹配到"交期紧"，还会把结构字符算进去）。
        # 2. 右边先 `literal(..., String)` 保证**按文本绑定**，再 cast 成 jsonb：
        #    直接传 Python 值的话类型推断会出错，PG 报 `invalid input syntax for type json`。
        # 3. 用 `json.dumps` 自己序列化，别指望 ORM 的 JSON 绑定处理器在这里生效。
        tags_json = cast(
            literal(json.dumps({"tags": [problem_tags]}, ensure_ascii=False), String), JSONB
        )
        stmt = stmt.where(SalesCase.problem_tags.op("@>")(tags_json))
    if keyword:
        like = f"%{keyword}%"
        if reviewer:
            # 有原文权限的人（主管/管理员）：照旧在 DB 层按原文搜，
            # **保留"按做法和问题检索"的能力**（审查方明确要求不能靠取消正文搜索来解决）。
            # 标题与正文都搜：他要找的可能是"这个客户"也可能是"这个做法"。
            stmt = stmt.where(
                or_(
                    SalesCase.title.like(like),
                    SalesCase.lessons.like(like),
                    SalesCase.key_actions.like(like),
                )
            )
        # 无原文权限的读者：这里**不加 DB 条件** —— 脱敏是 Python 层的字符串替换，
        # SQL 表达不了"脱敏之后长什么样"。改在下面取回候选用可见文字比对。
    if not include_history:
        # 默认只列**当前版本**。判据是"自己已被取代"，**不是**"存在指向自己的修订稿"：
        # 原先只要有人点过"修订"（生成一份还没发布的草稿），原版就立刻从列表里消失，
        # 作者会以为案例丢了（返工单第 6 条）。
        # 现在的边界：修订稿还在草稿/待审/被驳回 → 原版照常显示（它才是当前发布版）；
        # 修订稿发布并把原版置为 `superseded` 之后，原版才从默认列表退场
        # （仍可用 include_history=true 列出，详情也照样打得开）。
        stmt = stmt.where(SalesCase.status != "superseded")

    if keyword and not reviewer:
        # ---- 无原文权限：按**看得见的脱敏文字**匹配（返工单 P1-6）----
        #
        # 先把范围与其他筛选条件筛出来的候选取回，逐条算出"这个读者看得见什么"
        # （作者本人看自己那几条的原文，其余按分享版），再比关键字、再分页。
        #
        # 为什么敢全量取回：案例库是内部培训资料，量级几百到几千。
        # 若将来涨到需要预存脱敏文本，改的是**取数方式**，判据仍旧共用这一个函数 ——
        # 判据一分为二，就一定会漂。
        candidates = list(
            (await session.execute(stmt.order_by(SalesCase.created_at.desc()))).scalars().all()
        )
        cand_customer_ids = {row.customer_id for row in candidates if row.customer_id}
        cand_names: dict[int, str] = {}
        if cand_customer_ids:
            from app.modules.customer.model import Customer as _Customer

            cand_names = {
                int(cid): name
                for cid, name in (
                    await session.execute(
                        select(_Customer.id, _Customer.name).where(
                            _Customer.id.in_(cand_customer_ids)
                        )
                    )
                ).all()
            }
        matched = [
            case
            for case in candidates
            if keyword
            in visible_text_for_search(
                case,
                # 与列表下发时**同一句判据**（见下面 serialize_case 的 reveal_customer）
                share_view=not (reviewer or case.author_id == user.id),
                customer_name=cand_names.get(case.customer_id),
            )
        ]
        total = len(matched)
        start = max(0, (page - 1) * page_size)
        rows = matched[start : start + page_size]
    else:
        rows, total = await paginate(
            session, stmt.order_by(SalesCase.created_at.desc()), page, page_size
        )

    # 一次查清"谁被谁取代了"（§5.1.5）：有修订版的那些是只读历史版本
    child_of: dict[int, int] = {}
    row_ids = [row.id for row in rows]
    if row_ids:
        for child_id, parent_id in (
            await session.execute(
                select(SalesCase.id, SalesCase.revision_of_id).where(
                    SalesCase.revision_of_id.in_(row_ids),
                    SalesCase.deleted_at.is_(None),
                )
            )
        ).all():
            child_of.setdefault(parent_id, child_id)

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
    evidence_by_case = await load_evidences(session, [row.id for row in rows])
    items = [
        serialize_case(
            row,
            author_name=users.get(row.author_id),
            reviewer_name=users.get(row.reviewer_id),
            customer_name=customers.get(row.customer_id) if row.customer_id else None,
            reveal_customer=reviewer or row.author_id == user.id,
            evidence_scope=scope,
            superseded_by=child_of.get(row.id),
            evidences=evidence_by_case.get(row.id, []),
            reviewer_names=users,
        )
        for row in rows
    ]
    return items, total


async def get_case_detail(session: AsyncSession, *, case: SalesCase, user) -> dict:
    reviewer = is_reviewer(user)
    # `superseded`（被修订版取代的旧版）与 published 一样**人尽可读、只读**：
    # 修订稿上线不代表旧版要消失——培训资料得翻得到（返工单第 6 条的口径）。
    if case.status not in ("published", "superseded") and not (
        reviewer or case.author_id == user.id
    ):
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
    # 这一版有没有被修订版取代（有则只读，界面要标出来）
    child_id = (
        await session.execute(
            select(SalesCase.id)
            .where(SalesCase.revision_of_id == case.id, SalesCase.deleted_at.is_(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    return serialize_case(
        case,
        author_name=author.name if author else None,
        reviewer_name=reviewer_user.name if reviewer_user else None,
        customer_name=customer_name,
        reveal_customer=reveal,
        evidence_scope=redaction.evidence_scope_for(user),
        superseded_by=child_id,
        evidences=(await load_evidences(session, [case.id])).get(case.id, []),
        reviewer_names=(
            {reviewer_user.id: reviewer_user.name} if reviewer_user else None
        ),
    )


async def submit_case(session: AsyncSession, *, case: SalesCase, user) -> None:
    """提交审核。发布不制造业绩或跟进记录——这里刻意只改状态。"""
    if case.author_id != user.id and not is_reviewer(user):
        raise AppError(ErrorCode.FORBIDDEN, "只有作者能提交审核")
    # 行锁 + 锁内重读状态（返工单 P1-5）：不加锁时，作者提交的同时主管在驳回，
    # 两边都按"自己读到的旧状态"写，最后谁后提交谁说了算，中间那次操作白做。
    case = await _lock_case(session, case.id)
    if case.status not in ("draft", "rejected"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, f"当前状态（{CASE_STATUS_LABEL.get(case.status, case.status)}）不能提交审核")
    if not case.title or not (case.lessons or case.key_actions):
        raise AppError(ErrorCode.PARAM_ERROR, "提交前至少填写「标题」和「关键动作/可复用做法」")
    case.status = "pending_review"
    await session.flush()


async def _lock_case(session: AsyncSession, case_id: int) -> SalesCase:
    """按 id 锁住一条案例并返回**库里的最新值**（返工单 P1-5）。

    两个要点，缺一个锁就白加：
    ① `with_for_update()`：并发时后到的请求会等前面提交完；
    ② `populate_existing=True`：本项目 session 是 `expire_on_commit=False`，
       SQLAlchemy 默认不用查询结果覆盖已加载对象 —— 路由早就 `session.get()` 过这条，
       锁查询会返回 identity map 里的旧对象（状态还是进入时那个）。
       不写这一行，"锁住了、读到的却是旧状态"，锁等于没加（这个坑在别处踩过两次）。
    """
    row = (
        await session.execute(
            select(SalesCase)
            .where(SalesCase.id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if row is None or row.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "案例不存在", 404)
    return row


async def review_case(session: AsyncSession, *, case: SalesCase, user, approve: bool, note: str | None) -> None:
    """审核。**逐条追加审核历史**，并在批准修订稿时替换掉被取代的那一版（§5.1.5）。"""
    if not is_reviewer(user):
        raise AppError(ErrorCode.FORBIDDEN, "只有销售主管/管理员能审核案例")
    # 行锁 + 锁内重读（返工单 P1-5）：两个主管同时点"通过/驳回"时，
    # 后到的会等到前者提交完再读，读到的已不是待审 → 被状态检查挡下，只成一个。
    case = await _lock_case(session, case.id)
    if case.status != "pending_review":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该案例不在待审核状态")
    case.status = "published" if approve else "rejected"
    case.reviewer_id = user.id
    case.reviewed_at = datetime.now(UTC)
    case.review_note = note
    # 审核历史逐条追加：`review_note` 是单值，下一次审核就把它覆盖了，
    # 于是"被驳回过几次、每次谁批的、批的是哪一版"事后全查不出来。
    history = list(case.review_history or [])
    history.append({
        "round": len(history) + 1,
        "approve": bool(approve),
        "note": note,
        "reviewer_id": user.id,
        # 顺手把名字也存进去：只存 id 的话，前端要展示"谁批的"就得再查一次用户表，
        # 而修完这一条之前前端的类型里写的就是 `reviewer_name` —— 它一直取不到值
        # （后端从来没给过），于是审核历史那一块渲染出来只有时间没有名字。
        "reviewer_name": getattr(user, "name", None),
        "reviewed_at": case.reviewed_at.isoformat(),
        "version": case.version or 1,
    })
    case.review_history = history

    # 批准的是**修订稿** → 它替换被取代的那一版（已确认口径：修订稿）。
    # 旧版转 `superseded`：内容不再改动，仍可按已发布口径阅读（培训不断档）。
    if approve and case.revision_of_id:
        # 原版也加锁：批准与「从原版再开一份修订稿」可能同时发生，
        # 不加锁会各自按旧状态写（批准把它置 superseded、另一边还在基于它建 V3）。
        original = await session.get(SalesCase, case.revision_of_id)
        if original is not None and original.deleted_at is None:
            original = await _lock_case(session, original.id)
            if original.status != "superseded":
                original.status = "superseded"
                original.updated_at = datetime.now(UTC)
    await session.flush()


async def revise_case(session: AsyncSession, *, case: SalesCase, user) -> SalesCase:
    """从已发布的案例开一份**修订稿**（§5.1.5，已确认口径＝修订稿）。

    为什么不能原地改：审核批的是"这一版内容"，改完内容再挂着"已发布"，
    等于复用了一个对不上号的审核结论。所以已发布的版本**只读**，
    要改就复制一份新的重新走审核；批准后新版本替换当前发布版。

    修订稿继承原稿的全部内容与客户/证据引用（包括真实客户 id：内部字段，
    可见性仍由各视角决定），但**不继承审核结论**——它还没被批过。

    ⚠️ **只能从「当前发布版」开**，`superseded`（已被取代的旧版）不行。
    实测确认过的坑：从 V1 开出来的草稿取的是 **V1 的旧内容**，而且
    `version = V1.version + 1` 会跟已发布的 V2 **撞号**；这份草稿一旦审核通过，
    它就取代 V2 —— 等于**用旧内容把改进过的版本覆盖掉**（内容倒退）。
    旧版仍然读得到（只读），只是不能从它派生新版本。
    """
    if case.status == "superseded":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "这一版已被更新的一版取代，不能再从它开修订稿——"
            "那样会把旧内容倒着覆盖回当前版本。请基于「当前发布版」开新修订稿"
            "（在列表里找同名的、状态为「已发布」的那一条）。",
            422,
        )
    if case.status != "published":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"当前状态（{CASE_STATUS_LABEL.get(case.status, case.status)}）不需要开修订稿，"
            "直接改就行；只有已发布的版本不能原地改。",
            422,
        )
    # ---- 并发保护（返工单 P1-5）：**先锁原版这一行**，再谈"有没有在途修订稿" ----
    #
    # 老实现直接对"在途修订稿"那一行 `FOR UPDATE`。听起来对，但**第一次修订时
    # 那一行根本不存在** —— `SELECT ... FOR UPDATE` 锁不到任何行，于是两个并发
    # 请求都读到"没有在途修订稿"，各建一条 `revision_of_id=1, version=2`。
    # 双请求实测复现：生成两张 V2，之后谁也说不清哪份是正主。
    #
    # 正确做法是锁**它俩共同依赖的那一行**（原版）：两个请求争同一把锁，
    # 后到的会等前者提交完，然后在锁内重新查"有没有在途修订稿" —— 这次查得到，
    # 于是直接返回那一份。第一次并发也就被挡住了。
    case = await _lock_case(session, case.id)
    if case.status != "published":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "只能从「已发布」的案例开修订稿；"
            "直接改就行；只有已发布的版本不能原地改。",
            422,
        )
    # 幂等：同一原版**最多一份在途修订稿**。原先重复点"修订"会建出好几份同版本草稿，
    # 列表里一排 V2，谁也说不清哪份是正主（返工单第 6 条）。
    # 已发布 / 已被取代的那一份不算"在途"——那说明上一轮修订已经落地，可以再开新的。
    pending = (
        await session.execute(
            select(SalesCase)
            .where(
                SalesCase.revision_of_id == case.id,
                SalesCase.deleted_at.is_(None),
                SalesCase.status.in_(("draft", "pending_review", "rejected")),
            )
            .order_by(SalesCase.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if pending is not None:
        return pending

    fields = {
        field: getattr(case, field)
        for field in (
            "customer_id", "customer_label", "industry", "product_line", "stage_reached",
            "problem_tags", "background", "goal", "key_actions", "objection_handling",
            "process", "result", "lessons",
            "quote_id", "order_id", "sample_id", "opportunity_id",
        )
    }
    revision = SalesCase(
        title=case.title,
        author_id=case.author_id,
        status="draft",
        version=(case.version or 1) + 1,
        revision_of_id=case.id,
        created_at=datetime.now(UTC),
        **fields,
    )
    session.add(revision)
    await session.flush()
    return revision
