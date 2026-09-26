"""系统配置与业务规则。

06-需求澄清清单指出：文档里有 `customer-levels`、`numbering-rules`、`dictionaries`、
`settings`、`public-pool/rules` 这些接口，但 ER 里没有对应表。
这里用「一张通用配置表 + 两张规则表」把它补上，避免每加一个配置就加一张表。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, func
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
