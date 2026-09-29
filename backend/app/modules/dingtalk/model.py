"""OA 实例关联（文档 §四 :137「OA 实例关联」）。

文档点名要存四样：**需求版本、OA 类型、实例 ID、状态与结果及更新时间**，
用途写得很清楚——"防重复提交，并把询价结果回到正确需求"。

所以唯一约束落在**业务键**上（需求 + 需求版本 + OA 类型），而不是落在实例 ID 上：
实例 ID 是钉钉给的，重复提交时我们压根不该再向钉钉要一次实例；
靠业务键挡住第二次提交，才是真的"不重复建 OA 单"（场景11）。

`inquiry_version` 必须有：文档 §3.3 的定制需求是"改要求 = 新增一版"，
同一条需求会有多个版本，询价审批要回到**当时那一版**，否则结果会挂错版本。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

#: OA 类型。目前只有询价；将来开发评审、变更审批都复用这张表，所以类型要可扩展。
OA_TYPE_LABEL = {
    "inquiry": "定制询价",
}

#: 实例状态。与钉钉的状态词翻译过来，CRM 侧口径固定，避免各处 if 判断漂移。
OA_STATUS_LABEL = {
    "pending": "审批中",
    "approved": "已通过",
    "rejected": "已驳回",
    "withdrawn": "已撤销",
    "failed": "发起失败",
}


class OaInstance(Base, IdMixin):
    __tablename__ = "oa_instances"
    __table_args__ = (
        # 业务键唯一 = 防重复提交（场景11）。重试命中已有行就直接返回，
        # 不再打钉钉接口——网络重试不能重复建单（文档 :43 的硬要求）。
        UniqueConstraint(
            "inquiry_id", "inquiry_version", "oa_type", name="uq_oa_instance_business"
        ),
        Index("ix_oa_instances_instance", "instance_id"),
        Index("ix_oa_instances_status", "status"),
    )

    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 挂在"需求及版本"上——结果要回到正确的那一版（文档 §四 :137）
    inquiry_id: Mapped[int] = mapped_column(BigInteger)
    inquiry_version: Mapped[int] = mapped_column(BigInteger, default=1)
    oa_type: Mapped[str] = mapped_column(String(24), default="inquiry")

    #: 生成实例时用的模板标识与提交人，排障时要能还原"当时拿什么发的"
    process_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    originator_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 提交给钉钉的表单值快照：事后能回答"当初到底填进去什么"
    form_snapshot: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 状态最后从钉钉取回的时间——轮询模式下用它判断"多久没同步上了"
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
