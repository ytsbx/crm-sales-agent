"""期间实绩快照：结账后金额不再随订单状态变（第三批 §4.1.5 后半）。

这是 `target_bases.py` 的另一半。那一半冻的是"客户集合与首次成交日"，
这一半冻的是**完成额**——不冻的话，客户今年退掉去年的一张单，
去年那一期的数字就跟着变小，年底发奖金时拿的那份报表过几个月再看就变了。

两个刻意的设计：

1. **冻结是显式动作，不是"第一次读就偷偷冻"**（基准快照是惰性冻的，
   这里不一样）。"什么时候算结完账"只有人知道：财务关账、月度经营会开完、
   或者干脆按季度结——程序猜不出来。猜错了就是把账冻在半路上。

2. **重算必须带原因**。改历史数字是件要有人担责的事，
   不带原因的重算等于让"数字对不上"永远查不出是谁改的。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.analytics.model import ActualSnapshot, ActualSnapshotItem

#: 要冻结的指标，与 `targets.py` 组装行时用到的实际值一一对应。
#:
#: `repeat_net`（老客净额）也在这里：它同样是"这一期的完成额"，
#: 目标页有它的目标/实际/差额/达成率四处展示。不冻它，结账之后客户取消
#: 一张往年订单，复购那一列照样会变——而"结账"承诺的是**整行**不再变
#: （返工单第 4 条点名了这个缺口：原来只有 4 项，没有老客净额）。
ACTUAL_METRICS = ("sales", "received", "shipped", "new_customer", "repeat_net")

ACTUAL_METRIC_LABEL = {
    "sales": "签单额",
    "received": "确认回款（考核口径）",
    "shipped": "发货额",
    "new_customer": "新客户数",
    "repeat_net": "老客净额（复购）",
}


def scope_key_of(user_id: int | None, department_id: int | None) -> str:
    """作用域 → 字符串键。三者互斥，顺序即优先级（有 user 就不看 dept）。"""
    if user_id:
        return f"user:{user_id}"
    if department_id:
        return f"dept:{department_id}"
    return "company"


def parse_scope_key(key: str) -> tuple[int | None, int | None]:
    """字符串键 → (user_id, department_id)。给需要回填 id 的地方用。"""
    if key.startswith("user:"):
        return int(key[5:]), None
    if key.startswith("dept:"):
        return None, int(key[5:])
    return None, None


async def frozen_for(session: AsyncSession, period: str) -> dict[tuple[str, str], Decimal]:
    """取某期已冻结的实绩。没冻过就是空字典（调用方回落实时计算）。"""
    rows = (
        await session.execute(
            select(ActualSnapshot).where(ActualSnapshot.period == period)
        )
    ).scalars().all()
    return {(row.scope_key, row.metric): row.actual_value for row in rows}


async def frozen_periods(session: AsyncSession, year: int) -> set[str]:
    """这一年已经结过账的期间（给界面标"这一期是存档值"）。"""
    rows = (
        await session.execute(
            select(ActualSnapshot.period)
            .where(ActualSnapshot.period.like(f"{year}-%"))
            .distinct()
        )
    ).scalars().all()
    return set(rows)


async def frozen_map(
    session: AsyncSession, year: int
) -> dict[str, dict[tuple[str, str], Decimal]]:
    """这一年已冻结的实绩，按期间分好组：{期间: {(作用域, 指标): 值}}。

    报表渲染时按"这一期冻了没有"逐期决定用存档还是实时算——
    同一年里可能有几个月结了账、几个月还没结，不能一刀切。
    """
    rows = (
        await session.execute(
            select(ActualSnapshot).where(ActualSnapshot.period.like(f"{year}-%"))
        )
    ).scalars().all()
    out: dict[str, dict[tuple[str, str], Decimal]] = {}
    for row in rows:
        out.setdefault(row.period, {})[(row.scope_key, row.metric)] = row.actual_value
    return out


async def is_frozen(session: AsyncSession, period: str) -> bool:
    """这一期结过账没有。下钻据此决定"读存档"还是"实时算"。

    判据取**期间**而不是"这个指标有没有条目"：某一期结了账但那个月确实没有回款时，
    条目为空是**正确答案**，不能因此回落到实时算——那会把结账之后新发生的回款补进来。
    """
    row = (
        await session.execute(
            select(ActualSnapshot.period).where(ActualSnapshot.period == period).limit(1)
        )
    ).scalar()
    return row is not None


async def frozen_items(session: AsyncSession, period: str, metric: str) -> list[dict]:
    """取某一期某个指标的**存档明细**（结账那一刻抄下来的那批单据）。

    返回字段与实时算出来的 `targets._row(...)` **完全同形**，
    调用方不用为"存档"和"实时"写两套渲染。
    """
    rows = (
        await session.execute(
            select(ActualSnapshotItem)
            .where(
                ActualSnapshotItem.period == period,
                ActualSnapshotItem.metric == metric,
            )
            .order_by(ActualSnapshotItem.id)
        )
    ).scalars().all()
    return [
        {
            "record_type": row.record_type,
            "id": row.record_id,
            "label": row.label,
            "owner_id": row.owner_id,
            "amount": round(float(row.amount or 0), 2),
            "date": row.at_text,
        }
        for row in rows
    ]


async def freeze_period(
    session: AsyncSession,
    *,
    period: str,
    values: dict[tuple[str, str], Decimal],
    basis_version: str,
    operator_id: int | None,
    replace: bool = False,
    note: str | None = None,
    items: list[dict] | None = None,
) -> dict:
    """把算好的实绩落成快照，返回 `{"written": 写入行数, "removed": 清除的陈旧行数, "items": 明细条数}`。

    `replace=False`（正常结账）碰上已结过账的期间会**直接报错**，
    而不是悄悄覆盖：覆盖历史数字是要留说明的事，该走重算那条路。

    **重算（`replace=True`）不是"覆盖"，而是"按现在的数重出一份"**：
    上次有、这次没有的键必须一起删掉（返工单第 4 条）。留着会变成
    "某个作用域的数字永远停在上一版"，而报表读快照时又把它当真值显示出来。

    **明细跟着汇总一起冻**（返工单第 4 条）：`items` 是构成这批汇总值的单据清单，
    与汇总同一次写入、一起替换。只冻汇总的话，结账后一张退货单会让
    "点开明细"比"合计"少一笔，用户没法判断该信哪个。
    """
    existing = await frozen_for(session, period)
    if existing and not replace:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"{period} 已经结过账了。要改历史数字请走重算，并填写原因",
            422,
        )
    if replace and not (note or "").strip():
        # 重算必须说明为什么。历史数字被改而没人知道原因，
        # 事后对账就是一笔糊涂账。
        raise AppError(ErrorCode.PARAM_ERROR, "重算历史实绩必须填写原因", 422)

    now = datetime.now(UTC)
    written = 0
    for (scope_key, metric), value in values.items():
        row = await session.get(ActualSnapshot, (period, scope_key, metric))
        if row is None:
            row = ActualSnapshot(
                period=period,
                scope_key=scope_key,
                metric=metric,
                actual_value=value,
                metric_basis_version=basis_version,
                frozen_at=now,
                frozen_by=operator_id,
                note=note,
            )
            session.add(row)
        else:
            row.actual_value = value
            row.metric_basis_version = basis_version
            row.frozen_at = now
            row.frozen_by = operator_id
            row.note = note
        written += 1

    # 清掉"上次有、这次没有"的汇总键（只在重算时做：正常结账时这一期本来是空的）
    removed = 0
    if replace:
        for scope_key, metric in existing:
            if (scope_key, metric) in values:
                continue
            stale = await session.get(ActualSnapshot, (period, scope_key, metric))
            if stale is not None:
                await session.delete(stale)
                removed += 1

    # 明细条目：先清空这一期的旧条目，再全量写入。重算时旧条目必须跟着换掉，
    # 否则明细还是上一版，跟刚重算出来的汇总又对不上。
    old_items = (
        await session.execute(
            select(ActualSnapshotItem).where(ActualSnapshotItem.period == period)
        )
    ).scalars().all()
    for row in old_items:
        await session.delete(row)
    stored_items = 0
    for item in items or []:
        session.add(
            ActualSnapshotItem(
                period=period,
                metric=item["metric"],
                record_type=item["record_type"],
                record_id=int(item["id"]),
                owner_id=item.get("owner_id"),
                label=item.get("label"),
                amount=Decimal(str(item.get("amount") or 0)),
                at_text=item.get("date"),
                metric_basis_version=basis_version,
                frozen_at=now,
                frozen_by=operator_id,
            )
        )
        stored_items += 1

    await session.flush()
    return {"written": written, "removed": removed, "items": stored_items}
