"""时间线聚合。

设计取舍：时间线不新增业务表，而是把已有的审计日志、跟进记录、任务、
阶段历史、负责人变更合并后按时间倒序返回，避免出现「两份真相」。
"""

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.customer.model import CustomerOwnerHistory
from app.modules.followup.model import FollowUp
from app.modules.opportunity.model import OpportunityStage, OpportunityStageHistory
from app.modules.task.model import Task
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


async def _user_names(session: AsyncSession, user_ids: set[int]) -> dict[int, str]:
    ids = {uid for uid in user_ids if uid}
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
    return {int(uid): name for uid, name in rows}


async def build_timeline(
    session: AsyncSession,
    business_type: str,
    business_id: int,
    limit: int = 100,
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
                "title": f"{label}{BUSINESS_LABEL.get(business_type, '记录')}",
                "detail": _audit_detail(row),
                "operator_id": row.operator_id,
                "at": row.created_at,
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
                .where(followup_column == business_id)
                .order_by(FollowUp.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        for row in followups:
            events.append(
                {
                    "kind": "followup",
                    "title": f"跟进（{row.followup_type}）",
                    "detail": row.content,
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

    names = await _user_names(session, {event["operator_id"] for event in events})
    for event in events:
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


def _audit_detail(row: AuditLog) -> str | None:
    """把审计里的关键变更压成一行可读文本，避免前端直接铺原始 JSON。"""
    after = row.after_data if isinstance(row.after_data, dict) else {}
    before = row.before_data if isinstance(row.before_data, dict) else {}
    for field in ("stage", "loss_reason", "reason", "remark", "owner_id", "quote_version_id"):
        value = after.get(field)
        if value not in (None, ""):
            return f"{field}：{value}"
    if before and after:
        changed = [
            f"{key}: {before.get(key)} → {after.get(key)}"
            for key in after
            if key in before and before.get(key) != after.get(key)
        ]
        if changed:
            return "；".join(changed[:3])
    return None
