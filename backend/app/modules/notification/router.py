"""通知接口（03-API §32 通知 + §33 通知设置）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_current_user, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.notification.visibility import process_notification_filter
from app.modules.notification.model import (
    CHANNEL_LABEL,
    LEVEL_LABEL,
    WECOM_STATUS_LABEL,
    Notification,
)
from app.modules.notification.schema import NotificationSettingUpdate

router = APIRouter(tags=["Notification"])


def serialize(row: Notification) -> dict:
    return {
        "id": row.id,
        "type": row.type,
        "title": row.title,
        "content": row.content,
        "business_type": row.business_type,
        "business_id": row.business_id,
        # 分级（验收 24）：站内始终逐条可查，level 决定的是"怎么推给对方"
        "level": row.level,
        "level_label": LEVEL_LABEL.get(row.level, row.level),
        # 有值说明这条是夹在日报里出去的——"主管说没看到"时靠它解释
        "digest_at": row.digest_at.isoformat() if row.digest_at else None,
        "channel": row.channel,
        "channel_label": CHANNEL_LABEL.get(row.channel, row.channel),
        # wecom_status 为 None 表示"没走企微渠道"，与"发失败了"是两回事
        "wecom_status": row.wecom_status,
        "wecom_status_label": (
            WECOM_STATUS_LABEL.get(row.wecom_status, row.wecom_status)
            if row.wecom_status
            else None
        ),
        "wecom_error": row.wecom_error,
        "wecom_sent_at": row.wecom_sent_at,
        # 投递重试（文档 §六）：试过几次、下次什么时候再试都要能看见——
        # 只给一个 failed，运营不知道该等它自己好还是该点补投
        "wecom_attempts": row.wecom_attempts or 0,
        "wecom_next_retry_at": row.wecom_next_retry_at,
        # 可补投：失败与未投递两种状态都能人工重发；已投递/没走企微渠道的不需要
        "can_redispatch": row.wecom_status in ("failed", "skipped"),
        "read": row.read_at is not None,
        "created_at": row.created_at,
    }


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(Notification).where(Notification.user_id == user.id, await process_notification_filter(session, user))
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows, total = await paginate(session, stmt.order_by(Notification.id.desc()), page, page_size)
    return ok(page_data([serialize(row) for row in rows], total, page, page_size))


@router.get("/notifications/unread-count")
async def unread_count(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    count = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.read_at.is_(None),
                await process_notification_filter(session, user),
            )
        )
    ).scalar_one()
    return ok({"count": int(count)})


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    await session.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user.id)
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return ok(None, "已读")


@router.post("/notifications/read-all")
async def mark_all_read(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    await session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return ok(None, "全部已读")


# ---- 投递失败补投（文档 §六：发送失败保留业务记录并重试通知）----------------


@router.post("/notifications/{notification_id}/redispatch")
async def redispatch_notification(
    notification_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """补投单条通知。

    关键设计（回答"为什么原来的补发会失效"）：补投走的是**同一行通知**，
    不重跑业务动作，因此不经过 business_events 的唯一键——人工补发不会被
    去重挡住，也不会在客户时间线多出一条留痕。本人可补投自己的，
    管理员 / 设置管理员可补投任意人的。
    """
    from app.modules.notification import service

    row = await session.get(Notification, notification_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "通知不存在", 404)
    is_manager = "admin" in user.roles or user.has("settings:manage")
    if row.user_id != user.id and not is_manager:
        raise AppError(ErrorCode.FORBIDDEN, "只能补投自己的通知", 403)
    if row.wecom_status not in ("failed", "skipped"):
        label = WECOM_STATUS_LABEL.get(row.wecom_status or "", "未走企微渠道")
        raise AppError(ErrorCode.PARAM_ERROR, f"当前状态为「{label}」，不需要补投", 422)

    queued = await service.requeue_for_redispatch(session, ids=[row.id])
    result = await service.dispatch_pending(session, only_ids={row.id})
    if queued["requeued"]:
        await session.refresh(row)

    await write_audit(
        session,
        operator_id=user.id,
        action="redispatch_notification",
        business_type="notification",
        business_id=row.id,
        after={
            "wecom_status": row.wecom_status,
            "wecom_attempts": row.wecom_attempts,
            "wecom_error": row.wecom_error,
        },
        ip=client_ip(request),
    )
    await session.commit()

    if row.wecom_status == "sent":
        message = "已补投"
    else:
        message = f"仍未能投出：{row.wecom_error or '未知原因'}"
    return ok(
        {
            "id": row.id,
            "wecom_status": row.wecom_status,
            "wecom_status_label": WECOM_STATUS_LABEL.get(row.wecom_status or "", None),
            "wecom_error": row.wecom_error,
            **result,
        },
        message,
    )


@router.get("/notifications/delivery-failures")
async def delivery_failures(
    _: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """投递失败概览：多少条没出去、其中多少条还会自动重试。"""
    from app.modules.notification import service

    return ok(await service.delivery_failure_summary(session))


@router.post("/notifications/retry-failed")
async def retry_failed_notifications(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量补投：把失败与未投递的通知重新排队后立即投一遍。"""
    from app.modules.notification import service

    business_result = await service.materialize_business_notifications(session, limit=limit)
    await session.commit()
    queued = await service.requeue_for_redispatch(session, limit=limit)
    if not queued["requeued"]:
        if business_result['processed'] or business_result['failed']:
            await write_audit(session, operator_id=user.id, action="retry_business_notifications",
                              business_type="notification", after=business_result, ip=client_ip(request))
            await session.commit()
        return ok(
            {"requeued": 0, "attempted": 0, "sent": 0, "skipped": 0, "failed": 0,
             "business_events": business_result},
            "已处理主管通知待办" if business_result['processed'] else "没有需要补投的通知",
        )
    result = await service.dispatch_pending(
        session, only_ids=set(queued["ids"]), limit=len(queued["ids"])
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="redispatch_notifications",
        business_type="notification",
        after={"requeued": queued["requeued"], **result, "business_events": business_result},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {"requeued": queued["requeued"], **result, "business_events": business_result},
        f"补投 {result['sent']} 条，仍失败 {result['failed']} 条",
    )


# ---- 通知设置（API §33）---------------------------------------------------


def _settings_payload(value: dict, *, wecom_ready: bool, agent_configured: bool) -> dict:
    events = value.get("wecom_events") or {}
    return {
        "inapp_enabled": bool(value.get("inapp_enabled", True)),
        "wecom_enabled": bool(value.get("wecom_enabled", False)),
        "wecom_events": {
            "approval": bool(events.get("approval", True)),
            "task": bool(events.get("task", True)),
            "payment": bool(events.get("payment", True)),
            "followup": bool(events.get("followup", True)),
        },
        # 给界面看的就绪度：企微渠道开着但后面对接没配好时要能提示
        "wecom_ready": wecom_ready,
        "wecom_agent_configured": agent_configured,
    }


@router.get("/notification-settings")
async def get_notification_settings(
    _: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    from app.core.config import settings as app_settings
    from app.modules.settings import service as settings_service

    value = await settings_service.get_setting(session, "notification_channels")
    return ok(
        _settings_payload(
            value,
            wecom_ready=app_settings.wecom_contact_ready,
            agent_configured=bool(app_settings.wecom_agent_id),
        )
    )


@router.patch("/notification-settings")
async def update_notification_settings(
    payload: NotificationSettingUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改通知渠道。

    注意：企微渠道开着但应用没配（缺 WECOM_AGENT_ID）时**允许保存**，
    只给提示——配置顺序上先改设置再配密钥是正常的，
    这里硬拒会逼人按特定顺序操作。
    """
    from app.core.config import settings as app_settings
    from app.modules.settings import service as settings_service
    from app.modules.settings.model import SystemSetting

    current = dict(await settings_service.get_setting(session, "notification_channels"))
    current.setdefault("inapp_enabled", True)
    current.setdefault("wecom_enabled", False)
    events = dict(current.get("wecom_events") or {})
    events.setdefault("approval", True)
    events.setdefault("task", True)
    events.setdefault("payment", True)
    events.setdefault("followup", True)

    data = payload.model_dump(exclude_unset=True)
    if data.get("inapp_enabled") is not None:
        current["inapp_enabled"] = data["inapp_enabled"]
    if data.get("wecom_enabled") is not None:
        current["wecom_enabled"] = data["wecom_enabled"]
    if data.get("wecom_events"):
        events.update({k: v for k, v in data["wecom_events"].items() if v is not None})
    current["wecom_events"] = events

    if not current["inapp_enabled"] and not current["wecom_enabled"]:
        raise AppError(
            ErrorCode.PARAM_ERROR, "站内与企微不能同时关闭，否则通知无处可发", 422
        )

    row = (
        await session.execute(
            select(SystemSetting).where(SystemSetting.key == "notification_channels")
        )
    ).scalar_one_or_none()
    if row is None:
        row = SystemSetting(key="notification_channels", value=current)
        session.add(row)
    else:
        row.value = current

    await write_audit(
        session,
        operator_id=user.id,
        action="update_notification_settings",
        business_type="settings",
        business_id=row.id,
        after=current,
        ip=client_ip(request),
    )
    await session.commit()

    result = _settings_payload(
        current,
        wecom_ready=app_settings.wecom_contact_ready,
        agent_configured=bool(app_settings.wecom_agent_id),
    )
    message = "已保存"
    if current["wecom_enabled"] and not app_settings.wecom_agent_id:
        message = "已保存，但企微应用还没配置（缺 WECOM_AGENT_ID），当前投递会被标为未投递"
    return ok(result, message)


# ================================================================== 通知分级与日报
# 文档 §11.4 验收 24：主管一天收到大量业务事件时，按批准的逐次/分级策略投递，
# 且紧急项不被日报延误。分级策略是业务决策（§九 列在待批准里），
# 所以这里是"可配置的机制"，默认值保持全部即时推、不替业务改口径。


@router.get("/notifications/level-policy")
async def get_level_policy(
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """当前分级策略：哪些类型即时推、哪些攒进日报。"""
    from app.modules.notification import service

    policy = await service.level_policy(session)
    return ok(
        {
            **policy,
            "levels": [
                {"value": value, "label": label} for value, label in LEVEL_LABEL.items()
            ],
        }
    )


@router.put("/notifications/level-policy")
async def update_level_policy(
    payload: dict,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """保存分级策略。

    `by_type` 只接受合法级别；写进来的类型才改变投递方式，其余仍按默认级。
    校验放在这里而不是"存了再说"——策略写错会直接表现为"通知不发/乱发"，
    这种错必须在保存那一刻就拦住（与 settings 其它写接口同一纪律）。
    """
    from app.modules.settings.model import SystemSetting
    from sqlalchemy import select as _select

    default_level = payload.get("default_level", "normal")
    if default_level not in LEVEL_LABEL:
        raise AppError(ErrorCode.PARAM_ERROR, "default_level 不是合法级别", 422)
    by_type_raw = payload.get("by_type") or {}
    if not isinstance(by_type_raw, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "by_type 必须是对象", 422)
    bad = {k: v for k, v in by_type_raw.items() if v not in LEVEL_LABEL}
    if bad:
        raise AppError(ErrorCode.PARAM_ERROR, f"级别不合法：{bad}", 422)

    current = {
        "default_level": default_level,
        "by_type": {str(k): v for k, v in by_type_raw.items()},
    }
    existing = (
        await session.execute(
            _select(SystemSetting).where(SystemSetting.key == "notification_levels")
        )
    ).scalars().first()
    if existing is None:
        session.add(SystemSetting(key="notification_levels", value=current))
    else:
        existing.value = current

    await write_audit(
        session,
        operator_id=user.id,
        action="update_notification_levels",
        business_type="settings",
        business_id=existing.id if existing is not None else None,
        after=current,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(current, "分级策略已保存")


@router.post("/notifications/digest/run")
async def run_digest(
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """立刻跑一次日报（不等定时任务）。

    用途是验收与救急：设好策略后不用等到第二天早上，就能看到"几条攒在一起
    合成了一条"。它走的是与定时任务**同一个** service 函数，不存在两套逻辑。
    """
    from app.modules.notification import service

    result = await service.send_digest()
    await write_audit(
        session,
        operator_id=user.id,
        action="run_notification_digest",
        business_type="notification",
        business_id=None,
        after=result,
        ip=client_ip(request),
    )
    await session.commit()
    message = (
        f"日报已投递：{result['users']} 人 / {result['messages']} 条消息"
        f"，覆盖 {result['items']} 条事件"
        if result["messages"]
        else "没有攒着的日报级通知"
    )
    return ok(result, message)
