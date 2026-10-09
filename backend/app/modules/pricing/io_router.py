"""价格资料的批量导入（产品报价中心 · 方案 §6）。

覆盖三类：价格规则（含等级价）、客户专属价、SKU 成本。
单独成 router 的原因与产品/客户一致：`/price-rules/import` 这类静态路径
必须注册在动态路径之前，本 router 在 main.py 里排在 pricing_router 之前。

导入语义（方案 §6，第七批返修后）：
- 逐行校验，**每行一个 SAVEPOINT**：单行失败只回滚这一行，不影响其它行，
  也不会把 session 带进失败态（详见 `app/core/importing.py` 的说明）；
- 数值、区间、有效期、币种按与页面维护同一套规则校验，非法值逐行报错、不静默截断；
- 与界面维护同一套冲突检查：同 SKU 同等级/同客户、数量区间与有效期
  都重叠的行直接失败（不静默跳过，重叠资料必须人来裁决）；
- 成本按（SKU, 生效起始日）幂等：同日再导 = 更新那一版成本，不产生重复版本；
  空白 = 保留旧值，**四项全空的新建行不落库**（否则会造出"全零成本"的假毛利）。
"""

import hashlib
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csvio import csv_bytes, parse_csv_bytes
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.importing import (
    ImportReport,
    RowErrors,
    RowRejected,
    baseline_fingerprint,
    finalize,
    row_savepoint,
)
from app.modules.customer.model import Customer
from app.modules.pricing import service as svc
from app.modules.pricing.model import CustomerPriceRule, PriceRule, ProductCost
from app.modules.product.model import Sku

router = APIRouter(tags=["Pricing"])

LEVELS = {"A", "B", "C", "D"}
#: 成本币种（用户 2026-10-06 确认）：这一批**只允许人民币成本**。
#: 外币成本要先把汇率来源、换算时点和快照口径定下来，否则核价会把
#: 美元数字和人民币数字直接相加，得出一个看着正常的错毛利。
COST_CURRENCIES = {"CNY"}
REMARK_MAX = 255


async def _sku_map_by_code(session: AsyncSession, codes: set[str]) -> dict[str, Sku]:
    """按编码取 SKU。**已删除的一律不返回**。

    原来这里不筛 `deleted_at`：给已删除的 SKU 导价格/成本会成功落库，
    而取价、核价永远查不到它 —— 用户看到"导入成功"，实际什么都没生效。
    """
    if not codes:
        return {}
    rows = (
        await session.execute(
            select(Sku).where(Sku.sku_code.in_(codes), Sku.deleted_at.is_(None))
        )
    ).scalars().all()
    return {row.sku_code: row for row in rows}


def _sku_rejection(sku: Sku | None, code: str) -> str | None:
    """SKU 是否存在且可用于价格资料；返回拒绝原因（人话），可用则 None。"""
    if sku is None:
        return f"找不到可用 SKU「{code}」，请先导入产品/SKU（已删除的 SKU 不能挂价格）"
    if sku.status != "active":
        return f"SKU「{code}」当前状态是「{sku.status}」，停用的 SKU 不接收价格资料；如确需请先启用"
    return None


# 数量区间 / 有效期 / 数值的校验统一走 `svc.check_price_rule_values`：
# **导入与普通维护接口必须共用同一份判据**（见 pricing/service.py 里那段说明）。
# 原来这里各留一份 `_check_span` / `_check_period`，正是"导入被拦、换个接口就能写"
# 的成因 —— 同一份脏数据走哪条路进来，结果都该一样。


# ================================================================== 价格规则

RULE_TEMPLATE_HEADERS = [
    "SKU编码", "客户等级(留空=通用)", "数量下限", "数量上限(留空=不限)",
    "标准价", "指导价", "最低保护价", "目标利润率(如0.30)",
    "生效起始日(YYYY-MM-DD)", "生效截止日(YYYY-MM-DD)", "历史标记(填1=历史资料)", "备注",
]


@router.get("/price-rules/import-template")
async def price_rule_template(_: CurrentUser = Depends(require_permission("price:manage"))):
    content = csv_bytes(
        [
            ["SKU-001", "A", "100", "999", "95", "85", "70", "0.30", "2026-10-01", "2026-12-31", "", "四季度促销档"],
            ["SKU-001", "", "1", "", "", "100", "", "", "2025-01-01", "2025-06-30", "1", "去年成交价，仅留档（A14）"],
        ],
        RULE_TEMPLATE_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''price-rules-import-template.csv"},
    )


async def _lock_import_skus(session: AsyncSession, sku_map: dict) -> None:
    """导入前把本批涉及的 SKU **全部**加锁（审查 P1：导入绕过了并发冲突保护）。

    从前导入只调 `find_price_rule_conflict` / `find_customer_price_conflict` 查一遍，
    而"查"和"写"之间没有锁 —— 与页面新增并发时，两边都查到"没有冲突"、
    都插入，最终**同一 SKU 同一区间留下两条启用规则**（实测指导价 66 与 55 并存）。

    两点讲究：
    - **按 SKU id 升序**逐个加锁。多 SKU 的文件里，若两个导入按不同顺序拿锁，
      互相等对方持有的锁就会死锁；统一升序可以避免。
    - 一次事务内拿完全部锁再开始写入：本批所有行的冲突检查都在持锁状态下进行。

    导入本来就是"整批一个事务"，所以用事务级 advisory lock（连接级会在结束时释放，
    但事务级更精确：提交/回滚即释放）。
    """
    for sku_id in sorted({sku.id for sku in sku_map.values()}):
        await svc.lock_sku_price_rules(session, sku_id)


@router.post("/price-rules/import")
async def import_price_rules(
    request: Request,
    file: UploadFile = File(...),
    preview: bool = Form(False),
    preview_token: str | None = Form(None),
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入价格规则。同 SKU 同等级且区间重叠的行判失败（与界面维护同一规则）。"""
    raw = await file.read()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse_csv_bytes(
        raw, required_headers=["SKU编码", "指导价"], label="文件"
    )
    report = ImportReport("price_rule", len(rows))

    codes = {(row.get("SKU编码") or "").strip() for row in rows}
    sku_map = await _sku_map_by_code(session, codes)
    # ⚠️ 加锁必须在**任何冲突检查之前**（审查 P1）：否则与页面新增并发时，
    # 两边都查到"没有冲突"、都插入，同一区间落两条启用规则。
    await _lock_import_skus(session, sku_map)

    for index, row in enumerate(rows, start=2):
        code = (row.get("SKU编码") or "").strip()
        errs = RowErrors(index, code)

        level = (row.get("客户等级(留空=通用)") or "").strip().upper() or None
        if level and level not in LEVELS:
            errs.add(f"客户等级必须是 A/B/C/D（收到 {level}）")

        min_qty = errs.decimal(row.get("数量下限"), "数量下限")
        min_qty = Decimal(0) if min_qty is None else min_qty
        max_qty = errs.decimal(row.get("数量上限(留空=不限)"), "数量上限")
        guide_price = errs.decimal(row.get("指导价"), "指导价", required=True)
        standard_price = errs.decimal(row.get("标准价"), "标准价")
        minimum_price = errs.decimal(row.get("最低保护价"), "最低保护价")
        target_margin = errs.decimal(row.get("目标利润率(如0.30)"), "目标利润率")
        effective_from = errs.date_value(row.get("生效起始日(YYYY-MM-DD)"), "生效起始日")
        effective_to = errs.date_value(row.get("生效截止日(YYYY-MM-DD)"), "生效截止日")
        remark = errs.text_value(row.get("备注"), "备注", max_length=REMARK_MAX)
        # 数值、倒置区间、倒置有效期、利润率比率 —— **与普通维护接口共用同一份判据**。
        svc.check_price_rule_values(
            errs,
            min_qty=min_qty,
            max_qty=max_qty,
            standard_price=standard_price,
            guide_price=guide_price,
            minimum_price=minimum_price,
            target_margin=target_margin,
            effective_from=effective_from,
            effective_to=effective_to,
        )

        if not code:
            errs.add("SKU编码不能为空")
        if errs:
            report.failed_row(index, code, errs.reasons)
            continue

        rejection = _sku_rejection(sku_map.get(code), code)
        if rejection:
            report.failed_row(index, code, rejection)
            continue
        sku = sku_map[code]

        historical = (row.get("历史标记(填1=历史资料)") or "").strip()
        rule_status = "historical" if historical else "active"
        try:
            async with row_savepoint(session):
                if not historical:
                    # 历史资料允许与当前规则重叠（A14：不参与匹配也不参与冲突检查）
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
                        # 业务性拒绝不是异常：直接记行失败，不要走 savepoint 的异常通道
                        raise RowRejected(
                            f"与现有规则 #{conflict.id}（数量 {conflict.min_qty}-"
                            f"{conflict.max_qty or '∞'}、等级 {conflict.customer_level or '通用'}）"
                            "重叠，请先处理"
                        )
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
                    status=rule_status,
                    remark=remark,
                )
                session.add(rule)
                await session.flush()
                rule_id = rule.id
            report.created_row(index, code, id=rule_id, sku_code=code)
        except RowRejected as rejected:
            report.failed_row(index, code, str(rejected))
        except Exception as exc:  # 单行失败不影响其它行
            report.failed_row(index, code, f"写入失败：{str(exc)[:160]}")

    return await finalize(
        session,
        report,
        module="price_rule",
        operator_id=user.id,
        file_name=file.filename,
        file_sha256=file_sha256,
        ip=client_ip(request),
        preview=preview,
        preview_token=preview_token,
        business_type="price_rule",
    )


# ================================================================== 客户专属价

CP_TEMPLATE_HEADERS = [
    "客户编号(与客户名称二选一)", "客户名称(精确匹配)", "SKU编码",
    "数量下限", "数量上限(留空=不限)", "约定价", "最低价",
    "生效起始日(YYYY-MM-DD)", "生效截止日(YYYY-MM-DD)", "历史标记(填1=历史资料)", "备注",
]


@router.get("/customer-price-rules/import-template")
async def customer_price_template(_: CurrentUser = Depends(require_permission("price:manage"))):
    content = csv_bytes(
        [
            ["", "示例客户有限公司", "SKU-001", "1000", "", "88", "75", "2026-01-01", "2026-12-31", "", "年度框架协议价"],
            ["", "示例客户有限公司", "SKU-001", "1000", "", "80", "", "2025-01-01", "2025-12-31", "1", "去年协议价，仅留档（A14）"],
        ],
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
    preview: bool = Form(False),
    preview_token: str | None = Form(None),
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入客户专属价。

    客户用「编号」或「名称精确匹配」定位。第七批返修（7.3）补齐的四件事：
    1. 已删除 / 已停用的客户与 SKU 一律不接受（原来只判存在，
       给已删客户挂专属价会"导入成功但永远匹配不到"）；
    2. 编号与名称**同时填且指向不同客户**时明确报错，不悄悄以编号为准；
    3. 名称歧义（同名多家）必须改用编号；
    4. 逐行按调用者的数据范围校验客户归属：**范围外的客户不能写专属价**，
       预览也一样（预览会返回逐行结论，泄漏范围外客户身份等于给了个查询接口）。
       无负责人的公海客户按既有口径对所有有权限的人开放。
    """
    from app.core.data_scope import scoped_owner_ids

    raw = await file.read()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse_csv_bytes(raw, required_headers=["SKU编码", "约定价"], label="文件")
    report = ImportReport("customer_price_rule", len(rows))

    codes = {(row.get("SKU编码") or "").strip() for row in rows}
    sku_map = await _sku_map_by_code(session, codes)
    # 同 `import_price_rules`：锁必须在冲突检查之前（审查 P1，两个入口都复现了）
    await _lock_import_skus(session, sku_map)
    owner_ids = await scoped_owner_ids(session, user)

    for index, row in enumerate(rows, start=2):
        code = (row.get("SKU编码") or "").strip()
        customer_no = (row.get("客户编号(与客户名称二选一)") or "").strip()
        customer_name = (row.get("客户名称(精确匹配)") or "").strip()
        errs = RowErrors(index, code)

        agreed_price = errs.decimal(row.get("约定价"), "约定价", required=True)
        minimum_price = errs.decimal(row.get("最低价"), "最低价")
        min_qty = errs.decimal(row.get("数量下限"), "数量下限")
        min_qty = Decimal(0) if min_qty is None else min_qty
        max_qty = errs.decimal(row.get("数量上限(留空=不限)"), "数量上限")
        effective_from = errs.date_value(row.get("生效起始日(YYYY-MM-DD)"), "生效起始日")
        effective_to = errs.date_value(row.get("生效截止日(YYYY-MM-DD)"), "生效截止日")
        remark = errs.text_value(row.get("备注"), "备注", max_length=REMARK_MAX)
        # 数值、区间、有效期、最低价与约定价的关系 —— **与普通维护接口共用同一份判据**。
        svc.check_price_rule_values(
            errs,
            min_qty=min_qty,
            max_qty=max_qty,
            agreed_price=agreed_price,
            minimum_price=minimum_price,
            effective_from=effective_from,
            effective_to=effective_to,
        )

        if not code:
            errs.add("SKU编码不能为空")
        if not customer_no and not customer_name:
            errs.add("客户编号与客户名称至少填一个")
        if errs:
            report.failed_row(index, code, errs.reasons)
            continue

        rejection = _sku_rejection(sku_map.get(code), code)
        if rejection:
            report.failed_row(index, code, rejection)
            continue
        sku = sku_map[code]

        customer: Customer | None = None
        if customer_no:
            if not customer_no.isdigit():
                report.failed_row(index, code, f"客户编号应为数字（收到 {customer_no!r}）")
                continue
            customer = await session.get(Customer, int(customer_no))
            if customer is None or customer.deleted_at is not None:
                report.failed_row(index, code, f"客户编号 {customer_no} 不存在或已删除")
                continue
            if customer_name and customer.name != customer_name:
                report.failed_row(
                    index,
                    code,
                    f"客户编号 {customer_no} 对应的是「{customer.name}」，"
                    f"与填写的客户名称「{customer_name}」不一致，请核对后只填其一",
                )
                continue
        else:
            found = (
                await session.execute(
                    select(Customer)
                    .where(Customer.name == customer_name, Customer.deleted_at.is_(None))
                    .limit(2)
                )
            ).scalars().all()
            if not found:
                report.failed_row(index, code, f"找不到客户「{customer_name}」（已删除的不算）")
                continue
            if len(found) > 1:
                report.failed_row(
                    index,
                    code,
                    f"客户名称「{customer_name}」匹配到 {len(found)} 家，同名必须改用客户编号指定",
                )
                continue
            customer = found[0]

        # 数据范围：与"能不能看这个客户"同一口径。无负责人（公海）放行。
        if owner_ids is not None and customer.owner_id is not None:
            if int(customer.owner_id) not in owner_ids:
                report.failed_row(
                    index,
                    code,
                    "该客户不在你的数据范围内，不能为它维护专属价；"
                    "请联系对应负责人或主管处理",
                )
                continue

        historical = (row.get("历史标记(填1=历史资料)") or "").strip()
        rule_status = "historical" if historical else "active"
        try:
            async with row_savepoint(session):
                if not historical:
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
                        raise RowRejected(
                            f"该客户此 SKU 已有规则 #{conflict.id}（数量 {conflict.min_qty}-"
                            f"{conflict.max_qty or '∞'}）重叠，请先处理"
                        )
                rule = CustomerPriceRule(
                    customer_id=customer.id,
                    sku_id=sku.id,
                    min_qty=min_qty,
                    max_qty=max_qty,
                    agreed_price=agreed_price,
                    minimum_price=minimum_price,
                    effective_from=effective_from,
                    effective_to=effective_to,
                    status=rule_status,
                    remark=remark,
                )
                session.add(rule)
                await session.flush()
                rule_id = rule.id
            report.created_row(index, code, id=rule_id, sku_code=code)
        except RowRejected as rejected:
            report.failed_row(index, code, str(rejected))
        except Exception as exc:
            report.failed_row(index, code, f"写入失败：{str(exc)[:160]}")

    return await finalize(
        session,
        report,
        module="customer_price_rule",
        operator_id=user.id,
        file_name=file.filename,
        file_sha256=file_sha256,
        ip=client_ip(request),
        preview=preview,
        preview_token=preview_token,
        business_type="customer_price_rule",
    )


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
    preview: bool = Form(False),
    preview_token: str | None = Form(None),
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入成本。按（SKU, 生效起始日）幂等：同日再导 = 更新那一版成本。

    第七批返修（7.4）的口径：
    - **新建时四项成本全空 → 这行失败，不落库**。原实现把空白当 0，
      于是"只填了 SKU 和生效日"的模板行会造出一条四项全零的成本，
      而核价是靠"有没有成本行"判断成本已知的 —— 直接产生假毛利。
      确实为零请显式填 0，那是"已知成本为零"，与"没填"是两件事；
    - 只填部分 → 未填的列存 NULL（"未提供"），核价据此显示成本不完整，
      不再把 NULL 当成 0 混进合计；
    - 更新同日已有版本时，空白 = **保留旧值**；
    - 币种只接受人民币（用户 2026-10-06 确认），外币成本本轮不支持。
    """
    raw = await file.read()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse_csv_bytes(
        raw, required_headers=["SKU编码", "生效起始日(YYYY-MM-DD)"], label="文件"
    )
    report = ImportReport("product_cost", len(rows))

    codes = {(row.get("SKU编码") or "").strip() for row in rows}
    sku_map = await _sku_map_by_code(session, codes)

    for index, row in enumerate(rows, start=2):
        code = (row.get("SKU编码") or "").strip()
        errs = RowErrors(index, code)

        purchase = errs.decimal(row.get("采购成本"), "采购成本", non_negative=True)
        production = errs.decimal(row.get("生产成本"), "生产成本", non_negative=True)
        package = errs.decimal(row.get("包装成本"), "包装成本", non_negative=True)
        processing = errs.decimal(row.get("加工成本"), "加工成本", non_negative=True)
        effective_from = errs.date_value(
            row.get("生效起始日(YYYY-MM-DD)"), "生效起始日", required=True
        )
        effective_to = errs.date_value(row.get("生效截止日(YYYY-MM-DD)"), "生效截止日")
        currency = (row.get("币种(默认CNY)") or "CNY").strip().upper() or "CNY"
        if currency not in COST_CURRENCIES:
            errs.add(
                f"本轮只支持人民币成本，币种只能填 CNY（收到 {currency}）。"
                "外币成本要先确定汇率来源与换算时点，请先按人民币录入或联系管理员"
            )
        remark = errs.text_value(row.get("备注"), "备注", max_length=REMARK_MAX)
        # 成本也有生效区间，顺序判据与价格规则共用同一份。
        svc.check_effective_period(
            errs, effective_from=effective_from, effective_to=effective_to
        )
        if not code:
            errs.add("SKU编码不能为空")
        if errs:
            report.failed_row(index, code, errs.reasons)
            continue

        rejection = _sku_rejection(sku_map.get(code), code)
        if rejection:
            report.failed_row(index, code, rejection)
            continue
        sku = sku_map[code]

        provided = {
            "purchase_cost": purchase,
            "production_cost": production,
            "package_cost": package,
            "processing_cost": processing,
        }
        filled = {key: value for key, value in provided.items() if value is not None}

        try:
            async with row_savepoint(session):
                existing = (
                    await session.execute(
                        select(ProductCost)
                        .where(
                            ProductCost.sku_id == sku.id,
                            ProductCost.effective_from == effective_from,
                        )
                        # 行锁 + `populate_existing`：本会话此前可能已经读过这一行，
                        # 少了后面那句拿回来的是**内存里的旧值**（会话是
                        # `expire_on_commit=False`），"别人改过没有"就判不出来 ——
                        # 锁住了库里的行却比对了旧值，并发保护等于白加。
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalars().first()
                if existing is not None:
                    # 原值指纹必须在**动它之前**算：确认导入时再算一次比对，
                    # 就能发现"预览之后别人改过这一行"（审查 2026-10-07 第二轮实测：
                    # 两次结论都是"更新"，于是 20 直接盖掉了别人刚写的 200）。
                    # 只喂真正参与更新的字段 —— 无关字段变动不该拦下这次导入。
                    baseline = baseline_fingerprint(
                        existing.purchase_cost,
                        existing.production_cost,
                        existing.package_cost,
                        existing.processing_cost,
                        existing.effective_to,
                        existing.remark,
                    )
                    # 同日起导 = 更新那一版（幂等，不产生重复成本版本）；空白保留旧值
                    changed = False
                    for key, value in provided.items():
                        if value is None:
                            continue
                        if getattr(existing, key) != value:
                            setattr(existing, key, value)
                            changed = True
                    if effective_to is not None and existing.effective_to != effective_to:
                        existing.effective_to = effective_to
                        changed = True
                    if remark is not None and existing.remark != remark:
                        existing.remark = remark
                        changed = True
                    await session.flush()
                    if changed:
                        report.updated_row(
                            index, code, id=existing.id, changed=True,
                            reason=f"已更新同日成本版本 #{existing.id}",
                            baseline=baseline,
                        )
                    else:
                        report.skipped_row(
                            index, code,
                            f"同日成本版本 #{existing.id} 与本次内容一致，未改动",
                        )
                    continue

                if not filled:
                    # 新建 + 四项全空：不能落一条"全零成本"，那会让核价以为成本已知
                    raise RowRejected(
                        "四项成本全为空，不能新建成本版本：这会造出一条全零成本，"
                        "核价会据此算出假毛利。请至少填一项；确实为零请显式填 0"
                    )

                cost = ProductCost(
                    sku_id=sku.id,
                    # 未填的列存 NULL（"未提供"），不写 0（"明确为零"）
                    purchase_cost=purchase,
                    production_cost=production,
                    package_cost=package,
                    processing_cost=processing,
                    currency=currency,
                    effective_from=effective_from,
                    effective_to=effective_to,
                    remark=remark,
                    created_by=user.id,
                )
                session.add(cost)
                await session.flush()
                cost_id = cost.id
            report.created_row(index, code, id=cost_id, sku_code=code, filled=list(filled))
        except RowRejected as rejected:
            report.failed_row(index, code, str(rejected))
        except Exception as exc:
            report.failed_row(index, code, f"写入失败：{str(exc)[:160]}")

    return await finalize(
        session,
        report,
        module="product_cost",
        operator_id=user.id,
        file_name=file.filename,
        file_sha256=file_sha256,
        ip=client_ip(request),
        preview=preview,
        preview_token=preview_token,
        business_type="product_cost",
    )
