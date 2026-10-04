"""定制询价：加「已被新版取代」标记 + 同链条版本号唯一

口径 2026-10-04（"询价历史版"大改）：

1. 加 `superseded_at`：修订生成新版时，旧版打上"已被新版取代"。历史版本永不覆盖，
   但要能一眼看出哪版是旧的。不新增 status 取值——取代是**版本属性**，业务状态
   （待评估/已转商机…）是另一回事，混在一起会把"已转商机"这类信息覆盖掉。
2. 加 `(coalesce(root_id, id), version)` 唯一索引：ROOT 的 root_id 为空，
   所以用 coalesce 把"首版"也纳入唯一性。此前在 v1 上连点两次会生成两条 v2，
   历史视图里同版并列、分不清哪条才是当前要求。
"""

import sqlalchemy as sa
from alembic import op

revision = "d2a7b9c4e6f8"
down_revision = "c1f6a8d3e5b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_inquiries",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "create unique index uq_custom_inquiries_chain_version "
        "on custom_inquiries (coalesce(root_id, id), version)"
    )


def downgrade() -> None:
    op.execute("drop index uq_custom_inquiries_chain_version")
    op.drop_column("custom_inquiries", "superseded_at")
