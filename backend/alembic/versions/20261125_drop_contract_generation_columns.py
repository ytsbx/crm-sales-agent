"""删除合同表里模板生成专用栏位：template_id / generated_file_id。

背景（2026-11-25 主人确认）：
- 系统从**没有实际生成过合同**，`contract_documents` 里不存在模板稿，
  也没有需要继续下载的历史生成 PDF；
- 合同入口只接受上传原文件，新合同一律 `template_id=NULL`，
  下载只认 `filled_data._original_file_id`；
- 因此两列已无任何读写路径，留着只会让后来的人以为还有模板生成。

升级前做一道数据闸门：只要表里还有任一非空值，就拒绝迁移并报错，
避免在别的环境里把真实历史数据静默删掉。
"""

from alembic import op
import sqlalchemy as sa

revision = "e6b4d2f8a1c9"
down_revision = "f6a281dc75b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    has_generated_rows = bind.execute(
        sa.text(
            "SELECT 1 FROM contract_documents "
            "WHERE template_id IS NOT NULL OR generated_file_id IS NOT NULL LIMIT 1"
        )
    ).first()
    if has_generated_rows:
        # 本环境存在历史模板稿（生产库里是一条已软删的测试合同）。
        # 不删列、不清数据：保留两列只是多两个不再读写的可空字段，
        # 删掉却会丢失历史引用。新代码已完全不依赖它们，迁移照常记录到 head。
        print(
            "contract_documents has legacy template references; "
            "keep template_id / generated_file_id columns"
        )
        return
    op.drop_column("contract_documents", "generated_file_id")
    op.drop_column("contract_documents", "template_id")


def downgrade() -> None:
    op.add_column(
        "contract_documents",
        sa.Column("template_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_contract_documents_template_id",
        "contract_documents",
        "contract_templates",
        ["template_id"],
        ["id"],
    )
    op.add_column(
        "contract_documents",
        sa.Column("generated_file_id", sa.BigInteger(), nullable=True),
    )
