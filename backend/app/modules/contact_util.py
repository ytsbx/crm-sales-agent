"""跨模块复用的联系人工具与客户查重。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.similarity import similarity_score
from app.modules.customer.model import Contact, Customer

#: 显式授权码：拿到它的人可以看**完整**联系方式（不限于自己负责/本团队的客户）。
#: 用户 2026-10-06 确认的口径是「负责人本客户、主管本团队、管理员全部；
#: 其他可见人员脱敏，完整信息需另授权」—— 这个码就是"另授权"的落点。
#: 默认不授给任何角色（管理员角色本身不受限），由管理员按需开给特定岗位。
CONTACT_FULL_PERMISSION = "customer:contact_full"

#: 脱敏后仍然保留的字段：这些是"能联系上"的必要信息，不是禁用。
#: 口径是**脱敏**而不是**隐藏**：销售看得到"有没有填手机号、是不是同一个号"，
#: 但拿不到完整号码去批量导走（方案第六节：非授权完整信息脱敏）。
_MASKED_FIELDS = {
    "mobile": "phone",
    "phone": "phone",
    "email": "email",
    "wechat": "wechat",
}


def mask_contact_value(value: str | None, kind: str = "phone") -> str | None:
    """联系方式脱敏。空值原样返回（"没填"和"填了但看不到"必须区分得开）。

    - 手机号/电话：**按长度分档**保留，硬要求是"至少藏住一位数字"（见函数内注释）。
      11 位手机号仍是大家习惯的 `138****8000` 形状。
    - 邮箱：保留首字符与域名（`z***@example.com`）。
    - 微信：保留前 2 位。

    刻意**不做**"打码成 ***"：那会让用户分不清"没填"和"没权限看"，
    于是反复去找管理员要权限，而其实是他自己没录。
    """
    text = (value or "").strip()
    if not text:
        return value
    if kind == "email":
        name, _, domain = text.partition("@")
        if not domain:
            return text[:1] + "***"
        head = name[:1] if name else ""
        return f"{head}***@{domain}"
    if kind == "wechat":
        return text[:2] + "***" if len(text) > 2 else "***"
    # 电话类：**按长度分档**，硬要求是"至少藏住一位数字"。
    # 原来固定写 `前3 + **** + 后4`，遇到 7 位号码正好 3+4=7 —— 中间那四个星号
    # 一个数字都没挡住（1234567 原样可还原）；5、6 位还会把同一批数字重复显示。
    # 座机、短号、分机号都落在这些档里，业务上讲"脱敏"就必须真的挡住。
    length = len(text)
    if length <= 2:
        return "*" * length
    if length <= 4:
        return text[:1] + "*" * (length - 1)
    if length <= 7:
        # 5~7 位（短号 / 不带区号的座机）：留前 2 后 1
        return text[:2] + "*" * (length - 3) + text[-1]
    if length <= 10:
        # 8~10 位（带区号的座机）：留前 3 后 3
        return text[:3] + "*" * (length - 6) + text[-3:]
    # 11 位及以上（手机号）：保持"前 3 后 4"，这个形状业务已经看惯了
    return f"{text[:3]}****{text[-4:]}"


def mask_contact_fields(payload: dict) -> dict:
    """把序列化结果里的联系方式脱敏，并标出 `contact_masked=True`。

    只动 `_MASKED_FIELDS` 里的键：其余字段（姓名、职务、是否为联系人）与
    "这个客户有哪些联系人"这一层信息仍然可见 —— 要挡的是"拿到完整号码"，
    不是"知道有这个联系人"。
    """
    masked = dict(payload)
    for field, kind in _MASKED_FIELDS.items():
        if field in masked:
            masked[field] = mask_contact_value(masked.get(field), kind)
    masked["contact_masked"] = True
    return masked


async def can_view_full_contact(
    session: AsyncSession,
    user,
    *,
    customer_id: int | None,
    owner_id: int | None = None,
) -> bool:
    """当前用户能否看到这条联系人的**完整**联系方式。

    规则（用户 2026-10-06 确认）：
    - 管理员角色 / `data_scope == "all"`（管理员与财务这类全量可见者）→ 可以；
    - 持显式授权码 `customer:contact_full` → 可以；
    - 客户负责人本人 → 可以（"我的客户我总得能打电话"）；
    - 该负责人的主管（本团队）→ 可以。判定用 `scoped_owner_ids`：
      `department` / `department_and_sub` 数据范围的人，其集合就是本团队（及下级）成员；
    - 其余能看到这个客户的人（例如公海客户的所有可见者）→ **脱敏**。
      这也是"另授权"存在的意义：要完整信息就得显式给权限，而不是"能看客户就行"。
    """
    from app.core.data_scope import scoped_owner_ids

    if "admin" in getattr(user, "roles", []) or user.data_scope == "all":
        return True
    if CONTACT_FULL_PERMISSION in getattr(user, "permissions", set()):
        return True

    if owner_id is None and customer_id is not None:
        owner_row = (
            await session.execute(
                select(Customer.owner_id).where(Customer.id == customer_id)
            )
        ).first()
        owner_id = owner_row[0] if owner_row else None
    if owner_id is None:
        # 无负责人的客户（公海）：没人"负责"它，因此没有"负责人"这一档豁免
        return False
    if int(owner_id) == int(user.id):
        return True

    allowed = await scoped_owner_ids(session, user)
    return allowed is not None and int(owner_id) in allowed


async def full_contact_customer_ids(
    session: AsyncSession, user, customer_ids: set[int] | None
) -> set[int] | None:
    """批量判定：哪些客户的联系人可以对当前用户显示完整信息。

    返回 `None` 表示**不受限**（管理员/全量范围/显式授权）——
    列表接口据此走"不脱敏"的快路径，不必逐个客户查一遍。
    """
    from app.core.data_scope import scoped_owner_ids

    if "admin" in getattr(user, "roles", []) or user.data_scope == "all":
        return None
    if CONTACT_FULL_PERMISSION in getattr(user, "permissions", set()):
        return None
    ids = {int(cid) for cid in (customer_ids or set()) if cid is not None}
    if not ids:
        return set()

    allowed = await scoped_owner_ids(session, user)
    if not allowed:
        return set()
    rows = (
        await session.execute(
            select(Customer.id, Customer.owner_id).where(Customer.id.in_(ids))
        )
    ).all()
    return {
        int(cid)
        for cid, owner in rows
        if owner is not None and int(owner) in allowed
    }


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
    owner_ids: list[int] | None = None,
) -> list[dict]:
    """找疑似重复客户：先宽松捞候选，再用加权规则打分排序。

    打分权重与阈值从系统配置读（dedup_scoring），业务可调；
    返回的 score / reasons 会直接显示在界面上，业务能看到"为什么判它疑似"。

    `owner_ids`：调用者的数据范围（`scoped_owner_ids` 的结果）。**传 None 表示全量**
    （管理员/财务，或系统同步这类本来就要匹配全库的场景）。
    这个函数原来没有 user 概念，"先宽松捞候选"就直接捞了全库，返回里还带着
    客户名、等级、地区、负责人——业务员拿名称前缀/手机/税号就能把全公司客户
    枚举出来。与客户/线索模块"公海对所有有查看权限的人可见"的口径一致：
    无负责人的客户始终参与候选。
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
    if owner_ids is not None:
        # 范围内的 + 公海（无负责人）。公海对所有人可见，漏掉它会让"我查重查不到、
        # 但列表里翻得到"，用户会以为系统丢数据。
        stmt = stmt.where(or_(Customer.owner_id.in_(owner_ids), Customer.owner_id.is_(None)))
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

