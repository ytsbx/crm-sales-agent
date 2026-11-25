"""销售退货接口的请求模型。"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RETURN_REASON_CODES = Literal[
    "customer_rejected", "quality_issue", "wrong_item", "other"
]


class ReturnItemInput(BaseModel):
    shipment_batch_item_id: int
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)


class ReturnCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason_code: RETURN_REASON_CODES = "other"
    #: 至少 2 个字：退货是要进客户时间线和审计的动作，不能只写一个“退”。
    reason: str = Field(min_length=2, max_length=255)
    return_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[ReturnItemInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicate_batch_item(self):
        ids = [item.shipment_batch_item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一发货明细只能填一行，请合并数量")
        return self


class ReturnSettle(BaseModel):
    #: 财务**人工填写**的最终退货货款；系统不再按单价自动折算。
    settled_goods_amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    #: 运费默认不退；确需退时财务在这里手工加。
    freight_refund_amount: Decimal = Field(
        default=Decimal("0"), ge=0, max_digits=16, decimal_places=2
    )
    settlement_note: str | None = Field(default=None, max_length=255)


class ReturnReview(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _reject_needs_reason(self):
        if self.decision == "reject" and len((self.note or "").strip()) < 2:
            raise ValueError("驳回必须填写原因（至少 2 个字）")
        return self


class ReturnRefund(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    #: 退款流水/凭证说明，至少 2 个字，方便以后查账。
    refund_reference: str = Field(min_length=2, max_length=128)


class ReturnCancel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=2, max_length=255)


class ReturnVoid(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=2, max_length=255)
