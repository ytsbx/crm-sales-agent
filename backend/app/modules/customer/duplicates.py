"""撞单裁定（文档 §11.4 验收 20）。

查重打分（`find_duplicate_customers`）与合并都早就有，缺的是中间那一环：
**疑似之后由谁定**。这个模块补的就是它——系统只摆证据，归属由人写。
"""

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.modules.customer.model import (
    DECISION_LABEL,
    Customer,
    CustomerDuplicateCase,
)
from app.modules.user.model import User

DECISIONS = tuple(DECISION_LABEL)
MERGED_DECISION = "merged"
async def is_disputed(session: AsyncSession, customer_id: int) -> bool:
    """该客户是否处于撞单争议中（有未决裁定单）。

    文档 §11.5 :279 给的流程里有一环叫**「争议冻结自动改派」**：
    "谁先建档客户就归谁"不足以处理撞单，历史导入、重名公司、多人协作都会让
    建档时间失真。所以争议期间系统一律**不许自动改这两个客户的归属**——
    改归谁由主管裁定（`resolve_case`），不由自动逻辑先动手造成既成事实。

    注意区分：冻结的是**自动改派**（离职交接、公海回收这类批量/定时动作），
    人工转移仍然允许——人做的决定要留痕，但不能被系统拦住。
    """
    row = (
        await session.execute(
            select(CustomerDuplicateCase.id).where(
                CustomerDuplicateCase.status == "pending",
                or_(
                    CustomerDuplicateCase.customer_id == customer_id,
                    CustomerDuplicateCase.candidate_id == customer_id,
                ),
            ).limit(1)
        )
    ).first()
    return row is not None


def serialize_case(case: CustomerDuplicateCase, names: dict[int, str]) -> dict:
    decision_label = (
        "客户档案已合并" if case.decision == MERGED_DECISION else DECISION_LABEL.get(case.decision or "")
    )
    return {
        "id": case.id,
        "customer_id": case.customer_id,
        "customer_name": names.get(case.customer_id),
        "candidate_id": case.candidate_id,
        "candidate_name": names.get(case.candidate_id),
        "score": float(case.score) if case.score is not None else None,
        "evidence": case.evidence or {},
        "source": case.source,
        "status": case.status,
        "decision": case.decision,
        "decision_label": decision_label,
        "resolved_owner_id": None,
        "resolved_by": case.resolved_by,
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
        #: 客户没有负责人；保留字段兼容旧前端
        "before_owners": {},
        "remark": case.remark,
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }


async def open_cases_for_customer(
    session: AsyncSession, *, customer: Customer, source: str, actor_id: int | None = None
) -> list[CustomerDuplicateCase]:
    """对一条客户跑查重，把疑似逐条开成待裁定单。

    幂等：同一对（新客户, 候选）只留一张未决单，重复导入不会堆出几十条一样的待办
    ——否则裁定页会被同一件事刷屏，人就不看了。
    """
    # 查重打分在 contact_util（文档 §11.5 :269 点名的"疑似重复识别"），复用它
    from app.modules.contact_util import customer_match_mobile, find_duplicate_customers

    matches = await find_duplicate_customers(
        session,
        company_name=customer.name,
        customer_type=customer.customer_type,
        exclude_id=customer.id,
        mobile=await customer_match_mobile(session, customer.id) if customer.customer_type == "个人" else None,
        tax_no=customer.tax_no,
        domain=customer.domain,
        address=customer.address,
    )
    candidate_ids = {match["id"] for match in matches if match.get("id") is not None}
    if not candidate_ids - {customer.id}:
        return []
    # 等待合并完成后复查档案是否仍在用，再创建撞单。
    active_ids = set(await session.scalars(
        select(Customer.id)
        .where(Customer.id.in_(candidate_ids | {customer.id}), Customer.deleted_at.is_(None))
        .order_by(Customer.id).with_for_update(read=True, key_share=True)
    ))
    if customer.id not in active_ids:
        return []
    opened: list[CustomerDuplicateCase] = []
    for match in matches:
        candidate_id = match.get("id")
        if candidate_id not in active_ids or candidate_id == customer.id:
            continue
        existing = (
            await session.execute(
                select(CustomerDuplicateCase).where(
                    or_(
                        (
                            (CustomerDuplicateCase.customer_id == customer.id)
                            & (CustomerDuplicateCase.candidate_id == candidate_id)
                        ),
                        (
                            (CustomerDuplicateCase.customer_id == candidate_id)
                            & (CustomerDuplicateCase.candidate_id == customer.id)
                        ),
                    ),
                    CustomerDuplicateCase.status == "pending",
                )
            )
        ).scalars().first()
        if existing is not None:
            opened.append(existing)
            continue
        case = CustomerDuplicateCase(
            customer_id=customer.id,
            candidate_id=candidate_id,
            score=match.get("score"),
            # 证据存当时的样子：事后回看要能还原"当初凭什么提示"，
            # 而不是拿今天的数据去解释昨天的判断
            evidence={
                "candidate_name": match.get("name"),
                "reasons": match.get("reasons"),
                "snapshot": {
                    "name": customer.name,
                    "tax_no": customer.tax_no,
                    "domain": customer.domain,
                },
            },
            source=source,
            status="pending",
            created_at=datetime.now(UTC),
        )
        # **SAVEPOINT**：并发下两个查重会同时走到这里，
        # 未决唯一索引（`uq_customer_dup_pending_pair`）会让后到的插入失败。
        # 用嵌套事务接住这个冲突，转成"复用已有的那一张"——
        # 而不是让整段导入因为一条重复而回滚（返工单 6.5）。
        try:
            async with session.begin_nested():
                session.add(case)
                await session.flush()
        except IntegrityError:
            existing = (
                await session.execute(
                    select(CustomerDuplicateCase).where(
                        CustomerDuplicateCase.status == "pending",
                        or_(
                            (
                                (CustomerDuplicateCase.customer_id == customer.id)
                                & (CustomerDuplicateCase.candidate_id == candidate_id)
                            ),
                            (
                                (CustomerDuplicateCase.customer_id == candidate_id)
                                & (CustomerDuplicateCase.candidate_id == customer.id)
                            ),
                        ),
                    )
                )
            ).scalars().first()
            if existing is None:
                raise
            opened.append(existing)
            continue
        opened.append(case)
    await session.flush()
    return opened


async def list_cases(
    session: AsyncSession,
    *,
    user: CurrentUser,
    status: str | None = "pending",
    customer_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """待裁定队列，**真分页**（返工单 6.5）。

    原来是 `.limit(min(limit, 300))` 硬顶：第 301 条之后永远看不到，
    而且没有总数，用户以为"一共就这么些"——旧案件实际上再也打不开了。
    现在返回 `(条目, 总数)`，前端自己翻页。
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    conditions = []
    if status:
        conditions.append(CustomerDuplicateCase.status == status)
    if customer_id is not None:
        conditions.append(or_(
            CustomerDuplicateCase.customer_id == customer_id,
            CustomerDuplicateCase.candidate_id == customer_id,
        ))
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        from app.modules.customer.visibility import customer_scope_clause_from

        visible_customers = select(Customer.id).where(
            customer_scope_clause_from(owner_ids)
        )
        conditions.append(CustomerDuplicateCase.customer_id.in_(visible_customers))
        conditions.append(CustomerDuplicateCase.candidate_id.in_(visible_customers))

    total = (
        await session.execute(
            select(func.count()).select_from(CustomerDuplicateCase).where(*conditions)
        )
    ).scalar_one()

    cases = list(
        (
            await session.execute(
                select(CustomerDuplicateCase)
                .where(*conditions)
                .order_by(CustomerDuplicateCase.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    )
    ids = {c.customer_id for c in cases} | {c.candidate_id for c in cases}
    customers = {
        row.id: row
        for row in (
            await session.execute(select(Customer).where(Customer.id.in_(ids)))
        ).scalars().all()
    } if ids else {}
    names = {cid: customer.name for cid, customer in customers.items()}
    return [
        {
            **serialize_case(case, names),
        }
        for case in cases
    ], int(total)


async def resolve_case(
    session: AsyncSession,
    *,
    case: CustomerDuplicateCase,
    decision: str,
    owner_id: int | None,
    remark: str | None,
    actor: CurrentUser,
) -> CustomerDuplicateCase:
    """人工裁定（2026-11-27 起客户没有默认负责人）。

    - `keep_both`：判为两家不同客户，只结案、不动数据；
    - `assign_existing`：判为同一家生意——把两条客户的跟进人**并成一组**
      （各自的人都能看到两边档案），不合并记录、不删数据；
    - `assign_new`：指定一位同事加入两条客户的跟进人（钉住归属人）。
    真正把重复档案合成一条走 `/customers/merge`（单独留痕）。
    """
    from app.modules.customer import service as customer_service

    if case.status != "pending":
        raise AppError(ErrorCode.PARAM_ERROR, "该撞单已裁定过", 422)
    if decision not in DECISIONS:
        raise AppError(ErrorCode.PARAM_ERROR, f"裁定类型不合法：{decision}", 422)

    # 与合并同序：按编号锁客户、复查范围，然后锁撞单并重读状态。
    locked_customers = {}
    for cid in sorted({case.customer_id, case.candidate_id}):
        locked_customers[cid] = await customer_service.get_visible_customer(
            session, actor, cid, for_update=True
        )
    locked = (
        await session.execute(
            select(CustomerDuplicateCase)
            .where(CustomerDuplicateCase.id == case.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if locked is None:
        raise AppError(ErrorCode.NOT_FOUND, "撞单记录不存在", 404)
    case = locked
    if case.status != "pending":
        raise AppError(ErrorCode.PARAM_ERROR, "该撞单已裁定过", 422)

    from app.modules.customer.followers import active_follower_ids, add_followers

    resolved_user_id: int | None = None
    if decision in ("assign_existing", "assign_new"):
        if decision == "assign_existing":
            # 以「已有客户」为基准并入两条客户的跟进人
            base = locked_customers[case.candidate_id]
            target_ids = set(await active_follower_ids(session, base.id))
        else:
            if owner_id is None:
                raise AppError(
                    ErrorCode.REQUIRED_FIELD_MISSING, "裁定归属必须指定负责人", 422
                )
            target = await session.get(User, owner_id)
            if target is None:
                raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={owner_id} 不存在", 404)
            if target.status != "active":
                raise AppError(
                    ErrorCode.PARAM_ERROR, f"「{target.name}」已停用，不能接收客户", 422
                )
            target_ids = {owner_id}
            resolved_user_id = owner_id
        union_ids = set(target_ids)
        for customer in locked_customers.values():
            union_ids.update(await active_follower_ids(session, customer.id))
        for customer in locked_customers.values():
            await add_followers(
                session,
                customer.id,
                sorted(union_ids),
                source="duplicate_resolution",
                created_by=actor.id,
            )
            if customer.pool_status == "public":
                customer.pool_status = "private"

    case.status = "resolved"
    case.decision = decision
    case.resolved_owner_id = resolved_user_id
    case.resolved_by = actor.id
    case.resolved_at = datetime.now(UTC)
    case.remark = remark
    case.before_owners = None
    await session.flush()
    return case
