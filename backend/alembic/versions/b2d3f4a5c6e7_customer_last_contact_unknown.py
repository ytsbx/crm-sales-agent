"""客户「最近有效联系时间未知」标记（第七批 7.5，口径经用户 2026-10-06 确认）。

历史名单导进来的老客户，`created_at` 是导入当天，真实的最后联系时间可能在三年前、
也可能压根没有。原来 `_last_active_at()` 在两个时间都为空时回退建档时间，
于是这批客户进系统后一律"今天刚联系过"——冷落预警与公海回收都要再等一整个周期，
上线第一个月等于没有预警。

用户确认的口径是「**先标记未知，补核后才进自动回收候选**」：
- 导入时未提供联系日期 → `last_contact_unknown = true`，不参与自动回收扫描；
- 之后记录一次真实跟进、或在回收预告页补核联系时间 → 置回 false。

已存在的历史数据（老库）无法判断，**统一置 false**（保持原行为）：
真要说"未知"，得由业务在导入/核对时说，不能由迁移替他们猜。
新导入的行由 `customer/io_router.py` 按文件内容写这个标记。
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d3f4a5c6e7"
down_revision = "a1c2e3f4b5d6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "customers",
        sa.Column(
            "last_contact_unknown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade():
    op.drop_column("customers", "last_contact_unknown")
