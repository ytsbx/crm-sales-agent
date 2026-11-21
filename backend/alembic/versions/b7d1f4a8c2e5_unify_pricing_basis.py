"""统一产品核价口径：所有报价都是「单价不含运费」。

## 为什么把 `legacy` 去掉（2026-10-09 决定）

运费分离第一版给 `pricing_basis` 留了两条路：历史行 `legacy`（成本含运费）、
新报价 `actual_pass_through`（单价不含运费、运费代收代付）。当时的目的是
"别把已经发给客户的老口径改掉"。

但审查实测出四条缺陷，根因都指向**两套口径并存**：

1. 审批毛利率要在两套口径下各算一遍，结果把"单价之和"与"整单运费"混着减；
2. 建立新版/复制报价时口径切了、快照照抄，出现"显示利润 15、正确 20"；
3. 对客文案不知道该不该写"不含运费"，历史版本被强行加上这句。

而且**库里的历史报价全是测试数据**（开发阶段，还没有真实客户报价），
`legacy` 保护的是一个不存在的对象。所以决定：**统一成一套口径**，
所有版本一律 `actual_pass_through`，代码里不再有第二条分支。

## 这个迁移做什么

1. 存量 `quote_versions.pricing_basis` → `actual_pass_through`；
2. 存量 `sales_orders.pricing_basis` → `actual_pass_through`（订单是跟着版本走的）；
3. 把列注释改成单一口径的说法。

## 这个迁移**不**做什么（说清楚，别让人以为已经算过了）

**不重算存量明细的派生快照**（`minimum_price_snapshot` / `profit_snapshot` /
`profit_rate_snapshot` / `profit_with_refund_snapshot`）。

原因：底价是 `max(利润率反推价, 保护价)`，而快照里**只存了最后的底价**，
没有存"保护价"那一侧。若旧底价是被保护价顶住的，按新口径重算得不到同一个数 ——
迁移里无法判断当时是哪一侧生效，硬算会写出一个看起来正确、其实是猜的金额。

所以：**存量的这几列保持原值不动**，等业务在这条报价上重新核价/再来一版时，
由 `build_item_snapshot` 按单一口径自然算对。开发库里的历史报价都是测试数据，
影响可忽略；真要留着看，也仍然看得到当时的金额（不会被改坏）。

Revision ID: b7d1f4a8c2e5
Revises: a6c9e3d5b8f2
"""

from alembic import op

revision = "b7d1f4a8c2e5"
down_revision = "a6c9e3d5b8f2"
branch_labels = None
depends_on = None

BASIS = "actual_pass_through"
LEGACY = "legacy"


def upgrade() -> None:
    # 存量版本/订单统一到新口径。`WHERE` 写全条件而不是裸更新：
    # 幂等、且只动确实需要改的行（重跑不会"改了又改"）。
    op.execute(
        f"UPDATE quote_versions SET pricing_basis = '{BASIS}' "
        f"WHERE pricing_basis IS DISTINCT FROM '{BASIS}'"
    )
    op.execute(
        f"UPDATE sales_orders SET pricing_basis = '{BASIS}' "
        f"WHERE pricing_basis IS DISTINCT FROM '{BASIS}'"
    )
    # 注释跟着改：列注释是"这一列什么意思"的权威说明，留着双口径的说法会误导后来人。
    op.alter_column(
        "quote_versions",
        "pricing_basis",
        comment="产品核价口径。单一口径：actual_pass_through = 产品单价不含运费，"
                "运费按已确认的实际金额由公司代收代付（2026-10-09 统一，不再有 legacy）。",
        existing_type=None,
        existing_nullable=False,
    )


def downgrade() -> None:
    """回退：把**统一之后没有 legacy 行**这件事如实还原为 legacy。

    这不是"恢复原本的两套口径"——原本哪些行是 legacy 已经无从得知（升级时被
    一起改掉了）。所以回退只能把全部行标成 legacy（回到"成本含运费"的算法），
    并恢复注释。写清楚是为了让回退的人知道自己在做什么。
    """
    op.execute(f"UPDATE quote_versions SET pricing_basis = '{LEGACY}'")
    op.execute(f"UPDATE sales_orders SET pricing_basis = '{LEGACY}'")
    op.alter_column(
        "quote_versions",
        "pricing_basis",
        comment="产品核价口径：legacy=成本含运费；actual_pass_through=产品价不含运费、运费代收代付",
        existing_type=None,
        existing_nullable=False,
    )
