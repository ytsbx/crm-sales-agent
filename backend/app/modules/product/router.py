"""产品中心接口（对齐 03-API §14 / §15）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.product import service as svc
from app.modules.product.model import Product, Sku
from app.modules.product.schema import (
    ProductCreate,
    ProductUpdate,
    SkuCreate,
    SkuStandaloneCreate,
    SkuUpdate,
)

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
    product = await svc.get_product_or_404(session, product_id)
    before = svc.serialize_product(product)
    await svc.delete_product(session, product)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="product",
        business_id=product.id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "产品已删除")


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
    product = await svc.get_product_or_404(session, product_id)
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
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "SKU 已删除")


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
    """
    product = await svc.get_product_or_404(session, payload.product_id)
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
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    """给产品挂附件（03-API §14）：图纸、检测报告、认证证书等（POST）。

    与 `POST /business/product/{id}/files` 是同一份实现的两个入口 ——
    产品详情页用这个更自然。底层都是 `business_files`，不另建表。
    """
    from app.modules.file.model import BusinessFile, FileRecord

    product = await session.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, f"文件 id={file_id} 不存在", 404)

    # 与 `POST /business/{business_type}/{business_id}/files` 同一纪律：**两个方向都要校验**。
    # 这里曾经只校验目标产品存在、不校验源文件可见性，于是成了越权下载通道——
    # `product` 属 NO_OWNER_TYPES（全员可见），而 `can_access_file` 只要有一条可见关联就放行，
    # 所以"挂一条关联"本身就等于授权：把别人的 file_id 挂到任意产品上即可下载别人的原件。
    from app.modules.file.access import can_access_file

    if not await can_access_file(session, user, file_id):
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "该文件不在你的可见范围内", 403)

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
