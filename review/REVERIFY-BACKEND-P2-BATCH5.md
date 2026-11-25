# 后端 P2 复核（第五批：金额/精度 + 展示类）

> 日期 2026-11-24，只读当前代码。只审查，未改业务代码。

## 一、结果

本批 15 条：

- **14 条已修**
- **1 条部分修**：B17-04
- **0 条完全未动**

这批大部分是 2026-11-24 的 float→Decimal 专项直接改掉的，不是“计划要修”。

## 二、逐条证据

| 编号 | 当前代码 | 结论 |
|---|---|---|
| B01-02 | `quote/service.py:249` `_f()` 注释“从前转 float，现原样透传”，金额/成本/利润快照另给 `*_exact`；纯比率才显式 float | 已修 |
| B01-03 | `quote/service.py:1338-1340` 底价/换算 `quantize(..., ROUND_HALF_UP)`；`:1958-1965` 合计也 HALF_UP；不再用默认 HALF_EVEN | 已修 |
| B03-05 | `pricing/logistics.py:402-407` 输出 `amount_exact/by_weight_amount_exact`；落库 `:584-592` 用 `Decimal(prepared["quantity_exact"])`、`Decimal(option["amount_exact"])`，不再 float 往返 | 已修 |
| B03-06 | `pricing/logistics.py:452-456` 先把数量按 0.001 HALF_UP 归一，超三位直接 422；后续重量/金额都用归一后的 `quantity`，不再“算 4 位、存 3 位” | 已修 |
| B06-11 | `erp/collection.py:1291-1310` `serialize_record` 直接返回 `row.quantity/row.amount` 的 Decimal，不转 float | 已修 |
| B09-05 | `approval/router.py:96` `total_amount: version.total_amount` 原值，不转 float | 已修 |
| B13-07 | `customer/service.py:391-393` 商机 `expected_amount_exact`；`:415-417` 订单 `total_amount_exact`；`customer/router.py:763` 当前报价版本 `current_version_amount_exact` | 已修 |
| B16-02 | `agent/insights.py:40-44` `_f()` 改 Decimal + ROUND_HALF_UP 两位；`:800` 逐行金额先 Decimal 乘再 quantize；`commentary.py:107-108` Decimal 同样 HALF_UP | 已修 |
| B17-03 | `analytics/service.py:85` 序列化总闸改为保留 Decimal 原值；`targets.py` 差额/达成率补 `*_exact` | 已修 |
| B17-04 | 金额出口已 exact；`currency_note()`（`analytics/service.py:162-190`）只探订单/回款/应收/商机四种 `currency != CNY`，**没有探 Quote.currency**，报价侧外币仍不会触发提醒 | 部分修 |
| B18-02 | `targets.py:100-113` 目标快照带 `sales_target_exact/repeat_customer_target_exact`；`:746-782` 实绩、差额、达成率都有 exact；审计不再只能靠 float 复算 | 已修 |
| B19-03 | `inquiry/service.py:128-130` 数量/目标价 `*_exact`；`inquiry/router.py:207-213` 报价/最低价 `*_exact`；`opportunity/service.py:204` 预计金额 `expected_amount_exact` | 已修 |
| B20-01 | `opportunity/router.py:358/420` 漏斗直接用 `sum(Opportunity.expected_amount)` 的 Decimal；`:696` 报价版本取 `total_amount` 原值；`:716` 需求数量不转 float | 已修 |
| B21-01 | `sample/service.py:34-40` `_f()` 改为原样透传 Decimal；费用/运费/数量/工时/积分不再 `round(float,4)` | 已修 |
| B21-04 | `product_insight/router.py:104-110` `price_assumption_exact`；`:140-148` 评审快照 `Decimal -> format(...,'f')` 原值字符串；转需求描述读 exact | 已修 |

## 三、B17-04 具体缺口

`currency_note()` 的四条探针：

```text
SalesOrder.currency != CNY
PaymentRecord.currency != CNY
ReceivablePlan.currency != CNY
Opportunity.currency != CNY
```

没有 `Quote.currency != CNY`。如果只存在“报价是外币、还没转订单/商机”的数据，分析页不会弹币种提醒；金额本身虽已 exact，但跨币种汇总仍没有业务口径。原 P2 的“数组接口无法挂提醒”部分已通过路由层统一 note 解决，但报价探针缺失仍成立，保留 P2（范围收窄）。

## 四、结论

- 15 条里 14 条已是当前代码事实修复，4 条建议降 P3 的展示类（B01-02、B09-05、B13-07、B20-01）可以直接降。
- B17-04 只剩“Quote.currency 未探”的窄口径，不再是全量 float 问题。
- 下一批：剩余 P2 杂项（B02-12、B03-19、B04-07 等入口/异常项）与 P3 抽样。
