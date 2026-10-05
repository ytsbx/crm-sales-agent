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
from app.modules.customer import service as customer_service
from app.modules.customer.model import Contact, Customer
from app.modules.opportunity.model import Opportunity
from app.modules.task.model import Task
from app.modules.user.model import Department, User
from app.modules.wecom import client as wecom_client
from app.modules.wecom.model import (
    WeComExternalContact,
    WeComFollowRelationship,
    WeComSyncJob,
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


async def transfer_relations(
    session: AsyncSession,
    *,
    user: CurrentUser,
    handover_user_id: int,
    takeover_user_id: int,
    transfer_wecom: bool = True,
) -> WeComSyncJob:
    """离职继承：把 handover 名下的一切交给 takeover。

    企微侧：调 `externalcontact/transfer` 交接客户关系（需要外部联系人 secret）。
    CRM 侧按 PRD §8.4 / 文档 :61：
      - 转移「当前负责人」：客户、商机、未完成任务、**未取消订单**——
        接手人必须看得到这些单子，订单列表是按 owner_id 过滤的；
      - 保留：创建人（created_by）、历史跟进、历史报价、审批与审计日志，
        以及 **sales_owner_id（签单归属）**——"交接后保留历史业绩归属"，
        接手人接手的是跟进责任，不是别人已经谈成的业绩。
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

    detail: dict = {"handover": handover.name, "takeover": takeover.name}

    # 1) 企微客户关系
    if transfer_wecom:
        api = wecom_client.get_client()
        relations = (
            await session.execute(
                select(WeComFollowRelationship)
                .where(
                    WeComFollowRelationship.wecom_userid == (handover.wecom_userid or ""),
                    WeComFollowRelationship.status == "active",
                )
            )
        ).scalars().all()
        moved = 0
        failures: list[str] = []
        for relation in relations:
            contact = await session.get(WeComExternalContact, relation.external_contact_id)
            if contact is None:
                continue
            try:
                await api.transfer_customer(
                    external_userid=contact.external_userid,
                    handover_userid=handover.wecom_userid or "",
                    takeover_userid=takeover.wecom_userid or "",
                )
            except Exception as error:
                failures.append(f"{contact.external_userid}: {str(error)[:80]}")
                continue
            relation.status = "transferred"
            moved += 1
            # 接管人名下补一条跟进关系
            duplicate = (
                await session.execute(
                    select(WeComFollowRelationship).where(
                        WeComFollowRelationship.external_contact_id == contact.id,
                        WeComFollowRelationship.wecom_userid == (takeover.wecom_userid or ""),
                    )
                )
            ).scalars().first()
            if duplicate is None and takeover.wecom_userid:
                session.add(
                    WeComFollowRelationship(
                        external_contact_id=contact.id,
                        wecom_userid=takeover.wecom_userid,
                        add_time=datetime.now(UTC),
                        add_way="离职继承",
                        status="active",
                        last_sync_at=datetime.now(UTC),
                    )
                )
        detail["wecom_relations"] = moved
        if failures:
            detail["wecom_failures"] = failures[:10]

    # 2) CRM 客户负责人
    customer_ids = [
        int(cid)
        for cid in (
            await session.execute(
                select(Customer.id).where(
                    Customer.owner_id == handover.id, Customer.deleted_at.is_(None)
                )
            )
        ).scalars().all()
    ]
    for customer_id in customer_ids:
        customer = await session.get(Customer, customer_id)
        if customer is not None:
            await customer_service.transfer_customer(
                session, user, customer, takeover.id,
                f"离职继承：{handover.name} → {takeover.name}",
                # 离职交接是**系统自动改派**：撞单争议未结案时冻结，
                # 否则一次交接就把争议客户的归属改成了既成事实（文档 §11.5 :279）
                automatic=True,
            )
    detail["customers"] = len(customer_ids)

    # 3) 商机负责人（只动 owner_id，created_by 保持原样）
    opportunities = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.owner_id == handover.id, Opportunity.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    for opportunity in opportunities:
        opportunity.owner_id = takeover.id
    detail["opportunities"] = len(opportunities)

    # 4) 未完成任务
    tasks = (
        await session.execute(
            select(Task).where(
                Task.owner_id == handover.id, Task.status.in_(OPEN_TASK_STATUS)
            )
        )
    ).scalars().all()
    for task in tasks:
        task.owner_id = takeover.id
    detail["tasks"] = len(tasks)

    # 5) 订单当前负责人（文档 :61「逐项分配接手人」）。
    #    只动 owner_id：接手人要能看到并跟进这些订单（订单列表按 owner_id
    #    过滤，不动等于交接完没人看得到）；sales_owner_id 保持原样，
    #    这些单的业绩仍算签单的人——"交接后保留历史业绩归属"。
    from app.modules.order.model import SalesOrder, OrderDraft
    from app.modules.bizdoc.model import BizDoc

    drafts = list((await session.execute(select(OrderDraft).where(OrderDraft.owner_id == handover.id))).scalars())
    draft_ids = [row.id for row in drafts]
    for draft in drafts:
        draft.owner_id = takeover.id
    if draft_ids:
        documents = list((await session.execute(select(BizDoc).where(BizDoc.order_draft_id.in_(draft_ids)))).scalars())
        for document in documents:
            document.owner_id = takeover.id
    detail["order_drafts"] = len(drafts)

    orders = (
        await session.execute(
            select(SalesOrder).where(
                SalesOrder.owner_id == handover.id,
                SalesOrder.status != "cancelled",
            )
        )
    ).scalars().all()
    for order in orders:
        order.owner_id = takeover.id
    detail["orders"] = len(orders)

    finish_job(
        job,
        success=int(detail.get("wecom_relations", 0))
        + len(customer_ids)
        + len(opportunities)
        + len(tasks)
        + len(orders)
        + len(drafts),
        fail=len(detail.get("wecom_failures", [])),
        error="；".join(detail.get("wecom_failures", [])[:5]) or None,
        detail=detail,
    )
    return job


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
