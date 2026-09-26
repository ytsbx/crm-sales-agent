"""notifications: record delivery channel and wecom push result

PRD §25 要求通知支持"站内通知 + 企业微信通知"，03-API §33 要求
`GET/PATCH /notification-settings`。要回答"这条到底有没有发到企微"，
必须把渠道与投递结果落在通知行上，否则只能靠猜。

wecom_status 为 NULL 表示**没走企微渠道**（与"发失败了"区分开）；
skipped 表示走了企微但条件不具备（没配应用 / 该用户没绑 userid）。

Revision ID: 6e2b8d4f1c37
Revises: 4a9c2e6b8d1f
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6e2b8d4f1c37'
down_revision: Union[str, None] = '4a9c2e6b8d1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'notifications',
        sa.Column('channel', sa.String(length=16), nullable=False, server_default='inapp'),
    )
    op.add_column(
        'notifications', sa.Column('wecom_status', sa.String(length=16), nullable=True)
    )
    op.add_column(
        'notifications', sa.Column('wecom_error', sa.String(length=255), nullable=True)
    )
    op.add_column(
        'notifications',
        sa.Column('wecom_sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_notifications_wecom_status', 'notifications', ['wecom_status'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_notifications_wecom_status', table_name='notifications')
    op.drop_column('notifications', 'wecom_sent_at')
    op.drop_column('notifications', 'wecom_error')
    op.drop_column('notifications', 'wecom_status')
    op.drop_column('notifications', 'channel')
