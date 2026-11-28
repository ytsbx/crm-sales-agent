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
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.followup.model import FollowUp
from app.modules.notification.model import BusinessEvent

logger = logging.getLogger("crm.followup")

#: "业务主管"的角色。要给别的角色也推，改这里
MANAGER_ROLE_CODES = ["sales_manager"]


async def _insert_business_event_once(
    session: AsyncSession, *, event_key: str, business_type: str | None,
    business_id: int | None, customer_id: int | None, title: str,
) -> BusinessEvent | None:
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else postgresql_insert
    # 只忽略事件键重复；其他数据库错误仍交由外层事务处理。
    statement = insert(BusinessEvent).values(
        event_key=event_key, business_type=business_type, business_id=business_id,
        customer_id=customer_id, title=title, created_at=datetime.now(UTC),
    ).on_conflict_do_nothing(index_elements=[BusinessEvent.event_key]).returning(BusinessEvent)
    return await session.scalar(statement)


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
    if event_key:
        event = await _insert_business_event_once(
            session, event_key=event_key, business_type=business_type,
            business_id=business_id, customer_id=customer_id, title=title,
        )
        if event is None:
            logger.info("业务事件重放跳过（event_key=%s）", event_key)
            return False

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
    """过程记录（客户时间线），**不推送主管**。

    主管需要时自己查看时间线/单据即可（2026-11-28 主人确认：业务动作也不再
    逐条推主管）。幂等（场景04）：调用点传确定性 event_key（如 order:create:42），
    同一 key 的重放/重试整体跳过——不留痕，客户时间线只一条；业务事件本身落
    business_events 表（唯一约束兜底并发）。
    在业务事务内调用：留痕随业务一起 commit。主管不发推送，需要时自己查看。
    """
    identity = sample_id or (business_id if business_type == "sample" else None)
    if identity is not None:
        from app.core.draft_visibility import private_draft
        from app.modules.sample.model import SampleRequest

        if await session.scalar(select(private_draft(SampleRequest)).where(SampleRequest.id == identity)):
            return
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
