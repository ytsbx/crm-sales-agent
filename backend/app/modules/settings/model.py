"""系统配置与业务规则。

06-需求澄清清单指出：文档里有 `customer-levels`、`numbering-rules`、`dictionaries`、
`settings`、`public-pool/rules` 这些接口，但 ER 里没有对应表。
这里用「一张通用配置表 + 两张规则表」把它补上，避免每加一个配置就加一张表。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType


class SystemSetting(Base, IdMixin):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(), server_default=func.now(),
        nullable=False,
    )


class PublicPoolRule(Base, IdMixin):
    """公海回收规则：某等级客户多少天没跟进就回收。"""

    __tablename__ = "public_pool_rules"

    level: Mapped[str] = mapped_column(String(8))
    days: Mapped[int] = mapped_column(BigInteger, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


#: 候选的状态机。写死成一张表，避免各处 if 判断漂移。
#:
#: 为什么把"待复核"和"已执行"分成两步（返工单 6.3）：自动扫描命中后**直接清空负责人**
#: 是不可逆的 —— 一个客户可能只是业务员出差两周没点跟进，回收掉之后
#: 他辛辛苦苦跟了半年的客户就进了公海，谁都能领。文档 §11.2 要求的是
#: "先预告 → 主管复核 → 再执行"，扫描只负责**提名**。
RECYCLE_STATUS_LABEL = {
    "pending": "待复核",       # 已预告，等主管批
    "deferred": "已暂缓",       # 主管决定先不动，过一阵再看
    "approved": "已批准待执行",  # 批准了但还没落（正常路径上会立刻执行掉）
    "executed": "已回收",       # 归属已清空，客户进了公海
    "rejected": "已驳回",       # 主管认为不该回收
    "restored": "已恢复",       # 回收后又还给了原负责人
    "superseded": "已被取代",    # 同一客户又开了新的候选，这条作废
}

#: 还算"未结"的状态：同一客户同时只能有一张。
RECYCLE_OPEN_STATUSES = ("pending", "deferred", "approved")


class PublicPoolRecycleCandidate(Base, IdMixin):
    """公海回收**候选 / 预告**（返工单 6.3）。

    自动扫描命中后**不再直接改归属**，而是落一条这个 —— 它记录"为什么该回收"
    的全部依据，交给主管逐条或批量决定。批准执行时会**重新检查**最新情况
    （预告之后客户又有了新跟进、新报价、新订单、新回款，就不该再回收）。

    一行一次提名。同一客户重复扫描不会堆出多条：有未结的候选就跳过
    （靠 `uq_pool_candidate_open` 兜底，不只是应用层判断）。
    """

    __tablename__ = "public_pool_recycle_candidates"
    __table_args__ = (
        Index("ix_pool_candidate_status", "status"),
        Index("ix_pool_candidate_customer", "customer_id"),
        # 同一客户**同时只允许一张未结候选**。原来靠"先查有没有、再插入"，
        # 定时任务重跑或两个实例同时扫就会各插一条，主管看到重复的待办。
        # 部分唯一索引只在未结状态下生效 —— 结案/恢复之后能再提名。
        Index(
            "uq_pool_candidate_open",
            "customer_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'deferred', 'approved')"),
        ),
    )

    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    #: 提名那一刻的原负责人（快照）。执行时会再核对：**中途换过人就不该按老提名回收**。
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 命中的规则与等级（事后要能回答"这条是按哪条规则提上来的"）
    rule_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    rule_days: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 两个活跃时钟的快照（最近有效联系 / 最近业务进展）—— 复核时要看得到
    #: "是按哪个时间判它冷落的"，而不是只给一个"已超 N 天"
    last_contact_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_progress_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 提名那一刻的**履约保护明细快照**（`{原因: 说明}` 的列表）。
    #: 复核时谁拦着、拦的理由都摆在眼前；执行前还会再算一次，两次都要看。
    protection_snapshot: Mapped[list | None] = mapped_column(JSONType, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    #: 预告生成时间 / 预告到期时间（到期前不动，给业务员一个缓冲）
    notice_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    decided_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 主管的决定说明（驳回/暂缓/例外执行都要写清理由）
    decision_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 暂缓到什么时候（`status='deferred'` 时有值）
    deferred_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 例外执行：批准时**仍有履约保护**，主管明确要求放行（必须填原因）
    exception_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 恢复：把客户还给原负责人（保留这条记录，不删）
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restored_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    restore_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 恢复时如果客户已经被别人领走，记下"冲突"而不是硬抢（见 restore_candidate）
    restore_conflict_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskRule(Base, IdMixin):
    """自动任务规则。

    trigger_type: quote_no_followup / customer_silent / receivable_due
    """

    __tablename__ = "task_rules"
    __table_args__ = (Index("ix_task_rules_code", "code", unique=True),)

    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    trigger_type: Mapped[str] = mapped_column(String(32))
    trigger_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    action_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")


class NumberingRule(Base, IdMixin):
    """编号规则（PRD §2.6 系统管理员能力 / 03-API §36）。

    前面单号是硬编码的 `Q{YYYYMMDD}{4位}` / `SO{YYYYMMDD}{4位}`，
    且用 `count(*) + 1` 取流水 —— 删过历史单就会**重号**，并发还会撞。
    这里把规则做成数据：可配前缀、日期格式、流水位数、重置周期。
    """

    __tablename__ = "numbering_rules"
    __table_args__ = (Index("ix_numbering_rules_code", "code", unique=True),)

    # quote / order / sample 等业务对象代号
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))
    prefix: Mapped[str] = mapped_column(String(16), default="")
    # strftime 格式，例如 %Y%m%d；留空表示不带日期
    date_format: Mapped[str] = mapped_column(String(32), default="%Y%m%d")
    # 流水位数，不足补零
    seq_length: Mapped[int] = mapped_column(BigInteger, default=4)
    # none / daily / monthly / yearly —— 决定流水什么时候归零
    reset_period: Mapped[str] = mapped_column(String(16), default="daily")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class NumberSequence(Base, IdMixin):
    """取号计数器。

    每个「规则 + 周期」一行，取号时对该行 `SELECT ... FOR UPDATE` 后自增：
    并发请求会排队拿到不同的号，不会撞；计数器单调递增，
    删掉业务单据也不会让号被回收重用（这是 count(*) 方案最要命的地方）。
    """

    __tablename__ = "number_sequences"
    __table_args__ = (
        Index("ix_number_sequences_key", "rule_code", "period_key", unique=True),
    )

    rule_code: Mapped[str] = mapped_column(String(32))
    # 周期标识：daily -> 20260926，monthly -> 202609，yearly -> 2026，none -> ""
    period_key: Mapped[str] = mapped_column(String(16), default="")
    current_no: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(), server_default=func.now(),
        nullable=False,
    )


class DictionaryItem(Base, IdMixin):
    """受控词表（03-API §36 `GET/POST /dictionaries`、`/customer-levels`）。

    客户等级也放这张表（type='customer_level'），不另开一张：
    两者形状完全一样（code / label / 排序 / 启停），分成两张表只会
    让"改个等级名"和"改个分类名"走两套代码。
    """

    __tablename__ = "dictionary_items"
    __table_args__ = (
        Index("ix_dictionary_items_type_code", "type", "code", unique=True),
        Index("ix_dictionary_items_type", "type", "sort_no"),
    )

    # customer_level / product_category / customer_source / industry ...
    type: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(128))
    sort_no: Mapped[int] = mapped_column(BigInteger, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
