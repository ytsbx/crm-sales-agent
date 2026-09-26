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
