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
from app.core.errors import AppError
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

# 没有负责人概念（或按其它维度管控）的对象类型：
# 产品资料全公司可见（product:view 已经在路由层把守），附件跟随资料本身。
# 附件目前挂在上面 5 类 + product 上；出现未知类型时**默认拒绝**，
# 而不是默认放行——宁可让人来登记新类型，也不要默默漏数据。
NO_OWNER_TYPES: set[str] = {"product"}

#: 不可破坏的原件类别 → 人话标签（第一批返修 §3.1 要求"三条路径统一判断"）。
#: - `signed`：已签合同的签署扫描件，是"签的是哪一版"的唯一证据；
#: - `generated`：系统生成即落盘的合同生成稿，下载承诺"同一编号永远同一份"，
#:   删掉之后下载会**静默**回退成按当前资料重新渲染。
#: 判据集中在这一处，删除 / 解绑 / 以后任何会动到原件的入口都用同一份；
#: 各写一份判断迟早会分叉，分叉出来的那一份就是绕过通道。
PROTECTED_CATEGORIES: dict[str, str] = {
    "signed": "已签署的原件",
    "generated": "系统生成的原件",
}


def protection_label(category: str | None) -> str | None:
    """这条业务引用的类别是否受保护；受保护时返回人话标签，否则 None。"""
    return PROTECTED_CATEGORIES.get((category or "").strip())


async def file_protection_label(session: AsyncSession, file_id: int) -> str | None:
    """**文件级**判断：这份文件是否受"原件不可破坏"保护（任意一条受保护引用即算）。

    删除走这一档：删文件影响它身上**所有**引用，所以只要有一条受保护的引用，
    整份文件就不能被通用删除。
    """
    links = (
        await session.execute(select(BusinessFile).where(BusinessFile.file_id == file_id))
    ).scalars().all()
    for link in links:
        label = protection_label(link.category)
        if label is not None:
            return label
    return None

#: 有独立可见性入口、但没有统一 owner_id 列的业务类型。
#: 它们各自已有一套"数据范围 + 越权"判定，这里**复用那一套**，
#: 不在本文件重写第二份口径——两处口径迟早会分叉。
#: 名称是与前端、钉钉 OA 取图（`inquiry_file_business_type`，默认 `inquiry`）
#: 共用的字符串，改一处就得三处一起改。
DELEGATED_TYPES: set[str] = {"inquiry", "sample", "order_draft"}


async def visible_object(
    session: AsyncSession, user: CurrentUser, *, business_type: str, business_id: int
) -> bool:
    """当前用户能否看到这个业务对象。"""
    if business_type == "followup":
        from app.modules.followup.visibility import get_visible_followup

        try:
            await get_visible_followup(session, user, business_id)
            return True
        except AppError:
            return False
    # 合同文档不存负责人快照：可见性实时跟客户**当前**负责人走（场景14——
    # 换负责人后新负责人按权限查看历史原件，原负责人按数据范围失去访问）
    if business_type == "contract":
        from app.modules.contract.model import ContractDocument

        doc = await session.get(ContractDocument, business_id)
        if doc is None or doc.deleted_at is not None:
            return False
        customer = await session.get(Customer, doc.customer_id)
        if customer is None:
            return False
        owner_ids = await scoped_owner_ids(session, user)
        return owner_ids is None or customer.owner_id in owner_ids
    # 询价 / 打样 / 订单草稿：复用它们自己的可见性入口。
    # 没有这一段的后果是**上传和挂载都直接被拒**——钉钉 OA 要求先有询价图纸，
    # 而标准附件入口挂不上 `inquiry`，那条流程从入口就断了。
    if business_type in DELEGATED_TYPES:
        try:
            if business_type == "inquiry":
                from app.modules.inquiry import service as inquiry_service

                await inquiry_service.get_visible_or_404(session, user, business_id)
            elif business_type == "sample":
                from app.modules.sample import service as sample_service

                await sample_service.get_visible_or_404(session, user, business_id)
            else:  # order_draft
                from app.modules.order import drafts as order_drafts

                await order_drafts.get_visible(session, user, business_id)
        except AppError:
            return False
        return True
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
