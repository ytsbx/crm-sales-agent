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
    text,
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
    #: 草稿（第八批 §8.8）：模板变量没解析出来时只允许出草稿——
    #: 它**不进正式台账**（不可作废、纸面上写明不是正式对外文件），
    #: 必须与"有效件"在列表和文件上都一眼分得开。
    "draft": "草稿（非正式对外文件）",
    "void": "已作废",
}

#: 存档状态（第八批 §8.10）。三档必须分得开，否则用户拿到的到底是"当初发出去
#: 的那一份"还是"现在按快照重出的一份"就永远说不清：
#: - archived：生成时就把字节存下来了，下载读的就是这一份；
#: - legacy  ：迁移前的历史文件，**当初没有存档**，只能按快照重建——
#:             重建件在纸面上写明"由历史快照重建"，不伪称是当时的原件；
#: - missing ：本该有存档，但文件不见了或校验值对不上（告警，不静默替代）。
ARCHIVE_ARCHIVED = "archived"
ARCHIVE_LEGACY = "legacy"
ARCHIVE_MISSING = "missing"

ARCHIVE_STATUS_LABEL = {
    ARCHIVE_ARCHIVED: "存档原件",
    ARCHIVE_LEGACY: "由历史快照重建（无存档原件）",
    ARCHIVE_MISSING: "存档原件缺失或校验不符",
}

#: 各类对外单据的查看/维护权限。**不把 order:view 当成所有单据的总开关**（§8.5）：
#: 只有报价权限的人也要能下载自己出的报价单，只有订单权限的人不该拿到打样单。
DOC_TYPE_VIEW_PERMISSION = {
    "sample_request": "sample:view",
    "order_sheet": "order:view",
    "quote_sheet": "quote:view",
}

DOC_TYPE_MANAGE_PERMISSION = {
    "sample_request": "sample:manage",
    "order_sheet": "order:manage",
    "quote_sheet": "quote:manage",
}


def permitted_view_doc_types(user) -> list[str]:
    """当前用户有权查看的单据类型（管理员=全部）。

    列表与详情/下载用**同一个判据**，避免"列表看得见、点开 403"
    或者"列表过滤了、直查 id 却拿得到"这类两侧不一致。
    """
    if "admin" in getattr(user, "roles", []):
        return list(DOC_TYPE_VIEW_PERMISSION)
    return [
        doc_type
        for doc_type, code in DOC_TYPE_VIEW_PERMISSION.items()
        if user.has(code)
    ]


def has_doc_permission(user, doc_type: str, *, manage: bool = False) -> bool:
    if "admin" in getattr(user, "roles", []):
        return True
    table = DOC_TYPE_MANAGE_PERMISSION if manage else DOC_TYPE_VIEW_PERMISSION
    code = table.get(doc_type)
    return bool(code) and user.has(code)


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
        Index("ix_biz_docs_source_key", "source_key"),
        # **同源同版本唯一**（第八批 §8.9 的最后一道闸）。
        #
        # 为什么必须有数据库约束、而不是只在代码里"读最新版本 +1"：
        # 那个读法没有排他性——两个连接同时读到"空历史"就都会分配 V1，
        # 于是一张来源单据下出现两份 V1，parent 链断成两条。代码里的
        # 咨询锁（service._lock_bizdoc_sequence）负责让并发**排队**，
        # 这个索引负责让"万一没排上"变成一次明确的报错而不是静默的重复。
        #
        # 为什么是**部分**索引：`source_key` 为空的是迁移前无法归属来源的历史行，
        # 它们在 PostgreSQL 里彼此不相等，普通唯一索引根本挡不住它们；
        # 更重要的是不能因为"归不了属"就把历史数据判成冲突而迁不过去。
        Index(
            "uq_biz_docs_source_version",
            "doc_type",
            "source_key",
            "version",
            unique=True,
            postgresql_where=text("source_key IS NOT NULL"),
            sqlite_where=text("source_key IS NOT NULL"),
        ),
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
    order_draft_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inquiry_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quote_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    #: **来源单据及版本**（场景12：新单保留来源）。生成时的来源，之后不跟着变。
    source_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 版本序列的**共同来源身份键**（§8.9）：`sample_request:12` / `order:34` /
    #: `order_draft:56` / `quote:78`。
    #:
    #: 为什么单独存一列而不是"看哪个 id 字段非空"：版本号必须在**同一来源**内
    #: 连续，而"哪个字段非空"这个判据分散在四个可空列上——判错一次，两份不同
    #: 来源的文件就会共用一条历史链（该出 V1 的出了 V5），或者反过来永远出不了
    #: V2。把来源身份收敛成一个值，查询、咨询锁、唯一索引三处用的是同一个判据。
    #:
    #: 为空只可能是**迁移前无法归属来源的历史行**：这类行不参与同源唯一约束，
    #: 也不会被并发的版本分配当成"别人的历史"（见 service.source_key_for）。
    source_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    template_id: Mapped[int] = mapped_column(ForeignKey("biz_doc_templates.id"))
    template_version: Mapped[int] = mapped_column(BigInteger, default=1)

    #: 输入快照：渲染文件的唯一数据源（明细、差异、条款都在里面）
    input_snapshot: Mapped[dict] = mapped_column(JSONType, nullable=False)
    #: 校验值：见 service._content_hash 的说明（为什么不是 PDF 字节的哈希）
    content_sha256: Mapped[str] = mapped_column(String(64))

    # ---- 生成原件存档（第八批 §8.10）--------------------------------------
    #: 生成当时**实际产出的字节**在 files 里的编号。
    #: 为什么不能只留 content_sha256：那是对"渲染输入"算的哈希，下载时按快照
    #: 重新渲染出来的字节并不等于当时发出去的那一份——升级渲染器/字体、甚至
    #: Excel 打包时写进去的时间戳，都会让同一份快照产出不同的字节。要回答
    #: "这份文件当初发给客户的就是这个吗"，只有把当时的字节本身存下来。
    #: 为空 = 迁移前的历史文件（没有存档），下载时明确标注"由历史快照重建"。
    file_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 存档原件的**字节** SHA-256：下载前重算比对，对不上就告警，
    #: 绝不静默换成"按当前资料重新渲染"的一份（那等于伪造原件）。
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 出图程序版本（`bizdoc-pdf/1` / `bizdoc-xlsx/1`）。存它是为了解释
    #: "同一个编号为什么两次下载渲染结果不同"——升级渲染器后旧原件不变，
    #: 新生成的版本才用新渲染器，两者的这一列不一样，一眼分得出来。
    renderer_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    void_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
