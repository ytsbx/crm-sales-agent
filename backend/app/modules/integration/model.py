"""外部系统映射与集成日志。

`external_mappings` 让「每接一个系统就往业务表加一堆字段」这件事不再发生：
ERP/MES 的订单号、物料号统一记在这里，业务表只保留自己的主键。

关于第八批 §8.11 的"可信事件 / 推送请求状态"：本轮**不加表也不加列**
（交接约定不动 alembic/versions），先用 `integration_logs` 现有的
`status` + `request_data` / `response_data`(JSONB) 承载：
  · 推送请求状态 = status（sending / unknown / failed / not_sent / success）；
  · 事件键与处理状态 = response_data 里的 event_key / processing_status，
    按事件键查重用 JSONB 路径表达式（`response_data['event_key']`）。
这条路能用、能查，但每次查重都要扫 JSONB。**建议的迁移**见交接回报：
给 `integration_logs` 加 `event_key`（String(64)，可空）与
`integration_type+event_key` 的部分唯一索引，并把历史行的 event_key 回填。

## 第八批 §8.13 / §8.14 新增的承载表（本节以下）

§8.13 要的是「先只读采集外部订单/发货/售后原始事实，再匹配、再对账」；§8.14 要的是
「每个 SKU 关键字段的来源、更新时间、外部身份与人工确认版本」。这两件事都属于
**外部集成**，所以共同的基础设施（水位、原始事实、对象映射、来源台账、对账批次、
差异队列）统一放这里，而不是塞进 erp/ 或 product/ 之一，避免两边各造一套。

它们的关系是：

    external_sync_watermarks   一个 (系统, 店铺, 对象类型) 一条：采到哪了、断在哪
    external_records           采到的原始事实（单据一行，明细也是一行，用 parent_key 挂）
    external_object_mappings   (系统, 店铺, 对象类型, 外部编号) → 本地对象；internal_id 为空 = 待匹配
    external_source_registry   来源/店铺的**核实与授权**状态（默认未核实）
    reconciliation_runs        一次对账批次（run_key 幂等，重复跑不重复出差异）
    integration_diffs          统一差异/待确认队列（对账差异 + SKU 主数据差异）

**刻意不做的事**：`external_records` 只存外部事实，不写回款/应收/业绩；
差异必须经人工核定（`integration_diffs` + 带审计的确认动作）才可能影响本地。
"外部售后事实未经核定不得覆盖本地回款"这条纪律就落在这里。

真实字段名、签名、店铺授权都还没拿到（交接说明 §0.3 第 5 条），所以这些表承载的是
**规范化后的记录**：`payload` 原样子报文由适配器按官方资料填充，本轮不猜字段名。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType, TimestampMixin


class ExternalMapping(Base, IdMixin):
    __tablename__ = "external_mappings"
    __table_args__ = (
        Index("ix_external_mappings_lookup", "system_type", "business_type", "internal_id"),
        Index("ix_external_mappings_external", "system_type", "external_id"),
        # 防"重复关联"（第八批 §8.11）。两边的口径都要唯一，缺哪条都能造出矛盾状态：
        #   · 同一 CRM 对象在一个外部系统里只能有一行映射 —— 否则"这张单推没推过"
        #     取决于读了哪一行，推送幂等就会漏；
        #   · 同一个外部单号只能落到一个 CRM 对象上 —— 否则回调按外部单号定位会命中两单。
        # ⚠️ 这两条唯一约束的**迁移**由负责人统一写（本轮约定：不动 alembic/versions）。
        # 加约束前必须先查历史重复行：老代码没有约束，重复关联是可能的，
        # 直接建约束会失败在生产库的升级步骤上。
        UniqueConstraint(
            "system_type",
            "business_type",
            "internal_id",
            name="uq_external_mappings_internal",
        ),
        UniqueConstraint(
            "system_type",
            "business_type",
            "external_id",
            name="uq_external_mappings_external",
        ),
    )

    system_type: Mapped[str] = mapped_column(String(32))  # ERP / MES / WECOM / LOGISTICS
    business_type: Mapped[str] = mapped_column(String(32))  # order / sku / customer
    internal_id: Mapped[int] = mapped_column(BigInteger)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IntegrationLog(Base, IdMixin):
    __tablename__ = "integration_logs"

    integration_type: Mapped[str] = mapped_column(String(32))
    #: 厂商/适配器名（如「聚水潭」），**只用于显示**。
    #: 与 integration_type 分开是刻意的：类型是稳定的口径（查询按它），
    #: 厂商名会随接入的 ERP 变。此前只有 integration_type，写入存的是厂商名、
    #: 查询却按 '%ERP%' 匹配 —— 两边靠约定对齐，结果聚水潭的日志一条都查不到。
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str] = mapped_column(String(16))  # outbound / inbound
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    response_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="success")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ==================================================================== §8.13


class ExternalSyncWatermark(Base, IdMixin, TimestampMixin):
    """一次只读采集的**水位与断点**（§8.13 分段一）。

    一个 (系统, 店铺, 对象类型) 一行。三个字段各管一件事，缺一个都会出错：

      · `watermark` —— 增量水位，上次采到哪（对方口径的更新时间/游标）。
        没有它就只能每次全量重拉，分页一多就必然超时。
      · `page_token` / `page_no` —— **分页断点**。它存在的唯一理由是"中途挂了
        不能漏"：每落完一页才推进它，所以崩溃时它指向的那一页会被重拉一次，
        而重拉靠 `external_records` 的唯一键去重 —— 这就是"中断后续拉不漏、
        重复拉取不重复"的实现方式（至少一次投递 + 幂等落库）。
      · `status` + `lease_expires_at` —— **采集租约**。两个连接同时采同一范围
        会互相把水位推乱；带租约的 running 才能既挡住并发、又让"进程被 kill
        后卡在 running"可恢复（租约过期即可接管，不是永久卡死）。

    `shop_id` 用 `*` 表示不分店铺。**不能用 NULL**：唯一约束里 NULL 互不相等，
    同一范围会插出任意多行水位，断点续拉就没有唯一真源了。
    """

    __tablename__ = "external_sync_watermarks"
    __table_args__ = (
        UniqueConstraint(
            "system_type", "shop_id", "object_type", name="uq_external_sync_watermarks_scope"
        ),
        Index("ix_external_sync_watermarks_status", "status"),
    )

    system_type: Mapped[str] = mapped_column(String(32))
    shop_id: Mapped[str] = mapped_column(String(64), default="*")
    object_type: Mapped[str] = mapped_column(String(32))
    #: 增量水位（对方口径）。空 = 还没成功采过任何一页，下次从头开始。
    watermark: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 分页断点：下次从这一页继续。空 = 上一轮已经采完。
    page_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    page_no: Mapped[int] = mapped_column(BigInteger, default=1)
    status: Mapped[str] = mapped_column(String(16), default="idle")
    retry_count: Mapped[int] = mapped_column(BigInteger, default=0)
    records_collected: Mapped[int] = mapped_column(BigInteger, default=0)
    #: 最近一次失败/未接通的原因原文。**不吞**：断点续拉靠它解释"为什么停在这"。
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 采集租约到期时间。running 且租约过期 = 上一轮进程死了，可以被接管。
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExternalRecord(Base, IdMixin, TimestampMixin):
    """只读采到的**外部原始事实**（单据与明细都放这里）。

    为什么单据和明细同一张表：它们唯一的区别是"有没有父单"。用 `parent_key`
    挂上去以后，拆单（一单多次发货）、合单（多单并一次发货）、部分退换货
    都能在**行级**说清归属，汇总时按 `dedupe_key` 去重即可，不需要为明细
    另建一套表，也不需要靠"订单总号"硬扛（那正是 §8.13 点出的缺陷）。

    唯一约束 `(system_type, shop_id, object_type, dedupe_key)` 同时解决两件事：
      · **重复拉取不重复**：同一外部订单重拉命中唯一键，只更新 payload 摘要；
      · **两店同编号不串**：店铺参与唯一键，A 店的 `SO-1` 与 B 店的 `SO-1`
        是两行、两个映射，永远不会被合成一条。

    `payload` 是原样报文，`raw_digest` 是它的规范摘要：内容没变就不写库，
    内容变了才更新并记一次 `collected_count`。差异队列的"点回原始证据"
    点的就是这个 payload。
    """

    __tablename__ = "external_records"
    __table_args__ = (
        UniqueConstraint(
            "system_type", "shop_id", "object_type", "dedupe_key",
            name="uq_external_records_dedupe",
        ),
        Index(
            "ix_external_records_scope_time",
            "system_type", "shop_id", "object_type", "occurred_at",
        ),
        Index("ix_external_records_parent", "parent_key"),
        Index("ix_external_records_external", "system_type", "external_id"),
    )

    system_type: Mapped[str] = mapped_column(String(32))
    shop_id: Mapped[str] = mapped_column(String(64), default="*")
    object_type: Mapped[str] = mapped_column(String(32))
    #: 稳定去重键：主对象用外部编号；明细用 `父键#行键`。
    dedupe_key: Mapped[str] = mapped_column(String(160))
    #: 明细行的父单键（主对象为 NULL）。合单时同一批货的不同行指向不同父单。
    parent_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: 这条事实**最终归属的销售订单**稳定键。对账按它聚合。
    #: 为什么不用 `parent_key` 兼任：一张发货单可以同时发两张订单的货（合单），
    #: 那时"父单"是一个多值关系，只有落在**行**上才表达得出来（见 `reconcile.py`）。
    order_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: 业务性质：订单的 normal / 售后的 return（退货）、exchange（换货）、refund（退款）。
    #: 换货与退货必须分得开：换货不减少净交付，混进退货就会把数量算少（双算的反面）。
    kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: 明细行自己的行键（主对象为 NULL）。它 + parent_key 才是"这一行"的身份。
    line_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 对方系统的编号（受理凭据）。主对象必填，明细可空。
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: **我方**业务编码（本地订单号 / SKU 编码）：匹配本地对象靠它，不靠猜字段名。
    external_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 数量 / 金额 / 币种。**提成独立列而不是只留在 payload 里**：
    #: 对账要按它们做求和与比较，塞在 JSON 里就得每次在 SQL 里解析字符串，
    #: 而且 `"10"` 与 `10` 会算成两个不同的值。适配器负责把对方口径转成这三个列。
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    raw_digest: Mapped[str] = mapped_column(String(64))
    #: 外部业务发生时间（对账分期用它，不用我们采集的时间）。
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 对方口径的更新时间：增量水位取它的最大值。
    external_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: 被拉取过几次。大于 1 说明重复拉取被正确去重了（验收要看的数字）。
    collected_count: Mapped[int] = mapped_column(BigInteger, default=1)
    last_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ExternalObjectMapping(Base, IdMixin, TimestampMixin):
    """(系统, 店铺, 对象类型, 外部编号) → 本地对象（§8.13 分段二）。

    与旧的 `external_mappings` 的分工：旧表服务**推单**（订单主表 ↔ 外部单号），
    键里没有店铺、也撑不起"明细行 / 客户 / SKU"这几类对象。这张表是**采集侧**的
    映射台账，键里带店铺，`internal_id` 允许为空 —— 空就代表"待匹配"。

    "未知客户/SKU 可待匹配、补齐后可重放"就是靠它：采集时匹配不上先落一行
    `pending`，本地对象补建之后再跑一次匹配（replay），命中即改 `matched`，
    无需重新拉取外部数据（原始事实已经在 `external_records` 里了）。
    """

    __tablename__ = "external_object_mappings"
    __table_args__ = (
        UniqueConstraint(
            "system_type", "shop_id", "object_type", "external_id",
            name="uq_external_object_mappings_external",
        ),
        Index(
            "ix_external_object_mappings_internal",
            "system_type", "object_type", "internal_id",
        ),
        Index("ix_external_object_mappings_status", "match_status"),
    )

    system_type: Mapped[str] = mapped_column(String(32))
    shop_id: Mapped[str] = mapped_column(String(64), default="*")
    object_type: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    external_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 明细行所属单据的稳定键。**没有它就没法处理拆单/合单**：
    #: 一页明细拉回来时，只有知道每行属于哪张单，才能把"多单并一次发货"
    #: 拆回到各自的订单上去核对。
    parent_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: 本地对象 id。**空 = 待匹配**，不是"匹配到 0"。
    internal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    match_status: Mapped[str] = mapped_column(String(16), default="pending")
    #: 凭什么匹配上的（订单号 / SKU 编码 / 税号 / 人工指定）。留痕便于事后解释。
    match_basis: Mapped[str | None] = mapped_column(String(32), nullable=True)
    match_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    matched_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ExternalSourceRegistry(Base, IdMixin, TimestampMixin):
    """来源/店铺的**核实与授权**台账（§8.12 的 readiness 口径 × §8.14 的字段来源）。

    为什么必须有这张表：`readiness` 报的是"配置齐没齐"，但"来源可不可信"
    是另一件事 —— 聚水潭的签名算法、店铺授权、简道云的字段权威资料都还没拿到
    （交接说明 §0.3 第 5 条）。没有这张表，代码里就只剩两种选择：要么默认
    某个系统是权威（§8.14 明确禁止），要么每次都在注释里写"未核实"而系统里查不到。

    默认 `verified=False`（**待核实**）。只有人拿证据登记之后才变 true，
    并且记下是谁、什么时候、依据是什么；`authorization_note` 用来写清
    "店铺授权有没有到位"。这样前端/接口显示"待核实"是有数据支撑的，而不是硬编码。
    """

    __tablename__ = "external_source_registry"
    __table_args__ = (
        UniqueConstraint(
            "system_type", "shop_id", "source_kind", name="uq_external_source_registry_scope"
        ),
    )

    system_type: Mapped[str] = mapped_column(String(32))
    shop_id: Mapped[str] = mapped_column(String(64), default="*")
    #: 来源用途：collect_order / collect_shipment / collect_aftersale / sku_master / shop_auth
    source_kind: Mapped[str] = mapped_column(String(32))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    #: 核实的依据（验收报告编号、官方文档版本、截图位置……）。空 = 没证据。
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorization_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReconciliationRun(Base, IdMixin, TimestampMixin):
    """一次对账批次（§8.13 分段三）。

    `run_key` 唯一：同一个 (系统, 店铺, 期间) 重复触发**复用同一批次**，
    差异按 `diff_key` 幂等 upsert，所以"定期对账"可以放心重跑，
    不会把同一处差异记成十条。
    """

    __tablename__ = "reconciliation_runs"
    __table_args__ = (UniqueConstraint("run_key", name="uq_reconciliation_runs_key"),)

    run_key: Mapped[str] = mapped_column(String(96))
    system_type: Mapped[str] = mapped_column(String(32))
    shop_id: Mapped[str] = mapped_column(String(64), default="*")
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)  # 左闭右开 [start, end)
    status: Mapped[str] = mapped_column(String(16), default="running")
    #: 本次批次的计数（外部单数/本地单数/差异数/新增差异数……）。给人看的体检表。
    counters: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class IntegrationDiff(Base, IdMixin, TimestampMixin):
    """统一的**差异 / 待确认队列**（§8.13 对账差异 + §8.14 主数据差异）。

    为什么两个域共用一张表而不是各建一张：它们的处理动作几乎一样 ——
    列清单、看原始证据、带权限与审计地核定、留结论。建两张表就会有两套
    几乎相同的接口、两套权限判断、两套审计字段，然后其中一套慢慢腐烂。
    用 `domain` 区分，`diff_key` 保证幂等。

    `evidence` 里存**证据指针**（原始记录的稳定键）而不是只存一句结论：
    "跨期对账差异能点回原始证据"要求点开差异就能看到当时那份原始报文。
    `evidence["collect_batch_ids"]` 再把采集批次串上（批次留痕在 `integration_logs`
    里以 business_type='external_collect' 记录了），这样"这份数据是哪一轮采的"
    也答得出来。
    """

    __tablename__ = "integration_diffs"
    __table_args__ = (
        UniqueConstraint("diff_key", name="uq_integration_diffs_key"),
        Index("ix_integration_diffs_queue", "domain", "status", "id"),
        Index(
            "ix_integration_diffs_object",
            "domain", "system_type", "shop_id", "object_type", "external_id",
        ),
    )

    domain: Mapped[str] = mapped_column(String(32))
    system_type: Mapped[str] = mapped_column(String(32))
    shop_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    internal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 主数据差异才有：冲突落在哪个字段上。
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diff_type: Mapped[str] = mapped_column(String(48))
    #: 跨期对账的期间标签（如 `2026-09-01~2026-10-01`），方便按期间筛选。
    #: ⚠️ 长度按**实际标签**留足（21 字符）：SQLite 不校验 VARCHAR 长度，
    #: 原来写 String(16) 在内存用例里一路绿灯，到 PostgreSQL 才炸
    #: `value too long for type character varying(16)`。凡是"拼出来的标签"，
    #: 长度都要按最坏情况留。
    period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 双方的值。**刻意是任意 JSON**：主数据字段既有文本（单位/包装）也有数字
    #: （装箱数/重量），用 dict 会逼着调用方把 `"件"` 包成 `{"value": "件"}`，
    #: 差异清单看起来就多一层没意义的壳。
    current_value: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    incoming_value: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    diff_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="open")
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolve_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
