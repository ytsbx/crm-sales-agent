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
    from app.modules.settings import service as settings_service

    # 复用 inquiry 模块的可见性检查（P1 修复）：直接 `session.get` 按 id 取会**绕过数据范围**
    # ——业务员能对别人的定制需求发起审批。同一个文件里就有现成的
    # `get_visible_or_404`，没有理由另写一套（另写必然和列表页的判据分叉）。
    from app.modules.inquiry import service as inquiry_service

    inquiry = await inquiry_service.get_visible_or_404(session, user, inquiry_id)

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
    # 钉钉那格是必填的**联系人**控件，只能在这几位里选（编号从钉钉通讯录拿的）；
    # 销售在 CRM 里选好的那位直接带过去
    quote_component = (cfg or {}).get("quote_owner_component")
    if quote_component:
        if not inquiry.oa_quote_user_id:
            raise AppError(
                ErrorCode.REQUIRED_FIELD_MISSING,
                "钉钉要求先指定「对接报价员」，请在需求上选好再发起",
                422,
            )
        field_map[quote_component] = inquiry.oa_quote_user_id
    # 提报人 / 提报部门：钉钉要的是**人的编号与部门编号**，不是姓名。
    # 按 CRM 里这个人的姓名去钉钉找同名账号——**找不到就明确报错**，
    # 不能瞎填一个，否则单子会提给一个不相干的人（或者被钉钉拒掉还看不出原因）。
    originator_component = (cfg or {}).get("originator_component")
    dept_component = (cfg or {}).get("dept_component")
    if originator_component or dept_component:
        from app.modules.dingtalk.client import get_client

        ding_user_id = await get_client().search_user_id_by_name(user.name)
        if not ding_user_id:
            raise AppError(
                ErrorCode.PARAM_NOT_VALID if hasattr(ErrorCode, "PARAM_NOT_VALID") else ErrorCode.PARAM_ERROR,
                f"钉钉里找不到与「{user.name}」同名的账号，无法发起审批。"
                "请确认你的钉钉姓名与本系统一致，或联系管理员",
                422,
            )
        if originator_component:
            field_map[originator_component] = ding_user_id
        if dept_component:
            dept_ids = await get_client().get_user_dept_ids(ding_user_id)
            if dept_ids:
                field_map[dept_component] = dept_ids[0]
    # 额外字段（业务临时补的）按 componentId 直接给
    for component_id, value in (payload.extra_fields or {}).items():
        if component_id and value not in (None, ""):
            field_map[component_id] = value

    # 「产品参考图片」是**必填的图片控件**：图不能直接塞进审批单，
    # 得先上传给钉钉换 media_id——**这一步也是对外的**，所以只在总闸打开时真传；
    # 关着的时候只把"会用哪张图"记下来，等真发的时候再传。
    from app.core.config import settings as app_settings

    image_component = (cfg or {}).get("image_component")
    if image_component:
        biz_type = (cfg or {}).get("inquiry_file_business_type") or "inquiry"
        found = await svc.first_image_attachment(
            session, business_type=biz_type, business_id=inquiry.id
        )
        if found is None:
            raise AppError(
                ErrorCode.REQUIRED_FIELD_MISSING,
                "钉钉要求必须上传「产品参考图片」，请先在需求里上传图纸或参考图再发起",
                422,
            )
        filename, content = found
        if app_settings.dingtalk_push_off:
            # 干跑：不传图，只记下会用哪一张（对外零请求）
            field_map[image_component] = f"（待上传：{filename}）"
        else:
            from app.modules.dingtalk.client import get_client

            media_id = await get_client().upload_media(
                content=content, filename=filename, media_type="image"
            )
            field_map[image_component] = media_id

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
    # 读也要过范围（P1）：审批实例里可能带着价格与审批意见，不能按 id 直取
    from app.modules.inquiry import service as inquiry_service

    await inquiry_service.get_visible_or_404(session, user, inquiry_id)
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
