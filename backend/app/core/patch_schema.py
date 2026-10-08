"""部分更新（PATCH）入参的公共基类 + "不许被清空"的集中登记表（第十一批 11.9）。

## 要分清的三件事

更新类入参有三种情况，**必须分开**：

1. **没传这个字段** → 保持原值（`exclude_unset` 语义，一直是对的）；
2. **可清空字段传 `null`** → 允许清空（备注、描述、知识库这类）；
3. **必填字段显式传 `null`** → **参数校验阶段就拒绝**。原来走到数据库才撞非空约束，
   用户看到的是 500「服务器内部错误」——而它其实只是一句"这个名字不能为空"。

## 为什么用 `model_fields_set`

`Optional` 字段在模型里"没传"和"传了 null"**都是 `None`**，光看值分不出来。
pydantic 为此给了 `model_fields_set`：字段在集合里 = 调用方显式传了（含 null）。
少了它就只能猜，而猜错的方向恰好是"把用户想清空的字段当成没传"。

## 为什么登记表是集中一张

"哪些字段库里非空"这件事有 40 多处，散在每个 schema 文件里各写一遍，
必然漏、也看不出全貌。集中在这一张表里：一处看全、加新类时不容易忘。
代价是类名要写对 —— 表里写了但类不存在的，`_verify_registry()` 会当场报出来
（只在测试/启动自检里调，不在请求路径上）。

## 不在这里做的事

**不禁止所有 null**：备注、描述、知识库、`PaymentUpdate.currency`
（`null` = "按应收节点的币种重新对齐"，是明确语义）这些都必须继续可清空/可空传。
所以只登记"库里非空、清空没有业务意义"的那些字段。
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, model_validator


#: 类名 → {字段名: (中文名, 库列最大长度)}。
#: 判据是**数据库列非空**（不是"看起来像必填"）：这些字段传 `null` 从前必 500。
#: 长度取**库列的实际上限**（`None` = text 无上限）：超长从前也是 500
#: （"value too long for type character varying(N)"），一并在这里拦住。
NOT_NULLABLE: dict[str, dict[str, tuple[str, int | None]]] = {
    "ProductUpdate": {"name": ("产品名称", 200), "status": ("产品状态", 32)},
    "SkuUpdate": {"sku_code": ("SKU 编码", 64), "name": ("SKU 名称", 200), "status": ("SKU 状态", 32)},
    "CustomerUpdate": {"name": ("客户名称", 200), "domain": ("行业", 128), "source": ("客户来源", 32), "level": ("客户等级", 8), "status": ("客户状态", 32)},
    "ContactUpdate": {"name": ("联系人姓名", 64), "title": ("职务", 64)},
    "LeadUpdate": {"name": ("线索名称", 200), "source": ("线索来源", 32)},
    "OpportunityUpdate": {"title": ("商机名称", 200), "source": ("商机来源", 32), "risk_level": ("风险等级", 16)},
    "StageUpdate": {"name": ("阶段名称", 64), "status": ("阶段状态", 32)},
    "LossReasonUpdate": {"name": ("失单原因", 64), "status": ("状态", 32)},
    "TaskUpdate": {"title": ("任务标题", 200), "priority": ("优先级", 16)},
    "CaseUpdate": {"title": ("案例标题", 200)},  # 表名是 sales_cases
    "CustomInquiryUpdate": {"title": ("询价标题", 200), "status": ("询价状态", 16)},
    "InsightUpdate": {"title": ("情报标题", 200), "source": ("来源", 64), "direction": ("方向", None)},
    "ApprovalDefinitionUpdate": {"name": ("审批流名称", 128), "business_type": ("业务类型", 64), "status": ("状态", 16)},
    # 客户等级没有独立的表（等级定义走了可维护的设置项），取不到列长：
    # 长度留 None = 只查"不许清空"，不查长度（宁可不查，也别按猜的长度误拒）
    "CustomerLevelUpdate": {"name": ("客户等级名称", None)},
    "DepartmentUpdate": {"name": ("部门名称", 128), "status": ("部门状态", 32)},
    "RoleUpdate": {"name": ("角色名称", 64), "data_scope": ("数据范围", 32), "status": ("角色状态", 32)},
    "UserUpdate": {"name": ("姓名", 64), "wecom_userid": ("企微账号", 64)},
    "DictionaryItemUpdate": {"label": ("字典项名称", 128)},
    "NumberingRuleUpdate": {"name": ("编号规则名称", 64), "prefix": ("编号前缀", 16), "date_format": ("日期格式", 32), "reset_period": ("重置周期", 16)},
    "TaskRuleUpdate": {"code": ("规则编码", 64), "name": ("规则名称", 128), "trigger_type": ("触发方式", 32), "status": ("规则状态", 16)},
    "PublicPoolRuleUpdate": {"level": ("客户等级", 8)},
    "QuoteChargeUpdate": {"charge_type": ("费用类型", 32)},
    "FollowUpUpdate": {"content": ("跟进内容", None), "followup_type": ("跟进方式", 32)},
    "PriceRuleUpdate": {"status": ("价格规则状态", 16)},
    "OrderDraftUpdate": {"currency": ("币种", 8)},
    "ReceivableUpdate": {"plan_name": ("应收节点名称", 64)},
    # 运费费率（价格中心「运费费率」的「改」）。后两个是 Numeric 列，只查"不许清空"。
    # ⚠️ `origin_region` / `destination_region` **刻意不在这里**：它们允许清空，
    # 而且"留空 = 不限"是有意义的语义（写「全国」在匹配里是个**具体取值**，不是不限）。
    "LogisticsRateUpdate": {
        "provider": ("承运方式", 64),
        "shipping_method": ("运输方式", 32),
        "unit_price_per_kg": ("公斤单价", None),
        "min_charge": ("最低收费", None),
        "status": ("费率状态", 16),
    },
}



class PatchModel(BaseModel):
    """部分更新入参的公共基类。继承它即自动受 `NOT_NULLABLE` 保护。"""

    model_config = ConfigDict(extra="ignore")

    #: 子类可自行覆盖（一般不用）；表里查不到就什么都不做。
    REQUIRED_RULES: ClassVar[dict[str, tuple[str, int | None]] | None] = None

    @model_validator(mode="after")
    def _forbid_blank_required(self):
        rules = self.REQUIRED_RULES or NOT_NULLABLE.get(type(self).__name__) or {}
        for name, (label, max_len) in rules.items():
            # ① 没传 → 保持原值，放行（这是"部分更新"的本意）
            if name not in self.model_fields_set:
                continue
            value = getattr(self, name, None)
            # ② 传了 null，或传了空串/纯空白 → 都等于"把它清空了"，而它是必填
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(
                    f"{label}不能为空。要改就给它一个非空值；不打算改这个字段就别传它"
                )
            # ③ 超长也在这里拦：越过它只会撞库列上限（"value too long for type
            #    character varying(N)"），用户看到的同样是 500。
            #    `max_len=None` = 该列是 text 或取不到长度，不查。
            if max_len is not None and isinstance(value, str) and len(value) > max_len:
                raise ValueError(f"{label}最长 {max_len} 个字符（当前 {len(value)} 个）")
        return self


def _verify_registry() -> list[str]:
    """自查登记表：返回"登记了但类不存在"的类名（给测试/启动自检用）。

    为什么要有它：这张表靠**类名字符串**关联，写错一个字母就会静默失效 ——
    而失效的表现是"校验没生效"，不跑一遍根本看不出来。
    """
    import importlib
    import pkgutil

    import app.modules  # noqa: F401

    seen: set[str] = set()
    for module_info in pkgutil.walk_packages(app.modules.__path__, "app.modules."):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:  # noqa: BLE001 —— 导入失败不在这里报（有别的检查管）
            continue
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, BaseModel):
                seen.add(value.__name__)
    return sorted(name for name in NOT_NULLABLE if name not in seen)
