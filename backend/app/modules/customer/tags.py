"""客户标签与客户合并（02-ER §5 的 tags / customer_tags / customer_merge_logs）。

合并为什么要这么谨慎：
客户是我们所有业务对象的根，合并意味着把联系人、商机、报价、订单、跟进、任务、
附件、时间线相关记录全部改挂到目标客户，然后软删来源客户。
这是**不可逆**操作，所以：
1. 合并前先存来源客户的完整快照（`merge_snapshot`）；
2. 记录每类关联对象迁移了多少条（`moved`），事后可核对；
3. 来源客户走软删而不是物理删，出问题还能查；
4. 合并写审计日志与时间线。
"""

from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import (
    Contact,
    Customer,
    CustomerMergeLog,
    Tag,
    customer_tags,
)


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ 标签字典

async def list_tags(session: AsyncSession, *, only_active: bool = False) -> list[Tag]:
    stmt = select(Tag).order_by(Tag.sort_no.asc(), Tag.id.asc())
    if only_active:
        stmt = stmt.where(Tag.status == "active")
    return list((await session.execute(stmt)).scalars().all())


async def get_tag_or_404(session: AsyncSession, tag_id: int) -> Tag:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise AppError(ErrorCode.NOT_FOUND, "标签不存在", 404)
    return tag


async def find_tag_by_name(session: AsyncSession, name: str) -> Tag | None:
    return (
        await session.execute(select(Tag).where(Tag.name == name.strip()))
    ).scalar_one_or_none()


async def create_tag(
    session: AsyncSession, *, name: str, type_: str = "custom", sort_no: int = 0
) -> Tag:
    name = name.strip()
    if not name:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "标签名不能为空")
    if await find_tag_by_name(session, name) is not None:
        raise AppError(ErrorCode.DUPLICATE, f"标签「{name}」已存在", 409)
    tag = Tag(name=name, type=type_ or "custom", status="active", sort_no=sort_no)
    session.add(tag)
    await session.flush()
    return tag


async def update_tag(session: AsyncSession, tag: Tag, data: dict) -> Tag:
    if "name" in data and data["name"]:
        new_name = data["name"].strip()
        existing = await find_tag_by_name(session, new_name)
        if existing is not None and existing.id != tag.id:
            raise AppError(ErrorCode.DUPLICATE, f"标签「{new_name}」已存在", 409)
        tag.name = new_name
    for field in ("type", "status", "sort_no"):
        if field in data and data[field] is not None:
            setattr(tag, field, data[field])
    await session.flush()
    return tag


async def delete_tag(session: AsyncSession, tag: Tag) -> None:
    """删除标签：连带清掉与客户的关联，避免留下悬挂引用。"""
    await session.execute(delete(customer_tags).where(customer_tags.c.tag_id == tag.id))
    await session.delete(tag)


async def tags_of_customers(
    session: AsyncSession, customer_ids: list[int]
) -> dict[int, list[dict]]:
    """批量取多个客户的标签，避免列表页 N+1。"""
    if not customer_ids:
        return {}
    rows = (
        await session.execute(
            select(customer_tags.c.customer_id, Tag)
            .join(Tag, Tag.id == customer_tags.c.tag_id)
            .where(customer_tags.c.customer_id.in_(customer_ids))
            .order_by(Tag.sort_no.asc(), Tag.id.asc())
        )
    ).all()
    result: dict[int, list[dict]] = {}
    for customer_id, tag in rows:
        result.setdefault(customer_id, []).append(serialize_tag(tag))
    return result


def serialize_tag(tag: Tag) -> dict:
    return {
        "id": tag.id,
        "name": tag.name,
        "type": tag.type,
        "status": tag.status,
        "sort_no": tag.sort_no,
    }


async def attach_tags(session: AsyncSession, customer_id: int, tag_ids: list[int]) -> int:
    """给客户打标签（已存在的跳过）。返回实际新增数量。"""
    if not tag_ids:
        return 0
    existing = set(
        (
            await session.execute(
                select(customer_tags.c.tag_id).where(
                    customer_tags.c.customer_id == customer_id
                )
            )
        ).scalars().all()
    )
    # 校验标签都存在，避免打进不存在的 id
    found = set(
        (
            await session.execute(select(Tag.id).where(Tag.id.in_(tag_ids)))
        ).scalars().all()
    )
    missing = set(tag_ids) - found
    if missing:
        raise AppError(ErrorCode.NOT_FOUND, f"标签不存在：{sorted(missing)}", 404)

    added = 0
    for tag_id in dict.fromkeys(tag_ids):
        if tag_id not in existing:
            await session.execute(
                customer_tags.insert().values(customer_id=customer_id, tag_id=tag_id)
            )
            added += 1
    await session.flush()
    return added


async def detach_tag(session: AsyncSession, customer_id: int, tag_id: int) -> None:
    await session.execute(
        delete(customer_tags).where(
            customer_tags.c.customer_id == customer_id, customer_tags.c.tag_id == tag_id
        )
    )


# ------------------------------------------------------------------ 客户合并

# 需要改挂到目标客户的关联：模型 + 外键字段名 + 中文名（写进 moved，便于事后核对）
MERGE_TARGETS: list[tuple[str, str, str]] = [
    ("app.modules.customer.model", "Contact", "联系人"),
    ("app.modules.opportunity.model", "Opportunity", "商机"),
    ("app.modules.quote.model", "Quote", "报价单"),
    ("app.modules.order.model", "SalesOrder", "销售订单"),
    ("app.modules.followup.model", "FollowUp", "跟进记录"),
    ("app.modules.task.model", "Task", "任务"),
]


def _load_model(module_path: str, class_name: str):
    import importlib

    return getattr(importlib.import_module(module_path), class_name)


async def merge_customers(
    session: AsyncSession,
    *,
    source: Customer,
    target: Customer,
    operator_id: int | None,
    reason: str | None = None,
) -> dict:
    """把 source 合并进 target，返回迁移统计。

    规则：
    - 关联对象（联系人/商机/报价/订单/跟进/任务）全部改挂到 target；
    - 联系人里"主要联系人"唯一性若冲突，保留 target 原有的；
    - source 的标签并入 target；
    - 目标客户缺失的经营字段用来源客户补齐（不覆盖已有值）；
    - source 软删。
    """
    if source.id == target.id:
        raise AppError(ErrorCode.PARAM_ERROR, "不能把客户合并到自己", 422)
    if source.deleted_at is not None:
        raise AppError(ErrorCode.PARAM_ERROR, "来源客户已被删除，无法合并", 422)
    if target.deleted_at is not None:
        raise AppError(ErrorCode.PARAM_ERROR, "目标客户已被删除，无法合并", 422)

    snapshot = {
        "name": source.name,
        "short_name": source.short_name,
        "customer_type": source.customer_type,
        "country": source.country,
        "region": source.region,
        "address": source.address,
        "domain": source.domain,
        "tax_no": source.tax_no,
        "source": source.source,
        "level": source.level,
        "status": source.status,
        "pool_status": source.pool_status,
        "owner_id": source.owner_id,
        "remark": source.remark,
    }

    moved: dict[str, int] = {}

    # 1) 关联对象改挂
    # 并过来的联系人 id 要**先记下来**：下面判断"主要联系人唯一"时，
    # 只能降级"刚并过来的那几个"，不能连目标客户原有的主联系人一起清掉。
    # 原实现是 where(customer_id == target.id and is_primary) 全置假，
    # 分不清哪些是刚并过来的——注释写"把并过来的降级"，代码却把目标原有的也清了，
    # 结果合并完常常一个主联系人都不剩，要人工再设。
    merged_contact_ids = list(
        (
            await session.execute(select(Contact.id).where(Contact.customer_id == source.id))
        ).scalars().all()
    )
    for module_path, class_name, label in MERGE_TARGETS:
        model = _load_model(module_path, class_name)
        result = await session.execute(
            model.__table__.update()
            .where(model.customer_id == source.id)
            .values(customer_id=target.id)
        )
        moved[label] = int(result.rowcount or 0)

    # 2) 主要联系人唯一：target 已有主要联系人时，把并过来的降级为非主要
    has_primary = (
        await session.execute(
            select(func.count(Contact.id)).where(
                Contact.customer_id == target.id,
                Contact.is_primary.is_(True),
                # 必须是**目标原有的**主联系人：这个判断跑在"改挂之后"，
                # 不排掉并过来的那几个，就会把"目标原来没有主、并过来的那个是主"
                # 也当成有主而一起降级——合并完反而一个主联系人都不剩。
                Contact.id.not_in(merged_contact_ids or [0]),
            )
        )
    ).scalar_one()
    if has_primary:
        # 只降级刚并过来的联系人（它们在 source 名下时是主要的那几个）
        if merged_contact_ids:
            await session.execute(
                Contact.__table__.update()
                .where(
                    Contact.id.in_(merged_contact_ids),
                    Contact.customer_id == target.id,
                    Contact.is_primary.is_(True),
                )
                .values(is_primary=False)
            )

    # 3) 标签并入
    source_tag_ids = list(
        (
            await session.execute(
                select(customer_tags.c.tag_id).where(
                    customer_tags.c.customer_id == source.id
                )
            )
        ).scalars().all()
    )
    moved["标签"] = await attach_tags(session, target.id, source_tag_ids)
    await session.execute(
        delete(customer_tags).where(customer_tags.c.customer_id == source.id)
    )

    # 4) 补齐目标客户缺失的经营字段（不覆盖已有值）
    for column in ("short_name", "customer_type", "country", "region", "address",
                   "domain", "tax_no", "source", "level", "remark"):
        if getattr(target, column) in (None, "") and snapshot.get(column):
            setattr(target, column, snapshot[column])
    # 负责人：目标没有负责人时继承来源的
    if target.owner_id is None and source.owner_id is not None:
        target.owner_id = source.owner_id
        target.pool_status = "private"

    # 5) 来源软删 + 标记（避免它仍出现在列表/公海里）
    source.deleted_at = _now()
    source.owner_id = None
    source.pool_status = "public"

    log = CustomerMergeLog(
        source_customer_id=source.id,
        target_customer_id=target.id,
        operator_id=operator_id,
        merge_snapshot=snapshot,
        moved=moved,
        reason=reason,
        created_at=_now(),
    )
    session.add(log)
    await session.flush()

    return {"moved": moved, "merge_log_id": log.id, "snapshot": snapshot}


async def merge_logs(session: AsyncSession, customer_id: int) -> list[dict]:
    """某客户相关的合并记录（既是来源也是目标）。"""
    rows = (
        await session.execute(
            select(CustomerMergeLog)
            .where(
                (CustomerMergeLog.source_customer_id == customer_id)
                | (CustomerMergeLog.target_customer_id == customer_id)
            )
            .order_by(CustomerMergeLog.id.desc())
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "source_customer_id": row.source_customer_id,
            "target_customer_id": row.target_customer_id,
            "operator_id": row.operator_id,
            "moved": row.moved,
            "reason": row.reason,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
