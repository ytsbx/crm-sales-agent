"""联系人专属子资源接口（对齐 03-API §8）。

单独一个 router 是因为路由顺序：`/contacts/{id}/...` 这种"动态段 + 子路径"
必须注册在 `/contacts/{contact_id}` 之前，否则会被后者抢先匹配成一个 id 解析失败。
（同一个坑在 `/customers/export` 上踩过两次，见 main.py 的注释。）

## 与企微的关系

`GET /contacts/{id}/wecom` 是"这个 CRM 联系人对应哪个企微外部联系人"。
绑定关系存在 `wecom_external_contacts.crm_contact_id` 上，
是企微同步模块写入的；这里只读，不产生任何同步副作用。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.contact_util import find_duplicate_contacts
from app.modules.customer import service as svc

router = APIRouter(tags=["Customer"])


# ---------------------------------------------------------------- 联系人查重

@router.post("/contacts/deduplicate")
async def deduplicate_contacts(
    payload: dict,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """联系人查重（PRD §5.4 线索转化第 2 步）。

    线索转客户时要先判断"这个联系人是不是已经存在"，
    否则同一个人会被建两遍，跟进记录散在两处。

    可传已有联系人 id（编辑场景），也可传待建字段（新建场景）。
    """
    contact_id = payload.get("contact_id")
    fields = {
        "name": payload.get("name"),
        "mobile": payload.get("mobile"),
        "email": payload.get("email"),
    }
    exclude_id = None

    if contact_id:
        existing = await svc.get_visible_contact(session, user, contact_id)
        exclude_id = existing.id
        # 用库里已有值补全未传的字段；显式传的以后者为准
        fields = {
            "name": fields["name"] or existing.name,
            "mobile": fields["mobile"] or existing.mobile,
            "email": fields["email"] or existing.email,
        }

    if not any(fields.values()):
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            "至少要提供姓名、手机号、邮箱中的一项才能查重",
        )

    matches = await find_duplicate_contacts(session, user=user, **fields)
    if exclude_id is not None:
        matches = [m for m in matches if m.get("id") != exclude_id]
    return ok({"matches": matches, "count": len(matches)})


# ---------------------------------------------------------------- 单个联系人

@router.get("/contacts/{contact_id}/followups")
async def contact_followups(
    contact_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """该联系人的跟进记录（03-API §8）。

    跟进记录本身只挂在客户/线索上，但有相当一部分记录了 `contact_id`
    （"跟王经理通了电话"），按联系人看才有意义。
    """
    from app.modules.followup.model import FollowUp

    await svc.get_visible_contact(session, user, contact_id)
    stmt = (
        select(FollowUp)
        .where(FollowUp.contact_id == contact_id)
        .order_by(FollowUp.id.desc())
    )
    rows, total = await paginate(session, stmt, page, page_size)
    return ok(
        page_data(
            [
                {
                    "id": row.id,
                    "customer_id": row.customer_id,
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


@router.get("/contacts/{contact_id}/wecom")
async def contact_wecom(
    contact_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """该联系人对应的企微外部联系人（03-API §8）。

    没绑定过就返回 `bound: false`，而不是 404 —— "没绑定"是正常状态，
    前端据此显示"去企微归一"的入口。企微未配置也不影响这个查询。
    """
    from app.modules.wecom.model import WeComExternalContact, WeComFollowRelationship

    await svc.get_visible_contact(session, user, contact_id)

    externals = (
        await session.execute(
            select(WeComExternalContact)
            .where(WeComExternalContact.crm_contact_id == contact_id)
            .order_by(WeComExternalContact.id.desc())
        )
    ).scalars().all()

    if not externals:
        return ok({"bound": False, "contact_id": contact_id, "externals": []})

    # 每个外部联系人带上"谁加的、什么时候加的"
    external_ids = [item.id for item in externals]
    relations = (
        await session.execute(
            select(WeComFollowRelationship).where(
                WeComFollowRelationship.external_contact_id.in_(external_ids)
            )
        )
    ).scalars().all()
    by_external: dict[int, list[dict]] = {}
    for relation in relations:
        by_external.setdefault(relation.external_contact_id, []).append(
            {
                "wecom_userid": relation.wecom_userid,
                "add_time": relation.add_time,
                "add_way": relation.add_way,
                "remark": relation.remark,
                "status": relation.status,
            }
        )

    return ok(
        {
            "bound": True,
            "contact_id": contact_id,
            "externals": [
                {
                    "id": item.id,
                    "external_userid": item.external_userid,
                    "name": item.name,
                    "type": item.type,
                    "corp_name": item.corp_name,
                    "crm_customer_id": item.crm_customer_id,
                    "normalize_status": item.normalize_status,
                    "last_sync_at": item.last_sync_at,
                    "followers": by_external.get(item.id, []),
                }
                for item in externals
            ],
        }
    )
