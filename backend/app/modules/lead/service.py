"""线索业务逻辑。"""

from datetime import UTC, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.lead.model import Lead, LeadAssignment
from app.modules.user.model import User

STATUS_LABEL = {
    "pending": "待分配",
    "assigned": "已分配",
    "following": "跟进中",
    "converted": "已转客户",
    "invalid": "无效",
}


def serialize_lead(lead: Lead, *, owner_name: str | None = None) -> dict:
    return {
        "id": lead.id,
        "name": lead.name,
        "company_name": lead.company_name,
        "contact_name": lead.contact_name,
        "mobile": lead.mobile,
        "email": lead.email,
        "source": lead.source,
        "source_detail": lead.source_detail,
        "country": lead.country,
        "region": lead.region,
        "status": lead.status,
        "status_label": STATUS_LABEL.get(lead.status, lead.status),
        "owner_id": lead.owner_id,
        "owner_name": owner_name,
        "converted_customer_id": lead.converted_customer_id,
        "converted_contact_id": lead.converted_contact_id,
        "converted_opportunity_id": lead.converted_opportunity_id,
        "invalid_reason": lead.invalid_reason,
        "remark": lead.remark,
        "last_followup_at": lead.last_followup_at,
        "created_at": lead.created_at,
        "updated_at": lead.updated_at,
    }


def build_lead_stmt(
    keyword: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner_id: int | None = None,
    region: str | None = None,
    unassigned: bool = False,
    include_deleted: bool = False,
) -> Select:
    """线索列表/导出的公共查询条件。

    `include_deleted=True` 时带上回收站里的（导出用）。
    `email` 也参与关键字搜索：业务常拿邮箱来找人。
    """
    stmt = select(Lead)
    if not include_deleted:
        stmt = stmt.where(Lead.deleted_at.is_(None))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Lead.name.ilike(like),
                Lead.company_name.ilike(like),
                Lead.contact_name.ilike(like),
                Lead.mobile.ilike(like),
                Lead.email.ilike(like),
            )
        )
    if status:
        stmt = stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source == source)
    if region:
        stmt = stmt.where(Lead.region == region)
    if owner_id is not None:
        stmt = stmt.where(Lead.owner_id == owner_id)
    if unassigned:
        stmt = stmt.where(Lead.owner_id.is_(None))
    return stmt.order_by(Lead.id.desc())


async def apply_data_scope(
    stmt: Select, user: CurrentUser, session: AsyncSession
) -> Select:
    """线索池里的未分配线索大家都能看到，已分配的按数据范围过滤。

    `department_and_sub` 取本部门及所有下级部门，见 app/core/data_scope.py。
    """
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return stmt
    return stmt.where(or_(Lead.owner_id.in_(owner_ids), Lead.owner_id.is_(None)))


async def get_lead_or_404(session: AsyncSession, lead_id: int) -> Lead:
    lead = await session.get(Lead, lead_id)
    if lead is None or lead.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "线索不存在", 404)
    return lead


async def assert_lead_visible(session: AsyncSession, user, lead: Lead) -> None:
    """校验线索在当前用户的数据范围内（不含取数）。

    恢复回收站线索这类"已经有对象、只需校验"的场景用这个，
    避免为了校验再查一次库。
    """
    from app.core.data_scope import ensure_in_scope

    # 线索池里的无主线索（无负责人）仍可查看：口径与公海客户一致
    await ensure_in_scope(
        session, user, owner_id=lead.owner_id, label="线索", allow_unowned=True
    )


async def get_visible_lead(session: AsyncSession, user, lead_id: int) -> Lead:
    """取线索并校验数据范围。

    线索池里没有负责人的线索（owner_id 为空）对所有有权限的人可见 ——
    那正是"待分配"的意义；有负责人的则必须在范围内。
    """
    lead = await get_lead_or_404(session, lead_id)
    await assert_lead_visible(session, user, lead)
    return lead


async def resolve_owner(
    session: AsyncSession, username: str | None, default_user_id: int
) -> int:
    """线索导入用：负责人按登录名匹配，匹配不上就用当前操作人。

    与客户导入同一口径（customer/io.py 的 resolve_owner），
    故意不共用实现是因为客户那份签名里没有 session 之外的依赖，
    合并反而要为一个参数抽公共层；两处都只有 5 行。
    """
    if not username or not username.strip():
        return default_user_id
    row = (
        await session.execute(select(User).where(User.username == username.strip()))
    ).scalar_one_or_none()
    return row.id if row else default_user_id


async def owner_names(session: AsyncSession, owner_ids: list[int]) -> dict[int, str]:
    ids = [oid for oid in owner_ids if oid]
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
    return {int(uid): name for uid, name in rows}


def record_assignment(
    session: AsyncSession,
    *,
    lead: Lead,
    to_user_id: int | None,
    operator_id: int,
    reason: str | None,
) -> None:
    session.add(
        LeadAssignment(
            lead_id=lead.id,
            from_user_id=lead.owner_id,
            to_user_id=to_user_id,
            reason=reason,
            operator_id=operator_id,
        )
    )


async def assign_lead(
    session: AsyncSession,
    lead: Lead,
    *,
    to_user_id: int | None,
    operator_id: int,
    reason: str | None,
) -> None:
    """分配线索。`to_user_id=None` 表示释放回线索池。

    这里必须校验目标用户存在且在职：此前不校验，传一个不存在的 id 也会照分，
    线索会挂到一个乌有人身上、分配历史里还留下这个无效 id，事后无法判断
    到底是"分给了离职的人"还是"传错了参数"。
    客户转移（`customer/service.py`）早有这道校验，线索这条链路一直漏着。
    """
    if to_user_id is not None:
        target = await session.get(User, to_user_id)
        if target is None:
            raise AppError(ErrorCode.NOT_FOUND, f"接收人 id={to_user_id} 不存在", 404)
        if target.status != "active":
            raise AppError(
                ErrorCode.PARAM_ERROR, f"接收人「{target.name}」已停用，不能接收线索", 422
            )

    record_assignment(
        session, lead=lead, to_user_id=to_user_id, operator_id=operator_id, reason=reason
    )
    lead.owner_id = to_user_id
    if to_user_id is None:
        lead.status = "pending" if lead.status != "converted" else lead.status
    elif lead.status in ("pending", "assigned"):
        lead.status = "assigned"


def mark_discarded(session: AsyncSession, lead: Lead, *, reason: str, operator_id: int) -> None:
    lead.status = "invalid"
    lead.invalid_reason = reason
    lead.deleted_at = datetime.now(UTC)
    record_assignment(
        session, lead=lead, to_user_id=None, operator_id=operator_id, reason=f"废弃：{reason}"
    )


#: 可领取的线索状态。**只认 `pending`（待分配）**：
#: 线索没有独立的 pool_status，"没有负责人"就表示待分配（见 model 的说明）。
#: 但光看 `owner_id is None` 不够 —— 已转客户的线索（`converted`）与已废弃的
#: （`invalid`）也可能因为没有负责人而"看起来可领"，实际上一领就把
#: "这条线索已经变成客户了"这个事实盖掉。
LEAD_CLAIMABLE_STATUS = "pending"


async def claim_lead(
    session: AsyncSession,
    user: CurrentUser,
    lead_id: int,
    *,
    reason: str = "线索池领取",
) -> tuple[Lead, bool]:
    """领取线索池里的线索，返回 `(线索, 本次是否真的领取了)`。

    两条领取路径（线索中心与公海页面）都走这里。加锁与幂等的理由同
    `customer/service.claim_customer`：并发领取不能两个人都成功，
    重试也不能在分配历史里留下两条。
    """
    row = (
        await session.execute(
            select(Lead)
            .where(Lead.id == lead_id, Lead.deleted_at.is_(None))
            .with_for_update()
            # 本项目 session 是 expire_on_commit=False：不加这个，同一请求里
            # 先读过的旧对象会顶掉库里那一行，锁就白加了
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "线索不存在", 404)

    # 线索池里没有负责人的线索对所有人可见（allow_unowned），有负责人的要在范围内
    await assert_lead_visible(session, user, row)

    if row.owner_id is not None and row.owner_id == user.id:
        return row, False  # 已经是自己的：幂等返回
    if row.owner_id is not None:
        owner = await session.get(User, row.owner_id)
        who = owner.name if owner else f"id={row.owner_id}"
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            f"该线索刚被「{who}」领取，你不能再领了；请刷新后另选",
            409,
        )
    if row.status != LEAD_CLAIMABLE_STATUS:
        # 已转客户 / 已废弃的线索即使没有负责人也不许领
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该线索当前是「{STATUS_LABEL.get(row.status, row.status)}」，不能领取",
        )

    await assign_lead(session, row, to_user_id=user.id, operator_id=user.id, reason=reason)
    return row, True
