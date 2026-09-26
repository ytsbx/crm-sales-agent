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

## 自愈：为什么还要探测"号已被占用"

计数器自增和业务 INSERT 在**同一个事务**里（这是有意的：业务失败时
计数器跟着回滚，不留空洞）。但这带来一个死循环：

    计数器=3 → 取到 0004 → INSERT 撞唯一约束（0004 已被占用）
    → 事务回滚 → 计数器回到 3 → 下次又取到 0004 → 永远撞死

一旦计数器与已发布的号脱节（换过库、手工修过数据、清过
`number_sequences`），整个单据类型就再也建不出来了。

所以取号时额外做两件事：

1. **播种**：计数器为 0 时，先从库里已有的最大号起步，不从头开始；
2. **跳过**：候选号被占用就换下一个，最多试 `MAX_PROBE` 次。

`taken` 回调由调用方提供（`quote_no_taken` 之类），这样取号模块
不需要认识具体的业务模型。
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.settings.model import NumberSequence, NumberingRule

#: 取号时最多连续探测多少个候选号。够覆盖"计数器落后于实际数据"的常见情形，
#: 又不会在数据彻底错乱时无限循环。
MAX_PROBE = 50

#: 号是否已被占用的探测函数：(rule, 序号字符串) -> 已占用？
TakenProbe = Callable[[NumberingRule, str], Awaitable[bool]]

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


async def next_number(
    session: AsyncSession,
    code: str,
    *,
    now: datetime | None = None,
    taken: TakenProbe | None = None,
    seed_from: int | None = None,
) -> str:
    """按规则生成下一个单号。不 commit，随调用方事务提交或回滚。

    `seed_from`：计数器为 0（首次取号）时从这里起步，通常传"库里已有最大号"。
    不传就按 0 起，等于从 0001 开始 —— 如果库里已经有 0001，会靠 `taken`
    探测跳过（所以强烈建议两个都传）。

    `taken`：探测候选号是否已被占用。传了才能自愈（见模块 docstring）。
    """
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
        # 首次取号：先插一条再锁它。
        # 并发时两个请求可能同时插入 —— 唯一索引会让其中一个失败，
        # 这里用 ON CONFLICT DO NOTHING 让失败的那个改为读取已有行，
        # 避免把并发问题变成 500。
        #
        # 初始值用 seed_from（库里已有最大号）：换库/清过计数器之后，
        # 从 0 开始会一路撞已发布的号。
        await session.execute(
            text(
                "insert into number_sequences (rule_code, period_key, current_no) "
                "values (:code, :key, :seed) on conflict (rule_code, period_key) do nothing"
            ),
            {"code": code, "key": key, "seed": int(seed_from or 0)},
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
        # 已存在的计数器可能落后于实际数据（历史遗留），也要抬到 seed 之上
        if seed_from and int(row.current_no) < int(seed_from):
            row.current_no = int(seed_from)

    # 候选号被占用就往后跳。留一两格余量，避免每轮都从同一个号开始试。
    for _ in range(MAX_PROBE):
        row.current_no = int(row.current_no) + 1
        row.updated_at = moment
        await session.flush()
        candidate = format_number(rule, int(row.current_no), moment)
        if taken is None or not await taken(rule, candidate):
            return candidate
    raise AppError(
        ErrorCode.SYSTEM_ERROR,
        f"连续 {MAX_PROBE} 个候选号都已被占用，编号规则 {code} 的计数器可能已损坏，请检查",
        500,
    )


async def period_max_number(
    session: AsyncSession,
    *,
    model: Any,
    column: Any,
    rule: NumberingRule,
    now: datetime | None = None,
) -> int:
    """库里本周期已使用的最大流水号（用于给计数器播种）。

    按 `前缀 + 日期` 前缀取字典序最大的一条单号，再解析出尾部流水。
    字典序对"定长左补零"的流水号等价于数值序。
    """
    moment = now or datetime.now(UTC)
    head = rule.prefix + (moment.strftime(rule.date_format) if rule.date_format else "")
    if not head:
        return 0
    latest = (
        await session.execute(
            select(column).where(column.like(f"{head}%")).order_by(column.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if not latest:
        return 0
    try:
        return int(str(latest)[len(head):])
    except ValueError:
        return 0


async def generate_for(
    session: AsyncSession,
    code: str,
    *,
    model: Any,
    column: Any,
    now: datetime | None = None,
) -> str:
    """按单据类型取号，自愈参数自动装好。

    `model` / `column` 是承载单号的表与列（`Quote` / `Quote.quote_no`）。
    凡是"一个模型 + 一个唯一单号列"的单据都用这个，别再各写一遍探测。
    """
    rule = await get_rule(session, code)

    async def taken(_rule: NumberingRule, candidate: str) -> bool:
        row = (
            await session.execute(select(model.id).where(column == candidate).limit(1))
        ).first()
        return row is not None

    seed = await period_max_number(session, model=model, column=column, rule=rule, now=now)
    return await next_number(session, code, now=now, taken=taken, seed_from=seed)


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
