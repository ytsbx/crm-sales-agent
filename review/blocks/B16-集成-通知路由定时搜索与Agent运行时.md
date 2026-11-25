# B16 集成·通知路由/定时/搜索 + Agent 运行时（9 文件，4,241 行）

> 审查基线：当前工作区文件内容；只读业务代码，只写 `review/`。
> 本块 9 个文件逐文件读完；未运行测试，未做隔离库并发/断线复现。
> 实际行数：`notification/router.py` 555、`core/scheduler.py` 298、`search/router.py` 351、`agent/model.py` 101、`agent/schema.py` 56、`agent/commentary.py` 193、`agent/insights.py` 882、`agent/router.py` 809、`agent/runtime.py` 996，合计 **4,241**（台账旧值 4,237 / router 805 已按实际更新）。
> 结论：**P0 0、P1 1、P2 5、P3 1、待确认 0**。B15-P1 已在 B16 复核关闭。

## 一、本块发现清单

| 编号 | 严重度 | 位置 | 一句话 | 测试覆盖 |
|---|---|---|---|---|
| B16-01 | P1 | `notification/router.py:245-262` | 单条补投把 `wecom_error` 原样写进审计、并原样回显给管理员/本人；B15-03 的单条外部异常含 `access_token` 时，凭据从通知字段扩散到审计日志 | 未覆盖（`test_notification*` 未断言审计/响应不含 access_token） |
| B16-02 | P2 | `agent/insights.py:40-41、229-232、312-316、795、800-812` + `agent/commentary.py:107-108` | Agent 专用分析的金额统一 `float` 输出，`round(float(...),2)` 用银行家舍入；`quote_draft` 还把逐行 float 相加再 `round`，与核心 `Decimal + ROUND_HALF_UP` 不一致 | 未覆盖（`test_agent_scope_and_receivables.py` 只测工具应收 Decimal，不测 insights/quote_draft 金额） |
| B16-03 | P2 | `agent/insights.py:735-740、759-766、804-812` | `quote-draft` 允许传入与商机不匹配的 `customer_id`，并把该客户的专属价套到商机明细上；`product_recommendation` 有匹配校验，这里没有 | 未覆盖 |
| B16-04 | P2 | `agent/insights.py:463、475、541-542、583-586、623-624` | `followup_suggestion`/`opportunity_analysis` 把 `_visible_open_tasks` 的 `None`（无 `task:view`）“或”成空列表，随后返回 `open_task_count=0`，并可能给出“没有逾期任务/维持常规跟进”的错误结论 | 未覆盖 |
| B16-05 | P2 | `agent/router.py:195-221、388-423、604-622` | 删除会话只删消息和待处理动作，不删 `AgentExecution`；`get_execution` 不判 `deleted_at`、`list_executions` 也不过滤已删会话，工具入参/出参仍可读 | 未覆盖 |
| B16-06 | P2 | `agent/runtime.py:455-493、958-981、645-674` | L1 工具、确认执行、重试都只捕 `AppError`；工具抛非 `AppError`（DB/序列化/代码 bug）时不会落失败执行记录，最终被 `run_turn` 统一翻译成“AI 服务暂不可用” | 未覆盖（`test_agent_ai_safety` 只测 AppError/坏参数） |
| B16-07 | P3 | `search/router.py:146、154、316` | LIKE 关键词未转义 `%`/`_`，用户可发通配符做宽匹配；返回 `total` 是各组 `limit` 截断后的条数之和，不是全量 | 未覆盖 |

## 二、逐文件检查记录

### 1. `backend/app/modules/notification/router.py`（555 行，已读完）

**我检查了什么：**
- 列表/详情/标记已读/忽略的本人过滤、`process_notification_filter` 复用；
- 单条补投 `redispatch_notification` 的权限（本人 vs `settings:manage`/admin）、状态门槛、审计、响应字段；
- 批量补投 `retry-failed` 的 `settings:manage` 权限、`materialize_business_notifications` 先处理事件、再 `requeue_for_redispatch`；
- 通知设置、分级策略、日报的手动触发权限；
- 审计是否把敏感错误原样写入。

**结果如何：**
- **B15-P1 复核关闭**：`redispatch_notification` 本人只能补投 `row.user_id == user.id`，管理员/`settings:manage` 才能补任意人（234-235）；批量 `retry-failed` 整体要求 `settings:manage`（291）。服务层 `requeue_for_redispatch` 不校验 ids 的问题，在现有两个入口下不能跨用户利用；B15-P1 不再作为待确认。
- 发现 **B16-01**：单条补投 `after` 把 `row.wecom_error` 原样写审计（251-255），响应也原样返回/拼提示（260-262）。B15-03 已确认单条外部异常可能含带 `access_token` 的 URL；这条路径把同一份原文复制到审计和响应。
- 通知设置/分级策略/日报入口均要求 `settings:manage`；没有发现新的权限缺口。

### 2. `backend/app/core/scheduler.py`（298 行，已读完）

**我检查了什么：**
- `start_scheduler` 的启用条件、各 job 的 `max_instances/coalesce`、时区；
- 每日扫描 `run_auto_tasks_job` 的 `run_step` 隔离、锁、失败步骤审计；
- 回款逾期刷新、里程碑、公告回收、OA 同步、企微事件、通知重试的分工；
- 定时任务是否会绕过业务权限/审计。

**结果如何：**
- 调度器只调用已有服务；`run_step` 用 `lock_task_scan` 串行化每日扫描，单步失败会写入 `failed_steps`，不阻断其它步骤；通知重试走 `dispatch_pending(include_failed=True)`，OA/企微任务有 `max_instances=1/coalesce`。
- 本文件未发现需要单列的 P0/P1/P2。没有把“整体没问题”当结论：定时任务是否真正只启动一个实例，依赖部署层单 worker；本块未做多进程运行验证。

### 3. `backend/app/modules/search/router.py`（351 行，已读完）

**我检查了什么：**
- 六组搜索的权限码、数据范围、软删除、联系人连带排除已删客户；
- 联系人手机/邮箱完整值判定是否复用 `full_contact_customer_ids`；
- 空白关键词、`limit`、组顺序、`total` 口径；
- LIKE 通配符、已删客户下的商机/报价/订单。

**结果如何：**
- 权限与范围口径清楚：每组先判权限，再 `_scope`，联系人按客户负责人走并连带排除已删客户；完整联系方式按客户批量判定，不在搜索页另写一套。
- 发现 **B16-07**：`keyword` 直接拼 `%...%`，未转义 `%`/`_`（146、154），用户可用通配符扩大匹配；`total` 是六组 `limit` 截断条数之和（316），不是全量，前端若把它当“共 N 条”会误导。
- 未把“已删客户下的商机/报价/订单仍可搜”列为缺陷：订单/报价历史在客户合并/清理后可能仍需保留检索，是否过滤属业务取舍；本块没有结论。

### 4. `backend/app/modules/agent/model.py`（101 行，已读完）

**我检查了什么：**
- `AgentSession/Message/Action/Execution` 的索引、唯一约束、时间戳、JSON 字段；
- 是否有金额/状态枚举约束；
- 会话/执行记录的数据保留边界。

**结果如何：**
- `AgentExecution` 的 `(session_id)`/`(user_id)` 索引与迁移一致；`AgentAction` 有 `(session_id)` 索引；JSON 字段用于入参/出参，没有金额列。
- 未发现需要单列的 P0/P1/P2。`AgentMessage.created_at` 的 `default=lambda: datetime.now()` 是朴素时间，而同表其它时间列是 `timezone=True`；这是时区口径观察，本块没有拿到“已导致错误排序/错误重试”的证据，不升级为结论。

### 5. `backend/app/modules/agent/schema.py`（56 行，已读完）

**我检查了什么：**
- 会话入参的 `title/context_type/context_id` 长度/枚举；
- 消息 4000 上限、拒绝原因、专用分析 id/limit/quantity 边界。

**结果如何：**
- `MessageIn` 有 `min_length=1, max_length=4000`；`PricingAnalysisIn.quantity` 有 `gt=0, max_digits=16, decimal_places=3`；`limit` 1-20。
- `SessionIn.title/context_type` 无长度/枚举，`ActionReject.reason` 无长度；这些字段只进会话显示/审计，未发现可证据化的金额/权限后果，本块不单列。

### 6. `backend/app/modules/agent/commentary.py`（193 行，已读完）

**我检查了什么：**
- 模型未配/超时/报错/空返回的降级路径；
- 事实压缩是否剔除内部键、是否限量、是否脱敏；
- 金额 `Decimal` 是否原样送模型；
- 数据出境提示与 `_close_client`。

**结果如何：**
- 任何模型失败都返回 `(None, 说明)`，不抛异常；`test_agent_ai_safety.py:144-157` 覆盖 provider body 不回显；客户端在 `finally` 关闭。
- 发现 **B16-02** 的一部分：`_compact` 把 `Decimal` 转 `float`（107-108），模型看到的金额是二进制浮点转出来的文本；如果原金额是 0.1/0.005 这类值，叙述可能复述成另一个尾数。
- 数据会出境（客户名/金额/商机标题）已在文件头明确；不把“是否允许出境”替业务决定。

### 7. `backend/app/modules/agent/insights.py`（882 行，已读完）

**我检查了什么：**
- `_f` 的金额转换与舍入口径；
- `receivable_risk` 的订单/应收范围、Decimal 剩余、取消单、集中度、已分配/未分配；
- `customer_summary` 的客户可见性、金额按币种、任务权限；
- `followup_suggestion`/`opportunity_analysis` 的 `task:view`/`opportunity:view` 与 `None` 语义；
- `pricing_analysis` 的客户可见性、成本/底价脱敏顺序；
- `quote_draft` 的客户匹配、逐行算钱、总额汇总、脱敏顺序；
- `product_recommendation` 的客户/商机匹配。

**结果如何：**
- 范围/权限总体是清楚的：应收按订单负责人范围（不是员工 id 误当订单 id）；客户摘要保留 `restricted`；`pricing_analysis` 先 `get_visible_customer` 再核价并先脱敏；`quote_draft` 先取未脱敏价算金额、再脱敏返回；`product_recommendation` 校验客户与商机匹配。
- 发现 **B16-02**：`_f` 对所有金额输出 `round(float(value),2)`，`quote_draft` 的 `amount` 也是 float，`total` 用 float 相加后 `round`；`commentary` 再 `Decimal->float`。
- 发现 **B16-03**：`quote_draft` 的 `resolved_customer = customer_id or opportunity.customer_id`，没有校验 `customer_id` 与 `opportunity.customer_id` 一致；`product_recommendation` 有这段校验，说明不是统一口径。
- 发现 **B16-04**：`followup_suggestion` 与 `opportunity_analysis` 用 `or []` 把“无 task:view 的 None”变成空列表，随后 `open_task_count=0`，并可能输出“没有逾期任务/维持常规跟进”；`customer_summary` 反而保留了 `None`。

### 8. `backend/app/modules/agent/router.py`（809 行，已读完）

**我检查了什么：**
- 会话列表/创建/详情/删除、消息同步/流式、动作列表/详情/取消/确认/拒绝、执行列表/详情/重试的归属；
- 删除会话是否级联清理关联数据；
- 执行记录是否在删除后仍可读；
- 专用分析入口的权限前置；
- SSE 错误信息是否泄露内部细节。

**结果如何：**
- 会话/动作归属检查清楚：动作跟着会话走，越权按 404；确认/拒绝/取消都有动作行锁；`list_executions` 已按会话归属过滤；`model_error_message` 不把供应商正文回浏览器。
- 发现 **B16-05**：`delete_session` 只删 `AgentMessage` 和待处理 `AgentAction`，不删 `AgentExecution`；`get_execution` 只校验 `AgentSession.user_id`，不校验 `deleted_at`；`list_executions` 也不过滤已删会话。删除会话后，工具入参/出参仍能按 id 读取。

### 9. `backend/app/modules/agent/runtime.py`（996 行，已读完）

**我检查了什么：**
- 系统提示词/上下文渲染、模型客户端关闭；
- 流式工具调用累积、参数校验、权限闸门、L1 执行、L2/L3 落动作；
- 每轮写前重校验会话、断线部分回复落库；
- 确认/拒绝/取消的行锁、确认后执行、失败记录、审计；
- 重试只允许 L1、原入参、权限再校验；
- 异常分类与错误信息脱敏。

**结果如何：**
- 并发确认/拒绝有动作行锁；L2/L3 只落 pending，确认时再判权限；重试只允许 L1，避免写操作重复；`_record_execution` 存角色/数据范围快照；断线部分回复有 `GeneratorExit` 兜底并关闭客户端。
- 发现 **B16-06**：L1 执行、`execute_action`、`retry_tool_call` 都只 `except AppError`；非 `AppError`（DB 约束、序列化、代码 bug）不会写失败执行记录，最终在 `run_turn` 被统一翻译成“AI 服务暂时不可用”，把工具故障误报成模型故障。

## 三、发现详情

### B16-01 单条补投把 `wecom_error` 写审计并原样回显

- **严重度：P1**
- **文件:行号：**
  - `backend/app/modules/notification/router.py:245-255`（`after={"wecom_error": row.wecom_error}`）
  - `backend/app/modules/notification/router.py:260-262`（失败提示拼 `row.wecom_error`）
  - 同源：`backend/app/modules/notification/service.py:592-597、731-739`（单条发送失败把异常原文写 `wecom_error`）
  - 上游：`backend/app/modules/wecom/client.py:98-104`（`HTTPStatusError` 字符串可带含 `access_token` 的 URL）
- **触发条件：** 一条通知的企微发送因 401/403/5xx 失败，`row.wecom_error` 已含异常原文（B15-03）；本人或 `settings:manage`/admin 调 `POST /notifications/{id}/redispatch`。
- **后果：** 补投审计 `audit_logs.after_data` 里出现 `wecom_error`，响应也原样回显；`audit:view`/管理员可读审计，凭据从 `Notification.wecom_error` 扩散到审计日志。即使 B15-03 只在短时间窗口，审计是长期留存。
- **证据代码：**

```python
await write_audit(
    session, operator_id=user.id,
    action="redispatch_notification",
    business_type="notification",
    business_id=row.id,
    after={
        "wecom_status": row.wecom_status,
        "wecom_attempts": row.wecom_attempts,
        "wecom_error": row.wecom_error,   # ← 原样入审计
    },
    ip=client_ip(request),
)
...
message = f"仍未能投出：{row.wecom_error or '未知原因'}"
```

- **建议：**
  - 落审计前统一走 `redact_external_error()`（去 `access_token`/query string/URL），只留 `type + status_code + errcode/errmsg`；
  - 响应里的 `wecom_error` 用脱敏后的短原因；原始详情只进受控诊断日志，且日志本身也要脱敏；
  - 与 B15-03 一起修，避免“通知字段修了、审计又漏”。
- **测试覆盖：** 未覆盖。现有通知测试没有构造“`wecom_error` 含 access_token”的单条补投用例；应在隔离库断言审计/响应都不含 token。

### B16-02 Agent 专用分析金额 float + 银行家舍入，`quote_draft` 用 float 汇总

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/agent/insights.py:40-41`（`_f = round(float(value), 2)`）
  - `backend/app/modules/agent/insights.py:229-232、279-283、312-316`（应收/集中度/汇总金额）
  - `backend/app/modules/agent/insights.py:613`（商机预计金额）
  - `backend/app/modules/agent/insights.py:795、800-812`（报价草稿逐行 `amount` 是 float，`total` 用 float 相加再 `round`）
  - `backend/app/modules/agent/commentary.py:107-108`（`Decimal -> float` 送模型）
- **触发条件：** 调 `/agent/risk-analysis`、`/agent/opportunity-analysis`、`/agent/quote-draft` 等带金额的分析；`quote_draft` 有多行明细。
- **后果：**
  - `round(float(value), 2)` 使用银行家舍入（.005 可能向偶数舍），与项目 `core/money.py` 的 `ROUND_HALF_UP` 不一致；
  - `quote_draft` 逐行 `amount` 已经是 float，`sum` 再做二进制浮点加法，最后 `round(total, 2)`；多行 0.1/0.2 类金额可能差 1 分，且没有 `amount_exact` 可以复算；
  - `commentary` 又把 Decimal 转 float 送模型，模型复述金额时可能引用错误尾数。
- **证据代码：**

```python
def _f(value) -> float | None:
    return None if value is None else round(float(value), 2)

# quote_draft
"amount": _f((quantity * unit_price).quantize(Decimal("0.01"))),
...
total = sum((line["amount"] or 0) for line in lines)
"suggested_total": round(total, 2),
```

- **建议：**
  - 内部继续用 `Decimal`，逐行 `quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`，总额用 `sum(..., Decimal(0))`，只在最终 JSON 输出时转字符串/数字；
  - 返回 `amount_exact`/`suggested_total_exact`（字符串）和展示值，前端不要拿 float 做二次汇总；
  - `commentary` 送模型的事实金额用固定两位字符串，不转 float；模型提示词里明确“金额为字符串，原样引用”。
- **测试覆盖：** 未覆盖。`test_agent_scope_and_receivables.py` 测的是 `get_receivables_summary` 的 Decimal，不覆盖 `insights._f`/`quote_draft` 的 float 汇总；应补 0.1+0.2、0.005 舍入、多行报价草稿总额用例。

### B16-03 `quote-draft` 未校验 `customer_id` 与商机客户一致

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/agent/insights.py:735-740`（`resolved_customer = customer_id or opportunity.customer_id`，只校验该客户可见）
  - `backend/app/modules/agent/insights.py:759-766`（`calculate_price(... customer_id=resolved_customer)`）
  - `backend/app/modules/agent/insights.py:804-812`（返回 `customer_id=resolved_customer`，items 却来自原商机）
  - 对照：`backend/app/modules/agent/insights.py:852-860` 的 `product_recommendation` 明确校验 `opportunity.customer_id != customer_id` 就报错
- **触发条件：** 用户对商机 A（客户甲）调 `POST /agent/quote-draft`，同时传 `customer_id=乙`；甲乙都在用户可见范围内。
- **后果：** 报价草稿用乙的客户专属价/价格权限计算甲商机的明细，返回的 `customer_id` 是乙、`opportunity_item_id` 却是甲，形成“甲的需求、乙的价格”的错配；如果甲/乙专属价不同，用户会看到错误建议价/总额，甚至把乙的协议价带进甲的草稿。因为用户对乙有 `customer:view`，这不是越权读取，但金额与业务对象错配。
- **证据代码：**

```python
opportunity = await opp_service.get_visible_opportunity(session, user, opportunity_id)
resolved_customer = customer_id or opportunity.customer_id
if resolved_customer:
    await customer_service.get_visible_customer(session, user, resolved_customer)
...
result = await pricing_service.calculate_price(
    ..., customer_id=resolved_customer, ...
)
```

- **建议：** 显式校验 `customer_id is None or customer_id == opportunity.customer_id`，不匹配直接 422；或干脆不允许覆盖商机客户，只用 `opportunity.customer_id`。补“客户与商机不匹配”用例。
- **测试覆盖：** 未覆盖。

### B16-04 无 `task:view` 被压成 0，跟进/商机分析给出“没有逾期任务”的错误结论

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/agent/insights.py:463`（客户分支 `_visible_open_tasks(...) or []`）
  - `backend/app/modules/agent/insights.py:475`（线索分支同样）
  - `backend/app/modules/agent/insights.py:541-542`（`open_task_count=len(open_tasks)`、`overdue_task_count=len(overdue_tasks)`）
  - `backend/app/modules/agent/insights.py:583-586`（商机分析同样 `or []`）
  - `backend/app/modules/agent/insights.py:623-624`（商机分析计数）
  - 对照：`backend/app/modules/agent/insights.py:413-427` 客户摘要保留 `None` 并输出 `open_tasks=None`
- **触发条件：** 用户有 `customer:view`/`lead:view`/`opportunity:view`，但没有 `task:view`；调 `/agent/followup-suggestion` 或 `/agent/opportunity-analysis`。
- **后果：** `_visible_open_tasks` 返回 `None` 表示“你没权限看”，被 `or []` 变成空列表；接口返回 `open_task_count=0`，并在没有其它建议时输出“维持常规跟进，没有逾期任务”。这是把“看不到”说成“没有”，会让业务员误判客户没有逾期任务；与客户摘要保留 `None` 的口径自相矛盾。
- **证据代码：**

```python
open_tasks = await _visible_open_tasks(session, user, customer_id=customer_id) or []
...
"open_task_count": len(open_tasks),
"overdue_task_count": len(overdue_tasks),
```

- **建议：** 保留 `None` 语义：有权限才给 `open_task_count/overdue_task_count`，没权限给 `None`（或 `restricted` 标记），并在 suggestions 里不生成“没有逾期任务”的结论；与 `customer_summary` 统一。补“无 task:view”用例。
- **测试覆盖：** 未覆盖。`test_agent_ai_safety.py:159-172` 只测分析入口缺模块权限直接 403；`test_agent_scope_and_receivables` 测工具/摘要，不测 followup/opportunity 的 `None` 折叠。

### B16-05 删除会话不清理执行记录，删除后仍可按 id 读工具入参/出参

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/agent/router.py:203-207`（只删除 `AgentMessage` 和 pending/expired `AgentAction`）
  - `backend/app/modules/agent/router.py:399-404`（`get_execution` 只校验 `AgentSession.user_id`，不校验 `deleted_at`）
  - `backend/app/modules/agent/router.py:616-620`（`list_executions` join 会话但不滤 `deleted_at`）
- **触发条件：** 用户删除自己的 Agent 会话后，仍持有或猜到 `AgentExecution.id`；调 `GET /agent/executions/{id}` 或列表。
- **后果：** 会话列表/消息已删，但 `AgentExecution.input_payload/output_payload`（可能含客户 id、金额、工具返回）仍保留且可读；`list_executions` 还会列出已删会话的执行记录。用户以为“删除会话=清掉内容”，实际工具运行数据仍可访问。不是跨用户越权，但属于删除语义不完整/隐私留存。
- **证据代码：**

```python
await session.execute(delete(AgentMessage).where(...))
await session.execute(delete(AgentAction).where(...))
# 没有 delete(AgentExecution)
...
agent_session = await session.get(AgentSession, row.session_id)
if agent_session is None or agent_session.user_id != user.id: ...
```

- **建议：** 明确删除语义：若“删除会话”应清内容，则同步删除/脱敏该会话的 `AgentExecution`（或至少清空 `input_payload/output_payload`，保留审计计数）；若保留执行记录用于审计，则 `get_execution/list_executions` 应校验 `deleted_at` 或返回“会话已删除，仅管理员可查”，并在前端说明。补删除后执行记录不可读/列表不出现的用例。
- **测试覆盖：** 未覆盖。

### B16-06 工具非 `AppError` 异常被误报为“AI 服务不可用”，且不落失败执行记录

- **严重度：P2**
- **文件:行号：**
  - `backend/app/modules/agent/runtime.py:455-493`（L1 只 `except AppError`）
  - `backend/app/modules/agent/runtime.py:958-981`（`retry_tool_call` 只 `except AppError`）
  - `backend/app/modules/agent/runtime.py:863-880`（`execute_action` 只 `except AppError`）
  - `backend/app/modules/agent/runtime.py:667-674`（`run_turn` 把所有非 AppError 统一转 `model_error_message`）
- **触发条件：** 任一工具 handler 抛出非 `AppError`：数据库约束/连接错误、`json.dumps` 的 `TypeError`、工具代码 bug、`Decimal` 序列化失败等。
- **后果：** 不会写 `AgentExecution(status='failed')`，也没有工具级失败原因；`run_turn` 把它当成模型侧失败，返回“AI 服务暂时不可用，请稍后重试”。用户/运维会误判为 DeepSeek 故障，实际是工具/DB 故障；排查时没有执行记录。流式端点同样只发 `error` 事件并走 `model_error_message`。
- **证据代码：**

```python
try:
    result = await spec.handler(ctx, **args)
    ...
except AppError as exc:
    result = {"error": exc.message}
    ...
```

- **建议：** 在 L1/确认/重试统一加 `except Exception` 兜底：用脱敏后的 `type(error).__name__` 落 `AgentExecution(status='failed')`，再把受控错误返回模型/用户；不要把工具异常交给 `model_error_message`。日志只记类型/工具名，不记原始参数/凭据。
- **测试覆盖：** 未覆盖。`test_agent_ai_safety` 覆盖坏参数（AppError）和 provider body，不覆盖工具非 AppError。

### B16-07 搜索 LIKE 未转义通配符，`total` 只是截断条数之和

- **严重度：P3**
- **文件:行号：**
  - `backend/app/modules/search/router.py:146`（`like = f"%{keyword_key}%"`）
  - `backend/app/modules/search/router.py:154、190-192、230-232`（各组 `ilike(like)`）
  - `backend/app/modules/search/router.py:316`（`total = sum(len(items)...)`）
- **触发条件：** 用户在搜索框输入 `%`、`_` 或包含它们的字符串；或某组结果超过 `limit`。
- **后果：** `%`/`_` 被 SQL LIKE 当通配符，用户可绕开“精确关键词”做宽匹配（仍有权限/范围，不是越权），返回结果与输入字面不符；`total` 对每组只统计 `limit` 内条数（最多 5*6=30），不是全量，前端若显示“共 N 条”会低估。
- **证据代码：**

```python
like = f"%{keyword_key}%"
...
Customer.name.ilike(like)
...
"total": sum(len(items) for items in items_by_group.values()),
```

- **建议：** 对用户关键词转义 `%`/`_`/`\\` 后再拼 LIKE，或改用 `contains` 并显式 escape；`total` 改名为 `returned_total`，或对每组额外 `count`（需评估六组查询成本）。
- **测试覆盖：** 未覆盖。

## 四、补充维度检查

| 维度 | 本块结果 |
|---|---|
| 负数/0/超大值/小数位 | `PricingAnalysisIn.quantity` 有 `gt=0, max_digits=16, decimal_places=3`；`MessageIn` 1-4000；`limit` 1-20/1-100。`QuoteDraftIn`/`FollowupSuggestionIn` 的 id 无 `ge=1`，但业务层 `get_visible_*` 会 404；未形成金额/越权结论。`quote_draft` 的 `quantity` 来自商机明细，`or 1` 与 `or 0` 混用，0 数量行金额为 0；这是边界观察，未单列。 |
| 事务边界 | `delete_session` 只删消息/待处理动作，留下执行记录，见 B16-05；`run_turn_events` 先提交用户消息，工具轮次每轮提交，断线有 part 保存；未发现“订单建了明细没建”这类半成品，但会话删除语义不完整。 |
| 异常/敏感信息 | `model_error_message` 不返回供应商正文；`test_agent_ai_safety` 覆盖。但工具非 AppError 被误报 AI 故障（B16-06），通知补投把 `wecom_error` 入审计/回显（B16-01）。 |
| 审计 | 会话创建/删除、动作取消/拒绝/确认都有审计；确认审计含 `payload`，执行记录含角色/范围快照。B16-01 是把外部错误原文复制进审计，不是“没审计”。 |
| 测试 | 未运行。`test_agent_ai_safety.py` 覆盖坏参数、权限闸门、确认卡范围、断线保存、provider body；`test_agent_scope_and_receivables.py` 覆盖工具/摘要的权限与 Decimal；`test_batch12_allocation_chain.py` 覆盖 `insights.receivable_risk` 基本逾期。**B16-01..07 均无对应触发条件用例**。 |
| 未看范围 | 见下节。 |

## 五、本块没有看的部分及原因

- **`backend/app/modules/agent/tools.py`（工具声明与 handler）**：不在 B16 9 文件清单内，属于 B17；本块只审 runtime 对工具的网关/异常/记录，未逐工具验证金额返回、权限声明与 handler 副作用。B16-06 的“工具非 AppError”是 runtime 异常路径结论，不代表已找到具体工具 bug。
- **`backend/app/modules/notification/service.py` 的投递实现**：B15 已审；B16 只从 router 侧看补投审计/回显，未重复审 `dispatch_pending`/`send_digest` 内部。
- **`backend/app/core/scheduler.py` 的 APScheduler 多进程行为**：只读配置与 job 注册；未启动多 worker/多实例验证 `max_instances`、`coalesce` 在部署层是否真的只跑一份。
- **真实模型/DeepSeek 流式行为**：`test_agent_ai_safety` 用 fake client/SimpleNamespace；未实网验证工具调用分片累积、finish_reason=length、超时/断线。
- **前端 `frontend/src`**：未审；搜索 `total` 展示、Agent 删除会话后执行记录是否还展示、补投错误提示是否原样显示 `wecom_error`，需 F 系列前端块复核。
- **`backend/tests` 全量**：只定向读 `test_agent_ai_safety.py`、`test_agent_scope_and_receivables.py`、`test_batch12_allocation_chain.py`、`test_search_scope.py` 相关段；未运行测试，覆盖结论是“是否覆盖触发条件”，不是“全绿”。
- **隔离库并发/断线**：未执行。B16-01/05/06 为代码路径结论；B16-04 为权限语义结论；未做运行复现。

## 六、结论

B16 最需要先处理的是 **B16-01 P1**：补投审计把 `wecom_error` 原样落库，和 B15-03 的 `access_token` 泄露同源，但审计是长期留存。**B16-02/B16-03/B16-04** 是 Agent 分析的金额口径与业务对象匹配问题，**B16-05/B16-06** 是会话删除与异常分类问题，**B16-07** 是搜索体验/口径问题。

B15-P1 已在 B16 复核关闭：现有单条补投按 `user_id` 或 `settings:manage` 限制，批量补投要求 `settings:manage`，服务层不校验 ids 不能跨用户利用。台账已更新 B16 状态、实际行数、发现与 B15-P1 结论；下一块 B17 待用户说“继续”。
