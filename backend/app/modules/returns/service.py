"""销售退货的业务实现。

把“申请”和“退款”分开：
1. 业务员申请：必须挂订单，填货款/运费/改价调整的金额构成和退款登记表；
2. 主管审核：只审数量、原因和金额构成，通过后进入“待确认收到退货”；
3. 确认收货：业务员或主管点收退回货物后进入“待财务退款”；
4. 财务确认退款：不改金额、不手填抵扣；系统按订单当前欠款自动先冲未付款，剩余才现金退；
   收款账户和打款方式由财务在这一步核对并填写；
5. 财务发现金额不对可打回；业务员或主管修改后重新交主管复审，
   货已收过的复审通过直接回到待财务退款；财务也可以作废终止；
6. 申请可取消；确认收货前/待财务退款时可作废；已退款/已抵扣结清后不能作废。

历史自动抵扣单仍保留原 `sales_return_plan_adjustments`，作废时按原快照恢复。
"""

from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.errors import AppError, ErrorCode
from app.core.timebase import today_business
from app.modules.customer.model import Customer
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.followup.service import record_and_notify
from app.modules.order.model import (
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.order.service import get_order_or_404
from app.modules.payment import service as payment_svc
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.returns.model import (
    RETURN_REASON_LABEL,
    RETURN_STATUS_LABEL,
    SalesReturn,
    SalesReturnItem,
    SalesReturnPlanAdjustment,
)
from app.modules.returns.schema import ReturnCreate
from app.modules.settings import numbering
from app.modules.user.model import User

ZERO = Decimal("0")
CENT = Decimal("0.01")
UNIT = Decimal("0.0001")
#: 参与“占用量”的退货状态。cancelled/voided 不占，允许重新登记。
ACTIVE_RETURN_STATUSES = (
    "reviewing",
    "pending",
    "receiving_pending",
    "completed",
    "finance_returned",
    "refunded",
    "settled",
)
#: 已对钱做过处理的退货状态。作废时要把这些单的冲减算进“已冲”分母。
SETTLED_RETURN_STATUSES = ("completed", "refunded", "settled")


def _money(value: Decimal | int | None) -> Decimal:
    return (Decimal(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _quantity(value: Decimal | int | None) -> Decimal:
    return Decimal(value or 0)


async def generate_return_no(session: AsyncSession) -> str:
    return await numbering.generate_for(
        session, "return", model=SalesReturn, column=SalesReturn.return_no
    )


async def get_visible_return(
    session: AsyncSession, user, return_id: int, *, for_update: bool = False
) -> SalesReturn:
    stmt = select(SalesReturn).where(SalesReturn.id == return_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "退货单不存在", 404)
    order = await get_order_or_404(session, row.order_id)
    await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
    return row


async def _active_quantities_by_order_item(
    session: AsyncSession, order_item_ids: list[int]
) -> dict[int, Decimal]:
    """已登记（未取消/未作废）的退货数量，按订单明细汇总。

    2026-11-26 起退货不再挂发货批次；历史批次行也按 `order_item_id` 归并，
    这样同一产品分批发货时，旧单和新单占用的可退数量一起计算，不会漏。
    """
    if not order_item_ids:
        return {}
    rows = (
        await session.execute(
            select(
                SalesReturnItem.order_item_id,
                func.coalesce(func.sum(SalesReturnItem.quantity), 0),
            )
            .join(SalesReturn, SalesReturn.id == SalesReturnItem.return_id)
            .where(
                SalesReturnItem.order_item_id.in_(order_item_ids),
                SalesReturn.status.in_(ACTIVE_RETURN_STATUSES),
            )
            .group_by(SalesReturnItem.order_item_id)
        )
    ).all()
    return {int(order_item_id): _quantity(qty) for order_item_id, qty in rows}


async def returnable_overview(session: AsyncSession, order_id: int) -> dict:
    """订单还能退什么：按订单明细（产品/规格）汇总，不再拆发货批次。

    已发数量取所有已实发批次的合计；已退数量取所有仍然生效的退货单合计。
    """
    rows = (
        await session.execute(
            select(
                OrderShipmentBatchItem.order_item_id,
                SalesOrderItem.sku_snapshot,
                OrderShipmentBatchItem.shipped_qty,
                SalesOrderItem.specification,
                SalesOrderItem.quantity,
                SalesOrderItem.amount,
                SalesOrderItem.unit_price,
            )
            .join(
                OrderShipmentBatch,
                OrderShipmentBatch.id == OrderShipmentBatchItem.batch_id,
            )
            .join(
                SalesOrderItem,
                SalesOrderItem.id == OrderShipmentBatchItem.order_item_id,
            )
            .where(
                OrderShipmentBatch.order_id == order_id,
                OrderShipmentBatch.status == "shipped",
            )
            .order_by(OrderShipmentBatchItem.order_item_id.asc())
        )
    ).all()

    items: dict[int, dict] = {}
    for (
        order_item_id,
        sku,
        shipped_qty,
        specification,
        order_qty,
        order_amount,
        order_unit_price,
    ) in rows:
        entry = items.setdefault(
            int(order_item_id),
            {
                "order_item_id": int(order_item_id),
                "sku": sku,
                "specification": specification,
                "shipped": ZERO,
                "order_qty": _quantity(order_qty),
                "order_amount": _money(order_amount),
                "order_unit_price": Decimal(order_unit_price or 0),
            },
        )
        entry["shipped"] += _quantity(shipped_qty)

    active_qty = await _active_quantities_by_order_item(
        session, list(items.keys())
    )
    total_shipped = total_returned = ZERO
    result_items = []
    for order_item_id, entry in items.items():
        shipped = entry["shipped"]
        returned = active_qty.get(order_item_id, ZERO)
        remaining = max(shipped - returned, ZERO)
        unit_price = (
            entry["order_amount"] / entry["order_qty"]
            if entry["order_qty"] > 0
            else entry["order_unit_price"]
        )
        result_items.append(
            {
                "order_item_id": order_item_id,
                "sku": entry["sku"],
                "specification": entry["specification"],
                "shipped_quantity": float(shipped),
                "shipped_quantity_exact": format(shipped, "f"),
                "returned_quantity": float(returned),
                "returned_quantity_exact": format(returned, "f"),
                "remaining_quantity": float(remaining),
                "remaining_quantity_exact": format(remaining, "f"),
                "unit_price": float(_money(unit_price)),
                "unit_price_exact": format(unit_price, ".4f"),
            }
        )
        total_shipped += shipped
        total_returned += returned
    return {
        "order_id": order_id,
        "items": result_items,
        "summary": {
            "shipped_quantity": float(total_shipped),
            "shipped_quantity_exact": format(total_shipped, "f"),
            "returned_quantity": float(total_returned),
            "returned_quantity_exact": format(total_returned, "f"),
            "remaining_quantity": float(max(total_shipped - total_returned, ZERO)),
            "remaining_quantity_exact": format(max(total_shipped - total_returned, ZERO), "f"),
        },
    }


async def create_return(
    session: AsyncSession,
    user,
    order: SalesOrder,
    payload: ReturnCreate,
    *,
    ip: str | None = None,
) -> SalesReturn:
    if (order.currency or "CNY") != "CNY":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "本项目只支持人民币订单，外币订单不能登记售后",
            422,
        )
    application_file = await session.get(
        FileRecord, payload.application_attachment_file_id
    )
    if application_file is None:
        raise AppError(
            ErrorCode.NOT_FOUND,
            "退款登记表附件不存在，请重新上传",
            404,
        )
    if application_file.uploaded_by != user.id:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "退款登记表只能用当前账号刚上传的附件",
            403,
        )
    # 只校验“文件存在 + 当前账号上传”还不够：文件中心的 file_id 是全局的，
    # 同一账号可能先给别的订单传过附件，再拿那个 id 来这边登记售后，等于把
    # 别的订单的退款登记表挂到这张订单上。这里必须再反查业务附件挂载：
    # 只有“挂在本订单、类别为 return_application”的文件才能作为本单登记表。
    application_link = (
        await session.execute(
            select(BusinessFile.id).where(
                BusinessFile.file_id == application_file.id,
                BusinessFile.business_type == "order",
                BusinessFile.business_id == order.id,
                BusinessFile.category == "return_application",
            )
        )
    ).first()
    if application_link is None:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "退款登记表附件没有挂到这张订单的售后申请附件，请重新上传",
            403,
        )
    ids = [item.order_item_id for item in payload.items]
    order_items = (
        await session.execute(
            select(SalesOrderItem).where(
                SalesOrderItem.id.in_(ids),
                SalesOrderItem.order_id == order.id,
            )
        )
    ).scalars().all()
    found = {int(order_item.id): order_item for order_item in order_items}
    missing = [oid for oid in ids if oid not in found]
    if missing:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"有 {len(missing)} 条订单明细不可退：只能退这张订单里的已发产品",
            422,
        )

    shipped_rows = (
        await session.execute(
            select(
                OrderShipmentBatchItem.order_item_id,
                func.coalesce(func.sum(OrderShipmentBatchItem.shipped_qty), 0),
            )
            .join(
                OrderShipmentBatch,
                OrderShipmentBatch.id == OrderShipmentBatchItem.batch_id,
            )
            .where(
                OrderShipmentBatch.order_id == order.id,
                OrderShipmentBatch.status == "shipped",
                OrderShipmentBatchItem.order_item_id.in_(ids),
            )
            .group_by(OrderShipmentBatchItem.order_item_id)
        )
    ).all()
    shipped_qty = {int(oid): _quantity(qty) for oid, qty in shipped_rows}
    active_qty = await _active_quantities_by_order_item(session, ids)

    result_items: list[SalesReturnItem] = []
    goods_amount = ZERO
    for request_item in payload.items:
        order_item = found[request_item.order_item_id]
        shipped = shipped_qty.get(int(order_item.id), ZERO)
        already_qty = active_qty.get(int(order_item.id), ZERO)
        remaining = max(shipped - already_qty, ZERO)
        qty = _quantity(request_item.quantity)
        if qty > remaining + UNIT:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"{order_item.sku_snapshot or order_item.id} 已发 {shipped}、"
                f"已退 {already_qty}，最多还能退 {remaining}，本次填了 {qty}",
                422,
            )
        order_qty = _quantity(order_item.quantity)
        if order_qty <= 0:
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "订单明细数量为 0，无法计算退货金额", 422)
        # 参考金额一律按“订单明细单价 × 本次数量”算，不再按批次倒算。
        unit_price = (Decimal(order_item.amount or 0) / order_qty).quantize(
            UNIT, rounding=ROUND_HALF_UP
        )
        amount = _money(qty * unit_price)
        if amount <= 0:
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "退货金额必须大于 0", 422)
        result_items.append(
            SalesReturnItem(
                order_item_id=int(order_item.id),
                # 新退货不再挂发货批次；历史字段保留为空。
                shipment_batch_item_id=None,
                batch_id=None,
                sku_snapshot=order_item.sku_snapshot,
                specification_snapshot=order_item.specification,
                quantity=qty,
                unit_price=(amount / qty).quantize(UNIT, rounding=ROUND_HALF_UP),
                amount=amount,
            )
        )
        goods_amount += amount

    total_refund = _money(payload.refund_amount)
    goods_refund = _money(payload.goods_refund_amount)
    freight_refund = _money(payload.freight_refund_amount)
    adjustment = _money(payload.adjustment_amount)

    return_row = SalesReturn(
        return_no=await generate_return_no(session),
        order_id=order.id,
        status="reviewing",
        reason_code=payload.reason_code,
        reason=payload.reason.strip(),
        return_date=payload.return_date or today_business(),
        currency="CNY",
        goods_amount=_money(goods_amount),
        # 业务员填的金额构成：货款 / 运费 / 改价或其他调整；总额由服务端合计。
        goods_refund_amount=goods_refund,
        settled_goods_amount=total_refund,
        freight_refund_amount=freight_refund,
        adjustment_amount=adjustment,
        adjustment_note=(payload.adjustment_note or "").strip() or None,
        total_amount=total_refund,
        # 收款信息归财务，申请阶段不填；财务确认退款时再写。
        payment_method="",
        receiving_account=None,
        application_attachment_file_id=payload.application_attachment_file_id,
        return_tracking_no=(payload.return_tracking_no or "").strip() or None,
        # 审核前不抵扣；财务确认退款时由系统自动先冲客户未付款。
        offset_amount=ZERO,
        refund_amount=total_refund,
        created_by=user.id,
        remark=(payload.remark or "").strip() or None,
    )
    session.add(return_row)
    await session.flush()
    for item in result_items:
        item.return_id = return_row.id
        session.add(item)
    await session.flush()

    await write_audit(
        session,
        operator_id=user.id,
        action="create_return",
        business_type="sales_return",
        business_id=return_row.id,
        after={
            "return_no": return_row.return_no,
            "order_no": order.order_no,
            "goods_amount": format(return_row.goods_amount, ".2f"),
            "goods_refund_amount": format(goods_refund, ".2f"),
            "freight_refund_amount": format(freight_refund, ".2f"),
            "adjustment_amount": format(adjustment, ".2f"),
            "adjustment_note": return_row.adjustment_note,
            "refund_amount": format(total_refund, ".2f"),
            "settled_goods_amount": format(total_refund, ".2f"),
            "payment_method": return_row.payment_method,
            "receiving_account": return_row.receiving_account,
            "application_attachment_file_id": return_row.application_attachment_file_id,
            "return_tracking_no": return_row.return_tracking_no,
            "items": [
                {
                    "order_item_id": item.order_item_id,
                    "quantity": format(item.quantity, "f"),
                    "amount": format(item.amount, ".2f"),
                }
                for item in result_items
            ],
        },
        ip=ip,
    )
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title="提交销售售后申请",
        content=(
            f"订单 {order.order_no} 提交售后申请 {return_row.return_no}，"
            f"退款总额 {format(total_refund, '.2f')} 元"
            f"（货款 {format(goods_refund, '.2f')}、运费 {format(freight_refund, '.2f')}、"
            f"改价/其他 {format(adjustment, '.2f')}），"
            f"原因：{payload.reason.strip()}；待主管审核。"
        ),
        business_type="sales_return",
        business_id=return_row.id,
        order_id=order.id,
        event_key=f"order:return:{return_row.id}",
    )
    await session.flush()
    return return_row


async def review_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    *,
    decision: str,
    note: str | None,
    ip: str | None = None,
) -> SalesReturn:
    """主管审核：通过后进入“待确认收到退货”，驳回则释放占用的可退数量。

    2026-11-26 主人按 OA 表单拍板：申请时已填退款金额和登记表附件，
    主管只审核；审核通过后由业务员或主管确认货已退回，再交财务退款。
    历史旧单没有金额的，仍落到 `pending`，由业务员补填后再处理。

    权限在路由层用 `order:assign`（主管级）控制，服务层再校验一次：
    只有主管/管理员能审核；业务员、财务没有该权限，不能审核。
    主管可以审核自己登记的单（小团队只有一个主管时不会卡死），
    但审计会记录 `self_reviewed=True`，界面也会标“本人审核”，留痕可查。
    """
    if not user.has("order:assign"):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "只有主管或管理员能审核售后申请，业务员和财务不能审核",
            403,
        )
    if row.status != "reviewing":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的售后申请不在待审核状态",
            422,
        )
    if decision == "reject" and row.received_at is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "这张售后单已经确认收到退货，不能直接驳回；"
            "金额有误请让申请人修改后重新审核，确实不做这笔退款请财务作废",
            422,
        )
    before = row.status
    row.reviewed_by = user.id
    row.reviewed_at = datetime.now(UTC)
    row.review_note = (note or "").strip() or None
    if decision == "approve":
        # 申请时已填金额的新单进入待确认收货；兼容没有金额的历史旧单。
        # 财务打回后货已经收过，复审通过直接回到待财务退款，不再重复点收。
        if row.settled_goods_amount is None:
            row.status = "pending"
        elif row.received_at is not None:
            row.status = "completed"
        else:
            row.status = "receiving_pending"
        title = "销售售后申请已通过主管审核"
        if row.status == "completed":
            content = (
                f"订单售后申请 {row.return_no} 已通过主管复审，"
                f"退款总额 {format(row.total_amount or ZERO, '.2f')} 元；"
                "货已确认收到，待财务退款。"
            )
        elif row.status == "receiving_pending":
            content = (
                f"订单售后申请 {row.return_no} 已通过主管审核，"
                f"退款总额 {format(row.total_amount or ZERO, '.2f')} 元，待确认收到退货；"
                "业务员或主管确认收到货后由财务退款。"
            )
        else:
            content = (
                f"订单售后申请 {row.return_no} 已通过主管审核，待填退款总额。"
            )
    else:
        row.status = "rejected"
        title = "销售售后申请被主管驳回"
        content = (
            f"订单售后申请 {row.return_no} 被主管驳回，原因："
            f"{row.review_note or '未填写'}；可退数量已释放。"
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="review_return",
        business_type="sales_return",
        business_id=row.id,
        before={"status": before},
        after={
            "status": row.status,
            "decision": decision,
            "note": row.review_note,
            "refund_amount": format(row.total_amount or ZERO, ".2f"),
            "self_reviewed": row.created_by == user.id,
        },
        ip=ip,
    )
    order = await get_order_or_404(session, row.order_id)
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title=title,
        content=content,
        business_type="sales_return",
        business_id=row.id,
        order_id=order.id,
        event_key=f"order:return:review:{row.id}",
    )
    await session.flush()
    return row


async def confirm_received(
    session: AsyncSession,
    user,
    row: SalesReturn,
    *,
    note: str | None = None,
    tracking_no: str | None = None,
    ip: str | None = None,
) -> SalesReturn:
    """业务员或主管确认收到退货：之后才允许财务退款。

    系统里没有单独的收货岗位；谁实际收到客户退回的货，谁点确认。
    确认记录操作人、时间和备注，审计可查。
    """
    # 财务也有 order:manage，不能只看权限码；按实际岗位放行业务员/主管。
    if not (
        "admin" in user.roles
        or "salesperson" in user.roles
        or user.has("order:assign")
    ):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "只有业务员或主管能确认收到退货",
            403,
        )
    if row.status != "receiving_pending":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的售后申请不能确认收货",
            422,
        )
    before = row.status
    row.received_by = user.id
    row.received_at = datetime.now(UTC)
    row.receiving_note = (note or "").strip() or None
    if tracking_no and tracking_no.strip():
        row.return_tracking_no = tracking_no.strip()
    row.status = "completed"
    await write_audit(
        session,
        operator_id=user.id,
        action="confirm_return_received",
        business_type="sales_return",
        business_id=row.id,
        before={"status": before},
        after={
            "status": row.status,
            "receiving_note": row.receiving_note,
            "return_tracking_no": row.return_tracking_no,
        },
        ip=ip,
    )
    order = await get_order_or_404(session, row.order_id)
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title="销售售后已确认收到退货",
        content=(
            f"订单售后申请 {row.return_no} 已确认收到退货，"
            f"退款总额 {format(row.total_amount or ZERO, '.2f')} 元，待财务退款。"
        ),
        business_type="sales_return",
        business_id=row.id,
        order_id=order.id,
        event_key=f"order:return:receive:{row.id}",
    )
    await session.flush()
    return row


async def finance_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    reason: str,
    *,
    ip: str | None = None,
) -> SalesReturn:
    """财务打回：只退回修改，不替业务员改金额。

    财务发现金额不对时，写清原因把单退回业务员/主管；
    原单、收货记录和审计都保留。修改后重新走主管审核，通过后直接回到
    “待财务退款”，不再重复确认收货。收款信息仍由财务在确认退款时填写。
    """
    if not user.has("payment:manage"):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "只有财务可以打回售后单（需要 payment:manage 权限）",
            403,
        )
    if row.status != "completed":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的售后单不能打回；"
            "只有待财务退款的新售后单可以退回修改",
            422,
        )
    if row.currency != "CNY":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "本项目只支持人民币售后", 422)
    # 历史抵扣单的金额和应收快照绑定，打回改金额会破坏还原口径；
    # 这类单只能按原金额登记现金退款，或走作废。
    adjustment_count = (
        await session.execute(
            select(func.count())
            .select_from(SalesReturnPlanAdjustment)
            .where(SalesReturnPlanAdjustment.return_id == row.id)
        )
    ).scalar_one()
    if _money(row.offset_amount) > 0 or adjustment_count:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "这是历史“抵扣客户未付款”的售后单，不能打回改金额；"
            "请按原金额登记现金退款，或先作废后重新登记",
            409,
        )
    reason_text = (reason or "").strip()
    if len(reason_text) < 2:
        raise AppError(ErrorCode.PARAM_ERROR, "财务打回必须填写原因（至少 2 个字）", 422)

    before = {
        "status": row.status,
        "total_amount": format(row.total_amount or ZERO, ".2f"),
        "finance_return_count": row.finance_return_count or 0,
    }
    row.status = "finance_returned"
    row.finance_return_reason = reason_text
    row.finance_returned_by = user.id
    row.finance_returned_at = datetime.now(UTC)
    row.finance_return_count = (row.finance_return_count or 0) + 1
    # 财务还没实际付款；清掉可能残留的结算痕迹，金额仍以审核后的原数展示。
    row.settled_by = None
    row.settled_at = None
    row.refund_reference = None

    await write_audit(
        session,
        operator_id=user.id,
        action="finance_return",
        business_type="sales_return",
        business_id=row.id,
        before=before,
        after={
            "status": row.status,
            "reason": row.finance_return_reason,
            "finance_return_count": row.finance_return_count,
            "total_amount": format(row.total_amount or ZERO, ".2f"),
        },
        ip=ip,
    )
    order = await get_order_or_404(session, row.order_id)
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title="财务退回售后申请修改",
        content=(
            f"订单售后申请 {row.return_no} 被财务退回修改，原因：{reason_text}；"
            f"退款总额 {format(row.total_amount or ZERO, '.2f')} 元，"
            "请业务员或主管修改后重新提交审核。"
        ),
        business_type="sales_return",
        business_id=row.id,
        order_id=order.id,
        event_key=f"order:return:finance-return:{row.id}:{row.finance_return_count}",
    )
    await session.flush()
    return row


async def revise_return_amount(
    session: AsyncSession,
    user,
    row: SalesReturn,
    *,
    goods_refund_amount: Decimal,
    freight_refund_amount: Decimal,
    adjustment_amount: Decimal,
    adjustment_note: str | None = None,
    note: str | None = None,
    ip: str | None = None,
) -> SalesReturn:
    """财务打回后，由申请人或主管只修改退款金额，再交主管复审。

    财务没有改金额权限；这里也不允许财务调用。修改后状态回到 `reviewing`，
    货已收过的单据复审通过会直接回到 `completed`，不重复点收。
    收款账户和打款方式归财务，在最终确认退款时填写，这里不接收也不改动。
    """
    if not (
        row.created_by == user.id
        or user.has("order:assign")
        or "admin" in user.roles
    ):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "只有申请人本人或主管能修改被财务退回的售后单",
            403,
        )
    if row.status != "finance_returned":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的售后单不能修改金额；"
            "只有财务退回修改的单可以改",
            422,
        )
    if row.currency != "CNY":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "本项目只支持人民币售后", 422)

    goods = _money(goods_refund_amount)
    freight = _money(freight_refund_amount)
    adjustment = _money(adjustment_amount)
    if goods < 0 or freight < 0:
        raise AppError(ErrorCode.PARAM_ERROR, "货款和运费退款不能小于 0", 422)
    total = _money(goods + freight + adjustment)
    if total <= 0:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "退款总额必须大于 0", 422)

    before = {
        "status": row.status,
        "goods_refund_amount": format(row.goods_refund_amount or ZERO, ".2f"),
        "freight_refund_amount": format(row.freight_refund_amount or ZERO, ".2f"),
        "adjustment_amount": format(row.adjustment_amount or ZERO, ".2f"),
        "total_amount": format(row.total_amount or ZERO, ".2f"),
    }
    row.goods_refund_amount = goods
    row.freight_refund_amount = freight
    row.adjustment_amount = adjustment
    row.adjustment_note = (adjustment_note or "").strip() or None
    row.settled_goods_amount = total
    row.total_amount = total
    row.refund_amount = total
    row.offset_amount = ZERO
    # 收款账户和打款方式不在这一步改；财务确认退款时自己填。
    # 金额改了，原来的主管审核结论不再有效，必须重新审。
    row.status = "reviewing"
    row.reviewed_by = None
    row.reviewed_at = None
    row.review_note = None
    row.settled_by = None
    row.settled_at = None
    row.refund_reference = None

    await write_audit(
        session,
        operator_id=user.id,
        action="revise_return_amount",
        business_type="sales_return",
        business_id=row.id,
        before=before,
        after={
            "status": row.status,
            "goods_refund_amount": format(goods, ".2f"),
            "freight_refund_amount": format(freight, ".2f"),
            "adjustment_amount": format(adjustment, ".2f"),
            "adjustment_note": row.adjustment_note,
            "total_amount": format(total, ".2f"),
            "note": (note or "").strip() or None,
            "finance_return_reason": row.finance_return_reason,
        },
        ip=ip,
    )
    order = await get_order_or_404(session, row.order_id)
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title="售后退款金额已修改，待主管复审",
        content=(
            f"订单售后申请 {row.return_no} 已按财务退回意见修改，"
            f"退款总额 {format(total, '.2f')} 元"
            f"（货款 {format(goods, '.2f')}、运费 {format(freight, '.2f')}、"
            f"调整 {format(adjustment, '.2f')}），待主管复审。"
        ),
        business_type="sales_return",
        business_id=row.id,
        order_id=order.id,
        event_key=f"order:return:revise:{row.id}:{row.finance_return_count or 0}",
    )
    await session.flush()
    return row


async def cancel_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    reason: str,
    *,
    ip: str | None = None,
) -> SalesReturn:
    if row.received_at is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "这张售后单已经确认收到退货，不能取消；请让财务作废，或修改金额后重新审核",
            422,
        )
    if row.status not in ("reviewing", "pending"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的退货单不能取消；"
            "已确认退款总额的请走「作废退货」",
            422,
        )
    before = row.status
    row.status = "cancelled"
    row.cancelled_by = user.id
    row.cancelled_at = datetime.now(UTC)
    row.cancel_reason = reason.strip()
    await write_audit(
        session,
        operator_id=user.id,
        action="cancel_return",
        business_type="sales_return",
        business_id=row.id,
        before={"status": before},
        after={"status": row.status, "reason": row.cancel_reason},
        ip=ip,
    )
    await session.flush()
    return row


async def update_return_amount(
    session: AsyncSession,
    user,
    row: SalesReturn,
    *,
    refund_amount: Decimal,
    note: str | None = None,
    ip: str | None = None,
) -> SalesReturn:
    """只给历史 `pending` 旧单补金额；新售后单不允许任何人改金额。

    2026-11-26 新流程：业务员登记时填金额构成，主管审核；财务只按审核后的
    总额退款，不改金额。金额有误只能驳回重提，避免财务把申请单改成另一笔账。
    """
    if not user.has("payment:manage"):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "只有财务可以处理历史旧单的退款金额（需要 payment:manage 权限）",
            403,
        )
    if row.status != "pending":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的售后单不能改金额；"
            "新流程的金额由业务员登记、主管审核，财务不改金额",
            422,
        )
    if row.currency != "CNY":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "本项目只支持人民币售后", 422)

    # 历史抵扣单的应收计划已经被冲减过；它的金额和 plan 快照绑定，
    # 不能像新单一样只改一个数字。要改只能先作废、恢复应收，再重新登记。
    adjustment_count = (
        await session.execute(
            select(func.count())
            .select_from(SalesReturnPlanAdjustment)
            .where(SalesReturnPlanAdjustment.return_id == row.id)
        )
    ).scalar_one()
    if _money(row.offset_amount) > 0 or adjustment_count:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "这是历史“抵扣客户未付款”的售后单，不能直接改金额；请先作废并重新登记",
            409,
        )

    amount = _money(refund_amount)
    if amount <= 0:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "退款总额必须大于 0", 422)
    before = {
        "status": row.status,
        "settled_goods_amount": format(row.settled_goods_amount or ZERO, ".2f"),
        "refund_amount": format(row.refund_amount or ZERO, ".2f"),
        "total_amount": format(row.total_amount or ZERO, ".2f"),
    }
    row.goods_refund_amount = amount
    row.settled_goods_amount = amount
    row.total_amount = amount
    row.freight_refund_amount = ZERO
    row.adjustment_amount = ZERO
    row.offset_amount = ZERO
    row.refund_amount = amount
    row.settled_by = user.id
    row.settled_at = datetime.now(UTC)
    if note is not None:
        row.settlement_note = note.strip() or None
    # 历史 pending 旧单当年没有确认收货环节；补金额后直接进入待财务退款。
    row.status = "completed"

    await write_audit(
        session,
        operator_id=user.id,
        action="update_return_amount",
        business_type="sales_return",
        business_id=row.id,
        before=before,
        after={
            "status": row.status,
            "settled_goods_amount": format(row.settled_goods_amount, ".2f"),
            "refund_amount": format(row.refund_amount, ".2f"),
            "total_amount": format(row.total_amount, ".2f"),
            "offset_amount": "0.00",
            "note": row.settlement_note,
        },
        ip=ip,
    )
    await session.flush()
    return row


async def refund_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    reference: str,
    *,
    ip: str | None = None,
) -> SalesReturn:
    if row.status != "completed":
        if row.status == "settled":
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                "这张退货单已经用抵扣结清，没有现金退款可登记",
                422,
            )
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的退货单不能登记退款",
            422,
        )
    if _money(row.offset_amount) <= 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "新流程的售后退款请走「财务确认退款」，由系统自动先冲客户未付款；"
            "旧接口只保留给历史已抵扣的售后单补登记现金退款",
            422,
        )
    if row.refund_amount <= 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "这张退货单没有需要退给客户的钱，不用登记退款", 422
        )
    row.status = "refunded"
    row.refunded_by = user.id
    row.refunded_at = datetime.now(UTC)
    row.refund_reference = reference.strip()
    await write_audit(
        session,
        operator_id=user.id,
        action="refund_return",
        business_type="sales_return",
        business_id=row.id,
        before={"status": "completed", "refund_amount": format(row.refund_amount, ".2f")},
        after={
            "status": row.status,
            "refund_reference": row.refund_reference,
            "refund_attachment_file_id": row.refund_attachment_file_id,
        },
        ip=ip,
    )
    await session.flush()
    return row


async def settle_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    *,
    refund_reference: str | None = None,
    payment_method: str | None = None,
    receiving_account: str | None = None,
    note: str | None = None,
    ip: str | None = None,
) -> SalesReturn:
    """财务确认退款：系统自动先冲客户未付款，剩余才现金退。

    2026-11-26 按 OA 表单拍板：
    - 财务不改金额，也不手填抵扣；只确认“这笔退款已经按申请金额处理”；
    - 系统先按订单当前欠款自动抵扣，抵不完的部分才现金退；
    - 有现金退还时，财务必须填退款流水/凭证说明、打款方式和收款账户；
    - 抵扣逐期写快照，作废时能精确还原。
    """
    if not user.has("payment:manage"):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "只有财务可以确认退款（需要 payment:manage 权限）",
            403,
        )
    if row.status != "completed":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的售后单不能退款",
            422,
        )
    if row.currency != "CNY":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "本项目只支持人民币售后", 422
        )

    adjustment_count = (
        await session.execute(
            select(func.count())
            .select_from(SalesReturnPlanAdjustment)
            .where(SalesReturnPlanAdjustment.return_id == row.id)
        )
    ).scalar_one()
    if _money(row.offset_amount) > 0 or adjustment_count:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "这是历史“抵扣客户未付款”的售后单，不能重新退款；"
            "请按原金额登记现金退款，或先作废后重新登记",
            409,
        )

    # 退款总额只认业务员登记、主管审核后的数；财务不能在这里改。
    total = _money(row.total_amount)
    if total <= 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "退款总额必须大于 0", 422
        )
    reference = (refund_reference or "").strip() or None
    # 收款信息由财务在确认退款时填；申请阶段不再接收业务员填的账号。
    payment_method_text = (payment_method or "").strip()
    receiving_account_text = (receiving_account or "").strip()

    order = await get_order_or_404(session, row.order_id)
    plans = (
        await session.execute(
            select(ReceivablePlan)
            .where(
                ReceivablePlan.order_id == row.order_id,
                ReceivablePlan.status != "cancelled",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    balances = await payment_svc.confirmed_amounts(session, [p.id for p in plans])
    plan_outstanding = sum(
        (max(_money(plan.amount) - balances.get(plan.id, ZERO), ZERO) for plan in plans),
        ZERO,
    )
    received_total = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.order_id == row.order_id,
                PaymentRecord.status == "confirmed",
            )
        )
    ).scalar_one()
    prior_offsets = (
        await session.execute(
            select(func.coalesce(func.sum(SalesReturn.offset_amount), 0)).where(
                SalesReturn.order_id == row.order_id,
                SalesReturn.status.in_(SETTLED_RETURN_STATUSES),
            )
        )
    ).scalar_one()
    unreceived = _money(
        _money(order.total_amount) - _money(prior_offsets) - _money(received_total)
    )
    max_offset = min(max(unreceived, ZERO), max(plan_outstanding, ZERO))
    # 系统自动先冲客户未付款，最多冲到本次退款总额；剩余才现金退。
    offset = min(total, max_offset)
    cash = _money(total - offset)
    if cash > 0 and (not reference or len(reference) < 2):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "有现金退还时必须填写退款流水/凭证说明（至少 2 个字）",
            422,
        )
    if cash > 0 and (not payment_method_text or len(payment_method_text) < 2):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "有现金退还时必须填写打款方式（至少 2 个字）",
            422,
        )
    if cash > 0 and (
        not receiving_account_text or len(receiving_account_text) < 2
    ):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "有现金退还时必须填写收款账户（至少 2 个字）",
            422,
        )
    if cash == 0:
        reference = None

    adjustments: list[SalesReturnPlanAdjustment] = []
    remaining = offset
    for plan in sorted(plans, key=lambda p: (p.due_date is None, p.due_date, p.id)):
        if remaining <= 0:
            break
        balance = max(_money(plan.amount) - balances.get(plan.id, ZERO), ZERO)
        if balance <= 0:
            continue
        applied = min(balance, remaining)
        if applied <= 0:
            continue
        before_amount = _money(plan.amount)
        after_amount = _money(before_amount - applied)
        adjustment = SalesReturnPlanAdjustment(
            return_id=row.id,
            plan_id=plan.id,
            amount_before=before_amount,
            applied_amount=applied,
            amount_after=after_amount,
            status_before=plan.status,
        )
        session.add(adjustment)
        adjustments.append(adjustment)
        plan.amount = after_amount
        await payment_svc.recalc_plan(session, plan)
        remaining -= applied

    if remaining != 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "抵扣金额没有完全分配到应收计划，请刷新后重试",
            409,
        )

    before_total = _money(row.settled_goods_amount or row.total_amount)
    before_offset = _money(row.offset_amount)
    before_refund = _money(row.refund_amount)
    row.settled_goods_amount = total
    row.total_amount = total
    row.offset_amount = offset
    row.refund_amount = cash
    row.settled_by = user.id
    row.settled_at = datetime.now(UTC)
    # 财务确认退款时写入实际打款方式和收款账户；无现金退还时保留原值。
    if payment_method_text:
        row.payment_method = payment_method_text
    if receiving_account_text:
        row.receiving_account = receiving_account_text
    if note is not None:
        row.settlement_note = note.strip() or None
    if cash > 0:
        row.status = "refunded"
        row.refunded_by = user.id
        row.refunded_at = datetime.now(UTC)
        row.refund_reference = reference
    else:
        row.status = "settled"
        row.refund_reference = None
    await session.flush()

    from app.modules.order import milestones as milestones_svc

    await milestones_svc.sync_payment_milestones(session, order.id, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="settle_return",
        business_type="sales_return",
        business_id=row.id,
        before={
            "status": "completed",
            "total_amount": format(before_total, ".2f"),
            "offset_amount": format(before_offset, ".2f"),
            "refund_amount": format(before_refund, ".2f"),
            "payment_method": row.payment_method,
            "receiving_account": row.receiving_account,
        },
        after={
            "status": row.status,
            "total_amount": format(total, ".2f"),
            "offset_amount": format(offset, ".2f"),
            "refund_amount": format(cash, ".2f"),
            "refund_reference": row.refund_reference,
            "payment_method": row.payment_method,
            "receiving_account": row.receiving_account,
            "note": row.settlement_note,
            "plans": [
                {
                    "plan_id": adjustment.plan_id,
                    "amount_before": format(adjustment.amount_before, ".2f"),
                    "applied_amount": format(adjustment.applied_amount, ".2f"),
                    "amount_after": format(adjustment.amount_after, ".2f"),
                }
                for adjustment in adjustments
            ],
        },
        ip=ip,
    )
    if offset > 0 and cash > 0:
        settle_text = (
            f"抵扣客户未付款 {format(offset, '.2f')} 元，"
            f"现金退还 {format(cash, '.2f')} 元。"
        )
    elif offset > 0:
        settle_text = f"抵扣客户未付款 {format(offset, '.2f')} 元，无现金退还。"
    else:
        settle_text = f"现金退还 {format(cash, '.2f')} 元。"
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title="销售售后已退款",
        content=(
            f"订单 {order.order_no} 售后申请 {row.return_no} 已处理，"
            f"退款总额 {format(total, '.2f')} 元：{settle_text}"
        ),
        business_type="sales_return",
        business_id=row.id,
        order_id=order.id,
        event_key=f"order:return:settle:{row.id}",
    )
    await session.flush()
    return row


async def void_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    reason: str,
    *,
    ip: str | None = None,
) -> SalesReturn:
    # 财务打回后业务员已改金额、还没复审时，财务仍可作废终止；
    # 否则一直挂在“待主管审核”会没有出口。
    post_finance_revision = (
        row.status == "reviewing"
        and row.received_at is not None
        and (row.finance_return_count or 0) > 0
    )
    if row.status not in ("receiving_pending", "completed", "finance_returned") and not post_finance_revision:
        if row.status == "refunded":
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                "这笔售后已经实际退款，不能直接作废；请线下处理并保留退款凭证",
                422,
            )
        if row.status == "settled":
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                "这笔售后已经用抵扣客户未付款结清，不能直接作废",
                422,
            )
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的售后单不能作废",
            422,
        )
    order = await get_order_or_404(session, row.order_id)
    before_status = row.status
    adjustments = (
        await session.execute(
            select(SalesReturnPlanAdjustment)
            .where(SalesReturnPlanAdjustment.return_id == row.id)
            .order_by(SalesReturnPlanAdjustment.plan_id.asc())
            .with_for_update()
        )
    ).scalars().all()
    for adjustment in adjustments:
        plan = (
            await session.execute(
                select(ReceivablePlan)
                .where(ReceivablePlan.id == adjustment.plan_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if plan is None:
            raise AppError(ErrorCode.NOT_FOUND, "退货关联的应收期已不存在，不能作废", 409)
        # 这一期可能被多张退货单先后冲过：不能只拿本单的 amount_after 对比，
        # 否则“后一张退货”会被误判成“手工改过”，前面的退货永远作废不了。
        # 用这一期的原始金额 - 所有仍生效退货的冲减额，得到“现在应该是多少”。
        plan_adjustments = (
            await session.execute(
                select(SalesReturnPlanAdjustment)
                .where(SalesReturnPlanAdjustment.plan_id == adjustment.plan_id)
                .order_by(SalesReturnPlanAdjustment.id.asc())
                .with_for_update()
            )
        ).scalars().all()
        if not plan_adjustments:
            raise AppError(ErrorCode.NOT_FOUND, "退货关联的应收调整不存在，不能作废", 409)
        original_amount = _money(plan_adjustments[0].amount_before)
        active_applied = _money(
            (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(SalesReturnPlanAdjustment.applied_amount), 0
                        )
                    )
                    .join(
                        SalesReturn,
                        SalesReturn.id == SalesReturnPlanAdjustment.return_id,
                    )
                    .where(
                        SalesReturnPlanAdjustment.plan_id == adjustment.plan_id,
                        SalesReturn.status.in_(SETTLED_RETURN_STATUSES),
                    )
                )
            ).scalar_one()
        )
        expected_amount = _money(original_amount - active_applied)
        if _money(plan.amount or 0) != expected_amount:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"第 {adjustment.plan_id} 期客户欠款和退货记录对不上"
                f"（现在 {plan.amount}，按记录应为 {expected_amount}），"
                "可能被手工改过或还有别的退货未处理；请先核对，再线下处理",
                409,
            )
        # 把本单的冲减加回去；如果后面还有别的退货，它们冲减的部分保留。
        restored_amount = _money((plan.amount or ZERO) + adjustment.applied_amount)
        plan.amount = restored_amount
        plan.status = adjustment.status_before
        if plan.status == "cancelled" and restored_amount > 0:
            plan.status = "pending"
        if plan.status != "cancelled":
            await payment_svc.recalc_plan(session, plan)
    row.status = "voided"
    row.voided_by = user.id
    row.voided_at = datetime.now(UTC)
    row.void_reason = reason.strip()
    await session.flush()
    from app.modules.order import milestones as milestones_svc

    await milestones_svc.sync_payment_milestones(session, order.id, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="void_return",
        business_type="sales_return",
        business_id=row.id,
        before={
            "status": before_status,
            "refund_amount": format(row.refund_amount or ZERO, ".2f"),
            "offset_amount": format(row.offset_amount or ZERO, ".2f"),
        },
        after={
            "status": row.status,
            "reason": row.void_reason,
            "restored_plans": [
                {
                    "plan_id": adjustment.plan_id,
                    "amount_before": format(adjustment.amount_before, ".2f"),
                    "amount_after": format(adjustment.amount_after, ".2f"),
                }
                for adjustment in adjustments
            ],
        },
        ip=ip,
    )
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title="销售售后已作废",
        content=(
            f"订单 {order.order_no} 售后申请 {row.return_no} 已作废，"
            f"原因：{row.void_reason}；"
            + (
                "原抵扣的客户未付款已恢复。"
                if _money(row.offset_amount) > 0
                else "退款金额不再生效，可退数量已释放。"
            )
        ),
        business_type="sales_return",
        business_id=row.id,
        order_id=order.id,
        event_key=f"order:return:void:{row.id}",
    )
    await session.flush()
    return row


async def _user_names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
    return {int(uid): name for uid, name in rows}


async def serialize_return(session: AsyncSession, row: SalesReturn) -> dict:
    order = await get_order_or_404(session, row.order_id)
    customer = await session.get(Customer, order.customer_id)
    items = (
        await session.execute(
            select(SalesReturnItem)
            .where(SalesReturnItem.return_id == row.id)
            .order_by(SalesReturnItem.id.asc())
        )
    ).scalars().all()
    names = await _user_names(
        session,
        [
            row.created_by,
            row.reviewed_by,
            row.finance_returned_by,
            row.received_by,
            row.settled_by,
            row.refunded_by,
            row.voided_by,
            row.cancelled_by,
        ],
    )
    application_attachment = (
        await session.get(FileRecord, row.application_attachment_file_id)
        if row.application_attachment_file_id
        else None
    )
    refund_attachment = (
        await session.get(FileRecord, row.refund_attachment_file_id)
        if row.refund_attachment_file_id
        else None
    )
    return {
        "id": row.id,
        "return_no": row.return_no,
        "order_id": row.order_id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": customer.name if customer else None,
        "applicant_name": names.get(row.created_by or 0),
        "application_date": row.return_date.isoformat(),
        "status": row.status,
        "status_label": RETURN_STATUS_LABEL.get(row.status, row.status),
        "reason_code": row.reason_code,
        "reason_label": RETURN_REASON_LABEL.get(row.reason_code, row.reason_code),
        "reason": row.reason,
        "return_date": row.return_date.isoformat(),
        "currency": row.currency,
        "goods_amount": float(row.goods_amount or 0),
        "goods_amount_exact": format(row.goods_amount or 0, ".2f"),
        "goods_refund_amount": float(row.goods_refund_amount or 0),
        "goods_refund_amount_exact": format(row.goods_refund_amount or 0, ".2f"),
        "settled_goods_amount": float(row.settled_goods_amount or 0) if row.settled_goods_amount is not None else None,
        "settled_goods_amount_exact": format(row.settled_goods_amount, ".2f") if row.settled_goods_amount is not None else None,
        "freight_refund_amount": float(row.freight_refund_amount or 0),
        "freight_refund_amount_exact": format(row.freight_refund_amount or 0, ".2f"),
        "adjustment_amount": float(row.adjustment_amount or 0),
        "adjustment_amount_exact": format(row.adjustment_amount or 0, ".2f"),
        "adjustment_note": row.adjustment_note,
        "total_amount": float(row.total_amount or 0),
        "total_amount_exact": format(row.total_amount or 0, ".2f"),
        "payment_method": row.payment_method,
        "receiving_account": row.receiving_account,
        "application_attachment_file_id": row.application_attachment_file_id,
        "application_attachment_file_name": application_attachment.file_name if application_attachment else None,
        "application_attachment_file_size": application_attachment.size if application_attachment else None,
        "application_attachment_mime_type": application_attachment.mime_type if application_attachment else None,
        "return_tracking_no": row.return_tracking_no,
        "offset_amount": float(row.offset_amount or 0),
        "offset_amount_exact": format(row.offset_amount or 0, ".2f"),
        "refund_amount": float(row.refund_amount or 0),
        "refund_amount_exact": format(row.refund_amount or 0, ".2f"),
        "refund_reference": row.refund_reference,
        "refund_attachment_file_id": row.refund_attachment_file_id,
        "refund_attachment_file_name": refund_attachment.file_name if refund_attachment else None,
        "refund_attachment_file_size": refund_attachment.size if refund_attachment else None,
        "refund_attachment_mime_type": refund_attachment.mime_type if refund_attachment else None,
        "settlement_note": row.settlement_note,
        "remark": row.remark,
        "created_by": row.created_by,
        "created_by_name": names.get(row.created_by or 0),
        "reviewed_by_name": names.get(row.reviewed_by or 0),
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note,
        "self_reviewed": bool(row.reviewed_by and row.created_by == row.reviewed_by),
        "finance_return_reason": row.finance_return_reason,
        "finance_returned_by_name": names.get(row.finance_returned_by or 0),
        "finance_returned_at": row.finance_returned_at.isoformat() if row.finance_returned_at else None,
        "finance_return_count": row.finance_return_count or 0,
        "received_by_name": names.get(row.received_by or 0),
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "receiving_note": row.receiving_note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "settled_by_name": names.get(row.settled_by or 0),
        "settled_at": row.settled_at.isoformat() if row.settled_at else None,
        "refunded_by_name": names.get(row.refunded_by or 0),
        "refunded_at": row.refunded_at.isoformat() if row.refunded_at else None,
        "cancel_reason": row.cancel_reason,
        "void_reason": row.void_reason,
        "items": [
            {
                "id": item.id,
                "shipment_batch_item_id": item.shipment_batch_item_id,
                "order_item_id": item.order_item_id,
                "batch_id": item.batch_id,
                "sku": item.sku_snapshot,
                "specification": item.specification_snapshot,
                "quantity": float(item.quantity or 0),
                "quantity_exact": format(item.quantity or 0, "f"),
                "unit_price": float(item.unit_price or 0),
                "unit_price_exact": format(item.unit_price or 0, ".4f"),
                "amount": float(item.amount or 0),
                "amount_exact": format(item.amount or 0, ".2f"),
            }
            for item in items
        ],
    }


async def list_returns_for_order(session: AsyncSession, order_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(SalesReturn)
            .where(SalesReturn.order_id == order_id)
            .order_by(SalesReturn.id.desc())
        )
    ).scalars().all()
    return [await serialize_return(session, row) for row in rows]


async def list_returns(
    session: AsyncSession, user, *, status: str | None = None, limit: int = 100
) -> list[dict]:
    stmt = select(SalesReturn, SalesOrder).join(
        SalesOrder, SalesOrder.id == SalesReturn.order_id
    )
    if status:
        stmt = stmt.where(SalesReturn.status == status)
    owners = await scoped_owner_ids(session, user)
    if owners is not None:
        stmt = stmt.where(SalesOrder.owner_id.in_(owners))
    rows = (
        await session.execute(
            stmt.order_by(SalesReturn.id.desc()).limit(min(max(limit, 1), 200))
        )
    ).all()
    return [await serialize_return(session, row[0]) for row in rows]
