# 后端 P2 复核（第九批：B15–B19 剩余条目）

> 日期 2026-11-24，只读当前代码。只审查，未改业务代码。

## 一、结果

本批 12 条：

- **10 条已修**：B15-05、B15-06、B16-03、B16-04、B16-06、B17-05、B18-04、B19-01、B19-02、B19-05
- **2 条仍成立**：B16-05、B18-01
- **0 条部分修**

不重复统计：B15-04、B16-02、B17-02、B17-03、B17-06、B17-07、B18-02、B18-03、B19-03、B19-04 已在前批；B17-04 部分修。

## 二、已修

| 编号 | 当前代码 | 说明 |
|---|---|---|
| B15-05 | `wecom/service.py:880-886` 预览同时警告离职人与接管人未绑企微；`:1221-1228` 逐项接手人未绑时 `wecom_relation` 标 `skipped` 并写原因；`:2066-2072` 外部转接前再判 `owner.wecom_userid`，为空不发请求 | 不再用空 `takeover_userid` 发外部转接、留 partial |
| B15-06 | `integration/model.py:96-114` `IntegrationLog.__table_args__` 已含 `uq_integration_log_event_key` 部分唯一索引（erp/inbound/success/event_key 非空），PostgreSQL 与 SQLite 两套条件都有；`:130` 有 `event_key` 列 | 模型建库/新环境也兜底，不再只靠迁移 |
| B16-03 | `agent/insights.py:739-745` `resolved_customer = opportunity.customer_id`，整段不再读传入 `customer_id`；`:746-748` 还校验当前用户能看该客户 | 不会再把别的客户专属价套到本商机 |
| B16-04 | `agent/insights.py:129-153` `_visible_open_tasks` 无 `task:view` 返回 `None`；`:545-546`、`:627-628` 计数输出 `None` 而不是 0；`:532` 无权限时只写“近期跟进正常”，不再断言“没有逾期任务” | “看不到”不再被说成“一条都没有” |
| B16-06 | `agent/runtime.py:480-500` L1 工具有 `except AppError` + `except Exception`，非 AppError 也落失败执行记录并返回安全提示；`:880-905` 确认执行、`:1002-1025` 重试同样双分支 | 工具非 AppError 不再冒到 `run_turn` 被误报“AI 服务不可用” |
| B17-05 | `analytics/service.py:1154-1160` `_product_bucket` 无 SKU 时用 `n:{名称}|{规格}` 作分组键；`:1260-1290` 聚合统一用该键 | 同名不同规格不再合并，规格不丢 |
| B18-04 | `cases/service.py:1030-1038` 修订稿 `author_id=user.id`，注释明确“主管代开归主管，不再复制原作者” | 主管开稿后能改/提交/删除，不再 404 |
| B19-01 | `inquiry/model.py:94-95` 已有 `oa_quote_user_id/name` 列；`inquiry/router.py:435-436` 修订复制这两个字段；`inquiry/router.py:136` 创建仍存 `extra.oa_draft`，`oa/inquiry.py:81-86` `saved_quote_owner` 先读 extra 再读列。修订同时 `extra=old.extra`（`:450` 附近） | 报价员在创建、重开 OA、修订三条路径都能取到，不再丢 |
| B19-02 | `followup/router.py:357-359` 删除跟进后除回算 `last_followup_at`，还调 `customer_service.refresh_next_followup_at`；新增/修改路径 `:149`、`:292` 也调 | 下次跟进提醒不再指向已删计划 |
| B19-05 | `inquiry/service.py:239` `linked_quotes_for(..., viewer_id=viewer_id)`；`:365-372` 有 `draft_scope(Quote, viewer_id)` | 别人的私密报价草稿不再算作已关联、不再回显其 `quote_no` |

## 三、仍成立（逐条证据）

| 编号 | 当前代码事实 | 结论 |
|---|---|---|
| B16-05 | `agent/router.py:195-211` 删除会话只删 `AgentMessage` 与部分 `AgentAction`、软删会话，**不删** `AgentExecution`；`:399-405` `get_execution` 只校验 `agent_session.user_id == user.id`，不判 `deleted_at`；`:616-621` `list_executions` 只按 `AgentSession.user_id` 过滤，不判软删 | 已删会话的工具入参/出参仍可读 |
| B18-01 | `analytics/router.py:122-146` `/sales-targets`、`/sales-targets/bases` 直接 `ok(await ...)`，不调 `_with_fx_note`；`target_bases.py:373-475`、`targets.py:257/364/400` 对 `SalesOrder.total_amount`、`PaymentRecord.received_amount` 直接 `sum`，无币种过滤/折算 | 目标与年度口径仍跨币种直接相加，数字没有币种含义 |

## 四、结论

- 本批 10 条是实打实的代码修复：企微交接空账号、集成日志唯一索引、Agent 客户一致性/任务权限/异常兜底、定制项规格分组、主管修订稿归属、询价报价员、删除跟进提醒、私密报价关联。
- 仍成立只剩两条：
  1. B16-05 删除会话不清执行记录、查询不滤软删；
  2. B18-01 目标类接口跨币种直接求和且不挂币种提醒。
- 下一批：B22–B23 剩余 P2，然后进入 P3 抽样。
