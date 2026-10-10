"""业务对象的可见性校验。

为什么需要它：附件是按 `business_type + business_id` 挂在业务对象上的，
但文件的下载/预览接口只能看到 `file_id`。如果不反查挂载对象，
任何有 `file:view` 的人只要猜一个 id 就能拿到别人的合同、报价单扫描件——
这是典型的横向越权（BOLA）。

规则：**只要文件挂在任意一个"当前用户看得到"的业务对象上，就允许访问。**
一个文件可以同时挂在多个对象上，所以这里是"任一可见即可"，不是"全部可见"。

2026-10-06 补（§8.5）：这里的"看得到"包含**来源模块的查看权限**。
只守 `file:view` 时，有文件权限、没有 `quote:view` 的人也能从通用入口
（`GET /files/{id}`、`/business/quote/{id}/files`）把报价附件拿走；
挂载/解绑同理，要目标模块的**写入权**。判据集中在下面的 `BUSINESS_PERMISSIONS`。
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
from app.modules.product.model import Product, Sku
from app.modules.quote.model import Quote

# business_type -> (模型, 用于数据范围过滤的负责人字段名)
BUSINESS_MODELS: dict[str, tuple[type, str]] = {
    "customer": (Customer, "owner_id"),
    "lead": (Lead, "owner_id"),
    "opportunity": (Opportunity, "owner_id"),
    "quote": (Quote, "owner_id"),
    "order": (SalesOrder, "owner_id"),
}

#: business_type -> (查看权限, 写入权限)。
#:
#: 为什么需要这张表（2026-10-06 修，§8.5）：附件挂载点是**通用**的
#: （`/business/{type}/{id}/files`）。只守 `file:view` / `file:manage` 的话，
#: 一个有 `file:view` 而**没有** `quote:view` 的人猜一个 file_id 就能从通用入口
#: 拿走报价附件；反过来只有订单权限的人也能拿到报价/打样文件——每个模块都被
#: 开了一个"只认文件权限"的后门。
#:
#: 口径：**附件跟随它挂着的那个业务对象**。看要该对象的查看权，挂/解绑要写入权。
#: 权限码取自各模块自己的路由（不在这里另造）：写入用它们各自的写接口用的那个码，
#: 比如线索的 PATCH 走 `lead:create`、询价的写接口走 `quote:manage`。
#: 写入权限都写**本模块自己的**权限码。现在没有任何模块写 `None` 了：
#: 原来只有 product 写 `None`（表示"由文件中心的 `file:manage` 承担"），
#: 2026-10-07 按业务口径收紧为 `product:manage`，理由见 `NO_OWNER_MODELS` 的注释。
#: （`visible_object` 仍保留 `None` 的分支：将来若有模块确实该由文件权限承担，照旧可用。）
BUSINESS_PERMISSIONS: dict[str, tuple[str, str | None]] = {
    "customer": ("customer:view", "customer:update"),
    "lead": ("lead:view", "lead:create"),
    "opportunity": ("opportunity:view", "opportunity:manage"),
    "quote": ("quote:view", "quote:manage"),
    "order": ("order:view", "order:manage"),
    #: SKU 属于**产品模块**，所以权限码跟产品一致（2026-10-10 加，主人拍板 B 口径）。
    #:
    #: 为什么必须加：在此之前文件中心**不认识 `sku``**，于是给 SKU 上传图片一律
    #: 被兜底拒成 403「它不在你的数据范围内…」—— 措辞像权限问题，真实原因是
    #: "这个类型根本没登记"。而真实可售、客户真正要看图的是**具体型号**：
    #:   `products` 是概念/系列（名称、产品线、品牌、描述，**没有物理属性**），
    #:   `skus` 才是实物（编码、规格、颜色、材质、长宽高、重量、装箱数、MOQ）。
    #: 图片只能挂在概念上、挂不到实物上，等于"同一系列各颜色共用一批图"。
    "sku": ("product:view", "product:manage"),
    "product": ("product:view", "product:manage"),
    "sample": ("sample:view", "sample:manage"),
    "inquiry": ("quote:view", "quote:manage"),
    "order_draft": ("order:view", "order:manage"),
    "contract": ("order:view", "order:manage"),
    "followup": ("followup:view", "followup:create"),
}

#: 没有负责人概念（或按其它维度管控）的对象类型 -> 模型。
#:
#: 产品资料全公司可见，附件跟随资料本身，但**必须真查一次存在性**：
#: 此前这里直接 `return True`，于是给一个**不存在**或**已删除**的产品编号挂附件
#: 也会被放行（脏关联落库后，谁也说不清它挂在哪、也永远列不出来）。
#:
#: 产品附件的**写入**授权在 2026-10-07 收紧为 `product:manage`（业务口径已确认）：
#: 原先只要求 `file:manage`，于是"有文件管理权、但不管产品"的人（例如行政）
#: 也能给产品挂附件、换掉产品上的资料 —— 产品是全站唯一一个"写权限不跟业务模块走"
#: 的例外。现在与客户/报价/订单一致：**谁管产品，谁才管产品上的附件**。
#: 收紧时一并改了三处，漏一处就会出现"按钮能点、接口 403"：
#:   —— `product/router.py` 的 `POST /products/{id}/files`（路由级权限码）；
#:   —— 前端 `AttachmentPanel` 的 `writePermission`（上传按钮的显示条件）；
#:   —— 三个测试（`tests/test_file_access_authorization.py`、
#:      `scripts/check_attachment_business_auth.py`、`scripts/check_data_scope.py`），
#:      它们原来钉的是旧口径。
#: `sku` 与 `product` 同一档：产品资料全公司可见，附件跟随资料本身。
#: 放进这里就走"只查存在性 + 未软删"那条分支（`Sku` 有 `deleted_at`，
#: 已删型号与不存在同等待遇），不用另写可见性逻辑。
NO_OWNER_MODELS: dict[str, type] = {"product": Product, "sku": Sku}

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


#: 打样锁定后**仍允许新增**的附件类别：事后补进来的验收报告、整改说明、
#: 客户反馈截图这类资料。
#: 为什么要留这个口子：如果锁定后一律禁止新增，正常补料也补不进来，
#: 结果大家会把资料塞进备注文字里——那才是真的查不到。
#: 它反过来也是判据：**非此类别的新附件在锁定后一律拒绝**，
#: 这样"制作依据"和"事后补充"在台账上永远分得开。
SUPPLEMENT_CATEGORY = "supplement"
SUPPLEMENT_LABEL = "后续补充资料"


def sample_write_lock_label_for(sample) -> str | None:
    """从**已经加载好的**打样单对象判"附件是否已锁定"；锁定时返回人话原因。

    纯函数、不查库：列表装配时每条都算一次，逐条再 `session.get` 一遍是白花的
    N 次查询（而单据本来就已经在内存里）。

    触发条件（与"开修订版"的口径同源，见 `sample/router.revise_sample`）：
    已登记制作完成（`made_at` 非空），或状态已到寄样/签收。

    为什么需要这道锁（2026-10-06 修）：打样模块自己的写接口会检查历史版本，
    但**通用附件接口只检查"能不能看见这张打样单"**——于是已制作的单子，
    图纸照样能从 `DELETE /business-files/{id}` 解绑。原件保护原来只覆盖
    `signed` / `generated` 两类，图纸不在其中，等于开着一个后门。
    """
    if sample is None:
        return None
    if sample.status in ("shipped", "signed"):
        from app.modules.sample.model import SAMPLE_STATUS_LABEL

        label = SAMPLE_STATUS_LABEL.get(sample.status, sample.status)
        return f"打样单已是「{label}」"
    if sample.made_at is not None:
        return "打样单已登记制作完成"
    return None


async def sample_write_lock_label(session: AsyncSession, business_id: int) -> str | None:
    """同上，但按 id 取一次行（上传闸门那条路用它）。

    ⚠️ 判断逻辑**只在 `sample_write_lock_label_for` 里**，这里只负责取行 ——
    两处各写一份，迟早分叉出一扇绕过的门。
    """
    from app.modules.sample.model import SampleRequest

    return sample_write_lock_label_for(await session.get(SampleRequest, business_id))


async def sample_basis_lock_label(session: AsyncSession, file_id: int) -> str | None:
    """这份文件是否被某张**已锁定**的打样单当作制作依据引用着。

    删除走这一档：解绑只是摘掉一条关联，删文件影响的是文件本身，
    所以要把该文件身上所有打样关联都过一遍，只要有一条落在锁定的单子上就不许删。
    """
    links = (
        await session.execute(
            select(BusinessFile).where(
                BusinessFile.file_id == file_id,
                BusinessFile.business_type == "sample",
            )
        )
    ).scalars().all()
    for link in links:
        reason = await sample_write_lock_label(session, link.business_id)
        if reason is not None:
            return f"{reason}；这份文件是它的制作/过程附件"
    return None


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


def _permitted(user: CurrentUser, code: str | None) -> bool:
    """当前用户是否具备该权限码。

    管理员角色默认放行：与 `core.deps.require_permission` **同一口径**。
    两处不一致会造出"路由放行、附件判定拒绝"这种自相矛盾的 403
    （管理员反而看不到自己刚挂上的附件），而这种错最难排查。
    权限码为 None 表示本模块的写入授权不在这里判（见 BUSINESS_PERMISSIONS）。
    """
    if code is None:
        return True
    return "admin" in user.roles or user.has(code)


async def visible_object(
    session: AsyncSession,
    user: CurrentUser,
    *,
    business_type: str,
    business_id: int,
    write: bool = False,
) -> bool:
    """当前用户能否看到（`write=True` 时：能否改动）这个业务对象上的附件。

    `write=True` 用于挂载/解绑/上传这类**写入**入口：要目标业务对象的写入权，
    不是"能看见就能改"。读取（列表/详情/下载）用默认的 False。
    """
    perms = BUSINESS_PERMISSIONS.get(business_type)
    if perms is None:
        # 没登记的类型默认拒绝，而不是默认放行——宁可让人来登记新类型，
        # 也不要默默漏数据（这条此前由 `NO_OWNER_TYPES` 兜着，语义不变）。
        return False
    view_code, write_code = perms
    if not _permitted(user, write_code if write else view_code):
        # 权限不够时**提前返回**：不查库，越权的人就无法靠响应差异探测对象是否存在
        return False
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
    model = NO_OWNER_MODELS.get(business_type)
    if model is not None:
        row = await session.get(model, business_id)
        # 不存在 / 已软删：与"看不到"同等待遇（都不能再挂新的，也不能读旧的）
        return row is not None and getattr(row, "deleted_at", None) is None
    entry = BUSINESS_MODELS.get(business_type)
    if entry is None:
        return False
    model, owner_field = entry
    owner_ids = await scoped_owner_ids(session, user)
    stmt = select(model.id).where(model.id == business_id)
    deleted_at = getattr(model, "deleted_at", None)
    if deleted_at is not None:
        # 已软删的对象与"不存在"同等待遇：删掉的客户/报价上的附件不再可见，
        # 也不能再往上挂新的。此前这里不过滤，软删对象上的附件照样能下载。
        # （销售订单没有 deleted_at，所以要用 getattr 取，别写死。）
        stmt = stmt.where(deleted_at.is_(None))
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
