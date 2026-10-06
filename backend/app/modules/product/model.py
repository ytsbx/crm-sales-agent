"""产品与 SKU。

约定（对齐总设计文档 §11）：
- Product 是销售侧的产品资料，SKU 是可报价、可报价的最小单位；
- 完整 BOM 由 ERP/MES 维护，CRM 不重复建设，只存销售需要的规格与包装信息。

## 第八批 §8.14：SKU 权威字段的来源与确认

上面这些列是**当前值**，但它们回答不了验收要问的三件事：
这个值是从哪来的？什么时候变的？正式报价用的是哪一版确认过的主数据？

所以本节以下三张表把"值的来历"独立记下来（与当前值并存，不互相覆盖）：

    sku_identity_sources   这条 SKU 在各个外部系统/店铺里的身份（编码 + 名称）
    sku_field_authorities  每个关键字段的来源、外部身份、更新时间、确认版本
    sku_master_versions    人工确认过的版本快照（正式报价回溯用）

**刻意不做的事**：任何外部来源都**不自动写回** `Sku` 的列。来源未核实时
一律显示"待核实"，权威归属为空表示"还没拍板"（不默认任一系统为主）；
外部数据与本地值不一致时进差异队列，由带权限、带审计的确认动作决定谁生效。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType, TimestampMixin


class Product(Base, IdMixin, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (Index("ix_products_name", "name"),)

    name: Mapped[str] = mapped_column(String(200))
    product_line: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge: Mapped[str | None] = mapped_column(Text, nullable=True)  # 产品知识，供 Agent 检索
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Sku(Base, IdMixin, TimestampMixin):
    __tablename__ = "skus"
    __table_args__ = (
        Index("ix_skus_product", "product_id"),
        Index("ix_skus_code", "sku_code", unique=True),
    )

    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    sku_code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    specification: Mapped[str | None] = mapped_column(String(200), nullable=True)
    color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    material: Mapped[str | None] = mapped_column(String(64), nullable=True)
    length: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    width: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    height: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    carton_qty: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    carton_volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    moq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    package_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), default="件")
    status: Mapped[str] = mapped_column(String(32), default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ==================================================================== §8.14


class SkuIdentitySource(Base, IdMixin, TimestampMixin):
    """SKU 在某个外部系统/店铺里的**身份**（编码 + 名称）。

    为什么身份要单独一张表、而不是给 `skus` 加一列 `external_code`：
    §8.14 的验收明确要求"三系统同名不同码不自动合并"、"改码不破坏历史报价"。
    加一列外部编码意味着**一个 SKU 只能记住一个外部身份**，遇到"简道云叫 A、
    聚水潭叫 B"就必须改写这一列 —— 改完历史就对不上了。这里每个
    (系统, 店铺, 外部编码) 一行，可以同时挂到同一个 `sku_id` 上。

    改码怎么处理：**老行不删**，标成 `renamed` 并记 `superseded_by_code` 指向新码。
    这样"用老编码来查"仍然能找到同一个 SKU，历史报价/历史单据的编码快照不会失效。

    `sku_id` 允许为空 = 还没匹配到本地 SKU（待确认）。这时外部字段值先存在
    `source_payload` 里，等本地 SKU 补齐后重放（replay）即可落成字段权威行。
    """

    __tablename__ = "sku_identity_sources"
    __table_args__ = (
        UniqueConstraint(
            "system_type", "shop_id", "external_code", name="uq_sku_identity_sources_code"
        ),
        Index("ix_sku_identity_sources_sku", "sku_id"),
        Index("ix_sku_identity_sources_name", "system_type", "external_name"),
    )

    #: 空 = 待匹配（本地还没有对应 SKU）。有外键是为了"删了 SKU 不留悬空身份"，
    #: 生产库的约束与历史数据清洗要点见交接回报。
    sku_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("skus.id"), nullable=True
    )
    system_type: Mapped[str] = mapped_column(String(32))
    shop_id: Mapped[str] = mapped_column(String(64), default="*")
    external_code: Mapped[str] = mapped_column(String(64))
    external_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: pending / matched / renamed / stopped / conflict
    match_status: Mapped[str] = mapped_column(String(16), default="pending")
    #: 凭什么匹配上的：external_code=编码同名 / name_unique=名称唯一命中 / manual=人工指定
    match_basis: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 改码后的新编码。老行保留并指向它，所以按老码也能反查到同一个 SKU。
    superseded_by_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 还没匹配上时，来源给的字段值先存这里（可重放）。
    source_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class SkuFieldAuthority(Base, IdMixin, TimestampMixin):
    """SKU 某个关键字段的**来源、更新时间、外部身份与人工确认版本**（§8.14 核心）。

    一个 (SKU, 字段) 一行。三组信息刻意分开保存：

      · `source_*`      —— 当前来源是谁、来源里这条 SKU 叫什么、那边什么时候改的、
        这个来源**核没核实过**。没核实就是"待核实"，不给它任何可信度。
      · `authority`     —— 这个字段**归谁说了算**。空 = 还没拍板。§8.14 明确
        "不默认任一系统为主"，所以这里绝不给默认值；只有人显式登记才会填，
        并记 `authority_set_by/at`。
      · `confirmed_*`   —— 人工确认过的值与版本号。正式报价要用的就是它
        （见 `sku_master_versions`），而不是 `skus` 表上的当前值 —— 当前值
        可能刚被本地编辑改过、还没经过确认。

    `status` 的取值：unverified（待核实）/ pending_confirmation（有待确认差异）/
    confirmed（已确认）/ conflict（来源之间冲突，需人工裁定）。
    """

    __tablename__ = "sku_field_authorities"
    __table_args__ = (
        UniqueConstraint("sku_id", "field_name", name="uq_sku_field_authorities_field"),
        Index("ix_sku_field_authorities_status", "status"),
    )

    sku_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("skus.id"))
    field_name: Mapped[str] = mapped_column(String(64))
    source_system: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 来源是否已核实。默认 False —— 这一轮拿不到任何真实来源资料，
    #: 所以对外一律显示"待核实"，而不是"来自简道云（可信）"。
    source_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    #: 该来源里这条 SKU 的身份（编码）。同一 SKU 在不同系统里编码可以不同。
    external_identity: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_value: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    #: 字段权威归属（CRM / JUSHUITAN / JIANDAOYUN / MANUAL）。空 = 未拍板。
    authority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    authority_set_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    authority_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 人工确认的版本号（0 = 从没确认过）。与 `sku_master_versions.version_no` 对应。
    confirmed_version: Mapped[int] = mapped_column(BigInteger, default=0)
    confirmed_value: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="unverified")


class SkuMasterVersion(Base, IdMixin, TimestampMixin):
    """人工确认过的一版 SKU 主数据**快照**（§8.14 验收："正式报价能回溯用哪一版"）。

    为什么快照而不是只留版本号：报价一旦发出，事后有人改了 SKU 资料，
    就必须能回答"当时用的是哪一版、谁确认的、依据哪条差异"。只留一个自增号
    回答不了 —— 数字不会告诉你当时的值。
    """

    __tablename__ = "sku_master_versions"
    __table_args__ = (
        UniqueConstraint("sku_id", "version_no", name="uq_sku_master_versions_no"),
    )

    sku_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("skus.id"))
    version_no: Mapped[int] = mapped_column(BigInteger)
    #: 这一版确认后的完整字段值（只含字段权威表里有确认值的字段）。
    values: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    #: 每个字段的来源与确认版本（"这版里 unit 来自哪、v几确认的"）。
    source_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 由哪条差异确认产生的（可点回差异与原始证据）。
    diff_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
