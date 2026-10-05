"""合同文档加生成请求的幂等键（防网络重试生成重复草稿）。

Revision ID: c7e3a9f5b2d8
Revises: b5d1f7c3e9a2
Create Date: 2026-10-05

背景（外部审查 2026-10-05 第 6 条第 3 点）：生成合同草稿没有请求去重。
点一次「生成」如果超时重发，台账上会多出一份内容完全相同的草稿，
而且两份各有一个 doc_no，事后分不清哪份才是有效的。

做法：加一个**可空**的 request_key（前端打开生成弹窗时生成一个，
重复点击 / 重试带的是同一个值）。
- 唯一约束兜住「同一个 key 只能落一份」；
- 可空，所以历史数据完全不受影响（PostgreSQL 里多个 NULL 互不冲突）。

与已有的并发保护的分工：模板版本唯一约束 + 保存点重试管的是「两个管理员同时建模板」，
这条管的是「同一个人的同一次生成被重发」——两件事，别混。
"""

from alembic import op
import sqlalchemy as sa

revision = "c7e3a9f5b2d8"
down_revision = "b5d1f7c3e9a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contract_documents",
        sa.Column("request_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_contract_document_request_key", "contract_documents", ["request_key"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_contract_document_request_key", "contract_documents", type_="unique"
    )
    op.drop_column("contract_documents", "request_key")
