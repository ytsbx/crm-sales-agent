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
        #: 裁定前的归属（`{客户id: 原负责人id}`），与 resolved_* 配成一对
        "before_owners": case.before_owners or {},
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
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        visible_customers = select(Customer.id).where(
            or_(Customer.owner_id.is_(None), Customer.owner_id.in_(owner_ids))
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
    names = {
        row.id: row.name
        for row in (
            await session.execute(select(Customer).where(Customer.id.in_(ids)))
        ).scalars().all()
    } if ids else {}
    return [serialize_case(case, names) for case in cases], int(total)


async def resolve_case(
    session: AsyncSession,
    *,
    case: CustomerDuplicateCase,
    decision: str,
    owner_id: int | None,
    remark: str | None,
    actor: CurrentUser,
) -> CustomerDuplicateCase:
    """人工裁定。

    四条纪律：
    - 人选择沿用已有客户负责人，或明确指定负责人；代码不按建档时间推导；
    - `keep_both` 只结案、不动归属，也**不合并**：判为两家不同就各留各的；
    - 裁定只改归属，不删数据；真要合并走既有的 /customers/merge（它单独留痕）；
    - **归属变更复用普通转移那条路径**（`customer_service.transfer_customer`）：
      包括目标负责人在职校验、归属历史、以及**未完成待办的迁移**。
      此前这里是手写一遍"改 owner_id + 写历史"，于是裁定之后
      **待办一条都没跟着走** —— 新负责人看不到该做的动作，老负责人还在被
      一个已经不属于他的客户提醒（返工单 6.5）。两处各写一遍必然漂移。
    """
    # **加行锁**后再判状态：两个人同时点"裁定"时，后到的会等前面提交完再读，
    # 读到的就是 `resolved`，于是被下面这条挡下 —— 只有一个能成功。
    # 不加锁的话两个请求都会通过 `status != pending` 检查，各改一遍归属。
    #
    # ⚠️ `populate_existing=True` 不能省：本项目 session 是
    # `expire_on_commit=False`，SQLAlchemy 默认**不会用结果覆盖已加载对象的属性**。
    # 路由那边已经 `session.get()` 过一次这个案件，所以光加锁读到的是库里那一行、
    # 但拿回来的是**内存里的旧对象**（status 还是 pending）——
    # 并发裁定会两个都成功（实测：不加这句时两个请求都返回 200）。
    # 这个坑本项目文档里记过一次（identity map 挡读），这里是它在"锁"场景的翻版。
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
    if decision not in DECISIONS:
        raise AppError(ErrorCode.PARAM_ERROR, f"裁定类型不合法：{decision}", 422)

    # 裁定**前**两条客户的归属：与下面的 `resolved_owner_id`（裁定后）配成一对，
    # 事后回看才答得出"这次裁定把谁从谁手里改到了谁名下"。
    before_owners = {}
    for cid in (case.customer_id, case.candidate_id):
        row = await session.get(Customer, cid)
        before_owners[str(cid)] = row.owner_id if row is not None else None

    if decision in ("assign_existing", "assign_new"):
        if decision == "assign_existing":
            target = await session.get(Customer, case.candidate_id)
            if target is None or target.owner_id is None:
                raise AppError(
                    ErrorCode.PARAM_ERROR, "已有客户没有负责人，无法按它归属", 422
                )
            owner_id = target.owner_id
        elif owner_id is None:
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "裁定归属必须指定负责人", 422)

        # 两条都落到同一个负责人名下：裁定的是"这条生意归谁"，不是改一条留一条。
        # **走普通转移那条路径**，于是在职校验、归属历史、待办迁移三件事
        # 与"手工改负责人"完全一致（返工单 6.5 的核心要求）。
        from app.modules.customer import service as customer_service

        for cid in (case.customer_id, case.candidate_id):
            customer = await session.get(Customer, cid)
            if customer is None:
                continue
            if customer.owner_id != owner_id:
                await customer_service.transfer_customer(
                    session,
                    actor,
                    customer,
                    owner_id,
                    f"撞单裁定 #{case.id}（{DECISION_LABEL[decision]}）",
                )
            elif customer.pool_status != "private":
                # 归属本来就是这个人，但客户还在公海状态：把状态掰回来
                customer.pool_status = "private"

    case.status = "resolved"
    case.decision = decision
    case.resolved_owner_id = owner_id
    case.resolved_by = actor.id
    case.resolved_at = datetime.now(UTC)
    case.remark = remark
    case.before_owners = before_owners
    await session.flush()
    return case
