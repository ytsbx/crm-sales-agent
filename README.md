# 报价驱动型销售 CRM + Sales Agent V1.1

面向国内销售团队（人民币、中文）的销售 CRM，核心是把
**客户 → 商机 → 选品 → 核价 → 报价（多版本 + 审批）→ 跟进 → 成交 → 订单 → 回款**
这条链管住，上面再盖一层能查数据、算价、提议动作的 Sales Agent（DeepSeek）。

**规模**：79 张 ORM 表、约 30 个后端业务模块、28 个前端页面组件；
回归清单见 `ops/check_suites.txt`，单元测试见 `backend/tests`，UI 冒烟见 `ops/smoke_ui.mjs`，另有 GitHub Actions CI
（接口对账用 `ops/api_gap.py`，2026-09-29 复核"文档有、代码没有"0 条）。

## 先读哪份文档

| 你是谁 | 读这份 |
|---|---|
| **新接手的 agent 会话** | [27-第十二批返修实施与验收-2026-10-09.md](./27-第十二批返修实施与验收-2026-10-09.md)（最新本地实施与验收）；返修规则见 [26 号交接](./26-交接说明-2026-10-08-第十二批返修.md)，长期约定见 [10 号](./10-项目现状与交接说明.md) |
| **要用它 / 给别人演示的人** | [13-上手指南-V1.1.md](./13-上手指南-V1.1.md)（怎么登录、点哪里、数字从哪来） |
| 想知道项目整体设计、口径、踩坑 | [10-项目现状与交接说明](./10-项目现状与交接说明.md)（第六~八节长期有效） |
| 要带去开会拍板的事项 | [15-待领导确认清单-2026-09-28.md](./15-待领导确认清单-2026-09-28.md)（当前有效条目）；决策汇总见 [08 号](./08-待领导确认清单-V1.1.md) 的"已确认决策"表 |
| 要改业务规则参数 | [09-业务参数配置说明](./09-业务参数配置说明-V1.1.md) |
| 要加接口 | [11-审计日志覆盖说明](./11-审计日志覆盖说明.md) |
| 要改打样单/做打印样指令 | [19-打样指令单字段清单](./19-打样指令单字段清单-V1.1-行业通用占位.md)（**行业通用占位，待简道云正式清单替换**） |
| 需求 / 数据模型 / 接口 / UI / 架构基线 | 01–05 号文档（source of truth） |

---

## 目录结构

```text
CRM-Sales-Agent-V1.1/
├── 01…05 设计文档      PRD / ER / API / UI / 技术架构
├── 07…09/15 计划与确认  开发计划 / 待领导确认清单 / 业务参数
├── 10…21 现状与使用      全景与踩坑 / 审计覆盖 / 上手指南 / 打样字段 / 交接说明
├── backend/            FastAPI + SQLAlchemy(async) + Alembic
│   ├── app/core/       配置、数据库、统一响应、错误码、认证、审计、数据范围、
│   │                   相似度打分、引用校验、取号、定时调度
│   ├── app/modules/    21 个业务模块（model + schema + router + service）
│   ├── scripts/        seed.py 主数据 / seed_demo.py 全链路演示数据 / check_* 回归套件
│   └── tests/          pytest 纯函数单元测试（不连库不连网）
├── frontend/           React 19 + TypeScript + Vite + Semi Design
│   └── src/            app/ 路由菜单布局 · modules/ 页面 · shared/ 接口与组件
├── ops/                run_checks.sh（本地一键，与 CI 同清单）、smoke_ui.mjs、
│                       docker-compose.yml、*.ps1（仅 Windows）
└── stitch_remix_of_semi_design_sales_crm/   24 屏设计稿，只作视觉基线
```

## 依赖与端口

**只需要 PostgreSQL。** Redis 依赖已移除（零使用），单实例部署唯一真相源就是 PostgreSQL。

| 服务 | 本机实际 | 说明 |
|---|---|---|
| PostgreSQL | **127.0.0.1:5432**，库 `crm_sales_agent`，账号 `crm / crm123456` | 这台 Mac 上是原生安装 |
| 后端 | `127.0.0.1:8000`（或 `0.0.0.0` 供局域网访问） | FastAPI，无 `--reload` |
| 前端 | `5173` | Vite，`/api` 代理到 8000 |

`ops/docker-compose.yml` 提供一套**可选的**独立容器（PG 5433 + Redis 6381），
用于"不想用本机数据库"或多人隔离的场景——**当前 .env 没用它**，要用的话把
`DATABASE_URL` 端口改成 5433 并删掉 `REDIS_URL`。

## 启动

### 后端

```bash
cd backend
cp .env.example .env                      # 按本机情况改 DATABASE_URL
uv venv --python 3.12 .venv               # 或 python -m venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m scripts.seed          # 角色权限、部门账号、示例客户/产品/价格
.venv/bin/python -m scripts.seed_demo     # 可选：跑通报价→审批→订单→应收→回款，演示用
PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows 把 `.venv/bin/` 换成 `.venv\Scripts\`。接口文档：<http://127.0.0.1:8000/docs>（仅 `DEBUG=true`）。

**改 Python 代码必须重启后端**（没有 `--reload`）。

### 前端

```bash
cd frontend && pnpm install && pnpm dev     # http://127.0.0.1:5173
```

### 让局域网同事访问

```bash
# 后端：--host 0.0.0.0
PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`frontend/vite.config.ts` 已配 `host: true` + `allowedHosts: true`；
`backend/.env` 的 `CORS_ORIGINS` 里加上 `http://<本机IP>:5173`。
同事打开 `http://<本机IP>:5173` 即可（Vite 代理走服务端转发，后端不必暴露也能用界面）。

> ⚠️ 演示账号是弱口令（`admin123` / `123456`），局域网内任何人都能登进去改数据。
> 只发必要账号，别发 admin。

## 演示账号

| 账号 | 密码 | 角色 | 数据范围 | 实际能用的页面 |
|---|---|---|---|---|
| admin | admin123 | 管理员 | 全部 | 全部 + 系统设置（唯一入口） |
| lisi | 123456 | 销售主管 | 本部门及下级 | 业务全部 + 审批 + 价格中心 + 企业微信 |
| zhangsan | 123456 | 业务员 | 仅本人 | 线索/客户/商机/核价/报价/跟进/任务/订单 |
| wangwu | 123456 | 财务 | 全部 | **只有订单中心和回款中心**（其余无权限） |

## 怎么验证（改完必须跑）

```bash
# 必须显式指定「一次性隔离库的后端 + 一次性库」——不给就拒跑（2026-10-08 起）：
API_BASE=http://127.0.0.1:8001/api/v1 \
DATABASE_URL=postgresql+asyncpg://crm:***@127.0.0.1:5432/crm_iso_test \
  bash ops/run_checks.sh        # 静态检查 + pytest + 全部回归套件（与 CI 同清单）
node ops/smoke_ui.mjs           # 28 页 + 4 交互逐页截图、抓控制台报错（不依赖 Playwright）
```

**截图要人眼看一遍**——历史上靠截图抓到过"接口正常但界面出错"的问题。

> ⚠️ `scripts/check_*.py` 会**清库**，并且会留下 `CHK*` 前缀的测试数据、把价格权限改成 5%。
> **不要在验收/演示环境随手跑。**
>
> 2026-10-08 起，套件与 `run_checks.sh` 都**必须先过防呆**：`API_BASE` 不许指向
> 8000（开发后端）、`DATABASE_URL` 必须是一次性库（`crm_iso*` / `crm_check*` /
> `crm_test*` 开头，或 `_test` 结尾），否则直接退出。判据只有一处：
> `backend/scripts/_test_support.py`。真要在开发环境上临时跑一次，加
> `ALLOW_DEV_TARGETS=1`（明知故犯，会大声提醒）。
> 之前"不显式指定就直接跑"，等于在开发库上跑测试——开发库里因此留下过测试角色、
> 测试账号和订单残渣。

## 约定

- 接口统一返回 `{ "code": 0, "message": "ok", "data": ... }`，非 0 即业务错误；
  错误码 = HTTP 状态 × 100 + 序号（40302 越数据范围、42204 版本锁定、42901 登录过于频繁…）
- 所有写接口都要校验权限并写审计日志；
- 业务数据按角色数据范围（self / department / department_and_sub / all）过滤，**禁止只靠前端隐藏**；
- 报价类数据必须存快照，已发送或已审批的版本不可原地修改；
- 数据库变更一律走 Alembic，禁止手改表结构；
- 未配置的外部集成一律明确报错，**不返回"看起来成功"的空结果**。

完整约定与踩坑清单见 [10 号文档第七、八节](./10-项目现状与交接说明.md)。

## 文件存储

本地磁盘（`backend/data/files`），但字段按对象存储设计（`storage_provider` + `object_key`）：

```text
STORAGE_PROVIDER=local
FILE_ROOT=data/files
MAX_UPLOAD_MB=20
```

换 MinIO/OSS 只需在 `app/modules/file/storage.py` 加一个适配器，业务表不用动。

## Sales Agent（DeepSeek）

```text
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

未配置 Key 时明确提示"还没有配置模型"，其余功能不受影响。
20 个工具带三级风控：L1 自动执行（查询/核价）、L2 用户确认后执行（记跟进、建任务、建报价）、
L3 审批后执行（低价报价提交审批，Agent 自己批不了）。
每次调用写 `agent_executions`（含**当时的角色与数据范围快照**），写动作再写 `audit_logs`（来源 `AGENT`）。

## 企业微信 / ERP 集成（代码就绪，等凭据）

企微：部门与成员同步、外部联系人同步、待归一、离职继承、事件回调都已实现；
缺凭据时同步接口返回 `50202` 并点名缺哪个变量，**不会静默返回"成功 0 条"**。
回调地址：`https://<公网域名>/api/v1/webhooks/wecom/events`（企微要求公网 HTTPS）。

ERP：`SalesOrder → erp/service.py → ErpAdapter → 聚水潭/ERP321`，
CRM 订单状态固定六个值、由各 Adapter 的映射表翻译对方状态词，**换 ERP 时业务与前端都不用动**；
幂等靠 `erp_order_id` + `external_mappings` + 请求体 `idempotency_key`；未配置返回 `50203`。
还差聚水潭的 `sign` 签名算法（公司已有的 `erp-bridge` 里有一份可抄）。

## 用脚本测接口时的两个坑

1. **Windows PowerShell 5.1 发中文 JSON 必须显式转 UTF-8**，否则服务端收到乱码，
   看起来像"明明有华东费率却匹配不到"。排查先用纯 ASCII 值跑一遍。
2. **不要同时留多个后端进程**：两个 supervisor 会各起一个 uvicorn，
   一个绑定失败却仍在跑，表现为"代码是新的、接口返回旧的"。
