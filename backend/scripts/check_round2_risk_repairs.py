"""第二轮审查 11 项修复的真实验证（必须在一次性 PostgreSQL 库上跑）。

为什么单独一个脚本：本轮 4 项属于并发/事务边界，SQLite 单写者复现不出来；
其余几项（审批档位、外币利润、对客文件闸门、单价精度）也要连服务层一起验，
而不是只测纯函数。

跑法（仓库根；库名必须是 crm_iso / crm_check 开头的一次性库）：

    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE crm_check_x OWNER crm;"
    cd backend
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_x \
      PYTHONPATH=. .venv/bin/alembic upgrade head
    DATABASE_URL=... PYTHONPATH=. .venv/bin/python scripts/seed.py
    API_BASE=http://127.0.0.1:8079/api/v1 DATABASE_URL=... FILE_ROOT=data/iso-files-x \
      PYTHONPATH=. .venv/bin/python scripts/check_round2_risk_repairs.py

探针（对应第二轮 11 项）：
1. 并发改费用 / 明细 → 合计按锁内真值重算（两条独立连接，交错形态各验一次）；
2. Excel 报价文件：审批 / 主数据 / 运费三道闸门不过只能落草稿；
3. 缺成本、负产品收入 → 毛利率不可计算，免审规则不命中；
4. 拉取等待期间订单被取消 → 回写不覆盖取消；
5. 已取消订单不能再推外单；
6. 同键换订单 → 409（既不回放别张订单的结果，也不凭空生成）；
7. 三个推单入口统一 confirm 契约（42206、同一份文案）；
8. 对客文件单价保留实际精度（33.3333 × 1000 与金额一致）；
9. 用户文本（`=1+1`）在 Excel 里是字符串不是公式；
10. 外币按快照汇率折算的人民币金额选审批档位；
11. 新建外币版本先落汇率快照再重算利润。

跑完自己清掉本轮夹具与原件，不留脏数据。
"""

import asyncio
import io
import os
import sys
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select, text  # noqa: E402

from _test_support import require_api_base, require_isolated_db  # noqa: E402

require_api_base()
require_isolated_db()

from app.core.database import SessionLocal, engine  # noqa: E402
from app.core.deps import CurrentUser  # noqa: E402
from app.core.errors import AppError, ErrorCode  # noqa: E402
from app.core.money import format_unit_price, line_amount  # noqa: E402
from app.modules.approval import rules_engine  # noqa: E402
from app.modules.approval.model import ApprovalRule  # noqa: E402
from app.modules.bizdoc import service as bizdocs  # noqa: E402
from app.modules.bizdoc.model import BizDoc  # noqa: E402
from app.modules.customer.model import Customer  # noqa: E402
from app.modules.erp import router as erp_router  # noqa: E402
from app.modules.erp import service as erp_service  # noqa: E402
from app.modules.integration.model import ExternalMapping  # noqa: E402
from app.modules.erp.schema import OrderPushRequest  # noqa: E402
from app.modules.file import storage  # noqa: E402
from app.modules.file.model import FileRecord  # noqa: E402
from app.modules.order import router as order_router  # noqa: E402
from app.modules.order.model import SalesOrder  # noqa: E402
from app.modules.payment import router as payment_router  # noqa: E402
from app.modules.payment.model import ReceivablePlan  # noqa: E402
from app.modules.payment.schema import ReceivableGenerate  # noqa: E402
from app.modules.pricing.model import ExchangeRate  # noqa: E402
from app.modules.product import master as master_service  # noqa: E402
from app.modules.product.model import Product, Sku  # noqa: E402
from app.modules.quote import service as quotes  # noqa: E402
from app.modules.quote.model import Quote, QuoteCharge, QuoteItem, QuoteVersion  # noqa: E402
from app.modules.quote.router import update_charge  # noqa: E402
from app.modules.quote.schema import QuoteChargeUpdate  # noqa: E402
from app.modules.settings import service as settings_service  # noqa: E402
from app.modules.user.model import Role, User  # noqa: E402

PREFIX = f"CHK2{int(time.time())}"
FAILURES: list[str] = []


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=""):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


class _StubClient:
    host = "127.0.0.1"


class _StubRequest:
    """只给 `client_ip()` / 审计用；本脚本不经过 HTTP 层。"""

    client = _StubClient()
    headers: dict[str, str] = {}


REQUEST = _StubRequest()


def viewer(user: User, roles: list[str]) -> CurrentUser:
    return CurrentUser(user, {"*"}, roles, "all")


def code_of(error, default=None):
    return getattr(error, "code", default) if error is not None else None


async def _gather_aligned(*coros, timeout=120):
    """多条独立连接对齐后同时开跑（用事件而不是 sleep 碰运气）。"""
    start = asyncio.Event()

    async def _wrapped(coro):
        await start.wait()
        return await coro

    tasks = [asyncio.create_task(_wrapped(c)) for c in coros]
    await asyncio.sleep(0)
    start.set()
    return await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)


# --------------------------------------------------------------------- 夹具


async def make_quote(
    session,
    *,
    owner_id: int,
    unit_cost: Decimal | None = Decimal("60"),
    price: Decimal = Decimal("100"),
    quantity: Decimal = Decimal("10"),
    currency: str = "CNY",
    rate: Decimal | None = None,
) -> tuple[Quote, QuoteVersion, QuoteItem, Sku, Customer]:
    tag = f"{PREFIX}{uuid4().hex[:8]}"
    product = Product(name=tag)
    session.add(product)
    await session.flush()
    sku = Sku(product_id=product.id, sku_code=tag, name=tag, unit="件",
              specification="标准")
    session.add(sku)
    await session.flush()
    customer = Customer(name=tag, owner_id=owner_id, status="active", level="A")
    session.add(customer)
    await session.flush()
    quote = Quote(
        quote_no=tag, customer_id=customer.id, owner_id=owner_id, status="draft",
        valid_until=datetime.now(UTC).date(),
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    session.add(quote)
    await session.flush()
    version = QuoteVersion(
        quote_id=quote.id, version_no=1, currency=currency,
        exchange_rate_snapshot=rate,
        payment_terms="款到发货", delivery_terms="厂内交货",
        customer_name_snapshot=customer.name,
        created_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()
    item = QuoteItem(
        quote_version_id=version.id, sku_id=sku.id, sku_code_snapshot=sku.sku_code,
        sku_name_snapshot=sku.name, spec_snapshot="标准", unit_snapshot="件",
        quantity=quantity, quoted_price=price, cost_snapshot=unit_cost,
        package_cost_snapshot=Decimal("0"), logistics_cost_snapshot=Decimal("0"),
        standard_price_snapshot=price, minimum_price_snapshot=Decimal("0"),
    )
    session.add(item)
    await session.flush()
    quote.current_version_id = version.id
    await quotes.recalc_version(session, version)
    await session.flush()
    return quote, version, item, sku, customer


async def confirm_freight(session, version) -> None:
    """给这一版补一条**已确认的 0 元运费**。

    ⚠️ **只在需要"提交审批"的用例里调用**，不要塞进 `make_quote`：
    本套件有一处专门验"运费未确认 → 报价文件是草稿"（Excel 三道闸门那个用例），
    夹具里预加一条已确认的运费会把它的前提消掉（实测踩到：③ 与 ④ 双双失败）。

    用 **0 元**：不改变 `total_amount`，所以"分档金额 = 70000"这类断言一个都不用动。
    0 元也是"明确确认的运费"（口径如此）——运费为空与"确认为 0"是两回事。
    """
    session.add(QuoteCharge(
        quote_version_id=version.id, charge_type="logistics", amount=Decimal("0"),
        is_discount=False, logistics_confirmed_at=datetime.now(UTC),
        description="套件夹具：明确 0 元运费",
    ))
    await session.flush()
    await quotes.recalc_version(session, version)


async def make_order(session, *, owner_id: int, status: str = "pending") -> SalesOrder:
    tag = f"{PREFIX}{uuid4().hex[:8]}"
    customer = Customer(name=tag, owner_id=owner_id, status="active", level="A")
    session.add(customer)
    await session.flush()
    order = SalesOrder(
        order_no=tag, customer_id=customer.id, owner_id=owner_id, status=status,
        currency="CNY", total_amount=Decimal("100"), created_at=datetime.now(UTC),
    )
    session.add(order)
    await session.flush()
    return order


async def confirm_master(session, *, sku_id: int, version: QuoteVersion, user_id: int):
    """确认 SKU 的对客三字段，并把明细接到那一版上（走真实服务函数）。"""
    await master_service.confirm_local_master(session, sku_id=sku_id, operator_id=user_id)
    await session.flush()
    await quotes.link_master_only(session, version=version)
    await session.flush()


# --------------------------------------------------------------------- 探针


async def probe_formatting():
    """⑧⑨ 纯函数级：单价精度 + 文本类型（离线也能跑，钉在脚本里防回归）。"""
    print("=== 8/9. 单价精度与文本类型 ===")
    check("33.3333 原样保留", format_unit_price(Decimal("33.3333")), "33.3333")
    check("整数不补小数位", format_unit_price(Decimal("100")), "100")
    check("千分位", format_unit_price(Decimal("12500.5")), "12,500.5")
    check("空值给空串", format_unit_price(None), "")
    check("非数字原样返回", format_unit_price("abc"), "abc")
    check(
        "单价×数量与金额口径一致（旧写法会差 3.30）",
        str(line_amount(Decimal("1000"), Decimal("33.3333"))), "33333.30",
    )
    # ⑨ 单元格类型：直接验渲染器（office 文本必须落成字符串）
    from openpyxl import load_workbook

    from app.modules.bizdoc.xlsx import render_quote_xlsx

    data = {
        "company_name": "=1+1",
        "title": "报价单",
        "doc_no": "Q-CHK",
        "version": 1,
        "customer_name": "=cmd|'/c calc'!A0",
        "contact_name": "张三",
        "owner_name": "李四",
        "created_date": "2026-10-12",
        "status_label": "已发送",
        "items": [
            {"name": "=SUM(A1:A9)", "spec": "600×400", "quantity": Decimal("1000"),
             "unit": "个", "unit_price": Decimal("33.3333"),
             "amount": Decimal("33333.30"), "remark": "=HYPERLINK(\"http://x\")"},
        ],
        "subtotal_amount": Decimal("33333.30"),
        "total_amount": Decimal("33333.30"),
        "charges": [{"label": "宁波到苏州运费", "amount": Decimal("300.00")}],
        "sections": [],
        "frozen": {"items": [{"unit": "个"}]},
    }
    sheet = load_workbook(io.BytesIO(render_quote_xlsx(data))).active
    formulas = [
        c.coordinate for row in sheet.iter_rows() for c in row if c.data_type == "f"
    ]
    check("Excel 里没有公式单元格", formulas, [])
    values = [c.value for row in sheet.iter_rows() for c in row]
    check_true("单价按四位精度印出", "33.3333" in values, str(values[:6]))
    check_true("公司名原样保存（不被当公式）", "=1+1" in values)

    # PDF 单价（同一份格式器）：把内容流解开再找文本，证明它印的也是 33.3333
    from app.modules.quote.pdf import render_quote_pdf

    pdf = render_quote_pdf({
        "items": [{"sku_code": "S1", "specification": "600×400", "quantity": 1000,
                   "quoted_price": Decimal("33.3333"), "amount": Decimal("33333.30")}],
        "quote_no": "Q-CHK", "version_no": 1, "customer_name": "客户",
        "owner_name": "李四", "quote_date": "2026-10-12", "valid_until": "2026-11-12",
        "status_label": "已发送", "company_name": "本公司", "summary": {}, "charges": [],
    })
    text = _inflate_pdf_text(pdf)
    check_true("PDF 里单价也是 33.3333", "33.3333" in text, text[-160:])


def _inflate_pdf_text(data: bytes) -> str:
    """把 PDF 内容流解开，取出"文件上实际印了什么"（供断言用）。

    reportlab 的内容流是 `ASCII85Decode + FlateDecode`，且 ASCII85 带 `~>` 终止符
    （要用 `adobe=True` 解）；文本写在 `(...) Tj` 里，数字是 ASCII 字面量。
    不使用真实 PDF 解析库是有意的：这里只回答"那一串单价字符串印没印出来"，
    引一个解析依赖不值得。
    """
    import base64 as _b64
    import re as _re
    import zlib

    chunks: list[str] = []
    for match in _re.finditer(rb"(?<!end)stream\r?\n", data):
        start = match.end()
        end = data.find(b"endstream", start)
        blob = data[start:end].strip()
        for attempt in (
            lambda b: zlib.decompress(_b64.a85decode(b, adobe=True)),
            lambda b: zlib.decompress(_b64.a85decode(b.rstrip(b"~>"), adobe=False)),
            lambda b: zlib.decompress(b),
        ):
            try:
                chunks.append(attempt(blob).decode("latin-1"))
                break
            except Exception:  # noqa: BLE001 —— 换下一种解码方式
                continue
    content = "\n".join(chunks)
    # `(...) Tj` 里的字面量：数字与字母是 ASCII；中文是 CID 字节，这里只关心数字
    literals = _re.findall(r"\(((?:\\.|[^\\()])*)\)\s*Tj", content)
    plain = "\n".join(_re.sub(r"\\([0-7]{1,3})", "", item) for item in literals)
    return content + "\n" + plain


async def probe_margin_none(session, owner_id, roles, user):
    """③ 缺成本 / 负产品收入：毛利率不可计算时不得命中免审。"""
    print("=== 3. 缺成本 / 负产品收入 → 毛利率不可计算 ===")
    check("缺成本 → 综合毛利率（A06：不可计算）", await _margin(session, owner_id, None,
                                                                Decimal("50"), Decimal("1")), None)
    check("缺成本 → 最低明细毛利率", await _margin_min(session, owner_id, None,
                                                        Decimal("50"), Decimal("1")), None)
    check("负产品收入 → 综合毛利率", await _margin(session, owner_id, Decimal("100"),
                                                    Decimal("10"), Decimal("1"), Decimal("-100")), None)
    check("正常单 → 综合毛利率 40%", await _margin(session, owner_id, Decimal("60"),
                                                    Decimal("100"), Decimal("1")), 40.0)

    async def _hit(cost, price, quantity, subtotal=None):
        quote, version, item, _sku, _customer = await make_quote(
            session, owner_id=owner_id, unit_cost=cost, price=price,
            quantity=quantity,
        )
        if subtotal is not None:
            version.total_amount = subtotal
            version.subtotal_amount = subtotal
            await session.flush()
        items = await quotes.version_items(session, version.id)
        ctx = await rules_engine.build_context(
            session, quote=quote, version=version, items=items,
            fx=version.exchange_rate_snapshot,
        )
        return ctx

    auto = (await session.execute(
        select(ApprovalRule).where(ApprovalRule.enabled.is_(True),
                                   ApprovalRule.kind == "auto_pass")
    )).scalars().first()
    if auto is not None:
        conds = auto.conditions or []
        ctx_missing = await _hit(None, Decimal("50"), Decimal("1"))
        ctx_negative = await _hit(Decimal("100"), Decimal("10"), Decimal("1"), Decimal("-100"))
        ctx_ok = await _hit(Decimal("60"), Decimal("100"), Decimal("1"))
        hit_missing, _ = rules_engine.evaluate_conditions(conds, ctx_missing)
        hit_negative, _ = rules_engine.evaluate_conditions(conds, ctx_negative)
        hit_ok, _ = rules_engine.evaluate_conditions(conds, ctx_ok)
        print(f"  规则「{auto.name}」条件：{conds}")
        check_true("免审规则在缺成本时**不命中**（旧行为是毛利 100% 直接放行）",
                   not hit_missing)
        check_true("免审规则在负产品收入时不命中（旧行为是毛利 200%）",
                   not hit_negative)
        check_true("正常单仍可命中免审", hit_ok)
    else:
        check_true("种子里有启用的免审规则", False, "没找到 auto_pass 规则")
    _ = roles, user


async def _margin(session, owner_id, cost, price, quantity, subtotal=None):
    ctx = await _ctx(session, owner_id, cost, price, quantity, subtotal)
    return ctx["gross_margin"]


async def _margin_min(session, owner_id, cost, price, quantity):
    ctx = await _ctx(session, owner_id, cost, price, quantity)
    return ctx["min_item_margin"]


async def _ctx(session, owner_id, cost, price, quantity, subtotal=None):
    quote, version, item, _sku, _cust = await make_quote(
        session, owner_id=owner_id, unit_cost=cost, price=price, quantity=quantity
    )
    if subtotal is not None:
        version.total_amount = subtotal
        version.subtotal_amount = subtotal
        await session.flush()
    items = await quotes.version_items(session, version.id)
    return await rules_engine.build_context(
        session, quote=quote, version=version, items=items,
        fx=version.exchange_rate_snapshot,
    )


async def probe_excel_gates(session, owner_id, roles, user):
    """② Excel 报价文件的三道闸门。"""
    print("=== 2. Excel 报价文件的闸门（审批 / 主数据 / 运费）===")
    quote, version, item, sku, _customer = await make_quote(
        session, owner_id=owner_id, unit_cost=Decimal("60"), price=Decimal("33.3333"),
        quantity=Decimal("1000"),
    )
    session.add(QuoteCharge(
        quote_version_id=version.id, charge_type="logistics", description="宁波到苏州运费",
        amount=Decimal("0"), is_discount=False,
    ))
    await session.flush()
    await quotes.recalc_version(session, version)
    await session.flush()

    doc1 = await bizdocs.generate_quote_doc(session, quote_version_id=version.id, user=user)
    await session.flush()
    check("① 未提交审批 → 状态", doc1.status, "draft")
    check_true("① 标题带【草稿】", doc1.title.startswith("【草稿】"), doc1.title[:40])
    check_true("① 原因点名审批", "未通过审批" in (doc1.input_snapshot or {}).get("draft_reason", ""),
               (doc1.input_snapshot or {}).get("draft_reason", "")[:90])

    version.approval_status = "approved"
    await session.flush()
    doc2 = await bizdocs.generate_quote_doc(session, quote_version_id=version.id, user=user)
    await session.flush()
    check("② 已审批但主数据未确认 → 状态", doc2.status, "draft")
    check_true("② 原因点名主数据",
               "主数据未确认" in (doc2.input_snapshot or {}).get("draft_reason", ""),
               (doc2.input_snapshot or {}).get("draft_reason", "")[:90])

    await confirm_master(session, sku_id=sku.id, version=version, user_id=user.id)
    doc3 = await bizdocs.generate_quote_doc(session, quote_version_id=version.id, user=user)
    await session.flush()
    check("③ 主数据齐了但运费未确认 → 状态", doc3.status, "draft")
    reason3 = (doc3.input_snapshot or {}).get("draft_reason", "")
    check_true("③ 原因点名运费", "运费" in reason3, reason3[:90])

    # 运费确认（0 元也要显式确认）
    charge = (await session.execute(
        select(QuoteCharge).where(QuoteCharge.quote_version_id == version.id)
    )).scalars().first()
    quotes.mark_logistics_confirmed(charge)
    await session.flush()
    doc4 = await bizdocs.generate_quote_doc(session, quote_version_id=version.id, user=user)
    await session.flush()
    check("④ 三道闸门全过 → 状态", doc4.status, "active")
    check_true("④ 正式件标题没有【草稿】", not doc4.title.startswith("【草稿】"), doc4.title[:40])

    # ⑧⑨ 用**真实生成的那份文件**回读：单价精度 + 无公式
    record = await session.get(FileRecord, doc4.file_id)
    data = await asyncio.to_thread(storage.absolute_path(record.object_key).read_bytes)
    from openpyxl import load_workbook

    sheet = await asyncio.to_thread(load_workbook, io.BytesIO(data))
    formulas = [
        c.coordinate for row in sheet.active.iter_rows() for c in row
        if c.data_type == "f"
    ]
    check("Excel 里没有公式单元格", formulas, [])
    values = [c.value for row in sheet.active.iter_rows() for c in row]
    check_true("单价按 33.3333 印出（不再被截成 33.33）", "33.3333" in values,
               str([v for v in values if isinstance(v, str) and v.startswith("33")][:4]))
    print(f"  文件大小 {len(data)} 字节，文件号 {doc4.doc_no}")


async def probe_foreign_tier(session, owner_id, roles, user):
    """⑩ 外币审批档位按人民币折算金额。"""
    print("=== 10. 外币审批档位用人民币折算金额 ===")
    levels = await settings_service.get_list(session, "approval_levels")
    print(f"  当前审批分级：{levels}")
    check_true("系统设置里有审批分级", bool(levels))
    if not levels:
        return
    first_cap = levels[0].get("max_amount")
    # USD 10000 × 7 = 人民币 70000；原币数字 10000 会落进第一档（上限 50000）。
    # 成本给 90000 元人民币，让利润率低于角色授权线 → 必须生成审批单（否则会被免审吞掉，
    # 档位就无从检查）。
    quote, version, item, _sku, _cust = await make_quote(
        session, owner_id=owner_id, unit_cost=Decimal("90000"), price=Decimal("10000"),
        quantity=Decimal("1"), currency="USD", rate=Decimal("7"),
    )
    item.approval_required = True
    item.approval_reason = "检查用：强制生成审批单"
    await session.flush()
    q = await session.get(Quote, quote.id)
    v = await session.get(QuoteVersion, version.id)
    # 运费闸已前置到提交审批（2026-10-12）：先确认运费再提交
    await confirm_freight(session, v)
    with patch.object(rules_engine, "route", return_value=None):
        instance, _required = await quotes.submit_for_approval(
            session, quote=q, version=v, applicant_id=user.id, user_roles=roles,
            reason="外币档位检查",
        )
    await session.flush()
    check_true("生成了审批单（未被免审吞掉）", instance is not None)
    if instance is None:
        return
    summary = instance.summary or {}
    check("分档金额=人民币 70000", float(summary.get("total_amount", 0)), 70000.0)
    check("留痕里的原币金额", float(summary.get("original_amount", 0)), 10000.0)
    check("留痕里的币种", summary.get("currency"), "USD")
    check("留痕里的汇率", float(summary.get("exchange_rate", 0)), 7.0)
    check_true(
        f"档位不再落在第一档（上限 {first_cap}）",
        summary.get("node_label") != levels[0].get("label"),
        f"实际档位 {summary.get('node_label')}",
    )

    # 缺汇率的外币版本：不得静默按 1:1 折算，必须明确报错
    quote2, version2, item2, _sku2, _c2 = await make_quote(
        session, owner_id=owner_id, unit_cost=Decimal("10"), price=Decimal("100"),
        quantity=Decimal("1"), currency="USD", rate=None,
    )
    item2.approval_required = True
    await session.flush()
    await confirm_freight(session, version2)
    try:
        with patch.object(rules_engine, "route", return_value=None):
            await quotes.submit_for_approval(
                session, quote=await session.get(Quote, quote2.id),
                version=await session.get(QuoteVersion, version2.id),
                applicant_id=user.id, user_roles=roles, reason="缺汇率检查",
            )
        error = None
    except AppError as exc:
        error = exc
    check_true("缺汇率时明确拒绝（不猜）", error is not None, str(error))
    check("拒绝原因指向汇率", code_of(error), ErrorCode.REQUIRED_FIELD_MISSING)


async def probe_foreign_new_version(session, owner_id, roles, user):
    """⑪ 历史外币单新建版本：**新版落成人民币**，且不留外币快照。

    ## 这条为什么改了（2026-10-12 口径，CI 先红才发现的）

    原断言是「新版本币种**沿用 USD**、汇率快照=7、利润 USD 10 / 50%」——
    那套行为属于"系统支持外币"的年代。现在主人定稿：

        「汇率不要管，完全不用做，谁用外币直接拒绝」

    于是 `create_version` 不再继承源版本币种，**一律落成人民币**
    （否则历史外币单点"新建版本"会去找一条不存在的汇率，报
    「请先在价格中心维护汇率」——而那个界面根本没有，等于把唯一出路堵死；实测过）。

    ⚠️ **这条套件里原来的利润/币种断言全部作废是"对的"**，不是回归。
    真正需要守住的是下面这三条：
      ① 新版必须是人民币（不再沿用外币）；
      ② 不能留外币汇率快照（CNY 语义上不该有 fx）；
      ③ **历史原件一个字不动**（那份 USD 版本仍按 USD 看得到）。

    ⚠️ 另记一笔（**已知业务问题，不是本套件的断言**）：把历史外币明细搬到人民币版时，
    `quoted_price` 是"USD 20"这个**数字原样搬过来**、被当成 CNY 20 用 ——
    利润会从「USD 10」变成「CNY −50」。利润算法本身没错（`cost_in_quote` 在 CNY 下
    不折算），错在**数字没有换算**。见交接文档「未做完的事」。
    """
    print("=== 11. 历史外币单新建版本：落成人民币 ===")
    await ensure_usd_rate(session)
    quote, version, item, _sku, _cust = await make_quote(
        session, owner_id=owner_id, unit_cost=Decimal("70"), price=Decimal("20"),
        quantity=Decimal("1"), currency="USD", rate=Decimal("7"),
    )
    item.profit_snapshot = Decimal("0")
    item.profit_rate_snapshot = Decimal("0")
    await session.flush()
    q = await session.get(Quote, quote.id)
    src = await session.get(QuoteVersion, version.id)
    new_version = await quotes.create_version(session, quote=q, user=user,
                                              source_version_id=src.id)
    await session.flush()
    check("★新版本落成人民币（不再沿用源版本的外币）", new_version.currency, "CNY")
    check("★新版不留外币汇率快照（CNY 语义上没有 fx）",
          new_version.exchange_rate_snapshot, None)
    rows = await quotes.version_items(session, new_version.id)
    check_true("新版有明细（明细整份继承）", len(rows) > 0, f"{len(rows)} 条")
    # 历史原件不许被动过：那份 USD 版本仍按 USD 看得到
    check("★历史外币原件没被改动（仍是 USD + 快照 7）", src.currency, "USD")
    check("历史原件的汇率快照还在", float(src.exchange_rate_snapshot or 0), 7.0)


async def ensure_usd_rate(session):
    exists = (await session.execute(
        select(ExchangeRate.id).where(
            ExchangeRate.base_currency == "CNY",
            ExchangeRate.quote_currency == "USD",
        )
    )).scalars().first()
    if exists:
        return
    session.add(ExchangeRate(
        base_currency="CNY", quote_currency="USD", rate=Decimal("7"),
        source="检查用", effective_at=datetime.now(UTC),
    ))
    await session.flush()


async def probe_idempotency(session, owner_id, roles, user):
    """⑥ 同键换订单必须冲突，且不得回放别张订单的结果。"""
    print("=== 6. 幂等回放不跨订单 ===")
    from app.core import idempotency

    first = await make_order(session, owner_id=owner_id)
    second = await make_order(session, owner_id=owner_id)
    plans = [
        ReceivablePlan(order_id=first.id, plan_name="定金 30%", due_date=date(2026, 11, 1),
                       amount=Decimal("30"), currency="CNY", status="pending",
                       created_at=datetime.now(UTC)),
        ReceivablePlan(order_id=first.id, plan_name="尾款 70%", due_date=date(2026, 12, 1),
                       amount=Decimal("70"), currency="CNY", status="pending",
                       created_at=datetime.now(UTC)),
    ]
    session.add_all(plans)
    await session.flush()

    def _fp(payload: ReceivableGenerate, order_id: int) -> dict:
        """**与端点完全同源**地算一次指纹输入（少一个字段就会误判成"同键不同内容"）。"""
        data = payload.model_dump()
        data.pop("request_key", None)
        return {**data, "order_id": order_id}

    # A) 同一把键，先给订单 1 办完；再拿它提交订单 2
    key_a = f"{PREFIX}-KA"
    payload_a = ReceivableGenerate(
        ratios=[0.3, 0.7], first_due_date=date(2026, 11, 1),
        second_due_date=date(2026, 12, 1), request_key=key_a,
    )
    reservation_a = await idempotency.reserve(
        session, user_id=owner_id, action="receivable:generate", request_key=key_a,
        payload=_fp(payload_a, first.id), result_type="receivable_plan",
    )
    await idempotency.complete(session, reservation_a, result_payload={"ok": True},
                               result_id=plans[0].id)
    await session.flush()
    try:
        await payment_router.generate_receivables(second.id, payload_a, REQUEST, user, session)
        error_a = None
    except AppError as exc:
        error_a = exc
    check_true("同键换订单被明确拒绝", error_a is not None, str(error_a))
    check("冲突状态码 409", getattr(error_a, "http_status", None), 409)
    check_true(
        "冲突文案指向「同键不同内容」（而不是静默回放）",
        "不一致" in (getattr(error_a, "message", "") or ""),
        (getattr(error_a, "message", "") or "")[:60],
    )

    # B) 指纹"完全对得上"，但结果指向**另一张订单**的计划 → 必须拒绝回放
    key_b = f"{PREFIX}-KB"
    payload_b = ReceivableGenerate(
        ratios=[0.3, 0.7], first_due_date=date(2026, 11, 1),
        second_due_date=date(2026, 12, 1), request_key=key_b,
    )
    reservation_b = await idempotency.reserve(
        session, user_id=owner_id, action="receivable:generate", request_key=key_b,
        payload=_fp(payload_b, second.id), result_type="receivable_plan",
    )
    await idempotency.complete(session, reservation_b, result_payload={"ok": True},
                               result_id=plans[0].id)  # ← 故意指向订单 1 的计划
    await session.flush()
    try:
        await payment_router.generate_receivables(second.id, payload_b, REQUEST, user, session)
        error_b = None
    except AppError as exc:
        error_b = exc
    check_true("结果不属于本订单时不回放（抛冲突）", error_b is not None, str(error_b))
    check("冲突状态码 409（B）", getattr(error_b, "http_status", None), 409)
    check_true(
        "文案指向「结果不属于本订单」",
        "不属于本订单" in (getattr(error_b, "message", "") or ""),
        (getattr(error_b, "message", "") or "")[:60],
    )

    count = len((await session.execute(
        select(ReceivablePlan.id).where(ReceivablePlan.order_id == second.id)
    )).scalars().all())
    check("订单 2 不会凭空生成应收", count, 0)

    # C) 反例形态：库里存着的指纹是**旧格式**（不含 order_id）。
    # 现实现下它同样必须被拒（指纹对不上）；退回旧实现时它会命中回放，
    # 于是"订单 2 什么都没生成却提示成功"——这条断言就是照妖镜。
    key_c = f"{PREFIX}-KC"
    payload_c = ReceivableGenerate(
        ratios=[0.3, 0.7], first_due_date=date(2026, 11, 1),
        second_due_date=date(2026, 12, 1), request_key=key_c,
    )
    legacy_payload = dict(_fp(payload_c, second.id))
    legacy_payload.pop("order_id")  # ← 旧实现只算 body
    reservation_c = await idempotency.reserve(
        session, user_id=owner_id, action="receivable:generate", request_key=key_c,
        payload=legacy_payload, result_type="receivable_plan",
    )
    await idempotency.complete(session, reservation_c, result_payload={"ok": True},
                               result_id=plans[0].id)
    await session.flush()
    try:
        await payment_router.generate_receivables(second.id, payload_c, REQUEST, user, session)
        error_c = None
    except AppError as exc:
        error_c = exc
    check_true("旧格式指纹的记录也被拒（不会回放成「成功」）", error_c is not None, str(error_c))
    check("冲突状态码 409（C）", getattr(error_c, "http_status", None), 409)


async def probe_confirm_contract(session, owner_id, roles, user):
    """⑦ 三个推单入口统一 confirm 契约。"""
    print("=== 7. 推单入口统一确认契约 ===")
    order = await make_order(session, owner_id=owner_id)

    async def _call(coro):
        try:
            await coro
            return None
        except AppError as exc:
            return exc

    err_page = await _call(order_router.sync_erp(order.id, REQUEST, False, user, session))
    err_no = await _call(erp_router.push_by_order_no(
        OrderPushRequest(order_no=order.order_no), False, user, session))
    err_id = await _call(erp_router.sync_order(order.id, False, user, session))
    check("订单页入口要求确认（42206）", code_of(err_page), ErrorCode.CONFIRM_REQUIRED)
    check("按单号入口要求确认（42206）", code_of(err_no), ErrorCode.CONFIRM_REQUIRED)
    check("按 id 入口要求确认（42206）", code_of(err_id), ErrorCode.CONFIRM_REQUIRED)
    messages = {getattr(e, "message", None) for e in (err_page, err_no, err_id)}
    check("三处文案完全一致", len(messages), 1)
    check_true("文案讲清了后果",
               "撤回不了" in (getattr(err_no, "message", "") or ""),
               (getattr(err_no, "message", "") or "")[:120])


# ------------------------------------------------- 需要自行提交的并发/状态探针


async def probe_concurrent_charge(owner_id, roles, user):
    """① 并发改费用：锁内重读，合计不被旧值污染。"""
    print("=== 1. 并发修改费用后按锁内真值重算 ===")
    async with SessionLocal() as s:
        quote, version, item, _sku, _cust = await make_quote(
            s, owner_id=owner_id, unit_cost=Decimal("10"), price=Decimal("100"),
            quantity=Decimal("10"),
        )
        charge = QuoteCharge(
            quote_version_id=version.id, charge_type="other", description="原费用",
            amount=Decimal("100"), is_discount=False, sort_no=1,
        )
        s.add(charge)
        await s.flush()
        await quotes.recalc_version(s, version)
        await s.flush()
        check("初始合计（货款 1000 + 费用 100）", float(version.total_amount), 1100.0)
        charge_id, version_id = charge.id, version.id
        await s.commit()

    # A：同一把连接把这条费用改成 −100 的优惠并提交（与界面上"改成优惠"等价）
    async def _make_discount(s):
        # 传正数 + 标记为优惠：端点会把它归一成 −100
        await update_charge(
            charge_id,
            QuoteChargeUpdate(amount=Decimal("100"), is_discount=True),
            REQUEST, user, s,
        )

    # ---- 形态一：B 先读到旧值（锁外那次读），A 随后改完提交，B 才继续 ----
    # 会话 B 保持打开：它手里那条费用是**最早的 +100**（对象是干净的，
    # 所以不会被自动 flush 写回去），这正是"旧对象仍在路上"的真实形态。
    async with SessionLocal() as b:
        stale = await b.get(QuoteCharge, charge_id)
        check("B 锁外读到的还是旧值 +100", float(stale.amount), 100.0)
        check("B 看到的还不是优惠", bool(stale.is_discount), False)

        async with SessionLocal() as a:
            await _make_discount(a)
        async with SessionLocal() as probe:
            row = await probe.get(QuoteCharge, charge_id)
            check("A 提交后库里金额", float(row.amount), -100.0)
            check("A 提交后库里是优惠", bool(row.is_discount), True)

        # B 现在才继续（只改说明），必须按锁内真值重算
        await update_charge(
            charge_id, QuoteChargeUpdate(description="只改说明"), REQUEST, user, b
        )
    async with SessionLocal() as probe:
        row = await probe.get(QuoteVersion, version_id)
        check("只改说明后合计（货款 1000 − 优惠 100）", float(row.total_amount), 900.0)
        check("其他费用列未被旧值污染", float(row.other_charge_amount), 0.0)
        check("折扣列", float(row.discount_amount), -100.0)

    # 交错形态：A 持版本行锁未提交，B 读到锁外旧对象并阻塞在锁上，A 提交后 B 才继续
    ready, release = asyncio.Event(), asyncio.Event()

    async def _holder():
        async with SessionLocal() as s:
            locked = (await s.execute(
                select(QuoteVersion).where(QuoteVersion.id == version_id)
                .with_for_update()
            )).scalars().first()
            locked.discount_amount = Decimal("-100")
            locked.total_amount = Decimal("900")
            charge_row = await s.get(QuoteCharge, charge_id)
            charge_row.amount = Decimal("-100")
            charge_row.is_discount = True
            await s.flush()
            ready.set()
            await release.wait()
            await s.commit()

    async def _waiter():
        async with SessionLocal() as s:
            await ready.wait()
            await update_charge(
                charge_id, QuoteChargeUpdate(description="并发只改说明"), REQUEST, user, s
            )

    holder = asyncio.create_task(_holder())
    waiter = asyncio.create_task(_waiter())
    await ready.wait()
    await asyncio.sleep(0.6)  # 让 waiter 读到锁外的旧对象并阻塞在行锁上
    release.set()
    await asyncio.gather(holder, waiter)
    async with SessionLocal() as probe:
        row = await probe.get(QuoteVersion, version_id)
        check("交错场景最终合计", float(row.total_amount), 900.0)
        check("交错场景折扣列", float(row.discount_amount), -100.0)


async def probe_status_writeback(owner_id, roles, user):
    """④ 外部结果到达前订单被取消 → 不得覆盖取消。"""
    print("=== 4. 拉取等待期间订单被取消：不得写成已发货 ===")
    async with SessionLocal() as s:
        order = await make_order(s, owner_id=owner_id)
        ext_id = f"EXT-{uuid4().hex[:10]}"
        order.erp_order_id = ext_id
        s.add(ExternalMapping(
            system_type="ERP", business_type="order", internal_id=order.id,
            external_id=ext_id,
        ))
        await s.flush()
        order_id = order.id
        await s.commit()

    class _Adapter:
        label = "替身ERP"
        system_type = "ERP"

        async def fetch_order_status(self, external_id):
            # 外部响应"在路上"的这段时间里，另一个请求把订单取消了
            async with SessionLocal() as other:
                await order_router.cancel_order(order_id, REQUEST, user, other)
            return {"status": "shipped", "raw_status": "Shipped", "shipped_at": None}

    async with SessionLocal() as s:
        target = await s.get(SalesOrder, order_id)
        with patch("app.modules.erp.service.get_adapter", return_value=_Adapter()):
            result = await erp_service.refresh_status(s, order=target, operator_id=owner_id)
        await s.commit()
    async with SessionLocal() as probe:
        fresh = await probe.get(SalesOrder, order_id)
        check("订单状态仍是已取消", fresh.status, "cancelled")
        check("本次没有改状态", result.get("changed"), False)
        check_true("给出了被拦原因", bool(result.get("blocked_reason")),
                   str(result.get("blocked_reason"))[:80])


async def probe_cancelled_push(owner_id, roles, user):
    """⑤ 已取消订单不得再推外单。"""
    print("=== 5. 已取消订单不能再推外单 ===")
    async with SessionLocal() as s:
        order = await make_order(s, owner_id=owner_id, status="cancelled")
        order_id = order.id
        await s.commit()
    calls: list[str] = []

    class _Adapter:
        label = "替身ERP"
        system_type = "ERP"

        async def push_order(self, payload):
            calls.append("pushed")
            # 返回体要合规（带对方自己的外部单号）：这样"旧写法下确实推成功了"
            # 才是干净可见的，而不是崩在响应校验上；单号带随机后缀，避免跨轮撞唯一约束
            new_id = f"EXT-{uuid4().hex[:10]}"
            return {"external_id": new_id, "external_code": new_id, "raw": {}}

    async with SessionLocal() as s:
        target = await s.get(SalesOrder, order_id)
        with patch("app.modules.erp.service.get_adapter", return_value=_Adapter()):
            try:
                await erp_service.push_order(s, order=target, operator_id=owner_id)
                error = None
            except AppError as exc:
                error = exc
        check_true("抛出了业务错误", error is not None, str(error))
        check("错误码=状态不允许（400）", code_of(error), ErrorCode.STATUS_NOT_ALLOWED)
        check("没有真的调外部推单", calls, [])
        await s.rollback()


async def cleanup():
    """按前缀清掉本轮夹具（客户/订单/报价/文件/用户）。"""
    async with SessionLocal() as s:
        for sql in (
            "delete from biz_docs where customer_id in (select id from customers where name like :p)",
            "delete from request_keys where request_key like :p",
            "delete from receivable_plans where order_id in (select id from sales_orders where order_no like :p)",
            "delete from order_status_history where order_id in (select id from sales_orders where order_no like :p)",
            "delete from external_mappings where internal_id in (select id from sales_orders where order_no like :p)",
            "delete from external_mappings where external_id like 'EXT-%'",
            "delete from integration_logs where business_id in (select id from sales_orders where order_no like :p)",
            "delete from sales_order_items where order_id in (select id from sales_orders where order_no like :p)",
            "delete from sales_orders where order_no like :p",
            "delete from quote_charges where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where quote_no like :p))",
            "delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where quote_no like :p))",
            "delete from quote_versions where quote_id in (select id from quotes where quote_no like :p)",
            "delete from quotes where quote_no like :p",
            "delete from sku_field_authorities where sku_id in (select id from skus where sku_code like :p)",
            "delete from sku_master_versions where sku_id in (select id from skus where sku_code like :p)",
            "delete from skus where sku_code like :p",
            "delete from products where name like :p",
            "delete from customers where name like :p",
            "delete from users where lower(username) like lower(:p)",
        ):
            try:
                await s.execute(text(sql), {"p": f"{PREFIX}%"})
                await s.commit()
            except Exception as error:  # noqa: BLE001 —— 清理失败不该顶掉结论
                print(f"  （清理告警：{type(error).__name__}: {error}）")
                await s.rollback()
    await engine.dispose()


async def main():
    async with SessionLocal() as s:
        admin = (await s.execute(select(User).where(User.username == "admin"))).scalar_one()
        owner_id, roles = admin.id, ["admin"]
        user = viewer(admin, roles)
        await ensure_usd_rate(s)
        await s.commit()
    print(f"夹具前缀：{PREFIX}")

    await probe_formatting()

    async with SessionLocal() as s:
        try:
            await probe_margin_none(s, owner_id, roles, user)
            await probe_excel_gates(s, owner_id, roles, user)
            await probe_foreign_tier(s, owner_id, roles, user)
            await probe_foreign_new_version(s, owner_id, roles, user)
            await probe_idempotency(s, owner_id, roles, user)
            await probe_confirm_contract(s, owner_id, roles, user)
        finally:
            await s.rollback()

    try:
        await probe_concurrent_charge(owner_id, roles, user)
        await probe_status_writeback(owner_id, roles, user)
        await probe_cancelled_push(owner_id, roles, user)
    finally:
        # 无论成败都清夹具：残渣会让**下一轮**在唯一约束上炸（实测过一次）
        await cleanup()

    print()
    if FAILURES:
        print(f"发现 {len(FAILURES)} 项不符合预期：")
        for name in FAILURES:
            print(f"  - {name}")
    else:
        print("全部探针通过。")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
