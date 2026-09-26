"""agent_executions: record caller identity snapshot

03-API §38 末段要求「所有 Tool 调用必须记录 user_id / role / data_scope / risk_level /
input / output / result」。原有表已存 risk_level 与输入输出，缺调用者身份三项。

存快照（role_snapshot / data_scope_snapshot）而不是只存 user_id：
角色与数据范围以后会变，只靠 session_id 反查无法还原
「当时这个人有没有权限看到这条数据」。

Revision ID: f2c8d4e6a1b3
Revises: e9b3c5d7f1a2
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2c8d4e6a1b3'
down_revision: Union[str, None] = 'e9b3c5d7f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_executions', sa.Column('user_id', sa.BigInteger(), nullable=True))
    op.add_column(
        'agent_executions', sa.Column('role_snapshot', sa.String(length=255), nullable=True)
    )
    op.add_column(
        'agent_executions',
        sa.Column('data_scope_snapshot', sa.String(length=32), nullable=True),
    )
    op.create_index(
        'ix_agent_executions_user', 'agent_executions', ['user_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_agent_executions_user', table_name='agent_executions')
    op.drop_column('agent_executions', 'data_scope_snapshot')
    op.drop_column('agent_executions', 'role_snapshot')
    op.drop_column('agent_executions', 'user_id')
