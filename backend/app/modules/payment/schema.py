"""应收与回款入参。"""

from datetime import date

from pydantic import BaseModel, Field


class ReceivableCreate(BaseModel):
    """建应收节点。

    `order_id` 可选：`POST /orders/{id}/receivables` 从路径取，
    `POST /receivables` 从 body 取。两个入口共用这一份校验。
    """

    order_id: int | None = None
    plan_name: str = Field(min_length=1, max_length=64)
    due_date: date
    amount: float = Field(gt=0)
    remark: str | None = None


class ReceivableUpdate(BaseModel):
    """改应收节点：只改传进来的字段。"""

    plan_name: str | None = Field(default=None, min_length=1, max_length=64)
    due_date: date | None = None
    amount: float | None = Field(default=None, gt=0)
    remark: str | None = None


class ReceivableGenerate(BaseModel):
    """按比例生成应收计划，例如 30% 定金 + 70% 尾款。"""

    ratios: list[float]
    first_due_date: date
    second_due_date: date | None = None
    first_name: str = "定金"
    second_name: str = "尾款"


class PaymentCreate(BaseModel):
    receivable_plan_id: int | None = None
    received_date: date
    received_amount: float = Field(gt=0)
    payment_method: str | None = None
    voucher_note: str | None = None


class PaymentUpdate(BaseModel):
    """改回款登记：金额/日期/方式/凭证说明。

    改金额必须重算应收节点状态，否则"收齐了"还显示部分回款。
    已确认/已驳回的回款不允许改（财务结论不能事后改数）。
    """

    received_date: date | None = None
    received_amount: float | None = Field(default=None, gt=0)
    payment_method: str | None = None
    voucher_note: str | None = None


class PaymentAction(BaseModel):
    comment: str | None = None
