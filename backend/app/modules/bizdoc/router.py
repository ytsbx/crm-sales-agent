"""对外单据接口：模板版本 + 打样需求单 / 下单文件的生成与下载（场景12）。

授权口径（第八批 §8.5 中属于 bizdoc 的那部分）：**不把 order:view / order:manage
当成所有对外单据的总开关**。列表逐类型过滤，详情/下载/作废按 `doc_type`
判来源模块权限——只有报价权限的人能下载自己出的报价单，只有订单权限的人
拿不到报价/打样文件。生成入口仍各自要求来源模块的维护权限。

第八批 §8.9 / §8.10 在本层落的两件事：

- **生成请求幂等**：三个生成入口都接受可选 `request_key`（body 字段或
  `X-Request-Key` 头）。同一把键重试返回**原来那一份**，不再多建一份带独立
  编号的文件；要明确出新版就用**新键**，所以"内容完全一样的合法新版"不会被挡。
  没带键不是报错，但响应里会明说"本次未带幂等键"，不假装重试是安全的。
- **下载读存档原件**：生成时已经把 xlsx/pdf 的字节存档了，下载不再重新渲染。
  历史无存档的文件明确标"由历史快照重建"；存档读不到时**拒绝下载并告警**，
  不静默拿当前资料重渲染顶替。作废件的"已作废"展示走 `mode=state` 的状态副本。
"""

import asyncio
import hashlib
import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import request_key_from
from app.core.response import ok
from app.modules.bizdoc import service as svc
from app.modules.bizdoc.model import (
    DOC_TYPE_LABEL,
    has_doc_permission,
)

logger = logging.getLogger("crm.bizdoc")

router = APIRouter(tags=["BizDoc"])


class TemplateCreate(BaseModel):
    doc_type: str
    name: str = Field(min_length=1, max_length=120)
    body: str = ""
    enabled: bool = True
    remark: str | None = None


class TemplatePreview(BaseModel):
    doc_type: str
    body: str = ""
    extra_fields: dict[str, str] | None = None


class SampleDocGenerate(BaseModel):
    sample_request_id: int
    template_id: int | None = None
    extra_fields: dict[str, str] | None = None
    #: 幂等键（§8.9）：前端打开"生成"入口时生成一把，同一次生成的重复提交带同一把。
    #: 只放 body 不够——脚本/移动端重试更习惯放 `X-Request-Key` 头，两个入口都留。
    request_key: str | None = Field(default=None, max_length=128)


class OrderDocGenerate(BaseModel):
    order_id: int
    template_id: int | None = None
    extra_fields: dict[str, str] | None = None
    #: 模板变量填不出来时是否允许落"草稿"（§8.8）。默认 False = 直接拒绝出图；
    #: 明确传 True 才出草稿，且草稿在台账里标"草稿（非正式对外文件）"。
    allow_draft: bool = False
    request_key: str | None = Field(default=None, max_length=128)


class QuoteDocGenerate(BaseModel):
    """对客报价单按**报价版本**生成：金额必须来自那一版，而不是"当前价"。"""

    quote_version_id: int
    template_id: int | None = None
    extra_fields: dict[str, str] | None = None
    request_key: str | None = Field(default=None, max_length=128)


class VoidPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


@router.get("/biz-doc-templates")
async def list_templates(
    doc_type: str | None = Query(None),
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """对外单据模板（按类型多版本并存）。"""
    return ok(await svc.list_templates(session, doc_type))


@router.post("/biz-doc-templates/preview")
async def preview_template(
    payload: TemplatePreview,
    user: CurrentUser = Depends(require_permission("settings:manage")),
):
    """模板预览：用示例值填一遍，把填不出来的变量逐条列出来（§8.8）。

    只回 JSON、不落库、不出文件；正文里同样不会残留 `{{...}}` 模板语法。
    """
    if payload.doc_type not in DOC_TYPE_LABEL:
        raise AppError(ErrorCode.PARAM_ERROR, "不支持的单据类型", 422)
    return ok(await asyncio.to_thread(
        svc.preview_template_body, payload.body, payload.extra_fields
    ))


@router.post("/biz-doc-templates")
async def create_template(
    payload: TemplateCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新增一版模板：**不覆盖旧版**，已生成的文件仍指向它们当时用的那一版。

    保存时先校验变量写法（§8.8）：拼错的 `{{customer.nmae}}` 在这里就被拒，
    而不是等某天生成文件时才发现"文件出不来"。

    版本号分配走 `svc.create_template_version`（§8.9）：它先锁同类型序列再取号，
    两个管理员同时保存会排成 V(n)、V(n+1)，而不是后者撞唯一约束变 500。
    """
    if payload.doc_type not in DOC_TYPE_LABEL:
        raise AppError(ErrorCode.PARAM_ERROR, "不支持的单据类型", 422)
    analysis = svc.analyze_template_body(payload.body)
    if analysis["unsupported"]:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "模板变量不受支持：" + "；".join(
                item["message"] for item in analysis["unsupported"][:5]
            ),
            422,
        )
    template = await svc.create_template_version(
        session,
        doc_type=payload.doc_type,
        name=payload.name,
        body=payload.body,
        enabled=payload.enabled,
        remark=payload.remark,
        user_id=user.id,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="biz_doc_template",
        business_id=template.id,
        after={"doc_type": payload.doc_type, "version": template.version, "name": payload.name},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {**svc.serialize_template(template), "variables": analysis["variables"]},
        "模板已新增一版",
    )


@router.get("/biz-docs")
async def list_docs(
    doc_type: str | None = Query(None),
    sample_request_id: int | None = Query(None),
    order_id: int | None = Query(None),
    order_draft_id: int | None = Query(None),
    quote_id: int | None = Query(None),
    customer_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=300),
    # 任一看权限即可进入：真正的过滤在 service 里**逐类型**做（哪些类型能看
    # 由 DOC_TYPE_VIEW_PERMISSION 决定），这样"列表看得见、点开 403"不会发生。
    user: CurrentUser = Depends(require_permission("order:view", "quote:view", "sample:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(
        await svc.list_docs(
            session,
            user,
            doc_type=doc_type,
            sample_request_id=sample_request_id,
            order_id=order_id,
            order_draft_id=order_draft_id,
            quote_id=quote_id,
            customer_id=customer_id,
            limit=limit,
        )
    )


@router.post("/biz-docs/sample-request")
async def generate_sample_request_doc(
    payload: SampleDocGenerate,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按打样申请生成一份打样需求单（来源询价与差异一起落快照，原单不变）。

    幂等（§8.9）：带 `request_key` 时，同一把键重试返回**原来那一份**；
    要再出一份（哪怕内容一模一样）就换一把新键 —— 不能按内容去重，
    那会把"同一批货再出一份给工厂"这种合法需求永久挡掉。
    """
    key = request_key_from(request, payload.request_key)
    doc, replayed = await svc.run_idempotent_generation(
        session,
        user_id=user.id,
        action="bizdoc:sample_request",
        request_key=key,
        payload=_generate_fingerprint(
            doc_type="sample_request",
            sample_request_id=payload.sample_request_id,
            template_id=payload.template_id,
            extra_fields=payload.extra_fields,
        ),
        generate=lambda: svc.generate_sample_request_doc(
            session,
            sample_request_id=payload.sample_request_id,
            user=user,
            template_id=payload.template_id,
            extra_fields=payload.extra_fields,
        ),
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="generate_replay" if replayed else "generate",
        business_type="biz_doc",
        business_id=doc.id,
        after={
            "doc_no": doc.doc_no,
            "doc_type": doc.doc_type,
            "version": doc.version,
            "source_no": doc.source_no,
            "sample_request_id": doc.sample_request_id,
            "replayed": replayed,
        },
        ip=client_ip(request),
    )
    await svc.commit_doc_generation(session, doc)
    return ok(
        _generate_payload(doc, key),
        _generate_message("打样需求单", doc, replayed, key),
    )


@router.post("/biz-docs/order")
async def generate_order_sheet_doc(
    payload: OrderDocGenerate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按订单生成一份下单文件（来源报价与差异一起落快照，订单不变）。

    幂等口径同打样单（§8.9）；`allow_draft` 是 §8.8 的草稿闸门，与幂等互不影响
    （是否允许草稿也进请求指纹：同一把键不能一次要正式件、一次要草稿）。
    """
    key = request_key_from(request, payload.request_key)
    doc, replayed = await svc.run_idempotent_generation(
        session,
        user_id=user.id,
        action="bizdoc:order_sheet",
        request_key=key,
        payload=_generate_fingerprint(
            doc_type="order_sheet",
            order_id=payload.order_id,
            template_id=payload.template_id,
            extra_fields=payload.extra_fields,
            allow_draft=payload.allow_draft,
        ),
        generate=lambda: svc.generate_order_sheet_doc(
            session,
            order_id=payload.order_id,
            user=user,
            template_id=payload.template_id,
            extra_fields=payload.extra_fields,
            allow_draft=payload.allow_draft,
        ),
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="generate_replay" if replayed else "generate",
        business_type="biz_doc",
        business_id=doc.id,
        after={
            "doc_no": doc.doc_no,
            "doc_type": doc.doc_type,
            "version": doc.version,
            "status": doc.status,
            "source_no": doc.source_no,
            "order_id": doc.order_id,
            "replayed": replayed,
        },
        ip=client_ip(request),
    )
    await svc.commit_doc_generation(session, doc)
    if replayed:
        message = _generate_message("下单文件", doc, True, key)
    elif doc.status == "draft":
        message = "已生成下单文件草稿（模板变量未解析，非正式对外文件）" + _no_key_hint(key)
    else:
        message = _generate_message("下单文件", doc, False, key)
    return ok(_generate_payload(doc, key), message)


@router.post("/biz-docs/quote")
async def generate_quote_doc(
    payload: QuoteDocGenerate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按报价版本生成对客 Excel 报价单（金额取自那一版，不现算）。

    幂等口径同打样单（§8.9）：报价单最容易双击——生成失败提示与网络超时后
    用户会再点一次，没有幂等键就会多出一份 V2，客户手里两张表还长得一样。
    """
    key = request_key_from(request, payload.request_key)
    doc, replayed = await svc.run_idempotent_generation(
        session,
        user_id=user.id,
        action="bizdoc:quote_sheet",
        request_key=key,
        payload=_generate_fingerprint(
            doc_type="quote_sheet",
            quote_version_id=payload.quote_version_id,
            template_id=payload.template_id,
            extra_fields=payload.extra_fields,
        ),
        generate=lambda: svc.generate_quote_doc(
            session,
            quote_version_id=payload.quote_version_id,
            user=user,
            template_id=payload.template_id,
            extra_fields=payload.extra_fields,
        ),
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="generate_replay" if replayed else "generate",
        business_type="biz_doc",
        business_id=doc.id,
        after={
            "doc_no": doc.doc_no,
            "doc_type": doc.doc_type,
            "version": doc.version,
            "source_no": doc.source_no,
            "source_version": doc.source_version,
            "replayed": replayed,
        },
        ip=client_ip(request),
    )
    await svc.commit_doc_generation(session, doc)
    return ok(
        _generate_payload(doc, key),
        _generate_message("对客报价单", doc, replayed, key),
    )


def _generate_fingerprint(**fields) -> dict:
    """幂等键的**内容指纹**（§8.9）：同一把键换了来源/模板/附加字段就是另一件事。

    `extra_fields` 归一成字典再入指纹：前端"没填"可能发 `null`，也可能发 `{}`，
    两者是同一件事；不归一的话同一把键会因为这点差别被判成"内容不一致"。
    """
    normalized = dict(fields)
    if "extra_fields" in normalized:
        normalized["extra_fields"] = dict(normalized["extra_fields"] or {})
    return normalized


def _generate_payload(doc, key: str | None) -> dict:
    """生成接口的响应体。

    `idempotency_key_used` 是给前端/调用方看的**事实**：这一份到底受没受幂等保护。
    不写出来的话，不带键的调用方会默认"重试是安全的"，而实际上他每点一次就多一份。
    """
    body = svc.serialize_doc(doc)
    body["idempotency_key_used"] = bool(key)
    return body


def _no_key_hint(key: str | None) -> str:
    """没带幂等键时补一句实话——不假装重试是安全的。"""
    if key:
        return ""
    return "；本次未带幂等键，重复提交可能生成多份（要安全重试请带 request_key）"


def _generate_message(label: str, doc, replayed: bool, key: str | None = None) -> str:
    """生成结果的提示语。

    两种回放必须说清、且**不能**让人以为又出了一份：
    - 命中幂等键：这份刚才就生成过了，返回的是原来那一份；
    - 没带幂等键：明确告知"重试可能多出一份"，不假装有保护。
    """
    if replayed:
        return (
            f"这次提交与刚才那次是同一件事，已返回原文件 {doc.doc_no}"
            f"（V{doc.version}），未重复生成"
        )
    return f"{label}已生成（{doc.doc_no}，V{doc.version}）" + _no_key_hint(key)


@router.get("/biz-docs/{doc_id}")
async def get_doc(
    doc_id: int,
    # 进得来不代表看得到：真正的判据在 _ensure_doc_access（按 doc_type 判来源模块权限）
    user: CurrentUser = Depends(require_permission("order:view", "quote:view", "sample:view")),
    session: AsyncSession = Depends(get_db),
):
    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_access(session, user, doc)
    payload = svc.serialize_doc(doc)
    payload["input_snapshot"] = doc.input_snapshot
    return ok(payload)


@router.get("/biz-docs/{doc_id}/download")
async def download_doc(
    doc_id: int,
    request: Request,
    #: original（默认）= 生成时存档的那份字节；state = 按**当前状态**重出的副本。
    #: 为什么要分成两个：原件里印的是生成当时的"有效"，作废之后原件并不会变；
    #: 而业务需要看到"这一份已经作废了"。两者混在一起就会出现"下载到的到底是
    #: 当初发出去的原件，还是现在重出的状态副本"说不清（§8.10）。
    mode: str = Query("original"),
    #: 存档读不到时是否允许出一份**明确标注**的重建副本。默认 False = 拒绝下载。
    allow_rebuild: bool = Query(False),
    user: CurrentUser = Depends(require_permission("order:view", "quote:view", "sample:view")),
    session: AsyncSession = Depends(get_db),
):
    """下载文件。**读生成时存档的原件**，不再"每次下载重新渲染"。

    §8.10 的三条口径都体现在这里：
    1. 有存档 → 直接返回存档字节（重复下载字节一致，升级渲染器也不影响旧件）；
    2. 没有存档（迁移前的历史件）→ 按快照重出，并在纸面与响应头标明
       "由历史快照重建"，不伪称是当时的原件；
    3. 有存档却读不到/校验不符 → **拒绝并告警**，不静默用当前资料替代。

    **下载不等于发送**：这里只出图与留痕，不产生任何"已发送"状态
    （§8.7 明确不新增隐式"下载即正式发送"）。
    """
    if mode not in ("original", "state"):
        raise AppError(ErrorCode.PARAM_ERROR, "mode 只支持 original（存档原件）或 state（状态副本）", 422)

    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_access(session, user, doc)
    plan = await svc.plan_download(
        session, doc, mode=mode, allow_rebuild=allow_rebuild
    )
    if plan.blocked_reason:
        # 告警：原件读不到是**数据事故**（盘上文件被删/被换过），必须留下痕迹。
        logger.warning(
            "对外单据存档原件不可用：id=%s doc_no=%s type=%s 原因=%s",
            doc.id,
            doc.doc_no,
            doc.doc_type,
            plan.blocked_reason,
        )
        await write_audit(
            session,
            operator_id=user.id,
            action="download_blocked",
            business_type="biz_doc",
            business_id=doc.id,
            after={
                "doc_no": doc.doc_no,
                "status": doc.status,
                "reason": plan.blocked_reason,
            },
            ip=client_ip(request),
        )
        await session.commit()
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"该单据的存档原件不可用：{plan.blocked_reason}。"
            "为避免把重建内容冒充原件，本次下载已拒绝；"
            "如确需一份明确标注「由历史快照重建」的副本，请带 allow_rebuild=true 重新下载。",
            410,
        )

    if plan.content is not None:
        content = plan.content
    else:
        data = await svc.doc_pdf_data(session, doc, copy_notice=plan.notice)
        # 渲染是同步 CPU 操作，丢线程池避免卡住事件循环（与报价/合同 PDF 同）
        content = await asyncio.to_thread(svc.render_document, doc.doc_type, data)

    # 下载留痕：含价格的对客文件谁在什么时候取了哪一份要能查（作废件也一样取得到，
    # 只是纸上带"已作废"）。`source` 记的是**这份内容从哪来**——事后要能分辨
    # "下载的是存档原件"还是"当时没存档、按快照重建的"。
    await write_audit(
        session,
        operator_id=user.id,
        action="download",
        business_type="biz_doc",
        business_id=doc.id,
        after={
            "doc_no": doc.doc_no,
            "status": doc.status,
            "content_source": plan.source,
            "archive_status": svc.archive_status_of(doc),
            "delivered_sha256": hashlib.sha256(content).hexdigest(),
        },
        ip=client_ip(request),
    )
    await session.commit()

    suffix, media_type, _kind, renderer_version = svc.renderer_spec(doc.doc_type)
    headers = {
        "Content-Disposition": f'attachment; filename="{doc.doc_no}{suffix}"',
        # 这三行让调用方（以及排障的人）不看审计就知道拿到的是哪一份
        "X-Doc-Content-Source": plan.source,
        "X-Doc-Archive-Status": svc.archive_status_of(doc),
        "X-Doc-Delivered-Sha256": hashlib.sha256(content).hexdigest(),
    }
    if doc.file_sha256 and plan.content is not None:
        headers["X-Doc-Original-Sha256"] = doc.file_sha256
    if renderer_version and plan.content is None:
        # 只有"现在重出的"才谈得上渲染器版本；存档原件的渲染器版本在库里的
        # `archive.renderer_version` 上（它解释的是当初用哪版出的图）
        headers["X-Doc-Renderer-Version"] = renderer_version
    return Response(content=content, media_type=media_type, headers=headers)


@router.post("/biz-docs/{doc_id}/void")
async def void_doc(
    doc_id: int,
    payload: VoidPayload,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage", "quote:manage", "sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """作废：状态改掉，内容与校验值不动（作废 ≠ 删档）。

    作废按**单据类型的维护权限**判（报价单要 quote:manage、打样单要 sample:manage），
    不是统一要 order:manage——否则只有报价维护权的人作废不了自己的报价单，
    而只有订单维护权的人却能作废一份报价单。
    """
    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_access(session, user, doc, manage=True)
    await svc.void_doc(session, doc, reason=payload.reason)
    await write_audit(
        session,
        operator_id=user.id,
        action="void",
        business_type="biz_doc",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_doc(doc), "已作废")


async def _ensure_doc_access(
    session: AsyncSession, user: CurrentUser, doc, *, manage: bool = False
) -> None:
    """单据的查看/维护授权：**按单据类型判来源模块权限** + 负责人数据范围。

    两个都要过，缺一不可：
    - 类型权限挡住"只有订单权限的人拿到报价/打样文件"（§8.5）；
    - 数据范围挡住"猜到编号就能看别人的含价格文件"（与列表过滤同一口径）。
    """
    from app.core.data_scope import ensure_in_scope

    label = DOC_TYPE_LABEL.get(doc.doc_type, doc.doc_type)
    if not has_doc_permission(user, doc.doc_type, manage=manage):
        action = "维护（作废）" if manage else "查看"
        raise AppError(
            ErrorCode.FORBIDDEN,
            f"无权{action}{label}：该单据类型需要对应的模块权限，"
            f"请向管理员申请后再操作",
            403,
        )
    await ensure_in_scope(session, user, owner_id=doc.owner_id, label=label)
