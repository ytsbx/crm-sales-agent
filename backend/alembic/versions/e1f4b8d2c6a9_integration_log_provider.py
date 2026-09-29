"""集成日志区分「类型」与「厂商名」（P2 修复）

原来只有一个 `integration_type`：写入存的是厂商名（聚水潭 / ERP/MES），
查询却按 `like('%ERP%')` 匹配 —— **两边靠约定对齐，于是聚水潭的日志一条都查不到**，
而"未知 ERP：xxx"那种桩反而能查到。

拆成两列：

- `integration_type`：稳定口径，查询按它（本次统一为 `erp`）；
- `provider`：厂商名，只用于显示。

历史行按原值回填：老数据里 `integration_type` 存的就是厂商名，直接搬到 `provider`，
类型统一标成 `erp`（这张表此前只有 ERP 在写）。
"""

import sqlalchemy as sa
from alembic import op

revision = "e1f4b8d2c6a9"
down_revision = "d7a1c5e9b3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integration_logs", sa.Column("provider", sa.String(length=64), nullable=True)
    )
    # 老数据的 integration_type 存的是厂商名 → 搬进 provider，类型归一到 erp
    op.execute(
        "update integration_logs set provider = integration_type, integration_type = 'erp' "
        "where integration_type is not null and integration_type <> 'erp'"
    )


def downgrade() -> None:
    # 回退时把厂商名写回类型列，尽量还原成迁移前的样子
    op.execute(
        "update integration_logs set integration_type = provider where provider is not null"
    )
    op.drop_column("integration_logs", "provider")
