"""商机业务逻辑。"""

from datetime import UTC, date, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.timebase import today_business
from app.modules.customer.model import Contact, Customer
from app.modules.opportunity.model import (
    LossReason,
    Opportunity,
    OpportunityItem,
    OpportunityStage,
    OpportunityStageHistory,
)
from app.modules.product.model import Sku
from app.modules.user.model import User


def _number(value) -> float | None:
    return None if value is None else float(value)


async def get_first_stage(session: AsyncSession) -> OpportunityStage:
    """取「启用中的**普通**阶段」里排序最靠前的那个，作为新商机的初始阶段。

    必须排除成交/失单（第十二批 12.3）：否则管理员把成交阶段排到最前，
    新建的商机一出生就停在「成交」上（状态却还写着「进行中」）。
    """
    stage = (
        await session.execute(
            select(OpportunityStage)
            .where(
                OpportunityStage.status == "active",
                OpportunityStage.is_win.is_(False),
                OpportunityStage.is_loss.is_(False),
            )
            .order_by(OpportunityStage.sequence.asc(), OpportunityStage.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if stage is None:
        raise AppError(
            ErrorCode.SYSTEM_ERROR,
            "没有可用的普通阶段（成交/失单不算初始阶段），请先在阶段配置里启用一个",
            500,
        )
    return stage


async def get_won_stage(session: AsyncSession) -> OpportunityStage | None:
    """取成交阶段。

    配置侧保证只有一个（第十二批 12.3 在保存时拦重复标记）；这里再兜一层：
    万一库里已有历史脏配置（两个成交标记），也只取第一个，不让业务入口抛 500。
    """
    return (
        await session.execute(
            select(OpportunityStage)
            .where(OpportunityStage.is_win.is_(True))
            .order_by(OpportunityStage.sequence.asc(), OpportunityStage.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def lock_stages(session: AsyncSession) -> None:
    """锁住阶段配置（整张小表）。保存阶段前调用，把并发配置串成一条队。

    少了它，两个管理员各自"检查通过"、提交后合成一个冲突配置（第十二批 12.3）。
    """
    await session.execute(select(OpportunityStage.id).with_for_update())


async def ensure_stage_config_ok(session: AsyncSession) -> None:
    """保存阶段配置**之前**，确认"存完之后整体还能被业务正确使用"（第十二批 12.3）。

    当前业务按**单一销售流程**运行：成交阶段必须明确且唯一 —— 取成交阶段的代码
    只会用一个。若配置里出现两个"成交标记"的阶段，业务入口一找就找到两条直接
    500，而管理员那边保存是"成功"的，最难查。

    规则：
    - 成交标记（`is_win`）**最多一个**；
    - 同一个阶段**不能既是成交又是失单**。

    并发：调用方必须先 `lock_stages()`，否则两个管理员各自检查都通过、
    提交后仍会合成冲突。
    """
    rows = (
        await session.execute(select(OpportunityStage).order_by(OpportunityStage.id.asc()))
    ).scalars().all()
    wins = [row for row in rows if row.is_win]
    if len(wins) > 1:
        names = "、".join(f"「{row.name}」" for row in wins)
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"只能有一个成交阶段，现在是 {len(wins)} 个（{names}）；"
            f"请先取消多余的成交标记再保存",
            422,
        )
    for row in rows:
        if row.is_win and row.is_loss:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"阶段「{row.name}」不能同时是成交和失单",
                422,
            )


async def stage_map(session: AsyncSession) -> dict[int, OpportunityStage]:
    rows = (await session.execute(select(OpportunityStage))).scalars().all()
    return {stage.id: stage for stage in rows}


def serialize_stage(stage: OpportunityStage) -> dict:
    return {
        "id": stage.id,
        "code": stage.code,
        "name": stage.name,
        "sequence": stage.sequence,
        "is_win": stage.is_win,
        "is_loss": stage.is_loss,
        "status": stage.status,
    }


def serialize_stage_history_row(
    row: "OpportunityStageHistory", stages: dict[int, OpportunityStage]
) -> dict:
    """阶段历史的一行（第十二批 12.4）。

    `*_stage_missing`：这一步引用的阶段**现在不在配置里**（早年被物理删除留下的）。
    按口径**不猜**一个名字补进去，但要如实说出来 —— 只给 `null` 的话前端只能显示空白，
    看着像"系统把数据弄丢了"。新发生的不会再出现这种行：删除入口已改成停用。
    """
    from_stage = stages.get(row.from_stage_id) if row.from_stage_id else None
    to_stage = stages.get(row.to_stage_id) if row.to_stage_id else None
    return {
        "id": row.id,
        "from_stage": from_stage.name if from_stage else None,
        "from_stage_missing": row.from_stage_id is not None and from_stage is None,
        "to_stage": to_stage.name if to_stage else None,
        "to_stage_missing": row.to_stage_id is not None and to_stage is None,
        "remark": row.remark,
        "entered_at": row.entered_at,
        "left_at": row.left_at,
        "duration_seconds": row.duration_seconds,
    }


def serialize_opportunity(
    opportunity: Opportunity,
    *,
    stage: OpportunityStage | None = None,
    customer_name: str | None = None,
    owner_name: str | None = None,
    item_count: int = 0,
    loss_reason_name: str | None = None,
) -> dict:
    return {
        "id": opportunity.id,
        "customer_id": opportunity.customer_id,
        "customer_name": customer_name,
        "primary_contact_id": opportunity.primary_contact_id,
        "title": opportunity.title,
        "source": opportunity.source,
        "stage_id": opportunity.stage_id,
        "stage_name": stage.name if stage else None,
        "stage_code": stage.code if stage else None,
        "expected_amount": _number(opportunity.expected_amount),
        "currency": opportunity.currency,
        "expected_close_date": opportunity.expected_close_date,
        "owner_id": opportunity.owner_id,
        "owner_name": owner_name,
        "competitor": opportunity.competitor,
        "risk_level": opportunity.risk_level,
        "next_action": opportunity.next_action,
        "status": opportunity.status,
        "loss_reason_id": opportunity.loss_reason_id,
        "loss_reason_name": loss_reason_name,
        "loss_remark": opportunity.loss_remark,
        "reopen_at": opportunity.reopen_at,
        "item_count": item_count,
        "created_at": opportunity.created_at,
        "updated_at": opportunity.updated_at,
    }


def serialize_item(item: OpportunityItem, sku: Sku | None = None) -> dict:
    return {
        "id": item.id,
        "opportunity_id": item.opportunity_id,
        "sku_id": item.sku_id,
        "sku_code": sku.sku_code if sku else None,
        "sku_name": sku.name if sku else None,
        "product_id": sku.product_id if sku else None,
        "specification": item.specification or (sku.specification if sku else None),
        "color": item.color or (sku.color if sku else None),
        "quantity": _number(item.quantity),
        "target_price": _number(item.target_price),
        "currency": item.currency,
        "package_requirement": item.package_requirement,
        "delivery_date": item.delivery_date,
        "destination": item.destination,
        "remark": item.remark,
        "moq": sku.moq if sku else None,
        "unit": sku.unit if sku else None,
    }


def build_opportunity_stmt(
    keyword: str | None = None,
    stage_id: int | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    customer_id: int | None = None,
) -> Select:
    stmt = select(Opportunity).where(Opportunity.deleted_at.is_(None))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(Opportunity.title.ilike(like))
    if stage_id:
        stmt = stmt.where(Opportunity.stage_id == stage_id)
    if status:
        stmt = stmt.where(Opportunity.status == status)
    if owner_id is not None:
        stmt = stmt.where(Opportunity.owner_id == owner_id)
    if customer_id:
        stmt = stmt.where(Opportunity.customer_id == customer_id)
    return stmt.order_by(Opportunity.id.desc())


async def apply_data_scope(
    stmt: Select, user: CurrentUser, session: AsyncSession
) -> Select:
    """`department_and_sub` 取本部门及所有下级部门，见 app/core/data_scope.py。"""
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return stmt
    return stmt.where(Opportunity.owner_id.in_(owner_ids))


async def get_opportunity_or_404(session: AsyncSession, opportunity_id: int) -> Opportunity:
    opportunity = await session.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)
    return opportunity


async def get_visible_opportunity(
    session: AsyncSession, user: CurrentUser, opportunity_id: int, *, for_update: bool = False
) -> Opportunity:
    """取商机并校验数据范围。

    列表接口一直按 `owner_id` 过滤，但详情/改/删此前只判断存在 ——
    实测业务员改个 id 就能看和改别人的商机。读与写必须同一口径。
    """
    if for_update:
        opportunity = (await session.execute(select(Opportunity).where(
            Opportunity.id == opportunity_id
        ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if opportunity is None or opportunity.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)
    else:
        opportunity = await get_opportunity_or_404(session, opportunity_id)
    await ensure_in_scope(
        session, user, owner_id=opportunity.owner_id, label="商机"
    )
    return opportunity


async def validate_contact(session: AsyncSession, *, customer_id: int, contact_id: int | None) -> None:
    """商机联系人必须是该客户的有效联系人，创建/修改/复制共用。"""
    if contact_id is None:
        return
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
    if contact.customer_id != customer_id:
        raise AppError(ErrorCode.PARAM_ERROR, "联系人不属于该商机客户", 422)


async def get_visible_item(
    session: AsyncSession, user: CurrentUser, item_id: int
) -> OpportunityItem:
    """取需求明细并校验其所属商机在数据范围内（明细自己没有负责人）。"""
    item = await get_item_or_404(session, item_id)
    parent = await get_opportunity_or_404(session, item.opportunity_id)
    await ensure_in_scope(session, user, owner_id=parent.owner_id, label="商机")
    return item


async def enrichment(
    session: AsyncSession, opportunities: list[Opportunity]
) -> tuple[dict[int, str], dict[int, str], dict[int, int]]:
    """批量取客户名、负责人名、需求条数，避免列表页 N+1 查询。"""
    customer_ids = {o.customer_id for o in opportunities}
    owner_ids = {o.owner_id for o in opportunities if o.owner_id}
    opp_ids = [o.id for o in opportunities]

    customers: dict[int, str] = {}
    if customer_ids:
        rows = (
            await session.execute(
                select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
            )
        ).all()
        customers = {int(cid): name for cid, name in rows}

    owners: dict[int, str] = {}
    if owner_ids:
        rows = (
            await session.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
        ).all()
        owners = {int(uid): name for uid, name in rows}

    counts: dict[int, int] = {}
    if opp_ids:
        rows = (
            await session.execute(
                select(OpportunityItem.opportunity_id, func.count(OpportunityItem.id))
                .where(OpportunityItem.opportunity_id.in_(opp_ids))
                .group_by(OpportunityItem.opportunity_id)
            )
        ).all()
        counts = {int(oid): int(count) for oid, count in rows}

    return customers, owners, counts


async def create_opportunity_from_lead(
    session: AsyncSession,
    *,
    customer_id: int,
    primary_contact_id: int | None,
    title: str,
    owner_id: int | None,
    created_by: int | None,
    expected_amount: float | None = None,
    expected_close_date: date | None = None,
) -> Opportunity:
    stage = await get_first_stage(session)
    opportunity = Opportunity(
        customer_id=customer_id,
        primary_contact_id=primary_contact_id,
        title=title,
        source="线索转化",
        stage_id=stage.id,
        owner_id=owner_id,
        status="open",
        expected_amount=expected_amount,
        expected_close_date=expected_close_date,
        created_by=created_by,
    )
    session.add(opportunity)
    await session.flush()
    session.add(
        OpportunityStageHistory(
            opportunity_id=opportunity.id,
            from_stage_id=None,
            to_stage_id=stage.id,
            operator_id=created_by,
            remark="创建商机",
            entered_at=datetime.now(UTC),
        )
    )
    return opportunity


async def change_stage(
    session: AsyncSession,
    opportunity: Opportunity,
    *,
    to_stage: OpportunityStage,
    operator_id: int,
    remark: str | None = None,
    allow_terminal: bool = False,
) -> None:
    """切换阶段：先关闭当前停留记录，再开一条新的，保证停留时长可统计。

    `allow_terminal=False`（默认）**不许**把阶段直接切到成交/失单上（第十二批 12.2）：
    那两条路只能由正式出口走（成交要校验报价依据、建单权限并生成订单；失单要写
    失单原因）。从前普通「推进阶段」能直达成交 —— 实测一条没有任何报价的商机
    也能被推成「已成交」，整套成交校验形同虚设。
    """
    if (to_stage.is_win or to_stage.is_loss) and not allow_terminal:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{to_stage.name}」是成交/失单阶段，不能通过「推进阶段」直接切换；"
            f"成交请用「确认成交」，失单请用「标记失单」",
            422,
        )
    now = datetime.now(UTC)
    current = (
        await session.execute(
            select(OpportunityStageHistory)
            .where(
                OpportunityStageHistory.opportunity_id == opportunity.id,
                OpportunityStageHistory.left_at.is_(None),
            )
            .order_by(OpportunityStageHistory.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if current is not None:
        current.left_at = now
        entered = current.entered_at
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=UTC)
        current.duration_seconds = int((now - entered).total_seconds())

    session.add(
        OpportunityStageHistory(
            opportunity_id=opportunity.id,
            from_stage_id=opportunity.stage_id,
            to_stage_id=to_stage.id,
            operator_id=operator_id,
            remark=remark,
            entered_at=now,
        )
    )
    opportunity.stage_id = to_stage.id
    if to_stage.is_win:
        opportunity.status = "win"
    elif to_stage.is_loss:
        opportunity.status = "loss"
    else:
        opportunity.status = "open"


async def list_items(session: AsyncSession, opportunity_id: int) -> list[dict]:
    stmt = (
        select(OpportunityItem, Sku)
        .join(Sku, Sku.id == OpportunityItem.sku_id)
        .where(OpportunityItem.opportunity_id == opportunity_id)
        .order_by(OpportunityItem.id.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [serialize_item(item, sku) for item, sku in rows]


async def get_item_or_404(session: AsyncSession, item_id: int) -> OpportunityItem:
    item = await session.get(OpportunityItem, item_id)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "需求明细不存在", 404)
    return item


async def list_loss_reasons(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(
            select(LossReason).where(LossReason.status == "active").order_by(LossReason.id.asc())
        )
    ).scalars().all()
    return [
        {"id": r.id, "code": r.code, "name": r.name, "category": r.category} for r in rows
    ]


async def touch_last_followup(session: AsyncSession, opportunity: Opportunity) -> None:
    """记录最近跟进时间，供「多久没跟」这类规则使用。"""
    opportunity.updated_at = datetime.now(UTC)


def today() -> date:
    """业务日期（§9.10 复审）：原来取 UTC 日期，北京时间凌晨会差一天。"""
    return today_business()


# ---------------------------------------------------------------- 新增能力
# 以下四块对应 03-API §11 的 clone/assign 与 §12 的 items/batch、
# copy-from、recommend-products。


async def assert_owner_active(session: AsyncSession, owner_id: int | None) -> User | None:
    """校验负责人存在且在职。

    客户转移那边已经这么做了；商机这条链路原来没有校验，
    传一个不存在的 user id 会把商机挂到空负责人上，事后很难查。
    """
    if owner_id is None:
        return None
    owner = await session.get(User, owner_id)
    if owner is None:
        raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={owner_id} 不存在", 404)
    if owner.status != "active":
        raise AppError(
            ErrorCode.PARAM_ERROR, f"负责人「{owner.name}」已停用，不能作为商机负责人", 422
        )
    return owner


async def clone_opportunity(
    session: AsyncSession,
    *,
    source: Opportunity,
    user: CurrentUser,
    title: str | None = None,
    customer_id: int | None = None,
    owner_id: int | None = None,
    expected_close_date: date | None = None,
    copy_items: bool = True,
) -> Opportunity:
    """复制商机。

    复制的是"需求"，不是"结果"，所以刻意不带这些：
      - status / win_quote_version_id / loss_reason_id / loss_remark / reopen_at
        （上一单成交或失单的结论不该成为新单的事实）
      - expected_amount（金额随数量与价格变，让业务重新确认）
      - 阶段回到初始阶段
    需求明细按 `copy_items` 决定是否一起复制。
    """
    await assert_owner_active(session, owner_id)
    from app.modules.customer import service as customer_service
    target_customer_id = customer_id if customer_id is not None else source.customer_id
    await customer_service.get_visible_customer(session, user, target_customer_id)
    # 换客户复制只复制需求内容，原客户的联系人不能跟着新业务走。
    contact_id = source.primary_contact_id if target_customer_id == source.customer_id else None
    await validate_contact(session, customer_id=target_customer_id, contact_id=contact_id)

    first_stage = await get_first_stage(session)
    clone = Opportunity(
        customer_id=target_customer_id,
        primary_contact_id=contact_id,
        title=title or f"{source.title}（复制）",
        source=source.source,
        stage_id=first_stage.id,
        currency=source.currency,
        expected_close_date=expected_close_date or source.expected_close_date,
        owner_id=owner_id if owner_id is not None else source.owner_id,
        competitor=source.competitor,
        risk_level=source.risk_level,
        next_action=source.next_action,
        status="open",
        created_by=user.id,
    )
    session.add(clone)
    await session.flush()

    session.add(
        OpportunityStageHistory(
            opportunity_id=clone.id,
            from_stage_id=None,
            to_stage_id=first_stage.id,
            operator_id=user.id,
            remark=f"由商机 #{source.id} 复制而来",
            entered_at=datetime.now(UTC),
        )
    )

    if copy_items:
        rows = (
            await session.execute(
                select(OpportunityItem).where(OpportunityItem.opportunity_id == source.id)
            )
        ).scalars().all()
        for item in rows:
            session.add(
                OpportunityItem(
                    opportunity_id=clone.id,
                    sku_id=item.sku_id,
                    quantity=item.quantity,
                    target_price=item.target_price,
                    currency=item.currency,
                    specification=item.specification,
                    color=item.color,
                    package_requirement=item.package_requirement,
                    delivery_date=item.delivery_date,
                    destination=item.destination,
                    remark=item.remark,
                )
            )
    await session.flush()
    return clone


async def ensure_skus_exist(session: AsyncSession, sku_ids: list[int | None]) -> None:
    """明细引用的 SKU 必须真实存在且未删除（第十二批 12.5）。

    从前直接落库 → 撞外键 → 500「服务器内部错误」，用户看不出是哪一条错；
    而批量替换又是"先删光再写"，一条坏 SKU 会让**原明细整批消失**、新的又没进去。
    """
    ids = {int(x) for x in sku_ids if x is not None}
    if not ids:
        return
    found = set(
        (
            await session.execute(
                select(Sku.id).where(Sku.id.in_(ids), Sku.deleted_at.is_(None))
            )
        ).scalars().all()
    )
    missing = sorted(ids - found)
    if missing:
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"SKU id={missing[0]} 不存在或已删除，请重新选一个；"
            f"本次共 {len(missing)} 条明细的 SKU 查不到",
            404,
        )


async def replace_items(
    session: AsyncSession, *, opportunity_id: int, items: list[dict]
) -> list[OpportunityItem]:
    """整批替换需求明细。

    与报价版本的 `items/batch` 保持同一语义：先清空再写入，
    这样界面上的"保存整版"是一个原子动作，不会留下半新半旧的明细。

    ⚠️ 但"清空"之前必须**先把整批验完**（第十二批 12.5）：从前一条坏 SKU 会先删掉
    全部原明细、再在外键上炸掉 —— 用户既丢了原数据、又没存上新数据。
    """
    await ensure_skus_exist(session, [item.get("sku_id") for item in items])
    existing = (
        await session.execute(
            select(OpportunityItem).where(OpportunityItem.opportunity_id == opportunity_id)
        )
    ).scalars().all()
    for row in existing:
        await session.delete(row)
    await session.flush()

    created: list[OpportunityItem] = []
    for payload in items:
        item = OpportunityItem(opportunity_id=opportunity_id, **payload)
        session.add(item)
        created.append(item)
    await session.flush()
    return created


async def copy_items_from(
    session: AsyncSession,
    *,
    target_opportunity_id: int,
    source_opportunity_id: int,
    sku_ids: list[int] | None = None,
    on_conflict: str = "skip",
) -> dict:
    """把另一个商机的需求明细复制过来。

    `on_conflict` 决定同一个 SKU 已经存在时怎么办：
      skip    跳过（默认，最安全，不会悄悄改掉已谈好的数量）
      replace 用来源的数量覆盖
      add     追加一条（同一 SKU 分批交货的场景）
    """
    if on_conflict not in ("skip", "replace", "add"):
        raise AppError(
            ErrorCode.PARAM_ERROR, "on_conflict 只能是 skip / replace / add", 422
        )

    source_rows = (
        await session.execute(
            select(OpportunityItem).where(
                OpportunityItem.opportunity_id == source_opportunity_id
            )
        )
    ).scalars().all()
    if sku_ids:
        wanted = set(sku_ids)
        source_rows = [row for row in source_rows if row.sku_id in wanted]

    existing = {
        row.sku_id: row
        for row in (
            await session.execute(
                select(OpportunityItem).where(
                    OpportunityItem.opportunity_id == target_opportunity_id
                )
            )
        ).scalars().all()
    }

    added = skipped = replaced = 0
    for row in source_rows:
        current = existing.get(row.sku_id)
        if current is not None and on_conflict == "skip":
            skipped += 1
            continue
        if current is not None and on_conflict == "replace":
            current.quantity = row.quantity
            current.target_price = row.target_price
            current.specification = row.specification
            current.color = row.color
            current.package_requirement = row.package_requirement
            current.delivery_date = row.delivery_date
            current.destination = row.destination
            replaced += 1
            continue
        session.add(
            OpportunityItem(
                opportunity_id=target_opportunity_id,
                sku_id=row.sku_id,
                quantity=row.quantity,
                target_price=row.target_price,
                currency=row.currency,
                specification=row.specification,
                color=row.color,
                package_requirement=row.package_requirement,
                delivery_date=row.delivery_date,
                destination=row.destination,
                remark=row.remark,
            )
        )
        added += 1
    await session.flush()
    return {"added": added, "replaced": replaced, "skipped": skipped, "source_total": len(source_rows)}


async def recommend_products(
    session: AsyncSession,
    *,
    opportunity: Opportunity,
    limit: int = 10,
    keyword: str | None = None,
) -> list[dict]:
    """需求商品推荐。

    **没有接推荐模型，也不假装有**：这里按"该客户历史成交过的 SKU"排序，
    其次按"公司整体成交频次"补足，每条都带上推荐理由与来源单数，
    业务能看到"为什么推这个"。等接了模型再换掉实现，接口形状不变。
    """
    from app.modules.order.model import SalesOrder, SalesOrderItem

    # 该客户历史订单里出现过的 SKU（按出现次数排序）
    customer_rows = (
        await session.execute(
            select(
                SalesOrderItem.sku_id,
                func.count(SalesOrderItem.id).label("times"),
                func.max(SalesOrderItem.unit_price).label("last_price"),
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
            .where(SalesOrder.customer_id == opportunity.customer_id)
            # 定制件（无 SKU）不能进推荐：sku_id 为 None 时 `int(row.sku_id)`
            # 会直接 TypeError → 500。客户名下只要有一行定制明细，整个"推荐商品"
            # 就崩。推荐的是"可复用的 SKU"，本来也不该包含无 SKU 的定制行。
            .where(SalesOrderItem.sku_id.is_not(None))
            .group_by(SalesOrderItem.sku_id)
            .order_by(func.count(SalesOrderItem.id).desc())
            .limit(limit)
        )
    ).all()
    # 显式记住"哪些来自该客户"，后面补全时不会再改这个集合。
    # （第一版靠列表切片判断，追加数据后含义就变了，是写给自己看的坑。）
    customer_sku_ids = {int(row.sku_id) for row in customer_rows}
    seen = set(customer_sku_ids)

    # 不足时用公司整体成交频次补足
    global_rows = (
        await session.execute(
            select(
                SalesOrderItem.sku_id,
                func.count(SalesOrderItem.id).label("times"),
                func.max(SalesOrderItem.unit_price).label("last_price"),
            )
            .where(SalesOrderItem.sku_id.is_not(None))
            .group_by(SalesOrderItem.sku_id)
            .order_by(func.count(SalesOrderItem.id).desc())
            .limit(limit * 3)
        )
    ).all()
    all_rows = list(customer_rows)
    for row in global_rows:
        if int(row.sku_id) in seen:
            continue
        all_rows.append(row)
        seen.add(int(row.sku_id))
        if len(all_rows) >= limit * 2:
            break

    if not all_rows:
        return []

    sku_ids = [int(row.sku_id) for row in all_rows]
    skus = {
        sku.id: sku
        for sku in (
            await session.execute(select(Sku).where(Sku.id.in_(sku_ids)))
        ).scalars().all()
    }

    results: list[dict] = []
    for row in all_rows:
        sku = skus.get(int(row.sku_id))
        if sku is None or sku.deleted_at is not None:
            continue
        if keyword:
            haystack = f"{sku.sku_code} {sku.name or ''} {sku.specification or ''}"
            if keyword.strip().lower() not in haystack.lower():
                continue
        from_customer = int(row.sku_id) in customer_sku_ids
        results.append(
            {
                "sku_id": sku.id,
                "sku_code": sku.sku_code,
                "name": sku.name,
                "specification": sku.specification,
                "unit": sku.unit,
                "moq": sku.moq,
                "order_times": int(row.times),
                "last_unit_price": _number(row.last_price),
                "reason": (
                    f"该客户历史成交 {int(row.times)} 次"
                    if from_customer
                    else f"公司整体成交 {int(row.times)} 次（该客户没买过）"
                ),
                "source": "customer_history" if from_customer else "company_history",
            }
        )
        if len(results) >= limit:
            break
    return results


__all__ = [
    "apply_data_scope",
    "assert_owner_active",
    "build_opportunity_stmt",
    "change_stage",
    "clone_opportunity",
    "copy_items_from",
    "create_opportunity_from_lead",
    "enrichment",
    "get_first_stage",
    "get_item_or_404",
    "get_opportunity_or_404",
    "get_won_stage",
    "list_items",
    "list_loss_reasons",
    "recommend_products",
    "replace_items",
    "serialize_item",
    "serialize_opportunity",
    "serialize_stage",
    "stage_map",
    "today",
    "touch_last_followup",
]
