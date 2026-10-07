"""同一订单的发货批次号唯一（第九批 §9.6 返修）。

## 为什么加这条约束

排批次此前**没有锁**（而发货登记有）。受控交错复现：两个请求同时读到
"订购 10、未计划 10"，各排 8 件 → 总计划 16，两个批次的 `batch_no` 还都是 1。
另外，取消掉编号最大的那一批之后，新批次会复用该编号（取号只看未取消批次）。

应用层已经修了两处：

- 排批次 / 取消批次 / 实发统一锁订单行（先订单、后批次），等锁后重新读余额与状态；
- 取号改为看**全部批次（含已取消）**。

这条唯一约束是**兜底**：万一并发仍然漏过应用层的锁（极端隔离级别、手工 SQL），
也应该在库里撞住，而不是写出两个"第 1 批"。

## 迁移前先查重

历史库里若已存在重复的 `(order_id, batch_no)`，直接建约束会让整个升级中断，
而且报的是数据库层的约束错误，看不出是哪张订单。所以这里**先查再建**：
发现重复就抛出可读的错误并点名是哪张订单，由人来决定怎么处理。

**刻意不自动重编号**：已经发出去的批次改变编号，等于把"第 N 批发货"这个
事实改写掉（动态跟单节点也是按 batch_no 命名的）。这条与其他几处历史数据
口径一致 —— 宁可停下来让人处理，不静默改历史。
"""
import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    duplicates = connection.execute(
        sa.text(
            "select order_id, batch_no, count(*) as n "
            "from order_shipment_batches "
            "group by order_id, batch_no having count(*) > 1"
        )
    ).fetchall()
    if duplicates:
        detail = "、".join(
            f"订单 {row[0]} 的批次 {row[1]}（{row[2]} 条）" for row in duplicates
        )
        raise RuntimeError(
            "发货批次表存在重复的「订单 + 批次号」，必须先人工处理再升级："
            f"{detail}。本迁移刻意不自动重编号 —— 已发批次改号会把历史事实改掉。"
        )

    op.create_unique_constraint(
        "uq_shipment_batch_no", "order_shipment_batches", ["order_id", "batch_no"]
    )


def downgrade():
    op.drop_constraint(
        "uq_shipment_batch_no", "order_shipment_batches", type_="unique"
    )
