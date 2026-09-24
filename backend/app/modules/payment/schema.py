"""应收与回款入参。"""

from datetime import date

from pydantic import BaseModel


class ReceivableCreate(BaseModel):
    plan_name: str
    due_date: date
    amount: float
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
    received_amount: float
    payment_method: str | None = None
    voucher_note: str | None = None


class PaymentAction(BaseModel):
    comment: str | None = None
