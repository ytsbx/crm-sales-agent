"""订单金额组成快照：货款 / 运费 / 其他费用 / 优惠 / 核价口径。

## 为什么要落在订单上

「产品价格与运费分离」之后，订单总额里同时含产品货款与**代收代付的运费**。
订单页只显示一个总金额的话，业务员没法解释"这笔钱是怎么来的"，财务也没法
对着报价核。所以把组成一并冻结到订单上：

    应付合计 = 产品货款 + 运费 + 其他费用 + 优惠

## 为什么是快照，不是回查报价

报价在转单之后还可能被改（改备注、重算、甚至失效）。订单显示运费时若实时去读
报价，客户昨天看到的订单金额今天就会变 —— 对外单据不能这样。所以转单那一刻
把四个值抄到订单上，之后订单只读自己的。

## 对老数据的影响

四个列**可空**，历史订单一律 `NULL`（当时没有这个口径），订单页明确显示
"金额组成待核实"，**不拿今天的报价数据回填冒充** —— 那会造出一个当时并不存在的
组成。`pricing_basis` 同样留 NULL = 当时是含运费的老口径。

## 为什么手工建的订单要填满而不是留空

手工订单没有报价来源，它的全部金额就是产品货款本身。留 NULL 会让它显示成
"组成待核实"，那是另一回事（那是"数据缺失"，这是"确实没有运费"）。
写入点在 `order/service.create_order`，货款=总额、其余为 0。
"""
from alembic import op
import sqlalchemy as sa

revision = "f5b8d2c4a7e1"
down_revision = "e4a7c1b9f2d6"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("goods_amount", "产品货款（Σ 产品单价×数量）"),
    ("logistics_amount", "运费（代收代付，已含在 total_amount 里）"),
    ("other_charge_amount", "其他附加费用（非运费、非折扣，已含在 total_amount 里）"),
    ("discount_amount", "优惠（负数，与报价/库内约定一致）"),
)


def upgrade() -> None:
    for name, comment in _COLUMNS:
        op.add_column(
            "sales_orders",
            sa.Column(name, sa.Numeric(16, 2), nullable=True, comment=comment),
        )
    op.add_column(
        "sales_orders",
        sa.Column(
            "pricing_basis",
            sa.String(length=32),
            nullable=True,
            comment="转单时报价版本的产品核价口径；NULL=历史订单（含运费的老口径）",
        ),
    )


def downgrade() -> None:
    op.drop_column("sales_orders", "pricing_basis")
    for name, _ in reversed(_COLUMNS):
        op.drop_column("sales_orders", name)
