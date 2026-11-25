# F07 打样页 + 分析页复核：16 条

- 范围：`SampleListPage.tsx`、`SampleDingTalkModal.tsx`、`SampleFromSourceModal.tsx`、`shared/api/sample.ts`、`AnalyticsPage.tsx`。
- 方式：逐条重读当前代码与后端 sample schema/router，用 `review/repro/f07_frontend/check_f07.mjs` 做静态断言和 Node 数值验证。
- 命令：`node review/repro/f07_frontend/check_f07.mjs`
- 结果：**16 confirmed，0 missing**。没有整条误报，但有两处证据需要修正。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F07-01 | P2 | 确认 | 分析页 18 个查询只有目标区 1 个 `isError`，失败时 KPI 落 0、表落“还没有数据”。 |
| F07-02 | P2 | 确认 | 新建打样无请求键，超时重试多一张打样单。 |
| F07-03 | P2 | 确认 | 追加明细无请求键，超时重试多一条明细。 |
| F07-04 | P2 | 确认 | 来源打样任何编辑都换请求键，已成功未回执后再改会重复建单。 |
| F07-05 | P2 | 确认 | 运费/打样费用/工时先 `Number` 再提交，16 位边界丢分。 |
| F07-06 | P2 | 确认 | 制作依据附件失败被显示成“这张单子上还没有附件”。 |
| F07-07 | P3 | 确认（修正） | 重提/制作完成没透出后端已支持的请求键；修订后端本身无键，只靠父版锁。 |
| F07-08 | P3 | 确认 | 拒绝原因不填也能点，前端不校验，后端才 422。 |
| F07-09 | P3 | 确认 | 工时×10 用浮点直出，`0.07` 显示 `0.7000000000000001`。 |
| F07-10 | P3 | 确认 | 运费/打样费用展示对 number 再 `toFixed`/`toTwoDecimals`，二次舍入。 |
| F07-11 | P3 | 确认 | 分析页概览/KPI 取整到元，环图/下钻 `Math.round`，明细对 number 再舍入。 |
| F07-12 | P3 | 确认 | 目标表单只判 NaN，负数、`1e20`、3 位小数都直接提交。 |
| F07-13 | P3 | 确认（修正） | 来源数量只判有限且 >0，不判整数位/3 位小数；`1e3` 后端会接受，不是必然 422。 |
| F07-14 | P3 | 确认 | 新建打样只校验产品名称，不校验客户/商机必填。 |
| F07-15 | P3 | 确认 | 目标保存无请求键，409 时用户无法确认上次是否已保存。 |
| F07-16 | P3 | 确认 | OA 打样费用是自由文本，无负数/2 位小数/上限前端校验。 |

## 二、P2 复核

### F07-01 分析页查询失败静默

当前 `AnalyticsPage.tsx` 有 18 个 `useQuery`，`isError` 只出现 1 次（`targetsQuery`，`:1376`）。其余：

```ts
const money = (value?: number) => `¥${Math.round(value ?? 0).toLocaleString('zh-CN')}`
...
value: money(receivable?.unreceived_amount)
...
value: `${deliverySummary?.late_count ?? 0} 单`
...
dataSource={(productQuery.data ?? []).filter(...)}
empty={keyword ? noMatches : '还没有报价数据'}
```

- 回款/发货 KPI 查询失败：`receivable/payment/delivery` 为 `undefined`，KPI 显示 `¥0` 或 `0 单`。
- 产品/业务员/风险表失败：空数组 + “还没有报价数据/暂无数据”。
- 概览四个格用 `receivable ? money(...) : '—'`，这部分显示 `—`，不会变 `¥0`；原文“概览 KPI 都变 ¥0”略宽，但“绝大多数区块静默为零/空”成立。

这是管理决策页，接口故障被当成业务为零，误导性比普通列表大。

### F07-02 / F07-03 打样创建与明细无键

- `createSample(payload: Record<string, unknown>)` 直接 POST `/samples`；`SampleCreate` 没有 `request_key` 字段，`create_sample` 直接 `SampleRequest(...) + flush + commit`。
- `addSampleItem(id, payload)` 直接 POST `/samples/{id}/items`；`add_sample_item` 只有 `sample` 行锁，没有请求键/唯一约束。

超时重试：打样单多一张、明细多一条；两张单都能各自提 OA，车间可能按两份资料打样。

### F07-04 来源打样换键

```ts
const requestKey = useRef(newRequestKey())
...
function change(id: number, patch: Partial<Draft>) {
  if (sending.current) return
  requestKey.current = newRequestKey()
  setDrafts(...)
}
```

后端 `create_from_source` 按 `request_key + request_hash` 幂等。请求已成功但响应丢失时，用户改勾选/数量后会生成新键，后端把它当“新请求”，再建一张打样单/订单草稿。只改回原值也会重复，因为键已经变了。

### F07-05 三个金额/工时入口 Number 化

```ts
const fee = raw === '' ? 0 : Number(raw)
const fee = raw === '' ? null : Number(raw)
const hours = rawHours === '' ? null : Number(rawHours)
```

后端：

```python
shipping_fee: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)
sample_fee: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)
estimated_hours: Decimal | None = Field(default=None, ge=0, le=Decimal("9999"), max_digits=8, decimal_places=2)
```

Node 实测：

```text
Number('99999999999999.99') -> 99999999999999.98
```

`99999999999999.99` 是 16 位有效数字（14 位整数 + 2 位小数），在后端 `max_digits=16` 范围内，但进后端前已经少 1 分。运费/打样费用会进 OA 申请表和导出 Excel，车间/财务看到的金额与录入不一致。

### F07-06 制作依据附件失败当空

```tsx
{!madeFilesQuery.isLoading && (madeFilesQuery.data ?? []).length === 0 && (
  <div>这张单子上还没有附件。请先在上面的「附件」区上传图纸/确认件，再回来登记依据。</div>
)}
```

`madeFilesQuery` 没有 `isError`。500/403/超时后 `data` 为 `undefined`，`(data ?? []).length === 0` 成立，界面显示“还没有附件”。用户会重复上传，或直接不选依据登记完成，事后无法核对“按哪份图纸做的”。

## 三、P3 复核与修正

### F07-07 请求键只透出一半（修正）

- 后端 `SampleResubmit`、`SampleMade` 都有 `request_key`；前端 `resubmitSample(id)`、`madeSample(id, remark, basisFileIds)` 都没传。
- **修正原文**：`SampleRevise` 本身没有 `request_key`，后端修订也没做键回放；它靠“父版有子版本即冻结”的 `get_visible_or_404` 锁防第二次。原文“修订也带键，后端同键回放”不成立。
- 实际后果：重提/制作完成弱网重试只能靠后端状态/指纹兜底，前端拿不到“上次已成功”的确定回执；修订不会重复建单，但 409 文案仍让用户不确定。

### F07-08 拒绝原因

`approveSample(detailId!, approved, approved ? undefined : rejectReason)` 直接发；拒绝按钮没有 `rejectReason.trim()` 的 disabled。后端 `if not payload.approved and not (payload.reject_reason or "").strip(): 422` 才拦。用户填完弹窗才被打回。

### F07-09 积分浮点

两处直接乘 10：

```ts
Number(prodForm.estimated_hours) * 10
const points = raw.trim() && Number.isFinite(n) ? n * 10 : null
```

`0.07 * 10` 实测 `0.7000000000000001`，页面/OA 资料可能带一长串尾数。后端 `craft_points` 是 Decimal，但前端展示先脏了。

### F07-10 展示二次舍入

```tsx
render: (v: number) => `¥${v.toFixed(2)}`
formatMoney(toTwoDecimals(detail.sample_fee), 'CNY')
```

`SampleRequestRow.shipping_fee/sample_fee` 都是 number，没有 `*_exact`；展示端对已经失真的 number 再格式化，末位可能和录入/库内不一致。

### F07-11 分析页金额

`money` 统一 `Math.round` 到元；回款方式环图 `value: Math.round(row.value)`；下钻合计 `Math.round(total)`；明细 `formatMoney(toTwoDecimals(v ?? 0))` 对 number 再舍入。页面与应收中心/财务台账可能差几角。若产品上确实要显示整元，至少图例/悬浮提示应保留精确值并标注“显示到元”。

### F07-12 目标表单

`targetNumber`/`targetMoney` 只判 `Number.isNaN`，没有 `>=0`、整数位/小数位上限。负数、`1e20`、3 位小数都能送到后端；后端 422 后用户只看到字段错误，弹窗内没有就地提示。`targetMoney` 虽然用了 `parseOptionalDecimal`，但返回类型是 `number | string`，没有做边界校验。

### F07-13 来源数量（修正）

```ts
const quantityMissing = selected.filter(({ draft }) =>
  !Number.isFinite(Number(draft.quantity)) || !(Number(draft.quantity) > 0))
```

只判有限且 >0，不判整数位/3 位小数。**修正原文**：`1e3` 会被 Pydantic `Decimal` 解析成 `1000`，后端接受，不是必然 422；真正会被 422 的是 `1.0001`（4 位小数）或超过 13 位整数。问题仍是前端没有提前提示，用户提交后才看到字段错误。

### F07-14 客户/商机必填

前端创建只校验 `form.items` 和产品名称；`customer_id/opportunity_id` 为空也能点。后端 `if not customer_id and not opportunity: 422`。用户填完整个弹窗才被告知“必须指定商机或客户”。

### F07-15 目标保存

`upsertSalesTarget` 无请求键，只带 `expected_updated_at` 和唯一键。成功但响应丢失后再点，可能 409“目标已被修改”；用户不确定第一次是否保存。

### F07-16 OA 打样费用

`SampleDingTalkModal` 里 `_sample_fee` 是普通 `Input`，直接写 `draft.fields`；没有负数/2 位小数/16 位上限的前端校验。后端 `validated_sample_details` 按 Decimal 拒绝，但用户填完整页 OA 资料后才看到费用格式错误。

## 四、本块最大漏项

1. **F07-01**：分析页是管理决策页，接口失败被当成 0/空，优先级应高于其他 P3 展示问题。
2. **F07-02 + F07-03 + F07-04**：打样创建链路三处幂等缺口，弱网重试会多单/多明细，直接影响车间排产。
3. **F07-05**：打样费用/运费是进 OA 和 Excel 的金额，16 位边界少 1 分；应与 B 系列金额入口统一改字符串 Decimal。

## 五、残余

- 静态断言 + Node 数值验证，没有起浏览器做弱网、连点、16 位输入、附件 500 故障注入。
- 打样 OA 外部系统未连接，5 秒快照/提交结果只按前后端状态判断。
- 只审本块页面与调用到的 API 契约；`AttachmentPanel`、`ProductNameInput`、`CustomerSelect`、`charts/*`、`money.ts` 内部属 F08/F13，未逐行复核。
- 未改业务代码；F07 的 16 条确认项均未修复。
