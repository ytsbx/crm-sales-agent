"""业务文件：来源身份键 + 原件字节存档（第八批 8.9 / 8.10）。

## 8.10：为什么必须有 `file_id` / `file_sha256`

原来 BizDoc 只存 `input_snapshot` 与**内容** hash，下载时**重新渲染**。
内容 hash 不等于文件字节 hash：升级渲染器/字体/库版本之后，同一个单号下载到的
字节可能已经变了 —— 而对外单据的承诺是"同一编号永远同一份"。
所以正式生成时把**实际产出的 xlsx/pdf** 存成文件，并记下它的**字节** SHA-256；
下载原件读存档，不重新渲染；要出新内容就走新版本。

历史行留 NULL = "当时没有存档"，下载时明确标"由历史快照重建"，
**不回填**当前渲染结果 —— 那等于伪造历史原件。

## 8.9：`source_key` 与部分唯一索引

`source_key` 是"这份文件属于哪张来源单据"的稳定身份（`sample_request:12`、
`quote:7`…）。`(doc_type, source_key, version)` 上的唯一索引是**并发防线**：
两个请求同时读到"最新版是 3"、同时插 4，唯一索引会让其中一个失败，
而不是安静地产生两条 V4。

用**部分**唯一索引（`WHERE source_key IS NOT NULL`）是刻意的：
- 历史行无法归属来源 → `source_key` 为 NULL，它们之间不该互相判成冲突；
- PostgreSQL 里 NULL 彼此不相等，全量唯一索引本来就挡不住它们，
  写成部分索引只是把这件事说明白。

回填按来源外键推导（在**建索引之前**跑）。回填后若撞约束（并发双击时代码
没有任何约束，完全可能已有重复链），迁移**报错退出并列出冲突行**，
不自动删改：保留哪一份是业务判断，迁移替业务决定等于篡改对外台账。
"""
from alembic import op
import sqlalchemy as sa

revision = "a8b9c0d1e2f3"
down_revision = "f7b8c9d0e1a2"
branch_labels = None
depends_on = None

BACKFILL_SQL = """
UPDATE biz_docs SET source_key = CASE
  WHEN doc_type = 'sample_request' AND sample_request_id IS NOT NULL
    THEN 'sample_request:' || sample_request_id
  WHEN doc_type = 'quote_sheet' AND quote_id IS NOT NULL
    THEN 'quote:' || quote_id
  WHEN doc_type = 'order_sheet' AND order_draft_id IS NOT NULL
    THEN 'order_draft:' || order_draft_id
  WHEN doc_type = 'order_sheet' AND order_id IS NOT NULL
    THEN 'order:' || order_id
  ELSE NULL END
WHERE source_key IS NULL
"""

DUP_SQL = """
SELECT doc_type, source_key, version, count(*) AS n,
       string_agg(id::text, ',' ORDER BY id) AS ids
FROM biz_docs
WHERE source_key IS NOT NULL
GROUP BY doc_type, source_key, version
HAVING count(*) > 1
"""


def _assert_no_duplicate_source_version(connection) -> None:
    rows = connection.execute(sa.text(DUP_SQL)).fetchall()
    if not rows:
        return
    detail = "; ".join(
        f"{row[0]} / {row[1]} / V{row[2]} → {row[3]} 行(id={row[4]})" for row in rows[:20]
    )
    raise RuntimeError(
        "biz_docs 存在「同类型 + 同来源 + 同版本」的重复行，无法建立唯一索引"
        f"（第八批 8.9）：{detail}。请人工确认保留哪一份（其余行可把 source_key 置空"
        "并标注为历史副本）后重新执行迁移。排查语句：" + DUP_SQL
    )


def upgrade():
    op.add_column("biz_docs", sa.Column("source_key", sa.String(length=64), nullable=True))
    op.add_column("biz_docs", sa.Column("file_id", sa.BigInteger(), nullable=True))
    op.add_column("biz_docs", sa.Column("file_sha256", sa.String(length=64), nullable=True))
    op.add_column("biz_docs", sa.Column("file_size", sa.BigInteger(), nullable=True))
    op.add_column(
        "biz_docs", sa.Column("renderer_version", sa.String(length=32), nullable=True)
    )

    connection = op.get_bind()
    connection.execute(sa.text(BACKFILL_SQL))
    _assert_no_duplicate_source_version(connection)

    op.execute(
        "CREATE UNIQUE INDEX uq_biz_docs_source_version "
        "ON biz_docs (doc_type, source_key, version) WHERE source_key IS NOT NULL"
    )
    op.create_index("ix_biz_docs_source_key", "biz_docs", ["source_key"])


def downgrade():
    op.drop_index("ix_biz_docs_source_key", table_name="biz_docs")
    op.execute("DROP INDEX IF EXISTS uq_biz_docs_source_version")
    for column in ("renderer_version", "file_size", "file_sha256", "file_id", "source_key"):
        op.drop_column("biz_docs", column)
