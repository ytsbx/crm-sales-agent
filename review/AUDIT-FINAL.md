# CRM 审查总收口（B01–B25 / F01–F15）

## 一、范围与结论

- 后端：B01–B25 共 **262 条**逐条复核；B25 的 36 个 `__init__.py` 已核对，`import app.main` 冒烟通过。**当前证伪后的 P1 状态以 `review/FALSIFICATION-FINAL.md` 为准：20 确认 / 6 降级 / 14 证伪。**
- 前端：F01–F15（F15A/F15B）共 16 个片段，**206 条确认**（P0 2、P1 19）、**0 条待确认**、**8 条证伪**（含 1 条部分证伪）。
- 只读业务代码，只写 `review/`；**未修改任何业务代码，未提交 git**。

后端有效 P0 为 **0**：原 B04-01 P0 已证伪（接受后改运费被 `ensure_charge_change_allowed` 的 `accepted_at` 闸门 42204 拦住）。前端两条 P0 均已完成端到端复核，不是“前端露按钮、后端拦住”。

## 二、前端两条 P0

1. **F01-01 已转订单仍可新建报价版本，再生成第二张订单和应收。**
   - `QuoteDetailPage.tsx` 的“新建版本”只看 `canManage && !rejection`，不判 `quote.status === 'converted'`；
   - `POST /quotes/{id}/versions` 不拒 converted，`create_version` 直接把 `quote.status` 改回 `draft`；
   - `POST /quote-versions/{id}/convert-to-order` 只按 `quote_version_id` 查重，新版本 id 不同，能再建订单。
2. **F10-01 企微候选/建客/绑客绕过客户数据范围。**
   - 候选只要求 `wecom:view`，服务层明确“故意不做数据范围过滤”，返回全库客户名称/等级/地区/负责人/公海状态；
   - 绑客只要求 `wecom:manage`，`bind_customer` 取客户不 `ensure_in_scope`；
   - 默认暴露面是 `sales_manager`（`department_and_sub` 被绕过），业务员默认无 `wecom:*`；自定义角色可把 `wecom:*` 单独授给无 `customer:view` 的账号。

## 三、前端 P1 重点（19 条）

- **F02**：回款登记重开换键、手工建单 `Number` 丢精度、`createOrder` 不传请求键、无批次改发货/送达、取消后仍可删应收。
- **F03**：表单全走 `parseOptionalNumber`；后端 `_f` 转 float 回填，编辑不改直接保存也改价。
- **F04**：合并确认不等 `mergePreview` 加载/失败，不可逆合并可绕过影响清单。
- **F05**：签署件上传即挂 `signed`，取消也进签署原件，确认签署再挂一次。
- **F06**：新建商机预计金额 16 位丢分。
- **F08**：`/dashboard/team` 只按 `customer:view` 收门，财务角色拿到成员任务、商机成交额、风险商机标题/客户/金额。
- **F10**：待办交接候选人只按 `status=active`，不限部门/范围。
- **F11**：非法数量文本变 `null` 丢字段；转报价 16 位 4 位小数丢最后一位。
- **F12**：SKU 非数字清空原值；0 单重/尺寸落库后按 0 计费。
- **F13**：账号生成弹窗明文固定初始密码 `123456`。
- **F15A**：98 个无 `href` 的 `<a>` 键盘不可达；订单金额列 108/124px 被截断。

## 四、后端关键确认项

- **B06-06**：`sales_orders.erp_order_id` 无唯一约束，回读 `.first()`。
- **B06-08**：重放无锁。
- **B07-09**：先查范围后锁行 TOCTOU。
- **B10-02**：自研 OA 附件写死 `business_type="inquiry"`。
- **B11-03/04**：`GET /users` 对 assign 权限下发全量 PII；停用不吊销会话。
- **B12-04**：删除文件锁后不复查范围。
- **B13-06**：导入无幂等。
- **B15-06**：`IntegrationLog.event_key` 模型缺唯一约束。
- **B18-03**：冻结期缺键回落实时值仍标冻结。
- **B23-06**：`run_auto_tasks` 先覆盖 `customer.next_followup_at` 再判断“已约定”，字段独立写入口径仍待核对。
- **B24-01/03**：CORS `*` + credentials 无启动互斥；`normalize_name` 不处理 `\t/\n`。

## 五、收口掉的待确认

证据见 `review/PENDING-CLOSURE.md`。

- **F05-P1 证伪**：整份已发送/已接受报价就是后端定义的正式依据；UI 要求“补正式依据”不是“必须选版本”。
- **F10-10 证伪**：跨部门转交是已验收行为，`QUALITY_PROGRESS.md:150` 有主管转交外部门成功记录。
- **F11-02 证伪**：四项成本显式填写时 0 元合法；`test_quote_cost_gate.py -k zero` 实跑 2 passed。
- **F13-T01 证伪**：`payment/approval` 只是通知 `type`，链接按 `business_type` 走 `order/quote`，两条映射当前不可达。
- **B05-01 收口**：超收按到期日自动抵后续期，`test_payment_rebalance_order.py` 8 passed；只保留逐笔分配审计缺口。
- **B03-03 证伪**：报价、转单、订单草稿三处均过 `ensure_currency_allowed`，固定只允许 CNY。
- **B05-03 证伪**：手工新增/调高应收均调 `assert_receivable_amount_fits`，`test_payment_consistency.py` 26 passed，应收合计不能超订单总额。

## 六、产物与复现

- 总台账：`review/BACKEND-FINAL.md`、`review/FRONTEND-FINAL.md`、`review/PENDING-CLOSURE.md`、`review/P0-VERIFY.md`。
- 后端复现：`review/repro/test_b*.py`，命令 `cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/<file> -q`。
- 前端复现：`review/repro/f01_frontend/check_f01.mjs` 至 `f15_frontend/check_f15b.mjs`，命令 `node review/repro/<dir>/<script>.mjs`。
- 分块报告：`review/recheck-B*.md`、`review/recheck-P2-*.md`、`review/recheck-P3-*.md`、`review/recheck-F*.md`。

## 七、仍待业务拍板（不影响审查收口）

以下不是“没审查”，而是代码行为已经查清、但采用哪种业务规则需要主人决定：

- B04-01：转单是否要保存客户接受时的金额快照。
- B08-P1/P2：同一签署扫描件能否服务多份合同；对客 Excel 金额是否应可计算。
- B17-P1/P2：`unreceived_amount` 负数表示“超收”还是“剩余应收”；空重算是否允许清历史。
- B19-P1/P2：`copy_items replace` 是整条替换还是只换数量；非 PG 测试库的 advisory lock 取舍。
- B23-P1/P2：公海回收防重入锁注释与实现不一致；savepoint 回滚后既有对象是否恢复。

## 八、边界

- 静态审查与最小复现为主；未起浏览器做 F15 样式实测，未连生产库，未跑生产数据清理。
- 所有“确认”均已落到具体代码/测试；业务规则未替主人拍板，已收口的按现有代码与已验收记录判定。
- 修复未做：本轮只审查。P0/P1 的修法与影响已在各分块报告和本总收口写明。
