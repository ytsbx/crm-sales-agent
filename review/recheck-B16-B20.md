# B16–B20 高严重度复核（证伪）

- 范围：B16/B17 的 P1；B18/B19/B20 报告自身 0 P1，本批只确认其“无 P1”结论，P2 未逐条复核。
- 方法：重读通知补投审计/响应、Agent 客户全貌的待回款分支；其余块按报告结论核对目录与严重度分布。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b16_b20_highlights.py -q`
- 结果：`3 failed`，均为“修复后应有”的结构断言失败。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B16-01 P1 | **确认** | `redispatch_notification` 把 `row.wecom_error` 原样写进 `audit_logs.after_data`，失败提示和响应也原样回显；上游 B15-03 的 `HTTPStatusError` 字符串可带 `access_token`，凭据从短期通知字段扩散到长期审计 |
| B17-01 P1 | **确认** | `get_customer_overview` 的 `payment:view` 分支只遍历 `orders` 算 `order_total`；无 `order:view` 时 `orders=[]`，`pending = 0 − received` 直接为负，模型会读成“客户超付” |
| B18 | **0 P1 成立** | 报告列 4 条 P2 + 6 条 P3 + 2 待确认；本块无 P1 |
| B19 | **0 P1 成立** | 报告列 5 条 P2 + 8 条 P3 + 2 待确认；本块无 P1 |
| B20 | **0 P1 成立** | 报告列 6 条 P2 + 8 条 P3；本块无 P1 |

## 两条需要单独说明

### B16-01：审计是长期留存，比通知字段更严重

`redispatch_notification` 的 `after` 里直接放 `wecom_error`，响应 JSON 和失败 `message` 也直接拼同一个字段。B15-03 已确认单条企微失败会把 `str(HTTPStatusError)` 落进 `Notification.wecom_error`，而该字符串带请求 URL、URL 的 query 上有 `access_token`。

于是同一条凭据出现在三个地方：通知行（短期）、补投审计（长期）、补投响应（当场回给操作人）。修 B15-03 只修通知字段不够，`write_audit` 前必须统一脱敏；否则“通知修好了，审计还留着”。

### B17-01：不是金额精度，是权限缺失被当成“0”

代码注释写“①没有 payment:view 就不算”，但只处理了 payment 权限，没处理 order 权限。`payment:view` 是合法独立权限（财务/收款角色通常没有订单查看权），所以这不是异常输入：

- `orders = []`（因为无 `order:view`）
- `order_total = {}`
- `received_amount` 仍能从 `_receivables_by_currency` 取到
- `pending` 里每个币种都变成 `0 - received`，负数

同时 `unavailable_blocks` 只写“订单（需要 order:view）”，模型仍拿到一个负数金额，容易解释成“客户已超付/待回款为负”。正确做法是缺 `order:view` 时不要输出 `pending_receivable_amount`，或改用应收计划的 `plan_amount` 口径，不依赖整单金额。

## 本批最大漏项（B16-04 + B17-01 的系统性问题）

**Agent 读工具把“没有权限”退化成“空列表/0”，模型无法区分“业务上没有”与“我看不到”，于是会给出确定性的错误结论。**

审查员分别抓到了两个实例：

- B16-04：无 `task:view` 时 `_visible_open_tasks` 返回 `None`，被 `or []` 压成空，输出 `open_task_count=0`，建议“没有逾期任务/维持常规跟进”；
- B17-01：无 `order:view` 时 `orders=[]`，待回款算成负数。

但这不是两条孤立缺陷，而是同一套工具契约缺了“三态”：`available` / `restricted` / `empty`。只要模型拿到 `0` 或 `[]`，它就会当成事实。B16-04 的 `customer_summary` 已经有 `unavailable_blocks` 这种正确形态，其他工具却没有统一采用。修的时候应把“权限缺失”统一成 `None + restricted` 标记，或在系统提示里明确“`unavailable_blocks` 非空时不得对被隐藏字段下结论”，不能逐个工具打补丁。

## 残余/未覆盖

- B18/B19/B20 只核对了“0 P1”的结论与发现分布，P2/P3 未逐条复核。
- B16-01/B17-01 的复现是源码结构断言；真实数据库/权限组合用例未搭（需要隔离库和 `CurrentUser` 权限集）。
- B17 另有 2 条待确认（`receivable_stats` 预收/超收负数、`refreeze_sales_actuals` 空重算删存档），本批未定级。
