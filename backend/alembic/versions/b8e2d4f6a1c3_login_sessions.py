"""登录会话表（第十批 10.12）。

## 这张表解决什么

JWT 是**无状态**的：签名对、没过期就一路放行。于是此前：

- 「登出」只是前端把 token 丢掉，服务端只留一条审计；
- 「改 / 重置密码」只换 `password_hash`，已经发出去的令牌**一张都没作废**。

结果是任何一次凭据泄漏（浏览器缓存、日志、截图里的调试面板）在过期前
（默认 12 小时）都是有效通行证，服务端没有任何可作废的对象。

`login_sessions` 把"一次登录"变成服务端可见、可作废的一行：
令牌里带 `sid` 指向它，鉴权（`core/deps.py`）与续期（`/auth/refresh`）
走**同一个**判断 `load_active_session` —— 只看这一行是不是 `active`。

判据**只在数据库**里（无内存缓存），所以服务重启、多进程部署后
失效结果依然有效。

## 升级时的注意：存量令牌会集体失效

**这是有意的，不是故障。** 本机制上线前签发的令牌里没有 `sid`，
而「没有会话标识的凭据一律拒绝」是刻意的取舍：放行它们等于在
"登出/改密后旧凭据失效"上留一类永远吊销不了的凭据。
代价是升级后所有人**重新登录一次**。

表是新建的，无需回填数据。升级完记得**重启后端**
（本项目后端不开 `--reload`，代码里的新判断不会自己生效）。
"""
import sqlalchemy as sa
from alembic import op

revision = "b8e2d4f6a1c3"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        # 令牌里的 `sid`。用随机串而不是自增 id：自增序号可猜、可枚举，
        # 没有理由把它暴露在令牌里。
        sa.Column("sid", sa.String(length=64), nullable=False),
        # 删除用户时把会话一并带走，不留"指向已删用户"的孤儿行。
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # active / revoked。终态，不恢复。
        sa.Column("status", sa.String(length=32), nullable=False),
        # 为什么被吊销：logout / password_change。排查"人怎么突然掉线了"要看它。
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # 最近一次凭它换到新凭据的时间（登录那一刻也算）。
        # 刻意不做成"每次请求都更新"：那等于给每个读请求加一条写。
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint("uq_login_sessions_sid", "login_sessions", ["sid"])
    op.create_index("ix_login_sessions_user", "login_sessions", ["user_id"])


def downgrade():
    op.drop_index("ix_login_sessions_user", table_name="login_sessions")
    op.drop_constraint("uq_login_sessions_sid", "login_sessions", type_="unique")
    op.drop_table("login_sessions")
