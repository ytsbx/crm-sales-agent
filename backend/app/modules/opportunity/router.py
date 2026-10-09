"""商机中心接口（对齐 03-API §11 / §12 / §13）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.opportunity import service as svc
from app.modules.opportunity.model import (
    LossReason,
    Opportunity,
    OpportunityItem,
    OpportunityStage,
    OpportunityStageHistory,
)
from app.modules.opportunity.schema import (
    LossReasonCreate,
    LossReasonUpdate,
    OpportunityAssign,
    OpportunityClone,
    OpportunityCreate,
    OpportunityItemCopy,
    OpportunityItemCreate,
    OpportunityItemUpdate,
    OpportunityItemsBatch,
    OpportunityLose,
    OpportunityUpdate,
    OpportunityConfirmWin,
    OpportunityWin,
    RecommendProductsRequest,
    StageChange,
    StageCreate,
    StageReorder,
    StageUpdate,
)

router = APIRouter(tags=["Opportunity"])


# ---------------------------------------------------------------- 元数据

@router.get("/opportunity-stages")
async def list_stages(
    _: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(select(OpportunityStage).order_by(OpportunityStage.sequence.asc()))
    ).scalars().all()
    return ok([svc.serialize_stage(stage) for stage in rows])


@router.get("/loss-reasons")
async def list_loss_reasons(
    _: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.list_loss_reasons(session))


# --------------------------------------------- 03-API §13 阶段与失单原因维护
#
# 这两组都是"受控词表"：阶段决定流水线与漏斗口径，失单原因决定失单分析口径。
# 所以删除时都要挡两件事：
#   1. 还在被商机使用的不能删（否则历史商机会指向一个不存在的阶段/原因）；
#   2. 成交/失单这两个特殊阶段不能删（状态机依赖它们，见 change_stage/win/lose）。


def _serialize_loss_reason(row: LossReason) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "category": row.category,
        "status": row.status,
    }


@router.post("/opportunity-stages")
async def create_stage(
    payload: StageCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新增阶段。不传 sequence 时排在最后。"""
    # 先锁住阶段配置：并发保存的两个管理员在这里被串成一条队（第十二批 12.3）
    await svc.lock_stages(session)
    # `Field(min_length=1)` 挡不住纯空白字符串（'   ' 长度是 3），
    # 而空编码阶段会让 stage_map、漏斗分组这些按 code 找阶段的地方悄悄失效。
    code = payload.code.strip()
    name = payload.name.strip()
    if not code or not name:
        raise AppError(ErrorCode.PARAM_ERROR, "阶段编码与名称都不能为空白", 422)

    existing = (
        await session.execute(
            select(OpportunityStage).where(OpportunityStage.code == code)
        )
    ).scalars().first()
    if existing is not None:
        raise AppError(ErrorCode.DUPLICATE, f"阶段编码 {code} 已存在", 409)

    data = payload.model_dump()
    data["code"] = code
    data["name"] = name
    if data.get("sequence") is None:
        max_seq = (
            await session.execute(select(func.max(OpportunityStage.sequence)))
        ).scalar_one()
        data["sequence"] = int(max_seq or 0) + 1

    row = OpportunityStage(**data)
    session.add(row)
    await session.flush()
    # 落库之前先看"存完之后整体还能不能用"（第十二批 12.3）：
    # 两个成交标记 / 同一阶段又成交又失单，一律当场拒，别让它先存坏再让业务入口崩
    await svc.ensure_stage_config_ok(session)
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="opportunity_stage",
        business_id=row.id,
        after=svc.serialize_stage(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_stage(row), "阶段已创建")


@router.patch("/opportunity-stages/{stage_id}")
async def update_stage(
    stage_id: int,
    payload: StageUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    # 先锁住配置、再**重读**（扔掉内存里的旧副本）——第十二批 12.3：
    # 不重读会拿着加锁前的旧值改成冲突配置。
    await svc.lock_stages(session)
    row = await session.get(OpportunityStage, stage_id, populate_existing=True)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "阶段不存在", 404)
    before = svc.serialize_stage(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await svc.ensure_stage_config_ok(session)
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="opportunity_stage",
        business_id=row.id,
        before=before,
        after=svc.serialize_stage(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_stage(row), "已保存")


@router.delete("/opportunity-stages/{stage_id}")
async def delete_stage(
    stage_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """**停用**一个阶段（第十二批 12.4：这个入口不再做物理删除）。

    从前这里是真删行，而"这个阶段还有人用吗"只看了**此刻**有没有商机停在上面 ——
    商机一推进走，阶段就被删掉，可它的名字还留在那些商机的**阶段历史**里，
    于是历史里的"从哪个阶段来 / 到哪个阶段去"变成空白，停留时长也无从解释。

    现在改成停用：新业务不再选它（`get_first_stage` 只挑启用中的普通阶段），
    历史里的名字照旧显示。成交/失单阶段仍不许动（状态机依赖它们）；
    已经是停用的再点一次是幂等的。
    """
    row = await session.get(OpportunityStage, stage_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "阶段不存在", 404)
    if row.is_win or row.is_loss:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{row.name}」是成交/失单阶段，状态机依赖它，不能停用",
            422,
        )
    if row.status == "inactive":
        return ok(svc.serialize_stage(row), f"「{row.name}」已经是停用状态")

    before = svc.serialize_stage(row)
    row.status = "inactive"
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="deactivate_stage",
        business_type="opportunity_stage",
        business_id=stage_id,
        before=before,
        after=svc.serialize_stage(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        svc.serialize_stage(row),
        f"「{row.name}」已停用（新商机不再选它，历史记录里仍会显示这个名称）",
    )


@router.post("/opportunity-stages/reorder")
async def reorder_stages(
    payload: StageReorder,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按给定顺序重排阶段。

    只调整传入的这几个的先后，未传入的保持原相对顺序排在后面 ——
    界面拖动个别阶段时不用把整条流水线传上来。
    """
    if not payload.stage_ids:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "stage_ids 不能为空", 422)
    unique_ids = list(dict.fromkeys(payload.stage_ids))

    all_stages = (
        await session.execute(
            select(OpportunityStage).order_by(OpportunityStage.sequence.asc())
        )
    ).scalars().all()
    by_id = {row.id: row for row in all_stages}
    unknown = [sid for sid in unique_ids if sid not in by_id]
    if unknown:
        raise AppError(ErrorCode.NOT_FOUND, f"阶段不存在：{unknown}", 404)

    ordered = [by_id[sid] for sid in unique_ids] + [
        row for row in all_stages if row.id not in set(unique_ids)
    ]
    before = {row.id: row.sequence for row in all_stages}
    for index, row in enumerate(ordered, start=1):
        row.sequence = index
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="reorder",
        business_type="opportunity_stage",
        business_id=None,
        before=before,
        after={row.id: row.sequence for row in ordered},
        ip=client_ip(request),
    )
    await session.commit()
    return ok([svc.serialize_stage(row) for row in ordered], "顺序已保存")


@router.post("/loss-reasons")
async def create_loss_reason(
    payload: LossReasonCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    code = payload.code.strip()
    name = payload.name.strip()
    if not code or not name:
        raise AppError(ErrorCode.PARAM_ERROR, "失单原因编码与名称都不能为空白", 422)
    existing = (
        await session.execute(select(LossReason).where(LossReason.code == code))
    ).scalars().first()
    if existing is not None:
        raise AppError(ErrorCode.DUPLICATE, f"失单原因编码 {code} 已存在", 409)
    data = payload.model_dump()
    data["code"] = code
    data["name"] = name
    row = LossReason(**data)
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="loss_reason",
        business_id=row.id,
        after=_serialize_loss_reason(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize_loss_reason(row), "失单原因已创建")


@router.patch("/loss-reasons/{reason_id}")
async def update_loss_reason(
    reason_id: int,
    payload: LossReasonUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(LossReason, reason_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "失单原因不存在", 404)
    before = _serialize_loss_reason(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="loss_reason",
        business_id=row.id,
        before=before,
        after=_serialize_loss_reason(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize_loss_reason(row), "已保存")


@router.get("/opportunities/funnel")
async def funnel(
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    """销售漏斗：按阶段统计未成交商机的数量与金额。"""
    stages = (
        await session.execute(select(OpportunityStage).order_by(OpportunityStage.sequence.asc()))
    ).scalars().all()
    stmt = await svc.apply_data_scope(
        select(
            Opportunity.stage_id,
            func.count(Opportunity.id),
            func.coalesce(func.sum(Opportunity.expected_amount), 0),
        )
        .where(Opportunity.deleted_at.is_(None), Opportunity.status == "open")
        .group_by(Opportunity.stage_id),
        user,
        session,
    )
    rows = (await session.execute(stmt)).all()
    stats = {int(sid): (int(count), float(amount or 0)) for sid, count, amount in rows}
    return ok(
        [
            {
                "stage_id": stage.id,
                "stage_name": stage.name,
                "sequence": stage.sequence,
                "count": stats.get(stage.id, (0, 0.0))[0],
                "amount": stats.get(stage.id, (0, 0.0))[1],
            }
            for stage in stages
        ]
    )


# ---------------------------------------------------------------- 商机

@router.get("/opportunities")
async def list_opportunities(
    keyword: str | None = None,
    stage_id: int | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    customer_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = await svc.apply_data_scope(
        svc.build_opportunity_stmt(
            keyword=keyword,
            stage_id=stage_id,
            status=status,
            owner_id=owner_id,
            customer_id=customer_id,
        ),
        user,
        session,
    )
    rows, total = await paginate(session, stmt, page, page_size)
    customers, owners, counts = await svc.enrichment(session, rows)
    stages = await svc.stage_map(session)
    items = [
        svc.serialize_opportunity(
            opportunity,
            stage=stages.get(opportunity.stage_id),
            customer_name=customers.get(opportunity.customer_id),
            owner_name=owners.get(opportunity.owner_id) if opportunity.owner_id else None,
            item_count=counts.get(opportunity.id, 0),
        )
        for opportunity in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.post("/opportunities")
async def create_opportunity(
    payload: OpportunityCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.customer import service as customer_service
    customer = await customer_service.get_visible_customer(session, user, payload.customer_id)
    await svc.validate_contact(session, customer_id=customer.id, contact_id=payload.primary_contact_id)

    stage = await svc.get_initial_stage(session, payload.stage_id)

    data = payload.model_dump(exclude={"stage_id"})
    owner_id = data.pop("owner_id", None) or customer.owner_id or user.id
    # 负责人只校验"存在且在职"（第十二批 12.8 已拍板）：有 opportunity:manage 的人
    # 可以把商机交给**任意在职同事**，不受接收人的部门/数据范围限制 —— 与"复制商机"
    # 「改派商机」用同一把尺子（那两处一直是 `assert_owner_active`）。从前这里按数据
    # 范围判定，同一个动作三个入口三个答案：新建挂管理员 403、复制/改派却 200。
    # 注意两件事照旧：① **客户可见性**仍要过（上面 `get_visible_customer`）——
    # 允许跨部门分配，不等于能对看不见的客户建商机；② **继承来的负责人**（客户的
    # 负责人）也要过同一关，免得"指定时查了、继承时把新业务交给已停用的人"。
    owner = await svc.assert_owner_active(session, owner_id)
    opportunity = Opportunity(
        **data,
        stage_id=stage.id,
        owner_id=owner_id,
        status="open",
        created_by=user.id,
    )
    session.add(opportunity)
    await session.flush()
    session.add(
        OpportunityStageHistory(
            opportunity_id=opportunity.id,
            from_stage_id=None,
            to_stage_id=stage.id,
            operator_id=user.id,
            remark="创建商机",
            entered_at=datetime.now(UTC),
        )
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="opportunity",
        business_id=opportunity.id,
        after=svc.serialize_opportunity(opportunity, stage=stage),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        svc.serialize_opportunity(
            opportunity, stage=stage, customer_name=customer.name, owner_name=owner.name if owner else None
        ),
        "商机已创建",
    )


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(
    opportunity_id: int,
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id)
    customers, owners, counts = await svc.enrichment(session, [opportunity])
    stages = await svc.stage_map(session)
    loss_reason_name = None
    if opportunity.loss_reason_id:
        reason = await session.get(LossReason, opportunity.loss_reason_id)
        loss_reason_name = reason.name if reason else None
    return ok(
        svc.serialize_opportunity(
            opportunity,
            stage=stages.get(opportunity.stage_id),
            customer_name=customers.get(opportunity.customer_id),
            owner_name=owners.get(opportunity.owner_id) if opportunity.owner_id else None,
            item_count=counts.get(opportunity.id, 0),
            loss_reason_name=loss_reason_name,
        )
    )


@router.get("/opportunities/{opportunity_id}/overview")
async def opportunity_overview(
    opportunity_id: int,
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    """商机 360 概览（03-API §11）。

    详情页要同时显示：阶段进度、需求明细汇总、报价/订单/跟进/任务的关联情况。
    前端原本要发好几个请求才拼得出来；这里一次聚合，
    每个板块给"数量 + 最近几条"，点进各标签页再拉完整分页。

    金额口径说明：需求明细的 `target_price` 是**客户目标价**，
    不等于最终报价，所以这里只汇总数量，金额仍以报价为准 ——
    把目标价当收入显示出去会误导。
    """
    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id)
    stages = await svc.stage_map(session)

    from app.modules.followup.model import FollowUp
    from app.modules.followup.visibility import followup_visibility_filter
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.task.model import Task

    # 商机可见**只**证明能看到这条商机（第十二批 12.1）。概览里的报价/订单/任务/跟进
    # 要**分别**按各自模块的查看权限与数据范围过滤 —— 从前这里只按 opportunity_id 取数，
    # 于是"只有商机权限"的账号也能从概览读到别人报价的单号与金额（实测 73123 完整返回），
    # "本人范围"的业务员同样被穿透；而且"数量"连已删报价都算，与列表对不上。
    def allowed(code: str) -> bool:
        return "admin" in user.roles or user.has(code)

    scope = await scoped_owner_ids(session, user)  # None = 全部可见
    blocked: list[str] = []

    async def scoped_stmt(model, *conds, need: str, block_key: str):
        """按「模块权限 + 数据范围」过滤；无权限返回 None，并把板块名记进 `blocked`。"""
        if not allowed(need):
            blocked.append(block_key)
            return None
        stmt = select(model).where(*conds)
        if scope is not None:
            stmt = stmt.where(model.owner_id.in_(scope))
        return stmt

    async def count_of(stmt) -> int:
        return int(
            (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        )

    items = await svc.list_items(session, opportunity_id)
    stage_history = (
        await session.execute(
            select(OpportunityStageHistory)
            .where(OpportunityStageHistory.opportunity_id == opportunity_id)
            .order_by(OpportunityStageHistory.id.desc())
            .limit(10)
        )
    ).scalars().all()

    # ---- 报价：过滤**先于**取最近几条，数量与列表共用同一套条件（12.1）----
    quote_stmt = await scoped_stmt(
        Quote, Quote.opportunity_id == opportunity_id, Quote.deleted_at.is_(None),
        need="quote:view", block_key="quotes",
    )
    if quote_stmt is None:
        quote_total, quotes = None, []
    else:
        quote_total = await count_of(quote_stmt)
        quotes = (
            await session.execute(quote_stmt.order_by(Quote.id.desc()).limit(5))
        ).scalars().all()

    # ---- 订单 ----
    order_stmt = await scoped_stmt(
        SalesOrder, SalesOrder.opportunity_id == opportunity_id,
        need="order:view", block_key="orders",
    )
    if order_stmt is None:
        order_total = None
    else:
        order_total = await count_of(order_stmt)

    # ---- 跟进：还要过"系统过程记录"的可见性（来源单据得看得见）----
    if not allowed("followup:view"):
        blocked.append("followups")
        followup_total, followups = None, []
    else:
        f_conds = [FollowUp.opportunity_id == opportunity_id,
                   await followup_visibility_filter(session, user)]
        followup_total = int(
            (await session.execute(select(func.count(FollowUp.id)).where(*f_conds))).scalar_one()
        )
        followups = (
            await session.execute(
                select(FollowUp).where(*f_conds).order_by(FollowUp.id.desc()).limit(5)
            )
        ).scalars().all()

    # ---- 任务 ----
    task_stmt = await scoped_stmt(
        Task, Task.opportunity_id == opportunity_id, need="task:view", block_key="tasks",
    )
    if task_stmt is None:
        task_total, open_task_total, tasks = None, None, []
    else:
        task_total = await count_of(task_stmt)
        open_task_total = int(
            (
                await session.execute(
                    select(func.count(Task.id)).where(
                        Task.opportunity_id == opportunity_id,
                        Task.status.in_(("pending", "doing")),
                        *([Task.owner_id.in_(scope)] if scope is not None else []),
                    )
                )
            ).scalar_one()
        )
        tasks = (
            await session.execute(task_stmt.order_by(Task.id.desc()).limit(5))
        ).scalars().all()

    # 报价金额要看当前版本，得再查一次 quote_versions
    current_version_ids = {row.current_version_id for row in quotes if row.current_version_id}
    version_amounts: dict[int, float] = {}
    version_nos: dict[int, int] = {}
    if current_version_ids:
        from app.modules.quote.model import QuoteVersion

        for version_id, version_no, amount in (
            await session.execute(
                select(QuoteVersion.id, QuoteVersion.version_no, QuoteVersion.total_amount).where(
                    QuoteVersion.id.in_(current_version_ids)
                )
            )
        ).all():
            version_amounts[int(version_id)] = float(amount or 0)
            version_nos[int(version_id)] = int(version_no)

    customers, owners, _ = await svc.enrichment(session, [opportunity])
    return ok(
        {
            "opportunity": svc.serialize_opportunity(
                opportunity,
                stage=stages.get(opportunity.stage_id),
                customer_name=customers.get(opportunity.customer_id),
                owner_name=owners.get(opportunity.owner_id) if opportunity.owner_id else None,
                item_count=len(items),
            ),
            "counts": {
                "items": len(items),
                "item_quantity": float(sum(item["quantity"] for item in items)),
                # null = 这个板块**无权限查看**（第十二批 12.1）。前端据此显示
                # 「无权限查看」，别把"无权限"显示成"没有数据"。
                "quotes": quote_total,
                "orders": order_total,
                "followups": followup_total,
                "tasks": task_total,
                "open_tasks": open_task_total,
                "stage_changes": len(stage_history),
            },
            # 无权限的板块名（quotes / orders / followups / tasks）
            "blocked": blocked,
            "stage_history": [
                svc.serialize_stage_history_row(row, stages) for row in stage_history
            ],
            "items": items,
            "quotes": [
                {
                    "id": row.id,
                    "quote_no": row.quote_no,
                    "status": row.status,
                    "current_version_no": version_nos.get(row.current_version_id or 0),
                    "current_version_amount": version_amounts.get(row.current_version_id or 0),
                    "valid_until": row.valid_until,
                }
                for row in quotes
            ],
            "followups": [
                {
                    "id": row.id,
                    "followup_type": row.followup_type,
                    "content": row.content,
                    "next_action": row.next_action,
                    "task_due_at": row.planned_at,
                    "exemption_reason": row.exemption_reason,
                    "next_task_id": row.next_task_id,
                    "created_at": row.created_at,
                }
                for row in followups
            ],
            "tasks": [
                {
                    "id": row.id,
                    "title": row.title,
                    "status": row.status,
                    "priority": row.priority,
                    "due_at": row.due_at,
                }
                for row in tasks
            ],
        }
    )


@router.patch("/opportunities/{opportunity_id}")
async def update_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id)
    if "primary_contact_id" in payload.model_fields_set:
        await svc.validate_contact(session, customer_id=opportunity.customer_id,
                                   contact_id=payload.primary_contact_id)
    before = svc.serialize_opportunity(opportunity)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(opportunity, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="opportunity",
        business_id=opportunity.id,
        before=before,
        after=svc.serialize_opportunity(opportunity),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_opportunity(opportunity), "已保存")


@router.post("/opportunities/{opportunity_id}/change-stage")
async def change_stage(
    opportunity_id: int,
    payload: StageChange,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id, for_update=True)
    if opportunity.status in ("win", "loss"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已成交或已失单的商机不能改阶段")

    stage = None
    if payload.stage_id:
        stage = await session.get(OpportunityStage, payload.stage_id)
    elif payload.stage_code:
        stage = (
            await session.execute(
                select(OpportunityStage).where(OpportunityStage.code == payload.stage_code)
            )
        ).scalar_one_or_none()
    if stage is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请指定要推进到的阶段")

    await svc.change_stage(
        session, opportunity, to_stage=stage, operator_id=user.id, remark=payload.remark
    )
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="change_stage",
        business_type="opportunity",
        business_id=opportunity.id,
        after={"stage": stage.name, "remark": payload.remark},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_opportunity(opportunity, stage=stage), f"已推进到「{stage.name}」")


@router.post("/opportunities/{opportunity_id}/win")
async def win_opportunity(
    opportunity_id: int,
    payload: OpportunityWin,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    """标记成交（**旧兼容入口**）。

    第十二批 12.2：这个口子从前"不传报价版本也能成交""失单之后还能改成成交"。
    现在它**不再有自己的规则** —— 内部与 `/confirm-win` 走同一套业务服务
    （报价归属与当前版本、已发送/已接受、审批、有效期、客户确认、建单权限、转订单）。
    """
    return await _confirm_win_core(
        opportunity_id=opportunity_id,
        win_quote_version_id=payload.win_quote_version_id,
        delivery_date=None,
        remark=payload.remark,
        action="win",
        request=request,
        user=user,
        session=session,
    )


async def _confirm_win_core(
    *,
    opportunity_id: int,
    win_quote_version_id: int | None,
    delivery_date,
    remark: str | None,
    action: str,
    request: Request,
    user: CurrentUser,
    session: AsyncSession,
):
    """确认成交并生成订单 —— **两个成交入口的唯一实现**（第十二批 12.2）。

    `/confirm-win`（正式）与 `/win`（旧兼容）都调这里。旧口子从前"不传报价版本
    也能成交""失单后还能改成成交"，现在统一按同一口径：报价归属与当前版本、
    已发送/已接受、审批、有效期、客户确认、建单权限、转订单。

    一个动作完成：校验成交版本 → 商机标记成交 → 版本转订单。
    幂等：商机已成交不重复改；版本已转过单直接返回已有订单（重试安全）。
    """
    from app.modules.order import service as order_svc
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.quote import service as quote_svc, lifecycle as quote_lifecycle

    if not user.has("order:manage"):
        raise AppError(ErrorCode.FORBIDDEN, "确认成交并建单需要订单管理权限", 403)

    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id)
    opportunity = (await session.execute(select(Opportunity).where(Opportunity.id == opportunity.id)
                   .with_for_update().execution_options(populate_existing=True))).scalar_one()
    await svc.get_visible_opportunity(session, user, opportunity_id)
    # 已失单的商机不能借任何一个成交入口"越过状态"（第十二批 12.2）：
    # 失单要有明确的重新激活流程；否则失单原因与"已成交"会同时挂在这一行上。
    if opportunity.status == "loss":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该商机已失单，不能直接成交；请先「重新激活」再走成交",
            422,
        )

    # ---- 1. 定位成交版本：显式指定优先，否则取该商机下已发送/已接受的最新报价 ----
    if win_quote_version_id:
        version = await session.get(QuoteVersion, win_quote_version_id)
        if version is None:
            raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
    else:
        version = (
            await session.execute(
                select(QuoteVersion)
                .join(Quote, Quote.id == QuoteVersion.quote_id)
                .where(
                    Quote.opportunity_id == opportunity.id,
                    Quote.deleted_at.is_(None),
                    Quote.status.in_(["accepted", "sent"]),
                    QuoteVersion.id == Quote.current_version_id,
                    QuoteVersion.sent_at.is_not(None),
                )
                .order_by(QuoteVersion.id.desc())
                .limit(1)
            )
        ).scalars().first()
        if version is None:
            raise AppError(
                ErrorCode.REQUIRED_FIELD_MISSING,
                "该商机下没有已发送/已接受的报价版本，无法确认成交",
            )

    version = await quote_svc.get_visible_version(session, user, version.id, for_update=True)
    quote = await quote_svc.get_visible_quote(session, user, version.quote_id)

    # ---- 2. 校验（方案 §5：成交报价属于该客户和商机，审批及有效性符合规则）----
    if quote.opportunity_id != opportunity.id or quote.customer_id != opportunity.customer_id:
        raise AppError(ErrorCode.PARAM_ERROR, "该报价版本不属于此商机/客户")
    quote_lifecycle.ensure_current_version(quote, version)
    if opportunity.status == "win" and opportunity.win_quote_version_id != version.id:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该商机已成交，不能通过重复确认改换成交版本", 422)
    if quote.status not in ("accepted", "sent"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"报价当前状态「{quote.status}」不可成交（需已发送或客户已接受）",
        )
    if version.approval_status != "approved":
        raise AppError(ErrorCode.APPROVAL_PENDING, "该报价版本未通过审批，不能成交", 422)
    # 有效期一律走 `quote_is_expired()`（内部取**业务日期/北京时间**）——
    # 与上面旧的「标记成交」入口共用一个判据，杜绝两处各写一段日期逻辑又慢慢漂开。
    if quote_svc.quote_is_expired(quote.valid_until):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"报价已过有效期（{quote.valid_until}），请先刷新版本再成交",
        )

    # ---- 3. 客户确认：已发送未接受的，此动作即视为客户接受 ----
    # 注意：quotes 表没有 accepted_at 列（该列在 quote_versions 上），
    # 赋值给不存在的 ORM 属性会被 SQLAlchemy 静默丢弃
    quote_already_accepted = version.accepted_at is not None
    await quote_lifecycle.accept_version(session, quote=quote, version=version,
                                         operator_id=user.id, ip=client_ip(request))

    # ---- 4. 商机标记成交（幂等：已成交不重复改阶段）----
    already_won = opportunity.status == "win"
    if not already_won:
        stage = await svc.get_won_stage(session)
        if stage is None:
            raise AppError(ErrorCode.SYSTEM_ERROR, "未配置成交阶段", 500)
        await svc.change_stage(
            session, opportunity, to_stage=stage, operator_id=user.id,
            remark="确认成交", allow_terminal=True,
        )
        opportunity.status = "win"
        opportunity.win_quote_version_id = version.id
        await session.flush()

    # ---- 5. 转订单（版本级幂等：重试返回已有订单）----
    already_ordered = False
    try:
        order = await order_svc.create_order_from_quote(
            session,
            version=version,
            user_id=user.id,
            delivery_date=delivery_date,
            remark=remark,
        )
    except AppError as exc:
        if exc.code != ErrorCode.DUPLICATE_CONVERT:
            raise
        already_ordered = True
        order = (
            await session.execute(
                select(SalesOrder).where(SalesOrder.quote_version_id == version.id)
            )
        ).scalars().first()
        if order is None:
            raise

    await write_audit(
        session,
        operator_id=user.id,
        action=action,
        business_type="opportunity",
        business_id=opportunity.id,
        after={
            "quote_version_id": version.id,
            "order_id": order.id,
            "already_won": already_won,
            "already_ordered": already_ordered,
        },
        ip=client_ip(request),
    )
    await session.commit()
    # 转单时 service 里写了自动跟进留痕 + 主管通知（followup 事件），这里统一投递
    from app.modules.notification import service as notification_service

    await notification_service.dispatch_pending(session)
    return ok(
        {
            "opportunity_id": opportunity.id,
            "quote_id": quote.id,
            "win_quote_version_id": version.id,
            "order_id": order.id,
            "order_no": order.order_no,
            "already_won": already_won,
            "already_accepted": quote_already_accepted,
            "already_ordered": already_ordered,
        },
        "已确认成交并生成订单" if not (already_won and already_ordered) else "该版本此前已成交建单，返回既有订单",
    )


@router.post("/opportunities/{opportunity_id}/confirm-win")
async def confirm_win_and_create_order(
    opportunity_id: int,
    payload: OpportunityConfirmWin,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    """确认成交并生成订单（方案 §5 / A13）。

    实现见 `_confirm_win_core` —— 自第十二批 12.2 起与旧入口 `/win` 共用同一套。
    """
    return await _confirm_win_core(
        opportunity_id=opportunity_id,
        win_quote_version_id=payload.win_quote_version_id,
        delivery_date=payload.delivery_date,
        remark=payload.remark,
        action="confirm_win",
        request=request,
        user=user,
        session=session,
    )


@router.post("/opportunities/{opportunity_id}/lose")
async def lose_opportunity(
    opportunity_id: int,
    payload: OpportunityLose,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id, for_update=True)
    if opportunity.status == "loss":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该商机已经失单")
    reason = await session.get(LossReason, payload.loss_reason_id)
    if reason is None:
        raise AppError(ErrorCode.NOT_FOUND, "失单原因不存在", 404)

    # ---- 已成交且有订单时，必须先处理订单（2026-10-09 与主人确认）----
    #
    # 从前这里**完全不看订单**：商机成交后已生成订单（待生产），仍然可以直接标失单，
    # 于是商机变成"失单"、订单却继续待生产、成交报价依据还留着 —— 三处状态互相矛盾，
    # 事后没人说得清这单到底是成了还是没成。
    #
    # 口径：成交是**已发生的商业事实**，要撤回就得先把下游（订单）处理掉
    # （取消订单），而不是让商机状态单方面翻脸。所以这里**拦住并指路**，
    # 由人工决定订单怎么处理 —— 不自动替人取消订单，那会静默作废一张真单据。
    #
    # 只看**未取消**的订单：已取消的订单不构成阻碍（那正是"已经处理过了"）。
    from app.modules.order.model import SalesOrder

    live_orders = (
        await session.execute(
            select(SalesOrder.order_no)
            .where(
                SalesOrder.opportunity_id == opportunity_id,
                SalesOrder.status != "cancelled",
            )
            .order_by(SalesOrder.id)
        )
    ).scalars().all()
    if live_orders:
        shown = "、".join(live_orders[:3])
        more = f" 等 {len(live_orders)} 张" if len(live_orders) > 3 else ""
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该商机已生成订单（{shown}{more}），不能直接标失单；"
            "请先在订单中心处理（取消）这些订单，再回来标失单",
            422,
        )

    # ---- 保留最后阶段 + 冻结停留时间 + 补一条失单历史（2026-10-09 与主人确认）----
    #
    # 从前这里**什么都不记**：阶段不变、历史不加、原阶段那条历史的 `left_at`
    # 一直是空 —— 于是"这单死之前卡在哪一步、卡了多久"永远查不出来，
    # 失单原因分析也就只能看一个原因，看不到路径。
    #
    # 现在的口径：
    #   - **阶段不动**（保留最后阶段，供"卡在哪个阶段丢得最多"这类归因）；
    #   - 把当前这条未关闭的历史行**关闭**并冻结 `duration_seconds`；
    #   - 补一条 `from = to = 当前阶段` 的历史行，remark 记失单原因，作为**失单标记**。
    #
    # 为什么 `from = to`：它不是"阶段推进"，而是"在这个阶段终止"。这样
    #   - 漏斗按 `distinct to_stage_id` 统计到达过的阶段 → 不受影响；
    #   - 成交周期只统计 `status='win'` 的商机 → 这些行只出现在失单商机上 → 不受影响；
    #   - 想知道"在哪一步死的"直接读这条标记行即可。
    # 需要算纯停留时长的读法请加 `duration_seconds is not null`（标记行的该列为空）。
    now = datetime.now(UTC)
    current_hist = (
        await session.execute(
            select(OpportunityStageHistory)
            .where(
                OpportunityStageHistory.opportunity_id == opportunity_id,
                OpportunityStageHistory.left_at.is_(None),
            )
            .order_by(OpportunityStageHistory.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if current_hist is not None:
        current_hist.left_at = now
        entered = current_hist.entered_at
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=UTC)
        current_hist.duration_seconds = int((now - entered).total_seconds())
    if opportunity.stage_id is not None:
        session.add(
            OpportunityStageHistory(
                opportunity_id=opportunity_id,
                from_stage_id=opportunity.stage_id,
                to_stage_id=opportunity.stage_id,
                operator_id=user.id,
                remark=f"失单（{reason.name}）",
                entered_at=now,
            )
        )

    opportunity.status = "loss"
    opportunity.loss_reason_id = reason.id
    opportunity.loss_remark = payload.remark
    opportunity.reopen_at = payload.reopen_at
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="lose",
        business_type="opportunity",
        business_id=opportunity.id,
        after={"loss_reason": reason.name, "remark": payload.remark},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        svc.serialize_opportunity(opportunity, loss_reason_name=reason.name),
        f"商机已失单（{reason.name}）",
    )


@router.post("/opportunities/{opportunity_id}/reopen")
async def reopen_opportunity(
    opportunity_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id, for_update=True)
    if opportunity.status != "loss":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "只有失单的商机可以重新激活")
    stage = await svc.get_first_stage(session)
    opportunity.status = "open"
    opportunity.loss_reason_id = None
    opportunity.reopen_at = None
    await svc.change_stage(
        session, opportunity, to_stage=stage, operator_id=user.id, remark="失单后重新激活"
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="reopen",
        business_type="opportunity",
        business_id=opportunity.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_opportunity(opportunity, stage=stage), "商机已重新激活")


@router.get("/opportunities/{opportunity_id}/stage-history")
async def stage_history(
    opportunity_id: int,
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_opportunity(session, user, opportunity_id)
    stages = await svc.stage_map(session)
    rows = (
        await session.execute(
            select(OpportunityStageHistory)
            .where(OpportunityStageHistory.opportunity_id == opportunity_id)
            .order_by(OpportunityStageHistory.id.desc())
        )
    ).scalars().all()
    return ok([svc.serialize_stage_history_row(row, stages) for row in rows])


@router.delete("/opportunities/{opportunity_id}")
async def delete_opportunity(
    opportunity_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id, for_update=True)
    before = svc.serialize_opportunity(opportunity)
    opportunity.deleted_at = datetime.now(UTC)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="opportunity",
        business_id=opportunity.id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "商机已删除")


# ---------------------------------------------------------------- 需求明细

@router.get("/opportunities/{opportunity_id}/items")
async def list_items(
    opportunity_id: int,
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_opportunity(session, user, opportunity_id)
    return ok(await svc.list_items(session, opportunity_id))


@router.post("/opportunities/{opportunity_id}/items")
async def create_item(
    opportunity_id: int,
    payload: OpportunityItemCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_opportunity(session, user, opportunity_id)
    # SKU 先验存在（第十二批 12.5）：不验就是撞外键的 500，用户看不出哪条错了
    await svc.ensure_skus_exist(session, [payload.sku_id])
    item = OpportunityItem(**payload.model_dump(), opportunity_id=opportunity_id)
    session.add(item)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create_item",
        business_type="opportunity",
        business_id=opportunity_id,
        after=svc.serialize_item(item),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_item(item), "需求明细已添加")


@router.patch("/opportunity-items/{item_id}")
async def update_item(
    item_id: int,
    payload: OpportunityItemUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    item = await svc.get_visible_item(session, user, item_id)
    before = svc.serialize_item(item)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update_item",
        business_type="opportunity",
        business_id=item.opportunity_id,
        before=before,
        after=svc.serialize_item(item),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_item(item), "已保存")


@router.delete("/opportunity-items/{item_id}")
async def delete_item(
    item_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    item = await svc.get_visible_item(session, user, item_id)
    before = svc.serialize_item(item)
    await session.delete(item)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete_item",
        business_type="opportunity",
        business_id=item.opportunity_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "需求明细已删除")


# ------------------------------------------------- 03-API §12 新增的四个接口


@router.post("/opportunities/{opportunity_id}/items/batch")
async def replace_items_batch(
    opportunity_id: int,
    payload: OpportunityItemsBatch,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    """整批替换需求明细（界面上「保存整版」用）。

    先清空再写入，保证不出现半新半旧的明细。
    """
    await svc.get_visible_opportunity(session, user, opportunity_id)
    created = await svc.replace_items(
        session,
        opportunity_id=opportunity_id,
        items=[item.model_dump() for item in payload.items],
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="replace_items",
        business_type="opportunity",
        business_id=opportunity_id,
        after={"count": len(created)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {"count": len(created), "items": [svc.serialize_item(row) for row in created]},
        f"已保存 {len(created)} 条需求明细",
    )


@router.post("/opportunities/{opportunity_id}/items/copy-from/{source_opportunity_id}")
async def copy_items(
    opportunity_id: int,
    source_opportunity_id: int,
    payload: OpportunityItemCopy,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    """从另一个商机复制需求明细（同客户重复采购时最常用）。"""
    await svc.get_visible_opportunity(session, user, opportunity_id)
    await svc.get_visible_opportunity(session, user, source_opportunity_id)
    if opportunity_id == source_opportunity_id:
        raise AppError(ErrorCode.PARAM_ERROR, "不能从自己复制需求明细", 422)

    result = await svc.copy_items_from(
        session,
        target_opportunity_id=opportunity_id,
        source_opportunity_id=source_opportunity_id,
        sku_ids=payload.sku_ids,
        on_conflict=payload.on_conflict,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="copy_items",
        business_type="opportunity",
        business_id=opportunity_id,
        after={**result, "source_opportunity_id": source_opportunity_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, f"新增 {result['added']} 条、覆盖 {result['replaced']} 条、跳过 {result['skipped']} 条")


@router.post("/opportunities/{opportunity_id}/recommend-products")
async def recommend_products(
    opportunity_id: int,
    payload: RecommendProductsRequest,
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    """需求商品推荐。

    按"该客户历史成交过的 SKU"排序、公司整体成交频次补足，
    每条带推荐理由与来源。**不是模型推荐，也不假装是**：
    等接了推荐模型再换实现，接口形状不变。
    """
    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id)
    return ok(
        await svc.recommend_products(
            session, opportunity=opportunity, limit=payload.limit, keyword=payload.keyword
        )
    )


# ------------------------------------------------- 03-API §11 新增的两个接口


@router.post("/opportunities/{opportunity_id}/assign")
async def assign_opportunity(
    opportunity_id: int,
    payload: OpportunityAssign,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    """变更商机负责人。

    只动 owner_id，`created_by` 保持原样（02-ER §21：owner_id 可变，
    created_by 不覆盖）——否则"谁创建的"这条审计线索就断了。
    """
    opportunity = await svc.get_visible_opportunity(session, user, opportunity_id, for_update=True)
    await svc.assert_owner_active(session, payload.owner_id)

    before_owner = opportunity.owner_id
    opportunity.owner_id = payload.owner_id
    await write_audit(
        session,
        operator_id=user.id,
        action="assign",
        business_type="opportunity",
        business_id=opportunity.id,
        before={"owner_id": before_owner},
        after={"owner_id": payload.owner_id, "remark": payload.remark},
        ip=client_ip(request),
    )
    await session.commit()
    stage = (await svc.stage_map(session)).get(opportunity.stage_id)
    return ok(svc.serialize_opportunity(opportunity, stage=stage), "负责人已变更")


@router.post("/opportunities/{opportunity_id}/clone")
async def clone_opportunity(
    opportunity_id: int,
    payload: OpportunityClone,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    """复制商机（复制"需求"，不复制"结果"）。

    新商机不带成交/失单结论、不带金额，阶段回到初始阶段。
    """
    source = await svc.get_visible_opportunity(session, user, opportunity_id)
    clone = await svc.clone_opportunity(
        session,
        source=source,
        user=user,
        title=payload.title,
        customer_id=payload.customer_id,
        owner_id=payload.owner_id,
        expected_close_date=payload.expected_close_date,
        copy_items=payload.copy_items,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="clone",
        business_type="opportunity",
        business_id=clone.id,
        before={"source_opportunity_id": source.id},
        after=svc.serialize_opportunity(clone),
        ip=client_ip(request),
    )
    await session.commit()
    stage = (await svc.stage_map(session)).get(clone.stage_id)
    return ok(
        svc.serialize_opportunity(clone, stage=stage),
        f"已从「{source.title}」复制出新的商机",
    )
