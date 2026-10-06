"""外部只读采集/对账 与 SKU 主数据权威（第八批 8.13 / 8.14）：9 张全新表。

## 为什么这张迁移用模型对象建表，而不是手写 9 段 DDL

9 张表、上百个列与十来条唯一约束，手抄一遍必然有抄错的地方，而抄错的表现是
"某一列长度不对/唯一约束漏了"——那种错只在真库上、只在特定数据下才炸
（本轮已经踩过一次：`period` 写成 16 长度，SQLite 不校验 varchar，PostgreSQL 直接
`value too long for type character varying(16)`）。

这里改为直接引用模型里已经声明好的 `Table`：列的库内类型、可空性、长度与
唯一约束**只有一处定义**（模型），迁移与模型不可能对不上。
这个做法只适用于**全新表**：没有历史数据要改、没有回填要算，
所以"迁移读当前模型"不会造成"模型漂移导致迁移不可复现"的问题。
（有数据的表仍然必须手写 DDL —— 见本目录里其它几条迁移。）

真实外部系统的端点、签名、字段名一律没有猜：这些表存的是**采集到的原始事实、
水位、映射、差异**，接不通时状态如实落 `not_configured` / `not_verified`。
"""
from alembic import op

from app.modules.integration.model import (
    ExternalObjectMapping,
    ExternalRecord,
    ExternalSourceRegistry,
    ExternalSyncWatermark,
    IntegrationDiff,
    ReconciliationRun,
)
from app.modules.product.model import (
    SkuFieldAuthority,
    SkuIdentitySource,
    SkuMasterVersion,
)

revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None

#: 建表顺序：只依赖已存在的表（skus），彼此之间没有外键，所以顺序只为可读性
TABLES = [
    ExternalSyncWatermark.__table__,
    ExternalRecord.__table__,
    ExternalObjectMapping.__table__,
    ExternalSourceRegistry.__table__,
    ReconciliationRun.__table__,
    IntegrationDiff.__table__,
    SkuIdentitySource.__table__,
    SkuFieldAuthority.__table__,
    SkuMasterVersion.__table__,
]


def upgrade():
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind, checkfirst=False)


def downgrade():
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind, checkfirst=False)
