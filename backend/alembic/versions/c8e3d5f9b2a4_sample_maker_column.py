"""补齐打样单的「打样人」列（`sample_maker`）。

Revision ID: c8e3d5f9b2a4
Revises: b7f2c4e8a1d3
Create Date: 2026-10-16

## 为什么单独一条

上一个提交（`b7f2c4e8a1d3`）给「交样时间」加了列，**同时**在 schema、序列化、
生成器里加了 `sample_maker` —— **但忘了给模型加列**。
后果：`GET /api/v1/samples` 一查就 500
（`AttributeError: 'SampleRequest' object has no attribute 'sample_maker'`），
**打样列表整个打不开**（线上 13:42 连续报错）。

这条把列补上。**同一批改动里"加了字段却漏了模型列"是我第二次犯**
（上一次是 `extra="ignore"` 静默丢字段）——
所以模型、schema、序列化、迁移这四处必须一起改，缺一处就是线上 500。
"""
from alembic import op

revision = "c8e3d5f9b2a4"
down_revision = "b7f2c4e8a1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS sample_maker VARCHAR(64)")


def downgrade() -> None:
    op.execute("ALTER TABLE sample_requests DROP COLUMN IF EXISTS sample_maker")
