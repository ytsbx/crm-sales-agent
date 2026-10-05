"""打样单加修订版与修订关系（第一批返修 §3.3，口径已确认 A）。

Revision ID: e4b8d2f6a1c9
Revises: d2a6c8f4b1e7
Create Date: 2026-10-05

原来「已制作」之后还能**原地改**材质/工艺/图纸/尺寸：改完看不出这是第几版，
旧的制作时间留在同一行上，和已经做出来、甚至已经寄走的那个实物对不上；
图纸文件与当时的材料工艺也没有可核对的快照。

已确认口径（2026-10-05，方案 A）：**开新修订版**——原单出 V2，旧版冻结只读。

- `version`：本单是第几版（历史数据都是第 1 版）。
- `parent_id`：指向被它取代的那一版；为空表示这是首版。

"冻结"不额外加列：**有子版本即视为冻结**（`exists(child.parent_id = self.id)`），
避免再维护一个可能与事实不一致的状态位。V2 不会继承 V1 的制作/寄送/签收/客户确认
事实——那些是 **V1 身上的既成事实**，新版本还没做出来。
"""

from alembic import op

revision = "e4b8d2f6a1c9"
down_revision = "d2a6c8f4b1e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史数据一律记第 1 版、无父版本：它们确实没有版本链信息。
    op.execute(
        "ALTER TABLE sample_requests "
        "ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE sample_requests "
        "ADD COLUMN IF NOT EXISTS parent_id BIGINT REFERENCES sample_requests(id)"
    )
    # 按父版本找子版本（冻结判断要查"有没有子版本"），给它一个索引
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sample_requests_parent "
        "ON sample_requests (parent_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sample_requests_parent")
    op.execute("ALTER TABLE sample_requests DROP COLUMN IF EXISTS parent_id")
    op.execute("ALTER TABLE sample_requests DROP COLUMN IF EXISTS version")
