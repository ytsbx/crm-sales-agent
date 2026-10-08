"""规则执行：公海自动回收与自动任务生成。

这两件事原本写死在代码里，现在改成读规则表执行——业务想调天数不用找我改代码。
"""

"""规则执行：公海自动回收与自动任务生成。

另外这里集中放**所有可配置的业务参数**：代码里不再出现"魔法数字"，
每个数字都有一个配置键，管理员在系统设置界面上就能改。
"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.timebase import today_business
from app.modules.customer import duplicates
from app.modules.customer.model import Customer, CustomerOwnerHistory
from app.modules.order.model import ORDER_STATUS_LABEL, SalesOrder
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.quote.model import QUOTE_STATUS_LABEL, Quote, QuoteVersion
from app.modules.sample.model import (
    CONFIRM_ACCEPTED,
    CONFIRM_STATUS_LABEL,
    SAMPLE_OPEN_STATUSES,
    SAMPLE_STATUS_LABEL,
    SampleRequest,
)
from app.modules.settings.model import (
    RECYCLE_OPEN_STATUSES,
    RECYCLE_STATUS_LABEL,
    PublicPoolRecycleCandidate,
    PublicPoolRule,
    TaskRule,
)
from app.modules.task.model import Task
from app.modules.task.scanning import lock_task_scan
from app.modules.followup.model import FollowUp
from app.modules.user.model import User

# ---------------------------------------------------------------- 可配置参数
#
# 约定：键名 → {字段名: 默认值}。数据库里有同名的 SystemSetting 记录时以数据库为准，
# 所以业务想改数字不用改代码，改了立刻生效。
DEFAULT_SETTINGS: dict[str, dict] = {
    # 报价
    "company_name": {"text": "示例公司（请替换为公司全称）"},
    "quote_valid_days": {"days": 30},
    "default_payment_terms": {"text": "款到发货"},
    "default_delivery_terms": {"text": "含运费，送货上门"},
    # 核价
    "default_target_margin": {"ratio": 0.30},
    "default_min_margin": {"ratio": 0.15},
    "price_range_ratio": {"ratio": 0.04},
    # 绝对底价（D7 判定层 C 步，方案 §8）：命中即 422 硬拒、不生成审批单——
    # 与最低保护价（触发审批、可被批准）严格区分，任何审批角色都不能通过。
    # mode: off=不启用（默认，等业务给口径不替业务拍板）/ cost=不得低于生效成本
    #（商品成本+物流成本）/ cost_markup=不得低于 成本×(1+markup_ratio)。
    # 后续 A 步（价格规则/客户特殊价上的 hard_floor_price 覆盖列）落库后，
    # 列有值时覆盖这里的比例判定，无值时仍按本配置算——两套机制叠加而非二选一。
    "hard_floor": {"mode": "off", "markup_ratio": 0.0},
    # 导出闸门（§11.2/场景19）：单次导出条数上限，超过需缩小筛选范围
    # 导出闸门（§11.2/场景19）：单次上限 + 异常批量访问告警阈值（§六）
    "export": {"limit": 5000, "alert_rows": 20000, "alert_window_hours": 24},
    # 通知投递重试（文档 §六：「发送失败保留业务记录并重试通知」）：
    # 企微投递失败按退避自动重试（第 1/2/3 次失败后 5/30/120 分钟再试），
    # 到 max_attempts 后停在 failed 等人看——不再无限重试打接口，
    # 也不再像原来那样"失败即永久丢失"。skipped（没绑企微）不自动重试，
    # 由设置页的人工补投触发。
    "notification_retry": {
        "enabled": True,
        "max_attempts": 3,
        "backoff_minutes": [5, 30, 120],
    },
    # 物流试算：体积重系数（每立方米折多少公斤）。
    # **默认 0 = 不启用体积重，计费重只取实际重量**。
    # 为什么不给默认值：这个系数强依赖货物形态。纸箱类轻抛货通用 167（≈6000cm³/kg），
    # 但嵌套运输的塑料周转箱密度只有约 25 kg/m³，套 167 会让体积重变成实际重量的
    # 十几倍、运费超过货值。PRD §14 只要求输出「计费重」，没给系数口径，
    # 所以这里留 0，等业务给出真实口径再配——不替业务拍板。
    "logistics_volumetric_ratio": {"number": 0},
    # 工作台预警
    "customer_stale_days": {"days": 30},
    # 客户「活跃」口径：最近多少天内有跟进算活跃。
    # 与上面的「沉睡」分开配置，因为业务上"活跃"的门槛通常比"该回收了"更紧。
    "customer_active_days": {"days": 30},
    "opportunity_risk_days": {"days": 7},
    "opportunity_stale_days": {"days": 14},
    # 审批分级：按报价总额决定走到哪一级；role_codes 决定谁有权批这一级
    "approval_levels": {
        "levels": [
            {"node": "manager", "label": "销售主管", "max_amount": 50000, "role_codes": ["sales_manager"]},
            {
                "node": "director",
                "label": "销售经理",
                "max_amount": None,
                "role_codes": ["sales_manager"],
            },
        ]
    },
    # 客户查重：各维度权重，总分 ≥ 阈值即视为"疑似重复"
    # 默认值的取值逻辑：
    #   税号相同 → 必然同一家，单独就该提示（60 ≥ 阈值 50）
    #   名称互相包含（简称 vs 全称）→ 单独就该提示（55 ≥ 阈值 50）
    #   手机号相同 → 同一个人，但可能服务于不同公司，单独不提示、配合名称即提示（45）
    "dedup_scoring": {
        "threshold": 50,
        "weight_name_exact": 70,
        "weight_name_contains": 55,
        "weight_mobile": 45,
        "weight_tax_no": 60,
        "weight_domain": 30,
        "weight_address": 15,
    },
    # 贸易模式只影响"界面要不要显示外贸相关输入"，不影响数据结构——
    # 字段一直都在，内贸场景下留空即可，所以不存在"以后改回来"的成本。
    "trade_mode": {"mode": "domestic"},
    "default_currency": {"code": "CNY"},
    "export_tax_refund_rate": {"ratio": 0.0},
    # ---------------------------------------------------------------- 公海回收（返工单 6.3）
    #
    # 这三项是**业务口径，不能由开发随手写死**（审视方点名要求可配置）。
    # 这里给的是"看起来合理"的默认值，管理员在「系统设置」里随时能改，改完立刻生效。
    #: 预告提前多久：扫描命中后先预告这么多天，期间业务员仍可跟进自救。
    #:
    #: ⚠️ **7 天是开发默认值，不是已经确认的正式业务规则**（追加口径 1）。
    #: 界面上会照实标注；改成多少由业务定，改完立刻对新提名的候选生效。
    #: 已经在跑的候选**不受影响** —— 它们各自记着生成时的天数与绝对到期时间。
    "pool_recycle_notice_days": {"days": 7},
    #: 暂缓期限：主管点"暂缓"之后要等这么多天才能再批准回收（暂缓期内仍可驳回）。
    #:
    #: ⚠️ **30 天同样是开发默认值**，同上：口径未定，界面上标明。
    "pool_recycle_defer_days": {"days": 30},
    #: 恢复操作需要哪个权限码。做成配置而不是写死角色：
    #: "谁能把回收掉的客户还回去"是管理口径，不同公司不一样。
    #: 默认 `customer:assign` —— 销售主管有这个权限，普通业务员没有。
    #: 保存时会校验这个码在权限表里真实存在（写错等于谁都恢复不了）。
    "pool_recycle_restore_permission": {"text": "customer:assign"},
    # 通知渠道（PRD §25 要求"站内 + 企业微信"）。
    # 默认只开站内：企微投递依赖 WECOM_AGENT_ID 与每个用户的 wecom_userid，
    # 没配好之前开成默认会每次都失败，反而把"真失败"淹掉。
    "notification_channels": {
        "inapp_enabled": True,
        "wecom_enabled": False,
        # 哪几类通知走企微；站内通知始终全发
        "wecom_events": {
            "approval": True,
            "task": True,
            "payment": True,
        },
    },
    # 通知分级（文档 §11.4 验收 24）：逐次推 还是 攒进日报，属业务决策
    # （文档 §九 列为待批准事项）。默认**全部即时推**，与分级上线前完全一致——
    # 机制先备好，口径等批准后在设置里改：
    #   {"default_level": "normal",
    #    "by_type": {"followup": "digest", "approval": "urgent"}}
    # urgent/normal 即时推；digest 攒进日报合成一条；紧急项不进日报。
    "notification_levels": {
        "default_level": "normal",
        "by_type": {},
    },
    # 钉钉 OA 询价审批（文档 §11.3 :152 / 场景11）。
    # **模板与字段映射是配置，不是代码**：业务/IT 定了用哪个模板，
    # 只改这里就能跑。字段映射的 key 必须是钉钉控件的 **componentId**
    # （不是中文名——名字对不上钉钉不报错，只把那格留空，"免重复录入"会静默失效）。
    # 例：{"process_code": "PROC-XXXX", "field_map": {"title": "TextField_XXXX", "quantity": "NumberField_XXXX"}}
    "dingtalk_oa": {
        "process_code": "",
        "field_map": {},
        # 谁发起：owner = 需求负责人对应的人；不填则用当前操作者
        "originator_source": "operator",
    },
}


#: 配置项的**取值规则**（返修单第六批追加口径 1："后端校验参数合法性"）。
#:
#: 只列需要校验的项；没列的照旧存任意 JSON。
#: 放在服务层而不是路由里：接口、以后的批量导入、脚本改配置都共用一份，
#: 不会出现"页面上挡得住、脚本里绕过"的口子。
#:
#: 为什么必须挡在入口：预告期写成 -3 天，候选的到期时间会直接算成"过去"，
#: 于是扫描一提名就立刻可回收；而这类错值往往要到半夜定时任务里才现形。
SETTING_NUMBER_RULES: dict[str, tuple[str, int, int, str]] = {
    # key: (字段名, 下限, 上限, 中文名)
    "pool_recycle_notice_days": ("days", 0, 365, "回收预告期"),
    "pool_recycle_defer_days": ("days", 0, 365, "主管暂缓等待期"),
}


def _strict_int(raw: object, label: str, low: int, high: int) -> int:
    """把天数配置严格读成整数（返修单 R14）。

    此前用的是 `int(raw)`，它有两个安静的坑：

    - `int(1.9)` → **1**：界面上明明填的是 1.9，落库变成 1，
      保存值、展示值、执行值三者不一致，事后对账对不上；
    - `int(True)` → **1**：布尔是 int 的子类，真/假会被当整数收下。

    现在只认两种输入：**真正的整数**，和**纯数字字符串**（表单可能传字符串）。
    小数、布尔、空、负数串、其它类型一律 422，并说清收到的是什么。
    """
    if isinstance(raw, bool):
        # 必须先挡布尔：bool 是 int 的子类，不挡的话 True 会静默变成 1
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{label}必须是整数天（{low}–{high}），不能填真/假",
            422,
        )
    if isinstance(raw, int):
        number = raw
    elif isinstance(raw, str) and re.fullmatch(r"[0-9]+", raw.strip()):
        number = int(raw.strip())
    else:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{label}必须是整数天（{low}–{high}），收到的不是整数：{raw!r}",
            422,
        )
    if number < low or number > high:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{label}须在 {low}–{high} 天之间，当前填的是 {number}",
            422,
        )
    return number


async def validate_setting_value(
    session: AsyncSession, key: str, value: dict | None
) -> None:
    """保存配置前的合法性校验。不合法直接 422，不让坏值落库。"""
    rule = SETTING_NUMBER_RULES.get(key)
    if rule is not None:
        field, low, high, label = rule
        _strict_int((value or {}).get(field), label, low, high)
        return

    if key == "pool_recycle_restore_permission":
        from app.modules.user.model import Permission

        code = str((value or {}).get("text") or "").strip()
        if not code:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                "恢复权限不能留空（默认 customer:assign）",
                422,
            )
        exists = (
            await session.execute(select(Permission.code).where(Permission.code == code))
        ).first()
        if exists is None:
            # 写错一个字母，界面上看起来"设了权限"，实际谁都恢复不了 —— 挡在这里
            raise AppError(
                ErrorCode.PARAM_ERROR, f"权限码「{code}」不在系统权限表里，请核对", 422
            )


async def recompute_open_candidates(
    session: AsyncSession, *, key: str, days: int, now: datetime | None = None
) -> int:
    """改完「预告期 / 暂缓期」天数后，把**还在等的候选**一起重算到期时间。

    口径由主人 2026-10-06 定：**一起重算**。

    此前是"只影响以后新提名的候选，已经在跑的按生成时的天数走完"。主人要求
    改成跟着变 —— 管理员一改配置，所有还没结案的候选都按新天数重新算，
    避免库里同时跑着两套天数。

    只动 `pending` / `deferred` 这两种"还在等"的状态：
    `executed` / `rejected` / `superseded` / `restored` 已经结案，
    重算它们等于改历史，不动。

    `notice_at` / `decided_at` 是**当时的绝对时间点**（提名那一刻、暂缓那一刻），
    用它俩当基准加新天数，才不会把已经过去的时间也算进去。

    返回重算了几条，供审计和界面提示用。
    """
    moment = now or datetime.now(UTC)
    if key == "pool_recycle_notice_days":
        rows = (
            await session.execute(
                select(PublicPoolRecycleCandidate).where(
                    PublicPoolRecycleCandidate.status.in_(["pending", "deferred"])
                )
            )
        ).scalars().all()
        for row in rows:
            row.notice_days = days
            row.due_at = (row.notice_at or moment) + timedelta(days=days)
        return len(rows)

    if key == "pool_recycle_defer_days":
        rows = (
            await session.execute(
                select(PublicPoolRecycleCandidate).where(
                    PublicPoolRecycleCandidate.status == "deferred"
                )
            )
        ).scalars().all()
        for row in rows:
            row.defer_days = days
            row.deferred_until = (row.decided_at or moment) + timedelta(days=days)
        return len(rows)

    return 0


async def get_list(session: AsyncSession, key: str, field: str = "levels") -> list:
    value = await get_setting(session, key)
    raw = value.get(field)
    return raw if isinstance(raw, list) else []


async def get_setting(session: AsyncSession, key: str) -> dict:
    """读一个配置项：数据库优先，没有则用代码里的默认值。"""
    from app.modules.settings.model import SystemSetting

    row = (
        await session.execute(select(SystemSetting).where(SystemSetting.key == key))
    ).scalar_one_or_none()
    if row is not None and row.value:
        return row.value
    return DEFAULT_SETTINGS.get(key, {})


async def get_number(
    session: AsyncSession, key: str, field: str, fallback: float
) -> float:
    """读配置项里的数值字段；缺失或格式不对时用 fallback，避免因配置错误把业务搞挂。"""
    value = await get_setting(session, key)
    raw = value.get(field)
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


async def get_text(session: AsyncSession, key: str, field: str = "text", fallback: str = "") -> str:
    value = await get_setting(session, key)
    raw = value.get(field)
    return str(raw) if raw not in (None, "") else fallback


def _last_active_at(customer: Customer) -> datetime | None:
    """活跃时钟 = max(最近有效联系, 最近业务进展)，都没有退回建档时间。

    文档 §11.2：冷落/回收若只看手工跟进时间，"正在履约但没点记录跟进"
    的客户会被误判。业务进展（报价/打样/下单/回款）刷新的
    last_progress_at 同样算活跃。
    """
    candidates = [
        value
        for value in (customer.last_followup_at, customer.last_progress_at)
        if value is not None
    ]
    candidates = [value if value.tzinfo else value.replace(tzinfo=UTC) for value in candidates]
    latest = max(candidates) if candidates else customer.created_at
    return latest if latest is None or latest.tzinfo else latest.replace(tzinfo=UTC)


#: 构成"履约保护"的报价状态：**必须已经正式对客发出去**。
#:
#: 原来只判"未删除 + 在有效期内"，于是**草稿报价**也算保护 ——
#: 业务员建个草稿放在那儿，这个客户就永远不会进公海回收。
#: 客户已经拒绝（`declined`）的也一样：那笔生意黄了，不该继续保护。
#: `pending_approval` / `approved` 同理 —— 还没发给客户，客户根本不知道有这回事。
QUOTE_PROTECTIVE_STATUSES = ("sent", "accepted")

#: 客户已明确接受样品的确认状态 —— 直接引 `sample/model.CONFIRM_ACCEPTED`，
#: 不再自己抄一份字面量：抄一份就多一处会跟源定义漂开的地方。
SAMPLE_CONFIRM_ACCEPTED = CONFIRM_ACCEPTED


def _protection_reason(label: str, detail: str) -> str:
    """拼一句人看得懂的保护原因（主管复核时要能一眼看出是哪张单拦住的）。"""
    return f"{label}：{detail}"


async def protection_detail(session: AsyncSession) -> dict[int, list[str]]:
    """履约保护名单 + **每一笔的具体原因**（返工单 6.4）。

    返回 `{客户id: ["在途订单 SH2024-001（生产中）", "有效报价 QT2024-018（已发送，有效期 2026-12-31）"]}`。

    为什么要把原因也返回：只给一个 `set[int]` 的话，主管看到"这个客户没被回收"
    却不知道**是被哪张单据拦住的**，想去催也只能靠猜。回收预告页要用它。
    """
    # 回收预告里的"今天"按业务日期（§9.10 复审）：与报价过期、客户动态同一个基准
    today = today_business()
    out: dict[int, list[str]] = {}

    def add(customer_id, reason: str) -> None:
        if customer_id is None:
            return
        out.setdefault(int(customer_id), []).append(reason)

    # ---- 有效报价：**正式对客 + 未失效** ----
    for cid, quote_no, status, valid_until in (
        await session.execute(
            select(Quote.customer_id, Quote.quote_no, Quote.status, Quote.valid_until).where(
                Quote.deleted_at.is_(None),
                Quote.status.in_(QUOTE_PROTECTIVE_STATUSES),
                Quote.valid_until.is_not(None),
                Quote.valid_until >= today,
            )
        )
    ).all():
        add(cid, _protection_reason("有效报价", f"{quote_no}（{QUOTE_STATUS_LABEL.get(status, status)}，有效期至 {valid_until}）"))

    # ---- 在途订单 ----
    for cid, order_no, status in (
        await session.execute(
            select(SalesOrder.customer_id, SalesOrder.order_no, SalesOrder.status).where(
                SalesOrder.status.in_(("pending", "in_production", "shipped", "delivered"))
            )
        )
    ).all():
        add(cid, _protection_reason("在途订单", f"{order_no}（{ORDER_STATUS_LABEL.get(status, status)}）"))

    # ---- 未结应收 ----
    for cid, order_no in (
        await session.execute(
            select(SalesOrder.customer_id, SalesOrder.order_no)
            .join(ReceivablePlan, ReceivablePlan.order_id == SalesOrder.id)
            .where(
                ReceivablePlan.status.in_(("pending", "partial", "overdue")),
                SalesOrder.status != "cancelled",
            )
        )
    ).all():
        add(cid, _protection_reason("未结应收", f"{order_no} 尚有节点未收完"))

    # ---- 打样：**客户明确确认接受之后，这张单不再保护**（已确认口径，返工单 6.4）----
    #
    # 原实现是 `status != 'rejected'`：只看"审批有没有驳回"，
    # **完全没看客户的确认结果**。于是客户早就回复"样品可以，确认接受"、
    # 之后一直没下单也没再联系，这张历史打样仍然年年保护着他，永远进不了回收流程。
    #
    # 现在的边界：
    # - 已确认接受（`confirm_status='accepted'`）→ **这张单不再保护**
    #   （这次打样任务已经完成；客户如果还有订单/未结回款/有效报价/另一张进行中的打样，
    #    那些事项各自保护，不需要这张单再顶一遍）；
    # - 仅签收、还没给结论（`pending`）→ 继续保护（客户还在看）；
    # - 未通过（`rejected`）→ 继续保护（多半要重新打样，这单还没完）。
    #
    # 注意打样单**没有单号字段**，界面与列表都用 `id` 标识，这里也照它显示。
    #
    # ---- 修订链：被新版本替代的旧版**不再独立承担保护**（返修单第六批第 9 条）----
    #
    # 打样修订会新建一张单、`parent_id` 指向旧版。此前保护判断完全不看这条链：
    # V1 签收、客户没通过，之后开了 V2 并**确认接受**，V1 依然挂在保护名单上，
    # 而 V1 早被冻结、改都改不了 —— 保护一个已经作废的版本，客户就永远进不了回收。
    #
    # 口径（已确认）：
    # - 被替代的历史版本**不独立保护**（资料与确认结果照旧留着，不删历史）；
    # - 最新有效版本未完成 → 继续按下面的规则保护；
    # - 最新版本客户已接受 → 这张单到此为止；
    # - 同客户另一张**独立**打样（不在链上）各自保护。
    #
    # "最新版"就是"没有任何单把它当 parent"的那张：V1→V2→V3 时 V1、V2 都出现在
    # 这个集合里，只有 V3 参与判断，规则自动成立，不用递归去追链头。
    superseded_ids = set(
        (
            await session.execute(
                select(SampleRequest.parent_id).where(SampleRequest.parent_id.is_not(None))
            )
        ).scalars().all()
    )
    sample_stmt = select(
        SampleRequest.customer_id,
        SampleRequest.id,
        SampleRequest.status,
        SampleRequest.confirm_status,
    ).where(
        # 被审批驳回的单子不算保护
        SampleRequest.status != "rejected",
        SampleRequest.status.in_(SAMPLE_OPEN_STATUSES),
        # 客户已明确接受的：**这张单到此为止**
        SampleRequest.confirm_status.is_distinct_from(SAMPLE_CONFIRM_ACCEPTED),
    )
    if superseded_ids:
        sample_stmt = sample_stmt.where(SampleRequest.id.not_in(superseded_ids))
    for cid, sample_id, status, confirm in (await session.execute(sample_stmt)).all():
        label = SAMPLE_STATUS_LABEL.get(status, status)
        if confirm:
            label = f"{label} / {CONFIRM_STATUS_LABEL.get(confirm, confirm)}"
        add(cid, _protection_reason("在途打样", f"打样单 #{sample_id}（{label}）"))

    return out


async def _protected_customer_ids(session: AsyncSession) -> set[int]:
    """履约保护名单（文档 §11.2/场景21）：这些客户暂不回收。

    口径见 `protection_detail` —— 这里只是取它的键集合，
    **判据只写一处**，避免"定时扫描"和"人工释放"两处各判一套（返工单 6.4 第 6 条）。
    """
    return set((await protection_detail(session)).keys())


async def recycle_reviewer_ids(
    session: AsyncSession, owner_id: int | None
) -> list[int]:
    """能复核「这个客户」回收候选的主管（返修单第六批追加口径 3）。

    口径是**按管理范围挑**，不是"有权限的都通知"：

    1. 先取持 `customer:pool_review`（或管理员）的人 —— 这是"有资格复核"；
    2. 再按数据范围过滤 —— 只有"这条候选的原负责人正好归他管"的人才收得到。

    为什么要把范围加进来：大公司里持有复核权限的有几十个主管，只按权限发，
    每个人都会收到一堆跟自己团队无关的预告，很快就没人看了；
    而"给主管开全系统设置权限"更不是解法（会顺带放开全公司数据）。

    停用账号直接跳过（通知发出去也没人处理）。
    """
    from app.core.data_scope import scoped_owner_ids
    from app.core.deps import CurrentUser
    from app.modules.notification.service import approver_user_ids
    from app.modules.user.model import User
    from app.modules.user.service import (
        get_user_permission_codes,
        get_user_roles,
        resolve_data_scope,
    )

    out: list[int] = []
    for uid in await approver_user_ids(session, "customer:pool_review"):
        user = await session.get(User, uid)
        if user is None or user.status != "active":
            continue
        roles = await get_user_roles(session, uid)
        viewer = CurrentUser(
            user,
            await get_user_permission_codes(session, uid),
            [role.code for role in roles],
            resolve_data_scope(roles),
        )
        allowed = await scoped_owner_ids(session, viewer)
        # `None` = 全公司范围（管理员），不受限
        if allowed is None or owner_id is None or owner_id in allowed:
            out.append(uid)
    return out


async def _notify_recycle_candidate(
    session: AsyncSession,
    *,
    candidate: PublicPoolRecycleCandidate,
    customer: Customer,
    notice_days: int,
) -> int:
    """发出**回收预告**：原负责人收到一条，管理范围内的复核主管各收到一条。

    返修单第 8 条点名的缺口是"扫描只落了候选记录，一条预告都没发，
    原负责人也没有查看入口" —— 于是"预告"这两个字只体现在数据库里。

    接收对象与内容按确认口径来：两方都要收到，内容含**客户、回收原因、
    最近活跃时间、到期时间和查看入口**（放不下的部分放正文里说清去哪看）。

    去重：同一 (接收人, 类型, 业务对象) 在本轮预告期内已经有一条就不再发。
    定时任务重跑、扫描被重复触发、通知补投，都不会让人收到两条一样的预告。
    这也是"通知失败要能查到并补发"的前提 —— 补发走的是同一条记录，
    不会再造一条新的候选、也不会重复回收。

    ⚠️ 去重范围**必须限定在本轮预告期内**（返修 R11）：同一客户第二次进入
    预告时是一条**新候选**（新 id、新 notice_at），原负责人理应再收到一次提醒。
    早期实现只比四元组，而原负责人那条的对象编号用的是客户编号（跨轮次不变），
    于是第二轮被静默挡掉。详见下面去重处与 `candidate.notice_at` 的注释。

    返回新发出的条数。
    """
    from app.modules.notification.model import Notification
    from app.modules.notification.service import channel_settings, notify

    due_text = candidate.due_at.strftime("%Y-%m-%d") if candidate.due_at else "—"
    active = candidate.last_active_at
    active_text = active.strftime("%Y-%m-%d") if active else "无记录"
    reason = (
        f"{candidate.level or ''} 级客户超过 {candidate.rule_days} 天未跟进"
        if candidate.rule_days
        else "长期未跟进"
    )

    owner = await session.get(User, candidate.owner_id) if candidate.owner_id else None

    targets: list[tuple[int, str, int, str, str]] = []
    if candidate.owner_id:
        # 原负责人：点进**客户详情**（他关心的是"我的哪个客户要没了"）
        targets.append(
            (
                candidate.owner_id,
                "customer",
                customer.id,
                "客户回收预告",
                f"你的客户「{customer.name}」{reason}，最近活跃 {active_text}。"
                f"预告期至 {due_text}，到期后主管可批准回收。"
                f"请尽快跟进；如已不再负责，请联系主管说明。",
            )
        )
    for uid in await recycle_reviewer_ids(session, candidate.owner_id):
        if uid == candidate.owner_id:
            continue
        # 复核主管：点进**回收待复核**（他要去处理的那一屏）
        targets.append(
            (
                uid,
                "pool_recycle",
                candidate.id,
                "回收预告待复核",
                f"客户「{customer.name}」{reason}，最近活跃 {active_text}，"
                f"原负责人 {owner.name if owner else candidate.owner_id}。"
                f"预告期至 {due_text}，到期后可在「系统设置 → 业务规则 → "
                f"回收待复核」批准或驳回。",
            )
        )

    settings = await channel_settings(session)
    sent = 0
    for uid, business_type, business_id, title, content in targets:
        # 去重：只挡**本轮预告**里的重复投递（返修 R11 修的就是这个边界）。
        #
        # 为什么不能只按 (接收人, 类型, 对象, 标题) 四元组去重：
        # 原负责人那条的 business_id 用的是 `customer.id` —— 他点进去要落在
        # **客户详情**（"我的哪个客户要没了"），前端是按 business_type 拼跳转的，
        # 这个键不能动。可 `customer.id` **跨轮次不变**，于是同一客户第二次进入
        # 预告时四元组与上一轮完全相同，去重把第二条通知直接挡掉：
        # 客户要被回收了，原负责人却收不到第二次提醒。
        # （主管那条用的是 candidate.id，每轮新候选、id 自然不同，所以不受影响 ——
        #   这也正是"同一件事、两个人、一个收得到一个收不到"的原因。）
        #
        # 加 `created_at >= candidate.notice_at` 把去重范围收到本轮：
        #   · 同一轮扫描重跑 / 通知补投 → notice_at 不变，仍在范围内 → 照样挡住，
        #     不会重复打扰（原有行为保住）
        #   · 下一轮预告 → 新候选的 notice_at 更晚，上一轮那条落在范围外 → 重新发
        exists = (
            await session.execute(
                select(Notification.id).where(
                    Notification.user_id == uid,
                    Notification.business_type == business_type,
                    Notification.business_id == business_id,
                    Notification.title == title,
                    Notification.created_at >= candidate.notice_at,
                )
            )
        ).first()
        if exists is not None:
            continue
        created = await notify(
            session,
            user_id=uid,
            type_="approval",
            title=title,
            content=content,
            business_type=business_type,
            business_id=business_id,
            channel_settings_override=settings,
        )
        sent += int(created is not None)
    return sent


async def run_public_pool_recycle(
    session: AsyncSession, operator_id: int | None, source: str = "WEB"
) -> dict:
    """扫描长期没活跃的客户，**生成回收预告**（不直接改归属）。

    ⚠️ 行为与改前不同（返工单 6.3）：老实现扫到就直接把 `owner_id` 清空、
    客户当场进公海。问题是**不可逆**——业务员出差两周没点跟进，跟了半年的客户
    就没了，谁都能领走。文档 §11.2 要求的是"先预告 → 主管复核 → 再执行"，
    所以扫描只负责**提名**，真正回收在 `decide_candidate(approve)` 里，
    而且那一步会**重新检查**预告之后有没有新情况。

    活跃 = max(最近有效联系, 最近业务进展)；有效报价/在途订单/在途打样/
    未结应收的客户按政策保护（§11.2），保护明细随结果一并返回。
    """
    rules = (
        await session.execute(
            select(PublicPoolRule).where(PublicPoolRule.enabled.is_(True))
        )
    ).scalars().all()
    now = datetime.now(UTC)
    notice_days = int(await get_number(session, "pool_recycle_notice_days", "days", 7))
    due_at = now + timedelta(days=notice_days)
    protection = await protection_detail(session)

    nominated: list[dict] = []
    protected_skipped: list[dict] = []
    #: 撞单争议中、被冻结自动改派的客户（文档 §11.5 :279）
    disputed_skipped: list[dict] = []
    #: **最近联系时间未知**的历史导入客户（第七批 7.5，用户 2026-10-06 确认口径）：
    #: 先标记未知，补核后才进自动回收候选。这类客户不参与本轮扫描，
    #: 但要单独列出来 —— 名单上"看不见"才最危险，主管得知道有多少条等着补核。
    unknown_contact_skipped: list[dict] = []
    already_open: list[int] = []
    #: 本轮实际发出去的预告通知条数（一人一条，原负责人与主管分别算）
    notified = 0

    # 已经有未结候选的客户：本轮跳过（同一客户不重复预告）
    open_ids = set(
        (
            await session.execute(
                select(PublicPoolRecycleCandidate.customer_id).where(
                    PublicPoolRecycleCandidate.status.in_(RECYCLE_OPEN_STATUSES)
                )
            )
        ).scalars().all()
    )

    for rule in rules:
        cutoff = now - timedelta(days=rule.days)
        stmt = select(Customer).where(
            Customer.deleted_at.is_(None),
            Customer.pool_status == "private",
            Customer.owner_id.is_not(None),
            Customer.level == rule.level,
        )
        rows = (await session.execute(stmt)).scalars().all()
        for customer in rows:
            # 联系时间未知 + 没有任何真实业务进展 → 不能按"刚联系过"或"刚建档"算，
            # 也不能直接回收（我们并不知道他是什么时候联系的）。跳过并列入待补核。
            if customer.last_contact_unknown and customer.last_progress_at is None:
                unknown_contact_skipped.append(
                    {
                        "customer_id": customer.id,
                        "name": customer.name,
                        "level": rule.level,
                        "owner_id": customer.owner_id,
                        "note": "最近联系时间未知（历史导入未提供），补核后才参与自动回收",
                    }
                )
                continue
            last = _last_active_at(customer)
            if last is None:
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if last >= cutoff:
                continue
            if customer.id in protection:
                # 场景21：超期但仍在履约（有效报价/在途订单/打样/应收）→
                # 按政策豁免本轮。**保护原因一并记下**，主管要看得到是哪张单拦住的
                protected_skipped.append(
                    {
                        "customer_id": customer.id,
                        "name": customer.name,
                        "level": rule.level,
                        "reasons": protection[customer.id],
                    }
                )
                continue
            # 撞单争议未结案 → 冻结自动改派（文档 §11.5 :279）
            if await duplicates.is_disputed(session, customer.id):
                disputed_skipped.append(
                    {"customer_id": customer.id, "name": customer.name, "level": rule.level}
                )
                continue
            if customer.id in open_ids:
                already_open.append(customer.id)
                continue

            candidate = PublicPoolRecycleCandidate(
                customer_id=customer.id,
                owner_id=customer.owner_id,
                rule_id=rule.id,
                level=rule.level,
                rule_days=rule.days,
                last_contact_at=customer.last_followup_at,
                last_progress_at=customer.last_progress_at,
                last_active_at=last,
                protection_snapshot=protection.get(customer.id, []),
                status="pending",
                notice_at=now,
                due_at=due_at,
                # 把"这条是按几天预告的"留在行上：管理员以后改了预告期，
                # 已在跑的候选仍按当时的天数到期（追加口径 1）
                notice_days=notice_days,
                created_at=now,
            )
            # SAVEPOINT：定时任务重跑 / 两个实例同时扫时，未结唯一索引会让
            # 后到的插入失败。接住它、当作"已经提过名了"，而不是让整轮扫描中断。
            try:
                async with session.begin_nested():
                    session.add(candidate)
                    await session.flush()
            except IntegrityError:
                already_open.append(customer.id)
                continue
            open_ids.add(customer.id)
            # **发预告**（返修单第 8 条）：只落一条候选记录不算预告，
            # 原负责人和复核主管都要真的收到通知，才知道有这个事。
            notified += await _notify_recycle_candidate(
                session, candidate=candidate, customer=customer, notice_days=notice_days
            )
            nominated.append(
                {
                    "candidate_id": candidate.id,
                    "customer_id": customer.id,
                    "name": customer.name,
                    "level": rule.level,
                    "rule_days": rule.days,
                    "last_active_at": last.isoformat(),
                }
            )

    # 审计与 commit 必须在同一个事务里（本函数自己提交，调用方不再补写）
    await write_audit(
        session,
        operator_id=operator_id,
        action="scan_public_pool_recycle",
        source=source,
        business_type="public_pool_rule",
        business_id=None,
        after={
            "nominated_count": len(nominated),
            "nominated": nominated[:100],
            "protected_count": len(protected_skipped),
            "protected": protected_skipped[:100],
            "disputed_count": len(disputed_skipped),
            "disputed": disputed_skipped[:100],
            "unknown_contact_count": len(unknown_contact_skipped),
            "unknown_contact": unknown_contact_skipped[:100],
            "already_open_count": len(already_open),
            "notified_count": notified,
        },
    )
    await session.commit()
    return {
        #: 本轮**提名**（预告）了多少个 —— 注意不是"回收了多少"
        "nominated_count": len(nominated),
        "candidates": nominated,
        "protected_count": len(protected_skipped),
        "protected": protected_skipped[:100],
        "disputed_count": len(disputed_skipped),
        "disputed": disputed_skipped[:100],
        #: 联系时间未知、待补核的历史客户：这批不参与自动回收
        "unknown_contact_count": len(unknown_contact_skipped),
        "unknown_contact": unknown_contact_skipped[:100],
        "already_open_count": len(already_open),
        #: 发出了多少条预告通知（原负责人与复核主管分别计）
        "notified_count": notified,
        "notice_days": notice_days,
        #: 兼容老调用方（定时任务的日志、接口返回）：
        #: 新版这里恒为 0 —— 回收要等主管批准，不会再"一次调用就释放一批"
        "released_count": 0,
        "customers": [],
    }


# ---------------------------------------------------------------- 回收候选：复核与执行（返工单 6.3）


def _deadline_hint(row: PublicPoolRecycleCandidate, earliest: datetime) -> str:
    """这条到底卡在哪个等待期上 —— 提示必须说对（返修单 R13）。

    `earliest_action_at` 改成"取较晚者"之后，一个 `deferred` 的候选**也可能卡在
    预告期上**（暂缓天数比剩余预告期短时）。此时若还照着 `status` 说
    "暂缓期内不做回收"，主管会以为要等暂缓、其实该等预告 —— 提示反而把人带偏。
    所以按**绑定的是哪个时间点**来说。
    """
    due = row.due_at
    if due is not None and due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    if due is not None and earliest == due:
        return f"预告期 {row.notice_days or ''} 天内业务员还可以跟进自救"
    return f"主管已暂缓 {row.defer_days or ''} 天，暂缓期内不做回收"


def earliest_action_at(row: PublicPoolRecycleCandidate) -> datetime | None:
    """这条候选**最早能批准回收**的时间点（返修单第六批第 8 条）。

    规则：**暂缓只能延长，不能缩短**（返修单 R13，2026-10-06 修）。

    取「预告到期 `due_at`」与「暂缓到期 `deferred_until`」中**较晚的那个**。
    为什么要取较晚：暂缓期满不等于预告期满。比如预告还剩 7 天，主管暂缓 1 天 ——
    若只看暂缓到期，1 天后这条就显示成"正常到期"，等于**用一次暂缓把预告期
    悄悄缩掉了**，业务员那 7 天的自救窗口凭空消失。取较晚者，暂缓就只会
    把时间往后推，不会往前拉。

    想真的提前收，必须走**例外通道**（主管填原因 → `early_approved` 留痕，
    审计里单独一个 action），不能被当成"正常到期"。

    返回 None 表示不受等待期限制（例如老数据两个时间都没有），按原逻辑走。

    **驳回不受这个限制** —— 驳回是"不用回收了"，不会造成既成事实，
    想什么时候结案都可以（返修单第六批追加口径确认）。
    """
    stamps: list[datetime] = []
    for value in (row.due_at, row.deferred_until):
        if value is None:
            continue
        # 两个列都是带时区的，但老数据可能存成 naive —— 混着比会直接抛
        # TypeError（naive 与 aware 不可比），所以先统一挂上 UTC
        stamps.append(value if value.tzinfo is not None else value.replace(tzinfo=UTC))
    return max(stamps) if stamps else None


def serialize_candidate(
    row: PublicPoolRecycleCandidate, customer_name: str | None = None,
    owner_name: str | None = None,
) -> dict:
    earliest = earliest_action_at(row)
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "customer_name": customer_name,
        "owner_id": row.owner_id,
        "owner_name": owner_name,
        "level": row.level,
        "rule_days": row.rule_days,
        "last_contact_at": row.last_contact_at.isoformat() if row.last_contact_at else None,
        "last_progress_at": row.last_progress_at.isoformat() if row.last_progress_at else None,
        "last_active_at": row.last_active_at.isoformat() if row.last_active_at else None,
        "protection": row.protection_snapshot or [],
        "status": row.status,
        "status_label": RECYCLE_STATUS_LABEL.get(row.status, row.status),
        "notice_at": row.notice_at.isoformat() if row.notice_at else None,
        "due_at": row.due_at.isoformat() if row.due_at else None,
        #: 提名时用的预告天数快照。改配置会**连带重算**还没结案的候选，
        #: 所以这一列的意思是"这条当前按几天预告"，不是"当初提名时是几天"
        "notice_days": row.notice_days,
        #: **最早可回收时间**：取预告到期与暂缓到期中**较晚**的那个
        #: （暂缓只能延长不能缩短，见 earliest_action_at）。前端据此决定
        #: "批准回收"能不能点、并显示可操作时间（追加口径 1 + 返修单 R13）。
        "earliest_action_at": earliest.isoformat() if earliest else None,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
        "deferred_until": row.deferred_until.isoformat() if row.deferred_until else None,
        "defer_days": row.defer_days,
        "exception_approved": bool(row.exception_approved),
        "early_approved": bool(row.early_approved),
        "executed_at": row.executed_at.isoformat() if row.executed_at else None,
        "restored_at": row.restored_at.isoformat() if row.restored_at else None,
        "restore_note": row.restore_note,
        "restore_conflict_owner_id": row.restore_conflict_owner_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def list_candidates(
    session: AsyncSession,
    *,
    user: CurrentUser,
    status: str | None = "pending",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """回收候选列表（真分页）。主管在这里逐条或批量复核。

    **按客户数据范围过滤**（第六批审查第 7 条）：候选是"某人的客户要被收走"，
    所以拿提名时的原负责人 `owner_id` 去比对用户可见的负责人集合。
    此前完全不过滤 —— 任何拿到权限的人都能看到并处理全公司候选；
    而"给主管开整个系统设置权限"不是可接受的解法（那会顺带放开全公司数据）。
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    conditions = []
    if status:
        conditions.append(PublicPoolRecycleCandidate.status == status)
    allowed = await scoped_owner_ids(session, user)
    if allowed is not None:
        # `all` 范围返回 None，不加过滤；其余只给本范围内的
        conditions.append(PublicPoolRecycleCandidate.owner_id.in_(allowed))

    total = (
        await session.execute(
            select(func.count()).select_from(PublicPoolRecycleCandidate).where(*conditions)
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                select(PublicPoolRecycleCandidate)
                .where(*conditions)
                .order_by(PublicPoolRecycleCandidate.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    )
    cids = {row.customer_id for row in rows}
    names: dict[int, str] = {}
    if cids:
        names = {
            int(cid): name
            for cid, name in (
                await session.execute(
                    select(Customer.id, Customer.name).where(Customer.id.in_(cids))
                )
            ).all()
        }
    owner_ids = {row.owner_id for row in rows if row.owner_id}
    owner_names: dict[int, str] = {}
    if owner_ids:
        from app.modules.user.model import User

        owner_names = {
            int(uid): name
            for uid, name in (
                await session.execute(
                    select(User.id, User.name).where(User.id.in_(owner_ids))
                )
            ).all()
        }
    return (
        [
            serialize_candidate(
                row, names.get(row.customer_id),
                owner_names.get(row.owner_id) if row.owner_id else None,
            )
            for row in rows
        ],
        int(total),
    )


async def _customer_now(session: AsyncSession, customer_id: int) -> Customer | None:
    """取客户并**加行锁**。

    ⚠️ `populate_existing=True` 不能省（本项目 session 是 `expire_on_commit=False`）：
    SQLAlchemy 默认不用查询结果覆盖**已加载对象**的属性，而调用方往往在这个会话里
    已经读过这个客户 —— 那样拿回来的会是内存里的旧对象，锁白加了。
    """
    return (
        await session.execute(
            select(Customer)
            .where(Customer.id == customer_id, Customer.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()


async def assert_candidate_in_scope(
    session: AsyncSession, user: CurrentUser, candidate: PublicPoolRecycleCandidate
) -> None:
    """校验这条回收候选是否落在当前用户的**客户数据范围**内。

    列表过滤只解决"看不见"；直接拿 id 调单条/批量接口仍然能操作到别人的候选。
    所以复核与恢复的入口都要过这一关（第六批审查第 7 条）。

    比对的是候选**提名时记下的原负责人** —— 这次回收要动的就是这个人的客户。
    """
    allowed = await scoped_owner_ids(session, user)
    if allowed is None:
        return  # `all` 范围：都能处理
    if candidate.owner_id is None or candidate.owner_id not in allowed:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "这条回收候选不在你的管理范围内（该客户的原负责人不在你负责的团队里）",
            403,
        )


async def decide_candidate(
    session: AsyncSession,
    *,
    candidate: PublicPoolRecycleCandidate,
    decision: str,
    operator_id: int | None,
    note: str | None = None,
    source: str = "WEB",
    allow_early: bool = False,
) -> dict:
    """主管复核一条候选：`approve` 执行回收 / `reject` 驳回 / `defer` 暂缓。

    **批准执行前必须重新检查**（返工单 6.3 第 4 条）：预告是几天前发的，
    这期间客户可能又有了新跟进、新报价、新订单、新回款 —— 那些都会让
    "该回收"这个结论失效。检查用的还是同一套 `protection_detail`，
    扫描和执行不各判一套。

    如果确实仍有保护、但主管认为还是要收，走 `exception=True` 的例外执行，
    **必须填原因** —— 例外是要有人担责的事。

    ---- 等待期（返修单第六批第 8 条）----

    候选上早就存了 `due_at`（预告到期）和 `deferred_until`（暂缓到期），
    但此前**根本不看**：预告当天批准就能立刻收走，"预告 7 天""暂缓 30 天"
    只是数据库里的两个时间戳。现在两道闸门都真的关上：

    - `pending`：预告期没满 → 拒绝；
    - `deferred`：暂缓期没满 → 拒绝（说好"过一阵再看"，时间没到就再来收，
      等于暂缓两个字没写过）。

    **驳回不受限制** —— 驳回是"不用回收了"，不造成既成事实，随时可结案
    （口径确认：暂缓期内"能结掉、不能收掉"）。

    确需提前收的走 `allow_early=True` 的**提前回收**例外动作，
    **必须填原因**，并单独记 `early_approved` 与审计 —— 与"带着履约保护硬收"
    是两件事，分开留痕，事后统计才分得清。
    """
    now = datetime.now(UTC)
    locked = (
        await session.execute(
            select(PublicPoolRecycleCandidate)
            .where(PublicPoolRecycleCandidate.id == candidate.id)
            .with_for_update()
            # 同 _customer_now：不加这个，路由先读过的旧对象会顶掉库里那一行，
            # "只有一个人能批/能恢复"就守不住了（本项目 expire_on_commit=False）
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if locked is None:
        raise AppError(ErrorCode.NOT_FOUND, "该回收候选不存在", 404)
    row = locked
    if row.status not in ("pending", "deferred"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该候选当前是「{RECYCLE_STATUS_LABEL.get(row.status, row.status)}」，不能再处理",
        )

    if decision == "reject":
        row.status = "rejected"
        row.decided_by = operator_id
        row.decided_at = now
        row.decision_note = note
        await session.flush()
        await write_audit(
            session, operator_id=operator_id, action="reject_pool_candidate", source=source,
            business_type="public_pool_candidate", business_id=row.id,
            after={"customer_id": row.customer_id, "note": note},
        )
        await session.commit()
        return serialize_candidate(row)

    if decision == "defer":
        defer_days = int(await get_number(session, "pool_recycle_defer_days", "days", 30))
        row.status = "deferred"
        row.decided_by = operator_id
        row.decided_at = now
        row.decision_note = note
        # 暂缓的**等待期**同样快照下来：改配置不追溯已经在等的这条
        row.defer_days = defer_days
        row.deferred_until = now + timedelta(days=defer_days)
        await session.flush()
        await write_audit(
            session, operator_id=operator_id, action="defer_pool_candidate", source=source,
            business_type="public_pool_candidate", business_id=row.id,
            after={"customer_id": row.customer_id, "note": note, "deferred_until": row.deferred_until.isoformat()},
        )
        await session.commit()
        return serialize_candidate(row)

    if decision != "approve":
        raise AppError(ErrorCode.PARAM_ERROR, f"未知决定：{decision}", 422)

    customer = await _customer_now(session, row.customer_id)
    if customer is None:
        raise AppError(ErrorCode.NOT_FOUND, "该客户已删除，无法回收", 404)
    if customer.owner_id is None:
        # 客户已经被别人领走 / 早就进了公海：这条提名过期了，直接作废
        row.status = "superseded"
        row.decided_by = operator_id
        row.decided_at = now
        row.decision_note = note or "客户已不在原负责人名下，提名作废"
        await session.flush()
        await session.commit()
        return serialize_candidate(row)
    if row.owner_id is not None and customer.owner_id != row.owner_id:
        # 中途换过人：提名时的依据已经不成立，让主管重新看
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "该客户的负责人已经变过（提名时是别人），这条提名的依据已失效，"
            "请驳回它并重新扫描",
            409,
        )

    # **等待期校验**（返修单第六批第 8 条）：预告期 / 暂缓期没满就不该收。
    # 这是"等待期限真正执行"那一条的直接落点 —— 此前 due_at / deferred_until
    # 只是存着好看。
    earliest = earliest_action_at(row)
    if earliest is not None and earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=UTC)
    early = False
    if earliest is not None and now < earliest:
        if not allow_early:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"还没到可回收时间（最早 {earliest.strftime('%Y-%m-%d %H:%M')}）："
                + _deadline_hint(row, earliest)
                + "。确需提前回收，请由主管填写原因后按「提前回收」例外处理",
                422,
            )
        if not (note or "").strip():
            raise AppError(
                ErrorCode.REQUIRED_FIELD_MISSING,
                "提前回收会让预告/暂缓等待期失效，必须填写原因（会记入审计）",
                422,
            )
        early = True
        row.early_approved = True

    # **执行前重新检查**：预告发出之后有没有新的履约事项 / 新的跟进
    reasons = (await protection_detail(session)).get(customer.id, [])
    latest = _last_active_at(customer)
    if latest is not None and latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    if row.last_active_at is not None:
        recorded = row.last_active_at
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=UTC)
        if latest is not None and latest > recorded:
            reasons = reasons + [
                f"预告之后又有新的业务往来（最近活跃时间从 "
                f"{recorded.strftime('%Y-%m-%d %H:%M')} 变成 {latest.strftime('%Y-%m-%d %H:%M')}）"
            ]

    exception = False
    if reasons and not (note or "").strip():
        # 有保护、又没说为什么要破例 → 拦下（这条正是"批准前新增履约事项会重新拦截"）
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该客户现在有履约保护，不能直接回收："
            + "；".join(reasons)
            + "。确需例外回收请填写原因后重试",
            422,
        )
    if reasons:
        # 填了原因 = 主管明确要求例外执行，记下来
        exception = True
        row.exception_approved = True
        row.protection_snapshot = reasons

    session.add(
        CustomerOwnerHistory(
            customer_id=customer.id,
            old_owner_id=customer.owner_id,
            new_owner_id=None,
            reason=(
                f"{row.level or ''} 级客户超过 {row.rule_days} 天未跟进，"
                f"经主管复核回收"
                + ("（例外：仍有履约保护，理由见候选记录）" if exception else "")
                + ("（提前回收：未满预告/暂缓等待期，理由见候选记录）" if early else "")
            ),
            operator_id=operator_id,
            created_at=now,
        )
    )
    customer.owner_id = None
    customer.pool_status = "public"
    row.status = "executed"
    row.decided_by = operator_id
    row.decided_at = now
    row.decision_note = note
    row.executed_at = now
    await session.flush()
    await write_audit(
        session,
        operator_id=operator_id,
        # 提前回收单独一个 action：事后查审计能一眼分出"等满等待期正常收的"
        # 与"没等满就收的"，这也是例外动作要留痕的意义
        action=("execute_pool_candidate_early" if early else "execute_pool_candidate"),
        source=source,
        business_type="public_pool_candidate",
        business_id=row.id,
        after={
            "customer_id": customer.id,
            "reasons": reasons,
            "exception": exception,
            "early": early,
            "earliest_action_at": earliest.isoformat() if earliest else None,
            "note": note,
        },
    )
    await session.commit()
    return serialize_candidate(row)


async def restore_candidate(
    session: AsyncSession,
    *,
    candidate: PublicPoolRecycleCandidate,
    operator_id: int,
    note: str | None,
    source: str = "WEB",
) -> dict:
    """**恢复**：把被回收的客户还给原负责人。

    三条纪律（返工单 6.3 第 8 条）：
    - 保留原回收记录（这条候选不删，状态改成 `restored`）——
      回收发生过就是发生过，抹掉它等于让人查不出"为什么这个客户换过人"；
    - 客户如果**已经被别人合法领取**，绝不静默覆盖：记下冲突、
      提示交主管处理（`restore_conflict_owner_id`）；
    - 恢复要写归属变更历史，理由里说明是"恢复回收"。
    """
    now = datetime.now(UTC)
    locked = (
        await session.execute(
            select(PublicPoolRecycleCandidate)
            .where(PublicPoolRecycleCandidate.id == candidate.id)
            .with_for_update()
            # 同 _customer_now：不加这个，路由先读过的旧对象会顶掉库里那一行，
            # "只有一个人能批/能恢复"就守不住了（本项目 expire_on_commit=False）
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if locked is None:
        raise AppError(ErrorCode.NOT_FOUND, "该回收候选不存在", 404)
    row = locked
    if row.status != "executed":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"只有「已回收」的客户才能恢复，这条当前是「"
            f"{RECYCLE_STATUS_LABEL.get(row.status, row.status)}」",
        )

    customer = await _customer_now(session, row.customer_id)
    if customer is None:
        raise AppError(ErrorCode.NOT_FOUND, "该客户已删除，无法恢复", 404)

    if customer.owner_id is not None and customer.owner_id != row.owner_id:
        # 已经被别人领走了：**不抢**，把冲突记下来交主管
        row.restore_conflict_owner_id = customer.owner_id
        row.restore_note = note
        await session.flush()
        await write_audit(
            session, operator_id=operator_id, action="restore_pool_candidate_conflict",
            source=source, business_type="public_pool_candidate", business_id=row.id,
            after={"customer_id": customer.id, "current_owner_id": customer.owner_id},
        )
        await session.commit()
        current = await session.get(User, customer.owner_id)
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            f"该客户已经被「{current.name if current else customer.owner_id}」领取，"
            "不能直接恢复给原负责人；请交主管协调（这条冲突已记录）",
            409,
        )

    if row.owner_id is None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "这条回收没有原负责人，无法恢复")

    owner = await session.get(User, row.owner_id)
    if owner is None or owner.status != "active":
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"原负责人「{owner.name if owner else row.owner_id}」已停用，不能恢复给他；"
            "请改派给其他人",
            422,
        )

    session.add(
        CustomerOwnerHistory(
            customer_id=customer.id,
            old_owner_id=None,
            new_owner_id=row.owner_id,
            reason=f"恢复公海回收（候选 #{row.id}）",
            operator_id=operator_id,
            created_at=now,
        )
    )
    customer.owner_id = row.owner_id
    customer.pool_status = "private"
    row.status = "restored"
    row.restored_at = now
    row.restored_by = operator_id
    row.restore_note = note
    row.restore_conflict_owner_id = None
    await session.flush()
    await write_audit(
        session, operator_id=operator_id, action="restore_pool_candidate", source=source,
        business_type="public_pool_candidate", business_id=row.id,
        after={"customer_id": customer.id, "owner_id": row.owner_id, "note": note},
    )
    await session.commit()
    return serialize_candidate(row)


async def assert_no_protection(
    session: AsyncSession,
    *,
    customer_id: int,
    customer_name: str,
    reason: str | None,
    operator_id: int | None,
    allow_exception: bool = False,
) -> list[str]:
    """人工把客户放进公海前，检查有没有履约保护。

    普通操作遇保护**直接拦下并说清是哪张单**（返工单 6.3 第 5 条）；
    主管例外放行**必须填原因**，并把保护事项与例外决定记进审计（第 6 条）。
    返回保护原因列表（空 = 没有保护）。

    判据与定时扫描、回收执行**完全共用** `protection_detail`，
    不在这里另判一套 —— 两处各判一套必然漂移。
    """
    reasons = (await protection_detail(session)).get(customer_id, [])
    if not reasons:
        return []
    if allow_exception and (reason or "").strip():
        await write_audit(
            session,
            operator_id=operator_id,
            action="pool_release_exception",
            business_type="customer",
            business_id=customer_id,
            after={
                "customer_name": customer_name,
                "protection": reasons,
                "reason": reason,
            },
        )
        return reasons
    raise AppError(
        ErrorCode.STATUS_NOT_ALLOWED,
        f"客户「{customer_name}」还在履约中，不能直接放进公海："
        + "；".join(reasons)
        + "。确需释放请由主管填写原因后例外操作",
        422,
    )


async def _has_open_task(session: AsyncSession, rule_id: int, **filters) -> bool:
    stmt = select(Task.id).where(
        Task.source_rule_id == rule_id, Task.status.in_(["pending", "doing"])
    )
    for column, value in filters.items():
        stmt = stmt.where(getattr(Task, column) == value)
    return (await session.execute(stmt)).first() is not None


async def run_auto_tasks(
    session: AsyncSession, operator_id: int | None, source: str = "WEB"
) -> dict:
    """按规则生成自动任务。同一个对象不会重复生成（按规则去重）。"""
    await lock_task_scan(session)
    rules = (
        await session.execute(select(TaskRule).where(TaskRule.status == "active"))
    ).scalars().all()
    created: list[dict] = []
    # 被"已约定下次跟进"豁免的客户（§2.3 第三个时钟）：随结果返回，
    # 让"这个客户为什么没生成提醒"有据可查，而不是看起来漏扫了
    agreed_skipped: list[dict] = []
    errors: list[dict] = []
    followup_customer_ids: set[int] = set()
    now = datetime.now(UTC)

    for rule in rules:
        config = rule.trigger_config or {}
        action = rule.action_config or {}
        if (not isinstance(config, dict) or not isinstance(action, dict)
                or rule.trigger_type not in {"quote_no_followup", "customer_silent", "receivable_due"}):
            errors.append({"rule_id": rule.id, "code": rule.code, "error": "规则类型或配置格式无效"})
            continue
        title_template = action.get("title") or rule.name
        raw_days = config.get("days", 3)
        try:
            days_ahead = int(raw_days)
            if isinstance(raw_days, bool) or days_ahead < 0 or str(days_ahead) != str(raw_days):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            errors.append({"rule_id": rule.id, "code": rule.code, "error": "days 必须是非负整数"})
            continue

        if rule.trigger_type == "quote_no_followup":
            # 报价发送后 N 天没有跟进记录 → 提醒负责人
            cutoff = now - timedelta(days=days_ahead)
            latest_sent = (select(QuoteVersion.quote_id, func.max(QuoteVersion.sent_at).label("sent_at"))
                           .where(QuoteVersion.sent_at.is_not(None))
                           .group_by(QuoteVersion.quote_id).subquery())
            ordered = select(SalesOrder.id).where(SalesOrder.quote_id == Quote.id,
                                                   SalesOrder.status != "cancelled")
            communicated = select(FollowUp.id).where(
                FollowUp.followup_type != "系统", FollowUp.created_at >= latest_sent.c.sent_at,
                or_(FollowUp.quote_id == Quote.id,
                    and_(FollowUp.customer_id == Quote.customer_id,
                         FollowUp.quote_id.is_(None), FollowUp.order_id.is_(None),
                         FollowUp.sample_id.is_(None), FollowUp.lead_id.is_(None),
                         or_(FollowUp.opportunity_id == Quote.opportunity_id,
                             FollowUp.opportunity_id.is_(None)))),
            )
            rows = (
                await session.execute(
                    select(Quote)
                    .join(latest_sent, Quote.id == latest_sent.c.quote_id)
                    .join(Customer, Customer.id == Quote.customer_id)
                    .where(
                        latest_sent.c.sent_at <= cutoff,
                        Quote.deleted_at.is_(None), Customer.deleted_at.is_(None),
                        Quote.status == "sent",
                        ~ordered.exists(), ~communicated.exists(),
                    )
                )
            ).scalars().all()
            for quote in rows:
                if not quote.owner_id:
                    continue
                if await _has_open_task(session, rule.id, quote_id=quote.id):
                    continue
                task = Task(
                    title=f"{title_template}：{quote.quote_no}",
                    task_type="followup",
                    customer_id=quote.customer_id,
                    quote_id=quote.id,
                    owner_id=quote.owner_id,
                    priority="high",
                    status="pending",
                    due_at=now,
                    source="system",
                    source_rule_id=rule.id,
                )
                session.add(task)
                await session.flush()
                followup_customer_ids.add(quote.customer_id)
                created.append({"task_id": task.id, "title": task.title})

        elif rule.trigger_type == "customer_silent":
            # 某等级客户 N 天没联系 → 给负责人建跟进任务
            cutoff = now - timedelta(days=days_ahead)
            levels = config.get("levels") or ["A"]
            if not isinstance(levels, list) or not all(isinstance(level, str) for level in levels):
                errors.append({"rule_id": rule.id, "code": rule.code, "error": "levels 必须是客户等级列表"})
                continue
            rows = (
                await session.execute(
                    select(Customer).where(
                        Customer.deleted_at.is_(None),
                        Customer.pool_status == "private",
                        Customer.owner_id.is_not(None),
                        Customer.level.in_(levels),
                    )
                )
            ).scalars().all()
            # 派生缓存不能让既有未来任务失效；扫描时从真实未完任务批量校准。
            next_due = dict((await session.execute(
                select(Task.customer_id, func.min(Task.due_at)).where(
                    Task.customer_id.in_([row.id for row in rows]), Task.task_type == "followup",
                    Task.status.in_(("pending", "doing")), Task.due_at.is_not(None),
                ).group_by(Task.customer_id)
            )).all()) if rows else {}
            for customer in rows:
                customer.next_followup_at = next_due.get(customer.id)
                # 同一活跃时钟口径：业务进展（报价/打样/下单/回款）也算"有联系"
                last = _last_active_at(customer)
                if last is None:
                    continue
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if last >= cutoff:
                    continue
                # 第三个时钟（文档 §2.3）：已约定下次跟进且还没到 → 本轮豁免。
                # 销售说了"下个月联系"，今天不该再收到"你冷落了客户"。
                # 这条豁免**只用于提醒**，不用于公海回收：约定是销售的承诺，
                # 不该变成长期占位的挡箭牌（回收另有预告/复核/履约保护，见
                # run_public_pool_recycle），两者要求不同，故意不共用。
                agreed = customer.next_followup_at
                if agreed is not None:
                    if agreed.tzinfo is None:
                        agreed = agreed.replace(tzinfo=UTC)
                    if agreed > now:
                        agreed_skipped.append(
                            {"customer_id": customer.id, "name": customer.name,
                             "next_followup_at": str(agreed)}
                        )
                        continue
                if await _has_open_task(session, rule.id, customer_id=customer.id):
                    continue
                task = Task(
                    title=f"{title_template}：{customer.name}",
                    task_type="followup",
                    customer_id=customer.id,
                    owner_id=customer.owner_id,
                    priority="high",
                    status="pending",
                    due_at=now,
                    source="system",
                    source_rule_id=rule.id,
                )
                session.add(task)
                await session.flush()
                followup_customer_ids.add(customer.id)
                created.append({"task_id": task.id, "title": task.title})

        elif rule.trigger_type == "receivable_due":
            # 应收到期前 N 天 → 提醒负责人跟进回款
            # 已取消订单的应收不再派催收（整改审计点：取消订单必须全链路安静）
            # "今天 + 提前 N 天"的"今天"必须是**业务日期（北京时间）**（第十二批 12.7）。
            # 原来取的是 `now.date()`，而 now 是 UTC —— 北京时间凌晨 0-8 点会比业务日
            # 早一天，而默认的自动任务调度正好是凌晨跑：当天到期的提醒要等到第二天
            # 才发得出来。存储不变（due_date 本来就是 date 列），只换"今天"的取法。
            due_before = today_business() + timedelta(days=days_ahead)
            rows = (
                await session.execute(
                    select(ReceivablePlan, SalesOrder)
                    .join(SalesOrder, SalesOrder.id == ReceivablePlan.order_id)
                    .where(
                        ReceivablePlan.status.in_(["pending", "partial", "overdue"]),
                        ReceivablePlan.due_date <= due_before,
                        SalesOrder.status != "cancelled",
                    )
                )
            ).all()
            for plan, order in rows:
                if not order.owner_id:
                    continue
                if await _has_open_task(session, rule.id, order_id=order.id):
                    continue
                task = Task(
                    title=f"{title_template}：{order.order_no} {plan.plan_name}",
                    task_type="payment",
                    customer_id=order.customer_id,
                    order_id=order.id,
                    owner_id=order.owner_id,
                    priority="high",
                    status="pending",
                    due_at=datetime.combine(plan.due_date, datetime.min.time(), tzinfo=UTC),
                    source="system",
                    source_rule_id=rule.id,
                )
                session.add(task)
                await session.flush()
                created.append({"task_id": task.id, "title": task.title})

    from app.modules.customer.service import refresh_next_followup_at
    for customer_id in sorted(followup_customer_ids):
        await refresh_next_followup_at(session, customer_id)
    # 自动任务由规则批量生成，同样要留痕（谁触发、生成了几条、为何跳过）
    await write_audit(
        session,
        operator_id=operator_id,
        action="run_auto_tasks",
        source=source,
        business_type="task_rule",
        business_id=None,
        after={"created_count": len(created), "tasks": created,
               "agreed_skipped_count": len(agreed_skipped), "agreed_skipped": agreed_skipped[:100],
               "failed_rule_count": len(errors), "rule_errors": errors},
    )
    await session.commit()
    return {
        "created_count": len(created),
        "tasks": created,
        "agreed_skipped_count": len(agreed_skipped),
        "agreed_skipped": agreed_skipped[:100],
        "failed_rule_count": len(errors),
        "rule_errors": errors,
    }


async def notify_due_followups(session: AsyncSession) -> int:
    """「约定下次跟进时间」已到、任务还开着 → 推负责人一条（文档 §2.3 第三个时钟）。

    这个函数是第三个时钟存在的理由：只写不扫，next_followup_at 就只是个展示值——
    销售不会因为"我答应客户今天联系"被提醒，而是两周后直接被判冷落。
    到期未兑现与"从没联系过"是两种不同的失职，提醒文案也分开。

    按任务去重（同一任务只推一次），否则每日扫描会变成每日刷屏。
    用 type="followup" 而不是 "task"：后者和"新任务指派给你"共用，
    去重键会互相顶掉，导致这条提醒永远发不出去。
    """
    from app.modules.notification import service as notification_service
    from app.modules.notification.model import Notification

    await lock_task_scan(session)
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(Customer, Task)
            .join(Task, Task.customer_id == Customer.id)
            .where(
                Customer.deleted_at.is_(None),
                Customer.pool_status == "private",
                Task.task_type == "followup",
                Task.status.in_(("pending", "doing")),
                Task.due_at.is_not(None),
                Task.due_at <= now,
            )
        )
    ).all()
    sent = 0
    for customer, task in rows:
        owner_id = task.owner_id or customer.owner_id
        if not owner_id:
            continue
        already = (
            await session.execute(
                select(Notification.id).where(
                    Notification.user_id == owner_id,
                    Notification.type == "followup",
                    Notification.business_type == "task",
                    Notification.business_id == task.id,
                )
            )
        ).first()
        if already is not None:
            continue
        notification = await notification_service.notify(
            session,
            user_id=owner_id,
            type_="followup",
            title=f"已到约定的联系时间：{customer.name}",
            content=f"任务「{task.title}」已到期，按约定联系客户后记得记录跟进",
            business_type="task",
            business_id=task.id,
        )
        if notification is not None:
            sent += 1
    return sent


async def confirmed_not_paid_count(session: AsyncSession) -> int:
    """给设置页用的小指标：已确认回款但节点还没结清的条数。"""
    rows = (
        await session.execute(
            select(PaymentRecord.receivable_plan_id).where(PaymentRecord.status == "confirmed").distinct()
        )
    ).scalars().all()
    if not rows:
        return 0
    plans = (
        await session.execute(
            select(ReceivablePlan.id).where(
                ReceivablePlan.id.in_([int(x) for x in rows if x]), ReceivablePlan.status != "paid"
            )
        )
    ).scalars().all()
    return len(plans)
