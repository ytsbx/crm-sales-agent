"""合同/月结协议接口：模板维护（管理）、生成/签署/作废与台账（销售/主管）。"""

import logging
import json
import httpx

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data
from app.modules.contract import service as svc
from app.modules.contract.schema import DocumentCreate, DocumentSign, DocumentVoid, TemplateCreate
from app.modules.dingtalk import service as dingtalk_service
from app.modules.dingtalk.model import OaInstance, allowed_actions

router = APIRouter(tags=["Contract"])


def _contract_oa_resubmit(previous) -> bool:
    """本次提交是否该**换一轮重提**（在钉钉里另建一张新单）。

    口径与共享状态机同一份（`dingtalk/model.py` 的 `OA_STATUS_ACTIONS`）：
    `rejected` / `withdrawn` 明确结束、钉钉那边没有活单，重提是安全的；
    `pending`（可能正有一张活单）、`approved`（流程已走完）、`needs_review`
    （结果未知，可能已建单）一律不重提——各自要走的出口见 allowed_actions。
    """
    if previous is None:
        return False
    return "resubmit" in allowed_actions(previous.status, previous.resolve_state)


def _contract_oa_result(row, *, fresh: bool) -> dict[str, str]:
    """把 OA 行的真实状态翻成"这次到底发生了什么"，直接下发给前端。

    为什么不能一律报成功（2026-10-16 修）：`rejected` / `withdrawn` / `approved` /
    `needs_review` 这几种状态再点一次"提交钉钉签署"，`create_inquiry_instance`
    会**原样返回旧行、一个请求都不发**，而旧行的 `error` 是空的 —— 于是界面
    弹绿色的"已发起钉钉合同签署申请"，而钉钉那边什么都没有，用户以为发出去了。

    `level` 决定界面用哪种颜色：`success` 真的发出了；`warning` 是"状态本身如此、
    刷新后重试即可"（已通过 / 驳回 / 已撤回）；`error` 是"需要人去钉钉核对或
    重试也发不出去"。前端不能靠 status 自己猜，否则又是一份会分叉的口径。
    """
    if row.status == "pending" and row.instance_id:
        return {
            "level": "success",
            "message": "已发起钉钉合同签署申请" if fresh else "已重新发起钉钉合同签署申请",
        }
    if row.status == "approved":
        return {
            "level": "warning",
            "message": "这份合同的钉钉审批已经通过，无需再次发起；请上传客户回签文件",
        }
    if row.status == "rejected":
        return {
            "level": "warning",
            "message": (
                "上一次钉钉审批已被驳回，本次没有重新发起；"
                "请刷新台账后重试（会按新的一轮重提），或作废后重新生成合同"
            ),
        }
    if row.status == "withdrawn":
        return {
            "level": "warning",
            "message": (
                "上一次钉钉审批已撤回，本次没有重新发起；"
                "请刷新台账后重试（会按新的一轮重提）"
            ),
        }
    if row.status == "needs_review":
        return {
            "level": "error",
            "message": (
                "上一次发起的结果不明（钉钉那边可能已经建了审批单），本次不能自动重发；"
                "请先到钉钉按合同编号核对，再联系管理员处理"
            ),
        }
    if row.status == "submitting":
        return {
            "level": "error",
            "message": "这条审批正在发起中，请稍后刷新查看，不要重复点击",
        }
    return {
        "level": "error",
        "message": row.error or "本次没有向钉钉发起新的审批，请查看审批状态",
    }


@router.post("/contract-documents/{doc_id}/dingtalk-oa")
async def start_dingtalk_contract_approval(
    doc_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """发起合同签署 OA；审批过程在钉钉完成，CRM 只同步状态与过程快照。"""
    from app.modules.customer.model import Customer
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import QuoteVersion
    from app.modules.dingtalk.client import get_client
    from app.core.config import settings as app_settings
    from app.modules.settings import service as settings_service
    from app.modules.user.model import User

    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_in_scope(session, user, doc)
    if doc.status != "draft":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "只有待签合同可以发起钉钉签署申请", 422)
    if (doc.filled_data or {}).get("_source_type") != "customer_drafted":
        raise AppError(ErrorCode.PARAM_ERROR, "只有客户起草的合同需要发起钉钉签署申请", 422)
    cfg = await settings_service.get_setting(session, "dingtalk_contract_oa") or {}
    process_code = str(cfg.get("process_code") or "")
    if not process_code:
        raise AppError(ErrorCode.PARAM_ERROR, "尚未配置合同签署钉钉审批模板（dingtalk_contract_oa.process_code）", 422)
    from app.modules.contract.customer_flow import reject_suite_submission
    if not app_settings.dingtalk_push_off:
        await reject_suite_submission(get_client(), process_code)
    # 这一份合同在本模板上的历史轮次，用来决定"换一轮重提"还是原样返回。
    # **必须读在附件上传之前**：附件上传是真实的外部写操作，而"同轮重复点击"
    # 与"终态再点一次"这两种情况下 `create_inquiry_instance` 不会发起审批 ——
    # 先判定就不会白往钉盘传一份 PDF、留下没人认领的孤儿文件。
    previous = await dingtalk_service.get_by_business_key(
        session, inquiry_id=doc.id, inquiry_version=1, oa_type="contract"
    )
    resubmit = _contract_oa_resubmit(previous)
    if previous is not None and not resubmit and "retry" not in allowed_actions(previous.status, previous.resolve_state):
        outcome = _contract_oa_result(previous, fresh=False)
        if previous.status == "pending":
            outcome = {"level": "warning", "message": "该合同已在钉钉审批中，本次没有重复提交"}
        return ok({**dingtalk_service.serialize(previous), "result": outcome}, outcome["message"])

    account = await session.get(User, user.id)
    if account is None or account.status != "active":
        raise AppError(ErrorCode.UNAUTHORIZED, "账号不存在或已停用", 401)
    if app_settings.dingtalk_push_off:
        ding_user_id = ""
    else:
        ding_user_id = account.dingtalk_userid
        if not ding_user_id:
            raise AppError(ErrorCode.PARAM_ERROR, "找不到当前用户的钉钉身份，请先绑定钉钉账号", 422)
    customer = await session.get(Customer, doc.customer_id)
    header = (doc.filled_data or {}).get("_header") or {}
    order = await session.get(SalesOrder, doc.order_id) if doc.order_id else None
    quote_version = await session.get(QuoteVersion, doc.quote_version_id) if doc.quote_version_id else None
    amount = (getattr(quote_version, "total_amount", None) if quote_version else None) or (getattr(order, "total_amount", None) if order else None)
    description = "\n".join(
        item for item in [
            f"客户：{customer.name}" if customer else "",
            f"合同编号：{doc.doc_no}",
            f"订单：{header.get('order_no')}" if header.get("order_no") else "",
            f"报价：{header.get('quote_no')}" if header.get("quote_no") else "",
            "合同正文：",
            doc.content_snapshot,
        ] if item
    )
    source_values = {
        "doc_no": doc.doc_no,
        "title": doc.title,
        "customer_name": customer.name if customer else "",
        "order_no": header.get("order_no") or "",
        "quote_no": header.get("quote_no") or "",
        "start_type": "上传文件发起",
        "number_rule_type": "手动填写",
        "contract_number": doc.doc_no,
        "amount_type": "收入" if amount not in (None, "") else "无金额",
        "amount": str(amount or 0),
        "term_type": "固定期限" if doc.expiry_date else "无固定期限",
        "period_start": doc.effective_date.isoformat() if doc.effective_date else "",
        "period_end": doc.expiry_date.isoformat() if doc.expiry_date else "",
        "sign_mode": "纸质签署",
        "description": description,
        "biz_id": doc.doc_no,
        "operation_status": "待签署",
        "performance_status": "未开始",
        "sign_note": "由 CRM 发起，审批过程在钉钉完成",
    }
    mapping = cfg.get("field_map") or {}
    field_map = {
        str(mapping[key]): value
        for key, value in source_values.items()
        if mapping.get(key) and value not in (None, "")
    }
    originator_component = cfg.get("originator_component")
    if originator_component:
        field_map[str(originator_component)] = ding_user_id or f"（待查询：{user.name}的钉钉编号）"
    dept_component = cfg.get("dept_component")
    if dept_component:
        if app_settings.dingtalk_push_off:
            field_map[str(dept_component)] = "（待查询：钉钉部门编号）"
        else:
            dept_ids = await get_client().get_user_dept_ids(ding_user_id)
            if not dept_ids:
                raise AppError(ErrorCode.PARAM_ERROR, "钉钉未返回你的提报部门，请先核对员工部门资料", 422)
            field_map[str(dept_component)] = dept_ids[0]
    # 正文同时写入多行控件（便于直接在 OA 里阅读），并可上传生成 PDF 作为完整附件，
    # 避免长合同受 OA 文本控件长度限制而被截断。
    content_component = cfg.get("content_component")
    if content_component:
        field_map[str(content_component)] = doc.content_snapshot
    attachment_component = cfg.get("attachment_component")
    if (doc.filled_data or {}).get("_original_file_id") and not attachment_component:
        raise AppError(ErrorCode.PARAM_ERROR, "未配置合同正文附件字段，本次不能提交空内容审批", 422)
    if attachment_component:
        if app_settings.dingtalk_push_off:
            field_map[str(attachment_component)] = "（待上传：合同生成 PDF）"
        else:
            from app.modules.dingtalk.attachments import upload_approval_attachment
            from app.modules.file.model import FileRecord
            from app.modules.file.storage import absolute_path
            from app.modules.dingtalk.client import DingTalkError
            source_file_id = (doc.filled_data or {}).get("_original_file_id") or doc.generated_file_id
            if not source_file_id:
                raise AppError(ErrorCode.PARAM_ERROR, "合同生成稿尚未落盘，暂不能上传到钉钉", 422)
            record = await session.get(FileRecord, source_file_id)
            path = absolute_path(record.object_key) if record else None
            if record is None or path is None or not path.is_file():
                raise AppError(ErrorCode.PARAM_ERROR, "合同生成 PDF 不存在，请重新生成合同", 422)
            from app.modules.contract.customer_flow import verified_content
            content = verified_content(record)
            try:
                attachment = await upload_approval_attachment(
                    get_client(), user_id=ding_user_id, filename=record.file_name, content=content
                )
            except (DingTalkError, httpx.HTTPError, OSError, ValueError) as exc:
                raise AppError(ErrorCode.PARAM_ERROR, "合同 PDF 未成功上传到钉钉，本次尚未发起审批", 422) from exc
            field_map[str(attachment_component)] = json.dumps([attachment], ensure_ascii=False)
    row = await dingtalk_service.create_inquiry_instance(
        session,
        user=user,
        inquiry_id=doc.id,
        inquiry_version=1,
        customer_id=doc.customer_id,
        process_code=process_code,
        originator_user_id=ding_user_id,
        field_map=field_map,
        oa_type="contract",
        resubmit=resubmit,
    )
    # **展开合并，不能整体替换**（2026-10-16 修）：轮询回写进 `_dingtalk_oa` 的是
    # `oa_instance_id / instance_id / status / synced_at / result` 五个键，
    # 而轮询只捞 `status == "pending"` 的行。原先这里只写两个键，于是审批**结束后**
    # 再点一次"提交钉钉签署"（前端在终态下正好显示这个按钮），就会把节点、处理时间、
    # 意见和最终结果**永久抹掉**（终态行再也不会被轮询回来）——
    # 文档要的"过程和结果同步到合同详情"在这份合同上就此失效。
    previous_oa = (doc.filled_data or {}).get("_dingtalk_oa") or {}
    doc.filled_data = {
        **(doc.filled_data or {}),
        "_dingtalk_oa": {
            **previous_oa,
            "oa_instance_id": row.id,
            "instance_id": row.instance_id,
            "status": row.status,
        },
    }
    await write_audit(
        session,
        operator_id=user.id,
        action="start_contract_oa",
        business_type="contract",
        business_id=doc.id,
        after={
            "oa_instance_id": row.id,
            "status": row.status,
            "process_code": process_code,
            "resubmit": resubmit,
            "submit_round": row.submit_round,
        },
        ip=client_ip(request),
    )
    await session.commit()
    # 返回值同时带上 OA 行、这次到底发生了什么的说明、以及该用什么颜色 ——
    # 客户端的 `unwrap()` 只取 `data`、会丢掉响应 message（见 shared/api/client.ts），
    # 所以"真实结果"必须走 data，不能只写在 message 里。
    outcome = _contract_oa_result(row, fresh=not resubmit)
    return ok({**dingtalk_service.serialize(row), "result": outcome}, outcome["message"])


@router.post("/contract-documents/{doc_id}/dingtalk-oa-withdraw")
async def withdraw_dingtalk_contract_approval(
    doc_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """撤回仍在钉钉流转中的合同审批；已完成的审批不能从 CRM 撤回。"""
    from app.core.config import settings as app_settings
    from app.modules.dingtalk import service as dingtalk_service
    from app.modules.dingtalk.model import OaInstance

    doc = await svc.get_doc_or_404(session, doc_id, for_update=True)
    await _ensure_doc_in_scope(session, user, doc)
    row = (
        await session.execute(
            select(OaInstance)
            .where(OaInstance.inquiry_id == doc.id, OaInstance.oa_type == "contract")
            .order_by(OaInstance.submit_round.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalars().first()
    if row is None or not row.instance_id:
        raise AppError(ErrorCode.NOT_FOUND, "这份合同还没有可撤回的钉钉审批", 404)
    if row.status != "pending":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, f"当前钉钉审批状态为「{row.status}」，不能撤回", 422)
    if app_settings.dingtalk_push_off:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "钉钉推送已关闭，无法向钉钉发送撤回请求", 422)
    from app.modules.contract.customer_flow import withdraw_before_void
    await withdraw_before_void(session, user, doc)
    if row.status != "withdrawn":
        await session.commit()
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "审批已经结束，不能撤回；状态已同步，请刷新", 422)
    await write_audit(
        session,
        operator_id=user.id,
        action="withdraw_contract_oa",
        business_type="contract",
        business_id=doc.id,
        after={"oa_instance_id": row.id, "instance_id": row.instance_id, "status": row.status},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(dingtalk_service.serialize(row), "已向钉钉发起撤回，审批单已撤回")


@router.get("/contract-templates")
async def list_templates(
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.list_templates(session))


@router.post("/contract-templates")
async def create_template(
    payload: TemplateCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新增模板=新增版本；旧版本保留，已生成的文档钉死当时的版本。"""
    template = await svc.create_template(session, payload=payload, user_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="create_template",
        business_type="contract_template",
        business_id=template.id,
        after={"name": template.name, "doc_type": template.doc_type, "version": template.version},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_template(template, is_current=True), "模板版本已保存")


@router.get("/contract-documents")
async def list_documents(
    customer_id: int | None = Query(None),
    status: str | None = Query(None),
    # 业务详情页用：订单/报价详情页只看"这一单相关的合同"，不拉整个台账
    order_id: int | None = Query(None),
    quote_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    owner_ids = await scoped_owner_ids(session, user)
    items, total = await svc.list_documents(
        session,
        owner_ids=owner_ids,
        customer_id=customer_id,
        status=status,
        order_id=order_id,
        quote_id=quote_id,
        page=page,
        page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


logger = logging.getLogger(__name__)


@router.post("/contract-documents")
async def generate_document(
    payload: DocumentCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.customer import service as customer_service
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote

    # 补充协议 / 续签是**管理动作**：勾了 supersede 会结束原协议的全部待办，
    # 并把 `_superseded_by` 写进**已签**文档——效果接近作废，而作废已经限定主管。
    # 不设这道门，业务员用"续签"就能绕过"已签作废要主管"这条约束（已确认口径）。
    if payload.parent_id is not None and not _is_manager(user):
        raise AppError(ErrorCode.FORBIDDEN, "补充协议 / 续签需要主管权限", 403)

    customer = await customer_service.get_visible_customer(session, user, payload.customer_id)
    await ensure_in_scope(session, user, owner_id=customer.owner_id, label="客户")
    if payload.order_id is not None:
        order = await session.get(SalesOrder, payload.order_id)
        if order is None:
            raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
        if order.customer_id != customer.id:
            raise AppError(ErrorCode.PARAM_ERROR, "所选订单不属于该客户", 422)
        await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
    if payload.quote_id is not None:
        quote = await session.get(Quote, payload.quote_id)
        if quote is None:
            raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)
        if quote.customer_id != customer.id:
            raise AppError(ErrorCode.PARAM_ERROR, "所选报价不属于该客户", 422)
        await ensure_in_scope(session, user, owner_id=quote.owner_id, label="报价单")
    if payload.order_id is not None and payload.quote_id is not None:
        if order.quote_id is not None and order.quote_id != quote.id:
            raise AppError(ErrorCode.PARAM_ERROR, "所选报价与订单不匹配", 422)
    if payload.original_file_id:
        from app.modules.contract.customer_flow import verify_original
        await verify_original(session, user, payload.original_file_id)
    doc, replayed = await svc.generate_document(session, payload=payload, user_id=user.id)
    if replayed:
        # 重复请求（重试 / 连点两次）：既不建新单，也不再写一条审计 ——
        # 审计里出现两条「生成」会让人以为真生成了两份。
        return ok(
            svc.serialize_document(doc, customer_name=customer.name),
            "这份合同刚才已经生成过了，返回的是同一份",
        )
    if payload.source_type == "customer_drafted":
        # 唯一键保证重试不会重复创建“上传客户回签文件”待办。
        from app.modules.task.model import Task
        owner_id = customer.owner_id or user.id
        exists = await session.execute(
            select(Task.id).where(Task.source_key == f"contract:signed-upload:{doc.id}").limit(1)
        )
        if exists.scalar_one_or_none() is None:
            session.add(Task(
                title=f"上传客户回签合同：{doc.doc_no}",
                task_type="contract_signed_upload",
                customer_id=doc.customer_id,
                owner_id=owner_id,
                priority="high",
                status="pending",
                source="system",
                source_business_type="contract",
                source_business_id=doc.id,
                source_key=f"contract:signed-upload:{doc.id}",
            ))
    # ⚠️ **从"PDF 落盘"到"提交成功"整段都要能清理**（第四批复审 P1，2026-10-12）。
    #
    # 我上一版只把 `session.commit()` 那一步放进了 try，于是**写审计失败**这条路
    # 漏了：PDF 已落盘、数据库回滚、孤儿 PDF 留着（故障注入实测：合同 0 条、
    # 文件记录 0 条、PDF 仍在、删除调用 0 次）。
    #
    # 所以 try 从**落盘之前**就开始，覆盖中间的每一次 await。
    # 清理一律走 `svc.cleanup_pending_objects()` —— 它**先核查数据库再删**，
    # 因为"commit 抛异常"不等于"没提交"（见那个函数的说明）。
    try:
        # 生成即把 PDF 渲染一次落盘（带 sha256）。之后无论客户改几次名，
        # 下载拿到的都是这一份——同一编号两次下载内容不同是对外文件的硬伤。
        if payload.original_file_id:
            from app.modules.file.model import BusinessFile
            session.add(BusinessFile(business_type="contract", business_id=doc.id,
                                     file_id=payload.original_file_id, category="contract_original"))
        else:
            await svc.attach_generated_pdf(session, doc, user_id=user.id)
        # 补充协议 / 续签是**一次真实的业务动作**，要进客户时间线；
        # 普通草稿生成不记——草稿不代表签约（审查第 9 条明确要求）。
        if doc.parent_id:
            parent_doc = await svc.get_doc_or_404(session, doc.parent_id)
            supersedes = (doc.filled_data or {}).get("_supersedes") or {}
            label = "月结协议续签" if doc.doc_type == "monthly" else "新增补充协议"
            extra = ""
            if supersedes:
                extra = f"；已替代原协议 {supersedes.get('doc_no')}"
                if supersedes.get("cancelled_tasks"):
                    extra += f"，同时结束了 {supersedes['cancelled_tasks']} 条在办提醒"
            await _record_contract_event(
                session,
                doc,
                user=user,
                action="amend",
                title=label,
                content=f"{label} {doc.doc_no}，基于 {parent_doc.doc_no}{extra}",
            )
        await write_audit(
            session,
            operator_id=user.id,
            action="generate",
            business_type="contract",
            business_id=doc.id,
            after={"doc_no": doc.doc_no, "template_id": doc.template_id,
                   "customer_id": doc.customer_id, "order_id": doc.order_id,
                   "quote_id": doc.quote_id, "quote_version_id": doc.quote_version_id,
                   "generated_file_id": doc.generated_file_id},
            ip=client_ip(request),
        )
        await session.commit()
    except BaseException:  # noqa: BLE001
        # ⚠️ 捕 **`BaseException`**，不是 `Exception`（第四批复审 P2，2026-10-12）。
        #
        # `asyncio.CancelledError` 继承 `BaseException` —— 写 `except Exception`
        # **捕不到取消**：客户端断开、请求超时、服务优雅停机都会取消这个协程，
        # 于是清理被跳过。复审实测（PDF 落盘后、写审计处注入取消）：
        # 合同 0 条、文件记录 0 条，**存储仍有 1 份 PDF、待清理登记仍有 1 条**。
        #
        # 清理走 `_cancel_safe`：用 `asyncio.shield` 让这段**扛住取消跑完**，
        # 然后再把取消原样抛出去 —— 取消语义不变，只是"先收拾干净再走"。
        # 核查仍然照旧：查得到就保留（已提交的原件不许删），查不到才删。
        await svc._cancel_safe(
            svc.cleanup_pending_objects(session), label="合同 PDF 清理"
        )
        raise
    # 成功路径：**注销登记**。不清的话，本请求会话上会一直挂着"待删"的键 ——
    # 会话被复用（同一个请求里后面还有别的逻辑）时，那些键可能被当成待清理对象。
    svc.take_pending_objects(session)

    result = svc.serialize_document(doc, customer_name=customer.name)
    missing = result.get("missing_fields") or {}
    if missing:
        # 缺项必须说出来：正文里留着 {{...}}，不报一声用户会以为模板坏了、或者数据丢了。
        # 这里只提示、不拦——"必填项不齐就不许生成对外文件"是模板级配置，属于下一批。
        return ok(result, f"合同草稿已生成；有 {len(missing)} 处没填上（正文里保留了占位符）")
    return ok(result, "合同草稿已生成")


@router.get("/contract-documents/{doc_id}")
async def get_document(
    doc_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_in_scope(session, user, doc)
    from app.modules.customer.model import Customer

    customer = await session.get(Customer, doc.customer_id)
    result = svc.serialize_document(doc, customer_name=customer.name if customer else None)
    # 关系链两头都给：往上是"基于哪一份"（补充协议 / 续签），往下是"被哪几份补充过"。
    # 父文档用普通 get 而不是 get_doc_or_404：父件被软删时不该让子件详情也打不开。
    if doc.parent_id:
        from app.modules.contract.model import ContractDocument

        parent = await session.get(ContractDocument, doc.parent_id)
        result["parent_doc_no"] = parent.doc_no if parent else None
    result["amendments"] = await svc.list_amendments(session, doc.id)
    # 签署原件清单：已签状态下前端要有"看签回来的那一份"的入口。
    # 不给的话，用户点「下载」拿到的是生成稿，却以为拿的是签署件。
    result["signed_files"] = await svc.list_signed_files(session, doc.id)
    # **OA 行的活数据**（2026-10-16 加）：`filled_data._dingtalk_oa` 只是快照，
    # 里面没有行 id、也没有 `allowed_actions` —— 而"结果待人工核对"时的
    # 认领/重发/作废三个按钮**必须**按服务端给的动作清单渲染（前端自己判状态
    # 就是重复建实例的入口）。这里把最新一轮整行给出，口径与合同详情一致。
    latest_oa = (
        await session.execute(
            select(OaInstance)
            .where(OaInstance.inquiry_id == doc.id, OaInstance.oa_type == "contract")
            .order_by(OaInstance.submit_round.desc(), OaInstance.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if latest_oa is not None:
        result["dingtalk_oa_row"] = dingtalk_service.serialize(latest_oa)
    if result.get("dingtalk_oa"):
        from app.modules.contract.customer_flow import named_history
        result["dingtalk_oa"] = await named_history(session, result["dingtalk_oa"])
    return ok(result)


@router.post("/contract-documents/{doc_id}/sign")
async def sign_document(
    doc_id: int,
    payload: DocumentSign,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    doc = await svc.get_doc_or_404(session, doc_id, for_update=True)
    await _ensure_doc_in_scope(session, user, doc)
    from app.modules.file.access import can_access_file

    if not await can_access_file(session, user, payload.file_id):
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "无权将该文件登记为此合同的签署件", 403)
    await svc.sign_document(session, doc, file_id=payload.file_id, note=payload.note)
    # 接入客户时间线与通知（审查第 9 条）：审计回答"谁动了这个接口"，
    # 客户时间线回答"这个客户身上发生了什么"——两者用途不同，只写审计不够。
    # 复用打样/订单在用的同一套机制（写客户留痕 + 排主管通知），不另起一套。
    await _record_contract_event(
        session,
        doc,
        user=user,
        action="sign",
        title="合同已签署",
        content=f"合同 {doc.doc_no}（{doc.title}）已登记签署",
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="sign",
        business_type="contract",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "file_id": payload.file_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_document(doc), "已登记签署")


@router.get("/contract-documents/{doc_id}/download")
async def download_document(
    doc_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """下载**生成稿**（§3.6）。

    生成的时候就把 PDF 渲染一次落盘了，这里返回当时那一份——客户后来改名、
    公司换抬头、报价出了 V2，都不会让已经发出去的那份合同跟着变。
    只有本批之前生成的老数据（没有 `generated_file_id`）才回落到实时渲染。

    这是**生成稿**，不是签署原件：签回来的扫描件在详情接口的 `signed_files` 里，
    下载不等于已签，签了才算签。
    """
    import asyncio
    import hashlib

    from fastapi import Response

    from app.modules.contract.pdf import render_contract_pdf
    from app.modules.file import storage
    from app.modules.file.model import FileRecord

    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_in_scope(session, user, doc)
    original_id = (doc.filled_data or {}).get("_original_file_id")
    if original_id:
        from app.modules.contract.customer_flow import verified_content
        from urllib.parse import quote
        record = await session.get(FileRecord, original_id)
        content = verified_content(record)
        await write_audit(session, operator_id=user.id, action="download", business_type="contract", business_id=doc.id,
                          after={"doc_no": doc.doc_no, "source": "customer_original", "file_id": original_id})
        await session.commit()
        return Response(content=content, media_type=record.mime_type or "application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(record.file_name)}"})


    content: bytes | None = None
    source = "generated"
    if doc.generated_file_id:
        record = await session.get(FileRecord, doc.generated_file_id)
        missing: str | None = None
        if record is None:
            missing = "存档的文件记录已不存在"
        else:
            try:
                path = storage.absolute_path(record.object_key)
            except AppError:
                # ⚠️ `absolute_path` 对非法 object_key（目录穿越形状）抛的是 `AppError`，
                # 而它**不是** `OSError` —— 原来这个 except 只捕 OSError，于是那种情况
                # 会一路冒成 500，**绕过了下面那条"存档不可用 + 单独记审计 + 410"**的
                # 既定出口（2026-10-14 审查修）。object_key 正常只有系统写入，
                # 所以这不是可达的常见路径，但让两种"读不到"走同一个出口才对。
                missing = "存档文件的路径不合法（无法定位原件）"
            else:
                try:
                    content = await asyncio.to_thread(path.read_bytes)
                except OSError:
                    missing = "存档文件已不在磁盘上"
        # 校验值也要比：它是"这份原件没被换过"的唯一依据，光有文件不够
        if content is not None and record is not None and record.checksum:
            actual = hashlib.sha256(content).hexdigest()
            if actual != record.checksum:
                content = None
                missing = "存档文件与登记时的校验值不一致（内容被改动过）"
        if content is None:
            # **明确报错，不再静默重新渲染**（第十一批 11.3）。
            # 原实现是"读不到就当场重新排一份给你、HTTP 200"——用户会以为自己拿到的
            # 就是当初那份存档，台账上"同一编号永远同一份"这句承诺也就破了。
            # 先把这次异常**单独提交**记进审计（报错会把事务回滚掉，不先落库就查不到），
            # 便于事后从备份恢复。
            await write_audit(
                session,
                operator_id=user.id,
                action="download_original_missing",
                business_type="contract",
                business_id=doc.id,
                after={
                    "doc_no": doc.doc_no,
                    "reason": missing,
                    "generated_file_id": doc.generated_file_id,
                },
            )
            await session.commit()
            raise AppError(
                ErrorCode.NOT_FOUND,
                f"这份合同的存档原件不可用（{missing}）。"
                "为免把重新生成的稿子当成原件，这里不再返回 PDF；请从备份恢复原件",
                410,
            )
    else:
        # 本批之前生成的旧记录**本来就没有存档**（当时还没落盘这一步）。
        # 这类仍按历史数据渲染一份，但必须在文件名与响应头里标明它是**副本**，
        # 不能让它看起来像原存档（11.3 第 4 条）。
        source = "legacy_rendered"
        data = await svc.build_pdf_data(session, doc)
        # reportlab 渲染是同步 CPU 密集操作，丢线程池避免卡住事件循环（与报价 PDF 同）
        content = await asyncio.to_thread(render_contract_pdf, data)

    # 下载留痕：合同是含价格与条款的对客文件，谁在什么时候取了哪一份要能查
    # （作废件也一样取得到，只是纸上必须带"已作废"，所以这里记状态）。
    # `source` 记的是"这份内容从哪来"，用于分辨有没有回落到重新渲染。
    await write_audit(
        session,
        operator_id=user.id,
        action="download",
        business_type="contract",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "status": doc.status, "source": source},
    )
    await session.commit()
    from urllib.parse import quote

    filename = (
        f"{doc.doc_no}（依据历史数据生成的副本）.pdf"
        if source == "legacy_rendered"
        else f"{doc.doc_no}.pdf"
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            # `filename*` 走 RFC 5987 编码：副件的文件名带中文，直接塞进
            # `filename=` 会乱码或被截断
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            # 页面/调用方据此分辨拿到的是"当时的存档"还是"后来的副本"
            "X-Contract-Source": source,
        },
    )


@router.post("/contract-documents/{doc_id}/void")
async def void_document(
    doc_id: int,
    payload: DocumentVoid,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    doc = await svc.get_doc_or_404(session, doc_id, for_update=True)
    await _ensure_doc_in_scope(session, user, doc)
    # 已签合同的作废要主管点头（业务方 2026-10-05 定）：未签草稿业务员自己处理，
    # 但「已签」是台账上的既成事实，不该随手被改掉。
    if doc.status == "signed" and not _is_manager(user):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "已签署的合同要作废，需要主管权限；未签草稿你自己处理即可",
            403,
        )
    from app.modules.contract.customer_flow import withdraw_before_void
    await withdraw_before_void(session, user, doc)
    cancelled = await svc.void_document(session, doc, reason=payload.reason)
    await _record_contract_event(
        session,
        doc,
        user=user,
        action="void",
        title="合同已作废",
        content=(
            f"合同 {doc.doc_no}（{doc.title}）已作废，原因：{payload.reason}"
            + (f"；同时结束了 {cancelled} 条在办的到期待办" if cancelled else "")
        ),
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="void",
        business_type="contract",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_document(doc), "文档已作废")


#: 主管口径：与新品洞察 / 案例库评审共用同一套判断（见 product_insight/router.py）。
#: 项目里已经有两份同样的常量，这里是第三份——抽成共享常量要动那两个模块，
#: 不属这一批的范围，先就地写清楚出处，别让它悄悄漂移。
_REVIEWER_ROLES = ("sales_manager", "admin")


def _is_manager(user) -> bool:
    """主管或管理员。用于"已签合同作废"这类需要管理动作的场合。"""
    return any(role in _REVIEWER_ROLES for role in user.roles) or user.has("settings:manage")


async def _record_contract_event(
    session: AsyncSession,
    doc,
    *,
    user,
    action: str,
    title: str,
    content: str,
) -> None:
    """把合同上的一次真实动作写进**客户时间线**并推主管通知。

    审计回答"谁动了这个接口"，客户时间线回答"这个客户身上发生了什么"——
    两者用途不同，只写审计是不够的（审查第 9 条）。

    复用订单/打样在用的那一套（`record_and_notify`），不另起一套事件机制；
    幂等键按「动作:文档id」确定性生成，重放/重试命中同一条——
    客户时间线只留一条、主管只收一次。

    注意：**只有真实业务动作才走这里**。生成草稿不记——草稿不代表签约
    （审查原话："草稿生成不能记成已经签约"）。
    """
    from app.modules.customer.model import Customer
    from app.modules.followup import service as followup_service

    customer = await session.get(Customer, doc.customer_id)
    await followup_service.record_and_notify(
        session,
        customer_id=doc.customer_id,
        owner_id=customer.owner_id if customer else None,
        title=title,
        content=content,
        # ⚠️ **必须传 `customer`，不能传 `contract`**（2026-10-14 审查修）。
        #
        # 这两个值会被 `record_and_notify` 原样当成通知的 `source_type/source_id`，
        # 而通知侧靠 `source_type` 做两件事：
        #   ① `notification/service.py` 的 `models` 字典里取来源对象 —— 里面只有
        #      customer/lead/opportunity/quote/order/sample，**没有 contract**
        #      → `source=None` → 每个收件人都被 `continue`，`count=0`，
        #      而事件仍被标 `notification_processed_at`；
        #   ② 收件人权限判据 `viewer.has(f"{source_type}:view")` —— 全仓**没有
        #      `contract:view` 这个权限码**（合同用的是 order:view / order:manage），
        #      所以把 contract 加进字典也照样全被刷掉。
        #
        # 后果：合同签署/作废/续签的"推业务主管"**完全失效且无声无息** ——
        # 业务留痕已先写入，客户时间线看着一切正常，也不会进
        # `delivery_failure_summary` 的 business_pending。
        #
        # 走 `customer` 这条已有通路是对的：客户一定存在、`customer:view` 是真实权限、
        # 数据范围判据（含公海 `owner_id IS NULL`）自动生效，且通知中心可见性与
        # 生成侧共用同一份 `process_notification_filter`。
        # `event_key` 仍用 `contract:{action}:{doc.id}`，幂等不受影响。
        #
        # 注意：本文件另外 5 处 `business_type="contract"` 是 `write_audit` 的参数，
        # 那是**审计**的 business_type（按 doc.id 记流水），语义正确，**不要一起改**。
        business_type="customer",
        business_id=doc.customer_id,
        operator_id=user.id,
        event_key=f"contract:{action}:{doc.id}",
    )


async def _ensure_doc_in_scope(session: AsyncSession, user, doc) -> None:
    """详情/生成稿/签署附件**共用**的归属判据（第三批审查 P2，2026-10-12）。

    规则只留一份，落在 `contract/visibility.py`：客户当前负责人 → 订单负责人
    → 创建人。抽出来的原因：附件走的是通用文件入口，那边原来只认客户当前
    负责人，于是"客户进公海、负责人清空"时详情能看、原件下不了。
    """
    from app.modules.contract.visibility import contract_scope_owner_id

    owner_id = await contract_scope_owner_id(session, doc)
    await ensure_in_scope(session, user, owner_id=owner_id, label="合同文档")
