# 后端 P2 复核（第七批：B06–B08 剩余条目）

> 日期 2026-11-24，只读当前代码。只审查，未改业务代码。

## 一、结果

本批 11 条：

- **2 条已修**：B06-07、B07-05
- **9 条仍成立**：B06-06、B06-08、B06-09、B06-15、B06-16、B07-08、B07-09、B07-10、B08-01
- **0 条部分修**

已在前批计入、本批不重复统计的：B06-10、B07-06、B07-07、B07-15（仍成立），B08-02/B08-03/B09-03（已修），B09-05（已修）。

## 二、已修

| 编号 | 当前代码 | 说明 |
|---|---|---|
| B06-07 | `erp/collection.py:264-265` 缺币种默认 CNY 后立即 `reject_foreign_currency`；批处理 `:1112-1116` 也逐条判断，非人民币写 `_record_currency_rejection` 并计数 | 不再把外币缺失静默当人民币入账；非人民币明确拒收并留日志 |
| B07-05 | `erp/reconcile.py:1733-1744` 售后调整金额先取 `financial_adjustment_amount`，再取 `incoming["amount"]`；`raw_amount is None` 时不进入建台账分支（`:1745-1795` 的 `FinancialAdjustment` 只在有金额时创建） | 金额缺失不再生成 `amount=NULL` 的待处理退款台账，改为要求人工补金额 |

## 三、仍成立（逐条证据）

| 编号 | 当前代码事实 | 结论 |
|---|---|---|
| B06-06 | `order/model.py:85` `erp_order_id` 仍是普通 `String(64), nullable=True`，无 `unique`；`erp/service.py:977-982` `_load_order_by_external` 仍 `scalars().first()`；迁移 `25e2fd95da23` 也未建唯一索引 | 历史重复单号仍会挂错订单，不能靠数据库兜底 |
| B06-08 | `erp/collection.py:1510-1559` `replay_pending_mappings` 查询 `ExternalObjectMapping` 时没有 `with_for_update()`，循环里直接改 `internal_id/match_status`；`assign_mapping:1570-1600` 也直接改 | 并发人工匹配/重放仍可互相覆盖 |
| B06-09 | `erp/service.py:1173-1228` 回调 `new_status = STATUS_MAP.get(raw_status)`，为 `None` 时跳过状态变更，但末尾仍 `log.status = LOG_STATUS_SUCCESS`、`processing_status=EVENT_PROCESSED`；`refresh_status:798-835` 未知状态也返回成功并留 `LOG_STATUS_SUCCESS` | 未知 raw_status 仍记 success/processed，后续补映射不会被当作待重放 |
| B06-15 | `erp/collection.py:1249` `cursor.last_error = _clip(f"{type(error).__name__}: {error}")` 原异常文本；`:548` 状态接口直接 `"last_error": row.last_error` | 底层 SQL/表列/连接细节仍会返前端 |
| B06-16 | `erp/router.py:603-625` `replay_external_mappings` 调服务、`commit`、返回，无 `write_audit`；只有 `assign_mapping` 路由 `:585` 有审计 | 全局映射重放仍无批次/逐条变更审计 |
| B07-08 | `erp/reconcile.py:1331-1349` `occurred_at` 为空时只尝试父记录/订单头补时间，补不到就**不进 `period_rows`**；`:1350-1360` 随后把该期 open diff 自动置 `resolved/auto_reconciled`；`:577-590` 只统计 `missing_time_orders` | 缺时间的外部事实被当成“本期没有”，还会误关差异 |
| B07-09 | `erp/router.py:769-780` 先 `get_diff` 做 `_ensure_diff_scope`，再调 `confirm_diff`；`reconcile.py:1665-1675` 才 `with_for_update()` 锁行。范围判断与锁之间仍可被并发改 `internal_id` | TOCTOU 仍在 |
| B07-10 | `contract/customer_flow.py:270-311` `sync_approval` 外部/自建 OA 两条分支都只 `commit`，全程没有 `write_audit`；文件内审计只覆盖发起 OA（`:206`、`:227`、`:263`）和合同更新（`:451`） | 手工同步 OA 审批仍无审计 |
| B08-01 | `bizdoc/tokens.py:33` `SOURCE_FIELDS["customer"]` 仍含 `"level": "客户等级"`；生成时按 `getattr(customer, "level")` 直接填 | 客户内部等级仍可印到对客单据 |

## 四、结论

- B06-07 的修法比原报告更彻底：缺币种仍按 CNY，但非人民币一律拒收并留证据，不再“静默入账”。
- B07-05 已从“缺金额也建 NULL 台账”改成“缺金额不建、要求人工补”，这是代码事实。
- 其余 9 条仍是真实代码缺口，尤其 B06-06 的唯一约束、B06-08 的重放锁、B07-08 的缺时间误关差异。
- 下一批：B10–B14 剩余 P2。
