"""合同模板加 (类型, 名称, 版本) 唯一约束。

背景：模板是"一行一个版本"，版本号是永久标识——历史合同钉死"模板 v2"，
就应该永远只有一份正文。但此前库上没有任何约束，两个管理员同时新建同名模板时
会双双算出 v2 并各插一行，于是"模板 v2"有两个真相，已生成的文件到底按哪份
正文出的也说不清（见审查第 8 条）。

升级前会先把**历史重复行**的版本号往后挪（保留 id 最小的那行不动），
否则约束加不上。只动 version 这一列，`contract_documents.template_id` 指向的是
行 id、不是版本号，所以引用关系不受影响。
"""
from alembic import op

revision = "b5d1f7c3e9a2"
down_revision = "a2c6e8f4b1d7"
branch_labels = None
depends_on = None


def upgrade():
    # 先消掉历史重复，再上约束；顺序反了会因为存量数据直接失败
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY doc_type, name, version ORDER BY id
                   ) AS rn,
                   MAX(version) OVER (PARTITION BY doc_type, name) AS max_v
              FROM contract_templates
        )
        UPDATE contract_templates AS t
           SET version = r.max_v + r.rn
          FROM ranked AS r
         WHERE t.id = r.id
           AND r.rn > 1
        """
    )
    op.create_unique_constraint(
        "uq_contract_template_version",
        "contract_templates",
        ["doc_type", "name", "version"],
    )


def downgrade():
    op.drop_constraint(
        "uq_contract_template_version", "contract_templates", type_="unique"
    )
    # 不回滚上面的重编号：版本号已经被引用/展示过，往回改比留着更危险
