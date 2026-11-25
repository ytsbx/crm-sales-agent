# F01 报价页复核：14 条逐条证伪

- 范围：`QuoteDetailPage.tsx`、`QuoteListPage.tsx`、`QuoteSendLogsPanel.tsx`、`WhatIfPanel.tsx`、`sendChannel.ts`、`shared/api/quote.ts`。
- 方式：重读实现与后端 `quote/service.py`、`order/service.py`，再用 `review/repro/f01_frontend/check_f01.mjs` 做静态+数值复现。
- 命令：`node review/repro/f01_frontend/check_f01.mjs`
- 结果：**14/14 confirmed**。没有整条误报，但 F01-01 的根因在后端，前端只是把入口露出来。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F01-01 | P0 | 确认 | 已转订单的报价仍可新建版本、再审批/发送/接受/转单；转单只按 `quote_version_id` 查重，能生成第二张正式订单和应收。 |
| F01-02 | P1 | 确认 | URL 的 `version` 不校验是否属于 `quoteId`；B 版本可挂到 A 报价页，删除、合同、单据、What-if 会串单。 |
| F01-03 | P1 | 确认 | 费用金额走 JS `number`，`amount_exact`/`total_amount_exact` 又被 `Number()` 化；16 位金额丢 1 分。 |
| F01-04 | P2 | 确认 | 发送记录任一版本查询失败，整块显示“还没有发送记录”，错误伪装成空态。 |
| F01-05 | P2 | 确认 | 删除费用 `.then(refresh)` 无 `.catch`，失败无提示、不刷新。 |
| F01-06 | P2 | 确认 | What-if 的 `targetPrice`/`scenarios` 是内部 state，版本切换不重置，旧价格套到新版本。 |
| F01-07 | P2 | 确认 | 新建报价每次打开弹窗换 `request_key`；超时后关闭重开再提交，会建第二条报价。 |
| F01-08 | P2 | 确认 | 新建版本接口没有 `request_key`，超时重试可多建一版。 |
| F01-09 | P2 | 确认 | `canEditFreightOnly` 不看 `accepted_at`/`declined_at`/是否已转单，客户接受后仍可改运费。 |
| F01-10 | P3 | 确认 | 改价只判 `Number(newPrice) <= 0`，`1e309` 变 `Infinity` 能过；批量录入/费用/收件人长度校验不全。 |
| F01-11 | P3 | 确认 | 自定义发送渠道写入 localStorage，读取只认白名单，下次回落“企业微信”。 |
| F01-12 | P3 | 确认 | 发送记录按版本逐个 `GET`，版本多时 N 个请求，没有聚合/缓存。 |
| F01-13 | P3 | 确认 | What-if 金额/成本/利润全用 JS `number` 估算，边界与后端 Decimal 不一致。 |
| F01-14 | P3 | 确认 | 详情 500/网络错误统一显示“不存在或无权查看”，不区分错误、不提供重试。 |

## 二、P0：F01-01 重复转单链路

前端入口只判：

```tsx
'新建版本': Boolean(canManage && !rejection)
```

后端 `quote/service.py:1138-1142` 建完新版直接把 `quote.status = "draft"`，不检查这张报价是否已有订单；`order/service.py:221-231` 转单只查：

```python
select(SalesOrder).where(SalesOrder.quote_version_id == version.id)
```

唯一索引也只是 `quote_version_id`。所以：

1. V1 审批、发送、客户接受，转成 O1；
2. 回同一报价页点“新建版本”，生成 V2，报价状态回 `draft`；
3. V2 重新审批/发送/客户接受；
4. 点“转销售订单”，`quote_version_id` 与 O1 不同，查重不命中，生成 O2。

**这是重复入账，不是多一个版本。** 第二张订单会再生成应收计划，业绩、发货、回款都按两张单走。

修复不能只藏按钮：前端在 `quote.status === 'converted'` 或已有订单时隐藏/禁用“新建版本”和“转销售订单”；后端必须在转单事务内查 `SalesOrder.quote_id == quote.id AND status != 'cancelled'`，或明确允许“一价多单”并改唯一约束与业务口径。先定业务口径，再动约束。

## 三、P1 两条

### F01-02 版本归属串单

`QuoteDetailPage.tsx:205-219`：

```tsx
const requestedVersionId = searchParams.get('version') ? Number(...) : null
const allVersionIds = (versionsQuery.data ?? []).map((item) => item.id)
const versionId = requestedVersionId ?? allVersionIds[0]
```

`requestedVersionId` 没有 `allVersionIds.includes(...)` 校验。页面标题/明细/客户取 B，`quoteId` 仍是 A：

- 删除确认框显示“确认删除报价 B？”，实际 `deleteQuote(A)`；
- “新建版本”建在 A 上；
- 合同面板列 A 的合同，对外单据查 A 但生成时用 B 的 `versionId`；
- What-if 用 A 的对比接口 + B 的明细基线。

后端 `GET /quote-versions/{id}` 只校验“当前用户可见该版本”，不校验它属于 URL 的 quote。**前端必须校验归属，后端最好接受可选 `quote_id` 并复核。**

### F01-03 金额精度

`parseChargeAmount` 返回 `number`，提交的是 JSON number；`cny()` 和费用合计又对 `*_exact` 做 `Number()`：

- `Number('99999999999999.99')` 在 Node 实测是 `99999999999999.98`；
- 后端 `Numeric(16,2)` 允许 14 位整数 + 2 位小数，恰好在边界丢 1 分；
- 费用表 `v.toFixed(2)` 用 number，费用合计 `reduce(Number(charge.amount))`，应付合计 `cny(total_amount_exact)` 也先转 number。

这不是“显示差一分”，费用入参本身就是 number，入库金额已经少了 0.01。修复：费用提交用 `amount_exact` 字符串，展示优先 exact，合计用十进制/整数分，`cny()` 不要对字符串再 `Number()`。

## 四、P2/P3 复核要点

- **F01-04**：`QuoteSendLogsPanel` 的 `useQuery` 没有 `isError` 分支；`Promise.all` 任一失败整块失败，`rows.length === 0` 直接渲染“还没有发送记录”。审计场景不能把查询失败当“从未发送”。
- **F01-05**：同页其他 mutation 都有 `onError: Toast.error`，只有删除费用是 fire-and-forget；失败时 `Popconfirm` 已关闭、表格未刷新。
- **F01-06**：`WhatIfPanel` 内 `useState` 不随 `versionId` 重置，父组件 `<WhatIfPanel ... />` 也没有 `key`。切版本后旧方案价格仍在，用户会拿它做新版本报价决策。
- **F01-07**：`createRequestKeyRef` 在每次打开弹窗时被 `newRequestKey()` 覆盖；`onCancel` 只 `setCreateVisible(false)`。若第一次请求已在服务端提交但响应超时，关闭重开就是新键，能建第二条。
- **F01-08**：`createQuoteVersion(quoteId, confirm)` 没有 `request_key`；后端路由也只接 `confirm`。超时重试直接多一版。
- **F01-09**：`canEditFreightOnly` 只看 `approval_status === 'approved' || version.sent_at`，不看 `accepted_at`/`declined_at`/`quote.status`。客户接受后仍显示“去补填/改运费”，与 B02-01 叠加可改已接受金额。
- **F01-10**：`Number('1e309') === Infinity`，`Infinity <= 0` 为 false，改价前端放行；费用正则允许 15 位整数，后端 `Numeric(16,2)` 只允许 14 位；收件人/渠道没有列宽校验。
- **F01-11**：`rememberChannel` 写任意字符串，`rememberedChannel` 只接受 `SEND_CHANNELS` 白名单；自定义渠道下次必回落默认。
- **F01-12**：`Promise.all(versionIds.map(getSendLogs))`，N 个版本 N 个请求；没有按报价聚合接口，也没有把已取版本缓存合并。
- **F01-13**：What-if 的 `amountTotal = basePrice * estimateQuantity`、`profitTotal = unitProfit * estimateQuantity` 全是 number；不提交、不落库，但会误导报价决策。
- **F01-14**：`if (!detail || !version || !quote)` 不判 `detailQuery.isError`，500/超时和 404/403 都落到同一句“报价单不存在或无权查看”。

## 五、本块最大漏项

1. **F01-01**：重复订单/应收的完整链路已成立，且根因在后端查重维度，前端隐藏按钮只能减少入口，不能兜住 API。
2. **F01-02**：URL 归属不校验导致“看到的单”和“操作的单”不是同一张；删除整单是不可逆动作，优先修。
3. **F01-03**：金额入参用 number 是系统性口径问题，不只报价页；订单、费用、回款入口应统一检查。

## 六、残余

- 本批复现是静态代码断言 + Node 数值验证，没有起浏览器做真实 URL 篡改、弱网超时、双击和版本切换。
- F01-01 的重复转单依赖“新版能重新走完审批/发送/接受”，这条业务链是代码允许的；未在真实环境跑完整流程。
- 未改业务代码；F01 的 14 条均未修复。
