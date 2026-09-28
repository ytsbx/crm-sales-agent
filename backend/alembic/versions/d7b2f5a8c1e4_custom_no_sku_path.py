"""定制无 SKU 通路：需求编号 + 报价/打样明细可为定制项（CRM 完整实现方案 场景09）

文档场景09：「尚无正式 SKU 时，用需求编号也能询价、报价、打样，投产之后再关联上」。
此前三处都是硬约束，导致这条主线整条走不通：
- custom_inquiries 没有编号，只有自增 id——报价/打样无处引用；
- quote_items.sku_id 非空 + 服务层查不到 SKU 直接 404；
- sample_items.sku_id 非空 + 路由层同样 404。

本迁移把约束松开并加引用列：

1. custom_inquiries.inquiry_no（唯一）：走 settings/numbering 取号（XQ+日期+4位）。
   **历史行回填**，按"修订链"（coalesce(root_id, id)）共用一个号——
   与新规则一致：编号标识需求、version 标识修订，v2 不换号，
   否则已经发出去的报价会断在中间。回填日期取链条首版的创建日期。
2. quote_items：sku_id 去掉 NOT NULL，加 inquiry_id / inquiry_no_snapshot
   （编号快照：需求改名或归档后，这张报价仍要说得清"当时对着哪条需求报的"）。
3. sample_items：同上，另加 item_name（没有 SKU 名称可用时的展示名）。
"""

import sqlalchemy as sa
from alembic import op

revision = "d7b2f5a8c1e4"
down_revision = "c6a1e8d4f2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- 1. 定制需求编号 -------------------------------------------------
    op.add_column(
        "custom_inquiries", sa.Column("inquiry_no", sa.String(32), nullable=True)
    )
    # 回填：同一修订链一个号，日期取链条首版创建日，序号按当天先后
    op.execute(
        """
        with chains as (
            select coalesce(root_id, id) as chain_id,
                   min(id) as first_id,
                   min(created_at) as first_at
            from custom_inquiries
            group by coalesce(root_id, id)
        ),
        numbered as (
            select chain_id,
                   first_at,
                   row_number() over (partition by first_at::date order by first_id) as seq
            from chains
        )
        update custom_inquiries ci
        set inquiry_no = 'XQ' || to_char(n.first_at, 'YYYYMMDD')
                         || lpad(n.seq::text, 4, '0')
        from numbered n
        where coalesce(ci.root_id, ci.id) = n.chain_id
          and ci.inquiry_no is null
        """
    )
    # 普通索引而非唯一：修订链的 v1/v2 共用同一个需求编号，
    # 唯一索引会在"第一次修订"时就报冲突（编号标识需求、version 标识修订）
    op.create_index("ix_custom_inquiries_no", "custom_inquiries", ["inquiry_no"])

    # ---- 2. 报价明细可为定制项 -------------------------------------------
    op.alter_column("quote_items", "sku_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("quote_items", sa.Column("inquiry_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "quote_items", sa.Column("inquiry_no_snapshot", sa.String(32), nullable=True)
    )
    op.create_index("ix_quote_items_inquiry", "quote_items", ["inquiry_id"])

    # ---- 3. 打样明细可为定制项 -------------------------------------------
    op.alter_column("sample_items", "sku_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("sample_items", sa.Column("inquiry_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "sample_items", sa.Column("inquiry_no_snapshot", sa.String(32), nullable=True)
    )
    op.add_column("sample_items", sa.Column("item_name", sa.String(200), nullable=True))
    op.create_index("ix_sample_items_inquiry", "sample_items", ["inquiry_id"])


def downgrade() -> None:
    # 定制明细没有 SKU，回滚到非空约束前必须先处理这些行——这里显式删除，
    # 不静默留一堆违反约束的数据（回滚是有损的，可接受）
    op.execute("delete from sample_items where sku_id is null")
    op.execute("delete from quote_items where sku_id is null")
    op.drop_index("ix_sample_items_inquiry", table_name="sample_items")
    op.drop_column("sample_items", "item_name")
    op.drop_column("sample_items", "inquiry_no_snapshot")
    op.drop_column("sample_items", "inquiry_id")
    op.alter_column("sample_items", "sku_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_index("ix_quote_items_inquiry", table_name="quote_items")
    op.drop_column("quote_items", "inquiry_no_snapshot")
    op.drop_column("quote_items", "inquiry_id")
    op.alter_column("quote_items", "sku_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_index("ix_custom_inquiries_no", table_name="custom_inquiries")
    op.drop_column("custom_inquiries", "inquiry_no")
