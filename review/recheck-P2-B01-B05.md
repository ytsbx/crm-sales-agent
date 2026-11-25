# B01–B05 P2 逐条复核（证伪）

- 范围：主块 22 条 + 补充审查 9 条，共 31 条 P2。
  - 主块：B01-02/03、B02-03/04/05/07/12、B03-04/05/06/07、B04-04/05/06/07/08、B05-06/07/08/09/10/12。
  - 补充：B01-10、B02-14/15、B03-19/20/21、B04-19/20、B05-19。
- 方法：逐条重读路由、schema、服务层锁、金额出口和审计写入；每条至少一个源码/模型断言。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b01_b05_p2.py -q`
- 结果：`31 failed, 0 passed`。其中 3 条要改口径，见下文。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B01-02 | **确认** | `quote/service._f` 是 `float(value)`；`serialize_version` 只有 `total_amount_exact`，`subtotal/charge/discount/logistics/other_charge` 无 exact |
| B01-03 | **确认** | `convert_cny_to`、定制项底价、重建底价、版本合计多处 `.quantize(Decimal("0.01"))` 不带 `rounding`，默认 `ROUND_HALF_EVEN` |
| B01-10 | **确认** | `update_item` 审计 `before/after` 只有 `item_id/quoted_price`；`set_items` 只写 `after={"count": len(items)}`，数量/成本/物流成本/行金额都没进审计 |
| B02-03 | **确认** | `update_quote` 对 `data` 直接 `setattr`，`contact_id` 不校验存在/未删/属于本客户；`create_quote` 有完整校验 |
| B02-04 | **确认** | `delete_quote` 只查 `SalesOrder.quote_id`，不判 `sent_at/accepted_at/approval_status`；已发送/已接受未转单可软删 |
| B02-05 | **确认** | `resolve_discount_limit` 返回 `min(rows)`（最严），`resolve_min_margin` 也 `min(...)`（最松）；多角色叠加方向相反 |
| B02-07 | **确认** | 外币缺汇率只 warning、`fx=None`；`below_protection/below_authorized/below_margin` 比较不跳过，只有 `hard_floor` 有 `not (is_foreign and fx is None)` |
| B02-12 | **部分误报（入口写错）** | `/quote-versions` 的显式 null 被 `if valid_until is not None` 忽略，不会清空；会清空的是 `PATCH /quotes`（`if "valid_until" in data` 且无空值校验） |
| B02-14 | **确认** | `upsert_price_permission` 只有 `after`；`disable_price_rule` 只有 action/id；`delete_customer_price_rule` 只有 action/id，均无 `before` |
| B02-15 | **确认** | `lifecycle._record` 的 `after` 固定 `{version_id, version_no, **after}`；accept 传 `{}`、decline 只多 reason，无金额/币种/分项 |
| B03-04 | **确认** | `create_product_cost` 只校验日期倒置，直接 `ProductCost(...)`；不查同 SKU 同 `effective_from`，读取侧才 409 |
| B03-05 | **确认** | `quote_rate` 返回 `"amount": float(amount)`；`save_quote` 用 `Decimal(str(option["amount"]))`；`expected_amount` 比较也走 float |
| B03-06 | **确认** | `LogisticsTrialIn.quantity` 无 `max_digits/decimal_places`；重量/体积用原数量算，`prepare` 返回 4 位，落库列 3 位 |
| B03-07 | **确认** | `create_price_rule` 只判 `sku is None`，不判 `deleted_at/status`；导入端明确拒绝停用/软删 SKU |
| B03-19 | **部分误报（单行已修，整批仍泄露）** | 单行失败已改成 `report.write_failed_row(index, code, exc)`，内部只对 <500 的 `AppError` 回显 message，其余给稳定文案；但 `importing.finalize` 的 commit 失败仍 `str(exc)[:120]` |
| B03-20 | **确认** | `finalize` 的 `action="import"` 审计只有 `file/file_sha256/created/updated/skipped/failed/failed_rows`，无逐行 SKU/价格/旧值新值 |
| B03-21 | **确认** | `LogisticsCalculateRequest.quantity/weight_override/volume_override` 都是裸 `Decimal`，无 `gt/max_digits/decimal_places`；只拦 `<=0` |
| B04-04 | **确认** | `update_order` 的 `owner_id` 分支只查 `order:assign`、用户存在、active；没有 `ensure_in_scope`，创建订单时有 |
| B04-05 | **确认** | `create_change/confirm_change` 只拦 `cancelled`；`completed` 放行，可改交期与批次计划日 |
| B04-06 | **确认（建议升 P1）** | `refresh_status` 只要求 `order:view`，但 `erp_service.refresh_status` 会调 `order_service.change_status` 并写状态历史；只读可触发写入，且无订单级审计 |
| B04-07 | **行为成立，入口不成立（误报/降 P3）** | `ship_shipment_batch` 未传行默认 `planned_qty` 全发；但前端 `actualShipmentRows.map(...)` 每次传全行，没有“只传 A 行”的实际入口 |
| B04-08 | **确认** | `delta = ... if old_ship else None`；批次循环 `if batch.planned_date is None or delta is None: continue`，旧交期为空时计划日静默跳过 |
| B04-19 | **确认** | 取消订单审计 only `status/rejected_pending_payments/cancelled_receivable_plans/opportunity_id/note`，无订单总额、回款金额、被取消期次明细 |
| B04-20 | **确认** | `ShipmentBatchCreate.request_key` 可选；`create_shipment` 只在 `if request_key:` 时 reserve，无键直接建批次，双击出两条 |
| B05-06 | **确认** | `fetch_order_status` 直接 `first = orders[0]`，不校验 `o_id == external_id`；ERP 多单/错单会把别人状态写回 |
| B05-07 | **确认** | `_judge` 只认 `done` 或 409，无 `created_at` 超时/reaper；`in_flight` 落库后同键永久 409 |
| B05-08 | **确认** | `update_receivable` 不判 `plan.status`；已取消节点可改名称/到期日/金额/备注，金额变更会 `rebalance_order_allocations` |
| B05-09 | **确认** | `delete_receivable` 查 `PaymentRecord`/`PaymentAllocation` 不看回款状态；一条 `rejected` 回款永久阻止删除节点 |
| B05-10 | **确认** | `plan_payments` 用 `or_(receivable_plan_id, allocation)`，无 `PaymentRecord.order_id == plan.order_id`；脏数据可跨订单展示 |
| B05-12 | **确认** | `allocate_payment` manual 分支直接 sum 返回，不校验 `total <= received_amount`；路由有校验，service/迁移可绕过 |
| B05-19 | **确认** | confirm 审计 only `{"comment": ...}`；reject 只有 `status/comment`；无金额/币种/订单/期次/分配摘要 |

## 三条要改口径的

### B02-12：清空有效期的入口写错了

`PATCH /quote-versions/{id}`：

```python
valid_until = data.pop("valid_until", None)
if valid_until is not None:
    ...
```

显式 `null` 在这里被忽略，**不会清空**。真正会清空的是 `PATCH /quotes/{id}`：

```python
if "valid_until" in data:
    ...
    current_version.valid_until_snapshot = data["valid_until"]
for field, value in data.items():
    setattr(quote, field, value)
```

`data["valid_until"]` 可以是 `None`，没有 422。结论应是“**行为成立但入口写错**：`/quote-versions` 的显式 null 被忽略，`/quotes` 才会把主单和当前版快照一起清空”。修复时两个入口统一：显式 null 一律 422，长期有效走单独语义。

### B04-07：后端 API 的默认值成立，但前端没有这个入口

`ship_shipment_batch` 的默认值确实是“未传行按计划量全发”：

```python
qty = (
    ship_inputs.get(row.order_item_id, _d(row.planned_qty))
    if ship_inputs is not None
    else _d(row.planned_qty)
)
```

但前端实发页 `actualShipmentRows.map(...)` 每次提交整批全部行，不存在“只改 A、不传 B/C”的入口。按后端契约，这是“缺省值危险”；按现网可达路径，触发条件不成立。建议后端改成“`items` 非空必须覆盖本批全部行，缺行 422”，但严重度从 P2 降到 P3。

### B03-19：单行异常回显已经修了，只剩整批 commit

报告点名的三个单行位置现在都是：

```python
except Exception as exc:
    report.write_failed_row(index, code, exc)
```

`write_failed_row` 只对 `<500` 的 `AppError` 回显 message，其它异常只记日志并返回稳定文案。所以“单行失败回显 DB 原文”这部分是**过期结论**。仍成立的是 `core/importing.finalize`：

```python
except Exception as exc:
    await session.rollback()
    raise AppError(
        ErrorCode.SYSTEM_ERROR,
        f"导入提交失败，本次没有任何数据写入（{str(exc)[:120]}），请重试或联系管理员",
        500,
    )
```

修的时候只改整批 commit 这一处，不要重复改已经修好的单行路径。

## 本批最大漏项：B04-06 应该是权限边界 P1，不是 P2

审查员把 `order:view` 可刷新 ERP 状态写成 P2“只读权限能间接写状态”，但没有和 B04-03 的“状态机没有发货事实闸门”放在一起看。合起来是：

- `POST /orders/{id}/refresh-status` 只要求 `order:view`；
- 该入口不是只读：`erp_service.refresh_status` 拉取成功后会调 `order_service.change_status`，写 `order.status` 和 `order_status_history`；
- `change_status` 对 `shipped/delivered` 不要求已有发货批次/实发量；
- 这条写入没有订单审计，只有 `IntegrationLog`。

于是“只读账号”可以借 ERP 刷新把订单改成已发货/已签收，且没有业务审计可追。这不是体验/边界，是权限模型里的写操作越权，建议升 P1。修复要么把刷新拆成“只读拉取”和“应用状态”两个动作，后者要求 `order:manage` 并写订单审计；要么在 `change_status` 内对 `shipped/delivered` 强制校验发货事实。

## 残余

- B02-03 跨客户联系人、B03-04 同日成本、B04-04 范围外负责人未在隔离库造数据；当前是路由/服务层缺少校验的源码证据。
- B03-05/06 的 float 往返与 0.0004 数量未实际打 PostgreSQL；`Numeric(16,2/3)` 与 `float` 转换是确定事实。
- B05-06 的 ERP 多单返回依赖外部响应，未实网构造；适配器没有 `o_id` 匹配是确定事实。
- B03-19 的单行路径已用 `write_failed_row` 修掉；只保留整批 commit 的泄露结论。

## 2026-11-23 真实隔离库补验（B05-08/09/10/12）

`review/verification/p2_b05_receivable_state.py` 已用内存 SQLite 真实调用路由/服务，结果：

```text
[B05-08] 已取消节点改金额后 status=cancelled amount=200.00
[B05-10] 查 A 单期次 1 的回款，返回 order_id=[2]
[B05-12] 回款 100.00 的手工分配合计=150.00，service 返回=150.00（未拒绝）
[B05-09] 有 rejected 回款时删除被拒：AppError 该应收节点已有回款记录或收款分配，不能删除；请先核对并调整回款分配
```

结论：这 4 条不再只是源码断言，已在隔离库落到具体行为；仍未修复，先定业务口径再决定改代码或改测试。

## 2026-11-23 真实隔离库补验（B04-05/B04-06）

`review/verification/p2_b04_schedule_state.py`：

```text
[B04-05] status=completed -> 创建成功 change_id=1
[B04-05] status=cancelled -> 拒绝 AppError: 订单已取消，不能再创建或确认交期变更
```

B04-05 已从源码断言落到行为；B04-06 仍为源码级：`refresh-status` 只要求 `order:view`，却会调 `change_status` 写订单状态与历史，未连真实 ERP 做外部回包。

## 2026-11-23 金额精度补验（B01-03/B03-05/B03-06）

`review/verification/p2_money_precision.py` 实际调用：

```text
[B01-03] split_amounts(0.05, 50/50)=[Decimal('0.02'), Decimal('0.03')]；HALF_UP 应为 [Decimal('0.03'), Decimal('0.02')]
[B03-05] quote_rate(1.005kg, 1元/kg) amount=1.0 type=float；HALF_UP 应为 1.01
[B03-06] LogisticsCalculateRequest 接受 quantity=1.2345（4 位小数仍可通过）
[B03-06] 同值落到 Numeric(16,3) 会变 1.234
[B03-06] 超大 quantity 也通过：9999999999999999
```

结论：三条均可由真实函数/模型触发，不是纸面怀疑；B01-03 的默认 HALF_EVEN 与业务“四舍五入”口径不一致，B03-05 仍有 float 出口，B03-06 入参精度与落库精度不一致。

## 2026-11-23 B02 状态补验（B02-03/04/12）

`review/verification/p2_b02_quote_state.py` 实际输出：

```text
[B02-03] 不存在的 contact_id=999 被写入：contact_id=999
[B02-12] 显式 null 后 quote.valid_until=None version.valid_until_snapshot=None
[B02-04] status=accepted 且无订单，删除后 deleted_at=2026-11-23 03:27:56.801937+00:00
```

结论：B02-03 联系人归属不校验、B02-04 已接受未转单可删、B02-12 `/quotes` 显式 null 会清空主单与草稿快照，均已落到真实路由行为。

## 2026-11-23 B02-05 多角色权限补验

```text
[B02-05] 角色并集 discount_limit=5.0000（5 比 10 严，取 min 正确）
[B02-05] 角色并集 minimum_margin=0.1000 can_approve=True（0.10 比 0.20 松，取 min 反而放宽）
```

结论：折扣方向正确，最低毛利方向错误；多角色并集并非统一“最严”。

## 2026-11-23 B02-15 审计补验

```text
[B02-15] accept changed=True audit_after={'version_id': 1, 'version_no': 1}
```

结论：接受报价的审计未记录金额/币种，B02-15 由源码断言落到真实 AuditLog 内容。

## 2026-11-23 B02-14 审计补验

```text
[B02-14] 审计 after keys=['can_approve', 'discount_limit', 'id', 'minimum_margin', 'remark', 'role_id', 'role_name', 'status'] before=None
[B02-14] 新值 minimum_margin=0.1 discount_limit=0.05
```

结论：价格权限变更只留新值，没有旧值；B02-14 成立。

## 2026-11-23 B02-07 入口可达性

```text
[B02-07] currency=CNY -> 允许 CNY
[B02-07] currency=USD -> 拒绝 code=40002 ...
[B02-07] currency=EUR -> 拒绝 code=40002 ...
```

结论：正常核价入口不能进 USD/EUR，缺汇率分支只有历史外币版本的重算路径可能触发；按“当前业务入口”降为待确认。

## 2026-11-23 B03-04/B03-07 真实补验

```text
[B03-04] 同 SKU 同生效日成本条数=2（期望 1，实际 2）
[B03-07] 软删 SKU 建价格规则数=1 status=['active'] sku.deleted_at=2026-11-23 ...
```

结论：两条均由真实路由落到行为；B03-04 页面入口可重复落成本，B03-07 软删 SKU 仍可建 active 规则。

## 2026-11-23 B04-04 真实补验

```text
[B04-04] 调用者 data_scope=self，订单 owner 101 -> 202（外部门 202）
```

结论：订单改派不校验目标用户数据范围，B04-04 成立。
