"""quote_versions 加 (quote_id, version_no) 唯一约束

并发"再来一版"此前会静默产生两条同号版本（ER §21"版本号必须唯一"
一直没有落库）。约束是最后兜底：撞号的那次请求拿到 409 重试即可。

单号（quote_no）的重号问题已由 number_sequences 原子序列解决，
这里补的是**版本号**这一层。

Revision ID: b3e7a9c1d4f6
Revises: a5c8e2f7b9d1
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3e7a9c1d4f6'
down_revision: Union[str, None] = 'a5c8e2f7b9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    dupes = conn.execute(sa.text(
        "select quote_id, version_no, count(*) from quote_versions "
        "group by quote_id, version_no having count(*) > 1"
    )).all()
    if dupes:
        raise RuntimeError(
            f"quote_versions 存在同号版本，先人工合并后再升级：{dupes}"
        )
    op.create_unique_constraint(
        'uq_quote_versions_quote_version', 'quote_versions',
        ['quote_id', 'version_no'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_quote_versions_quote_version', 'quote_versions', type_='unique')
