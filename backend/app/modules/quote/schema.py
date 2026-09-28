from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class QuoteCreate(BaseModel):
    opportunity_id: int | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    valid_until: date | None = None
    payment_terms: str | None = None
    delivery_terms: str | None = None
    remark: str | None = None
    # 外贸口径（可选）：不填就是内贸，全人民币
    currency: str = "CNY"
    exchange_rate: Decimal | None = None
    """不填则自动取汇率表里该币种的当前汇率并落快照（02-ER §11）。"""


class QuoteVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    payment_terms: str | None = None
    delivery_terms: str | None = None
    remark: str | None = None
    valid_until: date | None = None


class QuoteItemInput(BaseModel):
    """报价明细入参。两条路径（文档场景09）：

    - **现货**：给 `sku_id`，`quoted_price` 留空则用系统适用价/核价建议价；
    - **定制**：尚无正式 SKU 时给 `inquiry_id` + 人工核价的 `unit_cost` 与
      `quoted_price`。定制项必须给成本——不给成本就只能按 0 算，
      会得出 100% 毛利、低价审批也不会触发（与 A06「无成本不造假」同口径）。
    """

    sku_id: int | None = None
    #: 定制需求 id（与 sku_id 至少给一个）
    inquiry_id: int | None = None
    #: 定制项展示名，落快照；不填用需求标题
    item_name: str | None = None
    #: 定制项人工核价成本（元/件，不含运费）
    unit_cost: Decimal | None = None
    quantity: Decimal = Decimal(1)
    quoted_price: Decimal | None = None
    opportunity_item_id: int | None = None
    spec_snapshot: str | None = None
    logistics_cost: Decimal | None = None
    remark: str | None = None


class QuoteItemUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quantity: Decimal | None = None
    quoted_price: Decimal | None = None
    logistics_cost: Decimal | None = None
    remark: str | None = None


class QuoteChargeInput(BaseModel):
    charge_type: str = "other"
    description: str | None = None
    amount: Decimal = Decimal(0)
    is_discount: bool = False


class QuoteChargeUpdate(BaseModel):
    """改一条附加费用（03-API §22）。只传要改的字段。"""

    model_config = ConfigDict(extra="ignore")

    charge_type: str | None = None
    description: str | None = None
    amount: Decimal | None = None
    is_discount: bool | None = None
    sort_no: int | None = None


class ApprovalAction(BaseModel):
    comment: str | None = None


class QuoteUpdate(BaseModel):
    """改报价单本身（03-API §20）。

    注意：**不能改金额、明细与备注** —— 那些都属于版本
    （`Quote` 表本身只有 contact_id / owner_id / valid_until 这几个可变字段，
    备注在 `QuoteVersion.remark` 上）。改版本的备注要走
    `PATCH /quote-versions/{id}`，否则会出现"单据改了但版本快照没变"。
    """

    model_config = ConfigDict(extra="ignore")

    contact_id: int | None = None
    owner_id: int | None = None
    valid_until: date | None = None


class QuoteClone(BaseModel):
    """复制报价单（03-API §20）。

    典型场景：同款产品给另一家客户报价、或客户要求"照上次再来一单"。
    复制出的报价是**草稿**，版本内容照抄但状态全部重置 ——
    审批通过/已发送是上一单的结论，不能继承。
    """

    model_config = ConfigDict(extra="ignore")

    customer_id: int | None = None
    opportunity_id: int | None = None
    contact_id: int | None = None
    owner_id: int | None = None
    valid_until: date | None = None
    copy_items: bool = True
    remark: str | None = None


class QuoteExpire(BaseModel):
    """把报价标记为已失效（03-API §21）。

    状态机里一直有 `expired` 但**没有任何地方会写它** ——
    过了有效期没人处理，看板上永远停在"已发送"。这里补上入口。
    """

    model_config = ConfigDict(extra="ignore")

    reason: str | None = None


class SendRequest(BaseModel):
    channel: str = "邮件"
    receiver: str | None = None


class SubmitApprovalRequest(BaseModel):
    reason: str | None = None


class DeclinedRequest(BaseModel):
    reason: str | None = None


__all__ = ["datetime", "Field"]
