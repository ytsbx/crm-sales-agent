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
  GET  /integrations/wecom/transfer-preview          交接清单预览（执行前先看）
  GET  /integrations/wecom/transfer/{job_id}         继承任务进度
  GET  /integrations/wecom/transfer/{job_id}/items   交接逐项结果（分页，含失败明细）
  POST /integrations/wecom/transfer/{job_id}/retry   按项重试未完成的部分
  GET  /integrations/wecom/sync-jobs                 同步任务列表
  GET  /integrations/wecom/sync-jobs/{id}            同步任务详情
  POST /webhooks/wecom/events                        事件回调（无需登录）

额外加了 GET /integrations/wecom/readiness：把"凭据配了没、数据同步到哪一步"
一次返回，前端页面据此显示还差什么——否则每次都要翻 .env 才知道。

关于"还没配凭据"：同步类接口会返回 50202 且带明确文案，而不是返回
"成功 0 条"。静默成功会让运营以为企微里真的没人，这是最坏的失败方式。
"""

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission, ensure_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.wecom import callback as wecom_callback, client as wecom_client
from app.modules.wecom import service as svc
from app.modules.wecom.client import WeComError, WeComNotConfigured, WeComResponseError
from app.modules.user.model import User
from app.modules.wecom.model import WeComSyncJob, WeComTransferItem
from app.modules.wecom.schema import (
    BindCustomerRequest,
    ImportCustomersRequest,
    CreateCustomerRequest,
    IgnoreContactRequest,
    TransferRequest,
)

#: 回调时间戳允许的最大偏差（秒）。企微官方建议 5 分钟；超出即视为重放/过期报文。
#: 没有它时，一份抓到的合法报文可以无限重放（每次都落一条同步任务）。
WECOM_CALLBACK_MAX_SKEW_SECONDS = 300
WECOM_CALLBACK_MAX_BODY_BYTES = 1024 * 1024

router = APIRouter(tags=["WeCom"])


def require_wecom_action(*codes: str):
    async def check(user: CurrentUser = Depends(require_permission(*codes))) -> CurrentUser:
        ensure_permission(user, "wecom:view")
        return user
    return check


async def _ensure_transfer_department(session: AsyncSession, user: CurrentUser, people: set[int]) -> None:
    if "admin" in user.roles:
        return
    in_department = set(await session.scalars(select(User.id).where(
        User.id.in_(people), User.department_id == user.department_id,
    ))) if user.department_id else set()
    if not people or in_department != people:
        raise AppError(ErrorCode.FORBIDDEN, "跨部门或未归属部门的离职交接请由管理员处理", 403)


async def _get_transfer_for_user(session: AsyncSession, user: CurrentUser, job_id: int) -> WeComSyncJob:
    job = await session.get(WeComSyncJob, job_id)
    if job is None or job.job_type != "transfer" or ("admin" not in user.roles and job.operator_id != user.id):
        raise AppError(ErrorCode.NOT_FOUND, "交接记录不存在或不在你的查看范围", 404)
    return job


def _translate(error: Exception) -> AppError:
    """把 adapter 的异常翻译成业务错误码，文案要能直接指导操作。"""
    if isinstance(error, WeComNotConfigured):
        return AppError(
            ErrorCode.WECOM_SYNC_FAILED,
            f"{error}；配置位置：backend/.env",
            422,
        )
    # 不回显HTTP异常原文：URL里可能含access_token、corpsecret。
    if isinstance(error, httpx.TimeoutException):
        return AppError(ErrorCode.WECOM_SYNC_FAILED, "连接企业微信超时，请稍后重试", 502)
    if isinstance(error, httpx.HTTPStatusError):
        return AppError(ErrorCode.WECOM_SYNC_FAILED,
                        f"企业微信服务返回 HTTP {error.response.status_code}，请稍后重试", 502)
    if isinstance(error, httpx.RequestError):
        return AppError(ErrorCode.WECOM_SYNC_FAILED, "连接企业微信失败，请检查网络后重试", 502)
    if isinstance(error, (WeComError, WeComResponseError)):
        return AppError(ErrorCode.WECOM_SYNC_FAILED, wecom_client.safe_error_text(error), 502)
    return AppError(ErrorCode.WECOM_SYNC_FAILED, wecom_client.safe_error_text(error), 502)


async def _run_sync(
    session: AsyncSession, *, user: CurrentUser, job_type: str,
    operation: Callable[..., Awaitable[WeComSyncJob]],
) -> WeComSyncJob:
    """整批外部失败先回滚同步事实，再单独提交一次失败流水。"""
    started_at = datetime.now(UTC)
    try:
        return await operation(session, user=user)
    except (WeComNotConfigured, WeComError, WeComResponseError, httpx.RequestError, httpx.HTTPStatusError) as error:
        translated = _translate(error)
        await session.rollback()
        job = await svc.start_job(session, job_type=job_type, operator_id=user.id)
        job.started_at = started_at
        svc.finish_job(job, success=0, fail=1, error=translated.message)
        await session.commit()
        raise translated from error


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
    user: CurrentUser = Depends(require_wecom_action("wecom:sync_directory")),
    session: AsyncSession = Depends(get_db),
):
    job = await _run_sync(session, user=user, job_type="department", operation=svc.sync_departments)
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
    user: CurrentUser = Depends(require_wecom_action("wecom:sync_directory")),
    session: AsyncSession = Depends(get_db),
):
    job = await _run_sync(session, user=user, job_type="user", operation=svc.sync_users)
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_sync_users",
        business_type="integration",
        business_id=job.id,
        after={
            "success": job.success_count,
            "unmatched": (job.detail or {}).get("unmatched"),
            "conflicts": (job.detail or {}).get("conflicts"),
        },
        ip=client_ip(request),
    )
    await session.commit()
    unmatched = len((job.detail or {}).get("unmatched") or [])
    conflicts = len((job.detail or {}).get("conflicts") or [])
    message = f"成员同步完成，成功 {job.success_count} 条"
    if unmatched:
        message += f"；{unmatched} 人未匹配到 CRM 账号，请核对员工资料"
    if conflicts:
        message += f"；{conflicts} 人的手机号或账号绑定有冲突，请人工核对"
    return ok(svc.serialize_job(job), message)


@router.post("/integrations/wecom/sync-external-contacts")
async def sync_external_contacts(
    request: Request,
    user: CurrentUser = Depends(require_wecom_action("wecom:sync_contacts")),
    session: AsyncSession = Depends(get_db),
):
    """拉取企微外部联系人（只落企微事实）。

    逐个成员拉客户列表，员工多时一轮要几十秒——网页请求只能等 30 秒，
    客户端先断开，后端跟着被取消，用户看到「网络异常」而实际没同步。
    与跟进关系同步一样改成后台任务：先落任务行、立即返回，页面轮询任务状态。
    """
    global _external_sync_task
    running = (
        await session.execute(
            select(WeComSyncJob)
            .where(
                WeComSyncJob.job_type == "external_contact",
                WeComSyncJob.status == "running",
            )
            .order_by(WeComSyncJob.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if running is not None:
        return ok(
            svc.serialize_job(running),
            "外部联系人同步正在进行中，完成后会自动刷新，请稍候",
        )
    job = await svc.start_job(session, job_type="external_contact", operator_id=user.id)
    await session.commit()
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_sync_external_contacts",
        business_type="integration",
        business_id=job.id,
        after={"status": "running"},
        ip=client_ip(request),
    )
    await session.commit()
    _external_sync_task = asyncio.create_task(
        _run_external_contacts_background(job.id, user.id)
    )
    return ok(
        svc.serialize_job(job),
        "外部联系人同步已开始；联系人较多时需要一两分钟，完成后页面会自动刷新",
    )


logger = logging.getLogger(__name__)

#: 本进程正在跑的跟进关系同步任务（单 worker 部署；只用于防重复启动）。
_follow_sync_task: asyncio.Task | None = None


async def _run_follow_relations_background(job_id: int, user_id: int) -> None:
    """后台跑跟进关系同步：数据量大时一轮要调 4,000+ 次企微接口，
    不能再占着网页请求（浏览器 30 秒就超时取消）。独立会话、独立事务。"""
    from types import SimpleNamespace

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        job = await session.get(WeComSyncJob, job_id, with_for_update=True)
        if job is None or job.status != "running":
            return
        try:
            await svc.sync_follow_relations(
                session, user=SimpleNamespace(id=user_id), job=job
            )
            await write_audit(
                session,
                operator_id=user_id,
                source="SYSTEM",
                action="wecom_sync_follow_relations",
                business_type="integration",
                business_id=job.id,
                after={"success": job.success_count, "fail": job.fail_count},
            )
            await session.commit()
        except Exception as error:  # noqa: BLE001 —— 后台任务必须把失败写回任务行
            logger.exception("跟进关系同步失败 job_id=%s", job_id)
            await session.rollback()
            job = await session.get(WeComSyncJob, job_id, with_for_update=True)
            if job is None:
                return
            message = wecom_client.safe_error_text(error)[:500] or "同步异常，请查看服务器日志"
            svc.finish_job(job, success=0, fail=1, error=message)
            await write_audit(
                session,
                operator_id=user_id,
                source="SYSTEM",
                action="wecom_sync_follow_relations",
                business_type="integration",
                business_id=job.id,
                after={"success": 0, "fail": 1, "error": message},
            )
            await session.commit()


#: 本进程正在跑的外部联系人同步任务（单 worker 部署；只用于防重复启动/任务被回收）。
_external_sync_task: asyncio.Task | None = None


async def _run_external_contacts_background(job_id: int, user_id: int) -> None:
    """后台跑外部联系人同步：逐个成员拉列表，几十秒起步，不能占着网页请求。"""
    from types import SimpleNamespace

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        job = await session.get(WeComSyncJob, job_id, with_for_update=True)
        if job is None or job.status != "running":
            return
        try:
            await svc.sync_external_contacts(
                session, user=SimpleNamespace(id=user_id), job=job
            )
            await write_audit(
                session,
                operator_id=user_id,
                source="SYSTEM",
                action="wecom_sync_external_contacts",
                business_type="integration",
                business_id=job.id,
                after={"success": job.success_count, "fail": job.fail_count},
            )
            await session.commit()
        except Exception as error:  # noqa: BLE001 —— 后台任务必须把失败写回任务行
            logger.exception("外部联系人同步失败 job_id=%s", job_id)
            await session.rollback()
            job = await session.get(WeComSyncJob, job_id, with_for_update=True)
            if job is None:
                return
            message = _translate(error).message[:500] or "同步异常，请查看服务器日志"
            svc.finish_job(job, success=0, fail=1, error=message)
            await write_audit(
                session,
                operator_id=user_id,
                source="SYSTEM",
                action="wecom_sync_external_contacts",
                business_type="integration",
                business_id=job.id,
                after={"success": 0, "fail": 1, "error": message},
            )
            await session.commit()


@router.post("/integrations/wecom/sync-follow-relations")
async def sync_follow_relations(
    request: Request,
    user: CurrentUser = Depends(require_wecom_action("wecom:sync_contacts")),
    session: AsyncSession = Depends(get_db),
):
    global _follow_sync_task
    running = (
        await session.execute(
            select(WeComSyncJob)
            .where(
                WeComSyncJob.job_type == "follow_relation",
                WeComSyncJob.status == "running",
            )
            .order_by(WeComSyncJob.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if running is not None:
        return ok(
            svc.serialize_job(running),
            "跟进关系同步正在进行中，完成后会自动刷新，请稍候",
        )
    job = await svc.start_job(session, job_type="follow_relation", operator_id=user.id)
    await session.commit()
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_sync_follow_relations",
        business_type="integration",
        business_id=job.id,
        after={"status": "running"},
        ip=client_ip(request),
    )
    await session.commit()
    _follow_sync_task = asyncio.create_task(
        _run_follow_relations_background(job.id, user.id)
    )
    return ok(
        svc.serialize_job(job),
        "跟进关系同步已开始；联系人较多时需要几分钟，完成后页面会自动刷新",
    )


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
    user: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    """候选客户匹配（PRD §8.3 的第 2 步）。"""
    ensure_permission(user, "customer:view")
    return ok(await svc.unbound_candidates(session, contact_id, user=user))


@router.post("/integrations/wecom/unbound-contacts/{contact_id}/bind-customer")
async def bind_customer(
    contact_id: int,
    payload: BindCustomerRequest,
    request: Request,
    user: CurrentUser = Depends(require_wecom_action("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    """关联已有客户（PRD §8.3 的第 3 步之一）。"""
    ensure_permission(user, "customer:update")
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
    user: CurrentUser = Depends(require_wecom_action("wecom:manage")),
    session: AsyncSession = Depends(get_db),
):
    """创建新客户（PRD §8.3 的第 3 步之二）。"""
    ensure_permission(user, "customer:create")
    if not payload.name.strip():
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "客户名称必填", 422)
    # 指定别人一起跟进是分配动作，和客户中心的创建接口同一道口子。
    if payload.owner_id is not None:
        ensure_permission(user, "customer:assign")
    result = await svc.create_customer_from_contact(
        session, user=user, contact_id=contact_id, payload=payload.model_dump()
    )
    if payload.owner_id is not None:
        from app.modules.customer.followers import add_followers
        from app.modules.user.model import User

        target = await session.get(User, payload.owner_id)
        if target is None:
            raise AppError(ErrorCode.NOT_FOUND, f"跟进人 id={payload.owner_id} 不存在", 404)
        if target.status != "active":
            raise AppError(
                ErrorCode.PARAM_ERROR, f"「{target.name}」已停用，不能作为跟进人", 422
            )
        await add_followers(
            session,
            result["customer_id"],
            [payload.owner_id],
            source="manual",
            created_by=user.id,
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
    user: CurrentUser = Depends(require_wecom_action("wecom:manage")),
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


@router.get("/integrations/wecom/transfer-people")
async def transfer_people(
    user: CurrentUser = Depends(require_wecom_action("wecom:transfer_preview", "wecom:transfer")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(User.id, User.name, User.username, User.status).order_by(User.name, User.id)
    if "admin" not in user.roles:
        if not user.department_id:
            return ok([])
        stmt = stmt.where(User.department_id == user.department_id)
    return ok([dict(row) for row in (await session.execute(stmt)).mappings()])


@router.get("/integrations/wecom/transfer-preview")
async def transfer_preview(
    handover_user_id: int = Query(..., description="离职人（交接方）"),
    takeover_user_id: int = Query(..., description="接管人"),
    user: CurrentUser = Depends(require_wecom_action("wecom:transfer_preview", "wecom:transfer")),
    session: AsyncSession = Depends(get_db),
):
    """离职交接**清单预览**（只读，返工单 6.6）。

    执行前先看这个：按客户/商机/待办/打样/订单/草稿分类列出每项的
    当前责任人、拟接手人、状态与"不能交接的原因"。看清单和实际执行
    读的是同一份数据——不会出现"清单里没有、执行时却动了"。
    """
    await _ensure_transfer_department(session, user, {handover_user_id, takeover_user_id})
    return ok(
        await svc.preview_transfer(
            session,
            handover_user_id=handover_user_id,
            takeover_user_id=takeover_user_id,
        )
    )


@router.post("/integrations/wecom/transfer")
async def transfer(
    payload: TransferRequest,
    request: Request,
    user: CurrentUser = Depends(require_wecom_action("wecom:transfer")),
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
    await _ensure_transfer_department(session, user, {
        payload.handover_user_id, payload.takeover_user_id, *(payload.item_assignees or {}).values(),
    })
    job = await svc.transfer_relations(
        session,
        user=user,
        handover_user_id=payload.handover_user_id,
        takeover_user_id=payload.takeover_user_id,
        transfer_wecom=payload.transfer_wecom,
        # 逐项调整过的接手人（清单里某几项指定给别人）
        item_assignees=payload.item_assignees,
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
    # 提示语按**实际结果**拼，不再笼统说"继承完成"（返工单 6.7）：
    # 部分成功时要说清成功了什么、还剩什么、下一步去哪处理。
    parts = [
        f"客户 {detail.get('customers', 0)} 个、商机 {detail.get('opportunities', 0)} 个、"
        f"待办 {detail.get('tasks', 0)} 个、"
        f"打样跟单 {detail.get('samples', 0)} 张、"
        f"打样生产责任 {detail.get('sample_production', 0)} 张、"
        f"订单 {detail.get('orders', 0)} 单、草稿 {detail.get('order_drafts', 0)} 份"
        "（订单/打样转的是跟进责任，业绩归属不变）"
    ]
    if detail.get("wecom_transferred") or detail.get("wecom_failed"):
        parts.append(
            f"企微客户关系转接 {detail.get('wecom_transferred', 0)} 条"
        )
    frozen = int(detail.get("frozen") or 0)
    failed = int(detail.get("failures_count") or 0)
    tail = []
    if frozen:
        tail.append(f"{frozen} 个客户因撞单争议被冻结，主管裁定后可再次发起交接")
    if failed:
        tail.append(f"{failed} 项失败，可在本页的交接明细里逐条重试")
    headline = "继承完成" if not (frozen or failed) else "继承部分完成"
    message = f"{headline}：" + "、".join(parts)
    if tail:
        message += "；" + "；".join(tail)
    return ok(svc.serialize_job(job), message)


@router.get("/integrations/wecom/transfer/{job_id}")
async def get_transfer_job(
    job_id: int,
    user: CurrentUser = Depends(require_wecom_action("wecom:transfer")),
    session: AsyncSession = Depends(get_db),
):
    return ok(svc.serialize_job(await _get_transfer_for_user(session, user, job_id)))


@router.get("/integrations/wecom/transfer/{job_id}/items")
async def list_transfer_items(
    job_id: int,
    kind: str | None = Query(None, description="按类别过滤（customer/sample/...）"),
    pending_only: bool = Query(False, description="只看还没办完的"),
    crm_status: str | None = Query(
        None, description="按 CRM 侧逐项状态过滤（skipped=已跳过，frozen=已冻结）"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_wecom_action("wecom:transfer")),
    session: AsyncSession = Depends(get_db),
):
    """交接**逐项结果**（返工单 6.7：失败明细完整保存、界面可翻页）。

    12 条失败就是 12 条，不会再像原来那样在存储阶段被截断成 10 条。
    """
    await _get_transfer_for_user(session, user, job_id)
    items, total = await svc.list_transfer_items(
        session, job_id=job_id, page=page, page_size=page_size,
        kind=kind, pending_only=pending_only, crm_status=crm_status,
    )
    return ok(page_data(items, total, page, page_size))


@router.post("/integrations/wecom/transfer/{job_id}/retry")
async def retry_transfer(
    job_id: int,
    request: Request,
    user: CurrentUser = Depends(require_wecom_action("wecom:transfer")),
    session: AsyncSession = Depends(get_db),
):
    """按**逐项状态**重试没办完的项（返工单 6.7）。

    已经转出去的企微关系、已经改好的归属都不会再动一遍 ——
    整体重跑会把转接重复发一次，企微那边会报重复操作。

    ⚠️ **硬锁（WECOM_TRANSFER_ENABLED）与首次执行同档**（第六批审查第 3 条）：
    重试同样会向真实客户发起企微转接，此前这里漏了这道开关，
    关掉开关之后重试仍然能把转接发出去。人员在职、撞单争议、
    责任人是否已被调整这些校验在 svc.retry_transfer 内部统一做。
    """
    from app.core.config import settings as app_settings

    if not app_settings.wecom_transfer_enabled:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "离职继承已锁定：该操作会变更客户在微信里看到的服务人员。"
            "需业务确认后由管理员设置 WECOM_TRANSFER_ENABLED=1 才能执行",
            403,
        )
    job = await _get_transfer_for_user(session, user, job_id)
    if "admin" not in user.roles:
        detail = job.detail or {}
        people = {detail.get("handover_id"), detail.get("takeover_id")}
        people.update(await session.scalars(select(WeComTransferItem.to_owner_id).where(
            WeComTransferItem.job_id == job_id, WeComTransferItem.to_owner_id.is_not(None),
        )))
        if None in people:
            raise AppError(ErrorCode.FORBIDDEN, "这条历史交接记录缺少人员信息，请由管理员处理", 403)
        await _ensure_transfer_department(session, user, people)
    result = await svc.retry_transfer(session, user=user, job_id=job_id)
    await write_audit(
        session,
        operator_id=user.id,
        action="wecom_transfer_retry",
        business_type="integration",
        business_id=job_id,
        after=result,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        result,
        f"已重试 {result['retried']} 项，还剩 {result['remaining']} 项未完成",
    )


@router.get("/integrations/wecom/sync-jobs")
async def list_sync_jobs(
    job_type: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(WeComSyncJob)
    if "admin" not in user.roles:
        visible = WeComSyncJob.job_type != "transfer"
        if user.has("wecom:transfer"):
            visible |= WeComSyncJob.operator_id == user.id
        stmt = stmt.where(visible)
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
    user: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    job = await session.get(WeComSyncJob, job_id)
    if job is None:
        raise AppError(ErrorCode.NOT_FOUND, "同步任务不存在", 404)
    if job.job_type == "transfer":
        ensure_permission(user, "wecom:transfer")
        await _get_transfer_for_user(session, user, job_id)
    return ok(svc.serialize_job(job))


# ---- 事件回调：无需登录，靠签名校验 ---------------------------------------


def _require_wecom_callback_config() -> None:
    if not wecom_client.get_client().callback_ready():
        raise AppError(ErrorCode.WECOM_SYNC_FAILED, "企业微信回调未配置", 503)


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
    _require_wecom_callback_config()
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
    """验签后持久化事件，后台同步联系人，快速应答企微。"""
    _require_wecom_callback_config()
    raw_length = request.headers.get("content-length")
    try:
        if raw_length is not None and int(raw_length) > WECOM_CALLBACK_MAX_BODY_BYTES:
            raise AppError(ErrorCode.PARAM_ERROR, "企微回调报文超过 1 MB 限制", 413)
    except ValueError:
        raise AppError(ErrorCode.PARAM_ERROR, "企微回调 Content-Length 无效", 400) from None

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > WECOM_CALLBACK_MAX_BODY_BYTES:
            raise AppError(ErrorCode.PARAM_ERROR, "企微回调报文超过 1 MB 限制", 413)
        chunks.append(chunk)
    try:
        body = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise AppError(ErrorCode.PARAM_ERROR, "企微回调报文不是有效 UTF-8", 400) from None
    try:
        encrypt = wecom_callback.parse_event(body).get("Encrypt", "")
        if not encrypt:
            raise wecom_callback.WeComCallbackError("回调报文里没有 Encrypt 字段")
        # ⚠️ 时间戳时效（2026-10-14 审查）：原来 `timestamp` 只参与签名计算、
        # **从不校验时效**，又不按事件去重 —— 抓到一份合法报文就能**无限重放**，
        # 每次都插一条 `wecom_sync_jobs`（企微自身的重试还会放大这件事）。
        # 企微标准防护是"拒绝 5 分钟以外的 timestamp"，这里照做。
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            raise wecom_callback.WeComCallbackError("回调 timestamp 不是整数") from None
        if abs(int(time.time()) - ts) > WECOM_CALLBACK_MAX_SKEW_SECONDS:
            raise wecom_callback.WeComCallbackError(
                f"回调已过期（timestamp 与本机相差超过 {WECOM_CALLBACK_MAX_SKEW_SECONDS} 秒），拒绝重放"
            )
        wecom_callback.verify_signature(
            signature=msg_signature, timestamp=timestamp, nonce=nonce, encrypt=str(encrypt)
        )
        event = wecom_callback.parse_event(wecom_callback.decrypt(str(encrypt)))
    except wecom_callback.WeComCallbackError as error:
        raise AppError(ErrorCode.WECOM_SYNC_FAILED, str(error), 400) from error

    # 按**密文指纹**去重：同一份报文（哪怕时间戳还在窗口内）只记一次。
    # 指纹取密文的 sha256 前 32 位 —— 不存原文（密文含业务内容，没必要留在库里）。
    fingerprint = hashlib.sha256(str(encrypt).encode("utf-8")).hexdigest()[:32]
    already = (
        await session.execute(
            select(WeComSyncJob.id)
            .where(WeComSyncJob.event_fingerprint == fingerprint)
            .limit(1)
        )
    ).scalars().first()
    if already is not None:
        # 重复投递：按企微要求回 success（否则它会一直重试），但**不再落新任务**。
        return Response(content="success", media_type="text/plain")

    should_sync = svc.contact_change_id(event) is not None
    job = WeComSyncJob(
        job_type="contact_event" if should_sync else "event_callback",
        status="running" if should_sync else "success",
        success_count=0,
        fail_count=0,
        finished_at=None if should_sync else datetime.now(UTC),
        event_fingerprint=fingerprint,
        detail={"source": "event_callback", "event": event, "fingerprint": fingerprint},
    )
    session.add(job)
    try:
        await session.commit()
    except IntegrityError:
        # 两个相同回调并发时，唯一索引只允许一个落库；另一个按企微要求
        # 视为已处理并返回 success，不能把第三方重试风暴放大成 500。
        await session.rollback()
        duplicate = await session.execute(
            select(WeComSyncJob.id)
            .where(WeComSyncJob.event_fingerprint == fingerprint)
            .limit(1)
        )
        if duplicate.scalar_one_or_none() is None:
            raise
    # 企微要求回 "success"，否则会重试
    return Response(content="success", media_type="text/plain")


@router.get("/integrations/wecom/customer-import/preview")
async def preview_customer_import(
    user: CurrentUser = Depends(require_wecom_action("wecom:import_preview", "wecom:import")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.wecom.customer_import import preview_import
    return ok(await preview_import(session))


@router.post("/integrations/wecom/customer-import")
async def import_customers(
    payload: ImportCustomersRequest,
    user: CurrentUser = Depends(require_wecom_action("wecom:import")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.wecom.customer_import import execute_import
    ensure_permission(user, "customer:create")
    ensure_permission(user, "customer:assign")
    result = await execute_import(session, digest=payload.digest, operator_id=user.id)
    await session.commit()
    return ok(result, f"已导入 {result['import_contacts']} 条联系人，待确认 {result['held_contacts']} 条")
