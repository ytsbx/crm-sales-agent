from datetime import date
from typing import Literal
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.core.patch_schema import CreateModel, PatchModel
from app.modules.payment.schema import ReceivableGenerate
from app.modules.sample.schema import SampleSource


class ReceivablePlanIn(BaseModel):
    """转单时一起定下的收款计划（2026-10-13 加）。

    **不传 = 一条「全款」**（沿用转单自动建全款的老行为，向后兼容）。
    传了就是按比例分期：`ratios` 合计必须为 1、每期 > 0、最多 24 期 ——
    与 `POST /orders/{id}/receivables/generate` **同一套判据**，
    所以校验参数（`ReceivableGenerate`）直接复用它，避免两处各写一遍漂移。

    `due_dates` / `names` 与 `ratios` **按下标对齐**；缺的位置分别回落到
    订单交期、以及「第 N 期」。后端不需要知道"定金/尾款"这类业务命名。
    """

    ratios: list[float]
    due_dates: list[date] | None = None
    #: 每期名称。**上限与库列 `receivable_plans.plan_name` 一致（64）**：
    #: 少了它，65 字会一路走到 INSERT 才撞 `value too long for character varying(64)`
    #: → 500（第九批问题 ⑦）。生成接口那边同样有 64 的限制，两处口径一致。
    names: list[str] | None = None

    @model_validator(mode="after")
    def _check(self):
        # 复用生成接口的**字段级**校验（NaN/Infinity），再自己补两条：
        # ⚠️ `ReceivableGenerate` 的校验器只管"是不是有限数"，
        # **"合计必须=1、每期>0、≤24 期"是在它的 service 里做的**（实测踩到：
        # 只构造模型时 `[0.9, 0.2]` 能过）。转单要在**参数阶段**就拒掉，
        # 否则等于先建了订单壳、再在生成应收时才炸。
        ReceivableGenerate(ratios=self.ratios, first_due_date=date.today())
        if not self.ratios:
            raise ValueError("至少要有一期比例")
        if abs(sum(self.ratios) - 1) > 0.0001:
            raise ValueError(f"各期比例之和必须等于 1（当前 {sum(self.ratios)}）")
        if any(r <= 0 for r in self.ratios):
            raise ValueError("每期比例必须大于 0")
        if len(self.ratios) > 24:
            raise ValueError(f"分期期数过多（{len(self.ratios)} 期，最多 24 期）")
        if self.due_dates is not None and len(self.due_dates) != len(self.ratios):
            raise ValueError("每期都要有一个到期日（due_dates 与 ratios 个数要一致）")
        if self.names is not None:
            if len(self.names) != len(self.ratios):
                raise ValueError("每期都要有一个名称（names 与 ratios 个数要一致）")
            if any(not str(n).strip() for n in self.names):
                raise ValueError("每期名称不能为空")
            too_long = [str(n) for n in self.names if len(str(n)) > 64]
            if too_long:
                raise ValueError(
                    f"每期名称最多 64 个字（与数据库列一致），收到 {len(too_long[0])} 字："
                    f"{too_long[0][:20]}…"
                )
        return self


class OrderFromQuote(BaseModel):
    delivery_date: date | None = None
    remark: str | None = None
    #: 收款计划：不传 = 一条「全款」。详见 `ReceivablePlanIn`。
    receivable_plan: ReceivablePlanIn | None = None


class MilestoneUpdate(CreateModel):
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
    """手工建单的明细行（03-API §27）。

    ## 精度必须与库列一致（审查 B2-02）

    `sales_order_items.quantity` 是 `Numeric(16,3)`、`unit_price` 是 `Numeric(16,4)`。
    从前这两个字段**只有 `gt=0` / `ge=0`**，没有小数位约束，于是：
      - 数量 `0.0001` → 接口 200，**落库被舍成 `0.000`**（等于零数量），货款却是 `0.02`；
      - 数量 `1.23456` → 落库 `1.235`，货款按**未落库的原值**算成 `185.18`，
        而"落库数量 × 单价" = `1.235 × 150` = `185.25` —— 同一张单两个数。
    现在口径与报价明细（`QuoteItemInput`）统一：**数量三位小数、单价四位小数**，
    超了直接报参数错误而不是静默舍入。
    """

    model_config = ConfigDict(extra="ignore")

    sku_id: int
    #: 与 `sales_order_items.quantity`（Numeric(16,3)）同一把尺子
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)
    #: 与 `sales_order_items.unit_price`（Numeric(16,4)）同一把尺子
    unit_price: Decimal = Field(ge=0, max_digits=16, decimal_places=4)
    specification: str | None = Field(default=None, max_length=200)
    remark: str | None = None


class OrderCreate(CreateModel):
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
    payment_terms: str | None = Field(default=None, max_length=128)
    remark: str | None = None


class OrderUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    delivery_date: date | None = None
    remark: str | None = None
    owner_id: int | None = None
    payment_terms: str | None = Field(default=None, max_length=128)


class OrderStatusChange(BaseModel):
    status: str
    remark: str | None = None


# ---- 发货批次（文档 §3.5 / 场景13：分批发货，首批不结束整单）----

class ShipmentBatchItemInput(BaseModel):
    order_item_id: int
    # 与 order_shipment_batch_items.planned_qty Numeric(16,3) 对齐。
    planned_qty: Decimal = Field(gt=0, max_digits=16, decimal_places=3)


class ShipmentBatchCreate(BaseModel):
    request_key: str | None = Field(default=None, max_length=128)
    planned_date: date | None = None
    overdue_reason: str | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[ShipmentBatchItemInput] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_items(self):
        """同一订单明细在一张计划里只能出现一次（第九批 §9.7）。

        重复提交（前端重放、手工拼包）时，原来的校验循环**逐行**比余额、
        且不扣减本请求内已占用的额度，两行都能通过；随后插入明细触发
        `uq_shipment_batch_item` 唯一约束 —— 前端只看到一个 500，
        既不知道哪一行错了，也不知道该怎么改。
        """
        ids = [row.order_item_id for row in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("同一订单明细不能在一张发货计划里重复出现")
        return self


class ShipmentShipItem(BaseModel):
    order_item_id: int
    # 与 order_shipment_batch_items.shipped_qty Numeric(16,3) 对齐。
    shipped_qty: Decimal = Field(ge=0, max_digits=16, decimal_places=3)


class ShipmentBatchShip(BaseModel):
    """登记实发。items 缺省 = 本批计划量全发；给出时逐明细覆盖。"""

    actual_ship_date: date | None = None
    logistics_company: str | None = Field(default=None, max_length=64)
    tracking_no: str | None = Field(default=None, max_length=64)
    #: 晚了就填原因（"因为分批/因为生产/因为客户改期"）——
    #: 系统能算出"晚几天"，算不出"为什么"，归因必须有人填
    overdue_reason: str | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[ShipmentShipItem] | None = None

    @model_validator(mode="after")
    def unique_items(self):
        """实发明细也不能重复（第九批 §9.7）。

        重复项在后端会被 `{order_item_id: qty}` 字典**静默覆盖**，
        表现为"少算了一条"，比直接报错更难发现。
        """
        if self.items is None:
            return self
        ids = [row.order_item_id for row in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("同一订单明细不能重复登记实发数量")
        return self


class OrderDraftLine(BaseModel):
    source_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=4)
    specification: str | None = Field(default=None, max_length=200)
    remark: str | None = None


class OrderDraftCreate(SampleSource):
    request_key: UUID
    items: list[OrderDraftLine] = Field(min_length=1, max_length=200)
    delivery_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=128)
    remark: str | None = None

    @model_validator(mode='after')
    def unique_sources(self):
        if len({i.source_item_id for i in self.items}) != len(self.items):
            raise ValueError('同一来源明细不能重复勾选')
        return self


class OrderDraftUpdate(PatchModel):
    revision: int = Field(gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    items: list[OrderDraftLine] = Field(min_length=1, max_length=200)
    delivery_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=128)
    remark: str | None = None


class OrderDraftConfirm(BaseModel):
    revision: int = Field(gt=0)
    quote_version_id: int = Field(gt=0)
