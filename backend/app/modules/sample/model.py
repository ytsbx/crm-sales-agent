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

#: 允许**软删**的状态（2026-10-15 补）。
#:
#: 打样单是留痕单据（审批轮次、车间依据、图纸版本都挂在它上面），所以不是谁都能删。
#: 只有还没走流程的两种能删：
#:   - `pending`  刚建出来 / 被驳回后重报，还没批；
#:   - `rejected` 审批驳回了，跟单决定"就此作罢"（见上面那条注释：驳回单不算未完成打样）。
#: `approved` / `shipped` / `signed` **一律拒绝**：它们已经进了生产 / 寄样环节，
#: 删掉会让审批留痕与实际做出来的东西对不上（与「已转订单的报价不能删」同一个道理）。
#:
#: 判据**只在这一处**：路由按它把关，列表按它给前端下发 `can_delete` ——
#: 前端不要自己再拼一份"什么状态能删"，那份迟早和后端分叉。
SAMPLE_DELETABLE_STATUSES: tuple[str, ...] = ("pending", "rejected")


def can_delete_sample(sample) -> bool:
    """已拒绝/撤回的钉钉申请可软删；审批中和已制作、寄送的单据仍受保护。"""
    if sample.deleted_at is not None or sample.status not in SAMPLE_DELETABLE_STATUSES:
        return False
    if sample.made_at is not None or sample.shipped_at is not None:
        return False
    submitted = ((sample.source_context or {}).get('dingtalk_oa') or {}).get('submitted')
    return not submitted or sample.status == 'rejected'

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
    # 「交样时间」——申请表 H2 那一格。**与 `target_completion_date` 是两件事**
    # （主人 2026-10-16：「要求完成时间和交样时间不一定是一个时间」）：
    #   · `target_completion_date` = 钉钉单上的「要求完成日期」：车间**什么时候做完**；
    #   · `deliver_date`           = 申请表上的「交样时间」：**什么时候交给客户**。
    # 我原来把前者直接当后者写进 Excel，等于把两个日期合并成一个 —— 语义就错了。
    deliver_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 交样时间
    # 「打样人」——申请表 H3 那一格：**谁动手打的样**。
    # 与第 17 行那个「打样人」（打样部回复内容区，打样部在钉钉里填）不是同一格；
    # 与 `production_owner_id`（CRM 里的制作责任人，是个用户外键）也不是一回事：
    # 这里存的是**写在纸上的名字**（可能是外部师傅，CRM 里没有账号）。
    sample_maker: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 打样人
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)  # 验收标准
    # 打样费用：**空着 = 未填**，与"免费（0 元）"是两回事，所以列可空、不设默认值。
    # 之前是 NOT NULL + default 0，前端清空输入框传 null 会直接撞非空约束。
    sample_fee: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)  # 打样费用
    production_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 责任人
    # ---- 打样申请单（钉钉「定制打样审批」那份单子）上的字段 ----
    # 缘由（2026-10-15）：CRM 原来缺这些格，业务员每次要**手打一遍**再去钉钉传
    # Excel（钉钉那份《打样申请单》是公司现有表单，格式不能改）。
    # 补上这些字段后，CRM 可以**一键生成填好的申请单**，业务员下载即传。
    #
    # 这四项与钉钉模板一一对应（选项就是钉钉下拉里的那几项，不要自造）：
    sample_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 打样类型
    product_category: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 打样产品大类
    maker_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 样品制作单位
    # 打样积分：口径是**师傅用时 1 小时 = 10 积分**（钉钉表上就这么写的）。
    # 为什么存两个字段而不是只存积分：积分是**派生值**，存了工时才能解释
    # "这 30 分是怎么来的"；将来口径若从 ×10 改成别的也能重算。
    # 申请时填的是**预计**工时（与钉钉表单位置一致：积分格就在申请单上）。
    estimated_hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)  # 预计工时
    craft_points: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)  # 打样积分
    # 申请单上的三个勾选项（存中文原文，与钉钉单据上的字面一致，便于肉眼对账）
    sample_purpose: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 打样目的
    urgency: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 紧急程度
    sample_situation: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 样品情况
    # ---- 「打样具体要求」那 6 格 ----
    # ⚠️ 与明细上的 `material`(材质) / `craft`(工艺) **不是一回事，别合并**：
    #   明细那两项是**逐行**的短值（一单两个盒子可能不同），是报价/生产用的结构化数据；
    #   这 6 项是**申请单上整段的文字要求**（要填进 Excel 的 B8~B13 六个格），
    #   内容是自由文本、面向车间阅读。
    # 为避免业务员两遍录入，前端在填这 6 格时**默认带出明细里的材质/工艺**，允许改。
    material_spec: Mapped[str | None] = mapped_column(Text, nullable=True)  # 主要材质和规格
    product_size: Mapped[str | None] = mapped_column(Text, nullable=True)  # 产品尺寸
    printing: Mapped[str | None] = mapped_column(Text, nullable=True)  # 产品印刷
    accessories: Mapped[str | None] = mapped_column(Text, nullable=True)  # 主要辅料
    packaging: Mapped[str | None] = mapped_column(Text, nullable=True)  # 包装要求
    craft_requirement: Mapped[str | None] = mapped_column(Text, nullable=True)  # 做法要求
    # 导出申请单时「产品名称」用哪个（B2/G2 那格）：
    # 明细可以一行多样（名称不同），而申请单只有一格 —— 所以**显式存一份**，
    # 不让导出时去猜"取第一条还是拼起来"。空 = 回退用第一条明细的名称。
    product_name: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 申请单上的产品名称
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
    #
    # **NULL 与空列表是两件事**（R06，2026-10-06 修）：
    #   NULL   = 这一单是在本字段上线**之前**制完的 —— 「历史未知」，不可考；
    #   []     = 登记的人**明确没选**任何依据 —— 一次真实的判断；
    #   [...]  = 指定的那几份。
    # 读出时不要用 `or []` 把 NULL 抹成空列表，那等于替老数据下结论。
    # 判据见 `service.basis_state`。
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

    # ---- 软删（2026-10-15）----
    # 这个模块**原来刻意不做软删**（理由见 `service.py` 里那两处注释：打样单是留痕单据）。
    # 但用起来有个死角 —— 误建的、或者刚建就发现选错客户的单子**一点办法都没有**，
    # 只能永远挂在列表里（主人 2026-10-15：「样品管理没有删除吗，就一直待在那？」）。
    #
    # 补上删除，但**收了闸**：只有还没走流程的（`pending` / `rejected`）能删；
    # 已批准 / 已寄出 / 已签收的不给删 —— 那几张连着审批轮次、车间依据和图纸，
    # 删掉等于把追溯链弄断（与「已转订单的报价不能删」是同一个道理）。
    #
    # **软删，不是物理删**：行还在、审计还在；列表默认看不见，需要时按「已删除」
    # 筛出来还能恢复（对齐客户回收站的做法）。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
