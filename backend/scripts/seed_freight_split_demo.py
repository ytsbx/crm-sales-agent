"""运费分离演示数据：货款 / 运费 / 其他费用 / 优惠 四项拆开看得见。

## 这是给谁看的

「产品价格与运费分离」上线后，需要一个**能照着走的样例**回答业务最常问的两句：

1. "运费改了，产品价会不会跟着变？" —— 不会。样例里两张报价单，产品、数量、
   单价完全相同，只有运费一个是 300、一个是 500；产品利润与利润率一模一样，
   应付合计差 200。
2. "运费在单子上长什么样？" —— 报价页/对客 Excel/PDF/订单页四处都是
   **货款 → 运费（代收代付）→ 其他费用 → 优惠 → 应付合计**，运费单列具体金额。

样例还带一条**明确的零运费**报价（客户自提），用来演示"空着没填"与
"确认是零运费"在系统里是两种状态 —— 前者正式发送会被拦，后者可以直接发。

## 口径（2026-10-09 与主人确认）

- 产品成本 80/件，数量 100，产品单价 100 → 货款 10,000、产品利润 2,000、利润率 20%；
- 运费由客户全额承担、公司**原额代收代付**（不赚不赔），所以它不进产品利润；
- 其他费用（税费）50 单独一列，不被算进运费。

## 与现有数据的关系

用**独立的产品线**（`DEMO2-FRT01`）与独立的客户名，
不碰 `seed.py` / `seed_demo_prices.py` 造的任何 SKU 与定价。
标识统一带 `运费分离演示` 前缀，撤销按它定位，不需要另记 id。

    PYTHONPATH=. .venv/bin/python scripts/seed_freight_split_demo.py            # 干跑
    PYTHONPATH=. .venv/bin/python scripts/seed_freight_split_demo.py --apply    # 写入
    PYTHONPATH=. .venv/bin/python scripts/seed_freight_split_demo.py --clean    # 撤销

⚠️ 报价单一旦被引用（转订单等）就走软删；本脚本的样例只到"草稿/已发送"为止，
`--clean` 物理删除自己造的那几张。
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.pricing.model import PriceRule, ProductCost
from app.modules.product.model import Product, Sku
from app.modules.quote.model import Quote, QuoteCharge, QuoteItem, QuoteVersion
from app.modules.user.model import User

#: 一律带这个标识，便于"只删自己造的"
MARK = "运费分离演示"
SKU_CODE = "DEMO2-FRT01"
PRODUCT_NAME = "演示产品线·运费分离样例"
CUSTOMER_NAME = "示例客户·运费分离演示"
OPP_TITLE_PREFIX = "运费分离演示"
QUOTE_REMARK = f"{MARK}：产品价格与运费分离样例"

#: 样例参数（元）：产品成本 80、数量 100、产品单价 100
UNIT_COST = Decimal("80.00")
QUANTITY = Decimal("100")
UNIT_PRICE = Decimal("100.00")
#: 三张报价单的运费与说明
CASES = [
    ("运费 300 的常规报价", Decimal("300.00"), "宁波到苏州运费", Decimal("0.00")),
    ("同一产品、运费改成 500", Decimal("500.00"), "宁波到广州运费", Decimal("0.00")),
    ("客户自提（明确零运费）", Decimal("0.00"), "客户自提，无运费", Decimal("50.00")),
]


async def _clean(session) -> None:
    """撤销整条演示线：先删引用方，再删被引用方（外键是 NO ACTION）。"""
    quote_ids = (
        await session.execute(select(Quote.id).where(Quote.quote_no.like("DEMO2Q%")))
    ).scalars().all()
    if quote_ids:
        version_ids = (
            await session.execute(
                select(QuoteVersion.id).where(QuoteVersion.quote_id.in_(quote_ids))
            )
        ).scalars().all()
        if version_ids:
            await session.execute(
                delete(QuoteCharge).where(QuoteCharge.quote_version_id.in_(version_ids))
            )
            await session.execute(
                delete(QuoteItem).where(QuoteItem.quote_version_id.in_(version_ids))
            )
            await session.execute(
                delete(QuoteVersion).where(QuoteVersion.id.in_(version_ids))
            )
        await session.execute(delete(Quote).where(Quote.id.in_(quote_ids)))
    opp_ids = (
        await session.execute(
            select(Opportunity.id).where(Opportunity.title.like(f"{OPP_TITLE_PREFIX}%"))
        )
    ).scalars().all()
    if opp_ids:
        await session.execute(delete(Opportunity).where(Opportunity.id.in_(opp_ids)))
    sku_ids = (
        await session.execute(select(Sku.id).where(Sku.sku_code == SKU_CODE))
    ).scalars().all()
    if sku_ids:
        await session.execute(delete(ProductCost).where(ProductCost.sku_id.in_(sku_ids)))
        await session.execute(delete(PriceRule).where(PriceRule.sku_id.in_(sku_ids)))
        await session.execute(delete(Sku).where(Sku.id.in_(sku_ids)))
    await session.execute(delete(Product).where(Product.name == PRODUCT_NAME))
    await session.execute(delete(Customer).where(Customer.name == CUSTOMER_NAME))
    await session.commit()


def _report() -> None:
    goods = UNIT_PRICE * QUANTITY
    profit = UNIT_PRICE - UNIT_COST
    rate = profit / UNIT_PRICE * 100
    print(f"""
样例内容（三张报价单，同一个产品与数量）：

  1) {CASES[0][0]}
     产品单价 {UNIT_PRICE} × {QUANTITY} = 货款 {goods}
     运费 {CASES[0][1]}（已确认，代收代付）
     → 应付合计 {goods + CASES[0][1]}
     产品利润 = {UNIT_PRICE} − {UNIT_COST} = {profit}，
     利润率 {rate:.0f}%（**不含运费**）

  2) {CASES[1][0]}
     产品、数量、单价与第 1 张**完全相同**，只有运费不同：{CASES[1][1]}
     → 应付合计 {goods + CASES[1][1]}
     产品利润与利润率**一个字都不变**（这正是"分离"的意义）

  3) {CASES[2][0]}
     运费明确填 {CASES[2][1]} 并确认（不是留空！），另加税费 {CASES[2][3]} 作"其他费用"
     → 应付合计 {goods + CASES[2][3]}
     对照：留空不填的报价单**正式发送会被拦下**，要求先确认运费；
     填 0 并确认的可以直接发 —— 两者在系统里是两种不同状态。
""".rstrip())


async def main(mode: str) -> int:
    async with SessionLocal() as session:
        if mode == "dry":
            _report()
            print("\n干跑结束。加 --apply 写入；--clean 撤销整条演示线。")
            return 0

        if mode == "clean":
            await _clean(session)
            print(f"已撤销「{MARK}」整条演示线。")
            return 0

        # ---- apply：幂等，先清后建 ----
        await _clean(session)

        admin = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalars().first()
        if admin is None:
            print("找不到 admin 账号；请先跑 scripts/seed.py。")
            return 1
        # 商机的 stage_id 是必填（NOT NULL）。取阶段列表里最早的那个，
        # 与 seed.py / 其它脚本同一做法 —— 不自己编一个阶段名。
        stage_id = (
            await session.execute(
                select(OpportunityStage.id).order_by(OpportunityStage.sequence.asc())
            )
        ).scalars().first()
        if stage_id is None:
            print("库里没有任何商机阶段；请先跑 scripts/seed.py。")
            return 1

        customer = Customer(name=CUSTOMER_NAME, owner_id=admin.id, level="B")
        session.add(customer)
        await session.flush()

        product = Product(name=PRODUCT_NAME, description=MARK, created_by=admin.id)
        session.add(product)
        await session.flush()

        sku = Sku(
            product_id=product.id, sku_code=SKU_CODE, name="运费分离样例件",
            specification="标准件", unit="件", status="active",
        )
        session.add(sku)
        await session.flush()

        # 成本：采购 60 + 包装 20 = 商品成本 80（**不含运费**）。
        # 四项分开写，正好演示"商品成本 = 采购+生产+包装+加工"。
        session.add(ProductCost(
            sku_id=sku.id,
            purchase_cost=Decimal("60.00"),
            package_cost=Decimal("20.00"),
            production_cost=Decimal("0.00"),
            processing_cost=Decimal("0.00"),
            currency="CNY",
            effective_from=datetime.now(UTC).replace(tzinfo=None).date(),
            remark=MARK,
        ))
        # 指导价 100：让报价单能取到"已维护售价"（A06：成本推算价不能当正式报价）
        session.add(PriceRule(
            sku_id=sku.id, min_qty=Decimal("0"), guide_price=UNIT_PRICE,
            currency="CNY", status="active", remark=MARK,
        ))
        await session.flush()

        created = []
        for index, (label, freight, freight_desc, other_fee) in enumerate(CASES, start=1):
            opportunity = Opportunity(
                customer_id=customer.id, title=f"{OPP_TITLE_PREFIX}-{label}",
                stage_id=stage_id, owner_id=admin.id, status="open",
            )
            session.add(opportunity)
            await session.flush()

            quote = Quote(
                quote_no=f"DEMO2Q{index:04d}", customer_id=customer.id,
                opportunity_id=opportunity.id, owner_id=admin.id, status="draft",
                valid_until=(datetime.now(UTC) + timedelta(days=30)).date(),
                created_by=admin.id,
            )
            session.add(quote)
            await session.flush()

            version = QuoteVersion(
                quote_id=quote.id, version_no=1,
                currency="CNY",
                # 新口径：产品单价不含运费，运费按已确认的实际金额代收代付
                pricing_basis="actual_pass_through",
                customer_name_snapshot=customer.name,
                delivery_terms="产品单价不含运费。运费单列，按已确认的实际金额由本公司代收代付。",
                remark=QUOTE_REMARK,
                approval_status="not_submitted",
                created_by=admin.id,
                created_at=datetime.now(UTC),
            )
            session.add(version)
            await session.flush()
            quote.current_version_id = version.id

            subtotal = (UNIT_PRICE * QUANTITY).quantize(Decimal("0.01"))
            session.add(QuoteItem(
                quote_version_id=version.id, sku_id=sku.id,
                sku_code_snapshot=sku.sku_code, sku_name_snapshot=sku.name,
                spec_snapshot=sku.specification, unit_snapshot=sku.unit,
                quantity=QUANTITY,
                cost_snapshot=UNIT_COST,
                package_cost_snapshot=Decimal("20.00"),
                # 旧口径的单件运费快照：新口径下不参与产品定价，这里如实留 0
                logistics_cost_snapshot=Decimal("0.00"),
                standard_price_snapshot=UNIT_PRICE,
                recommended_price_snapshot=UNIT_PRICE,
                quoted_price=UNIT_PRICE,
                price_source="general",
                # 产品利润 = 单价 − 商品成本（**不含运费**）
                profit_snapshot=(UNIT_PRICE - UNIT_COST),
                profit_rate_snapshot=((UNIT_PRICE - UNIT_COST) / UNIT_PRICE).quantize(
                    Decimal("0.000001")
                ),
            ))
            # 运费：向客户收取的运费就是这一条 `QuoteCharge(charge_type=logistics)`。
            # 打上确认时刻 = 业务已确认的实际金额（0 也要显式确认，不能靠留空）。
            session.add(QuoteCharge(
                quote_version_id=version.id, charge_type="logistics",
                description=freight_desc, amount=freight,
                logistics_confirmed_at=datetime.now(UTC),
            ))
            if other_fee:
                session.add(QuoteCharge(
                    quote_version_id=version.id, charge_type="tax",
                    description="税费", amount=other_fee,
                ))

            charge_total = freight + other_fee
            version.subtotal_amount = subtotal
            version.charge_amount = charge_total.quantize(Decimal("0.01"))
            version.logistics_amount = freight.quantize(Decimal("0.01"))
            version.other_charge_amount = other_fee.quantize(Decimal("0.01"))
            version.discount_amount = Decimal("0.00")
            version.total_amount = (subtotal + charge_total).quantize(Decimal("0.01"))
            created.append((quote.quote_no, version.id, version.total_amount))

        await session.commit()

        _report()
        print(f"\n已写入「{MARK}」：客户 1、产品/SKU 1、成本与指导价各 1、"
              f"商机 {len(created)}、报价单 {len(created)}。")
        for quote_no, version_id, total in created:
            print(f"  {quote_no}  V1 (id={version_id})  应付合计 {total}")
        print("\n撤销：--clean")
    return 0


if __name__ == "__main__":
    flag = sys.argv[1] if len(sys.argv) > 1 else ""
    mode = "apply" if flag == "--apply" else "clean" if flag == "--clean" else "dry"
    sys.exit(asyncio.run(main(mode)))
