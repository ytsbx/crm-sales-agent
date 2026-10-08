"""产品中心接口（对齐 03-API §14 / §15）。

文件末尾另有一组第八批 §8.14 的接口（前缀 `/sku-master`）：
在产 SKU 的字段来源、待核实状态、差异队列与人工确认。

为什么另起一个前缀而不是挂在 `/skus/{sku_id}` 下面：路由是**按注册顺序**匹配的，
`/skus/master/...` 这样的路径会被先注册的 `/skus/{sku_id}` 吃掉（sku_id="master"
直接 422）。换个前缀比调整注册顺序更不容易被后来的人改坏。
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.integration.model import IntegrationDiff
from app.modules.product import master as master_svc
from app.modules.product import service as svc
from app.modules.product.model import Product, Sku
from app.modules.product.schema import (
    ProductCreate,
    ProductUpdate,
    SkuCreate,
    SkuAuthorityRequest,
    SkuIngestRequest,
    SkuMasterDiffConfirmRequest,
    SkuRenameRequest,
    SkuStandaloneCreate,
    SkuStopRequest,
    SkuUpdate,
)
from app.modules.recycle import service as recycle_svc

router = APIRouter(tags=["Product"])


async def _sku_counts(session: AsyncSession, product_ids: list[int]) -> dict[int, int]:
    if not product_ids:
        return {}
    stmt = (
        select(Sku.product_id, func.count(Sku.id))
        .where(Sku.product_id.in_(product_ids), Sku.deleted_at.is_(None))
        .group_by(Sku.product_id)
    )
    return {int(pid): int(count) for pid, count in (await session.execute(stmt)).all()}


# ---------------------------------------------------------------- 产品

@router.get("/products")
async def list_products(
    keyword: str | None = None,
    status: str | None = None,
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = svc.build_product_stmt(keyword=keyword, status=status, category=category)
    rows, total = await paginate(session, stmt, page, page_size)
    counts = await _sku_counts(session, [p.id for p in rows])
    items = [svc.serialize_product(p, sku_count=counts.get(p.id, 0)) for p in rows]
    return ok(page_data(items, total, page, page_size))


@router.post("/products")
async def create_product(
    payload: ProductCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    product = Product(**payload.model_dump(), created_by=user.id)
    session.add(product)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="product",
        business_id=product.id,
        after=svc.serialize_product(product),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_product(product), "产品已创建")


@router.get("/products/{product_id}")
async def get_product(
    product_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    product = await svc.get_product_or_404(session, product_id)
    counts = await _sku_counts(session, [product.id])
    return ok(svc.serialize_product(product, sku_count=counts.get(product.id, 0)))


@router.patch("/products/{product_id}")
async def update_product(
    product_id: int,
    payload: ProductUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    product = await svc.get_product_or_404(session, product_id)
    before = svc.serialize_product(product)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="product",
        business_id=product.id,
        before=before,
        after=svc.serialize_product(product),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_product(product), "已保存")


@router.delete("/products/{product_id}")
async def delete_product(
    product_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """软删产品（连带软删它名下的 SKU）。

    **先锁产品、再扫 SKU**（锁序见 `svc.lock_product`）。不加锁的话，
    "正在删产品"与"同时恢复它名下的某个 SKU"会各看各的旧世界：
    产品删掉了、SKU 却被恢复成有效，留下挂在已删产品下的孤儿。

    留痕写**两处**：产品自己一条（这次删产品的动作），被连坐删掉的 SKU
    **每个各一条**。少了 SKU 那几条，回收站要回答"这条 SKU 是怎么没的"
    就只能拿产品那条留痕的时间去和 SKU 的删除时间比谁近 —— 一条几个月前
    就删掉的 SKU 会因此被算到今天删产品的人头上（回收站复审 RB07）。
    """
    product = await svc.lock_product(session, product_id)
    if product is None or product.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)
    before = svc.serialize_product(product)
    cascaded = await svc.delete_product(session, product)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="product",
        business_id=product.id,
        before=before,
        ip=client_ip(request),
    )
    for sku in cascaded:
        await write_audit(
            session,
            operator_id=user.id,
            action="delete",
            business_type="sku",
            business_id=sku.id,
            before={"sku_code": sku.sku_code, "product_id": product.id},
            # `via` 说清"是随产品删的"，`deleted_at` 把当时那个值钉下来 ——
            # 回收站据此**确认**这条留痕就是本次删除留下的（`_audit_is_this_deletion`），
            # 不必再靠"时间谁最近"去猜。单独删 SKU 时写 `via=direct`（见 delete_sku），
            # 两边合起来，SKU 的两种死法都有据可查。
            after={
                "via": "product_delete",
                "product_id": product.id,
                "deleted_at": sku.deleted_at.isoformat(),
            },
            ip=client_ip(request),
        )
    await session.commit()
    return ok(None, "产品已删除")


@router.post("/products/{product_id}/restore")
async def restore_product(
    product_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """从回收站恢复产品，连带把它下面被删的 SKU 一起捡回来。

    取数**带行锁**（`svc.lock_product`）：恢复要改 `deleted_at`、还要扫一遍它名下的
    SKU 逐个恢复，不加锁的话"正在恢复产品"与"同时去删它的 SKU"交叉，可能留下半截状态。
    锁序固定「先产品、后 SKU」，四个入口（删产品 / 恢复产品 / 恢复 SKU / 新建 SKU）共用。

    撞码的 SKU 单独跳过、在结果里报出来（见 `recycle.service.restore_product`）。
    """
    row = await svc.lock_product(session, product_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)

    result = await recycle_svc.restore_product(session, row)
    await write_audit(
        session,
        operator_id=user.id,
        action="restore",
        business_type="product",
        business_id=row.id,
        after=result,
        ip=client_ip(request),
    )
    await session.commit()
    skipped = len(result["skipped_skus"])
    if skipped:
        return ok(result, f"产品已恢复；有 {skipped} 个 SKU 因编码被占用未恢复")
    return ok(result, "产品已恢复")


# ---------------------------------------------------------------- SKU

@router.get("/products/{product_id}/skus")
async def list_product_skus(
    product_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    product = await svc.get_product_or_404(session, product_id)
    stmt = (
        select(Sku)
        .where(Sku.product_id == product_id, Sku.deleted_at.is_(None))
        .order_by(Sku.id.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return ok([svc.serialize_sku(sku, product_name=product.name) for sku in rows])


@router.post("/products/{product_id}/skus")
async def create_sku(
    product_id: int,
    payload: SkuCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新增 SKU。

    **带产品行锁**：产品有效才允许挂 SKU，而"读一眼产品还在不在"和"真的插进去"
    之间隔着一段时间 —— 期间产品可能正好被删掉（连它名下 SKU 一起软删），
    插入就会落成挂在已删产品下的孤儿。锁住产品行，这两步就与产品删除串起来了。
    """
    product = await svc.lock_product(session, product_id)
    if product is None or product.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)
    await svc.ensure_sku_code_unique(session, payload.sku_code)
    sku = Sku(**payload.model_dump(), product_id=product.id)
    session.add(sku)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="sku",
        business_id=sku.id,
        after=svc.serialize_sku(sku, product_name=product.name),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_sku(sku, product_name=product.name), "SKU 已创建")


@router.get("/skus")
async def list_skus(
    keyword: str | None = None,
    product_id: int | None = None,
    status: str | None = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(Sku, Product.name).join(Product, Product.id == Sku.product_id).where(
        Sku.deleted_at.is_(None)
    )
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            Sku.sku_code.ilike(like) | Sku.name.ilike(like) | Sku.specification.ilike(like)
        )
    if product_id:
        stmt = stmt.where(Sku.product_id == product_id)
    if status:
        stmt = stmt.where(Sku.status == status)
    stmt = stmt.order_by(Sku.id.desc())

    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (
        await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).all()
    items = [svc.serialize_sku(sku, product_name=name) for sku, name in rows]
    return ok(page_data(items, total, page, page_size))


@router.patch("/skus/{sku_id}")
async def update_sku(
    sku_id: int,
    payload: SkuUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    sku = await svc.get_sku_or_404(session, sku_id)
    data = payload.model_dump(exclude_unset=True)
    if "sku_code" in data:
        await svc.ensure_sku_code_unique(session, data["sku_code"], exclude_id=sku.id)
    before = svc.serialize_sku(sku)
    for field, value in data.items():
        setattr(sku, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="sku",
        business_id=sku.id,
        before=before,
        after=svc.serialize_sku(sku),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_sku(sku), "已保存")


@router.post("/skus/{sku_id}/enable")
async def enable_sku(
    sku_id: int,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    sku = await svc.get_sku_or_404(session, sku_id)
    sku.status = "active"
    await write_audit(
        session, operator_id=user.id, action="enable", business_type="sku", business_id=sku.id
    )
    await session.commit()
    return ok(None, "SKU 已启用")


@router.post("/skus/{sku_id}/disable")
async def disable_sku(
    sku_id: int,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    sku = await svc.get_sku_or_404(session, sku_id)
    sku.status = "disabled"
    await write_audit(
        session, operator_id=user.id, action="disable", business_type="sku", business_id=sku.id
    )
    await session.commit()
    return ok(None, "SKU 已停用")


@router.delete("/skus/{sku_id}")
async def delete_sku(
    sku_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    from datetime import UTC, datetime

    sku = await svc.get_sku_or_404(session, sku_id)
    before = svc.serialize_sku(sku)
    sku.deleted_at = datetime.now(UTC)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="sku",
        business_id=sku.id,
        before=before,
        # `via=direct` 表明"这条 SKU 是被人单独删的"（随产品删的那几条写
        # `via=product_delete`，见 delete_product）；`deleted_at` 把当时的值钉下来，
        # 回收站据此确认这条留痕就是本次删除留下的，不用靠时间猜。
        after={"via": "direct", "deleted_at": sku.deleted_at.isoformat()},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "SKU 已删除")


@router.post("/skus/{sku_id}/restore")
async def restore_sku(
    sku_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """从回收站恢复单个 SKU。

    它的产品必须**已经恢复**：产品还在回收站时恢复 SKU，等于造出一个挂在
    已删产品下的孤儿 —— 产品列表里根本看不到它（口径见
    `recycle.service.restore_sku`）。这种情况直接让用户先恢复产品。

    ⚠️ **锁序：「先产品、后 SKU」**，与删除产品 / 恢复产品 / 新增 SKU 一致。
    所以这里是"先只读一下 product_id（不加锁）→ 锁产品 → 再锁 SKU"，
    而不是先锁 SKU 再补产品锁 —— 后者与"恢复产品"方向相反，两边同时进行时死锁。
    锁住产品之后 SKU 还要**重新读一遍**（`populate_existing`），
    否则拿到的可能是锁之前读进内存的旧值。
    """
    product_id = (
        await session.execute(select(Sku.product_id).where(Sku.id == sku_id))
    ).scalar_one_or_none()
    if product_id is None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)

    product = await svc.lock_product(session, int(product_id))
    row = (
        await session.execute(
            select(Sku)
            .where(Sku.id == sku_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)

    await recycle_svc.restore_sku(session, row, product=product)
    await write_audit(
        session,
        operator_id=user.id,
        action="restore",
        business_type="sku",
        business_id=row.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "SKU 已恢复")


# ------------------- 03-API §14 §15 补齐：SKU 单条与产品子资源
#
# 这几条多数是"文档有路径、代码有等价能力"的补齐（SKU 的增改删原本挂在
# `/products/{id}/skus` 下），但 **产品附件与知识库是文档明确要求、
# 代码完全没有的**：产品详情页要看图纸/检测报告与产品知识。


@router.get("/skus/{sku_id}")
async def get_sku(
    sku_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """单条 SKU（03-API §15）。"""
    sku = await svc.get_sku_or_404(session, sku_id)
    product = await session.get(Product, sku.product_id)
    return ok({**svc.serialize_sku(sku), "product_name": product.name if product else None})


@router.post("/skus")
async def create_standalone_sku(
    payload: SkuStandaloneCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新增 SKU（03-API §15 的扁平写法）。

    与 `/products/{id}/skus` 同理：产品 id 在 URL 上的那种写法的扁平版，
    所以这里 product_id 必须在请求体里给。两者共用同一套校验
    （产品存在 + SKU 编码唯一），不会出现"从哪个入口进来规则不一样"。

    ⚠️ **也要先取产品行锁**，理由与嵌套写法一字不差：产品有效才允许挂 SKU，
    而"读一眼产品还在不在"和"真的插进去"之间隔着一段时间 —— 期间产品可能正好
    被删掉（连它名下 SKU 一起软删），插入就落成挂在已删产品下的孤儿。
    这里原先走的是**不带锁**的 `get_product_or_404`：同一个规矩、两个入口两套写法，
    等于把漏洞留在了没跟着改的那扇门上（并发下表现为"产品已删、SKU 还在"）。
    """
    product = await svc.lock_product(session, payload.product_id)
    if product is None or product.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)
    await svc.ensure_sku_code_unique(session, payload.sku_code)
    data = payload.model_dump(exclude={"product_id"})
    sku = Sku(**data, product_id=product.id)
    session.add(sku)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="sku",
        business_id=sku.id,
        after=svc.serialize_sku(sku, product_name=product.name),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_sku(sku, product_name=product.name), "SKU 已创建")


@router.get("/products/{product_id}/files")
async def list_product_files(
    product_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """产品的附件（03-API §14）：图纸、检测报告、认证证书等。

    复用通用附件表（`business_files`），与客户/商机的附件是同一套机制，
    不另建一张"产品文档表"。
    """
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.file.router import is_previewable

    product = await session.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)

    rows = (
        await session.execute(
            select(BusinessFile, FileRecord)
            .join(FileRecord, FileRecord.id == BusinessFile.file_id)
            .where(
                BusinessFile.business_type == "product",
                BusinessFile.business_id == product_id,
            )
            .order_by(BusinessFile.id.desc())
        )
    ).all()
    return ok(
        [
            {
                "business_file_id": link.id,
                "file_id": stored.id,
                "name": stored.file_name,
                "mime_type": stored.mime_type,
                "size": stored.size,
                "category": link.category,
                "remark": link.remark,
                "created_at": stored.created_at,
                "previewable": is_previewable(stored),
            }
            for link, stored in rows
        ]
    )


@router.post("/products/{product_id}/files")
async def attach_product_file(
    product_id: int,
    request: Request,
    file_id: int = Query(..., description="先调 POST /files/upload 拿到的文件 id"),
    category: str | None = None,
    remark: str | None = None,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """给产品挂附件（03-API §14）：图纸、检测报告、认证证书等（POST）。

    与 `POST /business/product/{id}/files` 是同一份实现的两个入口 ——
    产品详情页用这个更自然。底层都是 `business_files`，不另建表。
    """
    from app.modules.file.model import BusinessFile, FileRecord

    product = await session.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        # 存在性单独一条 404：前端要能区分"这个产品没了"和"你没权限"。
        # 已软删的产品与不存在同等待遇——不能再往上挂附件。
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, f"文件 id={file_id} 不存在", 404)

    # 与 `POST /business/{business_type}/{business_id}/files` 同一纪律：**两个方向都要校验**。
    # 这里曾经只校验目标产品存在、不校验源文件可见性，于是成了越权下载通道——
    # `product` 属 NO_OWNER_MODELS（对全体可见），而 `can_access_file` 只要有一条可见关联就放行，
    # 所以"挂一条关联"本身就等于授权：把别人的 file_id 挂到任意产品上即可下载别人的原件。
    from app.modules.file.access import can_access_file, visible_object

    if not await can_access_file(session, user, file_id):
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "该文件不在你的可见范围内", 403)
    # 目标侧也走**同一份判据**（不再在这里手写"产品在不在"）：产品附件的查看权是
    # `product:view`、写入权是 `product:manage`（2026-10-07 收紧，与路由级权限码
    # 是同一个；见 access.NO_OWNER_MODELS 的说明）。
    if not await visible_object(
        session, user, business_type="product", business_id=product_id, write=True
    ):
        raise AppError(
            ErrorCode.DATA_SCOPE_DENIED, "不能给该产品挂附件：你没有产品资料的维护权限", 403
        )

    # 给一份**已存在**的文件新增引用之前先锁它的行：与删除入口（通用删除、
    # 回款删凭证）串行化，避免"检查时没人引用、删掉后才挂上来"的悬空引用
    # （第十一批 11.2 第 7 条）。
    from app.modules.file import service as file_service

    # ⚠️ 拿到锁之后**必须用重读到的这一条**（2026-10-08 复审 11.2）：上面那次
    # `session.get` 是加锁前的快照，而"删文件"完全可以在这两步之间提交 ——
    # 只用锁不用重读，等锁等到了文件却已经被删，照样插一条悬空关联。
    # 返回 None = 这份文件已经不在库里了。
    record = await file_service.lock_file_row(session, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, f"文件 id={file_id} 不存在", 404)
    link = BusinessFile(
        business_type="product",
        business_id=product_id,
        file_id=file_id,
        category=category,
        remark=remark,
    )
    session.add(link)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="attach",
        business_type="product",
        business_id=product_id,
        after={"file_id": file_id, "file_name": record.file_name, "category": category},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {"business_file_id": link.id, "file_id": file_id, "name": record.file_name},
        "已关联",
    )


@router.get("/products/{product_id}/knowledge")
async def product_knowledge(
    product_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """产品知识（03-API §14），供销售查阅与 AI 检索引用。

    字段就是产品上的 `knowledge`（PRD 里叫"产品资料/卖点/常见问题"），
    这里单独出一个接口是因为详情页的"知识"标签要能独立刷新，
    也方便以后换成结构化知识库而不改前端。
    """
    product = await session.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)
    return ok(
        {
            "product_id": product.id,
            "name": product.name,
            "description": product.description,
            "knowledge": product.knowledge,
            # 界面上没填知识时给个明确提示，而不是显示空白让人以为加载失败
            "has_content": bool((product.knowledge or "").strip()),
        }
    )


# ================================================================ §8.14
# 在产 SKU 的权威字段、来源时间与差异确认。
#
# 权限口径：读要 `product:view`；凡是**改主数据或定口径**的动作（接收来源、改码、
# 停用、登记字段权威、核定差异）一律要 `product:manage`，并且每个动作都写审计 ——
# 这些动作会改掉正在报价用的口径，必须能回答"谁在什么时候改的、依据什么"。


@router.get("/sku-master/skus/{sku_id}")
async def sku_master_overview(
    sku_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """每个关键字段的**来源 / 更新时间 / 外部身份 / 人工确认版本** + 待确认差异。

    未核实的来源在这里显示"待核实"，未拍板的字段权威显示"未拍板"——
    这两句话是数据支撑的结论，不是页面上的静态提示。
    """
    return ok(await master_svc.sku_master_overview(session, sku_id))


@router.get("/sku-master/skus/{sku_id}/confirmed")
async def sku_confirmed_master(
    sku_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """这个 SKU 最近一版**已确认**的主数据（正式报价应当引用它）。

    没有确认版本时返回 `version=None` 与说明，而不是拿当前值冒充已确认值。
    """
    version = await master_svc.confirmed_master_version(session, sku_id)
    if version is None:
        return ok(
            {
                "sku_id": sku_id,
                "version": None,
                "message": "这个 SKU 还没有人工确认过的主数据版本，正式报价不可引用",
            }
        )
    return ok({"sku_id": sku_id, "version": version, "message": "已确认版本"})


@router.get("/sku-master/diffs")
async def list_sku_master_diffs(
    sku_id: int | None = None,
    status: str | None = None,
    diff_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """SKU 主数据差异清单（待确认队列）。"""
    items, total = await master_svc.list_sku_diffs(
        session,
        sku_id=sku_id,
        status=status,
        diff_type=diff_type,
        page=page,
        page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


@router.get("/sku-master/identities")
async def list_sku_identities(
    sku_id: int | None = None,
    system_type: str | None = None,
    match_status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """SKU 的外部身份台账；`match_status=pending` 就是**待匹配**（本地还没这条 SKU）。"""
    items, total = await master_svc.list_identity_sources(
        session,
        sku_id=sku_id,
        system_type=system_type,
        match_status=match_status,
        page=page,
        page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


@router.post("/sku-master/sources/ingest")
async def ingest_sku_source(
    payload: SkuIngestRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """接收一条外部来源的 SKU 数据（真实取数列为待外部验收，这里由桥接方喂）。

    只登记"来源值 + 差异"：**不会**改本地 SKU。空值不覆盖，同名不同码不合并。
    """
    result = await master_svc.ingest_external_sku(
        session,
        system_type=payload.system_type,
        external_code=payload.external_code,
        external_name=payload.external_name,
        shop_id=payload.shop_id,
        source_updated_at=payload.source_updated_at,
        fields=payload.fields,
        operator_id=user.id,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="sku_master_ingest",
        business_type="sku_identity_source",
        business_id=result["identity"]["id"],
        after={
            "system_type": payload.system_type,
            "external_code": payload.external_code,
            "matched_sku_id": result.get("matched_sku_id"),
            "fields": sorted(payload.fields),
        },
        source="INTEGRATION",
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result.get("message") or "已登记来源数据")


@router.post("/sku-master/identities/{source_id}/replay")
async def replay_sku_identity(
    source_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """待匹配的外部身份重放：本地补建 SKU 之后落成字段权威（不必重新取数）。"""
    source = await master_svc.get_identity_source(session, source_id)
    result = await master_svc.replay_identity_source(session, source, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="sku_master_replay",
        business_type="sku_identity_source",
        business_id=source.id,
        after={"replayed": result.get("replayed"), "sku_id": result["identity"]["sku_id"]},
        source="INTEGRATION",
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])


@router.post("/sku-master/identities/{source_id}/rename")
async def rename_sku_identity(
    source_id: int,
    payload: SkuRenameRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """来源改码：老身份行保留成历史，本地编码不被自动修改。"""
    source = await master_svc.get_identity_source(session, source_id)
    result = await master_svc.rename_identity_source(
        session, source, new_external_code=payload.new_external_code, operator_id=user.id
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="sku_master_rename_source",
        business_type="sku_identity_source",
        business_id=source.id,
        after={
            "old_code": result["old_identity"]["external_code"],
            "new_code": result["new_identity"]["external_code"],
        },
        source="INTEGRATION",
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])


@router.post("/sku-master/identities/{source_id}/stop")
async def stop_sku_identity(
    source_id: int,
    payload: SkuStopRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """来源标记停用：只挂差异，本地 SKU 状态不动（停用要人工确认）。"""
    source = await master_svc.get_identity_source(session, source_id)
    result = await master_svc.mark_identity_stopped(
        session, source, note=payload.note, operator_id=user.id
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="sku_master_source_stopped",
        business_type="sku_identity_source",
        business_id=source.id,
        after={"external_code": source.external_code, "note": payload.note},
        source="INTEGRATION",
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])


@router.post("/sku-master/authority")
async def set_sku_field_authority(
    payload: SkuAuthorityRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """登记字段权威归属（留空 = 撤回归属/未拍板）。

    默认是空的：§8.14 要求"不默认任一系统为主"，所以归属必须有人显式说清楚。
    """
    result = await master_svc.set_field_authority(
        session,
        sku_id=payload.sku_id,
        field_name=payload.field_name,
        authority=payload.authority,
        operator_id=user.id,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="sku_master_set_authority",
        business_type="sku",
        business_id=payload.sku_id,
        after={"field_name": payload.field_name, "authority": payload.authority},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])


@router.post("/sku-master/diffs/{diff_id}/confirm")
async def confirm_sku_master_diff(
    diff_id: int,
    payload: SkuMasterDiffConfirmRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """核定一条 SKU 主数据差异：**唯一**能改 SKU 关键字段的入口，改动留版本快照。"""
    diff = await session.get(IntegrationDiff, diff_id)
    if diff is None or diff.domain != "sku_master":
        raise AppError(ErrorCode.NOT_FOUND, f"SKU 主数据差异 #{diff_id} 不存在", 404)
    result = await master_svc.confirm_sku_diff(
        session,
        diff,
        resolution=payload.resolution,
        note=payload.note,
        operator_id=user.id,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="sku_master_diff_confirm",
        business_type="integration_diff",
        business_id=diff.id,
        before={"status": "open", "diff_type": diff.diff_type},
        after={
            "status": result["diff"]["status"],
            "resolution": payload.resolution,
            "applied_to_local": result["applied_to_local"],
            "detail": result["detail"],
            "version_no": (result.get("version") or {}).get("version_no"),
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])
