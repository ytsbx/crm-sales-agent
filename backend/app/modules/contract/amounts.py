"""合同金额冻结：生成时定一次，OA、签署审计、页面口径共用。

为什么需要这个模块：合同正文是生成时的快照，但 OA 审批金额和签署审计
过去都各自去数据库查"当前"的订单/报价，而且顺序相反（OA 先订单、审计先
报价版本）。订单后续一改，同一份合同就会出现"纸上 10 万、OA 批 11 万、
审计记 10 万"三份证据对不上。这里把生成时的金额写进 `filled_data` 的内部键，
后面所有消费者只读这份冻结值；老合同没有该键时才回退实时读取。
"""
from decimal import Decimal, InvalidOperation
from typing import Any

AMOUNT_KEY = "_amount"
CURRENCY_KEY = "_currency"
SOURCE_KEY = "_amount_source"


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Decimal(text)


def resolve_generation_amount(order, quote_version) -> tuple[Decimal | None, str]:
    """生成合同时定金额：**所选报价版本优先，订单兜底**。

    合同模板里的 `{{quote.total_amount}}` 取的就是选定版本，纸面认哪一版，
    OA 和审计就必须认同一版。订单与报价版本在生成时已被强制要求同版
    （`contract/service.py` 的 C4-02 校验），所以这里先取版本不会把
    订单另选一版带进来；只有没选版本时才回退订单总额。
    """
    if quote_version is not None:
        amount = _as_decimal(getattr(quote_version, "total_amount", None))
        if amount is not None:
            version_id = getattr(quote_version, "id", None)
            source = f"quote_version:{version_id}" if version_id is not None else "quote_version"
            return amount, source
    if order is not None:
        amount = _as_decimal(getattr(order, "total_amount", None))
        if amount is not None:
            return amount, "order"
    return None, "none"


def frozen_amount(filled_data: dict | None) -> tuple[Decimal | None, bool]:
    """读冻结金额，返回 (金额, 是否有冻结键)。

    第二个返回值必须区分"没有键"和"键存在但值为空"：
    - 没有键 = 老合同，允许回退实时读取；
    - 键存在且为 None = 生成时就是无金额合同，后续订单补了金额也不能变。
    """
    filled = filled_data or {}
    if AMOUNT_KEY not in filled:
        return None, False
    raw = filled.get(AMOUNT_KEY)
    if raw is None or str(raw).strip() == "":
        return None, True
    try:
        return Decimal(str(raw)), True
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("合同冻结金额格式不正确，请重新生成合同") from exc


async def contract_amount(session, doc) -> Decimal | None:
    """所有下游统一入口：先读冻结值，老合同才回退实时。

    回退顺序也统一为**报价版本优先、订单兜底**，与合同纸面一致；不再让
    OA 和审计各写一套相反的顺序。
    """
    amount, frozen = frozen_amount(getattr(doc, "filled_data", None))
    if frozen:
        return amount

    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import QuoteVersion

    if getattr(doc, "quote_version_id", None):
        version = await session.get(QuoteVersion, doc.quote_version_id)
        amount = _as_decimal(getattr(version, "total_amount", None)) if version is not None else None
        if amount is not None:
            return amount
    if getattr(doc, "order_id", None):
        order = await session.get(SalesOrder, doc.order_id)
        amount = _as_decimal(getattr(order, "total_amount", None)) if order is not None else None
        if amount is not None:
            return amount
    return None
