"""approval rules engine (设计稿 _6 的国内业务版)

审批流转规则：多条件免审 / 极速通道 / 异常路由加签 / 规则沙盒 / 版本管理。
`approval_rules` 存草稿与运维开关，`approval_rule_versions` 存每次发布的
不可变快照——引擎只按已发布版本求值，审批留痕才能回答"当时按哪版规则走的"。

Revision ID: a5c8e2f7b9d1
Revises: 2d6f9b4c8e15
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5c8e2f7b9d1'
down_revision: Union[str, None] = '2d6f9b4c8e15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'approval_rules',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('priority', sa.BigInteger(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('conditions', sa.JSON(), nullable=False),
        sa.Column('action', sa.JSON(), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('published_version_no', sa.BigInteger(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'approval_rule_versions',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('rule_id', sa.BigInteger(), sa.ForeignKey('approval_rules.id'), nullable=False),
        sa.Column('version_no', sa.BigInteger(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('published_by', sa.BigInteger(), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_approval_rule_versions_rule', 'approval_rule_versions', ['rule_id'])


def downgrade() -> None:
    op.drop_index('ix_approval_rule_versions_rule', table_name='approval_rule_versions')
    op.drop_table('approval_rule_versions')
    op.drop_table('approval_rules')
