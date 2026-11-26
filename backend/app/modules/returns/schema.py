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
    #: 至少 2 个字：售后申请要进客户时间线和审计，不能只写一个“退”。
    reason: str = Field(min_length=2, max_length=255)
    #: 业务员填的退款金额构成。总金额由系统合计，不再只填一个说不清来源的数。
    goods_refund_amount: Decimal | None = Field(
        default=None, ge=0, max_digits=16, decimal_places=2
    )
    freight_refund_amount: Decimal = Field(
        default=0, ge=0, max_digits=16, decimal_places=2
    )
    #: 改价/其他调整，可正可负；合计后总额必须大于 0。
    adjustment_amount: Decimal = Field(
        default=0, max_digits=16, decimal_places=2
    )
    adjustment_note: str | None = Field(default=None, max_length=255)
    #: 兼容旧调用：老接口只传一个 refund_amount 时，把它当作总额。
    refund_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=16, decimal_places=2
    )
    #: 打款方式；财务按这个渠道退款，不在系统里另选抵扣。
    payment_method: str = Field(default="线下转账", min_length=2, max_length=32)
    #: 收款账户：户名/开户行/账号，财务按它打款。
    receiving_account: str = Field(min_length=2, max_length=128)
    #: 申请单必须附退款登记表，文件先走 /files/upload 拿 file_id。
    application_attachment_file_id: int = Field(gt=0)
    #: 客户退回货物的物流单号，确认收货时核对用，选填。
    return_tracking_no: str | None = Field(default=None, max_length=64)
    return_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[ReturnItemInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicate_order_item(self):
        ids = [item.order_item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一订单明细只能填一行，请合并数量")
        return self

    @model_validator(mode="after")
    def _amount_breakdown(self):
        # 老调用只传 refund_amount：把它当总额，货款 = 总额 - 运费 - 调整。
        if self.goods_refund_amount is None:
            if self.refund_amount is None:
                raise ValueError("请填写货款退款金额")
            goods = self.refund_amount - self.freight_refund_amount - self.adjustment_amount
            if goods < 0:
                raise ValueError("货款退款金额不能小于 0，请检查运费/调整金额")
            self.goods_refund_amount = goods
        total = (
            self.goods_refund_amount
            + self.freight_refund_amount
            + self.adjustment_amount
        )
        if total <= 0:
            raise ValueError("退款总额必须大于 0")
        # 兼容字段：统一写成系统合计后的总额，后面 service 只认这个数。
        self.refund_amount = total
        return self


class ReturnAmountUpdate(BaseModel):
    """历史旧单补/改退款金额。

    2026-11-26 新流程：业务员登记时填构成，主管审核；财务不改金额。
    这个接口只保留给历史 `pending` 旧单补金额，不再给新单使用。
    """

    refund_amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    note: str | None = Field(default=None, max_length=255)


class ReturnSettle(BaseModel):
    """财务确认退款：金额由系统按订单欠款自动拆分，财务只登记退款结果。

    - 系统先冲客户未付款，剩余才现金退；
    - 财务不填抵扣金额、不改退款总额；
    - 有现金退还时必须填退款流水/凭证说明。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    refund_reference: str | None = Field(default=None, min_length=2, max_length=128)
    note: str | None = Field(default=None, max_length=255)


class ReturnReceiveConfirm(BaseModel):
    """业务员或主管确认收到退货。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=255)
    tracking_no: str | None = Field(default=None, max_length=64)


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
