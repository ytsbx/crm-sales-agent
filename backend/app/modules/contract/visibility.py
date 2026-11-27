"""合同文档的**统一可见性判据**（第三批审查 P2，2026-10-12）。

为什么单独一个模块：合同的详情、生成稿、签署附件（走通用文件入口
`file/access.py`）必须用**同一套**规则，否则会出现"详情点得开、附件下不了"
这种自相矛盾的状态 —— 实测反例：客户被超期回收进公海后负责人为空，
详情按"订单负责人"回退放行，附件却只认客户当前负责人，于是已签合同的原件
谁也取不出来。

口径（详情那边早就确认过的，这里只把它抽出来给所有入口共用）：

    客户**当前**负责人 → 有订单就跟订单负责人 → 都没有就跟当初创建这份合同的人

都没有才落回"无归属"（各入口按自己的语义处理：详情按数据范围拒绝，
附件同理拒）。**不含**权限码判定 —— 那是各模块自己的事（详情要 `contract:view`，
附件要 `file:view`），两件事不能混成一件。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.draft_visibility import ensure_draft_access
from app.core.errors import AppError
from app.modules.contract.model import ContractDocument
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder


async def contract_scope_owner_id(
    session: AsyncSession, doc: ContractDocument
) -> int | None:
    """这份合同按**谁**的数据范围判定；返回 None 表示"没有任何归属线索"。

    客户可能被"超期未跟进自动回收"进公海、或被合并而清空负责人 —— 合同本身
    是已发生的商务事实，不能因为客户进公海就谁都点不开。
    """
    customer = await session.get(Customer, doc.customer_id)
    owner_id = customer.owner_id if customer is not None else None
    if owner_id is None and doc.order_id:
        order = await session.get(SalesOrder, doc.order_id)
        owner_id = order.owner_id if order is not None else None
    if owner_id is None:
        owner_id = doc.created_by
    return owner_id


async def contract_in_scope(session: AsyncSession, user, doc: ContractDocument) -> bool:
    """当前用户的数据范围能不能覆盖这份合同（不判权限码）。"""
    from app.core.data_scope import scoped_owner_ids

    try:
        if await ensure_draft_access(session, ContractDocument, doc.id, user.id):
            return True
    except AppError:
        return False
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:  # None = 看全部
        return True
    # 多跟进人：客户当前负责人或任一有效跟进人在范围内，合同可见
    from app.modules.customer.model import CustomerFollower

    customer = await session.get(Customer, doc.customer_id)
    if customer is not None:
        if customer.owner_id is not None and int(customer.owner_id) in owner_ids:
            return True
        follower = await session.scalar(
            select(CustomerFollower.id)
            .where(
                CustomerFollower.customer_id == customer.id,
                CustomerFollower.user_id.in_(owner_ids),
                CustomerFollower.deleted_at.is_(None),
            )
            .limit(1)
        )
        if follower is not None:
            return True
    owner_id = await contract_scope_owner_id(session, doc)
    return owner_id is not None and owner_id in owner_ids
