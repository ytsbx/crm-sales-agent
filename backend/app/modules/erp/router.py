"""ERP/MES 集成接口（03-API §28，6 个接口）。

  POST /integrations/erp/orders                  按订单号推送（外部系统调用入口）
  POST /integrations/erp/orders/{id}/sync        按 CRM 订单 id 推送
  GET  /integrations/erp/orders/{id}/status      拉取并回写履约状态
  GET  /integrations/erp/sync-logs               集成日志
  POST /webhooks/erp/order-status                订单状态回调（无需登录）
  POST /webhooks/erp/shipment-status             发货状态回调（无需登录）

额外加了三个：
  GET  /integrations/erp/readiness               接入状态（配置 + **验收**，不是"接通"）
  GET  /integrations/erp/exception-queue         异常队列（冲突/未匹配/结果未知）
  POST /integrations/erp/orders/{id}/reconcile   人工核对后登记外部单号（结果未知的出口）

第八批 §8.13 又加了一组（只读采集 / 映射 / 对账）：

  GET  /integrations/erp/collection/status              采集水位、断点与**未接通**来源
  POST /integrations/erp/collection/run                 触发一次只读采集
  GET  /integrations/erp/external-records               已采集的原始事实（分页）
  GET  /integrations/erp/external-records/{id}          单条原始事实（含原始报文）
  GET  /integrations/erp/external-mappings              映射台账 / 待匹配队列
  POST /integrations/erp/external-mappings/{id}/match   人工指定本地对象
  POST /integrations/erp/external-mappings/replay       重放待匹配（补齐后不必重拉）
  POST /integrations/erp/sources/verify                 登记来源核实/店铺授权（要证据）
  POST /integrations/erp/reconcile/runs                 触发期间对账（幂等）
  GET  /integrations/erp/reconcile/runs                 对账批次
  GET  /integrations/erp/reconcile/diffs                差异清单
  GET  /integrations/erp/reconcile/diffs/{id}           差异详情 + **原始证据**
  POST /integrations/erp/reconcile/diffs/{id}/confirm   核定差异（带权限与审计）

**权限口径**（为什么这么分）：读接口按 `order:view` + 数据范围给；采集/对账/来源核实
这类**基础设施动作**要 `settings:manage`（与 readiness 诊断同一道门，普通订单查看者
不该触发全公司范围的外部拉取）；映射人工指定与差异核定是**业务动作**，要 `order:manage`，
并且能定位到本地订单时还要过数据范围。

与企微同一套原则：未配置时返回 50203 并说明缺哪个变量，
**不把订单标成已推送**（否则 ERP 里没有单、CRM 却显示已同步）。
结果未知（超时/响应缺成功字段）单独返回 409：它不是"失败"，不能盲目重试，
必须先在外部系统核对单号，所以状态码要让人一眼看出"要去核实"，而不是"重试一下"。
"""

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data
from app.modules.erp import collection as collect_svc
from app.modules.erp import reconcile as recon_svc
from app.modules.erp import service as svc
from app.modules.erp.adapter import ErpError, ErpNotConfigured, ErpResultUnknown
from app.modules.erp.schema import (
    CollectRequest,
    DiffConfirmRequest,
    MappingMatchRequest,
    MappingReplayRequest,
    OrderPushRequest,
    ReconcileRunRequest,
    ReconcileRequest,
    SourceVerifyRequest,
    StatusWebhookRequest,
)
from app.modules.integration.vocab import ALL_SHOPS, DIFF_DOMAIN_ORDER
from app.modules.order import service as order_service
from app.modules.order.model import SalesOrder

router = APIRouter(tags=["ERP"])


def _translate(error: Exception) -> AppError:
    if isinstance(error, ErpNotConfigured):
        return AppError(ErrorCode.ERP_SYNC_FAILED, f"{error}；配置位置：backend/.env", 422)
    if isinstance(error, ErpResultUnknown):
        # 结果未知不是"调用失败可重试"：可能对方已经建单了，重试会多建一张。
        # 409 让前端/调用方一眼看出"要先去核对外部单号"，而不是无脑重试。
        return AppError(ErrorCode.ERP_SYNC_FAILED, str(error), 409)
    if isinstance(error, ErpError):
        kind = getattr(error, "kind", "peer_error")
        if kind in ("not_verified", "mapping_mismatch"):
            # 未验收（没发出去）与本地映射矛盾（不敢推）都不是"对方返回错误"，
            # 用 422/409 区分开，别让运维去查对方系统的日志。
            status = 422 if kind == "not_verified" else 409
            return AppError(ErrorCode.ERP_SYNC_FAILED, str(error), status)
        return AppError(ErrorCode.ERP_SYNC_FAILED, str(error), 502)
    return AppError(ErrorCode.ERP_SYNC_FAILED, f"ERP/MES 同步失败：{error}", 502)


def translate_erp_error(error: Exception) -> AppError:
    """公开的异常翻译，供 order 模块的 `/orders/{id}/sync-erp` 复用。"""
    return _translate(error)


@router.get("/integrations/erp/readiness")
async def erp_readiness(
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """接入状态。

    必须带当前用户进来：统计数字要按他的数据范围给，配置明细只给集成管理员
    （此前这里是无条件 `count(*)`，任何 order:view 的人都能拿到全公司集成统计）。
    """
    return ok(await svc.readiness(session, user=user))


@router.post("/integrations/erp/orders")
async def push_by_order_no(
    payload: OrderPushRequest,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按 CRM 订单号推送。给外部系统或批量脚本调用，等价于 /orders/{id}/sync。"""
    if not payload.order_no:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "order_no 必填", 422)
    order = (
        await session.execute(
            select(SalesOrder).where(SalesOrder.order_no == payload.order_no.strip())
        )
    ).scalars().first()
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, f"订单 {payload.order_no} 不存在", 404)
    # 按单号推送也要过数据范围，否则知道单号就能推别人的订单
    await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
    try:
        result = await svc.push_order(session, order=order, operator_id=user.id)
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_push_order",
        business_type="order",
        business_id=order.id,
        after={"pushed": result["pushed"], "erp_order_id": result.get("erp_order_id")},
        ip=None,
    )
    await session.commit()
    return ok(result, result["message"])


@router.post("/integrations/erp/orders/{order_id}/sync")
async def sync_order(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """把订单推送到 ERP/MES（幂等：推过的直接返回，不重复建单）。"""
    order = await order_service.get_visible_order(session, user, order_id)
    try:
        result = await svc.push_order(session, order=order, operator_id=user.id)
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_push_order",
        business_type="order",
        business_id=order.id,
        after={"pushed": result["pushed"], "erp_order_id": result.get("erp_order_id")},
        ip=None,
    )
    await session.commit()
    return ok(result, result["message"])


@router.post("/integrations/erp/orders/{order_id}/reconcile")
async def reconcile_order(
    order_id: int,
    payload: ReconcileRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """推单结果未知的恢复出口：人工在外部系统核对到单号后登记回来。

    为什么不自动查询：按我方单号反查"对方是否已建单"要依赖真实的接口合同
    （聚水潭 / 企业 erp-bridge 的资料还没拿到），猜一个查询端点比不做更危险。
    登记之后订单变成"已同步"，遗留的 sending/unknown 记录一并了结。
    """
    order = await order_service.get_visible_order(session, user, order_id)
    try:
        result = await svc.reconcile_push(
            session, order=order, external_id=payload.external_id, operator_id=user.id
        )
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_reconcile_order",
        business_type="order",
        business_id=order.id,
        after={"erp_order_id": result["erp_order_id"], "cleared": result["cleared"]},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])


@router.get("/integrations/erp/orders/{order_id}/status")
async def order_status(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """拉取履约状态并回写（写入 order_status_history，source=ERP）。"""
    order = await order_service.get_visible_order(session, user, order_id)
    try:
        result = await svc.refresh_status(session, order=order, operator_id=user.id)
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        raise _translate(error) from error
    await session.commit()
    return ok(result, "状态已同步" if result["changed"] else "状态没有变化")


@router.get("/integrations/erp/sync-logs")
async def list_sync_logs(
    direction: str | None = None,
    status: str | None = None,
    business_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    items, total = await svc.sync_logs(
        session,
        user=user,
        direction=direction,
        status=status,
        business_id=business_id,
        page=page,
        page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


@router.get("/integrations/erp/exception-queue")
async def list_exception_queue(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """异常队列：编号冲突 / 未匹配 / 结果未知 / 未发出的集成留痕。

    这些行大多没有订单归属，按订单过滤的 sync-logs 里根本看不到它们，
    需要单独一个入口给人处理（数据范围照旧：看不到归属就看不到）。
    """
    items, total = await svc.exception_queue(
        session, user=user, status=status, page=page, page_size=page_size
    )
    return ok(page_data(items, total, page, page_size))


# ---- §8.13 只读采集：水位 / 原始事实 / 映射 / 待匹配 -----------------------


@router.get("/integrations/erp/collection/status")
async def collection_status(
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """采集侧总览：水位与断点、**未核实来源**、原始事实与待匹配计数。

    为什么单独一个入口而不是塞进 readiness：readiness 回答"这条通道配没配、验收没验收"，
    这里回答"采到哪了、断在哪、哪些来源还没核实"。两件事的读者和刷新频率都不同；
    而且 readiness 是所有人的高频接口，不该每次都去扫采集表。
    """
    return ok(await collect_svc.collection_status(session))


@router.post("/integrations/erp/collection/run")
async def collection_run(
    payload: CollectRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """触发一次只读采集（按对象类型 + 店铺，从上次断点续拉）。

    未配置 / 未验收时**明确失败**并返回缺什么：不返回"成功但 0 条"——
    那会让人以为"本期对方没有数据"，而实际上一次请求都没发出去。

    审计照写：即使失败也要留"谁在什么时候尝试拉过全公司外部数据"。
    """
    try:
        result = await collect_svc.collect_once(
            session,
            object_type=payload.object_type,
            shop_id=payload.shop_id or ALL_SHOPS,
            page_size=payload.page_size,
            max_pages=payload.max_pages,
            operator_id=user.id,
        )
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        await write_audit(
            session,
            operator_id=user.id,
            action="erp_collect_run",
            business_type="external_collect",
            business_id=None,
            after={
                "object_type": payload.object_type,
                "shop_id": payload.shop_id,
                "status": "failed",
                "reason": str(error),
            },
            source="INTEGRATION",
            ip=client_ip(request),
        )
        await session.commit()
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_collect_run",
        business_type="external_collect",
        business_id=None,
        after={
            "object_type": payload.object_type,
            "shop_id": payload.shop_id,
            "status": result.get("status"),
            "inserted": result.get("inserted"),
            "updated": result.get("updated"),
            "duplicates": result.get("duplicates"),
        },
        source="INTEGRATION",
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result.get("message"))


@router.get("/integrations/erp/external-records")
async def list_external_records(
    object_type: str | None = None,
    shop_id: str | None = None,
    system_type: str | None = None,
    parent_key: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """已采集的**原始事实**（含原样报文）。按数据范围过滤：看不到归属就看不到。"""
    owner_ids = await scoped_owner_ids(session, user)
    items, total = await collect_svc.list_records(
        session,
        owner_ids=owner_ids,
        system_type=system_type,
        shop_id=shop_id,
        object_type=object_type,
        parent_key=parent_key,
        page=page,
        page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


@router.get("/integrations/erp/external-records/{record_id}")
async def get_external_record(
    record_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """单条原始事实（原始报文全文）。"""
    row = await collect_svc.get_record(session, record_id)
    owner_ids = await scoped_owner_ids(session, user)
    # 逐条也要过范围：列表过滤 + 详情不校验，等于猜到 id 就能看别家的原始报文。
    # 判据必须**针对这一条**算（2026-10-07 修）：原来拿"可见列表第 1 页第 1 条"
    # 再判断目标在不在里面 —— 用户有两条以上合法记录时，请求不是排序第一条的那个
    # 会被**误判无权**，合法访问被 403 挡掉（方向与越权相反，但同样是 bug）。
    if not await collect_svc.record_is_visible(session, row.id, owner_ids):
        raise AppError(
            ErrorCode.DATA_SCOPE_DENIED,
            "该原始事实不在你的数据范围内（它关联的订单不属于你可见的负责人）",
            403,
        )
    return ok(collect_svc.serialize_record(row))


@router.get("/integrations/erp/external-mappings")
async def list_external_mappings(
    match_status: str | None = None,
    object_type: str | None = None,
    shop_id: str | None = None,
    system_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """映射台账；`match_status=pending` 就是**待匹配队列**。"""
    owner_ids = await scoped_owner_ids(session, user)
    items, total = await collect_svc.list_mappings(
        session,
        owner_ids=owner_ids,
        match_status=match_status,
        system_type=system_type,
        shop_id=shop_id,
        object_type=object_type,
        page=page,
        page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


@router.post("/integrations/erp/external-mappings/{mapping_id}/match")
async def match_external_mapping(
    mapping_id: int,
    payload: MappingMatchRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """人工把一条待匹配的外部对象指到本地对象上（未知客户/SKU 的补齐出口）。"""
    mapping = await collect_svc.get_mapping(session, mapping_id)
    if mapping.object_type == "order":
        target = await session.get(SalesOrder, payload.internal_id)
        if target is None:
            raise AppError(
                ErrorCode.NOT_FOUND, f"订单 #{payload.internal_id} 不存在", 404
            )
        await ensure_in_scope(session, user, owner_id=target.owner_id, label="订单")
    result = await collect_svc.assign_mapping(
        session,
        mapping,
        internal_id=payload.internal_id,
        operator_id=user.id,
        note=payload.note,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_mapping_match",
        business_type="external_mapping",
        business_id=mapping.id,
        after={
            "object_type": mapping.object_type,
            "external_id": mapping.external_id,
            "internal_id": payload.internal_id,
        },
        source="INTEGRATION",
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, f"已把 {mapping.object_type} {mapping.external_id} 指到本地对象")


@router.post("/integrations/erp/external-mappings/replay")
async def replay_external_mappings(
    payload: MappingReplayRequest,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """重放待匹配行：本地对象补齐后重新匹配，**不需要重新拉取外部数据**。"""
    result = await collect_svc.replay_pending_mappings(
        session,
        system_type=payload.system_type,
        shop_id=payload.shop_id,
        object_type=payload.object_type,
        limit=payload.limit,
    )
    await session.commit()
    return ok(
        result,
        f"重放完成：检查 {result['checked']} 条，匹配上 {result['matched']} 条，"
        f"仍待匹配 {result['still_pending']} 条",
    )


@router.post("/integrations/erp/sources/verify")
async def verify_external_source(
    payload: SourceVerifyRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """登记来源/店铺的核实与授权结论（`verified=True` 必须带证据）。"""
    result = await collect_svc.register_source(
        session,
        system_type=payload.system_type,
        shop_id=payload.shop_id or ALL_SHOPS,
        source_kind=payload.source_kind,
        verified=payload.verified,
        evidence=payload.evidence,
        authorization_note=payload.authorization_note,
        operator_id=user.id,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_source_verify",
        business_type="external_source",
        business_id=None,
        after={
            "system_type": payload.system_type,
            "shop_id": payload.shop_id,
            "source_kind": payload.source_kind,
            "verified": payload.verified,
        },
        source="INTEGRATION",
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        result,
        "来源已登记为已核实" if result["verified"] else "来源标记为未核实（待核实）",
    )


# ---- §8.13 对账与差异核定 --------------------------------------------------


@router.post("/integrations/erp/reconcile/runs")
async def create_reconcile_run(
    payload: ReconcileRunRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """跑一次期间对账（同一系统/店铺/期间重复跑是幂等的）。"""
    result = await recon_svc.run_reconciliation(
        session,
        period_start=payload.period_start,
        period_end=payload.period_end,
        shop_id=payload.shop_id or ALL_SHOPS,
        operator_id=user.id,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_reconcile_run",
        business_type="reconciliation_run",
        business_id=result["run"]["id"],
        after=result["counters"],
        source="INTEGRATION",
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])


@router.get("/integrations/erp/reconcile/runs")
async def list_reconcile_runs(
    system_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    items, total = await recon_svc.list_runs(
        session, system_type=system_type, page=page, page_size=page_size
    )
    return ok(page_data(items, total, page, page_size))


@router.get("/integrations/erp/reconcile/diffs")
async def list_reconcile_diffs(
    domain: str | None = None,
    status: str | None = None,
    diff_type: str | None = None,
    shop_id: str | None = None,
    period: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """差异清单（对账差异 + SKU 主数据差异共用这一张队列表）。"""
    items, total = await recon_svc.list_diffs(
        session,
        domain=domain,
        status=status,
        diff_type=diff_type,
        shop_id=shop_id,
        period=period,
        page=page,
        page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


@router.get("/integrations/erp/reconcile/diffs/{diff_id}")
async def get_reconcile_diff(
    diff_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """差异详情 + **按证据指针取回的原始报文**（"点回原始证据"的落地）。"""
    diff = await recon_svc.get_diff(session, diff_id)
    return ok(await recon_svc.diff_evidence(session, diff))


@router.post("/integrations/erp/reconcile/diffs/{diff_id}/confirm")
async def confirm_reconcile_diff(
    diff_id: int,
    payload: DiffConfirmRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """核定一条差异。

    核定**只写差异行**：外部事实不会因为有人点了"以外部为准"就去改本地回款/应收/业绩
    （退货退款的来源与时点口径用户还没拍板）。能定位到本地订单时还要过数据范围。
    """
    diff = await recon_svc.get_diff(session, diff_id)
    if diff.domain == DIFF_DOMAIN_ORDER and diff.internal_id is not None:
        order = await session.get(SalesOrder, diff.internal_id)
        if order is not None:
            await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
    result = await recon_svc.confirm_diff(
        session,
        diff,
        resolution=payload.resolution,
        note=payload.note,
        operator_id=user.id,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_diff_confirm",
        business_type="integration_diff",
        business_id=diff.id,
        before={"status": "open", "diff_type": diff.diff_type},
        after={
            "status": result["diff"]["status"],
            "resolution": payload.resolution,
            "note": payload.note,
        },
        source="INTEGRATION",
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])


# ---- 回调：无需登录，由对方系统调用 ---------------------------------------

def _verify_webhook_secret(request: Request) -> None:
    """回调来源校验（P0）。

    这两个回调**直接改订单状态**（取消/签收/完成），此前**没有任何鉴权**
    ——原注释写着"对方系统尚未提供签名"，但"对方没提供"不等于"我们可以不验"：
    入口是公开的，任何人都能伪造一张"已签收"。

    现在改成共享密钥（`X-ERP-Secret`），并且**没配密钥就关闭入口**（fail closed）。
    不这么做的话，最容易被忽略的恰恰是"忘了配密钥"这个状态——
    那时它又变成匿名开放，等于没修。

    重放与乱序由 `service.apply_status_webhook` 负责：事件带稳定事件键，
    同一事件重放不再写历史/日志；双编号必须同源。**不在这里**做状态合法性判断。
    """
    from app.core.config import settings as app_settings

    secret = (app_settings.erp_webhook_secret or "").strip()
    if not secret:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "ERP/MES 回调未启用：未配置 ERP_WEBHOOK_SECRET。"
            "配置密钥前该入口一律拒绝，避免匿名改写订单状态",
            403,
        )
    provided = (request.headers.get("X-ERP-Secret") or "").strip()
    if not provided or not secrets.compare_digest(provided, secret):
        raise AppError(ErrorCode.FORBIDDEN, "回调密钥校验失败", 403)


async def _raw_event(request: Request) -> dict[str, Any] | None:
    """取回调的原始报文，连同业务字段一起留存。

    为什么要原文：未匹配的事件事后要能重放、能核对，只留编号是不够的
    （§8.11：原来只存 order_no / erp_order_id，连推的是哪个状态都查不到）。
    FastAPI 校验请求体时已经读过 body，Starlette 会缓存，所以这里能再读一次；
    读不到就返回 None，用校验过的字段兜底，绝不让留痕失败影响处理。
    """
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else {"_raw": data}


@router.post("/webhooks/erp/order-status")
async def order_status_webhook(
    payload: StatusWebhookRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """订单状态回调。

    需要 `X-ERP-Secret` 头与 `ERP_WEBHOOK_SECRET` 一致；未配置密钥则入口直接关闭。
    只接受"能找到的订单 + 已知状态"，其余记一条失败日志（或进异常队列）并返回 200
    —— 让对方别因为我们反复重试。
    """
    _verify_webhook_secret(request)
    result = await svc.apply_status_webhook(
        session,
        order_no=payload.order_no,
        erp_order_id=payload.erp_order_id,
        raw_status=payload.status,
        remark=payload.remark,
        raw_event=await _raw_event(request),
    )
    return ok(result, result.get("message") or ("已处理" if result["matched"] else "未处理"))


@router.post("/webhooks/erp/shipment-status")
async def shipment_status_webhook(
    payload: StatusWebhookRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """发货状态回调：与订单状态共用处理逻辑（发货对应 shipped/delivered）。"""
    _verify_webhook_secret(request)
    result = await svc.apply_status_webhook(
        session,
        order_no=payload.order_no,
        erp_order_id=payload.erp_order_id,
        raw_status=payload.status,
        remark=payload.remark or "ERP/MES 发货状态回调",
        raw_event=await _raw_event(request),
    )
    return ok(result, result.get("message") or ("已处理" if result["matched"] else "未处理"))
