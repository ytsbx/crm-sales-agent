"""客户与联系人业务逻辑。"""

from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer import documents as documents_service
from app.modules.customer.model import Contact, Customer, CustomerOwnerHistory
from app.modules.customer import stage as stage_module
from app.modules.user.model import User


def _f(value) -> float | None:
    """Decimal/None → float/None。序列化 Numeric 字段时统一走这里。"""
    return None if value is None else float(value)


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

    `department_and_sub` 取本部门及所有下级部门，见 app/core/data_scope.py。
    客户允许没有负责人（公海），所以这里额外放行 owner_id 为空的行。
    """
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return stmt
    return stmt.where(or_(Customer.owner_id.in_(owner_ids), Customer.owner_id.is_(None)))


def not_deleted(stmt: Select) -> Select:
    return stmt.where(Customer.deleted_at.is_(None))


def build_list_stmt(
    keyword: str | None = None,
    level: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner_id: int | None = None,
    pool_status: str | None = None,
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
        stmt = stmt.where(Customer.level == level)
    if status:
        stmt = stmt.where(Customer.status == status)
    if source:
        stmt = stmt.where(Customer.source == source)
    if owner_id is not None:
        stmt = stmt.where(Customer.owner_id == owner_id)
    if pool_status:
        stmt = stmt.where(Customer.pool_status == pool_status)
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
        quote_base = scoped(
            select(Quote).where(
                Quote.customer_id == customer_id, Quote.deleted_at.is_(None)
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
            {"currency": currency, "amount": float(total or 0)}
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
        "owner_id": customer.owner_id,
        "owner_name": owner_name,
        # 领导六阶段（自动推导，stage.py）：了解/报价/打样/首单/返单/稳定复购
        "stage": stage,
        "stage_label": stage_module.STAGE_LABELS.get(stage) if stage else None,
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
    这正是公海的意义。
    """
    if customer.owner_id is None:
        return
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:  # 数据范围 all
        return
    if int(customer.owner_id) not in owner_ids:
        raise AppError(
            ErrorCode.DATA_SCOPE_DENIED, "该客户不在你的数据范围内", 403
        )


async def get_customer_or_404(session: AsyncSession, customer_id: int) -> Customer:
    customer = await session.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    return customer


async def get_visible_customer(
    session: AsyncSession, user: CurrentUser, customer_id: int
) -> Customer:
    """取客户并校验数据范围。读接口统一用这个，别再单独用 get_customer_or_404。"""
    customer = await get_customer_or_404(session, customer_id)
    await assert_customer_visible(session, user, customer)
    return customer


async def create_customer(
    session: AsyncSession,
    user: CurrentUser,
    payload: dict,
) -> Customer:
    """新建客户。

    ## 为什么不能用 `setdefault("owner_id", user.id)`

    `CustomerCreate.owner_id` 的默认值就是 `None`，`model_dump()` 会把
    `owner_id: None` **显式带进来**；而 `setdefault` 只在**键不存在**时才生效，
    于是 None 被原样保留 —— 任何人新建的客户都直接掉进公海（无负责人），
    谁都能看、谁都能领。这是个真实的数据归属缺陷，不是风格问题。

    正确口径：没指定负责人（键缺失或为 None）→ 归创建人自己。
    要把客户放进公海请走 `transfer_customer(..., None, ...)` / release-to-pool，
    那是有明确意图、且会写归属历史与审计的动作。
    """
    payload = dict(payload)
    if payload.get("owner_id") is None:
        payload["owner_id"] = user.id
    customer = Customer(**payload, created_by=user.id)
    session.add(customer)
    await session.flush()
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
) -> dict:
    """变更客户负责人；new_owner_id 为空表示放入公海。

    返回**这次单据跟着走的结果**（`customer/documents.empty_result()` 的形状：
    `moved` / `skipped` / `moved_total` / `skipped_total` / `skipped_labels`）；
    没有可搬的（放公海、离职交接、负责人没变）返回空结果。`skipped` 是并发下
    "已经被同事接走、因此留在同事手里"的那些 —— 接口要把它如实报给操作者。

    ⚠️ `reason` 是**调用方给的原始原因**，`default_reason` 只在它为空时用于
    归属历史（让档案里读起来像句人话，比如「放入公海」）。
    **两者绝不能混用**：履约保护的例外判断看的是"到底有没有填原因"，
    要是在这里先塞一个默认文案再判断，那"没填原因"就永远变成"填了"，
    例外闸门等于没有（2026-10-06 实测被 check_pool_recycle_and_merge 抓到）。

    目标负责人必须存在且在职：此前不校验，传一个不存在的 user id 也会照转，
    客户会挂到一个空负责人上，事后很难查（单个转移与批量转移都走这里，一处修两处生效）。

    `automatic=True` 表示这是**系统自动改派**（离职交接、公海回收这类
    批量/定时动作）。撞单争议未结案时这类改派一律拦下——文档 §11.5 :279
    的"争议冻结自动改派"：归属改谁由主管裁定，不让自动逻辑先动手造成既成事实。
    人工转移不受此限（人做的决定要留痕，但不该被系统拦住）。

    `move_documents`（2026-10-07 业务口径）：本函数除了改客户负责人，还会把
    **原本挂在这个客户原负责人名下**的单据（商机／打样／报价／订单草稿／订单
    以及它们生成的文件）一并改到新负责人名下 —— 否则"客户归了新人，
    但客户名下的报价、订单还写着原来那个人"，新人打开客户什么都看不到
    （子资源接口按单据自己的负责人过滤）。详见 `customer/documents.py`。

    只有**离职交接**会传 `move_documents=False`：它支持逐项指定不同的接手人，
    单据由它自己那套逐项搬运处理，这里再搬一遍会和逐项结果打架。
    """
    # 行锁。改负责人现在会连着搬这个客户名下的单据，两个人同时转移、
    # 或者"转移"和"公海回收/指派"撞上时，必须让后来者等前面提交完再读，
    # 否则两边都按自己读到的旧归属各搬一套 —— 轻则重复写，重则后提交的
    # 把前一次的搬运结果覆盖掉。
    #
    # ⚠️ 必须 `populate_existing`：本项目 session 是 `expire_on_commit=False`，
    # SQLAlchemy 默认**不用查询结果覆盖已加载对象**的属性。调用方（路由、
    # 批量转移、领取、撞单裁定）大多在同一个会话里先读过这个客户，少了这一条
    # 就是"锁住了库里的行、手里还捏着内存里的旧值"，锁等于白加
    # （`claim_customer` 那条路上踩过同样的坑）。
    #
    # 同一个会话 + 同一个主键，`populate_existing` 拿回来的**就是 `customer`
    # 这个对象本身**（只是属性被库里的最新值刷新了），所以下面继续用 `customer`。
    locked = (
        await session.execute(
            select(Customer)
            .where(Customer.id == customer.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if locked is None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)

    if automatic:
        from app.modules.customer import duplicates as dup_service

        if await dup_service.is_disputed(session, customer.id):
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"客户「{customer.name}」处于撞单争议中，自动改派已冻结，等主管裁定后再处理",
                422,
            )
    if new_owner_id is not None:
        owner = await session.get(User, new_owner_id)
        if owner is None:
            raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={new_owner_id} 不存在", 404)
        if owner.status != "active":
            raise AppError(
                ErrorCode.PARAM_ERROR, f"负责人「{owner.name}」已停用，不能接收客户", 422
            )
    else:
        # 清空负责人 = 把客户放回公海。这是「释放」，不是「转移」，必须过履约保护。
        #
        # 此前只有专门的 release-to-pool 接口做检查，而单个转移、分配、批量转移
        # 都能传 `owner_id=null` —— 换个入口就能把还在履约中（在途订单/未结应收/
        # 有效正式报价/在途打样）的客户丢进公海，连"填原因后例外释放"这一关
        # 也一并绕过去了（第六批审查第 1 条，已复现：保护检查调用次数 0）。
        #
        # 收口到服务层：**凡是把负责人清空的入口都共用这一处校验**，
        # 判据与定时扫描、回收执行共用 `protection_detail`，不各判一套。
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

    old_owner_id = customer.owner_id
    customer.owner_id = new_owner_id
    customer.pool_status = "public" if new_owner_id is None else "private"
    session.add(
        CustomerOwnerHistory(
            customer_id=customer.id,
            old_owner_id=old_owner_id,
            new_owner_id=new_owner_id,
            # 历史里用"人话"的原因：调用方没写时退回 default_reason
            reason=reason or default_reason,
            operator_id=user.id,
            created_at=datetime.now(UTC),
        )
    )
    # 待办责任跟着客户走（审查第 7 条）：客户换了负责人，他名下**还没办完**的事
    # 不能留在原来的人手里——否则新负责人看不到该做的动作，老负责人还在被一个
    # 已经不属于他的客户提醒。**已完成的原样不动**：那是历史记录，当时的处理人
    # 和完成时间要留在档案里，业绩与责任追溯都靠它。
    #
    # 放公海（new_owner_id 为空）时不动：待办终归要有个责任人，这时候保留原负责人
    # 比让任务悬空好；等主管重新分配客户时它会跟着走。
    #
    # `only_from_owner_id`（离职交接专用，业务方 2026-10-06 定）：
    # 离职交接只迁**离职人自己的**责任，其他在职协作人的任务原样留着。
    # 不带这个参数时（普通转移、撞单裁定）仍然是"这个客户名下的未完成待办全跟着走"——
    # 那里换的是客户归属，协作人没必要跟着换；而离职场景下把在职同事的活
    # 一起挪给接手人，等于把别人的工作悄悄抢走了。
    if new_owner_id is not None:
        from sqlalchemy import update

        from app.modules.task.model import Task

        conditions = [
            Task.customer_id == customer.id,
            Task.status.in_(("pending", "doing")),
            Task.owner_id.is_not(None),
            Task.owner_id != new_owner_id,
        ]
        if only_from_owner_id is not None:
            conditions.append(Task.owner_id == only_from_owner_id)
        await session.execute(update(Task).where(*conditions).values(owner_id=new_owner_id))

    # 单据跟着客户走（2026-10-07 业务口径）：原本挂在**这个客户原负责人**名下的
    # 商机／打样／报价／订单草稿／订单，以及它们生成的对客文件，一起改到新负责人名下。
    # 边界（只搬原负责人名下的、业绩归属与历史创建人一个字不碰）见
    # `customer/documents.py` 开头那段说明。
    #
    # 放公海（new_owner_id 为空）不搬：单据留在最后经手人名下，档案里仍查得到；
    # 等有人领走时按下面这条规则一并接过去。
    if move_documents and new_owner_id is not None:
        source_owner_id = old_owner_id
        if source_owner_id is None:
            # 客户当前没有负责人（从公海领走、或公海指派给某人）：取归属历史里
            # 最近一位非空的旧负责人 —— "客户被谁管过、单据就跟着谁"，
            # 有人接走时一并接过来，否则新人还是看不到历史。
            source_owner_id = await _previous_owner_from_history(session, customer.id)
        if source_owner_id is not None and source_owner_id != new_owner_id:
            return await documents_service.reassign_customer_documents(
                session,
                customer_id=customer.id,
                from_owner_id=source_owner_id,
                to_owner_id=new_owner_id,
            )
    return documents_service.empty_result()


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

    **两条客户领取路径（客户详情与公海页面）都走这里**，行为必须一模一样 ——
    此前两处各写一遍，检查项就已经漂移了（公海那边查 pool_status + owner_id，
    客户那边只查 pool_status）。

    为什么要**加行锁**：领取是"读当前归属 → 判断无主 → 改成自己"三步。
    两个用户同时读到公海状态时，两边都会通过检查、先后覆盖负责人，
    还各写一条领取历史 —— 归属看着像"最后点的那个"，档案里却记着两个人都领过。
    `SELECT ... FOR UPDATE` 把这三步变成原子的：后到的请求会等前面提交完再读，
    读到的就是"已有负责人"，于是按规则被拒。

    幂等：如果客户**已经是自己的**，直接返回 `(客户, False)`，不再写一条领取历史 ——
    网络重试或连点两下，不该在档案里留下两条"某人领取"。
    """
    # 先按主键加锁。可领取条件刻意放到**锁后**再判：
    # 这样"不在公海"与"已被别人领走"能给出不同提示，而不是笼统报一句。
    row = (
        await session.execute(
            select(Customer)
            .where(Customer.id == customer_id, Customer.deleted_at.is_(None))
            .with_for_update()
            # ⚠️ 必须 `populate_existing`：本项目 session 是 `expire_on_commit=False`，
            # SQLAlchemy 默认**不用查询结果覆盖已加载对象**的属性。调用方如果在
            # 同一个会话里先读过这个客户，拿回来的就是内存里的旧对象（owner 还是 None），
            # 于是"已被别人领走"判不出来，行锁等于白加。
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)

    # 锁后判数据范围：私有客户必须在操作者范围内；公海客户人人可领。
    # 顺序有意如此 —— **先判范围再判"已被谁领走"**：
    # 反过来的话，越权者能从报错里读出"这个客户现在归谁"。
    await assert_customer_visible(session, user, row)

    if row.owner_id is not None and row.owner_id == user.id:
        return row, False  # 已经是自己的：幂等返回，不重复记历史
    if row.owner_id is not None:
        owner = await session.get(User, row.owner_id)
        who = owner.name if owner else f"id={row.owner_id}"
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            f"该客户刚被「{who}」领取，你不能再领了；请刷新后另选",
            409,
        )
    if row.pool_status != "public":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该客户不在公海，无法领取")

    # 归属变更与人工改派共用同一条路径（历史、待办责任、目标负责人校验都在里面）
    await transfer_customer(session, user, row, user.id, reason)
    return row, True


async def delete_customer(session: AsyncSession, customer: Customer) -> None:
    customer.deleted_at = datetime.now(UTC)


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
    if customer is not None:
        await assert_customer_visible(session, user, customer)
    return contact


async def set_primary_contact(session: AsyncSession, contact: Contact) -> None:
    """主要联系人唯一：先把同客户下其它联系人取消标记。"""
    if contact.customer_id is None:
        return
    await session.execute(
        Contact.__table__.update()
        .where(Contact.customer_id == contact.customer_id, Contact.id != contact.id)
        .values(is_primary=False)
    )
    contact.is_primary = True


__all__ = [
    "apply_data_scope",
    "build_list_stmt",
    "contact_counts",
    "create_customer",
    "delete_customer",
    "claim_customer",
    "get_contact_or_404",
    "get_customer_or_404",
    "not_deleted",
    "owner_names",
    "serialize_contact",
    "serialize_customer",
    "set_primary_contact",
    "transfer_customer",
]
