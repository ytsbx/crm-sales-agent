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

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
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

#: 投递失败的默认退避（分钟）：第 1 次失败 5 分钟后重试、第 2 次 30 分钟、
#: 第 3 次 2 小时。到上限后停在 failed 等人工补投——无限自动重试会把
#: "对方没绑企微 userid""应用没发消息权限"这类确定性失败变成长期噪声。
DEFAULT_RETRY_BACKOFF_MINUTES = (5, 30, 120)


async def retry_policy(session: AsyncSession) -> dict:
    """读投递重试策略（设置项 notification_retry）。

    配置缺失或写坏时退回默认值——通知能不能发出去比策略好不好看重要。
    """
    from app.modules.settings import service as settings_service

    value = await settings_service.get_setting(session, "notification_retry")
    default_max = len(DEFAULT_RETRY_BACKOFF_MINUTES)
    try:
        max_attempts = max(1, int(value.get("max_attempts", default_max)))
    except (TypeError, ValueError):
        max_attempts = default_max
    raw_backoff = value.get("backoff_minutes")
    if not isinstance(raw_backoff, list) or not raw_backoff:
        raw_backoff = list(DEFAULT_RETRY_BACKOFF_MINUTES)
    try:
        backoff = [max(0.0, float(minutes)) for minutes in raw_backoff]
    except (TypeError, ValueError):
        backoff = list(DEFAULT_RETRY_BACKOFF_MINUTES)
    return {
        "enabled": bool(value.get("enabled", True)),
        "max_attempts": max_attempts,
        "backoff": backoff,
    }


def retry_due_clause(policy: dict, now: datetime):
    """失败行"到期该重试"的入选条件。

    单独提出来是为了让回归脚本能对**同一份谓词**做只读断言——
    否则测试只能靠真发一次消息去反推，而真发消息在开发环境要么被
    WECOM_PUSH_OFF 拦成 skipped、要么把库里其它待投递行一起改掉。

    三个条件缺一不可：状态是 failed、还没到次数上限、退避时间已过
    （next_retry_at 为空视为立即到期：迁移前的历史失败行靠这条进入第一轮重试）。
    """
    return and_(
        Notification.wecom_status == "failed",
        Notification.wecom_attempts < policy["max_attempts"],
        or_(
            Notification.wecom_next_retry_at.is_(None),
            Notification.wecom_next_retry_at <= now,
        ),
    )


def _mark_failed(row: Notification, error: str, policy: dict, now: datetime) -> None:
    """失败落痕 + 排下一次重试；到上限就不再排（停在 failed 等人工补投）。"""
    row.wecom_status = "failed"
    row.wecom_error = error[:255]
    row.wecom_attempts = (row.wecom_attempts or 0) + 1
    if row.wecom_attempts >= policy["max_attempts"]:
        row.wecom_next_retry_at = None
        return
    index = min(row.wecom_attempts, len(policy["backoff"])) - 1
    row.wecom_next_retry_at = now + timedelta(minutes=policy["backoff"][index])


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


async def dispatch_pending(
    session: AsyncSession,
    *,
    limit: int = 50,
    include_failed: bool = False,
    only_ids: set[int] | None = None,
) -> dict:
    """把待投递的企微通知发出去。

    由调用方在 `commit()` **之后**调用。这里刻意用独立会话：调用方的会话
    已经提交完毕，用它继续读没问题，但一旦投递过程中出错会污染调用方状态，
    单独开会话最省心。

    `include_failed=True` 连同"到期该重试"的失败行一起捞（自动重试）；
    `only_ids` 限定只处理指定行（人工补投单条/一批，不顺手把别人的失败行也发出去）。
    """
    from app.core.config import settings as app_settings
    from app.core.database import SessionLocal
    from app.modules.wecom.client import WeComError, WeComNotConfigured, get_client

    async with SessionLocal() as own:
        policy = await retry_policy(own)
        now = datetime.now(UTC)
        stmt = select(Notification)
        if only_ids:
            stmt = stmt.where(Notification.id.in_(only_ids))
        elif include_failed and policy["enabled"]:
            # 待投递 ∪ 到期可重试的失败行：重试要"到期"才捞，
            # 否则退避形同虚设、失败一次就立刻再打一次企微接口
            stmt = stmt.where(
                or_(Notification.wecom_status == "pending", retry_due_clause(policy, now))
            )
        else:
            stmt = stmt.where(Notification.wecom_status == "pending")
        rows = (
            await own.execute(stmt.order_by(Notification.id.asc()).limit(limit))
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
                # skipped 不参与自动重试（再试还是这个结果），等配置补上后
                # 由人工补投触发——见 requeue_for_redispatch。
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
                _mark_failed(row, str(error), policy, now)
                failed += 1
            except Exception as error:  # 网络等其它异常同样要落痕
                _mark_failed(row, f"{type(error).__name__}: {error}", policy, now)
                failed += 1
            else:
                row.wecom_status = "sent"
                row.wecom_sent_at = datetime.now(UTC)
                row.wecom_attempts = (row.wecom_attempts or 0) + 1
                row.wecom_next_retry_at = None
                sent += 1

        await own.commit()
        return {
            "attempted": len(rows),
            "sent": sent,
            "skipped": skipped,
            "failed": failed,
        }


async def requeue_for_redispatch(
    session: AsyncSession, *, ids: list[int] | None = None, limit: int = 200
) -> dict:
    """人工补投：把失败/未投递的行重新排队，不动任何业务数据。

    两条设计理由（文档 §六：「发送失败保留业务记录并重试通知；不能为了重发
    消息再次创建报价或订单」）：

    1. 补投动的是**同一行通知**，不重跑 `record_and_notify`。所以业务事件表
       (`business_events.event_key`) 的唯一键不在路径上——人工补发**不会**
       被去重挡住。这是原实现最致命的一处：失败行不再被自动选中、业务动作
       重放又被去重跳过，一条通知就此彻底消失；
    2. 不新增跟进留痕——补投只是投递动作，客户时间线不该多出一条"系统"记录。

    `attempts` 归零：人工补投是"重新开始"，不该被自动重试的配额拦住。
    """
    stmt = select(Notification).where(
        Notification.wecom_status.in_(("failed", "skipped"))
    )
    if ids:
        stmt = stmt.where(Notification.id.in_(ids))
    rows = (
        await session.execute(stmt.order_by(Notification.id.asc()).limit(limit))
    ).scalars().all()
    for row in rows:
        row.wecom_status = "pending"
        row.wecom_error = None
        row.wecom_attempts = 0
        row.wecom_next_retry_at = None
    await session.commit()
    return {"requeued": len(rows), "ids": [row.id for row in rows]}


async def delivery_failure_summary(session: AsyncSession) -> dict:
    """投递失败概览：给界面回答"有多少条没出去、还会不会自己再试"。

    三个数分开不是啰嗦——`failed` 里"会自动重试"和"已放弃等人工"必须区分，
    否则运营看到 30 条失败只能干瞪眼，不知道要不要点补投。
    """
    policy = await retry_policy(session)
    now = datetime.now(UTC)
    counts = dict(
        (
            await session.execute(
                select(Notification.wecom_status, func.count(Notification.id)).group_by(
                    Notification.wecom_status
                )
            )
        ).all()
    )
    queued_retry = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.wecom_status == "failed",
                Notification.wecom_attempts < policy["max_attempts"],
                or_(
                    Notification.wecom_next_retry_at.is_(None),
                    Notification.wecom_next_retry_at <= now,
                ),
            )
        )
    ).scalar_one()
    return {
        "pending": int(counts.get("pending", 0) or 0),
        "sent": int(counts.get("sent", 0) or 0),
        "failed": int(counts.get("failed", 0) or 0),
        "skipped": int(counts.get("skipped", 0) or 0),
        # 是"failed"的子集：下一次调度就会被自动捞走的条数
        "retrying": int(queued_retry or 0),
        "max_attempts": policy["max_attempts"],
        "auto_retry_enabled": policy["enabled"],
    }
