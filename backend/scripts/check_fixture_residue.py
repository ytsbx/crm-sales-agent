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
from _test_support import require_isolated_db

require_isolated_db()

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
    # 价格规则与成本：这一格此前不在检查范围里，于是验收套件留下的
    # CHK 前缀规则一直没人发现（本项目的价格维护界面会被它们堆成一片）。
    # 判据用前缀而不是"非有效状态"——历史上刻意留档的停用/历史行是正常业务形态，
    # 只有夹具才不该出现在这里。
    (
        "价格规则",
        "select count(*) from price_rules where remark like 'CHK%'",
        "select remark from price_rules where remark like 'CHK%' limit 3",
    ),
    (
        "成本记录",
        "select count(*) from product_costs where remark like 'CHK%'",
        "select remark from product_costs where remark like 'CHK%' limit 3",
    ),
    # 上面两条的判据是"备注带 CHK 前缀"，但套件未必把前缀写进备注——
    # `check_quote_center_acceptance` 的历史价备注写的是"验收历史行"、等级价甚至
    # 没有备注，于是 196 条规则挂在真实 SKU 上一直没被这条守门抓到。
    # 判据改成"**挂在 CHK 前缀 SKU 上的**规则/成本"：不管备注写什么，
    # 只要 SKU 是夹具，它名下的规则就都该随夹具一起消失。故意不按
    # `skus.deleted_at` 过滤——SKU 软删了、规则却还在，正是要抓的情形。
    (
        "夹具 SKU 上的价格规则",
        "select count(*) from price_rules pr join skus s on s.id = pr.sku_id "
        "where s.sku_code like 'CHK%'",
        "select s.sku_code || '/' || coalesce(nullif(pr.remark, ''), '(无备注)') "
        "from price_rules pr join skus s on s.id = pr.sku_id "
        "where s.sku_code like 'CHK%' limit 3",
    ),
    (
        "夹具 SKU 上的成本",
        "select count(*) from product_costs pc join skus s on s.id = pc.sku_id "
        "where s.sku_code like 'CHK%'",
        "select s.sku_code || '/' || coalesce(nullif(pc.remark, ''), '(无备注)') "
        "from product_costs pc join skus s on s.id = pc.sku_id "
        "where s.sku_code like 'CHK%' limit 3",
    ),
    # 客户合并留痕：这张表**没有外键**（source/target 是裸 id）、也没有 deleted_at，
    # 套件漏清一次就永久留在库里。判据是"引用的客户已经不存在"——真实业务不会物理
    # 删客户（直接删与合并都是软删，行还在、id 仍在），所以孤儿留痕必然是夹具残留。
    # 为什么单列这一条：它自己**不出现在任何列表/统计里**（所以以前一直没被发现），
    # 却会**污染下一轮回归**——新夹具复用同一个号之后，业务逻辑按 id 去捞留痕会捞到
    # 上一轮这条别的客户，据此得出莫名其妙的结论。实测症状：回收站套件的
    # "最终有效客户"被解析成 None，表现为「约 2~4 次全量回归红 1 次、单跑永不复现」。
    (
        "孤儿客户合并留痕",
        "select count(*) from customer_merge_logs m where "
        "not exists (select 1 from customers c where c.id = m.source_customer_id) "
        "or not exists (select 1 from customers c where c.id = m.target_customer_id)",
        "select m.id || '：' || m.source_customer_id || '→' || m.target_customer_id "
        "|| '（' || coalesce(m.merge_snapshot->>'name', '无快照') || '）' "
        "from customer_merge_logs m where "
        "not exists (select 1 from customers c where c.id = m.source_customer_id) "
        "or not exists (select 1 from customers c where c.id = m.target_customer_id) "
        "limit 3",
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
