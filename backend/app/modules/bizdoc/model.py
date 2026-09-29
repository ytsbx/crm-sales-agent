"""对外单据模板与已生成文件（文档 §四「对外模板及生成文件」）。

为什么单独一层，而不是塞进 contract 模块：

- 合同走的是**签署台账**（草稿→上传签署件→作废），一份合同只有一条主记录，
  状态是它的一部分；
- 打样需求单与下单文件是**生成即留档**的对外单据，同一张来源单据可能出很多份
  （改了数量再出一份给工厂），所以"版本"独立成行，旧文件一个字不动。

文档 §四 要求这个对象能回答三件事：模板类型与版本、单据版本与输入快照、
文件及校验值。下面两张表就是这三件事的落点。语义与 contract 一致的部分
（留档不覆盖、下载 ≠ 生效）刻意保持一致，不另创一套说法。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

#: 单据类型。新增类型时：这里加一项 + service 里加一段字段组装 + seed 默认模板。
DOC_TYPE_LABEL = {
    "sample_request": "打样需求单",
    "order_sheet": "下单文件",
    #: 对客报价单（文档 §3.5「统一的 Excel 模板嵌入系统」/ 场景10）：
    #: 与打样单、下单文件共用同一套台账（模板版本 + 输入快照 + 校验值 +
    #: 旧文件不覆盖），区别只在出图的格式是 xlsx
    "quote_sheet": "对客报价单",
}

#: 各类型出图格式。报价单是客户要拿去改/填的表，必须是 Excel；
#: 打样单与下单文件是给车间/客户的正式文件，PDF 更合适。
DOC_TYPE_FORMAT = {
    "sample_request": "pdf",
    "order_sheet": "pdf",
    "quote_sheet": "xlsx",
}

DOC_STATUS_LABEL = {
    "active": "有效",
    "void": "已作废",
}


class BizDocTemplate(Base, IdMixin):
    """对外单据模板，**同类型多版本并存**。

    这是"改模板不改变历史文件"的依据：生成时把 template_version 抄进文件行，
    之后模板改了，已生成的文件仍然指向它当时用的那一版。
    """

    __tablename__ = "biz_doc_templates"
    __table_args__ = (
        # 同一类型下版本号唯一；不同类型各自从 1 起
        UniqueConstraint("doc_type", "version", name="uq_biz_doc_template_version"),
    )

    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    #: 说明/条款文字，支持 {{customer.name}} 这类 token（与合同模板同一套渲染）
    body: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BizDoc(Base, IdMixin):
    """已生成的对外单据（对外模板及生成文件里的"文件"部分）。

    **旧文件不覆盖**（场景12）：重新生成不 UPDATE 旧行，而是插一行
    version+1、parent_id 指向前一版。所以"客户改完数量再出一份给工厂"之后，
    之前发出去的那一份仍然原样躺在库里、仍然下载得到。

    明细与差异都在 `input_snapshot` 里：文件渲染只读快照，不实时回查业务表——
    否则客户资料一改，历史文件的正文就跟着变了。
    """

    __tablename__ = "biz_docs"
    __table_args__ = (
        Index("ix_biz_docs_customer", "customer_id"),
        Index("ix_biz_docs_sample", "sample_request_id"),
        Index("ix_biz_docs_order", "order_id"),
    )

    doc_no: Mapped[str] = mapped_column(String(32), unique=True)
    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200))
    #: 同一张业务单据的第几份（重新生成递增）
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")

    #: 业务对象
    #: 负责人（生成时从业务对象带过来）：对外单据的可见性跟着"活"走，
    #: 与订单/样品列表同一口径，不另立一套
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sample_request_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inquiry_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quote_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    #: **来源单据及版本**（场景12：新单保留来源）。生成时的来源，之后不跟着变。
    source_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    template_id: Mapped[int] = mapped_column(ForeignKey("biz_doc_templates.id"))
    template_version: Mapped[int] = mapped_column(BigInteger, default=1)

    #: 输入快照：渲染文件的唯一数据源（明细、差异、条款都在里面）
    input_snapshot: Mapped[dict] = mapped_column(JSONType, nullable=False)
    #: 校验值：见 service._content_hash 的说明（为什么不是 PDF 字节的哈希）
    content_sha256: Mapped[str] = mapped_column(String(64))

    void_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
