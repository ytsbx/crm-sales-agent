"""外部映射的唯一性（第八批 8.11）。

模型 `ExternalMapping.__table_args__` 已经声明了这两条唯一约束，但数据库里一直
没有 —— 缺约束的后果是**两个方向都能造出矛盾状态**：

- 同一 CRM 对象在一个外部系统里有两行映射 → "这张单推没推过"取决于读了哪一行，
  推送幂等直接漏掉，同一张单会被推两次；
- 同一个外部单号落到两个 CRM 对象上 → 回调按外部单号定位会命中两单，
  `apply_status_webhook` 可能把状态改到错误的订单上。

## 建约束前先查重复

老代码（`order/service.record_external_order_id`）会**无条件插入**，重复关联是
真实可能的。这里不自动去重：保留哪一行、要不要合并外部号，是业务判断。
检测到重复就报错退出并打印冲突行，人工清理后再升级。

`external_id IS NULL` 的行**不冲突**（PostgreSQL 的唯一约束对 NULL 不比较）：
"还没拿到外部单号"是合法状态。
"""
from alembic import op
import sqlalchemy as sa

revision = "f7b8c9d0e1a2"
down_revision = "e6a7b8c9d0e1"
branch_labels = None
depends_on = None

DUP_INTERNAL_SQL = """
select system_type, business_type, internal_id, count(*) as n,
       string_agg(id::text, ',' order by id) as row_ids
from external_mappings
group by system_type, business_type, internal_id
having count(*) > 1
"""

DUP_EXTERNAL_SQL = """
select system_type, business_type, external_id, count(*) as n,
       string_agg(id::text, ',' order by id) as row_ids
from external_mappings
where external_id is not null
group by system_type, business_type, external_id
having count(*) > 1
"""


def _assert_no_duplicates(connection, *, sql: str, label: str) -> None:
    rows = connection.execute(sa.text(sql)).fetchall()
    if not rows:
        return
    detail = "; ".join(" / ".join(str(value) for value in row) for row in rows[:20])
    raise RuntimeError(
        f"external_mappings 的{label}存在重复，无法建立唯一约束（第八批 8.11）："
        f"{detail}。请人工确认保留哪一行后清理，再重新执行迁移。排查语句：{sql}"
    )


def upgrade():
    connection = op.get_bind()
    _assert_no_duplicates(
        connection, sql=DUP_INTERNAL_SQL, label="「同系统 + 同业务类型 + 同内部对象」"
    )
    _assert_no_duplicates(
        connection, sql=DUP_EXTERNAL_SQL, label="「同系统 + 同业务类型 + 同外部编号」"
    )

    op.create_unique_constraint(
        "uq_external_mappings_internal",
        "external_mappings",
        ["system_type", "business_type", "internal_id"],
    )
    op.create_unique_constraint(
        "uq_external_mappings_external",
        "external_mappings",
        ["system_type", "business_type", "external_id"],
    )


def downgrade():
    op.drop_constraint(
        "uq_external_mappings_external", "external_mappings", type_="unique"
    )
    op.drop_constraint(
        "uq_external_mappings_internal", "external_mappings", type_="unique"
    )
