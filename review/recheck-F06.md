# F06 OA 组件 + 商机/线索页复核：19 条

- 范围：`ResolveOaActions.tsx`、`AutoOaSync.tsx`、商机列表/详情/看板/记录、线索页、`opportunity.ts`、`inquiry.ts`、`lead.ts`。
- 方式：逐条重读当前工作区代码，用 `review/repro/f06_frontend/check_f06.mjs` 静态与数值复现。
- 命令：`node review/repro/f06_frontend/check_f06.mjs`
- 结果：**19 confirmed，0 missing**。没有整条误报。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F06-01 | P1 | 确认 | 新建商机预计金额先 `parseOptionalNumber` 转 number，16 位边界少 1 分。 |
| F06-02 | P2 | 确认 | 需求明细数量 `Number`，13 位 3 位小数边界丢最后一位。 |
| F06-03 | P2 | 确认 | 漏斗 `Math.round` 抹掉分位；看板金额源值已是 float。 |
| F06-04 | P2 | 确认 | 新建商机无请求键，超时重试多一条商机。 |
| F06-05 | P2 | 确认 | 添加需求明细无请求键，超时重试多一条明细。 |
| F06-06 | P2 | 确认 | 定制询价生成报价的 `unit_cost/quoted_price/quantity` 是 number，正式报价草稿从错值开始。 |
| F06-07 | P2 | 确认 | 明细/跟进/任务列表失败显示“还没有”，失败当空。 |
| F06-08 | P2 | 确认 | AI 结果只存 envelope 不存 kind，切页签会把旧分析显示到推荐区。 |
| F06-09 | P2 | 确认 | 看板阶段查询失败无错误态，统计有数据也渲染空列。 |
| F06-10 | P3 | 确认 | OA 重发/作废每次点击新键，失败重试不是同一次核定；链接无 pending 禁用。 |
| F06-11 | P3 | 确认 | `syncOaInstance` POST 放进 `useQuery`，后台标签页仍 10 秒轮询。 |
| F06-12 | P3 | 确认 | 线索新建/转化无请求键；新建重复落库，转化靠后端状态闸门。 |
| F06-13 | P3 | 确认 | 分配/释放不传 reason；分配失败只 refetch + Banner；金额只判非负。 |
| F06-14 | P3 | 确认 | 定制询价数量 `Number` 且无请求键。 |
| F06-15 | P3 | 确认 | 商机记录订单金额 `toTwoDecimals(number)` 二次舍入。 |
| F06-16 | P3 | 确认 | 商机列表阶段/漏斗查询失败无错误态。 |
| F06-17 | P3 | 确认 | 看板推进无卡片级 pending，连点两次都发，第二次 409 弹失败。 |
| F06-18 | P3 | 确认 | 定制询价创建 API 无请求键，与 F06-14 同根。 |
| F06-19 | P3 | 确认 | 金额 0 被 truthy 判断显示成 `-`/“未填金额”。 |

## 二、P1：F06-01

`OpportunityListPage.tsx`：

```tsx
expected_amount: (() => {
  const v = parseOptionalNumber(form.expected_amount)
  if (typeof v === 'number' && Number.isNaN(v)) {
    throw new Error('预计金额只能填数字')
  }
  return v
})(),
```

`parseOptionalNumber` 返回 JS number；API 类型 `expected_amount?: number | null`；后端 schema 是 `Decimal`，`max_digits=16, decimal_places=2`。Node 实测：

```text
Number('99999999999999.99') -> 99999999999999.98
```

即请求还没发出，金额已经少 1 分。商机预计金额、漏斗/看板汇总、后续报价草稿参考金额都从错值出发。

修复：表单改用 `parseOptionalDecimal` 保留字符串，API 类型改 `string | null`，后端补 `expected_amount_exact`；前端加 16 位/2 位小数校验，别把 422 当唯一防线。

## 三、P2 复核

### F06-02 明细数量

```tsx
quantity: itemForm.quantity ? Number(itemForm.quantity) : 1
```

`Number('9999999999999.999')` 实测变 `9999999999999.998`。后端 `Decimal(16,3)`，前端先丢一位，后续核价/报价明细数量跟着错。

### F06-03 漏斗/看板

```tsx
¥{Math.round(row.amount).toLocaleString('zh-CN')}
```

漏斗金额直接取整到元：`1234.56` 显示 `1235`，少 0.44。看板 `money` 虽保留两位，但源值已经是后端 `_number(Decimal)` 的 float，16 位边界仍不准。

### F06-04 / F06-05 创建无键

- `createOpportunity` API 只 `api.post('/opportunities', payload)`；前端 payload 没有 `request_key`；后端创建路由直接 `Opportunity(...)+commit`。
- `createItem` 同样没有键；后端 `item = OpportunityItem(**payload.model_dump())` 直接 add。

超时后用户再点，会多一条商机/明细；后续漏斗和报价明细都会重复计数。

### F06-06 定制询价转报价

`createQuoteFromInquiry` 入参：

```ts
unit_cost: number
quoted_price: number
quantity?: number | null
```

这是“正式报价草稿”的源头，金额在进后端前已经 number 化。调用点在 `KnowledgePage`（F11 范围），本条只确认 API 契约；F11 复审时要一起验收输入控件。

### F06-07 列表失败当空

`itemsQuery/followupsQuery/tasksQuery` 都是 `data ?? []` + `emptyLine('还没有...')`，没有 `isError`。接口 500/超时后，用户看到“还没有需求明细/还没有跟进记录/还没有任务”，会重复添加或误判客户没跟进。

### F06-08 AI 结果串段

`onSuccess: (data) => setAiEnvelope(data)` 只存结果，不存 `kind`；渲染只判 `aiKind === 'recommend' && <AgentInsight envelope={aiEnvelope}/>`。先跑商机分析、未返回时切推荐，旧分析结果会显示到推荐区；旧请求后到也会覆盖新结果。

### F06-09 看板阶段失败

只处理 `statsQuery.isError`，没有 `stagesQuery.isError`。`stages=[]` 时直接 `stages.map(...)` 渲染空横向容器。统计有数据、阶段失败，用户看到一片空白，以为商机被清空。

## 四、P3 复核要点

- **F06-10**：`onConfirm={() => resolve.mutate({ action: 'resend', requestKey: newRequestKey() })}`、abandon 同型。每次点击新键；`Popconfirm` 触发链接没有 `resolve.isPending` 禁用。后端状态机能挡重复建单，但前端拿不到“上次已成功”的确定回执。
- **F06-11**：`syncOaInstance(row.id)` 是同步外部 OA 的 POST，被包在 `useQuery`；`refetchIntervalInBackground: true` + 10 秒；后端 3 秒快照复用降低压力，但后台仍持续外部请求。
- **F06-12**：`createLead(form)`、`convertLead(id, {...})` 都没有 `request_key`。新建后端直接 `Lead(...)+commit`，重试多一条；转化有 `converted` 状态/行锁，但前端无法区分“已成功未回执”。
- **F06-13**：`assignLead(leadId, ownerId)` 不传 reason；`releaseLead(id)` API 根本没 reason 参数；分配失败 `onError: () => usersQuery.refetch()` 只刷新接收人，靠 Banner 显示错误；`invalidOpportunityAmount` 只判 `Number(...) < 0`，`1e20` 能过前端。
- **F06-14**：`quantity: inquiryForm.quantity.trim() ? Number(inquiryForm.quantity) : null`；`createCustomInquiry` 无键。与 F06-18 同根。
- **F06-15**：`formatMoney(toTwoDecimals(row.total_amount), 'CNY')`。订单金额出参已是 number，这里再 `toFixed(2)` 是二次舍入，边界差 1 分。
- **F06-16**：`stagesQuery`/`funnelQuery` 没有 `isError`；失败时阶段下拉为空、漏斗条不渲染，页面像“没有阶段/没有漏斗”。
- **F06-17**：`move` 直接 `await changeStage(...)`，无卡片级 in-flight 集合/按钮 pending。快速连点两次，第二次目标阶段已变，后端 409，前端一成功一失败。
- **F06-18**：`createCustomInquiry` 直接 POST，无 `request_key`；与 F06-14 一起修。
- **F06-19**：详情预计金额 `opportunity.expected_amount ? ... : '-'`，明细目标价 `v ? ... : '-'`，看板 `value ? ... : '-'`；0 元被当成空值，用户无法区分“0 元”和“未填”。

## 五、本块最大漏项

1. **F06-01**：新建商机的金额入口，number 化后所有漏斗/看板/报价参考都从错值开始。
2. **F06-04 + F06-05**：商机和需求明细都是核心业务对象，没有请求键，弱网重试直接重复计数。
3. **F06-06**：定制询价转报价是“生成钱”的动作，API 契约仍是 number；F11 接调用点时必须一起改。

## 六、残余

- 静态断言 + Node 数值验证，没有起浏览器做弱网、连点、切页签、16 位输入。
- F06-06 只审 API 契约，`KnowledgePage` 的输入控件属 F11，未逐行复核。
- F06-10/11 只按前端调用与后端状态机判断，没有连接真实同序 OA。
- 未改业务代码；F06 的 19 条确认项均未修复。
