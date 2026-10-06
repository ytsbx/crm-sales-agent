"""样品（PRD §19、02-ER §13）。

三张表对应 ER §13：申请单 / 明细 / 寄样记录。
状态机按 03-API §26 的接口语义推（文档没写明状态名，不自行发明）：
    pending → approved → shipped → signed
另有 rejected（approve 接口可拒）。驳回**不是终态**：驳回单改完资料会回到 pending
重新走一遍审批（见下面 SAMPLE_TRANSITIONS 的说明）。

为什么要独立模块而不是塞进商机：PRD §9.2 的商机阶段里本来就有「样品」阶段，
但只有阶段没有实体，业务走到那里没有任何东西可录入。这个模块补的就是它。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

SAMPLE_STATUS_LABEL: dict[str, str] = {
    "pending": "待审批",
    "approved": "已批准",
    "rejected": "已拒绝",
    "shipped": "已寄样",
    "signed": "已签收",
}

# 允许的状态流转。写死成一张表，避免各处 if 判断漂移。
#
# rejected 的两个出口（业务方 2026-10-05 定：「驳回不是终态」）：
#   → pending ：**重新提交**。改完资料会自动走这条（"改完再报"）；一个字都不想改的
#               走 POST /samples/{id}/resubmit 原样再报。没有它，驳回过的单子就只能
#               重开一张，中间那几轮沟通痕迹全断。
#   → approved：**主管改判**。上次驳错了直接批回来，不必让跟单先改点什么再绕一圈。
SAMPLE_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"approved", "rejected"},
    "approved": {"shipped"},
    "rejected": {"pending", "approved"},
    "shipped": {"signed"},
    "signed": set(),
}

#: **还没结束**的打样状态：既构成履约保护（客户还在等结果，不能当冷落客户回收），
#: 也是离职交接必须迁移责任的"未完成打样"。
#:
#: 注意它**不**包含 `rejected`：被驳回的单子要么重报、要么就此作罢，
#: 不该长期挂在一个离职人的名下当"未完成"。
#: 也不以 `confirm_status=accepted` 为结束依据 —— 客户接受了这张单的**保护**结束
#: （见 settings/service 的回收保护判据），但单子本身还在物流/归档流程里，
#: 责任仍然要有人接。
SAMPLE_OPEN_STATUSES: tuple[str, ...] = ("pending", "approved", "shipped", "signed")

#: 客户确认状态（文档 §3.5：「客户收到样品不等于样品被接受」）。
#:
#: 签收（signed）是物流事实，客户确认才是业务事实——把两者混在一个字段里，
#: "这批样到底过没过"就说不清。所以确认独立成三态，且**必须签收之后**才能确认。
CONFIRM_PENDING = "pending"
CONFIRM_ACCEPTED = "accepted"
CONFIRM_REJECTED = "rejected"

CONFIRM_STATUS_LABEL: dict[str, str] = {
    CONFIRM_PENDING: "待客户确认",
    CONFIRM_ACCEPTED: "客户已接受",
    CONFIRM_REJECTED: "客户未通过",
}


class SampleRequest(Base, IdMixin):
    __tablename__ = "sample_requests"
    __table_args__ = (
        Index("ix_sample_requests_opportunity", "opportunity_id"),
        Index("ix_sample_requests_customer", "customer_id"),
        Index("ix_sample_requests_owner_status", "owner_id", "status"),
        # "待客户确认"是跟单要盯的一类单，给它一个索引
        Index("ix_sample_requests_confirm_status", "confirm_status"),
        # 冻结判断要查"这一版有没有子版本"，按 parent_id 建索引
        Index("ix_sample_requests_parent", "parent_id"),
    )

    opportunity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("opportunities.id"), nullable=True
    )
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=True
    )
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    # 审批轮次（第一批返修 §3.2）：每次**真的重新回到待审批**（驳回后重提、
    # 已批准后改车间依据）自增一次。通知/时间线的事件键带上它，否则第二轮会撞上
    # 第一轮的固定键被去重吞掉，事后看不出"驳回过几次、每轮批的是哪版资料"。
    review_round: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default="1", nullable=False
    )
    # 当前这一轮是**哪次提交**开的（弱网重试靠它认幂等，不靠"状态是不是待审批"——
    # "待审批时调重提"仍然按原口径被拦）。换轮次时清空。
    review_request_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ---- 修订版（第一批返修 §3.3，口径已确认 A：开新修订版）----
    # 已制作的单子不能再原地改车间依据：那样看不出这是第几版，旧制作时间还留在同一行，
    # 和已经做出来/寄走的实物对不上。改成"原单出 V2、旧版冻结只读"。
    # `parent_id` 指向被取代的那一版；**有子版本即视为冻结**，不另设状态位。
    version: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default="1", nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sample_requests.id"), nullable=True
    )
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_context: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    request_key: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ---- 生产打样资料（文档 §3.5：用途、目标完成日、验收标准、费用和责任人）----
    # 没有这几项，打样需求单发给车间是干不了活的：不知道什么时候要、按什么标准验收。
    # **样品数量不在这里重复**：它在 sample_items.quantity 上（可以一单多样），
    # 另立一个汇总字段只会产生两个真相。
    #
    # **材质 / 工艺 / 图纸版本也不在这里**：它们逐行不同（一单里两个盒子可能用
    # 不同材质、走不同工艺、各自按自己的图纸），所以挂在 sample_items 上。
    # 早先这三项挂在这里（单头），一单多样时只能写进备注，车间拿到的单子看不出区别。
    purpose: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 用途
    target_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 目标完成日
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)  # 验收标准
    # 打样费用：**空着 = 未填**，与"免费（0 元）"是两回事，所以列可空、不设默认值。
    # 之前是 NOT NULL + default 0，前端清空输入框传 null 会直接撞非空约束。
    sample_fee: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)  # 打样费用
    production_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 责任人
    # 制作完成时间：由跟单登记（CRM 管不到车间，所以只记录事实、不当闸门）
    made_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 制作事件（结构化，第一批返修 §3.4）：每条 {key, at, note, by, recorded_at}。
    # 幂等**只看这里的 key**；之前是拿"整段备注里是否包含新说明"做子串匹配，
    # 新说明只要恰好是旧说明的子串就被判成"已经写过"而被吞掉。
    # `remark` 退回纯展示字段，不承担任何幂等判断。
    made_events: Mapped[list | None] = mapped_column(JSONType, nullable=True)

    # ---- 制作依据（2026-10-06）：这次到底是照**哪几份文件**做出来的 ----
    #
    # 为什么必须有它：`sample_items.drawing_version` 只是**一串自由文字**
    # （如 "DWG-2026-A3"），它和任何文件之间原本**没有任何关联**。
    # 于是"这批样按图纸做的，做出问题来了"这种事发生时报不了账——
    # 系统里查不出当时用的到底是哪一份图纸，照片、规格书也一样。
    #
    # 登记制作完成时由跟单**显式指定**，存下当时的文件 id、文件名、sha256、
    # 大小与挂载类别。**只记文件名不算**（文件可以被换掉），必须是能自证的
    # 校验值；将来对账时把它和实际文件一比，就知道是不是同一份。
    #
    # 语义：**登记即固化**。要换依据只能开修订版（新的制作依据属于新版本，
    # 不能拿旧版的凭证去背书新版的活）。
    basis_files: Mapped[list | None] = mapped_column(JSONType, nullable=True)

    # ---- 客户确认：与签收分开（「客户收到样品不等于样品被接受」）----
    confirm_status: Mapped[str] = mapped_column(
        String(16), default=CONFIRM_PENDING, server_default=CONFIRM_PENDING, nullable=False
    )
    customer_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirm_remark: Mapped[str | None] = mapped_column(String(255), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class SampleItem(Base, IdMixin):
    __tablename__ = "sample_items"
    __table_args__ = (Index("ix_sample_items_request", "sample_request_id"),)

    sample_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sample_requests.id")
    )
    # 定制项（文档场景09）：尚无正式 SKU 时也能打样——sku_id 为空，
    # 由需求编号说明"打的是哪条需求"。定制件本来就要先打样再定 SKU，
    # 强制先建档等于把这个顺序反过来。
    sku_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("skus.id"), nullable=True
    )
    inquiry_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inquiry_no_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 定制项的展示名（没有 SKU 名称可用）
    item_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_snapshot: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    original_quantity: Mapped[Decimal | None] = mapped_column(Numeric(16, 3), nullable=True)
    specification: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- 车间依据（文档 §3.5）----
    # 这三项**逐行不同**：一单里两个商品可能一个用瓦楞纸、一个用 PP 中空板，
    # 走不同工艺、各自按自己的图纸版本。挂在单头就只能写一份，车间照着做会做错。
    # 它们同时也是"审批批的是哪一版资料"的核心——改了就等于换了要求，
    # 所以 update 时会触发退回重审（见 router.update_sample）。
    craft: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 工艺
    material: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 材质
    drawing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 图纸版本
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=1)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class SampleShipment(Base, IdMixin):
    __tablename__ = "sample_shipments"
    __table_args__ = (Index("ix_sample_shipments_request", "sample_request_id"),)

    sample_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sample_requests.id")
    )
    carrier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
