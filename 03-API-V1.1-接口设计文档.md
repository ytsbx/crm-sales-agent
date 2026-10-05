# 03-API：报价驱动型销售 CRM + Sales Agent V1.1
## 接口设计文档（最终版）

> 目标：作为后端开发接口基线。  
> 路径统一使用 `/api/v1`。  
> 所有写接口必须做权限校验；关键写接口必须写审计日志。

---

# 1. 通用规范

## 1.1 返回结构

```json
{
  "code": 0,
  "message": "ok",
  "data": {}
}
```

分页：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "items": [],
    "page": 1,
    "page_size": 20,
    "total": 100
  }
}
```

## 1.2 通用要求

- JWT / SSO
- 统一错误码
- 幂等键：`Idempotency-Key`
- 乐观锁：`version`
- 高风险操作必须审计
- 外部集成请求需 trace_id
- Agent 写操作必须携带 actor / risk_level

---

# 2. Auth

- `POST /auth/login`
- `POST /auth/logout`
- `POST /auth/refresh`
- `GET /auth/me`
- `GET /auth/permissions`
- `POST /auth/sso/wecom/callback`

---

# 3. User

- `GET /users`
- `POST /users`
- `GET /users/{id}`
- `PATCH /users/{id}`
- `POST /users/{id}/enable`
- `POST /users/{id}/disable`
- `GET /users/{id}/roles`
- `PUT /users/{id}/roles`
- `GET /users/{id}/data-scope`

---

# 4. Department

- `GET /departments/tree`
- `GET /departments`
- `POST /departments`
- `GET /departments/{id}`
- `PATCH /departments/{id}`
- `DELETE /departments/{id}`
- `GET /departments/{id}/users`

---

# 5. Role / Permission

- `GET /roles`
- `POST /roles`
- `GET /roles/{id}`
- `PATCH /roles/{id}`
- `DELETE /roles/{id}`
- `GET /permissions`
- `GET /roles/{id}/permissions`
- `PUT /roles/{id}/permissions`
- `GET /roles/{id}/data-scope`
- `PUT /roles/{id}/data-scope`

---

# 6. Lead

- `GET /leads`
- `POST /leads`
- `GET /leads/{id}`
- `PATCH /leads/{id}`
- `DELETE /leads/{id}`
- `POST /leads/{id}/assign`
- `POST /leads/{id}/claim`
- `POST /leads/{id}/release`
- `POST /leads/{id}/discard`
- `POST /leads/{id}/restore`
- `POST /leads/{id}/deduplicate`
- `POST /leads/{id}/convert`
- `GET /leads/{id}/timeline`
- `POST /leads/import`
- `POST /leads/export`
- `POST /leads/batch-assign`

### 线索转化

`POST /leads/{id}/convert`

```json
{
  "customer_mode": "existing",
  "customer_id": 1001,
  "create_contact": true,
  "create_opportunity": true,
  "opportunity_title": "德国浴桶采购"
}
```

---

# 7. Customer

- `GET /customers`
- `POST /customers`
- `GET /customers/{id}`
- `PATCH /customers/{id}`
- `DELETE /customers/{id}`
- `GET /customers/{id}/overview`
- `GET /customers/{id}/contacts`
- `GET /customers/{id}/opportunities`
- `GET /customers/{id}/quotes`
- `GET /customers/{id}/orders`
- `GET /customers/{id}/followups`
- `GET /customers/{id}/tasks`
- `GET /customers/{id}/timeline`
- `GET /customers/{id}/files`
- `POST /customers/{id}/assign`
- `POST /customers/{id}/transfer`
- `POST /customers/{id}/release-to-pool`
- `POST /customers/{id}/claim`
- `POST /customers/{id}/tags`
- `DELETE /customers/{id}/tags/{tag_id}`
- `POST /customers/deduplicate`
- `POST /customers/{id}/duplicate-cases`
- `GET /customer-duplicate-cases`
- `POST /customer-duplicate-cases/{id}/resolve`
- `POST /customers/merge`
- `POST /customers/batch-transfer`
- `POST /customers/batch-tag`
- `POST /customers/import`
- `POST /customers/export`

客户批量导出用途必填，可选值：`customer_follow_up`（客户跟进）、`business_analysis`（经营分析）、
`management_report`（管理汇报）、`data_reconciliation`（数据核对）、
`historical_migration`（历史数据迁移）、`other`（其他）。选择 `other` 时，
`purpose_note` 必填，最多 200 字。POST 将用途及筛选条件放在 JSON body；
GET `/customers/export` 将 `purpose` 与 `purpose_note` 放在 query。两种入口均执行用户数据范围过滤，
并在审计中记录操作者/时间、用途、数据范围、筛选条件、结果数量及最多 200 个客户 ID。

客户详情的“撞单检查”调用 `POST /customers/{id}/duplicate-cases`，需要 `customer:assign`，
且发起客户必须在当前用户的数据范围内。它沿用现有查重规则，命中后创建待裁定记录；
同一对客户已有待裁定记录时直接复用。返回的 `opened` 是本次匹配的待裁定记录数量，
包含复用的记录；检查不会改变客户归属，人工发起操作写入审计。
裁定列表和裁定操作要求案件两边的客户均在用户的数据范围内。选择 `assign_existing`
时无需另传 `owner_id`，系统沿用候选客户的在职负责人；`assign_new` 表示人工指定在职负责人。
归属裁定成功后客户转入私海，归属变更和裁定均留痕。

---

# 8. Contact

- `GET /contacts`
- `POST /contacts`
- `GET /contacts/{id}`
- `PATCH /contacts/{id}`
- `DELETE /contacts/{id}`
- `POST /contacts/{id}/bind-customer`
- `POST /contacts/{id}/change-customer`
- `POST /contacts/{id}/set-primary`
- `GET /contacts/{id}/timeline`
- `GET /contacts/{id}/wecom`
- `POST /contacts/deduplicate`

---

# 9. Public Pool

- `GET /public-pool/customers`
- `GET /public-pool/leads`
- `POST /public-pool/customers/{id}/claim`
- `POST /public-pool/leads/{id}/claim`
- `POST /public-pool/customers/{id}/assign`
- `POST /public-pool/leads/{id}/assign`
- `GET /public-pool/rules`
- `POST /public-pool/rules`
- `PATCH /public-pool/rules/{id}`

---

# 10. WeCom Integration

- `POST /integrations/wecom/sync-departments`
- `POST /integrations/wecom/sync-users`
- `POST /integrations/wecom/sync-external-contacts`
- `POST /integrations/wecom/sync-follow-relations`
- `GET /integrations/wecom/unbound-contacts`
- `GET /integrations/wecom/unbound-contacts/{id}/candidates`
- `POST /integrations/wecom/unbound-contacts/{id}/bind-customer`
- `POST /integrations/wecom/unbound-contacts/{id}/create-customer`
- `POST /integrations/wecom/transfer`
- `GET /integrations/wecom/transfer/{job_id}`
- `GET /integrations/wecom/sync-jobs`
- `GET /integrations/wecom/sync-jobs/{id}`
- `POST /webhooks/wecom/events`

---

# 11. Opportunity

- `GET /opportunities`
- `POST /opportunities`
- `GET /opportunities/{id}`
- `PATCH /opportunities/{id}`
- `DELETE /opportunities/{id}`
- `GET /opportunities/{id}/overview`
- `GET /opportunities/{id}/timeline`
- `GET /opportunities/{id}/stage-history`
- `POST /opportunities/{id}/change-stage`
- `POST /opportunities/{id}/assign`
- `POST /opportunities/{id}/win`
- `POST /opportunities/{id}/lose`
- `POST /opportunities/{id}/reopen`
- `POST /opportunities/{id}/clone`
- `GET /opportunities/funnel`

---

# 12. OpportunityItem

- `GET /opportunities/{id}/items`
- `POST /opportunities/{id}/items`
- `POST /opportunities/{id}/items/batch`
- `PATCH /opportunity-items/{id}`
- `DELETE /opportunity-items/{id}`
- `POST /opportunities/{id}/items/copy-from/{source_opportunity_id}`
- `POST /opportunities/{id}/recommend-products`

---

# 13. Opportunity Stage / Loss Reason

- `GET /opportunity-stages`
- `POST /opportunity-stages`
- `PATCH /opportunity-stages/{id}`
- `DELETE /opportunity-stages/{id}`
- `POST /opportunity-stages/reorder`
- `GET /loss-reasons`
- `POST /loss-reasons`
- `PATCH /loss-reasons/{id}`

---

# 14. Product

- `GET /products`
- `POST /products`
- `GET /products/{id}`
- `PATCH /products/{id}`
- `DELETE /products/{id}`
- `GET /products/{id}/skus`
- `GET /products/{id}/files`
- `POST /products/{id}/files`
- `GET /products/{id}/knowledge`
- `POST /products/import`
- `POST /products/export`

---

# 15. SKU

- `GET /skus`
- `POST /skus`
- `GET /skus/{id}`
- `PATCH /skus/{id}`
- `DELETE /skus/{id}`
- `POST /skus/{id}/enable`
- `POST /skus/{id}/disable`
- `GET /skus/{id}/costs`
- `GET /skus/{id}/price-summary`
- `POST /skus/import`
- `POST /skus/export`

---

# 16. Cost

- `GET /skus/{sku_id}/costs`
- `POST /skus/{sku_id}/costs`
- `GET /skus/{sku_id}/cost-history`
- `GET /costs/{id}`
- `PATCH /costs/{id}`
- `POST /costs/{id}/expire`

---

# 17. Price Rule

- `GET /price-rules`
- `POST /price-rules`
- `GET /price-rules/{id}`
- `PATCH /price-rules/{id}`
- `DELETE /price-rules/{id}`
- `GET /customer-price-rules`
- `POST /customer-price-rules`
- `PATCH /customer-price-rules/{id}`
- `DELETE /customer-price-rules/{id}`
- `GET /price-permissions`
- `POST /price-permissions`
- `PATCH /price-permissions/{id}`
- `GET /exchange-rates`
- `POST /exchange-rates`

---

# 18. Pricing

- `POST /pricing/calculate`
- `POST /pricing/batch-calculate`
- `POST /pricing/check-permission`
- `POST /pricing/simulate`
- `GET /pricing/history`

返回示例：

```json
{
  "cost": 37.8,
  "recommended_price": 48,
  "recommended_range": [46, 50],
  "minimum_price": 43,
  "profit": 10.2,
  "profit_rate": 0.2125,
  "approval_required": false
}
```

---

# 19. Logistics

- `GET /logistics/providers`
- `GET /logistics/routes`
- `POST /logistics/calculate`
- `POST /logistics/compare`
- `GET /logistics/quotes`
- `GET /logistics/quotes/{id}`

---

# 20. Quote

- `GET /quotes`
- `POST /quotes`
- `GET /quotes/{id}`
- `PATCH /quotes/{id}`
- `DELETE /quotes/{id}`
- `GET /quotes/{id}/versions`
- `POST /quotes/{id}/versions`
- `GET /quotes/{id}/timeline`
- `GET /quotes/{id}/version-comparison`
- `GET /quotes/{id}/send-logs`
- `GET /quotes/{id}/approval-history`
- `POST /quotes/{id}/clone`

---

# 21. QuoteVersion

- `GET /quote-versions/{id}`
- `PATCH /quote-versions/{id}`
- `POST /quote-versions/{id}/copy`
- `POST /quote-versions/{id}/recalculate`
- `POST /quote-versions/{id}/submit-approval`
- `POST /quote-versions/{id}/withdraw-approval`
- `POST /quote-versions/{id}/generate-pdf`
- `POST /quote-versions/{id}/send-email`
- `POST /quote-versions/{id}/mark-sent`
- `POST /quote-versions/{id}/accept`
- `POST /quote-versions/{id}/reject`
- `POST /quote-versions/{id}/expire`
- `POST /quote-versions/{id}/convert-to-order`

正式对客动作只作用于当前版本。`mark-sent` 必须已审批通过且仍在有效期内；
`accept/reject` 必须该版本有真实发送时间，且报价仍为已发送、尚无客户结果。
历史版本不能借单据的当前状态接受、拒绝或发送；同一发送、接受、拒绝的重试
返回既有事实，不重复创建时间线、主管通知和审计，也不重置发送/客户结果时间。

`mark-sent` 可传 UUID `request_key`：同一次操作及网络重试沿用该编号；
同版本再次实际发送使用新编号，分别登记发送记录。省略编号时仅确认首次发送，
已发送的旧数据重试不补造发送事实。同编号改变渠道/收件人返回错误。
该接口是人工确认已实际发送，不调用邮件服务；`send-email` 在邮件服务未配置时
仅登记未投递记录，不产生正式发送事实。前端确认按钮明确写“确认已实际发送”。

正式发送、客户接受/拒绝同事务写入客户过程记录及主管通知待办，保留版本号和
实际操作者；只刷新业务进展时钟，不刷新有效客户联系或约定跟进时间。
通知生成失败保留待办，由已有重试机制恢复。`confirm-win` 共用接受动作，
与接受接口交错调用也只留一条接受事实；成交后不能重复确认改换成交版本。
转订单只允许已正式发送/客户接受的当前版本，已转单的重试仍返回既有订单。

---

# 22. QuoteItem / Charge

- `GET /quote-versions/{id}/items`
- `POST /quote-versions/{id}/items`
- `POST /quote-versions/{id}/items/batch`
- `PATCH /quote-items/{id}`
- `DELETE /quote-items/{id}`
- `GET /quote-versions/{id}/charges`
- `POST /quote-versions/{id}/charges`
- `PATCH /quote-charges/{id}`
- `DELETE /quote-charges/{id}`

---

# 23. Approval

- `GET /approvals`
- `GET /approvals/{id}`
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/reject`
- `POST /approvals/{id}/transfer`
- `POST /approvals/{id}/withdraw`
- `GET /approvals/{id}/records`
- `GET /approval-definitions`
- `POST /approval-definitions`
- `PATCH /approval-definitions/{id}`

`GET /approvals?pending_for_me=true` 用于“待我审批”：仅返回当前用户数据范围内、
符合当前节点审批资格的待审批单。列表与通过/拒绝操作共用资格判断，涵盖审批权限、
价格权限、节点角色、普通节点转交对象、禁止自审及财务会签；筛选后再计数和分页。
响应中的 `can_approve` 供页面显示通过/拒绝按钮，实际提交时仍重新校验。
`mine=true` 表示“我已提交”；查询所有提交状态时传 `status=`。
列表、详情、审批记录及审批操作均按关联报价的数据范围校验。
`POST /approvals/{id}/transfer` 在修改审批和创建通知前，检查接收人的在职状态、
报价查看权限、关联报价的数据范围及当前节点审批资格（包括价格权限、节点角色、
禁止自审及财务会签，管理员保留既有兜底权限）。检查拟接收人时不受旧处理人指派限制。
接收人不符合权限、范围或节点条件时返回 `422 / 40001` 并说明原因，原处理人保持不变。

## 钉钉 OA 开发总闸

- `POST /inquiries/{id}/oa-approval`
- `GET /inquiries/{id}/oa-approvals`
- `POST /oa-instances/{id}/resolve`
- `POST /dingtalk/oa-sync`

`DINGTALK_PUSH_OFF=1`（默认）阻止全部钉钉外部请求，含 token、人员/部门/模板查询、
图片上传、审批发起/重发和状态同步；客户端底层同样强制拦截。
发起仍保留本地 `skipped` 记录，外部身份留空、需外部查询的表单字段标为待查询。
人工重发返回 `403 / 40301` 并说明开发阶段未执行，原记录及尝试时间不变。
状态同步返回 `checked=0, changed=0, disabled=true` 和明确的未执行说明，不改原审批状态。

---

# 24. FollowUp

- `GET /followups`
- `POST /followups`
- `GET /followups/{id}`
- `PATCH /followups/{id}`
- `DELETE /followups/{id}`
- `GET /customers/{id}/followups`
- `GET /contacts/{id}/followups`
- `GET /opportunities/{id}/followups`
- `GET /quotes/{id}/followups`
- `POST /followups/{id}/create-next-task`

---

## 商机承载需求与正式报价自动推进（2026-10-05 用户确认）

商机新建和复制校验目标客户的数据范围及删除状态。新建/编辑的
`primary_contact_id` 必须指向该客户的有效联系人；换客户复制商机时清空原联系人，
同客户复制保留有效联系人，不改原商机。

一次独立采购需求复用一条商机；询价、报价、打样与订单通过 `opportunity_id`
关联。`POST /custom-inquiries` 支持 `opportunity_id`，客户未填时从商机带入，
客户、商机、联系人须一致且在当前账号数据范围内。修订链共享商机关联；
无商机的旧询价转报价时只创建一条商机，关联到整条修订链。
`GET /custom-inquiries` 与 `GET /orders` 新增 `opportunity_id` 筛选，保留各模块
原有权限和数据范围过滤。定制打样从已关联的询价继承商机，拒绝跨客户或跨商机引用。

当前版本首次正式发送时，发送事实与关联商机阶段推进同事务提交，记录实际操作者、
报价编号/版本、阶段历史及审计。只推进状态为 open、尚处于配置的 active `quoted`
阶段之前的商机；按配置的阶段 sequence 判断先后，不回退、不重开，重试/重发不重复
推进。不关联商机的历史报价、已删除商机、缺少或禁用 `quoted` 配置不自动推进。
生成/下载草稿、内部审批、未投递邮件记录、客户接受、打样及报价转单不新增阶段规则。

## 人工跟进计划规则（实现方案 §3.2）

`POST /followups` 普通跟进必填 `next_action`（去空白后 1–200 字）和
`task_due_at`（含时区的 ISO 8601 时间）；自动创建一条关联客户及原单的
`followup` 待办，返回 `task_id`，不再需要勾选创建任务。
免填时传 `exemption_reason`：`customer_declined`（客户明确拒绝）、
`business_closed`（业务已关闭）、`waiting_external`（等待外部固定节点）。
免填原因与下一动作/时间互斥，不创建待办。旧 `create_task` 字段仅兼容，
不再用于跳过有效计划的任务生成。系统过程记录仍由原单操作生成，不适用人工跟进必填规则。

页面提交携带 `request_key`；API 调用方重试时也应复用原键（最多 96 字）。
同一用户、同一键、同一输入返回原跟进/任务并标记 `replayed=true`；
相同键但不同输入返回 409。首次成功提交才更新联系时钟、审计及主管通知。
新一次真实沟通须使用新键；省略该兼容字段时不提供请求重放去重。

跟进返回 `task_due_at`（记录时的计划时间）、`exemption_reason` 和 `next_task_id`。
`PATCH /followups/{id}` 修改计划时校验合并后的完整计划：未完任务复用并调整；
改为免填须同时清空下一动作和时间，并取消本条未完任务；已完成任务保留历史。
历史无计划记录仍可读取、修改正文，不自动补造原因或任务。
客户 `next_followup_at` 继续从全部未完成跟进任务取最早时间，任务完成、取消和
延期均重算，不让跟进快照覆盖当前待办状态。补建历史后续任务会保存关联，
重复提交相同任务参数复用原任务，已有任务参数不同返回 409。

AI 的 `create_followup` 使用同一个写入口及权限/数据范围校验；确认卡展示
下一动作、时间或免填原因。跟进、任务、审计、主管通知待办与 AI 动作执行状态
在同一事务保存。未确认提议不写业务，重复确认不重复建单。


# 25. Task

- `GET /tasks`
- `POST /tasks`
- `GET /tasks/{id}`
- `PATCH /tasks/{id}`
- `POST /tasks/{id}/assign`
- `POST /tasks/{id}/transfer`
- `POST /tasks/{id}/postpone`
- `POST /tasks/{id}/complete`
- `POST /tasks/{id}/cancel`
- `POST /tasks/batch-complete`
- `GET /task-rules`
- `POST /task-rules`
- `PATCH /task-rules/{id}`
- `DELETE /task-rules/{id}`

- `POST /tasks/run-auto-rules`：手动执行与每日调度共用扫描服务。

`PATCH /task-rules/{id}` 和 `PATCH /public-pool/rules/{id}` 接受部分字段，
未提交的名称、类型、等级、状态或配置保持原值；必需数据库字段不能显式写 null。
公海回收天数须为正整数；自动扫描天数须为非负整数，页面空值不当作零保存。

自动扫描按最新正式发送时间判断报价未跟进；发送后关联该报价/需求或客户级的
人工联系会解除“未跟进”条件，内部系统过程记录不冒充沟通。已删除客户/报价及
已转成非取消订单的报价不再生成催报价任务。同一客户的不同报价按报价分别去重。
应收提醒包括 `pending/partial/overdue`，取消订单不生成催收。
新生成的跟进待办同步客户 `next_followup_at`；扫描已有未完任务时批量校准该派生值，
到期扫描直接以任务日期和状态为准，避免历史漏写缓存导致提醒漏掉。

扫描返回 `created_count/tasks`、`agreed_skipped_count/agreed_skipped`、
`failed_rule_count/rule_errors`；无效规则配置记错误并跳过，不挡住其他有效规则。
同样信息写入 `run_auto_tasks` 审计。每日到期扫描另写
`run_followup_deadlines` 审计，记录报价、月结协议及跟进到期提醒数量，来源为
`SCHEDULER`。通知渠道均关闭时不把未生成的通知算作提醒成功。
手动及定时扫描共用事务锁，重跑/并发不会重复创建同一规则的未完任务或同一任务的到期通知。

客户阶段的报价事实仅计正式发送或客户接受；未发送草稿及内部审批不算对客报价。
已正式发送后内部起草新版本仍保留原发送事实，订单复购判定阈值沿用既有口径。
开发阶段调度及外部推送开关保持关闭；隔离验证直接调用任务入口，不打开真实开关。

---

# 26. Sample（可选）

- `GET /samples`
- `POST /samples`
- `GET /samples/{id}`
- `PATCH /samples/{id}`
- `POST /samples/{id}/approve`
- `POST /samples/{id}/ship`
- `POST /samples/{id}/sign`
- `POST /samples/{id}/feedback`
- `GET /samples/{id}/items`
- `POST /samples/{id}/items`

---

# 27. Order

- `GET /orders`
- `POST /orders`
- `GET /orders/{id}`
- `PATCH /orders/{id}`
- `GET /orders/{id}/items`
- `GET /orders/{id}/status-history`
- `POST /orders/{id}/cancel`
- `POST /orders/{id}/sync-erp`
- `POST /orders/{id}/refresh-status`
- `GET /orders/{id}/receivables`
- `GET /orders/{id}/payments`

---

# 28. ERP/MES Integration

- `POST /integrations/erp/orders`
- `POST /integrations/erp/orders/{id}/sync`
- `GET /integrations/erp/orders/{id}/status`
- `GET /integrations/erp/sync-logs`
- `POST /webhooks/erp/order-status`
- `POST /webhooks/erp/shipment-status`

---

# 29. Receivable

- `GET /receivables`
- `POST /receivables`
- `GET /receivables/{id}`
- `PATCH /receivables/{id}`
- `DELETE /receivables/{id}`
- `POST /receivables/{id}/mark-overdue`
- `GET /orders/{id}/receivables`

---

# 30. Payment

- `GET /payments`
- `POST /payments`
- `GET /payments/{id}`
- `PATCH /payments/{id}`
- `GET /payments/{id}/voucher`
- `POST /payments/{id}/voucher`
- `DELETE /payments/{id}/voucher`
- `POST /payments/{id}/confirm`
- `POST /payments/{id}/reject`
- `GET /orders/{id}/payments`
- `GET /receivables/{id}/payments`

回款凭证随回款和订单的数据范围访问：有 `payment:view` 的销售与财务可以查看/下载；上传和删除仅需 `payment:manage`，且只允许回款待确认时操作。确认或驳回后凭证不可再增删。

---

# 31. File

- `POST /files/upload`
- `GET /files/{id}`
- `GET /files/{id}/download`
- `DELETE /files/{id}`
- `GET /business/{type}/{id}/files`
- `POST /business/{type}/{id}/files`
- `DELETE /business-files/{id}`

`business_type=followup` 可用于跟进附件；附件清单、上传、下载与删除均沿用该跟进所关联业务对象的数据范围。
上传时服务端会校验目标对象可见性。其他类型以服务端文件访问层登记的业务对象为准。

---

# 32. Notification

- `GET /notifications`
- `GET /notifications/unread-count`
- `POST /notifications/{id}/read`
- `POST /notifications/read-all`
- `GET /notification-settings`
- `PATCH /notification-settings`
- `GET /notifications/level-policy`
- `PUT /notifications/level-policy`
- `POST /notifications/digest/run`

`GET /notifications/delivery-failures` 新增 `business_pending`，表示待生成主管通知的业务事件数。
`POST /notifications/retry-failed` 同时处理通知生成待办，新增 `business_events: { processed, failed, notifications }`。
业务事件的通知生成失败与企微投递失败分开统计；前者由业务事件待办重试，后者沿用原通知及现有退避策略。
开发阶段保持外部推送关闭；隔离回归中的通知记录不代表已经对外投递。

---

# 33. Audit

- `GET /audit-logs`
- `GET /audit-logs/{id}`
- `GET /business/{type}/{id}/audit-logs`

---

# 34. Timeline

- `GET /leads/{id}/timeline`
- `GET /customers/{id}/timeline`
- `GET /contacts/{id}/timeline`
- `GET /opportunities/{id}/timeline`
- `GET /quotes/{id}/timeline`
- `GET /orders/{id}/timeline`

时间线沿用统一响应，`data` 为事件数组：`kind/title/detail/operator_name/at`。
跟进事件新增可选 `source: { type: "sample" | "order" | "quote" | "opportunity", id: number } | null`，
前端据此打开已有原单详情页。来源按模块查看权限及原单负责人数据范围批量校验，
不继承客户可见范围；不可见的系统单据事实在取数量上限前过滤，人工跟进仍沿用客户时间线范围。

财务确认回款、确认交期变更、实际批次发货在同一业务事务写系统过程记录；
待确认、驳回、仅排批次不生成上述事实。操作者为实际执行用户；只刷新业务进展时间，
不刷新有效客户联系时间或约定下次跟进时间。回款动态显示确认事实与收款日期，
金额和凭证在订单原单中按现有权限查看。每次真实交期变更、每个实发批次、每笔回款确认使用各自稳定事件键。
上述业务事实及新打样的申请、资料修改、审批、制作、寄出、签收、反馈、客户确认，均留系统过程记录并排队主管通知。
记录人使用实际操作者，负责人与通知部门定位分开；旧记录不猜测重写作者。
手工跟进新增/实际修改也逐次排队通知，相同内容重复保存不新增通知。
主管按既有负责人部门定位，再检查客户与原单查看权限及数据范围；通知包含客户、变化摘要、操作者、负责人和原单来源。
系统过程记录不可通过跟进编辑/删除接口改写；原单越权时，时间线、跟进列表/详情/附件均过滤或拒绝访问。

通知待办保存于业务事件，通知生成失败留错误并保持待处理；重试在事件锁内生成各接收人的通知，完成标记与通知同事务提交。
`notifications.business_event_id + user_id` 唯一约束防重复。提交后投递、定时重试及管理员补投均可处理未完成待办，
不会再次执行业务动作或新增过程记录。新过程通知的站内列表、未读计数、即时/日报投递会重新检查原单权限。
历史事件缺少新来源字段时仍可显示，不根据内容猜测补来源。

---

# 35. Dashboard / Analytics

- `GET /dashboard/summary`
- `GET /dashboard/tasks`
- `GET /dashboard/risks`
- `GET /analytics/customers`
- `GET /analytics/leads`
- `GET /analytics/opportunities`
- `GET /analytics/funnel`
- `GET /analytics/quotes`
- `GET /analytics/pricing`
- `GET /analytics/products`
- `GET /analytics/sales-users`
- `GET /analytics/losses`
- `GET /analytics/receivables`
- `GET /analytics/payments`
- `GET /analytics/delivery`

---

# 36. System Config

- `GET /settings`
- `PATCH /settings`
- `GET /dictionaries`
- `POST /dictionaries`
- `PATCH /dictionaries/{id}`
- `GET /numbering-rules`
- `POST /numbering-rules`
- `PATCH /numbering-rules/{id}`
- `GET /customer-levels`
- `POST /customer-levels`
- `PATCH /customer-levels/{id}`

---

# 37. Sales Agent

## Session

- `GET /agent/sessions`
- `POST /agent/sessions`
- `GET /agent/sessions/{id}`
- `DELETE /agent/sessions/{id}`

## Message

- `GET /agent/sessions/{id}/messages`
- `POST /agent/sessions/{id}/messages`
- `POST /agent/sessions/{id}/messages/stream`

## Action

- `GET /agent/actions`
- `GET /agent/actions/{id}`
- `POST /agent/actions/{id}/confirm`
- `POST /agent/actions/{id}/reject`
- `POST /agent/actions/{id}/cancel`

## Execution

- `GET /agent/executions`
- `GET /agent/executions/{id}`
- `POST /agent/executions/{id}/retry`

## Specialized

- `POST /agent/customer-summary`
- `POST /agent/opportunity-analysis`
- `POST /agent/product-recommendation`
- `POST /agent/pricing-analysis`
- `POST /agent/quote-draft`
- `POST /agent/followup-suggestion`
- `POST /agent/risk-analysis`

---

# 38. Agent 内部 Tool

建议：

- `search_leads`
- `get_customer`
- `search_customers`
- `get_contact`
- `get_opportunity`
- `search_opportunities`
- `get_product`
- `search_skus`
- `calculate_price`
- `calculate_logistics`
- `create_quote_draft`
- `create_quote_version`
- `create_followup`
- `create_task`
- `get_order`
- `get_receivable`
- `get_sales_summary`

所有 Tool 调用必须记录：

- user_id
- role
- data_scope
- risk_level
- input
- output
- result

---

# 39. 错误码

- 40001 参数错误
- 40002 状态不允许
- 40003 必填业务字段缺失
- 40101 未登录
- 40102 Token 失效
- 40301 无权限
- 40302 数据范围受限
- 40303 价格权限不足
- 40401 对象不存在
- 40901 重复数据
- 40902 版本冲突
- 40903 重复转化
- 42201 低于允许价格
- 42202 需要审批
- 42203 审批未完成
- 42204 报价版本不可修改
- 50001 系统异常
- 50201 外部系统异常
- 50202 企业微信同步失败
- 50203 ERP/MES 同步失败

---

# 40. 幂等与并发

必须幂等：

- Lead 转化
- 报价转订单
- ERP/MES 推送
- 企业微信同步
- 回款确认
- Agent 创建任务
- Agent 创建报价版本

并发控制：

- QuoteVersion 使用版本号
- Customer Merge 加事务锁
- Order → ERP/MES 使用幂等键
- Approval 操作校验当前状态


### 打样来源与报价版本取代（2026-10-05 已确认）

- `GET /samples/source?quote_version_id=ID` 或 `?inquiry_id=ID`：仅可指定一种来源。要求 `sample:manage`、`quote:view` 及来源客户/商机的数据范围；草稿与历史版本可读，返回来源版本和明细，不返回价格与成本。
- `POST /samples/from-source`：必填 UUID `request_key` 与勾选的 `items`，每项含 `source_item_id`、默认 1 的正数 `quantity`，可选本次 `specification`、`remark`。原采购数量独立保存在 `original_quantity`，原明细快照在 `source_snapshot`，来源单据及版本在 `source_context`。禁止覆盖原询价/报价；未记录的原数量保持空值。同一用户相同 key/内容重试返回同一申请；key 已用于不同内容返回 409。
- 新建报价版本在同一事务结束所有旧版待审实例，保留结束时间及 `superseded` 操作记录，`summary.closed_reason=superseded`、`superseded_by_version_id=新版ID`。实例状态兼容已有 `withdrawn`，展示为“已被新版取代”；不改已完成结论。审批操作按商机→报价→版本→审批实例锁定，只允许当前版本待审流程，已结束的旧审批不能修改新版。
- 生成打样需求单使用保存的来源快照，并展示原采购数量与本次样品数量、规格/备注差异；旧数据无快照时保持原兼容逻辑，不补造历史数量。


### 订单草稿与正式下单（2026-10-05）

`GET /order-drafts/source` 读取询价或报价版本的已知资料，要求 `order:manage`、报价查看权限与来源数据范围；询价目标价不能当成交价。`POST /order-drafts` 接收唯一来源、UUID `request_key`、勾选明细及正数本次数量，保留原快照；相同请求重放返回同一草稿、内容变化返回 409。`GET /order-drafts` 分页支持客户/商机筛选，详情与 `PATCH /order-drafts/{id}` 按负责人范围授权；编辑需要 `revision`，过期返回 409，已转单草稿不可编辑。草稿单价未知保持空值，不写正式订单、应收或成交数据。

`POST /order-drafts/{id}/documents` 生成明确标注“草稿”的需求单 PDF，单独按草稿编号保存文件版本和修改差异；`GET /biz-docs?order_draft_id=ID` 查询文件，既有下载与权限检查复用。生成不覆盖原资料或旧文件。

`POST /order-drafts/{id}/confirm` 接收 `revision` 和 `quote_version_id`。同客户/商机、当前且有效、已审批并正式发送、客户已接受的报价才能正式下单；草稿全部明细、数量、规格、单价、备注、币种及付款条件须与该确认版本一致，不同则先修订报价和取得确认。同草稿同确认版本重复请求返回同一正式订单；其他草稿不能再次消耗已转单版本。独立定制核定尚无清楚业务标记，本批不以询价状态或目标价假定已核定。

正式报价转单也要求客户接受事实，不再允许“仅发送”直接进入待生产和应收。正式订单明细保存 `quote_item_id` 及来源快照，重复 SKU 按原行逐条比较；历史明细缺少原行编号且匹配有歧义时明确提示无法逐条比较，不伪造数量差异。分批发货仍使用订单批次，一版一张正式订单的数据库约束保留。


### 订单交期与确认计划（2026-10-05）

- `GET /orders/{id}` 增加 `delivery_kind`（shipping 发货日 / arrival 到货日；历史未明确为 null）、`transit_days`、`plan_offsets`、`shipment_date`。`delivery_date` 保留客户要求日期，arrival 的计划发货日 = 客户日期 − 人工运输天数。
- `POST /orders/{id}/schedule-changes/preview` 和 `POST /orders/{id}/schedule-changes`：`new_delivery_date` 必填；交期类型首次配置必须明确，arrival 必填运输天数（整数 0—365）；后续可沿用已确认口径。`plan_offsets` 若传入须包含 contract、deposit、pre_sample_sent、pre_sample_confirmed、first_shipment、payment 全六键，每项严格整数 −365—365。正数表示计划发货日前，负数表示发货日后。自然日计算，默认值仅为责任人审核的建议。
- 预览不落库，提交仅保存 pending 记录；同一订单仅一张待确认记录。确认接口沿用现有负责人 / order:assign 权限规则，按订单行锁后变更单行锁防并发。确认后才写交期类型、运输天数、节点参数及计划日期。重复确认返回 422；口径过期返回 409。历史没有明确交期类型的旧待确认单须作废并重新明确口径。
- `affected` 包含 `planning.before/after`、`old_shipment_date/new_shipment_date`、`shift_days`、节点和批次差异；`affected.applied` 单独记录确认时实际生效结果。修改运输天数也会移动待执行计划；已有手工调整按发货日差值平移，参数改变的固定节点重新倒排。实际完成、跳过节点及已实发/取消批次不重排。
- `GET /orders/{id}/milestones` 首次仅初始化六个空计划节点；已存在的历史计划不改。旧 `POST /orders/{id}/milestones/replan` 返回 422 并引导使用预览与责任人确认流程，避免绕过确认直接写计划。
- `PATCH /orders/{id}/milestones/{node_id}` 支持 `skipped=true` + 非空 `skip_reason`，记录 `skipped_by/skipped_at`，返回 `status=skipped`（不适用）。已实际完成不得跳过；跳过中不得直接登记实际完成，须先以 `skipped=false` 恢复适用。跳过及恢复均审计留痕。逾期提醒和节点统计排除跳过项；到货型订单的发货履约比较使用计划发货日；历史交期类型未明确的订单归入交期待补充，不按发货日猜测，也不计入准时率分母。
- 新迁移 `e9c3a7b1d5f4` 在一次性测试库验收；业务数据库尚未升级。
