"""扩大报价版本的客户名快照列：128 → 200（第八批 §8.7 返修第二轮）。

## 为什么要改

上一轮给 `quote_versions` 加 `customer_name_snapshot` 时手写了 `varchar(128)`，
**没有回头核对源列**：`customers.name` 是 `varchar(200)`。

后果（审查 2026-10-07 在 PostgreSQL 上实测）：**129~200 字的合法客户名一建报价就挂**
—— `asyncpg: value too long for type character varying(128)`（SQLSTATE 22001），
报价单根本生成不出来。这是上一轮自己引入的缺陷，不是历史遗留。

## 取长度的一贯做法

快照列的长度**照源列抄**，不另想一个数：`contact_name_snapshot` 抄
`contacts.name`（64，本轮核对一致，无需改）。手写一个"看着够用"的数字，
迟早会在某个边界上炸出来。

## 为什么要单独一条迁移而不是改上一条

上一条迁移（`c1d2e3f4a5b6`）已经发到两个仓库、并且**已经在开发库上执行过**，
改它等于让"跑过的人"和"没跑过的人"结构不一致。追加一条是唯一安全的做法。
"""
import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "quote_versions",
        "customer_name_snapshot",
        existing_type=sa.String(length=128),
        type_=sa.String(length=200),
        existing_nullable=True,
    )


def downgrade():
    # 回退会把超过 128 字的名字截断 —— 明确写出来，别让人以为它是无损的。
    op.alter_column(
        "quote_versions",
        "customer_name_snapshot",
        existing_type=sa.String(length=200),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
