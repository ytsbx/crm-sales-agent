"""回收站业务逻辑：只列被删的、以及把它们捡回来。

为什么单独一个模块：回收站是**跨业务对象的一张视图**（线索 / 产品 / SKU / 客户），
挂在任何一个业务模块里都会造成"线索模块里在查产品"这种错位。

## 第一版范围

- **线索**：软删 + 恢复。删/恢复的入口本来就在 `lead/io_router.py`
  （`DELETE /leads/{id}`、`POST /leads/{id}/restore`），这里只补"只列被删的"清单。
- **产品 / SKU**：补恢复。恢复产品时**连带**把它下面被删的 SKU 一起捡回来。
- **客户**：只读。列出所有被软删的客户，被合并掉的额外标出"已并入某某"。
  不给恢复按钮 —— 合并怎么还原是 `customer/tags.py` 留痕快照的事，
  不在本模块做（主人 2026-10-07 口径：客户这块只做"看"）。

## 两条容易踩的线

1. **数据范围**。线索列表走线索的数据范围、客户列表走客户的数据范围
   （两者都是"范围内 OR 无主"，与各自的正经列表**同一套口径**）。
   产品 / SKU 是全局主数据，只看权限码。**不在这里重写范围判断** ——
   重写一份迟早和正经列表漂开。
2. **客户被删只有两个来源**：`DELETE /customers/{id}`（直接删）与合并
   （`merge_customers` 把来源客户置删）。合并那条同时把 `owner_id` 清空了，
   所以它会落进"无主"那一档 —— 与客户列表里公海客户的处理完全一致。
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.response import paginate
from app.modules.customer import service as customer_service
from app.modules.customer.model import Customer, CustomerMergeLog
from app.modules.lead import service as lead_service
from app.modules.lead.model import Lead
from app.modules.product.model import Product, Sku


def _iso(value) -> str | None:
    """时间统一成 ISO 字符串（前端直接 toLocaleString）。

    注意 `deleted_at` 可能为空 —— 那是"没删"的数据，列表里不该出现。
    """
    return value.isoformat() if value else None


# ============================================================ 线索


def serialize_recycle_lead(lead: Lead, *, owner_name: str | None = None) -> dict:
    return {
        "id": lead.id,
        "name": lead.name,
        "company_name": lead.company_name,
        "contact_name": lead.contact_name,
        "mobile": lead.mobile,
        "status": lead.status,
        "status_label": lead_service.STATUS_LABEL.get(lead.status, lead.status),
        "owner_id": lead.owner_id,
        "owner_name": owner_name,
        "deleted_at": _iso(lead.deleted_at),
        "created_at": _iso(lead.created_at),
    }


async def _deleted_lead_stmt(user: CurrentUser, session: AsyncSession) -> Select:
    """只含"已被软删"的线索，且落在当前用户的数据范围内。

    为什么不复用 `build_lead_stmt(include_deleted=True)`：那个会**带上没删的**，
    回收站只要删掉的。范围判断仍然借 `lead_service.apply_data_scope`，
    口径与线索列表完全一致（含无主线索）。
    """
    stmt = select(Lead).where(Lead.deleted_at.is_not(None))
    stmt = await lead_service.apply_data_scope(stmt, user, session)
    return stmt.order_by(Lead.deleted_at.desc(), Lead.id.desc())


async def list_deleted_leads(
    session: AsyncSession, user: CurrentUser, page: int, page_size: int
) -> tuple[list[dict], int]:
    rows, total = await paginate(
        session, await _deleted_lead_stmt(user, session), page, page_size
    )
    owners = await customer_service.owner_names(session, [r.owner_id for r in rows])
    items = [serialize_recycle_lead(r, owner_name=owners.get(r.owner_id)) for r in rows]
    return items, total


# ============================================================ 产品 / SKU


def serialize_recycle_product(product: Product, *, deleted_sku_count: int = 0) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "product_line": product.product_line,
        "category": product.category,
        "brand": product.brand,
        "status": product.status,
        # 恢复这个产品时，会连带把它下面这些 SKU 一起捡回来 —— 先让人知道有几条
        "deleted_sku_count": deleted_sku_count,
        "deleted_at": _iso(product.deleted_at),
        "created_at": _iso(product.created_at),
    }


def serialize_recycle_sku(
    sku: Sku,
    *,
    product_name: str | None = None,
    product_deleted: bool = False,
    code_occupied: bool = False,
) -> dict:
    return {
        "id": sku.id,
        "sku_code": sku.sku_code,
        "name": sku.name,
        "specification": sku.specification,
        "product_id": sku.product_id,
        "product_name": product_name,
        # 产品还在回收站里 → 单独恢复这个 SKU 会变成"挂在不存在产品下"的孤儿，
        # 前端据此把恢复按钮换成"请先恢复产品"
        "product_deleted": product_deleted,
        # 编码被别的 SKU 占着（当前库里不可能，见 sku_code_occupied 的说明）
        "code_occupied": code_occupied,
        "deleted_at": _iso(sku.deleted_at),
        "created_at": _iso(sku.created_at),
    }


async def list_deleted_products(
    session: AsyncSession, page: int, page_size: int
) -> tuple[list[dict], int]:
    stmt = (
        select(Product)
        .where(Product.deleted_at.is_not(None))
        .order_by(Product.deleted_at.desc(), Product.id.desc())
    )
    rows, total = await paginate(session, stmt, page, page_size)

    counts: dict[int, int] = {}
    product_ids = [p.id for p in rows]
    if product_ids:
        count_rows = (
            await session.execute(
                select(Sku.product_id, func.count(Sku.id))
                .where(
                    Sku.product_id.in_(product_ids),
                    Sku.deleted_at.is_not(None),
                )
                .group_by(Sku.product_id)
            )
        ).all()
        counts = {int(pid): int(cnt) for pid, cnt in count_rows}

    items = [
        serialize_recycle_product(p, deleted_sku_count=counts.get(p.id, 0)) for p in rows
    ]
    return items, total


async def sku_code_occupied(
    session: AsyncSession, sku_code: str, *, exclude_id: int
) -> bool:
    """这个编码是否被**另一个还没被删的** SKU 占着。

    当前库里 `ix_skus_code` 是**全局唯一索引**（不排除已删行），所以正常情况
    这个函数恒为 `False`：同码的第二个 SKU 根本插不进来（建 SKU 时
    `ensure_sku_code_unique` 也会先拦成 409）—— 换句话说，删掉的 SKU 的编码
    **一直占着位**，恢复它不会跟谁冲。

    保留这个判断是纵深防御：一旦将来把索引改成"排除已删行"的部分索引，
    恢复就必须靠它挡住冲突，而不是撞库报 500。
    """
    row = (
        await session.execute(
            select(Sku.id).where(
                Sku.sku_code == sku_code,
                Sku.id != exclude_id,
                Sku.deleted_at.is_(None),
            )
        )
    ).first()
    return row is not None


async def list_deleted_skus(
    session: AsyncSession, page: int, page_size: int
) -> tuple[list[dict], int]:
    stmt = (
        select(Sku)
        .where(Sku.deleted_at.is_not(None))
        .order_by(Sku.deleted_at.desc(), Sku.id.desc())
    )
    rows, total = await paginate(session, stmt, page, page_size)

    # 产品名字（产品可能也已经删了，名字仍要显示）
    product_ids = list({r.product_id for r in rows})
    products: dict[int, tuple[str | None, bool]] = {}
    if product_ids:
        product_rows = (
            await session.execute(
                select(Product.id, Product.name, Product.deleted_at).where(
                    Product.id.in_(product_ids)
                )
            )
        ).all()
        products = {
            int(pid): (name, deleted_at is not None)
            for pid, name, deleted_at in product_rows
        }

    # 编码占用：批一次查，别逐条去查库
    codes = list({r.sku_code for r in rows if r.sku_code})
    occupied: set[str] = set()
    if codes:
        occupied = set(
            (
                await session.execute(
                    select(Sku.sku_code).where(
                        Sku.sku_code.in_(codes), Sku.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )

    items = [
        serialize_recycle_sku(
            r,
            product_name=products.get(r.product_id, (None, False))[0],
            product_deleted=products.get(r.product_id, (None, False))[1],
            code_occupied=r.sku_code in occupied,
        )
        for r in rows
    ]
    return items, total


async def restore_product(session: AsyncSession, product: Product) -> dict:
    """恢复产品，并**连带**把它下面被删的 SKU 一起捡回来。

    为什么连带：删产品时 SKU 是一起被软删的（`delete_product` 的注释写明"避免
    出现挂在不存在的产品上的孤儿 SKU"）。只把产品捡回来、SKU 还躺在回收站里，
    等于把一个空壳产品还给用户 —— 而库里**没有记**"这些 SKU 是被产品连坐删的"
    还是"被单独删的"，所以只能按"产品下所有被删的 SKU"整体恢复
    （主人 2026-10-07 拍板的口径）。

    撞码怎么办：逐条先判 `sku_code_occupied`，被占的那条**跳过并报出来**，
    不让一次撞码把整批恢复搞崩。
    """
    if product.deleted_at is None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该产品没有被删除，无需恢复")

    product.deleted_at = None

    skus = (
        await session.execute(
            select(Sku)
            .where(Sku.product_id == product.id, Sku.deleted_at.is_not(None))
            .order_by(Sku.id.asc())
        )
    ).scalars().all()

    restored: list[dict] = []
    skipped: list[dict] = []
    for sku in skus:
        if await sku_code_occupied(session, sku.sku_code, exclude_id=sku.id):
            skipped.append(
                {
                    "id": sku.id,
                    "sku_code": sku.sku_code,
                    "reason": f"编码「{sku.sku_code}」已被另一个 SKU 占用，未恢复",
                }
            )
            continue
        sku.deleted_at = None
        restored.append({"id": sku.id, "sku_code": sku.sku_code, "name": sku.name})

    return {"restored_skus": restored, "skipped_skus": skipped}


async def restore_sku(session: AsyncSession, sku: Sku) -> None:
    """单独恢复一个 SKU。

    两类拦路：① 编码被别的 SKU 占着（当前库里不可能，见 `sku_code_occupied`）；
    ② 它挂着的产品**还在回收站里** —— 这时恢复出来的是"挂在已删产品下"的孤儿，
    产品列表里根本看不到它。第二种直接让用户先恢复产品，比恢复完一脸懵强。
    """
    if sku.deleted_at is None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该 SKU 没有被删除，无需恢复")

    product = await session.get(Product, sku.product_id)
    if product is None or product.deleted_at is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该 SKU 所属的产品还在回收站里，请先恢复产品",
        )

    if await sku_code_occupied(session, sku.sku_code, exclude_id=sku.id):
        raise AppError(
            ErrorCode.DUPLICATE,
            f"SKU 编码「{sku.sku_code}」已被另一个 SKU 占用，无法恢复",
            409,
        )
    sku.deleted_at = None


# ============================================================ 客户（只读）


def serialize_recycle_customer(
    customer: Customer,
    *,
    owner_name: str | None = None,
    merged_into: dict | None = None,
    merge_reason: str | None = None,
) -> dict:
    return {
        "id": customer.id,
        "name": customer.name,
        "short_name": customer.short_name,
        "owner_id": customer.owner_id,
        "owner_name": owner_name,
        "deleted_at": _iso(customer.deleted_at),
        "created_at": _iso(customer.created_at),
        # 被合并掉的：给出"并进了谁"，前端拿它做跳转链接；直接删的这里是 null
        "merged_into": merged_into,
        "merge_reason": merge_reason,
    }


async def list_deleted_customers(
    session: AsyncSession, user: CurrentUser, page: int, page_size: int
) -> tuple[list[dict], int]:
    stmt = select(Customer).where(Customer.deleted_at.is_not(None))
    stmt = await customer_service.apply_data_scope(stmt, user, session)
    stmt = stmt.order_by(Customer.deleted_at.desc(), Customer.id.desc())
    rows, total = await paginate(session, stmt, page, page_size)

    ids = [c.id for c in rows]
    # 合并留痕：这些客户里哪些是"被合并掉的"、并到哪去了。
    # 一个客户理论上只会是**一次**合并的来源（合并完就软删了，不可能再当来源），
    # 按 id 倒序取最新一条即可。
    merge_map: dict[int, CustomerMergeLog] = {}
    if ids:
        logs = (
            await session.execute(
                select(CustomerMergeLog)
                .where(CustomerMergeLog.source_customer_id.in_(ids))
                .order_by(CustomerMergeLog.id.desc())
            )
        ).scalars().all()
        for log in logs:
            merge_map.setdefault(log.source_customer_id, log)

    target_names: dict[int, str] = {}
    target_ids = list({log.target_customer_id for log in merge_map.values()})
    if target_ids:
        target_rows = (
            await session.execute(
                select(Customer.id, Customer.name).where(Customer.id.in_(target_ids))
            )
        ).all()
        target_names = {int(cid): name for cid, name in target_rows}

    owners = await customer_service.owner_names(session, [c.owner_id for c in rows])
    items = []
    for customer in rows:
        log = merge_map.get(customer.id)
        merged_into = None
        if log is not None:
            merged_into = {
                "id": log.target_customer_id,
                "name": target_names.get(log.target_customer_id),
            }
        items.append(
            serialize_recycle_customer(
                customer,
                owner_name=owners.get(customer.owner_id),
                merged_into=merged_into,
                merge_reason=log.reason if log else None,
            )
        )
    return items, total


__all__ = [
    "list_deleted_customers",
    "list_deleted_leads",
    "list_deleted_products",
    "list_deleted_skus",
    "restore_product",
    "restore_sku",
    "serialize_recycle_customer",
    "serialize_recycle_lead",
    "serialize_recycle_product",
    "serialize_recycle_sku",
    "sku_code_occupied",
]
