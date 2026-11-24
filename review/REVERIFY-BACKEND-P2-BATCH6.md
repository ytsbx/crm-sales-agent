# 后端 P2 复核（第六批：B01–B05 剩余条目）

> 日期 2026-11-24，只读当前代码。只审查，未改业务代码。

## 一、结果

本批 27 条：

- **7 条已修**：B02-03、B02-07、B03-19、B04-06、B04-07、B05-06、B05-12
- **2 条部分修**：B01-10、B02-14
- **18 条仍成立**

## 二、已修（代码证据）

| 编号 | 当前代码 | 说明 |
|---|---|---|
| B02-03 | `quote/router.py:426-427` 调 `require_quote_customer_contact`；`quote/service.py:740-748` 校验联系人存在且 `contact.customer_id == customer_id` | 跨客户/不存在联系人已 404/422，不再只写 id |
| B02-07 | `quote/service.py:776` 建报价先 `ensure_currency_allowed`；`core/trade_mode.py:72-90` 固定只允许 CNY，非人民币直接拒 | “外币无汇率仍按人民币底价算”在建壳前被挡 |
| B03-19 | `core/importing.py:317-323` `write_failed_row` 对 5xx/未知异常只返回“该行保存失败…请联系管理员”，原异常写日志不返前端 | 表/列/约束名不再泄露 |
| B04-06 | `order/router.py:225-238` 刷新 ERP 改成 `require_permission("order:manage")`；`erp/service.py:729-744` 写 `IntegrationLog`，`:770-800` 锁后重读再写状态 | `order:view` 不能再触发状态回写 |
| B04-07 | `order/service.py:1090-1111` 显式传 `items` 时必须覆盖本批全部明细，缺行 422，未传行不再按计划量全发；`:1123-1129` 只在 `items is None` 时才用 `planned_qty` | “只传部分明细，未传行默认全发”已封 |
| B05-06 | `erp/adapter.py:590-621` 取 `orders` 后按请求 `o_id` 过滤，`len(matching_orders) != 1` 抛 `ErpResultUnknown`，不再取 `[0]` | 不再把别的订单状态写进来 |
| B05-12 | `payment/router.py:852-861` 人工分配合计 `total > received` 直接 422；`payment/router.py` 是唯一写 `allocation_mode="manual"` 的入口（`:889-904`），`payment/service.py:207` 只读汇总 | 人工分配无法虚增分期已收 |

## 三、部分修

### B01-10 改价/整批替换/删除明细审计字段不完整

- 改价：`quote/router.py:1403-1410` `before/after` 只有 `item_id + quoted_price`，没记数量、成本、物流成本、标准价/最低价，无法复算这次改价对利润的影响。
- 整批替换：`:1269-1276` 只写 `after={"count": len(items)}`，旧明细与新明细的价格/数量/成本一条不留。
- 删除：`:1442-1453` 已用 `serialize_item(item)` 做 `before`，这一路算修好。

结论：删除已修，改价与整批替换仍不完整，保留 P2（部分修）。

### B02-14 价格权限/规则停用/客户专属价删除审计缺 before

- 价格权限：`pricing/router.py:986-997` `upsert_price_permission` 只写 `after`，没有旧 `minimum_margin/discount_limit/can_approve`。
- 客户专属价删除：`:915-925` 直接 `session.delete(rule)` 后写审计，没有 `before=serialize_customer_price(rule)`，删掉的价格无法复算。
- 价格规则停用：`:530-595` 已有 `before/after`，这一路修好。

结论：三条里一条修好、两条仍缺 before，保留 P2（部分修）。

## 四、仍成立（逐条证据）

| 编号 | 当前代码事实 | 结论 |
|---|---|---|
| B02-04 | `quote/router.py:462-487` 删除只查 `SalesOrder.quote_id`，不判 `quote.status`；`sent/accepted` 仍可软删 | 仍成立 |
| B02-05 | `pricing/service.py:802-824` 最低利润率 `min(rows)` 取最宽松、`can_approve=any`；`resolve_discount_limit:780-800` 折扣上限 `min(rows)` 取最严格 | 多角色口径仍不对称 |
| B02-12 | `quote/schema.py:23` `QuoteCreate.valid_until: date \| None = None`，建单仍可不填；只有更新/版本用 `QuoteValidityEdit` 禁止清空（`:16`、`:45`、`:171`） | 建单可造“永不失效”报价 |
| B02-15 | `quote/lifecycle.py:37-40` `_record` 审计只写 `version_id/version_no`；`:150-151` 接受 `after={}`、`:164-166` 拒绝只加 `reason`，不写金额/币种 | 接受后改费无法复算 |
| B03-04 | `pricing/router.py:96-150` `create_cost` 只锁 SKU、校验四项成本与日期，不查同日同 SKU 已有成本 | 同日重复成本仍可写，核价会 409 |
| B03-07 | `pricing/router.py:389-413` 建价格规则只 `session.get(Sku)`，不判 `deleted_at/status`；导入侧 `pricing/io_router.py` 有停用/删除校验 | 页面仍可给软删/停用 SKU 建规则 |
| B03-20 | `core/importing.py:678-691` 导入审计只写文件、计数、失败行号；不写每行改前/改后价格 | 导入无法逐行复算 |
| B03-21 | `pricing/logistics.py:447-458` 只对 `quantity` 在 `enforce_quantity_unit` 下设 13 位整数上限；`weight_override/volume_override` 只判 `>0`，无上限；落库列 `Numeric(16,4)`（`pricing/model.py:204-206`） | 超大重量/体积仍可溢出 500 |
| B04-04 | `order/router.py:380-405` 转负责人只判 `order:assign`、目标存在、`status=active`，无 `ensure_in_scope` / 客户范围校验 | 可把订单移出原部门范围 |
| B04-05 | `order/schedule.py:190-193`、`:257-260` 只拦 `status == "cancelled"`，`completed` 仍可创建/确认交期变更 | 已完成订单的履约历史仍被重排 |
| B04-08 | `order/schedule.py:144-176` `old_ship` 为空时 `delta=None`，已有批次 `planned_date` 不随新交期平移（`:174-177` 只在 `delta is not None` 时改） | 原无交期订单改期后，批次与交期仍脱节 |
| B04-19 | `order/router.py:1085-1099` 取消审计只写状态、驳回笔数、取消期数；不写订单金额、应收各期金额、回款金额与明细 | 取消无法独立复算 |
| B04-20 | `order/schema.py:168` `request_key: str \| None`；`order/router.py:1306-1315` 只有 `if request_key:` 才 `reserve`，不带键直接建批次 | 无键双击仍生成两条批次 |
| B05-07 | `core/idempotency.py:150-186` `in_flight` 无超时字段/清理任务；`_judge:188-200` 永远 409 | 进程崩溃后同键永久卡死 |
| B05-08 | `payment/router.py:450-478` 不判 `plan.status == "cancelled"`，名称/日期/金额都能改；金额变化仍 `rebalance_order_allocations` | 已取消节点仍可改并触发重平衡 |
| B05-09 | `payment/router.py:526-541` 删除只查 `PaymentRecord.receivable_plan_id` / `PaymentAllocation.plan_id`；被驳回回款仍是 `PaymentRecord`，照样阻止删除；`payment/router.py` 仍无删除/作废回款入口（只有 `:724` 删凭证） | 驳回回款仍堵住应收删除，且无清理入口 |
| B05-10 | `payment/router.py:1259-1265` `or_(PaymentRecord.receivable_plan_id == plan_id, ...)`，第一支不看 `PaymentRecord.order_id`；脏数据可跨订单展示 | 范围收窄但仍成立 |
| B05-19 | `payment/router.py:1209-1215` 确认审计 `after={"comment": ...}`；驳回 `:1230-1238` 只写状态/comment；不写金额、币种、订单、期次、分配 | 回款审计仍不能独立复算 |

## 五、结论

- 本批 27 条：7 修、2 部分、18 仍成立。
- 典型“真修了”的是 B02-03 联系人归属、B02-07 外币闸、B04-07 实发明细缺行、B05-06 ERP 单号匹配、B05-12 分配合计。
- 典型“口头修了但代码没动”的是 B05-07 幂等占位超时、B04-20 批次请求键、B05-19 回款审计金额。
- 下一批：B06–B09 剩余 P2。
