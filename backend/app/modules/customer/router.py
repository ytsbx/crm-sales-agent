"""客户中心与联系人接口（对齐 03-API §7 / §8）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, ensure_permission, require_permission
from app.core.errors import ErrorCode, AppError
from app.core.response import ok, page_data, paginate
from app.modules.contact_util import create_contact_for_customer, take_primary_slot
from app.modules.customer import service as svc
from app.modules.customer import stage, tags as tag_svc
from app.modules.customer.model import Contact, Customer
from app.modules.customer.schema import (
    ContactBindCustomer,
    ContactCreate,
    ContactStandaloneCreate,
    ContactUpdate,
    CustomerCreate,
    CustomerTransfer,
    CustomerUpdate,
    PoolRelease,
)

router = APIRouter(tags=["Customer"])


# ---------------------------------------------------------------- 客户

@router.get("/customers")
async def list_customers(
    keyword: str | None = None,
    level: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner_id: int | None = None,
    pool_status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = await svc.apply_data_scope(svc.not_deleted(svc.build_list_stmt(
        keyword=keyword,
        level=level,
        status=status,
        source=source,
        owner_id=owner_id,
        pool_status=pool_status,
    )), user, session)
    rows, total = await paginate(session, stmt, page, page_size)

    counts = await svc.contact_counts(session, [c.id for c in rows])
    owners = await svc.owner_names(session, [c.owner_id for c in rows])
    tag_map = await tag_svc.tags_of_customers(session, [c.id for c in rows])
    # 领导六阶段：算出来的字段（订单/打样/报价事实推导），不占人一分钟
    stage_map = await stage.stage_counts_map(session, [c.id for c in rows])
    items = [
        svc.serialize_customer(
            c,
            owner_name=owners.get(c.owner_id) if c.owner_id else None,
            contact_count=counts.get(c.id, 0),
            tags=tag_map.get(c.id, []),
            stage=stage.derive_stage(*stage_map.get(c.id, (0, 0, 0))),
        )
        for c in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.get("/customers/stage-distribution")
async def customers_stage_distribution(
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """六阶段分布：当前数据范围内各阶段客户数（了解/报价/打样/首单/返单/稳定复购）。"""
    stmt = await svc.apply_data_scope(
        svc.not_deleted(select(Customer.id)), user, session
    )
    ids = list((await session.execute(stmt)).scalars().all())
    return ok(await stage.stage_distribution(session, ids))


@router.post("/customers")
async def create_customer(
    payload: CustomerCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:create")),
    session: AsyncSession = Depends(get_db),
):
    """新建客户（第八批 8.15：支持请求幂等）。

    弱网重试是真实场景：服务端已经建好了、响应没回到客户端，用户再点一次。
    带同一把 `request_key`（body 字段或 `X-Request-Key` 头）时：
    同键同内容回放第一次的结果、同键不同内容报冲突、同键并发只允许一个成功。
    没带键就照旧创建，但响应里会说明**这次没有幂等保护**。
    """
    from app.core import idempotency

    data = payload.model_dump()
    request_key = idempotency.request_key_from(request, data.pop("request_key", None))

    reservation = None
    if request_key:
        reservation = await idempotency.reserve(
            session,
            user_id=user.id,
            action="customer:create",
            request_key=request_key,
            payload=data,
            result_type="customer",
        )
        if reservation.should_replay:
            # 回放前重查**当前**可见性（2026-10-07 修）：这条客户可能已经移交给别人、
            # 或者被软删了。原来直接返回缓存 —— 于是客户早就不是你的了，凭一把旧请求键
            # 照样能把资料读走。
            # 口径：**幂等保护的是"不重复创建"，不是"永久授权"**；失去权限就不给回放，
            # 但也不因此再创建一条新记录（回放失败不等于重新执行）。
            if reservation.replay_id is not None:
                try:
                    await svc.get_visible_customer(session, user, reservation.replay_id)
                except AppError:
                    raise AppError(
                        ErrorCode.DATA_SCOPE_DENIED,
                        "这条记录已不在你的可见范围内（可能已移交或删除），无法回放原结果",
                        403,
                    ) from None
            return ok(
                reservation.replay_payload,
                "这次提交此前已成功创建过，已返回原记录（没有重复创建）",
            )

    try:
        customer = await svc.create_customer(session, user, data)
        await write_audit(
            session,
            operator_id=user.id,
            action="create",
            business_type="customer",
            business_id=customer.id,
            after=svc.serialize_customer(customer),
            ip=client_ip(request),
        )
        body = svc.serialize_customer(customer)
        if reservation is not None:
            await idempotency.complete(
                session, reservation, result_payload=body, result_id=customer.id
            )
        await session.commit()
    except Exception:
        # 失败就释放占位：用户改完表单会带同一把键重试，内容必然不同，
        # 不释放会把"改错重填"误判成"同键不同内容"冲突。
        if reservation is not None:
            await idempotency.release(session, reservation)
        raise

    if request_key:
        return ok(body, "客户已创建")
    return ok(body, "客户已创建（本次未带请求键，弱网重试可能产生重复客户）")


@router.get("/customers/{customer_id}")
async def get_customer(
    customer_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_visible_customer(session, user, customer_id)
    owners = await svc.owner_names(session, [customer.owner_id])
    counts = await svc.contact_counts(session, [customer.id])
    tag_map = await tag_svc.tags_of_customers(session, [customer.id])
    return ok(
        svc.serialize_customer(
            customer,
            owner_name=owners.get(customer.owner_id) if customer.owner_id else None,
            contact_count=counts.get(customer.id, 0),
            tags=tag_map.get(customer.id, []),
        )
    )


@router.patch("/customers/{customer_id}")
async def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_visible_customer(session, user, customer_id)
    before = svc.serialize_customer(customer)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(customer, field, value)
    # 补核历史联系时间（第七批 7.5）：填了真实联系时间就说明"未知"不成立了，
    # 这个客户重新回到自动回收/冷落扫描的视野里。
    if changes.get("last_followup_at") is not None:
        customer.last_contact_unknown = False
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="customer",
        business_id=customer.id,
        before=before,
        after=svc.serialize_customer(customer),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_customer(customer), "已保存")


@router.delete("/customers/{customer_id}")
async def delete_customer(
    customer_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:delete")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_visible_customer(session, user, customer_id)
    before = svc.serialize_customer(customer)
    await svc.delete_customer(session, customer)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="customer",
        business_id=customer.id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "客户已删除")


def _transfer_message(base: str, documents: dict) -> str:
    """转移/分配成功后那句话：把"有单据被同事先接走"如实说出来。

    为什么要有：客户转过去之后，名下的单据也会跟着走（`documents.py`）。但如果
    某张单据**恰好在同一瞬间**被别的同事先接走了，它会留在那位同事手里 ——
    这是对的（谁先接的归谁），可操作者看到"客户名下少了一张单据"会莫名其妙。
    所以这里把张数与类别一并讲清楚（主人口径 2026-10-07：要提示，不要静默）。
    """
    skipped = documents.get("skipped_total") or 0
    if not skipped:
        return base
    kinds = "、".join(documents.get("skipped_labels") or [])
    detail = f"（{kinds}）" if kinds else ""
    return f"{base}；有 {skipped} 张单据{detail}因已被其他同事接手，未跟着转"


@router.post("/customers/{customer_id}/transfer")
async def transfer_customer(
    customer_id: int,
    payload: CustomerTransfer,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_visible_customer(session, user, customer_id)
    before = svc.serialize_customer(customer)
    documents = await svc.transfer_customer(
        session, user, customer, payload.owner_id, payload.reason
    )
    await session.flush()
    # 「转给某个人」与「放回公海」是两种动作，审计分开记（第六批审查第 1 条）：
    # 事后要能看出这个客户是被谁、从哪个入口放回公海的，而不是笼统一条 transfer。
    to_pool = payload.owner_id is None
    await write_audit(
        session,
        operator_id=user.id,
        action="transfer_to_pool" if to_pool else "transfer",
        business_type="customer",
        business_id=customer.id,
        before=before,
        after=svc.serialize_customer(customer),
        ip=client_ip(request),
    )
    await session.commit()
    data = svc.serialize_customer(customer)
    # 「哪些单据跟着走了、有没有被同事先接走的」一并回给前端（§并发提示）：
    # 统一客户端只把 `data` 交给页面，所以这项必须放在 data 里，不能只写在 message。
    data["document_transfer"] = documents
    return ok(data, _transfer_message("已放入公海" if to_pool else "负责人已变更", documents))


@router.post("/customers/{customer_id}/release-to-pool")
async def release_to_pool(
    customer_id: int,
    request: Request,
    payload: PoolRelease | None = None,
    user: CurrentUser = Depends(require_permission("customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    """把客户放进公海（**人工释放**）。

    履约保护在这里也要拦（返工单 6.3 第 5 条）：客户手上还有在途订单、
    未结应收、有效正式报价或在途打样时，普通操作会被拒，**并说清是哪张单拦住的**。

    主管确需释放时走**例外**：填了 `reason` 就放行，但保护事项与例外决定
    都会记进审计（第 6 条）—— 例外是要有人担责的事。
    判据与定时扫描、回收执行共用同一套 `protection_detail`，不各判一套。

    校验**不在这里做**：`svc.transfer_customer(..., None, ...)` 内部统一拦
    （第六批审查第 1 条）。这样单个转移、分配、批量转移这些同样能把负责人
    清空的入口，走的是同一道关，也不会出现两处各判一套。
    """
    customer = await svc.get_visible_customer(session, user, customer_id)
    reason = (payload.reason if payload else None) or None
    # 注意传的是**原始 reason**（可能为空）—— 保护校验靠它判断"到底有没有填原因"。
    # 默认文案交给 default_reason，只用于归属历史，不参与例外判断。
    await svc.transfer_customer(
        session, user, customer, None, reason, default_reason="放入公海"
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="release_to_pool",
        business_type="customer",
        business_id=customer.id,
        after={"reason": reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_customer(customer), "已放入公海")


@router.post("/customers/{customer_id}/claim")
async def claim_customer(
    customer_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """领取公海客户。

    与公海页面的 `POST /public-pool/customers/{id}/claim` 走**同一个服务函数**
    （`svc.claim_customer`）：行锁、可领取条件、幂等、报错文案全部一致。
    此前两处各写一份，检查项已经漂移（公海那边查 pool_status + owner_id，
    这边只查 pool_status）。
    """
    customer, claimed = await svc.claim_customer(session, user, customer_id, reason="公海领取")
    if claimed:
        await write_audit(
            session,
            operator_id=user.id,
            action="claim",
            business_type="customer",
            business_id=customer.id,
            after={"owner_id": user.id},
            ip=client_ip(request),
        )
    await session.commit()
    return ok(
        svc.serialize_customer(customer),
        "领取成功" if claimed else f"客户「{customer.name}」已经是你的",
    )


# ---------------------------------------------------------------- 客户 360

@router.get("/customers/{customer_id}/overview")
async def customer_overview(
    customer_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """客户 360 概览（03-API §7）。

    前端原先要发 5~6 个请求才能拼出这一屏；这里一次聚合，
    每个板块给"数量 + 最近 5 条"，点进各标签页再拉完整分页。

    2026-10-07（第九批 §9.1）：**逐板块**判对应模块的查看权限与数据范围。
    只要求 `customer:view` 是不够的 —— 有客户查看权、没有 `order:view` 的账号
    （例如财务）此前能从这一个入口拿到订单编号与金额。没权限的板块
    **不查询、不返回**，板块名进 `restricted`，前端据此显示"不可查看"。
    """
    await svc.get_visible_customer(session, user, customer_id)
    return ok(await svc.customer_overview(session, user, customer_id))


@router.get("/customers/{customer_id}/followups")
async def customer_followups(
    customer_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """该客户的跟进记录（03-API §7）。

    2026-10-07（第九批 §9.1）：补 `followup:view` 与系统过程记录的来源单据校验。
    此前只要求 `customer:view`、且不判记录归属 —— 有客户查看权的人能看到该客户
    名下**所有人**的跟进内容。现在与 `GET /followups?customer_id=` 同一口径
    （含 `system_source_filter`：系统过程记录仍要看得见来源单据）。
    """
    from app.modules.followup.model import FollowUp
    from app.modules.followup.visibility import system_source_filter

    ensure_permission(user, "followup:view")
    await svc.get_visible_customer(session, user, customer_id)
    stmt = select(FollowUp).where(
        FollowUp.customer_id == customer_id,
        await system_source_filter(session, user),
    )
    rows, total = await paginate(session, stmt.order_by(FollowUp.id.desc()), page, page_size)
    return ok(
        page_data(
            [
                {
                    "id": row.id,
                    "followup_type": row.followup_type,
                    "content": row.content,
                    "customer_feedback": row.customer_feedback,
                    "next_action": row.next_action,
                    "task_due_at": row.planned_at,
                    "exemption_reason": row.exemption_reason,
                    "next_task_id": row.next_task_id,
                    "owner_id": row.owner_id,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


@router.get("/customers/{customer_id}/tasks")
async def customer_tasks(
    customer_id: int,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """该客户关联的任务（03-API §7）。

    2026-10-07（第九批 §9.1）：补 `task:view` 与任务自己的数据范围 ——
    客户可见不代表客户名下**别人**的任务可见（与任务列表同一口径）。
    """
    from app.modules.task.model import Task

    ensure_permission(user, "task:view")
    await svc.get_visible_customer(session, user, customer_id)
    stmt = select(Task).where(Task.customer_id == customer_id)
    task_scope = await scoped_owner_ids(session, user)
    if task_scope is not None:
        stmt = stmt.where(Task.owner_id.in_(task_scope))
    if status:
        stmt = stmt.where(Task.status == status)
    rows, total = await paginate(session, stmt.order_by(Task.id.desc()), page, page_size)
    return ok(
        page_data(
            [
                {
                    "id": row.id,
                    "title": row.title,
                    "task_type": row.task_type,
                    "status": row.status,
                    "priority": row.priority,
                    "owner_id": row.owner_id,
                    "due_at": row.due_at,
                    "completed_at": row.completed_at,
                }
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


@router.get("/customers/{customer_id}/files")
async def customer_files(
    customer_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """该客户的附件（03-API §7）。

    与 `/business/customer/{id}/files` 等价，这里是文档里的客户子资源写法。
    输出字段与附件面板保持一致，避免前端为同一个东西维护两套解析。
    """
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.file.router import is_previewable

    await svc.get_visible_customer(session, user, customer_id)
    rows = (
        await session.execute(
            select(BusinessFile, FileRecord)
            .join(FileRecord, FileRecord.id == BusinessFile.file_id)
            .where(
                BusinessFile.business_type == "customer",
                BusinessFile.business_id == customer_id,
            )
            .order_by(BusinessFile.id.desc())
        )
    ).all()
    return ok(
        [
            {
                "business_file_id": link.id,
                "file_id": stored.id,
                "name": stored.file_name,
                "mime_type": stored.mime_type,
                "size": stored.size,
                "uploaded_by": stored.uploaded_by,
                "created_at": stored.created_at,
                "category": link.category,
                "remark": link.remark,
                # 与附件面板同口径：能在线预览的类型才给预览入口
                "previewable": is_previewable(stored),
            }
            for link, stored in rows
        ]
    )


# ---------------------------------------------------------------- 客户子资源

@router.get("/customers/{customer_id}/opportunities")
async def customer_opportunities(
    customer_id: int,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """该客户的商机（03-API §7）。

    与 `GET /opportunities?customer_id=` 等价，这里是客户详情页标签页的写法。
    同样按商机自己的数据范围过滤 —— 客户可见不代表客户名下每条商机都可见。
    第九批 §9.1 补：还要有 `opportunity:view`，否则"有客户权限就能绕过商机模块"。
    """
    from app.modules.opportunity import service as opp_svc

    ensure_permission(user, "opportunity:view")
    await svc.get_visible_customer(session, user, customer_id)
    stmt = await opp_svc.apply_data_scope(
        opp_svc.build_opportunity_stmt(customer_id=customer_id, status=status),
        user,
        session,
    )
    rows, total = await paginate(session, stmt, page, page_size)
    stages = await opp_svc.stage_map(session)
    _, owners, counts = await opp_svc.enrichment(session, rows)
    return ok(
        page_data(
            [
                opp_svc.serialize_opportunity(
                    row,
                    stage=stages.get(row.stage_id),
                    customer_name=None,
                    owner_name=owners.get(row.owner_id) if row.owner_id else None,
                    item_count=counts.get(row.id, 0),
                )
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


@router.get("/customers/{customer_id}/quotes")
async def customer_quotes(
    customer_id: int,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """该客户的报价单（03-API §7）。

    第九批 §9.1：补 `quote:view`（数据范围过滤原本就有，缺的是模块权限本身）。
    """
    from app.modules.quote.model import Quote, QuoteVersion

    ensure_permission(user, "quote:view")
    await svc.get_visible_customer(session, user, customer_id)
    stmt = select(Quote).where(
        Quote.customer_id == customer_id, Quote.deleted_at.is_(None)
    )
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(Quote.owner_id.in_(owner_ids))
    if status:
        stmt = stmt.where(Quote.status == status)
    rows, total = await paginate(session, stmt.order_by(Quote.id.desc()), page, page_size)

    # 审批状态与金额在**版本**上，不在报价单上（Quote 没有这两列）。
    version_ids = [row.current_version_id for row in rows if row.current_version_id]
    versions: dict[int, QuoteVersion] = {}
    if version_ids:
        found = (
            await session.execute(
                select(QuoteVersion).where(QuoteVersion.id.in_(version_ids))
            )
        ).scalars().all()
        versions = {item.id: item for item in found}

    def version_of(row):
        return versions.get(row.current_version_id) if row.current_version_id else None

    items = []
    for row in rows:
        version = version_of(row)
        items.append(
            {
                "id": row.id,
                "quote_no": row.quote_no,
                "customer_id": row.customer_id,
                "owner_id": row.owner_id,
                "status": row.status,
                "valid_until": row.valid_until,
                "current_version_id": row.current_version_id,
                "current_version_no": version.version_no if version else None,
                "current_version_amount": float(version.total_amount) if version else None,
                # 币种跟着金额一起给（第九批 §9.9）：前端不能假设是人民币。
                # 版本缺失时给 null，由前端提示"币种待核实"。
                "currency": version.currency if version else None,
                "approval_status": version.approval_status if version else None,
                "created_at": row.created_at,
            }
        )
    return ok(page_data(items, total, page, page_size))


@router.get("/customers/{customer_id}/orders")
async def customer_orders(
    customer_id: int,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """该客户的销售订单（03-API §7）。

    第九批 §9.1：补 `order:view`（数据范围过滤原本就有，缺的是模块权限本身）。
    """
    from app.modules.order import service as order_svc
    from app.modules.order.model import SalesOrder

    ensure_permission(user, "order:view")
    await svc.get_visible_customer(session, user, customer_id)
    stmt = select(SalesOrder).where(SalesOrder.customer_id == customer_id)
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(SalesOrder.owner_id.in_(owner_ids))
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    rows, total = await paginate(session, stmt.order_by(SalesOrder.id.desc()), page, page_size)
    ctx = await order_svc.order_context(session, rows)
    return ok(
        page_data(
            [
                order_svc.serialize_order(
                    row,
                    customer_name=ctx["customers"].get(row.customer_id),
                    owner_name=ctx["owners"].get(row.owner_id) if row.owner_id else None,
                    received_amount=ctx["received"].get(row.id),
                    item_count=ctx["counts"].get(row.id, 0),
                )
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


@router.post("/customers/{customer_id}/assign")
async def assign_customer(
    customer_id: int,
    payload: CustomerTransfer,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    """分配客户负责人（03-API §7）。

    与 `POST /customers/{id}/transfer` 是同一件事（同一份 `transfer_customer` 实现），
    区别只是权限点：transfer 是"业务员之间转"，assign 是"主管分配"。
    文档两个都列了，就都留着。
    """
    customer = await svc.get_visible_customer(session, user, customer_id)
    before = svc.serialize_customer(customer)
    documents = await svc.transfer_customer(
        session, user, customer, payload.owner_id, payload.reason,
        # 原始 reason 参与保护校验，默认文案只进归属历史
        default_reason="主管分配",
    )
    await session.flush()
    # 同 transfer：owner_id 为空就是「放回公海」，审计动作分开记
    to_pool = payload.owner_id is None
    await write_audit(
        session,
        operator_id=user.id,
        action="assign_to_pool" if to_pool else "assign",
        business_type="customer",
        business_id=customer.id,
        before=before,
        after=svc.serialize_customer(customer),
        ip=client_ip(request),
    )
    await session.commit()
    data = svc.serialize_customer(customer)
    data["document_transfer"] = documents  # 同 transfer：并发的如实交代要回给页面
    return ok(data, _transfer_message("已放入公海" if to_pool else "已分配", documents))


# ---------------------------------------------------------------- 联系人

@router.get("/customers/{customer_id}/contacts")
async def list_contacts(
    customer_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_customer(session, user, customer_id)
    stmt = (
        select(Contact)
        .where(Contact.customer_id == customer_id, Contact.deleted_at.is_(None))
        .order_by(Contact.is_primary.desc(), Contact.id.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    # 对外响应一律走脱敏版本（第八批 8.2）：是否完整按已确认规则决定，
    # 见 contact_util.can_view_full_contact。审计里仍然保留完整值。
    return ok(await svc.serialize_contacts_masked(session, user, list(rows)))


@router.post("/customers/{customer_id}/contacts")
async def create_contact(
    customer_id: int,
    payload: ContactCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_visible_customer(session, user, customer_id)
    if payload.is_primary:
        # **先腾位再插**：占主位必须排在任何写入之前，否则这次 INSERT 自己就会
        # 撞上那条部分唯一索引（第十批 10.4）。腾位 = 锁客户行 + 取消同客户
        # 其它主联系人的标记。
        await take_primary_slot(session, customer.id)
    contact = Contact(
        **payload.model_dump(),
        customer_id=customer.id,
        owner_id=customer.owner_id,
        source="手工录入",
    )
    session.add(contact)
    await session.flush()
    if contact.is_primary:
        await svc.set_primary_contact(session, contact)
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="contact",
        business_id=contact.id,
        after=svc.serialize_contact(contact),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_contact_masked(session, user, contact), "联系人已创建")


@router.patch("/contacts/{contact_id}")
async def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    contact = await svc.get_visible_contact(session, user, contact_id)
    before = svc.serialize_contact(contact)
    if payload.is_primary:
        # 要占主位：先腾位再写库（理由同新建那条接口）。
        # `exclude=自己`：这条联系人可能本来就是主，腾位时别把它一起刷掉。
        await take_primary_slot(session, contact.customer_id, exclude=contact.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    await session.flush()
    if contact.is_primary:
        await svc.set_primary_contact(session, contact)
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="contact",
        business_id=contact.id,
        before=before,
        after=svc.serialize_contact(contact),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_contact_masked(session, user, contact), "已保存")


@router.delete("/contacts/{contact_id}")
async def delete_contact(
    contact_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    from datetime import UTC, datetime

    contact = await svc.get_visible_contact(session, user, contact_id)
    before = svc.serialize_contact(contact)
    contact.deleted_at = datetime.now(UTC)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="contact",
        business_id=contact.id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "联系人已删除")


# ------------------------------------------- 03-API §8 的 RESTful 联系人接口
#
# 现有实现是嵌套在客户下的（`/customers/{id}/contacts`），前端一直用那套。
# 这里补文档要求的扁平写法，内部复用同一份查询与校验：
# 两套路径同一份实现，不会出现"从哪个入口进来行为不一样"。


@router.get("/contacts")
async def list_all_contacts(
    keyword: str | None = None,
    customer_id: int | None = None,
    owner_id: int | None = None,
    only_primary: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """联系人总列表（03-API §8）。

    与客户详情里的联系人列表共用数据范围规则：只能看到自己范围内的
    客户的联系人，否则会从联系人这条路绕过客户的数据权限。
    """
    stmt = select(Contact).where(Contact.deleted_at.is_(None))
    if customer_id:
        stmt = stmt.where(Contact.customer_id == customer_id)
    if owner_id:
        stmt = stmt.where(Contact.owner_id == owner_id)
    if only_primary:
        stmt = stmt.where(Contact.is_primary.is_(True))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(Contact.name.ilike(like), Contact.mobile.ilike(like), Contact.email.ilike(like))
        )

    # 数据范围：按联系人所属客户过滤
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        allowed = select(Customer.id).where(
            Customer.deleted_at.is_(None), Customer.owner_id.in_(owner_ids)
        )
        stmt = stmt.where(or_(Contact.customer_id.in_(allowed), Contact.customer_id.is_(None)))

    rows, total = await paginate(session, stmt.order_by(Contact.id.desc()), page, page_size)
    return ok(
        page_data(
            await svc.serialize_contacts_masked(session, user, list(rows)),
            total,
            page,
            page_size,
        )
    )


@router.get("/contacts/{contact_id}")
async def get_contact(
    contact_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    contact = await svc.get_visible_contact(session, user, contact_id)
    return ok(await svc.serialize_contact_masked(session, user, contact))


@router.post("/contacts")
async def create_standalone_contact(
    payload: ContactStandaloneCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    """新建联系人（03-API §8）。

    与嵌套写法共用 `svc.create_contact_for_customer`，
    所以"第一个联系人自动设为主联系人"这条规则两处一致。
    """
    customer = await svc.get_visible_customer(session, user, payload.customer_id)
    data = payload.model_dump(exclude={"customer_id"})
    contact = await create_contact_for_customer(
        session,
        customer_id=customer.id,
        name=data.pop("name"),
        mobile=data.get("mobile"),
        email=data.get("email"),
        owner_id=customer.owner_id,
        source="手工录入",
        is_primary=data.get("is_primary"),
    )
    # 其余可选字段（职位/部门/微信等）按入参补上
    for field, value in data.items():
        if value is not None and hasattr(contact, field):
            setattr(contact, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="contact",
        business_id=contact.id,
        after=svc.serialize_contact(contact),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_contact_masked(session, user, contact), "联系人已创建")


@router.post("/contacts/{contact_id}/set-primary")
async def set_primary_contact(
    contact_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    """把某个联系人设为主联系人（03-API §8）。

    同一客户下同时只能有一个主联系人 —— `set_primary_contact` 会把
    其余联系人取消主标记，这里不重复实现该规则。
    """
    contact = await svc.get_visible_contact(session, user, contact_id)
    if contact.customer_id is None:
        raise AppError(ErrorCode.PARAM_ERROR, "该联系人还没有关联客户，不能设为主联系人", 422)
    await svc.set_primary_contact(session, contact)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="set_primary",
        business_type="contact",
        business_id=contact.id,
        after={"customer_id": contact.customer_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        await svc.serialize_contact_masked(session, user, contact), "已设为主联系人"
    )


@router.post("/contacts/{contact_id}/bind-customer")
async def bind_contact_customer(
    contact_id: int,
    payload: ContactBindCustomer,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    """把联系人关联到客户（03-API §8）。

    与 `/contacts/{id}/change-customer` 的区别：这个用在校验阶段
    （联系人还没有客户，或要给一个错挂的联系人纠正归属），
    语义上是"绑定"，所以允许从"无客户"绑到"有客户"。
    """
    contact = await svc.get_visible_contact(session, user, contact_id)
    customer = await svc.get_visible_customer(session, user, payload.customer_id)
    before = svc.serialize_contact(contact)
    contact.customer_id = customer.id
    if payload.is_primary:
        await svc.set_primary_contact(session, contact)
    else:
        # **换了客户，主标记不能跟着走。** 这个联系人可能在原来的客户那里是主；
        # 不显式清掉，他到了新客户名下还挂着 is_primary —— 新客户凭空多一个主，
        # 而老客户的主位空着（第十批 10.4 修）。
        contact.is_primary = False
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="bind_customer",
        business_type="contact",
        business_id=contact.id,
        before=before,
        after=svc.serialize_contact(contact),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        await svc.serialize_contact_masked(session, user, contact),
        f"已关联到客户「{customer.name}」",
    )


@router.post("/contacts/{contact_id}/change-customer")
async def change_contact_customer(
    contact_id: int,
    payload: ContactBindCustomer,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    """把联系人改挂到另一个客户（03-API §8）。

    与 bind 的区别是这里要求**原本就有客户**：改挂是纠正性操作，
    如果原本没有客户，那是 bind 的场景，走错接口容易把"新建联系人时忘挂客户"
    当成一次改挂记录进审计。
    """
    contact = await svc.get_visible_contact(session, user, contact_id)
    if contact.customer_id is None:
        raise AppError(
            ErrorCode.PARAM_ERROR, "该联系人还没有关联客户，请用 bind-customer", 422
        )
    if contact.customer_id == payload.customer_id:
        raise AppError(ErrorCode.PARAM_ERROR, "联系人已经属于该客户", 422)
    customer = await svc.get_visible_customer(session, user, payload.customer_id)
    before = svc.serialize_contact(contact)
    contact.customer_id = customer.id
    if payload.is_primary:
        await svc.set_primary_contact(session, contact)
    else:
        # 同 bind：主标记是"在某个客户名下"的属性，换客户必须显式清掉，
        # 否则新客户凭空多一个主联系人（第十批 10.4 修）。
        contact.is_primary = False
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="change_customer",
        business_type="contact",
        business_id=contact.id,
        before=before,
        after=svc.serialize_contact(contact),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        await svc.serialize_contact_masked(session, user, contact),
        f"已改挂到客户「{customer.name}」",
    )
