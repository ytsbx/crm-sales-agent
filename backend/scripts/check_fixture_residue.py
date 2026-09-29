"""守门回归：跑完全部套件后，库里不该有**可见**的夹具残留。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_fixture_residue.py

## 为什么要有这条

三个套件曾经"报 OK 但漏清"，而且都不报错、只是安静地把数据留在库里：

- `check_product_pricing_api`：清理语句少了 `f` 前缀 → 客户/产品/SKU 三条等于空跑；
- `check_quote_center_acceptance`：第三个客户没登记进清理表；
- `check_reference_integrity`：建了商机但清理清单里没有商机。

它们是**攒着**的：每跑一次全量回归就再堆一批，最后可见客户里大多数是夹具，
分析页的客户数、等级分布、业务员客户数全被带着跑。单看每个套件的自检是发现不了的
——所以这条守门放在清单最后，专门盯"跨套件的整体性"。

判据是前缀 + "对业务可见"（`deleted_at is null` / 在岗）：
软删掉的夹具不打扰业务，不算残留；能被列表和统计捞到的才算。

存量如果已经脏了，用 `scripts/cleanup_fixtures.py` 收干净（它默认干跑）。
"""

import asyncio
import sys

from sqlalchemy import text

from app.core.database import SessionLocal

#: (说明, 计数 SQL, 一行样例行 SQL)
CHECKS: list[tuple[str, str, str]] = [
    (
        "客户",
        "select count(*) from customers where deleted_at is null and name like 'CHK%'",
        "select name from customers where deleted_at is null and name like 'CHK%' limit 3",
    ),
    (
        "商机",
        "select count(*) from opportunities where deleted_at is null "
        "and (title like 'CHK%' or title like 'REFCHK%')",
        "select title from opportunities where deleted_at is null "
        "and (title like 'CHK%' or title like 'REFCHK%') limit 3",
    ),
    (
        "产品",
        "select count(*) from products where deleted_at is null and name like 'CHK%'",
        "select name from products where deleted_at is null and name like 'CHK%' limit 3",
    ),
    (
        "SKU",
        "select count(*) from skus where deleted_at is null and sku_code like 'CHK%'",
        "select sku_code from skus where deleted_at is null and sku_code like 'CHK%' limit 3",
    ),
    (
        "用户",
        "select count(*) from users where status = 'active' and username like 'chk%'",
        "select username from users where status = 'active' and username like 'chk%' limit 3",
    ),
    (
        "任务",
        "select count(*) from tasks where title like 'REFCHK%'",
        "select title from tasks where title like 'REFCHK%' limit 3",
    ),
]


async def main() -> int:
    failures: list[str] = []
    async with SessionLocal() as s:
        for label, count_sql, sample_sql in CHECKS:
            count = (await s.execute(text(count_sql))).scalar_one()
            if count:
                samples = [row[0] for row in (await s.execute(text(sample_sql))).all()]
                print(f"  FAIL {label} 残留 {count} 条：{samples}")
                failures.append(label)
            else:
                print(f"  OK   {label} 无残留")

    print()
    if failures:
        print(
            "有套件没清干净："
            + "、".join(failures)
            + "。先看对应 scripts/check_*.py 的清理语句（常见原因：清理清单漏了表、"
            "SQL 少了 f 前缀导致 {RUN} 没被替换），"
            "存量用 scripts/cleanup_fixtures.py 收干净（默认干跑）。"
        )
        return 1
    print("夹具残留检查 通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
