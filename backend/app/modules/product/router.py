"""产品中心接口（对齐 03-API §14 / §15）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.response import ok, page_data, paginate
from app.modules.product import service as svc
from app.modules.product.model import Product, Sku
from app.modules.product.schema import ProductCreate, ProductUpdate, SkuCreate, SkuUpdate

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
