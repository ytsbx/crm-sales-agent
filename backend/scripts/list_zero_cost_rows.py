#!/usr/bin/env python
"""列出"四项成本全为零/未填"的历史成本行，供业务核对（第七批 7.4）。

## 为什么要这个脚本，而不是直接删/改

导入曾经把空白成本当 0，于是库里躺着一批"四项全零"的成本行。
核价是靠"有没有生效成本行"判断成本已知的，这批行会让核价算出一个看着正常的
**假毛利**。但是：

- 四项全零既可能是"导入时根本没填"，也可能是"客户供料、成本真的为零"；
- 两者在库里长得一模一样，**没有任何字段能区分**。

所以这里只出核对清单，不自动删改 —— 擅自把历史行删掉或清零，
等于替业务篡改历史事实。核对结论出来后，由业务在价格中心逐条改，
或者明确指示后再写数据修正脚本。

## 跑法（**只读**，可以放心对任何库跑）

    cd backend
    DATABASE_URL=postgresql+asyncpg://crm:...@127.0.0.1:5433/crm_sales_agent \\
      PYTHONPATH=. .venv/bin/python scripts/list_zero_cost_rows.py [--csv out.csv]

`--csv` 会把清单写成 CSV（带 BOM，Excel 直接打开）；不传则打印到屏幕。
"""

import argparse
import asyncio
import csv
import os
import sys
from datetime import UTC, datetime

import app.main  # noqa: F401  保证所有模型都注册进 metadata
from sqlalchemy import or_, select

from app.core.database import SessionLocal
from app.modules.pricing.model import ProductCost
from app.modules.product.model import Sku

#: 四个成本列；全为 0 或 NULL 的行才算"信息为零"
COST_COLUMNS = ("purchase_cost", "production_cost", "package_cost", "processing_cost")
HEADERS = [
    "成本行ID", "SKU编码", "SKU名称", "采购成本", "生产成本", "包装成本",
    "加工成本", "币种", "生效起", "生效止", "备注", "创建时间",
]


def _fmt(value) -> str:
    if value is None:
        return "未填"
    return str(value)


async def main(export_path: str | None) -> int:
    target = os.environ.get("DATABASE_URL") or "(来自 backend/.env)"
    print(f"目标库：{target}")
    print("说明：只读查询，不做任何写入。\n")

    async with SessionLocal() as session:
        stmt = (
            select(ProductCost, Sku.sku_code, Sku.name)
            .join(Sku, Sku.id == ProductCost.sku_id, isouter=True)
            .where(
                or_(
                    *[
                        getattr(ProductCost, column).is_(None)
                        for column in COST_COLUMNS
                    ],
                    *[
                        getattr(ProductCost, column) == 0
                        for column in COST_COLUMNS
                    ],
                )
            )
            .order_by(ProductCost.sku_id, ProductCost.effective_from)
        )
        rows = (await session.execute(stmt)).all()

    suspicious = []
    for cost, sku_code, sku_name in rows:
        values = [getattr(cost, column) for column in COST_COLUMNS]
        # 只要有一项非零，这条成本就不是"信息为零"，不用核对
        if any(value not in (None, 0) for value in values):
            continue
        suspicious.append(
            [
                cost.id,
                sku_code or f"(SKU {cost.sku_id} 已删除)",
                sku_name or "",
                _fmt(cost.purchase_cost),
                _fmt(cost.production_cost),
                _fmt(cost.package_cost),
                _fmt(cost.processing_cost),
                cost.currency,
                cost.effective_from.isoformat() if cost.effective_from else "",
                cost.effective_to.isoformat() if cost.effective_to else "",
                cost.remark or "",
                cost.created_at.isoformat() if cost.created_at else "",
            ]
        )

    print(f"四项成本全为 0 / 未填的成本行：{len(suspicious)} 条")
    print("（这些行会让核价按「已知的零成本」计算，请逐条确认是「未填」还是「确实为零」）\n")
    for row in suspicious[:200]:
        print(f"  #{row[0]}  {row[1]}  {row[2]}  生效 {row[8]}  {row[10]}")
    if len(suspicious) > 200:
        print(f"  …（还有 {len(suspicious) - 200} 条，用 --csv 导出完整清单）")

    if export_path:
        with open(export_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["导出时间", datetime.now(UTC).isoformat()])
            writer.writerow(HEADERS)
            writer.writerows(suspicious)
        print(f"\n完整清单已写入：{export_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="列出四项成本全为 0/未填的历史成本行（只读）")
    parser.add_argument("--csv", dest="csv_path", default=None, help="把完整清单导出为该 CSV 文件")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.csv_path)))
