"""产品与 SKU 业务逻辑。"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.product.model import Product, Sku


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def serialize_product(product: Product, *, sku_count: int = 0) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "product_line": product.product_line,
        "category": product.category,
        "brand": product.brand,
        "description": product.description,
        "knowledge": product.knowledge,
        "status": product.status,
        "sku_count": sku_count,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


def serialize_sku(sku: Sku, *, product_name: str | None = None) -> dict:
    return {
        "id": sku.id,
        "product_id": sku.product_id,
        "product_name": product_name,
        "sku_code": sku.sku_code,
        "name": sku.name,
        "specification": sku.specification,
        "color": sku.color,
        "material": sku.material,
        "length": _number(sku.length),
        "width": _number(sku.width),
        "height": _number(sku.height),
        "weight": _number(sku.weight),
        "carton_qty": sku.carton_qty,
        "carton_volume": _number(sku.carton_volume),
        "moq": sku.moq,
        "package_type": sku.package_type,
        "unit": sku.unit,
        "status": sku.status,
        "created_at": sku.created_at,
    }


def build_product_stmt(
    keyword: str | None = None,
    status: str | None = None,
    category: str | None = None,
) -> Select:
    stmt = select(Product).where(Product.deleted_at.is_(None))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(like),
                Product.product_line.ilike(like),
                Product.brand.ilike(like),
            )
        )
    if status:
        stmt = stmt.where(Product.status == status)
    if category:
        stmt = stmt.where(Product.category == category)
    return stmt.order_by(Product.id.desc())


async def get_product_or_404(session: AsyncSession, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)
    return product


async def get_sku_or_404(session: AsyncSession, sku_id: int) -> Sku:
    sku = await session.get(Sku, sku_id)
    if sku is None or sku.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)
    return sku


async def ensure_sku_code_unique(
    session: AsyncSession, sku_code: str, exclude_id: int | None = None
) -> None:
    stmt = select(Sku.id).where(Sku.sku_code == sku_code)
    if exclude_id is not None:
        stmt = stmt.where(Sku.id != exclude_id)
    if (await session.execute(stmt)).first():
        raise AppError(ErrorCode.DUPLICATE, f"SKU 编码 {sku_code} 已存在", 409)


async def delete_product(session: AsyncSession, product: Product) -> None:
    product.deleted_at = datetime.now(UTC)
    # 产品下的 SKU 一并软删除，避免出现挂在不存在的产品上的孤儿 SKU
    skus = (
        await session.execute(
            select(Sku).where(Sku.product_id == product.id, Sku.deleted_at.is_(None))
        )
    ).scalars().all()
    now = datetime.now(UTC)
    for sku in skus:
        sku.deleted_at = now
