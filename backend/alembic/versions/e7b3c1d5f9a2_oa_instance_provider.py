"""OA 实例来源：区分历史钉钉单与自研 OA 单

2026-11-13 起合同审批改走自研 OA，`oa_instances` 同时存两类实例：
老钉钉行只读保留（provider=dingtalk），新合同行 provider=self_oa。
"""

import sqlalchemy as sa
from alembic import op

revision = "e7b3c1d5f9a2"
down_revision = "d4f7a2c9b1e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oa_instances",
        sa.Column(
            "provider",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'dingtalk'"),
        ),
    )
    op.create_index("ix_oa_instances_provider", "oa_instances", ["provider"])


def downgrade() -> None:
    op.drop_index("ix_oa_instances_provider", table_name="oa_instances")
    op.drop_column("oa_instances", "provider")
