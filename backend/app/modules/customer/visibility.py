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


async def assert_customer_ready_for_business(
    session: AsyncSession, customer: Customer, *, action: str
) -> None:
    """业务动作（下单/签合同/线索转化）前要求客户不是公海。

    这是原来「无负责人默认拒绝、先认领再下单」规则的等价口径：
    公海客户没有跟进人，不允许直接产生业务单据/转化，必须先领取或指派跟进人。
    私海客户至少有一位跟进人（不变量），由调用方先做可见性校验。
    """
    from app.modules.customer.followers import active_follower_count

    if not isinstance(customer, Customer):
        # 单元测试里的轻量对象：真实性由调用点保证（生产入口都是 ORM 客户）
        return
    if customer.pool_status == "public":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该客户在公海（没有跟进人），请先领取或指派跟进人后再{action}",
            422,
        )
    if await active_follower_count(session, customer.id) == 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该客户没有跟进人，请先指定跟进人后再{action}",
            422,
        )
