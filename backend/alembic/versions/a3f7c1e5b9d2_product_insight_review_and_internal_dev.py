"""新品洞察：评审轮次与内容冻结 + 转换落地为「内部开发需求」（第五批 §6.1）。

Revision ID: a3f7c1e5b9d2
Revises: b2e6f8a4c0d3
Create Date: 2026-10-06

三件事收在这一处：

1. **`product_insights.review_round`**：审批轮次。原来"已通过"之后还能原地改
   方向/卖点/客户/结论，状态仍是"已通过"——批准的根本不是同一份内容。
   改成"改了关键内容就退回待评审、轮次加一"，与打样（§3.2）同一套做法。
   事件键带上轮次，否则第二轮的通知/留痕会撞上第一轮的固定键被去重吞掉。
   `review_request_key` 用于弱网重试的幂等（不带键时行为不变）。

2. **`custom_inquiries.origin`**：这条需求从哪来。默认 `customer`
   （客户提出的询价），`internal_dev` = 内部开发需求（洞察评审通过、
   但还没有具体客户时转出来的）。**必须有个显式字段**：靠"客户为空"去猜
   是不可靠的——业务上本来就有"客户还没定、先把需求记下来"的正常单据，
   两者权限口径完全不同（见 apply_scope 的改动）。

3. **`custom_inquiries.source_insight_id`**：来源洞察回链。带**部分唯一索引**
   （只在未软删时唯一）：一个洞察只能转出一条需求，并发双击时由数据库兜住，
   不靠应用层"先查后建"（那个有时间窗，两下都查不到就会各建一条）。
"""

from alembic import op

revision = "a3f7c1e5b9d2"
down_revision = "b2e6f8a4c0d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- ① 洞察：评审轮次 ----
    op.execute(
        "ALTER TABLE product_insights "
        "ADD COLUMN IF NOT EXISTS review_round INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE product_insights "
        "ADD COLUMN IF NOT EXISTS review_request_key VARCHAR(64)"
    )

    # ---- ② 需求：来源类型 + 来源洞察回链 ----
    # 存量一律 customer：它们都是客户询价那条路建的（内部开发需求是本批才有的概念）。
    op.execute(
        "ALTER TABLE custom_inquiries "
        "ADD COLUMN IF NOT EXISTS origin VARCHAR(16) NOT NULL DEFAULT 'customer'"
    )
    op.execute(
        "ALTER TABLE custom_inquiries "
        "ADD COLUMN IF NOT EXISTS source_insight_id BIGINT"
    )
    # 部分唯一索引：只约束"活着的"、且确实来自洞察的那些行。
    # NULL 不参与唯一（一堆客户询价都是 NULL，不能互相顶掉）。
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_custom_inquiries_source_insight "
        "ON custom_inquiries (source_insight_id) "
        "WHERE source_insight_id IS NOT NULL AND deleted_at IS NULL"
    )
    # 内部开发需求要能按来源筛出来（列表/权限判定都会查它）
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_custom_inquiries_origin "
        "ON custom_inquiries (origin)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_custom_inquiries_origin")
    op.execute("DROP INDEX IF EXISTS uq_custom_inquiries_source_insight")
    op.execute("ALTER TABLE custom_inquiries DROP COLUMN IF EXISTS source_insight_id")
    op.execute("ALTER TABLE custom_inquiries DROP COLUMN IF EXISTS origin")
    op.execute("ALTER TABLE product_insights DROP COLUMN IF EXISTS review_request_key")
    op.execute("ALTER TABLE product_insights DROP COLUMN IF EXISTS review_round")
