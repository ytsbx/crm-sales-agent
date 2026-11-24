# F10 企微 / 任务 / 回收站复核：9 条确认 + 1 条证伪

- 范围：`WeComPage.tsx`、`shared/api/wecom.ts`、`TaskListPage.tsx`、`shared/api/task.ts`、`RecycleBinPage.tsx`、`PoolRecyclePage.tsx`、`shared/api/recycle.ts`，以及后端 `wecom/router.py`、`wecom/service.py`、`task/router.py`、`customer/service.py` 相关段。
- 方式：逐条重读当前代码，用 `review/repro/f10_frontend/check_f10.mjs` 静态断言。
- 命令：`node review/repro/f10_frontend/check_f10.mjs`
- 结果：**9 confirmed，0 pending，1 refuted（F10-10），0 missing**。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F10-01 | P0 | 确认（与 B15 同根） | 企微候选/建客/绑客只看 `wecom` 权限，绕过 `customer:view/create/assign`。 |
| F10-02 | P2 | 确认 | 待确认列表失败显示“没有待确认客户”。 |
| F10-03 | P2 | 确认 | 新建任务无请求键，超时重试多一条任务。 |
| F10-04 | P2 | 确认 | 勾选跨筛选/跨页保留，批量完成/交接会带上隐藏任务。 |
| F10-05 | P1 | 确认 | 待办交接目标只校验“在职”，不限部门/数据范围。 |
| F10-06 | P2 | 确认 | 企微单条建客/绑客无请求键，第二次重试报“已经归一过了”。 |
| F10-07 | P2 | 确认 | 独立 `/pool-recycle` 无权限壳，无复核权限仍能看到并点批准/暂缓/驳回。 |
| F10-08 | P3 | 确认 | 线索窄屏把“历史操作人待核实”显示成 `—`。 |
| F10-09 | P3 | 确认 | 待确认列表最后一页正好 20 条时还能翻出空页。 |
| F10-10 | — | **证伪** | 恢复时指定新负责人复用 `transfer_customer`，跨部门转交是已验收行为；`QUALITY_PROGRESS.md:150` 有主管转交外部门成功记录。 |

## 二、P0/P1 复核

### F10-01 企微归一页权限绕过（P0）

前端：

```ts
const canManage = can('wecom:manage')
const canSearchCustomers = canManage && can('customer:view')
...
const candidatesQuery = useQuery({
  queryKey: ['wecom-candidates', activeId],
  queryFn: () => getUnboundCandidates(activeId!),
  enabled: Boolean(activeId),
})
...
{(candidatesQuery.data?.candidates ?? []).map((candidate, index) => (
  ...
  {candidate.name}
  {candidate.reasons.slice(0, 3).join('；')}
)}
```

- `candidatesQuery` 只按 `activeId` 开启，**没有** `can('customer:view')`；候选卡无条件渲染客户名、匹配分、匹配原因。
- “关联已有客户”“创建新客户”整组按钮只在 `canManage` 下，没有 `customer:create`/`customer:assign`。

后端：

```python
async def unbound_candidates(... require_permission("wecom:view"))
async def bind_customer(... require_wecom_action("wecom:manage"))
async def create_customer(... require_wecom_action("wecom:manage"))
```

服务层明确“这里**故意不做数据范围过滤**”，注释说“结果本身不直接对业务员开放：由有 `wecom:manage` 的人确认”，但路由把候选开放给所有 `wecom:view`，前端也没有按 `wecom:manage` 隐藏候选。

后果：

- 只有 `wecom:view` 的人能看到全库客户名、等级、地区、负责人、匹配理由，绕过客户数据范围。
- 只有 `wecom:manage`、没有 `customer:create` 的人可以建客户并成为负责人；绑客也只要求 `wecom:manage`，可把联系人挂到任意客户 id。
- 批量导入要求 `wecom:import + customer:create + customer:assign`，单条建客/绑客只要一把 `wecom:manage`，形成绕过口。

**定级说明**：本轮按“越权可见 = P0”记 P0；但 `BACKEND-FINAL.md` 的 B15-01/B15-02 已按 P1 记录同一根因。建议验收时合并成一条，不要同一漏洞两个级别。

### F10-05 待办交接目标越范围（P1）

后端候选人：

```python
stmt = select(User).where(User.status == "active")
```

没有部门/数据范围过滤。批量交接：

```python
target = await _active_owner(session, payload.owner_id)
```

`_active_owner` 只校验“存在 + 在职”，不校验目标是否在主管部门/数据范围。任务本身有范围校验（`subject in owner_ids`、原负责人同部门），但**目标**没有。

后果：销售主管可以把停用员工的待办交给其他部门/范围外的人；接手人能看到任务标题、客户名和待办内容。和 B22-05“任务负责人可改派范围外用户”同根。

## 三、P2 复核

### F10-02 待确认列表失败当空

```tsx
const rows = listQuery.data?.items ?? []
...
{rows.length === 0 ? (
  <Empty description={listQuery.isLoading ? '加载中…' : '没有待确认客户'} />
)
```

没有 `listQuery.isError`。403/500/超时后显示“没有待确认客户”，中栏/右栏也跟着显示“请先选择联系人”，操作者会以为队列已清空。确认 P2。

### F10-03 新建任务无请求键

```ts
export function createTask(payload: Record<string, unknown>) {
  return api.post<Task>('/tasks', payload)
}
```

`createTask` 只发标题/优先级/截止时间，没有请求键；后端 `POST /tasks` 直接 `TaskCreate` 落库。超时重试会多一条同样的任务，重复提醒、重复统计、重复交接。确认 P2。

### F10-04 勾选跨筛选/跨页保留

```ts
const [selectedVersions, setSelectedVersions] = useState<Record<number, string>>({})
const selectedIds = Object.keys(selectedVersions).map(Number)
```

- `setFilter` 改状态/负责人/逾期/页码时不清 `selectedVersions`；普通任务页翻页也不清。
- 只有“待办交接”页的翻页/每页条数调用了 `clearSelection()`。
- `batchCompleteMutation` 与 `batchHandoverMutation` 直接提交全部 `selectedIds`。

后果：用户在当前页勾选，切到别的筛选/翻页后，屏幕上已经看不到的任务仍被批量完成/交接。确认 P2。

### F10-06 企微单条建客/绑客无请求键

```ts
bindUnboundContact(contactId, { customer_id })
createCustomerFromContact(contactId, { name, region, level })
```

API 没有 `request_key`。后端用 `crm_contact_id` + `with_for_update` 防二次，第二次抛 422“这条联系人已经归一过了”。不会重复建客户，但用户看到的是失败 Banner，可能转去客户中心手工再建一个，反而造成真正重复。确认 P2。

### F10-07 独立 `/pool-recycle` 无权限壳

```tsx
{ path: 'pool-recycle', element: <PoolRecyclePage /> }
```

页面只有：

```ts
const canRestore = can('customer:pool_review') && can('customer:pool_restore')
```

没有 `canReview`。待复核/已暂缓行的“批准回收/提前回收/暂缓/驳回”和批量工具条都无条件渲染；无 `customer:pool_review` 的人打开老链接，列表 403 后按钮还在，点了才吃 403。确认 P2。

## 四、P3 复核

### F10-08 线索窄屏删除人

```tsx
compact ? (
  <span>{record.deleted_by_name ?? '—'}</span>
) : (
  renderDeletedBy(v, record.deleted_by_pending)
)
```

窄屏分支没看 `deleted_by_pending`，历史操作人未知显示成 `—`；产品/SKU/客户表的窄屏分支都有“历史操作人待核实”，只有线索表漏了。确认 P3。

### F10-09 待确认列表下一页

```tsx
<Button disabled={rows.length < 20} onClick={() => setPage((current) => current + 1)}>
```

接口没有下发 `total`；最后一页正好 20 条时按钮仍可点，下一页空数组显示“没有待确认客户”。确认 P3。

## 五、F10-10 证伪：跨部门转交是已验收行为

恢复客户时：

```ts
listUsers({ status: 'active', keyword: ownerKeyword, page: pageParam, page_size: 20 })
```

没有部门/数据范围参数。后端 `restore_customer`：

```python
target = await session.get(User, new_owner_id)
if target is None: 404
if target.status != "active": 422
```

只校验存在/在职，然后复用 `transfer_customer`，同样不限目标部门。之前把它记为“待业务定”，但 `QUALITY_PROGRESS.md:150` 已记录：

> 销售主管真实登录验证转交外部门后仍看到成功回执，并显示客户已离开查看范围。

跨部门转交是已验收行为，恢复只是复用同一服务。**F10-10 证伪，不计缺陷。** UI 仍可加“将转出本部门”的提示，但这不是权限漏洞。

## 六、本块最大漏项

1. **F10-01**：候选读取全库客户 + 单条建客/绑客只要 `wecom` 权限，读、写两侧都绕过客户权限；与 B15-01/B15-02 同根。
2. **F10-05**：待办交接只校验目标“在职”，不限部门/范围；任务可以交给范围外的人。
3. **F10-07**：独立 `/pool-recycle` 无页面权限壳，无复核权限仍能看到批准/驳回/暂缓按钮。
4. **F10-04**：批量操作使用全部 `selectedIds`，跨筛选/跨页隐藏的任务也会被一起完成/交接。

## 七、残余

- 静态断言，没有起浏览器做多角色权限组合、弱网重试、跨页勾选、窄屏渲染。
- 没有跑后端测试；`test_wecom_operation_permissions.py` 只覆盖离职交接，不覆盖候选/建客/绑客；任务交接无目标范围用例。
- F10-01 与 B15 的级别冲突未合并，建议验收时统一。
- 未改业务代码；F10 的确认项均未修复。
