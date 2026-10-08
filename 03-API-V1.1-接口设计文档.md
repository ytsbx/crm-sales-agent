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

## 2.1 登录会话与失效（第十批 10.12）

令牌是**有会话**的：每次 `POST /auth/login` 在服务端留一行登录会话，签发的令牌里带
会话标识；`/auth/logout` 与 `/auth/refresh` 以及所有需鉴权的接口，都按**同一个判据**
（那一行会话是否仍有效）决定放行。

对使用者的可观察行为：

| 动作 | 结果 |
| --- | --- |
| `POST /auth/logout` | **只作废本次登录**。同一个令牌再来（任意接口或 `/auth/refresh`）一律 `40101`；同账号在别处的登录不受影响 |
| `POST /auth/refresh` | 属于**同一会话**（不新建）。会话已失效时同样 `40101` —— 失效的凭据既不能访问、也不能续期 |
| 改 / 重置密码（`PATCH /users/{id}` 带 `password`） | 与写新密码**同一笔事务**：该账号**全部**旧会话一起失效（含操作者自己的当前会话），别的账号不受影响 |
| 服务重启 / 多进程 | 失效结果依然有效（判据只存库，无进程内缓存） |

两条与旧版本不同的硬约束，调用方需要知道：

1. **令牌必须带会话标识**。没有会话标识的令牌（本机制上线前签发的旧令牌、
   或自行构造的令牌）会被判为 `40101 登录状态无效` —— 放行它们等于留一类
   永远吊销不了的凭据。升级后需要重新登录一次。
2. **`logout` 之后不能再用同一令牌调 `logout`**：它此时已不是有效凭据，返回
   `40101`。前端清本地状态、跳登录页不受影响。

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

### 线索的归属：建线索时"指定负责人"

`POST /leads` 需要 `lead:create`。**带了 `owner_id` 时还要 `lead:assign`** ——
"把这条线索挂到某人名下"是**分配**动作，不是录入动作。少了这道闸门，只有"建线索"
权限的人就能借"新建"完成一次分配（页面上那个"分配线索"按钮他本来看不见）。

口径（2026-10-08 第十批 10.3 定）：**分配不看数据范围** —— 有 `lead:assign` 的人可以
把线索分给**任何人（含跨部门）**，不再按自己的部门/范围去卡目标。接收人必须
**存在且在岗**（不存在 404、已停用 422），分配会写一条分配历史（`lead_assignments`）
并把状态转成 `assigned`。不带 `owner_id` 则进线索池（状态 `pending`）。

### 线索转化

`POST /leads/{id}/convert`。需要 `lead:convert`。

```json
{
  "customer_mode": "existing",
  "customer_id": 1001,
  "create_contact": true,
  "reuse_contact_id": null,
  "create_opportunity": true,
  "opportunity_title": "德国浴桶采购"
}
```

**幂等与并发**（2026-10-08 第十批 10.1）：转化第一步就是**锁住这条线索**
（`lock_lead`：行锁 + `populate_existing`），锁内重读状态。从前读的是不加锁的
`get_visible_lead` —— 两个人同时点转化都会读到"还没转化"，然后各建一套客户，
幂等判断形同虚设（实测过 `[200, 200]`、库里多出一个客户）。已转化的线索再转
一律 **409**（`DUPLICATE_CONVERT`）。

**联系人复用**（同批 10.2）：

- 建联系人**之前**先看这个客户下有没有**同手机号 / 同邮箱**的人，有就**复用**
  那一条（不新建）。预览那一步查过一次，但预览到提交之间可能又有人录了一遍，
  提交时不再查就会在同一个客户下留下两个"同一个手机号"。
- 只查**同一个客户内**，不做跨客户匹配 —— 同一个人可以同时在两家客户处任职，
  拿它拦转化会误伤（那一类重复该走**客户合并**）。
- 想显式复用某一条（例如预览里给用户选过），传 `reuse_contact_id`。
  **只接受已经挂在目标客户下的联系人**：拿别人的联系人 id 过来等于借转化做一次
  越权的改挂，返回 **422**。
- `create_contact: false` 表示这次不碰联系人。

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
- `POST /customers/{id}/restore`（从回收站恢复；只认"直接删除"的，详见 #42.2）
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

## 主要联系人唯一（2026-10-08 第十批 10.4）

**一个客户最多一个主联系人** —— 库上有部分唯一索引兜底：

    UNIQUE (customer_id) WHERE is_primary AND deleted_at IS NULL

（只管"没删且是主"的行，删掉的联系人不占主位；建法与清老数据见迁移 `c3f8a1d6e9b4`。）

应用层三个入口在**写库之前**统一调 `take_primary_slot`（**先锁客户行、再把同客户
其它主联系人的标记取消**，这一整套叫"腾位"）：新建联系人 / 更新联系人 / 转化时
内部建联系人。顺序是关键 —— **腾位永远排在写入之前**：反过来（先 INSERT/UPDATE
再取消别人）这次写入自己就会撞上那条索引，用户看到的是 500。

两个容易出错的细节：

- **腾位要把"这次要设为主的那条自己"排掉**（`exclude=`）。`take_primary_slot`
  走的是 Core 的 `UPDATE`，SQLAlchemy 不知道它动过哪个 ORM 对象；把自己也刷成
  False 而内存里仍是 True 时，ORM 认为"没变化"不回写 —— 结果是接口回 200、
  主位却是空的。第一版就把原实现里的 `Contact.id != contact.id` 丢了，栽在这。
- **改挂客户时必须显式清掉主标记**（`bind-customer` / `change-customer` 且没传
  `is_primary`）。主标记是"在某个客户名下"的属性，跟着人走的话：新客户凭空多
  一个主联系人，而老客户的主位空着。

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
- `POST /products/{id}/restore`（从回收站恢复，连带恢复其名下的 SKU；详见 #42）
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
- `POST /skus/{id}/restore`（从回收站恢复；所属产品必须已恢复，详见 #42）
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

**`POST /costs/{id}/expire` = 人工立即停用**（第十一批 11.5，口径 2026-10-08 确认）：

- 写 `product_costs.stopped_at`（人工停用时刻），**不动 `effective_to`**。
- 取成本的判据因此变成两条**并**起来：`stopped_at IS NULL` **且** 落在生效区间内。
  区间那条仍是"**含截止日当天**"——**没有**为了"立即停用"把它改成"不含当天"，
  否则会连累另一类正常设置的有效期。
- 原实现的坑：失效操作把 `effective_to` 写成今天，而判据含当天 → 点了失效
  **当天照样取得到**，第二天才真的失效。
- 停用**不删成本历史**、也**不改已保存报价的成本快照**（历史报价记的是它当时取到
  的那份成本）。重复提交是幂等的：第二次不报错，也不改写第一次的停用时刻。
- 没有其他有效成本时，查价/核价如实提示"无生效成本、利润不可计算"，
  **不按零成本算**（这条一直如此）。
- 成本列表按 `stopped_at` 显示「已停用」，与「到某日止」「生效中」三态分开——
  只看 `effective_to` 会把已停用的那条显示成"生效中"。
- 取成本这条链只有 `pricing.service.get_effective_cost` 一个出口（查价、核价、
  价格中心的"当前生效"全走它），所以判据改一处即全一致。

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

汇率是**时点数据**，允许同一币种多条（按生效时间取最新）、也允许"提前录一条未来才生效的"。
取汇率（报价落快照时）的口径是**报价时刻已经生效的记录里取最新一条**（第十一批 11.4）：

- 原实现只按 `effective_at` 倒序取最新一条、**不过滤生效时点** —— 于是提前录的
  2030 年那条会被今天的报价用上（实测：当前生效 7、2030 年 99，快照存的是 99），
  来源还显示成那条未来记录，报价当场就错。
- 只有未来汇率、没有当前已生效的时，**明确拒绝**并说清"只有一条某日才生效的汇率"，
  不提前使用、也不静默按 1:1 折算。
- 手工指定 `exchange_rate` 时仍按原规则（`source=手工指定`），不走自动取数。
- 已确认报价的汇率快照**不受影响**：修的是"取数"，不是"重算历史"。

---

# 18. Pricing

- `POST /pricing/calculate`
- `POST /pricing/batch-calculate`
- `POST /pricing/check-permission`
- `POST /pricing/simulate`
- `GET /pricing/history`
  - 核价历史：报价 / 报价明细 / 成本 / 价格规则 / 客户特殊价的变更，取自审计日志。
  - 每条原本只有内部编号 `business_type` + `business_id`，界面上看不出是哪张单。
    **2026-10-06 起每条额外返回**：
    - `target_label`：业务对象的人话描述，如「报价单 Q202610060001 · ZX-6040-B」。
      报价明细可能已被硬删，此类记录靠快照里的 `quote_version_id` 反查报价单号；
      成本 / 价格规则 / 客户特殊价靠 `sku_id` 反查产品编码，客户特殊价另带客户名。
      反查不到时为 `null`（前端退回显示「类型 #编号」）。
    - `target_link`：可跳转的前端路径（目前只有报价类给得出来），跳不了为 `null`。
  - 成本与底价相关的 `before` / `after` 快照仍只对 `price:manage` 返回。

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
- `POST /logistics/calculate`：试算；`save=true` 时把选中方案落成一条试算记录。**落库前校验"这条记录记在谁名下"**（2026-10-08 新增）：`customer_id` 要存在、未被删、且在本人数据范围内（403/404）；`opportunity_id` 要存在、未被删，且**属于所选客户**（404/422）。两者都不给＝纯比价，照常保存。判据与列表 `GET /logistics/quotes`、详情同一口径——从前只有落库这一处没判，能把自己的试算挂到同事的客户上（同事按客户筛就会看见一条不是他算的）。
- `POST /logistics/compare`：多方案对比。与上面试算**走同一个入口**，所以下列校验同样适用。
- `GET /logistics/quotes`
- `GET /logistics/quotes/{id}`

**入参的数值口径**（2026-10-08 第十批 10.6 / 10.7）：

- **数量、单件重量、单件体积都必须大于 0**，否则 422。负数一路算下去会得到负的重量/体积、
  费用被"最低收费"托底，看着像"算得出方案"，实际是垃圾数据。数量本来就有这道闸门，
  两个"本次指定"的覆盖值（`weight_override` / `volume_override`）是漏的。
- **箱规体积缺箱装数时不当单件用。** SKU 上的 `carton_volume` 是**一整箱**的体积，
  要除以 `carton_qty` 才是单件。缺 `carton_qty` 时退回**长宽高推算**（那是单件尺寸），
  来源里注明"箱规体积因缺箱装数未采用"；连长宽高也没有，`volume` 按 0（缺失）并在
  `warnings` 里说清原因，**绝不拿整箱体积当单件**（否则按"一箱几件"的倍数虚高，
  而且从结果里看不出来）。
- SKU 维护侧同口径：`POST /products/{id}/skus` 与 `PATCH /skus/{id}` 的
  重量、长宽高、箱规体积、箱装数都**不许为负**（怎么清空？传 `null`，不要传 0 或负数）。

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

**补建后续任务时会继承跟进的业务关联**（第十一批 11.6）：

- 客户 / 联系人 / 线索 / 商机沿用原样；**报价、订单**填进任务对应的列；
  **打样**任务表没有那一列，改用任务上的"来源业务对象"承载（`source_business_type=sample`），
  任务详情据此能跳回那张打样单。原实现这三样**全丢**，新任务看不出是从哪张单子来的。
- 每一条关联在继承前**当场复核**：还在、没被软删、且**属于同一个客户**。
  对不上的**不继承**，并在返回的 `skipped_references`（人话列表，如 `["报价 #123"]`）
  与提示语里点名说明；原历史跟进一个字不动。**不为了"复制字段"把越权或跨客户的
  脏关联带进新任务。**
- 重复提交（内容一字不差）回放原任务，不产生第二张；内容变了会照旧 409
  （"已有后续任务"）。

**负责人校验不分来源**（第十一批 11.7）：显式选的、从跟进**继承来的**走同一道闸门
（存在 + 在职）。原实现只查显式传入的那个 —— 历史跟进的负责人早已停用时，
补建出来的任务会被分给一个停用账号：谁都看不到、也没人处理。默认那位已停用时
提示里会点明是"这条跟进的负责人"，并请另选一位在职的；**不改写历史跟进的原负责人**。

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

**删凭证 = 先解除这条回款的关联，文件本体只在没人再用它时才删**（第十一批 11.2）。判据与通用删文件**同一份**（`file.service.inspect_file_usage`）：
- 这份文件同时是**受保护的原件**（已签署的扫描件 / 系统生成的原件 / 已锁定打样的制作依据）→ **422 拒绝**，并说清它是什么；这条回款的关联也**不动**（拒绝就是整体不生效）。
- 别处还在用（通用附件挂载 / 合同生成稿 / 单据生成稿 / 打样依据 / 另一条回款的凭证）→ **只解绑**，返回 `detached_only=true`，文件记录与磁盘文件都留着。
- 确实没有别的引用 → 才把文件记录与磁盘文件一起清（`file_deleted=true`）。
- 上传是**登记失败即清理**：写入盘后若登记/审计失败，本次那份文件会被删掉（11.8），不留"盘上有、库里没有"的孤儿。多个上传入口共用同一份补偿逻辑。
- 删除与"给这份文件加引用"的入口（通用挂载 / 产品挂附件 / 合同登记签署）**共用同一把文件行锁**：否则"查完引用"与"真正删掉"之间还能被人挂上来，留下指向已删文件的悬空引用。

---

# 31. File

- `POST /files/upload`
- `GET /files/{id}`
- `GET /files/{id}/download`
- `PATCH /files/{id}`（2026-10-08 加）：改**展示名**。见下面「改文件名」。
- `DELETE /files/{id}`
- `GET /business/{type}/{id}/files`
- `POST /business/{type}/{id}/files`
- `DELETE /business-files/{id}`

`business_type=followup` 可用于跟进附件；附件清单、上传、下载与删除均沿用该跟进所关联业务对象的数据范围。
上传时服务端会校验目标对象可见性。其他类型以服务端文件访问层登记的业务对象为准。

删除（`DELETE /files/{id}`）与上传失败的补偿，判据统一在 `file/service.py`（第十一批 11.2 / 11.8）：
- **受保护的原件**一律不许走通用删除：已签署的扫描件、系统生成的原件、已锁定打样的制作依据；
- **还有别处引用**也不许从这一头删。引用检查覆盖**五处**：通用附件挂载、合同的生成稿、单据的生成稿、回款的凭证、打样的制作依据 —— 这些列**全都不是外键**，数据库不会替我们守住，只能应用层查全（原先只查了第一处）；
- 一个文件挂在多个业务对象上时，先在各处解绑，不要直接清原件；
- 删文件与"给文件加引用"的入口（通用挂载 / 产品挂附件 / 合同登记签署）**共用同一把文件行锁**：不加锁时，"查完引用"与"真正删掉"之间还能被人挂上来，留下指向已删文件的悬空引用。

## 改文件名（`PATCH /files/{id}`，2026-10-08）

入参 `{file_name}`，需要 `file:manage`，并且这份文件要在调用方的可见范围内。

- **只改展示名**：`files` 表里 `file_name` 是给人看的（列表里显示、下载时落成本地文件名），
  `object_key` 是磁盘上的存放位置。**改名绝不动 `object_key`** —— 动它就要搬文件，
  搬到一半失败会留下"记录指着不存在的路径"（下载时才 404）。
- **受保护的原件不许改名**（已签原件 / 系统生成的原件 / 已锁定打样的制作依据）→ **422**，
  与"不可删除"**共用同一份判据**（`file/service.inspect_file_usage` 的 `protected`）：
  名字本身是"当时是哪一份"的线索，合同生成稿从「…V1.pdf」被改成「…V2.pdf」之后版本就说不清了。
  确需纠错走作废 / 开修订版。
- 名字校验都在接口里给中文原因：空或全空白 → 400；超过 255 字 → 400（与列宽一致）；
  **含控制字符（换行 / 制表）→ 400** —— 这个名字会进 `Content-Disposition` 响应头，
  带换行的名字等于往响应头里注入一行。
- 名字没变 → 200 但**不写审计**（点了保存却什么都没改，不该在流水里留一条）。

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

## 部分更新（PATCH）里的「必填字段」—— 第十一批 11.9

更新类接口的三档必须分清，实现集中在 `app/core/patch_schema.py`（公共基类 +
一张"哪些字段库里非空"的登记表，长度取**库列上限**），26 个更新入参共用：

| 情况 | 行为 |
|---|---|
| **没传**这个字段 | 保持原值（部分更新的本意） |
| **可清空**字段传 `null` | 允许清空（备注、描述、知识库、`PaymentUpdate.currency` 等） |
| **必填**字段传 `null` / 空串 / 纯空白 / 超长 | **`40001` 参数错误**，且**不动原数据** |

- 从前这三档里第三种会一路走到数据库：`null` 撞非空约束、超长撞列上限，
  用户看到的是 **500「服务器内部错误」** —— 而它其实只是一句"这个名字不能为空"。
- 提示语里带**字段中文名**（如"产品名称不能为空"），并说清"不打算改就别传这个字段"。
- 后端只做长度上限拦截，**不改任何字段的既有语义**；`PaymentUpdate.currency`
  的 `null`（"按应收节点币种重新对齐"）属于"可空传"，不在拦截之列。
- 顺带一提：**用户更新（`PATCH /users/{id}`）里"姓名为空"**这类情况现在也在
  参数校验阶段拦下（`40001`），不再走到路由层那句 `40003`。都是拒绝，只是码不同。

---

# 39. 错误码

- 40001 参数错误
- 40002 状态不允许（也用于**业务口径不允许**：`trade_mode` 是「只做国内」时
  传了非人民币的币种 —— 见 §41.17，判据只有一处，`app/core/trade_mode.py`）
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


---

# 41. 实现已落地、本文档此前未登记的接口（2026-10-06 补齐）

> 起因：`ops/api_gap.py` 做的是**单向**对账（"文档有、代码没有"），一直报 0 条。
> 2026-10-06 补做了**反向**对账，发现代码注册 514 条、本文档只写了 380 条，**缺 113 条**：
> 案例库、新品洞察、合同、对外单据、订单草稿、销售目标、定制询价、审批规则、标签、
> 物流费率等**整章都没有**。
>
> 本章把这 113 条登记齐（跳过纯辅助类：导入模板下载、导出、前端埋点、`/meta`、
> `/search`、`/files/{id}/preview`、`/webhooks/wecom/events`、`/agent/tools`）。
> 补齐后 `api_gap.py` 两个方向都应为 0。
>
> 每条格式：`路径` + **权限码** + 关键约束。路径不含 `/api/v1` 前缀（与全文一致）。

## 41.1 案例库（cases）

- `GET /cases`：案例列表。需要 `quote:view`。**筛选维度按方案 §3.7：客户类型、产品线、阶段及问题**。`customer_type`（企业/个人）取的是**关联客户档案上的字段**，不案例自己的「行业」——行业是案例填的自由文本，一个企业客户可以属于任何行业，两者不能互相顶替（第四轮返工 P2-8）。
  **搜索按"读者看得见的文字"匹配（第四轮返工 P1-6）**：有原文权限的人（主管/管理员/作者看自己那条）照旧在 DB 层按原文搜标题与正文；其余读者用**脱敏后**的标题与正文做匹配 —— 因为分享版下发的就是脱敏文字，若 SQL 仍按原文匹配，读者拿一个被隐藏的手机号一搜就能确认"这条案例里有这个号码"，**搜索变成了探测接口**。判据与 `serialize_case` 共用同一份（同一套抹除规则、同一个客户全称换代称），不另立一套。返回**真分页**（`items/page/page_size/total`，第四批 §5.1.6 之前是 `.limit(200)` 硬顶，第 201 条起永远看不到也无提示）。筛选：`status`、`industry`、`product_line`、`stage`、`problem_tags`（JSONB 包含匹配）、`keyword`、`include_history`。**默认只列当前版本**，判据是「自己已被取代」（`status=superseded`）而**不是**「库里存在指向自己的修订稿」——后者会让作者一点「开修订稿」（只生成一份还没发布的草稿）原版就从列表里消失，看着像案例丢了（返工单第 6 条）。`include_history=true` 时把被取代的旧版一并列出。可见范围：`published` **与 `superseded`** 均人尽可读（脱敏），其余只有作者本人与主管可见。
- `POST /cases`：新建案例（落 `draft`）。需要 `quote:view`。`title` 必填；证据单据必须存在、同客户、在数据范围内（防"挂上别人的单子"变成越权读入口）。
- `GET /cases/{case_id}`：详情。可见范围与列表一致；`pending_review` 不对外。**已被取代的旧版（`superseded`）照样打得开**——它是培训资料，不该因为出了新版就读不到（只读，见 PATCH）。
- `PATCH /cases/{case_id}`：修改。需要 `quote:view`，且仅作者在 `draft` / `rejected` 状态可改。`published` / `superseded` **一律拒（422）**，连主管也不能原地改——审核批的是"这一版内容"，改完还挂着"已发布"等于复用了一个对不上号的审核结论；要改就开修订稿重新走审核。
- `DELETE /cases/{case_id}`：删除。需要 `quote:view`（作者或主管）。
- `POST /cases/{case_id}/submit`：提交审核。需要 `quote:view`，仅作者可提交，状态须为 `draft` / `rejected`。**闸门**：`title` 非空 **且**（`key_actions` 或 `lessons` 至少一项非空）——注意「关键动作」与「可复用做法」是**二选一**，不是各自必填。
- `POST /cases/{case_id}/review`：审核。需要 `quote:view`，仅主管/管理员可驳回或批准；状态须为 `pending_review`。逐条追加审核历史；批准修订稿时替换被取代的那一版。
- **证据单据可以挂多条**（第四轮返工 P2-8，§3.7「从已有时间线和单据选证据」）：请求体带 `evidences: [{kind, business_id, label}]`，`kind` ∈ `quote` / `order` / `sample` / `opportunity`；**传了就整体替换**（界面提交的是"这一版挂了哪几张单"）。新增表 `case_evidences`（唯一约束 `(case_id, kind, business_id)`，重复挂同一条会被 422 拒掉）；案例表上旧的四个 `*_id` 列**保留并自动同步成每类的第一条**，老查询不受影响。校验与单条版同一套：存在、**同客户**、在操作者数据范围内 —— 挂上别人的单子等于开了一条越权读入口。返回体的 `evidences` 是**按读者权限过滤后**的列表，被滤掉的那几类记在 `hidden_evidence` 里。
  为什么要改成多条：原来四个列每种只能挂一张，"三个订单一起支撑这个案例"就表达不了。
- `POST /cases/{case_id}/revise`：从已发布案例开**修订稿**（§5.1.5 已确认口径＝修订稿）。已发布版继续可供培训（不改动、不断档）；修订稿走「改完 → 提交审核 → 批准 → 替换当前发布版」，原版转「已被修订版取代」。审核结论不继承。**幂等**：同一原版最多一份「在途」修订稿（`draft` / `pending_review` / `rejected`）——重复调用返回**已有的那一份**，不会建出一排同版本草稿（返工单第 6 条）。

## 41.2 新品洞察（product-insights）

- `GET /product-insights`：列表。需要 `product:view`。数据范围按 `owner_id` 过滤。
  **2026-10-06 起每行额外返回逐条可操作性（返修 R08）**：
  `can_edit`（这条是否在操作者的数据范围内）、`can_review`（此刻能否评审这条）。
  此前前端只能按权限码粗判，于是数据范围外的那几条也长着「编辑 / 提交评审 / 转需求」按钮，
  点下去才 403。**藏按钮不是权限**：每个写入口仍会走 `_get_writable` 再判一次。
- `POST /product-insights`：新建。需要 `product:manage`。`title` 必填且**去空白后不得为空**；`price_assumption` 不得为负；窄接口 `extra="forbid"`。
- `GET /product-insights/{insight_id}`：详情。需要 `product:view`。返回参考图 `images`、来源洞察回链、评审轮次。
- `PATCH /product-insights/{insight_id}`：更新。需要 `product:manage`。**语义：传了就改（含传 `null` ＝ 清空），没传就不动**（`exclude_unset=True`；旧实现用 `if value is not None`，导致"传 null 想清空"被跳过，界面清了库里还在）。内容冻结：`under_review` 期间不得改关键内容；`approved` 后改关键内容会**退回待评审**并 `review_round + 1`。
  **写入口的数据范围（第四轮返工 P1-3）**：编辑 / 提交 / 删除 / 转换一律走 `_get_writable` —— **有 `product:review` 不等于能改别人的**。此前这四个入口复用了"评审可见性"那个取数函数，而它对评审权限直接放行，于是"仅本人范围 + 有评审权限"的账号能直接改掉他人名下的草稿。能评审是职责（看得到），不能改是边界（改不动），两件事分开判。
  **改负责人**要校验接收人：存在、在职、且在操作者可分配范围内（与创建同一套判据）。
  **"改了没有"比的是值，不是"请求带没带这个字段"（P1-4）**：把标题原样再提交一遍不会被当成改动（否则已通过的记录会被误退回重审、轮次 +1）。字符串首尾空白、`Decimal('12.30')` 与 `12.3`、`None` 与空串都算"没变"。
  **所有状态写入都加行锁**（编辑 / 提交 / 审核 / 删除 / 转换），并发时后到的会读到最新状态并被状态检查挡下。
- `DELETE /product-insights/{insight_id}`：删除。需要 `product:manage`。**已转换的洞察不可删**（会断开来源追溯）。
- `POST /product-insights/{insight_id}/submit`：提交评审。需要 `product:manage`。
- `POST /product-insights/{insight_id}/review`：评审。需要 `product:view` + **`product:review` 权限码**（2026-10-06 新增，不再写死"主管角色"）；否决必须写意见。**这是唯一放行评审权限的写入口**：评审人要能审别人的单子，所以可见性放开；但他仍然改不了别人的内容（见 PATCH 那条）。落结论时会在**当轮记录**上补审核结果（谁、什么时候、结论、意见）。
- `GET /product-insights/{insight_id}/rounds`：**逐轮评审记录**（第四轮返工 P1-4）。需要 `product:view`。每轮含：提交时的**关键内容快照**（标题/来源/目标客户/方向/卖点/价格假设/结论）、提交人、提交时间、审核结果、审核人（含姓名）、审核时间、审核意见。
  为什么要这张表：`product_insights` 上那几个字段只有**最后一轮**的值 —— 第 3 轮通过之后，第 1 轮报的是什么内容、第 2 轮是谁为什么否掉的，全被覆盖；审计日志只记了"改了哪几个字段名"，没有当时的内容。详情接口的返回里也带 `rounds`。
- `POST /product-insights/{insight_id}/convert`：转成需求。需要 `product:manage`。走 `inquiry` 模块的统一创建流程（统一取号 `inquiry_no` + 客户/商机一致性校验）；未选客户时转为**内部开发需求**（`origin=internal_dev`），其可见范围只归提出者、评审岗与管理员，**不再出现"没挂客户所以人人可见"**。带行锁与转换关系唯一约束，重复请求返回既有单据。

## 41.3 定制询价（custom-inquiries）

- `GET /custom-inquiries`：列表。需要 `quote:view`。数据范围：本范围客户 + 自己创建的 + 未挂客户的**客户询价**；内部开发需求只对提出者/评审岗/管理员可见。
- `POST /custom-inquiries`：新建。需要 `quote:manage`。`title` 必填。
- `GET /custom-inquiries/status-summary`：各状态条数（待评估/开发中/已转商机/已归档），页面顶部徽章用。需要 `quote:view`。
- `GET /custom-inquiries/{inquiry_id}`：详情。需要 `quote:view`。前端修订表单用它回填当前版内容。
- `PATCH /custom-inquiries/{inquiry_id}`：修改。需要 `quote:manage`。
- `DELETE /custom-inquiries/{inquiry_id}`：删除。需要 `quote:manage`。
- `GET /custom-inquiries/{inquiry_id}/history`：整条修订链，按版本升序（先看最早的原始要求）。需要 `quote:view`。
- `POST /custom-inquiries/{inquiry_id}/revise`：客户改要求 → 新增一版（版本号 +1、留修订说明），旧版原样保留。需要 `quote:manage`。（§3.3："改了三次要求却只留最新一版、看不出怎么变的"。）
- `POST /custom-inquiries/{inquiry_id}/create-quote`：从定制需求直接发起报价（§3.1/场景09）。需要 `quote:manage`。定制件投产前没有 SKU，按 SKU 选品选不到它，这条把「需求 → 商机 → 报价 → 定制明细」一步串起。

## 41.4 标签（tags）

- `GET /tags`：标签列表。需要 `customer:view`。
- `POST /tags`：新建标签。需要 `customer:update`。
- `PATCH /tags/{tag_id}`：修改。需要 `customer:update`。
- `DELETE /tags/{tag_id}`：删除。需要 `customer:update`。

## 41.5 审批规则（approval-rules）

> 与 #23 Approval（审批**单**）不是一回事：这里管的是"规则本身"的草稿 / 发布 / 版本。

- `GET /approval-rules`：规则列表。需要 `quote:view`。
- `POST /approval-rules`：新建规则（落草稿）。需要 `settings:manage`。
- `GET /approval-rules/condition-fields`：条件字段目录。需要 `quote:view`。前端编辑器的字段/操作符下拉、单位与提示语都来自这里。
- `GET /approval-rules/{rule_id}/versions`：规则版本列表。需要 `quote:view`。
- `PATCH /approval-rules/{rule_id}`：编辑草稿。需要 `settings:manage`。**改动不立即生效**——引擎只按已发布版本求值，要生效须 `publish`。
- `POST /approval-rules/{rule_id}/publish`：把当前草稿发布成新版本（生成不可变快照），引擎从此按这一版求值。需要 `settings:manage`。
- `PATCH /approval-rules/{rule_id}/enabled`：启停开关，立即生效的运维动作（与草稿/发布无关）。需要 `settings:manage`。未发布过的规则打开开关也**不会**生效。
- `POST /approval-rules/sandbox`：规则沙盒——拿一张真实报价版本试跑全部规则，逐条给出命中明细，**不产生任何副作用**。需要 `quote:view`。试算对象是当前草稿状态，正好用于"改完规则、发布前先验证"。
- `DELETE /approval-rules/{rule_id}`：删除规则。需要 `settings:manage`。审批单上的规则痕迹存的是名称快照，删规则不影响历史留痕。

## 41.6 合同 / 月结协议（contract-documents、contract-templates）

- `GET /contract-documents`：台账。需要 `order:view`。**真分页**（`items/page/page_size/total`）。筛选：`customer_id`、`status`、**`order_id`**、**`quote_id`**（后两个 2026-10-06 新增，供客户/订单/报价详情页嵌"这家客户的合同"用；服务端筛选而非前端本地过滤，否则分页后第 21 条起就看不见了）。数据范围跟客户负责人**当前**归属走。
- `POST /contract-documents`：从模板生成草稿。需要 `order:manage`。入参含 `template_id`、`customer_id`、`quote_id`、**`quote_version_id`**（2026-10-06 新增：把合同钉死在**具体报价版本**上，报价后来出 V2 不影响已生成的这份）、`order_id`、`extra_fields`、`expiry_date`、`parent_id`（补充协议/续签指回原件）、`request_key`（幂等键，重试/连点只出一份）。生成时把**抬头（公司名/客户名/订单号/报价号）连同正文一起落快照**，并把 PDF 渲染一次落盘、记 sha256。
- `GET /contract-documents/{doc_id}`：详情。需要 `order:view`。返回正文快照、生成时的缺项清单（`missing_fields`）、关系链（基于哪份 / 被哪几份补充或续签）、以及**签署原件清单 `signed_files`**（2026-10-06 新增）。
- `GET /contract-documents/{doc_id}/download`：下载**生成稿**（§3.6）。需要 `order:view`。**返回生成时落盘的那一份**——客户后来改名、公司换抬头、报价出了 V2，都不会让已经发出去的那份跟着变。签署原件不放这里，走签署件清单单独取。

  **存档不可用时不静默重新生成**（第十一批 11.3）：文件记录缺失 / 磁盘文件缺失 / 校验值与登记时不符 → 一律 **410** 并说明是哪一种（**不返回 PDF**），同时单独落一条 `download_original_missing` 审计便于从备份恢复。原实现是"读不到就当场重排一份给你、HTTP 200"，用户会以为拿到的就是当初那份存档，"同一编号永远同一份"这句承诺也就破了。历史上**本来就没有存档**的旧记录（`generated_file_id` 为空）仍可下载，但必须能分辨：响应头 `X-Contract-Source: legacy_rendered`、文件名带「（依据历史数据生成的副本）」（`filename*` 走 RFC 5987 编码）。正常读存档时为 `X-Contract-Source: generated`。前端两处下载入口（合同台账、业务详情页的合同面板）共用 `shared/download-contract.ts`，失败原因会以提示条显示 —— 原来 `void` 掉了一个可能失败的 Promise，点了没反应。
- `POST /contract-documents/{doc_id}/sign`：登记签署（上传客户签回的扫描件）。需要 `order:manage`。**闸门**：未签草稿可先备条款，但登记签署前必须挂上正式依据（正式订单，或**已发送/已接受**的报价；月结协议可只关联客户）——"允许提前备合同"口径，2026-10-05 定。**挂了报价版本时还要多问一句**（2026-10-08 新增）：合同钉住的那一版（`quote_version_id`）**客户得见过**（该版本自己的 `sent_at` / `accepted_at` 非空），否则 422 并说清"那一版没发过"——报价为了发出 V2 把状态改成"已发送"之后，一份钉在**从未发出的 V1** 上的合同不能签字，否则台账上会留下一个假的依据。没钉版本的（提前备条款时可以不选）沿用老口径：看整份报价的状态。
- `POST /contract-documents/{doc_id}/void`：作废。需要 `order:manage`。**已签合同的作废要求主管权限**，原因必填且不得为纯空白；原件保留（作废 ≠ 删档）。
- `GET /contract-templates`：模板列表（按类型多版本并存）。需要 `order:view`。
- `POST /contract-templates`：新增一版模板。需要 `settings:manage`。**不覆盖旧版**，已生成的文件仍指向它们当时用的那一版。`(doc_type, name, version)` 有唯一约束，并发建同名模板会拿保存点重试取下一个版本号。

## 41.7 对外单据（biz-docs、biz-doc-templates）

- `GET /biz-docs`：单据台账。需要 `order:view`。可用 `order_draft_id` 等条件查询。
- `GET /biz-docs/{doc_id}`：单据详情。需要 `order:view`。
- `GET /biz-docs/{doc_id}/download`：下载单据。需要 `order:view`。正文只取生成时的**快照**——之后改业务资料不影响已出的文件。格式按类型分流：报价单是客户要拿去改/填的 **Excel（xlsx）**，打样单与下单文件是正式文件（**PDF**）；两者共用同一套台账。明细表的「材质/工艺/图纸」列**只在真有值时才加**（下单文件共用同一个渲染函数，无脑加列会给它加一片空格子）。
- `POST /biz-docs/quote`：按报价版本生成对客 Excel 报价单。需要 `quote:manage`。金额取自那一版，不现算。
- `POST /biz-docs/order`：按订单生成下单文件。需要 `order:manage`。来源报价与差异一起落快照，订单本身不变。
- `POST /biz-docs/sample-request`：按打样申请生成打样需求单。需要 `sample:manage`。来源询价与差异一起落快照，原单不变。
- `POST /biz-docs/{doc_id}/void`：作废。需要 `order:manage`。状态改掉，**内容与校验值不动**（作废 ≠ 删档）。
- `GET /biz-doc-templates`：对外单据模板列表（按类型多版本并存）。需要 `settings:manage`。
- `POST /biz-doc-templates`：新增一版模板。需要 `settings:manage`。不覆盖旧版。

## 41.8 销售目标与实绩（sales-targets）

- `GET /sales-targets`：目标 vs 实际。需要 `customer:view`。非 admin 只看自己 + 全公司目标行；团队指标只对 `department` 及以上开放。**考核口径＝确认回款**（差额与达成率都用它），归月依据是**财务确认时间**（`payment_records.confirmed_at`）而*不是*客户打款那天——跨月确认（1 月底到账、2 月初确认）时按确认月计（返工单第 2 条）；签单额 / 发货额只展示、**不进差额**。返回里带：`attribution_note`（本页是**业绩口径**＝签单归属，与应收/账龄页的责任口径不同，同一页内计划/实绩/差额/明细必须同源）、`actual_frozen`（这一期是否为结账存档）、`missing_confirmed_at_count` 与 `missing_confirmed_at_note`（状态已确认却没记确认时间的回款笔数——这些钱**没进任何金额**，要提示业务去补录，不能拿打款日顶替）。
  **新客口径（第四轮返工 P1-2）**：`new_customer_actual` ＝ 该客户**首笔非取消订单**落在本月的客户数（考核口径，与年度统计、下钻明细、冻结快照同源）；另返回 `new_customer_created_actual` ＝ 本月**新建档**客户数，作为**过程指标**单独一列，**不进差额与达成率**。此前把"建档数"当成了考核实绩（9 月只建档、一单没成也显示"新客实绩 1"），而明细按首成交列 —— 同一页两个口径，点开还会互相打脸。
  **数据范围（第四轮返工 P1-1）**：**冻结快照的读取、补零行、覆盖值与人员名称查询统一按操作者范围过滤**，不因为"这一期结过账"就放宽。个人快照按人、部门快照按部门各自授权；公司汇总行仍按既定规则人人可见（它只有一个汇总值，不带任何个人业绩）。
- `GET /sales-targets/bases`：三种销售额口径 + 老客净额 + 两种新客口径（文档 §六 / 场景17）。需要 `customer:view`。**口径与数据来源随结果一起返回**（业务要能回答"这个数字怎么来的"）；签单/发货/回款三个数刻意分开、不互相顶替，发货口径按**实际发货批次**分摊到各批次所在月。**回款口径＝按财务确认时间（`payment_records.confirmed_at`）归月**，不是客户打款那天；已确认但没记确认时间的**不计入**（不拿打款日顶替，否则同一列里混进两个口径）——返工单第 2 条。本页同时给出 `new_by_created`（过程指标）与 `new_by_first_deal`（考核口径）两条序列。
- `GET /sales-targets/drilldown`：把某个指标的某个（期间, 作用域）拆到**具体业务记录**（§4.3 可追溯明细）。需要 `customer:view`。参数 `metric`、`period`、`user_id` / `department_id`。合计与上面两个接口用同一套口径与筛选——文档要求"所有断言应定位到业务记录或批次，而不是只比汇总数字"。**归属与汇总同源**：销售类指标一律按**签单归属**（`coalesce(sales_owner_id, owner_id)`），不再是"明细按当前负责人"——否则交接过的单子点开明细永远对不上汇总（返工单第 3 条）。**新客明细与汇总走同一份取数**（`target_bases.new_customer_rows`）：一行 = 一个首成交客户，`count` 必然等于汇总的 `new_customer_actual`（返工单 P1-2 要求"数量必须与明细一致"）。**已结账的期间读存档明细**（`source=snapshot`，`actual_frozen=true`），与冻结的汇总同一次写入，退货/改单之后仍然对得上；未结账的期间是 `source=live` 实时算。`items[].date` 已格式化为可读的本地时间（如 `2026-06-05 10:00`）。
- `POST /sales-targets/upsert`：新增/更新目标行。需要 `settings:manage`。
- `POST /sales-targets/bases/refreeze`：重算某一年的口径基准（老客池 / 首次成交）并重新冻结（§4.1.5）。需要 `settings:manage`。冻结的意义是"历史不被后来的订单变更改写"，但确实存在需要重算的正当理由（如历史订单状态当初录错）；与其让每次读取都悄悄重算（等于没冻结），不如给一个**显式、可审计**的重置动作。
- `POST /sales-targets/actuals/freeze`：**结账**——把已经过完的这一期的实绩抄一份存档（§4.1.5 后半）。需要 `settings:manage`，且必须 `data_scope=all`（只冻自己看得到的那部分，等于把半张报表当账结了）。之后这一期的数字不再随订单状态变：客户今年退掉去年的一张单，去年结过账的那一期照样是原来的数。（在此之前报表是每次打开现算的，年底发奖金拿的那份报表过几个月再看就变了。）**存档的是整行五个指标**（签单 / 确认回款 / 发货 / 新客 / **老客净额**——`target_actuals.ACTUAL_METRICS`），**构成这批数的明细会一起冻**（新表 `analytics_actual_snapshot_items`）——只冻汇总的话，结账后一张退货单就会让"点开明细"比"合计"少一笔（返工单第 4 条）。返回 `{period, rows, scopes, items}`。当月不允许结账（数据还在产生）。
- `POST /sales-targets/actuals/refreeze`：**重算**已结账期间的实绩。需要 `settings:manage` + `data_scope=all`。**必须填原因**——改历史数字是要有人担责的事；不带原因的重算等于让"数字为什么变了"永远查不出来。改动前后的考核口径合计都写进审计。重算是「按现在的数重出一份」，会把上一版有、这一版没有的**陈旧汇总键一并清掉**（返回里的 `removed`），明细也**按现在的数据重出**（不会把上一版的明细原样抄回去，那样"重算"等于什么都没干）。当月不允许结账（数据还在产生）。

## 41.9 订单（orders）

- `POST /orders/{order_id}/status`：变更订单**履约**状态（待生产 / 生产中 / 已发货 / 已签收 / 已完成）。需要 `order:manage`。
  **不接受 `status=cancelled` → 422**（2026-10-06 收口）：把状态改成"已取消"看起来只是改一个字段，但取消订单要连带处理钱和账，必须走下面的 `POST /orders/{id}/cancel`。此前前端把这个下拉里也放了"已取消"，走那条路**只改状态位** —— 订单显示已取消、催收与逾期提醒却照旧发，已收到钱的订单也能取消。现在服务端也堵一道，**换个入口同样绕不过去**。
  成功提示带订单号（`{order_no} 的履约状态已更新为「…」`）：提示是全局浮层，用户切到别的页面还会挂几秒，没有主语就对不上是哪个订单。
- `POST /orders/{order_id}/cancel`：**取消订单**（不可逆的终态动作）。需要 `order:manage`，并校验数据范围。
  四条连带规则（默认口径，要改先改 `order/router.py`）：① 已有**已确认**回款 → **422 拒绝取消**（钱不能随订单静默作废，先人工处理回款）；② **待确认**回款 → 随订单一并驳回（原因写进凭证备注）；③ **未回清**的应收计划 → 置 `cancelled`，**不再派催收/逾期提醒**；④ 未发货的批次 → 随单取消（已发货的批次是既成事实，保留）。
  商机成交状态**不自动回退**：成交是已发生的商业事实，撤销成交走 `POST /opportunities/{id}/lose` 由人工评估（这条也写进审计）。
  先锁整单再改：与"登记发货"并发时只能有一个成功。前端入口是订单详情页的**「取消订单」按钮**（独立确认弹窗，列出上述连带影响），不在"更新履约状态"的下拉里。
- `GET /orders/{order_id}/finance-summary`：财务汇总。需要 `payment:view`。
- `POST /orders/{order_id}/receivables`：新增手动应收计划。需要 `payment:manage`。**币种强制继承订单币种**，不接受调用方指定。
- `POST /orders/{order_id}/receivables/generate`：按比例生成应收计划（如 30% 定金 + 70% 尾款）。需要 `payment:manage`。**每一期的币种同样继承订单币种**——第十一批 11.1：这条路径原先不写 `currency`、落到列默认的 CNY，于是美元订单分出来的几期全成了人民币（"金额看着对、代表的钱已经不同"），而回款登记又是跟应收节点币种走的，错会一路传到回款、直到跨币种校验也形同虚设。该订单**已有任何应收计划**时拒绝生成（要重排请先清掉原计划）；带 `request_key` 的重复提交回放第一次结果，不生成第二组。末期金额用"总额 − 前面各期之和"，保证各期合计严格等于订单金额。
- `GET /orders/{order_id}/shipments`：批次与未发量——跟单看"承诺/事实"分开的数字。需要 `order:view`。
- `POST /orders/{order_id}/shipments`：新建发货批次（计划）。需要 `order:manage`。
- `POST /orders/{order_id}/shipments/{batch_id}/ship`：登记实发。需要 `order:manage`。只推进订单到「已发货」——整单完成由**未发量闸门**把关（场景13）。
- `DELETE /orders/{order_id}/shipments/{batch_id}`：删除批次。需要 `order:manage`。
- `GET /orders/{order_id}/schedule-changes`：交期变更历史（留着当时的前后版本对比）。需要 `order:view`。
- `POST /orders/{order_id}/schedule-changes`：生成交期变更单（待责任人确认）。需要 `order:manage`。**未确认前不动任何计划日期。**
- `POST /orders/{order_id}/schedule-changes/{change_id}/confirm`：责任人确认——这一刻才真正改交期、重排节点与批次计划日。需要 `order:manage`。
- `POST /orders/{order_id}/schedule-changes/{change_id}/cancel`：作废待确认的交期变更单。需要 `order:manage`。权限与确认一致：责任人本人，或有 `order:assign` 的主管。**没有这条出口**，库级"一单只允许一张 pending"的部分唯一索引会把该订单之后的交期变更**永久堵死**。
- `POST /orders/{order_id}/milestones/replan`：旧重排入口**已停用**，统一使用"预览 + 责任人确认"。需要 `order:manage`，调用返回 422 并引导走新流程（避免绕过确认直接写计划）。
- `POST /orders/{order_id}/repurchase`：复购——以老订单明细为基础直接开一个新商机（不重建客户）。需要 `opportunity:manage`。

## 41.10 订单草稿（order-drafts）

> 口径见前文「订单草稿与正式下单（2026-10-05）」。生成草稿**不等于**正式下单。

- `GET /order-drafts`：草稿列表。需要 `order:view`。分页支持 `opportunity_id` / `customer_id` 筛选，按负责人范围授权。
- `GET /order-drafts/source`：读取询价或报价版本的已知资料。需要 `order:manage`。参数 `quote_version_id` 或 `inquiry_id`，**只能指定一个**。询价目标价不能当成交价。
- `POST /order-drafts`：从来源生成草稿。需要 `order:manage`。接收唯一来源、UUID `request_key`、勾选明细及正数本次数量；保留原快照。相同请求重放返回同一草稿，内容变化返回 409。草稿单价未知保持空值，不写正式订单、应收或成交数据。
- `GET /order-drafts/{draft_id}`：草稿详情。需要 `order:view`。
- `PATCH /order-drafts/{draft_id}`：编辑草稿。需要 `order:manage`。编辑需带 `revision`，过期返回 409；**已转单的草稿不可编辑**。
- `POST /order-drafts/{draft_id}/confirm`：草稿转正式订单。需要 `order:manage`。接收 `revision` 与 `quote_version_id`。**同客户/商机、当前且有效、已审批并正式发送、客户已接受**的报价才能正式下单；草稿全部明细、数量、规格、单价、备注、币种及付款条件须与该确认版本一致，不同则先修订报价并取得确认。同草稿同确认版本重复请求返回同一正式订单；其他草稿不能再次消耗已转单版本。
- `POST /order-drafts/{draft_id}/documents`：生成明确标注"草稿"的需求单 PDF。需要 `order:manage`。单独按草稿编号保存文件版本和修改差异，不覆盖原资料或旧文件。

## 41.11 报价版本（quote-versions）

- `GET /quote-versions/{version_id}/pdf`：生成并下载报价单 PDF。需要 `quote:view`。数据全部取**快照**，不回查当前价格。
- `GET /quote-versions/{version_id}/send-logs`：发送记录。需要 `quote:view`。
- `GET /quote-versions/{version_id}/price-drift`：草稿版本"价格已有更新"检测（方案 §5/A09）。需要 `quote:view`。**只读**：逐明细按当前条件重查适用价，与快照拟报价比对。
- `POST /quote-versions/{version_id}/price-refresh`：把系统带价的明细刷新到当前适用价。需要 `quote:manage`。**仅草稿可刷；手工价明细不覆盖。**

## 41.12 产品与查价（products、pricing、logistics）

- `POST /products/{product_id}/skus`：给产品新增 SKU。需要 `product:manage`。
- `GET /pricing/lookup`：统一查价（方案 §4）——返回本次条件的适用价与命中来源。需要 `product:view`。**只读、不落库**；客户必须在当前用户数据范围内（与客户列表同一口径，公海客户放行）；**成本 / 最低保护价只对有 `price:manage` 的角色返回**（方案 §7 字段脱敏）；缺价返回 `status=pending`（待定价），不做成本推算兜底（D4/D5 已确认）。
- `GET /pricing/sku-options`：给价格中心与核价页的下拉用——SKU + 所属产品名，一次取全。需要 `product:view`。**注意路径**：不要挂到 `/products/xxx` 下面，否则会被 `/products/{product_id}` 抢先匹配。
- `PUT /price-permissions/{role_id}`：设置角色的价格权限（最小利润率、折扣上限等）。需要 `price:manage`。
- `GET /logistics/rates`：运费费率列表。需要 `product:view`。
  **没有 `keyword` 参数** —— 它无条件返回**全部**费率（传了也不生效）。想按名字筛就在
  客户端筛：把它当"按关键字查"用会出事（曾经有个套件的"按关键字兜底清理"因此
  把整张表删空，而断言照样绿 —— 见 `check_logistics_rate_admin` 里的守卫）。
- `POST /logistics/rates`：新增费率。需要 `price:manage`。可传全部字段：
  `provider` / `origin_region` / `destination_region` / `shipping_method` /
  `unit_price_per_kg` / `unit_price_per_volume` / `min_charge` / `eta_days` /
  `eta_days_max` / `remark`（一开始界面只收四个，配不出"这家到华东、按方计价"）。
- `PATCH /logistics/rates/{rate_id}`：**修改**费率。需要 `price:manage`（2026-10-08 加）。
  `exclude_unset` 语义：**没传的字段保持原值，传 `null` 才是"清空"**。
  - **不许清空**：`provider` / `shipping_method` / `unit_price_per_kg` / `min_charge` /
    `status`（库里非空）→ 传 null 是 **400（40001）**，不是 500；超长同样 400。
  - **可以清空**：两个地区字段、`unit_price_per_volume`、两个时效、`remark`。
  - ⚠️ **两个地区字段"留空 = 不限"**（匹配时视作通配），而写「全国」是一个**具体取值** ——
    两者在核价匹配里行为不同，别混。
  - `status` 改成 `inactive` = **停用**：匹配只认启用中的费率
    （`logistics.rate_query`），所以"先停掉、数据留着"不必非得删。
  - 不存在的 id → 404。改与删都写审计（`business_type='logistics_rate'`）。
- `DELETE /logistics/rates/{rate_id}`：删除费率。需要 `price:manage`。（此前该端点不存在：配错费率删不掉，测试清理也一直空转。）
- **费率匹配的命中级别要如实说出去**（2026-10-08）：`POST /logistics/calculate` 与
  `/logistics/compare` 的返回里带 `match_level`（`0` 没给条件 / `1` 精确命中 /
  `2~4` 放宽了起运地或目的地或运输方式 / `5` **兜底**＝列出全部启用费率）。
  核价估算取"匹配到的方案里最便宜那条"，一旦放宽过，**最便宜那条可能根本不属于
  这次要发的地方** —— 所以核价的 `warnings` 里会带上放宽的原因，兜底时另加一句
  「这条运费来自兜底匹配，可能不准，请手工核对」。**认 `match_level` 这个数字，
  别去匹配提示文字**（文案一改就悄悄失效）。

## 41.13 客户（customers，补充条目）

- `GET /customers/stage-distribution`：六阶段分布——当前数据范围内各阶段客户数（了解/报价/打样/首单/返单/稳定复购）。需要 `customer:view`。
- `POST /customers/{customer_id}/contacts`：给客户新增联系人。需要 `customer:update`。
- `GET /customers/{customer_id}/merge-logs`：客户合并记录。需要 `customer:view`。返回里含 `moved`（各类关联各自迁移了多少条）与 `conflicts`（**当时冲突是怎么处理的**）。
- `GET /customers/{customer_id}/merge-preview`：**合并影响清单**（只读，返工单 6.8）。需要 `customer:view`。参数 `target_customer_id`。
  返回 `targets`（15 类关联各有多少条会跟着走，含**字段名不叫 `customer_id`** 的企微客户映射与客户附件）、`total_links`、`conflicts`、`blocking`。
  **合并不可逆，先给人看这一页**：不能点一下才发现有两百张单子跟着换门牌。
- `POST /customers/merge`：把来源客户合并进目标客户。需要 `customer:update`。请求体多一个 `resolutions`（冲突处理口径，键见影响清单的 `blocking`）。
  - **合并前列出全部关联对象**：联系人、商机、报价、订单、跟进、任务、定制需求、打样单、订单草稿、合同、销售案例、物流报价、专属价格、企微客户映射、客户附件 —— 原实现只有前 6 类，其余单据在来源客户被软删后成了孤儿（订单在目标客户下、打样还挂在被删的来源客户上；合同和附件在目标档案里找不到）；
  - **冲突必须有人拍板，绝不静默覆盖**：专属价格（同一 SKU + 同一数量档两边价不同）要求选 `keep_target` / `keep_source`，被淘汰的一边转 `historical`（不删，价目历史要留着）；两边税号不一致要求 `confirm`。**不给口径 → 422**；
  - 历史报价、合同签署文件、打样图纸与确认记录的**内容不改写**，只换档案归属；
  - **并发保护**：先按 id 排序加行锁并重读最新值，两个人同时对同一对客户点合并不会各改一遍；
  - 合并日志记下**实际迁移的对象、数量与冲突处理结果**；任一必须处理的关联失败则不返回整体成功。
- `POST /customers/{customer_id}/claim`：**领取公海客户**。需要 `customer:view`。
  与 `POST /public-pool/customers/{id}/claim` 走**同一个服务函数**，行为完全一致。
  语义（返工单 6.2 统一后的口径）：
  - **取行锁**后才判可领取 —— 两个人同时领只有一个人成功，另一个拿到 **409**（提示被谁领走）；
  - 可领取条件 = 有效记录 + `pool_status=public` + 无负责人；
  - **幂等**：已经在自己名下时返回成功但**不再写一条归属变更历史**（网络重试不会留两条）；
  - 私有客户按操作者数据范围判（不在范围内 **403**），公海客户人人可领；
  - 失败**不写归属历史、不发通知**。

## 41.14 工作台看板（dashboard）

> 与 #35 Dashboard / Analytics 互补：这三个是**主管/个人工作台**的聚合接口。

- `GET /dashboard/trend`：趋势。需要 `customer:view`。
- `GET /dashboard/activities`：动态。需要 `customer:view`。
- `GET /dashboard/team`：PRD §4.2 主管工作台汇总。需要 `customer:view`。数据范围是 `self` 的用户会拿到 `is_team_view: false` 与**空指标**，而不是全员数据——团队指标只对 `department` 及以上开放。

## 41.17 业务口径「只做国内」是一道真闸（2026-10-08）

> 口径本身早就定了（`08-待领导确认清单`：2026-09-24，只做国内、币种固定人民币），
> 但**此前只做了半截**：`trade_mode` 全项目只有两处被读（下发配置、核价页拿它藏
> 输入框），**后端一处校验都没有**。于是页面看不到币种，`POST /quotes` 传
> `currency=USD` 照样把美元写进库；订单草稿页那个币种框也压根不看开关。

- **闸的位置**：`app/core/trade_mode.py` 的 `ensure_currency_allowed()`，
  口径是 `domestic` 时非 CNY → **400 / `40002`**，提示里说清是口径造成的。
- **三个写入点**（逐个入口确认过，不是按同类推的；登记在 `CURRENCY_GATE_SITES`，
  套件 `check_trade_mode_gate` 用 AST 对账，漏一处回归报红）：
  `quote/service.create_quote`（报价创建 —— 接口建单、复制报价、小助手工具都过它）、
  `order/service.create_order`（手工建订单）、`order/drafts.update`（订单草稿改币种 ——
  界面上唯一还能改币种的地方）。
- **不在这道闸里的**：订单继承报价版本、回款继承应收节点、商机走列默认值
  （这些是**继承**，源头已经拦住）；**成本**另有更严且与开关无关的一条
  （`COST_CURRENCIES = {"CNY"}`）；**外部同步（ERP）不拦** —— 拒收会让整批同步
  失败，它靠下面那条提醒兜底。
- **改口径即可放开**：`PATCH /settings` 把 `trade_mode` 改成 `both`，同一请求立刻
  通过，**不用改代码**。
- **不做折算时也绝不静默**：含金额汇总的接口会带一个可选字段
  `currency_warnings: string[]`（只有系统里真有外币、且还没折算时才出现），
  内容是「存在非人民币金额，下列汇总未做折算」。
  挂在 `analytics/router.py` 的 `_with_fx_note` 上，判据只有一处；**跟着数据范围走**
  （别人名下的外币单不会给看不到的人弹提示）。只覆盖**返回对象**的接口 ——
  `dashboard/trend`、`dashboard/risks`、`analytics/products`、`analytics/sales-users`、
  `analytics/funnel` 回的是数组，装不下这句话，但它们在**已挂了提醒的页面**上，
  提醒是页面级的，所以不挂不等于看不见（原因写在 `_with_fx_note` 的注释里）。

## 41.15 通知补投（notifications，补充条目）

- `GET /notifications/delivery-failures`：投递失败概览——多少条没出去、其中多少条还会自动重试。需要 `settings:manage`。
- `POST /notifications/retry-failed`：批量补投——把失败与未投递的通知重新排队后立即投一遍。需要 `settings:manage`。
- `POST /notifications/{notification_id}/redispatch`：补投**单条**通知。只需登录，权限在函数内判：**本人可补投自己的，管理员 / 设置管理员可补投任意人的**。关键设计：补投走的是同一行通知、不重跑业务动作，因此不经过 `business_events` 的唯一键——人工补发不会被去重挡住，也不会在客户时间线多出一条留痕。

## 41.16 集成就绪度（integrations）

- `GET /integrations/erp/readiness`：ERP 就绪度。需要 `order:view`。
- `GET /integrations/wecom/readiness`：企微就绪度——凭据缺哪些 + 本地已同步多少数据。需要 `wecom:view`。
- `POST /integrations/wecom/unbound-contacts/{contact_id}/ignore`：暂不处理某条未绑定联系人（PRD §8.3 的第 3 步之三）。需要 `wecom:manage`。

## 41.16b 离职交接（wecom，返工单 6.6 / 6.7）

- `GET /integrations/wecom/transfer-preview`：**交接清单预览**（只读）。需要 `wecom:view`。
  参数 `handover_user_id`、`takeover_user_id`。按类别返回逐项清单：客户、商机、待办任务、
  **打样单**、订单、订单草稿、企微客户关系；每项带当前责任人、拟接手人、状态与
  **不能交接的原因**（撞单争议冻结）。关注字段：`sections`（分类明细）、`blocked`（本次动不了的项）、
  `sample_count`（在途打样数）、`totals`。
  **看清单和实际执行读的是同一份数据** —— 不会出现"清单里明明没有、执行时却改了"。
- `POST /integrations/wecom/transfer`：离职继承（PRD §8.4）。需要 `wecom:manage`，
  且需管理员开启 `WECOM_TRANSFER_ENABLED=1`（该操作会变更客户在微信里看到的服务人员）。
  **执行顺序（返工单 6.7）**：先做完**全部**前置检查（接手人在职、撞单冻结、清单盘点），
  之后才发企微转接，最后改本地归属。老实现是"先调企微、再改 CRM"，CRM 那步一旦因撞单
  争议报错回滚，**已经发出去的企微转接撤不回来** —— 客户在微信里看到的服务人员已经变了，
  本地却什么都没留下。
  - 争议冻结的客户**不交接、也不发出企微转接**；
  - **只迁离职人的责任**：客户名下其他在职同事的未完成待办原样保留；
  - 转移：客户、商机、未完成待办、**未结束的打样单**、订单、订单草稿及其对外单据；
  - 保留：创建人、历史跟进、历史报价、审批与审计日志、`sales_owner_id`（签单业绩归属）；
  - 返回 `detail` 汇总（两侧分开算）：`crm_moved` / `wecom_transferred` / `wecom_failed` /
    `frozen` / `pending_items` / `failures`（**完整失败明细，不再截断到 10 条**）。
- `GET /integrations/wecom/transfer/{job_id}/items`：**逐项交接结果**，真分页。需要 `wecom:view`。
  `kind` 按类别过滤（`customer` / `sample` / `wecom_relation` …），`pending_only=true` 只看没办完的。
  每项**两侧状态分开记**：`crm_status` / `crm_status_label` 与 `wecom_status` / `wecom_status_label`，
  外加各自的错误原因与 `attempts`。企微侧的客户关系本身没有 CRM 归属要改，`crm_status` 记 `not_applicable`。
- `POST /integrations/wecom/transfer/{job_id}/retry`：**按逐项状态重试**没办完的项。需要 `wecom:manage`。
  已完成（成交付/已转接）的项一律不碰 —— 整体重跑会把已经转出去的关系再发一遍，
  企微那边会当成重复操作报错。返回 `{retried, remaining}`。

## 41.17 打样（samples，补充条目）

> 补齐 #26 Sample。以下 5 条与原 10 条合计 **15 条**。

- `POST /samples/{sample_id}/made`：登记**制作完成**（文档 §3.5「分别记录制作、寄出、签收、客户确认」）。需要 `sample:manage`。刻意**不做成状态闸门**：CRM 管不到车间，把"制作完成"变成必点的状态只会让跟单为了往下走随手一点，反而污染数据。这里只记事实与时间，供打样需求单与跟单看板回答"目标完成日到了没有"。
- `POST /samples/{sample_id}/confirm`：登记**客户确认结果**（文档 §3.5）。需要 `sample:manage`。规则只有一条但是重点：**必须先签收才能确认**——客户没收到样品就"确认接受"是假数据；签收是物流事实、确认是业务事实，分开记才答得了"这批样到底过没过"。
- `POST /samples/{sample_id}/resubmit`：把**已驳回**的打样单**原样**重新提交审批（业务方 2026-10-05 定）。需要 `sample:manage`。为什么要有专门的接口：「改资料会自动回到待审批」只覆盖了"改完再报"；跟单若认为驳回理由不成立、一个字都不想改，就**没有任何入口**——只能去改个无关字段"骗"系统回待审批，那条留痕是假的（审计里写着改了备注，实际什么都没改）。原样重提时**保留上次的驳回原因**（单子还要再批，主管需要看到上一轮为什么被打回）。已驳回的单子也可由主管**直接改判为批准**。
- `POST /samples/{sample_id}/revise`：开**新修订版**（第一批返修 §3.3，口径已确认 A：原单出 V2、旧版冻结只读）。需要 `sample:manage`。用在"车间依据（材质/工艺/图纸版本/目标完成日/验收标准/数量）要在**已制作或已寄出之后**改"的场合，代替原地改：复制单头与明细（含车间依据）作为起点，`version = 旧版 + 1`、`parent_id = 旧版`；**不继承旧版的制作与寄送事实**（继承过来就是伪造）。
- **打样单的附件区与「附件锁」**（2026-10-08）：`GET /samples/{id}` 与列表都多下发两个字段
  `attachment_locked`（布尔）与 `attachment_lock`（人话原因，没锁时为 `null`）。
  判据是 `file/access.sample_write_lock_label_for`——**与上传闸门同一份**，
  别在别处再写一遍（分叉出来的那份会让界面按错误的前提上传、被 422 挡住还说不出原因）。
  为什么需要它：打样单**已登记制作完成 / 已寄出签收**之后，新附件**只能**标成
  「后续补充资料」（`category=supplement`），否则 422。前端附件区据此**自动**带上该类别，
  并在锁定后显示提示 —— 用户不必（也没地方）自己选类别。
  原来界面上**根本没有上传附件的入口**，而「登记制作完成」那个弹窗却写着
  「请先在详情的附件区上传图纸/确认件」——指向一块不存在的地方，于是「制作依据」
  永远只能选空列表。现在详情里补了附件区。
- `PATCH /samples/{sample_id}/items/{item_id}`：改一条明细的**车间依据**（材质 / 工艺 / 图纸版本）。需要 `sample:manage`。与单头资料的编辑**共用同一套闸门**（`_gate_part_lock`）：已批准的单子改了这三项就退回「待审批」重新批，已寄样/已签收直接拒。两处各写一份规则迟早会漂移，而"哪一档能改"是业务口径，只该有一个出处。**注意**：明细与单头用同一套闸门，但"同值重发不算改动"——只传没变化的字段会被判为无改动，不会误退回。

## 41.18 商机补充：确认成交（opportunities）

- `POST /opportunities/{opportunity_id}/confirm-win`：确认成交并生成订单（方案 §5 / A13）。需要 `opportunity:manage`，**且需同时具备 `order:manage`**。一个动作完成：校验成交版本 → 商机标记成交 → 版本转订单。幂等：商机已成交不重复改；版本已转过单直接返回已有订单（重试安全）。

## 41.19 公海回收（public-pool，补充条目）

- `POST /public-pool/run-recycle`：触发一次公海回收**扫描**。需要 `settings:manage`。正式环境由定时任务调用。审计写在 service 内部（它自己 commit），路由层不再补写——避免提交后再写审计反而落到另一个事务里。
  **⚠️ 语义已变（返工单 6.3）**：老实现是"扫到就直接清空负责人、客户当场进公海"，不可逆——业务员出差两周没点跟进，跟了半年的客户就没了。现在**只提名**（落一条回收候选 + 预告），回收要等主管批准。返回 `{nominated_count, candidates, protected_count, protected, disputed_count, already_open_count, notified_count, notice_days}`；`released_count` 恒为 0（保留字段兼容老调用方）。
  **履约保护的客户不会被提名**（有效正式报价 / 在途订单 / 未结应收 / 在途打样），保护原因随 `protected[].reasons` 返回。
  **预告同时真的发出去**（返修单第六批 8 + 追加口径 3）：提名后给**原负责人**和**管理范围内的复核主管**各发一条站内通知，内容含客户、回收原因、最近活跃时间、到期时间与查看入口；`notified_count` 是本轮实际发出的条数。按 `(接收人, 类型, 业务对象, 标题)` 去重，定时任务重跑不会重复打扰；通知失败可在通知中心补投，不会重复生成候选或重复回收。
- `GET /public-pool/recycle-candidates`：回收候选（预告）列表，**真分页**（`items/page/page_size/total`）。需要 **`customer:pool_review`**（返修单第六批第 7 条：这是**独立业务权限**，默认授给销售主管；此前复用 `settings:manage`，而默认主管根本没有它，"主管逐条或批量批准"实际打不通）。`status` 默认 `pending`，可选 `deferred`（已暂缓）/ `executed`（已回收）/ `rejected`（已驳回）。**列表与单条操作都按客户数据范围过滤** —— 只能看到/处理本团队客户的候选。
  每行带：原负责人、命中规则与天数、最近有效联系 / 最近业务进展（复核要看得到"是按哪个时间判冷落"）、提名时的保护明细快照、`notice_days`（提名时的预告天数快照）、`earliest_action_at`（**最早可回收时间**：待复核看预告到期、已暂缓看暂缓到期）、`defer_days`、`early_approved`。
- `POST /public-pool/recycle-candidates/{id}/decide`：复核一条候选。需要 `customer:pool_review` + 数据范围校验。请求体 `{decision, note?, early?}`，`decision` = `approve`（执行回收）/ `reject`（驳回）/ `defer`（暂缓）。
  - **等待期必须真的走完**（返修单第六批第 8 条）：`pending` 状态下未到 `due_at`、`deferred` 状态下未到 `deferred_until` 时，`approve` 会被 **422** 拦下并提示最早可回收时间；此前这两个时间戳只是存着，当天就能收走。
  - **提前回收是独立例外动作**：等待期未满确需回收时传 `early=true` **且必须填 `note`**，单独记 `early_approved` 与审计动作 `execute_pool_candidate_early`（与"带着履约保护硬收"分开留痕）。
  - **驳回不受等待期限制**（暂缓期内也可以随时结案）。
  - **批准执行前会重新检查**：预告发出之后客户若又有了新跟进、新报价、新订单、新回款，会被 **422** 拦下（提示里说清是哪张单据）；
  - 仍想例外回收 → 填 `note` 后重试，会记 `exception_approved` 并写审计；
  - 驳回/暂缓也要填 `note`（谁、为什么）；
  - **同名客户中途换过人**（`owner_id` 与提名时不一致）→ 409，让主管重新看，避免按过时依据回收。
- `POST /public-pool/recycle-candidates/batch-decide`：批量复核。需要 `customer:pool_review` + 逐条数据范围校验。**请求体一次带齐** `{candidate_ids: number[], decision, note?, early?}`（返修单第六批第 10 条：此前 id 走查询参数、决定走请求体，与前端正好反着，从未调通）。**逐条处理、逐条报结果**（`{done: [{candidate_id, status}], failed: [{candidate_id, reason, code}]}`）：被拦下的不影响其余，且**不会被执行**，仍留在待复核里。
- `POST /public-pool/recycle-candidates/{id}/restore`：**恢复**——把被回收的客户还给原负责人。需要配置项 `pool_recycle_restore_permission` 指定的权限（默认 `customer:assign`，即销售主管；管理员不受限；**普通业务员不能恢复**），并**校验客户数据范围**——客户进了公海虽然人人可见，也不能因此让别的团队的主管捞走。
  三条纪律：**保留原回收记录**（状态转 `restored`，不删）；客户**已被别人合法领取**时报 **409** 并记下冲突（`restore_conflict_owner_id`），**绝不静默覆盖**；原负责人已停用时 422（改派给别人）。
- `PATCH /settings`（回收相关配置）：需要 `settings:manage`。可改 `pool_recycle_notice_days`（回收预告期，天）、`pool_recycle_defer_days`（主管暂缓等待期，天）、`pool_recycle_restore_permission`（恢复所需权限码）。
  **校验取值**（天数须为 0–365 的整数，权限码必须在权限表里真实存在），**审计记下修改前后值与修改人/时间**。
  **改动只对新提名的候选生效**：候选上各自存着生成时的天数与绝对到期时间，不追溯改期（追加口径 1）。`GET /settings` 会把"代码默认值里配了、库里还没落行"的项一并返回并标 `is_default=true`，界面上据此标注"当前为开发默认值"。
  三条纪律：**保留原回收记录**（状态转 `restored`，不删）；客户**已被别人合法领取**时报 **409** 并记下冲突（`restore_conflict_owner_id`），**绝不静默覆盖**；原负责人已停用时 422（改派给别人）。
- `POST /customers/{id}/release-to-pool`（**人工释放**）：需要 `customer:assign`。客户**还在履约中**时 **422** 拦下并说清是哪张单据；主管确需释放时在请求体里带 `reason` 表示**例外**，会记审计。判据与定时扫描、回收执行共用同一套保护规则。
- `GET /customer-duplicate-cases`：撞单待裁定队列，**真分页**（`items/page/page_size/total`，原来 `.limit(300)` 硬顶、第 301 条起永远打不开）。需要 `customer:view`。
- `POST /customer-duplicate-cases/{id}/resolve`：裁定。需要 `customer:assign`。
  **归属变更复用普通转移那条路径** → **未完成的待办跟着新负责人走**，已完成的原样不动（历史记录）；保存裁定**前**的归属（`before_owners`）与裁定后（`resolved_owner_id`）、证据、结论、操作者与理由。
  **并发保护**：只有一个人能裁定成功（行锁 + 状态复查），重复请求 422。
  **未决唯一**：同一对客户（A/B 与 B/A 视为同一对）最多一张未决案件（部分唯一索引），并发查重不会开出两张。
- `POST /public-pool/customers/{id}/claim` / `POST /public-pool/leads/{id}/claim`：领取公海客户 / 线索。
  客户需要 `customer:view`，线索需要 `lead:view`。与客户详情、线索中心那两条领取路径**共用同一个服务函数**，
  检查项、幂等、报错文案一致（此前两处各写一份、已经漂移）。
  客户可领取 = 有效 + `public` + 无负责人；线索可领取 = 无负责人 **且状态为 `pending`**
  （已转客户 `converted`、已废弃 `invalid` 即使没有负责人也不许领，拒绝码 40002）；
  并发只有一人成功（另一个 409）。
- `POST /public-pool/customers/{id}/assign` / `POST /public-pool/leads/{id}/assign`：把公海对象指派给某人。
  客户需要 `customer:assign`，线索需要 `lead:assign`。
  **取数必须与单条转移同口径**：`get_visible_customer` / `get_visible_lead` ——
  公海对象（无负责人）人人可见，**私有对象必须在操作者数据范围内**。
  此前这里用 `get_customer_or_404` / `get_lead_or_404`（只判存在），
  成了"本人仅自己范围的业务员拿 id 就能把别人私有客户改给自己"的旁路
  （返工单 6.1）。审计记录改前改后的负责人与原因。
- `POST /leads/batch-assign`：批量分配线索。需要 `lead:assign`。
  逐条走与单条入口**同一套范围校验**，越权的那条按"无权分配"跳过并给出**与单条一致的 code（40302）**，
  且**不改动任何数据**。每条包在 SAVEPOINT 里，单条失败不会留下"历史写了、负责人没改"的半截状态。

---

# 42. 回收站（2026-10-07 第一版；2026-10-08 复审修 RB01–RB07；同日晚些加客户恢复；
# 2026-10-08 第三轮：客户恢复可换负责人＝一次改派（多一道分配权限）、SKU 删除人改为精确判定）

> 跨业务对象的"看被删的 + 捡回来"。三个分区集中在一个页面（线索 / 产品 / 客户）。
> **客户只有"直接删除"的能恢复**（2026-10-08 加）；**被合并掉的仍只做看** ——
> 只给"跳到合并后那个客户"的链接，不给恢复按钮（理由见 42.2）。
> 清单只回**被软删**的行；"谁看得见"沿用各模块自己的数据范围。
> **例外：客户分区的合并来源**要按合并前快照判、合并目标要单独鉴权（见 42.1）——
> 直接套"无主即公海"会把别人团队的客户放出去。

## 42.1 清单（都支持 `page` / `page_size`，真分页）

- `GET /recycle-bin/leads`：被删的线索。需要 `lead:view`。
  数据范围与线索列表**同一套**（含无主线索）：没有范围的人看不到别人范围内的已删线索。
- `GET /recycle-bin/products`：被删的产品。需要 `product:view`。
  每行带 `deleted_sku_count` —— 恢复这个产品时会一并捡回来的 SKU 条数。
- `GET /recycle-bin/skus`：被删的 SKU。需要 `product:view`。
  每行带 `product_deleted`（所属产品是否也在回收站里）与 `code_occupied`（编码是否被别人占着）。
- `GET /recycle-bin/customers`：被删的客户，**只读**。需要 `customer:view`。
  **"谁看得见"与客户列表口径不同**（2026-10-08 复审 RB01 修）：合并来源客户的
  `owner_id` 在合并时被清空了，照搬"无主即公海"会让它全公司可见。所以：
  - 合并来源按 `merge_snapshot.owner_id`（**合并前**的负责人）判范围，并据此显示原负责人；
  - 快照里没有负责人的老记录 → **只有数据范围 all 的管理员**能看到，该行 `owner_pending = true`
    （前端显示"待核实"，不能因为字段缺失就公开）；
  - 直接删除的客户没有合并留痕，仍按老口径（范围内 OR 无主）。
  - 权限过滤在**分页与总数之前**完成，保证看得见的条数与 `total` 一致。
- 客户行的负责人字段（2026-10-08 复审 RB05 修）—— **两对字段，别混着用**：
  - `owner_id` / `owner_name`：客户**当前**的负责人，成对下发。合并来源在合并那一刻就被
    清空了，所以对合并来源必然是 `(null, null)`。
  - `original_owner_id` / `original_owner_name`：**原**负责人，也是成对下发 —— 合并来源取
    `merge_snapshot.owner_id`，直接删除的取当时字段。回收站界面看的是这一对。
  - 从前只下发 `owner_name`（取快照）+ `owner_id`（取当前字段），两个字段说的不是同一件事，
    前端拿到 `(null, "张三")` 只能猜 —— 这是 RB05 改成两对的原因。
  - 快照里没留下负责人时 `original_owner_*` 都是 `null`，并置 `owner_pending = true`。
  - `original_owner_active`（2026-10-08 回收站复审第三轮加）—— **三态，别当布尔看**：
    `null` ＝ 本来就没有原负责人（客户在公海，直接恢复即可）；`true` ＝ 账号在岗；
    `false` ＝ **已停用或账号已不存在** → 这条客户**必须先指定一位在职的接手人**才能
    恢复，否则后端拿 `REQUIRED_FIELD_MISSING`(40003) 拒掉。界面据此提前把「已停用」
    标出来、并直接弹选人框，不必等用户点下去被拒才知道。
    判据与 `restore_customer` 里那道**同一口径**（`User.status == "active"`）。
- 客户行的去向字段（2026-10-08 复审 RB02/RB04 修）：
  - `merged_into`：**直接**合并目标，形如 `{id, name, visible, state}`。
  - `final_target`：多级合并（A→B→C）时的**最终**客户 C；与 `merged_into` 相同时为 `null`。
  - 目标客户**单独鉴权**：不在数据范围内时 `id` 与 `name` 都为 `null`（不下发，避免泄露），
    `state` = `forbidden`；目标已被并走/删除时为 `gone`；链条成环为 `loop`。
  - **链条超过 20 层（`_MERGE_CHAIN_MAX_HOPS`）时为 `truncated`**（2026-10-08 复审 RB03 修）：
    从前会把"停下来的那个中间客户"当成终点报出去，用户看到"目标已不存在"，而真正的
    最终客户还在。现在追满上限**不给 `id`**，前端显示「合并链过长，最终去向待核实」。
  - `id` 只在"在范围内且还活着"时才给 —— 前端据此决定是否给出可点入口，
    避免给一个点开就 404 的链接；`loop` / `truncated` 一律不给 `id`（那时终点还没确定）。
- **「谁删的」「怎么没的」**（2026-10-08 复审 RB06 新增）—— **四个清单都下发**：
  - `deleted_by_id` / `deleted_by_name`：删除操作人，取 `audit_logs` 里**最新一条**
    `action='delete'` 的留痕（按 `business_type` + `business_id` 精确对应）。
    **「删掉 → 恢复 → 再删」之后给的是本次那个人**，不是第一次那条旧留痕。
    留痕缺失（老数据 / 流水账被清理）时两者为 `null`、`deleted_by_pending = true`，
    前端显示「历史操作人待核实」—— **不许拿负责人顶替**：负责人说的是"这归谁管"，
    跟"谁删的"是两件事，混起来会让人找错人。
  - `removed_via`（**只有客户和 SKU 有**；线索和产品只可能是被人直接删的，不为它们单列）：
    - 客户：`direct`（有人直接删）/ `merged`（被合并移除，并进别人、自己消失）；
    - SKU：`direct`（有人单独删掉）/ `with_product`（跟着所属产品一起被删）。
  - **SKU 的 `with_product` 与 `deleted_by_*` 都出自 SKU 自己的删除留痕，而且必须
    "确认是同一次操作"**（2026-10-08 复审 RB07 修，同日第三轮收尾）。
    **两种死法都会各写一条 SKU 自己的留痕**：单独删写 `via=direct`、
    随产品删写 `via=product_delete`，并且都带上当时的 `deleted_at`。
    判"是不是本次"**只认留痕里钉下的那个 `deleted_at` 与当前值是否完全相等**：
    - 新留痕本来就记准了，**不再留"1 秒内"的容差** —— 留容差等于把判据放松，
      另一次删除只差 0.5 秒也会被认成本次（第三轮复审已复现）；
    - 老留痕只有审计时间、**证明不了它对应本次删除** → 一律「待核实」，
      **不再拿 60 秒、20 秒去贴**（本质还是猜，猜错的方向正是把旧操作人算到今天头上）。
    随产品删时 `deleted_by_*` 给的是**删产品的那个人**
    —— 正是"谁通过删产品把它带走的"。
    ⚠️ **拿不到自己的留痕、或留痕对不上本次，一律 `removed_via=null` + 待核实，不猜。**
    从前这里会拿"它所属产品的那条留痕"当候选、**挑与 `deleted_at` 时间最贴近的一条**，
    没有"同一次操作"这道判定 —— 于是一条**一个月前**就删掉、只是没留下自己删除记录的
    SKU，会被算到**今天删产品的那个人**头上，还标成"无需核实"。那是把两次毫不相干的
    操作硬拼在一起（RB07）。
  - 客户的**合并来源**，操作人取 `customer_merge_logs.operator_id`（**精确**记着"谁把这条
    并进了哪条"），不绕流水账 —— 流水账里那条 `merge` 审计的 `business_id` 是**目标**客户，
    从来源反查要绕一圈、还容易认错人。直接删除的才走流水账。
  - 这一列**不扩大任何可见范围**：清单本身的可见性一个字没改，只是给**已经看得见**的那一行
    补上"谁删的"。

## 42.2 恢复

- `POST /leads/{id}/restore`：线索（复用 #6 已有的那条）。需要 `lead:assign`。
- `POST /products/{id}/restore`：需要 `product:manage`。**连带恢复**该产品名下被删的 SKU。
  返回 `{restored_skus, skipped_skus}`；编码被占用的 SKU 单独跳过、在 `skipped_skus` 里给出原因，
  不让一次冲突把整批恢复搞崩。
- `POST /skus/{id}/restore`：需要 `product:manage`。**它所属的产品必须已经恢复** ——
  产品还在回收站时恢复 SKU 会造出"挂在已删产品下"的孤儿，直接 **400** 让你先恢复产品。
- `POST /customers/{id}/restore`（2026-10-08 加；同日第三轮补权限与改派）：
  恢复一个被**直接删除**的客户。
  需要 **`customer:delete`** —— 与删除**共用同一把钥匙**（谁删的谁能拾回来），
  不为它单开权限码；线索、产品那两个模块的恢复也是复用已有码。
  入参 `{owner_id?}` 整个可以不传（最常见的情形就是"归还原负责人"）。
  **但传了 `owner_id` 就不再是"捡回来"，而是一次改派** → **额外要求 `customer:assign`**
  （否则只有"删除客户"权限的人借恢复这个入口就能把客户交到别人名下，等于绕开了
  分配权限）。缺这项权限 → **403**，且**一个字段都不动**（权限先判、再取数）。

  **只认"直接删除"那一种。** 客户进回收站有两条路（见 42.1 的 `removed_via`）：
  - **直接删除**：`delete_customer` **只**把客户本人的 `deleted_at` 置上 ——
    名下联系人、商机、报价、订单、跟进**一条都没动**、也没改挂。所以恢复就是把
    标记去掉，那些东西**自动就都回来了**，不需要连带恢复任何东西；
  - **被合并移除**：名下关联对象**已经被改挂到目标客户**，这条只剩空壳 ——
    **不给恢复**（400，并指路到合并后那个客户）。恢复它只会得到一个空壳，
    还会把"它已经被并进某某了"这个事实盖掉（用户会以为数据丢了，其实数据在那边）。

  **归属**（第三轮改写）：
  - **不传 `owner_id`** ＝ 撤销删除：只去掉删除标记，负责人与名下单据**一个字不动**
    （原先在公海的仍留在公海）。原负责人已停用 / 账号已没时 → **40003 + 422**，
    文案是"请在恢复时指定一位新的负责人"；**调用方认这个错误码，别匹配提示文字**
    （本接口只有这一处会抛 40003）。
  - **传了 `owner_id`** ＝ 一次改派，**复用 `transfer_customer`**（人工转移 / 公海指派 /
    离职交接走的是同一个函数），**在同一笔事务里**一次办完：客户负责人、
    **客户归属历史**、**未完成待办**、名下单据（商机／打样／报价／订单草稿／订单
    以及它们生成的文件）、以及 **`pool_status` 同步设为 `private`**。
    从前只把负责人一改了事，于是接手人打开原报价/原订单是 **403**、客户下的待办是
    **空列表**、单据负责人还写着旧人、公海标记也不动（第三轮复审复现的那张表）。
    两条边界跟着改派走：**只搬原负责人名下的**（其他在职同事手里的活留着）、
    订单的**业绩归属**与各表的**历史创建人**一个字不碰。
    客户被删时若**本就在公海**，没有"原负责人"可依 → 按"公海把客户指派给某人"的
    同一套语义走（名下未完成待办全跟过去）。
  - 恢复审计（`action=restore`）照旧单独记一条，**与归属历史并存**（不是二选一）。
  - 没被删除的客户调这个接口 → 404（"客户不存在或没有被删除"）。
  - **不扩大可见范围**：取数走 `lock_deleted_customer`（行锁 + 数据范围校验）。

  ⚠️ **它名下的商机、报价不跟着"藏"。** 删客户只藏客户自己 ——
  名下那些单据照旧出现在各自的列表里（客户名也正常显示）。这是**刻意**的：
  商机/报价是独立业务对象、各有负责人与进度，不该因为客户被删就一起消失
  （2026-10-08 主人拍板）。所以"恢复客户"也就是把客户本身放回列表。

## 42.3 口径备注（写下来省得后人再踩）

- **SKU 编码在库里是全局唯一索引**（`ix_skus_code`，不排除已删行），所以删掉的 SKU
  一直占着那个编码：既不能用同码建新 SKU（会被拦成 409），**恢复时反而不会撞码**。
  `code_occupied` 与 `skipped_skus` 是**纵深防御**，当前不会触发；一旦索引改成
  "排除已删行"的部分索引，它们就是必要的。
- **恢复产品为什么是"整体恢复它名下所有被删的 SKU"**：删产品时 SKU 是一起软删的
  （避免孤儿）。界面上现在能靠 SKU 自己的删除留痕说清"这条是随产品删的、还是被人单独删的"
  （见 42.1 的 `removed_via`；判不出来时如实标"待核实"），但**恢复行为一个字没改** ——
  `restore_product` 仍是整体恢复。
  区分结果**只用于展示**（让人知道该去恢复产品、而不是对着那条 SKU 点恢复）。不改成
  "只恢复随产品删的那些"：那会把"产品被删之前就已单独删掉的 SKU"一直留在回收站，
  与"恢复产品＝把原来那套 SKU 拿回来"的直觉不符。
- **`audit_logs` 需要 `(business_type, business_id, id DESC)` 索引**（迁移 `a7c1e5b9d3f2`）：
  回收站要给整页（最多 200 行 × 四类对象）补"谁删的"，没有这条索引就是全表扫描 ——
  而这张表是全项目长得最快的一张（每次写操作都记一条，本机开发库已 3.7 万行）。
  `id DESC` 是为了配合"取最新一条"的写法，顺着索引走、不必再排序。
- **客户软删只有两个来源**：`DELETE /customers/{id}`（直接删）与合并（来源客户被置删）。
  合并那条**同时把负责人清空了、并置 `pool_status=public`**，所以它"看起来"像公海客户 ——
  但它实际属于合并**前**的那位同事。这正是回收站客户清单**不能**直接套
  `apply_data_scope`（无主即放行）的原因，改按合并前快照判（见 42.1）。
  直接删的客户不写这条留痕，仍走老口径。
- **产品的四道写入口共用一条锁序「先产品、后 SKU」**（2026-10-08 复审 RB03 修）：
  删除产品 / 恢复产品 / 恢复 SKU / 新增 SKU 都先 `lock_product`（行锁 + `populate_existing`）
  再动 SKU。反例：从前恢复 SKU 只锁 SKU、读产品不加锁，于是"删产品"与"恢复 SKU"
  并发时各看各的旧世界 —— 产品删掉了、SKU 却被恢复成有效，留下挂在已删产品下的孤儿
  （新增 SKU 同理）。**不许反过来"先锁 SKU 再拿产品锁"**：那与"恢复产品"方向相反，会死锁。




