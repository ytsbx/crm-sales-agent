"""打样"制作完成"改成结构化事件（第一批返修 §3.4）。

Revision ID: c8f4a2e6b9d3
Revises: b6e2a8c4d1f7
Create Date: 2026-10-05

原来"这次制作说明是不是已经写过"靠**备注文本的子串匹配**：

    note_already_present = bool(note) and note in (sample.remark or "")

`remark` 是一段越拼越长的自由文本，于是：
- 新说明只要恰好是**旧说明的子串**（例如先是"已制作完成，等待寄出"，后来只写
  "已制作"），就被判成"已经写过"——**这条新说明被吞掉**，界面还回"没有变化"；
- 反过来，文本里出现同样的字眼也会误判，幂等判断完全是碰运气；
- 备注是给人看的展示字段，却同时承担了幂等判定。

改成结构化事件：`sample_requests.made_events` 存一串
`{key, at, note, by, recorded_at}`，**幂等只看 key**；`remark` 退回纯展示。

只加一列、可空，历史数据不受影响（旧的 `made_at` / `remark` 原样保留，
`made_events` 为空表示"这条单子还没有结构化事件"）。
"""

from alembic import op

revision = "c8f4a2e6b9d3"
down_revision = "b6e2a8c4d1f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSONB 而不是 JSON：与项目里其它 JSON 列一致（JSONB 才能建 GIN、也避免
    # 迁移执行顺序把类型打回 JSON）。
    op.execute(
        "ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS made_events JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sample_requests DROP COLUMN IF EXISTS made_events")
