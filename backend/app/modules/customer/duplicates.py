"""撞单裁定（文档 §11.4 验收 20）。

查重打分（`find_duplicate_customers`）与合并都早就有，缺的是中间那一环：
**疑似之后由谁定**。这个模块补的就是它——系统只摆证据，归属由人写。
"""

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import (
    DECISION_LABEL,
    Customer,
    CustomerDuplicateCase,
)

DECISIONS = tuple(DECISION_LABEL)


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
        "decision_label": DECISION_LABEL.get(case.decision or "", None),
        "resolved_owner_id": case.resolved_owner_id,
        "resolved_by": case.resolved_by,
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
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
    from app.modules.contact_util import find_duplicate_customers

    matches = await find_duplicate_customers(
        session,
        company_name=customer.name,
        # 这几个证据位客户档案上目前没有独立字段（联系人在 contacts 表），
        # 先按"没有"传；名称与域名是建档时最可靠的两项
        mobile=None,
        tax_no=customer.tax_no,
        domain=customer.domain,
    )
    opened: list[CustomerDuplicateCase] = []
    for match in matches:
        candidate_id = match.get("id")
        if candidate_id is None or candidate_id == customer.id:
            continue
        existing = (
            await session.execute(
                select(CustomerDuplicateCase).where(
                    CustomerDuplicateCase.customer_id == customer.id,
                    CustomerDuplicateCase.candidate_id == candidate_id,
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
        session.add(case)
        opened.append(case)
    await session.flush()
    return opened


async def list_cases(
    session: AsyncSession, *, status: str | None = "pending", limit: int = 100
) -> list[dict]:
    stmt = (
        select(CustomerDuplicateCase)
        .order_by(CustomerDuplicateCase.id.desc())
        .limit(max(1, min(limit, 300)))
    )
    if status:
        stmt = stmt.where(CustomerDuplicateCase.status == status)
    cases = list((await session.execute(stmt)).scalars().all())
    ids = {c.customer_id for c in cases} | {c.candidate_id for c in cases}
    names = {
        row.id: row.name
        for row in (
            await session.execute(select(Customer).where(Customer.id.in_(ids)))
        ).scalars().all()
    } if ids else {}
    return [serialize_case(case, names) for case in cases]


async def resolve_case(
    session: AsyncSession,
    *,
    case: CustomerDuplicateCase,
    decision: str,
    owner_id: int | None,
    remark: str | None,
    actor_id: int,
) -> CustomerDuplicateCase:
    """人工裁定。

    三条纪律：
    - 归属**必须由人指定**（`owner_id`），代码不按"谁先建档"推导——
      那等于把抢单结果交给数据库时间戳；
    - `keep_both` 只结案、不动归属，也**不合并**：判为两家不同就各留各的；
    - 裁定只改归属，不删数据；真要合并走既有的 /customers/merge（它单独留痕）。
    """
    if case.status != "pending":
        raise AppError(ErrorCode.PARAM_ERROR, "该撞单已裁定过", 422)
    if decision not in DECISIONS:
        raise AppError(ErrorCode.PARAM_ERROR, f"裁定类型不合法：{decision}", 422)

    if decision in ("assign_existing", "assign_new"):
        if owner_id is None:
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "裁定归属必须指定负责人", 422)
        if decision == "assign_existing":
            target = await session.get(Customer, case.candidate_id)
            if target is None or target.owner_id is None:
                raise AppError(
                    ErrorCode.PARAM_ERROR, "已有客户没有负责人，无法按它归属", 422
                )
            owner_id = target.owner_id
        # 两条都落到同一个负责人名下：裁定的是"这条生意归谁"，不是改一条留一条
        for cid in (case.customer_id, case.candidate_id):
            customer = await session.get(Customer, cid)
            if customer is not None and customer.owner_id != owner_id:
                from app.modules.customer.model import CustomerOwnerHistory

                session.add(
                    CustomerOwnerHistory(
                        customer_id=cid,
                        old_owner_id=customer.owner_id,
                        new_owner_id=owner_id,
                        reason=f"撞单裁定 #{case.id}（{DECISION_LABEL[decision]}）",
                        operator_id=actor_id,
                    )
                )
                customer.owner_id = owner_id

    case.status = "resolved"
    case.decision = decision
    case.resolved_owner_id = owner_id
    case.resolved_by = actor_id
    case.resolved_at = datetime.now(UTC)
    case.remark = remark
    await session.flush()
    return case
