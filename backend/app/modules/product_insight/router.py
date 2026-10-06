"""新品洞察接口（§3.3 第三类：运营选品 → 评审 → 转需求）。

第五批（2026-10-06）修的是七处已确认问题，都在这个文件里收口：

1. **内容冻结与评审轮次**：原来"已通过"之后还能原地改方向/卖点/客户/结论，
   状态仍是"已通过"——批准的根本不是同一份内容；待评审期间也能改，
   等于审的和提交的不是一份。现在：待评审期间不许改关键内容，
   已通过后改了就退回重审并自增轮次（与打样 §3.2 同一套做法）。
2. **字段校验与清空**：见 `schema.py`（空白标题、负数价格、null 清空、长度对齐）。
3. **转换复用统一流程**：不再直接 new 一个 CustomInquiry，改走 inquiry 模块的
   取号器与链接校验——否则"这条需求从哪来"没有编号可追。
4. **转换的并发与幂等**：行锁 + 数据库部分唯一索引；重复请求返回既有需求。
5. **权限连续性**：没客户的洞察转成**内部开发需求**（明确标识），
   不冒充"客户已经提出采购需求"，也不因为"没挂客户"就变成人人可见。
6. **删除保护**：已转需求的洞察不能删——删了来源就断了。
7. **评审人改成权限码**（`product:review`），不再写死"主管/管理员"角色：
   想让产品/开发岗评审，原来只能给他开主管角色，而那会把数据范围一起放大。
"""

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

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
from app.modules.product_insight.model import (
    FROZEN_CONTENT_FIELDS,
    INSIGHT_STATUS_LABEL,
    ROUND_RESULT_LABEL,
    ProductInsight,
    ProductInsightRound,
)
from app.modules.product_insight.schema import (
    InsightConvert,
    InsightCreate,
    InsightReview,
    InsightSubmit,
    InsightUpdate,
)
from app.modules.user.model import User

router = APIRouter(tags=["ProductInsight"])

#: 受冻结约束的字段给一句人话，报错时能说清"改不了的是哪几项"。
_FROZEN_LABEL = "标题、市场来源、目标客户、产品方向、假设卖点、价格假设、评估结论"


def _normalize_for_compare(value):
    """把一个字段值归一成"人眼看上去一样就是一样"的形态（返工单 P1-4）。

    用来判断"这次到底改了没有"。要归一三类差异，它们都不是真的改动：
    · 字符串的首尾空白 —— `"  标题"` 与 `"标题"` 是同一个标题；
    · 数字类型 —— 库里存的是 `Decimal("12.30")`，前端传 `12.3`，直接比不相等；
    · 空值 —— `None` 与 `""` 对"内容有没有变"是一回事。
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value)).normalize()
        except (InvalidOperation, ValueError):
            return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return value


def _can_review(user) -> bool:
    """能评审新品洞察（第五批 §6.3 口径已确认：指定产品/开发评审人 + 主管按授权）。

    **按权限码判，不按角色判**。原来写死 `("sales_manager", "admin")`，
    想让产品/开发岗评审就得给他开主管角色——主管的数据范围是"本部门及下级"，
    等于借评审之名拿到了整个部门的客户、报价、订单可见权。
    权限码授的是"能不能评审"这一件事，与看多少数据无关。

    ⚠️ 它只回答"能不能看见"（`_get_visible` 用）。**写入口不许用它放行** ——
    见 `_get_writable`（返工单 P1-3）。
    """
    return user.has("product:review")


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
        # 参考图：字段一直在模型里，但创建/更新/返回/页面四个环节都没打通（§6.1(6)）
        "images": row.images,
        "owner_id": row.owner_id,
        "owner_name": owner_name,
        "status": row.status,
        "status_label": INSIGHT_STATUS_LABEL.get(row.status, row.status),
        "reviewer_id": row.reviewer_id,
        "reviewed_at": row.reviewed_at,
        "review_note": row.review_note,
        # 审批轮次（§6.1(1)）：第几次提交评审。驳回重提、通过后改内容都会加一。
        "review_round": row.review_round or 1,
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


def _content_snapshot(row: ProductInsight) -> dict:
    """这一轮"报的是什么"：只取受评审约束的那几项（返工单 P1-4）。

    存快照而不是只存"改了哪几个字段名"——事后要能回答"当时批准的是这一份内容"，
    光记字段名答不了。价格转成 float 是为了 JSON 能直接存（`Decimal` 不是 JSON 类型）。
    """
    return {
        field: (
            float(value)
            if isinstance(value, Decimal)
            else (value if isinstance(value, (str, int, float, bool, list, dict)) else None)
        )
        for field in sorted(FROZEN_CONTENT_FIELDS)
        for value in [getattr(row, field)]
    }


async def _round_entry(
    session: AsyncSession, *, insight_id: int, round_no: int
) -> ProductInsightRound | None:
    return (
        await session.execute(
            select(ProductInsightRound)
            .where(
                ProductInsightRound.insight_id == insight_id,
                ProductInsightRound.round == round_no,
            )
            .with_for_update()
        )
    ).scalars().first()


async def _record_round_submit(
    session: AsyncSession, *, row: ProductInsight, user, when: datetime
) -> None:
    """落这一轮的"提交"部分。

    同一轮重复提交（带回同一个 `request_key` 的弱网重试）覆盖而不新增行：
    唯一约束 `(insight_id, round)` 是底线，覆盖是本意——重试不该在评审记录里
    留下两条一模一样的"第 2 轮提交"。
    """
    round_no = row.review_round or 1
    entry = await _round_entry(session, insight_id=row.id, round_no=round_no)
    if entry is None:
        entry = ProductInsightRound(
            insight_id=row.id, round=round_no, created_at=when
        )
        session.add(entry)
    entry.content_snapshot = _content_snapshot(row)
    entry.submitted_by = user.id
    entry.submitted_at = when
    # 这一次提交之前那一轮的结果不再适用于这一份内容
    entry.review_result = None
    entry.reviewer_id = None
    entry.reviewed_at = None
    entry.review_note = None
    await session.flush()


async def _record_round_review(
    session: AsyncSession, *, row: ProductInsight, user, approve: bool,
    note: str | None, when: datetime,
) -> None:
    """在当轮记录上补"审核结果"部分。

    找不到当轮记录时补建一行（只带审核结果）：这个功能上线之前提交的那些洞察
    没有提交留痕，不能因此把结论丢掉 —— 结论比内容更不可再生。
    """
    round_no = row.review_round or 1
    entry = await _round_entry(session, insight_id=row.id, round_no=round_no)
    if entry is None:
        entry = ProductInsightRound(
            insight_id=row.id, round=round_no, created_at=when
        )
        session.add(entry)
    entry.review_result = "approved" if approve else "rejected"
    entry.reviewer_id = user.id
    entry.reviewed_at = when
    entry.review_note = note
    await session.flush()


async def _serialize_rounds(session: AsyncSession, insight_id: int) -> list[dict]:
    """逐轮记录（提交内容 + 审核结果 + 审核人 + 时间），供前端展示。"""
    entries = (
        await session.execute(
            select(ProductInsightRound)
            .where(ProductInsightRound.insight_id == insight_id)
            .order_by(ProductInsightRound.round)
        )
    ).scalars().all()
    person_ids = set()
    for entry in entries:
        person_ids |= {entry.submitted_by, entry.reviewer_id} - {None}
    names: dict[int, str] = {}
    if person_ids:
        names = {
            int(uid): name
            for uid, name in (
                await session.execute(
                    select(User.id, User.name).where(User.id.in_(person_ids))
                )
            ).all()
        }
    return [
        {
            "round": entry.round,
            "content": entry.content_snapshot,
            "submitted_by": entry.submitted_by,
            "submitted_by_name": names.get(entry.submitted_by) if entry.submitted_by else None,
            "submitted_at": entry.submitted_at,
            "review_result": entry.review_result,
            "review_result_label": (
                ROUND_RESULT_LABEL.get(entry.review_result) if entry.review_result else None
            ),
            "reviewer_id": entry.reviewer_id,
            "reviewer_name": names.get(entry.reviewer_id) if entry.reviewer_id else None,
            "reviewed_at": entry.reviewed_at,
            "review_note": entry.review_note,
        }
        for entry in entries
    ]


async def _assert_assignee(session: AsyncSession, user, owner_id: int | None) -> None:
    """校验"要挂到这个负责人名下"这件事能不能做（返工单 P1-3）。

    三条：人存在、在职、以及**在我能看得到的范围内**（我不能把东西塞给一个
    我看不见的人 —— 那样既等于替他建了东西，又马上从我自己的列表里消失）。
    """
    if owner_id is None:
        return
    target = await session.get(User, owner_id)
    if target is None:
        raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={owner_id} 不存在", 404)
    if target.status != "active":
        raise AppError(
            ErrorCode.PARAM_ERROR, f"负责人「{target.name}」已停用，不能接收洞察", 422
        )
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None and owner_id not in owner_ids:
        raise AppError(
            ErrorCode.FORBIDDEN, "不能把洞察交给数据范围之外的人", 403
        )


async def _get_visible(
    session: AsyncSession, user, insight_id: int, *, for_update: bool = False
) -> ProductInsight:
    """取**可读**的洞察；`for_update` 时加行锁（转换要用，防并发双击转出两条需求）。

    这个函数只回答"**能不能看见**"。评审人要能审别人的洞察，所以
    `product:review` 在这里放行（他仍然看不到别人的客户与报价）。
    ⚠️ 但**不能拿它当写入口的取数**：能评审不等于能改别人的草稿。
    写入口一律用 `_get_writable`（返工单 P1-3）。
    """
    if for_update:
        row = (
            await session.execute(
                select(ProductInsight)
                .where(ProductInsight.id == insight_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    else:
        row = await session.get(ProductInsight, insight_id)
    if row is None or row.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "洞察记录不存在", 404)
    # 评审人要能审**别人**的洞察，否则"评审"无从谈起。这里放开的是洞察本身的
    # 可见性，不是他的数据范围——他仍然看不到别人的客户与报价。
    if _can_review(user):
        return row
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None and row.owner_id not in owner_ids:
        # 同上：数据范围之外是 403，不是 400
        raise AppError(ErrorCode.FORBIDDEN, "不在你的数据范围内", 403)
    return row


async def _get_writable(
    session: AsyncSession, user, insight_id: int, *, for_update: bool = False
) -> ProductInsight:
    """取**可写**的洞察：编辑、提交、删除、转换都走这里（返工单 P1-3）。

    与 `_get_visible` 的唯一区别是**不给评审权限开后门**。

    为什么必须分开：`_get_visible` 里那句"有 `product:review` 就直接返回"
    本来只为"评审人能看到别人的单子"，但编辑 / 提交 / 删除 / 转换四个写入口
    也都复用了它 —— 于是"仅本人范围 + 持有产品管理和评审权限"的账号，
    可以**直接改掉其他人名下的草稿**（实测复现）。
    评审要看别人的东西是职责，改别人的东西不是。

    数据范围之外是 **403**（不是 400）：这是权限问题，前端该提示"你改不了"，
    而不是让操作者去检查自己传的参数。
    """
    row = await _get_visible(session, user, insight_id, for_update=for_update)
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None and row.owner_id not in owner_ids:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "这条洞察的负责人不在你的数据范围内，不能修改"
            "（有评审权限可以看，但改不了别人的）",
            403,
        )
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
    # 评审人看全部（待评审的那些不是他的，但他必须看得到）；其他人仍按数据范围。
    if owner_ids is not None and not _can_review(user):
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
    payload = _serialize(row, owner_name=names.get(row.owner_id))
    # 逐轮评审记录一起带上（列表里那一行只有最后一轮的值，逐轮的要落表才留得住）
    payload["rounds"] = await _serialize_rounds(session, row.id)
    # 能不能改这条：前端据此决定按钮显不显示。**后端仍然逐入口再判一次** ——
    # 藏按钮不是权限（返工单 P1-3 明确说了不能只靠前端）。
    owner_ids = await scoped_owner_ids(session, user)
    payload["can_edit"] = owner_ids is None or row.owner_id in owner_ids
    payload["can_review"] = _can_review(user) and row.status == "under_review"
    return ok(payload)


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
    # 传了负责人就要校验一遍（返工单 P1-3：创建/修改负责人时要校验接收人及分配授权）。
    # 不校验的话，把 owner_id 填成数据范围外的人，这条洞察建完立刻从自己的列表里
    # 消失、还挂到了一个他管不着的人头上。
    await _assert_assignee(session, user, data.get("owner_id"))
    row = ProductInsight(
        **data,
        status="draft",
        review_round=1,
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
    """更新。**传了就改（含传 null = 清空），没传就不动。**

    关键点：不能用 `if value is not None` 判断——那样传 null 想清空价格假设时
    会被跳过，界面上看着清空了、库里旧值还在（§6.2 已确认的缺陷）。
    `exclude_unset=True` 拿到的只有"这次真的传了的字段"。
    """
    # 加行锁：编辑与「提交/审核/转换」会互相覆盖（返工单 P1-4）。
    # 不加锁时，一个人正在审、另一个人同时改了内容，审的就成了旧内容。
    row = await _get_writable(session, user, insight_id, for_update=True)
    if row.status == "converted":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已转询价线索的记录不可再修改")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return ok(_serialize(row), "没有改动")

    # 改负责人要校验接收人（在职 + 在可分配范围内），与创建同一套判据
    if "owner_id" in changes:
        await _assert_assignee(session, user, changes["owner_id"])

    # **只留下值确实变了的字段**（返工单 P1-4）。
    # 原来只看"请求里带没带这些字段名"：把标题原样再提交一遍，也会被当成改了内容、
    # 把已通过的记录退回待评审、轮次 +1（实测复现）。评审记录上就多出一轮
    # 内容完全相同的"重审"，事后看的人以为真改了什么。
    # 判据改成比较**值**：和库里一模一样就当作没改。
    effective = {
        field: value
        for field, value in changes.items()
        if _normalize_for_compare(getattr(row, field)) != _normalize_for_compare(value)
    }
    if not effective:
        return ok(_serialize(row), "没有改动")

    changes = effective
    touched = sorted(set(changes) & FROZEN_CONTENT_FIELDS)

    if row.status == "under_review" and touched:
        # 评审期间冻结内容：否则审到一半内容变了，批的是哪一份说不清
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该洞察正在评审中，评审期间不能改（{_FROZEN_LABEL}）——"
            f"改了就成了「审的和提交的不是一份」。确要修改请等评审结束",
            422,
        )

    before = {"status": row.status, "review_round": row.review_round}
    for field, value in changes.items():
        setattr(row, field, value)

    reopened = False
    if row.status == "approved" and touched:
        # 已通过后改了关键内容 → 那一版批准作废，退回待评审重来（与打样同一口径）。
        # 只有"锁"没有"退路"会让单据卡死：批准过的东西改不了也退不回，只能重开一条。
        row.status = "under_review"
        row.review_round = (row.review_round or 1) + 1
        row.reviewed_at = None
        row.reviewer_id = None
        # review_note 故意保留：上一轮的评价对新一轮评审有参考价值
        # （与打样保留 reject_reason 同一个理由）
        reopened = True

    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="product_insight",
        business_id=row.id,
        before=before,
        after={
            "status": row.status,
            "review_round": row.review_round,
            "changed": sorted(changes),
            "reopened": reopened,
        },
        ip=client_ip(request),
    )
    await session.commit()
    message = "已保存；因修改了关键内容，已退回「待评审」需重新评审" if reopened else "已保存"
    return ok(_serialize(row), message)


@router.post("/product-insights/{insight_id}/submit")
async def submit_insight(
    insight_id: int,
    request: Request,
    payload: InsightSubmit | None = None,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _get_writable(session, user, insight_id, for_update=True)
    request_key = payload.request_key if payload else None

    # 弱网重试：**带了同一个键**且已经在待评审 → 幂等返回（不加轮次、不重复留痕）。
    # 不带键时行为完全不变——用"状态是不是待评审"当幂等信号是错的，
    # 那会把"重复提交应该被拦"这条既有口径悄悄改掉。
    if request_key and row.status == "under_review" and row.review_request_key == request_key:
        return ok(_serialize(row), "已提交评审")

    if row.status not in ("draft", "rejected"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "当前状态不能提交评审")

    if row.status == "rejected":
        # 驳回后重新提交 = 新的一轮：事件键带上轮次，否则第二轮会撞上第一轮的
        # 固定键被去重吞掉，事后看不出"审过几轮、每轮批的是哪份内容"
        row.review_round = (row.review_round or 1) + 1
    row.status = "under_review"
    row.review_request_key = request_key

    # **把这一轮报到的东西记下来**（返工单 P1-4）：只记轮次号没用，
    # 事后要能回答"第 2 轮批的是哪一份内容"。快照与"冻结字段"同一份口径。
    await _record_round_submit(session, row=row, user=user, when=datetime.now(UTC))

    await write_audit(
        session, operator_id=user.id, action="submit",
        business_type="product_insight", business_id=row.id,
        after={"review_round": row.review_round},
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
    if not _can_review(user):
        # 必须显式给 403：`AppError` 不传 http_status 时默认 400，
        # 于是"没权限"会被报成"参数错误"，前端会当成自己传错了去改入参。
        raise AppError(
            ErrorCode.FORBIDDEN,
            "没有评审新品洞察的权限（需要 product:review）",
            403,
        )
    # 加行锁：两个人同时点「通过/驳回」时，后到的会等前面提交完再读，
    # 读到的是已审状态 → 被下面的状态检查挡下，只有一个能成功。
    row = await _get_visible(session, user, insight_id, for_update=True)
    if row.status != "under_review":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该记录不在待评审状态")

    reviewed_at = datetime.now(UTC)
    row.status = "approved" if payload.approve else "rejected"
    row.reviewer_id = user.id
    row.reviewed_at = reviewed_at
    row.review_note = payload.note
    # 在**当轮**记录上补审核结果（返工单 P1-4）：列表里那一行 `review_note`
    # 只有最后一轮的值，逐轮结论得落在轮次表上，否则第二轮通过之后
    # 第一轮是谁为什么否的就查不回来了。
    await _record_round_review(
        session,
        row=row,
        user=user,
        approve=payload.approve,
        note=payload.note,
        when=reviewed_at,
    )
    await write_audit(
        session, operator_id=user.id, action="review", business_type="product_insight",
        business_id=row.id,
        after={
            "approve": payload.approve,
            "note": payload.note,
            "review_round": row.review_round,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize(row), "评审完成")


@router.get("/product-insights/{insight_id}/rounds")
async def list_insight_rounds(
    insight_id: int,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """逐轮评审记录（返工单 P1-4）：每一轮报的内容、谁批的、什么时候、结论。

    读入口用 `_get_visible`（评审人要能看别人那条的评审史）。
    """
    await _get_visible(session, user, insight_id)
    return ok(await _serialize_rounds(session, insight_id))


@router.post("/product-insights/{insight_id}/convert")
async def convert_insight(
    insight_id: int,
    request: Request,
    payload: InsightConvert | None = None,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """评审通过后转成需求（§3.3 三类内容的转换关系，第五批 §6.1(3)(4)(5)）。

    与改造前的四点区别：

    1. **走 inquiry 模块的统一流程**：统一取号（`inquiry_no`）+ 客户/商机/联系人
       一致性校验。以前是直接 `CustomInquiry(...)`，绕过正常创建服务——
       转出来的需求**没有编号**，报价和打样就没法靠编号指回它，溯源直接断掉。
    2. **行锁 + 数据库唯一约束**：并发双击只会有一条需求，不是"应用层先查后建"
       （那个有时间窗，两下都查不到就会各建一条，而洞察只能回写其中一个）。
    3. **重复请求返回既有需求**：网络重试不该再建一条。
    4. **没填客户 → 内部开发需求**（口径已确认）：明确标识来源。
       市场研究资料不能伪装成"客户已经提出采购需求"，权限也保持独立
       （见 inquiry 模块 `apply_scope` 对 internal_dev 的处理）。

    价格假设仍只作为线索里的参考文字，**不写入任何价格规则**。
    """
    row = await _get_writable(session, user, insight_id, for_update=True)

    if row.status == "converted":
        # 幂等：已经转过了就把既有目标还回去（重试、连点两次都走这里）。
        # 明确告知"没有新建"，避免调用方以为又生成了一条。
        if row.converted_inquiry_id is not None:
            return ok(
                {"insight_id": row.id, "inquiry_id": row.converted_inquiry_id},
                "该洞察已经转过需求，返回的是原来那一条",
            )
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该洞察已标记为已转换，但没有关联需求")

    if row.status != "approved":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "只有评审通过的洞察才能转需求",
        )

    from app.modules.inquiry import service as inquiry_service

    data = payload or InsightConvert()
    customer_id = await inquiry_service.validate_links(
        session,
        user,
        customer_id=data.customer_id,
        opportunity_id=data.opportunity_id,
        contact_id=data.contact_id,
    )
    # 没挂客户 = 内部开发需求（口径已确认允许）。有客户的走正常客户询价。
    origin = "customer" if customer_id else "internal_dev"

    parts: list[str] = []
    if origin == "internal_dev":
        parts.append("【内部开发需求】来自市场研究，不是客户提出的采购需求；沿用洞察的独立来源与权限。")
    if row.source:
        parts.append(f"市场来源：{row.source}")
    if row.target_customer:
        parts.append(f"目标客户（洞察记录）：{row.target_customer}")
    if row.direction:
        parts.append(f"产品方向：{row.direction}")
    if row.selling_points:
        parts.append(f"假设卖点：{row.selling_points}")
    if row.price_assumption is not None:
        parts.append(f"价格假设（未确认，仅供内部参考）：{float(row.price_assumption)}")
    if row.conclusion:
        parts.append(f"评估结论：{row.conclusion}")

    inquiry = CustomInquiry(
        inquiry_no=await inquiry_service.generate_inquiry_no(session),
        title=row.title,
        description="\n".join(parts) or None,
        # 结构化带上来源，而不是只塞进描述文字里——列表/筛选要靠它
        extra={
            "insight_id": row.id,
            "insight_source": row.source,
            "insight_target_customer": row.target_customer,
        },
        customer_id=customer_id,
        opportunity_id=data.opportunity_id,
        contact_id=data.contact_id,
        quantity=data.quantity,
        target_price=data.target_price,
        status="open",
        origin=origin,
        source_insight_id=row.id,
        remark=data.remark or f"由新品洞察 #{row.id} 转入",
        created_by=user.id,
    )
    session.add(inquiry)
    await session.flush()
    row.status = "converted"
    row.converted_inquiry_id = inquiry.id
    await write_audit(
        session, operator_id=user.id, action="convert", business_type="product_insight",
        business_id=row.id,
        after={
            "inquiry_id": inquiry.id,
            "inquiry_no": inquiry.inquiry_no,
            "origin": origin,
        },
        ip=client_ip(request),
    )
    await session.commit()
    label = "内部开发需求" if origin == "internal_dev" else "定制询价"
    return ok(
        {
            "insight_id": row.id,
            "inquiry_id": inquiry.id,
            "inquiry_no": inquiry.inquiry_no,
            "origin": origin,
            "origin_label": "内部开发需求" if origin == "internal_dev" else "客户询价",
            "customer_id": customer_id,
        },
        f"已转为{label} {inquiry.inquiry_no or ''}".strip(),
    )


@router.delete("/product-insights/{insight_id}")
async def delete_insight(
    insight_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _get_writable(session, user, insight_id, for_update=True)
    if row.status == "converted":
        # 删了之后需求那边的 source_insight_id 就指向一个查不到的行，
        # 来源追溯直接断掉（§6.1(6)）。要停用应该去需求那边归档。
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该洞察已转成需求记录，不能删除——删了就看不出这条需求是从哪来的。"
            "确要停用请到需求那边归档",
            422,
        )
    row.deleted_at = datetime.now(UTC)
    await write_audit(
        session, operator_id=user.id, action="delete",
        business_type="product_insight", business_id=row.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已删除")
