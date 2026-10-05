"""样品业务逻辑（PRD §19、02-ER §13）。"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer
from app.modules.opportunity.model import Opportunity
from app.modules.product.model import Sku
from app.modules.sample.model import (
    CONFIRM_STATUS_LABEL,
    SAMPLE_STATUS_LABEL,
    SAMPLE_TRANSITIONS,
    SampleItem,
    SampleRequest,
    SampleShipment,
)

ZERO = Decimal(0)


def _f(value) -> float | None:
    return None if value is None else round(float(value), 4)


def serialize_item(item: SampleItem, sku: Sku | None = None) -> dict:
    source = item.source_snapshot
    return {
        "id": item.id,
        "sample_request_id": item.sample_request_id,
        "sku_id": item.sku_id,
        # 定制项（场景09）：没有 SKU 时用需求编号/需求名顶上，
        # 前端与打样单上要能看出"打的是哪条需求"
        "sku_code": (source.get('sku_code') or item.inquiry_no_snapshot) if source else (sku.sku_code if sku else item.inquiry_no_snapshot),
        "sku_name": item.item_name if source else (sku.name if sku else item.item_name),
        "specification": item.specification if source else (sku.specification if sku else None),
        "source_snapshot": source,
        "original_quantity": _f(item.original_quantity),
        "differences": ({'quantity_changed': item.original_quantity != item.quantity if item.original_quantity is not None else None,
                         'specification_changed': item.specification != source.get('specification'),
                         'remark_changed': item.remark != source.get('remark')} if source else None),
        "unit": source.get("unit") if source else (sku.unit if sku else None),
        "inquiry_id": item.inquiry_id,
        "inquiry_no": item.inquiry_no_snapshot,
        "is_custom": item.sku_id is None,
        "quantity": _f(item.quantity),
        # 车间依据：逐行不同，所以跟着明细走（见 model.SampleItem 的说明）
        "craft": item.craft,
        "material": item.material,
        "drawing_version": item.drawing_version,
        "remark": item.remark,
    }


def serialize_shipment(shipment: SampleShipment) -> dict:
    return {
        "id": shipment.id,
        "sample_request_id": shipment.sample_request_id,
        "carrier": shipment.carrier,
        "tracking_no": shipment.tracking_no,
        "shipping_fee": _f(shipment.shipping_fee),
        "shipped_at": shipment.shipped_at.isoformat() if shipment.shipped_at else None,
        "signed_at": shipment.signed_at.isoformat() if shipment.signed_at else None,
    }


def serialize_request(
    request: SampleRequest,
    *,
    customer_name: str | None = None,
    opportunity_title: str | None = None,
    owner_name: str | None = None,
    items: list[dict] | None = None,
    shipments: list[dict] | None = None,
    superseded_by: int | None = None,
) -> dict:
    return {
        "id": request.id,
        # 修订版（§3.3）：第几版、取代了谁、又被谁取代（superseded_by 有值即冻结只读）。
        # 由调用方查好传进来——本函数是同步的，拿不到 session。
        "version": request.version or 1,
        "parent_id": request.parent_id,
        "superseded_by": superseded_by,
        "source_context": request.source_context,
        "opportunity_id": request.opportunity_id,
        "opportunity_title": opportunity_title,
        "customer_id": request.customer_id,
        "customer_name": customer_name,
        "contact_id": request.contact_id,
        "owner_id": request.owner_id,
        "owner_name": owner_name,
        "status": request.status,
        # 审批轮次（§3.2）：驳回后重提 / 已批准后改车间依据会自增。
        # 界面据此显示"第 N 轮"，也让"被驳回过几次"数得出来。
        "review_round": request.review_round or 1,
        "status_label": SAMPLE_STATUS_LABEL.get(request.status, request.status),
        "remark": request.remark,
        "reject_reason": request.reject_reason,
        "requested_at": request.requested_at.isoformat() if request.requested_at else None,
        "approved_at": request.approved_at.isoformat() if request.approved_at else None,
        "shipped_at": request.shipped_at.isoformat() if request.shipped_at else None,
        "signed_at": request.signed_at.isoformat() if request.signed_at else None,
        "feedback": request.feedback,
        # ---- 生产打样资料（文档 §3.5）----
        # 材质 / 工艺 / 图纸版本不在这里：它们逐行不同，挂在明细上（见 items）。
        "purpose": request.purpose,
        "target_completion_date": (
            request.target_completion_date.isoformat() if request.target_completion_date else None
        ),
        "acceptance_criteria": request.acceptance_criteria,
        "sample_fee": _f(request.sample_fee),
        "production_owner_id": request.production_owner_id,
        "made_at": request.made_at.isoformat() if request.made_at else None,
        # 制作事件（结构化）：幂等的依据在这里；remark 只是给人看的展示文本
        "made_events": request.made_events or [],
        # ---- 客户确认（与签收分开：收到 ≠ 接受）----
        "confirm_status": request.confirm_status,
        "confirm_status_label": CONFIRM_STATUS_LABEL.get(
            request.confirm_status, request.confirm_status
        ),
        "customer_confirmed_at": (
            request.customer_confirmed_at.isoformat() if request.customer_confirmed_at else None
        ),
        "confirm_remark": request.confirm_remark,
        "created_by": request.created_by,
        "created_at": request.created_at.isoformat() if request.created_at else None,
        "items": items or [],
        "shipments": shipments or [],
    }


# ------------------------------------------------------------------ 查询

def base_query() -> Select:
    return select(SampleRequest)


async def apply_data_scope(
    stmt: Select, user: CurrentUser, session: AsyncSession
) -> Select:
    """按负责人数据范围过滤，与其他业务模块一致（department_and_sub 会递归到下级）。"""
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return stmt
    return stmt.where(SampleRequest.owner_id.in_(owner_ids))


def build_list_stmt(
    *,
    keyword: str | None = None,
    status: str | None = None,
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    owner_id: int | None = None,
) -> Select:
    stmt = base_query()
    if status:
        stmt = stmt.where(SampleRequest.status == status)
    if customer_id:
        stmt = stmt.where(SampleRequest.customer_id == customer_id)
    if opportunity_id:
        stmt = stmt.where(SampleRequest.opportunity_id == opportunity_id)
    if owner_id:
        stmt = stmt.where(SampleRequest.owner_id == owner_id)
    if keyword:
        # 样品单没有单号字段（ER §13 未定义），所以关键词只搜客户名与商机标题
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            SampleRequest.customer_id.in_(
                select(Customer.id).where(Customer.name.ilike(like))
            )
            | SampleRequest.opportunity_id.in_(
                select(Opportunity.id).where(Opportunity.title.ilike(like))
            )
        )
    return stmt


async def get_or_404(session: AsyncSession, sample_id: int) -> SampleRequest:
    request = await session.get(SampleRequest, sample_id)
    if request is None:
        raise AppError(ErrorCode.NOT_FOUND, "样品申请不存在", 404)
    return request


async def get_visible_or_404(
    session: AsyncSession, user: CurrentUser, sample_id: int, *, for_update: bool = False
) -> SampleRequest:
    """取样品申请并校验数据范围（列表按 owner_id 过滤，单条此前没校验）。

    `for_update=True` 是**写入口**的统一门（所有写接口都用它取单）。因此"这一版
    已经被新修订版取代、不能再动"的判断也放在这里——**一处把关胜过每个入口各写一遍**
    （第一批返修 §3.3 要求的正是"统一判断"，各写一遍迟早漏一个）。
    读接口（`for_update=False`）不受影响：历史版本永远查得到、对得上。
    """
    from app.core.data_scope import ensure_in_scope

    if for_update:
        request = (await session.execute(select(SampleRequest).where(SampleRequest.id == sample_id)
                   .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if request is None:
            raise AppError(ErrorCode.NOT_FOUND, "样品申请不存在", 404)
    else:
        request = await get_or_404(session, sample_id)
    await ensure_in_scope(session, user, owner_id=request.owner_id, label="样品申请")
    if for_update:
        # 注意：sample_requests **没有** deleted_at（这个模块不做软删），
        # 别照其它模块的习惯加 `deleted_at.is_(None)`——那是运行期 AttributeError。
        child = (
            await session.execute(
                select(SampleRequest.id)
                .where(SampleRequest.parent_id == request.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if child is not None:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"这是第 {request.version} 版，已被新修订版（#{child}）取代、不能再改。"
                "制作与寄送事实已按当时资料冻结保留；要继续改请在新版本上操作",
                422,
            )
    return request


async def items_of(session: AsyncSession, sample_id: int) -> list[SampleItem]:
    return list(
        (
            await session.execute(
                select(SampleItem)
                .where(SampleItem.sample_request_id == sample_id)
                .order_by(SampleItem.id.asc())
            )
        ).scalars().all()
    )


async def shipments_of(session: AsyncSession, sample_id: int) -> list[SampleShipment]:
    return list(
        (
            await session.execute(
                select(SampleShipment)
                .where(SampleShipment.sample_request_id == sample_id)
                .order_by(SampleShipment.id.asc())
            )
        ).scalars().all()
    )


async def enrichment(
    session: AsyncSession, rows: list[SampleRequest]
) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    """批量取客户名 / 商机标题 / 负责人名，避免 N+1。"""
    from app.modules.user.model import User

    customer_ids = {r.customer_id for r in rows if r.customer_id}
    opportunity_ids = {r.opportunity_id for r in rows if r.opportunity_id}
    owner_ids = {r.owner_id for r in rows if r.owner_id}

    customers: dict[int, str] = {}
    if customer_ids:
        customers = {
            cid: name
            for cid, name in (
                await session.execute(
                    select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
                )
            ).all()
        }
    opportunities: dict[int, str] = {}
    if opportunity_ids:
        opportunities = {
            oid: title
            for oid, title in (
                await session.execute(
                    select(Opportunity.id, Opportunity.title).where(
                        Opportunity.id.in_(opportunity_ids)
                    )
                )
            ).all()
        }
    owners: dict[int, str] = {}
    if owner_ids:
        owners = {
            uid: name
            for uid, name in (
                await session.execute(
                    select(User.id, User.name).where(User.id.in_(owner_ids))
                )
            ).all()
        }
    return customers, opportunities, owners


async def items_map(session: AsyncSession, sample_ids: list[int]) -> dict[int, list[SampleItem]]:
    """一次取回多个申请的明细（避免列表页 N+1）。"""
    if not sample_ids:
        return {}
    rows = (
        await session.execute(
            select(SampleItem)
            .where(SampleItem.sample_request_id.in_(sample_ids))
            .order_by(SampleItem.id.asc())
        )
    ).scalars().all()
    result: dict[int, list[SampleItem]] = {}
    for item in rows:
        result.setdefault(item.sample_request_id, []).append(item)
    return result


async def shipments_map(
    session: AsyncSession, sample_ids: list[int]
) -> dict[int, list[SampleShipment]]:
    if not sample_ids:
        return {}
    rows = (
        await session.execute(
            select(SampleShipment)
            .where(SampleShipment.sample_request_id.in_(sample_ids))
            .order_by(SampleShipment.id.asc())
        )
    ).scalars().all()
    result: dict[int, list[SampleShipment]] = {}
    for shipment in rows:
        result.setdefault(shipment.sample_request_id, []).append(shipment)
    return result


async def skus_map(session: AsyncSession, items: list[SampleItem]) -> dict[int, Sku]:
    sku_ids = {item.sku_id for item in items if item.sku_id}  # 定制项无 SKU
    if not sku_ids:
        return {}
    return {
        sku.id: sku
        for sku in (
            await session.execute(select(Sku).where(Sku.id.in_(sku_ids)))
        ).scalars().all()
    }


async def list_payload(session: AsyncSession, rows: list[SampleRequest]) -> list[dict]:
    """列表用的批量序列化：把明细、寄样、客户/商机/负责人名一次补齐。

    之前列表直接调 serialize_request 却没传 items/shipments，导致接口返回的
    这两个字段恒为空数组，界面显示「无明细 / 快递 -」。这里统一走批量装配。
    """
    ids = [row.id for row in rows]
    items_by_request = await items_map(session, ids)
    shipments_by_request = await shipments_map(session, ids)
    all_items = [item for items in items_by_request.values() for item in items]
    skus = await skus_map(session, all_items)
    customers, opportunities, owners = await enrichment(session, rows)
    # 一次查清"谁被谁取代了"（§3.3）：有子版本的那一版冻结只读，
    # 界面上要标出来。批量一次查询，别在循环里逐条问。
    child_of: dict[int, int] = {}
    if ids:
        # 同样注意：sample_requests 没有 deleted_at（本模块不做软删）
        for child_id, parent_id in (
            await session.execute(
                select(SampleRequest.id, SampleRequest.parent_id)
                .where(SampleRequest.parent_id.in_(ids))
            )
        ).all():
            child_of.setdefault(parent_id, child_id)

    payload = []
    for row in rows:
        payload.append(
            serialize_request(
                row,
                customer_name=customers.get(row.customer_id) if row.customer_id else None,
                opportunity_title=(
                    opportunities.get(row.opportunity_id) if row.opportunity_id else None
                ),
                owner_name=owners.get(row.owner_id) if row.owner_id else None,
                items=[
                    serialize_item(item, skus.get(item.sku_id))
                    for item in items_by_request.get(row.id, [])
                ],
                shipments=[
                    serialize_shipment(shipment)
                    for shipment in shipments_by_request.get(row.id, [])
                ],
                superseded_by=child_of.get(row.id),
            )
        )
    return payload


async def detail(session: AsyncSession, request: SampleRequest) -> dict:
    """单条详情：与列表共用同一套装配逻辑，避免两处字段漂移。"""
    payload = await list_payload(session, [request])
    return payload[0]


# ------------------------------------------------------------------ 状态流转

def ensure_transition(current: str, target: str) -> None:
    """校验状态流转，非法时给出明确提示而不是静默改状态。"""
    allowed = SAMPLE_TRANSITIONS.get(current, set())
    if target not in allowed:
        current_label = SAMPLE_STATUS_LABEL.get(current, current)
        if not allowed:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"样品当前状态是「{current_label}」，不能再变更",
                422,
            )
        allowed_labels = "、".join(
            SAMPLE_STATUS_LABEL.get(s, s) for s in sorted(allowed)
        )
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"样品当前状态是「{current_label}」，只能变更为：{allowed_labels}",
            422,
        )


async def record_opportunity_touch(
    session: AsyncSession, request: SampleRequest, action: str
) -> None:
    """样品动作回写商机的「下一步动作」，让销售在商机页就能看到样品进展。

    PRD §19 的「样品转商机跟进」：不新建商机，而是把样品进展反映到原商机，
    避免凭空多出一个商机。
    """
    if not request.opportunity_id:
        return
    opportunity = await session.get(Opportunity, request.opportunity_id)
    if opportunity is None or opportunity.deleted_at is not None:
        return
    label = SAMPLE_STATUS_LABEL.get(request.status, request.status)
    opportunity.next_action = f"样品（#{request.id}）{action}：当前{label}"


def now() -> datetime:
    return datetime.now(UTC)
