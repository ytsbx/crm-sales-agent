"""合同与月结协议：模板版本 + 文档台账（CRM 完整实现方案 §3.6/场景14）。

设计口径：
- 模板一行一个**版本**：改模板=新增版本，旧版本永远可追溯（生成文件钉死版本）；
- 文档保存**生成时数据快照**（content_snapshot/filled_data）——客户资料之后改了，
  已生成的合同原文不变（§四："历史已发送/已签署版本不原地重算"）；
- 待签草稿（draft）/已签署（signed）/已作废（void）状态分明，签署件走
  通用附件挂载（business_type="contract"），下载不等于已签，签了才算签；
- 电子签章不是前置：线下签=上传扫描件登记。
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

DOC_TYPE_LABEL = {
    "contract": "销售合同",
    "monthly": "月结协议",
}

DOC_STATUS_LABEL = {
    "draft": "待签草稿",
    "signed": "已签署",
    "void": "已作废",
}


class ContractTemplate(Base, IdMixin):
    __tablename__ = "contract_templates"

    doc_type: Mapped[str] = mapped_column(String(16), default="contract")  # contract/monthly
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    body: Mapped[str] = mapped_column(Text)  # 模板正文，{{customer.name}} 等占位符
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContractDocument(Base, IdMixin):
    __tablename__ = "contract_documents"

    doc_no: Mapped[str] = mapped_column(String(32), unique=True)
    doc_type: Mapped[str] = mapped_column(String(16), default="contract")
    title: Mapped[str] = mapped_column(String(200))
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sales_orders.id"), nullable=True)
    quote_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    template_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contract_templates.id"))
    # 生成时快照：正文与填入值分开存，可解释"哪个空是系统填的、哪个是人填的"
    content_snapshot: Mapped[str] = mapped_column(Text)
    filled_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/signed/void
    # 月结协议到期日：到期前按设置提前量提醒负责人（调度自动任务）
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 补充协议/续签指向被补充的文档；历史原件不改写
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("contract_documents.id"), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
