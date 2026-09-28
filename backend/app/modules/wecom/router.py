"""企业微信集成接口（03-API §10，13 个接口）。

  POST /integrations/wecom/sync-departments          部门同步
  POST /integrations/wecom/sync-users                成员同步
  POST /integrations/wecom/sync-external-contacts    外部联系人同步
  POST /integrations/wecom/sync-follow-relations     跟进关系同步
  GET  /integrations/wecom/unbound-contacts          待归一列表
  GET  /integrations/wecom/unbound-contacts/{id}/candidates   候选客户
  POST /integrations/wecom/unbound-contacts/{id}/bind-customer 关联已有客户
  POST /integrations/wecom/unbound-contacts/{id}/create-customer 建新客户
  POST /integrations/wecom/unbound-contacts/{id}/ignore          暂不处理
  POST /integrations/wecom/transfer                  离职继承
  GET  /integrations/wecom/transfer/{job_id}         继承任务进度
  GET  /integrations/wecom/sync-jobs                 同步任务列表
  GET  /integrations/wecom/sync-jobs/{id}            同步任务详情
  POST /webhooks/wecom/events                        事件回调（无需登录）

额外加了 GET /integrations/wecom/readiness：把"凭据配了没、数据同步到哪一步"
一次返回，前端页面据此显示还差什么——否则每次都要翻 .env 才知道。

关于"还没配凭据"：同步类接口会返回 50202 且带明确文案，而不是返回
"成功 0 条"。静默成功会让运营以为企微里真的没人，这是最坏的失败方式。
"""

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.wecom import callback as wecom_callback
from app.modules.wecom import service as svc
from app.modules.wecom.client import WeComError, WeComNotConfigured
from app.modules.wecom.model import WeComSyncJob
from app.modules.wecom.schema import (
    BindCustomerRequest,
    CreateCustomerRequest,
    IgnoreContactRequest,
    TransferRequest,
)

router = APIRouter(tags=["WeCom"])


def _translate(error: Exception) -> AppError:
    """把 adapter 的异常翻译成业务错误码，文案要能直接指导操作。"""
    if isinstance(error, WeComNotConfigured):
        return AppError(
            ErrorCode.WECOM_SYNC_FAILED,
            f"{error}；配置位置：backend/.env",
            422,
        )
    if isinstance(error, WeComError):
        return AppError(ErrorCode.WECOM_SYNC_FAILED, str(error), 502)
    return AppError(ErrorCode.WECOM_SYNC_FAILED, f"企业微信同步失败：{error}", 502)


@router.get("/integrations/wecom/readiness")
async def wecom_readiness(
    _: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    """企微就绪度：凭据缺哪些 + 本地已同步多少数据。"""
    return ok(await svc.readiness(session))


@router.post("/integrations/wecom/sync-departments")
async def sync_departments(
    request: Request,
    user: CurrentUser = Depends(require_permission("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    try:
        job = await svc.sync_departments(session, user=user)
    except (WeComNotConfigured, WeComError) as error:
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_sync_departments",
        business_type="integration",
        business_id=job.id,
        after={"success": job.success_count, "fail": job.fail_count},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_job(job), f"部门同步完成，成功 {job.success_count} 条")


@router.post("/integrations/wecom/sync-users")
async def sync_users(
    request: Request,
    user: CurrentUser = Depends(require_permission("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    try:
        job = await svc.sync_users(session, user=user)
    except (WeComNotConfigured, WeComError) as error:
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_sync_users",
        business_type="integration",
        business_id=job.id,
        after={"success": job.success_count, "unmatched": (job.detail or {}).get("unmatched")},
        ip=client_ip(request),
    )
    await session.commit()
    unmatched = len((job.detail or {}).get("unmatched") or [])
    message = f"成员同步完成，成功 {job.success_count} 条"
    if unmatched:
        message += f"；其中 {unmatched} 人在 CRM 里没有账号，需要人工建号并分配角色"
    return ok(svc.serialize_job(job), message)


@router.post("/integrations/wecom/sync-external-contacts")
async def sync_external_contacts(
    request: Request,
    user: CurrentUser = Depends(require_permission("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    try:
        job = await svc.sync_external_contacts(session, user=user)
    except (WeComNotConfigured, WeComError) as error:
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_sync_external_contacts",
        business_type="integration",
        business_id=job.id,
        after={"success": job.success_count},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_job(job), f"外部联系人同步完成，成功 {job.success_count} 条")


@router.post("/integrations/wecom/sync-follow-relations")
async def sync_follow_relations(
    request: Request,
    user: CurrentUser = Depends(require_permission("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    try:
        job = await svc.sync_follow_relations(session, user=user)
    except (WeComNotConfigured, WeComError) as error:
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_sync_follow_relations",
        business_type="integration",
        business_id=job.id,
        after={"success": job.success_count, "fail": job.fail_count},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_job(job), f"跟进关系同步完成，成功 {job.success_count} 条")


@router.get("/integrations/wecom/unbound-contacts")
async def list_unbound_contacts(
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    """待归一列表（PRD §8.3 的第 1 步：待处理联系人）。"""
    items, total = await svc.unbound_contacts(
        session, keyword=keyword, page=page, page_size=page_size
    )
    return ok(page_data(items, total, page, page_size))


@router.get("/integrations/wecom/unbound-contacts/{contact_id}/candidates")
async def unbound_candidates(
    contact_id: int,
    _: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    """候选客户匹配（PRD §8.3 的第 2 步）。"""
    return ok(await svc.unbound_candidates(session, contact_id))


@router.post("/integrations/wecom/unbound-contacts/{contact_id}/bind-customer")
async def bind_customer(
    contact_id: int,
    payload: BindCustomerRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    """关联已有客户（PRD §8.3 的第 3 步之一）。"""
    result = await svc.bind_customer(
        session,
        user=user,
        contact_id=contact_id,
        customer_id=payload.customer_id,
        is_primary=payload.is_primary,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_bind_customer",
        business_type="integration",
        business_id=contact_id,
        after=result,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, f"已关联到客户「{result['customer_name']}」")


@router.post("/integrations/wecom/unbound-contacts/{contact_id}/create-customer")
async def create_customer(
    contact_id: int,
    payload: CreateCustomerRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    """创建新客户（PRD §8.3 的第 3 步之二）。"""
    if not payload.name.strip():
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "客户名称必填", 422)
    result = await svc.create_customer_from_contact(
        session, user=user, contact_id=contact_id, payload=payload.model_dump()
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_create_customer",
        business_type="integration",
        business_id=contact_id,
        after=result,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, f"已创建客户「{result['customer_name']}」")


@router.post("/integrations/wecom/unbound-contacts/{contact_id}/ignore")
async def ignore_contact(
    contact_id: int,
    payload: IgnoreContactRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    """暂不处理（PRD §8.3 的第 3 步之三）。"""
    row = await svc.ignore_contact(
        session, user=user, contact_id=contact_id, reason=payload.reason
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_ignore_contact",
        business_type="integration",
        business_id=contact_id,
        after={"reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"id": row.id, "normalize_status": row.normalize_status}, "已标记为暂不处理")


@router.post("/integrations/wecom/transfer")
async def transfer(
    payload: TransferRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    """离职继承（PRD §8.4）。不配企微 secret 时传 transfer_wecom=false 只转 CRM 侧。

    ⚠️ 硬锁（WECOM_TRANSFER_ENABLED）：该操作会变更**真实客户**在微信里
    看到的服务人员，默认禁止执行——需业务确认后由管理员显式开启。
    """
    from app.core.config import settings as app_settings

    if not app_settings.wecom_transfer_enabled:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "离职继承已锁定：该操作会变更客户在微信里看到的服务人员。"
            "需业务确认后由管理员设置 WECOM_TRANSFER_ENABLED=1 才能执行",
            403,
        )
    job = await svc.transfer_relations(
        session,
        user=user,
        handover_user_id=payload.handover_user_id,
        takeover_user_id=payload.takeover_user_id,
        transfer_wecom=payload.transfer_wecom,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_transfer",
        business_type="integration",
        business_id=job.id,
        after=job.detail,
        ip=client_ip(request),
    )
    await session.commit()
    detail = job.detail or {}
    return ok(
        svc.serialize_job(job),
        f"继承完成：企微关系 {detail.get('wecom_relations', 0)} 条、"
        f"客户 {detail.get('customers', 0)} 个、商机 {detail.get('opportunities', 0)} 个、"
        f"任务 {detail.get('tasks', 0)} 个",
    )


@router.get("/integrations/wecom/transfer/{job_id}")
async def get_transfer_job(
    job_id: int,
    _: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    job = await session.get(WeComSyncJob, job_id)
    if job is None or job.job_type != "transfer":
        raise AppError(ErrorCode.NOT_FOUND, "继承任务不存在", 404)
    return ok(svc.serialize_job(job))


@router.get("/integrations/wecom/sync-jobs")
async def list_sync_jobs(
    job_type: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(WeComSyncJob)
    if job_type:
        stmt = stmt.where(WeComSyncJob.job_type == job_type)
    if status:
        stmt = stmt.where(WeComSyncJob.status == status)
    rows, total = await paginate(session, stmt.order_by(WeComSyncJob.id.desc()), page, page_size)
    return ok(
        page_data([svc.serialize_job(row) for row in rows], total, page, page_size)
    )


@router.get("/integrations/wecom/sync-jobs/{job_id}")
async def get_sync_job(
    job_id: int,
    _: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    job = await session.get(WeComSyncJob, job_id)
    if job is None:
        raise AppError(ErrorCode.NOT_FOUND, "同步任务不存在", 404)
    return ok(svc.serialize_job(job))


# ---- 事件回调：无需登录，靠签名校验 ---------------------------------------


@router.get("/webhooks/wecom/events")
async def verify_wecom_url(
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
):
    """企微后台配置回调 URL 时的校验：解密 echostr 并原样返回明文。

    这个接口**不能要求登录**（企微服务器没有我们的 token），
    安全性靠签名校验 + AES 解密来保证。
    """
    try:
        wecom_callback.verify_signature(
            signature=msg_signature, timestamp=timestamp, nonce=nonce, encrypt=echostr
        )
        return Response(content=wecom_callback.decrypt(echostr), media_type="text/plain")
    except wecom_callback.WeComCallbackError as error:
        raise AppError(ErrorCode.WECOM_SYNC_FAILED, str(error), 400) from error


@router.post("/webhooks/wecom/events")
async def receive_wecom_event(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    session: AsyncSession = Depends(get_db),
):
    """接收企微事件。

    当前落库范围：把事件本身记进 `wecom_sync_jobs`（job_type=外部事件），
    让运营能看到"企微推了什么过来"。真正的增量同步（change_external_contact
    等事件触发只拉那一个人）留给拿到凭据、能联调时再做——现在没有真实回调，
    写增量逻辑无法验证，等于凭空猜。
    """
    body = (await request.body()).decode("utf-8")
    try:
        encrypt = wecom_callback.parse_event(body).get("Encrypt", "")
        if not encrypt:
            raise wecom_callback.WeComCallbackError("回调报文里没有 Encrypt 字段")
        wecom_callback.verify_signature(
            signature=msg_signature, timestamp=timestamp, nonce=nonce, encrypt=str(encrypt)
        )
        event = wecom_callback.parse_event(wecom_callback.decrypt(str(encrypt)))
    except wecom_callback.WeComCallbackError as error:
        raise AppError(ErrorCode.WECOM_SYNC_FAILED, str(error), 400) from error

    job = WeComSyncJob(
        job_type="external_contact",
        status="success",
        success_count=1,
        fail_count=0,
        detail={"source": "event_callback", "event": event},
    )
    session.add(job)
    await session.commit()
    # 企微要求回 "success"，否则会重试
    return Response(content="success", media_type="text/plain")
