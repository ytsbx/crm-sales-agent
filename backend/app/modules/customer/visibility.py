"""客户可见性的统一判据（只认跟进人，不认默认负责人）。

- 管理员/财务等 `data_scope=all`：全部客户可见；
- 其余：任一有效跟进人在数据范围内即可见；
- 公海客户（`pool_status='public'`）对所有有 `customer:view` 的人可见；
- `customers.owner_id` 是历史字段，这里**不再读取**（2026-11-27 主人确认不要默认负责人）；
- 业务单据自身的可见性仍按单据负责人（本模块不改变单据口径）。

所有"这个客户能不能看"的入口都应走这里，避免各自拼 `Customer.owner_id in ...`
出现"跟进人能看客户、却打不开客户资料/附件/合同"的两套答案。
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer, CustomerFollower


def customer_scope_clause_from(owner_ids: list[int] | None):
    """已经取到 `scoped_owner_ids` 时使用；None 表示不做范围过滤。"""
    if owner_ids is None:
        return None
    return or_(
        Customer.pool_status == "public",
        Customer.id.in_(
            select(CustomerFollower.customer_id).where(
                CustomerFollower.user_id.in_(owner_ids),
                CustomerFollower.deleted_at.is_(None),
            )
        ),
    )


async def customer_scope_clause(session: AsyncSession, user):
    """按当前用户数据范围生成客户过滤条件；None 表示不过滤。"""
    return customer_scope_clause_from(await scoped_owner_ids(session, user))


async def assert_customer_in_scope(
    session: AsyncSession, user, customer: Customer
) -> None:
    """客户不在可见范围时抛 403（与 `customer/service.assert_customer_visible` 同口径）。"""
    if customer.pool_status == "public":
        return
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return
    is_follower = (
        await session.execute(
            select(CustomerFollower.id)
            .where(
                CustomerFollower.customer_id == customer.id,
                CustomerFollower.user_id.in_(owner_ids),
                CustomerFollower.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).first()
    if is_follower is not None:
        return
    raise AppError(
        ErrorCode.DATA_SCOPE_DENIED, "该客户不在你的数据范围内", 403
    )
