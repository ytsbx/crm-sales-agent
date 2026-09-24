# 05-TECH：报价驱动型销售 CRM + Sales Agent V1.1
## 技术架构设计文档

> 版本：V1.1  
> 状态：开发基线  
> 适用范围：Web CRM、企业微信集成、ERP/MES 集成、报价引擎、销售任务、Sales Agent

---

# 1. 架构目标

本系统技术架构需要满足以下目标：

1. 支撑“线索 → 客户 → 商机 → 报价 → 订单 → 回款 → 复购”的完整业务链；
2. 保证报价、审批、订单、回款等核心业务数据一致性；
3. 支持企业微信、ERP/MES、物流等第三方系统稳定集成；
4. 支持 Sales Agent 在权限、审批、审计机制下执行真实业务动作；
5. 支持后续业务快速调整，避免早期过度微服务化；
6. 允许未来按业务域逐步拆分服务；
7. 保证 Agent 故障时 CRM 主业务仍可正常运行；
8. 具备基本的监控、日志、重试、备份和审计能力。

---

# 2. 架构原则

## 2.1 V1 采用模块化单体，不采用全微服务

推荐：

> **Modular Monolith + Independent Worker + Independent Agent Runtime**

原因：

- CRM 各模块之间事务关系强；
- 当前业务规则仍在快速变化；
- 团队规模不适合维护大量微服务；
- 部署、调试和排障成本更低；
- 后续仍可按领域模块拆分。

不建议 V1 直接引入：

- Kubernetes
- Kafka
- Service Mesh
- 服务注册中心
- 多数据库分片
- 大量独立微服务

---

## 2.2 Agent 不允许直接操作数据库

禁止：

```text
LLM
↓
SQL
↓
Database
```

必须：

```text
LLM
↓
Tool
↓
Action Gateway
↓
Permission / Risk Check
↓
Business Service
↓
Repository
↓
Database
```

所有 Agent 写操作必须复用 CRM 本身的业务逻辑。

---

## 2.3 外部系统全部通过 Adapter 接入

企业微信、ERP/MES、物流等不直接侵入业务模块。

```text
Business Service
↓
Integration Interface
↓
Adapter
↓
External API
```

---

## 2.4 PostgreSQL 是业务唯一真相源

V1 核心数据统一进入 PostgreSQL。

Redis 只负责：

- 缓存
- 队列
- 分布式锁
- 临时状态
- 限流

MinIO / OSS 负责文件。

---

## 2.5 核心业务动作必须可审计

尤其：

- 客户转移
- 商机阶段变化
- 报价修改
- 价格修改
- 审批
- 订单生成
- 回款确认
- Agent 执行

---

# 3. 总体技术架构

```text
                         ┌─────────────────────────┐
                         │        用户入口          │
                         │ Web / 企业微信工作台      │
                         └────────────┬────────────┘
                                      │
                                  HTTPS
                                      │
                               ┌──────▼──────┐
                               │    Nginx    │
                               └──────┬──────┘
                                      │
                         ┌────────────▼────────────┐
                         │      CRM Frontend        │
                         │ React + TS + Vite        │
                         └────────────┬────────────┘
                                      │ REST / SSE
                                      │
                  ┌───────────────────▼───────────────────┐
                  │             FastAPI Backend            │
                  │                                        │
                  │ Auth / RBAC / Data Scope              │
                  │ Lead / Customer / Contact              │
                  │ Opportunity / Product / Pricing        │
                  │ Quote / Approval / Task                │
                  │ Order / Receivable / Payment           │
                  │ Analytics / Audit                      │
                  └───────┬─────────────┬─────────────┬────┘
                          │             │             │
                 ┌────────▼───┐   ┌────▼─────┐  ┌───▼────────┐
                 │ PostgreSQL │   │  Redis   │  │ MinIO/OSS  │
                 │ + pgvector │   │Cache/Queue│  │   Files    │
                 └────────────┘   └────┬─────┘  └────────────┘
                                      │
                              ┌───────▼────────┐
                              │ Async Workers  │
                              │ Celery         │
                              └───────┬────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          │                           │                           │
┌─────────▼────────┐       ┌──────────▼──────────┐      ┌────────▼────────┐
│ WeCom Adapter     │       │ ERP / MES Adapter   │      │ Logistics      │
│ 企业微信           │       │ 订单/履约同步        │      │ Adapter        │
└──────────────────┘       └─────────────────────┘      └─────────────────┘

                         ┌─────────────────────────┐
                         │      Sales Agent        │
                         │ LLM + Tool Calling      │
                         │ RAG + Action Gateway    │
                         └────────────┬────────────┘
                                      │
                              Business API / Service
                                      │
                                      ▼
                               FastAPI Backend
```

---

# 4. 技术选型

| 层级 | 技术 | 用途 |
|---|---|---|
| 前端 | React 19 | CRM Web |
| 语言 | TypeScript | 前端强类型 |
| 构建 | Vite 8 | 前端构建 |
| 路由 | React Router 7 | 页面路由 |
| UI | Semi Design | 主组件库 |
| Server State | TanStack Query | API 数据缓存 |
| Client State | Zustand | 页面状态 |
| 后端 | FastAPI | REST API / SSE |
| ORM | SQLAlchemy 2 | ORM |
| Schema | Pydantic 2 | DTO / 校验 |
| Migration | Alembic | 数据库迁移 |
| DB | PostgreSQL 16+ | 核心业务数据 |
| Vector | pgvector | RAG 向量 |
| Cache | Redis | 缓存/锁/队列 |
| Async | Celery | 异步任务 |
| Scheduler | Celery Beat | 定时任务 |
| Object Storage | MinIO / OSS | 文件 |
| Reverse Proxy | Nginx | HTTPS / 反代 |
| Container | Docker / Compose | 部署 |
| AI | LLM + Tool Calling | Sales Agent |
| RAG | pgvector + Embedding + Rerank | 产品/知识检索 |
| Error Tracking | Sentry | 前后端异常 |
| Logging | JSON Structured Log | 日志 |
| CI/CD | GitHub Actions | 构建部署 |

---

# 5. 前端架构

## 5.1 前端技术栈

```text
React 19
TypeScript
Vite 8
React Router 7
Semi Design
TanStack Query
Zustand
```

原则：

- Semi Design 作为唯一主 UI 组件库；
- Ant Design 仅保留少量无法替代的特殊组件；
- 新页面禁止继续无规则混用两套组件库。

---

## 5.2 前端目录建议

```text
frontend/
├── src/
│   ├── app/
│   │   ├── router/
│   │   ├── layout/
│   │   └── providers/
│   │
│   ├── modules/
│   │   ├── lead/
│   │   ├── customer/
│   │   ├── contact/
│   │   ├── opportunity/
│   │   ├── product/
│   │   ├── pricing/
│   │   ├── quote/
│   │   ├── approval/
│   │   ├── task/
│   │   ├── order/
│   │   ├── payment/
│   │   └── analytics/
│   │
│   ├── agent/
│   ├── shared/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── api/
│   │   ├── utils/
│   │   └── types/
│   │
│   └── main.tsx
```

---

## 5.3 前端状态划分

### Server State

使用 TanStack Query：

- 客户列表
- 商机详情
- 报价详情
- 任务
- 订单
- 回款
- 分析数据

### Client State

使用 Zustand：

- 多页面标签
- 当前用户 UI 偏好
- 页面筛选缓存
- Agent 面板状态
- 临时草稿状态

不建议把大量服务端数据重复存进 Zustand。

---

## 5.4 多页面标签

保留现有多页面标签能力。

要求：

- 关闭页面不丢列表筛选；
- 返回列表保留页码；
- 客户 → 商机 → 报价可直接跳转；
- 同一个实体避免重复打开多个相同标签；
- URL 与当前业务对象保持一致。

---

# 6. 后端架构

## 6.1 架构模式

采用：

> **DDD-lite + Modular Monolith**

每个业务域独立模块。

```text
backend/
├── app/
│   ├── core/
│   │   ├── config/
│   │   ├── auth/
│   │   ├── permission/
│   │   ├── database/
│   │   ├── events/
│   │   ├── audit/
│   │   ├── exceptions/
│   │   └── idempotency/
│   │
│   ├── modules/
│   │   ├── lead/
│   │   ├── customer/
│   │   ├── contact/
│   │   ├── opportunity/
│   │   ├── product/
│   │   ├── pricing/
│   │   ├── logistics/
│   │   ├── quote/
│   │   ├── approval/
│   │   ├── followup/
│   │   ├── task/
│   │   ├── order/
│   │   ├── receivable/
│   │   ├── payment/
│   │   └── analytics/
│   │
│   ├── integrations/
│   │   ├── wecom/
│   │   ├── erp/
│   │   ├── mes/
│   │   └── logistics/
│   │
│   └── agent/
│       ├── runtime/
│       ├── tools/
│       ├── actions/
│       ├── rag/
│       └── guards/
│
├── workers/
├── migrations/
├── tests/
└── main.py
```

---

# 7. 单模块内部结构

例如：

```text
customer/
├── router.py
├── schema.py
├── model.py
├── repository.py
├── service.py
├── permission.py
├── events.py
└── exceptions.py
```

职责：

### router.py

- HTTP
- 参数
- Response
- 身份上下文

### service.py

- 业务规则
- 事务编排
- 状态校验
- 领域动作

### repository.py

- 查询
- 持久化
- ORM

### permission.py

- 数据范围
- 操作权限

### events.py

- 业务事件

---

# 8. Service 层原则

禁止：

```text
Router
↓
直接 ORM
↓
Database
```

推荐：

```text
Router
↓
Application Service
↓
Domain Rule
↓
Repository
↓
PostgreSQL
```

例如：

```text
POST /quote-versions/{id}/convert-to-order
```

流程：

```text
QuoteService.convert_to_order()
↓
验证报价状态
↓
验证审批状态
↓
验证版本
↓
验证客户
↓
创建 SalesOrder
↓
创建 SalesOrderItem
↓
更新 Opportunity
↓
写 AuditLog
↓
发布 OrderCreatedEvent
```

---

# 9. 数据库架构

## 9.1 主数据库

PostgreSQL 保存：

- 组织权限
- 线索
- 客户
- 联系人
- 商机
- 产品
- SKU
- 成本
- 价格
- 报价
- 订单
- 回款
- 审批
- 任务
- 审计
- Agent 行为

---

## 9.2 JSONB 使用边界

可用于：

- 第三方 API 原始响应
- 可扩展规则配置
- Agent proposed_payload
- Integration raw_data

不用于替代关键业务字段。

---

## 9.3 pgvector

用于：

- 产品资料
- FAQ
- 销售知识
- 报价规则说明
- 客户背景资料检索

业务事实仍从结构化数据库查询。

---

# 10. Redis 设计

Redis 用于：

- API 缓存
- Celery Broker
- Celery Result Backend
- 分布式锁
- Idempotency Key
- 短期 Agent 状态
- 限流
- Webhook 去重

不作为永久业务数据库。

---

# 11. 异步任务架构

采用：

```text
Celery + Redis
```

适合：

- 企业微信同步
- ERP/MES 推送
- 外部接口重试
- 报价 PDF 生成
- 邮件发送
- 企业微信通知
- 定时任务
- 数据统计
- AI 长任务

---

# 12. 业务事件机制

V1 不需要 Kafka。

先采用：

```text
DB Transaction
↓
Business Event
↓
Outbox / Worker
↓
Async Handler
```

建议增加 Outbox 表，避免：

> 数据库提交成功，但异步消息没有发送。

示例：

```text
OrderCreated
↓
Outbox
↓
Worker
↓
ERPAdapter
```

---

# 13. Outbox Pattern

表：

```text
outbox_events
```

字段：

- id
- event_type
- aggregate_type
- aggregate_id
- payload
- status
- retry_count
- available_at
- created_at
- processed_at

Worker 轮询未处理事件。

V1 这是比直接上 Kafka 更合适的可靠方案。

---

# 14. 企业微信 Adapter

```text
CRM
↓
WeComService
↓
WeComAdapter
↓
企业微信 API
```

负责：

- 部门同步
- 成员同步
- 外部联系人同步
- 跟进关系同步
- 离职客户转移
- Webhook 事件
- 消息通知

所有同步写 integration_log。

---

# 15. ERP / MES Adapter

```text
SalesOrder
↓
OrderCreatedEvent
↓
ERPAdapter
↓
ERP / MES
```

负责：

- 销售订单推送
- 履约状态同步
- 发货状态同步
- 外部订单 ID 映射
- 错误重试

CRM 不直接依赖某一个 ERP/MES 的字段结构。

---

# 16. 外部系统映射

建议独立：

```text
external_mappings
```

字段：

- system_type
- business_type
- internal_id
- external_id
- external_code
- last_sync_at

避免每接一个系统就在业务表增加大量字段。

---

# 17. Sales Agent 架构

```text
User
↓
Agent Session
↓
LLM Runtime
↓
Planning / Tool Selection
↓
Tool
↓
Action Gateway
↓
Risk Guard
↓
Permission Check
↓
Business Service
↓
Database
```

---

# 18. Agent Runtime

职责：

- 会话上下文
- 模型调用
- Tool Calling
- RAG
- Action Proposal
- 执行结果回传
- Retry

Agent 不维护业务真相。

---

# 19. Agent Tool

建议：

```text
LeadTool
CustomerTool
ContactTool
OpportunityTool
ProductTool
PricingTool
LogisticsTool
QuoteTool
FollowUpTool
TaskTool
OrderTool
PaymentTool
AnalyticsTool
```

Tool 不应直接访问 ORM。

推荐：

```text
Tool
↓
Application Service
```

---

# 20. Agent Action Gateway

所有写动作必须经过统一 Gateway。

```text
Agent Proposed Action
↓
Action Gateway
↓
Risk Level
↓
Permission
↓
Need Confirm?
↓
Need Approval?
↓
Execute
↓
Audit
```

---

# 21. Agent 风险等级

## L1

允许自动：

- 查询
- 总结
- 数据分析
- 生成任务
- 生成草稿

## L2

用户确认：

- 修改商机
- 修改客户
- 新建报价版本
- 创建订单

## L3

审批：

- 低价报价
- 大额折扣
- 特殊账期
- 重要客户转移
- 删除重要数据

---

# 22. Agent RAG

检索来源：

- 产品资料
- 产品知识
- 销售 SOP
- 报价规则
- 客户资料
- 历史报价说明
- FAQ

检索流程：

```text
Query
↓
Metadata Filter
↓
Vector Recall
↓
Keyword Recall
↓
Rerank
↓
Context
↓
LLM
```

结构化事实优先使用 Tool 查询，不通过 RAG 猜测。

---

# 23. 权限架构

采用：

> **RBAC + Data Scope + Price Permission**

## RBAC

```text
User
↓ N:N
Role
↓ N:N
Permission
```

## Data Scope

- self
- department
- department_and_sub
- all

## Price Permission

单独控制：

- 最低价格
- 最低利润率
- 最大折扣
- 特殊条件审批

业务权限与价格权限分离。

---

# 24. 数据权限实现

Repository 查询自动带入 Scope。

例如：

```text
业务员
WHERE owner_id = current_user.id
```

主管：

```text
WHERE department_id IN (...)
```

禁止只依靠前端隐藏菜单实现权限。

---

# 25. 审计架构

AuditLog 来源：

- WEB
- API
- AGENT
- INTEGRATION
- SYSTEM

记录：

- 操作人
- 对象
- 动作
- before
- after
- IP
- trace_id
- 时间

---

# 26. 事务策略

强事务场景：

- Lead 转 Customer
- Customer Merge
- 报价新版本
- 报价审批
- 商机成交
- Quote → Order
- 回款确认
- 客户负责人转移

示例：

```text
BEGIN
校验 QuoteVersion
校验 Approval
锁定版本
创建 Order
创建 OrderItems
更新 Opportunity
写 Audit
写 Outbox
COMMIT
```

失败：

```text
ROLLBACK
```

---

# 27. 幂等

需要 Idempotency Key：

- Lead 转化
- 报价转订单
- 企业微信同步
- ERP/MES 推送
- 回款确认
- Agent 写动作
- Webhook

避免重复：

- 订单
- 回款
- 任务
- 报价版本

---

# 28. 并发控制

推荐：

- PostgreSQL Row Lock
- Optimistic Version
- Redis Lock（仅必要场景）

例如：

```text
QuoteVersion.version
```

更新时：

```text
WHERE id=? AND version=?
```

---

# 29. API 通信

前端与后端：

- REST：普通 CRUD
- SSE：Agent 流式输出
- WebSocket：暂不作为 V1 必需

第三方：

- HTTP API
- Webhook
- Async Worker

---

# 30. 文件架构

```text
Frontend
↓
Upload API
↓
MinIO / OSS
↓
files table
↓
business_files
```

支持：

- 产品图片
- 报价 PDF
- 合同
- 回款凭证
- 客户附件
- Agent 参考文件

---

# 31. 日志与可观测性

## 应用日志

JSON 格式：

```json
{
  "trace_id": "...",
  "user_id": 1001,
  "module": "quote",
  "action": "create_version",
  "status": "success"
}
```

## 建议

- Sentry：异常
- Nginx Access Log
- FastAPI Structured Log
- Celery Worker Log
- Integration Log
- Agent Execution Log

---

# 32. Trace ID

每个请求生成：

```text
trace_id
```

并贯穿：

```text
Frontend
→ API
→ Worker
→ ERP/MES
→ Audit
→ IntegrationLog
```

方便排查：

> “这个报价为什么没同步过去？”

---

# 33. 监控指标

最低需要：

- API QPS
- API P95
- 5xx 数量
- Worker 队列长度
- Worker 失败量
- 企业微信同步失败
- ERP 同步失败
- Agent Tool 失败
- PostgreSQL 连接数
- Redis 状态
- 磁盘容量

---

# 34. 安全

要求：

- HTTPS
- JWT / SSO
- Password Hash
- RBAC
- Data Scope
- Price Permission
- Rate Limit
- CSRF（如使用 Cookie Session）
- SQL Injection 防护
- 文件类型校验
- 文件大小限制
- Webhook 签名校验
- Secret 环境变量管理

敏感数据日志中脱敏。

---

# 35. 数据备份

PostgreSQL：

- 每日自动备份
- 保留 7/30 天策略
- 异地备份
- 定期恢复演练

MinIO / OSS：

- 开启对象版本或生命周期策略

---

# 36. 环境划分

```text
local
dev
test
staging
production
```

生产环境禁止直接执行未审核 migration。

---

# 37. 配置管理

使用：

```text
.env
+
Pydantic Settings
```

生产 Secret 不提交 Git。

配置：

- DB
- Redis
- MinIO
- 企业微信
- ERP/MES
- LLM
- Embedding
- Email
- Sentry

---

# 38. 数据库迁移

使用 Alembic。

流程：

```text
修改 Model
↓
生成 Migration
↓
开发验证
↓
测试环境
↓
备份
↓
生产执行
```

禁止生产库手工改表后不记录 migration。

---

# 39. 测试架构

## Unit Test

测试：

- Pricing
- Approval
- Quote
- Permission
- Agent Guard

## Integration Test

测试：

- DB
- WeCom Adapter
- ERP Adapter
- Redis
- Worker

## E2E

至少覆盖最终主链：

```text
企业微信联系人
→ 客户
→ 商机
→ 报价
→ 审批
→ 成交
→ ERP
→ 回款
```

---

# 40. CI/CD

GitHub Actions：

```text
Push / PR
↓
Lint
↓
Type Check
↓
Unit Test
↓
Build
↓
Docker Image
↓
Staging
↓
Manual Approval
↓
Production
```

---

# 41. Docker 部署

V1 推荐：

```text
docker-compose.yml

nginx
crm-web
crm-api
crm-agent
crm-worker
crm-beat
postgres
redis
minio
```

---

# 42. 进程隔离

建议：

```text
crm-api
crm-worker
crm-agent
crm-beat
```

Agent 单独进程。

即使：

- LLM 超时
- Agent 崩溃
- Embedding 服务异常

CRM 主业务仍可运行。

---

# 43. 生产部署拓扑

```text
Internet / Intranet
        │
      Nginx
        │
 ┌──────┴────────────┐
 │                   │
crm-web           crm-api
                     │
         ┌───────────┼───────────┐
         │           │           │
     PostgreSQL    Redis       MinIO
                     │
           ┌─────────┴─────────┐
           │                   │
       crm-worker          crm-agent
           │                   │
           ├─ WeCom            ├─ LLM
           ├─ ERP/MES          └─ RAG
           └─ Logistics
```

---

# 44. 扩展路线

## V1

模块化单体。

## V2

如某模块压力明显，可拆：

- Agent Service
- Integration Service
- Analytics Service

## V3

达到较大规模后再考虑：

- Kafka
- Elasticsearch / OpenSearch
- Kubernetes
- 独立数据仓库

不提前建设。

---

# 45. 开发顺序

建议严格按照：

```text
ER
↓
Model
↓
Repository
↓
Service
↓
Permission
↓
API
↓
Test
↓
UI
↓
Agent Tool
```

不要：

```text
先做 UI
↓
再猜数据库
```

也不要：

```text
Agent Tool
↓
直接操作数据库
```

---

# 46. 模块开发模板

每个模块完成前必须具备：

- Model
- Migration
- Repository
- Service
- API
- Permission
- Audit
- Unit Test
- UI
- API 文档
- Agent Tool（需要时）

---

# 47. 技术验收标准

## CRM

- 核心业务链可跑通；
- 权限有效；
- 数据范围有效；
- 报价历史不可覆盖；
- 审批状态正确；
- ERP/MES 同步可重试；
- 日志可追踪；
- 文件可追踪；
- 数据可备份。

## Agent

- 不直接写数据库；
- Tool 经过权限校验；
- L2 需要用户确认；
- L3 需要审批；
- 每次执行有记录；
- Tool 失败可追踪；
- Agent 不可绕过业务 Service。

---

# 48. 最终推荐技术栈

```text
Frontend
React 19
TypeScript
Vite 8
React Router 7
Semi Design
TanStack Query
Zustand

Backend
Python
FastAPI
Pydantic 2
SQLAlchemy 2
Alembic

Data
PostgreSQL 16+
pgvector
Redis
MinIO / OSS

Async
Celery
Celery Beat
Outbox Pattern

AI
LLM
Tool Calling
RAG
Embedding
Rerank
Action Gateway

Integration
WeCom Adapter
ERP/MES Adapter
Logistics Adapter

Infra
Nginx
Docker Compose
GitHub Actions
Sentry
Structured Logging
```

---

# 49. 最终架构结论

V1 技术架构正式确定为：

> **React + FastAPI + PostgreSQL + Redis + Celery + MinIO + Sales Agent + Adapter Integration**

架构形态：

> **模块化单体 CRM + 独立异步 Worker + 独立 Agent Runtime + Adapter 集成层**

该结构能够在不增加过度运维复杂度的前提下，支撑当前 CRM 的完整业务闭环，并为未来拆分 Agent、集成服务和分析服务保留清晰边界。
