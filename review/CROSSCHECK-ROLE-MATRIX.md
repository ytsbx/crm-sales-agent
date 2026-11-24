# 角色矩阵反查：F08-06 落定、F10-01 暴露面收窄

## 一、默认角色权限（`backend/scripts/seed.py`）

- `admin`：`data_scope=all`，全部权限。
- `sales_manager`：`data_scope=department_and_sub`，`MANAGER_PERMISSIONS = SALES_PERMISSIONS + ...`，含 `customer:view`、`opportunity:view`、`wecom:view`、`wecom:manage`。
- `salesperson`：`data_scope=self`，`SALES_PERMISSIONS`，含 `customer:view`、`opportunity:view`，**不含任何 `wecom:*`**。
- `finance`：`data_scope=all`，`FINANCE_PERMISSIONS`，含 `customer:view`，**不含 `opportunity:view`**。
- `product_reviewer`：`data_scope=self`，含 `product:review`、`quote:view`，**不含 `customer:view`**。

角色和权限都可以在设置页自建/改配，所以“默认角色”不是唯一组合；但默认矩阵已经足够判定下面两条。

## 二、F08-06：确认成立，升为 P2 确认项

原待确认问题：**是否存在“有 `customer:view`、无 `opportunity:view`”的生产角色？**

答案是 **`finance` 默认角色就是**。

- `/dashboard/summary` 只要求 `customer:view`（`analytics/router.py:497-502`）；
- `dashboard_summary` 里无条件算 `open_count/open_amount`，用 `_scope_filter` 按 `Opportunity.owner_id` 收范围（`analytics/service.py:328-340`）；
- 返回字段是 `open_opportunity_count`、`open_opportunity_amount`（`frontend/src/shared/api/analytics.ts:16-20`）；
- 工作台 KPI 卡片无条件渲染：

```tsx
{
  label: '进行中商机',
  value: String(summary?.open_opportunity_count ?? '-'),
  hint: `估算货值 ${money(summary?.open_opportunity_amount)}`,
  onClick: () => navigate('/opportunities'),
}
```

`canViewOpportunities` 只用在漏斗、重点商机列表和风险查询上，**没有用来挡这张卡**。财务账号能看全公司在谈商机数量和估算货值，点进去才被 `/opportunities` 的权限门挡住。

**结论：F08-06 由“待确认”落为确认，P2。** 泄露的是聚合数量和金额，不是商机明细；但财务没有 `opportunity:view`，不应看到这个口径。修法：后端按 `opportunity:view` 返回 `null` 或 0 并标注无权限；前端在 `canViewOpportunities` 为假时不渲染这张卡。

## 三、F10-01：仍然成立，但暴露面要写准

原表述“无权限用户可见他人客户线索”不准确：默认 `salesperson` 没有 `wecom:view`，默认只有 `admin`、`sales_manager` 能进企微页。

但 P0 仍然成立，因为：

1. `sales_manager` 的 `data_scope=department_and_sub`，候选接口不做数据范围过滤，返回**全公司**客户的 `name/level/region/owner_id/pool_status/score/reasons`；
2. `bind-customer` 只要求 `wecom:manage`，`bind_customer` 用 `get_customer_or_404`，不 `ensure_in_scope`，主管可把外部联系人挂到其他部门的任意客户 id；
3. 自定义角色可以把 `wecom:view`/`wecom:manage` 单独授给没有 `customer:view` 的账号，组合上仍能复现原描述的越权。

所以定性应改为：**默认暴露面是主管跨部门，不是业务员；根因仍是企微权限域未叠加客户数据范围。** 维持 P0，但不再写“任何无权限用户”。

## 四、对总台账的影响

- F08-06：待确认 → 确认，P2。
- 前端确认数：204 → **205**；待确认：5 → **4**。
- F10-01 仍为 P0，但描述收窄为“主管跨部门 + 自定义角色组合”。
