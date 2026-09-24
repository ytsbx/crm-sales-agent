"""初始化种子数据：角色、权限、用户、示例客户。

可重复执行：已存在的数据会跳过，不会重复插入。
用法：cd backend && .venv/bin/python -m scripts.seed
"""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.customer.model import Contact, Customer
from app.modules.followup.model import FollowUp
from app.modules.lead.model import Lead
from app.modules.opportunity.model import (
    LossReason,
    Opportunity,
    OpportunityItem,
    OpportunityStage,
    OpportunityStageHistory,
)
from app.modules.product.model import Product, Sku
from app.modules.pricing.model import (
    CustomerPriceRule,
    LogisticsRate,
    PricePermission,
    PriceRule,
    ProductCost,
)
from app.modules.task.model import Task
from app.modules.settings.model import PublicPoolRule, SystemSetting, TaskRule
from app.modules.user.model import Department, Permission, Role, User, role_permissions, user_roles

# ------------------------------------------------------------------ 权限清单

PERMISSIONS: list[tuple[str, str, str, str]] = [
    # (code, name, resource, action)
    ("customer:view", "查看客户", "customer", "view"),
    ("customer:create", "新建客户", "customer", "create"),
    ("customer:update", "编辑客户", "customer", "update"),
    ("customer:delete", "删除客户", "customer", "delete"),
    ("customer:assign", "分配客户", "customer", "assign"),
    ("product:view", "查看产品", "product", "view"),
    ("product:manage", "维护产品", "product", "manage"),
    ("opportunity:view", "查看商机", "opportunity", "view"),
    ("opportunity:manage", "维护商机", "opportunity", "manage"),
    ("lead:view", "查看线索", "lead", "view"),
    ("lead:create", "新建线索", "lead", "create"),
    ("lead:assign", "分配线索", "lead", "assign"),
    ("lead:convert", "线索转化", "lead", "convert"),
    ("followup:view", "查看跟进", "followup", "view"),
    ("followup:create", "记录跟进", "followup", "create"),
    ("task:view", "查看任务", "task", "view"),
    ("task:manage", "管理任务", "task", "manage"),
    ("quote:view", "查看报价", "quote", "view"),
    ("quote:manage", "维护报价", "quote", "manage"),
    ("quote:approve", "报价审批", "quote", "approve"),
    ("price:manage", "价格维护", "price", "manage"),
    ("order:view", "查看订单", "order", "view"),
    ("order:manage", "维护订单", "order", "manage"),
    ("payment:view", "查看回款", "payment", "view"),
    ("payment:manage", "登记与确认回款", "payment", "manage"),
    ("user:manage", "用户管理", "user", "manage"),
    ("audit:view", "查看审计", "audit", "view"),
    ("file:view", "查看文件", "file", "view"),
    ("file:manage", "上传与删除文件", "file", "manage"),
    ("settings:manage", "系统设置与规则", "settings", "manage"),
    ("agent:use", "使用 Sales Agent", "agent", "use"),
]

SALES_PERMISSIONS = [
    "customer:view",
    "customer:create",
    "customer:update",
    "product:view",
    "opportunity:view",
    "opportunity:manage",
    "lead:view",
    "lead:create",
    "lead:convert",
    "followup:view",
    "followup:create",
    "task:view",
    "task:manage",
    "quote:view",
    "quote:manage",
    "order:view",
    # 业务员要把成交的报价转成订单，所以给维护权；财务确认回款是另一项权限
    "order:manage",
    "payment:view",
    "file:view",
    "file:manage",
    "agent:use",
]

MANAGER_PERMISSIONS = SALES_PERMISSIONS + [
    "customer:assign",
    "lead:assign",
    "quote:approve",
    "price:manage",
    "product:manage",
    "customer:delete",
    "order:manage",
    "payment:manage",
]

FINANCE_PERMISSIONS = [
    "customer:view",
    "quote:view",
    "order:view",
    "payment:view",
    "payment:manage",
    "order:manage",
]


async def seed() -> None:
    async with SessionLocal() as session:
        # 1. 权限
        existing = {
            code for code in (await session.execute(select(Permission.code))).scalars().all()
        }
        for code, name, resource, action in PERMISSIONS:
            if code not in existing:
                session.add(
                    Permission(code=code, name=name, resource=resource, action=action)
                )
        await session.flush()

        perm_map = {
            p.code: p for p in (await session.execute(select(Permission))).scalars().all()
        }

        # 2. 部门
        dept = (
            await session.execute(select(Department).where(Department.name == "销售部"))
        ).scalar_one_or_none()
        if dept is None:
            dept = Department(name="销售部", status="active")
            session.add(dept)
            await session.flush()

        # 3. 角色
        role_defs = [
            ("admin", "管理员", "all", list(perm_map.keys()), "拥有全部权限"),
            ("sales_manager", "销售主管", "department_and_sub", MANAGER_PERMISSIONS, "管团队、审报价"),
            ("salesperson", "业务员", "self", SALES_PERMISSIONS, "管自己的客户与报价"),
            ("finance", "财务", "all", FINANCE_PERMISSIONS, "应收、回款确认"),
        ]
        role_map: dict[str, Role] = {}
        for code, name, scope, perms, desc in role_defs:
            role = (
                await session.execute(select(Role).where(Role.code == code))
            ).scalar_one_or_none()
            if role is None:
                role = Role(code=code, name=name, data_scope=scope, description=desc)
                session.add(role)
                await session.flush()
            role_map[code] = role
            have = {
                pid
                for pid in (
                    await session.execute(
                        select(role_permissions.c.permission_id).where(
                            role_permissions.c.role_id == role.id
                        )
                    )
                ).scalars().all()
            }
            for code_name in perms:
                perm = perm_map.get(code_name)
                if perm and perm.id not in have:
                    await session.execute(
                        role_permissions.insert().values(role_id=role.id, permission_id=perm.id)
                    )

        # 4. 用户
        user_defs = [
            ("admin", "系统管理员", "admin123", "admin"),
            ("zhangsan", "张三", "123456", "salesperson"),
            ("lisi", "李四", "123456", "sales_manager"),
            ("wangwu", "王五", "123456", "finance"),
        ]
        user_map: dict[str, User] = {}
        for username, name, password, role_code in user_defs:
            user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    username=username,
                    name=name,
                    password_hash=hash_password(password),
                    department_id=dept.id,
                    status="active",
                )
                session.add(user)
                await session.flush()
            user_map[username] = user
            exists = (
                await session.execute(
                    select(user_roles.c.user_id).where(
                        user_roles.c.user_id == user.id,
                        user_roles.c.role_id == role_map[role_code].id,
                    )
                )
            ).first()
            if not exists:
                await session.execute(
                    user_roles.insert().values(
                        user_id=user.id, role_id=role_map[role_code].id
                    )
                )

        # 5. 示例客户（仅为演示，可随时删除）
        demo_customers = [
            {
                "name": "宁波宏远包装制品有限公司",
                "short_name": "宏远包装",
                "region": "浙江",
                "address": "浙江省宁波市鄞州区工业园 12 号",
                "source": "展会",
                "level": "A",
                "owner": "zhangsan",
                "contacts": [
                    ("王建国", "采购经理", "13800000001", True),
                    ("周敏", "财务", "13800000002", False),
                ],
            },
            {
                "name": "苏州恒达精密机械有限公司",
                "short_name": "恒达机械",
                "region": "江苏",
                "address": "江苏省苏州市吴中区兴业路 88 号",
                "source": "老客户介绍",
                "level": "B",
                "owner": "zhangsan",
                "contacts": [("刘伟", "总经理", "13900000001", True)],
            },
            {
                "name": "上海云汐日用百货有限公司",
                "short_name": "云汐日用",
                "region": "上海",
                "address": "上海市松江区新桥镇新中路 66 号",
                "source": "官网",
                "level": "C",
                "owner": "lisi",
                "contacts": [],
            },
        ]
        for item in demo_customers:
            exists = (
                await session.execute(select(Customer).where(Customer.name == item["name"]))
            ).scalar_one_or_none()
            if exists:
                continue
            owner = user_map[item["owner"]]
            customer = Customer(
                name=item["name"],
                short_name=item["short_name"],
                customer_type="企业",
                country="中国",
                region=item["region"],
                address=item["address"],
                source=item["source"],
                level=item["level"],
                status="active",
                pool_status="private",
                owner_id=owner.id,
                created_by=owner.id,
                last_followup_at=datetime.now(UTC),
            )
            session.add(customer)
            await session.flush()
            for cname, title, mobile, is_primary in item["contacts"]:
                session.add(
                    Contact(
                        customer_id=customer.id,
                        name=cname,
                        title=title,
                        mobile=mobile,
                        is_primary=is_primary,
                        owner_id=owner.id,
                        source="手工录入",
                    )
                )

        # 6. 示例产品与 SKU（仅为演示，可随时删除；真实产品资料待业务方提供）
        demo_products = [
            {
                "name": "塑料周转箱",
                "product_line": "塑料制品",
                "category": "物流器具",
                "brand": "自有品牌",
                "description": "示例数据：用于演示产品与 SKU 的对应关系，可直接删除。",
                "skus": [
                    {
                        "sku_code": "ZX-6040-B",
                        "specification": "600×400×300mm",
                        "color": "蓝色",
                        "material": "全新 PP 料",
                        "length": 600,
                        "width": 400,
                        "height": 300,
                        "weight": 1.8,
                        "carton_qty": 20,
                        "carton_volume": 0.144,
                        "moq": 500,
                        "package_type": "编织袋",
                        "unit": "个",
                    },
                    {
                        "sku_code": "ZX-6040-G",
                        "specification": "600×400×300mm",
                        "color": "绿色",
                        "material": "全新 PP 料",
                        "length": 600,
                        "width": 400,
                        "height": 300,
                        "weight": 1.8,
                        "carton_qty": 20,
                        "carton_volume": 0.144,
                        "moq": 500,
                        "package_type": "编织袋",
                        "unit": "个",
                    },
                    {
                        "sku_code": "ZX-8050-B",
                        "specification": "800×500×400mm",
                        "color": "蓝色",
                        "material": "全新 PP 料",
                        "length": 800,
                        "width": 500,
                        "height": 400,
                        "weight": 3.2,
                        "carton_qty": 10,
                        "carton_volume": 0.16,
                        "moq": 300,
                        "package_type": "编织袋",
                        "unit": "个",
                    },
                ],
            },
            {
                "name": "中空板包装箱",
                "product_line": "包装材料",
                "category": "包装箱",
                "brand": "自有品牌",
                "description": "示例数据：可按客户尺寸定做，起订量较大。",
                "skus": [
                    {
                        "sku_code": "ZKB-1208-5",
                        "specification": "1200×800×500mm 板厚 5mm",
                        "material": "PP 中空板",
                        "length": 1200,
                        "width": 800,
                        "height": 500,
                        "weight": 2.6,
                        "carton_qty": 50,
                        "carton_volume": 0.48,
                        "moq": 1000,
                        "package_type": "托盘",
                        "unit": "个",
                    },
                    {
                        "sku_code": "ZKB-1208-8",
                        "specification": "1200×800×500mm 板厚 8mm",
                        "material": "PP 中空板",
                        "length": 1200,
                        "width": 800,
                        "height": 500,
                        "weight": 3.9,
                        "carton_qty": 50,
                        "carton_volume": 0.48,
                        "moq": 1000,
                        "package_type": "托盘",
                        "unit": "个",
                    },
                ],
            },
            {
                "name": "PE 缠绕膜",
                "product_line": "包装耗材",
                "category": "缠绕膜",
                "brand": "自有品牌",
                "description": "示例数据：按卷报价，整托出货。",
                "skus": [
                    {
                        "sku_code": "CR-500-3",
                        "specification": "宽 500mm × 厚 3 丝",
                        "weight": 3.5,
                        "carton_qty": 6,
                        "moq": 100,
                        "package_type": "纸箱",
                        "unit": "卷",
                    }
                ],
            },
        ]

        for item in demo_products:
            product = (
                await session.execute(select(Product).where(Product.name == item["name"]))
            ).scalar_one_or_none()
            if product is None:
                product = Product(
                    name=item["name"],
                    product_line=item["product_line"],
                    category=item["category"],
                    brand=item["brand"],
                    description=item["description"],
                    status="active",
                    created_by=user_map["admin"].id,
                )
                session.add(product)
                await session.flush()
            for sku_item in item["skus"]:
                exists = (
                    await session.execute(
                        select(Sku).where(Sku.sku_code == sku_item["sku_code"])
                    )
                ).scalar_one_or_none()
                if exists is None:
                    session.add(Sku(**sku_item, product_id=product.id, status="active"))

        # 7. 商机阶段（总设计文档 §10 的 9 个阶段，按顺序固定下来）
        stage_defs = [
            ("new_inquiry", "新询盘"),
            ("need_confirm", "需求确认"),
            ("product_recommend", "产品推荐"),
            ("pricing", "核价"),
            ("quoted", "已报价"),
            ("sample", "样品"),
            ("negotiation", "商务谈判"),
            ("pending_order", "待下单"),
            ("won", "成交"),
        ]
        stage_map: dict[str, OpportunityStage] = {}
        for index, (code, name) in enumerate(stage_defs, start=1):
            stage = (
                await session.execute(
                    select(OpportunityStage).where(OpportunityStage.code == code)
                )
            ).scalar_one_or_none()
            if stage is None:
                stage = OpportunityStage(
                    code=code,
                    name=name,
                    sequence=index,
                    is_win=(code == "won"),
                    is_loss=False,
                    status="active",
                )
                session.add(stage)
                await session.flush()
            stage_map[code] = stage

        # 8. 失单原因（总设计文档 §19 的 9 类，必须有初始数据才能失单）
        loss_defs = [
            ("price", "价格", "商务"),
            ("moq", "MOQ", "商务"),
            ("delivery", "交期", "交付"),
            ("product", "产品", "产品"),
            ("competitor", "竞争对手", "竞争"),
            ("payment_terms", "付款条件", "商务"),
            ("project_cancelled", "项目取消", "客户"),
            ("no_real_demand", "无真实需求", "客户"),
            ("other", "其他", "其他"),
        ]
        for code, name, category in loss_defs:
            exists = (
                await session.execute(select(LossReason).where(LossReason.code == code))
            ).scalar_one_or_none()
            if exists is None:
                session.add(LossReason(code=code, name=name, category=category, status="active"))

        # 9. 示例线索与商机（演示用，可随时删除）
        lead_defs = [
            {
                "name": "义乌小商品城采购询价",
                "company_name": "义乌市盛通日用品有限公司",
                "contact_name": "陈海",
                "mobile": "13700000001",
                "source": "官网",
                "region": "浙江",
                "owner": None,
                "status": "pending",
            },
            {
                "name": "杭州电商仓周转箱需求",
                "company_name": "杭州云仓供应链管理有限公司",
                "contact_name": "赵敏",
                "mobile": "13700000002",
                "source": "企业微信",
                "region": "浙江",
                "owner": "zhangsan",
                "status": "assigned",
            },
        ]
        for item in lead_defs:
            exists = (
                await session.execute(select(Lead).where(Lead.name == item["name"]))
            ).scalar_one_or_none()
            if exists:
                continue
            owner = user_map[item["owner"]] if item["owner"] else None
            session.add(
                Lead(
                    name=item["name"],
                    company_name=item["company_name"],
                    contact_name=item["contact_name"],
                    mobile=item["mobile"],
                    source=item["source"],
                    region=item["region"],
                    status=item["status"],
                    owner_id=owner.id if owner else None,
                    created_by=user_map["admin"].id,
                )
            )

        opp_exists = (
            await session.execute(
                select(Opportunity).where(Opportunity.title == "宏远包装周转箱年度采购")
            )
        ).scalar_one_or_none()
        if opp_exists is None:
            customer = (
                await session.execute(
                    select(Customer).where(Customer.name == "宁波宏远包装制品有限公司")
                )
            ).scalar_one_or_none()
            contact = (
                await session.execute(
                    select(Contact).where(Contact.customer_id == customer.id, Contact.is_primary.is_(True))
                )
            ).scalar_one_or_none()
            zhangsan = user_map["zhangsan"]
            opportunity = Opportunity(
                customer_id=customer.id,
                primary_contact_id=contact.id if contact else None,
                title="宏远包装周转箱年度采购",
                source="展会",
                stage_id=stage_map["need_confirm"].id,
                expected_amount=86000,
                currency="CNY",
                owner_id=zhangsan.id,
                competitor="宁波本地一家周转箱厂",
                risk_level="medium",
                next_action="确认三种规格的年用量后进入核价",
                status="open",
                created_by=zhangsan.id,
            )
            session.add(opportunity)
            await session.flush()
            session.add(
                OpportunityStageHistory(
                    opportunity_id=opportunity.id,
                    from_stage_id=None,
                    to_stage_id=stage_map["new_inquiry"].id,
                    operator_id=zhangsan.id,
                    remark="创建商机",
                    entered_at=datetime.now(UTC),
                )
            )
            session.add(
                OpportunityStageHistory(
                    opportunity_id=opportunity.id,
                    from_stage_id=stage_map["new_inquiry"].id,
                    to_stage_id=stage_map["need_confirm"].id,
                    operator_id=zhangsan.id,
                    remark="客户已确认采购规格",
                    entered_at=datetime.now(UTC),
                )
            )

            sku_map = {
                sku.sku_code: sku
                for sku in (await session.execute(select(Sku))).scalars().all()
            }
            session.add(
                OpportunityItem(
                    opportunity_id=opportunity.id,
                    sku_id=sku_map["ZX-6040-B"].id,
                    quantity=3000,
                    target_price=28,
                    specification="600×400×300mm",
                    color="蓝色",
                    package_requirement="编织袋，20 个一包",
                    destination="浙江宁波",
                    remark="年用量，分四批交付",
                )
            )
            session.add(
                OpportunityItem(
                    opportunity_id=opportunity.id,
                    sku_id=sku_map["ZKB-1208-5"].id,
                    quantity=1200,
                    target_price=52,
                    specification="1200×800×500mm 板厚 5mm",
                    package_requirement="托盘",
                    destination="浙江宁波",
                )
            )
            session.add(
                FollowUp(
                    customer_id=customer.id,
                    contact_id=contact.id if contact else None,
                    opportunity_id=opportunity.id,
                    owner_id=zhangsan.id,
                    followup_type="电话",
                    content="客户确认全年需要三种规格周转箱，要求先报 600×400×300 的价格。",
                    customer_feedback="对价格比较敏感，提到现有供应商报价偏低。",
                    next_action="整理成本后做核价",
                )
            )
            session.add(
                Task(
                    title="给宏远包装做周转箱核价",
                    task_type="pricing",
                    customer_id=customer.id,
                    opportunity_id=opportunity.id,
                    owner_id=zhangsan.id,
                    priority="high",
                    status="pending",
                    due_at=datetime.now(UTC),
                    source="manual",
                )
            )

        # 10. 成本与价格（演示占位值：真实价格表到位后直接在价格中心界面替换）
        today = datetime.now(UTC).date()
        price_book = [
            # sku_code, 采购, 包装, 标准价, 指导价, 最低价, 目标利润率, 阶梯(min_qty, 标准/指导/最低)
            ("ZX-6040-B", 18, 2, 30, 29, 24, "0.30", (3000, 28, 27, 23)),
            ("ZX-6040-G", 18, 2, 30, 29, 24, "0.30", None),
            ("ZX-8050-B", 30, 3, 48, 46, 40, "0.30", None),
            ("ZKB-1208-5", 38, 4, 62, 60, 52, "0.28", (1000, 58, 56, 50)),
            ("ZKB-1208-8", 55, 5, 88, 85, 74, "0.28", None),
            ("CR-500-3", 9, 1, 15, Decimal("14.5"), 12, "0.25", None),
        ]
        sku_by_code = {
            sku.sku_code: sku for sku in (await session.execute(select(Sku))).scalars().all()
        }
        for code, purchase, package, standard, guide, minimum, margin, tier in price_book:
            sku = sku_by_code.get(code)
            if sku is None:
                continue
            has_cost = (
                await session.execute(select(ProductCost.id).where(ProductCost.sku_id == sku.id))
            ).first()
            if not has_cost:
                session.add(
                    ProductCost(
                        sku_id=sku.id,
                        purchase_cost=Decimal(purchase),
                        package_cost=Decimal(package),
                        effective_from=date(today.year, 1, 1),
                        currency="CNY",
                        remark="演示占位成本，待业务确认后替换",
                        created_by=user_map["admin"].id,
                    )
                )
            has_rule = (
                await session.execute(select(PriceRule.id).where(PriceRule.sku_id == sku.id))
            ).first()
            if has_rule:
                continue
            session.add(
                PriceRule(
                    sku_id=sku.id,
                    min_qty=Decimal(0),
                    standard_price=Decimal(standard),
                    guide_price=Decimal(guide),
                    minimum_price=Decimal(minimum),
                    target_margin=Decimal(margin),
                    effective_from=date(today.year, 1, 1),
                    status="active",
                    remark="演示占位价，待业务确认后替换",
                )
            )
            if tier:
                min_qty, tier_standard, tier_guide, tier_minimum = tier
                session.add(
                    PriceRule(
                        sku_id=sku.id,
                        min_qty=Decimal(min_qty),
                        standard_price=Decimal(tier_standard),
                        guide_price=Decimal(tier_guide),
                        minimum_price=Decimal(tier_minimum),
                        target_margin=Decimal(margin),
                        effective_from=date(today.year, 1, 1),
                        status="active",
                        remark=f"阶梯价：{min_qty} 以上",
                    )
                )

        # 11. 价格权限（占位值：业务员最低利润率 15%，主管 5% 且有审批权）
        permission_defs = [
            ("admin", "0.0", True, "管理员不受价格限制"),
            ("sales_manager", "0.05", True, "主管可审批低价报价"),
            ("salesperson", "0.15", False, "业务员低于 15% 利润率需审批"),
        ]
        for role_code, margin, can_approve, remark in permission_defs:
            role = role_map[role_code]
            exists = (
                await session.execute(
                    select(PricePermission).where(PricePermission.role_id == role.id)
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    PricePermission(
                        role_id=role.id,
                        minimum_margin=Decimal(margin),
                        discount_limit=Decimal("0.10"),
                        can_approve=can_approve,
                        status="active",
                        remark=remark,
                    )
                )

        # 12. 运费费率（占位值：陆运 0.9 元/公斤，最低 50 元；改过值会同步更新）
        rate = (await session.execute(select(LogisticsRate))).scalars().first()
        if rate is None:
            session.add(
                LogisticsRate(
                    provider="自有合作物流",
                    destination_region="全国（示例）",
                    shipping_method="陆运",
                    unit_price_per_kg=Decimal("0.9"),
                    min_charge=Decimal("50"),
                    eta_days=5,
                    status="active",
                )
            )
        else:
            rate.unit_price_per_kg = Decimal("0.9")
            rate.min_charge = Decimal("50")

        # 13. 客户特殊价（宏远包装 3000 件以上执行的一客一价）
        customer_for_price = (
            await session.execute(
                select(Customer).where(Customer.name == "宁波宏远包装制品有限公司")
            )
        ).scalar_one_or_none()
        sku_for_price = sku_by_code.get("ZX-6040-B")
        if customer_for_price and sku_for_price:
            exists = (
                await session.execute(
                    select(CustomerPriceRule).where(
                        CustomerPriceRule.customer_id == customer_for_price.id,
                        CustomerPriceRule.sku_id == sku_for_price.id,
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    CustomerPriceRule(
                        customer_id=customer_for_price.id,
                        sku_id=sku_for_price.id,
                        min_qty=Decimal(3000),
                        agreed_price=Decimal(27),
                        minimum_price=Decimal(23),
                        remark="演示用一客一价，待业务确认",
                    )
                )

        # 14. 业务规则与系统配置（占位值，管理员可在界面上直接改）
        pool_rules = [
            ("A", 30, "A 级客户 30 天未跟进回收"),
            ("B", 30, "B 级客户 30 天未跟进回收"),
            ("C", 60, "C 级客户 60 天未跟进回收"),
            ("D", 90, "D 级客户 90 天未跟进回收"),
        ]
        for level, days, remark in pool_rules:
            exists = (
                await session.execute(
                    select(PublicPoolRule).where(PublicPoolRule.level == level)
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    PublicPoolRule(level=level, days=days, enabled=True, remark=remark)
                )

        task_rule_defs = [
            {
                "code": "quote_no_followup",
                "name": "报价后未跟进提醒",
                "trigger_type": "quote_no_followup",
                "trigger_config": {"days": 3},
                "action_config": {"title": "报价已发出但还没有跟进", "type": "followup"},
            },
            {
                "code": "customer_silent",
                "name": "重点客户久未联系提醒",
                "trigger_type": "customer_silent",
                "trigger_config": {"days": 14, "levels": ["A"]},
                "action_config": {"title": "A 级客户超过 14 天未联系", "type": "followup"},
            },
            {
                "code": "receivable_due",
                "name": "应收即将到期提醒",
                "trigger_type": "receivable_due",
                "trigger_config": {"days": 7},
                "action_config": {"title": "应收到期提醒", "type": "payment"},
            },
        ]
        for item in task_rule_defs:
            exists = (
                await session.execute(
                    select(TaskRule).where(TaskRule.code == item["code"])
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(TaskRule(**item, status="active"))

        settings_defs = [
            ("quote_valid_days", {"days": 30}, "报价单默认有效期（天）"),
            ("default_payment_terms", {"text": "款到发货"}, "报价单默认付款条件"),
            ("default_delivery_terms", {"text": "含运费，送货上门"}, "报价单默认交货条件"),
            ("company_name", {"text": "曼德拉（示例，请替换为公司全称）"}, "报价单抬头公司名"),
            ("default_target_margin", {"ratio": 0.3}, "无价格规则时的默认目标利润率（0.3=30%）"),
            ("default_min_margin", {"ratio": 0.15}, "角色未配价格权限时的最低利润率（0.15=15%）"),
            ("price_range_ratio", {"ratio": 0.04}, "核价建议区间的上下浮动比例（0.04=±4%）"),
            ("customer_stale_days", {"days": 30}, "多久没跟进算「待跟进客户」（天）"),
            ("opportunity_risk_days", {"days": 7}, "预计成交日临近的阈值（天）"),
            ("opportunity_stale_days", {"days": 14}, "商机多久没更新算风险（天）"),
            (
                "approval_levels",
                {
                    "levels": [
                        {
                            "node": "manager",
                            "label": "销售主管",
                            "max_amount": 50000,
                            "role_codes": ["sales_manager"],
                        },
                        {
                            "node": "director",
                            "label": "销售经理",
                            "max_amount": None,
                            "role_codes": ["sales_manager"],
                        },
                    ]
                },
                "审批分级：报价总额不超过 max_amount 走到该级；max_amount 为 null 表示兜底",
            ),
            (
                "dedup_scoring",
                {
                    "threshold": 50,
                    "weight_name_exact": 70,
                    "weight_name_contains": 55,
                    "weight_mobile": 45,
                    "weight_tax_no": 60,
                    "weight_domain": 30,
                    "weight_address": 15,
                },
                "客户查重打分权重与阈值",
            ),
            ("trade_mode", {"mode": "domestic"}, "贸易模式：domestic 内贸 / export 外贸 / both 两者"),
            ("default_currency", {"code": "CNY"}, "报价默认币种"),
            ("export_tax_refund_rate", {"ratio": 0.0}, "出口退税率（内贸填 0）"),
        ]
        for key, value, description in settings_defs:
            exists = (
                await session.execute(
                    select(SystemSetting).where(SystemSetting.key == key)
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    SystemSetting(key=key, value=value, description=description)
                )

        await session.commit()
        print(
            "种子数据完成：3 个角色、权限清单、3 个账号、3 个示例客户、3 个示例产品、"
            "9 个商机阶段、9 类失单原因、2 条示例线索、1 个示例商机"
            "、成本与价格规则（占位值）、价格权限、运费费率"
        )
        print("登录账号：admin / admin123     zhangsan / 123456     lisi / 123456")


if __name__ == "__main__":
    asyncio.run(seed())
