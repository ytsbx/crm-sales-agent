"""系统设置与业务规则接口。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_current_user, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data
from app.modules.settings import service as svc
from app.modules.settings.model import (
    DictionaryItem,
    NumberingRule,
    NumberSequence,
    PublicPoolRule,
    SystemSetting,
    TaskRule,
)
from app.modules.settings.numbering import RESET_PERIODS, period_key, serialize_rule
from app.modules.settings.schema import (
    CustomerLevelCreate,
    CustomerLevelUpdate,
    DictionaryItemCreate,
    DictionaryItemUpdate,
    NumberingRuleCreate,
    NumberingRuleUpdate,
    PublicPoolRuleInput,
    PublicPoolRuleUpdate,
    SettingInput,
    TaskRuleInput,
    TaskRuleUpdate,
)

router = APIRouter(tags=["Settings"])


@router.get("/meta/config")
async def public_config(
    _: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """给所有登录用户读的少量配置。

    用途：决定界面"要不要显示外贸相关的东西"。
    trade_mode = domestic 时，报价币种、汇率、退税率一律不出现——
    能力留在后端，但国内用户看不到多余字段。
    """
    return ok(
        {
            "trade_mode": (await svc.get_setting(session, "trade_mode")).get("mode", "domestic"),
            "default_currency": (await svc.get_setting(session, "default_currency")).get(
                "code", "CNY"
            ),
        }
    )


def serialize_setting(row: SystemSetting) -> dict:
    return {
        "id": row.id,
        "key": row.key,
        "value": row.value,
        "description": row.description,
        "updated_at": row.updated_at,
        #: 这条是**代码里的开发默认值**，系统里还没改过（返修单第六批追加口径 1：
        #: 页面要标明"开发默认值"，不能让人以为已经是确认过的正式规则）
        "is_default": False,
    }


def serialize_pool_rule(row: PublicPoolRule) -> dict:
    return {
        "id": row.id,
        "level": row.level,
        "days": row.days,
        "enabled": row.enabled,
        "remark": row.remark,
    }


def serialize_task_rule(row: TaskRule) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "trigger_type": row.trigger_type,
        "trigger_config": row.trigger_config,
        "action_config": row.action_config,
        "status": row.status,
    }


@router.get("/settings")
async def list_settings(
    _: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(select(SystemSetting).order_by(SystemSetting.key.asc()))
    ).scalars().all()
    items = [serialize_setting(row) for row in rows]
    # 代码默认值里配了、但库里还没落行的项一并返回（`is_default=True`）。
    # 不补这一步，"回收预告期 7 天"这类**开箱默认值**在界面上根本不会出现，
    # 管理员看得见却改不了来源，也就无从判断当前到底按哪套在跑。
    known = {row.key for row in rows}
    for key, value in svc.DEFAULT_SETTINGS.items():
        if key in known:
            continue
        items.append(
            {
                "id": None,
                "key": key,
                "value": value,
                "description": "开发默认值（系统里还没改过）",
                "updated_at": None,
                "is_default": True,
            }
        )
    items.sort(key=lambda item: item["key"])
    return ok(items)


@router.patch("/settings")
async def upsert_setting(
    payload: SettingInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """保存一条系统配置。

    - **校验取值**（追加口径 1 + 返修单 R14）：天数类必须是**严格整数**
      —— 小数（1.9）与布尔（true）一律 422，不再被 `int()` 悄悄截成整数；
      权限类必须真实存在；
    - **审计记下修改前后值**（追加口径 1 要求"保存修改人、修改时间及修改前后值"）：
      此前只记 after，"这个参数被谁从多少改到了多少"查不出来；
    - **改天数会连带重算还没结案的候选**（主人 2026-10-06 定的口径）：
      改「预告期 / 暂缓期」时，把所有 `pending` / `deferred` 的候选按新天数
      重算到期时间（已结案的不动）。原设计是"只影响新候选"，本次按要求
      改成一起重算，免得库里同时跑着两套天数。重算了几条一并记进审计。
    """
    row = (
        await session.execute(select(SystemSetting).where(SystemSetting.key == payload.key))
    ).scalar_one_or_none()
    before_value = row.value if row is not None else None
    await svc.validate_setting_value(session, payload.key, payload.value)

    # 天数类：把值**归一化成整数再落库**（返修单 R14）。
    # 校验放行 "13" 这种数字串，但直接存会把字符串写进 JSONB —— 于是
    # "保存值 / 展示值 / 执行值"三者形态不一致（执行时 int("13") 没问题，
    # 但下次读出来是字符串，别处一比较就出岔子）。这里统一成整数走到底。
    stored_value = payload.value
    days: int | None = None
    if payload.key in svc.SETTING_NUMBER_RULES:
        field, low, high, label = svc.SETTING_NUMBER_RULES[payload.key]
        days = svc._strict_int((payload.value or {}).get(field), label, low, high)
        stored_value = {**(payload.value or {}), field: days}

    if row is None:
        row = SystemSetting(key=payload.key, description=payload.description)
        session.add(row)
    row.value = stored_value
    if payload.description:
        row.description = payload.description
    row.updated_by = user.id
    await session.flush()

    # 改「预告期 / 暂缓期」→ 连带重算还在等的候选（返修单追加口径 1，主人 2026-10-06）
    recomputed = 0
    if days is not None:
        recomputed = await svc.recompute_open_candidates(
            session, key=payload.key, days=days
        )

    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="setting",
        business_id=row.id,
        before={"key": payload.key, "value": before_value},
        after={"key": payload.key, "value": stored_value, "recomputed_candidates": recomputed},
        ip=client_ip(request),
    )
    await session.commit()
    message = "已保存"
    if recomputed:
        message = f"已保存；同步重算了 {recomputed} 条还没结案的候选到期时间"
    return ok(serialize_setting(row), message)


@router.get("/public-pool/rules")
async def list_pool_rules(
    _: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(select(PublicPoolRule).order_by(PublicPoolRule.level.asc()))
    ).scalars().all()
    return ok([serialize_pool_rule(row) for row in rows])


@router.post("/public-pool/rules")
async def create_pool_rule(
    payload: PublicPoolRuleInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = PublicPoolRule(**payload.model_dump())
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="public_pool_rule",
        business_id=row.id,
        after=serialize_pool_rule(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_pool_rule(row), "规则已创建")


@router.patch("/public-pool/rules/{rule_id}")
async def update_pool_rule(
    rule_id: int,
    payload: PublicPoolRuleUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(PublicPoolRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "规则不存在", 404)
    before = serialize_pool_rule(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="public_pool_rule",
        business_id=row.id,
        before=before,
        after=serialize_pool_rule(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_pool_rule(row), "已保存")


@router.post("/public-pool/run-recycle")
async def run_recycle(
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """立即执行一次**公海回收扫描**（生成预告，不直接回收）。

    ⚠️ 行为与改前不同（返工单 6.3）：老实现扫到就直接清空负责人、当场进公海，
    不可逆。现在只**提名**，等主管在 `GET /public-pool/recycle-candidates` 里复核，
    批准时才执行（且执行前会再检查一遍有没有新的履约事项）。
    正式环境由定时任务调用。审计写在 service 内部（它自己 commit）。
    """
    result = await svc.run_public_pool_recycle(session, user.id)
    return ok(
        result,
        f"本轮提名 {result['nominated_count']} 个待复核客户"
        f"（另有 {result['protected_count']} 个在履约中、已豁免）",
    )


@router.get("/public-pool/recycle-candidates")
async def list_recycle_candidates(
    status: str | None = "pending",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(require_permission("customer:pool_review")),
    session: AsyncSession = Depends(get_db),
):
    """回收候选（预告）列表。主管在这里逐条或批量复核。

    需要 `customer:pool_review` —— **独立的业务权限**，不再复用 `settings:manage`
    （第六批审查第 7 条：默认销售主管没有系统设置权限，"主管逐条或批量批准"
    实际上打不通；而给主管开整个系统设置权限又会顺带放开全公司数据）。
    列表同时**按客户数据范围过滤**，只给得到自己管理范围内的候选。
    """
    items, total = await svc.list_candidates(
        session, user=user, status=status, page=page, page_size=page_size
    )
    return ok(page_data(items, total, page, page_size))


class CandidateDecision(BaseModel):
    """复核决定：approve 执行回收 / reject 驳回 / defer 暂缓。"""

    decision: str
    #: 驳回、暂缓、例外执行都要写理由（例外执行时**必填**，见 service）
    note: str | None = None
    #: **提前回收**（返修单第六批第 8 条）：预告期 / 暂缓等待期还没满就要求回收。
    #: 单独一个开关，不能把普通批准当成提前执行 —— 必须填原因，会单独记审计。
    early: bool = False


class BatchCandidateDecision(CandidateDecision):
    """批量复核的请求体：**候选 id 与决定放在同一个 body 里**。

    返修单第六批第 10 条：改之前前端把 id 数组塞进请求体、把决定挂到地址栏，
    而后端正好相反（id 走查询参数、决定走请求体），两边谁也调不通。
    统一成"全部走请求体"一个形状：参数在哪一栏不用猜，`note` 也才有地方放。
    """

    candidate_ids: list[int]


@router.post("/public-pool/recycle-candidates/{candidate_id}/decide")
async def decide_recycle_candidate(
    candidate_id: int,
    payload: CandidateDecision,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:pool_review")),
    session: AsyncSession = Depends(get_db),
):
    """复核一条回收候选。

    **批准执行前会重新检查**：预告发出之后客户如果又有了新跟进、新报价、
    新订单、新回款，会被拦下（要破例必须填原因，会记进审计）。

    单条操作也要**校验数据范围**（第六批审查第 7 条）：列表过滤只解决"看不见"，
    直接拿 id 调接口仍然要拦在本团队范围内。
    """
    from app.modules.settings.model import PublicPoolRecycleCandidate

    row = await session.get(PublicPoolRecycleCandidate, candidate_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "该回收候选不存在", 404)
    await svc.assert_candidate_in_scope(session, user, row)
    result = await svc.decide_candidate(
        session,
        candidate=row,
        decision=payload.decision,
        operator_id=user.id,
        note=payload.note,
        source="WEB",
        allow_early=payload.early,
    )
    return ok(result, f"已{result['status_label']}")


@router.post("/public-pool/recycle-candidates/batch-decide")
async def batch_decide_recycle_candidates(
    payload: BatchCandidateDecision,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:pool_review")),
    session: AsyncSession = Depends(get_db),
):
    """批量复核（返工单 6.3 第 3 条）。

    请求体一次带齐 `candidate_ids` + `decision`（+ `note`/`early`）——
    之前 id 走查询参数、决定走请求体，跟前端的传法正好反着，从来没能调通
    （返修单第六批第 10 条）。

    **逐条处理、逐条报结果**：某一条因为"还没到可回收时间""预告后又有了新履约事项"
    被拦下时，不影响其余的 —— 而且失败的那条**不会被执行**，会原样留在待复核里。

    每条都**校验数据范围**（第六批审查第 7 条）：越界的那条单独记为失败，
    其余照常处理，不会因为一条越界把整批拖停。
    """
    from app.modules.settings.model import PublicPoolRecycleCandidate

    candidate_ids = payload.candidate_ids
    if not candidate_ids:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请选择要处理的候选", 422)
    done: list[dict] = []
    failed: list[dict] = []
    for cid in dict.fromkeys(candidate_ids):
        row = await session.get(PublicPoolRecycleCandidate, cid)
        if row is None:
            failed.append({"candidate_id": cid, "reason": "候选不存在", "code": 40401})
            continue
        try:
            await svc.assert_candidate_in_scope(session, user, row)
            result = await svc.decide_candidate(
                session,
                candidate=row,
                decision=payload.decision,
                operator_id=user.id,
                note=payload.note,
                source="WEB",
                allow_early=payload.early,
            )
        except AppError as error:
            failed.append(
                {"candidate_id": cid, "reason": error.message, "code": error.code}
            )
            continue
        done.append({"candidate_id": cid, "status": result["status"]})
    return ok(
        {"done": done, "failed": failed},
        f"已处理 {len(done)} 条" + (f"，{len(failed)} 条未处理" if failed else ""),
    )


@router.post("/public-pool/recycle-candidates/{candidate_id}/restore")
async def restore_recycle_candidate(
    candidate_id: int,
    payload: dict,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:pool_review")),
    session: AsyncSession = Depends(get_db),
):
    """**恢复**：把被回收的客户还给原负责人。

    谁能恢复是配置项（`pool_recycle_restore_permission`，默认 `customer:assign`）——
    "谁能把回收掉的客户还回去"是管理口径，不同公司不一样，不该写死。
    **这个配置此前只在默认值表里躺着、从未参与授权**（第六批审查追加口径 2），
    现在真正生效：原负责人的主管能恢复本团队的客户，管理员不受限，
    普通业务员不能恢复。

    还要**校验客户数据范围** —— 客户进了公海虽然人人可见，但不能因此
    让任意主管把别人团队的客户捞回来。
    客户已经被别人合法领取时**不静默覆盖**，会报冲突并记下。
    """
    from app.modules.settings.model import PublicPoolRecycleCandidate

    row = await session.get(PublicPoolRecycleCandidate, candidate_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "该回收候选不存在", 404)
    await svc.assert_candidate_in_scope(session, user, row)

    # 恢复权限按配置项判断（默认 customer:assign）。管理员默认放行，
    # 与 require_permission 的口径保持一致。
    restore_perm = await svc.get_text(
        session, "pool_recycle_restore_permission", "text", "customer:assign"
    )
    if "admin" not in user.roles and not user.has(restore_perm):
        raise AppError(
            ErrorCode.FORBIDDEN,
            f"恢复已回收的客户需要权限：{restore_perm}",
            403,
        )
    result = await svc.restore_candidate(
        session,
        candidate=row,
        operator_id=user.id,
        note=payload.get("note"),
        source="WEB",
    )
    return ok(result, "客户已恢复给原负责人")


@router.get("/task-rules")
async def list_task_rules(
    _: CurrentUser = Depends(require_permission("task:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (await session.execute(select(TaskRule).order_by(TaskRule.id.asc()))).scalars().all()
    return ok([serialize_task_rule(row) for row in rows])


@router.post("/task-rules")
async def create_task_rule(
    payload: TaskRuleInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    # 不能写成 TaskRule(**payload.model_dump(), status="active")：
    # TaskRuleInput 自带 status 字段，model_dump() 里已经有它，
    # 再显式传一次会 TypeError: got multiple values for keyword argument 'status'，
    # 直接 500。这个接口此前一直是坏的（没有用例覆盖到）。
    data = payload.model_dump()
    data.setdefault("status", "active")
    if data.get("status") is None:
        data["status"] = "active"
    row = TaskRule(**data)
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="task_rule",
        business_id=row.id,
        after=serialize_task_rule(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_task_rule(row), "规则已创建")


@router.patch("/task-rules/{rule_id}")
async def update_task_rule(
    rule_id: int,
    payload: TaskRuleUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(TaskRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "规则不存在", 404)
    before = serialize_task_rule(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="task_rule",
        business_id=row.id,
        before=before,
        after=serialize_task_rule(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_task_rule(row), "已保存")


@router.delete("/task-rules/{rule_id}")
async def delete_task_rule(
    rule_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """删除自动任务规则（03-API §25）。

    规则是配置数据，删掉不会影响已经生成的任务 ——
    `tasks.source_rule_id` 只做溯源，不做外键级联，
    所以这里可以安全地真删，历史任务仍能看出"当初是哪条规则生成的"。
    """
    row = await session.get(TaskRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "规则不存在", 404)
    before = serialize_task_rule(row)
    await session.delete(row)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="task_rule",
        business_id=rule_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "规则已删除，已生成的任务不受影响")


@router.post("/tasks/run-auto-rules")
async def run_task_rules(
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """立即按规则生成一次自动任务。审计同样写在 service 内部。"""
    result = await svc.run_auto_tasks(session, user.id)
    return ok(result, f"已生成 {result['created_count']} 条自动任务")


# ---- 编号规则（03-API §36，PRD §2.6 系统管理员能力）-----------------------


async def _rule_with_current(session: AsyncSession, rule: NumberingRule) -> dict:
    key = period_key(rule.reset_period, datetime.now(UTC))
    current = (
        await session.execute(
            select(NumberSequence.current_no).where(
                NumberSequence.rule_code == rule.code,
                NumberSequence.period_key == key,
            )
        )
    ).scalar_one_or_none()
    return serialize_rule(rule, key, int(current) if current is not None else None)


@router.get("/numbering-rules")
async def list_numbering_rules(
    _: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """编号规则列表，附带"下一个号长什么样"的预览。"""
    rows = (
        await session.execute(select(NumberingRule).order_by(NumberingRule.id.asc()))
    ).scalars().all()
    return ok([await _rule_with_current(session, row) for row in rows])


@router.post("/numbering-rules")
async def create_numbering_rule(
    payload: NumberingRuleCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    if payload.reset_period not in RESET_PERIODS:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"重置周期只能是 {list(RESET_PERIODS)} 之一",
            422,
        )
    if payload.seq_length < 1 or payload.seq_length > 12:
        raise AppError(ErrorCode.PARAM_ERROR, "流水位数应在 1~12 之间", 422)
    existing = (
        await session.execute(
            select(NumberingRule).where(NumberingRule.code == payload.code)
        )
    ).scalars().first()
    if existing is not None:
        raise AppError(ErrorCode.DUPLICATE, f"编号规则 {payload.code} 已存在", 409)

    row = NumberingRule(**payload.model_dump())
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="numbering_rule",
        business_id=row.id,
        after=await _rule_with_current(session, row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await _rule_with_current(session, row), "规则已创建")


@router.patch("/numbering-rules/{rule_id}")
async def update_numbering_rule(
    rule_id: int,
    payload: NumberingRuleUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改编号规则。

    已经发出的单号**不会**被改写（历史数据保持原样），
    改规则只影响之后新生成的号 —— 这是刻意的：
    改一条规则就把历史单号一起改掉，对账时会灾难性。
    """
    row = await session.get(NumberingRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "编号规则不存在", 404)

    data = payload.model_dump(exclude_unset=True)
    if data.get("reset_period") is not None and data["reset_period"] not in RESET_PERIODS:
        raise AppError(
            ErrorCode.PARAM_ERROR, f"重置周期只能是 {list(RESET_PERIODS)} 之一", 422
        )
    if data.get("seq_length") is not None and not 1 <= data["seq_length"] <= 12:
        raise AppError(ErrorCode.PARAM_ERROR, "流水位数应在 1~12 之间", 422)

    before = await _rule_with_current(session, row)
    for field, value in data.items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="numbering_rule",
        business_id=row.id,
        before=before,
        after=await _rule_with_current(session, row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await _rule_with_current(session, row), "已保存，仅影响之后新生成的单号")


# ---- 字典（03-API §36）---------------------------------------------------


def serialize_dictionary(row: DictionaryItem) -> dict:
    return {
        "id": row.id,
        "type": row.type,
        "code": row.code,
        "label": row.label,
        "sort_no": row.sort_no,
        "enabled": row.enabled,
        "remark": row.remark,
    }


@router.get("/dictionaries")
async def list_dictionaries(
    type: str | None = None,
    enabled_only: bool = False,
    _: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """字典列表。不传 type 返回全部；界面下拉传 type + enabled_only=true。

    权限只要求登录：这些是下拉选项数据，业务员建单时也要读。
    """
    stmt = select(DictionaryItem)
    if type:
        stmt = stmt.where(DictionaryItem.type == type)
    if enabled_only:
        stmt = stmt.where(DictionaryItem.enabled.is_(True))
    rows = (
        await session.execute(stmt.order_by(DictionaryItem.type, DictionaryItem.sort_no, DictionaryItem.id))
    ).scalars().all()
    return ok([serialize_dictionary(row) for row in rows])


@router.post("/dictionaries")
async def create_dictionary(
    payload: DictionaryItemCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    existing = (
        await session.execute(
            select(DictionaryItem).where(
                DictionaryItem.type == payload.type, DictionaryItem.code == payload.code
            )
        )
    ).scalars().first()
    if existing is not None:
        raise AppError(
            ErrorCode.DUPLICATE, f"{payload.type} 下已有编码 {payload.code}", 409
        )
    row = DictionaryItem(**payload.model_dump())
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="dictionary",
        business_id=row.id,
        after=serialize_dictionary(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_dictionary(row), "字典项已创建")


@router.patch("/dictionaries/{item_id}")
async def update_dictionary(
    item_id: int,
    payload: DictionaryItemUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(DictionaryItem, item_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "字典项不存在", 404)
    before = serialize_dictionary(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="dictionary",
        business_id=row.id,
        before=before,
        after=serialize_dictionary(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_dictionary(row), "已保存")


# ---- 客户等级（03-API §36）-----------------------------------------------


CUSTOMER_LEVEL_TYPE = "customer_level"


@router.get("/customer-levels")
async def list_customer_levels(
    enabled_only: bool = False,
    _: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(DictionaryItem).where(DictionaryItem.type == CUSTOMER_LEVEL_TYPE)
    if enabled_only:
        stmt = stmt.where(DictionaryItem.enabled.is_(True))
    rows = (
        await session.execute(stmt.order_by(DictionaryItem.sort_no, DictionaryItem.id))
    ).scalars().all()
    return ok([serialize_dictionary(row) for row in rows])


@router.post("/customer-levels")
async def create_customer_level(
    payload: CustomerLevelCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    existing = (
        await session.execute(
            select(DictionaryItem).where(
                DictionaryItem.type == CUSTOMER_LEVEL_TYPE,
                DictionaryItem.code == payload.code,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise AppError(ErrorCode.DUPLICATE, f"客户等级 {payload.code} 已存在", 409)
    row = DictionaryItem(
        type=CUSTOMER_LEVEL_TYPE,
        code=payload.code,
        label=payload.name,
        sort_no=payload.sort_no,
        enabled=payload.enabled,
        remark=payload.remark,
    )
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="customer_level",
        business_id=row.id,
        after=serialize_dictionary(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_dictionary(row), "客户等级已创建")


@router.patch("/customer-levels/{level_id}")
async def update_customer_level(
    level_id: int,
    payload: CustomerLevelUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await session.get(DictionaryItem, level_id)
    if row is None or row.type != CUSTOMER_LEVEL_TYPE:
        raise AppError(ErrorCode.NOT_FOUND, "客户等级不存在", 404)
    before = serialize_dictionary(row)
    data = payload.model_dump(exclude_unset=True)
    if data.get("name") is not None:
        row.label = data["name"]
    for field in ("sort_no", "enabled", "remark"):
        if data.get(field) is not None:
            setattr(row, field, data[field])
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="customer_level",
        business_id=row.id,
        before=before,
        after=serialize_dictionary(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_dictionary(row), "已保存")
