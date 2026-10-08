"""一个客户最多一个主联系人（第十批 10.4）。

## 这条约束为什么该在库里

「主要联系人」是**客户级**的唯一资源 —— 一个客户只能有一个。但应用层此前有
三个口子都能把它破坏掉：

1. `create_contact_for_customer` 只在"自动判断"那条路上看有没有主，**显式传
   `is_primary=True` 直接置真**，不取消同客户其它主；
2. `bind-customer` / `change-customer` 两个改绑入口只在 `is_primary` 为真时进分支，
   **换客户时主标记跟着走** —— 新客户凭空多一个主，老客户主位空着；
3. `set_primary_contact` 的 UPDATE **不加锁**，并发下两个人各自"取消别人 + 设自己"
   交错执行，照样留下两个主。

前两条已在应用层收敛（同一个客户的三个入口现在都走同一条口径），这条索引是
**最后一道兜底**：挡住绕过接口直接写库、以及将来新增入口时漏掉的那一处。

## 为什么必须先清老数据再建索引

库里现在就存在"同一客户多个主"的历史行（三个口子长期敞着），
直接 `CREATE UNIQUE INDEX` 会报
`could not create unique index ... duplicate key`。所以先把重复的收敛掉。

**留哪一条**：同一个客户下保留 `id` **最小**的那条（最早录入的，通常是人工
真正指定的那个），其余清掉标记。开发阶段数据都是构造出来的，按这个口径足够；
真要追溯"后来为什么换了主"，那条历史在审计里。

## 索引形状

    UNIQUE (customer_id) WHERE is_primary AND deleted_at IS NULL

**部分索引**：只管"没删且是主"的行。删掉的联系人不占主位 ——
不然删一个主联系人之后，新主就再也设不上了。
"""
from alembic import op

revision = "c3f8a1d6e9b4"
down_revision = "a7c1e5b9d3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ① 同一个客户下只留 id 最小的那条主联系人，其余取消标记
    op.execute(
        """
        UPDATE contacts SET is_primary = FALSE
        WHERE is_primary
          AND deleted_at IS NULL
          AND id NOT IN (
              SELECT DISTINCT ON (customer_id) id
              FROM contacts
              WHERE is_primary AND deleted_at IS NULL
              ORDER BY customer_id, id ASC
          )
        """
    )
    # ② 兜底的部分唯一索引
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_contacts_primary_per_customer "
        "ON contacts (customer_id) WHERE is_primary AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_contacts_primary_per_customer")
