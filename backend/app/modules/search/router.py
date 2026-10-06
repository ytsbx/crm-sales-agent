"""跨对象的全局搜索。

设计取舍：不做全文检索引擎（ES 留给后续），先用最简单的 ILIKE 覆盖
客户/联系人/线索/商机/报价/订单六类，够日常找人找单用。

权限纪律（第八批 §8.2）：全局搜索是"另一个入口"，权限口径必须和各模块的
列表接口**完全一致**——否则把 `quote:view` 撤了的人还能在这里看到报价号，
等于权限形同虚设。所以每一组都：

1. 先检查对应模块的查看权限（权限码逐个到各模块 `router.py` 里核对过，
   见 `_ALL_PERMISSION` / `_GROUP_PERMISSION` 的注释）；
2. 再做数据范围过滤（`app/core/data_scope.py`）；
3. 再做软删除过滤——**联系人还要连带排除已删客户**；
4. 无权限的组**一次查询都不发**，返回空 items 并标记 `denied`，
   连条数/摘要都不给（"有几条"本身就是信息）。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.modules.customer.model import Contact, Customer
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.order.model import ORDER_STATUS_LABEL, SalesOrder
from app.modules.quote.model import QUOTE_STATUS_LABEL, Quote

router = APIRouter(tags=["Search"])

#: 各组搜索所需的查看权限码。**与各业务模块路由上的原字符串逐字一致**：
#:   customer  → app/modules/customer/router.py 列表/详情：`customer:view`
#:   lead      → app/modules/lead/router.py    列表/详情：`lead:view`
#:   opportunity → app/modules/opportunity/router.py 列表/详情：`opportunity:view`
#:   quote     → app/modules/quote/router.py   列表/详情：`quote:view`
#:   order     → app/modules/order/router.py   列表/详情：`order:view`
#: 联系人没有独立权限码：它始终是"客户下的联系人"，与客户详情页同一口径。
_GROUP_PERMISSION: dict[str, tuple[str, ...]] = {
    "customer": ("customer:view",),
    "contact": ("customer:view",),
    "lead": ("lead:view",),
    "opportunity": ("opportunity:view",),
    "quote": ("quote:view",),
    "order": ("order:view",),
}

#: 返回给前端的组顺序（保持既有顺序，前端按顺序渲染）
GROUP_ORDER: tuple[str, ...] = ("customer", "opportunity", "quote", "order", "lead", "contact")

#: 组的中文名与前端跳转地址（原实现在返回里内联，这里提出来给空结果分支复用）
_GROUP_LABEL: dict[str, str] = {
    "customer": "客户",
    "opportunity": "商机",
    "quote": "报价单",
    "order": "订单",
    "lead": "线索",
    "contact": "联系人",
}
_GROUP_ROUTE: dict[str, str] = {
    "customer": "/customers",
    "opportunity": "/opportunities",
    "quote": "/quotes",
    "order": "/orders",
    "lead": "/leads",
    "contact": "/customers",
}


def _maybe_mask_mobile(mobile: str | None) -> str | None:
    """手机号脱敏（统一口径，见 `contact_util.mask_contact_value`）。

    这里**只负责格式**：是否给完整值由调用方按已确认的规则判定
    （`contact_util.can_view_full_contact` / `full_contact_customer_ids`）。
    两处格式各写一份的话，同一份数据在搜索页与客户详情页会显示成两种样子，
    用户会以为其中一处是错的。
    """
    from app.modules.contact_util import mask_contact_value

    return mask_contact_value(mobile, "phone")


def _maybe_mask_email(email: str | None) -> str | None:
    """邮箱脱敏（统一口径）。"""
    from app.modules.contact_util import mask_contact_value

    return mask_contact_value(email, "email")


def _group_denied(key: str, user: CurrentUser) -> bool:
    """该组对当前用户是否无权（管理员与各模块路由同一口径：默认放行）。"""
    if "admin" in user.roles:
        return False
    return not any(user.has(code) for code in _GROUP_PERMISSION[key])


async def _scope(
    stmt, user: CurrentUser, column, session: AsyncSession, *, allow_unowned: bool = False
):
    """统一走 app/core/data_scope.py（`department_and_sub` 递归到下级部门）。

    `allow_unowned`：公海客户 / 无主线索（owner_id 为空）要显式放行——
    否则"详情给 id 能看、全局搜索却搜不到"，与业务口径不一致。
    """
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return stmt
    cond = column.in_(owner_ids)
    if allow_unowned:
        cond = or_(cond, column.is_(None))
    return stmt.where(cond)


@router.get("/search")
async def global_search(
    keyword: str = Query(min_length=1, max_length=64),
    limit: int = Query(5, ge=1, le=20),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    # 空白关键词直接空结果。原实现是先 `strip()` 再拼 `%...%`，
    # 于是 `"   "` 变成 `%%` —— 一次搜索把前 N 条全捞出来（每类一遍），
    # 既是全库通配又是"用返回条数探测库里有多少数据"的侧信道。
    keyword_key = keyword.strip()
    if not keyword_key:
        return ok(
            {
                "keyword": keyword,
                "total": 0,
                "groups": [
                    {
                        "type": key,
                        "label": _GROUP_LABEL[key],
                        "route": _GROUP_ROUTE[key],
                        "denied": _group_denied(key, user),
                        "items": [],
                    }
                    for key in GROUP_ORDER
                ],
            }
        )

    like = f"%{keyword_key}%"
    items_by_group: dict[str, list[dict]] = {key: [] for key in GROUP_ORDER}

    if not _group_denied("customer", user):
        customer_stmt = await _scope(
            select(Customer)
            .where(
                Customer.deleted_at.is_(None),
                or_(
                    Customer.name.ilike(like),
                    Customer.short_name.ilike(like),
                    Customer.region.ilike(like),
                ),
            )
            .order_by(Customer.id.desc())
            .limit(limit),
            user,
            Customer.owner_id,
            session,
            allow_unowned=True,
        )
        items_by_group["customer"] = [
            {
                "id": row.id,
                "title": row.name,
                "subtitle": f"{row.level or '-'} 级 · {row.region or '未填地区'}",
            }
            for row in (await session.execute(customer_stmt)).scalars().all()
        ]

    if not _group_denied("contact", user):
        # 联系人原先**是六类里唯一没过数据范围的**：按手机号/邮箱一搜全公司，
        # 姓名+手机+邮箱+所属客户名全出来。联系人跟着它所属客户的负责人走。
        #
        # `Customer.deleted_at.is_(None)` 是第八批补的：原实现只挡了联系人自己的
        # 软删除，客户被删（合并/清理）后它的联系人仍会被搜出来，
        # 点进去落到一个已经不存在的客户上。
        contact_stmt = await _scope(
            select(Contact, Customer.name)
            .join(Customer, Customer.id == Contact.customer_id)
            .where(
                Contact.deleted_at.is_(None),
                Customer.deleted_at.is_(None),
                or_(
                    Contact.name.ilike(like),
                    Contact.mobile.ilike(like),
                    Contact.email.ilike(like),
                ),
            )
            .order_by(Contact.id.desc())
            .limit(limit),
            user,
            Customer.owner_id,
            session,
            allow_unowned=True,
        )
        contact_rows = (await session.execute(contact_stmt)).all()
        # 是否给完整联系方式：**与其他入口共用同一条规则**（用户 2026-10-06 确认：
        # 负责人本客户 / 主管本团队 / 管理员全部 / 其他可见人员脱敏，另授权走
        # `customer:contact_full`）。搜索是跨范围入口，所以一次性按客户批量判定，
        # 不给逐条查的机会 —— 逐条判迟早会有一条漏判。
        from app.modules.contact_util import full_contact_customer_ids

        allowed_customers = await full_contact_customer_ids(
            session, user, {row[0].customer_id for row in contact_rows}
        )
        items_by_group["contact"] = [
            _serialize_contact(
                contact,
                customer_name,
                full=allowed_customers is None
                or (
                    contact.customer_id is not None
                    and int(contact.customer_id) in allowed_customers
                ),
            )
            for contact, customer_name in contact_rows
        ]

    if not _group_denied("lead", user):
        lead_stmt = await _scope(
            select(Lead)
            .where(
                Lead.deleted_at.is_(None),
                or_(
                    Lead.name.ilike(like),
                    Lead.company_name.ilike(like),
                    Lead.mobile.ilike(like),
                ),
            )
            .order_by(Lead.id.desc())
            .limit(limit),
            user,
            Lead.owner_id,
            session,
            allow_unowned=True,
        )
        items_by_group["lead"] = [
            {
                "id": row.id,
                "title": row.name,
                "subtitle": f"{row.company_name or '未填公司'} · {row.status}",
            }
            for row in (await session.execute(lead_stmt)).scalars().all()
        ]

    if not _group_denied("opportunity", user):
        opportunity_stmt = await _scope(
            select(Opportunity, OpportunityStage.name, Customer.name)
            .join(OpportunityStage, OpportunityStage.id == Opportunity.stage_id)
            .join(Customer, Customer.id == Opportunity.customer_id)
            .where(Opportunity.deleted_at.is_(None), Opportunity.title.ilike(like))
            .order_by(Opportunity.id.desc())
            .limit(limit),
            user,
            Opportunity.owner_id,
            session,
        )
        items_by_group["opportunity"] = [
            {
                "id": opp.id,
                "title": opp.title,
                "subtitle": f"{customer_name} · {stage_name}",
            }
            for opp, stage_name, customer_name in (await session.execute(opportunity_stmt)).all()
        ]

    if not _group_denied("quote", user):
        quote_stmt = await _scope(
            select(Quote, Customer.name)
            .join(Customer, Customer.id == Quote.customer_id)
            .where(Quote.deleted_at.is_(None), Quote.quote_no.ilike(like))
            .order_by(Quote.id.desc())
            .limit(limit),
            user,
            Quote.owner_id,
            session,
        )
        items_by_group["quote"] = [
            {
                "id": quote.id,
                "title": quote.quote_no,
                "subtitle": f"{customer_name} · {QUOTE_STATUS_LABEL.get(quote.status, quote.status)}",
            }
            for quote, customer_name in (await session.execute(quote_stmt)).all()
        ]

    if not _group_denied("order", user):
        order_stmt = await _scope(
            select(SalesOrder, Customer.name)
            .join(Customer, Customer.id == SalesOrder.customer_id)
            .where(SalesOrder.order_no.ilike(like))
            .order_by(SalesOrder.id.desc())
            .limit(limit),
            user,
            SalesOrder.owner_id,
            session,
        )
        items_by_group["order"] = [
            {
                "id": order.id,
                "title": order.order_no,
                "subtitle": f"{customer_name} · {ORDER_STATUS_LABEL.get(order.status, order.status)}",
            }
            for order, customer_name in (await session.execute(order_stmt)).all()
        ]

    return ok(
        {
            "keyword": keyword_key,
            "total": sum(len(items) for items in items_by_group.values()),
            "groups": [
                {
                    "type": key,
                    "label": _GROUP_LABEL[key],
                    "route": _GROUP_ROUTE[key],
                    "denied": _group_denied(key, user),
                    "items": items_by_group[key],
                }
                for key in GROUP_ORDER
            ],
        }
    )


def _serialize_contact(contact: Contact, customer_name: str | None, *, full: bool) -> dict:
    """联系人搜索项。

    `full=False` 时手机号/邮箱只给"可辨识但不完整"的形式（`138****8000`）。
    判定由调用方按**已确认的统一规则**给出（`full_contact_customer_ids`）：
    负责人本客户 / 主管本团队 / 管理员全部 / 显式授权 `customer:contact_full`。
    这里不自己判断"这个人算不算自己人" —— 那正是原来五处入口各写一份、
    最后漏掉一处的原因。
    """
    return {
        "id": contact.id,
        "customer_id": contact.customer_id,
        "title": contact.name,
        "subtitle": (
            f"{customer_name} · "
            f"{(contact.mobile if full else _maybe_mask_mobile(contact.mobile)) or '无手机'}"
        ),
        "mobile": contact.mobile if full else _maybe_mask_mobile(contact.mobile),
        "email": contact.email if full else _maybe_mask_email(contact.email),
        "contact_masked": not full,
    }
