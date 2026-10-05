"""跟进记录的写入口。

系统自动留痕（领导六阶段口径的"过程记录"）走 `record_system_event`：
- followup_type="系统"，内容带【系统】前缀——人工跟进与自动留痕一眼可分；
- **刻意不更新 customers.last_followup_at**：自动留痕不是销售动作，
  不能把"久未联系"的钟重置掉（否则冷落预警就废了）。

`record_and_notify` = 留痕 + 推业务主管一条龙：报价提交/打样/下单三个事件
在各自端点里一行调用。通知写失败只告警不阻断——业务动作不能被通知拖死。
"""

import logging
from uuid import uuid4
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.followup.model import FollowUp

logger = logging.getLogger("crm.followup")

#: "业务主管"的角色。要给别的角色也推，改这里
MANAGER_ROLE_CODES = ["sales_manager"]


async def record_system_event(
    session: AsyncSession,
    *,
    customer_id: int | None,
    content: str,
    owner_id: int | None = None,
    quote_id: int | None = None,
    order_id: int | None = None,
    opportunity_id: int | None = None,
    sample_id: int | None = None,
) -> FollowUp | None:
    """模块动作发生时自动写一条跟进留痕。没有客户归属（customer_id 为空）就跳过。"""
    if not customer_id:
        return None
    row = FollowUp(
        customer_id=customer_id,
        owner_id=owner_id,
        quote_id=quote_id,
        order_id=order_id,
        opportunity_id=opportunity_id,
        sample_id=sample_id,
        followup_type="系统",
        content=f"【系统】{content}",
        created_at=datetime.now(UTC),
    )
    session.add(row)
    return row


async def record_progress_event(
    session: AsyncSession,
    *,
    customer_id: int | None,
    operator_id: int | None,
    title: str,
    content: str,
    business_type: str | None = None,
    business_id: int | None = None,
    quote_id: int | None = None,
    order_id: int | None = None,
    opportunity_id: int | None = None,
    sample_id: int | None = None,
    event_key: str | None = None,
) -> bool:
    """同事务记录业务事实及进展时钟；不通知、不刷新客户联系时钟。"""
    from app.modules.notification.model import BusinessEvent

    if event_key:
        existing = (
            await session.execute(
                select(BusinessEvent.id).where(BusinessEvent.event_key == event_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("业务事件重放跳过（event_key=%s）", event_key)
            return False
        session.add(
            BusinessEvent(
                event_key=event_key,
                business_type=business_type,
                business_id=business_id,
                customer_id=customer_id,
                title=title,
                created_at=datetime.now(UTC),
            )
        )
        await session.flush()

    # 业务进展时钟（文档 §2.3/§11.2）：走到这里的动作（建/转订单、报价提交、
    # 打样建/寄、确认交期、实发、确认回款）都算客户活跃，冷落扫描与公海回收不应只盯手工跟进
    from app.modules.customer import service as customer_service

    await customer_service.touch_progress(session, customer_id)
    await record_system_event(
        session,
        customer_id=customer_id,
        owner_id=operator_id,
        content=content,
        quote_id=quote_id,
        order_id=order_id,
        opportunity_id=opportunity_id,
        sample_id=sample_id,
    )
    return True


async def record_and_notify(
    session: AsyncSession,
    *,
    customer_id: int | None,
    owner_id: int | None,
    title: str,
    content: str,
    business_type: str | None = None,
    business_id: int | None = None,
    quote_id: int | None = None,
    order_id: int | None = None,
    opportunity_id: int | None = None,
    sample_id: int | None = None,
    operator_id: int | None = None,
    exclude_user_id: int | None = None,
    event_key: str | None = None,
) -> None:
    """过程记录 + 推业务主管（站内/企微，按通知设置走 followup 事件开关）。

    幂等（场景04）：调用点传确定性 event_key（如 order:create:42），
    同一 key 的重放/重试整体跳过——不留痕、不推送，客户时间线只一条、
    主管只收一次。业务事件本身落 business_events 表（唯一约束兜底并发）。
    在业务事务内调用：留痕随业务一起 commit；企微投递由调用方
    commit 后 `dispatch_pending`。通知环节异常只告警，不回滚业务。
    """
    event_key = event_key or f"progress:{uuid4().hex}"
    recorded = await record_progress_event(
        session, customer_id=customer_id,
        operator_id=operator_id if operator_id is not None else owner_id,
        title=title, content=content, business_type=business_type,
        business_id=business_id, quote_id=quote_id, order_id=order_id,
        opportunity_id=opportunity_id, sample_id=sample_id, event_key=event_key,
    )
    if not recorded:
        return
    source_type, source_id = (
        ("sample", sample_id) if sample_id else
        ("order", order_id) if order_id else
        ("quote", quote_id) if quote_id else
        ("opportunity", opportunity_id) if opportunity_id else
        (business_type, business_id) if business_type and business_id else
        ("customer", customer_id)
    )
    await queue_manager_notification(
        session, event_key=event_key, customer_id=customer_id, owner_id=owner_id,
        operator_id=operator_id if operator_id is not None else owner_id,
        title=title, content=content, source_type=source_type, source_id=source_id,
        exclude_user_id=exclude_user_id,
    )


async def queue_manager_notification(
    session: AsyncSession, *, event_key: str, customer_id: int | None,
    owner_id: int | None, operator_id: int | None, title: str, content: str,
    source_type: str, source_id: int | None, exclude_user_id: int | None = None,
    required_permission: str | None = None,
) -> None:
    """持久化通知待办；生成失败留在事件上，重试不再创建跟进或业务单。"""
    from app.modules.customer.model import Customer
    from app.modules.notification.model import BusinessEvent
    from app.modules.notification import service as notification_service
    from app.modules.user.model import User

    event = (await session.execute(select(BusinessEvent).where(
        BusinessEvent.event_key == event_key))).scalar_one_or_none()
    if event is None:
        event = BusinessEvent(event_key=event_key, customer_id=customer_id,
                              business_type=source_type, business_id=source_id,
                              title=title, created_at=datetime.now(UTC))
        session.add(event)
        await session.flush()
    if event.notification_payload is not None:
        return
    owner = await session.get(User, owner_id) if owner_id else None
    actor = await session.get(User, operator_id) if operator_id else None
    customer = await session.get(Customer, customer_id) if customer_id else None
    event.notification_payload = {
        "department_id": owner.department_id if owner else None,
        "customer_id": customer_id,
        "source_type": source_type, "source_id": source_id,
        "exclude_user_id": exclude_user_id, "required_permission": required_permission,
        "title": title,
        "content": f"客户：{customer.name if customer else '未关联'}；"
                   f"操作者：{actor.name if actor else '系统'}；"
                   f"负责人：{owner.name if owner else '未分配'}；{content}",
    }
    await session.flush()
    await notification_service.materialize_business_notifications(session, event_id=event.id)
