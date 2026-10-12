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
    "OpportunityItemUpdate": "opportunity_items",
    "OrderDraftUpdate": "order_drafts",
    "PriceRuleUpdate": "price_rules",
    "ProductUpdate": "products",
    "PublicPoolRuleUpdate": "public_pool_rules",
    "QuoteChargeUpdate": "quote_charges",
    "QuoteItemInput": "quote_items",
    "QuoteItemUpdate": "quote_items",
    "PaymentUpdate": "payment_records",
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
    # 客户标签（审查 P3：标签名超长时提示写成了「产品名称」—— 因为表里没有
    # Tag 条目，模糊匹配命中了别的模型的 `name` 登记。补上本模型的登记。）
    "TagCreate.name": "标签名称",
    "TagUpdate.name": "标签名称",
    "TagUpdate.type": "标签分组",
    "TagUpdate.status": "标签状态",
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
    # 商机需求明细（新增、编辑、批量入口共用字段提示）。
    # 新增类使用完整入参；编辑类复用 PatchModel 的非空列校验。登记在这里还因为
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
    "QuoteChargeInput.amount": "金额",
    "QuoteChargeInput.charge_type": "费用类型",
    "QuoteItemInput.quantity": "数量",
    "QuoteItemInput.quoted_price": "报价单价",
    "QuoteItemInput.unit_cost": "核价成本",
    "QuoteItemInput.logistics_cost": "单件运费",
    "QuoteItemUpdate.quantity": "数量",
    "QuoteItemUpdate.quoted_price": "报价单价",
    "QuoteItemUpdate.unit_cost": "核价成本",
    "QuoteItemUpdate.logistics_cost": "单件运费",
    "FollowUpUpdate.content": "跟进内容",
    "FollowUpUpdate.followup_type": "跟进方式",
    "PriceRuleUpdate.status": "价格规则状态",
    "PriceRuleCreate.remark": "备注",
    "PriceRuleUpdate.remark": "备注",
    "CustomerPriceCreate.remark": "备注",
    "CustomerPriceUpdate.remark": "备注",
    "PriceRuleUpdate.min_qty": "最小数量",
    "PriceRuleUpdate.max_qty": "最大数量",
    "PriceRuleUpdate.effective_from": "生效日期",
    "PriceRuleUpdate.effective_to": "截止日期",
    "OrderDraftUpdate.currency": "币种",
    "ReceivableUpdate.plan_name": "应收节点名称",
    "PaymentUpdate.received_amount": "回款金额",
    "PaymentUpdate.received_date": "收款日期",
    "PaymentUpdate.payment_method": "收款方式",
    "PaymentUpdate.voucher_note": "凭证说明",
    "PaymentUpdate.currency": "币种",
    "CostUpdate.effective_from": "生效日期",
    "CostUpdate.effective_to": "截止日期",
    "LogisticsRateUpdate.provider": "承运方式",
    "LogisticsRateUpdate.shipping_method": "运输方式",
    "LogisticsRateUpdate.unit_price_per_kg": "公斤单价",
    "LogisticsRateUpdate.min_charge": "最低收费",
    "LogisticsRateUpdate.status": "费率状态",

    # ==================================================================
    # 审计日志展示用（2026-10-12 第四轮，主人：「审计日志也说一堆不是人话」）
    #
    # 为什么加在这里：`错误提示` 与 `审计展示` 都要"字段名 → 中文名"，而这张表是
    # 项目里**唯一**一张字段中文名表（它的注释写着"再建一张迟早会分叉"）。
    # 加在这一处的代价是零：这张表只影响**文案**，漏登记最多让提示显示成英文列名，
    # 不影响任何校验或写入（见本文件开头第 2 条说明）。
    #
    # 键用 `字段名`（不带模型前缀）：审计快照里只有一个裸字段名 + 对象类型，
    # 拿不到"是哪个 Update 模型"。同名不同义的 4 个字段（name/source/status/title）
    # **故意不在这里登记** —— 由前端按"这个字段属于哪类对象"去定名，
    # 免得把"客户名称"显示成"产品名称"（审查报告记过同类事故）。
    # ==================================================================
    # 订单
    "order_no": "订单号",
    "goods_amount": "货款金额",
    "logistics_amount": "运费金额",
    "other_charge_amount": "其他费用",
    "discount_amount": "折扣金额",
    "total_amount": "订单总额",
    "pricing_basis": "计价口径",
    "erp_order_id": "ERP/MES 单号",
    "delivery_date": "计划交期",
    "delivery_kind": "交期口径",
    "transit_days": "运输天数",
    "plan_offsets": "跟单节点提前天数",
    "payment_terms": "付款条件",
    "sales_owner_id": "签单归属",
    "cancelled_at": "取消时间",
    # 报价单 / 版本 / 明细 / 附加费用
    "quote_no": "报价单号",
    "current_version_id": "当前版本",
    "valid_until": "有效期",
    "subtotal_amount": "货款小计",
    "charge_amount": "附加费用合计",
    "exchange_rate_snapshot": "汇率快照",
    "exchange_rate_source": "汇率来源",
    "exchange_rate_time": "取汇率时间",
    "trade_terms": "贸易条款",
    "delivery_terms": "交货条款",
    "customer_name_snapshot": "客户抬头快照",
    "contact_name_snapshot": "联系人快照",
    "valid_until_snapshot": "有效期快照",
    "approval_status": "审批状态",
    "approval_required": "需审批",
    "approval_reason": "需审批原因",
    "submitted_at": "提交审批时间",
    "approved_at": "审批通过时间",
    "sent_at": "发送时间",
    "accepted_at": "客户接受时间",
    "declined_at": "客户拒绝时间",
    "inquiry_no_snapshot": "需求编号快照",
    "sku_code_snapshot": "产品编码快照",
    "sku_name_snapshot": "产品名称快照",
    "spec_snapshot": "规格快照",
    "unit_snapshot": "单位快照",
    "master_version_no": "引用的主数据版本",
    "cost_snapshot": "成本快照",
    "package_cost_snapshot": "包装成本快照",
    "logistics_cost_snapshot": "运费快照",
    "standard_price_snapshot": "标准价快照",
    "recommended_price_snapshot": "建议价快照",
    "minimum_price_snapshot": "最低保护价快照",
    "customer_level_snapshot": "客户等级快照",
    "profit_snapshot": "毛利快照",
    "profit_rate_snapshot": "毛利率快照",
    "tax_refund_snapshot": "退税快照",
    "profit_with_refund_snapshot": "含退税毛利快照",
    "price_source": "价格来源",
    "quoted_price": "报价单价",
    "is_discount": "是否折扣",
    "sort_no": "排序号",
    "logistics_confirmed_at": "运费确认时间",
    # 客户 / 联系人 / 标签
    "short_name": "客户简称",
    "customer_type": "客户类型",
    "country": "国家",
    "region": "地区",
    "address": "地址",
    "tax_no": "税号",
    "pool_status": "公海状态",
    "last_followup_at": "最近跟进时间",
    "last_contact_unknown": "最近联系人不明",
    "last_progress_at": "最近推进时间",
    "next_followup_at": "下次跟进时间",
    "department": "部门",
    "mobile": "手机",
    "phone": "座机",
    "email": "邮箱",
    "wechat": "微信号",
    "external_userid": "企微外部联系人",
    "is_primary": "是否主联系人",
    "tag": "标签",
    # 线索
    "company_name": "公司名称",
    "contact_name": "联系人",
    "source_detail": "来源说明",
    "converted_customer_id": "转成的客户",
    "converted_contact_id": "转成的联系人",
    "converted_opportunity_id": "转成的商机",
    "invalid_reason": "判无效原因",
    # 商机 / 阶段
    "primary_contact_id": "主要联系人",
    "stage_id": "所处阶段",
    "expected_amount": "预计金额",
    "expected_close_date": "预计成交日",
    "competitor": "竞争对手",
    "next_action": "下一步动作",
    "win_quote_version_id": "赢单报价版本",
    "loss_reason_id": "失单原因",
    "loss_remark": "失单说明",
    "reopen_at": "重新打开时间",
    "sequence": "排序",
    "is_win": "是否赢单",
    "is_loss": "是否失单",
    # 跟进 / 任务
    "customer_feedback": "客户反馈",
    "planned_at": "计划时间",
    "exemption_reason": "免填原因",
    "next_task_id": "生成的任务",
    "task_type": "任务类型",
    "due_at": "到期时间",
    "source_rule_id": "来源规则",
    "source_business_type": "来源对象类型",
    "source_business_id": "来源对象",
    "source_key": "来源标识",
    "completed_at": "完成时间",
    "completion_note": "完成说明",
    # 询价（定制需求）
    "inquiry_no": "需求编号",
    "description": "需求描述",
    "revision_note": "本版说明",
    "superseded_at": "被取代时间",
    "converted_sku_id": "转成的 SKU",
    "origin": "来源",
    "source_insight_id": "来源洞察",
    "oa_quote_user_id": "钉钉报价人",
    "oa_quote_user_name": "钉钉报价人",
    "extra": "补充信息",
    # 打样
    "review_round": "审批轮次",
    "review_request_key": "审批请求键",
    "reject_reason": "驳回原因",
    "purpose": "打样用途",
    "target_completion_date": "要求完成日",
    "acceptance_criteria": "验收标准",
    "sample_fee": "打样费",
    "production_owner_id": "生产负责人",
    "made_at": "完成打样时间",
    "made_events": "打样过程记录",
    "basis_files": "制作依据",
    "confirm_status": "客户确认状态",
    "customer_confirmed_at": "客户确认时间",
    "confirm_remark": "确认说明",
    "requested_at": "申请时间",
    "shipped_at": "寄出时间",
    "signed_at": "签收时间",
    "feedback": "客户反馈",
    "original_quantity": "原采购数量",
    "item_name": "品名",
    "craft": "工艺",
    "material": "材质",
    "drawing_version": "图纸版本",
    # 订单草稿 / 跟单节点
    "node": "节点",
    "planned_date": "计划日期",
    "actual_date": "实际日期",
    "evidence": "凭据",
    "overdue_reason": "逾期原因",
    "skip_reason": "跳过原因",
    "skipped_by": "跳过操作人",
    "skipped_at": "跳过时间",
    "overdue_notified_at": "逾期通知时间",
    # 回款 / 应收
    "receivable_plan_id": "应收节点",
    "voucher_file_id": "回款凭证",
    "confirmed_by": "确认人",
    "confirmed_at": "确认时间",
    "plan_name": "应收节点名称",
    "due_date": "到期日",
    # 合同 / 单据
    "doc_no": "单据编号",
    "doc_type": "单据类型",
    "renderer_version": "渲染器版本",
    "template_key": "模板",
    "template_id": "模板",
    "template_version": "模板版本",
    "void_reason": "作废原因",
    "effective_date": "生效日",
    "expiry_date": "到期日",
    "supersede_parent": "替代旧协议",
    "parent_id": "父单据",
    "source_type": "来源类型",
    "source_id": "来源对象",
    "source_no": "来源编号",
    "source_version": "来源版本",
    "input_snapshot": "生成时输入",
    "content_sha256": "内容校验值",
    "file_id": "文件",
    "file_sha256": "文件校验值",
    "file_size": "文件大小",
    "sample_request_id": "打样单",
    "order_draft_id": "订单草稿",
    # 产品 / SKU / 成本
    "product_id": "所属产品",
    "product_line": "产品线",
    "category": "分类",
    "brand": "品牌",
    "knowledge": "产品知识",
    "specification": "规格",
    "color": "颜色",
    "length": "长",
    "width": "宽",
    "height": "高",
    "weight": "重量",
    "carton_qty": "装箱数量",
    "carton_volume": "装箱体积",
    "moq": "起订量",
    "package_type": "包装方式",
    "unit": "单位",
    "purchase_cost": "采购成本",
    "production_cost": "生产成本",
    "package_cost": "包装成本",
    "processing_cost": "加工成本",
    "total_cost": "总成本",
    "standard_price": "标准价",
    "guide_price": "指导价",
    "minimum_price": "最低保护价",
    "target_margin": "目标利润率",
    "min_qty": "最小数量",
    "max_qty": "最大数量",
    "customer_level": "客户等级",
    "agreed_price": "约定价",
    "effective_from": "生效日期",
    "effective_to": "截止日期",
    "calculation": "计算口径",
    "item_count": "明细条数",
    "count": "条数",
    "file_name": "文件名",
    "mime_type": "文件类型",
    "storage_provider": "存储方式",
    "size": "大小",
    "uploaded_by": "上传人",
    # 用户 / 角色 / 部门
    "username": "登录名",
    "wecom_userid": "企微账号",
    "department_id": "所属部门",
    "data_scope": "数据范围",
    "role_ids": "角色",
    "permission_codes": "权限",
    "description_cn": "说明",
    "remark_cn": "备注",
    "label": "名称",
    "value": "值",
    "key": "键",
    "enabled": "是否启用",
    "level": "等级",
    "tag_ids": "标签",
    "owner_name": "负责人",
    "reviewer_id": "复核人",
    "review_note": "复核说明",
    "review_history": "复核历史",
    "status_label": "状态",
    "current_version_amount": "当前版本金额",
    "current_version_no": "当前版本号",
    "amount": "金额",
    "quantity": "数量",
    "currency": "币种",
    "title_cn": "标题",
    "name_cn": "名称",
    "reason": "原因",
    "note": "说明",
    "result": "结果",
    "progress": "进展",
    "stage_reached": "到达阶段",
    "industry": "行业",
    "problem_tags": "问题标签",
    "background": "背景",
    "goal": "目标",
    "key_actions": "关键动作",
    "objection_handling": "异议处理",
    "process": "过程",
    "lessons": "可复用做法",
    "author_id": "作者",
    "customer_label": "对外代称",
    "revision_of_id": "修订自",
    "reviewed_at": "审核时间",
    "estimated_amount": "预计金额",
    "expected_close": "预计成交日",
    "won_at": "赢单时间",
    "lost_at": "失单时间",
    # 通用字段（在所有对象上含义一致，可以在全站范围内统一叫法）
    "owner_id": "负责人",
    "customer_id": "客户",
    "contact_id": "联系人",
    "lead_id": "线索",
    "opportunity_id": "商机",
    "quote_id": "报价单",
    "quote_version_id": "报价版本",
    "order_id": "订单",
    "sample_id": "打样单",
    "inquiry_id": "定制需求",
    "opportunity_item_id": "商机需求",
    "converted_inquiry_id": "转成的需求",
    "version": "版本",
    "version_no": "版本号",
    "remark": "备注",
    "request_key": "请求编号",
    "role": "角色",
    "priority": "优先级",
    "direction": "方向",
    "target_price": "目标价",
    "type": "类型",
    "code": "编码",
    "root_id": "原始需求",
    "wecom_department_id": "企微部门",
    "source_snapshot": "来源快照",
    "followup_type": "跟进方式",
    "content": "内容",
    "risk_level": "风险等级",
    "domain": "行业",
    "source_context": "来源快照",
    "generated_file_id": "生成的 PDF",
    "content_snapshot": "正文快照",
    "filled_data": "填入的内容",
    "target_customer": "目标客户",
    "selling_points": "卖点",
    "price_assumption": "价格假设",
    "conclusion": "结论",
    "images": "图片",
    "received_date": "收款日期",
    "received_amount": "回款金额",
    "payment_method": "收款方式",
    "voucher_note": "凭证说明",
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
