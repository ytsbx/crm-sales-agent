"""系统设置与业务规则接口。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_current_user, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.settings import service as svc
from app.modules.settings.model import (
    DictionaryItem,
    NumberingRule,
    NumberSequence,
    PublicPoolRule,
    SystemSetting,
    TaskRule,
)
from app.modules.settings.numbering import RESET_PERIODS, period_key, serialize_rule
from app.modules.settings.schema import (
    CustomerLevelCreate,
    CustomerLevelUpdate,
    DictionaryItemCreate,
    DictionaryItemUpdate,
    NumberingRuleCreate,
    NumberingRuleUpdate,
    PublicPoolRuleInput,
    SettingInput,
    TaskRuleInput,
)

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
    # 不能写成 TaskRule(**payload.model_dump(), status="active")：
    # TaskRuleInput 自带 status 字段，model_dump() 里已经有它，
    # 再显式传一次会 TypeError: got multiple values for keyword argument 'status'，
    # 直接 500。这个接口此前一直是坏的（没有用例覆盖到）。
    data = payload.model_dump()
    data.setdefault("status", "active")
    if data.get("status") is None:
        data["status"] = "active"
    row = TaskRule(**data)
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


@router.delete("/task-rules/{rule_id}")
async def delete_task_rule(
    rule_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """删除自动任务规则（03-API §25）。

    规则是配置数据，删掉不会影响已经生成的任务 ——
    `tasks.source_rule_id` 只做溯源，不做外键级联，
    所以这里可以安全地真删，历史任务仍能看出"当初是哪条规则生成的"。
    """
    row = await session.get(TaskRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "规则不存在", 404)
    before = serialize_task_rule(row)
    await session.delete(row)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="task_rule",
        business_id=rule_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "规则已删除，已生成的任务不受影响")


@router.post("/tasks/run-auto-rules")
async def run_task_rules(
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """立即按规则生成一次自动任务。审计同样写在 service 内部。"""
    result = await svc.run_auto_tasks(session, user.id)
    return ok(result, f"已生成 {result['created_count']} 条自动任务")


# ---- 编号规则（03-API §36，PRD §2.6 系统管理员能力）-----------------------


async def _rule_with_current(session: AsyncSession, rule: NumberingRule) -> dict:
    key = period_key(rule.reset_period, datetime.now(UTC))
    current = (
        await session.execute(
            select(NumberSequence.current_no).where(
                NumberSequence.rule_code == rule.code,
                NumberSequence.period_key == key,
            )
        )
    ).scalar_one_or_none()
    return serialize_rule(rule, key, int(current) if current is not None else None)


@router.get("/numbering-rules")
async def list_numbering_rules(
    _: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """编号规则列表，附带"下一个号长什么样"的预览。"""
    rows = (
        await session.execute(select(NumberingRule).order_by(NumberingRule.id.asc()))
    ).scalars().all()
    return ok([await _rule_with_current(session, row) for row in rows])


@router.post("/numbering-rules")
async def create_numbering_rule(
    payload: NumberingRuleCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    if payload.reset_period not in RESET_PERIODS:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"重置周期只能是 {list(RESET_PERIODS)} 之一",
            422,
        )
    if payload.seq_length < 1 or payload.seq_length > 12:
        raise AppError(ErrorCode.PARAM_ERROR, "流水位数应在 1~12 之间", 422)
    existing = (
        await session.execute(
            select(NumberingRule).where(NumberingRule.code == payload.code)
        )
    ).scalars().first()
    if existing is not None:
        raise AppError(ErrorCode.DUPLICATE, f"编号规则 {payload.code} 已存在", 409)

    row = NumberingRule(**payload.model_dump())
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="numbering_rule",
        business_id=row.id,
        after=await _rule_with_current(session, row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await _rule_with_current(session, row), "规则已创建")


@router.patch("/numbering-rules/{rule_id}")
async def update_numbering_rule(
    rule_id: int,
    payload: NumberingRuleUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改编号规则。

    已经发出的单号**不会**被改写（历史数据保持原样），
    改规则只影响之后新生成的号 —— 这是刻意的：
    改一条规则就把历史单号一起改掉，对账时会灾难性。
    """
    row = await session.get(NumberingRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "编号规则不存在", 404)

    data = payload.model_dump(exclude_unset=True)
    if data.get("reset_period") is not None and data["reset_period"] not in RESET_PERIODS:
        raise AppError(
            ErrorCode.PARAM_ERROR, f"重置周期只能是 {list(RESET_PERIODS)} 之一", 422
        )
    if data.get("seq_length") is not None and not 1 <= data["seq_length"] <= 12:
        raise AppError(ErrorCode.PARAM_ERROR, "流水位数应在 1~12 之间", 422)

    before = await _rule_with_current(session, row)
    for field, value in data.items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="numbering_rule",
        business_id=row.id,
        before=before,
        after=await _rule_with_current(session, row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await _rule_with_current(session, row), "已保存，仅影响之后新生成的单号")


# ---- 字典（03-API §36）---------------------------------------------------


def serialize_dictionary(row: DictionaryItem) -> dict:
    return {
        "id": row.id,
        "type": row.type,
        "code": row.code,
        "label": row.label,
        "sort_no": row.sort_no,
        "enabled": row.enabled,
        "remark": row.remark,
    }


@router.get("/dictionaries")
async def list_dictionaries(
    type: str | None = None,
    enabled_only: bool = False,
    _: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """字典列表。不传 type 返回全部；界面下拉传 type + enabled_only=true。

    权限只要求登录：这些是下拉选项数据，业务员建单时也要读。
    """
    stmt = select(DictionaryItem)
    if type:
        stmt = stmt.where(DictionaryItem.type == type)
    if enabled_only:
        stmt = stmt.where(DictionaryItem.enabled.is_(True))
    rows = (
        await session.execute(stmt.order_by(DictionaryItem.type, DictionaryItem.sort_no, DictionaryItem.id))
    ).scalars().all()
    return ok([serialize_dictionary(row) for row in rows])


@router.post("/dictionaries")
async def create_dictionary(
    payload: DictionaryItemCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    existing = (
        await session.execute(
            select(DictionaryItem).where(
                DictionaryItem.type == payload.type, DictionaryItem.code == payload.code
            )
        )
    ).scalars().first()
    if existing is not None:
        raise AppError(
            ErrorCode.DUPLICATE, f"{payload.type} 下已有编码 {payload.code}", 409
        )
    row = DictionaryItem(**payload.model_dump())
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="dictionary",
        business_id=row.id,
        after=serialize_dictionary(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_dictionary(row), "字典项已创建")


@router.patch("/dictionaries/{item_id}")
async def update_dictionary(
    item_id: int,
    payload: DictionaryItemUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(DictionaryItem, item_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "字典项不存在", 404)
    before = serialize_dictionary(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="dictionary",
        business_id=row.id,
        before=before,
        after=serialize_dictionary(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_dictionary(row), "已保存")


# ---- 客户等级（03-API §36）-----------------------------------------------


CUSTOMER_LEVEL_TYPE = "customer_level"


@router.get("/customer-levels")
async def list_customer_levels(
    enabled_only: bool = False,
    _: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(DictionaryItem).where(DictionaryItem.type == CUSTOMER_LEVEL_TYPE)
    if enabled_only:
        stmt = stmt.where(DictionaryItem.enabled.is_(True))
    rows = (
        await session.execute(stmt.order_by(DictionaryItem.sort_no, DictionaryItem.id))
    ).scalars().all()
    return ok([serialize_dictionary(row) for row in rows])


@router.post("/customer-levels")
async def create_customer_level(
    payload: CustomerLevelCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    existing = (
        await session.execute(
            select(DictionaryItem).where(
                DictionaryItem.type == CUSTOMER_LEVEL_TYPE,
                DictionaryItem.code == payload.code,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise AppError(ErrorCode.DUPLICATE, f"客户等级 {payload.code} 已存在", 409)
    row = DictionaryItem(
        type=CUSTOMER_LEVEL_TYPE,
        code=payload.code,
        label=payload.name,
        sort_no=payload.sort_no,
        enabled=payload.enabled,
        remark=payload.remark,
    )
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="customer_level",
        business_id=row.id,
        after=serialize_dictionary(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_dictionary(row), "客户等级已创建")


@router.patch("/customer-levels/{level_id}")
async def update_customer_level(
    level_id: int,
    payload: CustomerLevelUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(DictionaryItem, level_id)
    if row is None or row.type != CUSTOMER_LEVEL_TYPE:
        raise AppError(ErrorCode.NOT_FOUND, "客户等级不存在", 404)
    before = serialize_dictionary(row)
    data = payload.model_dump(exclude_unset=True)
    if data.get("name") is not None:
        row.label = data["name"]
    for field in ("sort_no", "enabled", "remark"):
        if data.get(field) is not None:
            setattr(row, field, data[field])
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="customer_level",
        business_id=row.id,
        before=before,
        after=serialize_dictionary(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_dictionary(row), "已保存")
