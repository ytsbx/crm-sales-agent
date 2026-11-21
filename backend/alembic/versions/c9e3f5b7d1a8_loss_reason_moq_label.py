"""失单原因的「MOQ」改成中文「起订量」

主人 2026-10-10："新建sku列表那里写个moq很多人都不知道啥意思"。
同一个概念项目里本来就有中文说法（价格中心一直叫「起订量」），
失单原因里却挂着 `MOQ` —— 一起统一。

只改 `code = 'moq'` 这一条的**显示名**：
- `code` 是程序用的稳定标识，**必须原样保留**（历史商机的失单原因按 code 引用）；
- 只改 `name`（界面上给人看的那个）。

`seed.py` 里也同步改了（新库直接是中文）；这条迁移负责**已有库**。
`loss_reasons` 有管理界面（`PATCH /loss-reasons/{id}`），所以运维可能手工改过它 ——
这里只动名字恰好还是 `MOQ` 的那条，改过的（比如"起订量太高"）不覆盖。

Revision ID: c9e3f5b7d1a8
Revises: b7d1f4a8c2e5
"""

from alembic import op

revision = "c9e3f5b7d1a8"
down_revision = "b7d1f4a8c2e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE loss_reasons
        SET name = '起订量'
        WHERE code = 'moq' AND name = 'MOQ'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE loss_reasons
        SET name = 'MOQ'
        WHERE code = 'moq' AND name = '起订量'
        """
    )
