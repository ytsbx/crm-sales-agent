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

import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.config import settings as app_settings
from app.core.errors import AppError, ErrorCode
from app.modules.dingtalk.client import (
    DingTalkUnknownOutcome,
    DINGTALK_DISABLED_MESSAGE,
    DINGTALK_LOGIN_DISABLED_MESSAGE,
    OUTCOME_NOT_SENT,
    OUTCOME_REJECTED,
    OUTCOME_UNKNOWN,
    DingTalkDisabled,
    DingTalkError,
    DingTalkNotConfigured,
    classify_outcome,
    get_client,
)
from app.modules.dingtalk.model import (
    OA_ACTION_LABEL,
    OA_STATUS_LABEL,
    OA_TYPE_LABEL,
    RESOLVE_STATE_LABEL,
    OaInstance,
    allowed_actions,
)
from app.modules.user.model import User, Role, user_roles

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
    "TERMINATED": "withdrawn",
    "CANCELED": "withdrawn",
    "CANCELLED": "withdrawn",
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


def build_form_component_values(
    field_map: dict[str, Any], *, field_definitions: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """把 CRM 字段拼成钉钉的 `formComponentValues`（发起审批实例的请求体）。

    - `field_map` 的 key 必须是**模板控件的 id**（不是中文名）；
    - 提供当前模板定义时，`name` 使用 label，`id` 使用控件 id。
      官方创建接口要求 name 与模板 label 一致；旧调用方未提供定义时保留原键。
    - `componentType` 从控件 id 的前缀推导（`TextField_XXX` → `TextField`）。
      **这不是猜的**：实测两张模板共 69 个控件，服务端返回的 `componentType`
      与 id 前缀**全部一致**；推导不出来的（id 里没有下划线）就不带这个字段；
    - 空值**不提交**——钉钉对空值控件会覆盖已有内容，把"没填"当成"清空"，
      这在"驳回后重提"时会把上一次填的内容抹掉。
    """
    items: list[dict[str, Any]] = []
    definitions = {field['id']: field for field in (field_definitions or [])}
    for component_id, value in field_map.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, bool):
            # 布尔值不是钉钉表单控件支持的文本类型，跳过它，避免把
            # Python 的 True/False 误提交成用户可见的表单内容。
            continue
        definition = definitions.get(component_id, {})
        kind = definition.get('type') or (component_id.split('_', 1)[0] if '_' in component_id else None)
        if kind == 'InnerContactField':
            # 创建接口的单人联系人同样使用 userId 数组字符串；详情中的显示姓名不能回填。
            if isinstance(value, str):
                value = json.loads(value) if value.lstrip().startswith('[') else [value]
            if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
                raise ValueError(f'审批字段 {component_id} 需要真实 userId 数组')
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False, allow_nan=False)
        else:
            value = str(value)
        item: dict[str, Any] = {"name": definition.get('label') or component_id, "id": component_id, "value": value}
        if kind:
            item["componentType"] = kind
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
    dept_id: int | None = None,
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
        department_args = {'dept_id': dept_id} if dept_id is not None else {}
        instance_id = await get_client().create_process_instance(
            process_code=process_code,
            form_component_values=component_values,
            originator_user_id=originator_user_id,
            **department_args,
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
    field_definitions: list[dict[str, Any]] | None = None,
    dept_id: int | None = None,
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

    component_values = build_form_component_values(field_map, field_definitions=field_definitions)
    form_snapshot = {"formComponentValues": component_values}
    if dept_id is not None:
        form_snapshot['deptId'] = dept_id

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
            form_snapshot=form_snapshot,
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
        row.form_snapshot = form_snapshot
        row.originator_user_id = originator_user_id
        row.process_code = process_code
        await session.commit()
        return await _call_create_and_record(
            session,
            row,
            component_values=component_values,
            process_code=process_code,
            originator_user_id=originator_user_id,
            dept_id=dept_id,
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
        form_snapshot=form_snapshot,
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
        dept_id=dept_id,
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


@dataclass(frozen=True)
class BusinessKey:
    """一条 OA 记录指向的业务对象。

    `oa_instances.inquiry_id` 这一列**是复用的**：询价存需求 id、打样存打样单 id、
    合同存合同文档 id。读取侧一律按 `oa_type` 分派，不能假定它是需求
    —— 早先核定（认领/重发/作废）只认"需求"，于是合同与打样那两种单在
    "结果待人工核对"时没有任何出口，而认领时的来源核对还会拿错表的主键去比。
    """

    label: str          # 给人看的对象名，出现在报错与日志里
    accepted_ids: set[str]   # 钉钉单上的 businessId 可能是这几个值中的任意一个
    display: str        # 报错里用的"是哪一条"描述（编号或 id）


async def resolve_business_key(session: AsyncSession, row: OaInstance) -> BusinessKey:
    """按 `oa_type` 把 `inquiry_id` 解释成真正的业务对象。

    带上"对象自己的编号"（需求编号 / 合同编号）一起作为可接受的 businessId：
    钉钉模板里填的可能是编号而不是数据库主键，两种都得认，否则合法认领会被拒。
    """
    if row.oa_type == "contract":
        from app.modules.contract.model import ContractDocument

        doc = await session.get(ContractDocument, row.inquiry_id)
        accepted = {str(row.inquiry_id)}
        if doc is not None and getattr(doc, "doc_no", None):
            accepted.add(str(doc.doc_no))
        return BusinessKey(
            label="合同",
            accepted_ids=accepted,
            display=(doc.doc_no if doc is not None else f"合同 {row.inquiry_id}"),
        )
    if row.oa_type == "sample":
        from app.modules.sample.model import SAMPLE_STATUS_LABEL, SampleRequest

        sample = await session.get(SampleRequest, row.inquiry_id)
        state = SAMPLE_STATUS_LABEL.get(getattr(sample, "status", None), None)
        return BusinessKey(
            label="打样单",
            accepted_ids={str(row.inquiry_id)},
            display=f"打样单编号 {row.inquiry_id}" + (f"（{state}）" if state else ""),
        )
    from app.modules.inquiry.model import CustomInquiry

    inquiry = await session.get(CustomInquiry, row.inquiry_id)
    inquiry_no = getattr(inquiry, "inquiry_no", None)
    accepted = {str(row.inquiry_id)}
    if inquiry_no:
        accepted.add(str(inquiry_no))
    return BusinessKey(
        label="需求",
        accepted_ids=accepted,
        display=f"需求 {row.inquiry_id}（编号 {inquiry_no or '—'}）",
    )


async def _verify_instance_source(
    session: AsyncSession, row: OaInstance, payload: dict
) -> None:
    """核实"这张钉钉单确实是这一条业务记录发起的那一张"（7.8）。

    为什么要核实：`instance_id` 是人手输进来的字符串。不核实就采纳，等于让一个
    字符串决定"哪张审批单的结果回到哪条记录"——输错一位就认了别人的单，
    而后续轮询/回调会把那张单的审批结果写进这条记录（价格、交期都可能跟着变），
    同时真正属于这条记录的那张单变成没人管的挂单。

    **fail-closed**：模板、发起人、来源对象三项里任何一项拿不到或对不上都拒绝采纳。
    真实的字段名（`processCode` / `originatorUserId` / `businessId`）需要与钉钉联调
    确认后才能收紧，所以在拿不到字段时只能拒绝——**默认放行**才是这里最危险的做法。

    ⚠️ 来源对象按 `oa_type` 解析（2026-10-16）：这一列对合同/打样存的是另一张表的
    主键，直接当需求查会拿别人的编号去比对，合法认领永远过不了。
    """
    business = await resolve_business_key(session, row)

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
        problems.append(f"响应里没有来源{business.label}编号 businessId，无法确认这张单属于哪一条{business.label}")
    elif got_business not in business.accepted_ids:
        problems.append(
            f"来源{business.label}对不上：钉钉这张单是 {got_business}，这条记录是 "
            f"{business.display}"
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
            f"审批单 {instance_id} 已经关联到"
            f"{OA_TYPE_LABEL.get(taken.oa_type, taken.oa_type)} {taken.inquiry_id} 的第 "
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
        dept_id=(row.form_snapshot or {}).get('deptId'),
        resolve_action="resend",
    )


async def sync_pending_instances(session: AsyncSession, *, limit: int = 50, oa_id: int | None = None) -> dict:
    """统一回收钉钉快照：事件和手动同步指定单条，定时任务公平轮询未完成记录。

    同一行只允许一个查询者持锁写回，事件重复投递不会重复创建业务流程。
    """
    if app_settings.dingtalk_push_off:
        return {"checked": 0, "changed": 0, "disabled": True, "message": DINGTALK_DISABLED_MESSAGE}
    pending = or_(
        OaInstance.status == "pending",
        (OaInstance.status == "withdrawn") &
        (OaInstance.result['withdraw_snapshot_pending'].as_boolean().is_(True)),
    )
    rows = (
        await session.execute(
            select(OaInstance)
            .where(OaInstance.id == oa_id if oa_id is not None else pending,
                   OaInstance.instance_id.is_not(None))
            .order_by(OaInstance.updated_at.asc().nullsfirst(), OaInstance.id.asc())
            .limit(1 if oa_id is not None else limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    if not rows:
        return {"checked": 0, "changed": 0}

    client = get_client()
    changed = 0
    for row in rows:
        try:
            data = await client.get_process_instance(row.instance_id or "")
        except (DingTalkError, DingTalkUnknownOutcome) as exc:
            # 单条查失败不影响其他单：记在行上，下一轮还会再试。
            #
            # ⚠️ **两类都要捕**（2026-10-14 审查修）：
            # `DingTalkError` = 对方明确答复了（4xx/业务错）；
            # `DingTalkUnknownOutcome` = 请求发出去了但读不出结果（超时/连接断/响应不可解析）。
            # 两者**不是父子关系**（`classify_outcome` 靠这个区别决定"能不能重试"），
            # 而这里原来只捕前者 —— 于是一条超时就会打断整批（后面几十条不再轮询），
            # 且本轮改动要到循环末尾才落库，异常路径事务回滚 →
            # **前面已取回的状态全部丢失**，手工触发还会 500。
            # 这与本函数的承诺（"单条查失败不影响其他单"）直接矛盾。
            row.error = str(exc)[:500]
            row.updated_at = datetime.now(UTC)
            continue
        linked_sample = None
        linked_contract = None
        if row.oa_type == 'sample':
            from app.modules.sample.model import SampleRequest
            linked_sample = await session.get(SampleRequest, row.inquiry_id, with_for_update=True)
        elif row.oa_type == 'contract':
            from app.modules.contract.model import ContractDocument
            linked_contract = await session.get(ContractDocument, row.inquiry_id, with_for_update=True)
        row.error = None
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
        if row.status in ('approved', 'rejected', 'withdrawn') and new_status == 'pending':
            # 保留已确认终态及完整快照，等待外部数据收敛。
            row.updated_at = datetime.now(UTC)
            continue
        previous_result = row.result or {}
        withdrawal_pending = bool(previous_result.get('withdraw_snapshot_pending'))
        if withdrawal_pending and new_status == 'pending':
            # 撤回接口已明确成功；钉钉查询短暂返回旧快照时不能退回审批中。
            new_status = None
        row.result = {**previous_result, "raw_status": raw_status, "raw_result": raw_result, "payload": data}
        if withdrawal_pending:
            row.result = {**row.result, 'withdraw_snapshot_pending': new_status is None}
        row.synced_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        if new_status and new_status != row.status:
            row.status = new_status
            changed += 1
        if linked_sample is not None and linked_sample.deleted_at is None and int(linked_sample.version or 1)==row.inquiry_version:
            meta=(linked_sample.source_context or {}).get('dingtalk_oa') or {}
            if meta.get('mode')=='standalone' and meta.get('submitted') and linked_sample.status=='pending' and new_status in ('approved','rejected'):
                linked_sample.status=new_status
                linked_sample.approved_at=datetime.now(UTC)
                linked_sample.reject_reason='钉钉打样审批已拒绝，处理意见见钉钉审批记录' if new_status=='rejected' else None
        if linked_contract is not None and linked_contract.deleted_at is None:
            old_meta = (linked_contract.filled_data or {}).get('_dingtalk_oa') or {}
            linked_contract.filled_data = {
                **(linked_contract.filled_data or {}),
                '_dingtalk_oa': {
                    **old_meta,
                    'oa_instance_id': row.id,
                    'instance_id': row.instance_id,
                    'status': row.status,
                    'synced_at': row.synced_at.isoformat() if row.synced_at else None,
                    'result': row.result,
                },
            }
            # 审批**真的走到终态**时，提醒负责人去追客户回签，并把那张
            # 「上传客户回签合同」待办的截止日往前推（客户起草合同，2026-10-16）。
            #
            # 为什么必须有这一段：待办是**生成合同那一刻**就建好的，此后任凭审批
            # 通过、客户一直不回签，它都不会再响一声 —— 而这一刻才是真正该去追的
            # 时点（合同刚获批、客户手上还没盖章）。原先只有"登记回签件自动完成"
            # 和"作废取消"两个时点，中间全程静默。
            #
            # 去重靠**上面那句** `new_status != row.status`：只有状态真的从
            # pending 翻到终态的那一次才进得来（`changed` 也是同一个判据），
            # 所以每份合同每个终态只提醒一次，轮询反复回写同一状态不会重复推送。
            marker = f'{row.id}:{row.status}'
            if new_status in ('approved', 'rejected') and (linked_contract.filled_data or {}).get('_oa_notified') != marker:
                await _notify_contract_review_outcome(session, linked_contract, new_status)
                linked_contract.filled_data = {**linked_contract.filled_data, '_oa_notified': marker}

    await session.flush()
    return {"checked": len(rows), "changed": changed}


async def _notify_contract_review_outcome(
    session: AsyncSession, doc, new_status: str
) -> None:
    """合同审批出结果后提醒负责人，并刷新回签待办的截止日。

    ❗ 只负责**提醒**，不改合同状态：合同要等客户盖章的回签件登记上来才算签成
    （`contract/service.sign_document`），审批通过只是"可以寄给客户签了"。
    所以这里既不把 doc 置成 signed，也不结束待办 —— 待办的结束仍归登记签署那一步。
    """
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.timebase import business_day_end, today_business
    from app.modules.settings import service as settings_service
    from app.modules.task.model import Task

    task = (
        await session.execute(
            select(Task).where(
                Task.source_business_type == 'contract',
                Task.source_business_id == doc.id,
                Task.source_key == f'contract:signed-upload:{doc.id}',
                Task.status.in_(('pending', 'doing')),
            ).limit(1)
        )
    ).scalars().first()
    if task is None:
        return

    approver_label = '已通过' if new_status == 'approved' else '已被驳回'
    if new_status == 'approved':
        # 截止日按配置的天数往前推（默认 7 天），并且**必须走业务时区**：
        # 直接 `datetime.combine(day, time(23,59), tzinfo=UTC)` 会把北京时间
        # 当成 UTC，截止时刻早 8 小时还跨一天（`timebase.business_day_end` 的说明）。
        days = int(
            await settings_service.get_number(session, 'contract', 'countersign_days', 7)
        )
        task.due_at = business_day_end(today_business() + timedelta(days=days))
        task.priority = 'high'
    content = (
        f'合同 {doc.doc_no} 的钉钉签署审批{approver_label}。'
        + (
            '请跟进客户盖章并登记回签文件，登记后这条待办会自动完成。'
            if new_status == 'approved'
            else '请到钉钉查看处理意见；要重新发起请回到合同台账。'
        )
    )
    from app.modules.notification import service as notification_service

    await notification_service.notify(
        session,
        user_id=task.owner_id,
        type_='system',
        title=f'合同 {doc.doc_no} 的钉钉审批{approver_label}',
        content=content,
        business_type='contract',
        business_id=doc.id,
    )


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
                or_(BusinessFile.category.is_(None), BusinessFile.category != "oa_attachment"),
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
        "sync_notice": (
            '钉钉连接当前已关闭，审批记录不会自动更新，请联系管理员确认。'
            if app_settings.dingtalk_push_off else
            '当前服务未开启审批自动同步，记录可能滞后，请联系管理员确认。'
            if not (app_settings.scheduler_enabled or app_settings.scheduler_oa_sync_enabled) else None
        ),
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


# ====================================================== 人员绑定（钉钉登录用）==
#
# 目的：把「钉钉成员」和「CRM 账号」对上，结果写进 `users.dingtalk_userid`。
# 有了这一列，以后从钉钉扫码 / 工作台免登进来的人，系统才知道他是**谁**。
#
# 三条纪律（与 `wecom/service.py::sync_users` 是**同一套**，不是另写一份）：
#
# 1. **通讯录同步只绑定、不建账号。** 首次登录可为全新员工建业务员账号；
#    同步本身不创建账号，已有未绑定账号仍交管理员明确绑定。
# 2. **匹配顺序：已绑定 → 手机号 → 姓名。** 手机号在前，因为它唯一性强；
#    姓名只做兜底，且**只有唯一命中才算**——重名时宁可留给人工判，也不猜。
# 3. **已绑定的不覆盖。** 一个人已经绑了钉钉号，后续同步不因手机号变了就改绑：
#    改绑等于把别人的登录身份换掉，那必须由管理员在界面上明确操作。
#
# ⚠️ 与企微那套的差别只有"数据从哪来"：企微是先落一张 `wecom_users` 映射表、
# 人再慢慢归一；钉钉这里**不留中间表**——点一次按钮当场对完，对不上的直接列给
# 管理员看。绑定的全部内容就是"一个人 ↔ 一个编号"，不需要中间态。

#: 对不上的原因。**分开报**，不能笼统说一句"未匹配"——
#: 「CRM 里没这个账号」和「重名判不了」要做的事完全不同（前者去建号，后者去核人）。
BIND_REASON_LABEL: dict[str, str] = {
    "no_account": "尚无 CRM 账号，全新员工首次钉钉登录会自动建为业务员",
    "ambiguous_mobile": "手机号在系统里对到了多个账号",
    "ambiguous_name": "姓名重复，无法判断是哪一位",
}


def _norm_mobile(value: Any) -> str:
    """手机号比对口径：只留数字。

    钉钉给的是 `13800000000`，人工在 CRM 里可能填成 `138 0000 0000`。
    不归一化就会冒出"明明是同一个人却说对不上"。

    **只去非数字字符，不剥区号**：`+86` 前缀在两边不一致时，宁可对不上留给人工，
    也不能把 `8613800000000` 猜成同一个人——猜错就是把两个人绑串了，
    而绑串的后果是"张三用李四的身份登录"。
    """
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _norm_name(value: Any) -> str:
    return str(value or "").strip()


async def fetch_all_members(api: Any) -> list[dict[str, Any]]:
    """拉全公司成员：从根部门（id=1）广度优先走完部门树，再按 userid 去重。

    为什么要自己走树：钉钉**没有**"全公司成员"这一个接口，只有"某部门的直属成员"，
    而一个人可以同时挂在多个部门下 —— 不去重的话他会以多个身份出现，
    匹配阶段自己和自己撞（第一次绑上了，第二次又把它判成"已占用"）。
    """
    members: dict[str, dict[str, Any]] = {}
    pending: list[int] = [1]
    seen: set[int] = set()
    while pending:
        dept_id = pending.pop(0)
        if dept_id in seen:
            continue
        seen.add(dept_id)

        for sub in await api.list_sub_departments(dept_id):
            try:
                sub_id = int(sub.get("dept_id"))
            except (TypeError, ValueError):
                # 少 dept_id 的节点没法继续往下走，跳过；
                # 不为一个坏节点中断整棵树（否则全公司的人都拉不到）
                continue
            if sub_id not in seen:
                pending.append(sub_id)

        cursor = 0
        while True:
            page = await api.list_department_users(dept_id, cursor=cursor)
            for item in page.get("list") or []:
                uid = str(item.get("userid") or "")
                if uid and uid not in members:
                    members[uid] = item
            if not page.get("has_more"):
                break
            next_cursor = page.get("next_cursor")
            # 反复给同一个 cursor 会死循环（接口异常时的兜底），宁可少拉一页
            if next_cursor in (None, cursor):
                break
            cursor = int(next_cursor)
    return list(members.values())


async def sync_users(session: AsyncSession) -> dict:
    """把 CRM 账号与钉钉成员对上，写 `users.dingtalk_userid`。**不建账号。**

    判据顺序见本段开头三条纪律。返回里把"对不上的"和"还没绑的"分开列出来：
    前者要管理员去处理，后者只是提示"这个人还进不来"。
    """
    # 登录和审批都需要真实人员绑定；只开任一项都能同步，但不因此开启另一项。
    if app_settings.dingtalk_login_off and app_settings.dingtalk_push_off:
        return {
            "disabled": True,
            "message": DINGTALK_LOGIN_DISABLED_MESSAGE,
            "total": 0,
            "bound": 0,
            "already_bound": 0,
            "unmatched": [],
            "unbound_crm": [],
        }

    api = get_client()
    try:
        members = await fetch_all_members(api)
    except DingTalkDisabled as exc:
        # 走到这里说明闸是在**取通讯录的过程中**才关掉的（比如刚被人关掉）。
        # 用异常自己那句话：它认得清是哪把闸，别硬套审批总闸的文案。
        return {
            "disabled": True,
            "message": str(exc),
            "total": 0,
            "bound": 0,
            "already_bound": 0,
            "unmatched": [],
            "unbound_crm": [],
        }
    except DingTalkNotConfigured as exc:
        # 缺凭据是"服务端没配好"，不是对方的错 → 503，并把缺哪一项说清楚
        raise AppError(ErrorCode.EXTERNAL_ERROR, str(exc), 503) from exc
    except DingTalkError as exc:
        raise AppError(
            ErrorCode.EXTERNAL_ERROR, f"读取钉钉通讯录失败：{exc}", 502
        ) from exc
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        # 通讯录分页是外部响应边界；网络中断、非 JSON 或结构异常都应翻译成
        # 可重试的外部错误，不能让设置页直接收到 500。
        raise AppError(
            ErrorCode.EXTERNAL_ERROR,
            f"读取钉钉通讯录失败（{type(exc).__name__}）：{exc}",
            502,
        ) from exc

    await _lock_login_binding(session)
    crm_users = list((await session.execute(select(User))).scalars().all())

    bound: dict[str, User] = {}
    free_by_mobile: dict[str, list[User]] = {}
    free_by_name: dict[str, list[User]] = {}
    for row in crm_users:
        if row.dingtalk_userid:
            bound[str(row.dingtalk_userid)] = row
            continue
        mobile = _norm_mobile(row.mobile)
        if mobile:
            free_by_mobile.setdefault(mobile, []).append(row)
        name = _norm_name(row.name)
        if name:
            free_by_name.setdefault(name, []).append(row)

    newly_bound = 0
    already_bound = 0
    unmatched: list[dict[str, Any]] = []

    def _bind(target: User, dingtalk_userid: str) -> None:
        nonlocal newly_bound
        target.dingtalk_userid = dingtalk_userid
        bound[dingtalk_userid] = target
        # 立刻从"待匹配"里摘掉：同一次同步内，一个人只能被绑一次，
        # 否则后面同一个姓名的成员会把它当成候选再次命中。
        mobile = _norm_mobile(target.mobile)
        if mobile and target in free_by_mobile.get(mobile, []):
            free_by_mobile[mobile].remove(target)
        name = _norm_name(target.name)
        if name and target in free_by_name.get(name, []):
            free_by_name[name].remove(target)
        newly_bound += 1

    for member in members:
        dingtalk_userid = str(member.get("userid") or "")
        if not dingtalk_userid:
            continue
        if dingtalk_userid in bound:
            # 这个钉钉号已经有主了：**保持不动**（含本次刚绑的，见纪律 1）
            already_bound += 1
            continue

        name = _norm_name(member.get("name"))
        mobile = _norm_mobile(member.get("mobile"))
        display_mobile = str(member.get("mobile") or "")

        candidates = free_by_mobile.get(mobile, []) if mobile else []
        if len(candidates) == 1:
            _bind(candidates[0], dingtalk_userid)
            continue
        if len(candidates) > 1:
            unmatched.append(
                {
                    "name": name,
                    "mobile": display_mobile,
                    "reason": "ambiguous_mobile",
                    "reason_label": BIND_REASON_LABEL["ambiguous_mobile"],
                }
            )
            continue

        candidates = free_by_name.get(name, []) if name else []
        if len(candidates) == 1:
            _bind(candidates[0], dingtalk_userid)
            continue
        if len(candidates) > 1:
            unmatched.append(
                {
                    "name": name,
                    "mobile": display_mobile,
                    "reason": "ambiguous_name",
                    "reason_label": BIND_REASON_LABEL["ambiguous_name"],
                }
            )
            continue

        unmatched.append(
            {
                "name": name,
                "mobile": display_mobile,
                "reason": "no_account",
                "reason_label": BIND_REASON_LABEL["no_account"],
            }
        )

    await session.flush()

    # 还没绑上钉钉的 CRM 账号：不是错误，但管理员要知道"还有谁进不来"。
    # 只列**在用**的账号——停用的人绑不绑都不影响登录。
    unbound_crm = [
        {"id": row.id, "name": row.name, "username": row.username}
        for row in crm_users
        if not row.dingtalk_userid and row.status == "active"
    ]

    return {
        "disabled": False,
        "total": len(members),
        "bound": newly_bound,
        "already_bound": already_bound,
        "unmatched": unmatched,
        "unbound_crm": unbound_crm,
    }


# ---- 扫码登录 -------------------------------------------------------------
#
# 两样**短期信物**，寿命都是几十秒：
#
# - `state`：发起扫码时生成，回调时校验并**消费**。防的是"别人换个 code 把回调
#   塞给你"——只有我们自己发出去过的 state 才认。
# - `ticket`：回调处理完之后，把**已经签好的令牌**装进去，交给前端那一步用。
#   走 URL 的绝不能是令牌本身（会进浏览器历史、会进服务器访问日志），
#   所以用一层一次性信物过渡，60 秒内不来换就作废。
#
# 为什么放进程内、不加表：本项目部署口径是**单实例**（与 `core/rate_limit.py`
# 同一理由），而这两样只活几十秒。为它们建表 + 加迁移，换来的不过是"重启后
# 正在扫码的人不用重来"，不划算。将来真上多实例，整体换 Redis 即可，
# 下面四个函数的签名不用变。
#
# ⚠️ 与会话有效性（`auth/session.py::load_active_session`）**不是一回事**：
# 那个决定"一把令牌还能不能用"，必须只存库、绝不能有内存缓存；
# 这里的 state/ticket 只是"两步之间的一次性信物"，用完即弃。

LOGIN_STATE_TTL_SECONDS = 300
LOGIN_TICKET_TTL_SECONDS = 60

_short_lock = threading.Lock()
#: state -> 过期时刻（monotonic 秒）
_login_states: dict[str, float] = {}
#: ticket -> (已签发的令牌, 过期时刻)
_login_tickets: dict[str, tuple[str, float]] = {}


def _sweep_states(now: float) -> None:
    for k in [k for k, exp in _login_states.items() if exp <= now]:
        _login_states.pop(k, None)


def _sweep_tickets(now: float) -> None:
    for k in [k for k, (_, exp) in _login_tickets.items() if exp <= now]:
        _login_tickets.pop(k, None)


def issue_login_state() -> str:
    """发起一次扫码登录：生成并记住一个一次性 state。"""
    now = time.monotonic()
    state = secrets.token_urlsafe(24)
    with _short_lock:
        _sweep_states(now)
        _login_states[state] = now + LOGIN_STATE_TTL_SECONDS
    return state


def consume_login_state(state: str | None) -> bool:
    """校验并**消费** state：用过一次即作废（防重放）。"""
    if not state:
        return False
    now = time.monotonic()
    with _short_lock:
        expires = _login_states.pop(state, None)
        _sweep_states(now)
    return expires is not None and expires > now


def issue_login_ticket(token: str) -> str:
    """把一张**已经签好的令牌**装进一次性 ticket，交给前端换走。"""
    now = time.monotonic()
    ticket = secrets.token_urlsafe(32)
    with _short_lock:
        _sweep_tickets(now)
        _login_tickets[ticket] = (token, now + LOGIN_TICKET_TTL_SECONDS)
    return ticket


def consume_login_ticket(ticket: str | None) -> str | None:
    """取走 ticket 里的令牌；用过 / 过期 / 不认识一律返回 None。"""
    if not ticket:
        return None
    now = time.monotonic()
    with _short_lock:
        item = _login_tickets.pop(ticket, None)
        _sweep_tickets(now)
    if item is None:
        return None
    token, expires = item
    return token if expires > now else None


#: 扫码登录被拒的原因 → 给人看的说明。**拒绝理由要分开报**：
#: "钉钉那边出错了"和"你这个人进不来"是两件事，混成一句"登录失败"让人无从下手。
LOGIN_REASON_LABEL: dict[str, str] = {
    "not_in_contact": "这个钉钉账号不在企业通讯录里，请联系管理员把你加进部门",
    "not_bound": "这个钉钉账号还没和系统账号绑定，请管理员先在「系统设置」里点一次「同步钉钉人员」",
    "disabled": "这个账号已停用，请联系管理员",
    "ambiguous": "这个钉钉账号对上了多个系统账号，请联系管理员处理",
    "no_unionid": "钉钉没有返回这个人的身份标识，请重试或联系管理员",
    "role_unavailable": "默认业务员角色未启用或数据范围不是本人，请管理员检查角色设置",
    "invalid_profile": "企业通讯录身份资料不完整，请管理员核对姓名和成员编号",
}


async def resolve_login_user(
    session: AsyncSession, *, code: str
) -> tuple[User | None, str]:
    """用一次性授权码换出 CRM 用户，返回 `(user, reason)`。

    `reason == "ok"` 时 user 一定有值；否则 user 为 None，reason 是
    `LOGIN_REASON_LABEL` 里的键。外部调用本身失败会直接抛 `DingTalkError`，
    由接口层翻成"钉钉那边出错了"——与"这个人进不来"分开报。

    链路：code → 用户级令牌 → unionId → userid → `users.dingtalk_userid`。
    **身份只由服务端换**，调用方给什么都不信。
    """
    api = get_client()
    user_token = await api.exchange_user_token(code)
    info = await api.get_login_userinfo(user_token)
    unionid = str(info.get("unionId") or "").strip()
    if not unionid:
        return None, "no_unionid"

    userid = await api.userid_by_unionid(unionid)
    if not userid:
        return None, "not_in_contact"

    # 一个钉钉号只该对上一个人；真出现多条，宁可明确拒绝也不"取第一个"
    # （取第一个 = 把别人的账号登进来，与 `sync_users` 的"重名一律不猜"同一条纪律）。
    rows = (
        (await session.execute(select(User).where(User.dingtalk_userid == userid).limit(2)))
        .scalars()
        .all()
    )
    if not rows:
        # 已有账号由管理员绑定，不根据扫码昵称自动认领。企业资料只用来
        # 排除已有账号/冲突，以及给真正的新员工建立最低默认角色。
        member = await api.get_login_contact_user(userid)
        if str(member.get("userid") or "").strip() != str(userid):
            return None, "invalid_profile"
        if member.get("unionid") and str(member["unionid"]) != unionid:
            return None, "invalid_profile"
        return await _signup_login_user(session, userid=str(userid), member=member)
    if len(rows) > 1:
        return None, "ambiguous"
    user = rows[0]
    if user.status != "active":
        return None, "disabled"
    return user, "ok"


async def _lock_login_binding(session: AsyncSession) -> None:
    """首次建号与管理员通讯录绑定互斥，避免同一个钉钉号并发造两个人。"""
    try:
        dialect = session.get_bind().dialect.name
    except AttributeError:  # 内存数据库测试适配器
        dialect = ""
    if dialect == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('crm:dingtalk-binding', 0))"))


async def _signup_login_user(session: AsyncSession, *, userid: str, member: dict) -> tuple[User | None, str]:
    from app.core.audit import write_audit
    from app.core.security import hash_password

    name = _norm_name(member.get("name"))
    mobile = _norm_mobile(member.get("mobile"))
    if not name or len(name) > 64 or len(userid) > 128 or (mobile and len(mobile) > 32):
        return None, "invalid_profile"
    await _lock_login_binding(session)
    # 网络请求期间可能已被其他扫码/同步绑定。拿锁后重新读取，不靠旧快照。
    users = list((await session.execute(select(User).execution_options(populate_existing=True))).scalars())
    bound = [u for u in users if str(u.dingtalk_userid or "") == userid]
    if len(bound) > 1:
        return None, "ambiguous"
    if bound:
        return (bound[0], "ok") if bound[0].status == "active" else (None, "disabled")
    # 同名未绑定账号、停用账号和手机号冲突交管理员处理，不自动认领角色。
    # 已绑定且启用的同名同事是另一个身份，允许建号，由登录名编号区分。
    if any((_norm_name(u.name) == name and (not u.dingtalk_userid or u.status != "active"))
           or (mobile and _norm_mobile(u.mobile) == mobile) for u in users):
        return None, "not_bound"
    role = (await session.execute(select(Role).where(Role.code == "salesperson")
                .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if role is None or role.status != "active" or role.data_scope != "self":
        return None, "role_unavailable"
    from app.modules.user.usernames import allocate_username
    username = await allocate_username(session, name)
    if any(u.username == username for u in users):
        return None, "not_bound"
    # 用户明确指定：姓名全拼登录名、初始密码 123456，首次进入告知本人。
    user = User(username=username, name=name, mobile=mobile or None,
                password_hash=hash_password("123456"), initial_password_pending=True, status="active",
                dingtalk_userid=userid, department_id=None)
    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
            await session.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
    except IntegrityError:
        # 管理员也能建同名登录名，唯一冲突应拒绝本次建号而非冒成 500。
        return None, "not_bound"
    await write_audit(session, operator_id=user.id, action="create", business_type="user", business_id=user.id,
                      after={"username": user.username, "name": user.name, "roles": [role.code],
                             "data_scope": "self", "source": "钉钉首次登录自动建号"})
    return user, "ok"
