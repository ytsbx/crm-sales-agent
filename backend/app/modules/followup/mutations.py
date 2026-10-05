"""页面与 Agent 共用的人工跟进写入口；事务由调用方提交。"""
from datetime import UTC, datetime
import hashlib
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer import service as customer_service
from app.modules.customer.model import Contact, Customer
from app.modules.followup.model import FollowUp
from app.modules.followup.schema import FollowUpCreate, EXEMPTION_LABELS
from app.modules.followup.service import queue_manager_notification
from app.modules.followup.visibility import get_visible_followup
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.task.model import Task

def serialize(followup: FollowUp, owner_name: str | None = None) -> dict:
    return {
        "id": followup.id,
        "customer_id": followup.customer_id,
        "contact_id": followup.contact_id,
        "lead_id": followup.lead_id,
        "opportunity_id": followup.opportunity_id,
        "quote_id": followup.quote_id,
        "order_id": followup.order_id,
        "sample_id": followup.sample_id,
        "owner_id": followup.owner_id,
        "owner_name": owner_name,
        "followup_type": followup.followup_type,
        "content": followup.content,
        "customer_feedback": followup.customer_feedback,
        "next_action": followup.next_action,
        "task_due_at": followup.planned_at,
        "exemption_reason": followup.exemption_reason,
        "next_task_id": followup.next_task_id,
        "created_at": followup.created_at,
    }


async def notify_manual_followup(session: AsyncSession, followup: FollowUp, user: CurrentUser, *, action: str):
    # 原单优先；只关联客户时打开客户日志。使用客户/原单负责人定位部门。
    source_type, source_id = next(
        ((kind, getattr(followup, f"{kind}_id")) for kind in ('sample', 'order', 'quote', 'opportunity', 'lead', 'customer')
         if getattr(followup, f"{kind}_id")), ('customer', None)
    )
    models = {'customer': Customer, 'lead': Lead, 'opportunity': Opportunity, 'quote': Quote, 'order': SalesOrder}
    customer = await session.get(Customer, followup.customer_id) if followup.customer_id else None
    source = await session.get(models[source_type], source_id) if source_type in models and source_id else None
    owner_id = customer.owner_id if customer else (source.owner_id if source else followup.owner_id)
    await queue_manager_notification(
        session, event_key=f"followup:{action}:{followup.id}" + (f":{uuid4().hex}" if action == 'update' else ''),
        customer_id=followup.customer_id, owner_id=owner_id, operator_id=user.id,
        title='新增客户跟进' if action == 'create' else '修改客户跟进',
        content=followup.content + (f"；下一动作：{followup.next_action}；下次跟进：{followup.planned_at.isoformat()}" if followup.planned_at else f"；免填原因：{EXEMPTION_LABELS[followup.exemption_reason]}" if followup.exemption_reason else ""), source_type=source_type, source_id=source_id,
        exclude_user_id=user.id, required_permission='followup:view',
    )



async def create_manual_followup(session: AsyncSession, user: CurrentUser, payload: FollowUpCreate,
                                 *, source="WEB", ip=None) -> tuple[FollowUp, bool]:
    if "admin" not in user.roles and not user.has("followup:create"):
        raise AppError(ErrorCode.FORBIDDEN, "无操作权限：需要 followup:create", 403)
    if payload.followup_type == "系统":
        raise AppError(ErrorCode.PARAM_ERROR, "系统过程记录只能由原单业务操作生成", 422)
    if not any([payload.customer_id, payload.lead_id, payload.opportunity_id, payload.quote_id, payload.order_id]):
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "跟进记录必须关联一个业务对象")
    # 相同用户/请求键串行化，回滚后可以重新提交；唯一约束为最终兜底。
    fingerprint = hashlib.sha256(payload.model_dump_json(exclude={"request_key", "create_task"}).encode()).hexdigest()
    if payload.request_key:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                              {"key": f"followup:{user.id}:{payload.request_key}"})
        existing = (await session.execute(select(FollowUp).where(
            FollowUp.owner_id == user.id, FollowUp.request_key == payload.request_key))).scalar_one_or_none()
        if existing:
            await get_visible_followup(session, user, existing.id)
            if existing.request_hash != fingerprint:
                raise AppError(ErrorCode.VERSION_CONFLICT, "该请求已保存，内容不同请重新发起跟进", 409)
            return existing, True

    customer_id = payload.customer_id
    objects = {}
    for kind, model in (("customer", Customer), ("lead", Lead), ("opportunity", Opportunity), ("quote", Quote), ("order", SalesOrder)):
        obj_id = getattr(payload, f"{kind}_id")
        if not obj_id:
            continue
        obj = await session.get(model, obj_id)
        if obj is None or getattr(obj, "deleted_at", None) is not None:
            raise AppError(ErrorCode.NOT_FOUND, f"{kind} id={obj_id} 不存在", 404)
        await ensure_in_scope(session, user, owner_id=obj.owner_id, label="跟进关联对象",
                              allow_unowned=kind in ("customer", "lead"))
        objects[kind] = obj
        linked_customer = getattr(obj, "customer_id", None)
        if linked_customer:
            if customer_id and customer_id != linked_customer:
                raise AppError(ErrorCode.PARAM_ERROR, "跟进关联单据必须属于同一客户", 422)
            customer_id = linked_customer
    if customer_id and "customer" not in objects:
        customer = await session.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "关联客户不存在", 404)
        await ensure_in_scope(session, user, owner_id=customer.owner_id, label="客户", allow_unowned=True)
        objects["customer"] = customer
    if payload.contact_id:
        contact = await session.get(Contact, payload.contact_id)
        if contact is None or contact.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
        if contact.customer_id != customer_id:
            raise AppError(ErrorCode.PARAM_ERROR, "联系人不属于该客户", 422)

    data = payload.model_dump(exclude={"create_task", "task_title", "task_due_at"})
    data["customer_id"] = customer_id
    followup = FollowUp(**data, owner_id=user.id, planned_at=payload.task_due_at, request_hash=fingerprint)
    session.add(followup)
    await session.flush()
    if payload.task_due_at:
        task = Task(title=payload.task_title or payload.next_action, task_type="followup",
                    customer_id=customer_id, contact_id=payload.contact_id, lead_id=payload.lead_id,
                    opportunity_id=payload.opportunity_id, quote_id=payload.quote_id, order_id=payload.order_id,
                    owner_id=user.id, status="pending", due_at=payload.task_due_at,
                    source="agent" if source == "AGENT" else "manual")
        session.add(task)
        await session.flush()
        followup.next_task_id = task.id
    if objects.get("customer"):
        objects["customer"].last_followup_at = datetime.now(UTC)
    if objects.get("lead"):
        lead = objects["lead"]
        lead.last_followup_at = datetime.now(UTC)
        if lead.status in ("pending", "assigned"):
            lead.status = "following"
    await customer_service.refresh_next_followup_at(session, customer_id)
    await write_audit(session, operator_id=user.id, action="create", business_type="followup",
                      business_id=followup.id, after=serialize(followup), source=source, ip=ip)
    await notify_manual_followup(session, followup, user, action="create")
    return followup, False
