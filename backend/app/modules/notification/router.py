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
from app.modules.notification.model import (
    CHANNEL_LABEL,
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
    stmt = select(Notification).where(Notification.user_id == user.id)
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
                Notification.user_id == user.id, Notification.read_at.is_(None)
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
