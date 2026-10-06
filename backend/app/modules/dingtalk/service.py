"""询价审批的发起与结果回收（文档 §11.3 :152 / 场景11）。

链路：CRM 需求版本 → 发起钉钉审批实例 → 拿回实例 ID → 回收状态与结果 → 回到那条需求。

## 五条纪律

**1. 幂等靠请求号（业务键 + 轮次），不靠实例 ID。**
场景11 要求"不重复建 OA 单"。重复提交时我们**压根不该再向钉钉要一次实例**——
等实例 ID 回来再判重，单子已经建出去了。所以先用
（需求 + 需求版本 + OA 类型）查 `oa_instances`，命中就直接返回既有行；
真并发时还有 `idempotency_key` 唯一约束兜底（IntegrityError 里回查一次）。

**2. "能不能重试"由后果决定，不由"是不是异常"决定（第七批 7.7）。**
一次发起只有三种后果：确定没发出 / 明确失败 / 结果未知（见 `classify_outcome`）。
只有前两种能重试；**结果未知一律先查询或转人工**——钉钉的发起接口没有幂等键，
自动重试一次就可能在对方系统里多出一张单，本地只认其中一张，另一张成为隐形挂单。
同理，待审批 / 已通过 / 结果未知**都不许"重提"**：重提不是"再发一次"，
而是"另建一张单"（`model.OA_STATUS_ACTIONS` 里写死了这条口径）。

**3. 状态 → 允许动作只有一份口径。**
服务端用它守请求（`allowed_actions`），接口把它一起返回给前端渲染按钮。
服务端和前端各写一套 if 必然分叉，分叉出去的那一侧就是"待审批还能再点一次重提"。

**4. 外部动作的占用是数据库里的事实，不是进程锁（第七批 7.8）。**
`resolve_state` / `resolve_request_key` / `resolve_claimed_at` + 行级条件转换，
保证两个并发核定只有一个能发起外部创建；另一个拿到明确的 409 而不是各建一张单。
接管僵死占用之后**不盲目再建**（见 `resolve_reviewed_instance` 里的 was_stale 分支）。

**5. 表单值挂在 `formComponentValues` 上，且要按控件对齐。**
发起审批实例的请求体里，表单值是 `formComponentValues` 这个数组
（**不是 `formValues`**——那个字段不存在，我们写错过一次，后来拿官方 SDK 核对了）；
数组元素里 `name` / `value` 必填，另有可选的 `id` / `componentType`。
控件对不上时**钉钉不报错，只把那格留空**，于是"预填成功"的假象下业务还得手填一遍，
正好把"免重复录入"这条验收标准踩没。所以 `build_form_component_values`
只认调用方给的字段映射，映射从模板字段清单来（见 21-交接说明-2026-10-04.md §7.1 的待外部输入）。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.config import settings as app_settings
from app.core.errors import AppError, ErrorCode
from app.modules.dingtalk.client import (
    DINGTALK_DISABLED_MESSAGE,
    OUTCOME_NOT_SENT,
    OUTCOME_REJECTED,
    OUTCOME_UNKNOWN,
    DingTalkError,
    classify_outcome,
    get_client,
)
from app.modules.dingtalk.model import (
    OA_ACTION_LABEL,
    OA_STATUS_LABEL,
    RESOLVE_STATE_LABEL,
    OaInstance,
    allowed_actions,
)

#: 停在 `submitting` / 核定占用超过这个时长，就认为那次尝试已经死了
#: （进程被杀、机器重启、调用方再也没回来），允许重新发起或重新占用。
#: 取 10 分钟：正常的"占键 → 调钉钉"是秒级，留足余量给网络慢的情况，
#: 同时用户等十分钟后再点也确实该重试了。**这个量级只决定"多久以后能再试"，
#: 不决定安全性**——接管僵死占用之后也不会盲目再建，见 was_stale 的处理。
SUBMITTING_STUCK_AFTER = timedelta(minutes=10)

#: 核定占用的僵死阈值。与发起同为 10 分钟：两者都是"占住 → 调一次外部"，
#: 正常耗时一个量级；分开命名是为了将来单独调其中一个时不动另一个的语义。
RESOLVE_CLAIM_STUCK_AFTER = SUBMITTING_STUCK_AFTER

#: 人工核定"结果未知"的三种动作。三者都会动外部或动状态，
#: 所以与占用逻辑放在同一处收口，不散到 router/model 里。
RESOLVE_ACTIONS = ("adopt", "resend", "abandon")

#: 「同轮可以安全重试」的状态：这三种状态下外部**确定没有建单**，
#: 重试只是把同一次发起再发一遍，不是"另建一张单"。
#: pending / approved / needs_review 都不在这里。
RETRYABLE_STATUSES = ("skipped", "failed", "not_sent")

#: 三种后果 → CRM 侧状态。这是 7.7 的核心：**"能不能重试"由后果决定**。
#: 早先一律记 failed，等于把"结果未知"也标成可重试，重复建单的入口就开在那里。
_OUTCOME_STATUS = {
    OUTCOME_NOT_SENT: "not_sent",
    OUTCOME_REJECTED: "failed",
    OUTCOME_UNKNOWN: "needs_review",
}

#: 三种后果 → 给人看的说明。要写清"现在能做什么"，不能只甩一个异常类名。
_OUTCOME_HINT = {
    OUTCOME_NOT_SENT: "本次请求没有发出去（本地错误），可以原样重试",
    OUTCOME_REJECTED: "钉钉明确拒绝了这次发起，没有建单，可以修正后重试",
    OUTCOME_UNKNOWN: (
        "外部请求没有得到明确结果：钉钉那边**可能已经建了审批单**。"
        "不会自动重发——请先到钉钉按需求号核对，再选择「认领 / 重发 / 作废」"
    ),
}

#: 钉钉的审批状态词 → CRM 侧口径。CRM 侧固定这五个值，
#: 前端和查询都不用认对方系统的用词（与 ERP Adapter 同一套做法：翻译只发生在一处）。
_STATUS_MAP = {
    "RUNNING": "pending",
    "COMPLETED": "approved",
    "TERMINATED": "rejected",
    "CANCELED": "withdrawn",
}


def _as_utc(value: datetime | None) -> datetime | None:
    """把从库里取回来的时间统一成带时区的 UTC。

    为什么要它：PostgreSQL 的 `timestamptz` 取回来带时区，SQLite（单元测试替身）
    取回来是裸 datetime，两者直接相减会 `TypeError`。**不能用 try/except 兜**——
    那会让"到底僵死没有"这条判断在测试里静默失效。
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def build_form_component_values(field_map: dict[str, Any]) -> list[dict[str, Any]]:
    """把 CRM 字段拼成钉钉的 `formComponentValues`（发起审批实例的请求体）。

    - `field_map` 的 key 必须是**模板控件的 id**（不是中文名）；
    - `name` 和 `id` 都填控件 id：新版接口以 `id` 认控件，而 `name` 按官方文档
      也允许填 id——两个都带上，任一判据都能命中；
    - `componentType` 从控件 id 的前缀推导（`TextField_XXX` → `TextField`）。
      **这不是猜的**：实测两张模板共 69 个控件，服务端返回的 `componentType`
      与 id 前缀**全部一致**；推导不出来的（id 里没有下划线）就不带这个字段；
    - 空值**不提交**——钉钉对空值控件会覆盖已有内容，把"没填"当成"清空"，
      这在"驳回后重提"时会把上一次填的内容抹掉。
    """
    items: list[dict[str, Any]] = []
    for component_id, value in field_map.items():
        if value in (None, "", [], {}):
            continue
        item: dict[str, Any] = {"name": component_id, "id": component_id, "value": value}
        if "_" in component_id:
            item["componentType"] = component_id.split("_", 1)[0]
        items.append(item)
    return items


async def get_by_business_key(
    session: AsyncSession, *, inquiry_id: int, inquiry_version: int, oa_type: str
) -> OaInstance | None:
    return (
        await session.execute(
            select(OaInstance).where(
                OaInstance.inquiry_id == inquiry_id,
                OaInstance.inquiry_version == inquiry_version,
                OaInstance.oa_type == oa_type,
            )
            .order_by(OaInstance.submit_round.desc())
        )
    ).scalars().first()


async def _next_round(
    session: AsyncSession, *, inquiry_id: int, inquiry_version: int, oa_type: str
) -> int:
    latest = await get_by_business_key(
        session, inquiry_id=inquiry_id, inquiry_version=inquiry_version, oa_type=oa_type
    )
    return int(latest.submit_round) + 1 if latest is not None else 1


def _release_processing(row: OaInstance, *, keep_key: bool) -> None:
    """放掉占用。

    `keep_key=True` 用于**正常收尾**：请求号留着，同一个请求键回放同一份结果；
    `keep_key=False` 用于核定中断/被拒：键也还回去，否则同一个人拿着同一个键
    永远只能看到"正在处理中"，而这一行其实早就没人管了。
    """
    row.resolve_state = "idle"
    row.resolve_claimed_at = None
    if not keep_key:
        row.resolve_request_key = None


def is_claim_stale(row: OaInstance) -> bool:
    """核定占用是不是已经僵死（没有占用时返回 False）。

    "僵死"= 占着 `processing` 但已经超过 `RESOLVE_CLAIM_STUCK_AFTER` 没人收尾，
    典型是进程在"占住"和"写完结果"之间被杀。它决定两件事：能不能接管这一行、
    以及能不能清掉请求键表里那条同样没人收尾的占位。
    """
    if row.resolve_state != "processing":
        return False
    claimed_at = _as_utc(row.resolve_claimed_at)
    return claimed_at is None or (datetime.now(UTC) - claimed_at) > RESOLVE_CLAIM_STUCK_AFTER


def demote_stuck_submitting(row: OaInstance) -> bool:
    """把"卡死的发起中"转成「结果待人工核对」，返回是否发生了转换（不改库，由调用方提交）。

    进程在"占业务键"与"调完外部"之间被杀时，行会停在 `submitting`：轮询只捞
    `pending`，同轮又原样返回，既不更新也不重发——**死路**。
    这里统一口径：**结果不明 → 转人工，不自动重发**（钉钉接口没有幂等键，
    自动重发可能真建出第二张单）。发起路径与核定路径共用这一处判断，
    免得两边对"多久算死了"各有一套说法。
    """
    if row.status != "submitting":
        return False
    started = _as_utc(row.last_attempt_at) or _as_utc(row.created_at)
    if started is not None and (datetime.now(UTC) - started) <= SUBMITTING_STUCK_AFTER:
        return False
    row.status = "needs_review"
    row.error = (
        "上次发起结果不明（发起过程中断）：钉钉那边可能已经建了审批单。"
        "已转人工核对——请先去钉钉确认，再选择「认领 / 重发 / 作废」"
    )
    # 占用也要放掉，否则这条记录永远停在"处理中"，核定入口进不来
    _release_processing(row, keep_key=False)
    row.updated_at = datetime.now(UTC)
    return True


async def claim_external_call(
    session: AsyncSession, *, oa_id: int, claim_key: str
) -> tuple[OaInstance | None, bool]:
    """原子地占住"对这条记录做一次外部动作"的权利（第七批 7.8）。

    为什么必须是数据库里的条件转换，而不是进程内的锁：部署是多进程/多实例的，
    进程锁在另一个进程眼里根本不存在；两个核定请求会各自读到 `needs_review`、
    各自通过检查，然后**各建一张外部审批单**——外部多一张，本地只认一张。

    做法：`SELECT ... FOR UPDATE` 先锁住这一行。PostgreSQL 下第二个连接会阻塞到
    第一个提交，然后读到最新版本（READ COMMITTED 下重新取行），于是它看到的是
    `processing` 而不是 `needs_review`；随后判断占用是否僵死，再写占用并**提交**。
    **必须在调外部之前提交**：占用只存在于未提交事务里的话，别的连接看不见它。

    返回 `(占到的行, 是否接管了一个僵死占用)`；行是 `None` 表示没占到
    （别人正在处理），调用方必须报冲突而不是继续调外部。
    """
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(OaInstance)
            .where(OaInstance.id == oa_id)
            .with_for_update()
            # **必须 populate_existing**：这一行往往已经在会话的身份映射里
            # （同一个请求前面读过它）。SQLAlchemy 默认**不会**用新读到的行覆盖
            # 已有对象——那样即使数据库已经把它标成"处理中"，这里看到的仍是旧的
            # `idle`，两个并发连接于是各自"占住"、各建一张外部审批单。
            # 真 PostgreSQL 双连接回归里真的复现过（外部创建 2 次），别去掉它。
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if row is None:
        await session.commit()
        return None, False

    was_stale = False
    if row.resolve_state == "processing":
        if not is_claim_stale(row):
            # 有人正在处理，而且还没到僵死时间：**绝不插手**。
            # 这里必须把刚才那次 FOR UPDATE 的事务结束掉，否则这一行会被本连接一直
            # 锁着，真正在处理的对手紧接着的 UPDATE 会被它挡住——两边互相等，
            # 真库双连接回归里直接挂死过一次。
            # 用 **commit 而不是 rollback**：这一路上还挂着"请求键占位"的插入，
            # 回滚会把它一起丢掉；而且 rollback 会让调用方手里所有对象过期，
            # 之后读一个属性就触发同步 IO（异步会话里是 MissingGreenlet）。
            await session.commit()
            return None, False
        was_stale = True  # 上一次占用已经死了，接下来的尝试是"接管"

    row.resolve_state = "processing"
    row.resolve_request_key = claim_key
    row.resolve_claimed_at = now
    row.updated_at = now
    await session.commit()
    return row, was_stale


async def _call_create_and_record(
    session: AsyncSession,
    row: OaInstance,
    *,
    component_values: list[dict[str, Any]],
    process_code: str,
    originator_user_id: str,
    resolve_action: str | None = None,
) -> OaInstance:
    """真正调一次外部发起，并按**三种后果**落状态（调用前占用已由调用方占到）。

    三种后果的落法（7.7）：
    - 确定没发出 → `not_sent`：可以原样重试；
    - 明确失败   → `failed`  ：可以修正后重试；
    - 结果未知   → `needs_review`：只能先查询/人工核对，**不再自动重发**。
    """
    now = datetime.now(UTC)
    row.attempt_count = int(row.attempt_count or 0) + 1
    row.last_attempt_at = now
    try:
        instance_id = await get_client().create_process_instance(
            process_code=process_code,
            form_component_values=component_values,
            originator_user_id=originator_user_id,
        )
    except Exception as exc:  # noqa: BLE001 —— 外部怎么炸都要落痕，绝不能假装成功
        outcome = classify_outcome(exc)
        row.status = _OUTCOME_STATUS[outcome]
        row.error = f"{_OUTCOME_HINT[outcome]}｜{type(exc).__name__}: {exc}"[:500]
        if resolve_action:
            # 这一次核定到此收尾：同一个请求键必须回放这份结果，而不是再打一次钉钉
            row.resolved_at = now
            row.resolved_action = resolve_action
        _release_processing(row, keep_key=True)
        row.updated_at = now
        await session.commit()
        return row

    row.instance_id = instance_id
    row.status = "pending"
    row.error = None
    row.synced_at = now
    row.updated_at = now
    if resolve_action:
        row.resolved_at = now
        row.resolved_action = resolve_action
    _release_processing(row, keep_key=True)
    await session.commit()
    return row


async def create_inquiry_instance(
    session: AsyncSession,
    *,
    user: CurrentUser,
    inquiry_id: int,
    inquiry_version: int,
    customer_id: int | None,
    process_code: str,
    originator_user_id: str,
    field_map: dict[str, Any],
    oa_type: str = "inquiry",
    resubmit: bool = False,
) -> OaInstance:
    """发起询价审批。

    **提交轮次决定请求号**（文档 :43 挡网络重试，§11.3 :152 要重提能跑通）：

    - `resubmit=False`（默认）——同一轮：已有记录就返回，不再打钉钉。
      网络重试、页面重复点击都命中这条，不会重复建单；`failed` / `not_sent` /
      `skipped` 是例外：这三种状态外部确定没建单，**同轮重试是安全的**，
      而且不重试就会卡成死路（7.7 的原始缺陷之一）。
    - `resubmit=True`——换一轮重提：轮次 +1、换请求号，在钉钉里建一张新单。
      **只许在旧轮"明确结束"后用**（已驳回 / 人工作废）；待审批、已通过、
      结果未知都不许——那三种状态下钉钉那边可能正有一张活单。
    """
    now = datetime.now(UTC)
    row: OaInstance | None = None
    latest = await get_by_business_key(
        session, inquiry_id=inquiry_id, inquiry_version=inquiry_version, oa_type=oa_type
    )

    if latest is not None and not resubmit:
        if app_settings.dingtalk_push_off:
            # 闸门关着，点了也不会发出去：原样返回。
            # 不能把上一次的真实后果（failed/needs_review…）覆盖成"未发起"——
            # 那会把"结果未知、需要去钉钉核对"这条线索抹掉。
            return latest
        if latest.status == "submitting":
            # 进程在"占业务键"与"调完外部"之间被杀，行就会停在 submitting。
            # 口径见 demote_stuck_submitting：结果不明 → 转人工，不自动重发。
            if demote_stuck_submitting(latest):
                await session.commit()
            return latest
        if latest.status in RETRYABLE_STATUSES:
            # 同轮重试：复用同一行、同一请求号（外部确定没建单，不会重复实例）
            row = latest
        else:
            # pending / approved：**不许**用重提绕过去另建实例，这里连重试都不做；
            # rejected / withdrawn：要推进得显式走重提（换一轮）；
            # needs_review：只能走人工核定。
            # 前端按钮由 allowed_actions 决定，这里再守一道（服务端不能信前端）。
            return latest

    if latest is not None and resubmit:
        permitted = allowed_actions(latest.status, latest.resolve_state)
        if "resubmit" not in permitted:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                _resubmit_denied_message(latest, permitted),
                422,
            )

    submit_round = (int(latest.submit_round) + 1) if (resubmit and latest) else 1
    idempotency_key = f"{inquiry_id}:{inquiry_version}:{oa_type}:{submit_round}"

    component_values = build_form_component_values(field_map)

    # 推送总闸（默认关）：测试期绝不向外部系统发起真实审批单。
    # 与企微同一套语义——记 skipped 并写明原因，"没发"不等于"发失败"。
    if app_settings.dingtalk_push_off:
        blocked = OaInstance(
            customer_id=customer_id,
            inquiry_id=inquiry_id,
            inquiry_version=inquiry_version,
            oa_type=oa_type,
            idempotency_key=idempotency_key,
            submit_round=submit_round,
            process_code=process_code,
            originator_user_id=originator_user_id,
            form_snapshot={"formComponentValues": component_values},
            status="skipped",
            error=f"{DINGTALK_DISABLED_MESSAGE}；未发起审批",
            created_by=user.id,
            created_at=now,
            last_attempt_at=now,
        )
        session.add(blocked)
        await session.flush()
        return blocked

    if row is not None:
        # 同轮重试路径：复用同一行、**同一请求号**（不换行、不加轮次）。
        # 请求号取这一行自己的 idempotency_key——不能用上面算出来的 "第 1 轮"，
        # 否则一个已经重提到第 3 轮的行会被记上第 1 轮的键，既对不上历史，
        # 也可能撞上别的行占着的同一个键。
        # 先**原子占住**，两个并发重试只有一个能真的调外部。
        claimed, _was_stale = await claim_external_call(
            session, oa_id=row.id, claim_key=row.idempotency_key
        )
        if claimed is None:
            raise AppError(
                ErrorCode.VERSION_CONFLICT,
                "这条审批正在发起中，请稍后刷新查看，不要重复点击",
                409,
            )
        row = claimed
        # 刷新报文快照，保证"本次实际发出去的"与记录一致（复用同一行，
        # 所以不需要再走一次"占业务键"的插入与唯一约束处理）
        row.form_snapshot = {"formComponentValues": component_values}
        row.originator_user_id = originator_user_id
        row.process_code = process_code
        await session.commit()
        return await _call_create_and_record(
            session,
            row,
            component_values=component_values,
            process_code=process_code,
            originator_user_id=originator_user_id,
        )

    row = OaInstance(
        customer_id=customer_id,
        inquiry_id=inquiry_id,
        inquiry_version=inquiry_version,
        oa_type=oa_type,
        idempotency_key=idempotency_key,
        submit_round=submit_round,
        process_code=process_code,
        originator_user_id=originator_user_id,
        form_snapshot={"formComponentValues": component_values},
        status="pending",
        created_by=user.id,
        created_at=now,
        last_attempt_at=now,
    )
    # **先占住业务键，再调外部**（P1）：原顺序是"先调钉钉建单、后落本地唯一键"，
    # 于是两件事都可能重复建单——并发请求、以及"钉钉建成功了但响应没回来"的重试。
    # 现在先落一行 submitting 并提交，把唯一键占实；并发对手会撞唯一约束后复用它。
    row.status = "submitting"
    row.resolve_state = "processing"
    row.resolve_request_key = idempotency_key
    row.resolve_claimed_at = now
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # 并发对手已占同一轮：复用它，**不再向钉钉要第二次实例**
        await session.rollback()
        existing = await get_by_business_key(
            session,
            inquiry_id=inquiry_id,
            inquiry_version=inquiry_version,
            oa_type=oa_type,
        )
        if existing is not None:
            return existing
        raise

    return await _call_create_and_record(
        session,
        row,
        component_values=component_values,
        process_code=process_code,
        originator_user_id=originator_user_id,
    )


def _resubmit_denied_message(row: OaInstance, permitted: list[str]) -> str:
    """重提被拒时，要把"为什么不行"和"现在能做什么"一起说清楚。

    只说"状态不允许"的话，用户下一步只能去问开发；而这条口径本身就是业务规则，
    所以直接把允许的动作列出来（用的还是同一份 OA_ACTION_LABEL）。
    """
    current = OA_STATUS_LABEL.get(row.status, row.status)
    if row.status in ("pending", "approved"):
        reason = (
            f"这条需求第 {row.submit_round} 轮的审批已经是「{current}」"
            + ("（钉钉那边还有一张在走的单）" if row.status == "pending" else "")
            + "，重提会在钉钉里再建一张单"
        )
    elif row.resolve_state == "processing":
        reason = "这条审批正在被另一次核定处理中"
    else:
        reason = f"「{current}」不允许重提"
    todo = "、".join(OA_ACTION_LABEL.get(a, a) for a in permitted) or "（现在没有可执行的动作）"
    return f"{reason}。当前可以做的操作：{todo}"


async def resolve_reviewed_instance(
    session: AsyncSession,
    row: OaInstance,
    *,
    action: str,
    instance_id: str | None = None,
    note: str | None = None,
    request_key: str | None = None,
) -> OaInstance:
    """人工处理"结果不明/明确失败"的发起（口径 2026-10-04：不自动重发，转人工）。

    三种决定，都要人先到钉钉那边看一眼再选：
    - `adopt`：钉钉其实已经建单 → 填实例号接过来，**先核实模板/发起人/来源需求**；
    - `resend`：确认钉钉没有单 → 复用同一轮重新发起（同一行、同一请求号）；
    - `abandon`：确认不发了 → 作废本轮记录。

    7.8：三种动作都要先**原子占住**这一行（`claim_external_call` + 请求键），
    否则两个并发核定会各自读到 `needs_review`、各建一张外部单。
    """
    # 关闸时**先于任何数据库访问**拒绝：既有的"关闸不得触碰数据库"用例靠这一条守住，
    # 而且认领也要向钉钉核实来源，关闸时同样做不了（fail-closed）。
    if action in ("adopt", "resend") and app_settings.dingtalk_push_off:
        raise AppError(
            ErrorCode.FORBIDDEN, f"{DINGTALK_DISABLED_MESSAGE}；不能核定审批结果", 403
        )
    if action not in RESOLVE_ACTIONS:
        raise AppError(
            ErrorCode.PARAM_ERROR, "action 只能是 adopt / resend / abandon", 422
        )

    key = (request_key or "").strip() or f"oa-resolve:{row.id}:{action}"
    if len(key) > 128:
        raise AppError(ErrorCode.PARAM_ERROR, "请求键长度不能超过 128", 422)

    oa_id = row.id
    await session.refresh(row)
    # **同键回放**：上一次同键核定已经正常收尾，直接返回那份结果，不再动外部。
    # 这是"相同请求回放同结果"的落点——弱网重发不会变成第二次外部创建。
    if row.resolve_request_key == key and row.resolved_at is not None:
        return row

    claimed, was_stale = await claim_external_call(session, oa_id=oa_id, claim_key=key)
    if claimed is None:
        fresh = await session.get(OaInstance, oa_id)
        await session.refresh(fresh)
        if fresh.resolve_request_key == key and fresh.resolved_at is not None:
            # 对手方其实就是同一个请求，而且刚做完：回放它的结果
            return fresh
        if fresh.resolve_request_key == key:
            raise AppError(
                ErrorCode.DUPLICATE,
                "这次核定正在处理中，请稍后刷新查看，不要重复提交",
                409,
            )
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "这条审批记录正在被另一次核定处理中（请求号 "
            f"{fresh.resolve_request_key or '—'}）。请稍后刷新再操作，"
            "不要并发重复核定——并发重发会在钉钉里建出两张单",
            409,
        )
    row = claimed

    if was_stale and action == "resend":
        # 接管了一个僵死占用：上一次核定没正常结束，钉钉那边**可能已经建单**。
        # 这里绝不盲目再发一次，而是把占用放掉、把话说清楚，让人先核实再来。
        row.status = "needs_review"
        row.error = (
            "上一次核定没有正常结束（可能已向钉钉发起但没拿到结果）。"
            "请先到钉钉按需求核对：找到单号就用「认领」接过来；"
            "确认确实没有这张单，再重新核定并重发"
        )
        _release_processing(row, keep_key=False)
        row.updated_at = datetime.now(UTC)
        await session.commit()
        raise AppError(ErrorCode.VERSION_CONFLICT, row.error, 409)

    try:
        if action == "adopt":
            return await _adopt_instance(
                session, row, instance_id=instance_id, request_key=key
            )
        if action == "abandon":
            now = datetime.now(UTC)
            row.status = "withdrawn"
            row.error = note or "人工核对确认钉钉未建单，作废本轮"
            row.resolved_at = now
            row.resolved_action = "abandon"
            row.updated_at = now
            _release_processing(row, keep_key=True)
            await session.commit()
            return row
        return await resend_instance(session, row, request_key=key)
    except BaseException as exc:
        # 核定途中出任何意外：占用必须放掉（否则这一行永远停在"处理中"，
        # 谁也别想再核定），状态按**结果未知**处理——异常发生前可能已经调过外部，
        # 说成"失败"就是撒谎，会诱导下一个人直接重发。
        #
        # 这里刻意**不先 rollback**：这一路在异常点之前没有半成品写入（认领的核实
        # 失败只读不写、重发的结果由 _call_create_and_record 自己落），而 rollback 会
        # 让调用方手里的对象全部过期——同一次请求里随后读 `reservation.row.id` 之类
        # 就会在异步会话里抛 MissingGreenlet。只有"释放自己也失败"时才回滚兜底。
        try:
            if row.status == "submitting":
                row.status = "needs_review"
                row.error = (
                    f"核定过程中断（{type(exc).__name__}: {exc}）："
                    "外部结果未知，请先到钉钉核对再决定认领/重发/作废"
                )[:500]
            _release_processing(row, keep_key=False)
            row.updated_at = datetime.now(UTC)
            await session.commit()
        except Exception:  # noqa: BLE001 —— 释放只是补偿动作，不能盖住原始错误
            await session.rollback()
        raise


async def _verify_instance_source(
    session: AsyncSession, row: OaInstance, payload: dict
) -> None:
    """核实"这张钉钉单确实是这条需求发起的那一张"（7.8）。

    为什么要核实：`instance_id` 是人手输进来的字符串。不核实就采纳，等于让一个
    字符串决定"哪张审批单的结果回到哪条需求"——输错一位就认了别人的单，
    而后续轮询/回调会把那张单的审批结果写进这条需求（价格、交期都可能跟着变），
    同时真正属于这条需求的那张单变成没人管的挂单。

    **fail-closed**：模板、发起人、来源需求三项里任何一项拿不到或对不上都拒绝采纳。
    真实的字段名（`processCode` / `originatorUserId` / `businessId`）需要与钉钉联调
    确认后才能收紧，所以在拿不到字段时只能拒绝——**默认放行**才是这里最危险的做法。
    """
    from app.modules.inquiry.model import CustomInquiry

    inquiry = await session.get(CustomInquiry, row.inquiry_id)
    inquiry_no = getattr(inquiry, "inquiry_no", None)
    accepted_business = {str(row.inquiry_id)}
    if inquiry_no:
        accepted_business.add(str(inquiry_no))

    problems: list[str] = []

    got_process = str(
        payload.get("processCode") or payload.get("process_code") or ""
    ).strip()
    if not got_process:
        problems.append("响应里没有审批模板编号 processCode，无法确认模板")
    elif row.process_code and got_process != row.process_code:
        problems.append(
            f"审批模板对不上：钉钉是 {got_process}，这条记录当初用的是 {row.process_code}"
        )

    got_origin = str(
        payload.get("originatorUserId") or payload.get("originator_user_id") or ""
    ).strip()
    if not got_origin:
        problems.append("响应里没有发起人 originatorUserId，无法确认发起人")
    elif row.originator_user_id and got_origin != row.originator_user_id:
        problems.append(
            f"发起人对不上：钉钉是 {got_origin}，这条记录当初是 {row.originator_user_id}"
        )

    got_business = str(payload.get("businessId") or payload.get("business_id") or "").strip()
    if not got_business:
        problems.append("响应里没有来源需求编号 businessId，无法确认这张单属于哪条需求")
    elif got_business not in accepted_business:
        problems.append(
            f"来源需求对不上：钉钉这张单是 {got_business}，这条记录是需求 "
            f"{row.inquiry_id}（编号 {inquiry_no or '—'}）"
        )

    if problems:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "不能采纳这张审批单：" + "；".join(problems) + "。请核对钉钉单号后重试",
            409,
        )


async def _adopt_instance(
    session: AsyncSession, row: OaInstance, *, instance_id: str | None, request_key: str
) -> OaInstance:
    """认领钉钉那边已经存在的实例：**先核实来源，核实不过一律不采纳**。"""
    # **先 strip 再判空**：反过来写的话，用户只敲了空格就会被当成"填了单号"，
    # 直接拿去查钉钉，最后报成 502「无法核实审批单 ''」——明明是他没填，
    # 却显示成外部故障，还白打一次真实钉钉查询。
    instance_id = (instance_id or "").strip()
    if not instance_id:
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING, "认领需要填写钉钉那边已有的审批单号", 422
        )

    # 一个外部实例只能关联一行：先查本地（给出人能看懂的报错），
    # 真并发由 `uq_oa_instance_instance_id` 唯一索引兜底。
    taken = (
        await session.execute(
            select(OaInstance).where(
                OaInstance.instance_id == instance_id, OaInstance.id != row.id
            )
        )
    ).scalars().first()
    if taken is not None:
        raise AppError(
            ErrorCode.DUPLICATE,
            f"审批单 {instance_id} 已经关联到需求 {taken.inquiry_id} 的第 "
            f"{taken.submit_round} 轮审批记录，同一个钉钉单不能被两条记录同时认领",
            409,
        )

    try:
        payload = await get_client().get_process_instance(instance_id)
    except Exception as exc:  # noqa: BLE001 —— 查不动就不采纳，绝不默认放行
        # 外部查询失败**保留"结果未知"**：记录仍是 needs_review，人可以稍后再试。
        raise AppError(
            ErrorCode.EXTERNAL_ERROR,
            f"无法向钉钉核实审批单 {instance_id}（{type(exc).__name__}: {exc}）。"
            "核实不通过就不采纳，这条记录仍是「结果待人工核对」",
            502,
        ) from exc

    await _verify_instance_source(session, row, payload)

    now = datetime.now(UTC)
    row.instance_id = instance_id
    row.status = "pending"
    row.error = None
    row.synced_at = now
    row.updated_at = now
    row.resolved_at = now
    row.resolved_action = "adopt"
    _release_processing(row, keep_key=True)
    await session.commit()
    return row


async def resend_instance(
    session: AsyncSession, row: OaInstance, *, request_key: str | None = None
) -> OaInstance:
    """人工确认钉钉没建单后，复用同一轮重新发起（不换行、不加轮次）。

    提交前先落 `submitting` 并提交：万一进程在调用钉钉前后又被打断，下一个人
    打开还能看到"结果不明"，不会被当成没发过而重复点。
    7.8：占用由调用方（`resolve_reviewed_instance`）先原子占住，这里只负责调外部并落后果。
    """
    # 结果不明的原记录必须保留，关闸时不能改成 submitting / failed / skipped。
    if app_settings.dingtalk_push_off:
        raise AppError(
            ErrorCode.FORBIDDEN, f"{DINGTALK_DISABLED_MESSAGE}；不能重发审批", 403
        )
    component_values = (row.form_snapshot or {}).get("formComponentValues") or []
    row.status = "submitting"
    row.error = None
    row.updated_at = datetime.now(UTC)
    # **先提交再调外部**：一是让"正在发起"这个事实对别的连接可见，
    # 二是调外部期间不要抱着一个写事务（PG 会白占行锁，SQLite 直接锁库）
    await session.commit()
    return await _call_create_and_record(
        session,
        row,
        component_values=component_values,
        process_code=row.process_code or "",
        originator_user_id=row.originator_user_id or "",
        resolve_action="resend",
    )


async def sync_pending_instances(session: AsyncSession, *, limit: int = 50) -> dict:
    """轮询回写：把还在审批中的实例拉一次状态与结果。

    为什么先做轮询而不是事件订阅：轮询**不需要公网回调地址、也不需要管理员
    在 OA 后台额外授权**，而且天然不会丢消息——这一轮没查到，下一轮还会查。
    代价只是延迟几分钟，而询价审批本来就要几小时到几天。
    """
    if app_settings.dingtalk_push_off:
        return {"checked": 0, "changed": 0, "disabled": True, "message": DINGTALK_DISABLED_MESSAGE}
    rows = (
        await session.execute(
            select(OaInstance)
            .where(OaInstance.status == "pending", OaInstance.instance_id.is_not(None))
            .order_by(OaInstance.id.asc())
            .limit(limit)
        )
    ).scalars().all()
    if not rows:
        return {"checked": 0, "changed": 0}

    client = get_client()
    changed = 0
    for row in rows:
        try:
            data = await client.get_process_instance(row.instance_id or "")
        except DingTalkError as exc:
            # 单条查失败不影响其他单：记在行上，下一轮还会再试
            row.error = str(exc)[:500]
            continue
        raw_status = str(data.get("status") or "").upper()
        # 审批**走完**之后，通过还是驳回要看 `result`（agree / refuse）——
        # 只看 status 的话 COMPLETED 会被一律记成"已通过"，
        # **被驳回的单子在 CRM 里会显示成通过**，那是最危险的一类错。
        raw_result = str(data.get("result") or "").lower()
        if raw_status == "COMPLETED":
            if raw_result in ("refuse", "reject", "refused"):
                new_status = "rejected"
            elif raw_result in ("agree", "approved", "pass"):
                new_status = "approved"
            else:
                new_status = None
        else:
            new_status = _STATUS_MAP.get(raw_status)
        row.result = {"raw_status": raw_status, "raw_result": raw_result, "payload": data}
        row.synced_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        if new_status and new_status != row.status:
            row.status = new_status
            changed += 1
    await session.flush()
    return {"checked": len(rows), "changed": changed}


async def first_image_attachment(
    session: AsyncSession, *, business_type: str, business_id: int
) -> tuple[str, bytes] | None:
    """取某个业务对象挂的第一张图片附件，返回 (文件名, 内容)。

    钉钉模板里「产品参考图片」是**必填的图片控件**，所以发起前必须有一张图。
    图不能直接塞进审批单，得先上传给钉钉换 media_id——**这一步也是对外的**，
    所以调用方必须在推送总闸打开时才真正传（关着的时候只记录"会用哪张图"）。
    """
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.file.storage import absolute_path

    rows = (
        await session.execute(
            select(FileRecord)
            .join(BusinessFile, BusinessFile.file_id == FileRecord.id)
            .where(
                BusinessFile.business_type == business_type,
                BusinessFile.business_id == business_id,
            )
            .order_by(BusinessFile.id.asc())
        )
    ).scalars().all()
    for row in rows:
        mime = (row.mime_type or "").lower()
        if row.mime_type and not mime.startswith("image/"):
            continue
        path = absolute_path(row.object_key)
        if path.exists():
            return row.file_name, path.read_bytes()
    return None


def serialize(row: OaInstance) -> dict:
    return {
        "id": row.id,
        "inquiry_id": row.inquiry_id,
        "inquiry_version": row.inquiry_version,
        "oa_type": row.oa_type,
        "instance_id": row.instance_id,
        "status": row.status,
        "status_label": OA_STATUS_LABEL.get(row.status, row.status),
        # 请求号 / 轮次 / 尝试次数：7.7 要求"保留请求编号、次数与每轮历史"。
        # 轮次看 submit_round（每轮一行，历史不删），同轮内试过几次看 attempt_count。
        "request_no": row.idempotency_key,
        "submit_round": row.submit_round,
        "attempt_count": int(row.attempt_count or 0),
        "result": row.result,
        "error": row.error,
        # 7.8：占用状态与"允许动作"一起回给前端——按钮由服务端口径决定，
        # 前端不再自己判断"哪些状态下显示发起/重提"。
        "resolve_state": row.resolve_state,
        "resolve_state_label": RESOLVE_STATE_LABEL.get(row.resolve_state, row.resolve_state),
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolved_action": row.resolved_action,
        "allowed_actions": allowed_actions(row.status, row.resolve_state),
        "synced_at": row.synced_at.isoformat() if row.synced_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_attempt_at": (
            row.last_attempt_at.isoformat() if row.last_attempt_at else None
        ),
    }
