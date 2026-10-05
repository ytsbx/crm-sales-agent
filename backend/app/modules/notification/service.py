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

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notification.model import (
    CHANNEL_BOTH,
    CHANNEL_INAPP,
    LEVEL_DIGEST,
    LEVEL_NORMAL,
    LEVEL_URGENT,
    Notification,
    BusinessEvent,
)
from app.modules.user.model import Permission, Role, User, role_permissions, user_roles

#: 允许走企微的通知类型（与 notification_channels.wecom_events 的键对应）。
#: followup = 业务动作自动留痕推主管（报价提交/打样/下单，领导六阶段口径）
WECOM_EVENT_TYPES = ("approval", "task", "payment", "followup")

#: 投递失败的默认退避（分钟）：第 1 次失败 5 分钟后重试、第 2 次 30 分钟、
#: 第 3 次 2 小时。到上限后停在 failed 等人工补投——无限自动重试会把
#: "对方没绑企微 userid""应用没发消息权限"这类确定性失败变成长期噪声。
DEFAULT_RETRY_BACKOFF_MINUTES = (5, 30, 120)

#: 分级策略的兜底值（文档 §11.4 验收 24）。
#:
#: **默认刻意保持"全部即时推"**：分级是投递策略，属于业务决策
#: （文档 §九 把"逐次还是分级"列为待批准事项）。代码这里先备好机制、
#: 不替业务改口径——`by_type` 空着，等批准后在设置里配。
#: 配了之后：urgent/normal 即时推，digest 攒进日报。
DEFAULT_LEVEL_POLICY: dict = {
    "default_level": LEVEL_NORMAL,
    "by_type": {},
}

VALID_LEVELS = (LEVEL_URGENT, LEVEL_NORMAL, LEVEL_DIGEST)


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


async def level_policy(session: AsyncSession) -> dict:
    """读通知分级策略（设置项 notification_levels），缺什么用兜底补齐。"""
    from app.modules.settings import service as settings_service

    raw = await settings_service.get_setting(session, "notification_levels")
    policy = dict(DEFAULT_LEVEL_POLICY)
    if isinstance(raw, dict):
        default_level = raw.get("default_level")
        if default_level in VALID_LEVELS:
            policy["default_level"] = default_level
        by_type = raw.get("by_type")
        if isinstance(by_type, dict):
            policy["by_type"] = {
                str(k): v for k, v in by_type.items() if v in VALID_LEVELS
            }
    return policy


def resolve_level(policy: dict, type_: str) -> str:
    """这个类型的通知该用哪一级。策略里没写的走 default_level。"""
    level = (policy.get("by_type") or {}).get(type_)
    if level in VALID_LEVELS:
        return level
    fallback = policy.get("default_level")
    return fallback if fallback in VALID_LEVELS else LEVEL_NORMAL


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
    level_override: str | None = None,
    level_policy_override: dict | None = None,
) -> Notification | None:
    """写一条站内通知；按配置决定是否同时排队企微投递。

    返回创建的 Notification（站内渠道关闭时返回 None），
    调用方 commit 后调 `dispatch_pending()` 完成企微投递。

    `channel_settings_override` 用于批量场景：一次读配置给多条通知复用，
    避免 `notify_approvers` 里每条都回查一次 system_settings。

    分级（验收 24）：默认按策略给这个 type 定级；调用点明确知道该条有多急时
    可以传 `level_override` 覆盖策略（例如"这条是紧急停线通知"）。
    """
    settings = channel_settings_override
    if settings is None:
        settings = await channel_settings(session)

    if level_override in VALID_LEVELS:
        level = level_override
    else:
        policy = level_policy_override
        if policy is None:
            policy = await level_policy(session)
        level = resolve_level(policy, type_)

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
        level=level,
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


async def materialize_business_notifications(
    session: AsyncSession, *, event_id: int | None = None, limit: int = 100,
) -> dict:
    """把已持久化业务事件转为主管通知；行锁与同事务完成标记防止重复。

    独立 savepoint：通知生成失败只回滚通知，业务事件保留为待重试。
    不在这里 commit 或请求企微。调度与提交后的投递入口会再次处理积压事件。
    """
    from app.core.data_scope import scoped_owner_ids
    from app.core.deps import CurrentUser
    from app.modules.customer.model import Customer
    from app.modules.followup.service import MANAGER_ROLE_CODES
    from app.modules.lead.model import Lead
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest
    from app.modules.user.service import get_user_permission_codes, get_user_roles, resolve_data_scope

    stmt = select(BusinessEvent).where(
        BusinessEvent.notification_payload.is_not(None),
        BusinessEvent.notification_processed_at.is_(None),
    )
    if event_id is not None:
        stmt = stmt.where(BusinessEvent.id == event_id)
    events = (await session.execute(stmt.order_by(BusinessEvent.id).limit(limit)
                                   .with_for_update(skip_locked=True))).scalars().all()
    result = {"processed": 0, "failed": 0, "notifications": 0}
    models = {"customer": Customer, "lead": Lead, "opportunity": Opportunity,
              "quote": Quote, "order": SalesOrder, "sample": SampleRequest}
    for event in events:
        saved_id = event.id
        try:
            async with session.begin_nested():
                payload = event.notification_payload
                source_type, source_id = payload['source_type'], payload['source_id']
                model = models.get(source_type)
                source = await session.get(model, source_id) if model and source_id else None
                customer = await session.get(Customer, payload['customer_id']) if payload.get('customer_id') else None
                stmt_users = select(User).join(user_roles, user_roles.c.user_id == User.id).join(
                    Role, Role.id == user_roles.c.role_id
                ).where(Role.code.in_(MANAGER_ROLE_CODES), User.status == 'active').distinct()
                if payload.get('department_id') is not None:
                    stmt_users = stmt_users.where(User.department_id == payload['department_id'])
                recipients = (await session.execute(stmt_users)).scalars().all()
                channels = await channel_settings(session)
                policy = await level_policy(session)
                count = 0
                for recipient in recipients:
                    if recipient.id == payload.get('exclude_user_id') or source is None:
                        continue
                    roles = await get_user_roles(session, recipient.id)
                    viewer = CurrentUser(recipient, await get_user_permission_codes(session, recipient.id),
                                         [role.code for role in roles], resolve_data_scope(roles))
                    is_admin = 'admin' in viewer.roles
                    if not is_admin and not viewer.has(f'{source_type}:view'):
                        continue
                    if payload.get('required_permission') and not is_admin and not viewer.has(payload['required_permission']):
                        continue
                    if source_type == 'quote' and source.deleted_at is not None:
                        continue
                    owner_ids = await scoped_owner_ids(session, viewer)
                    if owner_ids is not None and source.owner_id not in owner_ids:
                        if source_type not in ('customer', 'lead') or source.owner_id is not None:
                            continue
                    if customer:
                        if not is_admin and not viewer.has('customer:view'):
                            continue
                        if owner_ids is not None and customer.owner_id is not None and customer.owner_id not in owner_ids:
                            continue
                    row = await notify(session, user_id=recipient.id, type_='followup',
                                       title=payload['title'], content=payload['content'],
                                       business_type=source_type, business_id=source_id,
                                       channel_settings_override=channels, level_policy_override=policy)
                    if row is not None:
                        row.business_event_id = event.id
                    count += int(row is not None)
                event.notification_processed_at = datetime.now(UTC)
                event.notification_error = None
                await session.flush()
            result['processed'] += 1
            result['notifications'] += count
        except Exception as exc:
            # savepoint 已撤掉本事件所有部分生成的通知，待办和业务事实仍在。
            await session.refresh(event)
            event.notification_error = f'{type(exc).__name__}: {exc}'[:255]
            result['failed'] += 1
            logging.getLogger('crm.notification').warning('主管通知生成失败（event=%s）：%s', saved_id, exc)
    return result


async def _check_process_recipients(session: AsyncSession, rows: list[Notification]) -> set[int]:
    from app.core.deps import CurrentUser
    from app.modules.notification.visibility import process_notification_filter
    from app.modules.user.service import get_user_permission_codes, get_user_roles, resolve_data_scope

    allowed = {row.id for row in rows if row.business_event_id is None}
    recipients = {row.user_id for row in rows if row.business_event_id is not None}
    for uid in recipients:
        user = await session.get(User, uid)
        if user is None or user.status != 'active':
            continue
        roles = await get_user_roles(session, uid)
        viewer = CurrentUser(user, await get_user_permission_codes(session, uid),
                             [r.code for r in roles], resolve_data_scope(roles))
        ids = (await session.execute(select(Notification.id).where(
            Notification.id.in_([row.id for row in rows if row.user_id == uid]),
            await process_notification_filter(session, viewer),
        ))).scalars().all()
        allowed.update(ids)
    for row in rows:
        if row.id not in allowed:
            row.wecom_status = 'skipped'
            row.wecom_error = '接收人已无原单查看权限，未投递'
    return allowed


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
        if not only_ids:
            await materialize_business_notifications(own, limit=limit)
            await own.commit()
        policy = await retry_policy(own)
        now = datetime.now(UTC)
        stmt = select(Notification)
        # 分级（验收 24）：**待投递**的日报级不进即时通道——它要攒起来合成一条。
        # 已经 failed 的日报行不在这个限制里：宁可让它按既有重投机制单独补发，
        # 也不能卡在日报里永远出不去（"别把消息攒丢了"比"别多推一条"重要）。
        immediate = and_(
            Notification.wecom_status == "pending", Notification.level != LEVEL_DIGEST
        )
        if only_ids:
            stmt = stmt.where(Notification.id.in_(only_ids))
        elif include_failed and policy["enabled"]:
            # 待投递 ∪ 到期可重试的失败行：重试要"到期"才捞，
            # 否则退避形同虚设、失败一次就立刻再打一次企微接口
            stmt = stmt.where(or_(immediate, retry_due_clause(policy, now)))
        else:
            stmt = stmt.where(immediate)
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
        visible_ids = await _check_process_recipients(own, rows)
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
            if row.id not in visible_ids:
                skipped += 1
                continue
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


def build_digest(rows: list[Notification], *, max_items: int = 20) -> tuple[str, str]:
    """把一批通知合成一条日报（标题, 正文）。

    纯函数：不碰库、不管怎么发。这样"聚合成几条"这件事可以直接断言，
    不必为了测分组去搭一个假的企微客户端。
    """
    shown = rows[:max_items]
    lines = [f"• {row.title}" for row in shown]
    if len(rows) > max_items:
        lines.append(f"…另有 {len(rows) - max_items} 条，请到系统通知中心查看")
    return f"业务日报（{len(rows)} 条）", "\n".join(lines)


async def send_digest(
    *, limit_users: int = 50, max_items: int = 20
) -> dict:
    """把攒着的日报级通知**每个收件人合成一条**发出去（文档 §11.4 验收 24）。

    与 dispatch_pending 的分工：
    - `dispatch_pending` 管即时通道（urgent/normal，以及失败重投）；
    - 这里只管 `level=digest 且 wecom_status=pending` 的行。

    **站内那些行一条都不动**：事件仍然逐条可查（列表、通知中心、时间线都在），
    这里只做两件事——把一个人的明细合成一条消息发出去；发完给这些行盖上
    `digest_at`，于是"主管说没看到某条"能查到它其实是夹在日报里出去的。
    """
    from app.core.config import settings as app_settings
    from app.core.database import SessionLocal
    from app.modules.wecom.client import WeComError, WeComNotConfigured, get_client

    async with SessionLocal() as own:
        policy = await retry_policy(own)
        now = datetime.now(UTC)
        rows = (
            await own.execute(
                select(Notification)
                .where(
                    Notification.wecom_status == "pending",
                    Notification.level == LEVEL_DIGEST,
                )
                .order_by(Notification.id.asc())
            )
        ).scalars().all()
        if not rows:
            return {"users": 0, "messages": 0, "items": 0, "sent": 0, "skipped": 0, "failed": 0}

        visible_ids = await _check_process_recipients(own, rows)
        denied = sum(row.id not in visible_ids for row in rows)
        rows = [row for row in rows if row.id in visible_ids]
        if not rows:
            await own.commit()
            return {"users": 0, "messages": 0, "items": denied, "sent": 0, "skipped": denied, "failed": 0}
        grouped: dict[int, list[Notification]] = {}
        for row in rows:
            grouped.setdefault(row.user_id, []).append(row)
        user_ids = sorted(grouped)[:limit_users]
        users = {
            user.id: user
            for user in (
                await own.execute(select(User).where(User.id.in_(user_ids)))
            ).scalars().all()
        }

        client = get_client()
        ready = bool(app_settings.wecom_agent_id and app_settings.wecom_contact_ready)
        messages = sent = failed = 0
        items = skipped = denied

        for uid in user_ids:
            batch = grouped[uid]
            title, description = build_digest(batch, max_items=max_items)
            messages += 1
            items += len(batch)
            user = users.get(uid)
            if app_settings.wecom_push_off or not ready or user is None or not user.wecom_userid:
                reason = (
                    "推送已临时关闭（WECOM_PUSH_OFF）"
                    if app_settings.wecom_push_off
                    else "未配置企微应用"
                    if not ready
                    else "该用户没有绑定企业微信 userid"
                )
                for row in batch:
                    row.wecom_status = "skipped"
                    row.wecom_error = reason
                    row.digest_at = now
                skipped += len(batch)
                continue
            try:
                await client.send_text_card(
                    to_user=user.wecom_userid, title=title, description=description
                )
            except (WeComNotConfigured, WeComError) as error:
                for row in batch:
                    _mark_failed(row, str(error), policy, now)
                    row.digest_at = now
                failed += len(batch)
            except Exception as error:  # 网络等其它异常同样要落痕
                for row in batch:
                    _mark_failed(row, f"{type(error).__name__}: {error}", policy, now)
                    row.digest_at = now
                failed += len(batch)
            else:
                for row in batch:
                    row.wecom_status = "sent"
                    row.wecom_sent_at = now
                    row.wecom_attempts = (row.wecom_attempts or 0) + 1
                    row.wecom_next_retry_at = None
                    row.digest_at = now
                sent += len(batch)

        await own.commit()
        return {
            "users": len(user_ids),
            "messages": messages,
            "items": items,
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
    # 自动重试关着时，"下一次会被捞走"的条数就是 0：真重试的扫描要 enabled
    # 才会捞 failed 行（见上面的 include_failed and policy["enabled"]）。
    # 这里不跟着开关走的话，页面会显示"N 条会自动重试"却永远不重试。
    queued_retry = 0
    if policy["enabled"]:
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
    business_pending = (await session.execute(select(func.count(BusinessEvent.id)).where(
        BusinessEvent.notification_payload.is_not(None), BusinessEvent.notification_processed_at.is_(None),
    ))).scalar_one()
    return {
        "business_pending": int(business_pending),
        "pending": int(counts.get("pending", 0) or 0),
        "sent": int(counts.get("sent", 0) or 0),
        "failed": int(counts.get("failed", 0) or 0),
        "skipped": int(counts.get("skipped", 0) or 0),
        # 是"failed"的子集：下一次调度就会被自动捞走的条数
        "retrying": int(queued_retry or 0),
        "max_attempts": policy["max_attempts"],
        "auto_retry_enabled": policy["enabled"],
    }
