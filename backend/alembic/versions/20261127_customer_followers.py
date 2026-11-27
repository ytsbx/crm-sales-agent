"""客户跟进人关联表（一个客户可以有多名跟进人）

背景：`customers.owner_id` 单负责人无法表达企微里"多个员工加同一个客户"。
本迁移只建表并回填现有负责人为第一条跟进人，不改任何业务数据、不删 `owner_id`。

回填包含软删客户（`deleted_at` 非空）——历史可追溯；`owner_id` 为空的公海客户
不产生跟进人。
"""

from alembic import op
import sqlalchemy as sa


revision = "d4f6a8b0c2e1"
down_revision = "c3d5e7f9a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_followers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index(
        "uq_customer_followers_active",
        "customer_followers",
        ["customer_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_customer_followers_customer", "customer_followers", ["customer_id"]
    )
    op.create_index("ix_customer_followers_user", "customer_followers", ["user_id"])

    op.execute(
        sa.text(
            """
            INSERT INTO customer_followers
                (customer_id, user_id, source, created_at, updated_at)
            SELECT id, owner_id, 'backfill', now(), now()
            FROM customers
            WHERE owner_id IS NOT NULL
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_customer_followers_user", table_name="customer_followers")
    op.drop_index("ix_customer_followers_customer", table_name="customer_followers")
    op.drop_index("uq_customer_followers_active", table_name="customer_followers")
    op.drop_table("customer_followers")
