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
#
# 返工单 6.8：原来只有前 6 项，**定制需求、打样、订单草稿、合同、销售案例、
# 物流报价**全漏了。来源客户随后被软删、负责人被清空，这些单据就成了孤儿：
# 订单已经在目标客户名下，同一笔生意的打样还挂在来源客户上；合同和附件在
# 目标档案里找不到；单据之间"同客户"的校验开始报错。
#
# 判据：**凡是带 `customer_id` 的业务表都要在这里登记**。漏登记一项，
# 合并就是"看起来成功了、其实留了一半"。
MERGE_TARGETS: list[tuple[str, str, str]] = [
    ("app.modules.customer.model", "Contact", "联系人"),
    ("app.modules.opportunity.model", "Opportunity", "商机"),
    ("app.modules.quote.model", "Quote", "报价单"),
    ("app.modules.order.model", "SalesOrder", "销售订单"),
    ("app.modules.followup.model", "FollowUp", "跟进记录"),
    ("app.modules.task.model", "Task", "任务"),
    ("app.modules.inquiry.model", "CustomInquiry", "定制需求"),
    ("app.modules.sample.model", "SampleRequest", "打样单"),
    ("app.modules.order.model", "OrderDraft", "订单草稿"),
    ("app.modules.contract.model", "ContractDocument", "合同"),
    ("app.modules.cases.model", "SalesCase", "销售案例"),
    ("app.modules.pricing.model", "LogisticsQuote", "物流报价"),
    # 专属价格要跟着走，但**有冲突时不能随便走**：两边同一个 SKU 定了不同的价，
    # 谁对要人说了算（见 `_detect_conflicts`）。登记在这里是为了参与迁移，
    # 冲突处理在迁移之前先按调用方选的口径把一边转成历史资料。
    ("app.modules.pricing.model", "CustomerPriceRule", "专属价格"),
]

#: 字段名不叫 `customer_id` 的关联，得单独写。放这儿是为了让
#: "到底有哪些东西要跟着走"一眼能看全，而不是散在函数体里。
SPECIAL_LINK_LABELS = {
    "wecom_mapping": "企微客户映射",
    "attachments": "客户附件",
}



def _load_model(module_path: str, class_name: str):
    import importlib

    return getattr(importlib.import_module(module_path), class_name)


async def _count_links(session: AsyncSession, *, source_id: int) -> list[dict]:
    """数一遍会跟着走的关联对象（合并预览用）。"""
    rows: list[dict] = []
    for module_path, class_name, label in MERGE_TARGETS:
        model = _load_model(module_path, class_name)
        count = (
            await session.execute(
                select(func.count()).select_from(model).where(model.customer_id == source_id)
            )
        ).scalar_one()
        rows.append({"key": class_name, "label": label, "count": int(count or 0)})
    return rows


async def _count_special_links(session: AsyncSession, *, source_id: int) -> list[dict]:
    """字段名不叫 `customer_id` 的那几类关联，单独数。"""
    from app.modules.file.model import BusinessFile
    from app.modules.wecom.model import WeComExternalContact

    wecom = (
        await session.execute(
            select(func.count())
            .select_from(WeComExternalContact)
            .where(WeComExternalContact.crm_customer_id == source_id)
        )
    ).scalar_one()
    attachments = (
        await session.execute(
            select(func.count())
            .select_from(BusinessFile)
            .where(
                BusinessFile.business_type == "customer",
                BusinessFile.business_id == source_id,
            )
        )
    ).scalar_one()
    return [
        {"key": "wecom_mapping", "label": SPECIAL_LINK_LABELS["wecom_mapping"],
         "count": int(wecom or 0)},
        {"key": "attachments", "label": SPECIAL_LINK_LABELS["attachments"],
         "count": int(attachments or 0)},
    ]


async def _detect_conflicts(
    session: AsyncSession, *, source: Customer, target: Customer
) -> list[dict]:
    """合并前**必须先有人拍板**的冲突（返工单 6.8 第 5 条：不许静默覆盖）。

    两类：
    1. **专属价格**：同一个 SKU + 同一数量档，两边各有一条生效中的客户特殊价，
       且价格不同。都留着就是两条互相矛盾的报价依据 —— 系统不替业务选价。
    2. **协议主体不一致**：两边都填了纳税人识别号却不一样。税号不同通常说明
       这本来就是两家公司，合之前要人确认。
    """
    conflicts: list[dict] = []

    from app.modules.pricing.model import CustomerPriceRule

    source_rules = (
        await session.execute(
            select(CustomerPriceRule).where(
                CustomerPriceRule.customer_id == source.id,
                CustomerPriceRule.status == "active",
            )
        )
    ).scalars().all()
    target_index = {
        (rule.sku_id, str(rule.min_qty)): rule
        for rule in (
            await session.execute(
                select(CustomerPriceRule).where(
                    CustomerPriceRule.customer_id == target.id,
                    CustomerPriceRule.status == "active",
                )
            )
        ).scalars().all()
    }
    price_conflicts: list[dict] = []
    for rule in source_rules:
        other = target_index.get((rule.sku_id, str(rule.min_qty)))
        if other is None or other.agreed_price == rule.agreed_price:
            continue
        price_conflicts.append(
            {
                "sku_id": rule.sku_id,
                "min_qty": str(rule.min_qty),
                "source_price": str(rule.agreed_price),
                "target_price": str(other.agreed_price),
                "source_rule_id": rule.id,
                "target_rule_id": other.id,
            }
        )
    if price_conflicts:
        conflicts.append(
            {
                "key": "customer_price",
                "label": "专属价格",
                "detail": f"{len(price_conflicts)} 个「SKU + 数量档」两边都定了不同的价",
                "options": [
                    {"value": "keep_target", "label": "保留目标客户的价格（来源那几条转为历史资料）"},
                    {"value": "keep_source", "label": "改用来源客户的价格（目标那几条转为历史资料）"},
                ],
                "items": price_conflicts[:20],
                "count": len(price_conflicts),
            }
        )

    if source.tax_no and target.tax_no and source.tax_no != target.tax_no:
        conflicts.append(
            {
                "key": "tax_no",
                "label": "协议主体（纳税人识别号不一致）",
                "detail": (
                    f"来源「{source.tax_no}」与目标「{target.tax_no}」不同，"
                    "通常说明这本来就是两家公司，请先确认"
                ),
                "options": [
                    {"value": "confirm", "label": "确认是同一家、继续合并（税号保留目标客户的）"}
                ],
                "items": [],
                "count": 1,
            }
        )
    return conflicts


async def merge_preview(
    session: AsyncSession, *, source: Customer, target: Customer
) -> dict:
    """合并影响清单（返工单 6.8 第 1 条）。**只读**，不动任何数据。

    返回"会跟着走的东西有哪些、各多少条"以及"必须先解决的冲突"。
    页面上先给操作的人看这一页，再让他点确认 —— 合并是不可逆的，
    不能点一下才发现有两百张单子跟着换门牌。
    """
    if source.id == target.id:
        raise AppError(ErrorCode.PARAM_ERROR, "不能把客户合并到自己", 422)

    targets = await _count_links(session, source_id=source.id)
    targets += await _count_special_links(session, source_id=source.id)
    conflicts = await _detect_conflicts(session, source=source, target=target)

    return {
        "source": {
            "id": source.id, "name": source.name, "owner_id": source.owner_id,
            "tax_no": source.tax_no,
        },
        "target": {
            "id": target.id, "name": target.name, "owner_id": target.owner_id,
            "tax_no": target.tax_no,
        },
        "targets": targets,
        "total_links": sum(row["count"] for row in targets),
        "conflicts": conflicts,
        #: 这些 key 必须先给出处理口径才能合并
        "blocking": [row["key"] for row in conflicts],
        "note": (
            "合并不可逆：来源客户会被软删，上列关联全部改挂到目标客户。"
            "历史报价、合同签署文件、打样图纸与确认记录的内容不会改写，"
            "只是档案归属换到目标客户。"
        ),
    }


async def _move_special_links(
    session: AsyncSession, *, source: Customer, target: Customer
) -> dict[str, int]:
    """改挂字段名不叫 `customer_id` 的关联（返工单 6.8 第 2 条）。

    这些是"通用关联"：企微外部联系人用 `crm_customer_id` 记归属，
    附件用 `business_type + business_id` 记挂载点。原来都没处理，
    于是合并之后企微里那个客户还指着被删掉的来源档案，附件也在目标档案里
    看不到。
    """
    from sqlalchemy import update

    from app.modules.file.model import BusinessFile
    from app.modules.wecom.model import WeComExternalContact

    moved: dict[str, int] = {}
    result = await session.execute(
        update(WeComExternalContact)
        .where(WeComExternalContact.crm_customer_id == source.id)
        .values(crm_customer_id=target.id)
    )
    moved[SPECIAL_LINK_LABELS["wecom_mapping"]] = int(result.rowcount or 0)

    result = await session.execute(
        update(BusinessFile)
        .where(
            BusinessFile.business_type == "customer",
            BusinessFile.business_id == source.id,
        )
        .values(business_id=target.id)
    )
    moved[SPECIAL_LINK_LABELS["attachments"]] = int(result.rowcount or 0)
    return moved


async def merge_customers(
    session: AsyncSession,
    *,
    source: Customer,
    target: Customer,
    operator_id: int | None,
    reason: str | None = None,
    resolutions: dict[str, str] | None = None,
) -> dict:
    """把 source 合并进 target，返回迁移统计。

    规则：
    - 关联对象（联系人/商机/报价/订单/跟进/任务/定制需求/打样/草稿/合同/
      案例/物流报价/专属价格）全部改挂到 target；
    - 字段名不叫 `customer_id` 的那两类（企微映射、附件）也一起处理；
    - 联系人里"主要联系人"唯一性若冲突，保留 target 原有的；
    - source 的标签并入 target；
    - 目标客户缺失的经营字段用来源客户补齐（不覆盖已有值）；
    - source 软删。

    **冲突必须先有人拍板**（`resolutions`）：专属价格两边不一样、税号不一致
    这类事，代码不替业务决定。没给口径就直接拒绝合并，而不是自己挑一个默默用 ——
    静默覆盖之后，两边各有一批人记着"这个客户是按另一个价走的"。

    并发保护：先按 id **排序**加行锁。两个人同时对同一对客户点合并时，
    不加锁会各改一遍、各写一条日志；按固定顺序加锁还能避免互相死等。
    """
    if source.id == target.id:
        raise AppError(ErrorCode.PARAM_ERROR, "不能把客户合并到自己", 422)
    if source.deleted_at is not None:
        raise AppError(ErrorCode.PARAM_ERROR, "来源客户已被删除，无法合并", 422)
    if target.deleted_at is not None:
        raise AppError(ErrorCode.PARAM_ERROR, "目标客户已被删除，无法合并", 422)

    # 加锁，并把两个对象重读成库里的最新值
    locked: dict[int, Customer] = {}
    for customer_id in sorted({source.id, target.id}):
        row = (
            await session.execute(
                select(Customer)
                .where(Customer.id == customer_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars().first()
        if row is None or row.deleted_at is not None:
            raise AppError(ErrorCode.PARAM_ERROR, "客户已被删除，无法合并", 422)
        locked[customer_id] = row
    source, target = locked[source.id], locked[target.id]

    # 冲突摆出来，等调用方给口径
    conflicts = await _detect_conflicts(session, source=source, target=target)
    given = resolutions or {}
    unresolved = [row for row in conflicts if not given.get(row["key"])]
    if unresolved:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "合并前要先处理这些冲突："
            + "、".join(row["label"] for row in unresolved)
            + "。请先在「合并影响」里做出选择再提交",
            422,
        )
    conflicts_applied = {row["key"]: given[row["key"]] for row in conflicts}

    # 按选定的口径把一边的专属价转成**历史资料**
    # （`pricing/model.CustomerPriceRule.status` 写明 historical 不参与匹配与
    # 冲突检查）。不删：价目变动是事实，删掉就查不出来了。
    price_conflict = next(
        (row for row in conflicts if row["key"] == "customer_price"), None
    )
    if price_conflict:
        from sqlalchemy import update as sql_update

        from app.modules.pricing.model import CustomerPriceRule

        if conflicts_applied["customer_price"] == "keep_source":
            losing = [row["target_rule_id"] for row in price_conflict["items"]]
        else:
            losing = [row["source_rule_id"] for row in price_conflict["items"]]
        await session.execute(
            sql_update(CustomerPriceRule)
            .where(CustomerPriceRule.id.in_(losing))
            .values(status="historical")
        )

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

    # 1b) 字段名不叫 customer_id 的关联（企微映射、附件）
    moved.update(await _move_special_links(session, source=source, target=target))

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
        # 冲突是怎么处理的也要留痕：事后有人问"这个价为什么变成这样"，
        # 日志里要能看出当时是谁选了保留哪一边
        conflicts=conflicts_applied or None,
        reason=reason,
        created_at=_now(),
    )
    session.add(log)
    await session.flush()

    return {
        "moved": moved,
        "merge_log_id": log.id,
        "snapshot": snapshot,
        "conflicts": conflicts_applied,
    }


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
            "conflicts": row.conflicts,
            "reason": row.reason,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
