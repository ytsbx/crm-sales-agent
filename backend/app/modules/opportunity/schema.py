from datetime import date

from pydantic import BaseModel, ConfigDict, Field
from app.core.patch_schema import PatchModel



class OpportunityCreate(BaseModel):
    customer_id: int
    title: str = Field(min_length=1, max_length=200)
    primary_contact_id: int | None = None
    source: str | None = None
    stage_id: int | None = None
    expected_amount: float | None = None
    expected_close_date: date | None = None
    owner_id: int | None = None
    competitor: str | None = None
    risk_level: str | None = None
    next_action: str | None = None


class OpportunityUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    primary_contact_id: int | None = None
    source: str | None = None
    expected_amount: float | None = None
    expected_close_date: date | None = None
    competitor: str | None = None
    risk_level: str | None = None
    next_action: str | None = None


class StageChange(BaseModel):
    stage_id: int | None = None
    stage_code: str | None = None
    remark: str | None = None


class OpportunityLose(BaseModel):
    loss_reason_id: int
    remark: str | None = None
    reopen_at: date | None = None


class OpportunityWin(BaseModel):
    win_quote_version_id: int | None = None
    remark: str | None = None


class OpportunityConfirmWin(BaseModel):
    """确认成交并生成订单（方案 §5 / A13）：一次动作完成 接受→成交→建单。"""

    win_quote_version_id: int | None = None
    delivery_date: date | None = None
    remark: str | None = None


class OpportunityItemCreate(BaseModel):
    sku_id: int
    quantity: float = 1
    target_price: float | None = None
    specification: str | None = None
    color: str | None = None
    package_requirement: str | None = None
    delivery_date: date | None = None
    destination: str | None = None
    remark: str | None = None


class OpportunityItemUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quantity: float | None = None
    target_price: float | None = None
    specification: str | None = None
    color: str | None = None
    package_requirement: str | None = None
    delivery_date: date | None = None
    destination: str | None = None
    remark: str | None = None


class OpportunityAssign(BaseModel):
    """变更商机负责人（03-API §11）。"""

    owner_id: int | None = None
    remark: str | None = None


class OpportunityClone(BaseModel):
    """复制商机（03-API §11）。

    典型场景：同一个客户下一年度的同类采购，字段和需求明细几乎一样，
    重新录一遍纯属浪费。复制出来的商机**不带**已成交/失单状态与金额快照，
    阶段回到初始阶段，避免把上一单的结果当成新单的事实。
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    customer_id: int | None = None
    owner_id: int | None = None
    expected_close_date: date | None = None
    copy_items: bool = True
    remark: str | None = None


class OpportunityItemsBatch(BaseModel):
    """整批替换需求明细（03-API §12）。"""

    items: list[OpportunityItemCreate]


class OpportunityItemCopy(BaseModel):
    """从另一个商机复制需求明细（03-API §12）。"""

    model_config = ConfigDict(extra="ignore")

    # 复制哪些 SKU；不传表示全量复制
    sku_ids: list[int] | None = None
    # 已存在的 SKU 如何处理：skip 跳过 / replace 覆盖数量 / add 追加一条
    on_conflict: str = "skip"


class RecommendProductsRequest(BaseModel):
    """需求商品推荐（03-API §12）。

    没有模型能力时的兜底实现：按"这个客户历史成交过的 SKU"排序推荐，
    而不是凭空生成 —— 宁可给一个说得清来源的列表。
    """

    model_config = ConfigDict(extra="ignore")

    limit: int = 10
    # 用于按关键词过滤产品名 / 规格
    keyword: str | None = None


class StageCreate(BaseModel):
    """新增阶段（03-API §13）。"""

    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    sequence: int | None = None
    is_win: bool = False
    is_loss: bool = False
    status: str = "active"


class StageUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    sequence: int | None = None
    is_win: bool | None = None
    is_loss: bool | None = None
    status: str | None = None


class StageReorder(BaseModel):
    """按给定顺序重排阶段（03-API §13）。

    只传要调整的 id 列表，未出现在列表里的阶段保持原顺序并排在其后 ——
    这样界面只拖动几个阶段时不用把整条流水线都传上来。
    """

    stage_ids: list[int]


class LossReasonCreate(BaseModel):
    """新增失单原因（03-API §13）。"""

    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    category: str | None = None
    status: str = "active"


class LossReasonUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    category: str | None = None
    status: str | None = None
