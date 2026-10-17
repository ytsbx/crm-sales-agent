"""回收站清单接口（03-API 新增「回收站」章节）。

这里**只**提供四个"列出被删的"读接口。恢复动作刻意不放在这里：

- 线索恢复复用 `lead/io_router.py` 现有的 `POST /leads/{id}/restore`
  —— 那本来就是线索自己的动作；
- 产品 / SKU 恢复挂在 `product/router.py` 的删除接口旁边
  —— "删了能捡回来"这件事，看产品的人应该在本模块里找得到。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.response import ok, page_data
from app.modules.recycle import service as svc

router = APIRouter(tags=["Recycle"])


@router.get("/recycle-bin/contracts")
async def list_recycle_contracts(
    keyword: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    from app.core.data_scope import scoped_owner_ids
    from app.modules.contract.service import list_documents
    items, total = await list_documents(session, owner_ids=await scoped_owner_ids(session, user),
        customer_id=None, status=None, page=page, page_size=page_size, deleted_only=True, keyword=keyword)
    return ok(page_data(items, total, page, page_size))


@router.get("/recycle-bin/leads")
async def list_recycle_leads(
    keyword: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    """被删的线索（限当前用户数据范围内）。"""
    items, total = await svc.list_deleted_leads(session, user, page, page_size, keyword=keyword)
    return ok(page_data(items, total, page, page_size))


@router.get("/recycle-bin/products")
async def list_recycle_products(
    keyword: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """被删的产品。恢复时会连带恢复它下面被删的 SKU。"""
    items, total = await svc.list_deleted_products(session, page, page_size, keyword=keyword)
    return ok(page_data(items, total, page, page_size))


@router.get("/recycle-bin/skus")
async def list_recycle_skus(
    keyword: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """被删的 SKU（含随产品一起被删的）。"""
    items, total = await svc.list_deleted_skus(session, page, page_size, keyword=keyword)
    return ok(page_data(items, total, page, page_size))


@router.get("/recycle-bin/customers")
async def list_recycle_customers(
    keyword: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """被删的客户（只读）。被合并掉的会带上「并入了谁」。"""
    items, total = await svc.list_deleted_customers(session, user, page, page_size, keyword=keyword)
    return ok(page_data(items, total, page, page_size))
