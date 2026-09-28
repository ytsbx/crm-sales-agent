"""通知写入与多渠道投递。

**事务约定（别改坏）**：`notify()` 只 `session.add()`，不 commit、不投递。
原因有两个：
1. 调用方多数在业务事务中间调它（审批通过、回款确认、任务分配），
   这里 commit 会把调用方未完成的事务一起提交，业务上不允许；
2. 企微投递是外部调用，如果在调用方 commit **之前**发出去，
   万一业务回滚，就出现了"通知已经发到员工企微、但业务其实没成功"——
   这是最难解释的一类问题。

所以投递拆成两步：`notify()` 落库并标 `wecom_status='pending'`，
调用方 `await session.commit()` 之后再调 `dispatch_pending()` 真正发送。
`dispatch_pending()` 自己开一个会话，投递结果（成功/失败/跳过）写回同一行。
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notification.model import (
    CHANNEL_BOTH,
    CHANNEL_INAPP,
    Notification,
)
from app.modules.user.model import Permission, Role, User, role_permissions, user_roles

#: 允许走企微的通知类型（与 notification_channels.wecom_events 的键对应）。
#: followup = 业务动作自动留痕推主管（报价提交/打样/下单，领导六阶段口径）
WECOM_EVENT_TYPES = ("approval", "task", "payment", "followup")


async def channel_settings(session: AsyncSession) -> dict:
    from app.modules.settings import service as settings_service

    return await settings_service.get_setting(session, "notification_channels")


def _wecom_enabled(settings: dict, type_: str) -> bool:
    if not settings.get("wecom_enabled"):
        return False
    events = settings.get("wecom_events") or {}
    if type_ in WECOM_EVENT_TYPES:
        return bool(events.get(type_, False))
    # 不在允许列表里的类型一律不发企微，避免多出一个渠道就多发一堆
    return False


async def notify(
    session: AsyncSession,
    *,
    user_id: int,
    type_: str,
    title: str,
    content: str | None = None,
    business_type: str | None = None,
    business_id: int | None = None,
    channel_settings_override: dict | None = None,
) -> Notification | None:
    """写一条站内通知；按配置决定是否同时排队企微投递。

    返回创建的 Notification（站内渠道关闭时返回 None），
    调用方 commit 后调 `dispatch_pending()` 完成企微投递。

    `channel_settings_override` 用于批量场景：一次读配置给多条通知复用，
    避免 `notify_approvers` 里每条都回查一次 system_settings。
    """
    settings = channel_settings_override
    if settings is None:
        settings = await channel_settings(session)

    to_wecom = _wecom_enabled(settings, type_)
    inapp_enabled = bool(settings.get("inapp_enabled", True))
    # 企微开着但站内关着时，站内那一行仍然要写：它是投递记录本身，
    # 否则"发过没有"就无处可查。
    if not inapp_enabled and not to_wecom:
        return None

    row = Notification(
        user_id=user_id,
        type=type_,
        title=title,
        content=content,
        business_type=business_type,
        business_id=business_id,
        channel=CHANNEL_BOTH if to_wecom else CHANNEL_INAPP,
        wecom_status="pending" if to_wecom else None,
    )
    session.add(row)
    return row


async def approver_user_ids(session: AsyncSession, permission_code: str) -> list[int]:
    """找出有某个权限（或管理员角色）的用户，用于审批类通知。"""
    stmt = (
        select(user_roles.c.user_id)
        .join(Role, Role.id == user_roles.c.role_id)
        .outerjoin(role_permissions, role_permissions.c.role_id == Role.id)
        .outerjoin(Permission, Permission.id == role_permissions.c.permission_id)
        .where((Permission.code == permission_code) | (Role.code == "admin"))
        .distinct()
    )
    return [int(uid) for uid in (await session.execute(stmt)).scalars().all()]


async def notify_approvers(
    session: AsyncSession,
    *,
    permission_code: str,
    title: str,
    content: str | None = None,
    business_type: str | None = None,
    business_id: int | None = None,
    exclude_user_id: int | None = None,
) -> int:
    user_ids = await approver_user_ids(session, permission_code)
    settings = await channel_settings(session)
    sent = 0
    for user_id in user_ids:
        if exclude_user_id and user_id == exclude_user_id:
            continue
        created = await notify(
            session,
            user_id=user_id,
            type_="approval",
            title=title,
            content=content,
            business_type=business_type,
            business_id=business_id,
            channel_settings_override=settings,
        )
        if created is not None:
            sent += 1
    return sent


async def notify_roles(
    session: AsyncSession,
    *,
    role_codes: list[str],
    type_: str,
    title: str,
    content: str | None = None,
    business_type: str | None = None,
    business_id: int | None = None,
    exclude_user_id: int | None = None,
    department_id: int | None = None,
) -> int:
    """按角色推通知：业务动作自动留痕时推给业务主管（sales_manager 等）。

    `department_id` 传了就只推该部门的角色用户（跨部门不互扰）；
    不传推全公司该角色——调用方应在能定位到部门时尽量传。
    """
    stmt = (
        select(User.id)
        .join(user_roles, user_roles.c.user_id == User.id)
        .join(Role, Role.id == user_roles.c.role_id)
        .where(Role.code.in_(role_codes), User.status == "active")
    )
    if department_id is not None:
        stmt = stmt.where(User.department_id == department_id)
    user_ids = (await session.execute(stmt)).scalars().all()
    settings = await channel_settings(session)
    sent = 0
    for user_id in user_ids:
        if exclude_user_id and user_id == exclude_user_id:
            continue
        created = await notify(
            session,
            user_id=user_id,
            type_=type_,
            title=title,
            content=content,
            business_type=business_type,
            business_id=business_id,
            channel_settings_override=settings,
        )
        if created is not None:
            sent += 1
    return sent


async def dispatch_pending(session: AsyncSession, *, limit: int = 50) -> dict:
    """把待投递的企微通知发出去。

    由调用方在 `commit()` **之后**调用。这里刻意用独立会话：调用方的会话
    已经提交完毕，用它继续读没问题，但一旦投递过程中出错会污染调用方状态，
    单独开会话最省心。
    """
    from app.core.config import settings as app_settings
    from app.core.database import SessionLocal
    from app.modules.wecom.client import WeComError, WeComNotConfigured, get_client

    async with SessionLocal() as own:
        rows = (
            await own.execute(
                select(Notification)
                .where(Notification.wecom_status == "pending")
                .order_by(Notification.id.asc())
                .limit(limit)
            )
        ).scalars().all()
        if not rows:
            return {"attempted": 0, "sent": 0, "skipped": 0, "failed": 0}

        client = get_client()
        ready = bool(app_settings.wecom_agent_id and app_settings.wecom_contact_ready)
        if app_settings.wecom_push_off:
            # 推送总闸（WECOM_PUSH_OFF=1）：开发/回归期间一条真实消息都不发，
            # 通知行标 skipped 留痕——"没发"和"发失败"依然分开
            for row in rows:
                row.wecom_status = "skipped"
                row.wecom_error = "推送已临时关闭（WECOM_PUSH_OFF）"
            await own.commit()
            return {"attempted": len(rows), "sent": 0, "skipped": len(rows), "failed": 0}
        users = {
            user.id: user
            for user in (
                await own.execute(
                    select(User).where(User.id.in_({row.user_id for row in rows}))
                )
            ).scalars().all()
        }

        sent = skipped = failed = 0
        for row in rows:
            user = users.get(row.user_id)
            if not ready or user is None or not user.wecom_userid:
                # 没配置或这个人没绑企微：标 skipped 而不是 failed，
                # 否则会把"还没配好"和"真发失败了"混在一起。
                row.wecom_status = "skipped"
                row.wecom_error = (
                    "未配置企微应用" if not ready else "该用户没有绑定企业微信 userid"
                )
                skipped += 1
                continue
            try:
                await client.send_text_card(
                    to_user=user.wecom_userid,
                    title=row.title,
                    description=(row.content or row.title)[:120],
                )
            except (WeComNotConfigured, WeComError) as error:
                row.wecom_status = "failed"
                row.wecom_error = str(error)[:255]
                failed += 1
            except Exception as error:  # 网络等其它异常同样要落痕
                row.wecom_status = "failed"
                row.wecom_error = f"{type(error).__name__}: {error}"[:255]
                failed += 1
            else:
                row.wecom_status = "sent"
                row.wecom_sent_at = datetime.now(UTC)
                sent += 1

        await own.commit()
        return {
            "attempted": len(rows),
            "sent": sent,
            "skipped": skipped,
            "failed": failed,
        }
