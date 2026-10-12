"""反向验证脚本（第三轮）：把第 2 项（跟单节点）退回旧写法（B 批）。

做法：把 `sync_payment_milestones` 整个函数体换回"只补不撤、没有节点就返回"的旧实现。

用法：
    python3 /tmp/revert3_b.py apply
    python3 /tmp/revert3_b.py restore
"""

import io
import sys

ROOT = "/Users/zhaorongkai/Desktop/CRM-Sales-Agent-V1.1/backend/"
PATH = "app/modules/order/milestones.py"

OLD_BODY = '''async def sync_payment_milestones(
    session: AsyncSession, order_id: int, *, operator_id: int | None = None
) -> None:
    """（反向验证用的旧实现：只往前写、节点不存在就直接返回。）"""
    rows = (
        await session.execute(
            select(OrderMilestone).where(
                OrderMilestone.order_id == order_id,
                OrderMilestone.node.in_(("deposit", "payment")),
            )
        )
    ).scalars().all()
    if not rows:
        return
    by_node = {row.node: row for row in rows}

    confirmed = (
        await session.execute(
            select(
                func.min(PaymentRecord.received_date),
                func.max(PaymentRecord.received_date),
                func.count(PaymentRecord.id),
            ).where(
                PaymentRecord.order_id == order_id,
                PaymentRecord.status == "confirmed",
                PaymentRecord.received_date.is_not(None),
            )
        )
    ).one()
    earliest, latest, count = confirmed
    if not count or earliest is None:
        return

    deposit = by_node.get("deposit")
    if deposit is not None and deposit.actual_date is None:
        deposit.actual_date = earliest
        _clear_skip(deposit)

    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(ReceivablePlan.amount), 0),
                func.coalesce(
                    func.sum(
                        select(func.coalesce(func.sum(PaymentRecord.received_amount), 0))
                        .where(
                            PaymentRecord.receivable_plan_id == ReceivablePlan.id,
                            PaymentRecord.status == "confirmed",
                        )
                        .scalar_subquery()
                    ),
                    0,
                ),
            ).where(ReceivablePlan.order_id == order_id)
        )
    ).one()
    planned, received = (Decimal(str(x or 0)) for x in totals)
    payment = by_node.get("payment")
    if payment is not None and planned > 0 and received >= planned:
        payment.actual_date = latest if latest is not None else payment.actual_date
        _clear_skip(payment)
    await session.flush()


'''


def _markers(text: str) -> tuple[int, int]:
    start = text.index("async def sync_payment_milestones(")
    end = text.index("async def _apply_derived_fact(")
    return start, end


def apply():
    full = ROOT + PATH
    text = io.open(full, encoding="utf-8").read()
    start, end = _markers(text)
    io.open(full, "w", encoding="utf-8").write(text[:start] + OLD_BODY + text[end:])
    print(f"  已退回：{PATH}（sync 换回只补不撤的旧实现）")


def restore():
    import shutil

    shutil.copyfile("/tmp/round3_revert_backup/app_modules_order_milestones.py", ROOT + PATH)
    print(f"  已还原：{PATH}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    {"apply": apply, "restore": restore}[action]()
