"""操作耗时埋点（文档 §六「评价操作是否省时」/ 场景18）。

场景18 要回答的不是"单据创建得多快"，而是"**业务员在这件事上花了多久**"，
并且要与现有 Excel 流程比。所以：

- 计时在前端做（进入流程记起点、提交成功报耗时），服务端只校验与聚合；
- 只接受白名单里的流程名，不接受自由文本，否则汇总会碎成一堆同义键；
- 明显不合理的耗时报 422 而不是硬塞进统计：隔夜挂着页面不叫"操作耗时"，
  把它算进去反而会让平均值变得毫无参考价值（宁可少一条样本）。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.analytics.model import OperationTiming
from app.modules.user.model import User

#: 流程白名单：键给程序用，标签给人看（汇总表直接显示标签）
OPERATION_LABELS: dict[str, str] = {
    "quote_from_inquiry": "需求 → 报价",
    "sample_from_inquiry": "需求 → 打样",
}

#: 单次操作上限：8 小时。超过基本是"开着页面过夜"，不是一次操作——
#: 记进去只会污染平均值。调这个数要问业务，不要自己放宽。
MAX_DURATION_MS = 8 * 60 * 60 * 1000

#: 汇总里要照实说明"哪些指标现在还没有"
_NOT_MEASURED_NOTE = (
    "耗时与手输字段数来自前端埋点；"
    "「跨系统重复录入次数」「与 Excel 流程的对比结果」需要真实试用时人工记录，"
    "系统暂不自动产出——不要拿这里的数字当成场景18 的全部证据。"
)


async def record_timing(
    session: AsyncSession,
    *,
    user: CurrentUser,
    operation: str,
    duration_ms: int,
    business_type: str | None = None,
    business_id: int | None = None,
    typed_fields: int = 0,
    rework_count: int = 0,
    source: str = "web",
) -> OperationTiming:
    if operation not in OPERATION_LABELS:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"未知的流程标识：{operation}（可选：{'、'.join(OPERATION_LABELS)}）",
            422,
        )
    if duration_ms <= 0 or duration_ms > MAX_DURATION_MS:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"耗时 {duration_ms}ms 超出可接受范围（0 < 耗时 ≤ {MAX_DURATION_MS}ms）。"
            "若是开着页面去做了别的事，请当作两次操作分别记，不要合并成一条",
            422,
        )
    row = OperationTiming(
        operation=operation,
        user_id=user.id,
        duration_ms=duration_ms,
        business_type=business_type,
        business_id=business_id,
        typed_fields=max(int(typed_fields or 0), 0),
        rework_count=max(int(rework_count or 0), 0),
        source=source,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


def serialize_timing(row: OperationTiming, *, user_name: str | None = None) -> dict:
    return {
        "id": row.id,
        "operation": row.operation,
        "operation_label": OPERATION_LABELS.get(row.operation, row.operation),
        "user_id": row.user_id,
        "user_name": user_name,
        "duration_ms": row.duration_ms,
        "typed_fields": row.typed_fields,
        "rework_count": row.rework_count,
        "business_type": row.business_type,
        "business_id": row.business_id,
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _percentile(sorted_values: list[int], ratio: float) -> int | None:
    """朴素百分位（样本量小，不做插值）。空样本返回 None，不给 0 假装有数。"""
    if not sorted_values:
        return None
    index = min(int(len(sorted_values) * ratio), len(sorted_values) - 1)
    return sorted_values[index]


async def timing_summary(
    session: AsyncSession, *, user: CurrentUser, days: int = 30
) -> dict:
    """按流程聚合耗时（同时给出按人的明细，便于看"是谁在哪些流程上慢"）。

    数据范围：非 all 范围的用户只看自己报的。汇总的意义是给本人/主管看
    "这件事要花多久"，跨范围看别人的耗时没有业务必要性。
    """
    since = datetime.now(UTC) - timedelta(days=max(int(days or 30), 1))
    stmt = select(OperationTiming).where(OperationTiming.created_at >= since)
    if user.data_scope != "all":
        stmt = stmt.where(OperationTiming.user_id == user.id)
    rows = list((await session.execute(stmt)).scalars().all())

    names: dict[int, str] = {}
    user_ids = {row.user_id for row in rows}
    if user_ids:
        name_rows = await session.execute(
            select(User.id, User.name).where(User.id.in_(user_ids))
        )
        names = {uid: name for uid, name in name_rows.all()}

    by_operation: dict[str, list[OperationTiming]] = {}
    for row in rows:
        by_operation.setdefault(row.operation, []).append(row)

    summary: list[dict] = []
    for operation, label in OPERATION_LABELS.items():
        group = by_operation.get(operation, [])
        durations = sorted(r.duration_ms for r in group)
        summary.append(
            {
                "operation": operation,
                "operation_label": label,
                "samples": len(group),
                "avg_ms": round(sum(durations) / len(durations)) if durations else None,
                "median_ms": _percentile(durations, 0.5),
                "p90_ms": _percentile(durations, 0.9),
                "avg_typed_fields": (
                    round(sum(r.typed_fields for r in group) / len(group), 1)
                    if group
                    else None
                ),
                "avg_rework_count": (
                    round(sum(r.rework_count for r in group) / len(group), 1)
                    if group
                    else None
                ),
            }
        )

    by_user: dict[int, dict] = {}
    for row in rows:
        bucket = by_user.setdefault(
            row.user_id, {"user_id": row.user_id, "samples": 0, "total_ms": 0}
        )
        bucket["samples"] += 1
        bucket["total_ms"] += row.duration_ms
    user_rows = [
        {
            "user_id": uid,
            "user_name": names.get(uid, f"#{uid}"),
            "samples": bucket["samples"],
            "avg_ms": round(bucket["total_ms"] / bucket["samples"]),
        }
        for uid, bucket in by_user.items()
    ]
    user_rows.sort(key=lambda r: -r["samples"])

    return {
        "days": days,
        # 汇总里回带白名单，前端拍耗时标签时不用再抄一份常量
        "operations": [{"value": k, "label": v} for k, v in OPERATION_LABELS.items()],
        "summary": summary,
        "by_user": user_rows,
        "note": _NOT_MEASURED_NOTE,
    }


__all__ = [
    "MAX_DURATION_MS",
    "OPERATION_LABELS",
    "record_timing",
    "serialize_timing",
    "timing_summary",
]
