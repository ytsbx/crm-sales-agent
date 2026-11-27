"""客户与联系人业务逻辑。"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.modules.customer.levels import level_filter
from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer import documents as documents_service
from app.modules.customer.model import (
    Contact,
    Customer,
    CustomerFollower,
    CustomerMergeLog,
    CustomerOwnerHistory,
)
from app.modules.customer import stage as stage_module
from app.modules.user.model import User


def _f(value) -> Decimal | None:
    """序列化总闸：保留 Decimal 原值（float→Decimal 专项批5）。

    JSON 出口（core/jsonio）按原值渲染精确数字，不再经 float 丢尾数。
    """
    return value


async def touch_progress(
    session: AsyncSession, customer_id: int | None, *, at: datetime | None = None
) -> None:
    """业务进展刷新「最近业务进展时间」（文档 §2.3/§11.2）。

    报价/打样/下单/回款等真实业务动作调用；与手工跟进写的
    last_followup_at 分开记。冷落扫描与公海回收取两者较新者——
    否则"正在履约但没点记录跟进"的客户会被误判冷落甚至回收进公海
    （场景 05/21 的口径）。customer_id 为空直接跳过，调用方事务内执行。
    """
    if not customer_id:
        return
    await session.execute(
        update(Customer)
        .where(Customer.id == customer_id)
        .values(last_progress_at=at or datetime.now(UTC))
    )


async def refresh_next_followup_at(
    session: AsyncSession, customer_id: int | None
) -> None:
    """重算「约定的下次跟进时间」（文档 §2.3 的第三个时钟）。

    口径：该客户**最近的未完成跟进任务**的到期时间；没有未完成任务就清空。

    为什么派生而不是让销售另填一个字段：约定这件事在系统里的载体本来就是
    "建了一条待办"，再维护一份手填时间必然和任务漂移（任务做完了、时间还挂着，
    冷落扫描继续拿它当豁免理由，客户就永远不失联了）。所以每次跟进留痕、
    任务完成/取消/延期后重算一次，口径单一。

    "约定了下次跟进"与"最近联系过"必须分开看：销售可以说"我下周联系"，
    这不代表本周已经联系过——合并成一个时间字段就回答不了"到底约没约、约了到没到"。
    """
    if not customer_id:
        return
    from app.modules.task.model import Task
    from app.modules.contact_util import lock_customer_row

    # 必须在锁内汇总，不能先算日期再等待 UPDATE 锁，否则会写回旧汇总。
    # 修改既有任务的调用方必须先客户、后任务取锁（task/locking.py）。
    await lock_customer_row(session, customer_id)

    due = (
        await session.execute(
            select(Task.due_at)
            .where(
                Task.customer_id == customer_id,
                Task.task_type == "followup",
                Task.status.in_(("pending", "doing")),
                Task.due_at.is_not(None),
            )
            .order_by(Task.due_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    await session.execute(
        update(Customer)
        .where(Customer.id == customer_id)
        .values(next_followup_at=due)
    )


# ---------------------------------------------------------------- 查询条件

async def apply_data_scope(
    stmt: Select, user: CurrentUser, session: AsyncSession
) -> Select:
    """按数据范围过滤客户（05-TECH §24：禁止只靠前端隐藏）。

    客户允许多人跟进：任一有效跟进人在范围内即可见，公海客户（`pool_status`）
    对有客户查看权限的人都可见。判据统一在 `customer/visibility.py`。
    """
    from app.modules.customer.visibility import customer_scope_clause_from

    clause = customer_scope_clause_from(await scoped_owner_ids(session, user))
    return stmt if clause is None else stmt.where(clause)


def not_deleted(stmt: Select) -> Select:
    return stmt.where(Customer.deleted_at.is_(None))


def in_flight_business_clause():
    """客户是否还有「在途业务」。

    只有存在未结束的业务时才值得催跟进：开放商机、已发送/已接受的报价、
    在途打样、未完成订单、未收清应收、待处理需求（询盘）。
    从没进过业务的导入联系人、只记过一条跟进但没有后续业务的客户，都不算。
    """
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import SalesOrder
    from app.modules.payment.model import ReceivablePlan
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest

    return or_(
        select(Opportunity.id).where(
            Opportunity.customer_id == Customer.id,
            Opportunity.deleted_at.is_(None),
            Opportunity.status == "open",
        ).exists(),
        select(Quote.id).where(
            Quote.customer_id == Customer.id,
            Quote.deleted_at.is_(None),
            Quote.status.in_(("sent", "accepted")),
        ).exists(),
        select(SampleRequest.id).where(
            SampleRequest.customer_id == Customer.id,
            SampleRequest.deleted_at.is_(None),
            SampleRequest.status.in_(("pending", "approved", "shipped")),
        ).exists(),
        select(SalesOrder.id).where(
            SalesOrder.customer_id == Customer.id,
            SalesOrder.status.not_in(("completed", "cancelled")),
        ).exists(),
        select(ReceivablePlan.id)
        .join(SalesOrder, SalesOrder.id == ReceivablePlan.order_id)
        .where(
            SalesOrder.customer_id == Customer.id,
            SalesOrder.status != "cancelled",
            ReceivablePlan.status.not_in(("paid", "cancelled")),
        ).exists(),
        select(CustomInquiry.id).where(
            CustomInquiry.customer_id == Customer.id,
            CustomInquiry.deleted_at.is_(None),
            CustomInquiry.status.in_(("open", "developing")),
        ).exists(),
    )


def build_list_stmt(
    keyword: str | None = None,
    level: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner_id: int | None = None,
    pool_status: str | None = None,
    follower_id: int | None = None,
    activity: str | None = None,
    stale_before: datetime | None = None,
) -> Select:
    stmt = select(Customer)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Customer.name.ilike(like),
                Customer.short_name.ilike(like),
                Customer.domain.ilike(like),
                Customer.tax_no.ilike(like),
                Customer.address.ilike(like),
            )
        )
    if level:
        stmt = stmt.where(level_filter(Customer.level, level))
    if status:
        stmt = stmt.where(Customer.status == status)
    if source:
        stmt = stmt.where(Customer.source == source)
    if owner_id is not None:
        # 历史参数：客户不再有「负责人」，这里按「跟进人包含该用户」解释，
        # 兼容旧导出/脚本；新代码请用 `follower_id`。
        stmt = stmt.where(
            Customer.id.in_(
                select(CustomerFollower.customer_id).where(
                    CustomerFollower.user_id == owner_id,
                    CustomerFollower.deleted_at.is_(None),
                )
            )
        )
    if pool_status:
        stmt = stmt.where(Customer.pool_status == pool_status)
    if follower_id is not None:
        # 「我跟进的」= 我是有效跟进人之一，而不是"默认负责人是我"
        stmt = stmt.where(
            Customer.id.in_(
                select(CustomerFollower.customer_id).where(
                    CustomerFollower.user_id == follower_id,
                    CustomerFollower.deleted_at.is_(None),
                )
            )
        )
    # “最近有效联系/进展”，两者都没有就退回建档时间；与 activity_status 口径一致。
    last_active = case(
        (
            and_(Customer.last_followup_at.is_(None), Customer.last_progress_at.is_(None)),
            Customer.created_at,
        ),
        (Customer.last_followup_at.is_(None), Customer.last_progress_at),
        (Customer.last_progress_at.is_(None), Customer.last_followup_at),
        (Customer.last_followup_at >= Customer.last_progress_at, Customer.last_followup_at),
        else_=Customer.last_progress_at,
    )
    if activity == "unknown_contact":
        # 历史导入的「联系时间待补核」：标记未知，且确实没有任何可信活动时间。
        # 只有标记不够——如果之后已经有报价/订单/回款，`last_progress_at` 会刷新，
        # 这类客户已经能判断活跃度，不该再挂在待补核名单里。
        # 企业微信导入只是同步联系人，不存在“历史联系时间”可补；它继续靠
        # `last_contact_unknown` 保留「不自动回收」保护，但不进入待补核统计/筛选，
        # 列表行内按「未跟进」显示。
        # 因此这里必须同时卡住两个时间，并排除企业微信来源，和客户列表标签口径一致。
        return stmt.where(
            Customer.last_contact_unknown.is_(True),
            Customer.last_followup_at.is_(None),
            Customer.last_progress_at.is_(None),
            or_(Customer.source.is_(None), Customer.source != "企业微信"),
        ).order_by(Customer.id.desc())
    if activity == "stale" and stale_before is not None:
        # 在途业务停滞：有未结束的业务，且最近一次跟进/进展早于阈值。
        # 没有在途业务的客户（导入后从没碰过）不在这里催。
        return stmt.where(
            in_flight_business_clause(), last_active < stale_before
        ).order_by(last_active.asc(), Customer.id.asc())
    return stmt.order_by(Customer.id.desc())


async def contact_counts(session: AsyncSession, customer_ids: list[int]) -> dict[int, int]:
    if not customer_ids:
        return {}
    stmt = (
        select(Contact.customer_id, func.count(Contact.id))
        .where(Contact.customer_id.in_(customer_ids), Contact.deleted_at.is_(None))
        .group_by(Contact.customer_id)
    )
    rows = (await session.execute(stmt)).all()
    return {int(cid): int(cnt) for cid, cnt in rows}


async def owner_names(session: AsyncSession, owner_ids: list[int]) -> dict[int, str]:
    ids = [oid for oid in owner_ids if oid]
    if not ids:
        return {}
    stmt = select(User.id, User.name).where(User.id.in_(ids))
    rows = (await session.execute(stmt)).all()
    return {int(uid): name for uid, name in rows}


async def owner_active_flags(
    session: AsyncSession, owner_ids: list[int]
) -> dict[int, bool]:
    """负责人 id → **还能不能接手**（账号存在且在岗）。

    给回收站客户清单用（回收站复审第三轮，2026-10-08）：原负责人已停用（或账号
    已经没了）时，恢复这条客户**必须先指定一位新负责人** —— 界面得能提前把这件事
    标出来，而不是等用户点下去被拒才知道。

    判据与 `transfer_customer` / `restore_customer` 里那道**同一口径**
    （`User.status == "active"`）。查不到的 id 不放进字典 —— 调用方按"不能接手"
    处理，正好等于"账号不存在"。
    """
    ids = [oid for oid in owner_ids if oid]
    if not ids:
        return {}
    stmt = select(User.id, User.status).where(User.id.in_(ids))
    rows = (await session.execute(stmt)).all()
    return {int(uid): status == "active" for uid, status in rows}


async def customer_overview(
    session: AsyncSession, user: CurrentUser, customer_id: int
) -> dict:
    """客户 360 概览（03-API §7 `GET /customers/{id}/overview`）。

    前端原先要发 5~6 个请求（商机/报价/订单/跟进/任务/文件）才拼得出这一屏。
    这里做**一次聚合**：每个板块给"数量 + 最近几条"，够首屏渲染，
    点进各标签页再拉完整分页。

    只读，不改变任何业务状态；板块之间互不依赖，单个板块为空不影响其他。

    ## 权限与数据范围（2026-10-07 第九批 §9.1 收口）

    此前这个函数**不接 user**：只要客户本身在范围内，就把该客户名下的订单金额、
    报价单号、跟进内容一次性交出去。于是"有 `customer:view`、没有 `order:view`"
    的账号（例如财务）能从这一个入口看到完整订单板块 —— 等于绕过订单模块自己的闸门；
    而且客户可见 ≠ 客户名下每条单据都可见（子资源列表接口是**按各自模块的数据范围**
    过滤的，概览却是全量聚合，两条路口径不一）。现在：

    - 每个板块先判**对应模块的查看权限**，没权限就**不查询、不返回**：
      计数给 `None`、列表给空数组，板块名进 `restricted`，前端据此显示"不可查看"；
    - 有权限的板块复用各模块自己的数据范围（`scoped_owner_ids`），
      与 `/customers/{id}/orders` 等子资源接口同一口径；
    - **计数与明细共用同一个 base 语句**：不再出现"显示有三份报价、只列出两份"
      （原来是计数不加软删、明细加软删，两处 where 各写一份）；
    - 跟进板块沿用 `followup/visibility` 的 `system_source_filter`：
      系统过程记录仍要能看见它的来源单据才放行。
    """
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.followup.model import FollowUp
    from app.modules.followup.visibility import system_source_filter
    from app.modules.opportunity.model import Opportunity, OpportunityStage
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.task.model import Task

    def can(code: str) -> bool:
        """本板块要的模块查看权限。管理员与 require_permission 同口径放行。"""
        return "admin" in user.roles or user.has(code)

    owner_ids = await scoped_owner_ids(session, user)
    restricted: list[str] = []

    def scoped(stmt, model):
        """按数据范围收窄。`owner_ids is None` = 全量（管理员/财务角色），不加条件。"""
        if owner_ids is None:
            return stmt
        return stmt.where(model.owner_id.in_(owner_ids))

    async def count_of(base) -> int:
        """计数由**明细的 base** 派生：同一份 where，口径不可能再漂。"""
        stmt = select(func.count()).select_from(base.order_by(None).subquery())
        return int((await session.execute(stmt)).scalar_one())


    # ── 商机 ──
    opportunity_rows: list = []
    opportunity_total: int | None = None
    open_opportunities: int | None = None
    if can("opportunity:view"):
        opp_base = scoped(
            select(Opportunity, OpportunityStage.name)
            .outerjoin(OpportunityStage, OpportunityStage.id == Opportunity.stage_id)
            .where(
                Opportunity.customer_id == customer_id,
                Opportunity.deleted_at.is_(None),
            ),
            Opportunity,
        )
        opportunity_total = await count_of(opp_base)
        open_opportunities = await count_of(opp_base.where(Opportunity.status == "open"))
        opportunity_rows = (
            await session.execute(opp_base.order_by(Opportunity.id.desc()).limit(5))
        ).all()
    else:
        restricted.append("opportunities")

    # ── 报价 ──
    quote_rows: list = []
    quote_total: int | None = None
    if can("quote:view"):
        from app.core.draft_visibility import draft_scope

        quote_base = scoped(
            select(Quote).where(
                Quote.customer_id == customer_id, Quote.deleted_at.is_(None), draft_scope(Quote, user.id)
            ),
            Quote,
        )
        quote_total = await count_of(quote_base)
        quote_rows = (
            await session.execute(quote_base.order_by(Quote.id.desc()).limit(5))
        ).scalars().all()
    else:
        restricted.append("quotes")

    # ── 订单（金额随权限一起消失：没权限就不算、不返回）──
    order_rows: list = []
    order_total: int | None = None
    order_amounts: list[dict] | None = None
    if can("order:view"):
        order_base = scoped(
            select(SalesOrder).where(SalesOrder.customer_id == customer_id), SalesOrder
        )
        order_total = await count_of(order_base)
        # 金额**按币种分组**（第九批 §9.9）：100 CNY + 100 USD 直接相加没有意义，
        # 而"统一折算规则"还没拍板，代码不该替业务选一个汇率。
        order_sub = order_base.order_by(None).subquery()
        amount_rows = (
            await session.execute(
                select(order_sub.c.currency, func.sum(order_sub.c.total_amount))
                .group_by(order_sub.c.currency)
                .order_by(order_sub.c.currency)
            )
        ).all()
        order_amounts = [
            # currency 为空 = 历史数据没记币种，**如实标出来**（curreny 给 null），
            # 不默认当成人民币 —— 那等于给一条没根据的数字。
            {"currency": currency, "amount": total if total is not None else Decimal(0),
             "amount_exact": str(total if total is not None else 0)}
            for currency, total in amount_rows
        ]
        order_rows = (
            await session.execute(order_base.order_by(SalesOrder.id.desc()).limit(5))
        ).scalars().all()
    else:
        restricted.append("orders")

    # ── 跟进（沿用 followup/visibility：系统过程记录仍要看得见来源单据）──
    followup_rows: list = []
    followup_total: int | None = None
    if can("followup:view"):
        followup_base = select(FollowUp).where(
            FollowUp.customer_id == customer_id,
            await system_source_filter(session, user),
        )
        followup_total = await count_of(followup_base)
        followup_rows = (
            await session.execute(followup_base.order_by(FollowUp.id.desc()).limit(5))
        ).scalars().all()
    else:
        restricted.append("followups")

    # ── 任务 ──
    task_rows: list = []
    task_total: int | None = None
    open_task_total: int | None = None
    if can("task:view"):
        task_base = scoped(select(Task).where(Task.customer_id == customer_id), Task)
        task_total = await count_of(task_base)
        open_task_total = await count_of(
            task_base.where(Task.status.in_(("pending", "doing")))
        )
        task_rows = (
            await session.execute(task_base.order_by(Task.id.desc()).limit(5))
        ).scalars().all()
    else:
        restricted.append("tasks")

    # ── 附件 ──
    # 这里**不另判 `file:view`**：按 file/access.py 的映射，"客户附件的查看权"
    # 就是 `customer:view`（附件跟随它挂着的业务对象），而客户本身已经过
    # `get_visible_customer`。多判一道会让"能看客户、看不到这个计数"，
    # 与 `/business/customer/{id}/files` 的口径打架。
    file_base = (
        select(BusinessFile, FileRecord)
        .join(FileRecord, FileRecord.id == BusinessFile.file_id)
        .where(
            BusinessFile.business_type == "customer",
            BusinessFile.business_id == customer_id,
        )
    )
    file_total = await count_of(file_base)
    file_rows = (
        await session.execute(file_base.order_by(BusinessFile.id.desc()).limit(5))
    ).all()

    return {
        # 没权限的板块计数给 None（而不是 0）："看不到"和"确实一条都没有"
        # 是两件事，前端据 restricted 显示"不可查看"，不要显示成 0 条。
        "counts": {
            "opportunities": opportunity_total,
            "open_opportunities": open_opportunities,
            "quotes": quote_total,
            "orders": order_total,
            "followups": followup_total,
            "tasks": task_total,
            "open_tasks": open_task_total,
            "files": file_total,
            # 按币种分组的订单金额（第九批 §9.9）。**没有**合并后的总额：
            # 不同币种直接相加没有意义，折算规则未拍板前不替业务选汇率。
            "order_amounts": order_amounts,
        },
        # 无权限、因而**根本没查**的板块（前端据此显示"不可查看"）
        "restricted": restricted,
        "opportunities": [
            {
                "id": row.id,
                "title": row.title,
                "stage_name": stage_name,
                "status": row.status,
                "expected_amount": _f(row.expected_amount),
                "expected_amount_exact": str(row.expected_amount) if row.expected_amount is not None else None,
                "expected_close_date": row.expected_close_date,
                "owner_id": row.owner_id,
            }
            for row, stage_name in opportunity_rows
        ],
        "quotes": [
            {
                "id": row.id,
                "quote_no": row.quote_no,
                "status": row.status,
                "valid_until": row.valid_until,
                "created_at": row.created_at,
            }
            for row in quote_rows
        ],
        "orders": [
            {
                "id": row.id,
                "order_no": row.order_no,
                "status": row.status,
                # 币种跟着金额一起给（第九批 §9.9）：前端不能假设是人民币
                "currency": row.currency,
                "total_amount": _f(row.total_amount),
                "total_amount_exact": str(row.total_amount) if row.total_amount is not None else None,
                "erp_order_id": row.erp_order_id,
                "delivery_date": row.delivery_date,
            }
            for row in order_rows
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
            for row in followup_rows
        ],
        "tasks": [
            {
                "id": row.id,
                "title": row.title,
                "status": row.status,
                "priority": row.priority,
                "due_at": row.due_at,
            }
            for row in task_rows
        ],
        "files": [
            {
                "business_file_id": link.id,
                "file_id": stored.id,
                "name": stored.file_name,
                "mime_type": stored.mime_type,
                "size": stored.size,
                "created_at": stored.created_at,
            }
            for link, stored in file_rows
        ],
    }


def serialize_customer(
    customer: Customer,
    *,
    owner_name: str | None = None,
    contact_count: int = 0,
    tags: list[dict] | None = None,
    stage: str | None = None,
    historical_order_count: int = 0,
    recent_order_count: int = 0,
    current_opportunity_count: int = 0,
    activity_status: str = "unknown",
    followers: list[dict] | None = None,
) -> dict:
    return {
        "id": customer.id,
        "name": customer.name,
        "short_name": customer.short_name,
        "customer_type": customer.customer_type,
        "country": customer.country,
        "region": customer.region,
        "address": customer.address,
        "domain": customer.domain,
        "tax_no": customer.tax_no,
        "source": customer.source,
        "level": customer.level,
        "status": customer.status,
        "pool_status": customer.pool_status,
        # 客户只有一组跟进人，没有默认负责人（2026-11-27 主人确认）。
        # `owner_id/owner_name` 仅作**兼容别名**（取第一位跟进人），
        # 不参与可见性、统计或任何业务判断；新前端一律用 `followers`。
        "owner_id": (followers or [{}])[0].get("user_id") if followers else None,
        "owner_name": (followers or [{}])[0].get("name") if followers else None,
        "followers": followers or [],
        "follower_count": len(followers or []),
        # 领导六阶段（自动推导，stage.py）：了解/报价/打样/首单/返单/稳定复购
        "stage": stage,
        "stage_label": stage_module.STAGE_LABELS.get(stage) if stage else None,
        "historical_order_count": historical_order_count,
        "recent_order_count": recent_order_count,
        "current_opportunity_count": current_opportunity_count,
        "activity_status": activity_status,
        "contact_count": contact_count,
        # PRD §6.1：客户列表要能展示标签
        "tags": tags or [],
        "remark": customer.remark,
        "last_followup_at": customer.last_followup_at,
        #: 联系时间未知（历史导入未提供）：前端要显式提示"待补核"，
        #: 否则这批客户在列表里和"刚联系过"长得一模一样（第七批 7.5）
        "last_contact_unknown": bool(getattr(customer, "last_contact_unknown", False)),
        "last_progress_at": customer.last_progress_at,
        "next_followup_at": customer.next_followup_at,
        "created_at": customer.created_at,
        "updated_at": customer.updated_at,
    }


def serialize_contact(contact: Contact) -> dict:
    """联系人**完整**序列化。

    这是内部口径（审计 before/after、写库后的返回值拼装都用它）：
    审计必须留完整值，否则"谁把电话改成了什么"就查不出来了。
    **对外的响应**要走下面那个脱敏版本（第七批 8.2）。
    """
    return {
        "id": contact.id,
        "customer_id": contact.customer_id,
        "name": contact.name,
        "title": contact.title,
        "department": contact.department,
        "mobile": contact.mobile,
        "phone": contact.phone,
        "email": contact.email,
        "wechat": contact.wechat,
        "is_primary": contact.is_primary,
        "owner_id": contact.owner_id,
        "source": contact.source,
        "remark": contact.remark,
        "created_at": contact.created_at,
    }


async def serialize_contacts_masked(
    session: AsyncSession, user: CurrentUser, contacts: list[Contact]
) -> list[dict]:
    """对外响应口径：按已确认规则决定是否脱敏（第八批 8.2）。

    规则见 `contact_util.can_view_full_contact`。为什么集中在这里而不是各路由
    自己判：联系人列表、详情、搜索、AI 工具、导出是**五个**入口，
    各写一份必然有一份忘了脱敏 —— 而"忘掉的那一份"就是泄漏面。
    """
    from app.modules.contact_util import full_contact_customer_ids, mask_contact_fields

    allowed = await full_contact_customer_ids(
        session, user, {contact.customer_id for contact in contacts}
    )
    out: list[dict] = []
    for contact in contacts:
        payload = serialize_contact(contact)
        if allowed is not None and (
            contact.customer_id is None or int(contact.customer_id) not in allowed
        ):
            payload = mask_contact_fields(payload)
        out.append(payload)
    return out


async def serialize_contact_masked(
    session: AsyncSession, user: CurrentUser, contact: Contact
) -> dict:
    return (await serialize_contacts_masked(session, user, [contact]))[0]


# ---------------------------------------------------------------- 客户操作

async def assert_customer_visible(
    session: AsyncSession, user: CurrentUser, customer: Customer
) -> None:
    """校验客户在当前用户的数据范围内。

    **这是一处真实的安全漏洞修复**：列表接口一直按数据范围过滤，
    但按 id 直取的详情、以及各写接口都只做了 `get_customer_or_404`
    （只判断存在），于是业务员把 id 改一改就能看到别人的客户。
    "列表看不到"和"拿不到"必须一致，否则前端隐藏毫无意义
    （05-TECH §24：禁止只靠前端隐藏）。

    公海客户（owner_id 为空）对所有有 customer:view 的人可见 ——
    这正是公海的意义。多跟进人客户的判断统一走 `customer/visibility.py`。
    """
    from app.modules.customer.visibility import assert_customer_in_scope

    await assert_customer_in_scope(session, user, customer)


async def get_customer_or_404(session: AsyncSession, customer_id: int) -> Customer:
    customer = await session.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    return customer


async def get_visible_customer(
    session: AsyncSession, user: CurrentUser, customer_id: int, *, for_update: bool = False
) -> Customer:
    """取客户并校验数据范围。读接口统一用这个，别再单独用 get_customer_or_404。"""
    if for_update:
        customer = (await session.execute(select(Customer).where(Customer.id == customer_id)
                    .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if customer is None or customer.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    else:
        customer = await get_customer_or_404(session, customer_id)
    await assert_customer_visible(session, user, customer)
    return customer


async def create_customer(
    session: AsyncSession,
    user: CurrentUser,
    payload: dict,
) -> Customer:
    """新建客户。

    客户没有默认负责人（2026-11-27 主人确认）：**创建人自动成为跟进人**。
    旧调用方带 `owner_id` 时按「再加一位跟进人」处理（不再写负责人列）；
    要加其他跟进人，创建后走 `POST /customers/{id}/followers`。
    """
    payload = dict(payload)
    extra_follower = payload.pop("owner_id", None)
    if extra_follower is not None:
        target = await session.get(User, extra_follower)
        if target is None:
            raise AppError(ErrorCode.NOT_FOUND, f"跟进人 id={extra_follower} 不存在", 404)
        if target.status != "active":
            raise AppError(
                ErrorCode.PARAM_ERROR, f"「{target.name}」已停用，不能作为跟进人", 422
            )
    customer = Customer(**payload, created_by=user.id)
    session.add(customer)
    await session.flush()
    from app.modules.customer.followers import add_followers

    # 旧接口的 `owner_id` 作为「第一位跟进人」先写入（兼容别名），创建人次之。
    if extra_follower is not None and extra_follower != user.id:
        await add_followers(
            session, customer.id, [extra_follower], source="manual", created_by=user.id
        )
    await add_followers(
        session, customer.id, [user.id], source="manual", created_by=user.id
    )
    return customer


async def transfer_customer(
    session: AsyncSession,
    user: CurrentUser,
    customer: Customer,
    new_owner_id: int | None,
    reason: str | None,
    *,
    automatic: bool = False,
    only_from_owner_id: int | None = None,
    default_reason: str | None = None,
    move_documents: bool = True,
    keep_previous_follower: bool = False,
) -> dict:
    """转交客户：用接手人替换指定的一位跟进人；`new_owner_id` 为空表示放入公海。

    2026-11-27 起客户**没有默认负责人**：本函数不再写 `customers.owner_id`。
    - 接手人先加入有效跟进人；
    - `only_from_owner_id` 是要被替换的那位跟进人（离职交接=离职人；手动转交=操作人）；
      不在当前跟进人里 → 409。为空且操作人不是跟进人时只加入接手人、不替换谁；
    - 被替换人只有一位（客户只有他）时**不移除**，避免出现零跟进人；
    - 未完成待办与名下单据**只搬被替换者的**（`only_from_owner_id` 语义），
      放入公海时单据/待办不动；
    - 放入公海 = `pool_status=public` 且**清空全部跟进人**（公海客户就是没有跟进人，
      主人 2026-11-27 确认）；领取/指派时再加入新跟进人。
    """
    if automatic:
        # 自动改派（离职交接/公海回收）由系统发起，沿用调用方已做的范围校验；
        # 其余入口必须重新在锁内复核可见性（防并发越权）。
        pass
    locked = (
        await session.execute(
            select(Customer)
            .where(Customer.id == customer.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked is None or locked.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    await assert_customer_visible(session, user, locked)
    customer = locked

    from app.modules.customer.followers import (
        is_follower,
        replace_follower,
    )

    if only_from_owner_id is not None and not await is_follower(
        session, customer.id, only_from_owner_id
    ):
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            f"客户「{customer.name}」的跟进人里没有这位同事，"
            "可能已被其他同事调整；请刷新后重新选择",
            409,
        )

    if automatic:
        from app.modules.customer import duplicates as dup_service

        if await dup_service.is_disputed(session, customer.id):
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"客户「{customer.name}」处于撞单争议中，自动改派已冻结，等主管裁定后再处理",
                422,
            )

    if new_owner_id is None:
        # 清空归属 = 把客户放回公海。这是「释放」，不是「转移」，必须过履约保护。
        from app.modules.settings import service as settings_service

        await settings_service.assert_no_protection(
            session,
            customer_id=customer.id,
            customer_name=customer.name,
            reason=reason,
            operator_id=user.id,
            # 填了原因 = 主管明确要求**例外**释放：放行，但保护事项与原因
            # 一起写进审计（例外是要有人担责的事）。
            allow_exception=True,
        )
        # 公海客户 = 没有跟进人：清空全部有效跟进关系（历史保留）。
        from app.modules.customer.followers import active_follower_ids, clear_followers

        old_followers = await active_follower_ids(session, customer.id)
        # 归属历史要记下“最后一位经手人”，领取时才能按它把单据接过来。
        # 新模型没有唯一负责人：优先记调用方指定的被替换人，否则记第一位跟进人。
        history_owner = (
            only_from_owner_id
            if only_from_owner_id is not None
            else (old_followers[0] if old_followers else None)
        )
        await clear_followers(session, customer)
        customer.pool_status = "public"
        session.add(
            CustomerOwnerHistory(
                customer_id=customer.id,
                old_owner_id=history_owner,
                new_owner_id=None,
                reason=reason or default_reason or "放入公海",
                operator_id=user.id,
                created_at=datetime.now(UTC),
            )
        )
        await session.flush()
        return documents_service.empty_result()

    owner = await session.get(User, new_owner_id)
    if owner is None:
        raise AppError(ErrorCode.NOT_FOUND, f"接手人 id={new_owner_id} 不存在", 404)
    if owner.status != "active":
        raise AppError(
            ErrorCode.PARAM_ERROR, f"接手人「{owner.name}」已停用，不能接收客户", 422
        )

    # 手动转交默认替换操作人自己；操作人不是跟进人（主管代转/公海指派）时只加接手人。
    replace_source = only_from_owner_id
    if replace_source is None and await is_follower(session, customer.id, user.id):
        replace_source = user.id

    await replace_follower(
        session,
        customer,
        replace_source,
        new_owner_id,
        source="handover" if automatic else "transfer",
        created_by=user.id,
        keep_old=keep_previous_follower,
    )

    session.add(
        CustomerOwnerHistory(
            customer_id=customer.id,
            old_owner_id=replace_source,
            new_owner_id=new_owner_id,
            reason=reason or default_reason,
            operator_id=user.id,
            created_at=datetime.now(UTC),
        )
    )

    moved = documents_service.empty_result()
    if replace_source is not None and replace_source != new_owner_id:
        # 待办责任只跟被替换者走：其他跟进人的活原样留着（离职场景尤其重要）。
        from sqlalchemy import update

        from app.modules.task.model import Task

        await session.execute(
            update(Task)
            .where(
                Task.customer_id == customer.id,
                Task.status.in_(("pending", "doing")),
                Task.owner_id == replace_source,
            )
            .values(owner_id=new_owner_id)
        )
        if move_documents:
            moved = await documents_service.reassign_customer_documents(
                session,
                customer_id=customer.id,
                from_owner_id=replace_source,
                to_owner_id=new_owner_id,
                operator_id=user.id,
            )
    return moved


async def _previous_owner_from_history(
    session: AsyncSession, customer_id: int
) -> int | None:
    """归属历史里最近一位**非空的**旧负责人。

    给"客户当前没有负责人"的场景用（从公海领取 / 公海指派）：客户在公海时自己
    没有负责人，但它的单据还挂在最后经手人名下；"有人领走时一并接过来"总得有个
    A，就取这一位。取不到（历史里从来没挂过人也可能是公海新建的客户）就返回 None，
    调用方按"没有可搬的"处理。
    """
    return (
        await session.execute(
            select(CustomerOwnerHistory.old_owner_id)
            .where(
                CustomerOwnerHistory.customer_id == customer_id,
                CustomerOwnerHistory.old_owner_id.is_not(None),
            )
            .order_by(CustomerOwnerHistory.id.desc())
            .limit(1)
        )
    ).scalars().first()


async def claim_customer(
    session: AsyncSession,
    user: CurrentUser,
    customer_id: int,
    *,
    reason: str = "公海领取",
) -> tuple[Customer, bool]:
    """领取公海客户，返回 `(客户, 本次是否真的领取了)`。

    2026-11-27 起公海客户**没有跟进人**：领取 = 把自己加入跟进人并转回私海。
    如果客户已在私海且本人就是跟进人，幂等返回「已参与」；私海但本人不是跟进人
    时不能领取（要主管加跟进人）。
    """
    row = (
        await session.execute(
            select(Customer)
            .where(Customer.id == customer_id, Customer.deleted_at.is_(None))
            .with_for_update()
            # ⚠️ 必须 `populate_existing`：本项目 session 是 `expire_on_commit=False`，
            # SQLAlchemy 默认不用查询结果覆盖已加载对象的属性。
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)

    # 锁后判数据范围：公海客户人人可领；私海客户必须在操作者范围内。
    await assert_customer_visible(session, user, row)

    from app.modules.customer.followers import is_follower

    if row.pool_status == "private" and await is_follower(session, row.id, user.id):
        return row, False  # 已经在跟进：幂等返回
    if row.pool_status != "public":
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "该客户已由其他同事跟进，不能直接领取；可请主管把你加为跟进人",
            409,
        )

    # 公海领取前先记住“最后一位经手人”：客户在公海没有跟进人，但它的
    # 单据/未完成待办还挂在上一任名下，领取时要一并接过来（旧口径）。
    previous_owner_id = await _previous_owner_from_history(session, row.id)

    # 公海领取 = 加入跟进人 + 转私海（历史与审计在同一条路径里）
    await transfer_customer(session, user, row, user.id, reason)

    if previous_owner_id is not None and previous_owner_id != user.id:
        from sqlalchemy import update

        from app.modules.task.model import Task

        await session.execute(
            update(Task)
            .where(
                Task.customer_id == row.id,
                Task.status.in_(("pending", "doing")),
                Task.owner_id == previous_owner_id,
            )
            .values(owner_id=user.id)
        )
        await documents_service.reassign_customer_documents(
            session,
            customer_id=row.id,
            from_owner_id=previous_owner_id,
            to_owner_id=user.id,
            operator_id=user.id,
        )
    return row, True


async def _ensure_own_customer_operation(
    session: AsyncSession, user: CurrentUser, customer: Customer, *, action: str
) -> None:
    """删除/恢复共用同一档归属规则（2026-11-27 主人确认）。

    - `customer:delete`：可操作**数据范围内**的客户（主管/管理员）；
    - `customer:delete_own`：客户**只有本人一位跟进人**时可操作（业务员）。
      多人跟进的客户不能用这把钥匙删/恢复——一个人删掉大家共用的客户代价太大；
      公海客户没有跟进人，也不属于"自己的客户"。
    """
    if "admin" in user.roles or user.has("customer:delete"):
        return
    if not user.has("customer:delete_own"):
        raise AppError(ErrorCode.FORBIDDEN, "无操作权限：需要 customer:delete", 403)
    from app.modules.customer.followers import active_follower_ids

    follower_ids = await active_follower_ids(session, customer.id)
    if follower_ids != [user.id]:
        raise AppError(
            ErrorCode.FORBIDDEN,
            f"只能{action}自己单独跟进的客户（该客户有 {len(follower_ids)} 位跟进人，"
            "或你不在其中）；请联系主管操作",
            403,
        )


async def ensure_can_delete_customer(
    session: AsyncSession, user: CurrentUser, customer: Customer
) -> None:
    """删除的授权边界：主管/管理员按数据范围，业务员只能删只有自己跟进的客户。"""
    await _ensure_own_customer_operation(session, user, customer, action="删除")


async def ensure_can_restore_customer(
    session: AsyncSession, user: CurrentUser, customer: Customer
) -> None:
    """恢复的授权边界：与删除同一档 —— 业务员只能恢复只有自己跟进的客户。"""
    await _ensure_own_customer_operation(session, user, customer, action="恢复")


async def delete_customer(session: AsyncSession, customer: Customer) -> int:
    """软删客户：只置 `deleted_at`，名下联系人/商机/报价/订单/跟进一条都不动。

    这是刻意的（03-API §42.1）：恢复时把这些标记一去掉，关联资料自动回来；
    不物理删除、不改挂，避免"删客户"顺手抹掉业务单据或把单据变成孤儿。

    唯一例外是**未完成的待办**：客户都删了，任务中心再挂着"去联系某某"
    只会变成一堆点不开的孤儿任务，所以一并取消（返回取消条数，写进审计）。
    恢复客户时任务不会自动恢复，需要重新建。
    """
    from app.modules.task.model import Task

    customer.deleted_at = datetime.now(UTC)
    result = await session.execute(
        update(Task)
        .where(Task.customer_id == customer.id, Task.status.in_(("pending", "doing")))
        .values(status="cancelled", completion_note="客户已删除，自动取消")
    )
    return result.rowcount or 0


async def lock_deleted_customer(
    session: AsyncSession, user: CurrentUser, customer_id: int
) -> Customer:
    """取一条**已被删除**的客户 + 行锁 + 校验数据范围（恢复入口的第一步）。

    为什么另开一个、不复用 `get_visible_customer`：那个的取数是
    `get_customer_or_404`，只认"活着"的客户 —— 恢复要的恰恰是**已经删掉**的那条。

    `populate_existing=True` 不能少：本项目会话是 `expire_on_commit=False`，
    SQLAlchemy 默认不用查询结果覆盖已加载对象的属性；调用方若在同一会话里先读过
    这条客户，少了它拿回来的就是内存里的旧值（`deleted_at` 还是空的），
    行锁等于白加 —— 并发下的表现是"时对时错"。
    """
    row = (
        await session.execute(
            select(Customer)
            .where(Customer.id == customer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if row is None or row.deleted_at is None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在或没有被删除", 404)
    await assert_customer_visible(session, user, row)
    return row


async def restore_customer(
    session: AsyncSession,
    user: CurrentUser,
    customer: Customer,
    *,
    new_owner_id: int | None = None,
) -> dict:
    """恢复一个**被直接删除**的客户（03-API §42.2）。

    删除只置 `deleted_at`，跟进人与名下资料都保留；恢复就是把标记去掉。
    被合并掉的来源客户不能恢复（空壳，数据已在目标客户下）。

    `new_owner_id` 保留为兼容参数：给了就作为**接手人**加入跟进人
    （客户转回私海）；不再有「指定新负责人」的换人改派语义 ——
    客户没有默认负责人，恢复不需要搬单据/待办。
    """
    merged = (
        await session.execute(
            select(CustomerMergeLog.id)
            .where(CustomerMergeLog.source_customer_id == customer.id)
            .limit(1)
        )
    ).first()
    if merged is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该客户是被合并掉的（已经并进另一个客户），不能直接恢复；"
            "请到合并后的那个客户下查看",
            400,
        )

    customer.deleted_at = None
    if new_owner_id is None:
        await session.flush()
        return documents_service.empty_result()

    target = await session.get(User, new_owner_id)
    if target is None:
        raise AppError(ErrorCode.NOT_FOUND, f"接手人 id={new_owner_id} 不存在", 404)
    if target.status != "active":
        raise AppError(
            ErrorCode.PARAM_ERROR, f"接手人「{target.name}」已停用，不能接收客户", 422
        )
    from app.modules.customer.followers import add_followers

    await add_followers(
        session,
        customer.id,
        [new_owner_id],
        source="restore",
        created_by=user.id,
    )
    if customer.pool_status == "public":
        customer.pool_status = "private"
    session.add(
        CustomerOwnerHistory(
            customer_id=customer.id,
            old_owner_id=None,
            new_owner_id=new_owner_id,
            reason="恢复客户时指定接手人",
            operator_id=user.id,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return documents_service.empty_result()


# ---------------------------------------------------------------- 联系人操作

async def get_contact_or_404(session: AsyncSession, contact_id: int) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
    return contact


async def get_visible_contact(
    session: AsyncSession, user: CurrentUser, contact_id: int
) -> Contact:
    """取联系人并校验数据范围。

    联系人自己没有独立的数据范围，跟着所属客户走；
    没有客户的联系人（还没归一/待绑定）对所有有 customer:view 的人可见 ——
    它们不属于任何人的客户，藏起来反而没人能处理。
    """
    contact = await get_contact_or_404(session, contact_id)
    if contact.customer_id is None:
        return contact
    customer = await session.get(Customer, contact.customer_id)
    # 客户软删后其联系人仍保留，供恢复客户时一并恢复业务关系；但正常联系人
    # 详情/写入口不能借 contact_id 绕过客户回收站继续查看或修改这些资料。
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "联系人所属客户不存在", 404)
    await assert_customer_visible(session, user, customer)
    return contact


async def set_primary_contact(session: AsyncSession, contact: Contact) -> None:
    """主要联系人唯一：先把同客户下其它联系人取消标记。

    ⚠️ 改标记之前**先锁住客户行**（2026-10-08 第十批 10.4 修）。从前这一步的
    UPDATE 完全不加锁，并发时两个人各自"取消别人 + 设自己"交错执行，会留下
    两个主联系人；库上那条部分唯一索引则会让后一个直接报 500。
    主联系人是**客户级**的唯一资源，锁客户行最自然。

    `deleted_at IS NULL` 与唯一索引的口径保持一致 —— 已删的联系人不占主位，
    不必再去清它的标记。
    """
    if contact.customer_id is None:
        return
    # 局部 import：`contact_util` 用的是 model 层，不会绕回来，
    # 但这里没必要为了一个函数把模块级依赖连上
    from app.modules.contact_util import lock_customer_row, take_primary_slot

    # 等待父锁期间可能已改挂；先核对真实归属，不能拿旧客户主位去操作新客户。
    expected_customer_id = contact.customer_id
    await lock_customer_row(session, expected_customer_id)
    current = (await session.execute(select(Contact.customer_id).where(
        Contact.id == contact.id, Contact.deleted_at.is_(None)
    ))).first()
    if current is None:
        raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
    if current[0] != expected_customer_id:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "联系人已改挂，请刷新后再操作", 409)

    # exclude=自己：腾位只腾**别人**，别把这条也刷成 False（见 take_primary_slot 的说明）
    await take_primary_slot(session, contact.customer_id, exclude=contact.id)
    contact.is_primary = True
    # 等客户锁期间，别的事务可能已取消本人的主标记；不能因内存仍为True漏掉回写。
    flag_modified(contact, "is_primary")


async def move_contact_to_customer(
    session: AsyncSession,
    contact: Contact,
    customer: Customer,
    *,
    user: CurrentUser,
    is_primary: bool,
) -> dict:
    """把联系人改挂到另一个客户 —— `bind-customer` 与 `change-customer` **共用这一条路径**。

    ## 顺序：先锁客户、腾位，再写归属

    两个客户按编号加锁并复查权限，再锁定联系人核对旧归属，最后腾位和改挂。

    反过来（先改归属）会翻车：`take_primary_slot` 里那句 `select(...).with_for_update()`
    会触发 autoflush，把"已经挂到目标客户、却还带着主标记"的这条先写下去 ——
    目标客户本来就有主，两条主撞上部分唯一索引 `uq_contacts_primary_per_customer`，
    接口直接 500。（第十批 10.4 复审复现的正是这一条。）

    ## 不要求当主时

    主标记**必须显式清掉**：换客户时主标记不能跟着走，否则新客户凭空多一个主、
    老客户的主位空着。

    ## 老客户的主位

    这条人走了之后，老客户名下可能就没有主联系人了 —— 这是允许的
    （"主联系人唯一"是**至多一个**，不是"必须有一个"），刻意不自动指认下一个。
    """
    from app.modules.contact_util import take_primary_slot

    old_customer_id = contact.customer_id
    customer_ids = {cid for cid in (old_customer_id, customer.id) if cid is not None}
    for customer_id in sorted(customer_ids):
        await get_visible_customer(session, user, customer_id, for_update=True)

    locked_contact = (await session.execute(
        select(Contact).where(Contact.id == contact.id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if locked_contact is None or locked_contact.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
    if locked_contact.customer_id != old_customer_id:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "联系人已改挂，请刷新后再操作", 409)
    before = serialize_contact(locked_contact)

    # ② 要当主，就先把目标客户的主位腾出来（锁已取，这一步只取消别人的标记）
    if is_primary:
        await take_primary_slot(session, customer.id, exclude=contact.id)
    # ③ 最后才写归属与主标记
    contact.customer_id = customer.id
    contact.is_primary = is_primary
    await session.flush()
    return before


__all__ = [
    "apply_data_scope",
    "build_list_stmt",
    "contact_counts",
    "create_customer",
    "delete_customer",
    "claim_customer",
    "get_contact_or_404",
    "get_customer_or_404",
    "move_contact_to_customer",
    "not_deleted",
    "owner_names",
    "serialize_contact",
    "serialize_customer",
    "set_primary_contact",
    "transfer_customer",
]
