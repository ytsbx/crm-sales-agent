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

## 三条容易踩的线

1. **数据范围**。线索列表走线索的数据范围、客户列表走客户的数据范围。
   产品 / SKU 是全局主数据，只看权限码。**不在这里重写范围判断** ——
   重写一份迟早和正经列表漂开。
2. **客户被删只有两个来源**：`DELETE /customers/{id}`（直接删）与合并
   （`merge_customers` 把来源客户置删）。**合并那条会把 `owner_id` 清空**
   （`customer/tags.py` 的 `merge_customers`），于是它看着像"公海客户"。
   若照搬客户列表"无主即公海、人人可见"的口径，被合并掉的客户就会全公司可见
   —— 可它其实属于合并**前**的那位同事，而且还会把合并原因一起带出去。
   所以合并来源改按 `merge_snapshot["owner_id"]`（合并前快照）判范围；
   快照里没有负责人的（老数据）**只有数据范围 all 的管理员**能看到，前端标"待核实"。
   直接删除的客户没有这条留痕，仍走老口径（含无主）。
3. **合并去向**要单独判权限，且要能追到底。目标客户可能不在当前用户范围内
   （此时连名字都不能给），也可能自己又被并走了一次（A→B→C，B 已经不存在）。
   两者都不能让前端拿到一个点了会 404 的链接。
"""

from __future__ import annotations

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
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

    ⚠️ **锁序**：调用方必须先锁住产品（见 `product.service.lock_product`），
    这里再按「产品 → SKU」的顺序连 SKU 一起锁（`with_for_update`）。
    SKU 不加锁的话，"正在恢复产品"与"同时有人删某个 SKU"会各看各的旧世界。
    """
    if product.deleted_at is None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该产品没有被删除，无需恢复")

    product.deleted_at = None

    skus = (
        await session.execute(
            select(Sku)
            .where(Sku.product_id == product.id, Sku.deleted_at.is_not(None))
            .order_by(Sku.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
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


async def restore_sku(
    session: AsyncSession, sku: Sku, *, product: Product | None
) -> None:
    """单独恢复一个 SKU。

    三类拦路：① 编码被别的 SKU 占着（当前库里不可能，见 `sku_code_occupied`）；
    ② 它挂着的产品**还在回收站里** —— 这时恢复出来的是"挂在已删产品下"的孤儿，
    产品列表里根本看不到它。第二种直接让用户先恢复产品，比恢复完一脸懵强；
    ③ 产品已不存在（脏数据）。

    ⚠️ **锁序**：`product` 必须是调用方**已经加锁、并且重新读过**的那一行
    （见 `product.service.lock_product`）。顺序固定「先产品、后 SKU」，
    与 `delete_product` / `restore_product` 一致。**不许先锁 SKU 再来拿产品锁**
    —— 那与"恢复产品"方向相反，两边同时进行时会互相等待、直接死锁。

    这个参数从前是函数内部 `session.get(Product, ...)` 现查的。那样查有两个问题：
    拿不到行锁（删产品的并发不会被挡住），以及拿到的是内存里的旧值。
    """
    if sku.deleted_at is None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该 SKU 没有被删除，无需恢复")

    if product is None:
        raise AppError(ErrorCode.NOT_FOUND, "该 SKU 所属的产品已不存在，无法恢复", 404)
    if product.deleted_at is not None:
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
    original_owner_id: int | None = None,
    original_owner_name: str | None = None,
    owner_pending: bool = False,
    merged_into: dict | None = None,
    final_target: dict | None = None,
    merge_reason: str | None = None,
) -> dict:
    return {
        "id": customer.id,
        "name": customer.name,
        "short_name": customer.short_name,
        # 【当前负责人】成对下发。合并来源的负责人**在合并那一刻就被清空了**，
        # 所以这里对合并来源必然是 (None, None) —— 想看的那个负责人见下面 original_*。
        "owner_id": customer.owner_id,
        "owner_name": owner_name,
        # 【原负责人】也是成对下发：合并来源取"合并前那张快照"，直接删除的取当时字段。
        # 从前只下发 `owner_name`（快照）+ `owner_id`（当前字段），两个字段说的不是
        # 同一件事，前端拿到 (null, "张三") 这种组合只能猜。
        "original_owner_id": original_owner_id,
        "original_owner_name": original_owner_name,
        # 快照里没留下负责人（老数据 / 快照缺失）→ 两个都是 null，这里置真。
        "owner_pending": owner_pending,
        "deleted_at": _iso(customer.deleted_at),
        "created_at": _iso(customer.created_at),
        # 【直接历史】被合并掉的：并进了谁。目标是**能打开**的才给 id，否则只给名字/状态
        "merged_into": merged_into,
        # 【最终去处】A→B→C 时的 C。与直接历史相同时为 None（不重复下发）
        "final_target": final_target,
        "merge_reason": merge_reason,
    }


#: 合并链条最多追几层。合并是人工操作，实际 1~2 层就到底了；给足余量，
#: 同时保证脏数据也不会把一次列表请求拖成无限循环（环路另有 visited 兜底）。
#:
#: ⚠️ **追满上限不等于"已经找到终点"**。早期实现直接把停下来的那个节点当成终点，
#: 于是超过 20 层的链会把第 21 个中间客户报成"最终去向"，用户看到"目标已不存在"，
#: 而真正的最终客户还好端端地在那儿。现在追满就标 `truncated`，界面说
#: 「合并链过长，最终去向待核实」—— 宁可不给链接，也不给一个错的链接。
_MERGE_CHAIN_MAX_HOPS = 20

#: 链条的三种结局。
CHAIN_OK = "ok"
CHAIN_LOOP = "loop"
CHAIN_TRUNCATED = "truncated"


def _walk_chain(root: int, edges: dict[int, int], unexpanded: set[int]) -> tuple[int | None, str]:
    """沿 `edges` 从 `root` 走到头，返回 `(终点 id, 结局)`。

    - 有环 → `(None, CHAIN_LOOP)`
    - 停在 `unexpanded`（这一层没让继续追，但它**确实还有下家**）→
      `(None, CHAIN_TRUNCATED)`
    - 正常走到底 → `(终点, CHAIN_OK)`
    """
    current = root
    visited = {root}
    while current in edges:
        nxt = edges[current]
        if nxt in visited:
            return None, CHAIN_LOOP
        visited.add(nxt)
        current = nxt
    if current in unexpanded:
        # 能走到这儿说明这一层的出边还没查过、但它**确实还有下家** —— 链没到头，
        # 既不能当终点，也不能说"链断了"，只能如实说一句"没追完"。
        return None, CHAIN_TRUNCATED
    return current, CHAIN_OK


async def _sources_with_out_edges(
    session: AsyncSession, candidates: set[int]
) -> set[int]:
    """这批客户里，**还有下家**的那些（即自己也是某条合并留痕的来源）。

    只在追到上限时用一次：用来把「链正好到这儿就没了」和「链还没完，只是
    没让追」分开 —— 不查这一步，正好卡在上限层数的链会被误报成"没追完"。
    """
    if not candidates:
        return set()
    rows = (
        await session.execute(
            select(CustomerMergeLog.source_customer_id)
            .where(CustomerMergeLog.source_customer_id.in_(candidates))
            .distinct()
        )
    ).scalars().all()
    return {int(sid) for sid in rows}


async def _resolve_merge_chain(
    session: AsyncSession, roots: set[int]
) -> dict[int, tuple[int | None, str]]:
    """从"直接合并目标"出发，追到**链条终点**（不再被别人并走的那个客户）。

    返回 `{起点 id: (终点 id, 结局)}`，结局取 `CHAIN_OK` / `CHAIN_LOOP` /
    `CHAIN_TRUNCATED`；后两种终点为 None（不该下发链接）。

    实现上**逐层批量查**（一次查一批来源的边），不是一条一条追 —— 后者在列表
    页有几十条合并记录时就是几十次往返。环路保护有两条：`seen_sources` 挡住
    已展开过的来源，逐条走链时再用 `visited` 兜一次。
    """
    if not roots:
        return {}

    edges: dict[int, int] = {}
    seen_sources: set[int] = set()
    #: 追到上限时"还没查过出边、且确实还有下家"的节点。它们的链**没到头** ——
    #: 所以既不能当成终点，也不能当成"链断了"，只能如实说一句"没追完"。
    unexpanded: set[int] = set()
    frontier = set(roots)
    for _ in range(_MERGE_CHAIN_MAX_HOPS):
        frontier = {n for n in frontier if n not in seen_sources}
        if not frontier:
            break
        seen_sources |= frontier
        rows = (
            await session.execute(
                select(
                    CustomerMergeLog.source_customer_id,
                    CustomerMergeLog.target_customer_id,
                )
                .where(CustomerMergeLog.source_customer_id.in_(frontier))
                # 升序遍历、后写覆盖前写 → 留下的是最新那条
                .order_by(CustomerMergeLog.id.asc())
            )
        ).all()
        for sid, tid in rows:
            edges[int(sid)] = int(tid)
        frontier = {int(tid) for _, tid in rows}
    else:
        # `for ... else`：只有在**没走到 break**（也就是跑满了上限）时才进来。
        # 这一层的出边还没查，补一次"谁还有下家"：查完还是空的，说明链就到这儿
        # 为止（照常给终点）；还有下家的才是真"没追完"。
        unexpanded = await _sources_with_out_edges(
            session, {n for n in frontier if n not in seen_sources}
        )

    return {root: _walk_chain(root, edges, unexpanded) for root in roots}


def _latest_merge_log_subq():
    """每个来源客户的**最新一条**合并留痕（来源 → 目标 + 快照 + 原因）。

    一个客户理论上只会当一次来源（合并完就被软删，不可能再被合并），
    但按 `id` 取最新是防御性写法 —— 真出现重复留痕时口径仍是确定的。
    """
    latest = (
        select(
            CustomerMergeLog.source_customer_id.label("sid"),
            func.max(CustomerMergeLog.id).label("log_id"),
        )
        .group_by(CustomerMergeLog.source_customer_id)
        .subquery()
    )
    return (
        select(
            CustomerMergeLog.source_customer_id.label("sid"),
            CustomerMergeLog.target_customer_id.label("target_id"),
            CustomerMergeLog.reason.label("reason"),
            CustomerMergeLog.merge_snapshot.label("snapshot"),
        )
        .join(latest, CustomerMergeLog.id == latest.c.log_id)
        .subquery()
    )


def _visible_customer_predicate(merge_subq, owner_ids: list[int]):
    """回收站语境下"这个客户该不该被当前用户看到"。

    与客户列表口径**故意不同的一处**：合并来源的 `owner_id` 在合并时被清空了，
    照搬"无主即公海"会让它人人可见（那正是从前的漏洞）。这里改按**合并前快照里的
    负责人**判；快照里没有负责人的（老数据 / 快照缺失）**一律不放行**，
    只有 `owner_ids is None`（数据范围 all 的管理员）才看得到，前端标"待核实"。
    直接删除的客户没有这条留痕，仍走老口径（范围内 OR 无主）。

    前提：`merge_subq` 已按 `Customer.id` 外连接进来（见各调用点）。
    """
    snapshot_owner = merge_subq.c.snapshot["owner_id"].as_integer()
    is_merged = merge_subq.c.sid.is_not(None)
    directly_deleted = and_(
        is_merged.is_(False),
        or_(Customer.owner_id.in_(owner_ids), Customer.owner_id.is_(None)),
    )
    merged_in_scope = and_(
        is_merged, snapshot_owner.is_not(None), snapshot_owner.in_(owner_ids)
    )
    return or_(directly_deleted, merged_in_scope)


async def _merge_refs(
    session: AsyncSession, user: CurrentUser, ids: set[int]
) -> dict[int, dict]:
    """把一批客户 id 变成前端能安全渲染的「去向」。

    返回 `{id: {"id", "name", "visible", "state"}}`：

    - `visible=True` 时给 `name`；**不可见时 name 与 id 都是 None**（不泄露）。
    - `id` 只在"**在范围内且还活着**"时才给 —— 它是要拿去做跳转的，
      指向一个 404 的链接比不给更糟。
    - `state`：`ok`（可跳转）/ `forbidden`（不在数据范围）/ `gone`（已不存在或被删）。
      另有 `loop` / `truncated` 两种，**不由本函数产生** —— 它们是"链没走通"，
      判断在 `_resolve_merge_chain` 那边，由调用方写进 `final_target`。
    """
    if not ids:
        return {}

    owner_ids = await scoped_owner_ids(session, user)
    merge = _latest_merge_log_subq()
    base = (
        select(Customer.id, Customer.name, Customer.deleted_at)
        .outerjoin(merge, merge.c.sid == Customer.id)
        .where(Customer.id.in_(ids))
    )
    if owner_ids is not None:
        base = base.where(_visible_customer_predicate(merge, owner_ids))
    visible_rows = (await session.execute(base)).all()
    visible = {
        int(cid): (name, deleted_at is not None) for cid, name, deleted_at in visible_rows
    }

    # 存在但不可见（forbidden）跟"压根没这条"（gone）要分开 —— 后者是链断了，
    # 前者只是没权限。这个查库只用于判断，**结果不外发**。
    existing = set(
        (
            await session.execute(select(Customer.id).where(Customer.id.in_(ids)))
        )
        .scalars()
        .all()
    )

    out: dict[int, dict] = {}
    for cid in ids:
        if cid in visible:
            name, deleted = visible[cid]
            out[cid] = {
                "id": None if deleted else cid,
                "name": name,
                "visible": True,
                "state": "gone" if deleted else "ok",
            }
        elif cid in existing:
            out[cid] = {"id": None, "name": None, "visible": False, "state": "forbidden"}
        else:
            out[cid] = {"id": None, "name": None, "visible": False, "state": "gone"}
    return out


async def list_deleted_customers(
    session: AsyncSession, user: CurrentUser, page: int, page_size: int
) -> tuple[list[dict], int]:
    """列出被软删的客户（只读）。

    **权限过滤在分页之前**完成：合并来源按合并前快照判、直接删的按老口径判，
    两者一起下到 SQL 的 WHERE 里 —— 否则总数会跟实际看得到的条数对不上。
    """
    owner_ids = await scoped_owner_ids(session, user)
    merge = _latest_merge_log_subq()

    stmt = (
        select(Customer)
        .outerjoin(merge, merge.c.sid == Customer.id)
        .where(Customer.deleted_at.is_not(None))
    )
    if owner_ids is not None:
        stmt = stmt.where(_visible_customer_predicate(merge, owner_ids))
    stmt = stmt.order_by(Customer.deleted_at.desc(), Customer.id.desc())

    rows, total = await paginate(session, stmt, page, page_size)

    ids = [c.id for c in rows]
    # 合并留痕：这些客户里哪些是"被合并掉的"、并到哪去了。
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

    # 原负责人：合并来源取"合并前快照"，直接删的取当前字段。快照里没有的记下来。
    original_owner: dict[int, int | None] = {}
    pending: set[int] = set()
    for customer in rows:
        log = merge_map.get(customer.id)
        if log is None:
            original_owner[customer.id] = customer.owner_id
            continue
        snapshot_owner = (log.merge_snapshot or {}).get("owner_id")
        original_owner[customer.id] = snapshot_owner
        if snapshot_owner is None:
            pending.add(customer.id)

    # 去向：直接目标 + 链条终点，都要过权限，批量一次算完
    direct_targets = {int(log.target_customer_id) for log in merge_map.values()}
    chain = await _resolve_merge_chain(session, direct_targets)
    # 只有**确实走到终点**的才拿来查引用；成环 / 追满上限的终点是 None，本来就不下发链接
    finals = {
        fin for fin, state in chain.values() if fin is not None and state == CHAIN_OK
    }
    refs = await _merge_refs(session, user, direct_targets | finals)

    # 当前负责人和原负责人可能不是同一个人（合并时负责人被清空过），一次把两批 id 都查了
    owner_names = await customer_service.owner_names(
        session,
        [c.owner_id for c in rows] + [original_owner.get(c.id) for c in rows],
    )
    items = []
    for customer in rows:
        log = merge_map.get(customer.id)
        merged_into = None
        final_target = None
        if log is not None:
            target_id = int(log.target_customer_id)
            merged_into = refs.get(target_id)
            final_id, state = chain.get(target_id, (None, CHAIN_OK))
            if state != CHAIN_OK:
                # 成环 / 追满上限都**不给链接** —— 给出去就是个错的链接，
                # 只在文案上把这两种情况分开（见前端 MERGE_STATE_TEXT）。
                final_target = {
                    "id": None,
                    "name": None,
                    "visible": False,
                    "state": state,
                }
            elif final_id is not None and final_id != target_id:
                # 只在与直接历史**不同**时才下发，省得前端重复渲染一层
                final_target = refs.get(final_id)
        original_id = original_owner.get(customer.id)
        items.append(
            serialize_recycle_customer(
                customer,
                owner_name=owner_names.get(customer.owner_id) if customer.owner_id else None,
                original_owner_id=original_id,
                original_owner_name=owner_names.get(original_id) if original_id else None,
                owner_pending=customer.id in pending,
                merged_into=merged_into,
                final_target=final_target,
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
