"""wecom integration tables

02-ER §6 的四张表：wecom_users / wecom_external_contacts /
wecom_follow_relationships / wecom_sync_jobs。

设计取舍（总设计文档 §5.2 / §5.3）：
- 企微外部联系人与 CRM Contact **分表**，`crm_contact_id` 为空即"待归一"，
  不给自动绑定留任何后门；
- 跟进关系单独一张表（一个联系人可被多个员工添加）；
- 同步任务落库（wecom_sync_jobs），成功/失败计数与错误信息可追溯。

Revision ID: b7d1e4f8c2a9
Revises: f2c8d4e6a1b3
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b7d1e4f8c2a9'
down_revision: Union[str, None] = 'f2c8d4e6a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 app.core.base.JSONType 保持一致：PG 下是 JSONB，其它库退回 JSON。
# 类型不一致会让 autogenerate 每次都报"有变更"，是长期噪声。
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        'wecom_users',
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('wecom_userid', sa.String(length=128), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=True),
        sa.Column('mobile', sa.String(length=32), nullable=True),
        sa.Column('email', sa.String(length=128), nullable=True),
        sa.Column('wecom_department_ids', sa.String(length=255), nullable=True),
        sa.Column('position', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=True),
        sa.Column('sync_status', sa.String(length=16), nullable=False, server_default='synced'),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('raw_data', JSONType, nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_wecom_users_userid', 'wecom_users', ['wecom_userid'], unique=True)
    op.create_index('ix_wecom_users_user', 'wecom_users', ['user_id'], unique=False)

    op.create_table(
        'wecom_external_contacts',
        sa.Column('external_userid', sa.String(length=128), nullable=False),
        sa.Column('crm_contact_id', sa.BigInteger(), nullable=True),
        sa.Column('crm_customer_id', sa.BigInteger(), nullable=True),
        sa.Column('name', sa.String(length=128), nullable=True),
        sa.Column('type', sa.String(length=8), nullable=True),
        sa.Column('avatar', sa.String(length=512), nullable=True),
        sa.Column('corp_name', sa.String(length=200), nullable=True),
        sa.Column('gender', sa.String(length=8), nullable=True),
        sa.Column('normalize_status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('normalized_by', sa.BigInteger(), nullable=True),
        sa.Column('normalized_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('raw_data', JSONType, nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(['crm_contact_id'], ['contacts.id']),
        sa.ForeignKeyConstraint(['crm_customer_id'], ['customers.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_wecom_external_contacts_userid',
        'wecom_external_contacts',
        ['external_userid'],
        unique=True,
    )
    op.create_index(
        'ix_wecom_external_contacts_crm',
        'wecom_external_contacts',
        ['crm_contact_id'],
        unique=False,
    )

    op.create_table(
        'wecom_follow_relationships',
        sa.Column('external_contact_id', sa.BigInteger(), nullable=False),
        sa.Column('wecom_userid', sa.String(length=128), nullable=False),
        sa.Column('add_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('add_way', sa.String(length=32), nullable=True),
        sa.Column('remark', sa.String(length=255), nullable=True),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('state', sa.String(length=128), nullable=True),
        sa.Column('tags_json', JSONType, nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(['external_contact_id'], ['wecom_external_contacts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_wecom_follow_ext_user',
        'wecom_follow_relationships',
        ['external_contact_id', 'wecom_userid'],
        unique=True,
    )
    op.create_index(
        'ix_wecom_follow_userid', 'wecom_follow_relationships', ['wecom_userid'], unique=False
    )

    op.create_table(
        'wecom_sync_jobs',
        sa.Column('job_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='running'),
        sa.Column('operator_id', sa.BigInteger(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('success_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('fail_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('detail', JSONType, nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_wecom_sync_jobs_type', 'wecom_sync_jobs', ['job_type', 'status'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_wecom_sync_jobs_type', table_name='wecom_sync_jobs')
    op.drop_table('wecom_sync_jobs')
    op.drop_index('ix_wecom_follow_userid', table_name='wecom_follow_relationships')
    op.drop_index('ix_wecom_follow_ext_user', table_name='wecom_follow_relationships')
    op.drop_table('wecom_follow_relationships')
    op.drop_index('ix_wecom_external_contacts_crm', table_name='wecom_external_contacts')
    op.drop_index('ix_wecom_external_contacts_userid', table_name='wecom_external_contacts')
    op.drop_table('wecom_external_contacts')
    op.drop_index('ix_wecom_users_user', table_name='wecom_users')
    op.drop_index('ix_wecom_users_userid', table_name='wecom_users')
    op.drop_table('wecom_users')
