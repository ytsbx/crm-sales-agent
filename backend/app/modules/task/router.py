"""销售任务接口（对齐 03-API §25）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.notification import service as notification_service
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
    session: AsyncSession, user: CurrentUser, task_id: int, *, for_update: bool = False
) -> Task:
    """取任务并校验数据范围。

    任务列表按 `owner_id` 过滤，但改/完成/延期等单条操作此前只判断存在 ——
    实测别人可以改到王五的任务。任务没有"公海"概念，无负责人同样是异常数据，
    这里一并校验。

    `for_update=True`（所有会改状态的端点都要传）：让后到的请求**等前一个提交完**
    再读，于是它读到的是最新状态，终态检查才拦得住（审查 B2-03）。
    注意这只解决"读到旧数据"，**挡不住"读完到写回之间状态被改掉"** ——
    那一层由 `_guard_task_active` 的原子条件更新兜底。
    """
    if for_update:
        # ⚠️ `populate_existing=True` 不能省（审查 B2-03 的关键一处）：
        # 只加 `with_for_update()` **不会刷新已加载对象** —— 会话的 identity map 里
        # 可能已经有这个 Task（本次请求早先读过，或同一会话里被改过），
        # 于是"锁住了行"但 `task.owner_id` 之类的属性仍是**旧值**。
        # 后果：守卫里 `expect_owner_id=task.owner_id` 拿到旧值，
        # 而 SQL 条件 `owner_id = <旧值>` 被数据库按**当前值**求值，两者不一致时
        # 守卫会误判通过。这条与 `pricing/router.py` 里修过的是同一个坑。
        task = (
            await session.execute(
                select(Task)
                .where(Task.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    else:
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


def _status_label(task: Task) -> str:
    return {"pending": "待处理", "doing": "进行中", "done": "已完成",
            "cancelled": "已取消"}.get(task.status, task.status)


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


async def _guard_task_field(
    session: AsyncSession, task: Task, *, seen_updated_at, fields: dict, action: str,
) -> None:
    """"我读到的这一版还在"作为 UPDATE 的条件（审查 B2-03）。

    ## 为什么"只判终态"和"只锁行"都不够

    `with_for_update()` 能让后到的请求**等前面提交完再读**，`populate_existing`
    保证读到的是最新值。但实测**仍会两边都成功**，因为并发下有两种交错：

    1. `assign` 先拿到锁并提交 → `complete` 随后读到的是 `owner_id=4, status=pending`
       （已经是**别人改过之后**的状态），它"读到什么就基于什么"judge，
       终态条件当然成立 → 两边都成功，最终"已完成 + 负责人已换人"；
    2. `complete` 先提交 → `assign` 后读到 `done`，被终态条件拦住（这条是对的）。

    第 1 种不是"丢更新"，而是**两边基于不同的状态各自成功** —— 从用户视角看，
    "我把任务派给李四"和"我完成这张单"两件事都被执行了，而完成的人是以
    "张三的单"为前提点的完成按钮。

    ## 做法：乐观锁

    读任务时记下 `updated_at`（任何写入都会把它抬高 —— 本项目的 `updated_at`
    由 `onupdate` 自动维护，改状态、改负责人都会变），原子更新时要求
    **它没变过**：

        UPDATE tasks SET <字段>
        WHERE id = ? AND updated_at = <我读到的那一版> AND status NOT IN (终态)

    受影响行数为 0 → 说明在我读之后有人改过这一行 → 400 让人刷新后重试。
    这样"先改派再完成"会被拦下（改派已经抬高了 `updated_at`），
    而"顺序操作"（一个请求完全结束再发另一个）不受影响。

    ⚠️ 五个写入点都要用（完成 / 取消 / 改期 / 改派 / 转交）：只给其中一个加，
    另一个仍能凭旧快照写进去，绕过照样成立 —— 我前面两次就是这么漏过去的。
    """
    result = await session.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.updated_at == seen_updated_at,
            Task.status.notin_(FINAL_TASK_STATUSES),
        )
        .values(**fields)
    )
    if result.rowcount == 0:
        await session.refresh(task)
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"任务已被他人改动（当前：{_status_label(task)} / "
            f"{_final_label(task) if task.status in FINAL_TASK_STATUSES else '进行中'}），"
            f"请刷新后重试；本次{action}未生效",
        )
    await session.refresh(task)


async def _reserialize_fresh(session: AsyncSession, task: Task, owner_name=None) -> dict:
    """提交之后，用**从库里重新查出来的值**序列化响应（审查 B2-03）。

    为什么 `refresh` 不够：并发下"完成"可能刚提交，而本请求的对象仍在会话的
    identity map 里带着旧 `status`（`refresh` 在提交后的这种时序里实测仍会
    回显旧值）。这里直接**新发一条查询**取当前行，彻底绕开对象缓存 ——
    宁可如实回显"这张单已经被完成了"，也不能给一个与库内不一样的答案。
    """
    fresh = (
        await session.execute(
            select(Task).where(Task.id == task.id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    target = fresh if fresh is not None else task
    return serialize(target, owner_name)


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


def serialize(
    task: Task,
    owner_name: str | None = None,
    source_doc_no: str | None = None,
    quote_no: str | None = None,
    order_no: str | None = None,
) -> dict:
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
        # 关联单据的**编号**（第十一批 11.6 复审）：列表上要能显示"报价 BJ2026xxx"
        # 而不是"#12"，否则用户还得自己去找是哪一份。
        "quote_no": quote_no,
        "order_no": order_no,
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
    # 关联单据的编号，批量取一次（别在序列化里逐条查）。
    # 第十一批 11.6 复审：原先只处理"自动待办 → 合同"这一种，报价、订单两类
    # 在任务页上拿不到单号 —— 于是只能退化成"客户 #3"，看不出业务来源是哪一份。
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
    # 报价 / 订单在任务表上有自己的列，单独查编号
    quote_ids = {row.quote_id for row in rows if row.quote_id}
    quote_nos: dict[int, str] = {}
    if quote_ids:
        from app.modules.quote.model import Quote

        quote_nos = dict(
            (
                await session.execute(
                    select(Quote.id, Quote.quote_no).where(Quote.id.in_(quote_ids))
                )
            ).all()
        )
    order_ids = {row.order_id for row in rows if row.order_id}
    order_nos: dict[int, str] = {}
    if order_ids:
        from app.modules.order.model import SalesOrder

        order_nos = dict(
            (
                await session.execute(
                    select(SalesOrder.id, SalesOrder.order_no).where(
                        SalesOrder.id.in_(order_ids)
                    )
                )
            ).all()
        )
    items = [
        serialize(
            row,
            names.get(row.owner_id) if row.owner_id else None,
            doc_nos.get(row.source_business_id) if row.source_business_id else None,
            quote_no=quote_nos.get(row.quote_id) if row.quote_id else None,
            order_no=order_nos.get(row.order_id) if row.order_id else None,
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
    # 关联校验**只此一份**：普通建任务与「补建后续任务」共用
    # `task/refs.normalize_task_refs`（第十一批 11.6 复审 —— 两个入口各写一套的
    # 结果是"普通入口拦得住、补建入口照单全收"）。三关：在不在 → 归不归我 → 同不同客户。
    from app.modules.task.refs import normalize_task_refs

    data, _ = await normalize_task_refs(session, user, data, strict=True)

    # 联系人归属、单据与客户的一致性、以及"推断出来的客户"的兜底范围校验，
    # 都在上面那次 `normalize_task_refs` 里做完了 —— 不再在这里重复一套。

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
    # 负责人姓名要取**最终负责人**（`task_owner`）的名字，不是操作人的（审查 N07）。
    # 代建场景（管理员给张三建任务）下 `user.name` 是"系统管理员"、负责人是"张三"，
    # 于是**新建响应**显示错误负责人，而详情却是对的 —— 同一张任务两个说法，
    # 调用方拿新建响应渲染时会直接显示错人。
    return ok(serialize(task, task_owner.name), "任务已创建")


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    task = await _visible_task(session, user, task_id, for_update=True)
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
    task = await _visible_task(session, user, task_id, for_update=True)
    if task.status == "done":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "任务已完成")
    if task.status == "cancelled":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "任务已取消，不能标记为完成")
    # 记下**读取那一刻**的状态，守卫要用它做条件（不能用内存里的值 ——
    # 守卫执行前 SQLAlchemy 可能已把对象刷新过，两者会不一致，见守卫的说明）
    # 乐观锁：记下读到的这一版（任何写入都会抬高 updated_at，见守卫的说明）
    seen = task.updated_at
    await _guard_task_field(
        session, task, seen_updated_at=seen, fields={"status": "done"}, action="完成",
    )
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
    task = await _visible_task(session, user, task_id, for_update=True)
    _ensure_not_final(task, "取消")
    await _guard_task_field(
        session, task, seen_updated_at=task.updated_at,
        fields={"status": "cancelled"}, action="取消",
    )
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
    task = await _visible_task(session, user, task_id, for_update=True)
    _ensure_not_final(task, "改派")
    target = await _active_owner(session, payload.owner_id)

    before_owner = task.owner_id
    # ⚠️ 守卫必须**先于**内存赋值：`_visible_task` 里的语句会触发 SQLAlchemy
    # 的 autoflush，若先把 `task.owner_id` 改了再调守卫，那次 autoflush 会抢在
    # 条件更新之前把新值写进库 —— 守卫就失去意义了（我第一版就是这个顺序）。
    await _guard_task_field(
        session, task, seen_updated_at=task.updated_at,
        fields={"owner_id": payload.owner_id}, action="改派",
    )
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
    # ⚠️ 提交后必须 `refresh` 再序列化（审查 B2-03）：守卫只 UPDATE 了 `owner_id`，
    # 内存对象的 `status` 还是读进来时的旧值。并发下"完成"可能已经提交，
    # 于是响应回显 `pending`、库里却是 `done` —— 审查实测到的"改派响应与数据库不一致"。
    # 宁可如实回显"这张单已经被完成了"，也不要给一个和库里不一样的答案。
    payload_out = await _reserialize_fresh(session, task, target.name)
    await notification_service.dispatch_pending(session)
    return ok(payload_out, f"已指派给「{target.name}」")


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
            task = await _visible_task(session, user, task_id, for_update=True)
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
    task = await _visible_task(session, user, task_id, for_update=True)
    if payload.due_at is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请给出新的截止时间")
    _ensure_not_final(task, "改期")
    await _guard_task_field(
        session, task, seen_updated_at=task.updated_at,
        fields={"due_at": payload.due_at}, action="改期",
    )
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
    task = await _visible_task(session, user, task_id, for_update=True)
    if payload.owner_id is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请选择转交给谁")
    # 第九批 §9.3：转交原来的检查**比改派还松** —— 不判终态、不查用户是否存在、
    # 不看是否停用。这里与 `/assign` 收在同一套约束里。
    _ensure_not_final(task, "转交")
    target = await _active_owner(session, payload.owner_id)
    before_owner = task.owner_id
    # 守卫先于内存赋值（同上：避免 autoflush 抢先把新值写进库）
    await _guard_task_field(
        session, task, seen_updated_at=task.updated_at,
        fields={"owner_id": target.id}, action="转交",
    )
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
    # 同 `/assign`：提交后刷新，避免回显与库内不一致（审查 B2-03）
    payload_out = await _reserialize_fresh(session, task, target.name)
    await notification_service.dispatch_pending(session)
    return ok(payload_out, f"已转交给「{target.name}」")
