# B06–B10 高严重度复核（证伪）

- 范围：B06/B07/B09/B10 的 P1；B08 本块无 P1，只记结论。B06/B07/B09/B10 的 P2 未逐条复核。
- 方法：重读采集/推单/合同/OA 闸门/审批上下文；能纯函数或最小替身跑的直接跑。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b06_b10_highlights.py -q`
- 结果：`11 failed`，全部是“修复后应有”的断言失败，说明缺陷可复现；没有控制项通过（本批 P1 均落在代码路径上，不是例子臆造）。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B06-01 P1 | **确认** | 订单映射的 `parent_key` 被写成自己的 `external_id`；人工匹配查“父订单”查到自身，`internal_id` 为空 → 409，订单永远匹配不上 |
| B06-02 P1 | **确认** | `assign_mapping` 调 `replay_pending_mappings` 只传 system/shop，不传 object_type、不看操作人范围；一次人工匹配会重放同店铺全部类型（上限 500 条） |
| B06-03 P1 | **确认** | `normalize_item` 对 `currency != CNY` 直接抛 422；`collect_once` 页级异常回滚整页并保留 `page_token`，一条外币记录让该来源永久卡在同一页 |
| B06-04 P1 | **确认（影响限大额）** | 推单报文 `pay_amount/qty/price/amount` 全走 `float`；Numeric(16,2) 上限附近会丢分，正常单量级不显现 |
| B06-05 P1 | **确认** | 报文只有 `pay_amount` 和行金额，没有 `logistics_amount/other_charge_amount/discount_amount`；有运费/折扣时行合计与总额必不相等 |
| B07-02 P1 | **确认** | `verify_instance` 只验流程码/发起人/合同编号/名称/“有附件”，不验金额、客户主体、附件 fileId/哈希；错金额审批可被关联成 approved |
| B07-03 P1 | **确认** | 合同正文/抬头是生成时快照，OA `amount` 取实时 `quote_version.total_amount` 或 `order.total_amount`；`ContractDocument` 无冻结金额列 |
| B07-04 P1 | **确认** | `DocumentCreate.request_key` 可选；不带键直接走新建，连点两次生成两份合同、两条待办 |
| B08 | **无 P1** | 报告自身结论 0 P1；B08-P1（签署件跨合同复用）仍是待确认，不定级 |
| B09-01 P1 | **确认** | 传了 `release_stage` 但 `result.business_stages` 缺该阶段时，直接回退 `row.status == approved`；阶段闸门降级成整体闸门 |
| B09-02 P1 | **误报（P1 触发不成立）** | 上下文转 float 是事实，但金额/毛利/让价都来自 2–4 位小数的 Decimal，默认 5 万档下最小差 0.01，远大于 float 在该量级的分辨率（5e4 时 ulp≈7.3e-12）；要翻档需金额到 1e14 附近。应降 P3 统一口径 |
| B10-01 P1 | **确认** | `_apply_sample_summary` 在缺 `business_stages.sample` 时用 `new_status == approved` 释放，打样单直接落 approved；现有测试把该回退写成期望 |

## 三条需要单独说明

### B06-01：不是“可能”，是订单类型必现

`normalize_item` 对 `object_type == order` 执行 `order_key = order_key or dedupe_key`，而订单的 `dedupe_key` 就是 `external_id`。`_ensure_mapping` 再执行 `mapping.parent_key = record.order_key or record.parent_key`，于是每条订单映射的 `parent_key` 都等于自己的 `external_id`。

`_ensure_mapping_target_scope` 只看 `mapping.parent_key` 非空，就去查 `object_type=order AND external_id=parent_key` 的映射，命中的就是这条自己；此时 `internal_id` 还是 `None`，直接 409。结果是“未知订单”这一类永远无法人工匹配，待匹配队列清不空。复现测试断言“订单映射的 parent_key 应为 None”，当前失败。

### B06-03：和 `trade_mode` 的明文口径相反

`backend/app/core/trade_mode.py` 的三条边界写得明确：**外部同步（ERP）不拦**，因为“拒收会让整批同步失败，外部数据不是我们说了算”，靠统计页“含外币、未折算”提示兜底。但 `normalize_item` 第一件事就是 `reject_foreign_currency`，异常向上冒到 `collect_once` 的页级 `except`，整页回滚、`cursor.status=failed`、`page_token` 保留。下一轮从同一页重试，仍然同一条外币。

这不是“外币该不该收”的口径问题，是“一条坏记录该不该拖死整页”的实现问题。按 `trade_mode` 应逐条保留并标记，或逐条跳过并进异常队列，都不能整页失败。

### B09-02：float 是真的，P1 是假的

`total_cny`、`gross_margin`、`min_item_margin`、`concession` 确实都 `float(...)`。但可触发翻档的输入全部是有限小数：

- 金额：`Numeric(16,2)`，最小差 0.01；5 万档 float 的 ulp 约 `7.3e-12`，0.01 比它大 9 个数量级。
- 毛利率：收入/成本来自 2–4 位小数，30% 附近的最小差远大于 float 在该处的 ulp（约 `3.6e-15`）。
- 让价：两版总额都是 2 位小数，1000 元档最小差 0.01，ulp 约 `1.1e-13`。

只有总额接近 `Numeric(16,2)` 上限（约 1e14，ulp≈0.0156）时，0.01 才可能被吞。默认审批档 50000，实际业务不会到 1e14，所以“边界单误命中免审”的 P1 不成立。口径该统一成 Decimal，但那是 P3 的整洁性问题，不是审批路由错误。

## 本批最大漏项（跨 B07/B08）

**签署件与 OA 审批附件之间没有文件级绑定，错审批 + 任意扫描件可以组合成一份“已签合同”。**

审查员分别写了 B07-02（关联审批不验金额/主体/附件内容）和 B08-P1（同一扫描件可签给多份合同），但没有把两段连起来：

1. `verify_instance` 只要求“有任意附件”，不要求附件 `fileId`/哈希等于 CRM 原件或生成稿；
2. `sign_document` 只要求 `latest_contract_row.status == approved`，加上签署件不是 `_original_file_id`，不要求签署件与 OA 里那份 approved 附件是同一份；
3. 于是“金额不对但编号/名称对得上的审批”可以先被关联成 approved，再用任何一张可见的扫描件登记签署，台账上就是一份审批通过、已签署的合同。

这比单独的“金额没校验”更危险：金额校验补上了，附件仍可替换；附件哈希补上了，审批仍可错金额。两处必须同时绑定到同一份合同快照（金额 + 客户主体 + 附件哈希），签署登记时再复核一次。

## 残余/未覆盖

- B06-06..10、B07-05..15、B09-03/05、B10-02..07 的 P2 未逐条复核；本批只覆盖 P1。
- B06-04 的 float 影响需要金额到 1e11–1e12 才显现，严重度建议从 P1 降 P2；但报文精度口径仍应改。
- B09-02 的复现测试断言“上下文保留 Decimal”，当前失败；它证明的是口径问题，不证明 P1 的翻档后果。
- B08-P1/P2 是待确认，不定级；本批没有把它们升级成 P1。
