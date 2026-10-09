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

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

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
#: 商机标题前缀（只在**建**的时候用来起名；删除一律按客户 id，不按标题猜）
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


async def _sku_referenced_by_business(session, sku_ids: list[int]) -> str | None:
    """演示 SKU 是否已被**业务单据**引用（订单明细、样品、试算、商机需求、专属价）。

    被引用时**拒绝清理并说清是哪里**，而不是抛一个外键异常让人猜。
    这里只查业务单据这一层；主数据配套表（字段权威、整版快照、外部身份）
    是跟着 SKU 一起删的配套数据，不算"被业务引用"。
    """
    if not sku_ids:
        return None
    for table, label in (
        ("sales_order_items", "订单明细"),
        ("sample_items", "样品明细"),
        ("logistics_quotes", "物流试算记录"),
        ("opportunity_items", "商机需求明细"),
        ("customer_price_rules", "客户专属价"),
    ):
        hit = (
            await session.execute(
                text(f"select count(*) from {table} where sku_id = any(:ids)"),
                {"ids": sku_ids},
            )
        ).scalar_one()
        if hit:
            return f"{label}（{table}）还有 {hit} 条"
    return None


#: 递归清理时**刻意不动**的表。它们代表"演示数据已经变成别的模块的地盘"，
#: 删它们会把这件事悄悄抹掉，而那是必须让人知道的信息：宁可报错，不可静默。
#: 真要清，请走界面的删除入口（会留审计、走软删）。
#:
#: 注：`sales_orders` **不在**这里 —— 演示商机上的订单是这条演示线自己跑出来的
#: 产物（转单验证），属于该清的范围，由"按客户子树"的递归覆盖。
#: 而"删客户"那一步仍会被任何没被递归覆盖到的真实订单以外键挡住，不会误删。
CLEAN_EXCLUDED_TABLES = {
    "contract_documents",  # 已经出过合同
    "sales_cases",         # 已经沉淀成案例
    "order_drafts",        # 已有客户确认过的下单草稿
}


async def _pk_columns(session, table: str) -> list[str]:
    """这张表的主键列（按列序号）。**不能假定叫 `id`** —— 关联表是联合主键。

    例如 `customer_tags` 的主键是 `(customer_id, tag_id)`、`user_roles` 是
    `(user_id, role_id)`。写死 `id` 会在这类表上直接报"列不存在"。
    """
    return list(
        (
            await session.execute(
                text(
                    """
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON kcu.constraint_name = tc.constraint_name
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema = 'public'
                      AND tc.table_name = :t
                    ORDER BY kcu.ordinal_position
                    """
                ),
                {"t": table},
            )
        ).scalars().all()
    )


async def _delete_rows(
    session, *, table: str, pk_columns: list[str], rows: list[dict]
) -> None:
    """按主键条件删掉这些行（`OR` 串联的等值匹配，无注入面）。"""
    if not rows:
        return
    clauses, params = [], {}
    for index, row in enumerate(rows):
        parts = []
        for col in pk_columns:
            key = f"p{index}_{col}"
            parts.append(f"{col} = :{key}")
            params[key] = row[col]
        clauses.append("(" + " AND ".join(parts) + ")")
    await session.execute(
        text(f"DELETE FROM {table} WHERE " + " OR ".join(clauses)), params
    )


async def _delete_subtree(
    session, *, table: str, pk_columns: list[str], rows: list[dict],
    excluded: set[str] | None = None,
) -> None:
    """删掉 `rows` 指定的这些行**及其整棵子树**（**子先父后**，按外键目录递归）。

    `rows` 是"行的定位条件"列表：每项形如 `{"id": 5}` 或
    `{"customer_id": 3, "tag_id": 7}` —— 键是主键列名。

    为什么按目录递归而不是手写一串表名：`customers` / `skus` / `opportunities`
    的下游各有十几张表，手写清单漏一张就报一个外键错误、改一轮再跑一次
    （实测就是这样耗掉的）。目录是数据库自己的事实，不会有版本漂移。

    **两个坑都是实测踩过的，别改回去**：
    1. 递归时要收集子表的**主键**，不能拿"命中行的外键值"当子表 id 用 ——
       `opportunities.customer_id` 返回的是客户 id，拿它当商机 id 去匹配，
       下一层永远命中 0 行，"子先父后"被绕过，接着撞
       `opportunity_stage_history` 的外键；
    2. 主键列名不能假定是 `id`（关联表是联合主键，见 `_pk_columns`）。

    ⚠️ `CLEAN_EXCLUDED_TABLES` 里的表刻意绕开（见那组常量的说明）。
    """
    if not rows:
        return
    excluded = CLEAN_EXCLUDED_TABLES if excluded is None else excluded
    children = (
        await session.execute(
            text(
                """
                SELECT tc.table_name, kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name = tc.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND ccu.table_name = :parent
                  AND tc.table_name <> :parent
                """
            ),
            {"parent": table},
        )
    ).all()
    for child_table, child_column in children:
        if child_table in excluded:
            continue
        child_pks = await _pk_columns(session, child_table)
        if not child_pks:
            continue
        # 父行的主键值 → 用来筛出子行（子表可能有多个外键指向同一父表，故去重）
        parent_pk = pk_columns[0]
        parent_values = list({row[parent_pk] for row in rows if parent_pk in row})
        if not parent_values:
            continue
        child_rows = (
            await session.execute(
                text(
                    f"SELECT DISTINCT {', '.join(child_pks)} FROM {child_table} "
                    f"WHERE {child_column} = any(:v)"
                ),
                {"v": parent_values},
            )
        ).mappings().all()
        if child_rows:
            # 递归删子表的子树：条件换成**子表自己的主键**
            await _delete_subtree(
                session,
                table=child_table,
                pk_columns=child_pks,
                rows=[dict(r) for r in child_rows],
                excluded=excluded,
            )
    await _delete_rows(session, table=table, pk_columns=pk_columns, rows=rows)


async def _clean(session, *, force: bool = False) -> None:
    """撤销整条演示线（被别的模块引用时**拒绝并说清原因**，不静默删别人的数据）。

    删除顺序按外键目录递归（子先父后），不手写表名清单 ——
    `customers` 的下游有 16 张直接引用表、递归下去更多，
    手写漏一张就报一个外键错误、改一轮再跑一次（实测就是这么耗掉的）。

    `force=True` 时才连 `CLEAN_EXCLUDED_TABLES` 里那几张（合同/案例/下单草稿）
    一起删 —— 这是**显式的重手**，只给一次性测试库用，默认绝不这么做。
    """
    try:
        await _clean_locked(session, force=force)
        await session.commit()
    except (IntegrityError, ProgrammingError) as exc:
        await session.rollback()
        raise SystemExit(
            "撤销未完成：演示数据已被其他模块的单据引用，脚本不替你删这些。\n"
            f"  数据库报的约束是：{getattr(exc, 'orig', exc)}\n"
            "  请先删掉引用它的单据（合同 / 案例 / 下单草稿等），再跑 --clean；\n"
            "  或直接在界面上把演示客户/产品删掉（走系统自己的软删与审计）。"
        ) from exc


async def _clean_locked(session, *, force: bool = False) -> None:
    """`_clean` 的实际内容（调用方负责提交与把外键错误翻成人话）。"""
    excluded = set() if force else CLEAN_EXCLUDED_TABLES
    quote_ids = list(
        (
            await session.execute(select(Quote.id).where(Quote.quote_no.like("DEMO2Q%")))
        ).scalars().all()
    )
    # 客户以**本脚本造的报价**为锚：报价上的 customer_id 才是这个脚本真正用过的客户。
    # 只按名字找会圈太宽 —— 冒烟脚本也会用同一个演示客户名建自己的数据，
    # 按名字删会连别人的夹具一起清掉（实测踩到：一次圈进 24 个商机）。
    customer_ids = set(
        (
            await session.execute(
                select(Quote.customer_id).where(Quote.id.in_(quote_ids)).distinct()
            )
        ).scalars().all()
    ) if quote_ids else set()
    # 兜底：报价一张都没建成（比如建到一半失败）时，仍按名字回收那个空客户
    for cid in (
        await session.execute(select(Customer.id).where(Customer.name == CUSTOMER_NAME))
    ).scalars().all():
        customer_ids.add(cid)
    customer_ids = sorted(customer_ids)
    sku_ids = list(
        (
            await session.execute(select(Sku.id).where(Sku.sku_code == SKU_CODE))
        ).scalars().all()
    )

    if sku_ids:
        blocked = await _sku_referenced_by_business(session, sku_ids)
        if blocked:
            raise SystemExit(
                f"演示 SKU {SKU_CODE} 已被业务单据引用（{blocked}），不能物理删除 —— "
                "请先删掉那些单据，或改用手工软删。"
            )

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

    # ---- 用递归清理把每个锚点的整棵子树删掉（子先父后，见 `_delete_subtree`）----
    # 商机、报价、明细、费用、跟进、任务、联系人、专属价……全在这一步里，
    # 不需要手写表名清单（手写漏一张就报一个外键错误，改一轮跑一次）。
    if customer_ids:
        await _delete_subtree(
            session,
            table="customers",
            pk_columns=["id"],
            rows=[{"id": cid} for cid in customer_ids],
            excluded=excluded,
        )

    if sku_ids:
        for table in ("sku_field_authorities", "sku_master_versions", "sku_identity_sources"):
            await session.execute(
                text(f"delete from {table} where sku_id = any(:ids)"), {"ids": sku_ids}
            )
        await session.execute(delete(ProductCost).where(ProductCost.sku_id.in_(sku_ids)))
        await session.execute(delete(PriceRule).where(PriceRule.sku_id.in_(sku_ids)))
        await session.execute(delete(Sku).where(Sku.id.in_(sku_ids)))

    await session.execute(delete(Product).where(Product.name == PRODUCT_NAME))


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


async def main(mode: str, *, force: bool = False) -> int:
    async with SessionLocal() as session:
        if mode == "dry":
            _report()
            print("\n干跑结束。加 --apply 写入；--clean 撤销整条演示线。")
            return 0

        if mode == "clean":
            await _clean(session, force=force)
            print(f"已撤销「{MARK}」整条演示线。" + ("（--force：连合同/案例/下单草稿一起删）" if force else ""))
            return 0

        # ---- apply：幂等，先清后建 ----
        # 清不掉那些"被合同/案例/草稿引用"的旧数据时**不阻断写入**：
        # 报价号是固定的 DEMO2Q0001..3，与其硬撞唯一约束，不如让"先清后建"
        # 里的"建"照常完成 —— 旧数据留着并由下面如实说明。
        reused: list[str] = []
        try:
            await _clean(session, force=force)
        except SystemExit as exc:
            reused = [line for line in str(exc).splitlines() if "约束" in line]
            print("提示：上一次的演示数据没能完全清掉，本次将复用它的编号：")
            for line in reused:
                print("   ", line.strip())

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
    args = [a for a in sys.argv[1:]]
    force = "--force" in args
    flag = next((a for a in args if a in ("--apply", "--clean")), "")
    mode = "apply" if flag == "--apply" else "clean" if flag == "--clean" else "dry"
    sys.exit(asyncio.run(main(mode, force=force)))
