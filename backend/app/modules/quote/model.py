"""报价单与报价版本。

状态机（收敛了 06-需求澄清清单第 1-5 条指出的重复枚举）：
  draft 草稿 → pending_approval 待审批 → approved 已通过 → sent 已发送
                                        ↘ approval_rejected 审批未通过
  sent → accepted 已接受 / declined 客户拒绝 / expired 已失效

版本内的 approval_status：not_submitted / pending / approved / rejected
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin

QUOTE_STATUS_LABEL = {
    "draft": "草稿",
    "pending_approval": "待审批",
    "approved": "已通过",
    "approval_rejected": "审批未通过",
    "sent": "已发送",
    "accepted": "已接受",
    "declined": "客户拒绝",
    "expired": "已失效",
}

#: 产品核价口径（2026-10-09「产品价格与运费分离」）。
#: 见 `QuoteVersion.pricing_basis` 上的说明。
PRICING_BASIS_LEGACY = "legacy"
PRICING_BASIS_ACTUAL_PASS_THROUGH = "actual_pass_through"
PRICING_BASIS_LABEL = {
    PRICING_BASIS_LEGACY: "历史计算方式（成本含运费）",
    PRICING_BASIS_ACTUAL_PASS_THROUGH: "产品价不含运费，运费原额代收代付",
}


class Quote(Base, IdMixin, TimestampMixin):
    __tablename__ = "quotes"
    __table_args__ = (
        Index("ix_quotes_opportunity", "opportunity_id"),
        Index("ix_quotes_customer", "customer_id"),
    )

    quote_no: Mapped[str] = mapped_column(String(32), unique=True)
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    current_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuoteVersion(Base, IdMixin):
    __tablename__ = "quote_versions"
    __table_args__ = (Index("ix_quote_versions_quote", "quote_id"),)

    quote_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("quotes.id"))
    version_no: Mapped[int] = mapped_column(BigInteger, default=1)
    subtotal_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    charge_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 这一版按哪种口径算产品价（2026-10-09 业务口径「产品价格与运费分离」）。
    #:
    #: - `legacy`：历史计算方式，`base_cost` **含**运费。历史版本一律留在这个口径上，
    #:   否则拿今天的公式回算会得出不同的建议价与底价——等于改写已经发给客户的东西。
    #: - `actual_pass_through`：产品价格不含运费，运费按**原额**代收代付（不赚不赔）。
    #:
    #: 存到版本上而不是全局开关：口径是**随版本**变的，同一张报价单的新旧版本可以
    #: 分别处在两个口径上，事后必须能答出"这一版当时按什么算的"。
    pricing_basis: Mapped[str] = mapped_column(
        String(32), default=PRICING_BASIS_ACTUAL_PASS_THROUGH
    )
    #: 运费收费金额 = 客户承担的运费（代收代付）。与 `other_charge_amount` 一起
    #: 把 `charge_amount` 拆开，只为**显示与对账**；三者关系恒为
    #: `charge_amount == logistics_amount + other_charge_amount`。
    #: ⚠️ 这三个都**已经含在** `total_amount` 里了（总额公式没变），
    #: 前端/文件任何地方都**不能**再把它加到总额上——会重复计费。
    logistics_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 非运费、非折扣的附加费用合计（`charge_amount` 减去运费的那一部分）。
    other_charge_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    # 外贸报价才用：报价时点的汇率快照（内贸留空）
    exchange_rate_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 6), nullable=True)
    exchange_rate_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange_rate_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 贸易条款：内贸「含运费 / 不含运费」；外贸 FOB / CIF / EXW
    trade_terms: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_terms: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 客户抬头快照（第八批 §8.7 返修，审查 2026-10-07 实测）。
    #: 出对客文件时"客户名"原来实时读 `customers.name`：客户改了名，**旧版本再出图
    #: 就印成新名字**，与当时真正发给客户的那一份对不上。必须跟计价单位一样存快照。
    #: 可空：历史版本没有留存，出图明确写"待核实"，不静默补当前客户名。
    #: 长度必须**与 `customers.name` 一致（200）**：原来是 128，于是 129~200 字的
    #: 合法客户名一建报价就撞 asyncpg 22001（value too long），报价直接建不出来。
    #: 加列时没有回头核对源字段长度 —— 快照列一律按源列长度取，别再手写一个数。
    customer_name_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: 联系人抬头快照，理由同上（原来实时读 `contacts.name`）。
    contact_name_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 有效期快照，理由同上（原来实时读 `quotes.valid_until` 的当前值）。
    valid_until_snapshot: Mapped[date | None] = mapped_column(Date, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(24), default="not_submitted")
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuoteItem(Base, IdMixin):
    __tablename__ = "quote_items"
    __table_args__ = (Index("ix_quote_items_version", "quote_version_id"),)

    quote_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("quote_versions.id"))
    # 与商机需求明细的追溯关系（06-需求澄清清单第 2-4 条的缺口）
    opportunity_item_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 定制项（文档场景09）：尚无正式 SKU 时也能报价——sku_id 为空，
    # 由 inquiry_id 指向定制需求，成本与价格靠人工核价填。
    # 非空约束在这里去掉是刻意的：定制件在打样投产前本来就没有 SKU 编码，
    # 强制先建 SKU 等于把"先报价接单、后建档"的真实流程堵死。
    sku_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("skus.id"), nullable=True
    )
    inquiry_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 编号快照：需求被改名/归档后，这张报价仍要能说明"当时对着哪条需求报的价"
    inquiry_no_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sku_code_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sku_name_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    spec_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: 计价单位快照（第八批 §8.7）：报价那一刻 SKU 的单位（件/套/箱…）。
    #: 必须存快照，不能等出对客 Excel 时回查 `skus.unit`——单位改了以后，
    #: 旧版本再生成出来的表就会拿今天的单位冒充当时报的价，客户一比对就是口径不一致。
    #: 可空：历史版本行没有这个值，出图时明确写"待核实"，不静默补当前单位。
    unit_snapshot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: 这条明细按**哪一版 SKU 主数据**算的（第八批 §8.14）。
    #: 为空表示生成这条明细时，这个 SKU 还没有任何"已确认"的主数据。
    #: 存版本号而不是只存"确认过"这个布尔：确认值会随新的确认动作演进，
    #: 只记"确认过"没法回答"当时用的是哪一版"。与旁边那排 *_snapshot 同一性质 ——
    #: 事后能追溯，不靠回查当下的主数据。
    master_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=1)
    cost_snapshot: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    package_cost_snapshot: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    logistics_cost_snapshot: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    standard_price_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    recommended_price_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    minimum_price_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    # A09：拟报价来源快照（customer_specific/level/general）与当时客户等级——
    # 事后要能回答"这一版当初按哪条规则带的价"
    # 不能只有 16：取价来源写成 `customer_specific`（17 字符）时会
    # "value too long for character varying(16)" —— 有专属价的客户一建明细就 500。
    price_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_level_snapshot: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quoted_price: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    profit_snapshot: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    profit_rate_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)
    # 出口退税额（单件）与退税后利润：内贸恒为 0
    tax_refund_snapshot: Mapped[Decimal] = mapped_column(
        Numeric(16, 4), default=0, server_default="0"
    )
    profit_with_refund_snapshot: Mapped[Decimal] = mapped_column(
        Numeric(16, 4), default=0, server_default="0"
    )
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuoteCharge(Base, IdMixin):
    __tablename__ = "quote_charges"

    quote_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("quote_versions.id"))
    charge_type: Mapped[str] = mapped_column(String(32), default="other")
    description: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    is_discount: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_no: Mapped[int] = mapped_column(BigInteger, default=0)
    #: 物流费用被业务确认的时刻（口径：运费按**已确认的实际金额**代收代付）。
    #:
    #: 为什么不能只看金额：`amount = 0` 同时代表「没填」和「明确是零运费」这两种
    #: 完全不同的状态 —— 前者正式发送时必须拦住并提示先确认，后者可以直接发。
    #: 金额列分不出来，只能单独记一个"确认过没有 + 什么时候确认的"。
    #: 与 `product_costs.stopped_at` 同一手法：时间戳而不是布尔，翻记录能看出时刻。
    logistics_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QuoteSendLog(Base, IdMixin):
    __tablename__ = "quote_send_logs"

    quote_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("quote_versions.id"))
    channel: Mapped[str] = mapped_column(String(32), default="手动标记")
    receiver: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="success")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
