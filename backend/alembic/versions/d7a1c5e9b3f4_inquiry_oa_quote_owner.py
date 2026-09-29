"""定制询价的"对接报价员"（文档 §11.3 :152 / 场景11）

钉钉「产品询价申请」模板里「对接报价员」是必填的联系人控件、只能从指定的人里选，
所以 CRM 侧也要有这一格：销售录需求时顺便选，发起审批时自动带过去。

存**钉钉的人的编号**（钉钉认编号不认姓名），另存一份姓名用于显示——
只存编号的话列表就得回查钉钉才能显示"给了谁"。
"""

import sqlalchemy as sa
from alembic import op

revision = "d7a1c5e9b3f4"
down_revision = "c4f8a2e6d1b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_inquiries", sa.Column("oa_quote_user_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "custom_inquiries", sa.Column("oa_quote_user_name", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("custom_inquiries", "oa_quote_user_name")
    op.drop_column("custom_inquiries", "oa_quote_user_id")
