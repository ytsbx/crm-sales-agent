# 工作台权限专项：财务角色能看到什么

## 结论

工作台两条读接口都只校验 `customer:view`，但返回内容远超客户权限：

| 接口 | 后端门槛 | 返回内容 |
|---|---|---|
| `GET /dashboard/summary` | `customer:view` | 在谈商机数量/货值、待办/逾期、待审批、线索、回款 |
| `GET /dashboard/team` | `customer:view` | 团队任务数/逾期、成员姓名与个人待办、本月商机成交额、风险商机明细、商机漏斗 |

默认 `finance` 角色恰好是 `customer:view` + `data_scope=all`，没有 `opportunity:view`、`task:view`。因此财务调用这两条接口，能拿到销售团队的商机与任务数据。

## 一、`/dashboard/team` 的越权返回（新增 F08-07）

`analytics/router.py:559-567`：

```python
@router.get("/dashboard/team")
async def dashboard_team(
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await _with_fx_note(session, user, await svc.team_summary(session, user)))
```

`team_summary` 只判两件事：

1. `user.data_scope in TEAM_SCOPES`（`department` / `department_and_sub` / `all`）；
2. 按 `scoped_owner_ids` 收范围。

没有判 `opportunity:view` 或 `task:view`。财务 `data_scope=all`，所以 `is_team_view=True`，返回：

- `team_task_count` / `team_overdue_count`：全公司未完成任务数和逾期数；
- `members[]`：每个成员的 `name`、`todo_count`、`overdue_count`、`won_amount_this_month`、`stale_customer_count`；
- `team_won_amount_this_month`：本月商机成交额；
- `risky_opportunities[]`：`id/title/customer_name/stage_name/expected_amount/expected_close_date/days_left/risk_reason`，最多 10 条；
- `funnel[]`：各阶段商机数量和金额。

前端 `WorkbenchPage.tsx`：

```tsx
const isTeamView = canViewCustomers && Boolean(team?.is_team_view)
```

财务满足，整块“团队概览 + 成员明细”会渲染；风险商机虽只渲染数量，接口已把标题、客户名、金额下发，直接调 API 可读全。

**定级 P1。** 不是“财务不能看业绩”的业务争论，而是权限码层面：没有 `opportunity:view` 却拿到商机标题/客户名/金额，没有 `task:view` 却拿到成员个人待办与逾期。修法：`team_summary` 按 `user.has('opportunity:view')` / `user.has('task:view')` 分别裁剪字段；或 `/dashboard/team` 叠加对应权限，前端按同一权限隐藏列。

## 二、`/dashboard/summary` 的同类问题（已记 F08-06）

`/dashboard/summary` 同样只要求 `customer:view`，`dashboard_summary` 无条件计算并返回：

- `open_opportunity_count` / `open_opportunity_amount`：在谈商机数量和估算货值；
- `todo_count` / `overdue_task_count`：任务数；
- `open_lead_count`：线索数；
- `pending_approval_count`：待审批数。

其中在谈商机 KPI 前端无条件渲染，已作为 F08-06 记 P2。`todo_count` 前端没有直接用作“我的待办”（改用 `team.members` 的 `myRow`），但接口仍对无 `task:view` 的账号下发任务聚合数；`open_lead_count` 同理对无 `lead:view` 的账号下发。

**建议合并为一条根因：工作台读接口只按 `customer:view` 收门，未按字段所属模块的权限码裁剪。** F08-06 是商机 KPI，F08-07 是团队/风险商机明细。

## 三、对总台账的影响

- 新增 **F08-07（P1）**：`/dashboard/team` 对财务下发成员任务、商机成交额、风险商机明细与漏斗。
- 前端确认数：204 → **206**；P1：18 → **19**；待确认仍 4。
- 原 F08-06 保持 P2（在谈商机 KPI）。
