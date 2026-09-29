"""清理开发库里堆积的回归夹具（维护脚本，不是业务功能）。

## 为什么需要它

`check_*` 系列套件在开发库里造临时客户/商机/产品/SKU/用户，统一带
CHK / CHKQC / CHKDEL / CHKBD 前缀。其中两个套件曾经漏清：

- `check_product_pricing_api`：清理语句少了 `f` 前缀，`{RUN}` 原样进 SQL，
  「删除客户/产品/SKU」三条等于空跑（脚本还报 OK）；
- `check_quote_center_acceptance`：第三个客户只存进局部变量、没登记进清理表。

于是残留越堆越多，实测可见客户里**大多数**是夹具，分析页的客户数、等级分布、
业务员客户数都被它们带着跑。两处漏清已修，本脚本负责把历史存量收干净。

## 为什么软删而不是物理删

`customers` 被 12 张表用外键引用（商机、报价、订单、样品、合同、企微外部联系人…），
物理删除必须按依赖顺序逐张清空，一步顺序错就整批删不掉。软删（`deleted_at`）是
所有读取路径——列表、统计、查重——本来就尊重的口径，**效果等价、可回滚、零外键风险**。
真要释放空间，另做一次依赖顺序的物理清理，不混在这条维护路径里。

## 用法

    cd backend
    PYTHONPATH=. .venv/bin/python scripts/cleanup_fixtures.py          # 干跑：只报告会动什么
    PYTHONPATH=. .venv/bin/python scripts/cleanup_fixtures.py --apply   # 真的执行

只按前缀匹配，绝不整表清理；干跑会打印样例名称，便于先人眼确认没有误伤。
"""

import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import text

from app.core.database import SessionLocal

#: (说明, 受影响的表, 候选条件)
TARGETS = [
    ("客户", "customers", "name like 'CHK%'"),
    ("商机", "opportunities", "title like '%CHK%'"),
    ("产品", "products", "name like 'CHK%'"),
    ("SKU", "skus", "sku_code like 'CHK%'"),
]

#: 用户没有 deleted_at，改用停用（同样从"业务员表现"这类只统计在岗的视图里消失）
USER_CONDITION = "username like 'chk%'"


async def main(apply: bool) -> int:
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        mode = "执行" if apply else "干跑"
        print(f"=== 回归夹具清理（{mode}）===")
        total = 0
        for label, table, condition in TARGETS:
            pending = (
                await s.execute(
                    text(f"select count(*) from {table} where deleted_at is null and {condition}")
                )
            ).scalar_one()
            samples = [
                row[0]
                for row in (
                    await s.execute(
                        text(
                            f"select {_name_column(table)} from {table} "
                            f"where deleted_at is null and {condition} limit 3"
                        )
                    )
                ).all()
            ]
            print(f"  {label:<4} 待处理 {pending:>4} 条" + (f"，样例：{samples}" if samples else ""))
            total += int(pending)
            if apply and pending:
                await s.execute(
                    text(f"update {table} set deleted_at = :now where deleted_at is null and {condition}"),
                    {"now": now},
                )

        active_users = (
            await s.execute(
                text(f"select count(*) from users where status = 'active' and {USER_CONDITION}")
            )
        ).scalar_one()
        print(f"  用户 待停用 {active_users:>4} 条")
        total += int(active_users)
        if apply and active_users:
            await s.execute(
                text(f"update users set status = 'disabled' where status = 'active' and {USER_CONDITION}")
            )

        if apply:
            await s.commit()
            print(f"\n已处理 {total} 条（软删/停用，可回滚）")
        else:
            print(f"\n干跑结束：共 {total} 条待处理。加 --apply 真正执行。")
    return 0


def _name_column(table: str) -> str:
    return {
        "customers": "name",
        "opportunities": "title",
        "products": "name",
        "skus": "sku_code",
    }[table]


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--apply" in sys.argv)))
