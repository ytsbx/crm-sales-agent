"""定制询价库（领导模块③：产品知识库 · 定制询价类）。

沉淀"客户问了但我们还没有标准产品"的询价——这是找开发方向的原料：
- 每一条 = 一个真实的定制需求信号（谁问的、要多少、能接受什么价）；
- 状态机：open（待评估）→ developing（已立项开发）→ converted（已转商机/报价），
  也可 archived（归档不做）；从不真删，deleted_at 软删；
- 与产品知识库的"已投产类"互不污染：没有 SKU 就进这里，定型后再建产品。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

STATUS_LABELS = {
    "open": "待评估",
    "developing": "开发中",
    "converted": "已转商机",
    "archived": "已归档",
}

#: 来源类型（第五批 §6.1(3)，口径已确认：允许转「内部开发需求」）。
#:
#: 为什么必须显式区分、不能靠"有没有客户"去猜：业务上本来就有"客户还没定、
#: 先把需求记下来"的正常询价；而洞察转出来的内部开发需求**性质完全不同**——
#: 它是市场研究资料，不是客户提出的采购需求（文档明说"不要把市场研究资料
#: 伪装成客户已提出采购需求"）。两者的权限口径也不一样：正常无客户询价
#: 按现有规则对同事可见，内部开发需求只对提出者与管理者可见。
ORIGIN_LABELS = {
    "customer": "客户询价",
    "internal_dev": "内部开发需求",
}

#: 字段归属（口径 2026-10-04）：把"需求"拆成**链级**与**版本级**两层。
#: - 链级：整条需求共享（哪家客户、哪个联系人、关联哪个商机、对接报价员），
#:   改一次应该对链条上每一版都生效；
#: - 版本级：某一版专属（标题/描述/数量/目标价/状态/备注），历史版本**不许改**，
#:   要改就发起修订生成新一版（§3.3"旧版永不覆盖"）。
CHAIN_FIELDS = frozenset(
    {"customer_id", "contact_id", "opportunity_id", "oa_quote_user_id", "oa_quote_user_name"}
)
VERSION_FIELDS = frozenset(
    {"title", "description", "quantity", "target_price", "status", "remark", "revision_note"}
)


class CustomInquiry(Base, IdMixin):
    __tablename__ = "custom_inquiries"
    __table_args__ = (
        Index("ix_custom_inquiries_customer", "customer_id"),
        Index("ix_custom_inquiries_status", "status"),
    )

    # 需求编号（文档场景09）：尚无正式 SKU 时，询价/报价/打样三头都靠它
    # 指向同一条需求——没有它，"这张报价是从哪条定制需求来的"就断了。
    # 走 settings/numbering 的取号器（XQ+日期+4 位），与报价/订单同一套机制。
    #
    # **不设唯一约束**：编号标识需求、version 标识修订，同一条需求的 v1/v2
    # 是两行、共用同一个编号——加唯一索引会让"修订一次就写不进去"。
    # 新号不撞旧号由取号器的 taken 探针保证（它按"号是否已存在"逐个探测）。
    inquiry_no: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 修订链（文档 §3.3/§四"需求及修订"）：客户改了三次要求，要能看出怎么变的。
    # 修订 = 新增一版，历史版本永不覆盖；root_id 指向链条首版（原始记录为 NULL，
    # 查询时把"自己"也算进链条）
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    root_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revision_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 「已被新版取代」标记：修订生成新版时给旧版打上。历史版本不覆盖，但要能
    #: 一眼看出哪版是旧的（不用新状态值去覆盖 status，否则"已转商机"这类
    #: 业务状态会丢——取代是版本属性，业务状态是另一回事）。
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 投产时显式关联新 SKU（历史需求不被覆盖）
    converted_sku_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(16, 3), nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # ---- 钉钉询价审批的对接报价员（场景11）----
    # 钉钉那张「产品询价申请」里「对接报价员」是**必填的联系人**控件，只能选
    # 指定的人（目前是子木、宋桂香两位）。钉钉存的是**人的编号**不是姓名，
    # 所以这里存编号，同时把姓名存一份用于显示——只存编号的话，
    # 列表里就得回查钉钉才知道"这单给了谁"，而钉钉那条路可能不通。
    oa_quote_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    oa_quote_user_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ---- 来源类型与来源洞察回链（第五批 §6.1(3)）----
    # `customer`（默认）= 客户提出的询价；`internal_dev` = 市场研究转来的内部开发需求。
    # 存量行一律 customer（见迁移注释）。
    origin: Mapped[str] = mapped_column(
        String(16), default="customer", server_default="customer", nullable=False
    )
    # 由哪条新品洞察转来（部分唯一索引见迁移：一个洞察只能转出一条）。
    # 有了它，需求详情能回链到洞察、洞察也能证明"我转出去的是哪一条"。
    source_insight_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
