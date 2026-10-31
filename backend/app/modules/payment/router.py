"""应收与回款接口（对齐 03-API §29 / §30）。"""

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import idempotency
from app.core.audit import json_safe, write_audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.file import service as file_service
from app.modules.file import storage
from app.modules.file.model import FileRecord
from app.modules.order.model import SalesOrder
from app.modules.notification import service as notification_service
from app.modules.order.service import get_order_or_404
from app.modules.payment import service as svc
from app.modules.payment.model import (
    PaymentAllocation,
    PaymentRecord,
    ReceivablePlan,
)
from app.modules.payment.schema import (
    PaymentAction,
    PaymentAllocationUpdate,
    PaymentCreate,
    PaymentUpdate,
    ReceivableCreate,
    ReceivableGenerate,
    ReceivableUpdate,
)

router = APIRouter(tags=["Payment"])
logger = logging.getLogger(__name__)


@router.get("/receivables")
async def list_receivables(
    status: str | None = None,
    order_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(ReceivablePlan)
    # 应收没有自己的负责人，可见性跟着订单走。此前这里完全没过滤，
    # 张三配了 scope=self 也能看到全公司的应收明细。
    visible = await svc.visible_order_ids_stmt(session, user)
    if visible is not None:
        stmt = stmt.where(ReceivablePlan.order_id.in_(visible))
    if status:
        # 逾期/部分回款**按口径现算**，不比缓存的状态列（2026-10-13 主人拍板）：
        # - 逾期 = 到期日已过且没收齐（收过一部分也算）；
        # - 部分回款 = 收过一部分 **且还没到期**。
        # 不这样做的话，"到期了但今天还没刷新状态"的节点会躲过逾期筛选，
        # 与工作台/审批条件的口径对不上（那正是"两套账"的来源）。
        received_sq = func.coalesce(svc.plan_received_expression(), 0)
        overdue_cond = svc.plan_overdue_expression()
        active = ReceivablePlan.status != "cancelled"
        if status == "overdue":
            stmt = stmt.where(overdue_cond)
        elif status == "partial":
            stmt = stmt.where(active, received_sq > 0, received_sq < ReceivablePlan.amount, not_(overdue_cond))
        elif status == "paid":
            stmt = stmt.where(active, received_sq > 0, received_sq >= ReceivablePlan.amount)
        elif status == "pending":
            stmt = stmt.where(active, received_sq <= 0, not_(overdue_cond))
        else:
            stmt = stmt.where(ReceivablePlan.status == status)
    if order_id:
        stmt = stmt.where(ReceivablePlan.order_id == order_id)
    rows, total = await paginate(session, stmt.order_by(ReceivablePlan.due_date.asc()), page, page_size)
    items = [await svc.serialize_plan(session, plan) for plan in rows]
    return ok(page_data(items, total, page, page_size))


@router.post("/receivables")
async def create_receivable(
    payload: ReceivableCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """建应收节点（03-API §29）；order_id 从 body 取。

    `POST /orders/{id}/receivables` 是同一条逻辑的订单内入口。
    """
    if not payload.order_id:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请指定 order_id")
    return await _create_plan(session, payload.order_id, payload, request, user)


@router.get("/receivables/{plan_id}")
async def get_receivable(
    plan_id: int,
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    plan = await svc.get_visible_plan(session, user, plan_id)
    return ok(await svc.serialize_plan(session, plan))


async def _create_plan(session, order_id, payload, request, user):
    order = await get_order_or_404(session, order_id)
    await svc.assert_order_visible(session, user, order_id)
    # 与"按比例生成"共用同一把订单行锁（锁序见 svc.lock_order）：否则生成那边
    # "查无计划"的窗口里还能挤进一个手工节点，两边判断都不算错、结果却混着两套。
    if await svc.lock_order(session, order_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    # ⚠️ 生命周期检查必须放在**拿锁之后**（C5-02，2026-10-10 修）。
    #
    # 原来它在 `lock_order` 之前：请求先读到订单 `pending`，随后订单被并发取消，
    # 加锁这一步不会发现 —— 因为它查的是自己刚读到的那个（已过期的）对象，
    # 于是新增应收照样成功，取消的订单上又长出一个催收节点。
    # `lock_order` 现在带 `populate_existing`，会**就地把同一个实例刷新成最新值**，
    # 所以这里重查一次 `order` 拿到的是锁内的事实。
    await svc.assert_order_can_add_receivable(order)
    plan = ReceivablePlan(
        order_id=order_id,
        plan_name=payload.plan_name,
        due_date=payload.due_date,
        amount=Decimal(str(payload.amount)),
        currency=order.currency,
        status="pending",
        remark=payload.remark,
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    await session.flush()
    # 刚插入的行只有本事务看得见（别的会话连行都选不到），不需要再取节点锁。
    await svc.recalc_plan(session, plan)
    await session.flush()
    # 多了一期应收 → "收齐"这个前提变了，跟单节点要跟着重算
    # （第三批审查 P1：已收齐的订单再加一期，收款节点必须退回未完成）
    from app.modules.order import milestones as milestones_svc

    await milestones_svc.sync_payment_milestones(session, order_id, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="receivable_plan",
        business_id=plan.id,
        after=await svc.serialize_plan(session, plan),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_plan(session, plan), "应收节点已创建")


@router.post("/orders/{order_id}/receivables")
async def create_order_receivable(
    order_id: int,
    payload: ReceivableCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    return await _create_plan(session, order_id, payload, request, user)


@router.post("/orders/{order_id}/receivables/preview")
async def preview_receivables(
    order_id: int,
    payload: ReceivableGenerate,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """**预览**按比例分期的各期金额，不落库（第九批问题 ⑤）。

    为什么要有这个接口：前端原来自己用 JavaScript 浮点 + `Math.round` 算预览，
    而后端用 `Decimal`（默认 `ROUND_HALF_EVEN`）。**同一份输入两边结果不同**：
    `0.05` 元分两期，前端显示 `0.03 / 0.02`、后端落库 `0.02 / 0.03` ——
    用户确认的金额与保存的金额不是一回事。

    现在预览与落库**都走 `svc.split_amounts`**，用户看到什么就存什么。
    不合法（分出不大于 0 的期）时同样返回 422，让前端在提交前就能说清原因。
    """
    # ⚠️ **数据范围必须先查**（第十批审查 ④，2026-10-13）。
    #
    # 这个预览接口是第九批加的，当时只调了 `get_order_or_404`（只判"存不存在"），
    # 漏了范围校验 —— 实测：只有 `payment:manage`、数据范围只有自己的账号，
    # 预览别人的订单能拿到总额 9876.54 元与各期金额；同一个账号调**正式生成**
    # 却被正确拒绝（那条路有 `assert_order_visible`）。
    #
    # 预览也是"把别人订单的金额读出来"，性质和详情一样，必须同一道闸门。
    await svc.assert_order_visible(session, user, order_id)
    # 取订单用本文件既有的入口（不是自己拼 `get_visible_order`；
    # 我第一版写了个不存在的 `_f` 与错误的取单方式，pyflakes 当场报出来了）
    order = await get_order_or_404(session, order_id)
    await svc.assert_order_visible(session, user, order_id)
    ratios = [Decimal(str(r)) for r in payload.ratios]
    amounts = svc.split_amounts(order.total_amount, ratios)
    return ok(
        {
            "total_amount": svc._f(order.total_amount),
            "currency": order.currency,
            "amounts": [svc._f(amount) for amount in amounts],
        }
    )


@router.post("/orders/{order_id}/receivables/generate")
async def generate_receivables(
    order_id: int,
    payload: ReceivableGenerate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按比例生成应收计划，例如 30% 定金 + 70% 尾款。

    第七批 7.9：从"查无再插"改成"**先锁订单行**再查无再插"。两个并发的生成
    请求以前都查到"还没有计划"，于是各插一套（金额各自合法，事后只能人工比对）；
    现在后到的那个会等在订单行锁上，等到时先到者已提交，`existing` 一定看得见。
    带 `request_key` 的重复点击另外走通用请求幂等，直接回放第一次那份结果。
    """
    order = await get_order_or_404(session, order_id)
    await svc.assert_order_visible(session, user, order_id)
    # 取消的订单不许再产生应收（N03）。放在幂等占位**之前**：注定失败的操作
    # 不该占掉一把请求键，否则用户"改完再来"会撞上同键冲突。
    await svc.assert_order_can_add_receivable(order)

    data = payload.model_dump()
    request_key = idempotency.request_key_from(request, data.pop("request_key", None))
    # ---- 幂等指纹必须含**路径里的 order_id**（第二轮审查 P1，2026-10-12）----
    #
    # 原来指纹只算 body（比例、期数、日期），而 order_id 在**路径**上。于是
    # "订单 1 用某把键生成过"之后，对订单 2 用同一把键、同样参数提交时指纹完全
    # 相同 → 直接回放订单 1 的那份结果：**订单 2 一个计划都没生成，却提示成功**。
    # 把 order_id 并进指纹后，同键换订单会走"同键不同内容"的 409 冲突，说得清楚。
    fingerprint = {**data, "order_id": order_id}
    reservation = None
    if request_key:
        reservation = await idempotency.reserve(
            session,
            user_id=user.id,
            action="receivable:generate",
            request_key=request_key,
            payload=fingerprint,
            result_type="receivable_plan",
        )
        if reservation.should_replay:
            # 回放前还要**复验那份结果确实是这张订单的**（存在性 + 归属）：
            # 加指纹之前的旧记录、以及"结果 id 指向别的单"这类异常，都不能当成功返回。
            # 数据范围由上面 `assert_order_visible` 对本次 order_id 的校验兜住。
            await _assert_replay_belongs_to_order(
                session, order_id=order_id, plan_id=reservation.replay_id
            )
            return ok(
                reservation.replay_payload,
                "这次生成此前已成功过，已返回原计划（没有重复生成）",
            )
    try:
        return await _generate_receivables(
            session, order, order_id, payload, request, user, reservation
        )
    except Exception:
        # 失败就释放占位：用户改完表单会带同一把键重试，内容必然不同，
        # 不释放会把"改错重填"误判成"同键不同内容"冲突。
        if reservation is not None:
            await idempotency.release(session, reservation)
        raise


async def _assert_replay_belongs_to_order(
    session: AsyncSession, *, order_id: int, plan_id: int | None
) -> None:
    """回放幂等结果前的复验：那份结果必须**存在**且**属于本次请求的订单**。

    为什么不能只靠指纹：指纹是"这次提交"与"上次提交"比对的依据，而它说明不了
    *上次那份结果到底建在哪张单上*。加 order_id 进指纹之前留下的记录、以及
    `result_id` 指错对象这类脏数据，都只能在这里拦。比对不上就明确报冲突，
    绝不把它当成"你已经成功过了"返回（那是让用户以为订单 2 有应收、实际没有）。
    """
    plan = await session.get(ReceivablePlan, plan_id) if plan_id else None
    if plan is None or plan.order_id != order_id:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "这把请求键此前的结果不属于本订单（可能是同一把键用在了别的订单上），"
            "已拒绝回放。请刷新页面重新打开表单后再提交",
            409,
        )


async def _generate_receivables(session, order, order_id, payload, request, user, reservation):
    # 锁共同订单行（统一锁序的第一把）：见函数说明。
    if await svc.lock_order(session, order_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    # ⚠️ 拿锁之后**再查一次生命周期**（C5-02，2026-10-10 修）。
    #
    # 调用方在进来之前已经查过一次，但那次是**锁外**的：请求读到订单 `pending`，
    # 随后订单被并发取消 —— 那次检查看不出任何异常，而这个窗口里新增应收会成功。
    # 保留锁外那次是有理由的（注释在调用方：注定失败的操作不该占掉一把请求键），
    # 所以这里**再查一次**而不是把它挪下来。
    # `lock_order` 带 `populate_existing`，会就地刷新同一个实例，所以此处读到的
    # 是锁内的事实。
    await svc.assert_order_can_add_receivable(order)
    # 比例校验**保持原样**（容差 0.0001）：实测浮点误差只有 1e-16 量级，够不到容差，
    # 不会误判；而比例微差的风险已由下面"最后一期补差额"兜住——就算有人把比例
    # 打成 99.99%，最后一期 = 总额 − 前面各期之和，合计仍然严格等于订单总额。
    if not payload.ratios or abs(sum(payload.ratios) - 1) > 0.0001:
        raise AppError(ErrorCode.PARAM_ERROR, "比例之和必须等于 1，例如 [0.3, 0.7]")
    # 金额计算用 Decimal：保留输入字面值，不引入浮点误差（属于舍入修复的一部分）
    ratios = [Decimal(str(r)) for r in payload.ratios]

    # 比例本身要合法：没有期数、有非正数、期数过多都会算出没意义的计划
    if not ratios:
        raise AppError(ErrorCode.PARAM_ERROR, "至少要有一期比例", 422)
    if any(r <= 0 for r in ratios):
        raise AppError(ErrorCode.PARAM_ERROR, "每期比例必须大于 0", 422)
    if len(ratios) > 24:
        raise AppError(ErrorCode.PARAM_ERROR, f"分期期数过多（{len(ratios)} 期，最多 24 期）", 422)
    if abs(sum(ratios, Decimal(0)) - Decimal(1)) > Decimal("0.0001"):
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"各期比例之和必须等于 1（当前 {sum(ratios, Decimal(0))}）",
            422,
        )

    existing = (
        await session.execute(
            select(ReceivablePlan.id).where(ReceivablePlan.order_id == order_id)
        )
    ).first()
    if existing:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该订单已有应收计划，请先删除再重新生成")

    due_dates = (
        list(payload.due_dates)
        if payload.due_dates
        else [payload.first_due_date, payload.second_due_date or payload.first_due_date]
    )
    # 名称/到期日的取法（第九批问题 ⑥）：
    #   · 给了整份 `names` / `due_dates`（按期对齐）→ 直接用，一次请求成型；
    #   · 只给前两个槽位（老写法）→ 用 `first_*` / `second_*`；
    #   · 都没给 → 「第 N 期」+ 第一期日期。
    # 三期以上从前只能"先生成、再逐条 PATCH"，中途失败就会留下占位名与
    # 第一期日期、且重试撞"已有应收计划"。现在整份一次落库（同一事务）。
    names = list(payload.names) if payload.names else [payload.first_name, payload.second_name]
    # 各期金额：前面按比例算，**最后一期用"总额 − 前面各期之和"**。
    # 每期独立四舍五入会让合计不等于总额（订单 0.05 元按 50%/50% 得
    # 0.02 + 0.02 = 0.04，少一分）；差额补在最后一期，合计严格等于订单总额。
    total = Decimal(order.total_amount)
    # 拆金额与"有没有非正数期"的校验**都在 `svc.split_amounts` 一处**
    # （第九批问题 ①：原来这里写了一遍带校验的，转单那边又写了一遍漏了校验的，
    #  于是转单能生成负数额应收）
    amounts = svc.split_amounts(total, ratios)

    created = []
    for index, ratio in enumerate(ratios):
        plan = ReceivablePlan(
            order_id=order_id,
            plan_name=names[index] if index < len(names) else f"第 {index + 1} 期",
            due_date=due_dates[index] if index < len(due_dates) else payload.first_due_date,
            amount=amounts[index],
            # **必须显式继承订单币种**（第十一批 11.1）。不写这一行，它会落到列默认的
            # CNY：美元订单按比例分出来的几期全成了人民币，金额看着对、代表的钱已经不同。
            # 手工新增那条路（`_create_plan`）一直是这样写的，两条路的币种规则必须一致。
            # 后面的回款登记是"跟着应收节点币种走"的，所以错在这里会一路错到回款。
            currency=order.currency,
            status="pending",
            created_at=datetime.now(UTC),
        )
        session.add(plan)
        await session.flush()
        created.append(plan)
    # 新增整套应收 → 跟单节点按新的事实重算（同上，第三批审查 P1）
    from app.modules.order import milestones as milestones_svc

    await milestones_svc.sync_payment_milestones(session, order_id, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="generate",
        business_type="receivable_plan",
        business_id=order_id,
        after={"order_id": order_id, "created": len(created), "ratios": payload.ratios},
        ip=client_ip(request),
    )
    body = [await svc.serialize_plan(session, plan) for plan in created]
    if reservation is not None:
        await idempotency.complete(
            session,
            reservation,
            result_payload=json_safe(body),
            result_id=created[0].id if created else order_id,
        )
    await session.commit()
    return ok(body, "应收计划已生成")


@router.patch("/receivables/{plan_id}")
async def update_receivable(
    plan_id: int,
    payload: ReceivableUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改应收节点 —— 部分更新，只改传进来的字段。

    取节点时必须带锁（`for_update=True`）：改金额要重算节点状态，重算不在锁内
    就是"读旧汇总、写覆盖"——与并发确认撞上时，后写的那个把 paid 改回 partial。

    ⚠️ 改完还要**同步跟单节点**（第三批审查 P1，2026-10-12）：把应收从 100 改成 200
    之后，已确认回款仍是 100，应收状态正确变 `partial`，而「收款」节点却继续显示
    已完成 —— 一份订单两个说法。同步与这次改动在同一把订单锁、同一个事务内
    （`get_visible_plan(for_update=True)` 内部已按"订单 → 节点"锁序取锁）。
    """
    plan = await svc.get_visible_plan(session, user, plan_id, for_update=True)
    before = await svc.serialize_plan(session, plan)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("plan_name") is not None:
        plan.plan_name = changes["plan_name"]
    if changes.get("due_date") is not None:
        plan.due_date = changes["due_date"]
    if changes.get("amount") is not None:
        plan.amount = Decimal(str(changes["amount"]))
    if "remark" in changes:
        plan.remark = changes["remark"]
    # ⚠️ 应收金额变了要**重新平衡分配**（第十批实测发现的缺口）。
    #
    # 分配原来只在"确认回款"时算一次；把某一期从 100 改大到 200 之后，
    # 原来收到的钱不够了，而**另一期超收出来的钱不会自动补过来** ——
    # 客户其实付够了，系统却显示还差 100（`check_round3_risk_repairs` 就是这么红的）。
    # 主人定的口径是"超收按到期日自动抵后续期"，所以平衡要跟着应收变化走。
    await svc.rebalance_order_allocations(session, plan.order_id, operator_id=user.id)
    for each_plan in (
        await session.execute(
            select(ReceivablePlan)
            .where(
                ReceivablePlan.order_id == plan.order_id,
                ReceivablePlan.status != "cancelled",
            )
            .order_by(ReceivablePlan.id.asc())
        )
    ).scalars().all():
        await svc.recalc_plan(session, each_plan)
    await session.flush()
    from app.modules.order import milestones as milestones_svc

    await milestones_svc.sync_payment_milestones(
        session, plan.order_id, operator_id=user.id
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="receivable_plan",
        business_id=plan.id,
        before=before,
        after=await svc.serialize_plan(session, plan),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_plan(session, plan), "已保存")


@router.delete("/receivables/{plan_id}")
async def delete_receivable(
    plan_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """删应收节点。

    取节点时带锁：删之前要确认"还没有回款挂在它上面"。不带锁时，另一个事务
    正好在给这个节点登记回款，两边各自读到"没有/节点还在"，结果回款挂在一个
    已被删掉的节点上（孤儿账）。锁上之后两种顺序都安全：先登记的先插、删除方
    看得到；先删的删完，登记方拿到锁后重查节点已不存在 → 404。

    删掉一个节点会让"应收合计"变小（删到"已确认收款 ≥ 应收"时「收款」节点该完成），
    所以删完要在**同一个事务**里同步跟单节点（第三批审查 P1，2026-10-12）。
    """
    plan = await svc.get_visible_plan(session, user, plan_id, for_update=True)
    paid = (
        await session.execute(
            select(PaymentRecord.id).where(PaymentRecord.receivable_plan_id == plan_id)
        )
    ).first()
    allocated = (await session.execute(select(PaymentAllocation.id).where(
        PaymentAllocation.plan_id == plan_id).limit(1))).first()
    if paid or allocated:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该应收节点已有回款记录或收款分配，不能删除；请先核对并调整回款分配", 422)
    order_id = plan.order_id
    before = await svc.serialize_plan(session, plan)
    await session.delete(plan)
    # 先 flush：同步要算的"应收合计"必须已经不含这一行
    await session.flush()
    from app.modules.order import milestones as milestones_svc

    await milestones_svc.sync_payment_milestones(session, order_id, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="receivable_plan",
        business_id=plan_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已删除")


@router.post("/receivables/{plan_id}/mark-overdue")
async def mark_overdue(
    plan_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """把节点直接标成逾期。

    **终态不许覆盖**（N02，2026-10-09 修）：已收齐（`paid`）与已取消
    （`cancelled`）的节点，这个接口从前是**无条件**写 `status="overdue"` ——
    实测把一条已结清的节点标成了逾期，财务结论被业务操作盖掉。
    现在先判终态，明确报错并说清当前状态。

    同样要先锁节点：这是"覆盖状态"的写入，与并发确认的重算放在同一把锁下，
    谁后写都是基于最新事实（否则确认刚把节点算成 paid，这边一个旧状态覆盖回去）。
    """
    plan = await svc.get_visible_plan(session, user, plan_id, for_update=True)
    # ⚠️ 这里**不能用 `svc.TERMINAL_PLAN_STATUSES`**（C4-01 补修，2026-10-09）：
    # 那个常量现在只剩 `cancelled`（`paid` 已移出，好让它参与"改金额后如实重算"）。
    # 而"不许把已结清的节点标成逾期"是**本端点自己的**业务规则，与重算无关 ——
    # 我第一版图省事复用了那个常量，结果把这个保护一起拆掉了（实测已结清的节点
    # 能被标成逾期），是靠新增的断言 ⑥ 抓出来的。
    if plan.status in ("paid", "cancelled"):
        # 文案与"只有待确认的回款可以操作"同一风格：先说清为什么不行，
        # 再说清当前是什么状态，让人知道该去看哪里。
        label = svc.PLAN_STATUS_LABEL.get(plan.status, plan.status)
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该应收节点已是「{label}」，不能再标为逾期"
            + ("；已收齐的节点不能退回待收。" if plan.status == "paid"
               else "；随订单取消的节点不再参与催收。"),
            422,
        )
    before_status = plan.status
    plan.status = "overdue"
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="mark_overdue",
        business_type="receivable_plan",
        business_id=plan.id,
        before={"status": before_status},
        after={"status": "overdue"},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_plan(session, plan), "已标记为逾期")


@router.get("/payments")
async def list_payments(
    status: str | None = None,
    order_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(PaymentRecord)
    # 同 list_receivables：可见性跟着订单走。
    visible = await svc.visible_order_ids_stmt(session, user)
    if visible is not None:
        stmt = stmt.where(PaymentRecord.order_id.in_(visible))
    if status:
        stmt = stmt.where(PaymentRecord.status == status)
    if order_id:
        stmt = stmt.where(PaymentRecord.order_id == order_id)
    rows, total = await paginate(session, stmt.order_by(PaymentRecord.id.desc()), page, page_size)
    items = [await svc.serialize_payment(session, row) for row in rows]
    return ok(page_data(items, total, page, page_size))


@router.get("/payments/{payment_id}")
async def get_payment(
    payment_id: int,
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    record = await svc.get_visible_payment(session, user, payment_id)
    return ok(await svc.serialize_payment(session, record))


@router.get("/payments/{payment_id}/voucher")
async def download_payment_voucher(
    payment_id: int,
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    """下载回款凭证；权限与回款记录相同，销售与财务都按订单数据范围校验。"""
    record = await svc.get_visible_payment(session, user, payment_id)
    if not record.voucher_file_id:
        raise AppError(ErrorCode.NOT_FOUND, "该回款还没有凭证", 404)
    voucher = await session.get(FileRecord, record.voucher_file_id)
    if voucher is None:
        raise AppError(ErrorCode.NOT_FOUND, "回款凭证文件不存在", 404)
    path = storage.absolute_path(voucher.object_key)
    if not path.exists():
        raise AppError(ErrorCode.NOT_FOUND, "回款凭证文件内容已丢失", 404)
    from urllib.parse import quote

    return FileResponse(
        path,
        media_type=voucher.mime_type or "application/octet-stream",
        filename=voucher.file_name,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(voucher.file_name)}"},
    )


@router.post("/payments/{payment_id}/voucher")
async def upload_payment_voucher(
    payment_id: int,
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """上传单个待确认回款凭证；通用 file 权限不授予财务角色。"""
    record = await svc.get_visible_payment(session, user, payment_id, for_update=True)
    if record.status != "pending":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "回款确认或驳回后不能再上传凭证")
    if record.voucher_file_id:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已有凭证，请先删除旧凭证再上传")

    object_key, size, checksum = await storage.save_upload(file)
    voucher = FileRecord(
        storage_provider=settings.storage_provider,
        object_key=object_key,
        file_name=file.filename or "回款凭证",
        mime_type=file.content_type,
        size=size,
        checksum=checksum,
        uploaded_by=user.id,
    )
    try:
        session.add(voucher)
        await session.flush()
        record.voucher_file_id = voucher.id
        await write_audit(
            session,
            operator_id=user.id,
            action="attach_voucher",
            business_type="payment",
            business_id=record.id,
            after={
                "voucher_file_id": voucher.id,
                "file_name": voucher.file_name,
                "size": size,
            },
            ip=client_ip(request),
        )
        await session.commit()
    except Exception:
        # 登记失败时事务会回滚，但刚写上盘的那份凭证还在 —— 不清理就永远躺在
        # 盘上，而且和"上传成功"的文件长得一模一样（第十一批 11.8）。
        # 补偿与通用上传共用同一份逻辑，不在这里另写一套。
        await file_service.discard_unregistered_upload(session, object_key)
        raise
    except asyncio.CancelledError:
        # 请求被取消同样要补偿（第七批 P2-9）：`CancelledError` 是 BaseException，
        # 上面 `except Exception` 捕不到 —— 客户端断开时这份凭证会留在盘上
        # 而库里没有记录。与通用上传入口同一个漏、同一套修法。
        await file_service.discard_unregistered_upload(session, object_key)
        raise
    return ok({"voucher_file_id": voucher.id, "voucher_file_name": voucher.file_name}, "凭证已上传")


@router.delete("/payments/{payment_id}/voucher")
async def delete_payment_voucher(
    payment_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """删掉待确认回款的凭证：**解除这条回款的关联**，文件本体只在没人再用它时才删。

    第十一批 11.2 修：这个入口原先直接 `session.delete(FileRecord)` + 删磁盘文件，
    **一道保护都没过**（通用删除入口那三道：已签/生成的原件、多对象引用、打样依据）。
    于是同一份文件先当回款凭证、又被关联成"合同已签文件"时，删凭证会让**已签合同
    的原件连带消失**，合同还显示"已签署"、已签文件列表却空了。

    现在判据统一走 `file.service.inspect_file_usage`：有受保护引用直接拒；
    别处还在用的**只解绑、不删文件**；只有确实没人用了才把文件记录和磁盘文件一起删。
    """
    record = await svc.get_visible_payment(session, user, payment_id, for_update=True)
    if record.status != "pending":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "回款确认或驳回后不能删除凭证")
    if not record.voucher_file_id:
        raise AppError(ErrorCode.NOT_FOUND, "该回款还没有凭证", 404)

    old_file_id = record.voucher_file_id
    # 文件行**带锁**取：检查与删除之间不能让别的入口（例如合同登记签署）把这份
    # 文件挂过去 —— 那正是"检查时没有引用、真正删除前又被人关联"的来路。
    voucher = (
        await session.execute(
            select(FileRecord).where(FileRecord.id == old_file_id).with_for_update()
        )
    ).scalars().first()

    usage = await file_service.inspect_file_usage(
        session, old_file_id, exclude_payment_id=payment_id
    )
    if usage.protected is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"这份文件同时是{usage.protected}，不能删除；"
            "确需纠错请走对应的作废/修订流程，或先解除那一处关联",
            422,
        )

    object_key = voucher.object_key if voucher is not None else None
    record.voucher_file_id = None
    # 别处还在用（业务附件 / 合同生成稿 / 单据生成稿 / 打样依据 / 另一条回款的凭证）
    # 时**只解绑**：删文件等于一次影响好几个业务对象。
    file_removed = not usage.shared
    if file_removed and voucher is not None:
        await session.delete(voucher)
    await write_audit(
        session,
        operator_id=user.id,
        action="remove_voucher",
        business_type="payment",
        business_id=record.id,
        before={
            "voucher_file_id": old_file_id,
            "file_name": voucher.file_name if voucher else None,
            # 记清这次是"解绑"还是"连文件一起删"——事后翻账要分得出来
            "file_removed": file_removed,
            "still_used_by": usage.other_holders,
        },
        ip=client_ip(request),
    )
    await session.commit()
    if file_removed and object_key is not None:
        # 数据库删除已提交。磁盘清理失败只能留下孤儿文件，不能把已成功的
        # 删除返回成 500，否则客户端重试时只会得到“凭证不存在”。
        try:
            storage.delete_object(object_key)
        except Exception:  # noqa: BLE001 —— 与通用文件删除保持相同的提交后清理语义
            logger.warning(
                "回款凭证 %s 已从库中删除，但磁盘原件清理失败：%s",
                old_file_id,
                object_key,
                exc_info=True,
            )
    if file_removed:
        return ok(
            {"detached_only": False, "file_deleted": True},
            "凭证已删除（该文件没有其他引用，文件本体一并清除）",
        )
    return ok(
        {"detached_only": True, "file_deleted": False},
        "已解除这条回款的凭证关联；该文件仍被其他业务使用，未删除",
    )


@router.put("/payments/{payment_id}/allocations")
async def update_payment_allocations(
    payment_id: int,
    payload: PaymentAllocationUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """人工调整一笔回款的**分配**（分给哪几期、各多少）——第十批主人定。

    主人原话（选方案1）：「系统不能擅自调整已经登记的收款归属，必须由有权限的
    人员确认后才能调整」+「调整时要保留完整的操作审计记录，包括原归属期数、
    新归属期数、调整金额、操作人员和操作时间」。

    所以这里：

    - **整份替换**：传进来的就是这笔回款的全部分配；没传的期即不分配。
      这样审计里"原归属 → 新归属"是一条清晰对照，而不是一串增删改；
    - 全部标 `source='manual'`：标过人工的回款，之后系统自动分配**不会覆盖**
      它们（`allocate_payment` 只重算 `auto` 那部分）；
    - 写审计（含 `before` / `after` 与原因），并标明是谁、什么时候改的；
    - 改完立刻重算相关各期的状态与跟单节点 —— 否则页面还显示旧的"未收齐"。

    用途（主人点的两个场景）：把挂错期的回款改到正确的期；或客户提前付了后续期，
    人工确认后把超额部分分给后续期（而不是被当成登记错误）。

    ⚠️ 分配总额不得超过回款金额：多出来的钱没有来源，等于凭空记了一笔已收。
    """
    record = await svc.get_visible_payment(session, user, payment_id, for_update=True)
    if record.status != "confirmed":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "只有已确认的回款才谈得上分配；这笔还是「待确认」，请先确认到账",
            422,
        )
    order = await session.get(SalesOrder, record.order_id)
    if order is not None and order.status == "cancelled":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "订单已取消，不能再调整回款分配", 422)

    total = sum((item.amount for item in payload.allocations), Decimal(0))
    received = Decimal(record.received_amount or 0)
    if total > received:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"分配合计 {total} 超过这笔回款的金额 {received}；"
            "多出来的部分没有来源，请先核对金额或减少分配",
            422,
        )
    # 目标期必须都属于这张单、且没被取消（不能把钱分到别的订单的期上）
    plan_ids = [item.plan_id for item in payload.allocations]
    plans: dict[int, ReceivablePlan] = {}
    if plan_ids:
        rows = (
            await session.execute(
                select(ReceivablePlan).where(ReceivablePlan.id.in_(plan_ids))
            )
        ).scalars().all()
        plans = {row.id: row for row in rows}
        for item in payload.allocations:
            plan = plans.get(item.plan_id)
            if plan is None:
                raise AppError(ErrorCode.NOT_FOUND, f"应收节点 {item.plan_id} 不存在", 404)
            if plan.order_id != record.order_id:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    f"应收节点 {item.plan_id} 不属于订单 {order.order_no if order else record.order_id}，"
                    "不能把回款分到别的订单上",
                    422,
                )
            if plan.status == "cancelled":
                raise AppError(
                    ErrorCode.PARAM_ERROR, f"应收节点「{plan.plan_name}」已取消，不能分配", 422
                )

    before = await svc.serialize_allocations(session, record.id)
    before_mode = record.allocation_mode
    record.allocation_mode = "manual"
    # 整份替换：删掉这笔回款的全部旧分配，按新的一份重建（都标 manual）
    await session.execute(
        delete(PaymentAllocation).where(PaymentAllocation.payment_id == record.id)
    )
    now = datetime.now(UTC)
    for item in payload.allocations:
        session.add(
            PaymentAllocation(
                payment_id=record.id,
                plan_id=item.plan_id,
                amount=item.amount,
                source="manual",
                created_by=user.id,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()
    after = await svc.serialize_allocations(session, record.id)

    # 重算受影响各期的状态（原来分到的期 + 现在分到的期，都可能变化）
    affected = {row["plan_id"] for row in before} | {row["plan_id"] for row in after}
    for plan_id in sorted(affected):
        plan = await svc.lock_plan(session, plan_id)
        if plan is not None:
            await svc.recalc_plan(session, plan)
    # 审计：原归属 / 新归属 / 金额 / 操作人 / 时间 都在 before/after 里
    await write_audit(
        session,
        operator_id=user.id,
        action="update_allocations",
        business_type="payment",
        business_id=record.id,
        before={"allocations": before, "allocation_mode": before_mode, "reason": None},
        after={"allocations": after, "allocation_mode": "manual", "reason": payload.reason},
        ip=client_ip(request),
    )
    if order is not None:
        from app.modules.order import milestones as milestones_svc

        await milestones_svc.sync_payment_milestones(session, order.id, operator_id=user.id)
    await session.commit()
    return ok(
        {"allocations": after, "reason": payload.reason},
        "回款分配已调整（原归属与新归属都已留审计）",
    )


@router.patch("/payments/{payment_id}")
async def update_payment(
    payment_id: int,
    payload: PaymentUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改回款登记（03-API §30）。

    只有待确认（pending）的回款能改。确认/驳回都是财务给出的事实结论，
    事后改金额会让已对账的账目对不上。

    取记录时带锁（`for_update=True`，内部按「订单 → 应收节点 → 回款记录」的顺序取锁）：
    改金额要重算节点状态，重算必须发生在节点锁内，否则与并发确认互相覆盖。
    """
    record = await svc.get_visible_payment(session, user, payment_id, for_update=True)
    if record.status != "pending":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"回款已{'确认' if record.status == 'confirmed' else '驳回'}，不能再修改",
        )
    # 节点行此时已在前面按统一锁序锁住；这里再取一次是同一事务内的同一把锁，
    # 不额外阻塞，但能让"重算必须在锁内"在调用点一眼可见。
    plan = (
        await svc.lock_plan(session, record.receivable_plan_id)
        if record.receivable_plan_id
        else None
    )
    before = await svc.serialize_payment(session, record)
    changes = payload.model_dump(exclude_unset=True)
    # 实收日期不能在未来（第四批审查 P1）：放在赋值之前，被拒时不留半改状态。
    # 这里判的是"改完之后的日期"—— 传了就用新值，没传就维持原值（原值也可能
    # 是修复前登记的异常未来日期，顺手也拦住）。
    if "received_date" in changes:
        svc.assert_received_date_not_future(changes["received_date"], action="修改回款")
    if "received_amount" in changes:
        record.received_amount = Decimal(str(changes["received_amount"]))
    if "received_date" in changes:
        record.received_date = changes["received_date"]
    if "payment_method" in changes:
        record.payment_method = changes["payment_method"]
    if "voucher_note" in changes:
        record.voucher_note = changes["voucher_note"]
    if "currency" in changes:
        # 币种不能随手改：它决定这笔钱能不能算进这个节点的已收金额。
        if plan is None:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                "这笔回款没有挂应收节点，无法核对币种；请先补挂节点再改币种",
                422,
            )
        record.currency = svc.resolve_payment_currency(plan, changes["currency"])
    await session.flush()
    # 金额变了要重算应收节点状态，否则收齐了还显示"部分回款"
    if plan is not None:
        await svc.recalc_plan(session, plan)
        await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="payment",
        business_id=record.id,
        before=before,
        after=await svc.serialize_payment(session, record),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_payment(session, record), "已保存")


@router.post("/payments")
async def create_payment(
    payload: PaymentCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage", "order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """登记一笔回款（03-API §30）。第七批 7.9 收敛三件事：

    1. **同一次录入有稳定请求键**：带 `request_key`（body 字段或 `X-Request-Key`
       头）时，同一把键反复提交只建一行并回放第一次的结果；键不同就各自建行 ——
       所以同额同日的两笔真实回款不会被"金额 + 日期相同"这种猜测合并掉。
       不带键仍然照旧登记，但响应里点明这次没有幂等保护。
    2. **币种继承应收节点**：给不同币种直接拒绝（跨币种核销口径未定，不猜汇率）。
    3. **先锁订单、再锁应收节点再插回款**（统一锁序的第一段）：与"删节点""确认回款"
       并发时不会出现"回款挂到已删节点上"或读旧汇总写覆盖。
    """
    if not payload.receivable_plan_id:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请指定这笔回款对应的应收节点")

    data = payload.model_dump()
    request_key = idempotency.request_key_from(request, data.pop("request_key", None))

    reservation = None
    if request_key:
        reservation = await idempotency.reserve(
            session,
            user_id=user.id,
            action="payment:create",
            request_key=request_key,
            payload=data,
            result_type="payment",
        )
        if reservation.should_replay:
            # 回放前重查**当前**可见性（2026-10-07 修）：订单可能已经换了负责人，
            # 这笔回款也就跟着不再属于你。原来直接返回缓存 —— 凭旧请求键照样读走。
            # 口径：**幂等保护的是"不重复登记"，不是"永久授权"**。
            if reservation.replay_id is not None:
                try:
                    await svc.get_visible_payment(session, user, reservation.replay_id)
                except AppError:
                    raise AppError(
                        ErrorCode.DATA_SCOPE_DENIED,
                        "这条记录已不在你的可见范围内（可能已移交或删除），无法回放原结果",
                        403,
                    ) from None
            return ok(
                reservation.replay_payload,
                "这笔回款此前已登记成功，已返回原记录（没有重复登记）",
            )

    try:
        plan = await svc.get_visible_plan(
            session, user, payload.receivable_plan_id, for_update=True
        )
        # 订单/节点已取消就不该再收钱（C5-01）：从前这里没有生命周期校验，
        # 实测订单与节点都已取消仍能登记回款 → 200 落库。
        await svc.assert_plan_accepts_payment(session, plan)
        # 实收日期不能在未来（第四批审查 P1）：放在建记录之前，被拒时不留半改状态
        svc.assert_received_date_not_future(payload.received_date)
        record = PaymentRecord(
            receivable_plan_id=plan.id,
            order_id=plan.order_id,
            received_date=payload.received_date,
            received_amount=Decimal(str(payload.received_amount)),
            # 不传币种时不能落到列默认的 CNY：那会把美元回款记成人民币。
            currency=svc.resolve_payment_currency(plan, payload.currency),
            payment_method=payload.payment_method,
            voucher_note=payload.voucher_note,
            status="pending",
            created_by=user.id,
            created_at=datetime.now(UTC),
        )
        session.add(record)
        await session.flush()
        await write_audit(
            session,
            operator_id=user.id,
            action="create",
            business_type="payment",
            business_id=record.id,
            after=await svc.serialize_payment(session, record),
            ip=client_ip(request),
        )
        body = await svc.serialize_payment(session, record)
        if reservation is not None:
            # 存进 request_keys 的响应体必须是 JSON 可序列化的：serialize_payment
            # 里有 date/datetime，直接塞 JSON 列会在 flush 时 TypeError（500）。
            await idempotency.complete(
                session,
                reservation,
                result_payload=json_safe(body),
                result_id=record.id,
            )
        await session.commit()
    except Exception:
        # 失败就释放占位：用户改完表单会带同一把键重试，内容必然不同，
        # 不释放会把"改错重填"误判成"同键不同内容"冲突。
        if reservation is not None:
            await idempotency.release(session, reservation)
        raise

    if request_key:
        return ok(body, "回款已登记，等待财务确认")
    return ok(body, "回款已登记，等待财务确认（本次未带请求键，弱网重试可能产生重复回款）")


@router.post("/payments/{payment_id}/confirm")
async def confirm_payment(
    payment_id: int,
    payload: PaymentAction,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """财务确认回款（03-API §30）。

    第七批 7.9：`get_visible_payment(..., for_update=True)` 内部按统一锁序
    先锁**共同应收节点**再锁回款记录。两个各 50 的并发确认锁的是两条不同的
    回款记录、锁不到彼此，只锁回款记录时双方都会读到"已确认 50"各写一次
    partial；先锁订单、再锁共同节点后，后到的那个在锁上等，等到时先到者已提交，
    重算才看得到 100 → 节点 paid。
    """
    record = await svc.get_visible_payment(session, user, payment_id, for_update=True, protect_transfer=True)
    svc.ensure_payment_pending(record)
    # 历史回款允许不挂应收节点，但它仍归属订单，不能绕过订单取消状态。
    # get_visible_payment 已锁订单并刷新，这里检查的是锁内最新状态。
    order = await session.get(SalesOrder, record.order_id)
    if order is not None and order.status == "cancelled":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "订单已取消，不能再确认回款", 422)
    # 订单/节点已取消就不该确认（C5-01）。放在这里而不是最后：被拒时**不能留下
    # 半改的状态**。`get_visible_payment` 已按统一锁序拿到订单与节点锁，这一步与它同序。
    # 没有节点（挂空）的回款不适用——那种本来就不参与节点重算。
    if record.receivable_plan_id:
        await svc.assert_plan_accepts_payment(
            session,
            await svc.lock_plan(session, record.receivable_plan_id),
            action="确认回款",
        )
    # 实收日期不能在未来（第四批审查 P1）：**锁内**再查一次，拦住修复前登记的
    # 存量异常记录 —— 只在登记处加校验挡不住它们。放在改状态之前。
    svc.assert_received_date_not_future(record.received_date, action="确认回款")
    record.status = "confirmed"
    record.confirmed_by = user.id
    record.confirmed_at = datetime.now(UTC)
    # ⚠️ **先把这笔钱分配到各期，再重算状态**（第十批主人 2026-10-13 拍板）。
    #
    # 顺序不能反：`recalc_plan` 现在读的是**分配额**（`confirmed_amount` 走
    # `payment_allocations`），先重算就会读到"这笔还没分配"的旧数，
    # 于是这一期该 `paid` 却停在 `partial`。
    #
    # 分配规则：人工分配优先（人定过的不动），剩下的按**到期日从早到晚**
    # 填各期空缺；全部填满后仍有余额就挂"待分配"，不硬塞给某一期。
    await svc.allocate_payment(session, record, operator_id=user.id)
    # 分配可能落到**多期**（超收抵后续期），所以这里重算本单**全部未取消**的期，
    # 不是只重算原来挂的那一期。
    for plan in (
        await session.execute(
            select(ReceivablePlan)
            .where(
                ReceivablePlan.order_id == record.order_id,
                ReceivablePlan.status != "cancelled",
            )
            .order_by(ReceivablePlan.id.asc())
        )
    ).scalars().all():
        await svc.recalc_plan(session, plan)
    if order:
        # 收款事实回流到跟单节点（issue #11）：「付定金」「收款」两个节点的实际日
        # 由**已确认收款**派生，不能人工直填。这一笔确认之后立刻同步，
        # 否则跟单页还停在"未完成"，与财务页对不上。
        from app.modules.order import milestones as milestones_svc

        await milestones_svc.sync_payment_milestones(session, order.id)
        from app.modules.followup.service import record_and_notify

        await record_and_notify(
            session, customer_id=order.customer_id, operator_id=user.id, owner_id=order.owner_id,
            title="财务确认回款",
            content=f"订单 {order.order_no} 的回款编号 {record.id} 已由财务确认，"
                    f"收款日期 {record.received_date}；金额与凭证请在原单查看",
            business_type="payment", business_id=record.id, order_id=order.id,
            event_key=f"payment:confirm:{record.id}",
        )
    if order and order.owner_id:
        await notification_service.notify(
            session,
            user_id=order.owner_id,
            type_="payment",
            title="回款已确认",
            content=f"订单 {order.order_no} 收到 ¥{record.received_amount:,.2f}",
            business_type="order",
            business_id=order.id,
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="confirm",
        business_type="payment",
        business_id=record.id,
        after={"comment": payload.comment},
        ip=client_ip(request),
    )
    # 先序列化取值，再 commit：commit 之后会话里的 ORM 对象会过期，
    # 那时再取属性可能触发额外的同步 IO（异步会话里会报 MissingGreenlet）。
    serialized = await svc.serialize_payment(session, record)
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(serialized, "财务已确认回款")


@router.post("/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: int,
    payload: PaymentAction,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """驳回回款。

    同样走带锁的取记录：驳回不重算节点，但要与同一笔/同一节点的并发确认串行，
    否则"一个确认一个驳回"会各自基于 pending 判断，两个都成功（终态被覆盖）。
    """
    record = await svc.get_visible_payment(session, user, payment_id, for_update=True)
    svc.ensure_payment_pending(record)
    record.status = "rejected"
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="reject",
        business_type="payment",
        business_id=record.id,
        before={"status": "pending"},
        after={"status": "rejected", "comment": payload.comment},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_payment(session, record), "已驳回该回款")


@router.get("/receivables/{plan_id}/payments")
async def plan_payments(
    plan_id: int,
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_plan(session, user, plan_id)
    effective = svc.effective_allocations()
    rows = (
        await session.execute(
            select(PaymentRecord)
            .where(or_(PaymentRecord.receivable_plan_id == plan_id,
                       PaymentRecord.id.in_(select(effective.c.payment_id).where(effective.c.plan_id == plan_id))))
            .order_by(PaymentRecord.received_date.asc())
        )
    ).scalars().all()
    items = []
    for row in rows:
        item = await svc.serialize_payment(session, row)
        amount = sum((Decimal(a["amount_exact"]) for a in item["allocations"] if a["plan_id"] == plan_id), Decimal(0))
        item["plan_allocated_amount"] = float(amount)
        item["plan_allocated_amount_exact"] = str(amount)
        items.append(item)
    return ok(items)


@router.get("/orders/{order_id}/finance-summary")
async def finance_summary(
    order_id: int,
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    await get_order_or_404(session, order_id)
    await svc.assert_order_visible(session, user, order_id)
    return ok(await svc.order_finance_summary(session, order_id))
