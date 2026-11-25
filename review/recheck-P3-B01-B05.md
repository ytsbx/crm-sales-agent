# B01–B05 P3 逐条复核（证伪）

- 范围：主块 31 条 + 补充审查 6 条，共 37 条 P3。
  - 主块：B01-04/05/06/09、B02-08/09/10/11/13、B03-08..18、B04-09..14/16、B05-11/13/14/15。
  - 补充：B01-11、B02-16/17、B03-22、B04-21、B05-20。
- 方法：逐条读代码；纯整洁度/文案/审计类用源码断言，结构类直接查模型约束。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b01_b05_p3.py -q`
- 结果：`37 failed, 0 passed`。P3 没有发现需要升级为 P0/P1 的隐藏路径；1 条与既有 P2 重复，1 条已关闭。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B01-04 | **确认** | `quote/service.py` 仍有 `getLogger("probe").warning("RECALC ... prev_floor=...")`，每次重建都打，且带底价派生值 |
| B01-05 | **确认** | 审批理由仍是 `成本 ¥{cost}×(1+{min_ratio})`；实际底价是 `成本 ÷ (1−r)`，按文案复算会偏低 |
| B01-06 | **确认** | `margin_delta = round(current["margin"] - previous["margin"], 6)`，Decimal 相减后转 float round |
| B01-09 | **确认（防御性）** | `create_version` 对 `source_version_id` 只查 `candidate.quote_id == quote.id`，不复核 `private_draft/draft_scope`；当前两个 HTTP 入口都先 `get_visible_version`，不是现网漏洞 |
| B01-11 | **确认** | `update_item` 审计写 `float(item.quoted_price)`；`create_version`/批量路径也写 `float(version.total_amount)`，审计与 Decimal 真值可差 1 分 |
| B02-08 | **确认** | `pricing/service.py` 的运费分摊、退税折算仍是 `.quantize(Decimal("0.0001"))`，默认 HALF_EVEN |
| B02-09 | **确认** | `serialize_quote` 的 `current_version_no` 取自传入的 `version`；历史版本详情页会把 V1 填成“当前版本”，而 `current_version_id` 仍指 V3 |
| B02-10 | **确认** | `send_email` 直接 `session.add(QuoteSendLog(...))`，无 `request_key`/唯一键；重复点击多登记 |
| B02-11 | **确认** | `quote/router.py` 仍有 `__all__ = ["date", "QuoteItem"]`，不是路由对象 |
| B02-13 | **确认（与 B03-04 同根）** | `ProductCost` 无 `(sku_id, effective_from)` 唯一约束；写侧不拦，核价读取才 409 |
| B02-16 | **确认** | `_session_dialect` 用 `except Exception: return ""`；`lock_sku_price_rules` 把空串当非 PostgreSQL，静默跳过 advisory 锁 |
| B02-17 | **确认** | `create_quote` 的 `except Exception` 里先 `await idempotency.release` 再 `raise`；release 自身失败会替换原始异常，占位也可能留下 |
| B03-08 | **确认** | `drafts.confirm_draft` 只比明细多重集/币种/付款条件，不校验 `draft.source_context['id'] == payload.quote_version_id` |
| B03-09 | **确认** | `logistics.py` 的 `by_weight/by_volume` 用 `.quantize(Decimal("0.01"))`，默认 HALF_EVEN；与 `core/money` 的 HALF_UP 不一致 |
| B03-10 | **确认** | `create_exchange_rate` 只拦 `quote == base`、`rate <= 0`，不要求 `base == "CNY"`；USD→EUR 可入库 |
| B03-11 | **确认** | `LogisticsRateCreate.provider/remark` 无 `max_length`；模型列 64/255，试算 `origin/destination` 也无 64 限制 |
| B03-12 | **确认** | `PriceRuleCreate.customer_level: str | None`、`status: str | None`，无 Literal/白名单；导入端只认 A/B/C/D，页面入口更松 |
| B03-13 | **确认** | `batch_calculate` 的 `items: list[PricingRequest]` 无 `max_length`、无分批；单请求可放大 DB 负载 |
| B03-14 | **确认** | `logistics_router.calculate` 无 `request_key`；`save=true` 双击/重试每次插一条 `LogisticsQuote` |
| B03-15 | **确认** | `FiniteReceivableRatios = Annotated[list[float], ...]`；拆金额 `(total * ratio).quantize(Decimal("0.01"))` 默认 HALF_EVEN |
| B03-16 | **确认** | `logistics.save_quote` 的 `unit_price` 固定取 `unit_price_per_kg`，体积计价时金额按 `unit_price_per_volume` 算，主字段与金额对不上 |
| B03-17 | **确认** | `delete_logistics_rate` 直接 `session.get` + `delete`，不拿 `_lock_logistics_rate_changes`；与 PATCH/新建并发可 500 |
| B03-18 | **确认（与 B06-06 重复）** | `SalesOrder.erp_order_id` 是普通 `String(64)`，无部分唯一索引；订单侧无法保证一 ERP 单只映射一次 |
| B03-22 | **确认** | `pricing/io_router` 逐行 `except Exception` 后只 `report.write_failed_row`；`write_failed_row` 只 `logger.warning(类型)`，无 `logger.exception`/堆栈，编程错误被降级成行失败 |
| B04-09 | **确认** | `cancel_order` 签名只有 `order_id/request/user/session`，无 reason/remark；`change_status` 对 completed→cancelled 要理由，接口无法完成 |
| B04-10 | **确认** | `cancel_order` 只把 `batch.status = "cancelled"`，不调 `milestones_svc.drop_batch_node`；动态批次节点残留 |
| B04-11 | **确认** | `OrderCreate.items: list[OrderItemInput] = Field(min_length=1)`，无 `max_length`；与草稿 200 上限不一致 |
| B04-12 | **确认** | `sync_payment_milestones` 的 `settled = bool(count) and planned > 0 and not unsettled`；手工单无应收计划，收款节点永不完成 |
| B04-13 | **确认** | `create_order_from_quote` 命中 existing 直接 `DUPLICATE_CONVERT` 409，不回放 `order_id/order_no`；响应丢失后重试看到“失败” |
| B04-14 | **确认（结构）** | `ReceivablePlan` 只有 `(order_id,status)` 普通索引，无 `(order_id, plan_name)` 唯一；`PaymentRecord.receivable_plan_id` 无 ForeignKey |
| B04-16 | **确认** | `create_order_from_quote` 的 `except IntegrityError` 执行 `await session.rollback()`，回滚整个调用方事务，不只本次插入 |
| B04-21 | **确认** | `change_status` 的 `write_audit` 只有 `after={"status": payload.status}`，无 before/remark/source；完整流转只在 `OrderStatusHistory` |
| B05-11 | **确认** | `order_finance_summary` 的 `unreceived_amount = total - received`，不判 cancelled，也不拆 overpaid；与 `serialize_order` 取消归零口径相反 |
| B05-13 | **确认（历史数据前提）** | `allocate_payment` 只 `plan.currency = plan.currency or record.currency`，不校验 `plan.currency != record.currency`；正常路径同币，历史混合数据可 1:1 错配 |
| B05-14 | **确认** | `require_confirmation` 全仓库只有定义和文档字符串，没有业务调用；各路由自行 `if not confirm ... confirmation_message` |
| B05-15 | **确认** | `upload_payment_voucher` 先 `get_visible_payment(for_update=True)`，再 `await storage.save_upload(file)`；订单/节点/回款锁跨磁盘 IO |
| B05-20 | **确认** | `refresh_overdue_plans` 在循环内 `if commit_per_order: await session.commit()`，无 `last_order_id` 断点/进度审计；中途失败留下半批次 |

## 需要单独说明的五条

### B03-18：与 B06-06 重复，不另算一条

`SalesOrder.erp_order_id` 的结构缺口已经在 B06-06（P2）确认过：“回读/匹配 `.first()`，无唯一约束”。本条 P3 是同一事实的订单侧描述，结论并入 B06-06，不单独升级、不重复修复。

### B04-14 / B05-13：不是“两个独立 P3”，是回款归属缺一组完整性约束

- `ReceivablePlan` 没有 `(order_id, plan_name)` 唯一约束，重复生成会多一条同期应收；
- `PaymentRecord.receivable_plan_id` 没有外键，回款可以挂到一个不存在/别的订单的期；
- `allocate_payment` 不校验 `plan.currency == record.currency`，只给空币种补值；
- `plan_payments` 又用 `or_(receivable_plan_id, allocation)` 查展示，不校验 `payment.order_id == plan.order_id`。

四条放在一起，根因是**回款 → 应收期的归属只靠服务层“正常路径都一样”**，没有 DB 外键/唯一约束兜底。历史混合数据或直接改库后，一笔钱可以被算到别的订单/期/币种上，现金总额不变但归属全错。修复应优先补 FK/唯一约束和服务层三项一致性校验，而不是只改展示。

### B03-10：任意基准币种会削弱 CNY-only 闸门

项目规则是“唯一业务币种 CNY，非人民币明确拒绝”。`create_exchange_rate` 却允许 `base_currency="USD"`、`quote_currency="EUR"`，只拦相同币种和 `rate <= 0`。这类交叉汇率虽然当前没人用，但会让后续“有汇率就能换”的路径误以为 USD/EUR 可用，和 B03-03 的 CNY-only 规则冲突。P3 保留，但修复应与 B03-03 一起收口：基准币种固定 CNY，非 CNY 直接 422。

### B05-15：锁内做磁盘 IO，是“锁持有时间”问题，不是金额问题

```python
record = await svc.get_visible_payment(session, user, payment_id, for_update=True)
if record.status != "pending": ...
object_key, size, checksum = await storage.save_upload(file)
```

`save_upload` 在订单/节点/回款行锁之后执行。大文件或慢对象存储会把同一订单的取消、确认、改应收全部阻塞在行锁上；超时重试又可能重复上传。修复顺序应是“锁外落盘/校验 → 短事务锁记录 → 绑定 `voucher_file_id`”，并加上传大小/超时上限。

### B02-17：`release` 失败会掩盖原始异常，但不会造成金额错误

`create_quote` 的异常分支先 `release` 再 `raise`，release 自身失败会替换原始异常，并可能留下 `request_keys` 占位。当前后果是“用户看到错误原因不对/同键重试被判同键不同内容”，不是重复建单或金额错。P3 保留；修复是把 release 包在自己的 try/except 里，记录 release 失败但保留原始异常。

## 本批最大漏项：回款归属的“订单/期/币种”三一致性没有统一闸门

审查员把 B04-14（缺唯一/FK）、B05-10（跨订单展示）、B05-13（币种不匹配）分开列，但它们是同一个根因：**钱挂到哪张单、哪一期、哪个币种，只在各服务函数里各自校验，没有一处统一的归属校验**。

- `PaymentRecord.receivable_plan_id` 无 FK，回款可挂不存在的期；
- `allocate_payment` 不校验 plan 属于同一 order，也不校验币种；
- `plan_payments` 展示时又不校验 order_id；
- 应收计划本身没有 `(order_id, plan_name)` 唯一，重复计划会放大上述问题。

正常 API 路径下这些字段都继承同一订单，所以现网触发面小；但历史导入、直接改库、未来新增的 Agent/脚本入口一旦绕过服务层，就会把一笔回款算到别的订单/期/币种上，且报表只看金额合计时完全看不出来。

建议做一层 `ensure_payment_belongs_to_order(payment, plan, order)`：校验 `payment.order_id == plan.order_id == order.id`、`payment.currency == plan.currency == order.currency`；在 `allocate_payment`、`plan_payments`、`update_payment_allocations`、迁移脚本四处统一调用；DB 侧补 `receivable_plan_id` FK 和 `(order_id, plan_name)` 唯一约束。不要继续在四个函数里各写一份 if。

## 残余

- B03-10/B03-13/B03-14/B03-17 的超大请求、并发删除/修改未在隔离库压测；当前是路由/服务层缺少闸门的源码证据。
- B04-14/B05-13 的历史混合币种/跨订单回款未造数据；正常 API 路径不会产生，属防御性缺口。
- B02-17 的 release 失败注入未做；当前是异常分支源码证据。
- B04-15 已由 B06 复核关闭，不再列入 P3 结论。
