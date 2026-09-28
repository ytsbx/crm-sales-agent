"""跟进记录的写入口。

系统自动留痕（领导六阶段口径的"过程记录"）走 `record_system_event`：
- followup_type="系统"，内容带【系统】前缀——人工跟进与自动留痕一眼可分；
- **刻意不更新 customers.last_followup_at**：自动留痕不是销售动作，
  不能把"久未联系"的钟重置掉（否则冷落预警就废了）。

`record_and_notify` = 留痕 + 推业务主管一条龙：报价提交/打样/下单三个事件
在各自端点里一行调用。通知写失败只告警不阻断——业务动作不能被通知拖死。
"""

import logging
from datetime import UTC, datetime

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
        followup_type="系统",
        content=f"【系统】{content}",
        created_at=datetime.now(UTC),
    )
    session.add(row)
    return row


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
    exclude_user_id: int | None = None,
) -> None:
    """过程记录 + 推业务主管（站内/企微，按通知设置走 followup 事件开关）。

    在业务事务内调用：留痕随业务一起 commit；企微投递由调用方
    commit 后 `dispatch_pending`。通知环节异常只告警，不回滚业务。
    """
    from app.modules.notification import service as notification_service

    await record_system_event(
        session,
        customer_id=customer_id,
        owner_id=owner_id,
        content=content,
        quote_id=quote_id,
        order_id=order_id,
        opportunity_id=opportunity_id,
    )
    try:
        # 主管按"负责人所在部门"定位：A 部门的动作不推 B 部门主管；
        # 负责人没有部门时回退为推全公司主管
        from app.modules.user.model import User

        department_id = None
        if owner_id:
            owner_row = await session.get(User, owner_id)
            department_id = owner_row.department_id if owner_row else None
        await notification_service.notify_roles(
            session,
            role_codes=MANAGER_ROLE_CODES,
            type_="followup",
            title=title,
            content=content,
            business_type=business_type,
            business_id=business_id,
            exclude_user_id=exclude_user_id,
            department_id=department_id,
        )
    except Exception as exc:  # noqa: BLE001 —— 通知失败不能挡业务
        logger.warning("自动通知业务主管失败（business=%s %s）：%s",
                       business_type, business_id, exc)
