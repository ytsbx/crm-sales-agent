"""users 加钉钉身份（钉钉登录的绑定位）。

一张表只加一列，**不建映射表**：绑定的全部内容就是「一个人 ↔ 一个钉钉编号」，
没有企微那种「外部联系人待归一」的中间态，所以不必为它开一张表。

列可空、无默认值：全部存量用户都是 NULL（= 还没绑），这正是我们要的语义 ——
绑定了才有值，没绑定就登录不了，不会因为迁移而让谁突然获得钉钉登录的能力。

不加唯一索引：`users.wecom_userid` 也没有。加唯一索引会让"两个账号填同一个
钉钉号"从一句友好提示变成 500（唯一约束报错冒到接口层），而这条判据在
服务层查一次更合适（见 dingtalk/service.py 的 sync_users）。
"""
import sqlalchemy as sa
from alembic import op

revision = "e1f3a5b7c9d2"
down_revision = "d8a2f6c1b4e9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("dingtalk_userid", sa.String(length=128), nullable=True))


def downgrade():
    op.drop_column("users", "dingtalk_userid")
