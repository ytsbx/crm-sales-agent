# F02 订单页复核：13 条 + 2 条待确认

- 范围：`OrderDetailPage.tsx`、`AllocationAdjustModal.tsx`、`OrderDraftsPage.tsx`、`OrderListPage.tsx`、`shared/api/order.ts`。
- 方式：逐条重读前端入口与后端 `order/router.py`、`order/service.py`、`payment/router.py`，再用 `review/repro/f02_frontend/check_f02.mjs` 复现。
- 命令：`node review/repro/f02_frontend/check_f02.mjs`
- 结果：**14 confirmed，1 refuted，0 missing**。F02-P1 的“错账”后果不成立；F02-P2 由“待确认”升为确认。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F02-01 | P1 | 确认 | 回款登记每次打开换 `request_key`，关闭不清；超时重开再提交可生成第二笔待确认回款。 |
| F02-02 | P1 | 确认 | 手工建单把数量/单价 `Number()` 后提交，16 位边界丢精度，订单总额和应收跟着错。 |
| F02-03 | P1 | 确认（根因修正） | 前端 `createOrder` 不传 `request_key`；后端其实支持可选键，是前端没给，重试仍会建第二张正式订单。 |
| F02-04 | P1 | 确认 | 没有发货批次也能把履约状态改成“已发货/已签收”；前端只拦 `completed`，后端也只在 `completed` 查实发量。 |
| F02-05 | P1 | 确认 | 订单取消后，未回款应收节点仍显示“删除”，后端删除也只查回款/分配，不查节点/订单已取消。 |
| F02-06 | P2 | 确认 | 生成应收的键写在 `mutationFn` 内，每次提交都换；后端同键回放被绕过，重试得到 409 而非原结果。 |
| F02-07 | P2 | 确认 | 排发货批次打开就换键、关闭不清；部分批次超时重开可再排一批重复计划。 |
| F02-08 | P2 | 确认 | 已完成回退固定传“在订单详情页更新”，后端只判非空，必填理由被架空。 |
| F02-09 | P3 | 确认 | 回款金额只判 finite/&gt;0，没有 2 位小数/16 位上限，靠后端 422。 |
| F02-10 | P3 | 确认 | 详情 500/超时统一显示“订单不存在或无权查看”；多处直接回显 `error.message`。 |
| F02-11 | P3 | 确认 | 确认/驳回/状态变更用固定审计文案，钱操作原因不可区分。 |
| F02-12 | P3 | 确认 | 草稿保存/确认无 `request_key`，只靠 revision，成功但响应丢失后重试 409。 |
| F02-13 | P3 | 确认 | 订单明细数量没有 `quantity_exact`，用 `number.toLocaleString()` 展示。 |
| F02-P1 | — | **证伪** | “已取消期旧分配留在隐藏状态会错账”后果不成立：后端对 cancelled plan 直接 422，且订单取消要求无已确认回款。 |
| F02-P2 | P2 | **确认（原待确认）** | 复购开新商机无 `request_key`，每次 POST 都新建 Opportunity，重试可多建。 |

## 二、五条 P1 的复核

### F02-01 回款登记换键

`OrderDetailPage.tsx`：

- 打开“登记回款”时 `paymentKeyRef.current = newRequestKey()`；
- `onCancel={() => setPaymentTarget(null)}` 只清目标，不清键；
- 成功后才 `paymentKeyRef.current = ''`。

所以首次请求已在服务端落库但响应超时，用户关闭再打开同一期，会生成新键并再次提交。后端测试明确“不同键保留两行”，会得到第二笔 `pending`；财务都确认后，`received_amount` 和分配重复计入。

### F02-02 手工建单精度

`OrderListPage.tsx` 提交：

```tsx
quantity: Number(item.quantity),
unit_price: Number(item.unit_price),
```

后端 `OrderItemInput` 是 `Numeric(16,3)` / `Numeric(16,4)`。Node 实测：

```text
Number('9999999999999.999') -> 9999999999999.998
```

提交的 JSON 已经是 `.998`，后端按这个值算 `line_amount` 和订单总额，后续应收全跟着错。修复应传原始字符串或 `quantity_exact`/`unit_price_exact`，预览合计也不要先转 number。

### F02-03 手工建单幂等

这里要修正审查报告的一句话：**后端不是没有幂等，而是前端没传键。**

`order/router.py` 的 `create_order` 已经调用：

```python
request_key = idempotency.request_key_from(request, payload.request_key)
```

`OrderCreate` 也有可选 `request_key`。但 `shared/api/order.ts` 的 `createOrder(payload)` 类型和调用都没带键，所以后端每次都按“新请求”处理。缺陷仍成立，修法很小：打开弹窗生成键，成功才清。

### F02-04 绕过发货批次

前端下拉直接给：

```tsx
{ value: 'shipped', label: '已发货' },
{ value: 'delivered', label: '已签收' },
```

只对 `completed` 算 `completionBlocked`。后端 `change_status` 也只在：

```python
if new_status == "completed":
    ... 查 all_shipped ...
```

`shipped` / `delivered` 没有实发量闸门。没有批次也能把订单改成已发货/已签收，看板和交期偏差按错误状态展示。前端应按实发量禁用选项，后端必须补同一流转矩阵。

### F02-05 取消后删应收

前端：

```tsx
const planCancelled = record.status === 'cancelled' || order.status === 'cancelled'
const canRegister = record.status !== 'paid' && !planCancelled && canRegisterPayment
const canDelete = canPayment && !hasPayment
```

`canDelete` 没带 `!planCancelled`。后端 `delete_receivable` 只查：

```python
paid = ...PaymentRecord...
allocated = ...PaymentAllocation...
if paid or allocated: raise
```

不查 `plan.status` 或订单状态。已取消订单的无回款节点能删掉，`sync_payment_milestones` 还会按剩余应收重算，审计虽有记录但原始财务历史没了。

## 三、F02-P1 为什么证伪

审查员说：`initialAmounts` 把 `payment.allocations` 全部灌进 `amounts`，`activePlans` 过滤 cancelled，但提交仍遍历 `amounts`，隐藏的已取消期金额会参与提交。

代码前半句成立，但后果不成立：

1. `payment/router.py` 的 `update_payment_allocations` 对每个 `plan_id` 明确检查：
   ```python
   if plan.status == "cancelled":
       raise AppError(..., "已取消，不能分配", 422)
   ```
   隐藏的 cancelled 分配只会让整次保存失败，不会静默写错账。
2. 订单取消前要求“无已确认回款”（有 confirmed 直接 422），而分配调整只允许 `confirmed` 回款；正常路径无法同时出现“已确认分配 + 已取消期”。
3. 因此这不是错账缺陷。剩余的是一个 UI 小瑕疵：如果历史脏数据里真有 cancelled 分配，用户看不到也删不掉，保存会被 422 卡住。可记 P3 观察项，不应按 P1 计。

## 四、F02-P2 为什么升确认

`shared/api/order.ts`：

```ts
export function repurchase(orderId: number) {
  return api.post<{ opportunity_id: number }>(`/orders/${orderId}/repurchase`)
}
```

没有 `request_key`。后端 `repurchase` 每次直接：

```python
opportunity = Opportunity(customer_id=..., title=f"{order.order_no} 复购", ...)
session.add(opportunity)
```

没有幂等表、没有按订单去重。首次成功但响应丢失后重试，会多开一个复购商机。后果不是金额，但会污染商机漏斗和销售待办，建议 P2。

## 五、本块最大漏项

1. **F02-01**：回款重复入账是钱路径，且后端明确“不同键保留两行”，不是理论风险。
2. **F02-03**：手工建单是正式订单入口，前端不传键导致整条幂等能力失效；后端已具备，改造成本低。
3. **F02-04**：履约状态是看板/交期依据，`shipped`/`delivered` 无闸门会让“没发货也算已发货”。

## 六、残余

- 本批复现是静态断言 + Node 数值验证，没有做浏览器弱网、关闭重开弹窗、并发提交。
- F02-07 的重复排批次依赖“首批只排部分未计划量”，全量排满时第二批会被后端挡住。
- F02-06 因后端已有“该订单已有应收计划”闸门，后果是误报失败而非重复落库；仍保留 P2，因为同键回放语义被前端破坏。
- 未改业务代码；F02 的 14 条确认项均未修复。
