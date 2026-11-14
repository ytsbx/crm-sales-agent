"""视觉演示数据：批量造客户 / 产品 / 商机，让分析页的图表有东西可看。

为什么需要它：分析页的趋势图、占比图、漏斗图全靠业务数据撑。
演示库里线索/商机/回款大多为 0，图表只有坐标轴，评不出好坏。

安全闸（两道）：
1. 库名含 `crm_sales_agent`（领导看的库）→ 直接拒绝；
2. 库名不含 `prod` / `demo` / `test` → 拒绝。

幂等：所有造出来的客户名带 `【演示造数】` 标记；重跑时先删掉上一批
（连同其商机），再重新生成。**只删带标记的**，不碰真实数据。

用法：
    cd backend
    DATABASE_URL=postgresql+asyncpg://crm:***@127.0.0.1:5432/crm_prod \
    PYTHONPATH=. .venv/bin/python scripts/seed_visual_demo.py
"""

import asyncio
import os
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.opportunity.model import (
    Opportunity,
    OpportunityStage,
    OpportunityStageHistory,
)
from app.modules.product.model import Product, Sku
from app.modules.user.model import User

MARK = '【演示造数】'
CUSTOMER_COUNT = 60
OPPORTUNITY_COUNT = 45
PRODUCT_COUNT = 12

REGIONS = ['华东', '华南', '华北', '华中', '西南', '西北', '东北']
CITIES = ['青岛', '宁波', '苏州', '佛山', '东莞', '温州', '厦门', '合肥', '郑州', '天津', '常州', '嘉兴']
SOURCES = ['展会', '客户介绍', '官网询盘', '电话开发', '老客复购', '企微添加']
LEVELS = ['A', 'B', 'B', 'C', None, None]
INDUSTRIES = ['包装', '家电', '汽配', '医疗', '食品', '电子', '建材']

PRODUCT_LINES = [
    ('周转箱', '塑料容器'),
    ('托盘', '塑料容器'),
    ('中空板箱', '包装材料'),
    ('缠绕膜', '包装材料'),
    ('气泡袋', '包装材料'),
]
COLORS = ['蓝色', '灰色', '透明', '黑色', '白色']
SIZES = ['400×300', '500×400', '600×400', '800×500']
SPECS = ['标准', '加厚', '轻型']

# 商机阶段按「越靠前越多」的权重抽：这样漏斗才是漏斗形，不是柱子
# 权重必须**逐级递减**：随机均匀抽会让下游阶段比上游还多，
# 漏斗图画出来会中途回升（实测「产品推荐 2 → 核价 1 → 已报价 2」）。
# 没有独立的「失单」阶段：失单是商机的 status，不是阶段（库里只有 9 个阶段）。
STAGE_WEIGHTS = [
    ('new_inquiry', 24),
    ('need_confirm', 18),
    ('product_recommend', 13),
    ('pricing', 9),
    ('quoted', 7),
    ('sample', 5),
    ('negotiation', 4),
    ('pending_order', 3),
    ('won', 6),
]


def weighted_stage(rng: random.Random) -> str:
    pool = [code for code, weight in STAGE_WEIGHTS for _ in range(weight)]
    return rng.choice(pool)


async def main() -> None:
    db_url = os.environ.get('DATABASE_URL', '')
    if 'crm_sales_agent' in db_url:
        raise SystemExit('拒绝对 crm_sales_agent（领导看的库）造数')
    if not any(key in db_url for key in ('crm_prod', 'demo', 'test')):
        raise SystemExit(f'库名不在白名单里，拒绝执行：{db_url.rsplit("/", 1)[-1]}')

    rng = random.Random(20261114)  # 固定种子：每次生成同一批名字，便于比对
    now = datetime.now(UTC)

    async with SessionLocal() as session:
        admin = (await session.execute(select(User).where(User.username == 'admin'))).scalar_one()
        zhangsan = (
            await session.execute(select(User).where(User.username == 'zhangsan'))
        ).scalar_one_or_none()
        owner = zhangsan or admin

        stages = {
            stage.code: stage
            for stage in (await session.execute(select(OpportunityStage))).scalars()
        }
        missing = {code for code, _ in STAGE_WEIGHTS} - set(stages)
        if missing:
            raise SystemExit(f'商机阶段缺失：{sorted(missing)}，先跑 scripts.seed')

        # ---- 幂等：清掉上一批（只删带标记的） ----
        old_customers = (
            await session.execute(select(Customer).where(Customer.name.like(f'%{MARK}%')))
        ).scalars().all()
        if old_customers:
            old_ids = [c.id for c in old_customers]
            await session.execute(
                delete(Opportunity).where(Opportunity.customer_id.in_(old_ids))
            )
            await session.execute(delete(Customer).where(Customer.id.in_(old_ids)))
            await session.commit()
            print(f'已清理上一批 {len(old_ids)} 个演示客户（含其商机）')

        old_products = (
            await session.execute(select(Product).where(Product.name.like(f'%{MARK}%')))
        ).scalars().all()
        if old_products:
            old_pids = [p.id for p in old_products]
            await session.execute(delete(Sku).where(Sku.product_id.in_(old_pids)))
            await session.execute(delete(Product).where(Product.id.in_(old_pids)))
            await session.commit()
            print(f'已清理上一批 {len(old_pids)} 个演示产品')

        # ---- 客户 ----
        customers: list[Customer] = []
        for index in range(CUSTOMER_COUNT):
            city = rng.choice(CITIES)
            industry = rng.choice(INDUSTRIES)
            created = now - timedelta(days=rng.randint(5, 330), hours=rng.randint(0, 20))
            customer = Customer(
                name=f'{city}{industry}{index + 1:02d}厂{MARK}',
                short_name=f'{city}{industry}{index + 1:02d}',
                customer_type='企业',
                country='中国',
                region=rng.choice(REGIONS),
                address=f'{city}市示例路 {rng.randint(1, 999)} 号',
                source=rng.choice(SOURCES),
                level=rng.choice(LEVELS),
                status='active',
                pool_status='private',
                owner_id=owner.id,
                created_by=admin.id,
                created_at=created,
                updated_at=created,
            )
            session.add(customer)
            customers.append(customer)
        await session.flush()
        print(f'客户 {len(customers)} 个')

        # ---- 产品 + SKU ----
        products = 0
        for index in range(PRODUCT_COUNT):
            line, category = rng.choice(PRODUCT_LINES)
            product = Product(
                name=f'{line}系列{index + 1:02d}{MARK}',
                product_line=line,
                category=category,
                brand='演示品牌',
                description='演示数据，用于图表效果评估',
                status='active',
                created_by=admin.id,
            )
            session.add(product)
            await session.flush()
            for sub in range(rng.randint(1, 3)):
                session.add(
                    Sku(
                        product_id=product.id,
                        sku_code=f'DEMO{index + 1:02d}{sub + 1}',  # 必填且唯一
                        name=f'{line}{index + 1:02d}-{sub + 1}',
                        specification=f'{rng.choice(SIZES)} {rng.choice(SPECS)}',
                        color=rng.choice(COLORS),
                        material='PP',
                        moq=rng.choice([100, 500, 1000]),
                        status='active',
                    )
                )
            products += 1
        await session.flush()
        print(f'产品 {products} 个（含 SKU）')

        # ---- 商机 ----
        stage_codes = [code for code, _ in STAGE_WEIGHTS]
        created_opportunities = 0
        for index in range(OPPORTUNITY_COUNT):
            customer = rng.choice(customers)
            stage_code = weighted_stage(rng)
            stage = stages[stage_code]
            created = now - timedelta(days=rng.randint(3, 300), hours=rng.randint(0, 20))
            amount = rng.choice([12000, 28000, 45000, 68000, 86000, 120000, 186000])
            is_won = stage_code == 'won'
            opportunity = Opportunity(
                customer_id=customer.id,
                title=f'{customer.short_name} {rng.choice(["年度采购", "试单", "补货", "打样后下单"])}',
                source=rng.choice(SOURCES),
                stage_id=stage.id,
                expected_amount=amount,
                currency='CNY',
                owner_id=owner.id,
                competitor=rng.choice(['本地一家同行', '暂无', '宁波供应商']),
                risk_level=rng.choice(['low', 'medium', 'medium', 'high']),
                next_action=rng.choice(['确认用量', '安排打样', '等客户回价', '准备合同']),
                status='won' if is_won else 'open',
                created_at=created,
                updated_at=created,
            )
            session.add(opportunity)
            await session.flush()  # 先拿到 id，才能挂阶段历史

            # 补阶段历史：**漏斗图统计的是「到达过」的阶段，不是当前阶段**。
            # 不补历史这些商机压根不进漏斗（实测漏斗里还是旧数据、而且中途回升）。
            # 从第一个阶段一路写到当前阶段，天然单调递减。
            prev_code: str | None = None
            reached = stage_codes.index(stage_code) + 1
            for step, code in enumerate(stage_codes[:reached]):
                session.add(
                    OpportunityStageHistory(
                        opportunity_id=opportunity.id,
                        from_stage_id=stages[prev_code].id if prev_code else None,
                        to_stage_id=stages[code].id,
                        operator_id=owner.id,
                        remark='演示造数',
                        entered_at=created + timedelta(days=step * rng.randint(1, 4)),
                    )
                )
                prev_code = code
            created_opportunities += 1
        await session.flush()
        print(f'商机 {created_opportunities} 条（阶段按 {len(stage_codes)} 档分布）')

        await session.commit()

    print('完成。刷新分析页看图表。')


if __name__ == '__main__':
    asyncio.run(main())
