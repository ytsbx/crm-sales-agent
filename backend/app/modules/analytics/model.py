"""目标管理（领导模块⑧）：销售目标表。

- 一行 = 某月某对象（user_id 为空 = 全公司）的目标：新客户数 + 销售额；
- 实际值不落这里：销售额从订单实时算、新客户从客户档案实时算——
  将来接聚水潭只换"实际值"的数据源，目标表不动；
- "老客户增长"口径未定（D 类待领导拍板），先不设列，定了再加。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType


class SalesTarget(Base, IdMixin):
    __tablename__ = "sales_targets"
    __table_args__ = (
        Index("ix_sales_targets_period_user", "period", "user_id"),
        # 团队目标按月查（文档 §六 :121）
        Index("ix_sales_targets_department", "department_id"),
        # 作用域唯一（第三批 §4.1.2）：同一期间同一作用域只允许一条活行，
        # 否则 "month-user_id" 键冲突时先查到哪条算哪条。
        # `NULLS NOT DISTINCT`（PG15+）：user_id / department_id 都可空，
        # 默认把 NULL 视为互不相同会让"全公司目标"插出无数行。
        # 与迁移 f6c2e8a4b1d9 里的同名索引保持一致，别只写在一边。
        Index(
            "uq_sales_targets_scope",
            "period",
            "user_id",
            "department_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            r"period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_sales_targets_period"
        ),
        CheckConstraint(
            "new_customer_target >= 0 AND sales_target >= 0 AND repeat_customer_target >= 0",
            name="ck_sales_targets_nonneg",
        ),
        # 个人目标与团队目标互斥：同时有值就说不清这条到底是谁的目标
        CheckConstraint(
            "NOT (user_id IS NOT NULL AND department_id IS NOT NULL)",
            name="ck_sales_targets_owner",
        ),
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


class BasisSnapshot(Base):
    """口径基准快照：老客池与首次成交日**按年冻结**（第三批 §4.1.5）。

    为什么要冻结：这两份基准原来每次都从**可变的订单状态**现算，于是事后取消一张
    往年订单，会让客户从整年老客池里消失、首次成交月往后跳——去年的数今年再看就变了，
    而且没法复现当初那一版。

    冻结边界（保守）：**过去年份**第一次被读取时算一次并落库，之后一直用快照；
    **当年**照旧实时算、不冻结（数据还在产生，冻了会冻在半路上）。
    管理员可用 `POST /analytics/sales-targets/bases/refreeze?year=` 重算某一年，写审计。

    一行一年，只存指标真正需要的两份数据（不整库快照）：
    - `veteran_customer_ids`：年初之前已有非取消订单的客户 id；
    - `first_deal_month`：首次成交落在该年的客户 → `YYYY-MM`。
    """

    __tablename__ = "analytics_basis_snapshots"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    veteran_customer_ids: Mapped[list] = mapped_column(JSONType, default=list)
    first_deal_month: Mapped[dict] = mapped_column(JSONType, default=dict)
    #: 冻结时用的口径版本：与 `target_bases`/`targets` 的版本号同一套，
    #: 口径改了就能看出这份快照是哪一版算出来的
    metric_basis_version: Mapped[str] = mapped_column(String(64))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    computed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ActualSnapshot(Base):
    """期间实绩快照：结账后把「这个月每个人的完成额」抄一份存档（第三批 §4.1.5 后半）。

    **为什么需要它**：报表原来是**每次打开现算**的。客户今年退掉去年的一张单，
    去年那一期的数字就跟着变小——年底发奖金、做总结、给领导查数拿的都是"当时那份报表"，
    以后再看却变了，对账永远对不上。这跟基准快照（老客池/首次成交，已按年冻结）
    是同一类问题，只是当初冻了一半，金额这一半没冻。

    **冻结边界（保守，与 BasisSnapshot 同一套思路）**：
    - 只冻**已经过完的期间**——当月不冻，数据还在产生，冻了等于冻在半路上；
    - 冻结是**显式动作**（结账时点由人定），不是"第一次读就偷偷冻"：
      什么时候算结完账，只有人知道；
    - 重算必须带原因、写审计（谁在什么时候、为什么改了历史数字）。

    **一行 =（期间 × 作用域 × 指标）**。作用域用字符串键
    （`company` / `dept:3` / `user:5`）而不是两个可空列：复合主键里的 NULL
    在唯一性上有坑——PG 里 NULL 互不相等，同一期间能插出无数行"全公司"。

    已声明的边界（**返工单第 4 条之后已补齐**）：汇总值冻在这里，
    构成它的**明细条目**冻在 `ActualSnapshotItem` 里——两者同一次结账写入，
    所以"点开明细加起来的数"永远等于这一行的汇总值。
    老实现只冻汇总、明细实时算，退货或取消订单之后两边就对不上了。
    """

    __tablename__ = "analytics_actual_snapshots"

    period: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM
    scope_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    metric: Mapped[str] = mapped_column(String(24), primary_key=True)
    actual_value: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 冻结时用的口径版本：口径改了能看出这份快照是哪一版算出来的
    metric_basis_version: Mapped[str] = mapped_column(String(64))
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    frozen_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 重算原因（首次冻结留空）
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ActualSnapshotItem(Base, IdMixin):
    """实绩快照的**明细条目**：结账时把"这几笔分别是哪张单"一起抄下来（返工单第 4 条）。

    为什么必须和汇总一起冻：结账之后客户退掉一张单，汇总还是结账那天的数（对），
    可明细是实时算的，点开会少一笔（对不上）。报表上"合计"和"点开看明细"
    是同一个数的一体两面，一个冻一个不冻，等于自相矛盾。

    存的是**当时的快照**，不是指针——金额、归属人、时间都是结账那一刻的值：
    - 金额：订单后来被改价、退货，这里不变；
    - `owner_id`：**当时的业绩归属人**（签单归属）。交接之后新负责人再看这张单，
      这里仍是老签单人，与汇总（也是按签单归属）一致；
    - `at_text`：已经格式化好的可读时间字符串（`2026-02-03 09:30`）。
      存字符串而不是时间戳，是因为"冻结"的意义就是连展示都钉住，
      也避免 date 与 datetime 两种类型混在一列里。

    一行 =（期间 × 指标 × 单据）。同一张订单在不同指标下（签单 / 老客净额）
    会各存一条，因为它们是两个指标各自的构成明细。
    """

    __tablename__ = "analytics_actual_snapshot_items"
    __table_args__ = (
        # 下钻永远是"某期间 + 某指标（+ 某些归属人）"，按这个顺序建索引
        Index("ix_actual_snapshot_items_lookup", "period", "metric", "owner_id"),
    )

    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    #: 与 `targets.DRILLDOWN_METRICS` 的键一致：signed / shipped / received /
    #: new_customer / repeat_net
    metric: Mapped[str] = mapped_column(String(24))
    #: order / shipment_batch / payment / customer
    record_type: Mapped[str] = mapped_column(String(24))
    record_id: Mapped[int] = mapped_column(BigInteger)
    #: 当时的业绩归属人（可为空：无归属的订单）
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 展示用标签：订单号 / 批次号 / 客户名
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 业务时间，已格式化（见类文档）
    at_text: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metric_basis_version: Mapped[str] = mapped_column(String(64))
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    frozen_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


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
