"""规则执行：公海自动回收与自动任务生成。

这两件事原本写死在代码里，现在改成读规则表执行——业务想调天数不用找我改代码。
"""

"""规则执行：公海自动回收与自动任务生成。

另外这里集中放**所有可配置的业务参数**：代码里不再出现"魔法数字"，
每个数字都有一个配置键，管理员在系统设置界面上就能改。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.modules.customer import duplicates
from app.modules.customer.model import Customer, CustomerOwnerHistory
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.sample.model import SampleRequest
from app.modules.settings.model import PublicPoolRule, TaskRule
from app.modules.task.model import Task
from app.modules.task.scanning import lock_task_scan
from app.modules.followup.model import FollowUp

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


async def _protected_customer_ids(session: AsyncSession) -> set[int]:
    """履约保护名单（文档 §11.2/场景21）：这些客户暂不回收。

    - 有效报价：未删除且仍在有效期内
    - 在途订单：未完成、未取消
    - 在途打样：未被拒绝（签收≠接受，也不算结束）
    - 未结应收：未收完且订单未取消
    """
    today = datetime.now(UTC).date()
    protected: set[int] = set()
    rows = await session.execute(
        select(Quote.customer_id).where(
            Quote.deleted_at.is_(None),
            Quote.valid_until.is_not(None),
            Quote.valid_until >= today,
        )
    )
    protected |= {int(cid) for cid in rows.scalars().all() if cid is not None}
    rows = await session.execute(
        select(SalesOrder.customer_id).where(
            SalesOrder.status.in_(("pending", "in_production", "shipped", "delivered"))
        )
    )
    protected |= {int(cid) for cid in rows.scalars().all() if cid is not None}
    rows = await session.execute(
        select(SampleRequest.customer_id).where(SampleRequest.status != "rejected")
    )
    protected |= {int(cid) for cid in rows.scalars().all() if cid is not None}
    rows = await session.execute(
        select(SalesOrder.customer_id)
        .join(ReceivablePlan, ReceivablePlan.order_id == SalesOrder.id)
        .where(
            ReceivablePlan.status.in_(("pending", "partial", "overdue")),
            SalesOrder.status != "cancelled",
        )
    )
    protected |= {int(cid) for cid in rows.scalars().all() if cid is not None}
    return protected


async def run_public_pool_recycle(
    session: AsyncSession, operator_id: int | None, source: str = "WEB"
) -> dict:
    """按规则把长期没活跃的客户释放回公海。

    活跃 = max(最近有效联系, 最近业务进展)；有效报价/在途订单/在途打样/
    未结应收的客户先按政策保护（§11.2），保护数量随执行结果一并返回。
    """
    rules = (
        await session.execute(
            select(PublicPoolRule).where(PublicPoolRule.enabled.is_(True))
        )
    ).scalars().all()
    released: list[dict] = []
    protected_skipped: list[dict] = []
    #: 撞单争议中、被冻结自动改派的客户（文档 §11.5 :279）
    disputed_skipped: list[dict] = []
    now = datetime.now(UTC)
    protected_ids = await _protected_customer_ids(session)

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
            last = _last_active_at(customer)
            if last is None:
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if last >= cutoff:
                continue
            if customer.id in protected_ids:
                # 场景21：超期但在履约中（有效报价/在途订单/打样/应收），
                # 按政策豁免本轮回收，记录下来让执行结果可解释
                protected_skipped.append(
                    {"customer_id": customer.id, "name": customer.name, "level": rule.level}
                )
                continue
            # 撞单争议未结案 → 冻结自动改派（文档 §11.5 :279）：
            # 回收会把归属清空成既成事实，等主管裁定完再按规则处理
            if await duplicates.is_disputed(session, customer.id):
                disputed_skipped.append(
                    {"customer_id": customer.id, "name": customer.name, "level": rule.level}
                )
                continue
            session.add(
                CustomerOwnerHistory(
                    customer_id=customer.id,
                    old_owner_id=customer.owner_id,
                    new_owner_id=None,
                    reason=f"{rule.level} 级客户超过 {rule.days} 天未跟进，自动回收",
                    operator_id=operator_id,
                    created_at=now,
                )
            )
            released.append(
                {"customer_id": customer.id, "name": customer.name, "level": rule.level}
            )
            customer.owner_id = None
            customer.pool_status = "public"

    # 审计与 commit 必须在同一个事务里（本函数自己提交，调用方不再补写）
    await write_audit(
        session,
        operator_id=operator_id,
        action="run_public_pool_recycle",
        source=source,
        business_type="public_pool_rule",
        business_id=None,
        after={
            "released_count": len(released),
            "customers": released,
            "protected_count": len(protected_skipped),
            "protected": protected_skipped[:100],
            "disputed_count": len(disputed_skipped),
            "disputed": disputed_skipped[:100],
        },
    )
    await session.commit()
    return {
        "released_count": len(released),
        "customers": released,
        "protected_count": len(protected_skipped),
        "protected": protected_skipped[:100],
        "disputed_count": len(disputed_skipped),
        "disputed": disputed_skipped[:100],
    }


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
            due_before = (now + timedelta(days=days_ahead)).date()
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
