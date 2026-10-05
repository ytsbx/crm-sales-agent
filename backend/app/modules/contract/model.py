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

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
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
    """模板一行一个版本。

    版本号是**永久标识**，所以 (类型, 名称, 版本) 上加唯一约束：
    - 「第 2 版」永远只能指向同一份正文。没有这个约束，两个管理员同时新建同名
      模板会双双算出 v2 并各插一行，历史文件钉死的"模板 v2"就有两个真相；
    - 约束也是并发创建的最后一道防线（见 service.create_template 的重取逻辑）。
    """

    __tablename__ = "contract_templates"
    __table_args__ = (
        UniqueConstraint("doc_type", "name", "version", name="uq_contract_template_version"),
    )

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
    # 合同钉死的报价**版本**：报价可以出 V2、V3，但这份合同依据的是哪一版必须可证。
    # 只记 quote_id 的话，事后只能看出"关联了这张报价单"，无法说明金额与条款依据的是
    # 哪一版——报价后来改过价，合同上写的金额到底有没有依据就成了扯不清的事。
    # 生成后固定，不因报价更新而漂移（要改就重新生成或走补充协议）。
    quote_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quote_versions.id"), nullable=True
    )
    template_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contract_templates.id"))
    # 生成时快照：正文与填入值分开存，可解释"哪个空是系统填的、哪个是人填的"
    content_snapshot: Mapped[str] = mapped_column(Text)
    filled_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/signed/void
    # 月结协议到期日：到期前按设置提前量提醒负责人（调度自动任务）
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 协议生效日。续签时必填（业务口径 2026-10-05：续签要明确新协议何时生效），
    # 否则处理不了「提前签、未来才生效」——那种情况下旧协议还得继续适用一段。
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 补充协议/续签指向被补充的文档；历史原件不改写
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("contract_documents.id"), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 生成时落盘的那份 PDF（生成稿）。有它才能保证"同一个编号，任何时候下载都是同一份内容"。
    # 此前是每次下载都拿当前资料重新渲染：客户名改了、公司名改了，同一份合同前后两次
    # 下载的抬头就不一样——对外文件出现这种事说不清。现在下载走存下来的原件；
    # 历史数据（本字段为空的老合同）才回落到实时渲染，见 router.download_document。
    generated_file_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 生成请求的幂等键：前端打开生成弹窗时生成一个，重复点击 / 网络重试带的是同一个。
    # 后端据此把第二次请求认成"这份刚才已经生成过了"，返回原来那份，
    # 而不是台账上多出一份内容完全相同的草稿。
    request_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
