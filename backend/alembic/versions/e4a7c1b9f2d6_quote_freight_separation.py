"""报价「产品价格与运费分离」：版本级计算口径 + 金额拆分 + 运费确认时刻。

## 业务口径（2026-10-09 与主人确认，以该份需求为准）

产品单价**不含运费**，运费单独列出、按实际金额**代收代付**（公司不赚不赔）。
因此：

1. **产品核价的基础改为商品成本**，运费不再进 `base_cost`
   —— 见 `pricing/service.calculate_price`；
2. 报价版本要能回答"这一版是按哪种口径算的"，于是新增 `pricing_basis`；
3. 总额公式不变（`货款 + 费用 + 折扣`），但界面与文件要把**运费从"附加费用"里
   单列出来**，于是新增 `logistics_amount` / `other_charge_amount` 两个拆分列；
4. 运费"未填写"与"明确确认零运费"必须分得开，于是新增确认时刻列。

## 为什么 `pricing_basis` 老数据一律 `legacy`

历史版本的单价、成本、运费、利润**必须保持原样**，不能被新口径重新解释 ——
老的 `base_cost` 里是**含**运费的，拿今天的公式去回算会得到不同的建议价与底价。
所以全部现有行标成 `legacy`（历史计算方式），新报价才用 `actual_pass_through`。

**注意列默认值刻意去掉**：迁移里先带 `server_default='legacy'` 完成回填，
随后立刻 `alter_column(server_default=None)` —— 让"忘写这个字段"变成显式的
NULL/报错，而不是静默落成某一个口径。ORM 侧的 `default='actual_pass_through'`
只作用于走 ORM 新建的版本（那正是"新报价"）。

## `logistics_confirmed_at` 为什么是时间戳而不是布尔

口径要求：**未填写运费** 与 **明确确认零运费** 要分得开，且"明确零运费需要确认
依据，不能把空输入默认成已确认的零元"。金额列本身分不出这两者（都是 0），
所以要单独记"什么时候被人确认的"。

用时间戳而不是布尔，和 `product_costs.stopped_at` 同一个理由：翻记录时能看出时刻。

## 对老数据的影响

- `quote_versions`：`logistics_amount` / `other_charge_amount` 先按**现有**
  `charge_amount` 里运费与非运费的实际构成回填，`pricing_basis='legacy'`。
  这样历史版本的界面拆分显示立刻是对的，且总额一分不变。
- `quote_charges`：`logistics_confirmed_at` 一律 NULL（历史费用没有"确认"这个动作），
  不对老数据做任何语义假设。
"""
from alembic import op
import sqlalchemy as sa

revision = "e4a7c1b9f2d6"
down_revision = "d9b4e2f7a1c8"
branch_labels = None
depends_on = None

#: 历史计算方式：`base_cost` 含运费（老口径）
BASIS_LEGACY = "legacy"
#: 产品价格不含运费、运费原额代收代付（新口径）
BASIS_ACTUAL_PASS_THROUGH = "actual_pass_through"


def upgrade() -> None:
    # ---- 1. 报价版本：计算口径与金额拆分 ----
    op.add_column(
        "quote_versions",
        sa.Column(
            "pricing_basis",
            sa.String(length=32),
            nullable=True,
            server_default=BASIS_LEGACY,
            comment="产品核价口径：legacy=成本含运费；actual_pass_through=产品价不含运费、运费代收代付",
        ),
    )
    op.add_column(
        "quote_versions",
        sa.Column(
            "logistics_amount",
            sa.Numeric(16, 2),
            nullable=False,
            server_default="0",
            comment="运费收费金额（= 客户承担的运费，代收代付）",
        ),
    )
    op.add_column(
        "quote_versions",
        sa.Column(
            "other_charge_amount",
            sa.Numeric(16, 2),
            nullable=False,
            server_default="0",
            comment="非运费、非折扣的附加费用合计",
        ),
    )

    # ---- 2. 按现有数据回填两个拆分列：总额一分不动 ----
    # 运费只认分类码 `logistics`，**不按说明文字里含"运费"去认**（口径明确要求）。
    op.execute(
        """
        UPDATE quote_versions v SET
          logistics_amount = COALESCE((
            SELECT SUM(c.amount) FROM quote_charges c
            WHERE c.quote_version_id = v.id
              AND c.is_discount = false
              AND c.charge_type = 'logistics'
          ), 0),
          other_charge_amount = COALESCE((
            SELECT SUM(c.amount) FROM quote_charges c
            WHERE c.quote_version_id = v.id
              AND c.is_discount = false
              AND c.charge_type <> 'logistics'
          ), 0)
        """
    )

    # ---- 3. 去掉列默认值：以后"忘写口径"要显式暴露，不静默落成某个值 ----
    op.alter_column("quote_versions", "pricing_basis", server_default=None)

    # ---- 4. 费用行：运费确认时刻（空输入 ≠ 已确认的零元）----
    op.add_column(
        "quote_charges",
        sa.Column(
            "logistics_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="物流费用被业务确认的时刻；NULL=尚未确认（金额为 0 不等于已确认零运费）",
        ),
    )


def downgrade() -> None:
    op.drop_column("quote_charges", "logistics_confirmed_at")
    op.drop_column("quote_versions", "other_charge_amount")
    op.drop_column("quote_versions", "logistics_amount")
    op.drop_column("quote_versions", "pricing_basis")
