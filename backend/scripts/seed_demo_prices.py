"""演示价格：自带一条**独立演示产品线**，不碰现有 SKU（默认干跑）。

第一版把等级价加在现有活跃 SKU 上，当场打挂了 `check_quote_center_acceptance`
——那些 SKU 正是验收套件造夹具的地方，**演示数据与测试夹具抢同一块地盘**。
现在自带产品线与 SKU，现有定价一个字不动。

标识沿用 seed.py 的约定：`演示占位价，待业务确认后替换`；撤销按 remark + 编码前缀
定位（不需要另记 id）。演示 SKU 一旦被单据引用，物理删会撞外键，所以撤销走软删。

    PYTHONPATH=. .venv/bin/python scripts/seed_demo_prices.py            # 干跑
    PYTHONPATH=. .venv/bin/python scripts/seed_demo_prices.py --apply     # 写入
    PYTHONPATH=. .venv/bin/python scripts/seed_demo_prices.py --clean     # 撤销整条演示线

定价口径（**编的，业务确认后替换**）：以基准指导价为锚，等级按折扣拉开
A −8%（大客户）、B −4%、C −1%、D +3%（新客/散单）。不用等差——真实生意里等级差
通常这么走，等差在演示"同 SKU 对不同等级不同价"时反而看不出谁是谁。
"""

import asyncio
import sys
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text

from app.core.database import SessionLocal

MARK = "演示占位价，待业务确认后替换"
CODE_PREFIX = "DEMO-"
PRODUCT_NAME = "演示产品线·包装耗材"
LEVEL_FACTOR = {"A": "0.92", "B": "0.96", "C": "0.99", "D": "1.03"}
#: 编码后缀, 名称, 规格, 材质, 采购成本, 包装成本, 基准指导价
SKUS = [
    ("BX01", "五层瓦楞纸箱", "600×400×300mm", "瓦楞纸", "18.00", "3.00", "29.00"),
    ("BX02", "三层瓦楞纸箱", "400×300×200mm", "瓦楞纸", "11.00", "2.00", "19.00"),
    ("DZ01", "PE 气泡袋", "300×400mm", "PE", "6.50", "1.00", "14.50"),
    ("MZ01", "木质托盘", "1200×1000mm", "松木", "55.00", "5.00", "85.00"),
]
_CODES = [f"{CODE_PREFIX}{row[0]}" for row in SKUS]
_START = date.today().replace(month=1, day=1)


async def _clean(s) -> None:
    params = {"m": MARK, "codes": _CODES}
    rules = await s.execute(
        text(
            "delete from price_rules where remark = :m and sku_id in "
            "(select id from skus where sku_code = any(:codes))"
        ),
        params,
    )
    costs = await s.execute(
        text(
            "delete from product_costs where remark = :m and sku_id in "
            "(select id from skus where sku_code = any(:codes))"
        ),
        params,
    )
    skus = await s.execute(
        text(
            "update skus set deleted_at = now() where sku_code = any(:codes) "
            "and deleted_at is null"
        ),
        {"codes": _CODES},
    )
    product = await s.execute(
        text("update products set deleted_at = now() where name = :n and deleted_at is null"),
        {"n": PRODUCT_NAME},
    )
    await s.commit()
    print(
        f"已撤销：规则 {rules.rowcount} 条、成本 {costs.rowcount} 条、"
        f"SKU {skus.rowcount} 个、产品 {product.rowcount} 个"
    )


async def main(mode: str) -> int:
    from app.modules.pricing.model import PriceRule, ProductCost
    from app.modules.product.model import Product, Sku

    async with SessionLocal() as s:
        if mode == "clean":
            await _clean(s)
            return 0

        print(
            f"计划：产品 1 个（{PRODUCT_NAME}）、SKU {len(SKUS)} 个、"
            f"成本 {len(SKUS)} 条、价格规则 {len(SKUS) * 5} 条（1 基准 + A/B/C/D）"
        )
        for suffix, name, spec, _material, purchase, package, guide in SKUS:
            print(f"  {CODE_PREFIX}{suffix} {name} {spec}：成本 {purchase}+{package}，指导价 {guide}")
        if mode != "apply":
            print("\n干跑结束。加 --apply 写入；--clean 撤销整条演示线。")
            return 0

        product = (
            await s.execute(select(Product).where(Product.name == PRODUCT_NAME))
        ).scalars().first()
        if product is None:
            product = Product(
                name=PRODUCT_NAME,
                product_line="演示用",
                category="包装耗材",
                brand="演示品牌",
                description="演示价格与报价功能用的占位产品线，正式上线前替换",
                status="active",
            )
            s.add(product)
            await s.flush()
        product.deleted_at = None

        for suffix, name, spec, material, purchase, package, guide in SKUS:
            code = f"{CODE_PREFIX}{suffix}"
            sku = (await s.execute(select(Sku).where(Sku.sku_code == code))).scalars().first()
            if sku is None:
                sku = Sku(
                    product_id=product.id,
                    sku_code=code,
                    name=name,
                    specification=spec,
                    material=material,
                )
                s.add(sku)
                await s.flush()
            sku.deleted_at = None
            s.add(
                ProductCost(
                    sku_id=sku.id,
                    purchase_cost=Decimal(purchase),
                    package_cost=Decimal(package),
                    effective_from=_START,
                    currency="CNY",
                    remark=MARK,
                )
            )
            base = Decimal(guide)
            for level, factor in [(None, "1")] + list(LEVEL_FACTOR.items()):
                price = (base * Decimal(factor)).quantize(Decimal("0.0001"))
                s.add(
                    PriceRule(
                        sku_id=sku.id,
                        customer_level=level,
                        min_qty=Decimal(0),
                        standard_price=(base * Decimal("1.12")).quantize(Decimal("0.0001")),
                        guide_price=price,
                        minimum_price=(price * Decimal("0.85")).quantize(Decimal("0.0001")),
                        effective_from=_START,
                        status="active",
                        remark=MARK,
                    )
                )
        await s.commit()
        print(f"\n已写入：{len(SKUS)} 个 SKU、{len(SKUS)} 条成本、{len(SKUS) * 5} 条规则（标识：{MARK}）")
    return 0


if __name__ == "__main__":
    flag = sys.argv[1] if len(sys.argv) > 1 else ""
    asyncio.run(main("apply" if flag == "--apply" else "clean" if flag == "--clean" else "dry"))
