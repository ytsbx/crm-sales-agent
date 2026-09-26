"""编号规则与取号（PRD §2.6 / 03-API §36）。

## 为什么重做取号

原来的实现是 `count(*) + 1`：

    count = select(func.count(Quote.id)).where(Quote.quote_no.like(f"{prefix}%"))
    return f"{prefix}{int(count) + 1:04d}"

它有两个真问题：

1. **会重号**：计数依赖"已经存在多少条"，删掉一张 0002 之后总数变成 1，
   下一张又会生成 0002 —— 如果 0003 还在，新的 0002 就和历史记录撞了；
2. **并发会撞**：两个请求同时 count 拿到同一个数，生成同一个单号。
   `sales_orders.order_no` 有唯一约束，这种撞法会直接抛 500。

现在的做法：`number_sequences` 表按「规则 + 周期」存一个计数器，
取号时对该行 `SELECT ... FOR UPDATE` 再自增。并发的请求会串行拿到
不同的号；计数器单调递增，删单不会让号被回收重用。

## 调用约定

`next_number()` 不 commit，跟着调用方的事务走：单号与业务单据在同一个
事务里落库，业务失败时计数器回滚，不会留下空洞。
"""

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.settings.model import NumberSequence, NumberingRule

#: 内置默认规则。库里没有该 code 的记录时按这里兜底，
#: 保证"加了新单据类型但还没配置规则"也能出号，不会直接 500。
DEFAULT_RULES: dict[str, dict] = {
    "quote": {
        "name": "报价单号",
        "prefix": "Q",
        "date_format": "%Y%m%d",
        "seq_length": 4,
        "reset_period": "daily",
    },
    "order": {
        "name": "销售订单号",
        "prefix": "SO",
        "date_format": "%Y%m%d",
        "seq_length": 4,
        "reset_period": "daily",
    },
    "sample": {
        "name": "样品申请单号",
        "prefix": "SP",
        "date_format": "%Y%m%d",
        "seq_length": 4,
        "reset_period": "daily",
    },
}

RESET_PERIODS = {
    "none": "不重置",
    "daily": "按日",
    "monthly": "按月",
    "yearly": "按年",
}


def period_key(reset_period: str, now: datetime) -> str:
    """当前时间落在哪个周期里。周期一变，流水自然归零。"""
    if reset_period == "daily":
        return now.strftime("%Y%m%d")
    if reset_period == "monthly":
        return now.strftime("%Y%m")
    if reset_period == "yearly":
        return now.strftime("%Y")
    return ""


def format_number(rule: NumberingRule, sequence: int, now: datetime) -> str:
    date_part = now.strftime(rule.date_format) if rule.date_format else ""
    return f"{rule.prefix}{date_part}{sequence:0{rule.seq_length}d}"


async def get_rule(session: AsyncSession, code: str) -> NumberingRule:
    """取规则：库里优先，没有则用内置默认值构造一个（不落库）。"""
    row = (
        await session.execute(select(NumberingRule).where(NumberingRule.code == code))
    ).scalars().first()
    if row is not None:
        return row
    fallback = DEFAULT_RULES.get(code)
    if fallback is None:
        raise AppError(ErrorCode.NOT_FOUND, f"没有编号规则：{code}", 404)
    # 构造一个未持久化的对象，仅用于本轮格式化
    return NumberingRule(code=code, **fallback)


async def next_number(session: AsyncSession, code: str, *, now: datetime | None = None) -> str:
    """按规则生成下一个单号。不 commit，随调用方事务提交或回滚。"""
    rule = await get_rule(session, code)
    if not rule.enabled:
        raise AppError(ErrorCode.PARAM_ERROR, f"编号规则 {code} 已停用", 422)

    moment = now or datetime.now(UTC)
    key = period_key(rule.reset_period, moment)

    # 取该周期的计数器并加行锁。并发的第二个请求会在这里等第一个提交，
    # 因此拿到的一定是不同的号。
    row = (
        await session.execute(
            select(NumberSequence)
            .where(NumberSequence.rule_code == code, NumberSequence.period_key == key)
            .with_for_update()
        )
    ).scalars().first()

    if row is None:
        # 首次取号：先插一条 0 再锁它。
        # 并发时两个请求可能同时插入 —— 唯一索引会让其中一个失败，
        # 这里用 ON CONFLICT DO NOTHING 让失败的那个改为读取已有行，
        # 避免把并发问题变成 500。
        await session.execute(
            text(
                "insert into number_sequences (rule_code, period_key, current_no) "
                "values (:code, :key, 0) on conflict (rule_code, period_key) do nothing"
            ),
            {"code": code, "key": key},
        )
        await session.flush()
        row = (
            await session.execute(
                select(NumberSequence)
                .where(NumberSequence.rule_code == code, NumberSequence.period_key == key)
                .with_for_update()
            )
        ).scalars().first()
        if row is None:  # 理论上到不了这里
            raise AppError(ErrorCode.SYSTEM_ERROR, "取号失败：计数器不可用", 500)

    row.current_no = int(row.current_no) + 1
    row.updated_at = moment
    await session.flush()
    return format_number(rule, int(row.current_no), moment)


def serialize_rule(rule: NumberingRule, period: str, current: int | None) -> dict:
    date_part = datetime.now(UTC).strftime(rule.date_format) if rule.date_format else ""
    sample_seq = (current or 0) + 1
    return {
        "id": rule.id,
        "code": rule.code,
        "name": rule.name,
        "prefix": rule.prefix,
        "date_format": rule.date_format,
        "seq_length": rule.seq_length,
        "reset_period": rule.reset_period,
        "reset_period_label": RESET_PERIODS.get(rule.reset_period, rule.reset_period),
        "enabled": rule.enabled,
        "remark": rule.remark,
        # 给界面看的"下一个号长什么样"，省得管理员改完还得去建单试
        "next_preview": (
            f"{rule.prefix}{date_part}{sample_seq:0{rule.seq_length}d}"
            if rule.enabled
            else None
        ),
        "current_no": current,
        "period_key": period,
    }


__all__ = [
    "DEFAULT_RULES",
    "RESET_PERIODS",
    "format_number",
    "get_rule",
    "next_number",
    "period_key",
    "serialize_rule",
]
