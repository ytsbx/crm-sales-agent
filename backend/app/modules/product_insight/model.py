"""新品洞察（文档 §3.3 产品知识库第三类：运营日常选品）。

- 记录市场来源、目标客户、产品方向、假设卖点、评估结论和负责人；
- 经评审通过后**可转成定制询价线索**（三类内容建立转换关系），
  从而接上"找开发方向"这条线；
- 价格只是**假设**：`price_assumption` 永远停留在本表，
  不会写入价格规则的任何字段（§3.3："未经确认的价格假设不会变成正式指导价"）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

INSIGHT_STATUS_LABEL = {
    "draft": "记录中",
    "under_review": "待评审",
    "approved": "已通过",
    "rejected": "已否决",
    "converted": "已转询价线索",
}

#: 市场来源候选（可管理字典扩展；这里只是给前端下拉的默认值）
INSIGHT_SOURCES = ["展会", "1688/阿里", "客户反馈", "竞品调研", "社媒", "供应商推荐", "其他"]


class ProductInsight(Base, IdMixin):
    __tablename__ = "product_insights"
    __table_args__ = (
        Index("ix_product_insights_status", "status"),
        Index("ix_product_insights_owner", "owner_id"),
    )

    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_customer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    direction: Mapped[str | None] = mapped_column(Text, nullable=True)  # 产品方向
    selling_points: Mapped[str | None] = mapped_column(Text, nullable=True)  # 假设卖点
    # 价格假设：仅内部参考，不进入任何价格规则
    price_assumption: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)  # 评估结论
    images: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # 参考图 URL 列表
    owner_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    reviewer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 转换关系：通过评审后转成的定制询价线索
    converted_inquiry_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
