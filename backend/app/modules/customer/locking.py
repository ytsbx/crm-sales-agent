"""单据写入与客户改派之间的协调锁。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customer.model import Customer


async def protect_customer_transfer(session: AsyncSession, customer_id: int) -> None:
    """在单据/订单锁之前调用，持有到事务结束。

    PostgreSQL FOR KEY SHARE 与改派的 FOR UPDATE 冲突，但兼容普通进展
    UPDATE 的 NO KEY UPDATE 和外键检查。不用排他的客户锁，避免与已有
    “订单→更新客户进展”路径形成新的反向等待；同客户不同单据仍能并发。
    """
    await session.execute(
        select(Customer).where(Customer.id == customer_id)
        .with_for_update(read=True, key_share=True)
        .execution_options(populate_existing=True)
    )
