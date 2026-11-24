# B17 集成·Agent 工具 + 报表服务（4 文件，4,998 行）

> 审查基线：当前工作区文件内容；只读业务代码，只写 `review/`。
> 本块 4 个文件逐文件读完；未运行测试，未做隔离库并发/故障注入。
> 实际行数：`agent/tools.py` 1,757、`analytics/model.py` 248、`analytics/service.py` 2,316、`analytics/router.py` 677，合计 **4,998**。
> 结论：**P0 0、P1 1、P2 6、P3 0、待确认 2**。

## 一、本块发现清单

| 编号 | 严重度 | 位置 | 一句话 | 测试覆盖 |
|---|---|---|---|---|
| B17-01 | P1 | `agent/tools.py:446-454、477-499` | 有 `payment:view` 但没有 `order:view` 时，`get_customer_overview` 的订单集合为空，待回款 = 0 − 已确认回款，直接变成负数 | 未覆盖（现有用例要么两项权限齐全，要么两项都缺） |
| B17-02 | P2 | `agent/tools.py:182-183、414、460、557、617、645、659、1619、1629-1631、1641、1647、1755` | Agent 所有金额出口统一 `round(float(value), 2)`；既与核心 `Decimal + ROUND_HALF_UP` 不一致，也被误用于数量/重量等非金额字段 | 部分覆盖（`test_agent_scope_and_receivables.py:266-298` 只测 0.1/0.25 这类二进制可精确表示的值） |
| B17-03 | P2 | `analytics/service.py:84-85、533-543、605-621、1288-1291、1369-1376、1515-1525、1632-1653、1733-1739、2287-2312` + `analytics/router.py:489-490` | 报表金额不仅出口转 float，`order_payment_trend`、`product_stats`、`pricing_stats` 还在 Python 里用 float 累加/平均；目标保存响应也直接 `float(...)` | 部分覆盖（`test_pricing_simulation_precision.py:98-115` 明确断言 `195.0`，`test_product_stats_versions.py:45-65` 只断言整数等价值；没有半分/多行累加用例） |
| B17-04 | P2 | `analytics/service.py:165-188、1221-1291、1415-1449、1629-1653` + `analytics/router.py:600-611` | 产品毛利、报价均价、人员成交/回款、趋势图把不同币种直接相加/平均；`currency_note` 也没有探测 `Quote.currency`，历史外币报价可静默混入 | 未覆盖（`test_agent_scope_and_receivables.py:365-406` 只覆盖 Agent 应收按币种分组，不覆盖 analytics 产品/价格/人员/趋势） |
| B17-05 | P2 | `analytics/service.py:1154-1156、1258-1263、1269-1291` | 无 SKU 的定制项只用 `sku_name_snapshot` 做分组键，规格被丢掉；同名不同规格的两个定制产品会被合并成一行，数量和毛利相加、规格只显示第一条 | 未覆盖（`test_product_stats_versions.py:69-71` 只测“不崩”，不测同名不同规格） |
| B17-06 | P2 | `analytics/service.py:1217-1248`，对照 `1012-1018、1610-1618` | `product_stats` 的报价明细查询没有 `draft_scope`/`private_draft` 过滤；私密草稿版本的商品名、规格、数量、草稿次数会进入产品统计，而 `quote_stats`/`pricing_stats` 有过滤 | 未覆盖（没有私密草稿进入/不进入产品统计的用例） |
| B17-07 | P2 | `agent/tools.py:1543-1563` + `pricing/logistics.py:411-445、148-178` | `calculate_logistics` 把 JSON 的 `quantity` 原样以 `float` 传给标注 `Decimal` 的 `logistics.prepare`；SKU 有单重时 `Decimal * float` 直接 `TypeError`，工具返回 500/被上层误报成 AI 不可用 | 未覆盖（`test_agent_scope_and_receivables.py:131` 只断言权限声明，没有执行用例） |
| B17-P1 | 待确认 | `analytics/service.py:1503-1520` | `receivable_stats.unreceived_amount = plan_amount − 全部已确认回款`；预收/超收（未分配回款）时会出现负数，且与同返回的 `unsettled_plan_amount`（逐节点余额合计）不一致 | 未覆盖（analytics 应收没有预收/超收用例） |
| B17-P2 | 待确认 | `analytics/router.py:351-389` + `analytics/target_actuals.py:330-349` | `refreeze_sales_actuals` 在 `values` 为空时仍调用 `freeze_period(replace=True)`，会删除该期间全部旧汇总和旧明细；普通结账有空值保护，重算没有 | 未覆盖（`test_target_freeze_limits.py` 只测整批金额上限，不测空重算） |

## 二、逐文件检查记录

### 1. `backend/app/modules/agent/tools.py`（1,757 行，已读完）

**我检查了什么：**
- 工具注册、`ToolSpec.permissions`、`validate_tool_arguments` 的类型/枚举/必填校验；
- `_scope`/`_ensure_in_scope` 的数据范围过滤，公海客户/线索的 `allow_unowned` 例外；
- `get_customer_overview` 的分模块取数、联系人脱敏、订单/待回款口径；
- `get_receivables_summary` 的订单范围、币种分组、Decimal 余额、取消单；
- `search_leads` 的线索池可见性、手机号完整值；
- `list_my_tasks` 的权限码与本人任务；
- `get_product`、`get_order`、`get_opportunity_detail` 的金额/数量出口；
- `calculate_price` 的 `PricingRequest` 复用、成本脱敏；
- `calculate_logistics` 的数量/重量/体积边界；
- `create_quote_draft`/`create_quote_version` 的商机/报价范围、币种、审计与 `flush`；
- `create_followup`/`create_task`/`update_opportunity_next_action`/`request_quote_approval` 的权限、幂等键、事务边界；
- 工具 handler 可能抛出的非 `AppError`（DB/JSON/Decimal 序列化）。

**结果如何：**
- 正面确认：`create_quote_version` 虽然先用 `get_quote_or_404`，但 `quote_service.create_version` 内部第一句重新 `get_visible_quote` 并 `for_update`，所以“改 id 给任意报价建版本”的候选不成立；`create_quote_draft` 的 `create_quote` 也内部 `get_visible_opportunity`，商机范围同样有兜底。此前 B17 候选关闭，不列为发现。
- 正面确认：`create_quote_draft`/`create_quote_version` 等写工具只 `flush` 不 `commit`，与 Gateway 的动作行锁/一次提交一致；`create_followup` 用 `agent:{action_id}` 作为 `request_key`，重复确认不会重复写跟进。
- 正面确认：`calculate_price` 复用了 `PricingRequest`，数量 0/负数/小数位超限/超大值在入工具时就是 422；成本/底价等由 `_sanitize_pricing_result` 统一脱敏。这条不是问题。
- 发现 **B17-01**：`get_customer_overview` 的订单列表只在 `order:view` 分支取（446-454），而待回款在 `payment:view` 分支直接遍历这个 `orders` 列表（477-486）；只有 `payment:view` 的角色会得到“0 − 已确认回款”的负数。现有测试 `test_overview_skips_unauthorised_blocks` 只覆盖“两项权限都没有”，`test_overview_pending...` 覆盖“两项都有”，没有覆盖只有 `payment:view`。
- 发现 **B17-02**：`_money` 对所有金额（以及订单数量、SKU 重量、物流计费重）统一 `round(float(value), 2)`；金额半分边界、数量/重量的小数位都会丢。`get_receivables_summary` 的 `by_currency` 虽然内部 Decimal，出口仍是 `float(round(...))`（971-979）。
- 发现 **B17-07**：`calculate_logistics` 的 `quantity` 是 float，`prepare` 的 `unit_weight`/`unit_volume` 是 Decimal；`compute_weight_and_volume` 的乘法会抛 `TypeError`。`calculate_price` 有 `PricingRequest` 这道边界，`calculate_logistics` 没有。
- 未把 `search_leads` 明文手机号单列：lead 列表/详情本身也返回完整 `mobile`（`lead/service.py:29`），Agent 与现有业务接口同口径；是否应在 AI 结果中额外脱敏属产品取舍，本块没有把它当结论。
- 未把 `list_my_tasks` 的 `followup:view` 单列为越权：它只查 `Task.owner_id == user.id`，且 `test_every_read_tool_declares_its_module_permission` 已把该权限码写成期望；这是“权限码与 task 路由不一致”的口径问题，不是跨用户读取。归入待确认/后续权限口径，不升级为本块 P1。

### 2. `backend/app/modules/analytics/model.py`（248 行，已读完）

**我检查了什么：**
- `SalesTarget` 的作用域唯一索引、期间正则、非负约束、个人/团队互斥；
- `BasisSnapshot` 的 JSON 列、冻结边界、旧版 `first_deal_detail` 空值语义；
- `ActualSnapshot` 的复合主键、金额列精度、口径版本；
- `ActualSnapshotItem` 的索引、金额列、明细与汇总同批写入的注释；
- `OperationTiming` 的索引、字段类型。

**结果如何：**
- `SalesTarget` 有 `(period,user_id,department_id)` 的 `NULLS NOT DISTINCT` 唯一索引、`YYYY-MM` 正则、`>=0` 约束、`NOT(user_id AND department_id)`，与迁移 `f6c2e8a4b1d9` 对齐；模型层没有发现新的金额/状态约束缺口。
- `ActualSnapshot` 的 `actual_value` 是 `Numeric(16,2)`；`ActualSnapshotItem.amount` 也是 `Numeric(16,2)`，但模型本身没有金额上限 CheckConstraint。由于 `freeze_period` 在写入前逐批调用 `_check_snapshot_amount` 且超限会整批拒绝，本块不把“模型可直写超限”列为已确认；如果后续新增绕过 `freeze_period` 的写入入口，需要补约束。
- `ActualSnapshotItem` 没有 `(period,metric,record_type,record_id)` 唯一约束；当前 `freeze_period` 在同一期间锁内先删旧明细再全量插入，正常路径不会重复。只有绕过该函数直写才会产生重复，本块不升级为结论。
- 未发现需要单列的 P0/P1/P2。

### 3. `backend/app/modules/analytics/service.py`（2,316 行，已读完）

**我检查了什么：**
- `_scope_filter`/`scoped_owner_ids` 在各统计函数的覆盖；业绩归属 `sales_owner_id` 与责任归属 `owner_id` 是否在各页面用对；
- `currency_note` 的外币探测集合；
- `dashboard_summary` 的待回款、本月回款、逾期应收、订单组成；
- `order_payment_trend` 的月份序列与 Python 累加；
- `stale_customers` 的分页、主联系人电话、与 summary 同口径；
- `quote_stats`/`customer_stats`/`product_stats` 的软删、草稿、私密草稿、版本去重；
- `sales_user_stats` 的业绩/回款口径、数据范围；
- `receivable_stats`/`payment_stats` 的余额 Decimal、逾期判据、账龄、币种；
- `delivery_stats` 的首批发货、逾期节点、批次偏差、在跟风险；
- `team_summary` 的团队范围、本月成交判据、停用人员小计、成员明细。

**结果如何：**
- 正面确认：`_scope_filter` 在 dashboard、trend、stale、funnel、risk、quote/customer/product/sales-user/receivable/payment/delivery/team 等入口基本都落到了 owner 列；`receivable_stats` 明确用责任口径（`SalesOrder.owner_id`）而不是签单归属，`_plan_balances` 用 Decimal；逾期判断统一委托 `payment.is_plan_overdue`，没有在本文件另写第二套公式。
- 正面确认：`product_stats` 已把草稿/正式拆列，正式毛利按“同一报价单的最新正式版本”取，订单成交数量排除取消单，未授权 `price:manage` 时 `quote_estimated_profit` 返回 `None`；这些口径本身是清楚的。
- 发现 **B17-03**：所有金额出口走 `_f`（84-85），`order_payment_trend` 用 float 累加（605-614），`product_stats` 用 float 累加毛利/数量（1288-1291），`pricing_stats` 把报价/建议价转 float 后算平均（1632-1653）；`dashboard_summary`/`receivable_stats`/`payment_stats`/`team_summary` 的金额出口也没有 exact 字段。
- 发现 **B17-04**：`product_stats` 取 `QuoteItem.profit_snapshot` 时不取/不分组 `QuoteVersion.currency`；`pricing_stats` 按客户等级平均报价时跨币种；`sales_user_stats` 的 `order_amount`/`received_amount` 与 `order_payment_trend` 也跨币种。`currency_note` 只探测订单/回款/应收/商机，不探测 `Quote.currency`，所以“只有历史外币报价”的产品毛利可以完全没有提醒。
- 发现 **B17-05**：`_product_bucket` 对无 SKU 定制项只返回 `n:{name}`，`name_fallback` 也只保存第一条的 `(name,spec)`；同名不同规格会合并。
- 发现 **B17-06**：`product_stats` 的 `quote_rows` 只过滤 `Quote.deleted_at`（1242），没有 `draft_scope(Quote, user.id)`，也没有 `pricing_stats` 那种 `private_draft(QuoteVersion)`/创建人判断；私密草稿明细会进入产品统计。
- 待确认 **B17-P1**：`receivable_stats.unreceived_amount` 用 `plan_amount - received`，`received` 是订单下全部已确认回款（含未分配预收/超收），会出现负数且与 `unsettled_plan_amount` 不一致；这是否是业务要表达的“超收”未确认，故不写成已确认缺陷。
- 未把 `recent_activities` 的“只按 operator 过滤、不按业务对象归属过滤”列为发现：它只返回 `business_id` 与通用动作标题，不含客户名/金额；是否可点开取决于前端，本块没有拿到实际越权读取证据，留在待确认。

### 4. `backend/app/modules/analytics/router.py`（677 行，已读完）

**我检查了什么：**
- 每个 analytics/dashboard 端点的权限码（`customer:view`/`opportunity:view`/`quote:view`/`product:view`/`payment:view`/`order:view`/`settings:manage`）；
- `_with_fx_note` 的对象形/数组形约定；
- `SalesTargetUpsert` 的边界、`upsert_target` 的审计与响应；
- `freeze`/`refreeze` 的 `data_scope == all`、过去期间校验、一致快照、审计 before/after；
- `sales-targets/drilldown` 的 `user_id/department_id` 范围；
- `report_operation_timing` 的入参与权限。

**结果如何：**
- 正面确认：`freeze_sales_actuals`/`refreeze_sales_actuals` 都要求 `settings:manage` 且 `data_scope == all`；`freeze` 用 `consistent_snapshot` 把汇总与明细读在同一 REPEATABLE READ 快照，`freeze_period` 在期间锁内重新检查“是否已结账”，普通结账不会静默覆盖；`refreeze` 填 `before` 并把改前/改后考核合计写审计，交易边界是清楚的。
- 正面确认：`sales_target_drilldown` 的 `user_id`/`department_id` 在 `targets.drilldown` 内按 `scoped_owner_ids`/`visible_department_ids` 二次校验，不是“传谁看谁”。
- 发现 **B17-03** 的一部分：`upsert_sales_target` 响应把 `sales_target`/`repeat_customer_target` 转 `float`（489-490），没有 exact 字符串；目标列表/实绩的金额由 `targets` 服务返回，是否已有 exact 需 B18 继续核对，但本端点自身缺 exact。
- 发现 **B17-04** 的一部分：`/analytics/products`、`/analytics/sales-users`、`/dashboard/trend` 等数组形返回无法挂 `currency_warnings`（600-611、538-545），代码注释已承认；在“只有外币报价/外币订单”而页面只打开这些数组接口时，用户看不到折算提醒。
- 待确认 **B17-P2**：`refreeze_sales_actuals` 没有 `freeze_sales_actuals` 的“`values` 为空就 404”保护；如果重算时源数据已全部不存在，`freeze_period(replace=True)` 会删除旧汇总/旧明细。由于这是管理员显式重算，是否应允许“清空历史”需业务确认，本块不写成 P0 数据丢失。
- `report_operation_timing` 只要求 `customer:view`，但耗时数据是运营指标而非客户数据；是否应改成管理员/运营权限属权限口径，未列入本块结论。

## 三、发现详情

### B17-01 只有 `payment:view` 时客户全貌的待回款变成负数

- **严重度：P1**
- **文件:行号：**
  - `backend/app/modules/agent/tools.py:446-454`（`orders` 只在 `order:view` 分支填充）
  - `backend/app/modules/agent/tools.py:477-499`（`payment:view` 分支遍历 `orders` 算 `order_total`，再减 `_receivables_by_currency` 的 `received_amount`）
- **触发条件：** 用户有 `agent:use` + `payment:view`，但没有 `order:view`；客户名下有未取消订单且已有财务确认回款，调用 `get_customer_overview(customer_id)`。
- **后果：** `orders=[]` → `order_total` 为空 → `pending = 0 - received`，`pending_receivable_amount` 为负数；工具同时只把“订单（需要 order:view）”放进 `unavailable_blocks`，模型会把负数当成“客户已超付/待回款为负”，给出错误的资金结论。权限码本身是合法的（财务/收款角色不一定有订单查看权），不是异常输入。
- **证据代码：**

```python
orders = []
if ctx.user.has("order:view"):
    order_stmt = await _scope(...)
    orders = (...).scalars().all()
    result["orders"] = [...]

if ctx.user.has("payment:view"):
    order_total: dict[str, Decimal] = {}
    for order in orders:                      # ← 没有 order:view 时是空列表
        if order.status == "cancelled":
            continue
        code = _currency_of(order.currency)
        order_total[code] = order_total.get(code, Decimal(0)) + Decimal(order.total_amount or 0)
    by_currency, _plans = await _receivables_by_currency(
        ctx, await _active_order_ids(ctx, customer_id=customer_id)
    )
    pending = {
        code: order_total.get(code, Decimal(0))
        - Decimal(str(by_currency.get(code, {}).get("received_amount", 0)))
        for code in sorted(set(order_total) | set(by_currency))
    }
    if len(pending) <= 1:
        result["pending_receivable_amount"] = float(round(pending.get(only_code, Decimal(0)), 2))
```

- **建议：**
  - `payment:view` 分支不要依赖 `orders` 列表；重新按 `_active_order_ids(ctx, customer_id=...)` + 订单范围/币种取 `order_total`，或在 `order:view` 缺失时不要输出 `pending_receivable_amount`，只输出“无法计算：缺少订单查看权限”；
  - 更稳妥的做法是让待回款直接复用应收计划口径（`_receivables_by_currency` 的 `plan_amount`），避免“整单金额 − 回款”的参考口径与正式应收混用；
  - 补测试：`permissions={"customer:view","payment:view"}`、无 `order:view`、客户有 100 元订单和 40 元确认回款，断言不出现负数且给出明确 unavailable 说明。
- **测试覆盖：** 未覆盖。`backend/tests/test_agent_scope_and_receivables.py:169-188` 只测“四项权限都没有”时相关键不出现；`:266-298` 测“有 order:view + payment:view”时 0.1；没有“只有 payment:view”的组合。

### B17-02 Agent 工具金额统一 `round(float(...),2)`，并误用于数量/重量

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/agent/tools.py:182-183`（`_money` 定义）
  - 金额出口：`414`（商机预计金额）、`460`（客户订单总额）、`557`（商机列表）、`617`（报价版本总额）、`645`（商机详情）、`659`（需求目标价）、`1619`（订单总额）、`1629-1631`（明细数量/单价/金额）、`1641`（应收计划金额）、`1647`（已回款）、`1755`（新版本总额）
  - 非金额但同样走 `_money`：`1471`（SKU 重量）、`1569-1571`（物流实际重量/体积/计费重）
- **触发条件：** Agent 工具返回金额恰好在半分边界（如 `2.675`），或订单数量/重量有 3-4 位小数；或金额较大（超过 float 精确整数范围）需要逐分复算。
- **后果：**
  - `round(float(value),2)` 先经过二进制 float，`2.675` 这类值可能输出 `2.67`，与项目核心 `Decimal + ROUND_HALF_UP` 的 `2.68` 不一致；模型复述/确认卡上的金额和报价/订单真值可能差 1 分；
  - 订单 `quantity`、SKU `weight`、物流计费重被强行舍到 2 位，3-4 位小数的数量/重量会丢精度，Agent 可能按错误数量解释；
  - 所有金额都没有 `*_exact` 字符串，模型/前端无法复算。
- **证据代码：**

```python
def _money(value) -> float | None:
    return None if value is None else round(float(value), 2)

# 金额
"total_amount": _money(o.total_amount),
"unit_price": _money(item.unit_price),
"amount": _money(item.amount),

# 非金额
"quantity": _money(item.quantity),
"weight": _money(sku.weight),
"chargeable_weight": _money(prepared["measures"]["chargeable_weight"]),
```

- **建议：**
  - 金额统一 `Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`，并同时返回 `amount_exact`（字符串）；
  - 数量/重量/体积不要复用 `_money`，按各自列精度（数量 3 位、重量/体积 4 位）单独格式化；
  - Agent 提示词/工具 schema 明确“金额字段有 `*_exact`，引用金额以 exact 为准”；
  - 补测试：`2.675`、`0.005`、`0.1+0.2`、数量 `1.005`、重量 `0.1234`。
- **测试覆盖：** 部分覆盖。`backend/tests/test_agent_scope_and_receivables.py:266-298` 只断言 `0.1` 与 `100.25`（二进制可精确表示）；`test_pricing_simulation_precision.py` 测的是 pricing 服务，不是 Agent 工具出参；没有半分/多行/数量小数用例。

### B17-03 报表金额在 Python 里用 float 累加/平均，出口也没有 exact

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/analytics/service.py:84-85`（`_f = float(value or 0)`）
  - `backend/app/modules/analytics/service.py:605-621`（`order_payment_trend` 用 `orders[key] = ... + _f(amount)`、`round(...,2)`）
  - `backend/app/modules/analytics/service.py:1288-1291、1369-1376`（`product_stats` 用 `float(profit)`、`float(quantity)` 累加，`round` 输出）
  - `backend/app/modules/analytics/service.py:1632-1653`（`pricing_stats` 把 `quoted_price/recommended` 转 float 算折扣、均价）
  - `backend/app/modules/analytics/service.py:533-543、1515-1525、1736-1739、2287-2312`（dashboard/receivable/payment/team 金额出口）
  - `backend/app/modules/analytics/router.py:489-490`（目标保存响应 `float(row.sales_target)`）
- **触发条件：** 报表跨多行金额、金额带半分边界，或需要从报表反算到明细；`pricing_stats` 的报价/建议价是 4 位单价。
- **后果：**
  - `product_stats` 的 `quote_estimated_profit` 是逐行 float 累加，多个 0.1/0.2 或 4 位单价乘数量后可能差 1 分；
  - `order_payment_trend` 的月度金额是 float 累加，趋势图与订单/回款明细可能对不上；
  - `pricing_stats` 的 `average_discount_rate`/`average_price` 是 float 除法，半分边界与核心 HALF_UP 不一致；
  - 除 `sales_user_stats` 外，大部分报表没有 `*_exact`，前端只能拿 float 二次汇总。
- **证据代码：**

```python
def _f(value) -> float:
    return float(value or 0)

# order_payment_trend
orders[key] = orders.get(key, 0.0) + _f(amount)
...
"order_amount": round(orders.get(m, 0.0), 2),

# product_stats
acc["profit"] += float(profit or 0)
acc["quantity"] += float(quantity or 0)
...
"quote_quantity": round(acc["quantity"], 2),
"quote_estimated_profit": round(acc["profit"], 2) if can_see_cost else None,

# pricing_stats
quoted_price = float(item.quoted_price)
discounted.append((float(recommended) - quoted_price) / float(recommended))
```

- **建议：**
  - 累加/平均用 `Decimal`，逐项按项目精度 `quantize(..., rounding=ROUND_HALF_UP)`；最终同时输出展示值与 `*_exact` 字符串；
  - `pricing_stats` 的折扣/均价只作为比率/展示值时，也用 `Decimal` 计算后再格式化，不要先转 float；
  - `analytics/router.py` 的目标保存响应补 `sales_target_exact`/`repeat_customer_target_exact`；
  - 补测试：多行 0.1+0.2、`2.675` 半分、4 位单价乘数量、`pricing_stats` 的 90/300 与半分组合。
- **测试覆盖：** 部分覆盖。`backend/tests/test_pricing_simulation_precision.py:98-115` 明确断言 `average_price == 195.0`，等于把 float 契约固定下来；`test_product_stats_versions.py:45-65` 只断言 `80` 这类整数等价值；没有精度/半分/多行用例。`backend/tests/test_agent_scope_and_receivables.py` 不覆盖 analytics 金额。

### B17-04 报表跨币种直接相加/平均，外币报价不被 `currency_note` 探测

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/analytics/service.py:165-188`（`currency_note` 只探测订单/回款/应收/商机，不探测 `Quote.currency`）
  - `backend/app/modules/analytics/service.py:1221-1248、1288-1291`（`product_stats` 取 `profit_snapshot * quantity`，不取 `QuoteVersion.currency`、不分组）
  - `backend/app/modules/analytics/service.py:1415-1449`（`sales_user_stats` 的 `order_amount`/`received_amount` 跨币种）
  - `backend/app/modules/analytics/service.py:1629-1653`（`pricing_stats` 按客户等级平均 `quoted_price`，未按币种）
  - `backend/app/modules/analytics/service.py:605-621`（`order_payment_trend` 订单/回款跨币种）
  - `backend/app/modules/analytics/router.py:600-611`（产品/人员数组接口无法挂 `currency_warnings`）
- **触发条件：** 系统里存在历史外币报价/订单（项目只对新写入做 CNY 闸门，历史数据/ERP 可带外币）；用户打开产品分析、价格分析、人员榜或趋势图。
- **后果：** 100 USD 与 100 CNY 会被加成 200 或平均成 100，产品毛利/人员成交/趋势数字没有币种含义；`currency_note` 对“只有外币报价、没有外币订单/商机”的场景不触发，用户看不到“未折算”的提醒。数组接口即使有提示也挂不上（代码注释已承认）。
- **证据代码：**

```python
# currency_note 的四个 probe：SalesOrder / PaymentRecord / ReceivablePlan / Opportunity
probes = [
    (select(SalesOrder.id).where(SalesOrder.currency != CNY), _sales_owner_col()),
    (select(PaymentRecord.id)...where(PaymentRecord.currency != CNY), _sales_owner_col()),
    (select(ReceivablePlan.id)...where(ReceivablePlan.currency != CNY), _sales_owner_col()),
    (select(Opportunity.id).where(Opportunity.currency != CNY), Opportunity.owner_id),
]

# product_stats：没有 currency 列
func.coalesce(QuoteItem.profit_snapshot * QuoteItem.quantity, 0).label("profit")
```

- **建议：**
  - `product_stats`/`pricing_stats`/`sales_user_stats`/`order_payment_trend` 增加币种维度（至少返回 `currency` + `by_currency`，多币种不给无币种合计），或明确只统计 CNY 并跳过/标注外币；
  - `currency_note` 增加 `Quote.currency`（必要时 `QuoteVersion.currency`）探测；
  - 数组形接口如果要提示，改成对象形 `{items, currency_warnings}` 或另设页面级告警接口，不能只在注释里假设“别的页面会带”；
  - 补测试：USD 报价 + CNY 报价的产品毛利、USD 订单 + CNY 订单的人员榜/趋势，断言不混算。
- **测试覆盖：** 未覆盖。`backend/tests/test_agent_scope_and_receivables.py:365-406` 只测 Agent 应收 `_receivables_by_currency` 的币种分组；`test_product_stats_versions.py`/`test_pricing_simulation_precision.py` 都用 CNY 数据；没有 analytics 跨币种用例。

### B17-05 无 SKU 定制项按名称合并，丢了规格

- **严重度：P2**
- **文件:行号：** `backend/app/modules/analytics/service.py:1154-1156、1258-1263、1269-1291、1353-1376`
- **触发条件：** 两张报价/同一报价的不同版本里有两个无 `sku_id` 的定制项，`sku_name_snapshot` 相同（如“纸箱”）但 `spec_snapshot` 不同（如 500×400 与 600×500）。
- **后果：** `_product_bucket` 返回 `n:{name}`，两个定制项落到同一个 `quoted` 桶：`quote_draft_times`、`formal_times`、`version_revision_times`、`quote_quantity`、`quote_estimated_profit` 全部合并；`name_fallback` 只保留第一条 `(name,spec)`，输出行只显示一个规格。产品统计会把两种不同产品当成同一种，数量和毛利都错。
- **证据代码：**

```python
def _product_bucket(sid: int | None, nm: str | None) -> object:
    """有 SKU 按 SKU 归并，无 SKU 的定制项按快照名单独成行。"""
    return sid if sid is not None else f"n:{nm or '定制项'}"

bid = _product_bucket(sku_id, q_name)
if sku_id is None and (q_name or q_spec):
    name_fallback.setdefault(bid, (q_name, q_spec))
```

- **建议：**
  - 分组键改为 `(sku_name_snapshot, spec_snapshot)`（或加 `inquiry_no_snapshot` 等稳定键），不要再只用名称；
  - `name_fallback` 按新桶保存，输出行分别显示各自规格；
  - 补测试：两个同名不同规格的无 SKU 明细，断言生成两行、数量/毛利不合并。
- **测试覆盖：** 未覆盖。`backend/tests/test_product_stats_versions.py:69-71` 只断言“无 SKU 不崩”，没有同名不同规格。

### B17-06 `product_stats` 未过滤私密草稿，草稿商品/数量进入产品统计

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/analytics/service.py:1217-1248`（`quote_rows` 只过滤 `Quote.deleted_at`，直接选 `QuoteItem`/`QuoteVersion`）
  - 对照 `backend/app/modules/analytics/service.py:1012-1018`（`quote_stats` 用 `draft_scope(Quote, user.id)`）
  - 对照 `backend/app/modules/analytics/service.py:1610-1618`（`pricing_stats` 用 `draft_scope(Quote, user.id)` + `private_draft(QuoteVersion)`/创建人判断）
- **触发条件：** 一个报价单对当前用户可见（`Quote.owner_id` 在范围内），但其中某个版本是别的用户创建的私密草稿（`approval_status=not_submitted` 且 `sent_at` 为空）；或报价单本身是私密草稿但负责人已转给当前用户。
- **后果：** `product_stats` 会读取该私密版本的 `QuoteItem`，输出 `sku_name_snapshot/spec_snapshot`、`quote_draft_times`、`quote_quantity`、`version_revision_times`。用户因此能看到别人未发送草稿里的商品、规格和数量；价格/成本没有直接暴露（`quote_estimated_profit` 只取正式版本），但产品组合信息已泄露。
- **证据代码：**

```python
quote_rows = (
    await session.execute(
        await _scope_filter(
            select(...)
            .join(QuoteVersion, QuoteVersion.id == QuoteItem.quote_version_id)
            .join(Quote, Quote.id == QuoteVersion.quote_id)
            .where(Quote.deleted_at.is_(None)),   # ← 没有 draft_scope/private_draft
            user, Quote.owner_id, session,
        )
    )
).all()
```

- **建议：**
  - `product_stats` 的报价查询复用 `draft_scope(Quote, user.id)`，并对 `QuoteVersion` 应用与 `pricing_stats` 相同的 `private_draft`/创建人条件；
  - 如果产品分析确实需要统计所有草稿，至少只统计本人创建的私密草稿，且不返回他人草稿的商品名/规格；
  - 补测试：他人私密草稿版本的商品不得出现在 `product_stats`，本人私密草稿可出现。
- **测试覆盖：** 未覆盖。现有 `test_product_stats_versions.py` 都是公开/正式版本；没有 `draft_scope`/`private_draft` 用例。

### B17-07 `calculate_logistics` 把 float 数量传给 Decimal 物流计算，可能 500

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/agent/tools.py:1543-1563`（工具 schema `quantity: number`、handler `quantity: float`、直接传 `prepare`）
  - `backend/app/modules/pricing/logistics.py:411-445`（`prepare` 标注 `quantity: Decimal`，只校验 `<=0`，不转换类型）
  - `backend/app/modules/pricing/logistics.py:148-178`（`unit_weight * quantity`、`unit_volume * quantity`）
- **触发条件：** 模型调用 `calculate_logistics` 时把数量输出成 JSON number 的浮点形态（如 `2.0`/`1.5`），或用户说“1.5 吨/1.5 件”；SKU 维护了单重/体积。
- **后果：** `Decimal * float` 抛 `TypeError`（实测 `Decimal('1.2') * 1.5` → `TypeError`），工具 500；B16-06 的 runtime 只捕 `AppError`，最终可能被翻译成“AI 服务暂时不可用”，用户以为模型坏了。`calculate_price` 已经用 `PricingRequest` 把数量转成 Decimal，物流工具没有复用这条边界。
- **证据代码：**

```python
# tools.py
"quantity": {"type": "number"},
...
async def calculate_logistics(ctx: ToolContext, sku_id: int, quantity: float, ...):
    prepared = await logistics_service.prepare(
        ctx.session, sku_id=sku_id, quantity=quantity, ...
    )

# logistics.py
async def prepare(..., quantity: Decimal, ...):
    if quantity <= 0:
        ...
    measures = await compute_weight_and_volume(session, sku=sku, quantity=quantity, ...)

# compute_weight_and_volume
actual_weight = (unit_weight * quantity).quantize(Decimal("0.0001"))
```

- **建议：**
  - 工具层用 `Decimal(str(quantity))` 转换，并按 `PricingRequest` 的 `gt=0, max_digits=16, decimal_places=3` 校验；非法/超大/小数位超限返回 422；
  - 或在 `logistics.prepare` 入口统一 `Decimal(str(quantity))`，让 HTTP 与 Agent 两条路径同一套类型转换；
  - 补测试：`quantity=2.0`、`1.5`、`0`、`-1`、`1e20`，断言 2.0/1.5 正常算、非法值 422 而不是 500。
- **测试覆盖：** 未覆盖。`backend/tests/test_agent_scope_and_receivables.py:131` 只断言 `calculate_logistics` 声明 `product:view`，没有执行工具；没有 float 数量用例。

### B17-P1 `receivable_stats.unreceived_amount` 在预收/超收时可能为负（待确认）

- **严重度：待确认（若业务把“未收”定义为计划减全部实收，则负数是超收；若定义为剩余应收，则应改用余额）**
- **文件:行号：** `backend/app/modules/analytics/service.py:1503-1520`
- **触发条件：** 订单应收计划合计 100，财务确认回款 150（未分配预收或超收）；或回款分配到已取消节点但计划合计已排除。
- **后果：** `receivable_stats` 同时返回 `unsettled_plan_amount`（逐节点余额合计）与 `unreceived_amount`（计划 − 全部实收）；前者可能 0，后者 -50，页面两个“未收”数字不一致，用户可能把负数当成系统错误或反向应收。
- **证据代码：**

```python
plan_amount = sum((plan.amount for plan in plans if plan.status != "cancelled"), ZERO)
cash = await payment_service.allocation_totals(session, order_ids)
...
"unsettled_plan_amount": _f(sum(balances.values(), ZERO)),
"unreceived_amount": _f(plan_amount - sum((t["received_amount"] for t in cash.values()), ZERO)),
```

- **建议：** 先确认业务口径：若“未收”是剩余应收，改用 `sum(balances.values())`；若要保留“计划 − 实收”，增加 `overpayment_amount`/`prepayment_amount` 字段并在负数时显示“已超收”，不要让两个字段同名不同义。
- **测试覆盖：** 未覆盖。analytics 应收没有预收/超收用例；Agent 应收测试 `test_receivables...` 不覆盖 analytics。

### B17-P2 `refreeze_sales_actuals` 空重算会删除旧存档（待确认）

- **严重度：待确认（管理员显式重算，是否允许清空历史需业务确认；若视为误操作则是数据丢失）**
- **文件:行号：**
  - `backend/app/modules/analytics/router.py:351-389`（`refreeze` 没有 `values` 空值保护）
  - `backend/app/modules/analytics/target_actuals.py:330-349`（`replace=True` 删除所有不在 `values` 里的旧汇总，并删除全部旧明细）
- **触发条件：** 某期已结账；之后源订单被取消/删除或客户数据被清理，`_collect_period_actuals` 返回空 `values`；管理员调 `POST /sales-targets/actuals/refreeze?period=...&reason=...`。
- **后果：** `freeze_period(replace=True)` 会删除该期间全部 `ActualSnapshot` 和 `ActualSnapshotItem`，审计记录 `removed` 数；历史报表从“有存档”变成“空”。普通 `freeze` 在空值时有 404 保护（`router.py:307-310`），重算没有，两个入口行为不一致。
- **证据代码：**

```python
# refreeze：没有 if not values: raise
values = await _collect_period_actuals(snap, user, period)
items = await _collect_period_items(snap, user, period)
...
written = await target_actuals.freeze_period(..., replace=True, ...)
```

- **建议：** 与 `freeze` 对齐：空 `values` 时 404/422 并提示“重算没有产生任何实绩，已保留原存档”；如果业务确实要清空，增加显式 `allow_empty=true` 参数与二次确认，并在审计里标明“清空历史”。
- **测试覆盖：** 未覆盖。`backend/tests/test_target_freeze_limits.py:26` 只测整批金额上限，不测空重算删除。

## 四、补充维度逐项检查

### 4.1 负数、0、超大值、小数位超限

- `get_receivables_summary` 的订单范围/取消单有明确处理；`_receivables_by_currency` 的 `plan_amount` 用 Decimal，但 `plan.amount` 若为负/0 没有在读取时拦截，依赖应收写入侧校验；本块没有把“历史负数计划”当新发现，因为它不是 Agent 写入的新数据。
- `calculate_price` 复用 `PricingRequest`，数量/拟报价的 `gt=0`、`max_digits`、`decimal_places` 都在入口拦截；`calculate_logistics` 只有 `prepare` 的 `quantity<=0` 一道，且没有上限/小数位/类型转换，见 B17-07。
- `analytics/model.py` 的 `SalesTarget` 有 `>=0`、`YYYY-MM`、`max_digits=16`（Pydantic）约束；`ActualSnapshot`/`ActualSnapshotItem` 的金额上限靠 `_check_snapshot_amount`，不是模型 CheckConstraint；`freeze_period` 在写入前整批检查，超限不会写半批。
- `analytics/router.py` 的 `SalesTargetUpsert` 对 `user_id/department_id` 有 `gt=0, le=2^63-1`，`new_customer_target` 有 `le=2^31-1`，金额有 `ge=0/max_digits=16/decimal_places=2`，`remark` 有 255 上限；`period` 本身不在 schema 正则，但 `targets.upsert_target` 的 `normalize_period` 会规范化/拒绝，尚未发现绕过路径。
- `analytics/router.py` 的 `_ensure_past_period` 只校验 `\d{4}-\d{2}`，不校验月份 01-12；传入 `2026-13` 会在 `_collect_period_actuals` 得到空集，最终 404，不会落库错误月份；不升级。

### 4.2 事务边界

- `agent/tools.py` 的 L2 写工具（`create_quote_draft`、`create_quote_version` 等）只 `flush`，由 Gateway 的动作状态、审计、执行记录一次提交；`create_followup` 有 `request_key`，确认重试可幂等。正面确认。
- `analytics/router.py` 的 `freeze`/`refreeze`：`consistent_snapshot` 用独立连接读同一 REPEATABLE READ 快照，写事务先 `lock_period`，`freeze_period` 在锁内重新检查已结账；审计和写入同一 `session`，最后统一 `commit`。没有发现“汇总写了、明细没写”或“审计失败但快照已提交”的路径。
- B17-P2 的 `refreeze` 空值删除是事务内的显式删除，不是中途失败留下的半成品；风险是业务语义，不是事务边界。

### 4.3 异常与日志

- Agent 工具 handler 大量直接 `await service`，没有宽泛 `except` 吞异常；异常会交给 runtime（B16-06 只捕 `AppError` 的缺口已在 B16 记录，本块不重复）。B17-07 是新发现的非 `AppError` 触发点：`Decimal * float` 的 `TypeError` 会被 runtime 误报成 AI 故障。
- `analytics/service.py` 的 `currency_note`、`_plan_overdue`、`_f` 没有吞异常；`_f` 对 `Decimal('NaN')` 会返回 nan，但金额列来自 `Numeric(16,2)`，本块没有可复现的 NaN 路径。
- 本块未发现新的“错误信息把 SQL/堆栈/内部 ID 返回前端”路径；`analytics/router.py` 的 `_with_fx_note` 对非 dict 抛 `TypeError`，但当前所有调用点都是对象形，没有用户可达的数组误传。
- 日志方面，本块没有新增 `logger` 打印金额/成本；Agent 工具不自己打日志。

### 4.4 关键钱操作审计

- `create_quote_draft`/`create_quote_version` 都写审计：前者 `after={**payload, source:AGENT}`，后者 `after={version_id,version_no,source}`；金额不完整（`create_quote_version` 只记 version_no，未记 `total_amount`）与 B01-10/B02 的审计缺口同类，本块不再单列。
- `analytics` 的目标 `upsert` 有 `before`/`after`（`router.py:468-479`），结账/重算有 `before`/`after` 与原因；`refreeze` 的 `before` 由 `freeze_period` 填充。关键报表写操作审计基本完整。
- 本块没有“收款/改价/作废”这类写操作（Agent 工具只有报价草稿/版本、跟进、任务等）；`get_receivables_summary` 是只读。

### 4.5 测试覆盖汇总

- 已读：`backend/tests/test_agent_scope_and_receivables.py`（工具权限、客户全貌、应收 Decimal/币种/范围）、`test_agent_action_rejection.py`（确认动作过期/拒绝/取消）、`test_product_stats_versions.py`（产品统计版本/取消单/无 SKU 不崩）、`test_pricing_simulation_precision.py`（pricing 服务精度 + `pricing_stats` 一个用例）、`test_stale_customer_drilldown.py`、`test_target_freeze_limits.py`、`test_target_scope_input.py`、`test_task_dashboard_drilldown.py`。
- 覆盖情况：
  - B17-01：未覆盖（只有“全有/全无”两种权限组合）。
  - B17-02：部分覆盖（0.1/0.25 可精确表示；无半分/数量小数）。
  - B17-03：部分覆盖（`pricing_stats` 断言 float 195.0，等于固定了展示契约；无精度用例）。
  - B17-04：未覆盖（没有 analytics 跨币种）。
  - B17-05：未覆盖（只测不崩）。
  - B17-06：未覆盖（无私密草稿）。
  - B17-07：未覆盖（只测权限声明，不执行工具）。
  - B17-P1/B17-P2：未覆盖。
- 未运行 pytest；所有“覆盖”是读用例后的判断，不是“跑过全绿”。项目要求测试只能在隔离库运行，本块未获授权改测试。

## 五、本块没有看的部分及原因

| 文件/范围 | 读到什么程度 | 原因 |
|---|---|---|
| `backend/app/modules/analytics/targets.py`（1,229 行） | 只定向读 `target_snapshot`、`targets_with_actuals` 的冻结覆盖段、`drilldown` 的范围校验段、`upsert_target` | 属于 B18；B17 只验证 router 调用的 `drilldown` 范围与 `upsert` 审计，不能替代 B18 全文 |
| `backend/app/modules/analytics/target_actuals.py`（367 行） | 只定向读 `consistent_snapshot`、`lock_period`、`_check_snapshot_amount`、`freeze_period` | 属于 B18；B17 只验证结账/重算的事务边界与空值删除 |
| `backend/app/modules/analytics/target_bases.py`（582 行） | 未读 | 属于 B18；B17 的 `sales_target_bases` 只调用入口 |
| `backend/app/modules/analytics/usage.py`（200 行） | 未读 | 属于 B18；B17 的 `report_operation_timing`/summary 只调用入口 |
| `backend/app/modules/quote/service.py` | 只定向读 `get_quote_or_404`、`get_visible_quote`、`create_quote`、`create_version` 的范围/币种段 | 用于复核 B17 候选 `create_quote_draft`/`create_quote_version` 是否越权；未重审全文件，B01/B02 已审主体 |
| `backend/app/modules/payment/service.py` | 只定向读 `allocation_totals`、`confirmed_amounts`、`plan_received_totals`、`is_plan_overdue` 相关段 | 用于 B17-04/B17-P1 的金额口径；B05 已审主体 |
| `backend/app/modules/pricing/logistics.py` | 只定向读 `prepare`、`compute_weight_and_volume` 相关段 | 用于 B17-07；B03 已审物流主体 |
| `backend/tests` 全量 | 只读上述 8 个文件相关段 | 未运行测试；覆盖结论是“用例是否覆盖触发条件”，不是“全绿” |
| 隔离库并发/故障注入 | 未执行 | 项目要求隔离库，未授权改测试；B17-01/07 是代码路径结论，B17-P2 是显式删除路径，未做运行复现 |
| 前端 `frontend/src` | 未审 | 属于 F 系列；产品分析/人员榜/趋势图是否展示 exact、是否把负“未收”当超收、是否展示 `currency_warnings` 需前端块复核 |
| 真实 DeepSeek/Agent 工具调用 | 未实网 | 只用代码路径；未验证模型是否会把数量输出成 float、是否触发 B17-07 |
| 真实 PostgreSQL 期间锁/REPEATABLE READ | 未连接/未跑 | B17-P2 的删除路径与事务边界来自代码；未在生产/隔离 PG 上复现并发重算 |

## 六、结论

B17 的 4 个文件已逐文件读完。Agent 工具的读取范围、成本脱敏、写工具 `flush`/`request_key` 基本是正面确认；主要风险集中在“只有部分权限时的待回款口径”（B17-01 P1）、“金额/数量 float 化”（B17-02/B17-03）、“跨币种报表”（B17-04）、“产品统计分组/私密草稿”（B17-05/B17-06）和“物流工具 Decimal 类型边界”（B17-07）。两个待确认项需要业务口径，不在本块直接判 P0/P1。下一块 B18 继续审 `analytics/targets.py`、`target_actuals.py`、`target_bases.py`、`usage.py` 及案例/跟进模型。
