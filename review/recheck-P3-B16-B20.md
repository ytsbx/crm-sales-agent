# B16、B18–B20 P3 逐条复核（证伪）

- 范围：B16-07；B18-05..10；B19-06..12；B20-07..14，共 22 条。B17 无 P3。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b16_b20_p3.py -q`
- 结果：`22 failed, 0 passed`。全部成立，无误报；B20-14 建议升 P2。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B16-07 | **确认** | `global_search` 的 `like = f"%{keyword_key}%"` 未转义 `%/_`；`total = sum(len(items)...)` 是各组 `limit` 截断后的条数，不是全量 |
| B18-05 | **确认** | `list_cases` 的 `child_of.setdefault(parent_id, child_id)` 无 `order_by`；详情 `.limit(1)` 也无排序，`superseded_by` 可能指向 withdrawn/rejected 修订稿 |
| B18-06 | **确认** | `TimingReport.business_type` 无 `max_length`；`business_id/typed_fields/rework_count` 无 `le`；模型列 `String(32)/BigInteger/Integer`，超大值到 DB 才 500 |
| B18-07 | **确认** | `GET /sales-targets`、`/sales-targets/bases` 的 `year: int = Query(...)` 无 `ge/le`；`basis_for` 对任意缺失年份会 `_freeze_basis` 并提交，GET 产生写副作用、无审计 |
| B18-08 | **确认** | 案例 create 审计只有 `after={"title"}`；update 连 after 都没有；submit/review/delete 只有 action，无 before/after，证据替换无留痕 |
| B18-09 | **确认** | `if payload.customer_id:` 把 0 当 False 跳过校验；`CaseUpdate.title` 只有 max_length 200、无 min_length；`submit_case` 只判 `not case.title`，空格标题可提交 |
| B18-10 | **确认** | `contact_util` 的客户/联系人查重 `ilike(f"%{...}%")` 未转义；`%/_` 会扩大候选范围（仍受数据范围限制） |
| B19-06 | **确认** | `FollowUpCreate.followup_type: str = "电话"` 无 `max_length`；`FollowUpUpdate.followup_type: str | None` 允许 null/空；模型 `String(32)` 非空，null/33 字符到 DB 才 500 |
| B19-07 | **确认** | `LeadCreate` 只有 `name` 有 max；`LeadUpdate.name` 无 min；`LeadBatchAssign.lead_ids/LeadBatchIds.lead_ids` 无 max；超长/空名到 DB 才 500 |
| B19-08 | **确认** | `OpportunityUpdate.title` 无 min；`source/competitor/risk_level/next_action` 和明细 `specification/color/package_requirement/destination` 无 max；`items/sku_ids` 无上限；`limit` 无 ge/le |
| B19-09 | **确认** | `create_lead`、`create_inquiry`、`revise_inquiry` 都没有 `request_key`；`CustomInquiryUpdate.status` 允许 null，router 只对非 null 调 `ensure_status`，null 会落非空列 500 |
| B19-10 | **确认** | `update_inquiry` 审计只有 after；`delete_inquiry` 无 before/after；`release_lead` 无 before/after；`deprecate_lead` 只有 after；批量分配只有数量；删跟进无 before/after |
| B19-11 | **确认** | `lead/service.build_lead_stmt` 的 `like = f"%{keyword.strip()}%"` 未转义；询价/商机同类；与 B16-07/B18-10 同一根因 |
| B19-12 | **确认** | `record_progress_event` 先查 `event_key`，再 `add + flush`；`BusinessEvent.event_key` 有 unique，但没有捕获 `IntegrityError` 转“跳过”，并发重放会 500/回滚业务事务 |
| B20-07 | **确认** | `product/service._number` 是 `float(value)`；SKU 长宽高/重量/体积全转 float；`master._json_value(Decimal)` 转 float；`SkuMasterVersion.values` 存 `confirmed_value`（float） |
| B20-08 | **确认** | `StageCreate/StageUpdate.status`、`LossReasonCreate/Update.status` 都是自由 `str`；`reorder_stages` 不 `lock_stages`；失单原因先查后插不 catch 唯一冲突 |
| B20-09 | **确认** | `create_opportunity`、`create_item`、`copy_opportunity_items(add)`、`clone_opportunity` 都无 `request_key`；双击/重试生成重复商机、明细、克隆 |
| B20-10 | **确认** | `reopen_opportunity` 只清 `loss_reason_id/reopen_at`，不清 `loss_remark`；审计只有 action，无 before/after |
| B20-11 | **确认** | `update_opportunity`、`update_item` 都是普通 `get_visible_*`，无 `for_update`；整批替换有行锁，单条没有，并发最后写入覆盖 |
| B20-12 | **确认** | `replace_items` 审计只写 `count`；`copy_opportunity_items` 只写 added/replaced/skipped/source_total；删除商机/明细虽有 before，但不记被删下游关系/是否被报价引用；失单无 before |
| B20-13 | **确认** | `ingest_external_sku` 内部 `await session.commit()`；`ingest_sku_source` 在 service 返回后才 `write_audit`，审计失败留下“来源已提交、无审计” |
| B20-14 | **确认（建议升 P2）** | `delete_item` 直接 `session.delete(item)`，不查 `QuoteItem.opportunity_item_id`；`QuoteItem.opportunity_item_id` 只是 `BigInteger`，无 ForeignKey；硬删后历史报价溯源悬空 |

## 四条要单独说明的

### B18-09：`customer_id=0` 不是“空值”，是被当成“没传”

```python
if payload.customer_id:
    await customer_service.get_visible_customer(session, user, payload.customer_id)
```

`0` 是 falsy，直接跳过可见性校验；随后 `Customer.id=0` 不存在，FK 冲突变 500，而不是 422。`CaseUpdate.title` 只有 `max_length=200`、没有 `min_length=1`，`submit_case` 又只判 `not case.title`，`"   "` 是 truthy，能提交发布。修的时候三处一起改：`if payload.customer_id is not None`、`Field(ge=1)`、提交前 `strip()` 判空。

### B20-14：硬删商机明细，报价侧没有外键也没有删除前检查

`delete_item` 直接 `session.delete(item)`；`QuoteItem.opportunity_item_id` 只是普通 `BigInteger`。商机明细被物理删除后，历史报价仍保存这个 id，报价详情/刷新/洞察按 id 找商机明细会 404 或静默丢来源。

这条我建议从 P3 升 P2：它不是“审计少一个字段”，是**历史报价的溯源链被物理删除**。修复要么删除前查 `QuoteItem` 引用并 422/改软删，要么给 `opportunity_item_id` 加 FK 并允许 `ON DELETE SET NULL`，至少审计记录被引用的 quote id。

### B16-07 / B18-10 / B19-11：LIKE 通配符是同一个 helper 缺口

三处都是 `f"%{用户输入}%"` 直接进 `ilike`：

- 搜索：`search/router.py`；
- 客户/联系人查重：`contact_util.py`；
- 线索/询价/商机关键词：`lead/service.py`、`inquiry/router.py`、`opportunity/service.py`。

输入 `%`/`_` 会把精确关键词变成宽匹配，结果与字面不符、扫描量放大；数据范围仍在，所以不是越权。修的时候做一个统一 `like_escape()`（转义 `\`、`%`、`_`）并替换所有入口，不要五处各写一遍。

### B20-13：和 B06-18 同一个“service 先 commit、路由后审计”模式

`ingest_external_sku` 自己 `commit`，`ingest_sku_source` 再 `write_audit`。审计失败就留下“来源数据/差异已提交、`sku_master_ingest` 无记录”；重试可能走更新路径，审计仍补不上。修法同 B06-18：service 只 `flush`，路由统一提交“数据 + 审计”；桥接脚本需要提交时用显式 `ingest_and_commit` 包装。

## 本批最大漏项：审计不完整 + 硬删组合，历史溯源不可重建

审查员把 B18-08（案例审计缺 before/after）、B19-10（线索/询价/跟进审计缺 before）、B20-12（商机删除/替换/复制审计缺明细）分开列，但它们和 B20-14 的硬删是同一个后果：

- 案例证据被整体替换，没有 before，无法知道删了哪几张；
- 线索释放/废弃、询价改目标价，没有 before，无法复算改前值；
- 商机整批替换/复制只记 count，删明细又物理删除，报价侧 `opportunity_item_id` 悬空。

单独看每条都是 P3“审计字段不全”；合起来是**关键业务对象的历史无法从本地重建**：案例证据、线索归属、询价目标价、商机明细/报价溯源，一旦被改/删，只能靠外部备份。建议把“改状态/改金额/改归属/删明细”统一成带 before 快照的审计，删除一律先软删或至少保留可回查快照；不要继续用 `after={"count": n}` 这种计数审计。

## 残余

- B18-05 的多修订稿选择未造数据；查询无 `order_by` 是源码事实。
- B18-07 的 `year=0` GET 未实际请求；`_freeze_basis` 自动提交是源码事实。
- B19-12 的并发唯一冲突未做双请求；先查后插无 catch 是源码事实。
- B20-14 的报价引用未造数据；无 FK、删除前不查 `QuoteItem` 是源码事实。
- B16-07/B18-10/B19-11 的 `%/_` 输入未实测；未转义是源码事实。
