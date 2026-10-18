"""产品分类字典退役：清掉 `type='product_category'` 的字典项。

Revision ID: c2f5a8d1e4b7
Revises: b4d1f8a2c6e3
Create Date: 2026-10-15

## 背景（人话版）

「产品分类」以前是**受控字典**：产品页的分类只能从「系统设置 → 数据字典」
里预先配好的几项里挑（当时预置的是「物流器具 / 包装箱 / 缠绕膜」）。

2026-10-15 主人确认：产品分类改成**在产品页直接填写**（最多 64 字，
后端不再校验"必须命中字典成员"）。也就是说，字典里那三条**已经没有任何
消费方**：产品表单不再读它，代码里也没有第二处按 `product_category` 查字典
（打样单里那个同名字段是它自己的硬编码选项，跟这张表无关）。

主人当天的原话是「彻底清掉」—— 留下这几条，只会让人在数据字典页误以为
产品分类还归这里管，配了也不生效。

## 这版做什么

把 `dictionary_items` 里 `type='product_category'` 的记录清干净：

- **没有产品引用它的** → 直接删；
- **万一有产品在用**（理论上不会；本机两库 + 线上都已确认没有）→ 只把它
  `enabled` 改成 false，**不删**。宁可留一条停用的，也不制造
  "产品指向一个不存在的分类"这种悬空引用。

写法照 `4b7e2a9c5f31_product_categories_align.py` —— 当时清理英文占位分类
用的就是这套"能删就删、有人用就停用"的口径。

## 为什么不写死那三个 code

不把「物流器具 / 包装箱 / 缠绕膜」硬编码进来，而是**先查出这个库里实际
存在哪些 `product_category` 记录，再逐条处理**。原因是各环境历史不一样：
线上那三条早就被停用了（`enabled=false`），本机两个库还是启用状态；
动态查询一次覆盖所有情况，也顺带收拾掉可能残留的英文占位分类
（`turnover_box / pallet / cold_chain`）。

## 存量数据

- 本机 `crm_sales_agent` / `crm_prod`：3 条，均无产品引用 → 全删。
- 线上容器库：3 条（已停用），均无产品引用 → 全删。
"""
from alembic import op

revision = "c2f5a8d1e4b7"
# 接在样品软删那条迁移之后（当前主干唯一的 head）。本迁移只动字典表的数据，
# 与 `sample_requests` 的改动没有先后依赖，纯粹排它后面保持单链。
down_revision = "b4d1f8a2c6e3"
branch_labels = None
depends_on = None

#: 回滚时补回的分类（就是当前库里实际存在的那三个；中文即值，与旧口径一致）
RETIRED_CATEGORIES = ["物流器具", "包装箱", "缠绕膜"]


def upgrade() -> None:
    # 用集合 SQL 表达原先逐条查询/删除/停用的逻辑，既保留在线升级语义，
    # 又能让 Alembic 离线模式生成可执行脚本（离线模式没有可迭代的查询结果）。
    op.execute(
        "UPDATE dictionary_items "
        "SET enabled = false, "
        "    remark = '产品分类已改为产品页直接填写，此字典项退役停用' "
        "WHERE type = 'product_category' "
        "  AND EXISTS ("
        "    SELECT 1 FROM products p "
        "    WHERE p.deleted_at IS NULL AND p.category = dictionary_items.code"
        "  )"
    )
    op.execute(
        "DELETE FROM dictionary_items "
        "WHERE type = 'product_category' "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM products p "
        "    WHERE p.deleted_at IS NULL AND p.category = dictionary_items.code"
        "  )"
    )


def downgrade() -> None:
    # 回滚 = 整套改动一起退（前端下拉也会把「产品分类」加回来），
    # 所以这里把三个分类补回去，`on conflict do nothing` 保证幂等。
    # 补不回来的是各环境原先的启停状态（线上原本就是停用的）—— 这里统一
    # 按启用补，尽量贴近"产品分类还是受控字典"那套旧行为。
    for index, code in enumerate(RETIRED_CATEGORIES, start=1):
        op.execute(
            f"""
            insert into dictionary_items (type, code, label, sort_no, enabled, remark)
            values ('product_category', '{code}', '{code}', {index}, true,
                    '产品分类字典（退役前的口径；中文即值）')
            on conflict (type, code) do nothing
            """
        )
