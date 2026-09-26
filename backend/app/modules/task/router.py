"""销售任务接口（对齐 03-API §25）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.task.model import Task
from app.modules.notification import service as notification_service
from app.modules.task.schema import TaskComplete, TaskCreate, TaskUpdate
from app.modules.user.model import User

router = APIRouter(tags=["Task"])

PRIORITY_LABEL = {"high": "高", "normal": "中", "low": "低"}
STATUS_LABEL = {"pending": "待处理", "doing": "处理中", "done": "已完成", "cancelled": "已取消"}


def serialize(task: Task, owner_name: str | None = None) -> dict:
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
    items = [serialize(row, names.get(row.owner_id) if row.owner_id else None) for row in rows]
    return ok(page_data(items, total, page, page_size))


@router.post("/tasks")
async def create_task(
    payload: TaskCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    data["owner_id"] = data.get("owner_id") or user.id
    task = Task(**data, source="manual")
    session.add(task)
    await session.flush()
    if task.owner_id and task.owner_id != user.id:
        notification_service.notify(
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
    return ok(serialize(task, user.name), "任务已创建")


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    task = await session.get(Task, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, "任务不存在", 404)
    before = serialize(task)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    await session.flush()
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
    return ok(serialize(task), "已保存")


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    payload: TaskComplete,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    task = await session.get(Task, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, "任务不存在", 404)
    if task.status == "done":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "任务已完成")
    task.status = "done"
    task.completed_at = datetime.now(UTC)
    task.completion_note = payload.completion_note
    await session.flush()
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
    task = await session.get(Task, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, "任务不存在", 404)
    task.status = "cancelled"
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


@router.post("/tasks/{task_id}/postpone")
async def postpone_task(
    task_id: int,
    payload: TaskUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    task = await session.get(Task, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, "任务不存在", 404)
    if payload.due_at is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请给出新的截止时间")
    task.due_at = payload.due_at
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
    task = await session.get(Task, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, "任务不存在", 404)
    if payload.owner_id is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请选择转交给谁")
    task.owner_id = payload.owner_id
    await write_audit(
        session,
        operator_id=user.id,
        action="transfer",
        business_type="task",
        business_id=task.id,
        after={"owner_id": payload.owner_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize(task), "任务已转交")
