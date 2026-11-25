"""部门增加同序/钉钉部门编号，用于按需对齐部门而不是导入整套组织树。

2026-11-23 主人：只有一部分部门会使用 CRM；新人员要能用同序身份建账号并落到
所在部门。这里给 `departments` 增加可空的 `dingtalk_department_id` 与
`oa_department_id`，已有企微部门保持 NULL；同步时只给真实用到的部门补编号，
避免同名部门被并错。两套编号分开存，钉钉数字编号与同序 UUID 不混用。
"""

from alembic import op
import sqlalchemy as sa

revision = "c3a9d1e7f5b2"
down_revision = "c92b6f8143de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 2026-11-25：生产库存在"有列无版本"的历史漂移——`dingtalk_department_id`
    # 与唯一索引已由早期手工脚本建好，alembic_version 却仍停在 c92，直接
    # add_column 会因"列已存在"整条迁移失败。按当前 schema 逐项判断：
    # 已有的跳过、缺的补上——干净库上的最终结果与原来完全一致。
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("departments")}
    indexes = {index["name"] for index in inspector.get_indexes("departments")}
    if "dingtalk_department_id" not in columns:
        op.add_column(
            "departments",
            sa.Column("dingtalk_department_id", sa.String(length=64), nullable=True),
        )
    if "uq_departments_dingtalk_department_id" not in indexes:
        op.create_index(
            "uq_departments_dingtalk_department_id",
            "departments",
            ["dingtalk_department_id"],
            unique=True,
        )
    if "oa_department_id" not in columns:
        op.add_column(
            "departments",
            sa.Column("oa_department_id", sa.String(length=64), nullable=True),
        )
    if "uq_departments_oa_department_id" not in indexes:
        op.create_index(
            "uq_departments_oa_department_id",
            "departments",
            ["oa_department_id"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("uq_departments_oa_department_id", table_name="departments")
    op.drop_column("departments", "oa_department_id")
    op.drop_index("uq_departments_dingtalk_department_id", table_name="departments")
    op.drop_column("departments", "dingtalk_department_id")
