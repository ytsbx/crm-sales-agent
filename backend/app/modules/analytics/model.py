"""目标管理（领导模块⑧）：销售目标表。

- 一行 = 某月某对象（user_id 为空 = 全公司）的目标：新客户数 + 销售额；
- 实际值不落这里：销售额从订单实时算、新客户从客户档案实时算——
  将来接聚水潭只换"实际值"的数据源，目标表不动；
- "老客户增长"口径未定（D 类待领导拍板），先不设列，定了再加。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin


class SalesTarget(Base, IdMixin):
    __tablename__ = "sales_targets"
    __table_args__ = (
        Index("ix_sales_targets_period_user", "period", "user_id"),
        # 团队目标按月查（文档 §六 :121）
        Index("ix_sales_targets_department", "department_id"),
    )

    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    # 为空 = 全公司目标；否则为该业务员的目标
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 团队维度（文档 §六 :121「目标按团队、业务员、周期设置」）。
    #: 现在只有"按业务员"和"全公司"两种，中间那层团队目标无处可放。
    #: 与 user_id 互斥语义：department_id 有值 = 团队目标；两者都空 = 全公司目标。
    department_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_customer_target: Mapped[int] = mapped_column(Integer, default=0)
    sales_target: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 老客户增长目标（金额口径）。文档要求"老客增长要固定比较客户集合、周期和净额"，
    #: 所以这里是**净额**目标而不是客户数——返单客户数不反映做了多少生意。
    repeat_customer_target: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), default=0, server_default="0"
    )
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperationTiming(Base, IdMixin):
    """操作耗时埋点（文档 §六「评价操作是否省时」/ 场景18）。

    场景18 要回答的是"系统比 Excel 快多少"，而**服务端看不出这件事**：
    单据落库时间只能给出流程跨度（可能开着页面去开会，也可能隔夜），
    不代表业务员在这件事上真正花了多久。所以计时由**前端**做：
    进入流程时记起点，提交成功时把耗时报上来；服务端只做校验与聚合，不猜。

    `typed_fields` / `rework_count` 是**尽力而为**的计数（前端数）：
    - typed_fields：用户真正手输的字段数（用于回答"重复填写字段数"）；
    - rework_count：提交前退回重填的次数（用于回答"返工次数"）。
    数不准时宁可报 0 并在汇总里标注，也不要用估算值去证明"我们更快"。
    """

    __tablename__ = "operation_timings"
    __table_args__ = (
        Index("ix_operation_timings_op_user", "operation", "user_id"),
        Index("ix_operation_timings_created", "created_at"),
    )

    #: 流程标识，取值见 service.OPERATION_LABELS（白名单，不接受自由文本）
    operation: Mapped[str] = mapped_column(String(48))
    user_id: Mapped[int] = mapped_column(BigInteger)
    #: 前端从进入流程到提交成功的毫秒数
    duration_ms: Mapped[int] = mapped_column(BigInteger)
    #: 这次操作对应的单据（可选，用于事后核对是哪一单）
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    typed_fields: Mapped[int] = mapped_column(Integer, default=0)
    rework_count: Mapped[int] = mapped_column(Integer, default=0)
    #: 来源标记：web / 手工补录等，便于区分"真实试用"和"补录数据"
    source: Mapped[str] = mapped_column(String(16), default="web")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
