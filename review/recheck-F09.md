# F09 设置与审计复核：10 条

- 范围：`SettingsPage.tsx`、`AuditLogPanel.tsx`、`PersonalProfilePanel.tsx`、`shared/api/settings.ts`，以及后端 `user/schema.py`、`customer/tags.py` 的相关段。
- 方式：逐条重读当前代码，用 `review/repro/f09_frontend/check_f09.mjs` 做静态断言和数值验证。
- 命令：`node review/repro/f09_frontend/check_f09.mjs`
- 结果：**10 confirmed，0 missing**。原报告没有误报。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F09-01 | P2 | 确认 | 用户/角色/部门/标签/字典/公海列表失败时都显示“还没有…”，失败被当空。 |
| F09-02 | P2 | 确认 | 编辑标签只提交 `name/type`，状态选择器不生效。 |
| F09-03 | P2 | 确认 | 新建角色选“停用”不生效，`RoleCreate` 根本不收 `status`。 |
| F09-04 | P2 | 确认 | 审批分档金额上限 `Number` 解析，16 位两位小数少 1 分，可能错档。 |
| F09-05 | P2 | 确认 | 公海天数失焦与开关并发，两个全字段 PATCH 互相覆盖旧值。 |
| F09-06 | P2 | 确认 | 审计查询失败显示“没有符合条件的记录”，且 `placeholderData` 会把上一筛选的旧行留着。 |
| F09-07 | P2 | 确认 | 审计明细直接 `<pre>{JSON.stringify(...)}</pre>`，违反“前端不展示原始 JSON”的固定约束。 |
| F09-08 | P3 | 确认 | 我的权限查询失败显示“你还没有任何权限”，把故障说成权限被清空。 |
| F09-09 | P3 | 确认 | 部门上级下拉只排除自己，不排除下级；界面却写“不能选下级”。 |
| F09-10 | P3 | 确认 | 公海天数填 0/负数/小数只弹 Toast，非受控输入框仍显示非法值。 |

## 二、P2 复核

### F09-01 列表失败当空态

六个查询都没有 `isError`：

```ts
usersQuery / rolesQuery / deptsQuery
poolRulesQuery / dictionariesQuery / tagsQuery
```

失败后 `data ?? []` 直接落空态：

- 用户表：无筛选 `还没有用户`，有筛选 `没有符合条件的用户，试试放宽筛选条件`。
- 角色表：`还没有角色`；部门表：`还没有部门`；标签：`还没有标签`；字典：`还没有数据字典项`；公海表：空表。
- 更隐蔽的是用户/角色编辑弹窗：`roleOptions`、`deptOptions` 都从这些查询来，失败后下拉为空，管理员无法分配角色/部门。

只有 `settingsQuery.isError` 有“无法读取业务设置”，`taskRulesQuery.isError` 交给 `AutoTaskRulesPanel`；其余六个列表没有错误分支。

### F09-02 标签状态不提交

```ts
updateTag(tagEditTarget!.id, { name: tagEditTarget!.name, type: tagEditTarget!.type })
```

弹窗里有完整的 `status` Select，`onChange` 写 `tagEditTarget.status`；后端 `update_tag`：

```python
for field in ("status", "sort_no"):
    if field in data:
        ...
```

只要传就生效，但前端没传。保存后 Toast“标签已保存”，标签仍是启用，客户详情仍能选到。确认 P2。

### F09-03 新建角色状态不提交

```ts
: createRole({
    code: roleForm.code,
    name: roleForm.name,
    description: roleForm.description || null,
    data_scope: roleForm.data_scope,
    permission_codes: roleForm.permission_codes,
  })
```

`RoleCreate` 只有 `code/name/description/data_scope/permission_codes`，`extra="ignore"`，没有 `status`；`RoleUpdate` 才有。弹窗对新建/编辑共用同一个状态 Select，新建时默认 `active`，用户可改成 `disabled`，但被后端静默丢掉。确认 P2。

### F09-04 审批金额上限丢分

```ts
const AMOUNT_TEXT_PATTERN = /^\d+(\.\d{1,2})?$/
const parsed = Number(text)
prevMax = parsed
next.push({ ..., max_amount: parsed, ... })
```

正则允许 16 位，`Number('99999999999999.99')` 实测 `99999999999999.98`。后端 `_strict_amount` 接受 string，但前端发的是 JSON number，已无法恢复原文；`prevMax` 也是 number，逐档递增比较同样有误差。

这是审批路由阈值：边界报价可能落进下一档，找错审批角色。确认 P2。

### F09-05 公海规则并发覆盖

```ts
updatePublicPoolRule(id, { days, enabled })
```

- 天数失焦：`{ days: next, enabled: record.enabled }`
- 开关：`{ days: record.days, enabled: value }`

两个 PATCH 都带全字段，mutation 没有 per-row pending/串行。请求交错时，后到的会把先改的字段回退：

- 先改 30→60，再点开关；开关请求带旧 `days: 30`，最终可能回到 30。
- 两个 Toast 都显示“公海规则已保存”，没有冲突提示。

确认 P2。公海回收天数直接影响客户何时进入回收候选。

### F09-06 审计失败显示“没有记录”

```ts
const rows = query.data?.items ?? []
const total = query.data?.total ?? 0
...
placeholderData: (prev) => prev,
...
<Table loading={query.isFetching} dataSource={rows} empty="没有符合条件的记录">
```

没有 `query.isError`。失败时有两种坏结果：

- 首次加载失败：空表 + “没有符合条件的记录”，合规上等于告诉你“这段时间没有任何操作”。
- 已加载后改筛选再失败：`placeholderData` 保留上一筛选的旧行，**新筛选条件下显示旧数据**，比空表更危险。

确认 P2。

### F09-07 审计明细原始 JSON

```tsx
<pre style={preStyle}>{JSON.stringify(detail.before_data, null, 2)}</pre>
<pre style={preStyle}>{JSON.stringify(detail.after_data, null, 2)}</pre>
```

`QUALITY_GOAL.md` 的 2026-11-16 固定约束：“前端不展示技术诊断信息，包括实例/请求等内部标识、原始 JSON、调试参数、服务地址和实现说明。” 审计明细不是例外；同一页已有 `auditChangeSummary`、`objectLabel`、中文动作标签，可以改成结构化字段差异。确认 P2。

## 三、P3 复核

### F09-08 权限查询失败

```ts
const owned = permQuery.data?.permissions ?? []
...
!permQuery.isLoading && owned.length === 0
  -> 你还没有任何权限，请联系管理员分配角色。
```

没有 `isError`。500/超时后用户会以为权限被管理员清空。确认 P3。

### F09-09 部门上级下拉

```tsx
optionList={deptOptions.filter((d) => d.value !== deptEditTarget?.id)}
```

只排除当前部门自己，未排除后代；说明文字却写“不能选它自己，也不能选它的下级”。后端 `_assert_no_cycle` 会拒绝，但用户是在一个承诺过过滤的控件里选到非法值，最后吃 422。确认 P3。

### F09-10 公海天数非法值不回填

```tsx
<Input defaultValue={String(days)} onBlur={...} />
...
if (!raw || !Number.isInteger(next) || next <= 0) {
  Toast.warning('公海回收天数须为正整数')
  return
}
```

`defaultValue` 非受控；校验失败只 Toast + return，没有 `event.target.value = String(days)`。输入框仍显示 `0`/`-1`/`1.5`，用户以为已保存或下次刷新才“跳回去”。确认 P3。

## 四、本块最大漏项

1. **F09-03 + F09-02**：两个“看起来能改、实际不提交”的状态控件。角色停用不生效会把带权限的角色直接启用；标签停用不生效会让客户继续选到已“停用”的标签。
2. **F09-06**：审计失败显示“没有记录”，且旧筛选结果会冒充新筛选结果；合规取证页不能把故障当“无审计”。
3. **F09-05**：公海天数/开关并发覆盖，管理员以为改了 60 天，实际回到 30 天。
4. **F09-04**：审批分档金额上限是决定“找谁批”的阈值，16 位边界少 1 分就可能错档。

## 五、残余

- 静态断言 + Node 数值验证，没有起浏览器做并发点击、接口 403/500、审计 SideSheet 渲染。
- 没有跑后端测试；标签/角色/公海规则的后端事务只按代码路径判断。
- `PermissionPicker`、`EmptyState`、`AuditLogPanel` 的真实视觉/滚动未做 E2E。
- 未改业务代码；F09 的 10 条确认项均未修复。
