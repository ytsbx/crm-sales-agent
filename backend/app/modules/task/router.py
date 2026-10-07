"""销售任务接口（对齐 03-API §25）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer.model import Contact, Customer
from app.modules.lead.model import Lead
from app.modules.notification import service as notification_service
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.task.model import Task
from app.modules.task.schema import (
    TaskAssign,
    TaskBatchComplete,
    TaskComplete,
    TaskCreate,
    TaskUpdate,
)
from app.modules.user.model import User

router = APIRouter(tags=["Task"])

PRIORITY_LABEL = {"high": "高", "normal": "中", "low": "低"}
STATUS_LABEL = {"pending": "待处理", "doing": "处理中", "done": "已完成", "cancelled": "已取消"}


async def _visible_task(
    session: AsyncSession, user: CurrentUser, task_id: int
) -> Task:
    """取任务并校验数据范围。

    任务列表按 `owner_id` 过滤，但改/完成/延期等单条操作此前只判断存在 ——
    实测别人可以改到王五的任务。任务没有"公海"概念，无负责人同样是异常数据，
    这里一并校验。
    """
    task = await session.get(Task, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, "任务不存在", 404)
    await ensure_in_scope(session, user, owner_id=task.owner_id, label="任务")
    return task


#: 任务的**终态**：到了这两个状态，"谁做的、什么时候做的"已经落定。
#: 终态任务不允许再改派、改期、取消，也不允许凭普通编辑把它们改回进行中
#: （"重新打开已完成任务"是另一条业务规则，本批**不自行增加**）。
FINAL_TASK_STATUSES = ("done", "cancelled")

#: 库里 NOT NULL、但更新模型里写成 `X | None` 的字段（那是为了"不传就不改"）。
#: **显式传 `null` 不算"不传"** —— 它会撞上 NOT NULL 变成 500（§9.3 复审实测
#: `{"status": null}`）。这些字段明确传空要报参数错误。
_NOT_NULL_LABELS = {"title": "标题", "priority": "优先级", "status": "状态"}


def _final_label(task: Task) -> str:
    return "完成" if task.status == "done" else "取消"


def _ensure_not_final(task: Task, action: str) -> None:
    """终态任务不许再做 {action}。

    第九批 §9.3：改派接口（`/assign`）原来单独挡了这条路，但**普通编辑**
    （`PATCH /tasks/{id}`）直接写 `owner_id`/`status` 就绕过去了 ——
    "一个动作换个入口就放行"。这里收成一份，五个入口共用。
    """
    if task.status in FINAL_TASK_STATUSES:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, f"任务已{_final_label(task)}，不能{action}"
        )


async def _active_owner(session: AsyncSession, owner_id: int) -> User:
    """改派目标必须**存在且在岗**（指派 / 转交 / 普通编辑改负责人共用一份）。

    原来只有 `/assign` 做了"已停用"检查，`/transfer` 连"这个用户存不存在"都没查。
    """
    target = await session.get(User, owner_id)
    if target is None:
        raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={owner_id} 不存在", 404)
    if target.status != "active":
        raise AppError(ErrorCode.PARAM_ERROR, f"负责人「{target.name}」已停用", 422)
    return target


def serialize(task: Task, owner_name: str | None = None, source_doc_no: str | None = None) -> dict:
    overdue = False
    if task.due_at and task.status in ("pending", "doing"):
        due = task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=UTC)
        overdue = due < datetime.now(UTC)
    return {
        "id": task.id,
        "title": task.title,
        "task_type": task.task_type,
        "customer_id": task.customer_id,
        "contact_id": task.contact_id,
        "lead_id": task.lead_id,
        "opportunity_id": task.opportunity_id,
        "quote_id": task.quote_id,
        "order_id": task.order_id,
        "owner_id": task.owner_id,
        "owner_name": owner_name,
        "priority": task.priority,
        "priority_label": PRIORITY_LABEL.get(task.priority, task.priority),
        "status": task.status,
        "status_label": STATUS_LABEL.get(task.status, task.status),
        "due_at": task.due_at,
        "source": task.source,
        # 自动待办指向的来源对象：界面据此显示「月结协议 CT2026xxxx」并能跳过去。
        # 没这两个字段时，用户只看到一句"某某协议即将到期"，不知道是哪一份
        # （审查第 7 条：待办要能进到具体协议）。
        "source_business_type": task.source_business_type,
        "source_business_id": task.source_business_id,
        "source_doc_no": source_doc_no,
        "overdue": overdue,
        "completed_at": task.completed_at,
        "completion_note": task.completion_note,
        "created_at": task.created_at,
    }


@router.get("/tasks")
async def list_tasks(
    status: str | None = None,
    owner_id: int | None = None,
    mine: bool = False,
    overdue: bool = False,
    opportunity_id: int | None = None,
    customer_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("task:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(Task)
    if mine or user.data_scope == "self":
        stmt = stmt.where(Task.owner_id == user.id)
    else:
        # 原先这里对 department / department_and_sub 完全不施加部门条件
        # （直接落到 elif owner_id），等于部门范围失效、能看到全公司的任务。
        owner_ids = await scoped_owner_ids(session, user)
        if owner_ids is not None:
            stmt = stmt.where(Task.owner_id.in_(owner_ids))
        if owner_id is not None:
            stmt = stmt.where(Task.owner_id == owner_id)
    if status:
        stmt = stmt.where(Task.status == status)
    if overdue:
        stmt = stmt.where(
            Task.due_at < datetime.now(UTC), Task.status.in_(["pending", "doing"])
        )
    if opportunity_id:
        stmt = stmt.where(Task.opportunity_id == opportunity_id)
    if customer_id:
        stmt = stmt.where(Task.customer_id == customer_id)
    # 用 `NULLS LAST` 的可移植写法：先按"有没有到期日"排，再按到期日排。
    # 直接调 .nullslast() 是 PostgreSQL 专有，换库即 500（core/config.py 承诺换库只改连接串）。
    stmt = stmt.order_by(
        Task.status.asc(), Task.due_at.is_(None).asc(), Task.due_at.asc(), Task.id.desc()
    )

    rows, total = await paginate(session, stmt, page, page_size)
    owner_ids = {row.owner_id for row in rows if row.owner_id}
    names: dict[int, str] = {}
    if owner_ids:
        name_rows = (
            await session.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
        ).all()
        names = {int(uid): name for uid, name in name_rows}
    # 自动待办的来源单据号（目前只有月结协议一种）：批量取一次，别在序列化里逐条查
    doc_nos: dict[int, str] = {}
    contract_ids = {
        row.source_business_id
        for row in rows
        if row.source_business_type == "contract" and row.source_business_id
    }
    if contract_ids:
        from app.modules.contract.model import ContractDocument

        doc_nos = dict(
            (
                await session.execute(
                    select(ContractDocument.id, ContractDocument.doc_no).where(
                        ContractDocument.id.in_(contract_ids)
                    )
                )
            ).all()
        )
    items = [
        serialize(
            row,
            names.get(row.owner_id) if row.owner_id else None,
            doc_nos.get(row.source_business_id) if row.source_business_id else None,
        )
        for row in rows
    ]
    return ok(page_data(items, total, page, page_size))


async def _sync_next_followup(session: AsyncSession, task: Task) -> None:
    """任务变化后重算客户的「约定下次跟进时间」（文档 §2.3 的第三个时钟）。

    约定在系统里的载体就是"一条未完成的跟进任务"，所以任务新建/改期/完成/
    取消都要把派生值带着走——否则任务做完了客户上还挂着一个过去的约定，
    冷落扫描会继续拿它当"已约好"的豁免理由。
    """
    if task.task_type != "followup" or not task.customer_id:
        return
    from app.modules.customer import service as customer_service

    await customer_service.refresh_next_followup_at(session, task.customer_id)


@router.post("/tasks")
async def create_task(
    payload: TaskCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    data["owner_id"] = data.get("owner_id") or user.id

    # 负责人必须存在且**在职**（第九批 §9.2）。
    # `ensure_refs` 只判"有没有这一行"，于是停用账号照样能被指派 ——
    # 与 `/tasks/{id}/assign` 的"已停用"检查口径不一致，这里对齐。
    task_owner = await session.get(User, data["owner_id"])
    if task_owner is None:
        raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={data['owner_id']} 不存在", 404)
    if task_owner.status != "active":
        raise AppError(ErrorCode.PARAM_ERROR, f"负责人「{task_owner.name}」已停用", 422)

    # 关联对象：存在性 → **数据范围** → **是否同一客户**（第九批 §9.2）。
    #
    # 此前这里**只判存在**（`ensure_refs`），实测可以 ①给别人的客户建待办；
    # ②一张待办同时挂"客户 B"和"客户 A 的订单"——后者会污染客户待办、
    # 后续提醒和交接清单。跟进创建早就有这整套校验
    # （`followup/mutations.py`），待办漏了，现在与它对齐；差别只在
    # **待办允许完全没有关联对象**（纯日程待办）。
    #
    # 这些引用在库里没有外键约束，不校验就会留下悬空引用（静默 200）。
    # 注意：Contact 与 Customer 是**两个不同的模型**，不能塞进同一个 ids 字典
    # （那样会把 customer_id 当联系人主键去查，报"联系人 id=客户id 不存在"）。
    ref_labels = {
        "customer": "客户",
        "lead": "线索",
        "opportunity": "商机",
        "quote": "报价单",
        "order": "订单",
    }
    customer_id = data.get("customer_id")
    checked_customer_id: int | None = None
    for kind, model in (
        ("customer", Customer),
        ("lead", Lead),
        ("opportunity", Opportunity),
        ("quote", Quote),
        ("order", SalesOrder),
    ):
        obj_id = data.get(f"{kind}_id")
        if not obj_id:
            continue
        obj = await session.get(model, obj_id)
        if obj is None or getattr(obj, "deleted_at", None) is not None:
            raise AppError(
                ErrorCode.NOT_FOUND, f"{ref_labels[kind]} id={obj_id} 不存在", 404
            )
        # 客户/线索允许无主（公海 / 线索池），其余必须落在我的数据范围内。
        await ensure_in_scope(
            session,
            user,
            owner_id=obj.owner_id,
            label=ref_labels[kind],
            allow_unowned=kind in ("customer", "lead"),
        )
        if kind == "customer":
            checked_customer_id = obj_id
        # 单据自带客户：它就确定了这张待办的客户；显式传了别的客户直接拒。
        linked_customer = getattr(obj, "customer_id", None)
        if linked_customer:
            if customer_id and customer_id != linked_customer:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    "待办所选的客户与关联单据不是同一家客户，请确认后再保存",
                    422,
                )
            customer_id = linked_customer

    # 联系人：先由它确定客户，再比归属（第九批 §9.2）。
    # 原实现只在"同时传了 customer_id"时才比对，于是"只传联系人 + 一张别人的单"
    # 这条路径整段跳过校验。
    if data.get("contact_id") is not None:
        task_contact = await session.get(Contact, data["contact_id"])
        if task_contact is None or task_contact.deleted_at is not None:
            raise AppError(
                ErrorCode.NOT_FOUND, f"联系人 id={data['contact_id']} 不存在", 404
            )
        if customer_id is None:
            customer_id = task_contact.customer_id
        elif task_contact.customer_id != customer_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"联系人 id={data['contact_id']} 不属于客户 id={customer_id}",
                422,
            )

    # 最终确定的客户统一再过一次范围校验：它可能是**推断**出来的
    # （来自单据或联系人），推断不该成为绕过数据范围的通道。
    if customer_id is not None and customer_id != checked_customer_id:
        final_customer = await session.get(Customer, customer_id)
        if final_customer is None or final_customer.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "关联客户不存在", 404)
        await ensure_in_scope(
            session,
            user,
            owner_id=final_customer.owner_id,
            label="客户",
            allow_unowned=True,
        )
    data["customer_id"] = customer_id

    task = Task(**data, source="manual")
    session.add(task)
    await session.flush()
    await _sync_next_followup(session, task)
    if task.owner_id and task.owner_id != user.id:
        await notification_service.notify(
            session,
            user_id=task.owner_id,
            type_="task",
            title="有新任务指派给你",
            content=task.title,
            business_type="task",
            business_id=task.id,
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="task",
        business_id=task.id,
        after=serialize(task),
        ip=client_ip(request),
    )
    await session.commit()
    # 业务已经落库，再投企微：投递失败不影响任务创建，失败原因记在通知行上
    await notification_service.dispatch_pending(session)
    return ok(serialize(task, user.name), "任务已创建")


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    task = await _visible_task(session, user, task_id)
    before = serialize(task)
    changes = payload.model_dump(exclude_unset=True)

    # ── 终态任务：状态与责任都已落定，普通编辑不能再动这两样（第九批 §9.3）──
    # 原来只有 `/assign` 挡住了终点改派，改走 PATCH 就能把已完成的任务改给别人；
    # 而且"直接改成 done"不写完成时间，会留下"已完成但没有完成时间"的脏数据。
    # （"重新打开已完成任务"是另一条业务规则，本批不自行增加 —— 所以这里拒绝，
    #   而不是顺手做一个"重开"。）
    is_final = task.status in FINAL_TASK_STATUSES
    if changes.get("status") not in (None, task.status) and is_final:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"任务已{_final_label(task)}，若要重新打开请走专门的流程",
        )

    # ── 显式传空 ≠ 不传（§9.3 复审）──
    # 这几个字段在模型里是 `X | None`（为了"不传就不改"），可**明确传 `null`** 时
    # `exclude_unset` 仍会保留它，接着 `setattr(..., None)` 撞上库里的 NOT NULL，
    # `flush()` 抛 IntegrityError、被全局兜底翻成 **500**（实测 `{"status": null}`）。
    # 参数错误必须在写库**之前**拒绝 —— 否则既报 500，还可能留下半截更新。
    for field, label in _NOT_NULL_LABELS.items():
        if field in changes and changes[field] is None:
            raise AppError(ErrorCode.PARAM_ERROR, f"{label}不能为空", 422)

    # ── 终态任务同样不许改期（§9.3 复审）──
    # 原来只挡了状态与负责人：`/postpone` 会拦，`PATCH {"due_at": …}` 却返回 200、
    # 而且真的把截止时间改掉了（**清空也算改**）。同一个动作两条入口两套规矩，
    # 正是审查点名的"换个入口就绕过"。这里走 `_ensure_not_final`，
    # 与 postpone / cancel / assign / transfer 用同一份判据。
    if is_final and "due_at" in changes and changes["due_at"] != task.due_at:
        _ensure_not_final(task, "改期")

    reassigned_to: User | None = None
    if "owner_id" in changes:
        new_owner_id = changes["owner_id"]
        if new_owner_id is None:
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "负责人不能为空", 422)
        if new_owner_id != task.owner_id:
            if is_final:
                raise AppError(
                    ErrorCode.STATUS_NOT_ALLOWED,
                    f"任务已{_final_label(task)}，不能改负责人（谁完成的已经落定）",
                )
            reassigned_to = await _active_owner(session, new_owner_id)

    for field, value in changes.items():
        setattr(task, field, value)

    # 完成时间跟着状态走：编辑改成 done 与走 `/tasks/{id}/complete` 必须留下
    # 同样的痕迹，否则"什么时候完成的"只在一条路径上有。
    if changes.get("status") == "done" and task.completed_at is None:
        task.completed_at = datetime.now(UTC)

    await session.flush()
    await _sync_next_followup(session, task)
    # 普通编辑改了负责人，被指派的人也要收到提醒（与 /assign 同口径）
    if reassigned_to is not None and reassigned_to.id != user.id:
        await notification_service.notify(
            session,
            user_id=reassigned_to.id,
            type_="task",
            title="有任务指派给你",
            content=task.title,
            business_type="task",
            business_id=task.id,
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="task",
        business_id=task.id,
        before=before,
        after=serialize(task),
        ip=client_ip(request),
    )
    await session.commit()
    # 有改派才需要投递：业务已落库，投递失败不影响保存
    if reassigned_to is not None:
        await notification_service.dispatch_pending(session)
    return ok(serialize(task), "已保存")


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    payload: TaskComplete,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    task = await _visible_task(session, user, task_id)
    if task.status == "done":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "任务已完成")
    if task.status == "cancelled":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "任务已取消，不能标记为完成")
    task.status = "done"
    task.completed_at = datetime.now(UTC)
    task.completion_note = payload.completion_note
    await session.flush()
    await _sync_next_followup(session, task)
    await write_audit(
        session,
        operator_id=user.id,
        action="complete",
        business_type="task",
        business_id=task.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize(task), "任务已完成")


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    task = await _visible_task(session, user, task_id)
    _ensure_not_final(task, "取消")
    task.status = "cancelled"
    await session.flush()
    await _sync_next_followup(session, task)
    await write_audit(
        session,
        operator_id=user.id,
        action="cancel",
        business_type="task",
        business_id=task.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize(task), "任务已取消")


# ------------------------------------------------- 03-API §25 新增的三个接口


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int,
    user: CurrentUser = Depends(require_permission("task:view")),
    session: AsyncSession = Depends(get_db),
):
    """单条任务详情。"""
    task = await _visible_task(session, user, task_id)
    owners = (
        await session.execute(select(User.name).where(User.id == task.owner_id))
    ).scalar_one_or_none() if task.owner_id else None
    return ok(serialize(task, owners))


@router.post("/tasks/{task_id}/assign")
async def assign_task(
    task_id: int,
    payload: TaskAssign,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    """把任务指派 / 改派给他人。

    只动 `owner_id`；已完成或已取消的任务不允许改派 ——
    改派一个已完成的任务只会让"谁做的"这件事变得说不清。
    """
    task = await _visible_task(session, user, task_id)
    _ensure_not_final(task, "改派")
    target = await _active_owner(session, payload.owner_id)

    before_owner = task.owner_id
    task.owner_id = payload.owner_id
    await session.flush()

    # 被指派的人要收到提醒，否则改派了也没人知道
    await notification_service.notify(
        session,
        user_id=target.id,
        type_="task",
        title="有任务指派给你",
        content=task.title,
        business_type="task",
        business_id=task.id,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="assign",
        business_type="task",
        business_id=task.id,
        before={"owner_id": before_owner},
        after={"owner_id": payload.owner_id, "reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(serialize(task, target.name), f"已指派给「{target.name}」")


@router.post("/tasks/batch-complete")
async def batch_complete_tasks(
    payload: TaskBatchComplete,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量完成任务。

    逐个走 `_visible_task`：别人的任务会被跳过并说明原因，
    **不是整批失败** —— 一次勾选十几条时因为一条越权就全不生效，
    用起来会很难受，而跳过清单能让人看清是哪些。
    """
    if not payload.task_ids:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "task_ids 不能为空", 422)
    unique_ids = list(dict.fromkeys(payload.task_ids))

    completed: list[int] = []
    skipped: list[dict] = []
    touched_customers: set[int] = set()
    now = datetime.now(UTC)
    for task_id in unique_ids:
        try:
            task = await _visible_task(session, user, task_id)
        except AppError as error:
            skipped.append({"task_id": task_id, "reason": error.message})
            continue
        if task.status in FINAL_TASK_STATUSES:
            skipped.append(
                {"task_id": task_id, "reason": f"任务已{_final_label(task)}"}
            )
            continue
        task.status = "done"
        task.completed_at = now
        task.completion_note = payload.completion_note
        completed.append(task_id)
        if task.task_type == "followup" and task.customer_id:
            touched_customers.add(task.customer_id)

    await session.flush()
    # 批量完成后统一重算「约定下次跟进时间」（§2.3）：约定随任务一起消失，
    # 否则这批客户身上会留着已完成的旧约定，冷落扫描继续误豁免
    if touched_customers:
        from app.modules.customer import service as customer_service

        for customer_id in touched_customers:
            await customer_service.refresh_next_followup_at(session, customer_id)
    await write_audit(
        session,
        operator_id=user.id,
        action="batch_complete",
        business_type="task",
        business_id=None,
        after={"completed": len(completed), "skipped": len(skipped)},
        ip=client_ip(request),
    )
    await session.commit()
    message = f"已完成 {len(completed)} 条"
    if skipped:
        message += f"，跳过 {len(skipped)} 条"
    return ok({"completed": completed, "skipped": skipped}, message)


@router.post("/tasks/{task_id}/postpone")
async def postpone_task(
    task_id: int,
    payload: TaskUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    task = await _visible_task(session, user, task_id)
    if payload.due_at is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请给出新的截止时间")
    _ensure_not_final(task, "改期")
    task.due_at = payload.due_at
    await session.flush()
    # 改期就是改约定：客户上的「约定下次跟进时间」要跟着走（§2.3）
    await _sync_next_followup(session, task)
    await write_audit(
        session,
        operator_id=user.id,
        action="postpone",
        business_type="task",
        business_id=task.id,
        after={"due_at": str(payload.due_at)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize(task), "任务已延期")


@router.post("/tasks/{task_id}/transfer")
async def transfer_task(
    task_id: int,
    payload: TaskUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    task = await _visible_task(session, user, task_id)
    if payload.owner_id is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请选择转交给谁")
    # 第九批 §9.3：转交原来的检查**比改派还松** —— 不判终态、不查用户是否存在、
    # 不看是否停用。这里与 `/assign` 收在同一套约束里。
    _ensure_not_final(task, "转交")
    target = await _active_owner(session, payload.owner_id)
    before_owner = task.owner_id
    task.owner_id = target.id
    await session.flush()
    if target.id != user.id:
        await notification_service.notify(
            session,
            user_id=target.id,
            type_="task",
            title="有任务指派给你",
            content=task.title,
            business_type="task",
            business_id=task.id,
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="transfer",
        business_type="task",
        business_id=task.id,
        before={"owner_id": before_owner},
        after={"owner_id": target.id},
        ip=client_ip(request),
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(serialize(task, target.name), f"已转交给「{target.name}」")
