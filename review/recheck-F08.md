# F08 工作台/图表复核：7 条确认

- 范围：`WorkbenchPage.tsx`、`shared/api/analytics.ts`、`KpiStrip.tsx`、`charts/{options,DonutLegend}.tsx`，后端 `analytics/service.py`、`analytics/router.py` 的 dashboard/team/trend 段。
- 方式：逐条重读代码，用 `review/repro/f08_frontend/check_f08.mjs` 做静态断言和数值验证。
- 命令：`node review/repro/f08_frontend/check_f08.mjs`
- 结果：**原报告 4 条全部确认；新增 F08-05、F08-07 两条漏项；原待确认 F08-06 已核实为确认，合计 7 条**。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F08-01 | P2 | 确认 | 工作台 8 个业务查询都没有 `isError`，失败落空列表/`¥0`/永久“加载中”。 |
| F08-02 | P2 | 确认 | Dashboard/Team/Trend 金额契约全是 `number`，后端 `_f` 转 float 汇总，`exactMoney` 救不回精度。 |
| F08-03 | P3 | 确认 | 图表金额 `Math.round` 到元，`compactMoney` 把 199,999 显示成 20 万。 |
| F08-04 | P3 | 确认 | KPI 卡整卡 `div onClick`，无键盘入口；key 用 label，重复 label 会冲突。 |
| **F08-05** | **P2** | **新增确认** | `data_scope=self` 的用户“我的待办”永远显示 `-`、逾期 0 项，即使任务查询有数据。 |
| F08-06 | P2 | 确认 | “进行中商机”卡片不按 `opportunity:view` 收门，后端 summary 只要求 `customer:view`；默认 `finance` 角色正好有 `customer:view`、无 `opportunity:view`。 |

## 二、原报告 4 条复核

### F08-01 工作台查询失败无错误态

8 个业务查询：

```ts
summaryQuery / tasksQuery / risksQuery / funnelQuery
trendQuery / activitiesQuery / opportunitiesQuery / teamQuery
```

全部只读 `.data`，没有一个读 `.isError`。实际落法：

- `summary` 为 `undefined`：6 张 KPI 卡里“我的待办”`-`，“待跟进客户”`-`，金额卡走 `money(undefined)` 变 `¥0`。
- `orderAmountBreakdown(summary)` 在 `!summary` 时返回“货款与运费明细加载中”，失败后永久停在这句，和真加载无法区分。
- `funnel.length === 0` 显示“暂无商机数据”；`activities.length === 0` 显示“暂无动态”；`opportunities.length === 0` 显示“暂无进行中的商机”。
- 任务区只在 `tasksQuery.isSuccess && tasks.length === 0` 显示“今天没有待办”，失败时一片空白。

确认 P2。`refreshWorkbench` 的手动刷新 Toast（`:177`）不能替代区块错误态。

### F08-02 金额契约无 exact、后端 float

前端类型：

```ts
open_opportunity_amount: number
month_won_amount: number
month_received_amount: number
pending_receivable_amount: number
order_amount: number
won_amount_this_month: number
```

后端：

```python
def _f(value) -> float:
    return float(value or 0)

"month_won_amount": _f(month_won_amount)
orders: dict[str, float] = {}
orders[key] = orders.get(key, 0.0) + _f(amount)
```

前端：

```ts
const exactMoney = (value?: number) =>
  `¥${(value ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
```

`exactMoney` 只是把已经转成 float 的 number 再格式化，无法恢复 Decimal。后端没有给 Dashboard/Team/Trend 返回 `*_exact`，和 `SalesUserStat`/`SalesTargetRow` 已有的 exact 约定不一致。确认 P2。

注意：工作台大数字用整数是主人 2026-11-17 明确口径（代码注释 `:230`），所以“KPI 显示整数”不算错；问题在团队成交额/趋势汇总/表格明细仍拿不到精确值。

### F08-03 图表金额取整

```ts
export const asMoney = (value) => `¥${Math.round(value).toLocaleString('zh-CN')}`
if (abs >= 10_000) return `¥${(value / 10_000).toFixed(abs >= 100_000 ? 0 : 1)}万`
```

- `asMoney(123.45)` -> `¥123`。
- `compactMoney(199999)` -> `20万`（实测 `(199999/10000).toFixed(0) === '20'`）。
- `DonutLegend` 百分比 `Math.round((item.value / total) * 100)`，三段各 33% 时合计 99%。

确认 P3，属展示口径，不回写数据。

### F08-04 KPI 卡可访问性

```tsx
<div
  key={item.label}
  className={`kpi-card ...`}
  style={{ cursor: item.onClick ? 'pointer' : 'default' }}
  onClick={item.onClick}
>
```

没有 `role="button"`、`tabIndex`、`onKeyDown`。有 `onClick` 的卡片只能用鼠标；`key={item.label}` 在重复 label 时会冲突。确认 P3。

## 三、新增漏项：F08-05（P2）

### self 范围用户“我的待办”永远显示 `-`

后端 `team_summary` 对 `data_scope=self` 直接早退：

```python
if user.data_scope not in TEAM_SCOPES:
    return {"is_team_view": False, "data_scope": user.data_scope}
```

没有 `members`。前端：

```ts
const myRow = (team?.members ?? []).find((m) => m.user_id === user?.id)
...
{
  label: '我的待办',
  value: String(myRow?.todo_count ?? '-'),
  hint: `逾期 ${myRow?.overdue_count ?? 0} 项`,
}
```

`myRow` 对 self 范围用户永远 `undefined`，所以：

- 第一张 KPI“我的待办”永远显示 `-`；
- 小字永远“逾期 0 项”；
- 即使 `tasksQuery` 已经拿到自己的待办，也完全没用上。

代码注释说这张卡是为了避免拿团队总数（原来 `summary.todo_count` 是全团队 83），改成从成员明细找“我”；但 self 范围没有成员明细，改动把普通业务员的主卡打成了空值。这是本块最大漏项，P2。

修复方向：后端在 self 范围也返回 `my_todo_count/my_overdue_count`，或前端在 `myRow` 缺失时从 `tasks` 兜底（注意 `my_tasks` 默认 `limit=10`，只能当展示兜底，不能当完整计数）。

## 四、F08-06：已确认（原待确认）

“进行中商机”卡片无条件渲染：

```ts
value: String(summary?.open_opportunity_count ?? '-'),
hint: `估算货值 ${money(summary?.open_opportunity_amount)}`,
```

前端有 `canViewOpportunities`，但只用来 gate 漏斗/重点商机区，没有 gate 这张卡。后端 `/dashboard/summary` 只要求 `customer:view`，返回里带 `open_opportunity_count`/`open_opportunity_amount`。

如果存在“有 `customer:view`、没有 `opportunity:view`”的角色，这张卡就会把商机数量和估算金额展示给无商机权限的人。

**已核实：默认 `finance` 角色就是这种组合。** `scripts/seed.py` 的 `FINANCE_PERMISSIONS` 含 `customer:view`、`quote:view`、`order:view`、`payment:view/manage`，**不含 `opportunity:view`**，且 `data_scope=all`。财务登录后 `/dashboard/summary` 能取到全公司 `open_opportunity_count/open_opportunity_amount`，KPI 卡无条件渲染；点卡片进 `/opportunities` 才被权限门挡住。

故 F08-06 由“待确认”落为**确认，P2**：泄露的是聚合数量和估算货值，不是商机明细。

## 五、本块最大漏项

1. **F08-05**：self 范围用户的“我的待办”永久 `-`，这是工作台第一张卡，普通业务员每天都会看到。
2. **F08-01**：8 个查询无错误态，接口故障被伪装成“零/空/加载中”，主管会误判团队没业务。
3. **F08-02**：团队成交额/趋势没有 exact，后端 float 汇总；金额边界无法对上台账。

## 六、新增 F08-07：`/dashboard/team` 对财务下发团队商机/任务数据

- `GET /dashboard/team` 只要求 `customer:view`；`team_summary` 只判 `data_scope in TEAM_SCOPES`，不判 `opportunity:view`/`task:view`。
- 默认 `finance` 角色：`customer:view`、`data_scope=all`，无 `opportunity:view`、`task:view`。因此 `is_team_view=true`，接口返回 `members[].name/todo_count/overdue_count/won_amount_this_month`、`team_task_count/team_overdue_count`、`team_won_amount_this_month`、`risky_opportunities[]`（含 `title/customer_name/stage_name/expected_amount`）、`funnel[]`。
- 前端 `isTeamView = canViewCustomers && team.is_team_view`，财务整块团队概览与成员明细会渲染；风险商机虽只渲染数量，直接调 API 可读标题、客户名、金额。
- **P1**：没有商机权限却拿到商机明细，没有任务权限却拿到成员个人待办/逾期。修法：后端按字段所属权限裁剪，或接口叠加 `opportunity:view`/`task:view`。

## 七、残余

- 静态断言 + Node 数值验证，没有起浏览器做接口 500、键盘操作、真实图表渲染。
- F08-06 已用默认角色矩阵核实；生产环境是否另建过自定义角色未查库，但不影响该结论。
- 未重审 dashboard 全模块（属 B16/B17/B18）；只核对了 summary/team/trend 的金额与权限入口。
- 未改业务代码；F08 的确认项均未修复。
