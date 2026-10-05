"""打样审批加轮次（第一批返修 §3.2）。

Revision ID: d2a6c8f4b1e7
Revises: c8f4a2e6b9d3
Create Date: 2026-10-05

打样审批的幂等事件键原来是**只有单号**的固定值：

    event_key=f"sample:approve:{sample.id}"
    event_key=f"sample:resubmit:{sample.id}"

于是"V1 被驳回 → 改资料/原样重提 → 第二轮通过"这条路上，第二轮的
通知、客户时间线留痕会撞上第一轮的键**被去重吞掉**——事后翻记录只看得到一轮，
"驳回过几次、谁批的、每轮批的是哪版资料"全丢了。

加 `review_round`：每次**真的重新回到待审批**（驳回后重提、已批准后改车间依据）
自增一次。事件键带上轮次，每轮各留各的痕；待审批期间反复编辑不会虚增轮次。

再加 `review_request_key`：当前这一轮是**哪次提交**开的。弱网重试带着同一个请求键
回来时按幂等返回（不加轮次、不重复通知），而"待审批的单子没带键又调重提"
仍然按原口径被拦——两者靠请求键区分，不是靠状态。
"""

from alembic import op

revision = "d2a6c8f4b1e7"
down_revision = "c8f4a2e6b9d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史数据一律记第 1 轮：它们确实没有轮次信息，编造一个更早的轮次是伪造事实。
    op.execute(
        "ALTER TABLE sample_requests "
        "ADD COLUMN IF NOT EXISTS review_round INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE sample_requests "
        "ADD COLUMN IF NOT EXISTS review_request_key VARCHAR(64)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sample_requests DROP COLUMN IF EXISTS review_request_key")
    op.execute("ALTER TABLE sample_requests DROP COLUMN IF EXISTS review_round")
