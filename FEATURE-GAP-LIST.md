# CRM-Sales-Agent 功能差距清单（Definitive Feature Gap List）

> 生成方式：逐行通读 `01-PRD-V1.1`、`02-ER-V1.1`、`03-API-V1.1` 全文，与 `backend/app/**`（全部 `main.py` / `modules/*/router.py` / `model.py` / `service.py` / `scripts/seed.py` / `alembic/versions/`）与 `frontend/src/**`（`app/router.tsx`、`app/menu.ts`、`modules/**`、`shared/api/**`）逐项比对。**未修改任何源码文件。**
>
> 口径：文档=需求（source of truth）；代码=现状（ground truth）。每条给出文档出处 + 代码 `文件:行` 证据，或注明 `not found in code`。
>
> 代码规模基线（本次实测）：`__tablename__` 共 51 处（含 `user_roles` / `role_permissions` 两张关联表）——项目交接文档 `10-项目现状与交接说明.md:16,255` 写的"52 张"含 alembic 的 `alembic_version` 版本表；路由装饰器 190 个；前端页面组件 20 个（`app/router.tsx:5-24` 的 import 数）；实际可交互页面 18 个 + 2 个占位 div（`app/router.tsx:70-93`）；Agent 工具 12 个。

---

## 0. 优先级总览（按"是否被文档强制 + 是否卡断端到端链路 + 工作量"排序）

图例：**必需性** `P0=PRD/ER/API 明文要求且卡端到端链路` / `P1=明文要求但不卡主链路` / `P2=明文要求、锦上添花`；**工作量** `S≈≤1人日` / `M≈2-5人日` / `L≈>1周`。

| # | 事项 | 必需性 | 卡链路 | 工作量 | 归属章节 |
|---|---|---|---|---|---|
| 1 | 企业微信集成整体缺失（10 张表里的 4 张 + 13 个接口 + 全部页面） | P0 | 卡（PRD §26 链路起点 `ExternalContact`） | L | §1.10 §2 §3.3 |
| 2 | 客户标签体系（`tags`/`customer_tags` 表 + 6 个接口 + 列表筛选/批量打标） | P0 | 卡（PRD §6.1 客户列表"标签"列） | M | §1.7 §2 §3.3 |
| 3 | 客户归一：合并（`customer_merge_logs` + `POST /customers/merge`） | P0 | 卡（PRD §6.3、§26） | M | §1.7 §2 §3.3 |
| 4 | 物流试算模块（ER `logistics_quotes` 表 + API §19 六个接口 + 费用/时效/计费重/方案列表） | P0 | 卡（PRD §26 `Pricing + Logistics`） | L | §1.19 §2 §3.1 |
| 5 | 核价引擎输入项缺失 5 项（国家/包装/物流方式/付款方式/利润要求），输出语义偏差（最低价=授权底线而非保护价） | P0 | 卡（PRD §13 明文输入输出） | M | §3.8 §4.10 |
| 6 | 编号规则配置（`/numbering-rules` 三接口 + PRD §2.6 系统管理员能力）；当前单号硬编码 `Q{YYYYMMDD}{seq}` / `SO{YYYYMMDD}{seq}` | P1 | 不卡 | M | §1.36 §3.9 §4.11 |
| 7 | 报价多方案对比 `GET /quotes/{id}/version-comparison` + 前端对比视图 | P1 | 不卡 | M | §1.20 §3.10 |
| 8 | 商机 Kanban 看板（前端）+ 商机 `assign` / `clone` 接口 | P1 | 不卡 | M | §1.11 §3.10 |
| 9 | 批量操作（客户批量转移/打标、线索批量分配/导入导出、任务批量完成） | P1 | 不卡 | M | §1.6-1.7 §1.25 §3.11 |
| 10 | 审批动作完整性：转交 `/transfer`、独立撤回 `/withdraw`、审批定义 CRUD | P1 | 部分卡（转交是 PRD §16 明文支持项） | S-M | §1.23 §3.12 |
| 11 | 通知的企业微信渠道 + 通知设置（`/notification-settings`） | P1 | 不卡 | M | §1.32 §3.13 |
| 12 | Excel 导入导出覆盖范围（仅客户有；线索/产品/SKU 全缺，且只支持 CSV 非 xlsx） | P1 | 不卡 | M | §1.6/1.14/1.15 §3.14 |
| 13 | 客户列表高级筛选字段（国家/来源/最近跟进/下次跟进/商机数/累计报价/累计成交/待回款） | P1 | 不卡 | M | §3.15 |
| 14 | 样品模块（PRD §19 可选；ER §13 三表 + API §26 十接口） | P2 | 不卡 | M | §1.26 §2 §3.2 |
| 15 | 产品标签、产品图片/附件（`product_files` 表 + 3 接口） | P2 | 不卡 | S-M | §1.14 §2 |
| 16 | ERP/MES 集成（API §28 六接口 + 真实推送 + `refresh-status`） | P2（外部依赖） | 卡（PRD §26 末端） | L | §1.28 §3.16 |
| 17 | 数据分析缺口（`/analytics/leads`、`/analytics/pricing`、`/analytics/payments` 三接口 + 客户活跃/沉睡/复购等细分） | P1 | 不卡 | M | §1.35 §3.17 |
| 18 | Agent 专用七接口 + 工具集补齐 17 项（当前 12 项）+ 流式 / cancel / retry / 详情 | P1 | 不卡 | M-L | §1.37 §3.19 |
| 19 | 用户/部门/角色/权限写操作（API §3 §4 §5 共 20 个写接口，当前 3 个只读接口） | P1 | 不卡 | M | §1.3-1.5 §3.18 |
| 20 | 审计日志详情 + 业务对象审计日志（§33 后两个接口） | P2 | 不卡 | S | §1.33 |

---

## 1. MISSING API ENDPOINTS（文档要求但未实现的接口）

> 全部按 `03-API` 章节顺序。写法：`方法 路径` — 代码证据或 `not found in code`；存在等价实现时同时给出**现有路径**。

### 1.1 Auth（`03-API §2`）
| 文档接口 | 结论 | 代码证据 |
|---|---|---|
| `POST /auth/login` | 已实现 | `backend/app/modules/auth/router.py:19` |
| `POST /auth/logout` | 已实现（占位，无 token 失效） | `auth/router.py:45` |
| `POST /auth/refresh` | **缺失** | not found in code（`grep "refresh"` 无命中；`access_token_expire_minutes=720` 一次性 token，`core/config.py:28`） |
| `GET /auth/me` | 已实现 | `auth/router.py:51` |
| `GET /auth/permissions` | 已实现 | `auth/router.py:73` |
| `POST /auth/sso/wecom/callback` | **缺失** | not found in code（无任何 SSO/企微登录入口） |

### 1.2 User（`03-API §3`）
| 文档接口 | 结论 | 代码证据 |
|---|---|---|
| `GET /users` | 已实现 | `modules/user/router.py:18` |
| `POST /users` | **缺失** | not found in code（`user/router.py` 文件头注释明说"这一版只提供查询…留到 Phase 5"，`:3`） |
| `GET /users/{id}` | **缺失** | not found in code |
| `PATCH /users/{id}` | **缺失** | not found in code |
| `POST /users/{id}/enable` | **缺失** | not found in code |
| `POST /users/{id}/disable` | **缺失** | not found in code |
| `GET /users/{id}/roles` | **缺失** | not found in code（角色只能靠 `GET /users` 列表 + `GET /roles` 自行拼） |
| `PUT /users/{id}/roles` | **缺失** | not found in code（数据须靠 `scripts/seed.py:207-220` 直接写库） |
| `GET /users/{id}/data-scope` | **缺失** | not found in code（`Roles.data_scope` 只在 `GET /roles` 返回，`user/router.py:88`） |

### 1.3 Department（`03-API §4`）
`GET /departments` 已实现（`user/router.py:55`）。其余 **全部缺失**，not found in code：`GET /departments/tree`、`POST /departments`、`GET /departments/{id}`、`PATCH /departments/{id}`、`DELETE /departments/{id}`、`GET /departments/{id}/users`。

### 1.4 Role / Permission（`03-API §5`）
`GET /roles` 已实现（`user/router.py:74`）。**缺失**：`POST /roles`、`GET /roles/{id}`、`PATCH /roles/{id}`、`DELETE /roles/{id}`、`GET /permissions`、`GET /roles/{id}/permissions`、`PUT /roles/{id}/permissions`、`GET /roles/{id}/data-scope`、`PUT /roles/{id}/data-scope` — 全部 not found in code（`Permission` 表存在但无任何 HTTP 出口，`user/model.py:48-54`）。

### 1.5 Lead（`03-API §6`）
| 文档接口 | 结论 | 代码证据 |
|---|---|---|
| `GET/POST /leads` | 已实现 | `lead/router.py:21,48` |
| `GET/PATCH /leads/{id}` | 已实现 | `lead/router.py:76,87` |
| `DELETE /leads/{id}` | **缺失** | not found in code（只有 `POST /leads/{id}/discard` 软删，`lead/router.py:188`） |
| `POST /leads/{id}/assign` | 已实现 | `lead/router.py:114` |
| `POST /leads/{id}/claim` | 已实现 | `lead/router.py:140` |
| `POST /leads/{id}/release` | 已实现 | `lead/router.py:165` |
| `POST /leads/{id}/discard` | 已实现 | `lead/router.py:188` |
| `POST /leads/{id}/restore` | **缺失** | not found in code（`discard` 会写 `deleted_at`（`lead/service.py:143`），无回滚入口） |
| `POST /leads/{id}/deduplicate` | 已实现 | `lead/router.py:211` |
| `POST /leads/{id}/convert` | 已实现 | `lead/router.py:227` |
| `GET /leads/{id}/timeline` | 已实现（路径一致） | `timeline/router.py:45` |
| `POST /leads/import` | **缺失** | not found in code（`customer/io_router.py` 只有客户导入，`:66`） |
| `POST /leads/export` | **缺失** | not found in code |
| `POST /leads/batch-assign` | **缺失**（有等价：逐个调 `POST /leads/{id}/assign`，`lead/router.py:114`） | not found in code |

### 1.6 Customer（`03-API §7`）
已实现：`GET/POST /customers`（`customer/router.py:27,63`）、`GET/PATCH/DELETE /customers/{id}`（`:85,103,130`）、`GET /customers/{id}/contacts`（`:225`）、`POST /customers/{id}/transfer`（`:153`）、`POST /customers/{id}/release-to-pool`（`:179`）、`POST /customers/{id}/claim`（`:200`）、`POST /customers/import`（`customer/io_router.py:66`）、`GET /customers/export`（`io_router.py:35`，**方法为 GET，文档写 POST**）。

**缺失**：
- `GET /customers/{id}/overview` — not found in code；**等价**：`GET /customers/{id}`（`customer/router.py:85`）+ `GET /customers/{id}/contacts`（`:225`）前端自行拼装（`modules/customer/CustomerDetailPage.tsx:64-74`）。
- `GET /customers/{id}/opportunities` — not found in code；**等价**：`GET /opportunities?customer_id=`（`opportunity/router.py:94,100`），前端即如此用（`CustomerDetailPage.tsx:132`）。
- `GET /customers/{id}/quotes` — not found in code；**等价**：`GET /quotes?customer_id=`（`quote/router.py:88,107`），前端 `CustomerDetailPage.tsx:148`。
- `GET /customers/{id}/orders` — not found in code；**等价**：`GET /orders?customer_id=`（`order/router.py:29,44`），前端 `CustomerDetailPage.tsx:153`。
- `GET /customers/{id}/followups` — not found in code；**等价**：`GET /followups?customer_id=`（`followup/router.py:44,56`），前端 `CustomerDetailPage.tsx:137`。
- `GET /customers/{id}/tasks` — **缺失**（等价：`GET /tasks?customer_id=`，`task/router.py:55,81`）。
- `GET /customers/{id}/timeline` — 已实现（`timeline/router.py:25`）。
- `GET /customers/{id}/files` — not found in code；**等价**：`GET /business/customer/{id}/files`（`file/router.py:147`）。
- `POST /customers/{id}/assign` — **缺失**（等价：`POST /customers/{id}/transfer`，`customer/router.py:153`）。
- `POST /customers/{id}/tags` — **缺失**；not found in code（无 tags 表，见 §2）。
- `DELETE /customers/{id}/tags/{tag_id}` — **缺失**；not found in code。
- `POST /customers/deduplicate` — **缺失**（仅有线索维度 `POST /leads/{id}/deduplicate`，`lead/router.py:211`；客户维度查重函数存在但无 HTTP 出口：`modules/contact_util.py:48 find_duplicate_customers`）。
- `POST /customers/merge` — **缺失**；not found in code（无合并逻辑、无 `customer_merge_logs`）。
- `POST /customers/batch-transfer` — **缺失**；not found in code。
- `POST /customers/batch-tag` — **缺失**；not found in code。
- `POST /customers/import` 已实现但**口径不一致**：文档未要求模板接口，代码多出 `GET /customers/import-template`（`io_router.py:19`，属扩展）。

### 1.7 Contact（`03-API §8`）
已实现：`POST /customers/{id}/contacts`（`customer/router.py:241`，**路径不同**：文档为 `GET/POST /contacts`）、`PATCH /contacts/{id}`（`:273`）、`DELETE /contacts/{id}`（`:302`）。

**缺失**：
- `GET /contacts` — **缺失**（无全局联系人列表；只有 `GET /customers/{id}/contacts`，`:225`）。
- `POST /contacts` — not found in code；**等价**：`POST /customers/{id}/contacts`（`:241`）。
- `GET /contacts/{id}` — **缺失**；not found in code。
- `POST /contacts/{id}/bind-customer` — **缺失**；not found in code。
- `POST /contacts/{id}/change-customer` — **缺失**；not found in code（PRD §7 明文"联系人必须允许变更所属客户"）。
- `POST /contacts/{id}/set-primary` — **缺失**；not found in code（只能在 `PATCH /contacts/{id}` 里传 `is_primary=true` 间接实现，`customer/router.py:286` + `customer/service.py:197`）。
- `GET /contacts/{id}/timeline` — **缺失**（timeline 仅支持 customer/opportunity/lead，`timeline/router.py:25,35,45`）。
- `GET /contacts/{id}/wecom` — **缺失**；not found in code（无企微数据）。
- `POST /contacts/deduplicate` — **缺失**；not found in code（PRD §5.4 转化时要求"联系人查重"，`contact_util.create_contact_for_customer` 不做查重，`contact_util.py:10-45`）。

### 1.8 Public Pool（`03-API §9`）
- `GET /public-pool/customers` — not found in code；**等价**：`GET /customers?pool_status=public`（`customer/router.py:27,34`）。
- `GET /public-pool/leads` — not found in code；**等价**：`GET /leads?unassigned=true`（`lead/router.py:21,27`）。
- `POST /public-pool/customers/{id}/claim` — not found in code；**等价**：`POST /customers/{id}/claim`（`customer/router.py:200`）。
- `POST /public-pool/leads/{id}/claim` — not found in code；**等价**：`POST /leads/{id}/claim`（`lead/router.py:140`）。
- `POST /public-pool/customers/{id}/assign` — **缺失**（等价：`POST /customers/{id}/transfer`）。
- `POST /public-pool/leads/{id}/assign` — **缺失**（等价：`POST /leads/{id}/assign`）。
- `GET /public-pool/rules` | `POST /public-pool/rules` | `PATCH /public-pool/rules/{id}` — 已实现（`settings/router.py:114,125,138`）。
- 代码额外提供 `POST /public-pool/run-recycle`（`settings/router.py:154`，文档未要求，属扩展）。

### 1.9 WeCom Integration（`03-API §10`）— **整段缺失**
以下 13 项全部 `not found in code`（`backend/app/modules/integration/` 只有 `model.py`，无 `router.py`、无 HTTP 客户端；`main.py:9-30` 未注册任何 integration 路由）：
`POST /integrations/wecom/sync-departments`、`POST /integrations/wecom/sync-users`、`POST /integrations/wecom/sync-external-contacts`、`POST /integrations/wecom/sync-follow-relations`、`GET /integrations/wecom/unbound-contacts`、`GET /integrations/wecom/unbound-contacts/{id}/candidates`、`POST /integrations/wecom/unbound-contacts/{id}/bind-customer`、`POST /integrations/wecom/unbound-contacts/{id}/create-customer`、`POST /integrations/wecom/transfer`、`GET /integrations/wecom/transfer/{job_id}`、`GET /integrations/wecom/sync-jobs`、`GET /integrations/wecom/sync-jobs/{id}`、`POST /webhooks/wecom/events`。

### 1.10 Opportunity（`03-API §11`）
已实现：`GET/POST /opportunities`（`opportunity/router.py:94,132`）、`GET/PATCH/DELETE /opportunities/{id}`（`:189,214,412`）、`GET /opportunities/{id}/stage-history`（`:381`）、`POST /opportunities/{id}/change-stage`（`:241`）、`POST /opportunities/{id}/win`（`:282`）、`POST /opportunities/{id}/lose`（`:316`）、`POST /opportunities/{id}/reopen`（`:352`）、`GET /opportunities/funnel`（`:57`）。

**缺失**：`GET /opportunities/{id}/overview`（等价 `GET /opportunities/{id}`，`:189`）、`GET /opportunities/{id}/timeline`（已实现，`timeline/router.py:35`）、`POST /opportunities/{id}/assign`、`POST /opportunities/{id}/clone` — 后两个 not found in code。

### 1.11 OpportunityItem（`03-API §12`）
已实现：`GET /opportunities/{id}/items`（`opportunity/router.py:437`）、`POST /opportunities/{id}/items`（`:447`）、`PATCH/DELETE /opportunity-items/{id}`（`:472,499`）。
**缺失**：`POST /opportunities/{id}/items/batch`（批量添加 SKU，PRD §10 明文）、`POST /opportunities/{id}/items/copy-from/{source_opportunity_id}`（复制商机需求，PRD §10）、`POST /opportunities/{id}/recommend-products`（AI 推荐商品，PRD §10）— 全部 not found in code。

### 1.12 Opportunity Stage / Loss Reason（`03-API §13`）
已实现：`GET /opportunity-stages`（`opportunity/router.py:38`）、`GET /loss-reasons`（`:49`）。
**缺失**：`POST /opportunity-stages`、`PATCH /opportunity-stages/{id}`、`DELETE /opportunity-stages/{id}`、`POST /opportunity-stages/reorder`、`POST /loss-reasons`、`PATCH /loss-reasons/{id}` — 全部 not found in code（阶段/失单原因只能靠 `scripts/seed.py:434-482` 维护）。

### 1.13 Product（`03-API §14`）
已实现：`GET/POST /products`（`product/router.py:31,48`）、`GET/PATCH/DELETE /products/{id}`（`:71,82,109`）、`GET /products/{id}/skus`（`:134`）。
**缺失**：`GET /products/{id}/files`、`POST /products/{id}/files`、`GET /products/{id}/knowledge`（知识以 `knowledge` 字段随产品返回，无独立接口：`product/model.py:26`、`product/service.py:25`）、`POST /products/import`、`POST /products/export` — 全部 not found in code。
（代码额外有 `POST /products/{id}/skus`，`:150`，文档未列。）

### 1.14 SKU（`03-API §15`）
已实现：`GET /skus`（`product/router.py:176`）、`PATCH /skus/{id}`（`:209`）、`POST /skus/{id}/enable|disable`（`:239,254`）、`DELETE /skus/{id}`（`:269`）。**注意 `POST /skus` 不存在**，只能 `POST /products/{id}/skus`（`:150`）。
**缺失**：`POST /skus`、`GET /skus/{id}`、`GET /skus/{id}/costs`（已实现，见 §16）、`POST /skus/import`、`POST /skus/export` — 除 costs 外均 not found in code。

### 1.15 Cost（`03-API §16`）
已实现：`GET /skus/{sku_id}/costs`（`pricing/router.py:49`）、`POST /skus/{sku_id}/costs`（`:68`）、`PATCH /costs/{id}`（`:95`）、`POST /costs/{id}/expire`（`:124`）。
**缺失**：`GET /skus/{sku_id}/cost-history`（not found in code；`GET /skus/{id}/costs` 返回全部历史，可算等价但语义不同）、`GET /costs/{id}`（not found in code）。

### 1.16 Price Rule（`03-API §17`）
已实现：`GET/POST /price-rules`（`pricing/router.py:149,170`）、`PATCH/DELETE /price-rules/{id}`（`:196,225`）、`GET/POST /customer-price-rules`（`:250,279`）、`DELETE /customer-price-rules/{id}`（`:302`）、`GET /price-permissions`（`:327`）、`PUT /price-permissions/{role_id}`（`:347`，**方法/路径与文档不同**：文档为 `POST /price-permissions` + `PATCH /price-permissions/{id}`）、`GET/POST /exchange-rates` 中 **两者都缺**。
**缺失**：`PATCH /customer-price-rules/{id}`（not found in code）、`PATCH /price-permissions/{id}`（not found in code）、`GET /exchange-rates`（not found in code）、`POST /exchange-rates`（not found in code — 汇率表 `pricing/model.py:125` 存在但**无任何 HTTP 出口**，报价时汇率只能手工传参）。

### 1.17 Pricing（`03-API §18`）
已实现：`POST /pricing/calculate`（`pricing/router.py:422`）、`POST /pricing/batch-calculate`（`:445`）。
**缺失**：`POST /pricing/check-permission`（not found in code；等价物是 `calculate_price` 返回值里的 `approval_required`/`can_approve`/`authorized_min_margin`，`pricing/service.py:429-443`）、`POST /pricing/simulate`（not found in code）、`GET /pricing/history`（not found in code — **无任何核价历史表**，核价结果不落库）。
（代码额外：`GET /skus/{sku_id}/price-summary`、`GET /pricing/sku-options`，`:471,480`。）

### 1.18 Logistics（`03-API §19`）— **除费率表外整段缺失**
- `GET /logistics/providers` — not found in code
- `GET /logistics/routes` — not found in code
- `POST /logistics/calculate` — **not found in code**（PRD §14 物流试算整节缺失）
- `POST /logistics/compare` — not found in code
- `GET /logistics/quotes` — not found in code
- `GET /logistics/quotes/{id}` — not found in code
- 现有的只是费率维护：`GET /logistics/rates`（`pricing/router.py:386`）、`POST /logistics/rates`（`:397`），且核价时**只取费率表第一条**（`pricing/service.py:225-229`：`.order_by(LogisticsRate.id.asc()).limit(1)`），不按目的地/运输方式匹配。

### 1.19 Quote（`03-API §20`）
已实现：`GET/POST /quotes`（`quote/router.py:88,132`）、`GET /quotes/{id}`（`:220`）、`GET/POST /quotes/{id}/versions`（`:239,256`）。
**缺失**：`PATCH /quotes/{id}`（not found in code）、`DELETE /quotes/{id}`（not found in code — `Quote.deleted_at` 字段存在但无接口，`quote/model.py:47`）、`GET /quotes/{id}/timeline`（not found in code；timeline 模块不支持 quote，`timeline/router.py`）、`GET /quotes/{id}/version-comparison`（**not found in code** — PRD §15.2 多版本对比无落点）、`GET /quotes/{id}/send-logs`（not found in code；**等价**：`GET /quote-versions/{id}/send-logs`，`quote/router.py:720`）、`GET /quotes/{id}/approval-history`（not found in code；**等价**：`GET /quote-versions/{id}` 返回内嵌 `approval.records`，`quote/router.py:344,381-394`）、`POST /quotes/{id}/clone`（not found in code）。

### 1.20 QuoteVersion（`03-API §21`）
已实现：`GET /quote-versions/{id}`（`quote/router.py:344`）、`PATCH /quote-versions/{id}`（`:399`）、`POST /quote-versions/{id}/submit-approval`（`:601`）、`POST /quote-versions/{id}/withdraw-approval`（`:651`）、`POST /quote-versions/{id}/mark-sent`（`:683`）、`POST /quote-versions/{id}/accept`（`:747`）、`POST /quote-versions/{id}/reject`（`:772`）、`POST /quote-versions/{id}/convert-to-order`（**已实现但在 order 模块**：`order/router.py:67`）。
**缺失**：`POST /quote-versions/{id}/copy`（not found in code；**等价**：`POST /quotes/{id}/versions` 即"复制上一版"，`quote/router.py:256`）、`POST /quote-versions/{id}/recalculate`（not found in code；`recalc_version` 仅在写操作内部隐式调用，`quote/service.py:216`，无独立接口）、`POST /quote-versions/{id}/generate-pdf`（**路径不一致**：实现为 `GET /quote-versions/{id}/pdf`，`quote/router.py:797`）、`POST /quote-versions/{id}/send-email`（**not found in code** — 邮件发送完全没做，只有 `mark-sent` 手工标记，`:683`）、`POST /quote-versions/{id}/expire`（**not found in code** — `"expired"` 状态在 `QUOTE_STATUS_LABEL` 有定义（`quote/model.py:27`）但全库无任何位置写入该状态，`grep '"expired"'` 仅命中字典定义）。

### 1.21 QuoteItem / Charge（`03-API §22`）
已实现：`POST /quote-versions/{id}/items/batch`（`quote/router.py:429`）、`PATCH/DELETE /quote-items/{id}`（`:477,542`）、`POST /quote-versions/{id}/charges`（`:560`）、`DELETE /quote-charges/{id}`（`:583`）。
**缺失**：`GET /quote-versions/{id}/items`（not found in code；**等价**：`GET /quote-versions/{id}` 返回 `items`/`charges`，`:344,376-380`）、`POST /quote-versions/{id}/items`（单条新增，not found in code；只有整版替换的 batch）、`GET /quote-versions/{id}/charges`（同上前者等价）、`PATCH /quote-charges/{id}`（**not found in code** — 附加费用只能删了重加）。

### 1.22 Approval（`03-API §23`）
已实现：`GET /approvals`（`approval/router.py:87`）、`GET /approvals/{id}`（`:106`）、`POST /approvals/{id}/approve`（`:168`）、`POST /approvals/{id}/reject`（`:219`）、`GET /approvals/{id}/records`（`:271`）。
**缺失**：`POST /approvals/{id}/transfer`（**not found in code** — PRD §16 明文要求"转交"；`ApprovalRecord.action` 注释里列了 transfer 但无人写入，`approval/model.py:48`）、`POST /approvals/{id}/withdraw`（not found in code；**等价**：`POST /quote-versions/{id}/withdraw-approval`，`quote/router.py:651`）、`GET /approval-definitions`、`POST /approval-definitions`、`PATCH /approval-definitions/{id}`（全部 not found in code；`ApprovalDefinition` 只在提交审批时自动补一条，`quote/service.py:316-329`）。

### 1.23 FollowUp（`03-API §24`）
已实现：`GET/POST /followups`（`followup/router.py:44,78`）、`PATCH/DELETE /followups/{id}`（`:144,173`）、`GET /opportunities/{id}/followups`（`:196`）。
**缺失**：`GET /followups/{id}`（not found in code）、`GET /contacts/{id}/followups`（not found in code；**等价**：`GET /followups` 不支持 contact_id 过滤，`followup/router.py:45-51` 只有 customer/opportunity/lead/owner 四个过滤参数——**这是真实缺口**）、`GET /quotes/{id}/followups`（not found in code）、`POST /followups/{id}/create-next-task`（not found in code；**等价**：`POST /followups` 支持 `create_task`+`task_due_at` 一次创建，`followup/schema.py:18-20`、`followup/router.py:111-126`）。

### 1.24 Task（`03-API §25`）
已实现：`GET/POST /tasks`（`task/router.py:55,97`）、`PATCH /tasks/{id}`（`:132`）、`POST /tasks/{id}/complete|cancel|postpone|transfer`（`:161,190,213,240`）、`GET/POST /task-rules`（`settings/router.py:164,173`）、`PATCH /task-rules/{id}`（`:186`）。
**缺失**：`GET /tasks/{id}`（not found in code）、`POST /tasks/{id}/assign`（not found in code；**等价**：创建时传 `owner_id`，`task/router.py:105`）、`POST /tasks/batch-complete`（**not found in code** — PRD §18 明文"批量完成"）、`DELETE /task-rules/{id}`（not found in code）。

### 1.25 Sample（`03-API §26`，PRD §19 为"若启用…可选"）
`GET /samples`、`POST /samples`、`GET /samples/{id}`、`PATCH /samples/{id}`、`POST /samples/{id}/approve`、`POST /samples/{id}/ship`、`POST /samples/{id}/sign`、`POST /samples/{id}/feedback`、`GET /samples/{id}/items`、`POST /samples/{id}/items` — **10 项全部 not found in code**；无样品表（§2）、无页面、无路由。

### 1.26 Order（`03-API §27`）
已实现：`GET /orders`（`order/router.py:29`）、`GET /orders/{id}`（`:98`）、`PATCH /orders/{id}`（`:117`）、`GET /orders/{id}/items`（`:131`）、`GET /orders/{id}/status-history`（`:149`）、`POST /orders/{id}/cancel`（`:206`）、`POST /orders/{id}/sync-erp`（`:218`，**空壳**，见 §4）、`GET /orders/{id}/receivables`（`:303`）、`GET /orders/{id}/payments`（`:322`）。
**缺失**：`POST /orders`（**not found in code** — 订单只能由报价版本转（`:67`），无法手工建单）、`POST /orders/{id}/refresh-status`（not found in code）。
（代码额外：`POST /orders/{id}/status`（`:181`）、`POST /orders/{id}/repurchase`（`:252`）、`GET /orders/{id}/finance-summary`（`payment/router.py:291`），文档未列。）

### 1.27 ERP/MES Integration（`03-API §28`）— **整段缺失**
`POST /integrations/erp/orders`、`POST /integrations/erp/orders/{id}/sync`、`GET /integrations/erp/orders/{id}/status`、`GET /integrations/erp/sync-logs`、`POST /webhooks/erp/order-status`、`POST /webhooks/erp/shipment-status` — 全部 not found in code。最近的等价物是 `POST /orders/{id}/sync-erp`（`order/router.py:218`），但它只写一条 `IntegrationLog(status="skipped")` 并返回 `pushed: False`（`:230-249`），**不写 `erp_order_id`、不建 `ExternalMapping`**（虽然 `order/service.py:214 record_external_order_id` 已写好，但**全库无调用点**）。

### 1.28 Receivable（`03-API §29`）
已实现：`GET /receivables`（`payment/router.py:30`）、`POST /orders/{order_id}/receivables`（`:49`，**路径不同**，文档为 `POST /receivables`）、`PATCH/DELETE /receivables/{id}`（`:111,128`）、`POST /receivables/{id}/mark-overdue`（`:147`）、`GET /orders/{id}/receivables`（`order/router.py:303`）。
**缺失**：`POST /receivables`（not found in code；等价见上）、`GET /receivables/{id}`（not found in code）。
（代码额外：`POST /orders/{id}/receivables/generate`，`payment/router.py:72`。）

### 1.29 Payment（`03-API §30`）
已实现：`GET/POST /payments`（`payment/router.py:159,178`）、`POST /payments/{id}/confirm|reject`（`:219,260`）、`GET /orders/{id}/payments`（`order/router.py:322`）、`GET /receivables/{id}/payments`（`payment/router.py:275`）。
**缺失**：`GET /payments/{id}`（not found in code）、`PATCH /payments/{id}`（not found in code）。

### 1.30 File（`03-API §31`）
全部已实现：`POST /files/upload`（`file/router.py:37`）、`GET /files/{id}`（`:82`）、`GET /files/{id}/download`（`:94`）、`DELETE /files/{id}`（`:117`）、`GET/POST /business/{type}/{id}/files`（`:147,179`）、`DELETE /business-files/{id}`（`:203`）。
→ **文件模块唯一缺口是"预览"**（PRD §25 明文要求"预览"）：无 `preview` 接口/前端预览组件，只有下载（`frontend/src/shared/api/file.ts:40`）。

### 1.31 Notification（`03-API §32`）
已实现：`GET /notifications`（`notification/router.py:30`）、`GET /notifications/unread-count`（`:45`）、`POST /notifications/{id}/read`（`:60`）、`POST /notifications/read-all`（`:75`）。
**缺失**：`GET /notification-settings`、`PATCH /notification-settings` — not found in code（无通知渠道/开关配置，PRD §25 要求企微通知渠道）。

### 1.32 Audit（`03-API §33`）
已实现：`GET /audit-logs`（`audit_router.py:18`）。
**缺失**：`GET /audit-logs/{id}`（not found in code）、`GET /business/{type}/{id}/audit-logs`（not found in code）。

### 1.33 Timeline（`03-API §34`）
已实现：`GET /leads/{id}/timeline`（`timeline/router.py:45`）、`GET /customers/{id}/timeline`（`:25`）、`GET /opportunities/{id}/timeline`（`:35`）。
**缺失**：`GET /contacts/{id}/timeline`、`GET /quotes/{id}/timeline`、`GET /orders/{id}/timeline` — not found in code（`timeline/service.py` 的 `build_timeline` 只接受 `customer|opportunity|lead` 三类）。

### 1.34 Dashboard / Analytics（`03-API §35`）
已实现：`GET /dashboard/summary`（`analytics/router.py:15`）、`GET /dashboard/tasks`（`:23`）、`GET /dashboard/risks`（`:31`）、`GET /analytics/customers`（`:73`）、`GET /analytics/opportunities`（`:57`）、`GET /analytics/funnel`（`:113`）、`GET /analytics/quotes`（`:65`）、`GET /analytics/products`（`:81`）、`GET /analytics/sales-users`（`:89`）、`GET /analytics/losses`（`:105`）、`GET /analytics/receivables`（`:97`）。
**缺失**：`GET /analytics/leads`（not found in code — 线索漏斗/来源分析无接口）、`GET /analytics/pricing`（not found in code — 价格分析/让价分析无接口）、`GET /analytics/payments`（not found in code；**部分等价**：`GET /analytics/receivables` 返回 `received_amount`，`analytics/service.py:642-650`）。
（代码额外：`GET /dashboard/trend`、`GET /dashboard/activities`，`:39,48`。）

### 1.35 System Config（`03-API §36`）
已实现：`GET/PATCH /settings`（`settings/router.py:72,83`）。
**缺失**：`GET /dictionaries`、`POST /dictionaries`、`PATCH /dictionaries/{id}`、`GET /numbering-rules`、`POST /numbering-rules`、`PATCH /numbering-rules/{id}`、`GET /customer-levels`、`POST /customer-levels`、`PATCH /customer-levels/{id}` — **9 项全部 not found in code**（PRD §2.6 明确把"字典/流程配置/编号规则"列为系统管理员能力；`settings/model.py:1-6` 注释承认"文档里有 `customer-levels`、`numbering-rules`、`dictionaries` 这些接口，但 ER 里没有对应表…用一张通用配置表补上"，**但补的是表，不是接口**）。

### 1.36 Sales Agent（`03-API §37`）
Session 已实现：`GET/POST /agent/sessions`（`agent/router.py:32,59`）、`GET/DELETE /agent/sessions/{id}`（`:76,125`）。
Message 已实现：`POST /agent/sessions/{id}/messages`（`:137`）。
**缺失**：`GET /agent/sessions/{id}/messages`（not found in code；**等价**：`GET /agent/sessions/{id}` 内嵌 `messages`，`:76,106-116`）、`POST /agent/sessions/{id}/messages/stream`（**not found in code** — 无 SSE/流式，前端 `AgentPage` 走同步 POST，`frontend/src/shared/api/agent.ts:73`）。
Action 已实现：`GET /agent/actions`（`:151`）、`POST /agent/actions/{id}/confirm`（`:178`）、`POST /agent/actions/{id}/reject`（`:194`）。
**缺失**：`GET /agent/actions/{id}`（not found in code）、`POST /agent/actions/{id}/cancel`（not found in code；`reject` 语义接近但状态写成 `rejected`）。
Execution 已实现：`GET /agent/executions`（`:215`）。
**缺失**：`GET /agent/executions/{id}`（not found in code）、`POST /agent/executions/{id}/retry`（not found in code）。
**Specialized 七个全部缺失**（not found in code）：`POST /agent/customer-summary`、`POST /agent/opportunity-analysis`、`POST /agent/product-recommendation`、`POST /agent/pricing-analysis`、`POST /agent/quote-draft`、`POST /agent/followup-suggestion`、`POST /agent/risk-analysis`。这些能力目前只能靠自然语言对话触发通用工具（`agent/runtime.py:116 run_turn`），没有结构化入参/出参。
（代码额外：`GET /agent/tools`，`:245`。）

### 1.37 Agent 内部 Tool（`03-API §38`）
文档建议 17 个工具，代码 **12 个**（`agent/tools.py`，标签表 `:54-67`）：`search_customers:123`、`get_customer_overview:154`、`list_opportunities:249`、`get_opportunity_detail:291`、`list_my_tasks:383`、`list_sku_options:417`、`calculate_price:448`、`get_receivables_summary:494`、`create_followup:529`、`create_task:569`、`update_opportunity_next_action:621`、`request_quote_approval:659`。
**缺失 8 个**：`search_leads`、`get_contact`、`get_product`、`search_skus`（`list_sku_options` 近似但不支持搜索语义）、`calculate_logistics`（依赖物流模块，见 §1.18）、`create_quote_draft`、`create_quote_version`、`get_order`。
另：文档要求"所有 Tool 调用必须记录 user_id / role / data_scope / risk_level / input / output / result"（§38 末）——代码 `agent_executions` 存了 `tool_name/risk_level/input_payload/output_payload/status`（`agent/model.py:79-86`），**未存 `user_id` / `role` / `data_scope`** 快照（只能用 `session_id → agent_sessions.user_id` 间接推）。

### 1.38 通用规范（`03-API §1.2`、`§40`）
- **幂等键 `Idempotency-Key`**：not found in code（全库无该 header 处理）。
- **乐观锁 `version`**：not found in code（无 version 字段/ETag；仅 `QuoteVersion.version_no` 是业务版本号，非并发控制，`quote/model.py:55`）。
- **外部集成 trace_id**：not found in code（`IntegrationLog` 无 trace_id 字段，`integration/model.py:33-46`）。
- **`40902 版本冲突`、`42201 低于允许价格`、`40302 数据范围受限` 三个错误码**：在 `core/errors.py:19,22,16` 已定义，但 `grep` 无任何 `raise AppError(ErrorCode.VERSION_CONFLICT/DATA_SCOPE_DENIED/PRICE_TOO_LOW)` 调用点 —— 定义未使用。

---

## 2. MISSING DATA MODEL（`02-ER` 要求但模型不存在 / 字段不一致）

### 2.1 整表缺失（ER 章节 → 代码结论）

| ER 表（章节） | 结论 | 说明 |
|---|---|---|
| `tags`（§5） | **不存在** | `grep "tags\b"` 只命中 APIRouter 的 `tags=` 参数；无 `Tag` 模型 |
| `customer_tags`（§5） | **不存在** | 同上；客户与标签的多对多关系无法表达（PRD §6.1"标签"列无数据源） |
| `customer_merge_logs`（§5） | **不存在** | 字段 `source_customer_id/target_customer_id/operator_id/merge_snapshot/created_at` 全部无落点 |
| `wecom_users`（§6） | **不存在** | not found in code |
| `wecom_external_contacts`（§6） | **不存在** | not found in code |
| `wecom_follow_relationships`（§6） | **不存在** | not found in code |
| `wecom_sync_jobs`（§6） | **不存在** | not found in code |
| `product_files`（§8） | **不存在** | 产品图片/附件无表（`products` 无 image 字段，`product/model.py:17-29`） |
| `logistics_quotes`（§10） | **不存在** | 物流试算结果无处落库；代码只有 `logistics_rates` 费率表（`pricing/model.py:106`） |
| `sample_requests`（§13） | **不存在** | not found in code |
| `sample_items`（§13） | **不存在** | not found in code |
| `sample_shipments`（§13） | **不存在** | not found in code |

### 2.2 代码有、ER 未定义的表（反向差异，供人工确认）

`system_settings`（`settings/model.py:17`）、`public_pool_rules`（`:29`）、`task_rules`（ER **有** `task_rules`，一致）、`logistics_rates`（`pricing/model.py:106`）、`external_mappings`（`integration/model.py:15`）。前两张与 `logistics_rates`、`external_mappings` 是 ER 之外的扩展表 —— `settings/model.py:1-6` 已注明是"为补 ER 缺口"的刻意设计，**不列为冲突**。

### 2.3 逐表字段差异（ER 有 → 模型缺；模型有 → ER 未列）

| 表 | ER 要求但**模型缺失**的字段 | 模型**多出**（ER 未列）的字段 |
|---|---|---|
| `departments`（§3） | — | `created_at/updated_at`（`user/model.py:13` TimestampMixin） |
| `users`（§3） | `password_hash` 不在 ER（ERP 口径按 SSO 设计）但代码必需 | `username`（`user/model.py:26`）、`password_hash`（`:27`）、`created_at/updated_at` |
| `roles`（§3） | — | `data_scope`（`user/model.py:44`）、`created_at/updated_at`（代码注释 `:1-5` 说明是刻意补的缺口） |
| `permissions`（§3） | — | `created_at/updated_at`（缺 `TimestampMixin`？实为 `Permission(Base, IdMixin)` 无时间戳，`:48`） |
| `leads`（§4） | — | `region`（`lead/model.py:27`）、`remark`（`:35`）、`last_followup_at`（`:36`）、`deleted_at`（`:39`） |
| `lead_assignments`（§4） | — | `operator_id`（`lead/model.py:49`） |
| `customers`（§5） | — | `pool_status`（`customer/model.py:35`，代码注释明说是补 06-清单缺口）、`remark`（`:40`）、`updated_at` |
| `contacts`（§5） | — | `remark`（`customer/model.py:68`）、`deleted_at`（`:69`）、`updated_at` |
| `opportunities`（§7） | — | `loss_remark`（`opportunity/model.py:49`）、`reopen_at`（`:50`）、`updated_at` |
| `opportunity_items`（§7） | `customer_requirement`（客户特殊要求，PRD §10 明文"客户特殊要求"） | — |
| `products`（§8） | `category_id`（ER 为外键/ID）→ 代码是**字符串** `category`（`product/model.py:23`） | `knowledge`（`:26`）、`created_by`（`:28`）、`deleted_at`（`:29`） |
| `skus`（§8） | — | `unit`（`product/model.py:53`）、`deleted_at`（`:55`） |
| `price_permissions`（§9） | `sku_id`、`minimum_price`（**两者都不存在**，见 §5 冲突表） | `can_approve`（`pricing/model.py:94`）、`remark`（`:96`） |
| `product_costs`（§9） | — | `remark`（`pricing/model.py:27`） |
| `price_rules`（§9） | — | `remark`（`pricing/model.py:61`） |
| `customer_price_rules`（§9） | — | `remark`（`pricing/model.py:79`） |
| `exchange_rates`（§9） | — | `remark`（`pricing/model.py:135`） |
| `quotes`（§11） | — | `deleted_at`（`quote/model.py:47`） |
| `quote_versions`（§11） | — | `trade_terms`（`quote/model.py:68`）、`approval_required`（`:73`）、`submitted_at/approved_at/sent_at/accepted_at/declined_at`（`:76-80`） |
| `quote_items`（§11） | — | `sku_code_snapshot`（`quote/model.py:91`）、`opportunity_item_id`（`:89`）、`tax_refund_snapshot`（`:105`）、`profit_with_refund_snapshot`（`:108`）、`approval_required`（`:111`）、`approval_reason`（`:112`）、`remark`（`:113`） |
| `followups`（§12） | — | `lead_id`（`followup/model.py:25`） |
| `tasks`（§12） | — | `contact_id`（`task/model.py:18`）、`lead_id`（`:19`）、`updated_at` |
| `sales_orders`（§14） | — | `payment_terms`（`order/model.py:45`）、`remark`（`:46`）、`cancelled_at`（`:48`）、`updated_at` |
| `sales_order_items`（§14） | — | `specification`（`order/model.py:58`） |
| `receivable_plans`（§15） | — | `remark`（`payment/model.py:39`） |
| `payment_records`（§15） | — | `voucher_note`（`payment/model.py:57`）、`confirmed_at`（`:59`）、`created_by`（`:61`） |
| `approval_instances`（§16） | — | `summary`（`approval/model.py:34`） |
| `approval_definitions`（§16） | — | `created_at`（`approval/model.py:19`） |
| `business_files`（§17） | — | `remark`（`file/model.py:45`） |
| `files`（§17） | — | `checksum`（`file/model.py:27`） |
| `agent_messages`（§19） | — | `tool_name`（`agent/model.py:44`） |
| `agent_actions`（§19） | — | `tool_name`（`agent/model.py:57`）、`title`（`:61`）、`result`（`:64`） |
| `agent_executions`（§19） | — | `session_id`（`agent/model.py:77`）、`risk_level`（`:80`） |

> 说明：多出的字段绝大多数是**实现必需的扩展**（时间戳、软删、快照、审计辅助）。真正需要人工决策的是 §5 里的三处：`products.category` vs `category_id`、`opportunity_items.customer_requirement`、`price_permissions.sku_id/minimum_price`。

### 2.4 索引缺失（`02-ER §20`"索引建议"）
ER 列了 20 条重点索引，逐条核对（`grep -r 'Index(' modules/*/model.py`）：

| ER 索引 | 代码 |
|---|---|
| `leads(owner_id,status)` | ✅ `lead/model.py:17` |
| `customers(owner_id,status)` | ✅ `customer/model.py:20` |
| `customers(name)` | ✅ `customer/model.py:21` |
| `contacts(customer_id)` | ✅ `customer/model.py:52` |
| `contacts(external_userid)` | ❌ **缺失**（`contacts.external_userid` 无索引） |
| `wecom_external_contacts(external_userid)` | ❌ 表不存在 |
| `wecom_follow_relationships(external_contact_id,wecom_userid)` | ❌ 表不存在 |
| `opportunities(customer_id,status)` | ✅ `opportunity/model.py:30` |
| `opportunities(owner_id,stage_id)` | ✅ `opportunity/model.py:31` |
| `opportunity_items(opportunity_id)` | ✅ `opportunity/model.py:71` |
| `quotes(opportunity_id)` | ✅ `quote/model.py:34` |
| `quotes(customer_id)` | ✅ `quote/model.py:35` |
| `quote_versions(quote_id)` | ✅ `quote/model.py:52` |
| `quote_items(quote_version_id)` | ✅ `quote/model.py:85` |
| `tasks(owner_id,status,due_at)` | ✅ `task/model.py:13` |
| `followups(customer_id,created_at)` | ✅ `followup/model.py:19` |
| `sales_orders(customer_id)` | ✅ `order/model.py:29` |
| `receivable_plans(order_id,status)` | ✅ `payment/model.py:31` |
| `payment_records(order_id)` | ✅ `payment/model.py:47` |
| `notifications(user_id,read_at)` | ✅ `notification/model.py:17` |

→ 唯一可实现缺口：`contacts(external_userid)` 未建索引。

### 2.5 关键约束（`02-ER §21`）落实情况
- "已发送/已审批通过报价版本不可原地修改" ✅ `quote/service.py:149-156 ensure_version_editable`。
- "报价必须保存价格与汇率快照" ⚠️ 价格快照 ✅（`quote/service.py:193-213`），**汇率快照字段存在但从不写入**（`quote/model.py:62-66`，`grep exchange_rate_snapshot` 无写入点，仅 alembic 加列 `alembic/versions/a4034e4ebed1:34`）→ **违反**。
- "owner_id 可变，created_by 不覆盖" ✅（`customer/service.py:162-181` 只改 owner_id）。
- "关键对象优先软删除" ✅（customers/contacts/leads/opportunities/quotes/products/skus）。
- "外部系统 ID 不作为 CRM 主键" ✅（`external_mappings` 独立表）。
- "一次 Lead 转化必须幂等" ✅ `lead/router.py:236-238`（以 `status=="converted"` 判重，非真正幂等键，见 §4）。
- "QuoteVersion 版本号必须唯一" ❌ **无唯一约束**（`quote/model.py:51-55` 无 `UniqueConstraint(quote_id, version_no)`；并发下 `latest.version_no+1` 会撞号，`quote/router.py:278`）。
- "SalesOrder 转 ERP/MES 必须幂等" ❌ 未实现（§1.27）。
- "PaymentRecord 不得覆盖历史记录" ✅（回款新增不覆盖，`payment/router.py:178-216`）。
- "Agent 高风险动作必须经确认或审批" ✅ L2/L3 挂起（`agent/runtime.py:246-279`）。

---

## 3. MISSING FUNCTIONAL REQUIREMENTS（PRD 要求但未实现）

### 3.1 物流试算（PRD §14）— **整节缺失，P0**
- PRD §14 要求输入 `SKU/数量/包装方式/重量/体积/起运地/目的地/运输方式`，输出 `预计费用/预计时效/计费重/方案列表`。
- 代码：**not found in code**。核价里的"运费"只是 `estimate_logistics`（`pricing/service.py:215-235`）：取 `LogisticsRate` 表**第一条**费率 × SKU 单重，最低收费按数量摊分——**无目的地、无起运地、无包装方式、无体积/计费重、无时效输出、无多方案对比**。
- 前端：只有价格中心"运费费率"维护 tab（`frontend/src/modules/pricing/PriceCenterPage.tsx:34`）；核价页只有一个手填"单件运费"输入框（`PricingPage.tsx:119`）。
- 连带：`agent` 的 `calculate_logistics` 工具缺失（§1.37），报价明细的 `logistics_cost_snapshot` 只能手填（`quote/schema.py:34`）。

### 3.2 样品模块（PRD §19，ER §13，API §26）— P2，可选
- PRD 用"若启用独立样品模块"表述（`:610`），ER 明确给出 3 张表（§13），API 给出 10 个接口（§26）。
- 代码：**全部 not found in code**（无表、无接口、无页面、无 Agent 工具）。商机阶段里有 `sample`（"样品"）阶段（`seed.py:440`），但阶段存在 ≠ 样品模块存在。
- 需领导确认是否启用（`08-待领导确认清单` 范畴）。

### 3.3 编号规则（PRD §2.6 系统管理员能力；API §36）— P1
- PRD §2.6 把"编号规则"列入系统管理员职责。
- 代码：**无 `numbering_rules` 表、无接口**。单号生成是硬编码：
  - 报价：`Q{YYYYMMDD}{count+1:04d}`，`quote/service.py:124-132`；
  - 订单：`SO{YYYYMMDD}{count+1:04d}`，`order/service.py:72-80`。
- 风险：`count` 用 `select(func.count())` 且过滤 `like prefix%`，删除历史单会造成**重号**；且两者都无并发保护。`settings/model.py:3` 注释承认"文档里有 `numbering-rules`…这些接口"但只补了表没补接口。

### 3.4 企业微信（PRD §8 全节）— P0
逐条对照：

| PRD 要求 | 代码 |
|---|---|
| §8.1 内部成员：部门同步 / 成员同步 / 在职状态 / CRM User 映射 | **not found in code**（`users.wecom_userid`、`departments.wecom_department_id` 字段存在——`user/model.py:33,18`——但无同步逻辑、无 wecom_users 表） |
| §8.2 外部联系人：外部联系人同步 / 跟进员工关系同步 / 客户标签 / 添加时间 / 添加来源 / 备注信息 | **not found in code**（无表无接口） |
| §8.3 待归一独立页面：待处理联系人 → 系统候选客户匹配 → 关联已有/创建新客户/暂不处理 | **not found in code**（前端 `/wecom` 是占位页："企业微信集成尚未开发"，`frontend/src/app/router.tsx:82-93`） |
| §8.4 离职继承：转移企微客户关系/CRM 客户负责人/商机负责人/未完成任务；保留创建人/历史跟进/历史报价/日志 | **not found in code**（`POST /integrations/wecom/transfer` 缺失） |

**连锁影响**：PRD §26 验收链路的**起点就是 `ExternalContact → 待归一 → Customer + Contact`**，整条链路无法跑通。

### 3.5 客户标签 / 客户合并（PRD §6.1、§6.3）
- §6.1 客户列表要求支持"标签"筛选/展示 → **not found in code**（无 tags 表、无接口、`CustomerListPage.tsx:140-186` 列定义里无标签列）。
- §6.3 客户归一要求`查重/疑似客户提示/合并/强制创建/企微联系人绑定客户`：
  - 查重打分 ✅（`contact_util.py:48-149`，权重可配 `settings/service.py:59-67`）；
  - 疑似提示 ✅ 仅在线索转化/导入时有（`lead/router.py:219`、`customer/io_router.py:82`）；
  - **合并 ❌ not found in code**（`10-项目现状与交接说明.md:67,325-327` 自认"合并没做"）；
  - **新建客户时的疑似重复提示 ❌ not found in code**（`CustomerListPage.tsx:320-394` 新建弹窗直接提交，无查重调用）；
  - **企微联系人绑定客户 ❌ 缺失**。

### 3.6 商机 Kanban 看板（PRD §9 阶段可视化 + UI 设计稿）
- PRD §9.2 定义 9 阶段流水线；代码阶段数据齐全（`seed.py:434-444`：新询盘→需求确认→产品推荐→核价→已报价→样品→商务谈判→待下单→成交）。
- **看板视图 not found in code**：`OpportunityListPage.tsx:5` 只用 Semi `Table`；无拖拽/分列看板组件；无 `POST /opportunities/{id}/change-stage` 的看板拖拽调用（阶段推进是弹窗选择，`OpportunityDetailPage.tsx:503-533`）。
- 关联缺口：`GET /opportunities` 无按阶段分组的聚合（`opportunity/router.py:94-129` 返回平铺分页）。

### 3.7 报价多方案对比（PRD §15.2 "旧版本不可覆盖" + 设计稿 What-if）
- PRD §15.2 要求 V1/V2/V3 版本链、旧版本不可覆盖 → ✅ 已实现（`quote/router.py:256-341`，`quote/service.py:149-156`）。
- **多方案对比视图/接口 not found in code**：无 `GET /quotes/{id}/version-comparison`（`grep "version-comparison"` 无命中）；前端 `QuoteDetailPage.tsx:304` 只有"新建版本 + 版本切换"，无并排对比表。`10-项目现状与交接说明.md:332` 列为待做。

### 3.8 核价引擎输入输出项（PRD §13）— P0
| PRD §13 输入 | 代码支持 | 证据 |
|---|---|---|
| 客户 | ✅ `customer_id` | `pricing/schema.py:86` |
| 客户等级 | ⚠️ 间接（由 `customer.level` 推出，**不能显式传入**） | `pricing/service.py:264-265` |
| SKU | ✅ | `schema.py:84` |
| 数量 | ✅ | `schema.py:85` |
| **国家** | ❌ **缺失** | not found in code（`PricingRequest` 无 country） |
| **包装** | ❌ **缺失** | not found in code（只用 SKU 上固定的 `package_cost`，`service.py:272`） |
| **物流方式** | ❌ **缺失** | not found in code（只按重量估运费，`service.py:215-235`） |
| **付款方式** | ❌ **缺失** | not found in code（核价不考虑账期资金成本） |
| 币种 | ✅ `currency` | `schema.py:92` |
| 汇率 | ✅ `exchange_rate` | `schema.py:93` |
| **利润要求** | ⚠️ 部分：`target_margin` 可传（`schema.py:88`），但 PRD 口径的"利润要求"含利润额，**无绝对利润额输入** | — |

| PRD §13 输出 | 代码支持 | 证据 |
|---|---|---|
| 成本 | ✅ `cost.{purchase,production,package,processing,goods,logistics,base}` | `service.py:412-421` |
| 建议报价 | ✅ `recommended_price` | `service.py:426` |
| 推荐成交区间 | ✅ `recommended_range` | `service.py:427` |
| **最低允许价** | ⚠️ **语义偏差**：返回的是 `max(按授权利润率反推价, 规则最低价, 客户特殊价, 协议价)`（`service.py:322-335`），即"当前用户权限内的底价"，**不等于** PRD/ER 里的"最低保护价（`price_rules.minimum_price`）"——两者被合并取 max，业务无法区分"保护价"与"我的授权底价" | `service.py:428` |
| 利润 | ✅ `profit` | `service.py:439` |
| 利润率 | ✅ `profit_rate` | `service.py:440` |
| 是否需要审批 | ✅ `approval_required` | `service.py:443` |

另：PRD §12"历史价格"→ `GET /pricing/history` 缺失，核价结果不落库（`pricing/service.py:399-445` 纯计算无写入）。

### 3.9 批量操作（PRD §5.3、§6.1、§10、§18）
- 线索：§5.3 核心操作含"转移/回收"→ 仅 `assign/claim/release/discard`（`lead/router.py:114-208`），**无批量分配**（`POST /leads/batch-assign` 缺失）、**无批量导入导出**。
- 客户：`POST /customers/batch-transfer`、`POST /customers/batch-tag` **缺失**；前端 `CustomerListPage.tsx:248-267` 的 Table 无 `rowSelection`，无法勾选批量。
- 商机需求：`POST /opportunities/{id}/items/batch` **缺失**（只能逐条 `POST /opportunities/{id}/items`，`opportunity/router.py:447`）。
- 任务：`POST /tasks/batch-complete` **缺失**（`task/router.py` 无该路由；前端 `TaskListPage.tsx` 无多选批量完成）。
- 报价明细：唯一有批量语义的是整版替换 `POST /quote-versions/{id}/items/batch`（`quote/router.py:429`）。

### 3.10 通知的企业微信渠道（PRD §25）
- PRD §25 要求通知支持"站内通知 + 企业微信通知"，并含已读未读/任务提醒/审批提醒/回款提醒。
- 代码：站内通知 ✅（`notification/router.py`，`notification/service.py:10-70`，审批/任务/回款三类触发点分别见 `quote/router.py:622`、`task/router.py:110`、`payment/router.py:238`）。
- **企微渠道 ❌ not found in code**：`notify()` 只写 `notifications` 表（`notification/service.py:10-29`），无任何 outbound 调用；`notifications` 表无 `channel` 字段（`notification/model.py:16-26`）；无 `GET/PATCH /notification-settings`。

### 3.11 Excel 导入导出覆盖范围（PRD §5.1"来源：Excel"、API §6/§14/§15）
| 对象 | 文档要求 | 代码 |
|---|---|---|
| 客户 | `POST /customers/import`、`POST /customers/export` | ✅ 实现（`customer/io_router.py:66,35`），但**格式是 CSV 不是 xlsx**，且无 openpyxl（`customer/io.py:1-10` 注释说明是有意取舍）；模板接口 `GET /customers/import-template` 是扩展 |
| 线索 | `POST /leads/import`、`POST /leads/export` | ❌ **全部缺失** |
| 产品 | `POST /products/import`、`POST /products/export` | ❌ **全部缺失** |
| SKU | `POST /skus/import`、`POST /skus/export` | ❌ **全部缺失** |
- 前端只有客户列表的导入/导出/模板三个按钮（`CustomerListPage.tsx:224-242`）。

### 3.12 客户列表高级筛选字段（PRD §6.1 明文 13 项）
| PRD §6.1 | 代码（后端 `customer/service.py:35-65` + 前端 `CustomerListPage.tsx:194-246`） |
|---|---|
| 搜索 | ✅ `keyword`（前端 260px 输入框） |
| 高级筛选 | ⚠️ 仅 `level` 下拉一个条件 |
| 标签 | ❌ 无（无 tags 表） |
| 等级 | ✅ `level` |
| 国家 | ❌ `country` 参数不存在（DB 有字段，`customer/model.py:27`） |
| 来源 | ⚠️ 后端支持 `source` 参数（`service.py:59`），**前端未提供入口**（`CustomerListPage.tsx:202-221` 只有等级与范围两个下拉） |
| 负责人 | ⚠️ 后端支持 `owner_id`（`service.py:61`），前端只用于"我负责的"三态范围（`:118`），无任意负责人选择 |
| 公海/私海 | ✅ `pool_status` |
| **最近跟进** | ❌ not found in code（无 `last_followup_at` 过滤） |
| **下次跟进** | ❌ not found in code（无 `next_followup_at` 过滤，DB 字段存在 `customer/model.py:44`） |
| **商机数** | ❌ not found in code（列表返回 `contact_count` 而非商机数，`customer/service.py:111`） |
| **累计报价** | ❌ not found in code（列表无报价金额聚合） |
| **累计成交** | ❌ not found in code |
| **待回款** | ❌ not found in code |
- 另：`CustomerListPage.tsx:140-186` 表格列只有名称/等级/地区/来源/联系人/负责人/状态/创建时间——缺"最近跟进/下次跟进/商机数/累计报价/累计成交/待回款"六列。

### 3.13 报价状态完整性（PRD §15.1 七/八个状态；`02-ER §21`）
- PRD §15.1 列出：草稿/待审批/已通过/已拒绝/已发送/已接受/已失效。
- 代码 `QUOTE_STATUS_LABEL`（`quote/model.py:19-28`）：`draft/pending_approval/approved/approval_rejected/sent/accepted/declined/expired`（8 个，含"客户拒绝"细分）— 词表**齐了**。
- **但 `expired`（已失效）无任何写入路径**：`grep '"expired"'` 只命中字典定义 `quote/model.py:27`；无 `POST /quote-versions/{id}/expire`；无定时任务扫描 `valid_until` 过期（`quote/router.py` 全文无 expire 逻辑）。
- "已拒绝"在代码里被拆成两个：`approval_rejected`（审批未通过，`approval/router.py:247`）与 `declined`（客户拒绝，`quote/router.py:783`）——口径比 PRD 更细，**不列为冲突**。

### 3.14 审批动作完整性（PRD §16）
- PRD §16 支持动作：提交/撤回/同意/拒绝/**转交**/审批记录/审批意见。
- 已实现：提交 ✅（`quote/router.py:601`）、撤回 ✅（`:651`，但见 §4）、同意 ✅（`approval/router.py:168`）、拒绝 ✅（`:219`）、审批记录 ✅（`:271`）、审批意见 ✅（`ApprovalRecord.comment`，`approval/model.py:49`）。
- **转交 ❌ not found in code**（无 `POST /approvals/{id}/transfer`；`ApprovalRecord.action` 注释含 transfer 但无写入点，`approval/model.py:48`）。
- PRD §16 触发条件：低于业务员授权价/低于保护价/折扣超权限/特殊付款条件/特殊账期 —— 代码只实现前两条的半条（低于授权底线或利润率不足，`quote/service.py:288-294`），**"折扣超权限"（`discount_limit` 字段存在但全库无读取点，`pricing/model.py:93`）、"特殊付款条件/账期"完全未判定**。

### 3.15 任务状态与自动提醒（PRD §18）
- PRD §18 状态含"已逾期"→ 代码把逾期做成**计算属性**而非存储状态（`task/router.py:26-29`、`task/serializer`），`Task.status` 注释只有 `pending/doing/done/cancelled`（`task/model.py:25`）。UI 上也真不显示"已逾期"状态徽标（`TaskListPage.tsx:112`）。属设计取舍，**不列为冲突**，但"已逾期"筛选依赖 `due_at < now` 参数（`task/router.py:75-78`）✅。
- PRD §18"自动提醒"✅ 部分实现（`settings/service.py:168-291` 三条规则可手工触发 `POST /tasks/run-auto-rules`），**但无定时调度器**（无 APScheduler/celery/夜间任务；`main.py` 无 startup 调度注册）——规则必须有人在设置页点按钮才跑（`settings/router.py:202`），公海回收同理（`:154`）。

### 3.16 成交/失单（PRD §20）
- 成交：标记成交 ✅（`opportunity/router.py:282`）、绑定最终报价版本 ✅（`win_quote_version_id`，`:301`）、**生成销售订单** ✅ 由 `POST /quote-versions/{id}/convert-to-order` 承担（`order/router.py:67`）——但**商机成交动作本身不触发建单**，需人工再点一次，与 PRD"商机标记成交 → 绑定最终报价版本 → 生成销售订单"的三步链不同（属交互设计差异）。
- 失单：必须选择原因 ✅（`loss_reason_id` 必填，`opportunity/schema.py`）、9 类原因初始数据 ✅（`seed.py:466-476`）、设置重新联系时间 ✅（`reopen_at`，`opportunity/router.py:334`）、后续重新创建商机 ✅（`POST /opportunities/{id}/reopen`，`:352`；以及 `POST /orders/{id}/repurchase`，`order/router.py:252`，超出 PRD 要求）。

### 3.17 数据分析缺口（PRD §23）
| PRD §23 维度 | 代码 |
|---|---|
| 客户：新增 ✅（`analytics/service.py:550-553`）/ 来源 ✅（`:557`）/ 等级 ✅（`:560`）/ **活跃 ❌** / **沉睡 ❌** / **复购 ❌** | 缺 3 项 |
| 商机：漏斗 ✅（`:423`）/ **阶段转化 ❌**（只有漏斗计数，无阶段间转化率；前端自行算相邻比，`WorkbenchPage.tsx:285-287`）/ 成交率 ✅（`:467`）/ 失单率 ✅（`:468`）/ **周期 ❌**（无成交周期统计，尽管 `opportunity_stage_history.duration_seconds` 已存，`opportunity/model.py:66`） | 缺 2 项 |
| 报价：报价次数 ✅（`:525`）/ 平均版本 ✅（`:526`）/ 平均让价 ✅（`:527`）/ **低价审批率** ⚠️（返回 `approval_rate` = 需要审批的版本数/总版本数，`:529`，口径近似）/ 报价转成交率 ✅（`accept_rate`，`:528`） | 基本齐 |
| 产品：**询盘 ❌** / 报价 ✅（`:564-591` 只有被报价次数）/ **成交 ❌** / **失单 ❌** / **利润 ❌** | 缺 4 项 |
| 人员：客户数 ✅ / **跟进数 ❌** / 商机数 ✅ / 报价数 ✅ / 成交额 ✅ / **回款额 ❌** | 缺 2 项（`analytics/service.py:594-626`） |
- 缺整接口：`GET /analytics/leads`、`GET /analytics/pricing`、`GET /analytics/payments`（§1.34）。
- 隐藏缺陷：`dashboard/trend` 用了 PostgreSQL 专有函数 `func.to_char`（`analytics/service.py:223,237`），与"改 MySQL 只换连接串"的配置承诺（`core/config.py:22`）冲突 —— 换库即 500。

### 3.18 工作台主管视图（PRD §4.2）
- PRD §4.1 业务员视图：今日待办 ✅/逾期任务 ✅/待跟进客户 ✅/进行中商机 ✅/报价待处理 ⚠️（用"待审批报价"替代，`analytics/service.py:192 pending_approval_count`）/回款提醒 ✅（`month_received_amount`+`pending_receivable_amount`）/最近客户动态 ✅/AI 建议 ⚠️（`WorkbenchPage.tsx:535-587` 是"重点跟进商机"跳转列表 + 营销文案"实时运行中"，**非真实 AI 生成**）。
- PRD §4.2 主管视图：团队任务 ❌/团队逾期 ❌/待审批报价 ✅/商机漏斗 ✅/风险商机 ✅/客户分配 ❌/团队成交情况 ❌。
- 代码是**单一工作台**（`WorkbenchPage.tsx` 无按角色分支渲染；只在顶部显示角色 chip，`:214-224`）。缺主管专属指标（团队维度聚合）。

### 3.19 文件预览（PRD §25）
PRD §25 文件能力：上传 ✅/下载 ✅/**预览 ❌**/删除 ✅/关联业务对象 ✅。
- 预览 not found in code：无预览接口/前端组件；`frontend/src/modules/common/AttachmentPanel.tsx` 只有上传/下载/删除（`:88,103,138`）。

### 3.20 Agent 能力覆盖（PRD §24）
| PRD §24 能力 | 代码 |
|---|---|
| 查询客户 | ✅ `search_customers`/`get_customer_overview` |
| 总结客户 | ✅ 靠 `get_customer_overview` + LLM 汇总（无专用接口） |
| 分析商机 | ✅ `list_opportunities`/`get_opportunity_detail` |
| 推荐产品 | ⚠️ `list_sku_options` 仅罗列，无推荐逻辑；`POST /agent/product-recommendation` 与 `POST /opportunities/{id}/recommend-products` 均缺失 |
| 核价 | ✅ `calculate_price` |
| 生成报价草稿 | ❌ **缺失**（无 `create_quote_draft`/`create_quote_version` 工具，无 `POST /agent/quote-draft`） |
| 创建任务 | ✅ `create_task`（L2） |
| 跟进建议 | ⚠️ 无专用工具/接口（`POST /agent/followup-suggestion` 缺失） |
| 回款风险 | ⚠️ `get_receivables_summary` 只读汇总，无风险评分；`POST /agent/risk-analysis` 缺失 |
| 数据分析 | ⚠️ 只能读 `get_receivables_summary`/`list_my_tasks`，无销售汇总工具（`get_sales_summary` 缺失） |
| 风险控制 L1/L2/L3 | ✅ 完整（`agent/tools.py` 各 `@tool` 第三/四参数标 risk；`agent/runtime.py:200-279` 分流；L3 落 `approval_required`，`:259`） |

---

## 4. PARTIAL / BROKEN IMPLEMENTATIONS（存在但与文档不符）

> 每条：文档要求 vs 实际代码。已排除代码注释里明确说明的"刻意简化"。

### 4.1 ERP 同步是空壳（API §28；PRD §21"ERP/MES ID"）
- 文档：CRM 保存"ERP/MES ID、履约状态"，`POST /orders/{id}/sync-erp` 应真实推送并回写。
- 代码：`order/router.py:218-249` 只 `session.add(IntegrationLog(status="skipped"))` 并 `return {"pushed": False}`；**`order.erp_order_id` 永不写入**；`order/service.py:214 record_external_order_id`（会建 `ExternalMapping`）**全库无调用点**。
- 后果：订单详情页永远显示"未推送 ERP/MES"（`frontend/src/modules/order/OrderDetailPage.tsx:244`），PRD §26 验收链路 `SalesOrder → ERP/MES` 断在最后一步。

### 4.2 报价汇率快照从不写入（PRD §15.5；ER §11/§21）
- 文档：`QuoteVersion` 必须保存"币种、汇率快照、汇率来源、汇率时间"。
- 代码：字段齐全（`quote/model.py:60-66`），但 `create_quote`（`quote/router.py:174-183`）与 `create_version`（`:276-286`）**都不传这三个字段**；`grep exchange_rate_snapshot` 无写入点（仅 alembic `a4034e4ebed1:34` 加列）。`build_item_snapshot` 调用 `calculate_price` 时也不传 `currency`/`exchange_rate`（`quote/service.py:179-187`）→ **报价永远按 CNY 核价**，外贸场景下快照全空。前端 `QuoteDetailPage` 也无货币/汇率展示。

### 4.3 报价明细成本快照口径与 ER 不一致（ER §11）
- ER：`quote_items.cost_snapshot` + `package_cost_snapshot` + `logistics_cost_snapshot` 并存，`cost_snapshot` 字面应含包装。
- 代码：`cost_snapshot = result["cost"]["goods_cost"]`（`quote/service.py:201`），而 `goods_cost = purchase + production + package + processing`（`pricing/service.py:274`）→ **包装成本被重复计入 `cost_snapshot` 与 `package_cost_snapshot`**；审批判定时又用 `base_cost = cost_snapshot + logistics_cost_snapshot`（`quote/service.py:284`）。前端详情页展示"成本"列时用 `cost_snapshot + logistics_cost_snapshot`（`QuoteDetailPage.tsx:203`）。三处口径不统一，人工需定口径。

### 4.4 `profit_snapshot` 忽略退税（ER/代码自身字段矛盾）
- 代码：`profit = quoted_price - goods_cost`（`quote/service.py:190`），**未减运费**；而审批判定用 `price - (cost_snapshot + logistics_cost_snapshot)`（`:284-287`）。
- 同表还有 `tax_refund_snapshot`、`profit_with_refund_snapshot`（`quote/model.py:105-110`）**默认恒为 0、永不写入**（`grep profit_with_refund_snapshot` 无赋值点）→ 前端展示的"利润"低估，且退税额永远显示 0（`frontend/src/modules/quote/QuoteDetailPage.tsx` 无相应列）。

### 4.5 `withdraw_approval` 抹掉审批痕迹（PRD §16；ER §21）
- 文档：审批支持"撤回"，且"关键状态必须记录历史"（ER §1.14）。
- 代码：`quote/router.py:651-680` 把 `version.approval_status` 重置为 `not_submitted`、`approval_required=False`、`submitted_at=None`。实例虽然标 `withdrawn`，但版本上的"曾提交过审批"痕迹被抹平，审计只能靠 `approval_records`。属真实语义损失。

### 4.6 Lead 转化不是幂等键实现（API §40；ER §21）
- 文档："一次 Lead 转化必须幂等"、"幂等键 `Idempotency-Key`"。
- 代码：`lead/router.py:236-238` 仅以 `lead.status == "converted"` 判重。并发两次请求（同一 lead）都会通过检查 → 建两个客户/商机。事务内无行锁、无唯一约束保护。

### 4.7 公海"领取"权限过宽（PRD §2.1/§6.4）
- 文档：领取属于销售作业能力，应受数据范围约束。
- 代码：`lead/router.py:144` 领取线索只要求 `lead:view`；`customer/router.py:204` 领取客户也只要求 `customer:view`——任意有查看权的人可领取任何公海对象（数据范围过滤在列表层，领取接口本身不校验 `apply_data_scope`）。

### 4.8 `PATCH /orders/{id}` 无审计、无状态校验（API §1.2"关键写接口必须写审计日志"）
- 代码：`order/router.py:117-128` 直接 `setattr` + `commit`，**没有 `write_audit`**（同文件其他写接口都有，如 `:193-201`）。可用它绕过状态机直接改 `status` 而不留痕、不写 `order_status_history`（状态历史只有 `POST /orders/{id}/status` 才写，`:181`）。

### 4.9 多处写接口缺审计（逐条核对 `write_audit` 调用）
- `payment/router.py`：`create_receivable:49`、`generate_receivables:72`、`update_receivable:111`、`delete_receivable:128`、`mark_overdue:147`、`reject_payment:260` —— **6 处无审计**（仅 `create_payment`/`confirm_payment` 有，`:206,247`）。
- `quote/router.py`：`delete_item:542`、`add_charge:560`、`delete_charge:583`、`withdraw_approval:651` —— **4 处无审计**。
- `task/router.py`：`cancel_task:190`、`postpone_task:213`、`transfer_task:240`、`complete_task:161` ✅有、`create:97` ✅有、`update:132` ✅有 —— cancel/postpone/transfer 有审计 ✅（实为全部有）。
- `settings/router.py`：`create_pool_rule:125`、`update_pool_rule:138`、`run_recycle:154`、`create_task_rule:173`、`update_task_rule:186`、`run_task_rules:202` —— **6 处无审计**（仅 `upsert_setting:83` 有）。
- `file/router.py`：`attach_file:179`、`unlink_file:203` —— **2 处无审计**。
- `customer/router.py`：`create_contact:241` ✅、`update_contact:273` ✅、`delete_contact:302` ✅ 全有。
- `agent/router.py`：`create_session:59`/`delete_session:125`/`confirm_action`/`reject_action` —— **无 `write_audit`**（Agent 写动作靠 `agent_executions` + `write_audit` 在工具内部，`agent/tools.py:16`；但会话与动作确认本身无审计记录）。

### 4.10 核价"最低允许价"语义合并（PRD §12/§13）
见 §3.8。核心问题：ER 的 `price_rules.minimum_price`（最低保护价）与 `price_permissions.minimum_margin`（角色授权底线）在 `pricing/service.py:322-335` 被 `max()` 合并成单一 `minimum_price` 返回。业务无法判断"是踩了保护价还是踩了我自己的权限"，而 PRD §16 恰恰把这两个触发条件分列为两条。

### 4.11 单号生成的并发/重号缺陷（PRD §2.6）
见 §3.3。`quote/service.py:127-132` 用 `count(*) + 1`；删除/软删历史报价会造成新单号与历史重号 → 撞 `quote_no` 唯一约束（`quote/model.py:38`）直接 500。订单同理（`order/service.py:75-80`）。

### 4.12 QuoteVersion 版本号无唯一约束（ER §21"QuoteVersion 版本号必须唯一"）
`quote/model.py:51-55` 只有索引 `ix_quote_versions_quote`，无 `UniqueConstraint("quote_id","version_no")`。并发生成版本（`quote/router.py:265-278` 读 latest + 1）会撞号且静默产生两条同号版本。

### 4.13 审核分级"兜底角色"配置错误（PRD §16；`09-业务参数配置说明`）
- `seed.py:846-852` 与 `settings/service.py:43-53` 把第二级（`director`，无金额上限）的 `role_codes` 也配成 `["sales_manager"]` —— 与第一级相同。结果：任何超过 5 万的报价，最终还是销售主管自己批，**"销售经理/管理层"层级形同虚设**（PRD §2.3 要求管理层参与）。需业务确认。

### 4.14 审批意见入参位置不安全（API §23）
- 文档：拒绝/通过应带审批意见。
- 代码：`approval/router.py:223` 把 `comment` 声明为 **query 参数**（`async def reject(approval_id: int, request: Request, comment: str | None = None, ...)`），前端被迫拼 URL（`frontend/src/shared/api/quote.ts:199`：`/approvals/${id}/reject?comment=...`）。中文经 URL 编码易被中断/长度截断，且与"统一 JSON body"约定不符；`approve` 则**完全不接受意见**（`approval/router.py:169-174` 无 comment 参数）。

### 4.15 通知渠道字段缺失导致的不可扩展（PRD §25）
`notifications` 表无 `channel` 字段（`notification/model.py:16-26`），`notify()` 无渠道参数（`notification/service.py:10-19`）→ 即使接入企微，也无法区分"已发站内/已发企微"，`03-API §32` 也未提供发送状态查询。

### 4.16 文件接口权限粒度不足（API §31 + PRD §25）
- `GET /files/{id}/download` 只校验 `file:view`（`file/router.py:96`），**不校验该文件是否属于当前用户可见的业务对象**。任意登录用户拿到 file_id 即可下载他人客户/报价的附件（横向越权）。`GET /business/{type}/{id}/files` 同样只查 `file:view`（`:151`），不校验业务对象的存在性与数据范围。

### 4.17 Agent 会话/动作隔离不完整
- `GET /agent/actions` 用 `join(AgentSession).where(user_id == user.id)` ✅（`agent/router.py:159-163`）。
- `GET /agent/executions` **无任何用户过滤**（`:222`：`select(AgentExecution)` 全表），任何有 `agent:use` 权限的人可看到所有人的工具调用入参/出参 —— 含客户名、价格等敏感数据。**与 PRD §2 角色数据隔离要求冲突**。
- `POST /agent/actions/{id}/confirm` 校验了 session 属主（`:187-189`），✅。

### 4.18 报价"已发送"可绕过审批
- `mark_sent` 要求 `approval_status == "approved"` ✅（`quote/router.py:692-693`）。
- 但 `accept_quote` 允许 `quote.status in ("sent","approved")`（`:756`）→ 未发送即可标记"客户已接受"，跳过"已发送"环节与发送日志（`quote_send_logs` 不会产生记录），破坏 PRD §15.1 状态顺序与 `02-ER §21` 的可追溯性。
- 另外 `reject_quote`（客户拒绝）**无状态校验**（`:772-794`），任意状态（包括 `draft`）都能被标成"客户拒绝"。

### 4.19 订单日期/金额校验缺失
- `POST /quote-versions/{id}/convert-to-order` 只校验审批状态与幂等（`order/router.py:75-84`），不校验 `quote.valid_until` 是否已过期（PRD §15.1 的"已失效"报价仍可转单）。
- `PATCH /orders/{id}` 可任意改 `total_amount`（`order/schema.py` + `order/router.py:125-126`），与 `sales_order_items` 金额不再一致，且无审计（见 §4.8）。

### 4.20 客户列表的"标签"与"最近跟进"空位（前端）
已在 §3.12 列出。此处只补前端证据：`CustomerListPage.tsx:140-186` 列定义确无标签列与跟进列；`CustomerDetailPage.tsx:265-289` 概览卡片有"最近跟进"但**无"下次跟进"**（`next_followup_at` 字段存在却不展示，`:41-46` 后端已返回）。

---

## 5. DOC-vs-CODE CONFLICTS（文档与代码真实冲突，需人工裁决）

> 已排除 `settings/model.py:1-6`、`customer/model.py:1-7`、`order/model.py:1-6`、`product/model.py:1-6`、`quote/model.py:1-9`、`agent/model.py:1-6`、`contact_util.py:58-62` 等**代码注释里已自认的刻意简化**。

### 5.1 `products.category`（字符串）vs ER `products.category_id`（ID/外键）— 需裁决
- 文档：`02-ER §8 products` 列 `category_id`；`03-API §36` 另有 `GET/POST /customer-levels` 一类的字典接口，暗示分类是**受控字典**。
- 代码：`product/model.py:23` 是自由字符串 `category`，`product/service.py:74-75` 直接等值过滤，`frontend/src/modules/product/ProductListPage.tsx:161` 是硬编码占位符 `"物流器具"`。
- 冲突本质：分类到底是"字典实体"还是"自由文本"。这决定是否需要新建字典表 + `GET /dictionaries` + 分类下拉数据源。**注意：这不属于"刻意简化"，因为代码里没有对应注释。**

### 5.2 `price_permissions`：ER 有 `sku_id` / `minimum_price`，代码没有 — 需裁决
- 文档：`02-ER §9 price_permissions` 列 `(role_id, sku_id, minimum_price, minimum_margin, discount_limit, status)` —— 即"按 SKU 的角色底价"。
- 代码：`pricing/model.py:88-96` 只有 `(role_id, minimum_margin, discount_limit, can_approve, status)`，**无 `sku_id`、无 `minimum_price`**；`pricing/router.py:347` 的 `PUT /price-permissions/{role_id}` 也按角色一对一 upsert。
- 冲突本质：粒度不同——ER 是"角色 × SKU 的绝对底价"，代码是"角色级的利润率下限"。二者不能同时成立（代码的 `minimum_margin` 与 ER 的 `minimum_margin` 语义重合，但 ER 还多一层 SKU 维度）。若按 ER 实现，需改表结构 + 改 `resolve_min_margin`（`pricing/service.py:191-212`）+ 改价格权限页面（`PriceCenterPage.tsx:33,403-440`）。

### 5.3 `opportunity_items.customer_requirement`：PRD/ER 要求，代码无字段 — 需裁决
- 文档：`01-PRD §10` 明列 OpportunityItem 字段含"客户特殊要求"；`02-ER §7 opportunity_items` 有 `customer_requirement`。
- 代码：`opportunity/model.py:70-83` 无该列；API §12 的 payload 里也没有；前端商机需求表单（`OpportunityDetailPage.tsx`）无该输入。
- 冲突本质：是漏建字段（应补），还是被合并进 `remark`（`opportunity/model.py:83`）？需业务确认。

### 5.4 报价状态枚举：PRD 7 值 vs 代码 8 值（拆分"已拒绝"）— 低风险，但需确认
- 文档 `01-PRD §15.1` 列出：草稿/待审批/已通过/**已拒绝**/已发送/已接受/**已拒绝**/已失效（文档自身重复列了"已拒绝"两次）。
- 代码 `quote/model.py:19-28` 拆成 `approval_rejected`（审批拒绝）与 `declined`（客户拒绝）。
- 冲突本质：文档自身有歧义（同一列表里"已拒绝"出现两次，推测本意是"审批拒绝"与"客户拒绝"各一）。代码的拆分是合理消歧，但**前端筛选下拉的取值需与之对齐**——需确认后再固化。

### 5.5 `04-UI` 设计稿要求 vs 已实现交互（多处）— 需裁决取舍
- `04-UI-V1.1-页面与交互设计文档.md` 存在（未被列为 source of truth，但与 PRD 相关）：详情页右侧常驻 AI 栏、商机 Kanban、报价 What-if 对比、客户"核心决策人"卡片。
- 代码：均未实现（`10-项目现状与交接说明.md:66` 自认）。
- 冲突本质：这三项在 PRD 里的强度不同——Kanban/多方案对比可追溯到 PRD §9.2/§15.2，而"右侧常驻 AI 栏""核心决策人卡片"**只在 UI 文档里，PRD 无对应条文**。需裁决是否纳入 V1.1 范围。

### 5.6 样品模块的"可选"边界 — 需裁决
- `01-PRD §19` 用"若启用独立样品模块"；`02-ER §13` 表标题写"样品（可选）"；但 `03-API §26` 写了 10 个接口且**没有**"可选"标注。
- 代码：完全未实现。
- 冲突本质：三份文档对"是否属于 V1.1 交付范围"表述不一致。且 PRD §9.2 商机阶段里**有 `样品` 阶段**（`seed.py:440`）——阶段存在但没有样品实体，业务走到"样品"阶段后无任何可录入对象。需领导确认（`08-待领导确认清单`）。

### 5.7 客户编号 / 引用数据源的"数字 ID"约定 — 需裁决
- `03-API §6` 转化示例用 `"customer_id": 1001`、`02-ER`/`base.py:3-4` 注释说"主键统一 BIGINT 自增（与 03-API 文档里的 1001 这类示例编号一致）"。
- 代码：`core/base.py:23-24` 确是自增 BIGINT，**但**业务单号（`quote_no`/`order_no`）走的是 `Q20250920 0001` 这类字符串（`quote/service.py:132`），与 PRD §2.6"编号规则"可配置的期望不一致。
- 冲突本质：ID 形态（自增数字 vs 业务编号）在文档里没有统一口径。若将来引入 `numbering_rules`，需明确"业务单号 ≠ 主键"这一分层，并决定历史数据是否迁移。

### 5.8 `customers.deleted_at` 与"公海"语义重叠 — 低风险
- ER §5 有 `deleted_at`（软删）；PRD §6.4 有"放入公海"（`pool_status`）。
- 代码两者都有（`customer/model.py:35,47`），但 `DELETE /customers/{id}` 走软删（`customer/router.py:130` + `service.py:184-186`），而"公海"客户仍可被列表查询（`customer/service.py:17-28` 对 `owner_id IS NULL` 放行）。
- 冲突本质：删除的客户是否应进入公海回收流程（PRD §6.4"自动回收"只看 `pool_status='private'`，`settings/service.py:124-129`，软删客户被排除）→ 需明确"删除"与"释放"的业务边界。

---

## 6. 端到端链路（PRD §26）达标状态

```text
ExternalContact  →  ❌ 缺失（无表无接口）
待归一            →  ❌ 缺失
Customer+Contact →  ✅ 可创建（POST /customers、POST /customers/{id}/contacts）
Opportunity      →  ✅ 可创建（POST /opportunities）
OpportunityItem  →  ✅ 可创建（POST /opportunities/{id}/items）
SKU              →  ✅ 可创建（POST /products/{id}/skus）
Pricing          →  ⚠️ 部分（缺国家/包装/物流方式/付款方式/利润要求；无历史落库）
Logistics        →  ❌ 缺失（无试算、无方案对比、无物流报价表）
Quote V1         →  ✅ 可生成（POST /quotes）
FollowUp         →  ✅ 可记录（POST /followups）
Task             →  ✅ 可创建（POST /tasks）
Quote V2         →  ✅ 可生成（POST /quotes/{id}/versions）
Approval         →  ⚠️ 可用（提交/同意/拒绝/撤回齐全；转交与审批定义管理缺失；汇率快照不写）
成交              →  ✅ 可标记（POST /opportunities/{id}/win）
SalesOrder       →  ✅ 可生成（POST /quote-versions/{id}/convert-to-order）
ERP/MES          →  ❌ 空壳（sync-erp 只写一条 skipped 日志）
ReceivablePlan   →  ✅ 可生成（POST /orders/{id}/receivables/generate）
PaymentRecord    →  ✅ 可登记+确认（POST /payments、/payments/{id}/confirm）
复购              →  ✅ 可生成（POST /orders/{id}/repurchase）
New Opportunity  →  ✅ 同上
```

**链路断点 4 处**：起点 `ExternalContact→待归一`（§3.4）、中段 `Logistics`（§3.1）、中段 `Pricing` 输入不全（§3.8）、末端 `ERP/MES`（§4.1）。

---

## 7. 关键代码证据速查（供实施时定位）

| 主题 | 文件:行 |
|---|---|
| 路由注册总表 | `backend/app/main.py:49-72` |
| 权限依赖 | `backend/app/core/deps.py:61-70`（管理员直通 + 任一权限命中） |
| 数据范围实现 | `backend/app/core/data_scope.py`；`customer/service.py:17-28` |
| 审计写入 | `backend/app/core/audit.py:48-71` |
| 错误码定义（含未使用项） | `backend/app/core/errors.py:9-29` |
| 核价主逻辑 | `backend/app/modules/pricing/service.py:238-445` |
| 核价运费估算（单费率） | `backend/app/modules/pricing/service.py:215-235` |
| 报价明细快照构建 | `backend/app/modules/quote/service.py:159-213` |
| 审批需求判定 | `backend/app/modules/quote/service.py:261-379` |
| 版本不可编辑 | `backend/app/modules/quote/service.py:149-156` |
| 报价/订单单号生成 | `backend/app/modules/quote/service.py:124-132`；`order/service.py:72-80` |
| 报价转订单（幂等） | `backend/app/modules/order/service.py:103-180` |
| ERP 推送空壳 | `backend/app/modules/order/router.py:218-249` |
| 未被调用的 ERP 映射写入 | `backend/app/modules/order/service.py:214-225` |
| 客户查重打分 | `backend/app/modules/contact_util.py:48-149`；权重 `settings/service.py:59-67` |
| 公海回收规则执行 | `backend/app/modules/settings/service.py:112-156` |
| 自动任务规则执行 | `backend/app/modules/settings/service.py:168-291` |
| Agent 风险网关 | `backend/app/modules/agent/runtime.py:200-279` |
| Agent 工具清单/标签 | `backend/app/modules/agent/tools.py:54-67`，实现 `:123-704` |
| 种子数据（阶段/原因/参数） | `backend/scripts/seed.py:434-482`、`:825-872` |
| 前端路由（含两个占位页） | `frontend/src/app/router.tsx:70-93` |
| 前端菜单（ready 标记） | `frontend/src/app/menu.ts:41-85` |
| 统一 HTTP 客户端 | `frontend/src/shared/api/client.ts:18-77` |

---

## 8. 建议实施顺序（严格按"文档强制 + 卡链路"排序，非泛化建议）

1. **企业微信骨架**（表 4 张 + 同步接口 + 待归一页 + 离职继承）— 否则 PRD §26 链路无起点，P0/L。
2. **物流试算**（`logistics_quotes` 表 + §19 六接口 + 核价接入 + 报价运费来源）— P0/L，与 §1.18、§3.1 绑定。
3. **核价输入输出对齐**（补 country/packaging/shipping_method/payment_terms/profit 要求；拆分"保护价"与"授权底价"）— P0/M，改 `PricingRequest` + `calculate_price` + 两个前端页。
4. **客户标签 + 客户合并**（2 张表 + 6 接口 + 列表列/筛选 + 合并确认弹窗）— P0/M。
5. **报价汇率快照落库 + 明细成本口径统一 + 审批转交** — P0-S/M，都是小改动但直接影响数据可信度。
6. **编号规则 + 并发/重号治理**（`numbering_rules` 表 + 3 接口 + 把两处单号生成改为规则驱动 + `QuoteVersion` 唯一约束）— P1/M。
7. **批量操作 + Excel 导入导出补齐（线索/产品/SKU）** — P1/M。
8. **多方案对比 + Kanban + 高级筛选字段** — P1/M（前端为主）。
9. **Agent 工具补齐 17 项 + 专用七接口 + 执行记录补 user/role/scope** — P1/M-L。
10. **用户/部门/角色/权限写接口 + 通知设置 + 企微通知渠道 + 审计详情接口** — P1/M。
11. **ERP/MES 真实对接（外部依赖，需对方系统就绪）** — 排期上独立。
12. **样品模块、产品附件/标签、文件预览、知识库页** — P2，按领导确认结果决定是否进 V1.1。
