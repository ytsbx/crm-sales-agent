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


async def find_duplicate_contacts(
    session: AsyncSession,
    *,
    user,
    name: str | None,
    mobile: str | None = None,
    email: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """找疑似重复联系人（PRD §5.4 线索转化第 2 步"联系人查重"）。

    ## 为什么手机号/邮箱一致就判重，不走阈值

    `dedup_scoring` 的权重是给**客户**定的：客户靠公司名（70）+ 税号（60）
    识别，手机号只是辅助（45）。联系人不适用这一套 —— 没有税号，
    而"手机号一致"几乎就是同一个自然人。

    直接套阈值会得出荒谬结果：阈值 50 > 手机号权重 45，
    **手机号完全一致也查不出来**，查重形同虚设。

    所以这里的口径是：
      - 手机号或邮箱**完全一致** → 判重（这两个是唯一性标识）；
      - 只有姓名 → 走加权打分 + 阈值（人名重名很常见，必须保守）。

    只搜当前用户数据范围内的联系人（联系人跟随所属客户的数据范围；
    没有客户的"待归一"联系人不属于任何人，也一并纳入 —— 它们正是最可能
    造成重复的那一批）。
    """
    from app.core.data_scope import scoped_owner_ids
    from app.modules.settings import service as settings_service

    weights = await settings_service.get_setting(session, "dedup_scoring")
    threshold = int(weights.get("threshold", 50))

    mobile_key = (mobile or "").strip()
    email_key = (email or "").strip().lower()
    name_key = (name or "").strip()

    conditions = []
    if name_key:
        conditions.append(Contact.name.ilike(f"%{name_key}%"))
    if mobile_key:
        conditions.append(Contact.mobile == mobile_key)
    if email_key:
        conditions.append(Contact.email == email_key)
    if not conditions:
        return []

    stmt = (
        select(Contact)
        .where(Contact.deleted_at.is_(None), or_(*conditions))
        .limit(max(limit * 4, 20))
    )
    rows = (await session.execute(stmt)).scalars().all()

    # 数据范围：联系人跟着客户走；没客户的也放行
    visible: list[Contact] = []
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        visible = list(rows)
    else:
        customer_ids = {row.customer_id for row in rows if row.customer_id}
        allowed_customers: set[int] = set()
        if customer_ids:
            owner_rows = (
                await session.execute(
                    select(Customer.id, Customer.owner_id).where(
                        Customer.id.in_(customer_ids)
                    )
                )
            ).all()
            allowed_customers = {
                int(cid)
                for cid, owner in owner_rows
                if owner is None or int(owner) in owner_ids
            }
        visible = [
            row
            for row in rows
            if row.customer_id is None or int(row.customer_id) in allowed_customers
        ]

    scored: list[dict] = []
    for contact in visible:
        score = 0
        reasons: list[str] = []

        strong = False
        if mobile_key and (contact.mobile or "").strip() == mobile_key:
            reasons.append("手机号一致")
            strong = True
        if email_key and (contact.email or "").strip().lower() == email_key:
            reasons.append("邮箱一致")
            strong = True

        if strong:
            score = 100
        elif name_key:
            # 人名不比公司名：不能用 similarity_score 那套（它的 name 权重
            # 是按"公司名去掉有限公司后缀"定的），否则"张伟"和"张伟明"会被
            # 判成高度相似。这里只做"完全一致 / 互相包含"两档。
            actual = (contact.name or "").strip()
            if actual:
                if name_key == actual:
                    score += int(weights.get("weight_name_exact", 70))
                    reasons.append("姓名一致")
                elif name_key in actual or actual in name_key:
                    score += int(weights.get("weight_name_contains", 55))
                    reasons.append("姓名互相包含")

        if score >= threshold:
            scored.append(
                {
                    "id": contact.id,
                    "name": contact.name,
                    "customer_id": contact.customer_id,
                    "mobile": contact.mobile,
                    "email": contact.email,
                    "is_primary": contact.is_primary,
                    "score": min(score, 100),
                    "reasons": reasons,
                }
            )

    scored.sort(key=lambda item: -item["score"])
    return scored[:limit]

