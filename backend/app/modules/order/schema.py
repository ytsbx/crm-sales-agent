from datetime import date
from typing import Literal
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class OrderFromQuote(BaseModel):
    delivery_date: date | None = None
    remark: str | None = None


class MilestoneUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skipped: bool | None = None
    skip_reason: str | None = Field(default=None, max_length=2000)
    planned_date: date | None = None
    actual_date: date | None = None
    # 方案 :103 要求节点记录责任人、来源证据、逾期原因——
    # 「计划日/实际日」只是进度，「谁负责/凭什么/为什么晚」才是能追责、能复盘的部分
    owner_id: int | None = None
    evidence: str | None = None
    overdue_reason: str | None = None
    remark: str | None = None


class ScheduleChangeCreate(BaseModel):
    """交期变更（方案 :105）：先预览受影响面，责任人确认后才生效。"""

    new_delivery_date: date
    delivery_kind: Literal['shipping', 'arrival'] | None = None
    transit_days: int | None = Field(default=None, ge=0, le=365, strict=True)
    plan_offsets: dict[str, StrictInt] | None = None
    reason: str | None = None


class ScheduleChangeConfirm(BaseModel):
    remark: str | None = None


class ScheduleChangeCancel(BaseModel):
    """作废待确认的交期变更单（不存在的出口比没有约束更糟：约束会堵死业务）。"""

    reason: str | None = None


class OrderItemInput(BaseModel):
    sku_id: int
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    specification: str | None = None
    remark: str | None = None


class OrderCreate(BaseModel):
    """手工建订单（03-API §27）。

    PRD §20 说明订单正常来自"成交"环节（报价版本转订单），
    但线下签约、补录历史单等场景需要一个直接建单的入口。
    金额不由前端传 —— 由明细算出，避免两者不一致。
    """

    customer_id: int
    items: list[OrderItemInput] = Field(min_length=1)
    opportunity_id: int | None = None
    quote_id: int | None = None
    owner_id: int | None = None
    currency: str = "CNY"
    delivery_date: date | None = None
    payment_terms: str | None = None
    remark: str | None = None


class OrderUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    delivery_date: date | None = None
    remark: str | None = None
    owner_id: int | None = None
    payment_terms: str | None = None


class OrderStatusChange(BaseModel):
    status: str
    remark: str | None = None


# ---- 发货批次（文档 §3.5 / 场景13：分批发货，首批不结束整单）----

class ShipmentBatchItemInput(BaseModel):
    order_item_id: int
    planned_qty: Decimal = Field(gt=0)


class ShipmentBatchCreate(BaseModel):
    planned_date: date | None = None
    overdue_reason: str | None = None
    remark: str | None = None
    items: list[ShipmentBatchItemInput] = Field(min_length=1)


class ShipmentShipItem(BaseModel):
    order_item_id: int
    shipped_qty: Decimal = Field(ge=0)


class ShipmentBatchShip(BaseModel):
    """登记实发。items 缺省 = 本批计划量全发；给出时逐明细覆盖。"""

    actual_ship_date: date | None = None
    logistics_company: str | None = None
    tracking_no: str | None = None
    #: 晚了就填原因（"因为分批/因为生产/因为客户改期"）——
    #: 系统能算出"晚几天"，算不出"为什么"，归因必须有人填
    overdue_reason: str | None = None
    remark: str | None = None
    items: list[ShipmentShipItem] | None = None


from uuid import UUID
from pydantic import model_validator
from app.modules.sample.schema import SampleSource


class OrderDraftLine(BaseModel):
    source_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=4)
    specification: str | None = None
    remark: str | None = None


class OrderDraftCreate(SampleSource):
    request_key: UUID
    items: list[OrderDraftLine] = Field(min_length=1, max_length=200)
    delivery_date: date | None = None
    payment_terms: str | None = None
    remark: str | None = None

    @model_validator(mode='after')
    def unique_sources(self):
        if len({i.source_item_id for i in self.items}) != len(self.items):
            raise ValueError('同一来源明细不能重复勾选')
        return self


class OrderDraftUpdate(BaseModel):
    revision: int = Field(gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    items: list[OrderDraftLine] = Field(min_length=1, max_length=200)
    delivery_date: date | None = None
    payment_terms: str | None = None
    remark: str | None = None


class OrderDraftConfirm(BaseModel):
    revision: int = Field(gt=0)
    quote_version_id: int = Field(gt=0)
