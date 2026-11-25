# P0/P1 隔离复现报告（2026-11-23）

## 一、边界与方法

- 只读业务代码，只写 `review/`；未改任何业务文件、未动 `QUALITY_PROGRESS.md`。
- 数据库：`backend/tests/conftest.py` 的内存 SQLite 引擎；不连生产/演示库。
- 外部副作用：转单脚本对 `generate_order_no`、`followup_service.record_and_notify` 做了替身；未发通知、未连 ERP/企微。
- 前端：用 `frontend/node_modules/.bin/tsc` 编译真实 `number-input.ts`；`toNumber` 用 TypeScript 从 `ProductDetailPage.tsx` 抽取后转译执行。不是手写同构函数。
- `backend/tests` 只做静态对照，未运行会改库的套件。本文的“测试覆盖”指用例是否覆盖该触发条件，不代表测试全绿。

复现文件：

- `review/verification/p0_quote_duplicate_convert.py`
- `review/verification/p0_wecom_candidate_scope.py`
- `review/verification/p1_validation_gaps.py`
- `review/verification/check_b04_trigger.py`
- `review/verification/js/number-input-check.ts`
- `review/verification/js/sku-to-number-check.cjs`

---

## 二、P0 复现结果

### P0-1｜F01-01｜同一报价跨版本生成第二张正式订单：**成立**

命令：

```bash
cd /Users/zhaorongkai/Desktop/CRM-Sales-Agent-V1.1
backend/.venv/bin/python review/verification/p0_quote_duplicate_convert.py
```

实际输出：

```text
[第一次转单] quote=1 version=1 -> order_no=P0-SO-1 total=100.00
[改价后] 当前版本=2 version2.total=120.00
[第二次转单] quote=1 version=2 -> order_no=P0-SO-2 total=120.00
[结果] 同一报价生成订单数=2（期望 1，实际 2）
[B04-01] 脚本模拟：接受时版本金额=100.00，接受后改成 120.00，订单落库总额=120.00（无接受快照可比对）
```

证据链：

- 后端只按 `SalesOrder.quote_version_id` 的唯一索引查重（`order/model.py:46-50`、`order/service.py:221-231`），**没有按 `quote_id` 再查一次是否已有正式订单**。
- `create_version` 在客户已接受、且订单已生成后仍可新建版本，并把 `quote.status` 重置为 `draft`（`quote/service.py:1143-1147`）；新版本之后可重新审批、发送、接受，再转单。
- 复现脚本里第二版是预置的，但脚本同时证明：只要两版都满足 `quote.status=accepted`、`version.accepted_at/sent_at` 非空、`approval_status=approved`，`create_order_from_quote` 就会各生成一张订单和一套应收。

后果：同一报价产生两张正式订单、两套应收；业绩、发货、回款都会重复。测试：`backend/tests` 无“跨版本重复转单”用例。

### P0-2｜B04-01｜接受后改费再转单导致订单金额≠客户接受金额：**当前代码不成立（原 P0 降级）**

命令：

```bash
backend/.venv/bin/python review/verification/check_b04_trigger.py
```

实际输出：

```text
[B04-01触发] 已接受版本改运费被拒：code=42204 message=客户已接受这版报价。要调整费用，请新建报价版本并请客户重新确认。
```

同时，`ensure_charge_change_allowed` 当前在 `quote/service.py:130-135` 明确先判 `accepted_at`；接受后新增/修改/删除运费都会在这里 422，不会走到 `recalc_version` 改写总额。`recheck-B04.md` 里“改运费仍被允许、只清 sent_at”的结论是旧代码状态，当前代码已不成立。

保留的设计缺口：

- 转单仍直接取 `version.total_amount`（`order/service.py:295-302`），没有“客户接受时金额快照”字段；
- 若未来任何路径（内部脚本、数据修复、新的费用入口）在 `accepted_at` 之后改写版本总额，订单会静默取新值，没有第二道比对；
- 建议增加 `accepted_total_amount/accepted_currency` 快照并在转单时比对；这是加固项，不是当前可触发的 P0。

结论：原 B04-01 的“接受后改费→转单”触发条件已被现有闸门堵住，不能继续按 P0 计；降为待确认/设计缺口。严重度调整见 `LEDGER.md`。

### P0-3｜F10-01｜只有 `wecom:view` 可看到他人客户：**成立**

命令：

```bash
backend/.venv/bin/python review/verification/p0_wecom_candidate_scope.py
```

实际输出：

```text
[调用者权限] 只有 wecom:view（路由依赖 require_permission('wecom:view')）
[返回候选数] 1
[泄露字段] id=1 name=宁波海辰进出口有限公司 level=A region=华东 owner_id=999 score=70
```

证据链：

- `GET /integrations/wecom/unbound-contacts/{id}/candidates` 只要求 `wecom:view`（`wecom/router.py:250-257`）。
- `unbound_candidates` 调 `find_duplicate_customers` 时不传 `owner_ids`（`wecom/service.py:566-574`）；`find_duplicate_customers` 的注释明确“传 None 表示全量”，返回 `name/level/region/owner_id/pool_status`（`contact_util.py:322-437`）。
- 复现里的两个客户负责人分别是 999/888，调用者只有 `wecom:view` 且数据范围 `self`，仍拿到 999 名下客户名、A 级、华东、负责人 id。

同一根因的写侧：`bind-customer`/`create-customer` 只要求 `wecom:manage`（`wecom/router.py:260-301`），不要求 `customer:view/create/assign`；可把联系人挂到任意客户，或建客后成为负责人。与 B15-01/02 合并定级时，F10-01 仍应按“越权可见 + 越权写入”处理。

测试：`test_wecom_operation_permissions.py` 只测 transfer；导入三权限测试不覆盖 candidates/create/bind。

---

## 三、P1 复现结果

| 编号 | 结果 | 证据 |
|---|---|---|
| F10-05 待办交接候选人跨范围 | **成立** | 脚本 `p1_validation_gaps.py` 输出候选人 `[(101, '主管'), (201, '外部门员工')]`；`task/router.py:759-774` 只 `User.status == "active"`，无部门/数据范围过滤；`_active_owner` 只校验存在/在职 |
| F11-01 非数字数量/目标价静默变 null | **成立** | 编译真实 `number-input.ts`：`parseOptionalNumber("abc")=NaN`，`JSON.stringify({quantity:NaN})={"quantity":null}`；后端把 null 当未填/清空 |
| F11-02 成本 0 元可过完整性闸门 | **成立（业务口径待拍板）** | `item_cost_problems` 对 `cost_snapshot=0, cost_complete=True` 返回 0 个问题；`test_quote_cost_gate.py:28-32` 明确允许 0。0 成本会算出 100% 毛利、低价审批不触发，是否合法需业务确认 |
| F11-03 转报价金额用 JS number 丢分 | **成立** | 真实 `parseOptionalNumber`：`9007199254740993 -> 9007199254740992`；`900719925474.0993 -> 900719925474.0992`（4 位单价丢 0.0001） |
| F12-01 SKU 非数字清空原值 | **成立** | 从 `ProductDetailPage.tsx` 抽取真实 `toNumber`：`"abc" -> null`，PATCH 载荷 `{"length":null}`；后端允许 null，原值被清空 |
| F12-02 SKU 单重 0 被接受 | **成立** | `SkuCreate(weight=0)` 通过；`compute_weight_and_volume` 输出 `actual_weight=0.0000 warnings=[]`；对照 `logistics.py:437` 手填 `weight_override<=0` 被拒 |
| F13-01 默认密码明文展示 | **静态确认（未做运行态渲染）** | `AppLayout.tsx:295-296` 直接渲染 `user.username` 与字面量 `123456`；没有条件脱敏，也没有首次登录强制改密的前端阻断 |

---

## 四、测试覆盖对照

| 编号 | `backend/tests` 现状 | 结论 |
|---|---|---|
| F01-01 | 无跨版本重复转单用例 | 未覆盖 |
| B04-01 | 无“接受后改费再转单”用例；`repro/test_b04_p0_p1.py` 的控制项已因代码变化失效 | 未覆盖；原 P0 不成立 |
| F10-01 / B15-01/02 | `test_wecom_operation_permissions.py` 只测 transfer；导入测试不覆盖 candidates/create/bind | 未覆盖 |
| F10-05 | 企微交接有跨部门测试；任务交接没有 | 未覆盖 |
| F11-01 | `test_inquiry_draft`、`test_inquiry_submit_required` 不测非数字文本 | 未覆盖 |
| F11-02 | `test_quote_cost_gate.py:28-32` 明确允许 0 | 已覆盖“允许 0”这一现状，不等于业务正确 |
| F11-03 | `test_inquiry_quote_stage` 不测 16 位/4 位小数 | 未覆盖 |
| F12-01 | `test_sku_optional_code` 只测编码 | 未覆盖 |
| F12-02 | `test_logistics_quote` 测最低收费，不测 SKU 0 值 | 未覆盖 |
| F13-01 | `test_personal_account` 只测改密审计 | 未覆盖默认口令展示 |

---

## 五、本阶段没有看的部分及原因

- 未做浏览器 E2E：F13-01 的弹窗实际渲染、F11/F12 表单的真实输入路径、响应式与键盘行为没有跑浏览器；本轮用源码级函数执行替代，不能替代像素/交互验收。
- 未做 PostgreSQL 并发：B04-01 的锁顺序、F01-01 的并发转单、F10-05 的交接并发都只在 SQLite 单会话验证；生产 PG 的行锁/隔离级别未实测。
- 未连真实企微/ERP/OA：F10-01 只验证本地候选查询与路由权限依赖，未验证真实企微账号下的 token、外部联系人字段与前端展示。
- 未运行 `backend/tests`：项目要求隔离库；本轮只跑了自建脚本，没有跑全量 pytest，避免误连演示库。
- P2：只跑了仓库里已有的复现套件（见第六节），未逐条做真实数据库动态验证；P3 仍未复现。


## 六、P2 复核进度（后端，2026-11-23 续）

这是“继续”后的第一步：先跑现有 P2 套件，确认哪些还开着，不重复造新结论。

命令：

```bash
cd backend
PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b0*_p2.py ../review/repro/test_b1*_p2.py ../review/repro/test_b2*_p2.py -q
```

当前结果：`94 failed, 7 passed`。失败项是“修复后应有”的断言仍失败，说明 P2 绝大多数**未修复**；通过项是已闭合/控制项：

- `B08-02` PDF 页脚转义
- `B10-04` unionid 控制项
- `B19-04` 线索导入不回显原始异常
- `B21-02` 打样文本长度
- `B21-03` 产品导入不回显原始异常
- `B22-04` 修订复制申请单字段
- `B23-03` 导入 commit 不回显原始异常

金额核心 B01–B05 的 P2 套件单独跑仍为 `31 failed, 0 passed`；详见 `review/recheck-P2-B01-B05.md`。这些断言主要是源码/模型断言，不是 PostgreSQL 运行时；所以只能说明“代码路径仍存在”，不能说明“已实机复现”。

优先仍未闭合的金额/对账 P2：

- B05-08：已取消应收节点仍可改金额并触发重排；
- B05-09：一条 `rejected` 回款也会永久阻止删除节点；
- B05-10：`plan_payments` 不校验 `PaymentRecord.order_id == plan.order_id`；
- B05-12：service 层手工分配不校验 `total <= received_amount`；
- B04-05：已完成订单仍可创建/确认交期变更；
- B04-06：仅 `order:view` 可触发 ERP 刷新写订单状态；
- B01-03 / B03-05 / B03-06：金额舍入与 float 往返仍在。

本轮补了 4 条真实隔离库用例：`review/verification/p2_b05_receivable_state.py`。实际输出：

```text
[B05-08] 已取消节点改金额后 status=cancelled amount=200.00
[B05-10] 查 A 单期次 1 的回款，返回 order_id=[2]
[B05-12] 回款 100.00 的手工分配合计=150.00，service 返回=150.00（未拒绝）
[B05-09] 有 rejected 回款时删除被拒：AppError 该应收节点已有回款记录或收款分配，不能删除；请先核对并调整回款分配
```

这说明 4 条回款/应收 P2 不是“纸面怀疑”：B05-08 已取消节点仍能改金额；B05-10 跨订单回款会被本期列表显示；B05-12 service 层允许分配额超过回款额；B05-09 驳回回款仍永久阻止删除。下一步若要“关闭”，先为这 4 条确定业务口径，再决定是改代码还是改测试；P3 仍未复现。

### B04-05/B04-06 补验（2026-11-23）

- **B04-05 已用隔离库 + 替身 preview 复现**：`review/verification/p2_b04_schedule_state.py`
  ```text
  [B04-05] status=completed -> 创建成功 change_id=1
  [B04-05] status=cancelled -> 拒绝 AppError: 订单已取消，不能再创建或确认交期变更
  ```
  `schedule.create_change` 只拦 `cancelled`（`order/schedule.py:190-192`）；`completed` 不在拦截范围，仍能创建待确认变更单。控制项 `cancelled` 被拒，说明不是替身把状态判丢了。
- **B04-06 仍为源码级确认**：`order/router.py:224-237` 的 `refresh-status` 依赖只有 `order:view`，而 `erp/service.py:800-815` 在拿到外部状态后调用 `order_service.change_status` 写订单状态与历史。没有真实 ERP 连接，未做外部回包运行态；P2 套件 `test_b04_06_refresh_status_should_require_manage` 仍失败。


### B01-03 / B03-05 / B03-06 金额精度补验（2026-11-23）

脚本：`review/verification/p2_money_precision.py`，直接调用真实 `split_amounts`、`quote_rate`、`LogisticsCalculateRequest`。

```text
[B01-03] split_amounts(0.05, 50/50)=[Decimal('0.02'), Decimal('0.03')]；HALF_UP 应为 [Decimal('0.03'), Decimal('0.02')]
[B03-05] quote_rate(1.005kg, 1元/kg) amount=1.0 type=float；HALF_UP 应为 1.01
[B03-06] LogisticsCalculateRequest 接受 quantity=1.2345（4 位小数仍可通过）
[B03-06] 同值落到 Numeric(16,3) 会变 1.234
[B03-06] 超大 quantity 也通过：9999999999999999
```

结论：

- B01-03：`Decimal.quantize(Decimal("0.01"))` 默认 HALF_EVEN，和业务口径的 HALF_UP 不一致；`0.05` 对半拆会先给 0.02 而不是 0.03。
- B03-05：`quote_rate` 的 `amount` 出参是 `float`；`1.005` 被默认舍成 1.00，返回 1.0，前端再按 float 处理会继续丢分。
- B03-06：试算 `quantity` 入参没有 `max_digits/decimal_places`；`1.2345` 可进，落库 `Numeric(16,3)` 只剩 1.234；超大数量也能进。

### B02-03 / B02-04 / B02-12 报价状态补验（2026-11-23）

脚本：`review/verification/p2_b02_quote_state.py`，真实调用报价路由。

```text
[B02-03] 不存在的 contact_id=999 被写入：contact_id=999
[B02-12] 显式 null 后 quote.valid_until=None version.valid_until_snapshot=None
[B02-04] status=accepted 且无订单，删除后 deleted_at=2026-11-23 03:27:56.801937+00:00
```

结论：

- B02-03：`update_quote` 对 `contact_id` 不做存在/归属校验，能把联系人改成任意不存在的 id；创建报价时有完整校验。
- B02-04：已接受、但还没转订单的报价，删除只查 `SalesOrder.quote_id`，不判 `sent_at/accepted_at`，仍可软删。
- B02-12：`PATCH /quotes/{id}` 显式传 `valid_until=null` 会同时清空主单与当前草稿版快照；`/quote-versions` 的显式 null 则被 `if valid_until is not None` 忽略，两个入口语义不一致。

### B02-05 多角色价格权限补验（2026-11-23）

脚本：`review/verification/p2_b02_permissions.py`，用两个角色模拟“业务员 + 销售主管”。

```text
[B02-05] 角色并集 discount_limit=5.0000（5 比 10 严，取 min 正确）
[B02-05] 角色并集 minimum_margin=0.1000 can_approve=True（0.10 比 0.20 松，取 min 反而放宽）
```

结论：同一套“多角色取最严”注释下，折扣上限取 `min` 是对的；最低毛利率 `0.20` 才是更严，却也被 `min` 成 `0.10`，等于给多角色用户放宽。两个方向相反，B02-05 成立。

### B02-15 客户接受报价审计补验（2026-11-23）

脚本：`review/verification/p2_b02_audit.py`，真实调用 `lifecycle.accept_version`（通知侧用替身，审计落库真实）。

```text
[B02-15] accept changed=True audit_after={'version_id': 1, 'version_no': 1}
```

结论：客户接受报价后，审计只有 `version_id/version_no`，没有 `amount/currency/分项`。报价金额 120.00、币种 CNY 都没进审计；以后要回答“客户到底接受的是多少钱”，只能回查版本当前值，版本一旦被新版本覆盖就对不上。B02-15 成立。

### B02-14 价格权限审计补验（2026-11-23）

脚本：`review/verification/p2_b02_audit_permission.py`，真实调用 `PUT /price-permissions/{role_id}`。

```text
[B02-14] 审计 after keys=['can_approve', 'discount_limit', 'id', 'minimum_margin', 'remark', 'role_id', 'role_name', 'status'] before=None
[B02-14] 新值 minimum_margin=0.1 discount_limit=0.05
```

结论：改价格权限只写 `after`，`before_data` 为 `None`。把毛利率从 20% 降到 10%、折扣从 10% 放到 5% 这种“放宽权限”的操作，审计里看不到旧值，事后无法判断谁把权限放宽了多少。B02-14 成立。

### B02-07 外币入口可达性（2026-11-23）

脚本：`review/verification/p2_b02_currency_entry.py`。

```text
[B02-07] currency=CNY -> 允许 CNY
[B02-07] currency=USD -> 拒绝 code=40002 message=核价币种只能用人民币（CNY），收到「USD」...
[B02-07] currency=EUR -> 拒绝 code=40002 ...
```

结论：当前核价入口先用 `ensure_currency_allowed` 拒绝 USD/EUR，`fx=None` 的缺汇率分支从正常入口不可达；只有“历史外币版本重算”这类内部路径可能进入。B02-07 不再按当前 P2 入口 bug 计，降为“历史数据路径待确认”；如果确认系统从未有过外币数据，可关闭。

### B03-04 / B03-07 产品主数据补验（2026-11-23）

脚本：`review/verification/p2_b03_catalog_rules.py`。

```text
[B03-04] 同 SKU 同生效日成本条数=2（期望 1，实际 2）
[B03-07] 软删 SKU 建价格规则数=1 status=['active'] sku.deleted_at=2026-11-23 03:38:00.426685
```

结论：

- B03-04：`create_cost` 只校验日期倒置，不校验同 SKU 同 `effective_from` 是否已有成本；连点两次会落两条，读取侧才报 409，已经晚了。
- B03-07：`create_price_rule` 只判 `sku is None`，不判 `deleted_at/status`；软删 SKU 仍能建出 `active` 价格规则，和导入端“拒绝停用/软删 SKU”的口径不一致。

### B04-04 订单改派跨范围补验（2026-11-23）

脚本：`review/verification/p2_b04_order_assign.py`。

```text
[B04-04] 调用者 data_scope=self，订单 owner 101 -> 202（外部门 202）
```

结论：`update_order` 改 `owner_id` 时只查 `order:assign`、目标用户存在且 active，没有 `ensure_in_scope`；`data_scope=self` 的销售能把订单交给外部门用户，交出去后自己按范围可能立刻看不到这张单。B04-04 成立。

### B04-19 / B04-20 取消与发货补验（2026-11-23）

- **B04-20 入口层已复现**：`review/verification/p2_b04_shipment_key.py`
  ```text
  [B04-20] 无 body key、无 X-Request-Key -> None
  [B04-20] 只给 body key -> 'abc'
  [B04-20] 只给 header -> 'hdr'
  ```
  `create_shipment` 只有 `if request_key: reserve(...)`；无键时直接建批次，双击/网络重试没有幂等保护。未真建两次批次做 DB 级复现，只证明入口会产出 `None`。
- **B04-19 源码级确认**：`order/router.py:1082-1090` 取消订单审计 `after` 只有 `status/rejected_pending_payments/cancelled_receivable_plans/opportunity_id/note`，没有订单总额、回款金额、被取消期次明细；`before` 只有 `status`。未构造整套合同/回款/批次数据做运行态。

### B05-06 / B05-07 / B05-19（2026-11-23）

- **B05-06 已用假 ERP 响应复现**：`review/verification/p2_b05_erp_status.py`
  ```text
  [B05-06] 查 WANTED-ORDER，返回 status=shipped raw_status=Sent shipped_at=2026-11-20
  ```
  构造 `orders[0]` 是 `OTHER-ORDER/Sent`、`orders[1]` 才是查询键 `WANTED-ORDER/Cancelled`；适配器直接取 `orders[0]`，把别的单的 `Sent` 当成查询单的状态返回，不校验 `o_id == external_id`。外部响应为假，但“取首单不校验”是真实代码路径。
- **B05-07 源码级**：`erp/service.py` 的 `_judge` 只认 `done` 或 409；占位记录落 `in_flight` 后没有 `created_at` 超时/reaper，同键重试永久 409。未构造长时间占位做时钟推进。
- **B05-19 源码级**：`payment/router.py:1199-1206` confirm 审计 `after` 只有 `comment`；`:1233-1241` reject 审计 `before` 只有 `status`、`after` 只有 `status/comment`。没有金额、币种、订单号、期次、分配摘要。未跑真实确认/驳回路由。

### B06-06 / B06-07 / B08-03 / B09-03（2026-11-23）

- **B06-07 已实测**：不传 `currency` 的采集记录归一到 `CNY`；USD 被 `reject_foreign_currency` 拒。缺币种静默当人民币仍成立。
  ```text
  [B06-07] 不传 currency -> currency=CNY
  [B06-07] USD 被拒 -> AppError: 采集第 2 条的币种是「USD」...
  ```
- **B06-06 已实测元数据**：`erp_order_id.unique=None`，`SalesOrder` 只有 `quote_version_id` 唯一索引和 `customer_id` 普通索引，没有 ERP 单号唯一约束。
  ```text
  [B06-06] erp_order_id unique=None indexes=[('ix_sales_orders_quote_version', True, ['quote_version_id']), ('ix_sales_orders_customer', False, ['customer_id'])]
  ```
- **B08-03 已不成立（当前代码已修）**：`bizdoc/router.py` 的 `TemplateCreate.remark` 已有 `max_length=255`，300 字被 Pydantic 拒 `string_too_long`，不会到数据库才 500。
- **B09-03 已不成立（当前代码已修）**：`RulePayload.description` 已有 `max_length=255`；`ApprovalDefinitionUpdate` 继承 `PatchModel`，按 `approval_definitions` 列宽校验，300 字名称被拒 `value_error`（审批流名称最长 128）。原报告“缺长度”已过期。

### B11-11 / B12-01 / B12-02 / B14-04（2026-11-23）

- **B11-11 已实测**：`_external_base` 直接拼 `Host` 头，伪造 `Host=evil.example.com` 得到 `https://evil.example.com`；这个值会进钉钉 `redirect_uri`。
  ```text
  [B11-11] Host=evil.example.com -> https://evil.example.com
  ```
- **B12-01 已实测**：`json_safe(Decimal("12.30"))` 转成 `12.3`，类型 `float`；审计里的金额是二进制近似。
  ```text
  [B12-01] Decimal 12.30 -> 12.3 type=float
  ```
- **B12-02 已实测**：200 字密码触发 `LoginRequest` 长度校验后，异常处理响应 `400`，`details.input` 回显完整 200 字原文。
  ```text
  [B12-02] status=400 原文是否回显=True input 长度=200
  ```
- **B14-04 已实测模型**：`WeComSyncJob` 只有普通索引 `ix_wecom_sync_jobs_type(job_type,status)`，没有唯一索引；`create_all` 建库不会带 Alembic 里的唯一保护。
  ```text
  [B14-04] WeComSyncJob indexes=[('ix_wecom_sync_jobs_type', False, ['job_type', 'status'])]
  ```

### B07-05 / B07-06 / B10-05（2026-11-23）

- **B07-05 已实测入参**：`DiffConfirmRequest(resolution="manual")` 通过，`financial_adjustment_amount=None`。入口允许“人工处理但不填金额”，后续仍会创建 `pending` 的财务调整。
  ```text
  [B07-05] resolution=manual financial_adjustment_amount=None
  ```
- **B07-06 源码级**：核定审计 `after` 只有 `status/resolution/note`，没有 `financial_adjustment.id/type/amount/currency/order_id`。未造差异运行态。
- **B10-05 源码级**：`dingtalk/router.py:336` 图片 URL 写死 `https://crm.mandla.cn/api/v1/public/inquiry-images/...`，不读 `public_base_url`；非生产域名钉钉取不到图。

### B13-04 / B13-05 / B13-06 / B13-07 / B13-09（2026-11-23）

- **B13-06 入口实测**：`import_customers` 参数只有 `request/file/preview/preview_token/user/session`，没有 `request_key`，路由里也没有 `idempotency.reserve`；CSV 导入双击/重试会再建一遍。
  ```text
  [B13-06] import_customers 参数=['request', 'file', 'preview', 'preview_token', 'user', 'session']
  [B13-06] 是否含 request_key=False
  ```
- **B13-07 已实测**：`_f(Decimal("123456789.1234"))` 返回 `123456789.1234`，类型 `float`；`_f` 这条展示路径把 Decimal 转成二进制近似。
  ```text
  [B13-07] _f(Decimal('123456789.1234'))=123456789.1234 type=float
  ```
- **B13-04 源码级**：`customer/tags.py:814-815`，目标 `owner_id is None` 且来源有负责人时直接 `target.owner_id = source.owner_id`；不查 `User.status`，也不写 `CustomerOwnerHistory`。
- **B13-05 源码级**：导入单行失败与 `finalize` 失败都把 `str(exc)[:160]/[:120]` 放进响应，数据库约束/字段值会回显。未造约束冲突实跑。
- **B13-09 源码级**：`move_contact_to_customer` 只锁两个客户，不锁 `Contact`、不重读 `customer_id`；并发改绑可覆盖归属。未做并发交错。

### B15-06 / B16-02 / B17-02 / B17-07（2026-11-23）

- **B15-06 已实测模型**：`IntegrationLog.__table__.indexes=[]`，模型层完全没有索引；Alembic 里的 `uq_integration_logs_erp_success_event` 不会由 `create_all` 建出来。
  ```text
  [B15-06] IntegrationLog indexes=[]
  ```
- **B16-02 已实测**：`insights._f(1.005)` 得到 `1.0`；`round(float(value),2)` 的二进制/银行家舍入，不是 1.01。
  ```text
  [B16-02] insights._f(1.005)=1.0
  ```
- **B17-02 已实测**：`tools._money(Decimal("1.005"))` 得到 `1.0`；同一函数还用于 quantity/weight，数量也会被压到 2 位。
  ```text
  [B17-02] tools._money(Decimal('1.005'))=1.0
  ```
- **B17-07 已实测 TypeError**：`quantity` 传 float `2.5` 调 `compute_weight_and_volume`，`Decimal * float` 直接炸。
  ```text
  [B17-07] float quantity -> TypeError: unsupported operand type(s) for *: 'decimal.Decimal' and 'float'
  ```

### B18-02 / B19-03（2026-11-23）

- **B18-02 已实测**：`target_snapshot` 把 `Decimal("123456789.123456789")` 输出成 `123456789.12345679`（float）；`repeat_customer_target` 同样丢位。
  ```text
  [B18-02] sales_target=123456789.12345679 type=float
  [B18-02] repeat_customer_target=987654321.9876543 type=float
  ```
- **B19-03 已实测**：`opportunity._number(Decimal("123456789.123456789"))` 输出 `123456789.12345679`（float），金额出口无 exact 串。
  ```text
  [B19-03] _number(...)=123456789.12345679 type=float
  ```


### B20-03 / B21-02（2026-11-23）

- **B20-03 已实测**：`OpportunityAssign(owner_id=None)` 通过；清空负责人后，`data_scope=self` 的可见性会按 `owner_id is None` 落到 403/不可见分支。
  ```text
  [B20-03] owner_id=None 通过
  ```
- **B21-02 已不成立（当前代码已修）**：`SampleItemInput.item_name`、`SampleConfirm.remark`、`SampleApprove.reject_reason`、`SampleShip.carrier/tracking_no` 全部已有 `max_length`，300 字均被 Pydantic 拒 `string_too_long`。
  ```text
  [B21-02-item_name] 被拒 string_too_long
  [B21-02-confirm] 被拒 string_too_long
  [B21-02-approve] 被拒 string_too_long
  [B21-02-ship] 被拒 string_too_long
  ```

### B21-01（2026-11-23）

`sample/service._f` 实测：

```text
[B21-01] _f(Decimal('1.00005'))=1.0001
[B21-01] _f(Decimal('123456789.123456'))=123456789.1235
```

`sample_fee/shipping_fee/quantity/craft_points` 走 `round(float(value),4)`，第五位直接四舍五入，出口无 exact 串；B21-01 成立。


### B20-05（2026-11-23）

- **已不成立（当前代码已修）**：`sku_fields_from_row` 现在走 `normalize_numeric`/`parse_numeric_field`，按 `NUMERIC_RULES` 校验。实测 13 位长度、5 位小数、负数、1.9 装箱数全部被 `ValueError` 拒。
  ```text
  长=1234567890123 -> 被拒 ValueError: ...最多 12 位...
  宽=1.23456 -> 被拒 ValueError: ...最多 12 位...
  重量=-5 -> 被拒 ValueError: ...不能是负数...
  装箱数=1.9 -> 被拒 ValueError: ...必须是整数...
  ```

### B20-06（2026-11-23）

`normalize_field_value` 对文本字段只 `str(value).strip()`，不按列宽校验。实测 300 字全部通过，随后会撞 `String(200/64/16)`：

```text
[B20-06] name 长度300 -> 通过 len=300 列宽=200
[B20-06] specification 长度300 -> 通过 len=300 列宽=200
[B20-06] color 长度300 -> 通过 len=300 列宽=64
[B20-06] material 长度300 -> 通过 len=300 列宽=64
[B20-06] package_type 长度300 -> 通过 len=300 列宽=64
[B20-06] unit 长度300 -> 通过 len=300 列宽=16
```

结论：B20-06 成立；核定“以外部为准”写超长文本时，错误会落在数据库 `flush()`，不是核定入口。


### B22-04 / B23-03（2026-11-23）

- **B22-04 已不成立（当前代码已修）**：`sample/router.py:791-807` 的 `revise_sample` 已复制 `sample_type/product_category/maker_unit/estimated_hours/craft_points/sample_purpose/urgency/sample_situation/deliver_date/sample_maker` 等单头字段；原报告“全没复制”已过期。
- **B23-03 已不成立（当前代码已修）**：`core/importing.py:696-705` 捕获 `commit` 异常后只记 `logger.warning` 的类型名，并抛统一文案「暂时无法确认导入是否完成…」，不再把 `str(exc)` 放进响应。

### B21-04 / B22-05（2026-11-23）

- **B21-04 已实测**：`ProductInsight._serialize` 把 `price_assumption=Decimal("123456789.12")` 输出为 `123456789.12`，类型 `float`；洞察价格假设出口无 exact 串。
  ```text
  [B21-04] price_assumption=123456789.12 type=float
  ```
- **B22-05 已实测**：`_active_owner` 只接收 `(session, owner_id)`，没有调用者/范围参数；传一个外部门 20 的 active 用户 202，直接通过并返回。
  ```text
  [B22-05] _active_owner(202) -> id=202 name=外部门员工 dept=20 status=active
  ```
