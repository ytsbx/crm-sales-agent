"""报价明细记录所用的 SKU 主数据版本号（第八批 §8.14 返修）。

## 为什么加这一列

审查（2026-10-07）指出：`require_confirmed_master` 写好了但业务代码零调用，
"正式报价只用已确认的主数据版本"只存在于注释里。

本轮把闸门接进了报价流程（`quote/service.build_item_snapshot` 逐条明细经过
`product.master.resolve_confirmed_master`）。接线之后还差一件事：
**事后能回答"这条明细是按哪一版主数据算的"** —— 所以把版本号落成快照。

存版本号而不是只存"确认过"这个布尔：确认值会随新的确认动作演进
（"以来源为准"会写回本地并生成新版本），只记布尔就没法还原当时用的是哪一版。
与旁边那排 `*_snapshot` 同一性质：追溯靠这一行自己的记录，不回查当下的主数据。

## 口径

`NULL` 表示生成这条明细时该 SKU **还没有任何已确认的主数据**（当前业务下
字段权威表整表为空，所以新数据基本都会是 NULL —— 这是如实的，不是漏写）。

## 历史数据不回填

老明细行留 NULL。当时有没有确认记录、是哪一版，只有当事人知道；
迁移若拿"当前最新确认版本"填进去，就是在制造假的追溯证据。
"""
import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "quote_items",
        sa.Column("master_version_no", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("quote_items", "master_version_no")
