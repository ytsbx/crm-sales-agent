"""SKU 成本四项改为可空：「未提供」与「明确为 0」必须分得开（第七批 7.4）。

背景：这四列原来是 NOT NULL + server_default 0，导入把空白当 0，
于是"只填了 SKU 和生效日"的模板行会造出一条四项全零的成本；
而核价是靠"有没有生效成本行"判断成本已知的，结果就是**假毛利**。

改可空之后：
- NULL = 未提供（核价提示成本不完整，不按零成本算利润看着正常）；
- 0 = 明确为零（客户供料这类真实场景照常表达）；
- 四项全空的新建行在导入与页面两条入口都已被拒绝落库。

历史数据**不动**：原来存 0 的仍是 0。这批"四项全零"的行无法自动分辨是
哪种来源，只通过 `scripts/list_zero_cost_rows.py` 出核对清单，由业务确认后
再逐条改，不在这里批量删改（那等于篡改历史事实）。
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c2e3f4b5d6"
down_revision = "e1b4d8a2c6f0"
branch_labels = None
depends_on = None

COLUMNS = ("purchase_cost", "production_cost", "package_cost", "processing_cost")


def upgrade():
    for column in COLUMNS:
        op.alter_column(
            "product_costs",
            column,
            existing_type=sa.Numeric(14, 4),
            nullable=True,
            server_default=None,
        )


def downgrade():
    # 回滚前先把 NULL 兜成 0，否则收紧 NOT NULL 会直接失败；
    # 这一步会**丢失**"未提供"的信息，所以只在确实要回退到旧版本时用。
    for column in COLUMNS:
        op.execute(f"UPDATE product_costs SET {column} = 0 WHERE {column} IS NULL")
        op.alter_column(
            "product_costs",
            column,
            existing_type=sa.Numeric(14, 4),
            nullable=False,
            server_default="0",
        )
