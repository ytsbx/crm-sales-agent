"""价格资料的批量导入（产品报价中心 · 方案 §6）。

覆盖三类：价格规则（含等级价）、客户专属价、SKU 成本。
单独成 router 的原因与产品/客户一致：`/price-rules/import` 这类静态路径
必须注册在动态路径之前，本 router 在 main.py 里排在 pricing_router 之前。

导入语义（方案 §6）：
- 逐行校验，单行失败不影响其它行，结果里给出失败行与原因；
- 与界面维护同一套冲突检查：同 SKU 同等级/同客户、数量区间与有效期
  都重叠的行直接失败（不静默跳过，重叠资料必须人来裁决）；
- 成本按（SKU, 生效起始日）幂等：同日再导 = 更新那一版成本，不产生重复版本。
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.csvio import csv_bytes, parse_csv_upload
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.response import ok
from app.modules.customer.model import Customer
from app.modules.pricing import service as svc
from app.modules.pricing.model import CustomerPriceRule, PriceRule, ProductCost
from app.modules.product.model import Sku

router = APIRouter(tags=["Pricing"])

LEVELS = {"A", "B", "C", "D"}


def _parse_date(value: str | None, *, row: int, field: str, errors: list[dict], name: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        errors.append({"row": row, "name": name, "reason": f"{field} 格式应为 YYYY-MM-DD（收到 {text!r}）"})
        return None


def _parse_decimal(value: str | None, *, row: int, field: str, errors: list[dict], name: str) -> Decimal | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        errors.append({"row": row, "name": name, "reason": f"{field} 不是有效数字（收到 {text!r}）"})
        return None


async def _sku_map_by_code(session: AsyncSession, codes: set[str]) -> dict[str, Sku]:
    if not codes:
        return {}
    rows = (await session.execute(select(Sku).where(Sku.sku_code.in_(codes)))).scalars().all()
    return {row.sku_code: row for row in rows}


def _summary(rows_n: int, created: int, skipped: int, failed: int, skipped_label: str = "跳过") -> str:
    return f"导入完成：成功 {created} 条，{skipped_label} {skipped} 条，失败 {failed} 条（共 {rows_n} 行）"


def _result(rows_n: int, created: list, skipped: list, failed: list, message: str) -> dict:
    return ok(
        {
            "total": rows_n,
            "created_count": len(created),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "created": created[:50],
            "skipped": skipped[:50],
            "failed": failed[:50],
        },
        message,
    )


# ================================================================== 价格规则

RULE_TEMPLATE_HEADERS = [
    "SKU编码", "客户等级(留空=通用)", "数量下限", "数量上限(留空=不限)",
    "标准价", "指导价", "最低保护价", "目标利润率(如0.30)",
    "生效起始日(YYYY-MM-DD)", "生效截止日(YYYY-MM-DD)", "备注",
]


@router.get("/price-rules/import-template")
async def price_rule_template(_: CurrentUser = Depends(require_permission("price:manage"))):
    content = csv_bytes(
        [["SKU-001", "A", "100", "999", "95", "85", "70", "0.30", "2026-10-01", "2026-12-31", "四季度促销档"]],
        RULE_TEMPLATE_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''price-rules-import-template.csv"},
    )


@router.post("/price-rules/import")
async def import_price_rules(
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入价格规则。同 SKU 同等级且区间重叠的行判失败（与界面维护同一规则）。"""
    rows = await parse_csv_upload(file, required_headers=["SKU编码", "指导价"], label="文件")
    created: list = []
    skipped: list = []
    failed: list = []

    codes = {(row.get("SKU编码") or "").strip() for row in rows}
    sku_map = await _sku_map_by_code(session, codes)

    for index, row in enumerate(rows, start=2):
        code = (row.get("SKU编码") or "").strip()
        errors: list[dict] = []
        level = (row.get("客户等级(留空=通用)") or "").strip().upper() or None
        if level and level not in LEVELS:
            errors.append({"row": index, "name": code, "reason": f"客户等级必须是 A/B/C/D（收到 {level}）"})

        min_qty = _parse_decimal(row.get("数量下限"), row=index, field="数量下限", errors=errors, name=code) or Decimal(0)
        max_qty = _parse_decimal(row.get("数量上限(留空=不限)"), row=index, field="数量上限", errors=errors, name=code)
        guide_price = _parse_decimal(row.get("指导价"), row=index, field="指导价", errors=errors, name=code)
        standard_price = _parse_decimal(row.get("标准价"), row=index, field="标准价", errors=errors, name=code)
        minimum_price = _parse_decimal(row.get("最低保护价"), row=index, field="最低保护价", errors=errors, name=code)
        target_margin = _parse_decimal(row.get("目标利润率(如0.30)"), row=index, field="目标利润率", errors=errors, name=code)
        effective_from = _parse_date(row.get("生效起始日(YYYY-MM-DD)"), row=index, field="生效起始日", errors=errors, name=code)
        effective_to = _parse_date(row.get("生效截止日(YYYY-MM-DD)"), row=index, field="生效截止日", errors=errors, name=code)
        if errors:
            failed.extend(errors)
            continue

        if not code:
            failed.append({"row": index, "name": code, "reason": "SKU编码不能为空"})
            continue
        if guide_price is None:
            failed.append({"row": index, "name": code, "reason": "指导价必填——没有指导价的规则无法作为取价依据"})
            continue
        sku = sku_map.get(code)
        if sku is None:
            failed.append({"row": index, "name": code, "reason": f"找不到 SKU「{code}」，请先导入产品/SKU"})
            continue

        try:
            conflict = await svc.find_price_rule_conflict(
                session,
                sku_id=sku.id,
                customer_level=level,
                min_qty=min_qty,
                max_qty=max_qty,
                effective_from=effective_from,
                effective_to=effective_to,
            )
            if conflict is not None:
                failed.append({
                    "row": index,
                    "name": code,
                    "reason": (
                        f"与现有规则 #{conflict.id}（数量 {conflict.min_qty}-{conflict.max_qty or '∞'}、"
                        f"等级 {conflict.customer_level or '通用'}）重叠，请先处理"
                    ),
                })
                continue

            rule = PriceRule(
                sku_id=sku.id,
                customer_level=level,
                min_qty=min_qty,
                max_qty=max_qty,
                standard_price=standard_price,
                guide_price=guide_price,
                minimum_price=minimum_price,
                target_margin=target_margin,
                effective_from=effective_from,
                effective_to=effective_to,
                status="active",
                remark=(row.get("备注") or "").strip() or None,
            )
            session.add(rule)
            await session.flush()
            created.append({"row": index, "id": rule.id, "sku_code": code})
        except Exception as exc:  # 单行失败不影响其它行
            failed.append({"row": index, "name": code, "reason": str(exc)[:120]})

    await write_audit(
        session,
        operator_id=user.id,
        action="import",
        business_type="price_rule",
        after={"created": len(created), "skipped": len(skipped), "failed": len(failed)},
        ip=client_ip(request),
    )
    await session.commit()
    return _result(len(rows), created, skipped, failed, _summary(len(rows), len(created), len(skipped), len(failed)))


# ================================================================== 客户专属价

CP_TEMPLATE_HEADERS = [
    "客户编号(与客户名称二选一)", "客户名称(精确匹配)", "SKU编码",
    "数量下限", "数量上限(留空=不限)", "约定价", "最低价",
    "生效起始日(YYYY-MM-DD)", "生效截止日(YYYY-MM-DD)", "备注",
]


@router.get("/customer-price-rules/import-template")
async def customer_price_template(_: CurrentUser = Depends(require_permission("price:manage"))):
    content = csv_bytes(
        [["", "示例客户有限公司", "SKU-001", "1000", "", "88", "75", "2026-01-01", "2026-12-31", "年度框架协议价"]],
        CP_TEMPLATE_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''customer-price-import-template.csv"},
    )


@router.post("/customer-price-rules/import")
async def import_customer_prices(
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入客户专属价。客户用「编号」或「名称精确匹配」定位，两种都填以编号为准。"""
    rows = await parse_csv_upload(file, required_headers=["SKU编码", "约定价"], label="文件")
    created: list = []
    skipped: list = []
    failed: list = []

    codes = {(row.get("SKU编码") or "").strip() for row in rows}
    sku_map = await _sku_map_by_code(session, codes)

    for index, row in enumerate(rows, start=2):
        code = (row.get("SKU编码") or "").strip()
        customer_no = (row.get("客户编号(与客户名称二选一)") or "").strip()
        customer_name = (row.get("客户名称(精确匹配)") or "").strip()
        errors: list[dict] = []

        agreed_price = _parse_decimal(row.get("约定价"), row=index, field="约定价", errors=errors, name=code)
        minimum_price = _parse_decimal(row.get("最低价"), row=index, field="最低价", errors=errors, name=code)
        min_qty = _parse_decimal(row.get("数量下限"), row=index, field="数量下限", errors=errors, name=code) or Decimal(0)
        max_qty = _parse_decimal(row.get("数量上限(留空=不限)"), row=index, field="数量上限", errors=errors, name=code)
        effective_from = _parse_date(row.get("生效起始日(YYYY-MM-DD)"), row=index, field="生效起始日", errors=errors, name=code)
        effective_to = _parse_date(row.get("生效截止日(YYYY-MM-DD)"), row=index, field="生效截止日", errors=errors, name=code)
        if errors:
            failed.extend(errors)
            continue

        if not code:
            failed.append({"row": index, "name": code, "reason": "SKU编码不能为空"})
            continue
        if agreed_price is None:
            failed.append({"row": index, "name": code, "reason": "约定价必填"})
            continue
        if not customer_no and not customer_name:
            failed.append({"row": index, "name": code, "reason": "客户编号与客户名称至少填一个"})
            continue

        try:
            customer: Customer | None = None
            if customer_no:
                customer = await session.get(Customer, int(customer_no))
                if customer is None:
                    failed.append({"row": index, "name": code, "reason": f"客户编号 {customer_no} 不存在"})
                    continue
            else:
                found = (
                    await session.execute(
                        select(Customer).where(Customer.name == customer_name).limit(2)
                    )
                ).scalars().all()
                if not found:
                    failed.append({"row": index, "name": code, "reason": f"找不到客户「{customer_name}」"})
                    continue
                if len(found) > 1:
                    failed.append({
                        "row": index,
                        "name": code,
                        "reason": f"客户名称「{customer_name}」匹配到 {len(found)} 家，请改用客户编号",
                    })
                    continue
                customer = found[0]

            sku = sku_map.get(code)
            if sku is None:
                failed.append({"row": index, "name": code, "reason": f"找不到 SKU「{code}」"})
                continue

            conflict = await svc.find_customer_price_conflict(
                session,
                customer_id=customer.id,
                sku_id=sku.id,
                min_qty=min_qty,
                max_qty=max_qty,
                effective_from=effective_from,
                effective_to=effective_to,
            )
            if conflict is not None:
                failed.append({
                    "row": index,
                    "name": code,
                    "reason": (
                        f"该客户此 SKU 已有规则 #{conflict.id}（数量 {conflict.min_qty}-{conflict.max_qty or '∞'}）重叠，请先处理"
                    ),
                })
                continue

            rule = CustomerPriceRule(
                customer_id=customer.id,
                sku_id=sku.id,
                min_qty=min_qty,
                max_qty=max_qty,
                agreed_price=agreed_price,
                minimum_price=minimum_price,
                effective_from=effective_from,
                effective_to=effective_to,
                remark=(row.get("备注") or "").strip() or None,
            )
            session.add(rule)
            await session.flush()
            created.append({"row": index, "id": rule.id, "sku_code": code})
        except Exception as exc:
            failed.append({"row": index, "name": code, "reason": str(exc)[:120]})

    await write_audit(
        session,
        operator_id=user.id,
        action="import",
        business_type="customer_price_rule",
        after={"created": len(created), "skipped": len(skipped), "failed": len(failed)},
        ip=client_ip(request),
    )
    await session.commit()
    return _result(len(rows), created, skipped, failed, _summary(len(rows), len(created), len(skipped), len(failed)))


# ================================================================== 成本

COST_TEMPLATE_HEADERS = [
    "SKU编码", "采购成本", "生产成本", "包装成本", "加工成本",
    "币种(默认CNY)", "生效起始日(YYYY-MM-DD)", "生效截止日(YYYY-MM-DD)", "备注",
]


@router.get("/costs/import-template")
async def cost_template(_: CurrentUser = Depends(require_permission("price:manage"))):
    content = csv_bytes(
        [["SKU-001", "12.5", "3", "0.8", "0.4", "CNY", "2026-10-01", "", "四季度采购价"]],
        COST_TEMPLATE_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''costs-import-template.csv"},
    )


@router.post("/costs/import")
async def import_costs(
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入成本。按（SKU, 生效起始日）幂等：同日再导 = 更新那一版成本。"""
    rows = await parse_csv_upload(
        file, required_headers=["SKU编码", "生效起始日(YYYY-MM-DD)"], label="文件"
    )
    created: list = []
    skipped: list = []
    failed: list = []

    codes = {(row.get("SKU编码") or "").strip() for row in rows}
    sku_map = await _sku_map_by_code(session, codes)

    for index, row in enumerate(rows, start=2):
        code = (row.get("SKU编码") or "").strip()
        errors: list[dict] = []

        def num(field: str):
            return _parse_decimal(row.get(field), row=index, field=field, errors=errors, name=code)

        purchase = num("采购成本")
        production = num("生产成本")
        package = num("包装成本")
        processing = num("加工成本")
        effective_from = _parse_date(
            row.get("生效起始日(YYYY-MM-DD)"), row=index, field="生效起始日", errors=errors, name=code
        )
        effective_to = _parse_date(
            row.get("生效截止日(YYYY-MM-DD)"), row=index, field="生效截止日", errors=errors, name=code
        )
        if errors:
            failed.extend(errors)
            continue

        if not code:
            failed.append({"row": index, "name": code, "reason": "SKU编码不能为空"})
            continue
        if effective_from is None:
            failed.append({"row": index, "name": code, "reason": "生效起始日必填——成本是带生效区间的历史版本"})
            continue
        if any(v is not None and v < 0 for v in (purchase, production, package, processing)):
            failed.append({"row": index, "name": code, "reason": "成本项不能为负数"})
            continue
        sku = sku_map.get(code)
        if sku is None:
            failed.append({"row": index, "name": code, "reason": f"找不到 SKU「{code}」"})
            continue

        try:
            existing = (
                await session.execute(
                    select(ProductCost).where(
                        ProductCost.sku_id == sku.id,
                        ProductCost.effective_from == effective_from,
                    )
                )
            ).scalars().first()
            if existing is not None:
                # 同日起导 = 更新那一版（幂等，不产生重复成本版本）
                existing.purchase_cost = purchase if purchase is not None else existing.purchase_cost
                existing.production_cost = production if production is not None else existing.production_cost
                existing.package_cost = package if package is not None else existing.package_cost
                existing.processing_cost = processing if processing is not None else existing.processing_cost
                existing.effective_to = effective_to if effective_to is not None else existing.effective_to
                existing.remark = (row.get("备注") or "").strip() or existing.remark
                skipped.append({"row": index, "name": code, "reason": f"已更新现有成本版本 #{existing.id}（同生效日）"})
                continue

            cost = ProductCost(
                sku_id=sku.id,
                purchase_cost=purchase or Decimal(0),
                production_cost=production or Decimal(0),
                package_cost=package or Decimal(0),
                processing_cost=processing or Decimal(0),
                currency=(row.get("币种(默认CNY)") or "CNY").strip() or "CNY",
                effective_from=effective_from,
                effective_to=effective_to,
                remark=(row.get("备注") or "").strip() or None,
                created_by=user.id,
            )
            session.add(cost)
            await session.flush()
            created.append({"row": index, "id": cost.id, "sku_code": code})
        except Exception as exc:
            failed.append({"row": index, "name": code, "reason": str(exc)[:120]})

    await write_audit(
        session,
        operator_id=user.id,
        action="import",
        business_type="product_cost",
        after={"created": len(created), "updated": len(skipped), "failed": len(failed)},
        ip=client_ip(request),
    )
    await session.commit()
    return _result(len(rows), created, skipped, failed, _summary(len(rows), len(created), len(skipped), len(failed), "更新"))
