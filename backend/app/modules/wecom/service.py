"""企业微信集成业务逻辑（PRD §8 / API §10）。

所有函数都不 commit，由接口层统一提交；抛出的 `WeComError` /
`WeComNotConfigured` 由接口层翻译成业务响应。

三块业务：
1. **同步**（§8.1 / §8.2）——部门、成员、外部联系人、跟进关系，每次写一条
   `wecom_sync_jobs` 流水；单条失败不让整批回滚，而是计入 fail_count。
2. **待归一**（§8.3）——外部联系人 ↔ CRM 客户的人工确认闭环。这里只做
   "列出来 + 给候选 + 执行绑定"，绝不自动绑。
3. **离职继承**（§8.4）——企微客户关系交给接管人，同时按 PRD 要求转移
   CRM 客户负责人、商机负责人、未完成任务；创建人与历史记录一律不动。
"""

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.contact_util import create_contact_for_customer, find_duplicate_customers
from app.modules.customer import duplicates as duplicate_service
from app.modules.customer import service as customer_service
from app.modules.customer.model import Contact, Customer
from app.modules.opportunity.model import Opportunity
from app.modules.sample.model import SAMPLE_OPEN_STATUSES, SAMPLE_STATUS_LABEL, SampleRequest
from app.modules.task.model import Task
from app.modules.user.model import Department, User
from app.modules.wecom import client as wecom_client
from app.modules.wecom.model import (
    CRM_OPEN_STATUSES,
    TRANSFER_KIND_LABEL,
    WECOM_OPEN_STATUSES,
    WeComExternalContact,
    WeComFollowRelationship,
    WeComSyncJob,
    WeComTransferItem,
    WeComUser,
)

# 任务里"还没做完"的状态，离职继承时要把这些任务的负责人换掉
OPEN_TASK_STATUS = ("pending", "doing")

NORMALIZE_LABEL = {
    "pending": "待处理",
    "bound": "已关联客户",
    "created": "已建客户",
    "ignored": "暂不处理",
}


def _to_datetime(value: object) -> datetime | None:
    """企微给的时间戳是秒级整数（偶尔是字符串），统一转成 datetime。"""
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return None


async def start_job(
    session: AsyncSession, *, job_type: str, operator_id: int | None
) -> WeComSyncJob:
    job = WeComSyncJob(job_type=job_type, status="running", operator_id=operator_id)
    session.add(job)
    await session.flush()
    return job


def finish_job(
    job: WeComSyncJob,
    *,
    success: int,
    fail: int,
    error: str | None = None,
    detail: dict | None = None,
) -> None:
    job.success_count = success
    job.fail_count = fail
    job.finished_at = datetime.now(UTC)
    job.error_message = error
    if detail:
        job.detail = detail
    if error and success == 0:
        job.status = "failed"
    elif fail:
        job.status = "partial"
    else:
        job.status = "success"


# ---- 通讯录同步（PRD §8.1）------------------------------------------------


async def sync_departments(session: AsyncSession, *, user: CurrentUser) -> WeComSyncJob:
    """同步部门：按企微 department_id 映射到 CRM Department，缺失则新建。

    父子关系分两轮处理：先把所有部门落库拿到 CRM id，再回填 parent_id，
    否则子部门先于父部门返回时会挂空（企微不保证返回顺序）。
    """
    job = await start_job(session, job_type="department", operator_id=user.id)
    api = wecom_client.get_client()
    departments = await api.list_departments()

    existing = {
        row.wecom_department_id: row
        for row in (
            await session.execute(select(Department).where(Department.wecom_department_id.isnot(None)))
        ).scalars().all()
    }
    by_wecom_id: dict[str, Department] = {}
    success = 0
    for item in departments:
        wecom_id = str(item.get("id"))
        row = existing.get(wecom_id)
        if row is None:
            row = Department(name=str(item.get("name") or wecom_id), wecom_department_id=wecom_id)
            session.add(row)
        else:
            row.name = str(item.get("name") or row.name)
        by_wecom_id[wecom_id] = row
        success += 1
    await session.flush()

    # 第二轮：回填父子关系（企微根部门 parentid 为 0 或 1，不建立 CRM 父级）
    for item in departments:
        wecom_id = str(item.get("id"))
        parent_wecom_id = str(item.get("parentid") or "")
        row = by_wecom_id.get(wecom_id)
        if row is None:
            continue
        if parent_wecom_id in ("", "0", "1"):
            row.parent_id = None
        else:
            parent = by_wecom_id.get(parent_wecom_id)
            row.parent_id = parent.id if parent else None

    finish_job(job, success=success, fail=0, detail={"total": len(departments)})
    return job


async def sync_users(session: AsyncSession, *, user: CurrentUser) -> WeComSyncJob:
    """同步成员：写 wecom_users 映射，并把 users.wecom_userid 回填上。

    匹配顺序：已有映射 → CRM 里同 wecom_userid 的用户 → 手机号相同的用户。
    都匹配不上就只留一条待处理的映射记录，**不自动建账号**——
    建账号涉及角色与数据权限，必须有人来定。
    """
    job = await start_job(session, job_type="user", operator_id=user.id)
    api = wecom_client.get_client()

    departments = await api.list_departments()
    root_id = next((int(item["id"]) for item in departments if str(item.get("parentid")) in ("0", "1")), None)
    if root_id is None and departments:
        root_id = int(departments[0]["id"])

    members: list[dict] = []
    if root_id is not None:
        # fetch_child=1 一次拿全公司，避免逐部门重复拉
        members = await api.list_department_users(root_id, fetch_child=True)

    mapping_rows = {
        row.wecom_userid: row
        for row in (await session.execute(select(WeComUser))).scalars().all()
    }
    crm_by_wecom = {
        row.wecom_userid: row
        for row in (
            await session.execute(select(User).where(User.wecom_userid.isnot(None)))
        ).scalars().all()
    }

    success = 0
    unmatched: list[str] = []
    now = datetime.now(UTC)
    for item in members:
        wecom_userid = str(item.get("userid") or "")
        if not wecom_userid:
            continue
        mobile = item.get("mobile")
        row = mapping_rows.get(wecom_userid)
        if row is None:
            row = WeComUser(wecom_userid=wecom_userid)
            session.add(row)

        crm_user = crm_by_wecom.get(wecom_userid)
        if crm_user is None and mobile:
            crm_user = (
                await session.execute(
                    select(User).where(User.mobile == mobile, User.status == "active")
                )
            ).scalars().first()

        row.user_id = crm_user.id if crm_user else None
        row.name = item.get("name")
        row.mobile = mobile
        row.email = item.get("email")
        row.position = item.get("position")
        row.status = str(item.get("status", ""))
        department_ids = item.get("department") or []
        row.wecom_department_ids = ",".join(str(x) for x in department_ids) or None
        row.sync_status = "synced" if crm_user else "pending"
        row.last_sync_at = now
        row.raw_data = item

        if crm_user is not None and crm_user.wecom_userid != wecom_userid:
            crm_user.wecom_userid = wecom_userid
        if crm_user is None:
            unmatched.append(f"{item.get('name') or wecom_userid}({wecom_userid})")
        success += 1

    finish_job(
        job,
        success=success,
        fail=0,
        detail={
            "total": len(members),
            # 这些人企微里有、CRM 里没有账号，需要人工建号并分角色
            "unmatched": unmatched,
        },
    )
    return job


# ---- 外部联系人与跟进关系（PRD §8.2）-------------------------------------


async def sync_external_contacts(session: AsyncSession, *, user: CurrentUser) -> WeComSyncJob:
    """同步外部联系人。只落"企微侧事实"，不碰 CRM 客户与联系人。"""
    job = await start_job(session, job_type="external_contact", operator_id=user.id)
    api = wecom_client.get_client()
    contacts = await api.list_all_external_contacts()

    existing = {
        row.external_userid: row
        for row in (await session.execute(select(WeComExternalContact))).scalars().all()
    }
    now = datetime.now(UTC)
    success = 0
    for item in contacts:
        external_userid = str(item.get("external_userid") or "")
        if not external_userid:
            continue
        row = existing.get(external_userid)
        if row is None:
            row = WeComExternalContact(external_userid=external_userid)
            session.add(row)
        row.name = item.get("name")
        row.type = str(item.get("type")) if item.get("type") is not None else None
        row.avatar = item.get("avatar")
        row.corp_name = item.get("corp_name")
        row.gender = str(item.get("gender")) if item.get("gender") is not None else None
        row.last_sync_at = now
        row.raw_data = item
        success += 1

    finish_job(job, success=success, fail=0, detail={"total": len(contacts)})
    return job


async def sync_follow_relations(session: AsyncSession, *, user: CurrentUser) -> WeComSyncJob:
    """同步「谁加了谁」。同一个外部联系人可能有多个跟进成员，逐条 upsert。"""
    job = await start_job(session, job_type="follow_relation", operator_id=user.id)
    api = wecom_client.get_client()

    contacts = (
        await session.execute(select(WeComExternalContact))
    ).scalars().all()

    existing = {
        (row.external_contact_id, row.wecom_userid): row
        for row in (await session.execute(select(WeComFollowRelationship))).scalars().all()
    }
    now = datetime.now(UTC)
    success = 0
    fail = 0
    errors: list[str] = []
    for contact in contacts:
        try:
            follow_users = await api.get_follow_users(contact.external_userid)
        except Exception as error:  # 单条失败不影响整批
            fail += 1
            errors.append(f"{contact.external_userid}: {str(error)[:80]}")
            continue
        for item in follow_users:
            wecom_userid = str(item.get("userid") or "")
            if not wecom_userid:
                continue
            row = existing.get((contact.id, wecom_userid))
            if row is None:
                row = WeComFollowRelationship(
                    external_contact_id=contact.id, wecom_userid=wecom_userid
                )
                session.add(row)
            row.add_time = _to_datetime(item.get("createtime"))
            row.add_way = str(item.get("add_way")) if item.get("add_way") is not None else None
            row.remark = item.get("remark")
            row.description = item.get("description")
            row.state = item.get("state")
            row.tags_json = item.get("tags") or None
            row.last_sync_at = now
            success += 1

    finish_job(
        job,
        success=success,
        fail=fail,
        error="；".join(errors[:5]) if errors else None,
        detail={"total": len(contacts)},
    )
    return job


# ---- 待归一（PRD §8.3）----------------------------------------------------


async def unbound_contacts(
    session: AsyncSession, *, keyword: str | None, page: int, page_size: int
) -> tuple[list[dict], int]:
    """待归一列表：归一状态仍为 pending 的外部联系人。"""
    stmt = select(WeComExternalContact).where(
        WeComExternalContact.normalize_status == "pending"
    )
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                WeComExternalContact.name.ilike(like),
                WeComExternalContact.corp_name.ilike(like),
            )
        )
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(WeComExternalContact.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    # 每条带上跟进员工，界面要显示"谁加的"
    contact_ids = [row.id for row in rows]
    follows = (
        await session.execute(
            select(WeComFollowRelationship).where(
                WeComFollowRelationship.external_contact_id.in_(contact_ids)
            )
        )
    ).scalars().all() if contact_ids else []
    follow_index: dict[int, list[dict]] = {}
    for item in follows:
        follow_index.setdefault(item.external_contact_id, []).append(
            {
                "wecom_userid": item.wecom_userid,
                "add_time": item.add_time,
                "add_way": item.add_way,
                "remark": item.remark,
                "status": item.status,
            }
        )

    return [
        {
            "id": row.id,
            "external_userid": row.external_userid,
            "name": row.name,
            "corp_name": row.corp_name,
            "avatar": row.avatar,
            "type": row.type,
            "normalize_status": row.normalize_status,
            "normalize_status_label": NORMALIZE_LABEL.get(row.normalize_status, row.normalize_status),
            "last_sync_at": row.last_sync_at,
            "followers": follow_index.get(row.id, []),
        }
        for row in rows
    ], int(total)


async def unbound_candidates(session: AsyncSession, contact_id: int) -> dict:
    """给一条待归一联系人算候选客户。

    复用客户查重那套加权打分（`dedup_scoring` 配置、score/reasons 可解释），
    用「企微备注名 / 企业名 + 跟进员工手机号」当输入：
    企微备注里常直接写着客户联系人名，企业名则匹配客户全称。
    """
    row = await session.get(WeComExternalContact, contact_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "企业微信联系人不存在", 404)

    # 跟进员工的手机号是匹配客户联系人最有效的线索
    follow_mobiles = [
        mobile
        for mobile in (
            await session.execute(
                select(User.mobile)
                .join(WeComUser, WeComUser.user_id == User.id)
                .join(
                    WeComFollowRelationship,
                    WeComFollowRelationship.wecom_userid == WeComUser.wecom_userid,
                )
                .where(
                    WeComFollowRelationship.external_contact_id == contact_id,
                    User.mobile.isnot(None),
                )
            )
        ).scalars().all()
        if mobile
    ]

    # 这里**故意不做数据范围过滤**（不传 owner_ids）：企微外部联系人归一是系统级
    # 匹配动作，要判断"这个新联系人是不是已经存在的某家客户"，只在全库范围内比才有意义；
    # 过滤成某个人的范围会让同一条外部联系人被不同人重复建档。
    # 结果本身不直接对业务员开放：它进"待归一"，由有 wecom:manage 的人确认。
    matches = await find_duplicate_customers(
        session,
        company_name=row.corp_name or row.name,
        mobile=follow_mobiles[0] if follow_mobiles else None,
    )
    return {
        "contact": {
            "id": row.id,
            "external_userid": row.external_userid,
            "name": row.name,
            "corp_name": row.corp_name,
            "avatar": row.avatar,
            "normalize_status": row.normalize_status,
        },
        "candidates": matches,
    }


async def bind_customer(
    session: AsyncSession,
    *,
    user: CurrentUser,
    contact_id: int,
    customer_id: int,
    is_primary: bool | None = None,
) -> dict:
    """把企微外部联系人关联到已有客户：建一条 CRM Contact 并回填关联。"""
    row = await session.get(WeComExternalContact, contact_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "企业微信联系人不存在", 404)
    if row.crm_contact_id:
        raise AppError(ErrorCode.PARAM_ERROR, "这条联系人已经归一过了", 422)

    customer = await customer_service.get_customer_or_404(session, customer_id)
    contact = await create_contact_for_customer(
        session,
        customer_id=customer.id,
        name=row.name or row.external_userid,
        owner_id=customer.owner_id,
        source="企业微信",
        is_primary=is_primary,
    )
    row.crm_contact_id = contact.id
    row.crm_customer_id = customer.id
    row.normalize_status = "bound"
    row.normalized_by = user.id
    row.normalized_at = datetime.now(UTC)
    return {"contact_id": contact.id, "customer_id": customer.id, "customer_name": customer.name}


async def create_customer_from_contact(
    session: AsyncSession,
    *,
    user: CurrentUser,
    contact_id: int,
    payload: dict,
) -> dict:
    """给待归一联系人建新客户，并顺手把这名联系人挂上去。"""
    row = await session.get(WeComExternalContact, contact_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "企业微信联系人不存在", 404)
    if row.crm_contact_id:
        raise AppError(ErrorCode.PARAM_ERROR, "这条联系人已经归一过了", 422)

    data = dict(payload)
    data.setdefault("source", "企业微信")
    customer = await customer_service.create_customer(session, user, data)
    contact = await create_contact_for_customer(
        session,
        customer_id=customer.id,
        name=row.name or row.external_userid,
        source="企业微信",
        owner_id=customer.owner_id,
    )
    row.crm_contact_id = contact.id
    row.crm_customer_id = customer.id
    row.normalize_status = "created"
    row.normalized_by = user.id
    row.normalized_at = datetime.now(UTC)
    return {
        "customer_id": customer.id,
        "customer_name": customer.name,
        "contact_id": contact.id,
    }


async def ignore_contact(
    session: AsyncSession, *, user: CurrentUser, contact_id: int, reason: str | None = None
) -> WeComExternalContact:
    """暂不处理：留在库里但不再出现在待归一列表（PRD §8.3 的第三个出口）。"""
    row = await session.get(WeComExternalContact, contact_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "企业微信联系人不存在", 404)
    row.normalize_status = "ignored"
    row.normalized_by = user.id
    row.normalized_at = datetime.now(UTC)
    if reason:
        raw = dict(row.raw_data or {})
        raw["ignore_reason"] = reason
        row.raw_data = raw
    return row


# ---- 离职继承（PRD §8.4）--------------------------------------------------


async def collect_transfer_scope(
    session: AsyncSession, *, handover: User, takeover: User
) -> dict[str, list[dict]]:
    """盘点离职人名下**要交接的东西**，按类别返回逐项清单。

    这是**预览和执行共用的同一份清单**：页面上看到的和实际会动的必须完全一致，
    否则"清单里明明没有、执行时却改了"就成了新的黑箱。

    每项固定带这几个字段，界面照着渲染即可：
      - `kind` 类别、`business_id` 对象 id、`label` 人看的标识；
      - `status` 现状（人话）；
      - `blocked_reason` **不能交接**的原因（能交接则为空）。
    """
    scope: dict[str, list[dict]] = {kind: [] for kind in TRANSFER_KIND_LABEL}

    # ---- 客户：撞单争议未结案的先标出来，它会被冻结，企微那边也不发转接 ----
    customers = (
        await session.execute(
            select(Customer).where(
                Customer.owner_id == handover.id, Customer.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    for customer in customers:
        scope["customer"].append(
            {
                "kind": "customer",
                "business_id": customer.id,
                "label": customer.name,
                "status": "待交接",
                "blocked_reason": (
                    "处于撞单争议中，自动改派已冻结，等主管裁定后再交接"
                    if await duplicate_service.is_disputed(session, customer.id)
                    else None
                ),
            }
        )

    # ---- 商机 ----
    for opportunity in (
        await session.execute(
            select(Opportunity).where(
                Opportunity.owner_id == handover.id, Opportunity.deleted_at.is_(None)
            )
        )
    ).scalars().all():
        scope["opportunity"].append(
            {
                "kind": "opportunity",
                "business_id": opportunity.id,
                "label": opportunity.title,
                "status": "跟进中",
                "blocked_reason": None,
            }
        )

    # ---- 未完成任务（已完成的属于历史记录，不在此列）----
    for task in (
        await session.execute(
            select(Task).where(
                Task.owner_id == handover.id, Task.status.in_(OPEN_TASK_STATUS)
            )
        )
    ).scalars().all():
        scope["task"].append(
            {
                "kind": "task",
                "business_id": task.id,
                "label": task.title,
                "status": "待办" if task.status == "pending" else "进行中",
                "blocked_reason": None,
            }
        )

    # ---- 打样单：**这部分原来整块漏了**（返工单 6.6）----
    # 打样列表和详情的可见性按 `SampleRequest.owner_id` 判（见 sample/service
    # 的 ensure_in_scope）。客户交给新人、打样还挂在离职人名下的话，
    # 接手人打开打样列表根本看不到它，也就没法继续跟——单子成了没人管的孤儿。
    # 只交接**没结束**的打样：驳回作罢、已归档的不该再算"接手人的活"。
    for sample in (
        await session.execute(
            select(SampleRequest).where(
                SampleRequest.owner_id == handover.id,
                SampleRequest.status.in_(SAMPLE_OPEN_STATUSES),
            )
        )
    ).scalars().all():
        status_label = SAMPLE_STATUS_LABEL.get(sample.status, sample.status)
        confirm = sample.confirm_status or "pending"
        scope["sample"].append(
            {
                "kind": "sample",
                "business_id": sample.id,
                "label": f"打样单 #{sample.id}（{status_label}，"
                f"{'客户已确认' if confirm == 'accepted' else '待客户确认'}）",
                "status": status_label,
                "blocked_reason": None,
            }
        )

    # ---- 订单与草稿 ----
    from app.modules.order.model import OrderDraft, SalesOrder

    for order in (
        await session.execute(
            select(SalesOrder).where(
                SalesOrder.owner_id == handover.id, SalesOrder.status != "cancelled"
            )
        )
    ).scalars().all():
        scope["order"].append(
            {
                "kind": "order",
                "business_id": order.id,
                "label": order.order_no,
                "status": "在途",
                "blocked_reason": None,
            }
        )

    for draft in (
        await session.execute(select(OrderDraft).where(OrderDraft.owner_id == handover.id))
    ).scalars().all():
        scope["order_draft"].append(
            {
                "kind": "order_draft",
                "business_id": draft.id,
                "label": f"订单草稿 #{draft.id}",
                "status": "已转订单" if draft.order_id else "起草中",
                "blocked_reason": None,
            }
        )

    # ---- 企微客户关系 ----
    for relation in (
        await session.execute(
            select(WeComFollowRelationship).where(
                WeComFollowRelationship.wecom_userid == (handover.wecom_userid or ""),
                WeComFollowRelationship.status == "active",
            )
        )
    ).scalars().all():
        contact = await session.get(WeComExternalContact, relation.external_contact_id)
        scope["wecom_relation"].append(
            {
                "kind": "wecom_relation",
                "business_id": relation.id,
                "label": (contact.external_userid if contact else None)
                or f"关系 #{relation.id}",
                "status": "跟进中",
                "blocked_reason": None,
                # 私用字段：执行时要知道对应哪个 CRM 客户，好判断它是否被冻结
                "_crm_customer_id": contact.crm_customer_id if contact else None,
            }
        )

    return scope


async def preview_transfer(
    session: AsyncSession, *, handover_user_id: int, takeover_user_id: int
) -> dict:
    """交接清单预览（只读）。**执行前先看这个**，逐项确认接手人。

    返回里 `blocked` 是"这次交接动不了"的项（撞单争议冻结），
    `totals` 是各类别的条数。看清单和执行拿到的是同一份数据。
    """
    handover = await session.get(User, handover_user_id)
    takeover = await session.get(User, takeover_user_id)
    if handover is None or takeover is None:
        raise AppError(ErrorCode.NOT_FOUND, "交接人或接管人不存在", 404)

    scope = await collect_transfer_scope(session, handover=handover, takeover=takeover)
    sections = []
    totals: dict[str, int] = {}
    blocked: list[dict] = []
    for kind, rows in scope.items():
        # 内部用的关联字段不下发
        clean = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
        totals[kind] = len(clean)
        for row in clean:
            if row["blocked_reason"]:
                blocked.append(row)
        if clean:
            sections.append({"kind": kind, "label": TRANSFER_KIND_LABEL[kind], "items": clean})

    return {
        "handover": {"id": handover.id, "name": handover.name},
        "takeover": {"id": takeover.id, "name": takeover.name},
        "sections": sections,
        "totals": totals,
        "total": sum(totals.values()),
        "blocked": blocked,
        # 打样单以前不在清单里，界面上要能看出这次包含了多少
        "sample_count": totals.get("sample", 0),
        "note": (
            "清单与执行读的是同一份数据。撞单争议中的客户会被冻结"
            "（不交接、也不发出企微转接），等主管裁定后单独处理。"
            "只迁移离职人的责任，其他在职同事的待办保留不动。"
            "每一项都可以单独指定接手人，不指定就跟统一接管人。"
        ),
    }


async def transfer_relations(
    session: AsyncSession,
    *,
    user: CurrentUser,
    handover_user_id: int,
    takeover_user_id: int,
    transfer_wecom: bool = True,
    item_assignees: dict[str, int] | None = None,
) -> WeComSyncJob:
    """离职继承：把 handover 名下的一切交给 takeover。

    企微侧：调 `externalcontact/transfer` 交接客户关系（需要外部联系人 secret）。
    CRM 侧按 PRD §8.4 / 文档 :61：
      - 转移「当前负责人」：客户、商机、未完成任务、**未完成打样**、草稿、
        **未取消订单**——接手人必须看得到这些单子，列表是按 `owner_id` 过滤的，
        不动等于交接完没人看得到；
      - 保留：创建人（created_by）、历史跟进、历史报价、审批与审计日志，
        以及 **sales_owner_id（签单归属）**——"交接后保留历史业绩归属"，
        接手人接手的是跟进责任，不是别人已经谈成的业绩；
      - **只迁离职人的责任**（业务方 2026-10-06 定）：客户名下其他在职同事的
        未完成待办原样保留。旧实现把该客户下**所有**负责人的未办事项一起改给
        接手人，等于把在职同事手上的活悄悄挪走了。

    **执行顺序**（返工单 6.7）：先检查、后调外部接口。
    旧实现是"先调企微转接 → 再改 CRM 归属"，而 CRM 那一步可能因为撞单争议
    报错回滚——可**已经发出去的企微转接回滚不了**：客户在微信里看到的服务人员
    已经变了，本地却什么都没留下。现在把所有前置检查放在最前面，
    确认哪些能动、哪些要冻，之后才发外部调用，最后改本地归属。
    """
    job = await start_job(session, job_type="transfer", operator_id=user.id)

    handover = await session.get(User, handover_user_id)
    takeover = await session.get(User, takeover_user_id)
    if handover is None or takeover is None:
        finish_job(job, success=0, fail=1, error="交接人或接管人不存在")
        raise AppError(ErrorCode.NOT_FOUND, "交接人或接管人不存在", 404)
    if handover.id == takeover.id:
        finish_job(job, success=0, fail=1, error="交接人与接管人不能是同一个人")
        raise AppError(ErrorCode.PARAM_ERROR, "交接人与接管人不能是同一个人", 422)
    if takeover.status != "active":
        finish_job(job, success=0, fail=1, error=f"接管人「{takeover.name}」已停用")
        raise AppError(ErrorCode.PARAM_ERROR, f"接管人「{takeover.name}」已停用", 422)

    detail: dict = {
        "handover": handover.name,
        "takeover": takeover.name,
        # 存 id 而不只是名字：重试要按 id 找回这两个账号
        "handover_id": handover.id,
        "takeover_id": takeover.id,
    }

    # 1) 盘点：清单在动手之前就定下来，预览页看到的和这里动的是同一份
    scope = await collect_transfer_scope(session, handover=handover, takeover=takeover)
    frozen_customer_ids = {
        row["business_id"] for row in scope["customer"] if row["blocked_reason"]
    }

    # 1b) **逐项接手人**（业务方 2026-10-06 定："交接清单可以逐项调整"）
    #     默认全部交给 takeover；个别项可以指定别人（比如某个客户本来就该归
    #     另一位同事）。键是 `"{kind}:{business_id}"`，与清单里那一项一一对应。
    overrides: dict[str, int] = {
        key: int(value) for key, value in (item_assignees or {}).items() if value
    }
    assignees: dict[int, User] = {takeover.id: takeover}
    for user_id in set(overrides.values()):
        if user_id in assignees:
            continue
        row_user = await session.get(User, user_id)
        if row_user is None:
            raise AppError(ErrorCode.NOT_FOUND, f"接手人 id={user_id} 不存在", 404)
        if row_user.status != "active":
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"接手人「{row_user.name}」已停用，不能接收交接",
                422,
            )
        assignees[user_id] = row_user

    def _assignee_of(kind: str, business_id: int) -> User:
        """这一项实际交给谁：逐项指定优先，否则跟统一接手人。"""
        return assignees[overrides.get(f"{kind}:{business_id}", takeover.id)]

    # 客户 id → 该项在逐项结果里的行，便于边执行边更新状态
    items: dict[tuple[str, int], WeComTransferItem] = {}
    for kind, rows in scope.items():
        for row in rows:
            target_user = _assignee_of(kind, row["business_id"])
            item = WeComTransferItem(
                job_id=job.id,
                kind=kind,
                business_id=row["business_id"],
                label=row["label"],
                from_owner_id=handover.id,
                to_owner_id=target_user.id,
                from_owner_name=handover.name,
                # 记**实际**接手人而不是统一那个人：逐项调整过之后，
                # 事后要能看出这一项到底给了谁
                to_owner_name=target_user.name,
                # 企微关系没有"CRM 归属"要改（它本身就是外部系统的记录），
                # CRM 侧直接记"不涉及"而不是"待处理" —— 否则它会一直挂在
                # "未完成"清单里，重试也会白跑一趟。
                crm_status="not_applicable" if kind == "wecom_relation" else "pending",
                wecom_status="pending" if kind == "wecom_relation" else "not_applicable",
                attempts=0,
            )
            session.add(item)
            items[(kind, row["business_id"])] = item
    await session.flush()
    detail["item_assignees"] = {
        key: assignees[value].name for key, value in overrides.items()
    }

    def _mark_crm(kind: str, business_id: int, status: str, error: str | None = None) -> None:
        """就地更新某一项的 CRM 侧结果（不存在就忽略，不让记录影响主流程）。"""
        item = items.get((kind, business_id))
        if item is None:
            return
        item.crm_status = status
        item.crm_error = error
        item.attempts = (item.attempts or 0) + 1
        item.updated_at = datetime.now(UTC)

    def _mark_wecom(kind: str, business_id: int, status: str, error: str | None = None) -> None:
        item = items.get((kind, business_id))
        if item is None:
            return
        item.wecom_status = status
        item.wecom_error = error
        item.attempts = (item.attempts or 0) + 1
        item.updated_at = datetime.now(UTC)

    # 冻结的客户：CRM 侧明确标成 frozen（不留成"待处理"，否则重试会一直捞它）
    for row in scope["customer"]:
        if row["blocked_reason"]:
            _mark_crm("customer", row["business_id"], "frozen", row["blocked_reason"])

    # 2) 企微客户关系 —— **只转没被冻结的客户**
    #    这一步是外部调用，发出去就撤不回来，所以放在所有本地检查之后，
    #    并严格按第 1 步算出来的冻结清单过滤：争议客户的客户关系不动。
    if transfer_wecom:
        api = wecom_client.get_client()
        moved = 0
        failed = 0
        for row in scope["wecom_relation"]:
            relation_id = row["business_id"]
            relation = await session.get(WeComFollowRelationship, relation_id)
            contact = (
                await session.get(WeComExternalContact, relation.external_contact_id)
                if relation is not None
                else None
            )
            if relation is None or contact is None:
                _mark_wecom("wecom_relation", relation_id, "skipped", "跟进关系已不存在")
                continue
            if row.get("_crm_customer_id") in frozen_customer_ids:
                _mark_wecom(
                    "wecom_relation",
                    relation_id,
                    "skipped",
                    "对应客户处于撞单争议中，未发出转接",
                )
                continue
            # 这条关系交给谁 —— 可能是逐项指定过的别人，不一定是最初那个接管人
            relation_item = items[("wecom_relation", relation_id)]
            relation_owner = assignees.get(relation_item.to_owner_id) or takeover
            try:
                await api.transfer_customer(
                    external_userid=contact.external_userid,
                    handover_userid=handover.wecom_userid or "",
                    takeover_userid=relation_owner.wecom_userid or "",
                )
            except Exception as error:
                failed += 1
                # 失败原因**整条存下来**（原来截断到 10 条并挤进 JSON，
                # 存进去的那一份本身就是残的，事后想补查都查不到）
                _mark_wecom("wecom_relation", relation_id, "failed", str(error)[:500])
                continue
            relation.status = "transferred"
            moved += 1
            _mark_wecom("wecom_relation", relation_id, "transferred")
            # 接手人名下补一条跟进关系
            duplicate = (
                await session.execute(
                    select(WeComFollowRelationship).where(
                        WeComFollowRelationship.external_contact_id == contact.id,
                        WeComFollowRelationship.wecom_userid
                        == (relation_owner.wecom_userid or ""),
                    )
                )
            ).scalars().first()
            if duplicate is None and relation_owner.wecom_userid:
                session.add(
                    WeComFollowRelationship(
                        external_contact_id=contact.id,
                        wecom_userid=relation_owner.wecom_userid,
                        add_time=datetime.now(UTC),
                        add_way="离职继承",
                        status="active",
                        last_sync_at=datetime.now(UTC),
                    )
                )
        detail["wecom_relations"] = moved
        detail["wecom_failed"] = failed
    else:
        for row in scope["wecom_relation"]:
            _mark_wecom(
                "wecom_relation", row["business_id"], "skipped", "本次未要求转接企微关系"
            )
        detail["wecom_relations"] = 0
        detail["wecom_failed"] = 0

    # 3) CRM 客户负责人（冻结的原样留着，等主管裁定）
    customer_ids = [
        row["business_id"] for row in scope["customer"] if not row["blocked_reason"]
    ]
    for customer_id in customer_ids:
        customer = await session.get(Customer, customer_id)
        if customer is None:
            _mark_crm("customer", customer_id, "skipped", "客户已不存在")
            continue
        owner = items[("customer", customer_id)].to_owner_id
        owner_name = items[("customer", customer_id)].to_owner_name
        await customer_service.transfer_customer(
            session, user, customer, owner,
            f"离职继承：{handover.name} → {owner_name}",
            # 离职交接是**系统自动改派**：撞单争议未结案时冻结，
            # 否则一次交接就把争议客户的归属改成了既成事实（文档 §11.5 :279）
            automatic=True,
            # **只迁离职人的待办**：客户名下在职同事的活留着（业务方 2026-10-06 定）
            only_from_owner_id=handover.id,
        )
        _mark_crm("customer", customer_id, "moved")
    detail["customers"] = len(customer_ids)
    detail["customers_frozen"] = len(frozen_customer_ids)

    # 4) 商机负责人（只动 owner_id，created_by 保持原样）
    for row in scope["opportunity"]:
        opportunity = await session.get(Opportunity, row["business_id"])
        if opportunity is None or opportunity.deleted_at is not None:
            _mark_crm("opportunity", row["business_id"], "skipped", "商机已不存在")
            continue
        opportunity.owner_id = items[("opportunity", row["business_id"])].to_owner_id
        _mark_crm("opportunity", row["business_id"], "moved")
    detail["opportunities"] = len(scope["opportunity"])

    # 5) 未完成任务（`status in OPEN_TASK_STATUS` 已在盘点时过滤）
    for row in scope["task"]:
        task = await session.get(Task, row["business_id"])
        if task is None:
            _mark_crm("task", row["business_id"], "skipped", "任务已不存在")
            continue
        task.owner_id = items[("task", row["business_id"])].to_owner_id
        _mark_crm("task", row["business_id"], "moved")
    detail["tasks"] = len(scope["task"])

    # 6) 打样单（返工单 6.6 补的整块）
    #    只动 `owner_id`：打样列表/详情就是按它判可见性的，不动等于
    #    接手人打开列表看不到这张单。产品、图纸、制作依据、确认记录都不动。
    for row in scope["sample"]:
        sample = await session.get(SampleRequest, row["business_id"])
        if sample is None:
            _mark_crm("sample", row["business_id"], "skipped", "打样单已不存在")
            continue
        sample.owner_id = items[("sample", row["business_id"])].to_owner_id
        _mark_crm("sample", row["business_id"], "moved")
    detail["samples"] = len(scope["sample"])

    # 7) 订单草稿 + 它生成的对外单据
    from app.modules.bizdoc.model import BizDoc
    from app.modules.order.model import OrderDraft, SalesOrder

    for row in scope["order_draft"]:
        draft = await session.get(OrderDraft, row["business_id"])
        if draft is None:
            _mark_crm("order_draft", row["business_id"], "skipped", "草稿已不存在")
            continue
        owner = items[("order_draft", row["business_id"])].to_owner_id
        draft.owner_id = owner
        documents = (
            await session.execute(
                select(BizDoc).where(BizDoc.order_draft_id == draft.id)
            )
        ).scalars().all()
        for document in documents:
            document.owner_id = owner
        _mark_crm("order_draft", row["business_id"], "moved")
    detail["order_drafts"] = len(scope["order_draft"])

    # 8) 订单当前负责人（文档 :61「逐项分配接手人」）。
    #    只动 owner_id：接手人要能看到并跟进这些订单（订单列表按 owner_id
    #    过滤，不动等于交接完没人看得到）；sales_owner_id 保持原样，
    #    这些单的业绩仍算签单的人——"交接后保留历史业绩归属"。
    for row in scope["order"]:
        order = await session.get(SalesOrder, row["business_id"])
        if order is None:
            _mark_crm("order", row["business_id"], "skipped", "订单已不存在")
            continue
        order.owner_id = items[("order", row["business_id"])].to_owner_id
        _mark_crm("order", row["business_id"], "moved")
    detail["orders"] = len(scope["order"])

    # 9) 汇总（返工单 6.7：两侧结果分开算，失败明细完整保存不再截断）
    crm_moved = sum(1 for i in items.values() if i.crm_status == "moved")
    crm_failed = sum(1 for i in items.values() if i.crm_status == "failed")
    wecom_transferred = sum(1 for i in items.values() if i.wecom_status == "transferred")
    wecom_failed = sum(1 for i in items.values() if i.wecom_status == "failed")
    frozen = sum(1 for i in items.values() if i.crm_status == "frozen")
    #: 还没办完的项：任一侧处于未完成状态。重试只挑这些。
    #: `frozen` 不算（它是"现在不能做"，要等裁定后重新发起，不是原地重试）。
    pending_items = [
        item
        for item in items.values()
        if item.crm_status in CRM_OPEN_STATUSES or item.wecom_status in WECOM_OPEN_STATUSES
    ]
    failure_messages = [
        f"{TRANSFER_KIND_LABEL.get(item.kind, item.kind)}「{item.label}」："
        f"{item.crm_error or item.wecom_error}"
        for item in items.values()
        if item.crm_status == "failed" or item.wecom_status == "failed"
    ]

    detail.update(
        {
            "crm_moved": crm_moved,
            "crm_failed": crm_failed,
            "wecom_transferred": wecom_transferred,
            "wecom_failed": wecom_failed,
            "frozen": frozen,
            "items_total": len(items),
            "pending_items": len(pending_items),
            # 失败明细**完整**列出：12 条就报 12 条
            "failures": failure_messages,
            "failures_count": len(failure_messages),
        }
    )

    finish_job(
        job,
        success=crm_moved + wecom_transferred,
        # 这里以前是 `len(detail["wecom_failures"])`，而那个列表**在存的时候就
        # 截断成 10 条了** —— 失败 12 条，报表上只数出 10 条，明细还少 2 条。
        fail=len(failure_messages),
        error="；".join(failure_messages[:5]) or None,
        detail=detail,
    )
    return job


# ---- 交接结果查询与重试（API §10）------------------------------------------


async def list_transfer_items(
    session: AsyncSession, *, job_id: int, page: int = 1, page_size: int = 20,
    kind: str | None = None, pending_only: bool = False,
) -> tuple[list[dict], int]:
    """某次交接的**逐项结果**，真分页。

    界面据此显示"哪些成了、哪些没成、为什么"，失败 12 条就是 12 条，
    可以一页页翻完（原来失败明细在存储阶段就被截断，翻都翻不到）。
    """
    from sqlalchemy import func as _func

    job = await session.get(WeComSyncJob, job_id)
    if job is None or job.job_type != "transfer":
        raise AppError(ErrorCode.NOT_FOUND, "继承任务不存在", 404)

    conditions = [WeComTransferItem.job_id == job_id]
    if kind:
        conditions.append(WeComTransferItem.kind == kind)
    if pending_only:
        conditions.append(
            or_(
                WeComTransferItem.crm_status.in_(CRM_OPEN_STATUSES),
                WeComTransferItem.wecom_status.in_(WECOM_OPEN_STATUSES),
            )
        )

    total = (
        await session.execute(
            select(_func.count(WeComTransferItem.id)).where(*conditions)
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(WeComTransferItem)
            .where(*conditions)
            .order_by(WeComTransferItem.kind, WeComTransferItem.id)
            .offset(max(0, (page - 1) * page_size))
            .limit(page_size)
        )
    ).scalars().all()
    return [serialize_transfer_item(row) for row in rows], int(total)


def serialize_transfer_item(item: WeComTransferItem) -> dict:
    from app.modules.wecom.model import TRANSFER_CRM_STATUS_LABEL, TRANSFER_WECOM_STATUS_LABEL

    return {
        "id": item.id,
        "job_id": item.job_id,
        "kind": item.kind,
        "kind_label": TRANSFER_KIND_LABEL.get(item.kind, item.kind),
        "business_id": item.business_id,
        "label": item.label,
        "from_owner_id": item.from_owner_id,
        "from_owner_name": item.from_owner_name,
        "to_owner_id": item.to_owner_id,
        "to_owner_name": item.to_owner_name,
        "crm_status": item.crm_status,
        "crm_status_label": TRANSFER_CRM_STATUS_LABEL.get(item.crm_status, item.crm_status),
        "crm_error": item.crm_error,
        "wecom_status": item.wecom_status,
        "wecom_status_label": TRANSFER_WECOM_STATUS_LABEL.get(
            item.wecom_status, item.wecom_status
        ),
        "wecom_error": item.wecom_error,
        "attempts": item.attempts,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


async def retry_transfer(
    session: AsyncSession, *, user: CurrentUser, job_id: int
) -> dict:
    """按**逐项状态**重试没办完的项，已完成的一律不碰。

    为什么要按项重试而不是整体重跑：企微的转接是外部调用，
    把已经转出去的关系再发一遍，企微那边会当成重复操作报错，
    运营看到一堆莫名其妙的失败。这里只捞 `pending`/`failed` 的项。
    """
    job = await session.get(WeComSyncJob, job_id)
    if job is None or job.job_type != "transfer":
        raise AppError(ErrorCode.NOT_FOUND, "继承任务不存在", 404)

    detail = job.detail or {}
    handover = await session.get(User, detail.get("handover_id") or 0)
    takeover = await session.get(User, detail.get("takeover_id") or 0)
    if handover is None or takeover is None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "这一版之前的交接记录里没有留存双方账号，无法自动重试。请重新发起一次交接（系统只处理未完成的项）",
            422,
        )

    rows = (
        await session.execute(
            select(WeComTransferItem).where(WeComTransferItem.job_id == job_id)
        )
    ).scalars().all()
    retried = 0

    for item in rows:
        crm_open = item.crm_status in CRM_OPEN_STATUSES
        wecom_open = item.wecom_status in WECOM_OPEN_STATUSES
        if not (crm_open or wecom_open):
            continue
        retried += 1
        # ---- CRM 侧：归属还没改过去 ----
        if crm_open and item.business_id is not None:
            try:
                moved = await _retry_move_crm(
                    session, item=item, handover=handover, takeover=takeover
                )
            except AppError as error:
                item.crm_status = "failed"
                item.crm_error = error.message
            else:
                item.crm_status = "moved" if moved else "skipped"
                item.crm_error = None
            item.attempts = (item.attempts or 0) + 1
            item.updated_at = datetime.now(UTC)

        # ---- 企微侧：关系还没转出去 ----
        if wecom_open and item.kind == "wecom_relation" and item.business_id is not None:
            await _retry_move_wecom(
                session, item=item, handover=handover, takeover=takeover
            )
            item.attempts = (item.attempts or 0) + 1
            item.updated_at = datetime.now(UTC)

    await session.flush()
    remaining = (
        await session.execute(
            select(WeComTransferItem).where(
                WeComTransferItem.job_id == job_id,
                or_(
                    WeComTransferItem.crm_status.in_(CRM_OPEN_STATUSES),
                    WeComTransferItem.wecom_status.in_(WECOM_OPEN_STATUSES),
                ),
            )
        )
    ).scalars().all()
    return {
        "job_id": job_id,
        "retried": retried,
        "remaining": len(remaining),
    }


async def _retry_move_crm(
    session: AsyncSession, *, item: WeComTransferItem, handover: User, takeover: User
) -> bool:
    """重试某一项的 CRM 归属变更。返回是否真的改了。"""
    from app.modules.order.model import OrderDraft, SalesOrder

    # 每一项都按**它自己被记下的**接手人走：清单里可能逐项调整过，
    # 重试时不能一律丢给最初那个接管人
    owner = item.to_owner_id
    if owner is None:
        return False
    if item.kind == "customer":
        customer = await session.get(Customer, item.business_id)
        if customer is None or customer.deleted_at is not None:
            return False
        await customer_service.transfer_customer(
            session, _system_user(handover), customer, owner,
            f"离职继承重试：{handover.name} → {item.to_owner_name or ''}",
            automatic=True,
            only_from_owner_id=handover.id,
        )
        return True
    if item.kind == "opportunity":
        row = await session.get(Opportunity, item.business_id)
        if row is None or row.deleted_at is not None:
            return False
        row.owner_id = owner
        return True
    if item.kind == "task":
        row = await session.get(Task, item.business_id)
        if row is None:
            return False
        row.owner_id = owner
        return True
    if item.kind == "sample":
        row = await session.get(SampleRequest, item.business_id)
        if row is None:
            return False
        row.owner_id = owner
        return True
    if item.kind == "order":
        row = await session.get(SalesOrder, item.business_id)
        if row is None:
            return False
        row.owner_id = owner
        return True
    if item.kind == "order_draft":
        row = await session.get(OrderDraft, item.business_id)
        if row is None:
            return False
        row.owner_id = owner
        return True
    return False


async def _retry_move_wecom(
    session: AsyncSession, *, item: WeComTransferItem, handover: User, takeover: User
) -> bool:
    """重试某一条企微客户关系的转接。已转过的不会再转（调用方按状态筛选）。"""
    relation = await session.get(WeComFollowRelationship, item.business_id)
    if relation is None:
        item.wecom_status = "skipped"
        item.wecom_error = "跟进关系已不存在"
        return False
    if relation.status == "transferred":
        # 上一次其实转成功了、只是没来得及落状态：补上状态，不再发一次调用
        item.wecom_status = "transferred"
        item.wecom_error = None
        return True
    contact = await session.get(WeComExternalContact, relation.external_contact_id)
    if contact is None:
        item.wecom_status = "skipped"
        item.wecom_error = "外部联系人已不存在"
        return False
    # 这条关系交给谁：按该项记下的接手人（可能逐项指定过）
    owner = await session.get(User, item.to_owner_id) if item.to_owner_id else takeover
    if owner is None:
        item.wecom_status = "failed"
        item.wecom_error = "接手人已不存在"
        return False
    api = wecom_client.get_client()
    try:
        await api.transfer_customer(
            external_userid=contact.external_userid,
            handover_userid=handover.wecom_userid or "",
            takeover_userid=owner.wecom_userid or "",
        )
    except Exception as error:
        item.wecom_status = "failed"
        item.wecom_error = str(error)[:500]
        return False
    relation.status = "transferred"
    item.wecom_status = "transferred"
    item.wecom_error = None
    return True


def _system_user(actor: User) -> CurrentUser:
    """把发起交接的人包装成 `CurrentUser`，供只记 `operator_id` 的调用使用。

    只用于归属变更的留痕（`transfer_customer` 只读 `user.id`），
    所以权限与数据范围给空集/全量即可 —— 这里不做任何鉴权判断。
    """
    return CurrentUser(actor, set(), [], "all")


# ---- 同步任务查询（API §10 sync-jobs）------------------------------------


def serialize_job(job: WeComSyncJob) -> dict:
    from app.modules.wecom.model import SYNC_JOB_STATUS, SYNC_JOB_TYPES

    return {
        "id": job.id,
        "job_type": job.job_type,
        "job_type_label": SYNC_JOB_TYPES.get(job.job_type, job.job_type),
        "status": job.status,
        "status_label": SYNC_JOB_STATUS.get(job.status, job.status),
        "operator_id": job.operator_id,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "success_count": job.success_count,
        "fail_count": job.fail_count,
        "error_message": job.error_message,
        "detail": job.detail,
    }


async def readiness(session: AsyncSession) -> dict:
    """企微配置与数据就绪度：界面据此显示"还差什么"，不用瞎猜。"""
    counts = {
        "wecom_users": (
            await session.execute(select(func.count()).select_from(WeComUser))
        ).scalar_one(),
        "external_contacts": (
            await session.execute(select(func.count()).select_from(WeComExternalContact))
        ).scalar_one(),
        "follow_relations": (
            await session.execute(select(func.count()).select_from(WeComFollowRelationship))
        ).scalar_one(),
        "unbound_contacts": (
            await session.execute(
                select(func.count())
                .select_from(WeComExternalContact)
                .where(WeComExternalContact.normalize_status == "pending")
            )
        ).scalar_one(),
        "unmatched_users": (
            await session.execute(
                select(func.count())
                .select_from(WeComUser)
                .where(WeComUser.user_id.is_(None))
            )
        ).scalar_one(),
    }
    from app.core.config import settings

    return {
        "configured": {
            "corp_id": bool(settings.wecom_corp_id),
            "agent_id": bool(settings.wecom_agent_id),
            "contact_secret": bool(settings.wecom_contact_secret),
            "external_contact_secret": bool(settings.wecom_external_contact_secret),
            "callback": bool(settings.wecom_callback_token and settings.wecom_callback_aes_key),
        },
        "can_sync_contact": settings.wecom_contact_ready,
        "can_sync_external": settings.wecom_external_ready,
        "counts": counts,
    }


__all__ = [
    "WeComUser",
    "Contact",
    "bind_customer",
    "create_customer_from_contact",
    "ignore_contact",
    "readiness",
    "serialize_job",
    "sync_departments",
    "sync_external_contacts",
    "sync_follow_relations",
    "sync_users",
    "transfer_relations",
    "unbound_candidates",
    "unbound_contacts",
]
