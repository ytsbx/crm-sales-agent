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
    __table_args__ = (Index("ix_sales_targets_period_user", "period", "user_id"),)

    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    # 为空 = 全公司目标；否则为该业务员的目标
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_customer_target: Mapped[int] = mapped_column(Integer, default=0)
    sales_target: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
