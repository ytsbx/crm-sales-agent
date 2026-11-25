# CRM-Sales-Agent-V1.1 代码审查最终报告

- 审查日期：2026-11-21 至 2026-11-23
- 审查方式：只读业务代码，只写 `review/`
- 审查范围：`backend/app` 247 个 `.py`（99,453 行）+ `frontend/src` 175 个 `.ts/.tsx/.css`（69,093 行），合计 168,546 行
- 结论口径：静态审查已全覆盖；P2/P3 复现套件已全量执行；已补登录后真实浏览器逐页烟测、PostgreSQL 并发、历史外币、钉钉真实只读；真实 ERP 按用户指示暂不纳入本轮

## 一、总体结论

**不建议按“审查已全部关闭”上线。** 静态层面已经没有未审源文件，但验证层面仍有缺口。

当前成立的问题：

| 级别 | 数量 | 说明 |
|---|---:|---|
| P0 | 2 | 同一报价可生成两张正式订单；企微候选人越权可见 |
| P1 | 7 | 见 `VERIFICATION.md` 第二节 |
| P2 | 109 | 原列 115 条，复测后 6 条确认已修/过期 |
| P3 | 107 | 复现套件已全量执行，`107 failed, 0 passed`（失败即代表缺陷可复现） |

其中 P0/P1 是上线前必须处理的；P2 里涉及金额精度、审计缺字段、幂等缺失的，建议一并修。

## 二、P0 / P1 关键问题

### P0

1. **F01-01 同一报价的两个已接受版本各生成一张正式订单**
   - 证据：`review/verification/p0_quote_duplicate_convert.py` 输出 `P0-SO-1` / `P0-SO-2`，同一报价订单数=2。
   - 根因：后端只按 `SalesOrder.quote_version_id` 查重，没有按 `quote_id` 查“是否已有正式订单”；`create_version` 还会把已接受报价重置为 `draft`，允许重新走审批/发送/接受。

2. **F10-01 只有 `wecom:view` 的调用者能拿到他人客户**
   - 证据：`review/verification/p0_wecom_candidate_scope.py` 输出 `name=宁波海辰进出口有限公司 level=A region=华东 owner_id=999`。
   - 根因：`bind-customer` / `create-customer` 只要求 `wecom:manage`，不要求 `customer:view/create/assign`。

### P1

- `F10-05` 待办交接候选人只筛 active，无部门/数据范围过滤。
- `F11-01` 前端 `parseOptionalNumber("abc")=NaN`，`JSON.stringify` 后变 `null`，关键数量/金额静默丢失。
- `F11-02` 0 成本完整明细不报问题，会算 100% 毛利、低价审批不触发。
- `F11-03` 前端大数/四位单价丢精度：`9007199254740993 -> 9007199254740992`。
- `F12-01` 产品详情 `toNumber("abc") -> null`，PATCH 载荷 `{"length":null}`，原值被清空。
- `F12-02` `SkuCreate(weight=0)` 通过，物流重量算 0 且无警告。
- `F13-01` 登录页明文渲染登录名与初始密码 `123456`。

## 三、验证运行结果

- 后端 P2 复现套件：`92 failed, 9 passed`（`review/p2-run.txt`）。
- 后端 P3 复现套件：`107 failed, 0 passed`（`review/p3-run.txt`）。
- 前端分块检查：F01–F15 脚本已跑，合计 `208 confirmed`（含 P1/P2/P3），另有 F08 运行态 6 个断言失败；输出见 `review/frontend-checks.txt`。
- 前端构建：`npm run build` 成功，输出见 `review/frontend-build.txt`。
- 前端页面 smoke：Vite dev 返回 `HTTP/1.1 200 OK`，见 `review/frontend-dev.log`。
- 钉钉真实只读：令牌获取成功（32 位），部门列表返回 3 个；输出见 `review/dingtalk-token.txt`、`review/dingtalk-read.txt`。
- 历史外币：已接受 USD 报价走真实转单被 `40002` 拒绝，未落订单，输出见 `review/historical-fx.txt`。
- 未做：真实 ERP 联调（本机 `ERP_BASE_URL`/`ERP_APP_KEY`/`ERP_APP_SECRET` 全缺，适配器三项能力均未验收）；钉钉/OA 生产写操作。

## 四、动态复现已覆盖的证据

已用隔离 SQLite / 真实函数跑出输出，脚本在 `review/verification/`：

- 报价重复转单：`p0_quote_duplicate_convert.py`
- 企微候选人越权：`p0_wecom_candidate_scope.py`
- 前端数字解析：`js/number-input-check.ts`、`js/sku-to-number-check.cjs`
- 金额精度：`p2_money_precision.py`
- 报价状态/权限/审计：`p2_b02_quote_state.py`、`p2_b02_permissions.py`、`p2_b02_audit.py`、`p2_b02_audit_permission.py`
- 订单改派/取消/发货：`p2_b04_order_assign.py`、`p2_b04_shipment_key.py`
- 回款/应收：`p2_b05_receivable_state.py`
- ERP 多单：`p2_b05_erp_status.py`
- ERP 采集规范化：`p2_b06_erp_normalize.py`
- 对账入参：`p2_b07_diff_schema.py`
- 合同/审批长度：`p2_b08_b09_schema.py`
- 钉钉 Host / 审计精度 / 错误回显 / 企微索引：`p2_b11_b12_b14.py`
- 客户导入/金额：`p2_b13_import_precision.py`
- 集成/洞察/物流：`p2_b15_b17_batch.py`
- 目标/商机精度：`p2_b18_b19_precision.py`
- 商机/打样：`p2_b20_b21_schema.py`、`p2_b21_b22_misc.py`

## 五、复测后确认“报告过期、代码已修”的 6 条

- `B08-03`：`TemplateCreate.remark` 已有 `max_length=255`。
- `B09-03`：`RulePayload.description` 已有 `max_length=255`；`ApprovalDefinitionUpdate` 走 `PatchModel` 按列宽校验。
- `B20-05`：SKU 导入已走 `NUMERIC_RULES`，13 位长度/5 位小数/负数/1.9 装箱数均被拒。
- `B21-02`：打样四个字段已有 `max_length`，300 字被 Pydantic 拒。
- `B22-04`：`revise_sample` 已复制全部单头字段。
- `B23-03`：`finalize` 提交异常只记类型名，不再把 `str(exc)` 回显。

这 6 条已从确认清单移除，不计入 109 条 P2。

## 六、浏览器、PostgreSQL 与外部联调补充验证（2026-11-23）

已在一次性隔离库 `crm_review_smoke_20261123` 上完成真实后端 + 真实 PostgreSQL + 无头 Chrome 的登录后逐页烟测：

- 启动方式：`uvicorn app.main:app --port 8008` + `vite preview --port 5274`，关闭钉钉/企微推送、关闭定时任务、清空 AI Key。
- 登录：真实调用 `/auth/login`，使用 `admin/admin123`；浏览器注入同一 token 后逐页打开。
- 页面结果：工作台、分析、线索、客户、商机、产品、价格、报价、审批、订单、草稿、应收、文档、任务、设置各页、AI、询价、案例、洞察、回收站、样品、企微、物流共 **36 个页面全部返回真实业务内容**，输出见 `review/browser-e2e-2.txt` 与 `review/smoke-shots-2/`。
- 交互结果：报价创建/明细/运费/提交审批/发送/客户接受/转订单、订单建单/交期变更/发货/应收/回款确认均通过；最后一步 `POST /samples/2/made` 被后端以「打样审批尚未通过」422 拒绝，属于审批前置条件未满足，不是页面白屏或接口 500。
- 说明：原 `ops/smoke_ui.mjs` 在 `vite preview` 下只在首屏注入登录态，页面跳转后丢失；本次用副本 `ops/smoke_ui_review.mjs` 增加 `Page.addScriptToEvaluateOnNewDocument`，不改业务代码、不改原脚本。

PostgreSQL 并发验证：

- 脚本 `review/verification/pg_b06_duplicate_erp.py` 用两个真实连接同时插入不同订单号、相同 `erp_order_id` 的订单。
- 输出：`PG-DUP-A: committed id=5`、`PG-DUP-B: committed id=4`，同 `erp_order_id` 计数 **2**，证明 `B06-06` 的 `erp_order_id` 缺唯一约束在 PostgreSQL 下可并发写入重复值。
- 证据：`review/pg-concurrency.txt`。

外部联调：

- 钉钉真实只读：临时打开登录闸（`DINGTALK_LOGIN_OFF=false`）后，`access_token()` 返回 32 位令牌；`list_sub_departments(1)` 返回 3 个部门，首个为「天初(金华市)塑业有限公司」。证据 `review/dingtalk-token.txt`、`review/dingtalk-read.txt`。未调用审批写接口。
- ERP 就绪状态：`get_adapter()` 返回 `StubAdapter`，`configured=false`，缺 `ERP_BASE_URL`、`ERP_APP_KEY`、`ERP_APP_SECRET`；`read/write/collect` 三项均 `verified=false`。证据 `review/erp-readiness.txt`。按现有代码，配置不全或能力未验收时适配器会在发请求前拒绝，不能伪造联调成功。
- 历史外币：在隔离库直接造 `HIST-FX-USD-001`（已接受 USD 报价），走真实 `create_order_from_quote`，返回 `40002`「报价币种只能用人民币（CNY）」，转单前后均无订单落库。证据 `review/historical-fx.txt`、脚本 `review/verification/p2_b02_historical_fx.py`。

## 七、仍未完成的部分及原因

1. **P3 107 条已跑复现套件，但部分用例是源码断言**：`p3-run.txt` 为 `107 failed, 0 passed`；失败说明“修复后应有”的断言不成立，不等于每条都做了真实业务路径。
2. **P2 剩余条目以复现套件覆盖为主**：`p2-run.txt` 为 `92 failed, 9 passed`；其中 9 个通过项是控制/已闭合项，剩余失败项仍需按条目区分“真实行为”与“源码断言”。
3. **真实 ERP 联调按用户指示暂不纳入本轮**：本机 `ERP_BASE_URL`/`ERP_APP_KEY`/`ERP_APP_SECRET` 全缺，适配器 `configured=false`；代码本身又把 `read/write/collect` 标为未验收并拒绝发请求。用户已明确「ERP 暂时不管」，本轮不作为审查缺口。
4. **业务口径未定项**：0 成本、0 重量、取消期次改金额、手工分配超额等，代码行为已确认，但是否算缺陷需业务验收时定。

## 八、建议的上线前处理顺序

1. 先修 P0：报价重复转单、企微候选人越权。
2. 再修 P1：前端数字静默丢失、0 成本/0 重量、默认密码展示、待办跨范围。
3. P2 优先修金额精度、审计缺字段、幂等缺失、错误信息回显。
4. 修完后按 `review/verification/` 的脚本回归，再补 PostgreSQL 并发与浏览器 E2E。
5. 最后再决定 P3 是否清理；P3 不阻塞上线。

## 九、交付物索引

- 主台账：`review/LEDGER.md`
- 复核报告：`review/VERIFICATION.md`
- 后端总结：`review/BACKEND-FINAL.md`
- 分块复核：`review/recheck-P2-B01-B05.md`、`recheck-P2-B06-B09.md`、`recheck-P2-B10-B14.md`、`recheck-P2-B15-B19.md`、`recheck-P2-B20-B23.md`
- 静态分块：`review/blocks/B01-*.md` 至 `B25-*.md`、`F01-*.md` 至 `F15B-*.md`
- 动态脚本：`review/verification/`
- 套件输出：`review/p2-run.txt`、`review/p3-run.txt`、`review/frontend-checks.txt`、`review/frontend-build.txt`、`review/frontend-dev.log`
- 浏览器 E2E：`review/browser-e2e-2.txt`、`review/smoke-shots-2/`、`ops/smoke_ui_review.mjs`
- PostgreSQL 并发：`review/pg-concurrency.txt`、`review/verification/pg_b06_duplicate_erp.py`
- 外部联调：`review/dingtalk-token.txt`、`review/dingtalk-read.txt`、`review/erp-readiness.txt`、`review/historical-fx.txt`、`review/verification/p2_b02_historical_fx.py`
