"""跨模块复用的联系人工具与客户查重。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.similarity import similarity_score
from app.modules.customer.model import Contact, Customer


async def create_contact_for_customer(
    session: AsyncSession,
    *,
    customer_id: int,
    name: str,
    mobile: str | None = None,
    email: str | None = None,
    owner_id: int | None = None,
    source: str | None = None,
    is_primary: bool | None = None,
) -> Contact:
    """创建联系人。未显式指定时：该客户还没有主联系人就把这条设为主联系人。"""
    if is_primary is None:
        existing_primary = (
            await session.execute(
                select(Contact.id).where(
                    Contact.customer_id == customer_id,
                    Contact.is_primary.is_(True),
                    Contact.deleted_at.is_(None),
                )
            )
        ).first()
        is_primary = existing_primary is None

    contact = Contact(
        customer_id=customer_id,
        name=name,
        mobile=mobile,
        email=email,
        owner_id=owner_id,
        source=source,
        is_primary=is_primary,
    )
    session.add(contact)
    await session.flush()
    return contact


async def find_duplicate_customers(
    session: AsyncSession,
    *,
    company_name: str | None,
    mobile: str | None,
    tax_no: str | None = None,
    domain: str | None = None,
    address: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """找疑似重复客户：先宽松捞候选，再用加权规则打分排序。

    打分权重与阈值从系统配置读（dedup_scoring），业务可调；
    返回的 score / reasons 会直接显示在界面上，业务能看到"为什么判它疑似"。
    """
    from app.modules.settings import service as settings_service

    weights = await settings_service.get_setting(session, "dedup_scoring")
    threshold = int(weights.get("threshold", 50))

    conditions = []
    if company_name:
        conditions.append(Customer.name.ilike(f"%{company_name.strip()}%"))
        conditions.append(Customer.short_name.ilike(f"%{company_name.strip()}%"))
        # 名称相似但不相含的（例如只差后缀），用前几个字再捞一轮候选
        head = company_name.strip()[:4]
        if len(head) >= 2:
            conditions.append(Customer.name.ilike(f"%{head}%"))
    if mobile:
        contact_customer_ids = select(Contact.customer_id).where(
            Contact.mobile == mobile, Contact.deleted_at.is_(None)
        )
        conditions.append(Customer.id.in_(contact_customer_ids))
    for value, column in ((tax_no, Customer.tax_no), (domain, Customer.domain)):
        if value:
            conditions.append(column == value.strip())
    if not conditions:
        return []

    stmt = (
        select(Customer)
        .where(Customer.deleted_at.is_(None), or_(*conditions))
        .limit(max(limit * 4, 20))
    )
    rows = (await session.execute(stmt)).scalars().all()

    # 取候选客户的主联系人手机号，参与打分
    candidate_ids = [row.id for row in rows]
    contact_rows = (
        await session.execute(
            select(Contact.customer_id, Contact.mobile, Contact.email).where(
                Contact.customer_id.in_(candidate_ids), Contact.deleted_at.is_(None)
            )
        )
    ).all() if candidate_ids else []
    contact_index: dict[int, list[dict]] = {}
    for customer_id, contact_mobile, contact_email in contact_rows:
        contact_index.setdefault(int(customer_id), []).append(
            {"mobile": contact_mobile, "email": contact_email}
        )

    scored: list[dict] = []
    for customer in rows:
        best_score, best_reasons = 0, []
        my_mobile = mobile or ""
        candidates = contact_index.get(customer.id) or [{}]
        for contact in candidates:
            score, reasons = similarity_score(
                left={
                    "name": company_name,
                    "mobile": my_mobile,
                    "tax_no": tax_no,
                    "domain": domain,
                    "address": address,
                },
                right={
                    "name": customer.name,
                    "mobile": contact.get("mobile"),
                    "tax_no": customer.tax_no,
                    "domain": customer.domain,
                    "address": customer.address,
                },
                weights=weights,
            )
            if score > best_score:
                best_score, best_reasons = score, reasons
        if best_score >= threshold:
            scored.append(
                {
                    "id": customer.id,
                    "name": customer.name,
                    "level": customer.level,
                    "region": customer.region,
                    "owner_id": customer.owner_id,
                    "pool_status": customer.pool_status,
                    "score": best_score,
                    "reasons": best_reasons,
                }
            )

    scored.sort(key=lambda item: -item["score"])
    return scored[:limit]
