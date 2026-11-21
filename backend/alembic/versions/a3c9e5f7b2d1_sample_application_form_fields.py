"""打样申请单字段：CRM 补上钉钉「定制打样审批」那份 Excel 上的格。

Revision ID: a3c9e5f7b2d1
Revises: d8a2f6c1b4e9
Create Date: 2026-10-15

## 为什么加这些列

钉钉上那份《打样申请单》是**公司现有表单**（表头写着"表单编码：D01-003"），
格式不能改。而 CRM 的打样单原来缺它上面一大半的格，于是业务员每次都要
**手打一遍 Excel** 再去钉钉上传。

补上这些列之后，CRM 能**一键生成填好的申请单**（见 `sample/application_form.py`），
业务员下载即传 —— 这是本次改动的**唯一目的**。

## 列的来源：逐格对照实物文件

物品文件（主人提供）：`打样申请单，打样物料需求单，物料信息单模板，下载后使用.xlsx`
第一张表「打样申请单」A1:M21，其中要填的格与列对应关系：

    C2 打样类型            → sample_type
    G2 产品名称            → product_name
    I2 打样数量            → 取明细数量合计（不存列）
    K2 交样时间            → target_completion_date（已有）
    C3 打样费用            → sample_fee（已有）
    G3 申请人 / I3 打样部门 / K3 打样人 → 申请人与其部门（不存列，导出时算）
    C4 打样目的            → sample_purpose
    G4 紧急程度            → urgency
    C5 样品情况            → sample_situation
    B8 主要材质和规格      → material_spec
    B9 产品尺寸            → product_size
    B10 产品印刷           → printing
    B11 主要辅料           → accessories
    B12 包装要求           → packaging
    B13 做法要求           → craft_requirement

## 两个刻意的设计（别顺手改）

1. **存了 `estimated_hours` 又存 `craft_points`**：积分 = 工时 × 10 是派生值，
   但只存积分就答不出"这 30 分怎么来的"，将来口径变了也没法重算。
   两个都留，口径由代码算、不让人手填积分（避免两个人填出两个数）。

2. **6 个"要求"列与明细上的 `material` / `craft` 并存，不合并**：
   明细那两项是**逐行**的短值（一单两个盒子可能不同），是报价/生产用的结构化数据；
   这 6 列是**申请单上整段的文字要求**（要落进 Excel 的 B8~B13），面向车间阅读。
   合并会丢信息：一格装不下一单多样，一段文字也当不了逐行依据。

## 存量数据

全部**可空、无默认值** —— 老单据这几项就是"没填过"，
不设默认值、不回填，避免把"未填"伪装成"填了空字符串"。
"""
from alembic import op

revision = "a3c9e5f7b2d1"
# ⚠️ 接在**当前主干 head** 之后，不要改回 `d8a2f6c1b4e9`：
# 两条线并行时各自加了迁移，`d8a2f6c1b4e9` 已被 `c35e9a2b7d14`
# （独立财务会签权限）接走 —— 沿用旧的 down_revision 会让 alembic 出现
# **两个 head**，`alembic upgrade head` 直接报
# "Multiple head revisions are present"，部署时才发现就晚了。
# 本迁移只给 `sample_requests` 加列，与其它迁移无先后依赖。
down_revision = "c35e9a2b7d14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 与钉钉模板一一对应的四项（含两个数值列）
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS sample_type VARCHAR(32)")
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS product_category VARCHAR(32)")
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS maker_unit VARCHAR(32)")
    op.execute(
        "ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS estimated_hours NUMERIC(8, 2)"
    )
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS craft_points NUMERIC(12, 2)")

    # 申请单上的三个勾选项（存中文原文，便于与钉钉单据肉眼对账）
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS sample_purpose VARCHAR(32)")
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS urgency VARCHAR(16)")
    op.execute(
        "ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS sample_situation VARCHAR(32)"
    )

    # 「打样具体要求」那 6 格（整段文字）
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS material_spec TEXT")
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS product_size TEXT")
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS printing TEXT")
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS accessories TEXT")
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS packaging TEXT")
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS craft_requirement TEXT")

    # 申请单上「产品名称」只有一格，而明细可以一行多样 —— 显式存一份，导出时不猜
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS product_name VARCHAR(200)")


def downgrade() -> None:
    for col in (
        "product_name",
        "craft_requirement",
        "packaging",
        "accessories",
        "printing",
        "product_size",
        "material_spec",
        "sample_situation",
        "urgency",
        "sample_purpose",
        "craft_points",
        "estimated_hours",
        "maker_unit",
        "product_category",
        "sample_type",
    ):
        op.execute(f"ALTER TABLE sample_requests DROP COLUMN IF EXISTS {col}")
