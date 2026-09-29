"""演示价格数据：给每个有指导价的 SKU 补 A/B/C/D 等级价（默认干跑）。

## 为什么单独一个脚本，而不是塞进 seed.py

- `seed.py` 是"从零搭环境"的入口，往里加东西风险大；这里是可重复执行的**数据填充**；
- 演示数据必须**可识别、可一键撤销**：上线前要把它们整批换成真实价格，
  不能留下"来源不明的数字"（成本错一条，低价审批和毛利就全错）。

标识沿用 seed.py 已有的约定：`演示占位价，待业务确认后替换`。
撤销就按这个 remark 删，不需要另记 id。

## 用法

    cd backend
    PYTHONPATH=. .venv/bin/python scripts/seed_demo_prices.py           # 干跑：只报告会写什么
    PYTHONPATH=. .venv/bin/python scripts/seed_demo_prices.py --apply    # 真的写入
    PYTHONPATH=. .venv/bin/python scripts/seed_demo_prices.py --clean    # 撤销（只删本脚本造的等级价）

## 定价口径（**编的，业务确认后替换**）

以每条现成规则的 `guide_price` 为基准，等级按折扣拉开：
A 级 -8%（大客户）、B 级 -4%、C 级 -1%、D 级 +3%（新客/散单）。
梯度不是等差是因为真实生意里等级差通常这么走；演示"同一 SKU 对不同等级出不同价"
要能一眼看出差异，等差反而看不出谁是谁。
"""

import asyncio
import sys
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text

from app.core.database import SessionLocal

MARK = "演示占位价，待业务确认后替换"
#: 等级 → 相对指导价的系数
LEVEL_FACTOR = {"A": Decimal("0.92"), "B": Decimal("0.96"), "C": Decimal("0.99"), "D": Decimal("1.03")}


async def main(mode: str) -> int:
    from app.modules.pricing.model import PriceRule
    from app.modules.product.model import Sku

    async with SessionLocal() as s:
        live_skus = set(
            (
                await s.execute(select(Sku.id).where(Sku.deleted_at.is_(None)))
            ).scalars().all()
        )
        base_rules = (
            await s.execute(
                select(PriceRule).where(
                    PriceRule.status == "active",
                    PriceRule.customer_level.is_(None),
                    # 只取"通用基准价"（min_qty=0）。阶梯价（3000 以上 27）也是
                    # customer_level 为空的非有效行……不，它是 active 的，所以必须
                    # 用 min_qty=0 把它排除掉：否则同一个 SKU 会生成两条 min_qty=0
                    # 的等级价，直接构成"区间重叠"——定价引擎会拒绝，演示也跑不通。
                    PriceRule.min_qty == 0,
                    PriceRule.sku_id.in_(live_skus),
                    PriceRule.guide_price.is_not(None),
                )
            )
        ).scalars().all()
        # 已有等级价（本脚本造的）——按 remark + 等级识别，避免误删业务自己维护的
        existing = {
            (row.sku_id, row.customer_level)
            for row in (
                await s.execute(
                    select(PriceRule).where(
                        PriceRule.remark == MARK, PriceRule.customer_level.is_not(None)
                    )
                )
            ).scalars().all()
        }

        if mode == "clean":
            result = await s.execute(
                text(
                    "delete from price_rules where remark = :m and customer_level is not null"
                ),
                {"m": MARK},
            )
            await s.commit()
            print(f"已撤销演示等级价 {result.rowcount} 条")
            return 0

        plan = []
        seen_skus: set[int] = set()
        for rule in base_rules:
            if rule.sku_id in seen_skus:
                # 一个 SKU 只按一条基准价推等级价，宁可少造也不要造出重叠
                continue
            seen_skus.add(rule.sku_id)
            guide = Decimal(str(rule.guide_price))
            for level, factor in LEVEL_FACTOR.items():
                if (rule.sku_id, level) in existing:
                    continue
                price = (guide * factor).quantize(Decimal("0.0001"))
                minimum = (price * Decimal("0.85")).quantize(Decimal("0.0001"))
                plan.append(
                    {
                        "sku_id": rule.sku_id,
                        "level": level,
                        "guide": price,
                        "minimum": minimum,
                        "standard": (guide * Decimal("1.1")).quantize(Decimal("0.0001")),
                        "margin": rule.target_margin,
                    }
                )

        print(f"基准规则 {len(base_rules)} 条 → 计划写 {len(plan)} 条等级价")
        for row in plan[:6]:
            print(
                f"  sku {row['sku_id']} {row['level']} 级：指导价 {row['guide']}"
                f"（保底 {row['minimum']}）"
            )
        if len(plan) > 6:
            print(f"  …另有 {len(plan) - 6} 条")

        if mode != "apply":
            print("\n干跑结束。加 --apply 真正写入；--clean 撤销。")
            return 0

        for row in plan:
            s.add(
                PriceRule(
                    sku_id=row["sku_id"],
                    customer_level=row["level"],
                    min_qty=Decimal(0),
                    standard_price=row["standard"],
                    guide_price=row["guide"],
                    minimum_price=row["minimum"],
                    target_margin=row["margin"],
                    effective_from=date.today().replace(month=1, day=1),
                    status="active",
                    remark=MARK,
                )
            )
        await s.commit()
        print(f"\n已写入 {len(plan)} 条演示等级价（标识：{MARK}）")
    return 0


if __name__ == "__main__":
    flag = sys.argv[1] if len(sys.argv) > 1 else ""
    asyncio.run(main("apply" if flag == "--apply" else "clean" if flag == "--clean" else "dry"))
