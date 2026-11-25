# B06–B09 P2 逐条复核（证伪）

- 范围：B06-06..10、B07-05..10/15、B08-01..03、B09-03/05，共 17 条 P2。
- 方法：每条重读触发路径与上下游，查约束/锁/其他闸门；能纯函数或最小替身跑的进测试。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b06_b09_p2.py -q`
- 结果：`16 failed, 1 passed`。通过的是 B08-02 的控制项——当前 PDF 页脚已经转义，说明该条报告已过期，不是缺陷。
- 2026-11-23 复测更正：B08-03、B09-03 也已被当前代码修掉（见下表），仍成立的为 14 条；旧测试断言失败只说明用例还按旧代码写。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B06-06 | **确认（已实测）** | 模型 `erp_order_id.unique=None`，仅有两个索引 `quote_version_id`、`customer_id`；无唯一约束，回读/采集都用 `.first()` |
| B06-07 | **确认（已实测）** | 不传 currency 归一到 `CNY`；USD 会被 `reject_foreign_currency` 拒。缺币种仍被静默当人民币 |
| B06-08 | **确认** | `replay_pending_mappings` 只 `select`，无 `with_for_update`、无版本条件；人工匹配提交后会被旧 pending 结果覆盖 |
| B06-09 | **确认** | `new_status=None` 没有单独分支，仍写 `LOG_STATUS_SUCCESS` + `processing_status=processed`；补映射后 `_find_processed_event` 命中 success，永不再改状态 |
| B06-10 | **确认** | `collection_status(session)` 不接收 `owner_ids`/`shop_ids`，路由只要 `order:view`；全表 count、全量来源证据、全量 cursor |
| B07-05 | **确认** | `DiffConfirmRequest.financial_adjustment_amount` 可选；售后 `manual` 时 `raw_amount` 可为 `None`，`FinancialAdjustment.amount` 可空，仍落 `pending` |
| B07-06 | **确认** | 核定审计 `after` 只有 `status/resolution/note`，没有 `financial_adjustment.id/type/amount/currency/order_id` |
| B07-07 | **确认** | `list_runs(session, system_type, page, page_size)` 无 `owner_ids`；`order:view` 能看到全公司批次 `counters` |
| B07-08 | **确认** | `seen_external` 只收本期 `facts["orders"]`；`_period_orders` 带 `occurred_at` 过滤；缺时间的已映射外部单会被判 `missing_external`，且该类型不在 `AUTO_CLOSE_TYPES` |
| B07-09 | **确认** | 路由先 `_ensure_diff_scope`，`confirm_diff` 再 `FOR UPDATE` 并 `populate_existing`；锁后 `internal_id` 可已是另一张订单，范围检查对象与落账对象不一致 |
| B07-10 | **确认** | `sync_approval` 内没有 `write_audit`；只 `store_snapshot` + commit，审批状态变化无操作人/前后状态 |
| B07-15 | **确认** | 签署审计 `after` 只有 `doc_no/file_id`，没有金额、报价版本、OA 实例、签署件哈希 |
| B08-01 | **确认** | `SOURCE_FIELDS["customer"]` 仍有 `level`；模块 docstring 自己把等级列为内部字段，模板作者可直接印到对客单据 |
| B08-02 | **误报（报告已过期）** | 当前 `pdf.py` 的 gaps 页脚已写 `_esc(gap.get('label'))` / `_esc(gap.get('display'))`，`<`/`&` 不再炸；控制测试通过 |
| B08-03 | **不成立（已修）** | 当前 `bizdoc/router.py` 的 `TemplateCreate.remark` 已有 `max_length=255`；300 字被 Pydantic 拒（`string_too_long`），不再到 DB 才 500 |
| B09-03 | **不成立（已修）** | 当前 `RulePayload.description` 已有 `max_length=255`；`ApprovalDefinitionUpdate` 继承 `PatchModel`，按 `approval_definitions` 列宽校验，300 字 name 被拒。原报告“description/Update 无长度”已过期 |
| B09-05 | **确认** | `approval/router.py` 仍 `float(version.total_amount)`，审批确认入口金额是 JSON number |

## 四条需要单独说明

### B06-06：唯一约束缺失，但 `.first()` 才是直接风险

`erp_order_id` 在模型和迁移里都没有唯一约束。正常写入路径虽然会先查重，但历史数据、人工直写、旧 `record_external_order_id` 都可能留下重复。`_load_order_by_external` 先查主表 `.first()`，`_resolve_mapping` 也先按 `erp_order_id` `.first()`；一旦重复，回调状态、采集事实、对账归属可能写到另一张单。`ExternalMapping` 的 `(system_type,business_type,external_id)` 唯一只能保证映射表不双绑，保证不了主表。

### B06-09：未知状态不是“没变化”，是“已处理”

`new_status=None` 时跳过 `change_status`，但 `log.status` 仍写 `SUCCESS`，`response_data.processing_status` 仍写 `EVENT_PROCESSED`，只把 `mapped` 记成 `False`。下一次同一事件重放，`_find_processed_event` 看到 success 直接返回“此前已处理”。所以补 `STATUS_MAP` 也不会生效。修的时候要把 `new_status is None` 当成 `unmatched`/`failed` 落库，并让异常队列能按 `raw_status` 重放。

### B07-09：先查范围、后锁行，锁后对象已经换了

`confirm_reconcile_diff` 先用当前 `diff.internal_id` 过 `_ensure_diff_scope`，然后 `confirm_diff` 用 `SELECT ... FOR UPDATE` + `populate_existing` 重新读。另一个对账事务如果在两步之间把 `internal_id` 从 A 订单改成 B 订单，锁后拿到的是 B；范围检查却按 A 做的。`FinancialAdjustment.order_id` 就挂到 B。修的时候要么把范围检查搬进 `confirm_diff` 的锁内，要么路由先锁再查范围。

### B08-02：这条是报告过期，不是误判

报告引用的 `pdf.py:309-316` 在当前仓库已经是：

```python
f"{_esc(gap.get('label'))}（{_esc(gap.get('display'))}）"
```

`_esc` 对 `&<>` 做了实体转义，gaps 页脚不再按 XML 解析裸文本。`test_b08_02_pdf_footer_should_escape_gaps` 通过。说明审查员写报告后这里被修过，或者报告引用了旧版本；总之当前不成立。

## 本批最大漏项

**B09-03 被写成“创建/更新全都没长度”，实际创建路径已经对齐，真正漏的是 `ApprovalDefinitionUpdate` 和 `RulePayload.description`。** 这不是“误报整条”，而是“范围写错”：如果按报告原文去补 `ApprovalDefinitionCreate`，会重复加已经有的约束；真正会 500 的 PATCH 路径仍然没修。复核时要把 create/update 分开看，不能共用一句“Pydantic 没有 max_length”。

## 残余

- B06-11..14、B07-11..14、B08-04、B09-04 是 P3，本批未逐条展开；B08-04 的 `_cancel_safe` 复用 coroutine 已在代码里确认仍存在。
- B07-08 的 `occurred_at IS NULL` 真实数据形态未造库复现；当前是源码路径 + 查询条件证据。
- B09-03 的 `RulePayload.description` 只做了源码核对，未跑 256 字建规则。


## 2026-11-23 真实补验

`review/verification/p2_b06_erp_normalize.py`：

```text
[B06-07] 不传 currency -> currency=CNY
[B06-07] USD 被拒 -> AppError: 采集第 2 条的币种是「USD」...
[B06-06] erp_order_id unique=None indexes=[('ix_sales_orders_quote_version', True, ['quote_version_id']), ('ix_sales_orders_customer', False, ['customer_id'])]
```

`review/verification/p2_b08_b09_schema.py`：

```text
[B08-03] 被拒 string_too_long
[B09-03-desc] 被拒 string_too_long
[B09-03-update] 被拒 value_error（审批流名称最长 128 个字符，当前 300 个）
```

结论：B06-06、B06-07 仍成立；B08-03、B09-03 在当前代码上已不成立，应从确认清单移除，不再要求修复。


## 2026-11-23 B07-05 补充

`review/verification/p2_b07_diff_schema.py`：

```text
[B07-05] resolution=manual financial_adjustment_amount=None
```

入口允许不填金额，B07-05 成立；B07-06 审计缺字段仍为源码级。
