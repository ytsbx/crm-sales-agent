# F12 产品 / 物流 / 导入通用复核：14 条确认

- 范围：`ProductDetailPage.tsx`、`ProductListPage.tsx`、`SkuImageCell.tsx`、`LogisticsPage.tsx`、`GuardedButton.tsx`、`ConfirmRequiredModal.tsx`、`CsvImportButtons.tsx`、`shared/api/product.ts`、`shared/quote-todo.ts`，以及后端 `product/{schema,field_rules}.py`、`pricing/{logistics,logistics_router}.py`。
- 方式：逐条重读当前代码，用 `review/repro/f12_frontend/check_f12.mjs` 做静态断言和 Node 数值验证。
- 命令：`node review/repro/f12_frontend/check_f12.mjs`
- 结果：**原报告 13 条全部确认；另新增 1 条漏项。0 条误报。**

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F12-01 | P1 | 确认 | SKU 物理字段非数字被静默变 `null`，保存时清空原值。 |
| F12-02 | P1 | 确认（0 合法性待业务定） | SKU 0 单重/尺寸落库后按 0 计费，与手工 override 拒绝 `<=0` 矛盾。 |
| F12-03 | P2 | 确认 | 新建产品无请求键，响应丢失重试多建产品。 |
| F12-04 | P2 | 确认 | 产品详情/SKU 查询失败显示“产品不存在/没有 SKU”。 |
| F12-05 | P2 | 确认 | 产品/SKU 编辑无版本冲突，后保存整份覆盖先保存。 |
| F12-06 | P2 | 确认 | 新建 SKU 无请求键，响应丢失重试多建 SKU。 |
| F12-07 | P2 | 确认 | 物流保存方案无请求键，重试/双击落两条。 |
| F12-08 | P2 | 确认 | 物流金额/数量走 number，16 位金额丢分。 |
| F12-09 | P3 | 确认 | CSV 模板/导出下载失败无提示。 |
| F12-10 | P3 | 确认 | SKU 图片批量查询/单图预览失败静默成“无图”。 |
| F12-11 | P3 | 确认 | `GuardedButton` 禁用态键盘不可达。 |
| F12-12 | P3 | 确认（潜伏） | `useConfirmGate` 不自动补 `confirm`，与注释示例不符；当前无生产调用。 |
| F12-13 | P3 | 确认（潜伏） | `splitAmountsLikeBackend` 仍返回 number；当前只被测试使用。 |
| **F12-14** | **P3** | **新增确认** | SKU 图片上传无幂等，响应丢失后按提示补传会重复挂图。 |

## 二、P1 复核

### F12-01 非数字静默清空

```ts
const toNumber = (value: string): number | null => {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

length: toNumber(skuForm.length),
...
weight: toNumber(skuForm.weight),
carton_qty: toNumber(skuForm.carton_qty),
```

`toNumber('12cm')` 返回 `null`，payload 把该字段作为 `null` 发给 `PATCH /skus/{id}`；`SkuUpdate` 的 `length/weight/...` 都是 `Decimal | None`，`null` 表示清空。用户看到“SKU 已保存”，但原单重/尺寸被清掉，物流后续按缺失/0 计算。

确认 P1。改法：非法文本按字段名提示并中止；空串才允许 `null`。

### F12-02 0 单重进入运费

后端物理量最小值就是 0：

```python
# field_rules.py
#: 允许的最小值。物理量一律 0（负数没有意义）
minimum: Decimal = Decimal(0)
```

但物流计算只在 `None` 时告警：

```python
unit_weight = weight_override if weight_override is not None else sku.weight
if unit_weight is None:
    warnings.append("该 SKU 没有维护单重，无法计算实际重量")
    unit_weight = ZERO
```

而手工覆盖值：

```python
if weight_override is not None and weight_override <= 0:
    raise AppError(..., "单件重量必须大于 0", 422)
```

同一个“0 单重”，SKU 落库后被当有效重量算 0；手工 override 却被拒绝。用户把 SKU 单重填 0，物流试算实际重量为 0，按最低收费/少收运费。行为确认 P1；修法取决于业务是否允许 0：

- 不允许：前端拦 0，后端物理量改 `gt=0`；
- 允许：物流侧把 0 也当“未维护”并告警，或在 SKU 上显式标“零重/非实物”。

## 三、P2 复核

### F12-03 新建产品无键

`ProductCreate` 只有名称/产品线/分类/品牌/描述/知识，没有 `request_key`；前端 `createProduct(payload)` 直接 POST。响应丢失重试多建一条产品，列表出现同名重复，SKU 可能挂到两个产品上。确认 P2。

### F12-04 详情失败当“不存在/没有 SKU”

```tsx
const product = productQuery.data
if (productQuery.isLoading) ...
if (!product) return <EmptyState title="产品不存在或已下线" ...>
...
dataSource={skuQuery.data ?? []}
empty="还没有 SKU，先加一个"
```

`productQuery`/`skuQuery` 都没有 `isError`。500/超时/403 时 `data` 为空，直接把接口故障说成产品被删、SKU 没有；用户可能重复新建产品/SKU。确认 P2。

### F12-05 编辑无版本

`ProductUpdate`/`SkuUpdate` 都没有 `expected_updated_at/version`。前端产品保存 `updateMutation.mutate(productForm)`，SKU 保存 `updateSku(editingSku.id, payload)`，都是整份字段。两个 `product:manage` 同时编辑，A 先保存、B 后保存会把 A 的名称/规格/重量覆盖回旧值。确认 P2。

### F12-06 新建 SKU 无键

`SkuCreate` 无 `request_key`；前端 `createSku(productId, payload)` 直接 POST。SKU 编码留空时后端自动生成，重复编码拦不住第二次创建；产品下出现重复 SKU。确认 P2。

### F12-07 物流保存无键

```ts
const saved = await calculateLogistics({
  ...calculation.variables,
  save: true,
  selected_rate_id: option.rate_id,
  expected_amount: option.amount,
})
```

后端只校验 `expected_amount` 是否与费率变化一致，没有请求键/唯一约束。响应丢失或双击会落两条 `logistics_quotes`，试算历史重复；若按客户/商机汇总物流成本，会重复计算。确认 P2。

### F12-08 物流金额 number

`money()` 用 `toFixed(2)`；`expected_amount` 是 number；后端 `_f` 已经把 Decimal 转 float。Node 实测：

```text
Number('99999999999999.99') -> 99999999999999.98
```

16 位金额在展示和“费率是否变化”的比较上都会偏，可能误触 409 或显示错金额。确认 P2。

## 四、P3 复核

### F12-09 CSV 下载无 catch

`ProductListPage.downloadCsv` 和 `CsvImportButtons.downloadCsv` 都是：

```ts
const response = await axios.get(url, { responseType: 'blob', headers: tokenHeader() })
```

没有 `try/catch`，401/500/断网时 Promise 拒绝，页面无 Toast，用户只看到没下载。确认 P3。

### F12-10 图片失败静默

`skuImagesQuery` 没有 `isError`，失败时 `skuImages` 为空，列表显示“无图”；`SkuImageCell` 的 `catch` 只 `setFailed(true)`，UI 只显示红色 `!`，没有文案。用户会以为图片没传，重复上传。确认 P3。

### F12-11 GuardedButton 键盘

外层 `span onClick` 没有 `role="button"`/`tabIndex`/`onKeyDown`；内层 `Button disabled` 不可聚焦。鼠标用户点灰按钮能看原因，键盘用户只能看到一个不可聚焦的灰按钮。确认 P3。

### F12-12 `useConfirmGate` 潜伏

```ts
async function proceed(): Promise<unknown> {
  const action = pending
  cancel()
  if (!action) return undefined
  return action()
}
```

`proceed()` 重发原 `action`，不补 `confirm`；注释示例 `someApi({ ... })` 也没参数。当前生产代码没有调用这个 hook，属共用件潜伏问题。确认但不定为当前业务缺陷。

### F12-13 `splitAmountsLikeBackend` 潜伏

```ts
return splitAmountsExact(total, ratios)?.map(Number) ?? null
```

exact 字符串再转 number，金额大时会丢分。当前只被 `ops/quote_todo.test.mjs` 调用，生产未检索到调用，属未来误用风险。确认 P3。

### F12-14 新增：SKU 图片上传无幂等

```ts
for (const file of pendingImages) {
  try {
    await uploadFile(file, { businessType: 'sku', businessId: skuId })
  } catch {
    failed.push(file.name)
  }
}
```

`uploadFile` 无请求键。上传成功但响应丢失时进入 `failed`，页面提示“可在列表「图片」列补传”；用户补传会再挂一张，SKU 图片列表出现重复。

新增 P3。修法：上传接口接请求键，或按 `sku_id + 文件 hash` 去重。

## 五、本块最大漏项

1. **F12-01 + F12-02**：一个把非法输入静默清空，一个把 0 重量/体积当有效计费依据；都直接影响运费。
2. **F12-03 + F12-06 + F12-07**：产品、SKU、物流方案三个创建/保存入口都没有请求键，弱网重试会多建/重复落库。
3. **F12-05**：产品/SKU 编辑无版本，SKU 单重被并发覆盖回旧值后，运费会按旧重量算。
4. **F12-14**：图片上传无幂等，失败提示引导用户补传反而制造重复图；原报告只在逐文件记录里提到，没有形成编号发现。

## 六、残余

- 静态断言 + Node 数值验证，没有起浏览器做非法输入、0 重量试算、弱网重试、并发编辑、CSV 401。
- 没有跑后端测试；F12-02 只按代码路径判断，0 是否业务允许未拍板。
- `useConfirmGate`/`splitAmountsLikeBackend` 是潜伏项，当前无生产调用，不能当线上缺陷。
- 未改业务代码；F12 的确认项均未修复。
