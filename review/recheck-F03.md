# F03 价格中心复核：10 条 + 1 条待确认

- 范围：`PriceCenterPage.tsx`、`PricingPage.tsx`、`PriceTerm.tsx`、`shared/api/pricing.ts`、`money.ts`、`payment-allocation.ts`、`number-input.ts`、`shared/api/bizdoc.ts`。
- 方式：逐条重读前端表单/展示路径与后端 `pricing/service.py`、`pricing/router.py`，用 `review/repro/f03_frontend/check_f03.mjs` 复现。
- 命令：`node review/repro/f03_frontend/check_f03.mjs`
- 结果：**10 confirmed，1 refuted，0 missing**。F03-P1 的“重复出文档”不成立。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F03-01 | P1 | 确认 | 成本/价格规则/客户特殊价/费率表单用 `parseOptionalNumber` 转 number，16 位边界价被改写。 |
| F03-02 | P1 | 确认 | 客户特殊价/费率从后端 `_f` float 回填，打开不改直接保存也会改价。 |
| F03-03 | P2 | 确认 | 查价数量走 number，跨阶梯可能查错档；“加入商机需求”把 number 数量/单价写进商机。 |
| F03-04 | P2 | 确认 | 成本表强制两位小数，4 位成本/合计成本被截，价格管理员看到的不是核价成本。 |
| F03-05 | P2 | 确认 | 新增成本无 `request_key`，后端也无唯一约束，超时重试多一条同生效日成本。 |
| F03-06 | P2 | 确认 | 停用/恢复价格规则、删除客户特殊价无 `catch`，409/500 静默失败。 |
| F03-07 | P3 | 确认 | 价格表单缺 0..1/负数/16 位/4 位校验，主要靠后端 422；成本提示“2 位”与后端 4 位不符。 |
| F03-08 | P3 | 确认 | 列表/查价失败显示空表或原始 `error.message`，用户把加载失败当没数据。 |
| F03-09 | P3 | 确认 | `toTwoDecimals` 数字四舍五入、字符串截断；`formatUnitPrice` 第 5 位直接截断。 |
| F03-10 | P3 | 确认 | 核价主结果/权限 verdict 用 float 和 raw `quoted_price` 展示，深链价格用 `Number` 回填。 |
| F03-P1 | — | **证伪** | `bizdoc` 生成键虽是可选项，但全仓唯一调用点 `BizDocPanel` 每次都传稳定键，现网不会重复出文档。 |

## 二、两条 P1

### F03-01 表单先把十进制转 number

`PriceCenterPage.tsx`：

```tsx
purchase_cost: parseOptionalNumber(costForm.purchase_cost),
standard_price: parseOptionalNumber(ruleForm.standard_price),
agreed_price: parseOptionalNumber(customerPriceForm.agreed_price),
unit_price_per_kg: requiredNumberOrThrow(rateForm.unit_price_per_kg, '公斤单价'),
```

`parseOptionalNumber` 返回 JS number。Node 实测：

```text
Number('999999999999.9997') -> 999999999999.9998
Number('9999999999999.999')  -> 9999999999999.998
```

项目已经有 `parseOptionalDecimal`（保留十进制文本），但 PriceCenter 没有 import。价格中心是报价取价源，错一个最小单位会一路带到报价/订单。

### F03-02 编辑回填已经丢精度

后端 serializer 统一 `_f(Decimal) -> float`：

```python
def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
```

前端编辑：

```tsx
agreed_price: String(row.agreed_price ?? ''),
unit_price_per_kg: String(row.unit_price_per_kg ?? ''),
```

打开“编辑”不修改直接保存，`.9997` 已经变成 float 的 `.9998`，再被 `parseOptionalNumber` 提交。客户专属价/费率被无声改掉，审计还记成一次“修改”。这不是“显示差一点”，保存动作本身会改库。

## 三、P2/P3 复核要点

- **F03-03**：`lookupQuantity = parseQuantity(lookupQty)` 返回 number；“加入商机需求”直接 `quantity: lookupResult.quantity, target_price: lookupResult.unit_price`。查价和下游需求都吃 float。
- **F03-04**：`const money = value => formatMoney(toTwoDecimals(value), 'CNY')`；成本表采购/生产/包装/加工/合计全用它，而成本列是 `Numeric(14,4)`。`33.3333` 显示成 `¥33.33`，核价实际按 4 位算。
- **F03-05**：`create_cost` 直接 `session.add(cost)`，没有 `idempotency.reserve`；`ProductCost` 只有普通索引 `ix_product_costs_sku (sku_id, effective_from)`，不是唯一约束。重试会插两条同生效日成本，取哪条看排序。
- **F03-06**：三处都是 `.then(refreshAll)`：
  ```tsx
  disablePriceRule(record.id).then(refreshAll)
  restorePriceRule(record.id).then(refreshAll)
  deleteCustomerPriceRule(record.id).then(refreshAll)
  ```
  没有 `.catch`。恢复冲突（40901）时页面无提示，规则仍停用。
- **F03-07**：`margin` 只判 `Number.isNaN`，没有 `>1`；后端 schema 是 `ge=0, le=1`。成本提示写“最多 2 位小数”，后端 `CostCreate` 是 4 位。
- **F03-08**：规则列表 `dataSource={rulesQuery.data?.items ?? []}`，没有 `rulesQuery.isError` 分支；失败时只剩“还没有价格规则”。查价错误直接展示 `lookupMutation.error.message`。
- **F03-09**：`toTwoDecimals` 对 number 走 `toFixed(2)`，对字符串走 `slice(0,2)`。实测 number `2.346 → 2.35`，字符串 `'2.346' → '2.34'`；`formatUnitPrice` 对第 5 位 `slice(0,4)` 是截断。
- **F03-10**：`paramNumber` 把深链 `quoted_price` 先 `Number` 再 `String`；verdict 直接 `报 {verdict.quoted_price} 元`；`PricingResult`、`PricePermissionVerdict` 都没有 `*_exact`。

## 四、F03-P1 为什么证伪

审查报告说 `generateQuoteDoc` 等三个函数的 `requestKey?: string` 是可选项，调用方漏传就没有幂等。但全仓检索：

```text
shared/components/BizDocPanel.tsx:23-25  导入三个 generate
shared/components/BizDocPanel.tsx:100-103 三处调用都传 key
```

`BizDocPanel` 内部：

```tsx
const requestKeyRef = useRef<string | null>(null)
if (!requestKeyRef.current) requestKeyRef.current = newRequestKey()
const key = requestKeyRef.current
```

成功后才 `requestKeyRef.current = null`。这是唯一调用点，弱网重试会复用同一把键；成功后清空表示“明确再出一版”，语义正确。

所以“可选参数”是 API 设计冗余，不是现网缺陷。可保留为“未来调用方可能漏传”的 P3 观察项，但 F03-P1 本身不成立。

## 五、本块最大漏项

1. **F03-01 + F03-02 是同一条链**：后端出参 float、前端表单 number、回填再 number。只在提交前改一处不够，必须“出参 exact + 表单保留字符串 + 提交字符串”三处一起收。
2. **F03-04**：成本展示截两位会让价格管理员按错误成本判断毛利，属于“看错数做决策”，不能只当视觉问题。
3. **F03-05**：成本没有唯一约束，超时重试会留下两条同生效日记录；取价按排序命中，价格可能随机漂。

## 六、残余

- 本批复现是静态断言 + Node 数值验证，没有起浏览器做表单输入、编辑原样保存、弱网重试。
- F03-05 的“第二条成本谁胜出”取决于 `get_effective_cost` 的排序/ID，未在真实库上验证。
- 未改业务代码；F03 的 10 条确认项均未修复。
