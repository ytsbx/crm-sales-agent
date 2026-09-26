"""系统设置与业务规则接口。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_current_user, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.settings import service as svc
from app.modules.settings.model import PublicPoolRule, SystemSetting, TaskRule
from app.modules.settings.schema import PublicPoolRuleInput, SettingInput, TaskRuleInput

router = APIRouter(tags=["Settings"])


@router.get("/meta/config")
async def public_config(
    _: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """给所有登录用户读的少量配置。

    用途：决定界面"要不要显示外贸相关的东西"。
    trade_mode = domestic 时，报价币种、汇率、退税率一律不出现——
    能力留在后端，但国内用户看不到多余字段。
    """
    return ok(
        {
            "trade_mode": (await svc.get_setting(session, "trade_mode")).get("mode", "domestic"),
            "default_currency": (await svc.get_setting(session, "default_currency")).get(
                "code", "CNY"
            ),
        }
    )


def serialize_setting(row: SystemSetting) -> dict:
    return {
        "id": row.id,
        "key": row.key,
        "value": row.value,
        "description": row.description,
        "updated_at": row.updated_at,
    }


def serialize_pool_rule(row: PublicPoolRule) -> dict:
    return {
        "id": row.id,
        "level": row.level,
        "days": row.days,
        "enabled": row.enabled,
        "remark": row.remark,
    }


def serialize_task_rule(row: TaskRule) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "trigger_type": row.trigger_type,
        "trigger_config": row.trigger_config,
        "action_config": row.action_config,
        "status": row.status,
    }


@router.get("/settings")
async def list_settings(
    _: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(select(SystemSetting).order_by(SystemSetting.key.asc()))
    ).scalars().all()
    return ok([serialize_setting(row) for row in rows])


@router.patch("/settings")
async def upsert_setting(
    payload: SettingInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = (
        await session.execute(select(SystemSetting).where(SystemSetting.key == payload.key))
    ).scalar_one_or_none()
    if row is None:
        row = SystemSetting(key=payload.key, description=payload.description)
        session.add(row)
    row.value = payload.value
    if payload.description:
        row.description = payload.description
    row.updated_by = user.id
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="setting",
        business_id=row.id,
        after={"key": payload.key, "value": payload.value},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_setting(row), "已保存")


@router.get("/public-pool/rules")
async def list_pool_rules(
    _: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(select(PublicPoolRule).order_by(PublicPoolRule.level.asc()))
    ).scalars().all()
    return ok([serialize_pool_rule(row) for row in rows])


@router.post("/public-pool/rules")
async def create_pool_rule(
    payload: PublicPoolRuleInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = PublicPoolRule(**payload.model_dump())
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="public_pool_rule",
        business_id=row.id,
        after=serialize_pool_rule(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_pool_rule(row), "规则已创建")


@router.patch("/public-pool/rules/{rule_id}")
async def update_pool_rule(
    rule_id: int,
    payload: PublicPoolRuleInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(PublicPoolRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "规则不存在", 404)
    before = serialize_pool_rule(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="public_pool_rule",
        business_id=row.id,
        before=before,
        after=serialize_pool_rule(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_pool_rule(row), "已保存")


@router.post("/public-pool/run-recycle")
async def run_recycle(
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """立即执行一次公海回收。

    正式环境由定时任务调用。审计写在 service 内部（它自己 commit），
    这里不再补写，避免提交后再写审计反而落到另一个事务里。
    """
    result = await svc.run_public_pool_recycle(session, user.id)
    return ok(result, f"已回收 {result['released_count']} 个客户")


@router.get("/task-rules")
async def list_task_rules(
    _: CurrentUser = Depends(require_permission("task:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (await session.execute(select(TaskRule).order_by(TaskRule.id.asc()))).scalars().all()
    return ok([serialize_task_rule(row) for row in rows])


@router.post("/task-rules")
async def create_task_rule(
    payload: TaskRuleInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = TaskRule(**payload.model_dump(), status="active")
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="task_rule",
        business_id=row.id,
        after=serialize_task_rule(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_task_rule(row), "规则已创建")


@router.patch("/task-rules/{rule_id}")
async def update_task_rule(
    rule_id: int,
    payload: TaskRuleInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(TaskRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "规则不存在", 404)
    before = serialize_task_rule(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="task_rule",
        business_id=row.id,
        before=before,
        after=serialize_task_rule(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_task_rule(row), "已保存")


@router.post("/tasks/run-auto-rules")
async def run_task_rules(
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """立即按规则生成一次自动任务。审计同样写在 service 内部。"""
    result = await svc.run_auto_tasks(session, user.id)
    return ok(result, f"已生成 {result['created_count']} 条自动任务")
