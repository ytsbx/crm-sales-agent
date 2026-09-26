"""业务对象的可见性校验。

为什么需要它：附件是按 `business_type + business_id` 挂在业务对象上的，
但文件的下载/预览接口只能看到 `file_id`。如果不反查挂载对象，
任何有 `file:view` 的人只要猜一个 id 就能拿到别人的合同、报价单扫描件——
这是典型的横向越权（BOLA）。

规则：**只要文件挂在任意一个"当前用户看得到"的业务对象上，就允许访问。**
一个文件可以同时挂在多个对象上，所以这里是"任一可见即可"，不是"全部可见"。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.modules.customer.model import Customer
from app.modules.file.model import BusinessFile
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote

# business_type -> (模型, 用于数据范围过滤的负责人字段名)
BUSINESS_MODELS: dict[str, tuple[type, str]] = {
    "customer": (Customer, "owner_id"),
    "lead": (Lead, "owner_id"),
    "opportunity": (Opportunity, "owner_id"),
    "quote": (Quote, "owner_id"),
    "order": (SalesOrder, "owner_id"),
}

# 没有负责人概念（或按其它维度管控）的对象类型。
# 附件目前只挂在上面 5 类上；出现未知类型时**默认拒绝**，
# 而不是默认放行——宁可让人来登记新类型，也不要默默漏数据。
NO_OWNER_TYPES: set[str] = set()


async def visible_object(
    session: AsyncSession, user: CurrentUser, *, business_type: str, business_id: int
) -> bool:
    """当前用户能否看到这个业务对象。"""
    entry = BUSINESS_MODELS.get(business_type)
    if entry is None:
        return business_type in NO_OWNER_TYPES
    model, owner_field = entry
    owner_ids = await scoped_owner_ids(session, user)
    stmt = select(model.id).where(model.id == business_id)
    if owner_ids is not None:
        stmt = stmt.where(getattr(model, owner_field).in_(owner_ids))
    return (await session.execute(stmt)).first() is not None


async def can_access_file(
    session: AsyncSession, user: CurrentUser, file_id: int
) -> bool:
    """文件是否允许当前用户访问：挂在任一可见对象上即可。

    没有任何挂载记录的文件（刚上传还没关联）只允许上传者本人访问，
    否则"上传后尚未关联"的文件会变成人人可取的裸文件。
    """
    from app.modules.file.model import FileRecord

    links = (
        await session.execute(
            select(BusinessFile).where(BusinessFile.file_id == file_id)
        )
    ).scalars().all()

    if not links:
        record = await session.get(FileRecord, file_id)
        return record is not None and record.uploaded_by == user.id

    for link in links:
        if await visible_object(
            session, user, business_type=link.business_type, business_id=link.business_id
        ):
            return True
    return False
