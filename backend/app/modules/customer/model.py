"""客户与联系人（对齐 02-ER §5）。

相对文档的补充：
- `customers.pool_status`：区分"私海 / 公海"，避免只靠 owner_id IS NULL 判断
  （06-需求澄清清单第 1-8 条的缺口）；
- `customers.level` 用 A/B/C/D，等级定义待业务确认。
- `tags` / `customer_tags` / `customer_merge_logs`：02-ER §5 要求的三张表，
  补齐客户标签与客户合并（查重打分早已完成，但没有"合并"这个后续动作）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType, TimestampMixin


class Customer(Base, IdMixin, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_owner_status", "owner_id", "status"),
        Index("ix_customers_name", "name"),
    )

    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 企业 / 个人
    country: Mapped[str | None] = mapped_column(String(64), default="中国")
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tax_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active / lost / disabled
    pool_status: Mapped[str] = mapped_column(String(16), default="private")  # private / public
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_followup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 最近有效联系时间**未知**（历史导入没给、且没有其它可信来源）。
    #:
    #: 为什么不能只靠"两个时间都是空"来判断（第七批 7.5，用户 2026-10-06 确认口径）：
    #: 历史名单导进来的老客户，`created_at` 是**导入当天**，而真实联系时间可能是
    #: 三年前甚至压根没有。原来 `_last_active_at` 在两者都空时回退建档时间，
    #: 于是这批客户进系统后一律"今天刚联系过"，冷落预警与公海回收都要等一整个
    #: 周期才可能触发 —— 上线第一个月等于没有预警。
    #: 现在的口径是「先标记未知，补核后才进自动回收候选」：
    #:   · 导入未提供联系日期 → 置 True，**不参与自动回收扫描**；
    #:   · 之后记录一次真实跟进、或在回收预告页补核联系时间 → 置 False，恢复参与。
    #: 与 `last_followup_at IS NULL` 的区别正在这里：那是"从没跟进过"，
    #: 这是"我们不知道他上次联系是什么时候"，后者不能当成刚刚联系过。
    last_contact_unknown: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # 最近业务进展时间（文档 §2.3/§11.2）：报价/打样/下单/回款等真实业务动作刷新，
    # 与"最近有效联系时间"（手工跟进写 last_followup_at）分开记。
    # 冷落扫描与公海回收看两者取新——避免在履约客户只因没点"记录跟进"被判冷落/回收
    last_progress_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_followup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Contact(Base, IdMixin, TimestampMixin):
    __tablename__ = "contacts"
    __table_args__ = (Index("ix_contacts_customer", "customer_id"),)

    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    wechat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_userid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerOwnerHistory(Base, IdMixin):
    __tablename__ = "customer_owner_history"

    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    old_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Tag(Base, IdMixin, TimestampMixin):
    """客户标签（02-ER §5 tags）。

    `type` 用来分组（例如「行业」「等级」「渠道」），前端按 type 分栏展示。
    标签是受控字典而不是自由文本，否则会出现「华东」「华东区」「华东大区」这类脏数据。
    """

    __tablename__ = "tags"
    __table_args__ = (Index("ix_tags_type_status", "type", "status"),)

    name: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(32), default="custom")
    status: Mapped[str] = mapped_column(String(16), default="active")
    sort_no: Mapped[int] = mapped_column(BigInteger, default=0)


# 02-ER §5：customer_tags 是 customer_id + tag_id 的关联表
customer_tags = Table(
    "customer_tags",
    Base.metadata,
    Column("customer_id", BigInteger, ForeignKey("customers.id"), primary_key=True),
    Column("tag_id", BigInteger, ForeignKey("tags.id"), primary_key=True),
)


class CustomerMergeLog(Base, IdMixin):
    """客户合并留痕（02-ER §5 customer_merge_logs）。

    合并是不可逆操作，必须记清楚"谁把哪个并进了哪个、当时两边长什么样"。
    `merge_snapshot` 存被合并方的完整快照，万一合错了还能人工还原关键字段。
    """

    __tablename__ = "customer_merge_logs"
    __table_args__ = (
        Index("ix_customer_merge_logs_source", "source_customer_id"),
        Index("ix_customer_merge_logs_target", "target_customer_id"),
    )

    source_customer_id: Mapped[int] = mapped_column(BigInteger)
    target_customer_id: Mapped[int] = mapped_column(BigInteger)
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    merge_snapshot: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # 迁移了哪些关联对象，便于事后核对
    moved: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    #: 冲突是怎么处理的（返工单 6.8）：例如 `{"customer_price": "keep_target"}`
    #: 表示两边同一个 SKU 定了不同价、当时选择保留目标客户的价格。
    #: 事后有人问"这个价为什么变成这样"，答案在这里而不在某个人的记忆里。
    conflicts: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


#: 撞单裁定的结论（文档 §11.4 验收 20）。
#:
#: 系统只负责**摆证据**，归属由人定：常见纠纷是"这条线索到底算谁的"，
#: 让代码按建档先后自动判，等于把抢单结果交给数据库的时间戳。
DECISION_LABEL: dict[str, str] = {
    "keep_both": "判为不同客户，各自保留",
    "assign_existing": "归已有客户的负责人",
    "assign_new": "指定负责人",
}


class CustomerDuplicateCase(Base, IdMixin):
    """撞单裁定单（文档 §11.4 验收 20：历史导入与现有客户撞单）。

    查重打分早就有（`find_duplicate_customers`），合并接口也有，
    缺的是中间那一环：**疑似之后由谁来定**。此前导入遇到疑似是"跳过并报告"，
    业务拿到的是一行文字提示，没有可跟进的待办，也没有留下"谁定的、依据是什么"。

    两条纪律写进表结构里：
    - `evidence` 存**当时**的匹配证据快照：事后回看要能还原"当初凭什么提示"，
      而不是拿今天的数据解释昨天的判断；
    - `resolved_owner_id` 只由人填：**没有默认值、没有按建档时间自动推导**。
    """

    __tablename__ = "customer_duplicate_cases"
    __table_args__ = (
        Index("ix_customer_dup_status", "status"),
        Index("ix_customer_dup_customer", "customer_id"),
        # 同一对客户**最多一张未决案件**（返工单 6.5）。
        # 原来只有"先查有没有、再插入"，两个并发的查重会各插一条。
        # 用**表达式索引**把 A/B 与 B/A 归一成同一对（LEAST/GREATEST），
        # 并且只约束未决的（`status='pending'`）——结案后允许再开新的，
        # 那时是新一轮争议，不该被历史挡着。
        # 与迁移里的同名索引保持一致，别只写在一边。
        Index(
            "uq_customer_dup_pending_pair",
            text("LEAST(customer_id, candidate_id)"),
            text("GREATEST(customer_id, candidate_id)"),
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    #: 新导入/新建的那条
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    #: 库里已有的疑似同一条
    candidate_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: 匹配证据快照（命中哪些字段、各自说明了什么）
    evidence: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    #: 来源：import（批量导入）/ create（建档）/ manual（人工发起）
    source: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: 裁定后的归属负责人（人为指定）
    resolved_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 裁定**前**两条客户的归属（`{客户id: 原负责人id}`）。
    #: 与 `resolved_owner_id`（裁定后）配成一对 —— 事后回看要答得出
    #: "这次裁定把谁从谁手里改到了谁名下"，只留一个结果是不够的（返工单 6.5）。
    before_owners: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
