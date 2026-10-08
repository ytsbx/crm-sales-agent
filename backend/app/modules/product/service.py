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


async def lock_product(session: AsyncSession, product_id: int) -> Product | None:
    """按**统一锁序**取产品行锁（「先产品、后 SKU」里的第一步）。返回 None 表示不存在。

    为什么所有涉及产品的入口都得先走这里：产品删除、产品恢复、SKU 恢复、新增 SKU
    这四件事都要"先看产品还在不在、再动它名下的 SKU"。恢复产品是
    「锁产品 → 扫它名下的 SKU」，如果哪一处反过来「先锁 SKU 再拿产品锁」，
    两边同时进行时就会互相等待 —— 典型的死锁。锁序只有一处，就不容易走反。

    `populate_existing=True` 不能少：本项目会话是 `expire_on_commit=False`，
    SQLAlchemy 默认**不用查询结果覆盖已加载对象**的属性。调用方若在同一个会话里
    先读过这个产品，少了它拿回来的就是内存里的旧值（`deleted_at` 还是删之前的），
    行锁等于白加 —— 并发下的表现就是"时对时错"。
    """
    return (
        await session.execute(
            select(Product)
            .where(Product.id == product_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()


async def delete_product(session: AsyncSession, product: Product) -> list[Sku]:
    """软删产品，并连同它名下还没删的 SKU 一起软删。**返回这些被连坐删掉的 SKU**。

    ⚠️ 调用方必须先 `lock_product`。这里扫 SKU 时也带行锁，理由同
    `recycle.service.restore_product`：不加锁的话"正在删产品"与"同时恢复某个 SKU"
    会各看各的旧世界，收尾时留下一个挂在已删产品下的有效 SKU。

    为什么要把"被删掉的那些 SKU"交出去：**这些 SKU 各自也需要一条删除留痕**
    （由调用方写，见 `product.router.delete_product`）。从前只写产品那一条，
    回收站要判断"某条 SKU 是不是被这次删产品带走的"，只能拿产品留痕的时间去和
    SKU 自己的删除时间比谁近 —— 那会张冠李戴（回收站复审 RB07：一条一个月前
    就删掉的 SKU，被算到今天删产品的人头上）。
    """
    product.deleted_at = datetime.now(UTC)
    # 产品下的 SKU 一并软删除，避免出现挂在不存在的产品上的孤儿 SKU
    skus = (
        await session.execute(
            select(Sku)
            .where(Sku.product_id == product.id, Sku.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    now = datetime.now(UTC)
    for sku in skus:
        sku.deleted_at = now
    return list(skus)
