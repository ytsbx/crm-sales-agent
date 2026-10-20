"""产品与 SKU 业务逻辑。"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

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
        # SKU 的编码 / 名称也能搜到它所属的产品（2026-10-10 主人提的）。
        #
        # 为什么是这个场景：产品中心那页是**按产品**呈现的，但业务员手里拿到的
        # 往往是 SKU 编码（`TP-1210-ST`）或 SKU 名（"田字塑料托盘 1200×1000 黑色"）。
        # 从前只能搜产品名/产品线/品牌，拿编码搜是空结果 —— 明明库里有。
        #
        # ⚠️ 必须用 `exists` 子查询，**不能 join skus**：
        # 一个产品下有 N 个 SKU 时 join 会把产品行复制 N 份，
        # 于是"共几条"的分页总数和列表内容都会不对（同一个产品出现多次）。
        # `exists` 只判断"有没有匹配的 SKU"，不改变行数。
        #
        # 软删的 SKU 不算（`deleted_at is null`）：它已经不在 SKU 列表里了，
        # 拿它把产品搜出来会让人以为那个 SKU 还在。
        matched_sku = (
            select(Sku.id)
            .where(
                Sku.product_id == Product.id,
                Sku.deleted_at.is_(None),
                or_(Sku.sku_code.ilike(like), Sku.name.ilike(like)),
            )
            .exists()
        )
        stmt = stmt.where(
            or_(
                Product.name.ilike(like),
                Product.product_line.ilike(like),
                Product.brand.ilike(like),
                matched_sku,
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


async def resolve_new_sku_code(session: AsyncSession, provided: str | None) -> str:
    """编码可不填；只在新建时分配，已存在/已删除的编码都不复用。"""
    code = (provided or "").strip()
    if code:
        await ensure_sku_code_unique(session, code)
        return code
    for _ in range(5):
        code = "SKU-" + uuid4().hex.upper()
        existing = (await session.execute(select(Sku.id).where(Sku.sku_code == code))).first()
        if existing is None:
            return code
    raise AppError(ErrorCode.DUPLICATE, "自动编码暂时无法分配，请重试", 409)


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


async def lock_sku_with_product(
    session: AsyncSession, sku_id: int
) -> tuple[Sku, Product] | None:
    """按统一的「产品 → SKU」顺序锁住 SKU 及其所属产品。

    SKU 的状态/删除写入口必须和产品级联删除、恢复保持同一锁序；只锁 SKU
    会让产品删除（先产品后 SKU）产生循环等待，并允许 SKU 在产品已被删除时
    被并发改回有效。
    """
    product_id = (
        await session.execute(select(Sku.product_id).where(Sku.id == sku_id))
    ).scalar_one_or_none()
    if product_id is None:
        return None
    product = await lock_product(session, int(product_id))
    if product is None:
        return None
    sku = (
        await session.execute(
            select(Sku)
            .where(Sku.id == sku_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if sku is None:
        return None
    return sku, product


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


# ---------------------------------------------------------------- SKU 可用状态

#: 「允许新业务」的状态白名单。**只有 active 能接新业务。**
#:
#: 为什么要有统一判据（issue #7 实测）：停用 SKU 之后，查价仍返回
#: `status=ok` 与有效适用价、还能加进报价、手工建单也成功 ——
#: "停售"这个操作对实际销售完全失效。四处入口各自只判 `deleted_at`、
#: 谁都没看 `status`，所以修的时候必须**接到同一份判据上**，
#: 否则下次新加一个入口又会漏。
SKU_AVAILABLE_STATUSES = ("active",)


def sku_blocked_reason(sku: Sku) -> str | None:
    """这个 SKU 现在能不能接**新业务**；能则返回 None，不能则返回给用户看的原因。

    只判"能不能接新业务"，**不管历史单据**：已经发出去的报价、已建的订单
    引用的还是它，照常能打开、能看 —— 停用不该让历史记录变成打不开的空白。
    """
    if sku.deleted_at is not None:
        return f"SKU {sku.sku_code} 已删除，不能用于新业务"
    if (sku.status or "").strip().lower() not in SKU_AVAILABLE_STATUSES:
        return f"SKU {sku.sku_code} 已停用，不能用于新业务（历史单据不受影响）"
    return None


async def require_available_sku(session: AsyncSession, sku_id: int) -> Sku:
    """取 SKU 并校验"允许新业务"。查价 / 加入需求 / 报价 / 手工订单共用这一份。"""
    sku = await session.get(Sku, sku_id)
    if sku is None:
        raise AppError(ErrorCode.NOT_FOUND, f"SKU id={sku_id} 不存在", 404)
    reason = sku_blocked_reason(sku)
    if reason is not None:
        # 409：状态不允许，不是"找不到"。用户要能分清"这个 SKU 没了"
        # 和"这个 SKU 停售了，换个在用型号"。
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, reason, 409)
    return sku


def product_blocked_reason(product: Product | None) -> str | None:
    """所属产品还能不能接新业务；能则 None。判据与 SKU 那条**并列且同源**。"""
    if product is None:
        return "所属产品不存在，不能用于新业务"
    if product.deleted_at is not None:
        return f"产品「{product.name}」已删除，不能用于新业务"
    if (product.status or "").strip().lower() not in SKU_AVAILABLE_STATUSES:
        return f"产品「{product.name}」已停用，不能用于新业务（历史单据不受影响）"
    return None


async def require_sku_for_new_business(session: AsyncSession, sku_id: int) -> Sku:
    """**新业务入口的完整判据**：SKU 可用 **且** 所属产品可用。

    与 `require_available_sku` 的关系（第三批审查 P2，2026-10-12）：

    - 后者只判 SKU 自身（`deleted_at` / `status`），是查价、报价明细、手工订单
      一直用的口径；
    - **产品停用/删除从来没有任何入口判过** —— 一个停用产品下的在用 SKU 照样能被
      加进新需求，等于停用没生效。
    - 这里在它的基础上补判产品，供"整批写入"的新业务入口（复制商机、订单复购）使用。

    ⚠️ 是否把产品这一条**并入全项目统一判据**要单独决定：并进 `require_available_sku`
    会连带拦住"历史报价再来一版 / 刷新价格"这类既有业务的续做（`build_item_snapshot`
    也被那些路径调用），属于口径变更，不在这里顺手改。
    """
    sku = await require_available_sku(session, sku_id)
    product = await session.get(Product, sku.product_id)
    reason = product_blocked_reason(product)
    if reason is not None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, reason, 409)
    return sku


async def ensure_skus_for_new_business(
    session: AsyncSession, sku_ids: list[int | None]
) -> None:
    """整批判定，**把所有不可用项一次点全**再拒绝。

    为什么不是"遇到第一个就抛"：复制商机 / 订单复购都是"一次写一批"的动作，
    逐个报错会让人改一个跑一次、再撞下一个（一批 20 行要跑 20 遍）。
    判据仍然只有 `require_sku_for_new_business` 一份，这里只负责收集与措辞。
    """
    problems: list[str] = []
    for sku_id in sorted({int(x) for x in sku_ids if x is not None}):
        try:
            await require_sku_for_new_business(session, sku_id)
        except AppError as error:
            problems.append(error.message)
    if problems:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "以下商品当前不能用于新业务，本次操作已整单取消（没有写入任何数据）："
            + "；".join(problems),
            409,
        )
