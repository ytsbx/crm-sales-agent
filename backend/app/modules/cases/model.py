"""优秀业务员案例库（文档 §3.7 / 验收场景15）。

与普通文档库的区别（§3.7 原文）：
- 案例是**带结构的**：客户背景/目标/关键动作/异议处理/经过/结果/可复用做法，
  能被检索、能被培训引用；
- 主管审核后才发布；发布不制造新的业绩或跟进记录；
- 分享版脱敏：非授权人看得到做法、看不到受限客户资料（真实客户身份
  只对作者/主管/管理员可见，其余人只看到作者写的 customer_label 代称）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

CASE_STATUS_LABEL = {
    "draft": "编写中",
    "pending_review": "待审核",
    "published": "已发布",
    "rejected": "已驳回",
    #: 已被新修订版取代（§5.1.5）：内容不再改动，仍可按已发布口径阅读
    "superseded": "已被修订版取代",
}


class SalesCase(Base, IdMixin):
    __tablename__ = "sales_cases"
    __table_args__ = (
        Index("ix_sales_cases_status", "status"),
        Index("ix_sales_cases_customer", "customer_id"),
        # 列表要按"有没有被修订版取代"过滤，给它一个索引
        Index("ix_sales_cases_revision_of", "revision_of_id"),
    )

    title: Mapped[str] = mapped_column(String(200))
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    # 客户身份与对外代称分离：代称（如"某包装制品厂"）人人可见，
    # 真实客户只对作者/主管/管理员可见
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customers.id"), nullable=True)
    customer_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 检索维度（§3.7：按客户类型、产品线、阶段及问题检索）
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_line: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_reached: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 六阶段
    problem_tags: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # ["价格异议","交期紧"]
    # 七段结构化正文
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_actions: Mapped[str | None] = mapped_column(Text, nullable=True)
    objection_handling: Mapped[str | None] = mapped_column(Text, nullable=True)
    process: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 证据单据（原单据仍按各业务模块自己的权限访问，案例页只放引用）
    quote_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sample_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # ---- 修订版与审核历史（第四批 §5.1.5，口径已确认＝修订稿）----
    # 已发布案例**不允许原地改**：批准的是 A 版内容，改完变成 B 版却被复用审核结论。
    # 要改就另开修订稿重新走审核，批准后替换当前发布版；被取代的那一版转 superseded。
    version: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1", nullable=False)
    revision_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sales_cases.id"), nullable=True
    )
    #: 逐条追加的审核历史（轮次/结论/意见/审核人/时间）。
    #: 原来只有一个 `review_note` 单值，下一次审核就把它覆盖了——"被驳回过几次、
    #: 每次谁批的"事后查不出来。与打样"制作完成"的结构化事件同一套做法。
    review_history: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
