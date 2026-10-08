"""部分更新（PATCH）入参的公共基类 + "不许清空 / 长度上限"的统一判据（第十一批 11.9）。

## 要分清的三件事

更新类入参有三种情况，**必须分开**：

1. **没传这个字段** → 保持原值（`exclude_unset` 语义，一直是对的）；
2. **可空字段传 `null`** → 允许清空（备注、描述、行业、来源、职务……）；
3. **必填字段显式传 `null`** → **参数校验阶段就拒绝**。原来走到数据库才撞非空约束，
   用户看到的是 500「服务器内部错误」—— 而它其实只是一句"这个名字不能为空"。

## 判据从哪来：**数据库列定义**，不是人工印象

2026-10-08 复审（11.9）实测，上一版把 `是否非空` 与 `最大长度` **都人工写在一张表里**，
结果两个方向都错：

- 库里**可空**的字段（客户行业/来源/等级、联系人职务、SKU 名称、商机来源/风险等级、
  用户企微账号）被当成必填 —— 用户想清空一个**允许清空**的字段，系统说"不能为空"；
- 库里**非空**的字段（联系人 `is_primary`、价格规则 `min_qty`、成本 `effective_from`）
  压根没登记 —— 传 `null` 照样 500。

所以现在**人工只写一件事：哪张表**（`PATCH_TABLES`）**和报错时的中文名**（`FIELD_LABELS`）。
`是否不许清空` 与 `最大长度` 一律**现场从 `Base.metadata` 的列定义读**
（`nullable` / `String(n)` 的 `length`）。
人工登记与真实列**不可能再对不上** —— 因为压根没有再登记一次。

字段清单也是**自动扫**的（类自己的字段 ∩ 表里的列），不人工列一遍：
漏写一个字段名最多让提示显示成英文列名，**不会让校验失效**。

## 长度与"不许清空"是两件事

可空字段**也有长度上限**（`varchar(128)` 就是 128），不能因为"允许清空"就跳过长度检查；
反过来，也不能为了查长度而禁止清空。两个判据各自独立。

## "不许 null"与"不许空白"也是两件事（2026-10-08 补修）

`nullable=False` 只说明**不能存 null**，推不出"业务上不能存空串"。编号规则就是反例：
`prefix` 留空 = 不带前缀、`date_format` 留空 = 不带日期，取号逻辑本来就支持；可公共
校验按列定义一刀切，于是"新建规则传 `""` 能过、编辑传 `{"prefix": ""}` 却报 400
「编号前缀不能为空」"—— 同一条业务规则，两个入口两个答案。

现在拆开：**不许 null** 看列定义（非空列照拦 null），**必须有内容** 看
`BLANK_ALLOWED_FIELDS`（需要放行的字段登记在那里）。默认从严：漏登记最多是
"少放行一个字段"，不会静默把校验放宽。
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, model_validator

from app.core.base import Base

#: Update 类名 → 它对应的**表名**（取列定义用）。
#: 写在一处而不是散在二十多个 schema 文件里：一处看全，加新类时不容易忘。
#: ⚠️ **没有独立表的类不给它表名**（如 `CustomerLevelUpdate` —— 等级定义走可维护的
#: 设置项，库里没有这张表），这类只能靠 `REQUIRED_FIELDS` 人工兜底。
PATCH_TABLES: dict[str, str] = {
    "ApprovalDefinitionUpdate": "approval_definitions",
    "CaseUpdate": "sales_cases",
    "ContactUpdate": "contacts",
    "CostUpdate": "product_costs",
    "CustomInquiryUpdate": "custom_inquiries",
    "CustomerUpdate": "customers",
    "DepartmentUpdate": "departments",
    "DictionaryItemUpdate": "dictionary_items",
    "FollowUpUpdate": "followups",
    "InsightUpdate": "product_insights",
    "LeadUpdate": "leads",
    "LogisticsRateUpdate": "logistics_rates",
    "LossReasonUpdate": "loss_reasons",
    "NumberingRuleUpdate": "numbering_rules",
    "OpportunityUpdate": "opportunities",
    "OrderDraftUpdate": "order_drafts",
    "PriceRuleUpdate": "price_rules",
    "ProductUpdate": "products",
    "PublicPoolRuleUpdate": "public_pool_rules",
    "QuoteChargeUpdate": "quote_charges",
    "ReceivableUpdate": "receivable_plans",
    "RoleUpdate": "roles",
    "SkuUpdate": "skus",
    "StageUpdate": "opportunity_stages",
    "TaskRuleUpdate": "task_rules",
    "TaskUpdate": "tasks",
    "UserUpdate": "users",
}

#: `类名.字段名` → 报错时用的中文名。**没写的用字段名兜底**，
#: 所以漏写只影响提示好不好看，不影响校验有没有生效。
FIELD_LABELS: dict[str, str] = {
    # 产品 / SKU
    "ProductUpdate.name": "产品名称",
    "ProductUpdate.status": "产品状态",
    "SkuUpdate.sku_code": "SKU 编码",
    "SkuUpdate.name": "SKU 名称",
    "SkuUpdate.status": "SKU 状态",
    # 客户 / 联系人
    "CustomerUpdate.name": "客户名称",
    "CustomerUpdate.domain": "行业",
    "CustomerUpdate.source": "客户来源",
    "CustomerUpdate.level": "客户等级",
    "CustomerUpdate.status": "客户状态",
    "ContactUpdate.name": "联系人姓名",
    "ContactUpdate.title": "职务",
    "ContactUpdate.is_primary": "是否主联系人",
    # 线索 / 商机 / 阶段 / 失单原因
    "LeadUpdate.name": "线索名称",
    "LeadUpdate.source": "线索来源",
    "OpportunityUpdate.title": "商机名称",
    "OpportunityUpdate.source": "商机来源",
    "OpportunityUpdate.risk_level": "风险等级",
    "StageUpdate.name": "阶段名称",
    "StageUpdate.status": "阶段状态",
    "LossReasonUpdate.name": "失单原因",
    "LossReasonUpdate.status": "状态",
    # 商机需求明细（新增/编辑/批量三个入口共用同一份入参）。
    # 这两个类**不是** PatchModel（它们是新建用的完整入参），登记在这里只是因为
    # `core/errors.py` 会用这张表把校验错误翻成人话，而这张表是项目里**唯一**
    # 一张字段中文名表 —— 再建一张迟早会分叉。
    "OpportunityItemCreate.sku_id": "SKU",
    "OpportunityItemCreate.quantity": "数量",
    "OpportunityItemCreate.target_price": "目标价",
    "OpportunityItemUpdate.quantity": "数量",
    "OpportunityItemUpdate.target_price": "目标价",
    # 任务 / 案例 / 询价 / 情报
    "TaskUpdate.title": "任务标题",
    "TaskUpdate.priority": "优先级",
    "CaseUpdate.title": "案例标题",
    "CustomInquiryUpdate.title": "询价标题",
    "CustomInquiryUpdate.status": "询价状态",
    "InsightUpdate.title": "情报标题",
    "InsightUpdate.source": "来源",
    "InsightUpdate.direction": "方向",
    # 审批流 / 部门 / 角色 / 用户 / 字典
    "ApprovalDefinitionUpdate.name": "审批流名称",
    "ApprovalDefinitionUpdate.business_type": "业务类型",
    "ApprovalDefinitionUpdate.status": "状态",
    "DepartmentUpdate.name": "部门名称",
    "DepartmentUpdate.status": "部门状态",
    "RoleUpdate.name": "角色名称",
    "RoleUpdate.data_scope": "数据范围",
    "RoleUpdate.status": "角色状态",
    "UserUpdate.name": "姓名",
    "UserUpdate.wecom_userid": "企微账号",
    "DictionaryItemUpdate.label": "字典项名称",
    # 编号规则 / 任务规则 / 公海规则
    "NumberingRuleUpdate.name": "编号规则名称",
    "NumberingRuleUpdate.prefix": "编号前缀",
    "NumberingRuleUpdate.date_format": "日期格式",
    "NumberingRuleUpdate.reset_period": "重置周期",
    "TaskRuleUpdate.code": "规则编码",
    "TaskRuleUpdate.name": "规则名称",
    "TaskRuleUpdate.trigger_type": "触发方式",
    "TaskRuleUpdate.status": "规则状态",
    "PublicPoolRuleUpdate.level": "客户等级",
    # 报价 / 跟进 / 价格 / 订单草稿 / 应收 / 运费
    "QuoteChargeUpdate.charge_type": "费用类型",
    "FollowUpUpdate.content": "跟进内容",
    "FollowUpUpdate.followup_type": "跟进方式",
    "PriceRuleUpdate.status": "价格规则状态",
    "PriceRuleUpdate.min_qty": "最小数量",
    "PriceRuleUpdate.max_qty": "最大数量",
    "PriceRuleUpdate.effective_from": "生效日期",
    "PriceRuleUpdate.effective_to": "截止日期",
    "OrderDraftUpdate.currency": "币种",
    "ReceivableUpdate.plan_name": "应收节点名称",
    "CostUpdate.effective_from": "生效日期",
    "CostUpdate.effective_to": "截止日期",
    "LogisticsRateUpdate.provider": "承运方式",
    "LogisticsRateUpdate.shipping_method": "运输方式",
    "LogisticsRateUpdate.unit_price_per_kg": "公斤单价",
    "LogisticsRateUpdate.min_charge": "最低收费",
    "LogisticsRateUpdate.status": "费率状态",
}

#: **没有独立表**的类，只能人工兜底"哪些字段不许清空"（取不到列定义，也就不查长度）。
REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    # 客户等级的定义走了可维护的设置项，库里没有这张表
    "CustomerLevelUpdate": frozenset({"name"}),
}

#: **允许存空字符串**的字段（写成 `类名.字段名`）。
#:
#: 第十一批 11.9 补修新增。数据库那一列 `nullable=False` 只说明**不能存 null**，
#: 推不出"业务上不能存空串"—— 编号规则就是现成的例子：`prefix` 留空 = 不带前缀、
#: `date_format` 留空 = 不带日期（模型注释与取号逻辑都是这个口径）。但公共校验
#: 按列定义一刀切，于是"新建规则传 `""` 能过、编辑传 `{"prefix": ""}` 却报
#: 400「编号前缀不能为空」"，同一条业务规则前后自相矛盾。
#:
#: 所以拆成两件事：**不许 null** 仍从列定义读（非空列照拦），
#: **必须有内容** 改成这份独立规则 —— 默认跟着"非空列"走，需要放行的登记在这里。
#: 默认从严：漏登记最多是"少放行一个字段"，不会静默把校验放宽。
BLANK_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "NumberingRuleUpdate.prefix",
    "NumberingRuleUpdate.date_format",
})

#: 规则四元组：`(中文名, 是否不许 null, 是否必须有内容, 长度上限)`。
#: 第二、三项是**两件事**：`not_null` 看列定义；`need_content` 看
#: `BLANK_ALLOWED_FIELDS`（非字符串字段的 `need_content` 不起作用）。
Rule = tuple[str, bool, bool, int | None]


def _rules_for(cls: type[BaseModel]) -> dict[str, Rule]:
    """算出这个更新类要守的字段规则。

    - **字段清单**：类自己的字段（`model_fields`）里，**在那张表里找得到同名列**的那些
      —— 不人工列一遍，所以"漏登记一个字段"这种事不会再发生；
    - **是否不许 null**：看库列的 `nullable`（不是"看起来像必填"）；
    - **是否必须有内容**：默认跟着上一条走（非空列要求有内容），
      但 `BLANK_ALLOWED_FIELDS` 里登记的字段放行空串 —— 两件事分开，见该常量的说明；
    - **长度上限**：看库列的 `String(n)` / `varchar(n)`。

    表里没有同名列的字段（如案例的 `evidences`、草稿的 `items` 这类虚拟字段）
    不参与校验 —— 它们本来就不是直接落库的列。
    """
    name = cls.__name__
    table_name = PATCH_TABLES.get(name)
    if table_name is None:
        # 没有独立表：只按人工登记做"不许清空"，不查长度
        return {
            field: (FIELD_LABELS.get(f"{name}.{field}", field), True, True, None)
            for field in REQUIRED_FIELDS.get(name, frozenset())
        }
    table = Base.metadata.tables.get(table_name)
    if table is None:
        return {}
    rules: dict[str, Rule] = {}
    for field in cls.model_fields:
        column = table.columns.get(field)
        if column is None or column.primary_key:
            continue
        length = getattr(column.type, "length", None)
        not_null = not column.nullable
        rules[field] = (
            FIELD_LABELS.get(f"{name}.{field}", field),
            not_null,
            # 「必须有内容」是**独立**判据（11.9 补修）：默认跟"不许 null"走，
            # 登记为可空的字段（编号规则的前缀 / 日期格式）放行空串。
            not_null and f"{name}.{field}" not in BLANK_ALLOWED_FIELDS,
            length if isinstance(length, int) else None,
        )
    return rules


class PatchModel(BaseModel):
    """部分更新入参的公共基类。继承它即自动受"不许清空 + 长度上限"保护。

    判据**现场从数据库列定义读**（见 `_rules_for`），所以人工登记与真实列不会分叉。
    子类可以用 `REQUIRED_RULES` 手工追加（**只给没有独立表的类用**）。
    """

    model_config = ConfigDict(extra="ignore")

    #: 手工兜底：`{字段名: 中文名}`，与自动规则合并（自动的优先）。
    REQUIRED_RULES: ClassVar[dict[str, str] | None] = None

    @model_validator(mode="after")
    def _guard_blank_and_length(self):
        rules = dict(_rules_for(type(self)))
        for field, label in (self.REQUIRED_RULES or {}).items():
            rules.setdefault(field, (label, True, True, None))
        for field, (label, not_null, need_content, max_len) in rules.items():
            # ① 没传 → 保持原值，放行（这是"部分更新"的本意）
            if field not in self.model_fields_set:
                continue
            value = getattr(self, field, None)
            # ② 传 null：库里非空就拒（原来会走到数据库才撞非空约束 → 500）；
            #    可空字段传 null = 允许清空。
            if value is None:
                if not_null:
                    raise ValueError(
                        f"{label}不能为空。要改就给它一个非空值；不打算改这个字段就别传它"
                    )
                continue
            # ③ 「必须有内容」与「不许 null」是**两件事**（11.9 补修）：名称、标题这类
            #    真正必填的文字仍拦空串与纯空白；编号规则的前缀 / 日期格式允许 `""`
            #    （留空 = 不带前缀 / 不带日期，模型与取号逻辑本来就这么定义）。
            if need_content and isinstance(value, str) and not value.strip():
                raise ValueError(
                    f"{label}不能为空。要改就给它一个非空值；不打算改这个字段就别传它"
                )
            # ④ 超长也在这里拦：越过它只会撞库列上限（"value too long for type
            #    character varying(N)"），用户看到的同样是 500。
            #    这一档**与上面两档都无关** —— 可空字段照样有长度上限，
            #    允许留空的字段也照样有上限。
            if max_len is not None and isinstance(value, str) and len(value) > max_len:
                raise ValueError(f"{label}最长 {max_len} 个字符（当前 {len(value)} 个）")
        return self


def verify_patch_registry() -> list[str]:
    """自查登记表，返回问题清单（给测试 / 启动自检用，不在请求路径上）。

    上一版只有一个"类名存在"的检查 —— 那**查不出字段登记错误**，而这正是复审
    发现的问题（客户行业/来源/等级被当成必填、联系人 `is_primary` 等真非空字段
    压根没登记）。现在字段级判据全部来自列定义，所以这里只查"**登记本身写错**"：

    1. `PATCH_TABLES` 里的类名在代码里找得到；
    2. `PATCH_TABLES` 里登记的表名在 `Base.metadata` 里存在；
    3. `REQUIRED_FIELDS` 里的类名找得到；
    4. 每个登记了表名的类，`_rules_for` 至少要能算出一条规则
       （一条都算不出来 = 表名或字段名整体对不上，等于整类没受保护）。
    """
    import importlib
    import pkgutil

    import app.modules  # noqa: F401

    problems: list[str] = []

    classes: dict[str, type[BaseModel]] = {}
    for module_info in pkgutil.walk_packages(app.modules.__path__, "app.modules."):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:  # noqa: BLE001 —— 导入失败有别的检查管
            continue
        for value in vars(module).values():
            if (isinstance(value, type) and issubclass(value, PatchModel)
                    and value is not PatchModel):
                classes[value.__name__] = value

    for cls_name, table_name in PATCH_TABLES.items():
        if cls_name not in classes:
            problems.append(f"PATCH_TABLES 登记了 {cls_name}，但代码里没有这个类")
            continue
        if table_name not in Base.metadata.tables:
            problems.append(f"{cls_name} 登记的表 {table_name} 不在 metadata 里")
            continue
        if not _rules_for(classes[cls_name]):
            problems.append(
                f"{cls_name}（表 {table_name}）算不出任何校验规则 —— 字段名整体对不上？"
            )

    for cls_name in REQUIRED_FIELDS:
        if cls_name not in classes:
            problems.append(f"REQUIRED_FIELDS 登记了 {cls_name}，但代码里没有这个类")

    # 5. 放行空白的那份登记也要对得上：写错一个字段名 = 该字段仍然被拦空白
    #    （症状是"明明登记了还是报 400"），所以自查一条。
    for key in BLANK_ALLOWED_FIELDS:
        cls_name, _, field = key.partition(".")
        if cls_name not in classes:
            problems.append(f"BLANK_ALLOWED_FIELDS 登记了 {key}，但代码里没有 {cls_name}")
        elif field not in classes[cls_name].model_fields:
            problems.append(f"BLANK_ALLOWED_FIELDS 登记的 {key} 不在 {cls_name} 里")

    return sorted(problems)


__all__ = [
    "BLANK_ALLOWED_FIELDS",
    "FIELD_LABELS",
    "PATCH_TABLES",
    "REQUIRED_FIELDS",
    "PatchModel",
    "verify_patch_registry",
]
