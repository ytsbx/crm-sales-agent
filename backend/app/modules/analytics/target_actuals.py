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

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text
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


@asynccontextmanager
async def consistent_snapshot(period: str):
    """**一条独立连接**，读同一个快照，并持有这一期的期间锁（第五批审查 P1）。

    ## 为什么必须是独立连接（我第一版写错了，记下来）

    第一版是在**请求会话**上执行 `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ`，
    结果结账接口直接 **500**：

        ActiveSQLTransactionError: SET TRANSACTION ISOLATION LEVEL must be called
        before any query

    因为请求会话在进入处理函数之前**就已经查过库了**（依赖注入要加载当前用户）。
    隔离级别只能在事务的第一条语句之前设置 —— 这条业务路径做不到。

    独立连接则天然满足：它的第一条语句就是这个 `SET TRANSACTION`。

    ## 它解决什么

    汇总（`_collect_period_actuals`）与明细（`_collect_period_items`）是**两条查询**，
    默认 READ COMMITTED 下看到的是**两个快照**。复审实测：先读到历史签单汇总 100，
    另一个会话取消该订单并提交，随后读明细为空 —— 结账照样成功，
    于是**永久存档成"汇总 100、明细 0 条"**，以后点开明细永远对不上。

    期间锁只能串行化"结账 vs 结账"，**挡不住"结账 vs 业务改单"**（取消订单、退货、
    改回款跑在别的模块里，不认识那把锁）。要挡它只能让这几条查询读同一个快照：
    本事务内第一次读建立快照，之后所有读都看这一份，别的会话怎么改都影响不了。

    ⚠️ 锁与快照必须在**同一条连接**上：锁的作用域是事务，换条连接锁就没了。
    所以这里 `SET TRANSACTION` 和 `pg_advisory_xact_lock` 成对出现，别再拆开写。

    ⚠️ REPEATABLE READ 下若并发事务改了本事务读过的行，提交时会报序列化失败 ——
    那时结账**失败**，而不是存下一份自相矛盾的账。宁可让人重跑一次。
    """
    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        try:
            dialect = session.get_bind().dialect.name
        except Exception:  # noqa: BLE001 —— 判方言失败不该挡住结账
            dialect = ""
        if dialect == "postgresql":
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        await lock_period(session, period)
        yield session
        await session.rollback()  # 只读事务，读完整份快照就结束


async def lock_period(session: AsyncSession, period: str) -> None:
    """给某一期加**事务级期间锁**：同一期的结账/重算必须排队。

    为什么需要它（第五批审查 P1，2026-10-12 真库复现）：从前"这一期结过账没有"
    是**先查一次、再写**，中间没有锁。READ COMMITTED 下两个请求可以都查到
    "没结过"，然后**双双写入**：

        A: 查 → 未结账 → （暂停）
        B: 普通结账 100 → 提交（第一次结账）
        A: 继续 → 也以 `replace=False` 写入 200 → 提交
        终态：数字变成 200、原因仍为空、原明细被替换

    等于**绕过**了"改历史实绩必须走重算并填原因"这条规矩 —— 而结账的意义正是
    让这一期的数字从此不再变。

    为什么用 advisory lock 而不是行锁：结账时这一期**还没有行**（第一行正是要插进去
    的东西），没有行可锁；`analytics_actual_snapshots` 的主键是
    （期间 × 作用域 × 指标），锁任一行都只覆盖一部分作用域。
    期间级互斥就该用期间级的锁。

    与 `order/drafts.py`、`contract/service.py` 同一套写法：SQLite（内存测试/本地
    开发）没有 advisory lock，跳过即可 —— 那边靠唯一约束兜底，这边靠下面
    "锁内重新检查"那一句兜底（单进程测试串行，露不出这个交错）。
    """
    try:
        dialect = session.get_bind().dialect.name
    except Exception:  # noqa: BLE001 —— 拿不到就当作非 PG，别因为判方言失败而挡住结账
        dialect = ""
    if dialect != "postgresql":
        return
    # 键用**期间字符串的稳定摘要**：同一个 period 永远同一个键，
    # 跨进程/跨重启一致（不能用 Python 的 hash()，它带随机盐）。
    key = int.from_bytes(
        hashlib.sha256(f'actual-period:{period}'.encode()).digest()[:8], 'big', signed=True
    )
    await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


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
    before: dict | None = None,
) -> dict:
    """把算好的实绩落成快照，返回 `{"written": 写入行数, "removed": 清除的陈旧行数, "items": 明细条数}`。

    `replace=False`（正常结账）碰上已结过账的期间会**直接报错**，
    而不是悄悄覆盖：覆盖历史数字是要留说明的事，该走重算那条路。

    **进入函数第一件事是取期间锁**（`lock_period`），取到之后才查"结过账没有"。
    这样两个并发的普通结账会串行：后到的那个一定看得见先到的写入，于是走
    "已经结过账了"那条分支报错，而不是把它覆盖掉。

    **重算（`replace=True`）不是"覆盖"，而是"按现在的数重出一份"**：
    上次有、这次没有的键必须一起删掉（返工单第 4 条）。留着会变成
    "某个作用域的数字永远停在上一版"，而报表读快照时又把它当真值显示出来。

    **明细跟着汇总一起冻**（返工单第 4 条）：`items` 是构成这批汇总值的单据清单，
    与汇总同一次写入、一起替换。只冻汇总的话，结账后一张退货单会让
    "点开明细"比"合计"少一笔，用户没法判断该信哪个。

    `before` 若提供，在期间锁内填入实际被替换的旧值，供同事务审计使用。
    """
    # ⚠️ **先取期间锁，再查是否已结账**（第五批审查 P1，2026-10-12）。
    # 顺序反过来就是"查完到写之前有一段没锁的窗口"：两个普通结账请求会双双越过
    # 下面这道检查、后写的把先写的覆盖掉 —— 而结账的全部意义就是"这一期以后不再变"。
    await lock_period(session, period)
    existing = await frozen_for(session, period)
    if before is not None:
        before.clear()
        before.update(existing)
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
