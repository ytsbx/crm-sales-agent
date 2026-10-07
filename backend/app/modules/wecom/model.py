"""企业微信集成数据模型（对齐 02-ER §6）。

设计要点（来自总设计文档 §5.2 / §5.3，别改坏）：

1. **企微外部联系人不等于 CRM 客户**。`wecom_external_contacts` 是企微侧的事实，
   `crm_contact_id` 为空就表示"还没归一"，必须由人在「待归一」页面确认后
   才关联到已有客户或创建新客户——不给任何自动绑定兜底。
2. **跟进关系单独建模**。同一个外部联系人可能被多个员工添加，
   一个员工也可能加了很多客户，所以是 1:N 关系表，不往联系人表塞字段。
3. **同步任务留痕**。每次同步写一条 `wecom_sync_jobs`，
   成功/失败计数与错误信息都落库，出问题能查是哪一次、拉到第几页断的。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

SYNC_JOB_TYPES = {
    "department": "部门同步",
    "user": "成员同步",
    "external_contact": "外部联系人同步",
    "follow_relation": "跟进关系同步",
    "transfer": "离职继承",
}

SYNC_JOB_STATUS = {
    "running": "进行中",
    "success": "成功",
    "partial": "部分失败",
    "failed": "失败",
}

SYNC_STATUS_LABEL = {
    "pending": "待同步",
    "synced": "已同步",
    "conflict": "有冲突",
    # 企微里已经查不到这个成员了（离职或被删）
    "missing": "已不存在",
}


class WeComUser(Base, IdMixin):
    """企微成员 ↔ CRM User 的映射（PRD §8.1）。"""

    __tablename__ = "wecom_users"
    __table_args__ = (
        Index("ix_wecom_users_userid", "wecom_userid", unique=True),
        Index("ix_wecom_users_user", "user_id"),
    )

    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    wecom_userid: Mapped[str] = mapped_column(String(128))
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 企微部门的数字 id，用逗号分隔的原始串保留，便于排查归属
    wecom_department_ids: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 1 表示已激活（在职），企微返回的是数字，这里按原样存字符串
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(16), default="synced")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class WeComExternalContact(Base, IdMixin):
    """企微外部联系人（PRD §8.2）。`crm_contact_id` 为空即待归一。"""

    __tablename__ = "wecom_external_contacts"
    __table_args__ = (
        Index("ix_wecom_external_contacts_userid", "external_userid", unique=True),
        Index("ix_wecom_external_contacts_crm", "crm_contact_id"),
    )

    external_userid: Mapped[str] = mapped_column(String(128))
    crm_contact_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("contacts.id"), nullable=True
    )
    # 归一后归属的客户，冗余一份便于"待归一"列表直接按客户过滤/展示
    crm_customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 企微口径：1 微信用户 / 2 企业微信用户
    type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)
    corp_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # 归一处理结果：pending 待处理 / bound 已关联 / created 已建客户 / ignored 暂不处理
    normalize_status: Mapped[str] = mapped_column(String(16), default="pending")
    normalized_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    normalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class WeComFollowRelationship(Base, IdMixin):
    """企微「谁加了谁」的跟进关系（PRD §8.2）。"""

    __tablename__ = "wecom_follow_relationships"
    __table_args__ = (
        Index("ix_wecom_follow_ext_user", "external_contact_id", "wecom_userid", unique=True),
        Index("ix_wecom_follow_userid", "wecom_userid"),
    )

    external_contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("wecom_external_contacts.id")
    )
    # 企微成员 userid（不是 CRM user_id），离职继承时要靠它找人
    wecom_userid: Mapped[str] = mapped_column(String(128))
    add_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    add_way: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tags_json: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    # active 跟进中 / transferred 已转交 / deleted 已删除
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WeComSyncJob(Base, IdMixin):
    """同步任务流水（API §10 的 sync-jobs / transfer 都读这张表）。"""

    __tablename__ = "wecom_sync_jobs"
    __table_args__ = (Index("ix_wecom_sync_jobs_type", "job_type", "status"),)

    job_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running")
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    success_count: Mapped[int] = mapped_column(BigInteger, default=0)
    fail_count: Mapped[int] = mapped_column(BigInteger, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 同步/转交的业务明细：拉了哪些页、跳过了哪些人、转交清单等
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


# ---- 离职继承的逐项结果（PRD §8.4）----------------------------------------
#
# 为什么要单开一张表，而不是继续往 `WeComSyncJob.detail` 这个 JSON 里塞：
#
# 1. **明细会被截断**。原实现是 `detail["wecom_failures"] = failures[:10]`，
#    而失败计数又取这个截断后列表的长度——12 条失败最终只报出来一部分，
#    而且**存进去的那一份本身就是残的**，事后想补查也查不到。JSON 只适合
#    放汇总，逐项明细必须一行一项。
# 2. **两侧结果要分开记**。一次交接有两个独立的动作：改 CRM 归属、
#    调企微转接客户关系。企微那边的调用一旦发出就去不掉（第三方系统，
#    本地事务回滚不了它），所以"CRM 成功、企微失败"是必须能表达的状态，
#    而不是一个笼统的"成功/失败"。
# 3. **重试要按项来**。整体重试会把已经转出去的关系再发一遍，
#    企微侧会报重复；按项重试才知道哪些真的还没完成。

#: 交接对象的类别（`kind`）。也是界面上分组的依据。
TRANSFER_KIND_LABEL: dict[str, str] = {
    "customer": "客户",
    "contact": "联系人",
    "opportunity": "商机",
    "task": "待办任务",
    # 打样有两个责任字段，都可能挂在离职人名下，要分开盘点、可以指定不同接手人
    # （跟单给业务员、生产给车间主管是常见分工）——
    # 此前只盘"跟单"，离职人担任**生产责任人**的单子没人接（第六批审查第 4 条）
    "sample": "打样单（跟单）",
    "sample_production": "打样单（生产）",
    "order": "销售订单",
    "order_draft": "订单草稿",
    # 报价单此前**不在清单里**（第九批 §9.4 核对时发现）：离职人名下的报价
    # 没人接手 → 接手人在报价列表里看不到它；**它生成的报价档案**更是谁也打不开
    # （文件按 `BizDoc.owner_id` 判可见性）。这一类和"订单草稿"一样，
    # 迁移时连带把生成文件的责任一起带走。
    "quote": "报价单",
    "wecom_relation": "企微客户关系",
}

#: CRM 侧的逐项结果。
#: `not_applicable` 用于"这一项没有 CRM 归属要改"——企微客户关系就是这种：
#: 它是外部系统的关系记录，不构成 CRM 里的归属，重试也不该把它算成待办。
TRANSFER_CRM_STATUS_LABEL: dict[str, str] = {
    "moved": "已交接",
    "frozen": "已冻结（撞单争议）",
    "failed": "交接失败",
    "skipped": "无需处理",
    "pending": "待交接",
    "not_applicable": "不涉及",
}

#: 企微侧的逐项结果。`not_applicable` 用于"这项没有对应的企微关系"。
TRANSFER_WECOM_STATUS_LABEL: dict[str, str] = {
    "transferred": "已转接",
    "failed": "转接失败",
    "skipped": "未转接",
    "not_applicable": "不涉及",
    "pending": "待转接",
}

#: **还没办完**的状态，重试只挑这些。
#: 注意 `frozen`（撞单争议冻结）不算在内：它不是"没做完"，而是"现在不能做"，
#: 要等主管裁定后**重新发起一次交接**，不是原地重试。
CRM_OPEN_STATUSES = ("pending", "failed")
WECOM_OPEN_STATUSES = ("pending", "failed")

#: 这些「跳过」**不是无事可做，而是对象已经被别人先动过** ——
#: 并发下同事先接手，或负责人已被重新分配，交接明明走到这一步、主动让开了。
#: 必须让人看见，不能和"客户已不存在""本次未要求转接企微关系"那种真·无需处理
#: 挤在同一个「无需处理」标签下：操作者看到「无需处理」会以为本来就不用管，
#: 实际是**有别人的操作把这一项顶掉了**（第九批复审 P1 的收尾）。
#:
#: 为什么不新增一个机器状态值：`crm_status` 只按"两侧结果"分类，重试、汇总、
#: 索引都挂在它上面；这一层是**展示口径**的细分，改了会牵动统计口径与历史数据。
#: 判据放这里集中一处，序列化时折算成 `crm_taken` 下发，前端不猜文案。
#: ⚠️ 新增这类跳过原因时，`service.py` 的调用点与这里必须同步，
#: `check_customer_handover_documents.py` 有双向对账的断言守着。
TRANSFER_SKIP_TAKEN_REASONS: frozenset[str] = frozenset(
    {
        "客户已由其他同事接手",
        "商机负责人已改，不再是离职人",
        "任务负责人已改，不再是离职人",
        "跟单责任人已改，不再是离职人",
        "生产责任人已改，不再是离职人",
        "报价负责人已改，不再是离职人",
        "草稿负责人已改，不再是离职人",
        "订单负责人已改，不再是离职人",
    }
)


class WeComTransferItem(Base, IdMixin):
    """离职继承的**逐项结果**：一行 = 一个被交接的对象。

    两侧状态分开记：`crm_status` 是 CRM 里的归属有没有改成功，
    `wecom_status` 是企微侧的关系有没有转出去。一侧成功一侧失败时，
    这一项保持"待处理"，可以单独重试，不会把已经成功的那侧再动一遍。
    """

    __tablename__ = "wecom_transfer_items"
    __table_args__ = (
        Index("ix_wecom_transfer_items_job", "job_id", "kind"),
        Index("ix_wecom_transfer_items_open", "job_id", "crm_status"),
    )

    job_id: Mapped[int] = mapped_column(
        # 级联删除：逐项结果是**某次交接的从属记录**，任务本身没了就没有留着的意义。
        # 更现实的原因是：清理历史数据时（套件/运维脚本会按条件删 sync_jobs），
        # 没有级联就会撞外键、把清理脚本整个打断，连带它自己的夹具也清不掉。
        BigInteger, ForeignKey("wecom_sync_jobs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24))
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 人看的标识（客户名 / 订单号 / 外部联系人 id），排查时不用再去反查 id
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    from_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    from_owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    crm_status: Mapped[str] = mapped_column(String(16), default="pending")
    crm_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    wecom_status: Mapped[str] = mapped_column(String(16), default="not_applicable")
    wecom_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 这一项被尝试过几次（含人工重试）。只增不减，便于看出哪项一直不过。
    attempts: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
