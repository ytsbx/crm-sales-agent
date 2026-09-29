"""钉钉 OA 审批接口（文档 §11.3 :152 / 场景11）。

发起与回收都在这里收口；**模板与字段映射读设置项 `dingtalk_oa`**，
所以换模板不用改代码——这正是"业务定了走钉钉"之后还需要的那一步。
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.dingtalk import service as svc
from app.modules.dingtalk.model import OaInstance

router = APIRouter(tags=["DingTalk"])

#: CRM 侧字段 → 取哪条需求的哪个属性。放在这里而不是散在业务代码里，
#: 是因为"预填什么"是业务口径，改它不该动流程代码。
INQUIRY_FIELDS = {
    "inquiry_no": lambda q: q.inquiry_no,
    "title": lambda q: q.title,
    "description": lambda q: q.description,
    "quantity": lambda q: str(q.quantity) if q.quantity is not None else None,
    "target_price": lambda q: str(q.target_price) if q.target_price is not None else None,
    "version": lambda q: str(q.version),
}


class StartApproval(BaseModel):
    """发起询价审批。`resubmit=True` 用于**驳回后重提**（建新一轮，见 service 注释）。"""

    resubmit: bool = False
    extra_fields: dict[str, str] | None = None


@router.post("/inquiries/{inquiry_id}/oa-approval")
async def start_inquiry_approval(
    inquiry_id: int,
    payload: StartApproval,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """从 CRM 的定制需求发起钉钉询价审批（幂等；驳回后重提走 resubmit）。"""
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.settings import service as settings_service

    inquiry = await session.get(CustomInquiry, inquiry_id)
    if inquiry is None or inquiry.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "定制需求不存在", 404)

    cfg = await settings_service.get_setting(session, "dingtalk_oa")
    process_code = (cfg or {}).get("process_code") or ""
    if not process_code:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "还没配置钉钉审批模板（设置项 dingtalk_oa.process_code）。"
            "业务定了用哪个模板后填上即可，不用改代码",
            422,
        )
    mapping = (cfg or {}).get("field_map") or {}
    field_map = {}
    for crm_field, component_id in mapping.items():
        getter = INQUIRY_FIELDS.get(crm_field)
        if getter is None or not component_id:
            continue
        value = getter(inquiry)
        if value not in (None, ""):
            field_map[component_id] = value
    # 额外字段（业务临时补的）按 componentId 直接给
    for component_id, value in (payload.extra_fields or {}).items():
        if component_id and value not in (None, ""):
            field_map[component_id] = value

    row = await svc.create_inquiry_instance(
        session,
        user=user,
        inquiry_id=inquiry.id,
        inquiry_version=int(inquiry.version or 1),
        customer_id=inquiry.customer_id,
        process_code=process_code,
        originator_user_id=str(user.id),
        field_map=field_map,
        resubmit=payload.resubmit,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="start_oa_approval",
        business_type="oa_instance",
        business_id=row.id,
        after={
            "status": row.status,
            "instance_id": row.instance_id,
            "submit_round": row.submit_round,
            "inquiry_id": inquiry.id,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize(row), row.error or "已发起钉钉询价审批")


@router.get("/inquiries/{inquiry_id}/oa-approvals")
async def list_inquiry_approvals(
    inquiry_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """该需求历次提交的审批实例（含被驳回的旧轮次——重提不改写历史）。"""
    rows = (
        await session.execute(
            select(OaInstance)
            .where(OaInstance.inquiry_id == inquiry_id)
            .order_by(OaInstance.submit_round.desc())
        )
    ).scalars().all()
    return ok([svc.serialize(row) for row in rows])


@router.post("/dingtalk/oa-sync")
async def trigger_oa_sync(
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """立刻拉一次审批状态（与定时任务同一个函数，验收时不用等）。"""
    # 这里不需要 Query 装饰器：它是函数参数，不是请求参数
    result = await svc.sync_pending_instances(session, limit=50)
    await session.commit()
    return ok(result, f"已检查 {result['checked']} 条，状态变更 {result['changed']} 条")
