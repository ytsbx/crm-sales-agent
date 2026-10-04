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
