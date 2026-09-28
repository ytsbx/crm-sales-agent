"""规则执行：公海自动回收与自动任务生成。

这两件事原本写死在代码里，现在改成读规则表执行——业务想调天数不用找我改代码。
"""

"""规则执行：公海自动回收与自动任务生成。

另外这里集中放**所有可配置的业务参数**：代码里不再出现"魔法数字"，
每个数字都有一个配置键，管理员在系统设置界面上就能改。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.modules.customer.model import Customer, CustomerOwnerHistory
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.settings.model import PublicPoolRule, TaskRule
from app.modules.task.model import Task

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


async def run_public_pool_recycle(
    session: AsyncSession, operator_id: int | None, source: str = "WEB"
) -> dict:
    """按规则把长期没跟进的客户释放回公海。"""
    rules = (
        await session.execute(
            select(PublicPoolRule).where(PublicPoolRule.enabled.is_(True))
        )
    ).scalars().all()
    released: list[dict] = []
    now = datetime.now(UTC)

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
            last = customer.last_followup_at or customer.created_at
            if last is None:
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if last >= cutoff:
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
        after={"released_count": len(released), "customers": released},
    )
    await session.commit()
    return {"released_count": len(released), "customers": released}


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
    rules = (
        await session.execute(select(TaskRule).where(TaskRule.status == "active"))
    ).scalars().all()
    created: list[dict] = []
    now = datetime.now(UTC)

    for rule in rules:
        config = rule.trigger_config or {}
        action = rule.action_config or {}
        title_template = action.get("title") or rule.name
        days_ahead = int(config.get("days", 3))

        if rule.trigger_type == "quote_no_followup":
            # 报价发送后 N 天没有跟进记录 → 提醒负责人
            cutoff = now - timedelta(days=days_ahead)
            rows = (
                await session.execute(
                    select(QuoteVersion, Quote)
                    .join(Quote, Quote.id == QuoteVersion.quote_id)
                    .where(
                        QuoteVersion.sent_at.is_not(None),
                        QuoteVersion.sent_at <= cutoff,
                        Quote.status == "sent",
                    )
                )
            ).all()
            for version, quote in rows:
                if not quote.owner_id:
                    continue
                if await _has_open_task(session, rule.id, customer_id=quote.customer_id):
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
                created.append({"task_id": task.id, "title": task.title})

        elif rule.trigger_type == "customer_silent":
            # 某等级客户 N 天没联系 → 给负责人建跟进任务
            cutoff = now - timedelta(days=days_ahead)
            levels = config.get("levels") or ["A"]
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
            for customer in rows:
                last = customer.last_followup_at or customer.created_at
                if last is None:
                    continue
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if last >= cutoff:
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
                        ReceivablePlan.status.in_(["pending", "partial"]),
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

    # 自动任务由规则批量生成，同样要留痕（谁触发、生成了几条）
    await write_audit(
        session,
        operator_id=operator_id,
        action="run_auto_tasks",
        source=source,
        business_type="task_rule",
        business_id=None,
        after={"created_count": len(created), "tasks": created},
    )
    await session.commit()
    return {"created_count": len(created), "tasks": created}


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
