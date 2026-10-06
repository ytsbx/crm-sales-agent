"""OA 实例关联（文档 §四 :137「OA 实例关联」）。

文档点名要存四样：**需求版本、OA 类型、实例 ID、状态与结果及更新时间**，
用途写得很清楚——"防重复提交，并把询价结果回到正确需求"。

唯一性分三层，各挡一件不同的事（第七批 7.7/7.8 定稿）：
1. `idempotency_key`（含 `submit_round`）唯一：**同一轮**的重复提交复用同一行，
   驳回后重提换一个键落成新一轮（文档 :43 挡网络重试 vs §11.3 :152 要重提能跑通）；
2. `instance_id` 部分唯一：**一个外部实例只能关联一行**，否则人工采纳/回调会改错行；
3. `resolve_request_key` 部分唯一：**同一把核定请求键只能落一行**，同键回放同结果。

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

#: OA 类型。目前只有询价；将来开发评审、变更审批都复用这张表，所以类型要可扩展。
OA_TYPE_LABEL = {
    "inquiry": "定制询价",
}

#: 实例状态。与钉钉的状态词翻译过来，CRM 侧口径固定，避免各处 if 判断漂移。
#:
#: 第七批 7.7 把"这次发起到底发生了什么"拆成三类，因为**能不能重试**的答案完全不同：
#:   - `skipped` / `not_sent`：确定没发出（总闸关着 / 本地异常，请求根本没出去）→ 重试安全；
#:   - `failed`：明确失败（外部 4xx 拒绝、或 200 但没给实例号）→ 没有建单，重试安全；
#:   - `needs_review`：**结果未知**（超时/连接断/5xx/进程中断）→ 钉钉那边可能已建单，
#:     只能先查询或人工核对，**不允许自动重发，也不允许直接重提另建**。
#: 早先把这三类统一记成 `failed`：于是"没发"被当成故障去查，而"结果未知"被重试成重复建单。
OA_STATUS_LABEL = {
    # 已占住业务键、正在向钉钉发起：这是**中间态**，说明本地已登记但外部结果未知
    "submitting": "发起中",
    "pending": "审批中",
    "approved": "已通过",
    "rejected": "已驳回",
    "withdrawn": "已撤销",
    "failed": "发起失败",
    # 本地就没发出去（参数/取 token 出错）：与"被外部拒绝"分开，同轮重试安全
    "not_sent": "未发出",
    # 上次发起结果不明（进程在调钉钉前后中断、或请求超时）：钉钉那边可能已建单。
    # 钉钉接口没有幂等键，**不能自动重发**（可能真建出第二张），转人工核对后决定。
    "needs_review": "结果待人工核对",
    # 推送总闸关着（DINGTALK_PUSH_OFF）时用这个状态：
    # **"没发"和"发失败"必须分开**，否则测试期的"没发"会被当成故障去查
    "skipped": "未发起（推送已关闭）",
}

#: 「状态 → 允许动作」的**唯一口径**（第七批 7.7）。
#:
#: 服务端用它守请求，接口把它一起返回（`serialize` 里的 `allowed_actions`），
#: 前端按它渲染按钮——服务端与前端各写一套 if 必然分叉，而分叉出去的那一侧
#: 就是"待审批还能再点一次重提、钉钉里于是多出一张单"。
#:
#: 动作名与服务端入口的对应：
#:   - `retry`   ：同轮重新发起（复用同一行、同一请求号，**不新建实例**）；
#:   - `resubmit`：换一轮重提（会向钉钉建新实例，只许在旧轮**明确结束**后用）；
#:   - `resolve_adopt` / `resolve_resend` / `resolve_abandon`：结果未知时的人工核定；
#:   - `sync`    ：拉一次状态（只有审批中才有意义）。
OA_ACTION_LABEL = {
    "sync": "查询状态",
    "retry": "重新发起",
    "resubmit": "驳回后重提",
    "resolve_adopt": "认领钉钉已有单",
    "resolve_resend": "确认未建单后重发",
    "resolve_abandon": "作废本轮",
}

OA_STATUS_ACTIONS: dict[str, tuple[str, ...]] = {
    # 正在飞：等它出结果，或者僵死后由上层的"转人工"改成 needs_review
    "submitting": (),
    # **审批中不许重提**：重提会在钉钉里另建一张（7.7 的原始缺陷）
    "pending": ("sync",),
    # 已通过：流程已经走完，同样不许再建实例
    "approved": (),
    # 已驳回 / 人工作废：业务要重开流程，允许换一轮重提（文档 §11.3 :152 把"驳回、重提"并列）
    "rejected": ("resubmit",),
    "withdrawn": ("resubmit",),
    # 明确失败 / 确定没发出：同轮重试是安全的——外部压根没建单，这不是"另建实例"
    "failed": ("retry",),
    "not_sent": ("retry",),
    # 结果未知：先查询/人工核对，**不允许直接重提另建**
    "needs_review": ("resolve_adopt", "resolve_resend", "resolve_abandon"),
    # 总闸关着没发：确定没发出去，开闸后同轮重试
    "skipped": ("retry",),
}

#: 核定占用状态（第七批 7.8）。它与业务状态是两个维度：
#: `status` 回答"这单在钉钉那边走到哪了"，`resolve_state` 回答"有没有人正在对它做外部动作"。
#: 分开的好处是**不新增业务状态**（不擅自把"正在核定"定义成一种审批状态），
#: 同时占用又是数据库里的持久事实——进程锁挡不住多实例部署。
RESOLVE_STATE_LABEL = {
    "idle": "空闲",
    "processing": "核定处理中",
}

#: 进入核定占用、以及"正在发起"的僵死判定阈值见 service.STUCK_AFTER（10 分钟）。
def allowed_actions(status: str, resolve_state: str = "idle") -> list[str]:
    """某条记录当前允许的动作清单（服务端与前端共用这一份口径）。

    占用中（`resolve_state == "processing"`）时除了查询什么都不许做：
    占用期间再点一次重发，就是第二次外部创建的机会——这正是 7.8 要挡的。
    """
    if resolve_state == "processing":
        return ["sync"] if status == "pending" else []
    return list(OA_STATUS_ACTIONS.get(status, ()))


class OaInstance(Base, IdMixin):
    __tablename__ = "oa_instances"
    __table_args__ = (
        # **唯一约束落在幂等键上，不落在业务键上**。
        #
        # 文档 :43 要挡的是"**网络重试**不能重复建单"；而 §11.3 :152 又要求
        # "**驳回后重提**跑通"。这两件事对唯一性的要求正好相反：
        #   - 同一轮提交被重试 → 必须复用同一行（挡住重复建单）
        #   - 驳回后重提         → 必须能建出**新一轮**（否则永远发不出去）
        # 早先把唯一约束放在（需求+版本+类型）上，等于把"重提"也一起挡死了。
        # 现在业务键只做普通索引（列表按它查），唯一性交给 idempotency_key，
        # 由调用方按"第几轮提交"决定它的值。
        Index("ix_oa_instances_business", "inquiry_id", "inquiry_version", "oa_type"),
        UniqueConstraint("idempotency_key", name="uq_oa_instance_idempotency"),
        Index("ix_oa_instances_instance", "instance_id"),
        Index("ix_oa_instances_status", "status"),
        # **一个外部实例只能关联一行**（第七批 7.8）：人工"认领实例号"时，
        # 同一个钉钉单号被两行认领会造成"采纳哪个实例"命中两行、回调改错状态。
        # 用部分唯一索引（NULL 表示"还没有实例"，不参与唯一性）。
        Index(
            "uq_oa_instance_instance_id",
            "instance_id",
            unique=True,
            postgresql_where=text("instance_id IS NOT NULL"),
        ),
        # **同一把核定请求键只能落一行**（7.8）：同键回放同结果，键不能跨记录误复用。
        Index(
            "uq_oa_instance_resolve_key",
            "resolve_request_key",
            unique=True,
            postgresql_where=text("resolve_request_key IS NOT NULL"),
        ),
    )

    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 挂在"需求及版本"上——结果要回到正确的那一版（文档 §四 :137）
    inquiry_id: Mapped[int] = mapped_column(BigInteger)
    inquiry_version: Mapped[int] = mapped_column(BigInteger, default=1)
    oa_type: Mapped[str] = mapped_column(String(24), default="inquiry")
    #: 幂等键：同一轮提交重复调用只落一行；驳回后重提换一个键，落成新一轮
    idempotency_key: Mapped[str] = mapped_column(String(128))
    #: 第几轮提交（驳回后重提 +1）
    submit_round: Mapped[int] = mapped_column(BigInteger, default=1)

    #: 生成实例时用的模板标识与提交人，排障时要能还原"当时拿什么发的"
    process_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    originator_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 提交给钉钉的表单值快照：事后能回答"当初到底填进去什么"
    form_snapshot: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 不在这里写 index=True：__table_args__ 里已经有同名的
    # ix_oa_instances_status，两处都声明会生成两条同名 CREATE INDEX，
    # `create_all`（测试/新环境）直接报"索引已存在"。
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 「已经向钉钉发起过几次」——一轮之内重试会累加（轮次本身看 submit_round）。
    #: 为什么要它：结果未知时人要先判断"这单到底试过几次"，只靠 error 文本判断不了。
    attempt_count: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )

    #: 核定（人工处理"结果未知"）的**占用状态**（第七批 7.8）。
    #: 它是数据库里的持久事实，不是进程锁——两个并发核定请求靠它做原子状态转换，
    #: 只有一个能拿到"处理中"，另一个明确报冲突。
    resolve_state: Mapped[str] = mapped_column(
        String(16), default="idle", server_default=text("'idle'")
    )
    #: 本次占用的请求键：同键回放同结果；也是"键不能跨记录误复用"的唯一约束载体。
    resolve_request_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 占用开始时间：超过 STUCK_AFTER 就认为那次核定已经死了，允许重新占用
    #: （中途重启可恢复；但接管后不许盲目再建，见 service 里的说明）。
    resolve_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 核定**结束**的时间与动作：为空表示这次核定没有正常收尾（崩溃/被拒），
    #: 也是"同键回放"的判据——有结束时间才说明那次请求真的出了结果。
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_action: Mapped[str | None] = mapped_column(String(16), nullable=True)

    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 本次**尝试**发起的时间（重试会刷新它）。
    #: 为什么不复用 created_at：created_at 的语义是"这条记录什么时候产生的"
    #: （第一次发起），拿它当"本次尝试时间"会让排查时问"这单最早什么时候发的"
    #: 得到最后一次重试的时间——一个字段两个含义，迟早有人被它骗。
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 状态最后从钉钉取回的时间——轮询模式下用它判断"多久没同步上了"
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
