"""材质 / 工艺 / 图纸版本从打样单头挪到明细行。

背景：这三项原先挂在 `sample_requests`（单头），但一张打样单可以有多个商品——
两个盒子可能一个用瓦楞纸、一个用 PP 中空板，走不同工艺、各自按自己的图纸版本。
挂在单头就只能写一份，车间照着做会做错（除非把差异写进备注，但那不是结构化字段，
出图时看不出来）。

口径由业务方确认为「挪到明细行，每个商品各存一套」。

数据迁移分两步，顺序不能反：
1. 先把单头已有的值**下移到该单的每一行明细**——这是把"整单共用"这个旧事实
   按原样摊到每行，历史单据出图看到的内容与迁移前完全一致；
2. 再删单头三列。
明细行原本没有这三列，所以下移只是"填充空白"，不会覆盖任何已有数据。
"""
from alembic import op
import sqlalchemy as sa

revision = "a2c6e8f4b1d7"
down_revision = "f4d8b2e6a1c9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sample_items", sa.Column("craft", sa.String(length=128), nullable=True))
    op.add_column("sample_items", sa.Column("material", sa.String(length=128), nullable=True))
    op.add_column("sample_items", sa.Column("drawing_version", sa.String(length=64), nullable=True))

    # 单头的值下移到每一行明细（旧口径"整单共用" → 每行都写上同一份值）
    op.execute(
        """
        UPDATE sample_items AS i
           SET craft = r.craft,
               material = r.material,
               drawing_version = r.drawing_version
          FROM sample_requests AS r
         WHERE i.sample_request_id = r.id
           AND (r.craft IS NOT NULL
                OR r.material IS NOT NULL
                OR r.drawing_version IS NOT NULL)
        """
    )

    op.drop_column("sample_requests", "craft")
    op.drop_column("sample_requests", "material")
    op.drop_column("sample_requests", "drawing_version")


def downgrade():
    op.add_column("sample_requests", sa.Column("craft", sa.String(length=128), nullable=True))
    op.add_column("sample_requests", sa.Column("material", sa.String(length=128), nullable=True))
    op.add_column("sample_requests", sa.Column("drawing_version", sa.String(length=64), nullable=True))

    # 信息有损：明细行可能各不相同，单头只放得下一份，取该单**第一行**的值。
    # 这是回滚的固有限制，不是可以绕过去的实现细节——回滚前请先确认业务能接受。
    op.execute(
        """
        UPDATE sample_requests AS r
           SET craft = i.craft,
               material = i.material,
               drawing_version = i.drawing_version
          FROM (
                SELECT DISTINCT ON (sample_request_id)
                       sample_request_id, craft, material, drawing_version
                  FROM sample_items
                 ORDER BY sample_request_id, id
               ) AS i
         WHERE i.sample_request_id = r.id
        """
    )

    op.drop_column("sample_items", "craft")
    op.drop_column("sample_items", "material")
    op.drop_column("sample_items", "drawing_version")
