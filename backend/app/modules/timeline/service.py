"""时间线聚合。

设计取舍：时间线不新增业务表，而是把已有的审计日志、跟进记录、任务、
阶段历史、负责人变更合并后按时间倒序返回，避免出现「两份真相」。
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.modules.customer.model import CustomerOwnerHistory
from app.modules.followup.model import FollowUp
from app.modules.followup.schema import EXEMPTION_LABELS
from app.modules.followup.visibility import system_source_filter
from app.modules.opportunity.model import Opportunity, OpportunityStage, OpportunityStageHistory
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.task.model import Task
from app.modules.sample.model import SampleRequest
from app.modules.user.model import User

BUSINESS_LABEL = {
    "customer": "客户",
    "contact": "联系人",
    "lead": "线索",
    "opportunity": "商机",
    "quote": "报价",
    "order": "订单",
    "task": "任务",
    "followup": "跟进",
    "product": "产品",
    "sku": "SKU",
}

ACTION_LABEL = {
    "create": "创建",
    "update": "修改",
    "delete": "删除",
    "transfer": "转移负责人",
    "assign": "分配",
    "claim": "领取",
    "release": "释放回池",
    "release_to_pool": "放入公海",
    "discard": "废弃",
    "convert": "线索转化",
    "change_stage": "推进阶段",
    "win": "标记成交",
    "lose": "标记失单",
    "reopen": "重新激活",
    "enable": "启用",
    "disable": "停用",
    "create_item": "添加需求明细",
    "update_item": "修改需求明细",
    "delete_item": "删除需求明细",
    "complete": "完成",
    "cancel": "取消",
    "postpone": "延期",
    "login": "登录",
}

#: 审计里这几个键以前是**原样摆给用户看的**（渲染出来长 `owner_id：2`、
#: `remark：没有备注`），等于把内部字段名和内部编号亮在了时间线上。
#: 这里补一张中文标签表；值本身要不要翻译由 _audit_value 决定。
AUDIT_FIELD_LABEL = {
    "stage": "阶段",
    "loss_reason": "失单原因",
    "reason": "原因",
    "remark": "备注",
    "owner_id": "负责人",
    "quote_version_id": "依据报价版本",
}


async def _user_names(session: AsyncSession, user_ids: set[int]) -> dict[int, str]:
    ids = {uid for uid in user_ids if uid}
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
    return {int(uid): name for uid, name in rows}


async def _visible_sources(
    session: AsyncSession, user: CurrentUser, followups: list[FollowUp]
) -> dict[tuple[str, int], dict]:
    """按模块权限与原单范围批量查可见来源，不继承客户的范围。"""
    owner_ids = await scoped_owner_ids(session, user)
    visible = {}
    for kind, model, column in (
        ("sample", SampleRequest, "sample_id"),
        ("order", SalesOrder, "order_id"),
        ("quote", Quote, "quote_id"),
        ("opportunity", Opportunity, "opportunity_id"),
    ):
        if "admin" not in user.roles and not user.has(f"{kind}:view"):
            continue
        ids = {getattr(row, column) for row in followups if getattr(row, column)}
        if not ids:
            continue
        stmt = select(model.id).where(model.id.in_(ids))
        if owner_ids is not None:
            stmt = stmt.where(model.owner_id.in_(owner_ids))
        if kind == "quote":
            stmt = stmt.where(Quote.deleted_at.is_(None))
        for source_id in (await session.execute(stmt)).scalars():
            visible[(kind, source_id)] = {"type": kind, "id": source_id}
    return visible


def _followup_source(row: FollowUp) -> tuple[str, int] | None:
    for kind in ("sample", "order", "quote", "opportunity"):
        source_id = getattr(row, f"{kind}_id")
        if source_id:
            return kind, source_id
    return None


async def build_timeline(
    session: AsyncSession,
    business_type: str,
    business_id: int,
    limit: int = 100,
    *,
    user: CurrentUser,
) -> list[dict]:
    events: list[dict] = []

    # 1. 审计日志：所有写操作都会留下记录
    audit_rows = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.business_type == business_type,
                AuditLog.business_id == business_id,
            )
            .order_by(AuditLog.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    for row in audit_rows:
        label = ACTION_LABEL.get(row.action, row.action)
        events.append(
            {
                "kind": "audit",
                # 原来拼成「创建商机」「添加需求明细商机」—— 动作和对象硬接在一起，
                # 后一种读不通（实测渲染出来就是这个）。改成「商机：添加需求明细」，
                # 动作和对象分得开，新增动作也不用再想怎么接。
                "title": f"{BUSINESS_LABEL.get(business_type, '记录')}：{label}",
                # 留到最后补：detail 里要显示负责人姓名，而姓名得等「要查哪些 id」
                # 全收集齐才查得到（见文件末尾的统一后处理）。
                "detail": None,
                "operator_id": row.operator_id,
                "at": row.created_at,
                "_audit_row": row,
            }
        )

    # 2. 跟进记录
    followup_column = {
        "customer": FollowUp.customer_id,
        "lead": FollowUp.lead_id,
        "opportunity": FollowUp.opportunity_id,
    }.get(business_type)
    if followup_column is not None:
        followups = (
            await session.execute(
                select(FollowUp)
                .where(
                    followup_column == business_id,
                    await system_source_filter(session, user),
                )
                .order_by(FollowUp.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        visible_sources = await _visible_sources(session, user, followups)
        for row in followups:
            source_key = _followup_source(row)
            source = visible_sources.get(source_key)
            # 系统单据内容也按来源授权；客户转移/公海不能泄露旧单据事实。
            if row.followup_type == "系统" and source_key and source is None:
                continue
            events.append(
                {
                    "kind": "followup",
                    "title": "业务进展" if row.followup_type == "系统" else f"跟进（{row.followup_type}）",
                    "source": source,
                    "detail": row.content + (
                        # 别再 isoformat()：界面上曾原样出现
                        # 「记录时约定：2026-10-21T02:00:26+00:00」
                        f"；下一动作：{row.next_action}；记录时约定：{_human_time(row.planned_at)}"
                        if row.planned_at else
                        f"；免填原因：{EXEMPTION_LABELS.get(row.exemption_reason, row.exemption_reason)}"
                        if row.exemption_reason else ""
                    ),
                    "operator_id": row.owner_id,
                    "at": row.created_at,
                }
            )

    # 3. 任务：只取与当前对象直接相关的
    task_column = {
        "customer": Task.customer_id,
        "lead": Task.lead_id,
        "opportunity": Task.opportunity_id,
    }.get(business_type)
    if task_column is not None:
        tasks = (
            await session.execute(
                select(Task)
                .where(task_column == business_id)
                .order_by(Task.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        for row in tasks:
            events.append(
                {
                    "kind": "task",
                    "title": "新建任务",
                    "detail": row.title,
                    "operator_id": row.owner_id,
                    "at": row.created_at,
                }
            )
            if row.completed_at:
                events.append(
                    {
                        "kind": "task",
                        "title": "完成任务",
                        "detail": row.completion_note or row.title,
                        "operator_id": row.owner_id,
                        "at": row.completed_at,
                    }
                )

    # 4. 商机阶段历史
    if business_type == "opportunity":
        stages = {
            stage.id: stage.name
            for stage in (await session.execute(select(OpportunityStage))).scalars().all()
        }
        histories = (
            await session.execute(
                select(OpportunityStageHistory)
                .where(OpportunityStageHistory.opportunity_id == business_id)
                .order_by(OpportunityStageHistory.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        for row in histories:
            from_name = stages.get(row.from_stage_id, "新建")
            to_name = stages.get(row.to_stage_id, "-")
            suffix = f"（{row.remark}）" if row.remark else ""
            events.append(
                {
                    "kind": "stage",
                    "title": "推进阶段",
                    "detail": f"{from_name} → {to_name}{suffix}",
                    "operator_id": row.operator_id,
                    "at": row.entered_at,
                }
            )

    # 5. 客户负责人变更
    if business_type == "customer":
        histories = (
            await session.execute(
                select(CustomerOwnerHistory)
                .where(CustomerOwnerHistory.customer_id == business_id)
                .order_by(CustomerOwnerHistory.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        names = await _user_names(
            session,
            {h.old_owner_id for h in histories} | {h.new_owner_id for h in histories},
        )
        for row in histories:
            old_name = names.get(row.old_owner_id, "未知") if row.old_owner_id else "未分配"
            new_name = names.get(row.new_owner_id, "未知") if row.new_owner_id else "公海"
            suffix = f"（{row.reason}）" if row.reason else ""
            events.append(
                {
                    "kind": "owner_change",
                    "title": "负责人变更",
                    "detail": f"{old_name} → {new_name}{suffix}",
                    "operator_id": row.operator_id,
                    "at": row.created_at,
                }
            )

    # 要查名字的人 = 操作人 + 审计里出现过的「变更后负责人」。
    # 后者不在 operator_id 集合里：不一起查的话，detail 里只能退回显示内部编号
    # （这正是老版本渲染出 `owner_id：2` 的原因之一）。
    audit_owner_ids: set[int] = set()
    for row in audit_rows:
        after = row.after_data if isinstance(row.after_data, dict) else {}
        value = after.get("owner_id")
        if isinstance(value, int):
            audit_owner_ids.add(value)
        elif isinstance(value, str) and value.isdigit():
            audit_owner_ids.add(int(value))

    names = await _user_names(
        session, {event["operator_id"] for event in events} | audit_owner_ids
    )
    for event in events:
        audit_row = event.pop("_audit_row", None)
        if audit_row is not None:
            event["detail"] = _audit_detail(audit_row, names)
        event["operator_name"] = (
            names.get(event["operator_id"], "未知用户") if event["operator_id"] else "系统"
        )

    events = [event for event in events if event["at"] is not None]

    def sort_key(event: dict):
        at = event["at"]
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return at

    events.sort(key=sort_key, reverse=True)
    return events[:limit]


def _audit_value(field: str, value, names: dict[int, str]) -> str:
    """把内部值翻译成人话。

    目前只有 owner_id 需要翻译：它存的是用户主键，界面直接显示就成了 `2`，
    业务同事根本不知道是谁。查不到时退回「未知用户（#2）」—— 宁可难看，
    也别假装知道。
    """
    if field == "owner_id":
        try:
            return names.get(int(value), f"未知用户（#{value}）")
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _human_time(value: datetime) -> str:
    """时间点写成业务看得懂的「2026-10-21 10:00」。

    老版本直接 `isoformat()`，界面上就出现 `2026-10-21T02:00:26+00:00`：
    机器格式不说，库里存的是 +08 的 10:00，isoformat 输出的是 UTC 表示，
    看着像早了 8 小时。项目没有统一时区配置（其余地方直接用系统本地时间），
    这里按同一惯例 `astimezone()` 转到本地再格式化。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _audit_detail(row: AuditLog, names: dict[int, str] | None = None) -> str | None:
    """把审计里的关键变更压成一行可读文本，避免前端直接铺原始 JSON。

    键名走 AUDIT_FIELD_LABEL 翻成中文，值走 _audit_value（负责人查成人名）。
    认不出的键**保持原样**：宁可在界面上看到个英文键来提意见，
    也不要因为没登记就整条变更都不显示 —— 那等于把事实藏起来了。
    """
    after = row.after_data if isinstance(row.after_data, dict) else {}
    before = row.before_data if isinstance(row.before_data, dict) else {}
    names = names or {}
    for field, label in AUDIT_FIELD_LABEL.items():
        value = after.get(field)
        if value not in (None, ""):
            return f"{label}：{_audit_value(field, value, names)}"
    if before and after:
        changed = [
            f"{AUDIT_FIELD_LABEL.get(key, key)}："
            f"{_audit_value(key, before.get(key), names)} → "
            f"{_audit_value(key, after.get(key), names)}"
            for key in after
            if key in before and before.get(key) != after.get(key)
        ]
        if changed:
            return "；".join(changed[:3])
    return None
