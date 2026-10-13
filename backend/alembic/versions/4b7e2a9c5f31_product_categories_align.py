"""产品分类字典与示例产品对齐

## 为什么要这一版（2026-10-13）

产品分类在 2026-10-12 之后成了**受控字典**：新建/编辑产品都会校验
"值必须在启用的字典项里"（`settings.ensure_dictionary_value`）。

但字典里的产品分类是当初拍脑袋预置的三个占位值
（`turnover_box / pallet / cold_chain`），而 `scripts/seed.py` 的示例产品
与业务实际用的是另一套中文分类（`物流器具 / 包装箱 / 缠绕膜`）。
两套词表谁也不挨着，后果是：

- 示例产品一进编辑页，**下拉里没有当前值**（显示为空）；
- 直接在详情页点保存，会被后端 400 拒掉 —— 用户看到的是
  "我什么都没改，怎么就存不上"。

口径（主人 2026-10-13 确认）：**业务字段里的中文就是值**，
和客户等级 `A/B/C/D` 一致；字典 `code` 直接用中文，
不再另造英文码 —— 否则界面上显示的和库里存的是两套东西，
导数据、对账、写 SQL 都要多翻译一层。

## 这版做什么

1. 补上示例产品真正使用的三个分类（中文 code）；
2. 三个**未被任何产品引用**的英文占位分类直接**删除** ——
   已确认 `crm_sales_agent` / `crm_prod` 里都没有产品用过它们
   （产品分类当时是空的），留着只会让分类下拉多三条无意义选项；
   若某个库里真有产品在用（理论上不会有），这里**不删**，只把它
   改成停用，宁可留一条也不制造"产品指向不存在的分类"。

Revision ID: 4b7e2a9c5f31
Revises: 8c6d39b1a7c8
Create Date: 2026-10-13

"""
from typing import Sequence, Union

from alembic import op

revision: str = '4b7e2a9c5f31'
down_revision: Union[str, None] = '8c6d39b1a7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 示例产品与业务实际在用的分类（中文即值）
REAL_CATEGORIES = ["物流器具", "包装箱", "缠绕膜"]

#: 迁移 `2d6f9b4c8e15` 预置的英文占位分类（没有任何业务含义）
PLACEHOLDER_CATEGORIES = ["turnover_box", "pallet", "cold_chain"]


def upgrade() -> None:
    for index, code in enumerate(REAL_CATEGORIES, start=1):
        op.execute(
            f"""
            insert into dictionary_items (type, code, label, sort_no, enabled, remark)
            values ('product_category', '{code}', '{code}', {index}, true,
                    '示例产品在用的分类；中文即值（与客户等级 A/B/C/D 同一口径）')
            on conflict (type, code) do nothing
            """
        )

    # 占位分类：没有产品引用就删掉；有引用（不应出现）就只停用，不制造悬空引用
    for code in PLACEHOLDER_CATEGORIES:
        op.execute(
            f"""
            delete from dictionary_items
             where type = 'product_category' and code = '{code}'
               and not exists (
                   select 1 from products p
                    where p.deleted_at is null and p.category = '{code}'
               )
            """
        )
        op.execute(
            f"""
            update dictionary_items
               set enabled = false,
                   remark = '占位分类，已有产品引用因而保留停用；新业务请用中文分类'
             where type = 'product_category' and code = '{code}'
            """
        )


def downgrade() -> None:
    # 只回滚"补进来的中文分类"。占位分类的删除**不还原**——
    # 它们是误配、没有业务含义，重建回去只会把同一个坑再挖一遍。
    for code in REAL_CATEGORIES:
        op.execute(
            f"delete from dictionary_items where type = 'product_category' and code = '{code}'"
        )
