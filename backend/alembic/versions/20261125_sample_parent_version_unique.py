"""打样修订版唯一：同一父单只能有一个下一版（B22-07）。

双击/弱网重试开修订版时，先查子版本再插入之间没有约束兜底，会出两份 V2。
这里补 `(parent_id, version)` 唯一约束；`parent_id` 为 NULL 的根单不受影响
（PostgreSQL 不对 NULL 做唯一比较）。

生产库上线前已核对：`parent_id is not null` 的行数为 0，无重复数据。
"""

from alembic import op

revision = "c4f8a1d6e2b9"
down_revision = "b7d3f9a2c4e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_sample_requests_parent_version",
        "sample_requests",
        ["parent_id", "version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_sample_requests_parent_version", "sample_requests", type_="unique"
    )
