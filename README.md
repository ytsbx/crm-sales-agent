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

## 中间件（本项目自带，独立容器）

本仓库自带 `ops/docker-compose.yml`，起一套 CRM 专用的 PostgreSQL 15 + Redis 7：

```bash
docker compose -f ops/docker-compose.yml up -d      # 启动
docker compose -f ops/docker-compose.yml ps         # 看状态（应为 healthy）
docker compose -f ops/docker-compose.yml down       # 停止（保留数据）
docker compose -f ops/docker-compose.yml down -v    # 停止并清空数据
```

| 服务 | 地址 | 账号 / 库 |
|---|---|---|
| PostgreSQL 15 | 127.0.0.1:**5433** | `crm / crm123456`，库 `crm_sales_agent` |
| Redis 7 | 127.0.0.1:**6381** | CRM 用 db 2 |

> **为什么端口不是默认的 5432 / 6379**：这台机器上 5432 被 `nexus-postgres`、
> 6379 被 `nexus-redis` 占用（别的项目在用）。为了互不干扰，CRM 用 5433 / 6381
> 并跑在独立容器里，可以整组启停。
>
> Elasticsearch / Qdrant 留给将来 Agent 知识库（RAG）用，当前版本没接。

## 启动（Windows / macOS / Linux 通用）

### 后端

```bash
# ---- 首次 ----
cd backend
cp .env.example .env            # 然后把 DATABASE_URL 端口改成 5433、REDIS_URL 改成 6381
uv venv --python 3.12 .venv     # 没有 uv 就用 python -m venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt   # macOS/Linux 用 .venv/bin/python
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m scripts.seed

# ---- 启动 ----
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

> Windows 下可执行文件在 `.venv\Scripts\`，macOS/Linux 在 `.venv/bin/`；下文只写前者。

接口文档：<http://127.0.0.1:8000/docs>

### 前端

```bash
cd frontend
pnpm install        # 没有 pnpm 就用 npm install
pnpm dev            # http://127.0.0.1:5173
```

开发期前端通过 Vite 代理把 `/api` 转给后端 8000 端口，无需额外跨域配置。

## 常驻运行（推荐，不依赖终端）

手工起的 dev server 会随终端关闭而死。用 `ops` 下的脚本 + 计划任务可以让网站常驻：

```powershell
powershell -ExecutionPolicy Bypass -File ops\start_backend.ps1    # 起后端（已在跑则跳过）
powershell -ExecutionPolicy Bypass -File ops\start_frontend.ps1   # 起前端
powershell -ExecutionPolicy Bypass -File ops\stop_services.ps1    # 停两个服务

# 注册开机自启（需要「以管理员身份运行」的 PowerShell）
powershell -ExecutionPolicy Bypass -File ops\install_services.ps1
```

| 文件 | 作用 |
|---|---|
| `ops/docker-compose.yml` | PostgreSQL 15 + Redis 7（5433 / 6381） |
| `ops/start_backend.ps1` | 起 uvicorn，带崩溃自动重启；日志写 `ops/logs/` |
| `ops/start_frontend.ps1` | 起 Vite，带崩溃自动重启；日志写 `ops/logs/` |
| `ops/stop_services.ps1` | 按端口停止，且只停本项目自己的进程 |
| `ops/install_services.ps1` | 注册 `CRM-Backend` / `CRM-Frontend` 登录自启任务并立即启动 |
| `ops/smoke_ui.mjs` | 逐页截图冒烟测试，见下节 |

> 计划任务必须在**以管理员身份运行**的 PowerShell 里注册（`Register-ScheduledTask` 需要提权）。
> 注册前先跑一次 `stop_services.ps1`：start 脚本发现端口被占用会跳过启动，
> 否则任务会空转、而旧的手工进程继续占着端口。
>
> 这些脚本刻意只用 ASCII 字符：Windows PowerShell 5.1 在文件没有 BOM 时按 GBK 读 `.ps1`，
> 中文注释会导致语法错误。

## 演示账号

| 账号 | 密码 | 角色 | 数据范围 |
|---|---|---|---|
| admin | admin123 | 管理员 | 全部 |
| lisi | 123456 | 销售主管 | 本部门及下级 |
| zhangsan | 123456 | 业务员 | 仅本人 |

## 用脚本测接口时的两个坑（都踩过，别再犯）

1. **Windows PowerShell 5.1 发中文 JSON 必须显式转 UTF-8**。
   `Invoke-RestMethod -Body '{"destination":"华东"}'` 默认按 Latin-1 编码，
   服务端收到的是乱码，于是"明明有华东费率却匹配不到"，看起来像后端 bug。
   正确写法：

   ```powershell
   $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
   Invoke-RestMethod -Uri $url -Method Post -Headers $h -Body $bytes
   ```

   排查这类问题时，先用纯 ASCII 值（如 `DST_A`）跑一遍：若 ASCII 正常而中文异常，
   就是编码问题，不是业务逻辑问题。

2. **不要同时留多个 `start_backend.ps1` / `start_frontend.ps1`**。
   脚本有"端口已监听则跳过"的保护，但两个 supervisor 同时存在时会各起一个 uvicorn，
   其中一个绑定失败却仍在运行，表现为"代码明明是新的，接口却返回旧结果"。
   重启用 `ops\stop_services.ps1` 先停干净，再起一个。

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

## 企业微信集成（框架已就绪，等凭据）

PRD §8 的四块（部门/成员同步、外部联系人同步、待归一、离职继承）与 API §10 的
13 个接口都已实现，前端「企业微信」页是设计稿的三栏布局（左待处理 / 中企微详情 / 右候选客户）。

**差的就是凭据。** 在 `backend/.env` 里补齐后重启后端即可，代码不用改：

```text
WECOM_CORP_ID=ww...
WECOM_AGENT_ID=1000002
# 通讯录同步密钥（部门 / 成员）
WECOM_CONTACT_SECRET=...
# 客户联系密钥（外部联系人），企微里是独立的一把
WECOM_EXTERNAL_CONTACT_SECRET=...
# 事件回调，企微要求公网 HTTPS
WECOM_CALLBACK_TOKEN=...
WECOM_CALLBACK_AES_KEY=...
```

回调地址填：`https://<你的公网域名>/api/v1/webhooks/wecom/events`
（GET 用于企微后台的 URL 校验，POST 收事件；两者都靠签名校验，不需要登录）。

未配置期间的行为是**刻意设计**的：

- 同步接口返回 `50202` 并说明缺哪个变量，**不会**静默返回"成功 0 条"——
  否则运营会以为企微里真的没人；
- 「企业微信」页顶部横幅列出缺哪些配置，页面与接口可以直接联调；
- 离职继承传 `transfer_wecom=false` 可先只转 CRM 侧（客户/商机/任务负责人），
  不依赖企微凭据。

一个已知的待办：事件回调目前只把报文记进 `wecom_sync_jobs`（便于确认"企微推了什么"），
真正的增量同步（收到 `change_external_contact` 只拉那一个人）等拿到真实回调再写——
没有真实报文的情况下写增量逻辑只能靠猜。
