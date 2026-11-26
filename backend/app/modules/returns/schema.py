"""销售退货接口的请求模型。"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RETURN_REASON_CODES = Literal[
    "customer_rejected", "quality_issue", "wrong_item", "other"
]


class ReturnItemInput(BaseModel):
    #: 退货按订单明细（产品/规格）登记，不再要求选发货批次。
    order_item_id: int
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)


class ReturnCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason_code: RETURN_REASON_CODES = "other"
    #: 至少 2 个字：退货是要进客户时间线和审计的动作，不能只写一个“退”。
    reason: str = Field(min_length=2, max_length=255)
    #: 2026-11-26 主人拍板：登记时就填退款金额（含货款和运费），不再等主管通过后另填。
    refund_amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    return_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[ReturnItemInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicate_order_item(self):
        ids = [item.order_item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一订单明细只能填一行，请合并数量")
        return self


class ReturnAmountUpdate(BaseModel):
    """财务核对/修改退款金额。

    2026-11-26 主人拍板：退款金额在登记时由业务员填写，财务后续可以改；
    每次修改都必须写审计，保留修改前后金额、操作人和说明。
    """

    refund_amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    note: str | None = Field(default=None, max_length=255)


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
