"""请求幂等表 `request_keys`（第八批 8.15，供 7.9 / 8.9 复用）。

一次"服务端已成功、客户端没收到响应"的重试，如果没有稳定请求键就会造出重复
客户/重复报价/重复单据。这张表就是那个键的落点：同键同内容回放原结果、
同键不同内容报冲突、同键并发只允许一个成功。

唯一约束 `(user_id, action, request_key)` 是**并发防线**：它必须落在数据库上，
不能只靠进程内的锁或"先查再插"——两个进程同时查都查不到，然后各插一行。
"""
from alembic import op
import sqlalchemy as sa

revision = "d4f5a6b7c8e9"
down_revision = "c3e4f5a6b7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "request_keys",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("result_type", sa.String(length=64), nullable=True),
        sa.Column("result_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "action", "request_key", name="uq_request_key_scope"
        ),
    )
    op.create_index("ix_request_keys_created", "request_keys", ["created_at"])


def downgrade():
    op.drop_index("ix_request_keys_created", table_name="request_keys")
    op.drop_table("request_keys")
