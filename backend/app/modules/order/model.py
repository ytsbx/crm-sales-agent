"""销售订单、订单明细与履约状态历史。

关于 ERP/MES：文档要求 CRM 推订单、拉履约状态，但对方系统尚未就绪。
这一版把 `erp_order_id`、状态历史、`external_mappings` 都留好，
接口接通前状态由人工维护，接通后由 Adapter 回写，CRM 侧结构不用改。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin

SHIPMENT_STATUS_LABEL = {
    "planned": "待发货",
    "shipped": "已发货",
    "cancelled": "已取消",
}

ORDER_STATUS_LABEL = {
    "pending": "待生产",
    "in_production": "生产中",
    "shipped": "已发货",
    "delivered": "已签收",
    "completed": "已完成",
    "cancelled": "已取消",
}


class SalesOrder(Base, IdMixin, TimestampMixin):
    __tablename__ = "sales_orders"
    __table_args__ = (
        Index("ix_sales_orders_customer", "customer_id"),
        # 同一报价版本只能转一次订单（幂等）
        Index("ix_sales_orders_quote_version", "quote_version_id", unique=True),
    )

    order_no: Mapped[str] = mapped_column(String(32), unique=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quote_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quote_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 签单负责人 = 业绩归属（文档 :61「交接后保留历史业绩归属」/ §3.8）。
    # 创建订单时写死，**离职交接与手工改负责人都不动它**。
    #
    # 为什么必须和 owner_id 分两列：owner_id 是"当前负责人"，管数据范围与
    # 跟进责任（谁看得到这单、谁去催款），它会随交接和手工调整而变；业绩若
    # 跟着它走，销售离职后他谈下来的单子收的钱就记到接手人名下了。反过来
    # 交接如果不动 owner_id，接手人又看不到这些订单、等于没人跟进。
    # 两列并存才同时满足"有人接"和"历史业绩不改写"。
    sales_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    erp_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SalesOrderItem(Base, IdMixin):
    __tablename__ = "sales_order_items"
    __table_args__ = (Index("ix_sales_order_items_order", "order_id"),)

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_orders.id"))
    sku_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("skus.id"))
    sku_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    specification: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=0)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderShipmentBatch(Base, IdMixin):
    """发货批次（文档 §3.5/场景13）：分批发货的计划与事实。

    计划（planned_date/各明细 planned_qty）与实际（actual_ship_date/shipped_qty、
    物流单号）分开记——跟单的承诺和事实不能混在一个字段里。
    首批发货只推进订单到"已发货"；整单 completed 的闸门在 change_status。
    """

    __tablename__ = "order_shipment_batches"
    __table_args__ = (Index("ix_order_shipment_batches_order", "order_id"),)

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_orders.id"))
    batch_no: Mapped[int] = mapped_column(BigInteger, default=1)  # 第几批，从 1 起
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned/shipped/cancelled
    planned_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    logistics_company: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 逾期原因：把"第 2 批晚了几天"归因到"因为分批/因为生产/因为客户改期"。
    #: 数据只能算出"晚了几天"，算不出"为什么"——归因必须有人填。
    overdue_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderShipmentBatchItem(Base, IdMixin):
    """批次明细：本批对哪个订单明细发多少。剩余量 = 订购量 − 各已发批次合计。"""

    __tablename__ = "order_shipment_batch_items"
    __table_args__ = (
        Index("ix_order_shipment_batch_items_batch", "batch_id"),
        UniqueConstraint("batch_id", "order_item_id", name="uq_shipment_batch_item"),
    )

    batch_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("order_shipment_batches.id"))
    order_item_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order_items.id"))
    sku_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    planned_qty: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=0)
    shipped_qty: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=0)


class OrderStatusHistory(Base, IdMixin):
    __tablename__ = "order_status_history"
    __table_args__ = (Index("ix_order_status_history_order", "order_id"),)

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_orders.id"))
    old_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    new_status: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(16), default="WEB")  # WEB/ERP/AGENT
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderMilestone(Base, IdMixin):
    """跟单里程碑（领导模块⑤）：从客户交期倒推的关键节点，跟单人工登记实际日期。"""

    __tablename__ = "order_milestones"
    __table_args__ = (
        Index("ix_order_milestones_order", "order_id"),
        # 并发首次打开同一订单的跟单 Tab 会同时初始化：唯一约束封死重复行
        UniqueConstraint("order_id", "node", name="uq_order_milestones_order_node"),
    )

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_orders.id"))
    node: Mapped[str] = mapped_column(String(32))
    planned_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # ---- 文档（方案 :103）要求节点记录「计划日、实际日、责任人、来源证据、逾期原因和状态」----
    # 前三项原先只有计划日/实际日，责任人、来源证据、逾期原因三项没落地：
    # 没有"责任人"就不知道该催谁；没有"来源证据"事后无法回看当初凭什么这么排；
    # 没有"逾期原因"就只能说"晚了 5 天"，说不出"为什么晚"——**归因全靠人填**，
    # 系统不猜（猜测出来的归因比没有归因更危险，会被当成事实引用）。
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    overdue_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 逾期提醒只在第一次逾期时推一次，这个时间戳就是"推过了"的凭证
    overdue_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
