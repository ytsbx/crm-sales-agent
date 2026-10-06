"""OA 结果未知的原子核定状态（第七批 7.8）。

## 为什么这些列是 7.8 的命门

`resolve_state` / `resolve_request_key` / `resolve_claimed_at` 承载"**这次核定被谁占住了**"。
没有数据库层的占用状态时，两个并发的核定请求会各自读到 `needs_review`、
各自通过进程内的检查，然后**各建一个外部审批实例** —— 外部系统里出现两张单，
CRM 这边只认一张，另一张成为隐形挂单（`service.py` 里那段注释描述的就是这个后果）。
进程锁挡不住多实例部署，所以占用必须是数据库里的状态 + 条件更新（CAS）。

两个**部分唯一索引**分别守住两件事：
- `uq_oa_instance_instance_id`：一个外部实例只能关联一行 —— 否则"采纳哪个实例"
  会命中两行，回调改状态也会改错；
- `uq_oa_instance_resolve_key`：同一把核定请求键只能落一行 —— 同键回放同结果。

## 建索引前先查重复

两个索引都可能被**历史脏数据**挡住（老代码没有约束、也没有占用状态）。
这里刻意**不自动清理**：把哪一行算"正确的关联"是业务判断，
迁移替业务做决定等于篡改历史。检测到重复就报错退出，并把冲突行打出来，
人工确认清理后再升级。查询语句见报错信息里的 SQL。
"""
from alembic import op
import sqlalchemy as sa

revision = "e6a7b8c9d0e1"
down_revision = "d4f5a6b7c8e9"
branch_labels = None
depends_on = None

#: 重复检查：建唯一索引前必须先为空。查询与报错文案共用同一段 SQL，
#: 免得"报错让你去查"但给的语句和实际判断不一致。
DUP_INSTANCE_SQL = """
select instance_id, count(*) as n, string_agg(id::text, ',' order by id) as row_ids
from oa_instances
where instance_id is not null
group by instance_id
having count(*) > 1
"""


def _assert_no_duplicates(connection) -> None:
    rows = connection.execute(sa.text(DUP_INSTANCE_SQL)).fetchall()
    if not rows:
        return
    detail = "; ".join(
        f"instance_id={row[0]}({row[1]} 行: {row[2]})" for row in rows[:20]
    )
    raise RuntimeError(
        "oa_instances.instance_id 存在重复，无法建立唯一约束（第七批 7.8）："
        f"{detail}。请人工确认每一行该保留哪个实例后清理，再重新执行迁移。"
        "排查语句：" + DUP_INSTANCE_SQL
    )


def upgrade():
    connection = op.get_bind()
    _assert_no_duplicates(connection)

    op.add_column(
        "oa_instances",
        sa.Column(
            "resolve_state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'idle'"),
        ),
    )
    op.add_column(
        "oa_instances",
        sa.Column("resolve_request_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "oa_instances",
        sa.Column("resolve_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "oa_instances",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "oa_instances",
        sa.Column("resolved_action", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "oa_instances",
        sa.Column(
            "attempt_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # 部分唯一索引：NULL 表示"还没有实例 / 还没占用"，不参与唯一性
    op.execute(
        "CREATE UNIQUE INDEX uq_oa_instance_instance_id "
        "ON oa_instances(instance_id) WHERE instance_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_oa_instance_resolve_key "
        "ON oa_instances(resolve_request_key) WHERE resolve_request_key IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_oa_instance_resolve_key")
    op.execute("DROP INDEX IF EXISTS uq_oa_instance_instance_id")
    for column in (
        "attempt_count",
        "resolved_action",
        "resolved_at",
        "resolve_claimed_at",
        "resolve_request_key",
        "resolve_state",
    ):
        op.drop_column("oa_instances", column)
