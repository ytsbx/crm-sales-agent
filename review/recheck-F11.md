# F11 案例 / 知识 / 新品洞察复核：21 条确认 + 1 条证伪

- 范围：`KnowledgePage.tsx`、`CasesPage.tsx`、`ProductInsightsPage.tsx`、`shared/api/{cases,inquiry,insight}.ts`，以及后端 `inquiry/schema.py`、`cases/{schema,service}.py`、`product_insight/{schema,router}.py`、`quote/service.py`。
- 方式：逐条重读当前代码，用 `review/repro/f11_frontend/check_f11.mjs` 做静态断言和 Node 数值验证。
- 命令：`node review/repro/f11_frontend/check_f11.mjs`
- 结果：**21 confirmed（原报告 20 + 新增 1），1 refuted（F11-02），0 pending。没有整条误报。**

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F11-01 | P1 | 确认 | 数量/目标价非法文本被静默变 `null`，保存成功但字段丢失。 |
| F11-02 | P1 | **证伪（非缺陷）** | 四项成本显式填写时 0 元合法；专用测试 `-k zero` 实跑 2 passed，缺成本仍被拒。 |
| F11-03 | P1 | 确认 | 转报价成本/报价/数量走 JS number，16 位 4 位小数丢最后一位。 |
| F11-04 | P2 | 确认 | 新建案例无请求键，响应丢失重试多建草稿。 |
| F11-05 | P2 | 确认 | 编辑案例无版本冲突，后保存整份覆盖先保存。 |
| F11-06 | P2 | 确认 | 换客户不清证据，保存被后端同客户校验 422。 |
| F11-07 | P2 | 确认 | 证据/时间线查询失败显示“还没有单据/事件”。 |
| F11-08 | P2 | 确认 | 审核人看到他人草稿的编辑/提交/删除，点了才 404。 |
| F11-09 | P2 | 确认 | 新建询价无请求键，响应丢失重试多建一条。 |
| F11-10 | P2 | 确认 | 编辑询价无版本冲突，后保存覆盖先保存。 |
| F11-11 | P2 | 确认 | 审批记录/版本历史失败显示“暂无数据”。 |
| F11-12 | P2 | 确认 | 洞察价格假设/转换目标价 float/number 往返丢分。 |
| F11-13 | P2 | 确认 | 新建洞察无请求键，响应丢失多建一条。 |
| F11-14 | P2 | 确认 | 提交洞察评审不传已有请求键，重试显示“不能提交”。 |
| F11-15 | P2 | 确认 | 评审历史失败显示“还没有评审记录”。 |
| F11-16 | P3 | 确认 | 案例详情失败静默；旧版详情无竞态守卫。 |
| F11-17 | P3 | 确认 | 证据单据打开即全量分页拉取，客户单据多时拖慢。 |
| F11-18 | P3 | 确认 | 报价/数量前端 `min=0`，无小数位/上限，提交才 422。 |
| F11-19 | P3 | 确认 | 状态汇总失败静默；商机错误原文直接上屏。 |
| F11-20 | P3 | 确认 | 转需求客户搜索失败下拉为空，像没有客户。 |
| F11-21 | P3 | 确认 | 案例驳回原因无 255 字上限，提交才 422。 |
| **F11-22** | **P2** | **新增确认** | 洞察编辑无版本冲突，后保存整份覆盖先保存。 |

## 二、P1 复核

### F11-01 非法数值静默变 null（确认）

```ts
quantity: form.quantity.trim() ? parseOptionalNumber(form.quantity) : null,
target_price: form.target_price.trim() ? parseOptionalNumber(form.target_price) : null,
...
quantity: reviseForm.quantity ? Number(reviseForm.quantity) : null,
target_price: reviseForm.target_price ? Number(reviseForm.target_price) : null,
```

`parseOptionalNumber('abc')` 返回 `NaN`，`saveForm` 没有 `Number.isNaN` 拦截；`JSON.stringify(NaN)` 变 `null`。后端把 `null` 当“未填/清空”，保存成功但数量和目标价消失。`reviseMutation` 同样用裸 `Number`。

确认 P1。改法：非法输入直接 Toast + 中止；或全链路改用十进制字符串，别让 `null` 同时表示“非法”和“未填”。

### F11-02 证伪：0 元成本是明确允许的业务规则

当前行为确实允许 0：

```ts
<InputNumber min={0} ... />
```

```python
unit_cost: Decimal = Field(ge=0, max_digits=16, decimal_places=4)
```

后端测试明确把 0 当合法：

```python
@pytest.mark.parametrize("amount", ["0", "10"])
async def test_explicit_complete_cost_including_zero_can_submit(...):
    ...
    assert version.approval_status == "approved"
```

`test_cost_completeness.py` 也写“显式填 0 意思是「不花钱」，不是「没填」”。本轮执行：

```text
PYTHONPATH=. .venv/bin/python -m pytest tests/test_quote_cost_gate.py -q -k zero
..  [100%]
```

**2 passed。** 结论：四项成本都显式填写时，0 元合法；缺成本仍被拒。原“业务口径未拍板”不成立，**F11-02 证伪，不计缺陷**。

### F11-03 转报价 number 精度（确认）

```ts
unit_cost: quoteForm.unit_cost!,
quoted_price: quoteForm.quoted_price!,
quantity: quoteForm.quantity ?? undefined,
```

三个值来自 `InputNumber`，类型是 number。后端 `Decimal(16,4)` / `Decimal(16,3)`。Node 实测：

```text
Number('999999999999.9997') -> 999999999999.9998
```

这是一个合法的 16 位 4 位小数，进 JSON 前已经变值；成本和报价偏差会改变毛利/低价审批边界。确认 P1。

## 三、P2 复核

### F11-04 / F11-09 / F11-13 创建无请求键

- 案例：`createCase` 直接 `POST /cases`；`CaseCreate` 无 `request_key`。
- 询价：`createCustomInquiry` 直接 `POST /custom-inquiries`；`CustomInquiryCreate` 无键。`savedInquiry` 要等成功响应后才赋值，响应丢失防不了。
- 洞察：`createProductInsight` 直接 `POST /product-insights`；`InsightCreate` 无键且 `extra="forbid"`。

三处都是“已创建成功但客户端没收到”，用户再点一次就多一条。确认 P2。

### F11-05 / F11-10 编辑无版本

- 案例 `CaseUpdate` 无 `expected_updated_at/version`；前端提交整份 `...form`。
- 询价 `CustomInquiryUpdate` 无版本；前端 PATCH 整份 `data`。
- 后端行锁只保证串行，不比较版本：A 先保存，B 等待锁后仍用旧 payload 覆盖 A 的修改。

确认 P2，属静默丢更新。

### F11-06 换客户不清证据

```tsx
onChange={(customer) =>
  setForm({
    ...form,
    customer_id: customer?.id,
    quote_id: undefined,
    order_id: undefined,
    sample_id: undefined,
  })
}
```

只清三个旧字段，`form.evidences` 原样保留。后端 `validate_evidences` 要求证据与案例同客户，提交 422。用户得逐条删证据才能保存。确认 P2。

### F11-07 证据/时间线失败当空

`quotesQuery/ordersQuery/samplesQuery/formTimelineQuery` 都没有 `isError`。失败后 `data` 为空：

- 证据下拉没有选项，用户以为客户没有报价/订单/打样；
- 时间线显示“这个客户还没有时间线事件”。

确认 P2。

### F11-08 审核人看到他人草稿动作

前端对任何 `draft/rejected` 都显示“编辑/提交审核”；删除对 `isReviewer || author` 显示。后端 `ensure_case_draft_access` 先判 `author_id != user.id` 就 404，所以审核人点编辑/提交/删除都报“案例不存在”。

这不是越权，是**权限提示与后端不一致**。确认 P2。

### F11-11 / F11-15 历史失败当空

- `historyQuery / approvalsQuery` 无 `isError`，审批记录/版本历史失败显示空表。
- `roundsQuery` 无 `isError`，评审历史失败显示“还没有评审记录”。

审批历史是关键证据，误判会导致重复提交或错误追责。确认 P2。

### F11-12 洞察价格 number/float 往返

后端 `_serialize`：

```python
"price_assumption": float(row.price_assumption)
```

前端类型 `number`；编辑回填 `String(row.price_assumption)`，保存再 `parseOptionalNumber`。16 位 2 位小数边界：

```text
Number('99999999999999.99') -> 99999999999999.98
```

打开编辑不改金额直接保存，也会把原值写坏。转换目标价同样走 number。确认 P2。

### F11-14 提交洞察评审不传键

API 已经支持：

```ts
submitProductInsight(id: number, requestKey?: string)
```

页面只传 id：

```ts
mutationFn: (id: number) => submitProductInsight(id)
```

响应丢失后再点，后端状态已变，提示“当前状态不能提交评审”。不会多开轮次，但用户会把成功当失败。确认 P2。

### F11-22 洞察编辑无版本（新增漏项）

前端保存是整份 payload：

```ts
const payload = {
  title: form.title,
  source: form.source ?? null,
  ...
}
updateProductInsight(editingId, payload)
```

`InsightUpdate` 没有 `expected_updated_at/version`；后端虽然 `for_update=True`，但拿到锁后用 `exclude_unset=True` 只比较“这次传了的字段”，仍会把旧值写回去。两个评审人同时改同一条洞察，后保存覆盖先保存。

这与 F11-05（案例）、F11-10（询价）同根，原报告漏了洞察这一条。确认 P2。

## 四、P3 复核

### F11-16 详情静默/竞态

`openDetail` 的 `catch` 只保留列表记录，不提示；`openDetailById` 直接 `setDetail(await getCase(id))`，没有请求序号/当前 id 守卫。快速点两条/两个版本，先发旧请求后返回会覆盖当前详情。确认 P3。

### F11-17 证据全量拉取

```ts
const first = await load(1)
for (let page = 2; all.length < first.total; page += 1) {
  ...
}
```

打开证据选择就串行拉完该客户全部报价/订单/打样，每页 100。客户单据多时页面卡顿。确认 P3。

### F11-18 报价/数量前端 0

`InputNumber min={0}` 允许 0；后端 `quoted_price gt=0`、`quantity gt=0` 提交才 422。也没有 `precision` / 16 位上限。确认 P3。

### F11-19 汇总静默、商机错误原文

`summaryQuery` 无 `isError`；`linkedOpportunity.error?.message` 直接拼到页头。确认 P3。

### F11-20 转需求客户搜索失败

`convertCustomers` 无 `isError`，失败 `optionList` 为空，用户以为没有客户可转。确认 P3。

### F11-21 驳回原因长度

`TextArea rows={4}` 没有 `maxCount={255}`；后端 `CaseReview.note max_length=255`，提交才 422。确认 P3。

## 五、本块最大漏项

1. **F11-01 + F11-03**：一个把非法值静默变 `null`，一个把 16 位金额变 number；都直接伤“数量/金额”数据。
2. **F11-05 + F11-10 + F11-22**：案例、询价、洞察三套编辑都没有版本冲突校验，后保存整份覆盖先保存；原报告只记了前两个，漏了洞察。
3. **F11-07 + F11-11 + F11-15**：证据/审批/评审历史失败都伪装成“没有数据”，审批证据页尤其危险。
4. **F11-02 已证伪**：0 成本在四项成本显式填写时合法，测试固定该口径，不是代码漏检。

## 六、残余

- 静态断言 + Node 数值验证，没有起浏览器做非法输入、弱网重试、并发编辑、客户单据大数据量。
- F11-02 已执行 `tests/test_quote_cost_gate.py -k zero`，2 passed；其余仍为静态断言。
- 未审 `CasesPage` 之外的案例后端全生命周期；B 系列已有结论保留。
- 未改业务代码；F11 的确认项均未修复。
