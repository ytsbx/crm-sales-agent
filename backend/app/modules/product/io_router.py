"""产品与 SKU 的导入导出接口（对齐 03-API §14 §15）。

单独成 router 的原因与客户/线索一致：`/products/import`、`/skus/export`
这类静态路径必须注册在 `/products/{product_id}` 之前，否则会被
动态路由抢先匹配成 id="import"。
"""

import hashlib

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csvio import csv_bytes, parse_csv_bytes
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.importing import (
    ImportReport,
    RowErrors,
    RowRejected,
    RowSkipped,
    finalize,
    row_savepoint,
)
from app.modules.product import io as io_util
from app.modules.product.model import Product, Sku

router = APIRouter(tags=["Product"])


async def _sku_count_map(session: AsyncSession, product_ids: list[int]) -> dict[int, int]:
    if not product_ids:
        return {}
    stmt = (
        select(Sku.product_id, func.count(Sku.id))
        .where(Sku.product_id.in_(product_ids), Sku.deleted_at.is_(None))
        .group_by(Sku.product_id)
    )
    return {int(pid): int(count) for pid, count in (await session.execute(stmt)).all()}


# ================================================================== 产品

@router.get("/products/import-template")
async def product_import_template(
    _: CurrentUser = Depends(require_permission("product:view")),
):
    content = csv_bytes(
        [["示例：三层瓦楞纸箱", "纸箱", "包装", "宏远", "用于外包装", "可承重 15kg"]],
        io_util.PRODUCT_TEMPLATE_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''product-import-template.csv"
        },
    )


@router.get("/products/export")
async def export_products(
    keyword: str | None = None,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    return await _export_products(session, keyword=keyword)


@router.post("/products/export")
async def export_products_filtered(
    payload: dict,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """按筛选条件导出产品（03-API §14）。"""
    return await _export_products(session, keyword=payload.get("keyword"))


async def _export_products(session: AsyncSession, *, keyword: str | None = None) -> Response:
    stmt = select(Product).where(Product.deleted_at.is_(None))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            Product.name.ilike(like)
            | Product.product_line.ilike(like)
            | Product.brand.ilike(like)
        )
    rows = (await session.execute(stmt.order_by(Product.id.desc()))).scalars().all()
    counts = await _sku_count_map(session, [row.id for row in rows])
    content = csv_bytes(
        [io_util.product_export_row(row, counts.get(row.id, 0)) for row in rows],
        io_util.PRODUCT_EXPORT_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''products.csv"},
    )


@router.post("/products/import")
async def import_products(
    request: Request,
    file: UploadFile = File(...),
    preview: bool = Form(False),
    preview_token: str | None = Form(None),
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入产品：按名称查重，重名跳过。

    第七批 7.1 / 7.6：
    - 每行一个 SAVEPOINT：坏行只回滚自己，不会把 session 带进失败态；
    - **支持 `preview=1`**：原来这个接口没有 preview 参数，而通用导入组件
      一律先发 `preview=true` —— FastAPI 忽略未知表单字段，于是"预览"这一步
      实际上**真的写库了**，用户以为只是在看结果；
    - 失败清单全量返回（防爆上限见 settings.import_max_error_rows），
      同一行的多个问题算一行失败。
    """
    raw = await file.read()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse_csv_bytes(raw, required_headers=["产品名称"], label="文件")
    report = ImportReport("product", len(rows))

    for index, row in enumerate(rows, start=2):
        name = (row.get("产品名称") or "").strip()
        errs = RowErrors(index, name)
        if not name:
            errs.add("产品名称不能为空")
        if len(name) > 200:
            errs.add(f"产品名称长度不能超过 200（当前 {len(name)}）")
        try:
            fields = io_util.product_fields_from_row(row)
        except ValueError as exc:
            fields = None
            errs.add(str(exc))
        if errs:
            report.failed_row(index, name, errs.reasons)
            continue

        try:
            async with row_savepoint(session):
                duplicate = (
                    await session.execute(
                        select(Product)
                        .where(Product.name == name, Product.deleted_at.is_(None))
                        .limit(1)
                    )
                ).scalars().first()
                if duplicate is not None:
                    raise RowSkipped(f"同名产品已存在（id={duplicate.id}）")
                product = Product(
                    name=name,
                    status="active",
                    created_by=user.id,
                    **(fields or {}),
                )
                session.add(product)
                await session.flush()
                product_id = product.id
            report.created_row(index, name, id=product_id)
        except RowSkipped as skipped:
            report.skipped_row(index, name, str(skipped))
        except Exception as exc:
            report.failed_row(index, name, f"写入失败：{str(exc)[:160]}")

    return await finalize(
        session,
        report,
        module="product",
        operator_id=user.id,
        file_name=file.filename,
        file_sha256=file_sha256,
        ip=client_ip(request),
        preview=preview,
        preview_token=preview_token,
        business_type="product",
    )


# ================================================================== SKU

@router.get("/skus/import-template")
async def sku_import_template(
    _: CurrentUser = Depends(require_permission("product:view")),
):
    content = csv_bytes(
        [["SKU-001", "示例：三层瓦楞纸箱", "中号", "400x300x200", "本色", "瓦楞纸",
          "400", "300", "200", "0.5", "20", "0.024", "100", "纸箱", "个"]],
        io_util.SKU_TEMPLATE_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''sku-import-template.csv"},
    )


@router.get("/skus/export")
async def export_skus(
    keyword: str | None = None,
    product_id: int | None = None,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    return await _export_skus(session, keyword=keyword, product_id=product_id)


@router.post("/skus/export")
async def export_skus_filtered(
    payload: dict,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """按筛选条件导出 SKU（03-API §15）。"""
    return await _export_skus(
        session, keyword=payload.get("keyword"), product_id=payload.get("product_id")
    )


async def _export_skus(
    session: AsyncSession, *, keyword: str | None = None, product_id: int | None = None
) -> Response:
    stmt = select(Sku).where(Sku.deleted_at.is_(None))
    if product_id:
        stmt = stmt.where(Sku.product_id == product_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            Sku.sku_code.ilike(like) | Sku.name.ilike(like) | Sku.specification.ilike(like)
        )
    rows = (await session.execute(stmt.order_by(Sku.id.desc()))).scalars().all()

    product_ids = {row.product_id for row in rows}
    names: dict[int, str] = {}
    if product_ids:
        found = (
            await session.execute(
                select(Product.id, Product.name).where(Product.id.in_(product_ids))
            )
        ).all()
        names = {int(pid): name for pid, name in found}

    content = csv_bytes(
        [io_util.sku_export_row(row, names.get(row.product_id)) for row in rows],
        io_util.SKU_EXPORT_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''skus.csv"},
    )


@router.post("/skus/import")
async def import_skus(
    request: Request,
    file: UploadFile = File(...),
    preview: bool = Form(False),
    preview_token: str | None = Form(None),
    user: CurrentUser = Depends(require_permission("product:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入 SKU。

    - `SKU编码` 全局唯一，重复的直接跳过；
    - `产品名称` 必须能匹配到已有产品 —— **不自动建产品**：
      型号录错时自动建会把问题变成一堆重复产品，比导入失败难收拾；
    - 第七批 7.1 / 7.2 / 7.6：每行一个 SAVEPOINT、支持 preview、
      数值（含 MOQ 必须是整数、不能为负）逐行校验、失败清单全量返回。
    """
    raw = await file.read()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse_csv_bytes(raw, required_headers=["SKU编码", "产品名称"], label="文件")
    report = ImportReport("sku", len(rows))

    for index, row in enumerate(rows, start=2):
        code = (row.get("SKU编码") or "").strip()
        product_name = (row.get("产品名称") or "").strip()
        errs = RowErrors(index, code)
        if not code:
            errs.add("SKU编码不能为空")
        elif len(code) > 64:
            errs.add(f"SKU编码长度不能超过 64（当前 {len(code)}）")
        if not product_name:
            errs.add("产品名称不能为空")
        try:
            fields = io_util.sku_fields_from_row(row)
        except ValueError as exc:
            fields = None
            errs.add(str(exc))
        if errs:
            report.failed_row(index, code, errs.reasons)
            continue

        try:
            async with row_savepoint(session):
                duplicate = (
                    await session.execute(
                        select(Sku).where(Sku.sku_code == code).limit(1)
                    )
                ).scalars().first()
                if duplicate is not None:
                    raise RowSkipped(f"SKU编码已存在（id={duplicate.id}）")

                product = (
                    await session.execute(
                        select(Product)
                        .where(Product.name == product_name, Product.deleted_at.is_(None))
                        .limit(1)
                    )
                ).scalars().first()
                if product is None:
                    raise RowRejected(
                        f"找不到产品「{product_name}」，请先导入产品（已删除的产品不算）"
                    )
                sku = Sku(product_id=product.id, status="active", **(fields or {}))
                session.add(sku)
                await session.flush()
                sku_id = sku.id
            report.created_row(index, code, id=sku_id, sku_code=code)
        except (RowSkipped, RowRejected) as exc:
            if isinstance(exc, RowSkipped):
                report.skipped_row(index, code, str(exc))
            else:
                report.failed_row(index, code, str(exc))
        except Exception as exc:  # 单行失败不影响其它行
            report.failed_row(index, code, f"写入失败：{str(exc)[:160]}")

    return await finalize(
        session,
        report,
        module="sku",
        operator_id=user.id,
        file_name=file.filename,
        file_sha256=file_sha256,
        ip=client_ip(request),
        preview=preview,
        preview_token=preview_token,
        business_type="sku",
    )
