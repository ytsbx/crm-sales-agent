# 报价驱动型销售 CRM + Sales Agent V1.1

面向国内销售团队（人民币、中文）的销售 CRM，核心是把
**客户 → 商机 → 选品 → 核价 → 报价（多版本 + 审批）→ 跟进 → 成交 → 订单 → 回款**
这条链管住。

当前进度见 [07-开发计划与阶段划分-V1.1.md](./07-开发计划与阶段划分-V1.1.md)。

---

## 目录结构

```text
CRM-Sales-Agent-V1.1/
├── 01-PRD … 07-开发计划    设计文档与施工计划
├── backend/                FastAPI + SQLAlchemy + Alembic
│   ├── app/core/           配置、数据库、统一响应、错误码、认证、审计
│   ├── app/modules/        业务模块（auth / user / customer / product / lead /
│   │                       opportunity / pricing / quote / approval / followup /
│   │                       task / timeline / order / payment / file / settings /
│   │                       notification / analytics）
│   ├── alembic/            数据库迁移
│   └── scripts/seed.py     初始数据（角色、权限、账号、示例客户/产品/商机/价格）
├── frontend/               React 19 + TypeScript + Vite + Semi Design
│   └── src/
│       ├── app/            路由、布局、多页签
│       ├── modules/        业务页面（auth / workbench / lead / customer / product /
│       │                   opportunity / pricing / quote / approval / order /
│       │                   task / analytics / settings）
│       └── shared/         API 客户端、状态、类型
└── ops/smoke_ui.mjs        界面冒烟测试（无头浏览器逐页截图）
```

## 中间件（复用本机已有服务，不新装）

| 服务 | 地址 | 来源 |
|---|---|---|
| PostgreSQL 15 | 127.0.0.1:5432 | Homebrew，库 `crm_sales_agent`，账号 `crm / crm123456` |
| Redis 7 | 127.0.0.1:6379 | Docker（rag-assistant-redis），CRM 用 db 2 |
| Elasticsearch / Qdrant | 9200 / 6333 | Docker，留给 Phase 6 的 Agent 知识库 |

## 启动

### 后端

```bash
cd backend
cp .env.example .env          # 首次
uv venv --python 3.12 .venv   # 首次
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

接口文档：<http://127.0.0.1:8000/docs>

### 前端

```bash
cd frontend
pnpm install        # 首次
pnpm dev            # http://localhost:5173
```

开发期前端通过 Vite 代理把 `/api` 转给后端 8000 端口，无需额外跨域配置。

## 演示账号

| 账号 | 密码 | 角色 | 数据范围 |
|---|---|---|---|
| admin | admin123 | 管理员 | 全部 |
| lisi | 123456 | 销售主管 | 本部门及下级 |
| zhangsan | 123456 | 业务员 | 仅本人 |

## 界面冒烟测试

改完前端或后端后，一条命令确认「页面能打开、数据能读出来、控制台无报错」：

```bash
node ops/smoke_ui.mjs                 # 截图默认落在临时目录
SMOKE_OUT=/tmp/crm-shots node ops/smoke_ui.mjs
```

它用浏览器调试协议驱动 Edge/Chrome，不依赖 Playwright。

## 文件存储

当前用本地磁盘（`backend/data/files`），配置项在 `backend/.env`：

```text
STORAGE_PROVIDER=local
FILE_ROOT=data/files
MAX_UPLOAD_MB=20
```

字段按对象存储设计（`storage_provider` + `object_key`），
将来换成 MinIO/OSS 只需在 `app/modules/file/storage.py` 增加一个适配器，业务表不用动。

## 约定

- 接口统一返回 `{ "code": 0, "message": "ok", "data": ... }`，非 0 即业务错误；
- 所有写接口都要校验权限并写审计日志；
- 业务数据按角色数据范围（self / department / department_and_sub / all）过滤，禁止只靠前端隐藏；
- 报价类数据必须存快照，已发送或已审批的版本不可原地修改（Phase 3 落地）；
- 数据库变更一律走 Alembic，禁止手改表结构。

## Sales Agent（DeepSeek）

Agent 接的是 DeepSeek（与公司现有知识库项目共用同一套配置），在 `backend/.env` 里配置：

```text
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

未配置 Key 时 Agent 会明确提示"还没有配置模型"，其余功能不受影响。

风控规则（写在 `app/modules/agent/tools.py` 的每个工具上）：

| 等级 | 含义 | 例子 |
|---|---|---|
| L1 | 自动执行 | 查客户、查商机、核价、查回款 |
| L2 | 用户确认后执行 | 记录跟进、创建任务、改商机下一步动作 |
| L3 | 审批后执行 | 低价报价提交审批（Agent 不能自己批） |

每次工具调用都会写 `agent_executions`，写动作额外写 `audit_logs`（来源标为 AGENT），
所以"AI 建议了什么、谁确认的、改了什么"全程可查。
