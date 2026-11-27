"""客户跟进人表：客户被硬删时连带清理跟进关系

软删客户不触发这里；只有回收站/测试夹具真的物理删客户时才会级联。
跟进关系是客户的从属数据，不能反过来把客户删不掉；产品没有"物理删客户"
入口，所以这不改变任何业务流程。

Revision ID: b3e7c1a9d5f2
Revises: fbe435eab96a
Create Date: 2026-11-27 11:45:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3e7c1a9d5f2"
down_revision: Union[str, None] = "fbe435eab96a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "customer_followers_customer_id_fkey", "customer_followers", type_="foreignkey"
    )
    op.create_foreign_key(
        "customer_followers_customer_id_fkey",
        "customer_followers",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "customer_followers_customer_id_fkey", "customer_followers", type_="foreignkey"
    )
    op.create_foreign_key(
        "customer_followers_customer_id_fkey",
        "customer_followers",
        "customers",
        ["customer_id"],
        ["id"],
    )
