# B05 金额核心·收款/幂等 + ERP 适配器（9 文件，3,641 行）

> 审查范围：`payment/service.py`、`payment/router.py`、`core/idempotency.py`、`core/trade_mode.py`、`core/timebase.py`、`core/confirmation.py`、`core/base.py`、`core/database.py`、`modules/erp/adapter.py`。
> 全部逐文件读完；辅助核对了 `payment/model.py`、`payment/schema.py`、`order/service.py:70-89`、`core/deps.py:91-106` 与前端调用点。只读，未改业务代码。
>
> 补充审查（2026-11-21）：边界值、事务边界、异常/日志、审计与测试覆盖见 `review/blocks/B05-补充审查-边界事务异常审计与测试覆盖.md`；新增 B05-19（确认/驳回审计缺金额/币种/订单/期次）、B05-20（逾期刷新分批提交非原子）。原 B05-01..16 未重写。

## 一、逐文件检查记录

### 1. `backend/app/modules/payment/service.py`（1,029 行，已读全文）

**我检查了什么**：应收/回款/分配三张表的口径；`effective_allocations` 是否双计或跨订单；`allocate_payment` 的自动/人工分配、币种、合计上限；`rebalance_order_allocations` 的触发与审计；`recalc_plan` 的终态与逾期口径；`split_amounts` 的舍入与末期补差；`get_visible_payment` 的锁序与脏数据冻结；`order_finance_summary` 的取消单/超收口径。

**结果如何**：
- 金额主链路是 `Decimal`；`Numeric(16,2)` 与 `Decimal` 入参一致，没有发现 float 直接参与分配计算（出参 `_f` 另计，沿用 B01-02）。
- `effective_allocations` 用“有效分配 + 仅无分配行的 legacy 隐含归属”并 join 校验 `ReceivablePlan.order_id == PaymentRecord.order_id`，未发现同一笔钱被双计；`allocation_totals` 对每笔回款的待分配额做 `max(cash-allocated, 0)`，不会用一笔超分配掩盖另一笔的待分配。
- 锁序为“订单 → 应收节点 → 回款 → 分配行”。`get_visible_payment(for_update=True)` 先锁订单，再锁节点，再锁回款；发现 `record.receivable_plan_id` 与 `record.order_id` 归属不一致时直接 422 冻结。`get_visible_plan(for_update=True)` 也先锁订单。`update_receivable`、`confirm_payment`、`create_payment`、`update_payment_allocations` 都会先拿同一张订单行锁，因此同一订单的收款/改应收是串行的，未发现“两笔并发确认各写各的”的竞态。
- **主要问题在“改应收金额后系统自动重排历史回款归属”**：`rebalance_order_allocations` 会删掉全部非人工回款的自动分配，把 legacy 回款也改成 `auto`，再按到期日重分；调用点只有 `update_receivable`，且只写了一条“应收节点修改”审计，没有逐笔回款的“原期 → 新期”审计（B05-01）。
- `allocate_payment` 的人工分支只把人工分配行求和返回，服务层不再校验“合计 ≤ 回款金额”；路由层校验了，但迁移/直调/历史脏数据可绕过（B05-12）。
- 自动分配只给空币种的应收节点填回款币种，不拒绝“节点币种 ≠ 回款币种”；正常新建/生成/转单路径都继承订单币种，只有历史混合数据能触发（B05-13，待确认）。
- `split_amounts` 前 N-1 期 `(total*ratio).quantize(0.01)` 未指定 `rounding`，实际为 `ROUND_HALF_EVEN`，与 `core/money` 的 HALF_UP 不一致；末期补差保证合计等于总额，未发现少一分/多一分。沿用 B03-15，本块复核成立。
- `order_finance_summary` 对取消订单仍按 `total - received` 返回 `unreceived_amount`，而订单详情 `serialize_order` 对取消单已归零；当前前端未调用 `finance-summary`，但接口语义不一致（B05-11）。

### 2. `backend/app/modules/payment/router.py`（1,252 行，已读全文）

**我检查了什么**：14 个应收/回款接口的权限、数据范围、生命周期闸门、锁、幂等键、分配合计、币种、凭证、审计；重点看“重复点击/网络重试/回调重放”。

**结果如何**：
- 列表/详情/凭证下载都按订单数据范围过滤；`get_receivable`/`get_payment` 走 `get_visible_*`；`preview_receivables` 已补 `assert_order_visible`；`generate` 的幂等指纹含路径 `order_id`，回放前复验结果归属，失败释放占位。这部分没有发现越权或重复生成。
- `create_payment` 的 `request_key` 是**可选**的：不带键时直接建行，响应只提示“弱网重试可能产生重复回款”。双击/重试确实会生成两笔待确认回款；若财务再逐笔确认，就是重复入账（B05-04）。
- 手工建应收 `_create_plan` **完全没有幂等键**，而按比例生成 `generate` 有；双击 `POST /receivables` 会建两条相同应收（B05-02）。
- 手工建应收/改应收金额都没有“有效应收合计 ≤ 订单总额”的校验；`generate` 用 `total = order.total_amount` 并末期补差，手工路径可以建出总额 200 的应收挂在总额 100 的订单上（B05-03）。
- `update_receivable` 不检查 `plan.status == "cancelled"`，已取消节点仍可改名称/到期日/金额/备注；改金额还会触发 `rebalance_order_allocations`（B05-08）。
- `delete_receivable` 的删除闸门是“只要有任何 `PaymentRecord`（不论 pending/confirmed/rejected）或任何分配行就拒绝”；系统没有删除/作废回款的接口，因此一条被驳回的回款会让对应应收节点永久不可删（B05-09）。
- `update_payment_allocations` 校验了分配合计 ≤ 回款金额、目标期属于本单且未取消、整份替换、写审计；但未校验单期分配合计 ≤ 该期应收金额（属业务取舍，暂记观察，不单列）。
- `confirm_payment` 用 `ensure_payment_pending` + 订单/节点生命周期 + 锁内重查未来日期，重复确认会被 422 拦下；确认后先分配再逐期重算，未发现重复确认叠加。
- `upload_payment_voucher` 在持有订单/节点/回款行锁期间做磁盘 `save_upload`，大文件会把订单取消/确认阻塞在锁上（B05-16，P3）。
- `plan_payments` 只按 `receivable_plan_id` 或有效分配关联，没有再校验回款的 `order_id`；历史脏数据（回款挂 A 期但 order_id 是 B 单）可把 B 单回款展示在 A 期（B05-10）。
- `finance_summary` 入口本身有 `assert_order_visible`；金额口径问题在 service（B05-11）。

### 3. `backend/app/core/idempotency.py`（241 行，已读全文）

**我检查了什么**：`reserve`/`_judge`/`complete`/`release` 的唯一键、同键同内容、同键不同内容、并发占位、失败释放、过期清理。

**结果如何**：
- 唯一键 `(user_id, action, request_key)` 正确；`begin_nested()` 让并发插入撞唯一约束时只回滚本次占位，不连带外层事务；`_judge` 对 done 回放、对 in_flight 明确 409，未发现“同键并发建两条”。
- `complete` 统一做 `json_safe`，避免 `datetime/Decimal` 在 commit 阶段才报错；这是对的。
- **in_flight 没有超时/清理**：进程在 `reserve` 之后、`complete`/`release` 之前崩溃，行会永久停在 in_flight；同一把键后续永远 409“正在处理中”，只能换键。表只有 `created_at` 索引，没有 reaper（B05-07）。
- `release` 失败时 `await session.rollback()` 会回滚整个外层事务；作为补偿动作可以接受，但会让“释放失败”顺带丢掉同事务其他写入（P3，未单列）。
- `request_key_from` 明确允许“都不给就是 None，不做幂等”；这不是底座缺陷，但调用方把它用在回款创建上就形成 B05-04。

### 4. `backend/app/core/trade_mode.py`（161 行，已读全文）

**我检查了什么**：CNY 白名单函数、口径开关、继承路径、下游拒绝函数、AST 对账清单。

**结果如何**：
- `ensure_currency_allowed` 现在是固定 CNY 白名单，不读 `trade_mode`；空值按 CNY；非 CNY 400。`reject_foreign_currency` 对历史外币在下游计算前拒绝。函数本身正确。
- **`CURRENCY_GATE_SITES` 只列 `quote/service.py:create_quote`、`order/service.py:create_order`、`order/drafts.py:update` 三处**，注释明确把“订单继承报价版本、回款继承应收节点、商机复制”等继承路径排除；而同一文件又承认“历史数据里本来就可能已经有外币记录（口径曾经是 export 或 ERP 同步）”。这意味着历史外币报价仍可经“报价 → 草稿 → 订单 → 应收 → 回款”继续流转，和项目规则“非人民币应明确拒绝”存在缺口。
- 这不是本文件的实现错误，而是**规则边界**：B03-03 已记录该链路；本块复核确认 `trade_mode` 是“有意不拦继承”，因此 B03-03 应作为**待业务决定**的规则缺口，而不是“实现漏了一个 if”。本块不重复编号，只在跨块观察中写清。

### 5. `backend/app/core/timebase.py`（124 行，已读全文）

**我检查了什么**：业务时区、`today_business`、`business_day_start/end`、`year_bounds`、SQL 归月/归年。

**结果如何**：统一 `Asia/Shanghai`；存储仍 UTC；`to_business` 对 naive 按 UTC 解释；`business_day_end` 修正了“北京时间当 UTC”的 8 小时偏移；`business_month/year` 用 SQL 字面量避免 GROUP BY 参数节点不等。未发现金额/逾期日期口径问题。

### 6. `backend/app/core/confirmation.py`（45 行，已读全文）

**我检查了什么**：确认动作的统一判据是否真的被使用。

**结果如何**：`confirmation_message` 被订单/报价/审批/ERP/打样等路由使用；但 `require_confirmation()` **没有任何调用点**（`rg` 只命中定义本身，各路由是自行 `if not confirm: raise AppError(... confirmation_message(...))`）。文件开头“判据只有这一份：`require_confirmation()`”与实际不符，属死代码/文档漂移；新增确认动作可能继续各写一份（B05-15，P3）。

### 7. `backend/app/core/base.py`（48 行，已读全文）

**我检查了什么**：主键、时间戳默认值、JSON 列变体。

**结果如何**：`BigInt` 自增；`created_at/updated_at` 同时有 Python 与数据库默认值，避免异步会话 flush 后回查触发 `MissingGreenlet`；`JSONType` 在 PG 用 JSONB。未发现金额相关逻辑。

### 8. `backend/app/core/database.py`（31 行，已读全文）

**我检查了什么**：连接池、会话生命周期、异常回滚。

**结果如何**：`pool_pre_ping`、`expire_on_commit=False`；`get_db` 对 `Exception` 显式 rollback，`CancelledError` 依赖 `async with SessionLocal()` 关闭时回滚。未发现金额/事务半截写入问题；取消请求的回滚是隐式的，但 SQLAlchemy 会话关闭会回滚，不单列。

### 9. `backend/app/modules/erp/adapter.py`（710 行，已读全文）

**我检查了什么**：推送/回读的幂等语义、明确失败与“结果未知”的区分、外部单号校验、能力验收、错误分类、连接生命周期。

**结果如何**：
- 适配器把“4xx / 业务 code 非 0”归为 `ErpError`（明确失败），把超时/传输/非 JSON/5xx 归为 `ErpResultUnknown`；本地 `push_order` 由 service 用订单行锁 + `erp_order_id`/`ExternalMapping` 防重（service 未在本块，B06 复核）。
- **HTTP 200 但响应缺 `code`、或 `code` 不是整数时，抛的是 `ErpError`（`peer_error`），不是 `ErpResultUnknown`**。上层会把“对方可能已经建单、只是回包结构不对”当成“明确失败”而直接重试，存在重复建外单的风险（B05-05，P1）。
- `fetch_order_status` 直接取 `data.orders[0]`，没有校验返回的 `o_id` 是否等于请求的 `external_id`；ERP 返回多单/错单时，会把别人的履约状态写到当前 CRM 订单上（B05-06，P2）。
- `push_order` 在 `data` 缺失时返回 `external_id=""`，`external_code` 还会退回 `payload["so_id"]`（本地单号）。service 是否把“空 external_id + 本地 code”判成成功，需读 `erp/service.py` 才能定论，列入待确认（B05-P1，已由 B06 复核关闭）。
- 聚水潭的 read/write/collect 三项能力都 `verified=False`，`_call` 在发请求前就 `ErpNotVerified`；所以当前生产不会真的推单，也不会产生外部重复。等验收后如果把 `verified=True` 而不补签名，才会走到半成品请求；这是验收流程问题，不是当前金额漏洞。
- `get_adapter` 的 `_INSTANCES` 懒加载无锁，首次并发可能建两个 `httpx.AsyncClient`，其中一个泄漏；P3，不单列。

## 二、本块发现清单

### B05-01｜P1｜改应收金额会静默重排全部已确认回款的期次归属，且没有逐笔审计

- **位置**：`backend/app/modules/payment/service.py:300-333`、`backend/app/modules/payment/router.py:440-451`、`:460-469`
- **触发条件**：对一张已有确认回款的订单，`PATCH /receivables/{plan_id}` 修改任一期的 `amount`。`update_receivable` 调 `rebalance_order_allocations`，后者选出该订单全部 `confirmed` 回款；只要回款不是 `manual` 且没有人工分配行，就删掉它的 `auto` 分配、把 `allocation_mode` 改成 `auto`，再按到期日从早到晚重分。
- **后果**：已确认回款原来记在哪一期会被系统改写（legacy 回款也从“隐含整笔挂原期”变成“按到期日重分”）。主人明确要求“系统不能擅自调整已经登记的收款归属，必须由有权限的人员确认后才能调整”，且调整要保留原归属期数、新归属期数、金额、操作人、时间；现在只有一条 `business_type="receivable_plan"` 的审计，回款分配的前后对照不在审计里。对账时无法回答“这笔 5 万为什么从第 2 期跑到第 1 期”。
- **证据代码**：
  ```python
  # payment/service.py:321-333
  automatic = [record for record in records
               if record.allocation_mode != "manual" and record.id not in manual_ids]
  if automatic:
      await session.execute(delete(PaymentAllocation).where(
          PaymentAllocation.payment_id.in_([record.id for record in automatic]),
          PaymentAllocation.source == "auto",
      ))
      for record in automatic:
          record.allocation_mode = "auto"   # legacy 也被改成 auto
      await session.flush()
  for record in records:
      await allocate_payment(session, record, operator_id=operator_id)
  ```
  ```python
  # payment/router.py:440-441
  if amount_changed:
      await svc.rebalance_order_allocations(session, plan.order_id, operator_id=user.id)
  # :460-469 只写 receivable_plan 的 before/after，不含回款分配
  ```
- **建议**：把重平衡限制为“只处理未分配余额”，不动已登记回款的既有归属；确需重排时，逐笔写 `payment` 审计（原 `plan_id/金额`、新 `plan_id/金额`、操作人、时间、原因），并要求显式确认；对 legacy 回款保留原期，除非人工确认。补一条“改应收金额后回款归属不变/需确认”的回归用例。

### B05-02｜P1｜手工建应收没有请求幂等，双击会生成两条相同应收

- **位置**：`backend/app/modules/payment/router.py:113-158`、`backend/app/modules/payment/schema.py:15-28`
- **触发条件**：`POST /receivables` 或 `POST /orders/{id}/receivables` 双击/网络重试。`ReceivableCreate` 没有 `request_key`，`_create_plan` 也没有 `idempotency.reserve`；订单锁只能串行化两次插入，不能去重。
- **后果**：同一订单出现两条同名、同到期日、同金额的应收节点，`planned_amount` 翻倍。后续客户按计划付款，可能被要求付两次；即使发现后删除，也已产生重复催收和审计噪音。按比例生成 `generate` 有 `request_key`，手工入口没有，形成两条路的安全口径不一致。
- **证据代码**：
  ```python
  # router.py:128-138：直接建节点，没有 request_key / reserve
  plan = ReceivablePlan(
      order_id=order_id, plan_name=payload.plan_name, due_date=payload.due_date,
      amount=Decimal(str(payload.amount)), currency=order.currency, status="pending", ...
  )
  session.add(plan)
  ```
- **建议**：`ReceivableCreate` 增加 `request_key`，`_create_plan` 走 `idempotency.reserve/complete/release`；或加 `(order_id, plan_name, due_date, amount)` 唯一约束 + 409 提示“已存在相同应收节点”。

### B05-03｜P1｜应收合计没有上限，单条新建/改额可超过订单总额

- **位置**：`backend/app/modules/payment/router.py:128-137`、`:428-429`；对照 `:352-356` 的按比例生成。
- **触发条件**：对总额 100 的订单，`POST /receivables` 新建金额 200 的节点；或 `PATCH /receivables/{id}` 把某期从 50 改成 200。`assert_order_can_add_receivable` 只拦 `cancelled`，不校验“有效应收合计 ≤ 订单总额”。
- **后果**：`planned_amount` 可以大于 `order_amount`；`order_finance_summary` 的 `unreceived_amount = total - received` 会变成负数，订单详情却仍显示订单总额 100。客户可能按 200 被催收/收款；`allocate_payment` 会把回款一直填到这些超额的期里，形成“订单 100、应收 200、已收 200”的账。若业务确实允许附加费用，应先改订单金额并留审计，而不是让应收单方面超出。
- **证据代码**：
  ```python
  # _create_plan：amount 直接落库，没有 sum(active plans) 校验
  amount=Decimal(str(payload.amount))
  # update_receivable：只比较新旧金额，没有上限
  if amount_changed:
      plan.amount = Decimal(str(amount))
  ```
  ```python
  # generate 路径才有“合计等于订单总额”
  total = Decimal(order.total_amount)
  amounts = svc.split_amounts(total, ratios)
  ```
- **建议**：在 `_create_plan` 和 `update_receivable` 里校验 `sum(非 cancelled 计划金额) <= order.total_amount`；超出时 422，提示“请先调整订单金额/走订单变更”。若业务允许超额应收，需在验收标准里明确并禁止 `unreceived` 出现负数。

### B05-04｜P1｜`POST /payments` 的幂等键可选，不带键双击/重试会生成两笔待确认回款

- **位置**：`backend/app/modules/payment/router.py:993-1008`、`:1078-1080`；`backend/app/modules/payment/schema.py:145-159`
- **触发条件**：前端/脚本不传 `request_key`，也不带 `X-Request-Key`；双击“登记回款”或弱网自动重试。代码明确允许 `request_key=None`，只在响应文案里提示“本次未带请求键，弱网重试可能产生重复回款”。
- **后果**：同一客户、同一节点、同一日期、同一金额出现两笔 `pending` 回款；财务如果按列表逐笔确认，就会形成两笔 `confirmed`、两笔分配，重复入账。只在响应里警告不能防止重复，尤其是前端未展示该警告或用户没看到时。
- **证据代码**：
  ```python
  # router.py:997-1008
  request_key = idempotency.request_key_from(request, data.pop("request_key", None))
  reservation = None
  if request_key:
      reservation = await idempotency.reserve(...)
  ```
  ```python
  # router.py:1078-1080
  if request_key:
      return ok(body, "回款已登记，等待财务确认")
  return ok(body, "回款已登记，等待财务确认（本次未带请求键，弱网重试可能产生重复回款）")
  ```
- **建议**：`POST /payments` 强制要求请求键（前端表单生成 UUID，服务端拒绝空键）；或服务端在“同订单 + 同节点 + 同金额 + 同日期 + pending”已存在时返回 409/原记录。至少把“无键”从可选改成显式 `allow_duplicate=true` 的危险开关，默认不允许。

### B05-05｜P1｜ERP 适配器把 200 缺 `code`/`code` 非整数判成“明确失败”，上层可直接重试造成重复建单

- **位置**：`backend/app/modules/erp/adapter.py:544-556`
- **触发条件**：ERP 已受理建单，但 HTTP 200 响应缺少 `code` 字段，或 `code` 是字符串/非整数（网关包装、字段改名、截断）。`_call` 抛 `ErpError(kind="peer_error")`，不是 `ErpResultUnknown(kind="result_unknown")`。
- **后果**：`erp/service.py` 按适配器语义区分“明确失败可重试”和“结果未知先核对”；这里会把“可能已经建单”的情况交给“明确失败”分支，重试时如果对方没有按 `idempotency_key` 真正去重，就会建出第二张外部单。适配器注释自己写了“对方是否真按这个字段去重尚未真实验收”，所以这个分类错误是实际风险。
- **证据代码**：
  ```python
  raw_code = data.get("code")
  if raw_code is None:
      raise ErpError("响应缺少 code 字段，无法确认对方是否受理", ...)
  try:
      code = int(raw_code)
  except (TypeError, ValueError):
      raise ErpError(f"响应 code 不是整数：{raw_code!r}", ...)
  ```
- **建议**：HTTP 200 但成功信封不完整/不可解析时一律 `ErpResultUnknown`；只有 4xx 或明确 `code != 0` 且 code 可解析才是 `ErpError`。同时在 service 侧对 `result_unknown` 先按 `erp_order_id`/`so_id` 回查，确认没有外单再重试。

### B05-06｜P2｜`fetch_order_status` 取第一条订单，不校验 `o_id` 与请求一致

- **位置**：`backend/app/modules/erp/adapter.py:593-623`
- **触发条件**：ERP 查询接口返回 `data.orders` 多条，或返回的不是请求的 `external_id`（对方分页/过滤 bug、缓存串单）。适配器直接取 `orders[0]`，把它的 `status` 和 `send_date` 返回。
- **后果**：当前 CRM 订单会被写成另一张订单的履约状态；若对方第一单是 `Received/Finished`，本单可能被错误标记为已签收/已完成，后续发货、收款、对账都按错误状态走。
- **证据代码**：
  ```python
  if not orders:
      return {"status": None, "shipped_at": None, "raw": data}
  first = orders[0]
  raw_status = str(first.get("status") or "")
  return {"status": self.STATUS_MAP.get(raw_status), "shipped_at": first.get("send_date"), ...}
  # 没有 first.get("o_id") == external_id 的校验
  ```
- **建议**：在 `orders` 中精确匹配 `o_id == external_id`；匹配不到返回 `status=None` 或 `ErpResultUnknown`，由 service 保持原状态并记日志。不要把“第一条”当成“请求的那一条”。

### B05-07｜P2｜`in_flight` 幂等占位无超时/清理，进程崩溃后同键永久 409

- **位置**：`backend/app/core/idempotency.py:168-186`、`:52-77`
- **触发条件**：请求在 `reserve` 成功（写入了 `in_flight` 行）之后、`complete`/`release` 之前进程崩溃/被杀/数据库连接中断；外层事务回滚可能带走占位，但如果占位已随某个提交落库，就会留下永久 `in_flight`。同一用户、同一 action、同一 `request_key` 再提交时，`_judge` 只认 `done` 或抛 409。
- **后果**：用户拿着同一把键永远无法重试，只能刷新页面换键；如果前端把键存在表单里，会表现为“一直提示正在处理中”。表也没有定期清理，`request_keys` 会无限增长。
- **证据代码**：
  ```python
  if existing.status == "done":
      return Reservation(...)
  raise AppError(ErrorCode.DUPLICATE, "这次提交正在处理中（或上一次没有正常结束），请稍后重试，不要重复点击", 409)
  # 没有 created_at 超时判断，也没有 reaper
  ```
- **建议**：给 `in_flight` 设超时（例如 5/10 分钟），超时后在同一唯一键下用 `SELECT ... FOR UPDATE` 接管重试；增加定时清理 `created_at` 超过 N 天的 done/in_flight 行；前端对 409 提供“换键重试”入口。

### B05-08｜P2｜`update_receivable` 对已取消节点没有终态保护

- **位置**：`backend/app/modules/payment/router.py:419-441`
- **触发条件**：订单取消后，应收节点被置 `cancelled`；随后 `PATCH /receivables/{plan_id}` 改名称、到期日、金额或备注。`get_visible_plan` 不拦 `cancelled`，`changes` 直接赋值。
- **后果**：已取消节点的金额/到期日被改写，历史“取消时金额”丢失；改金额还会触发 `rebalance_order_allocations`（虽然 `recalc_plan` 跳过 cancelled，但重排会改其他期的分配）。订单取消是终态，这种修改会让取消时的账无法复原。
- **证据代码**：
  ```python
  plan = await svc.get_visible_plan(session, user, plan_id, for_update=True)
  changes = payload.model_dump(exclude_unset=True)
  ...
  if amount_changed:
      plan.amount = Decimal(str(amount))
  ...
  if amount_changed:
      await svc.rebalance_order_allocations(session, plan.order_id, operator_id=user.id)
  ```
- **建议**：`plan.status == "cancelled"` 时拒绝除“备注/说明”外的所有修改；要恢复金额必须先恢复订单并留审计。补一条“取消节点 PATCH 422”的回归。

### B05-09｜P2｜已驳回回款永久阻止删除应收节点，系统没有删除/作废回款入口

- **位置**：`backend/app/modules/payment/router.py:491-500`；回款路由无 `DELETE /payments/{id}`（只有凭证和分配）。
- **触发条件**：财务驳回一笔回款（例如金额录错），之后想删除对应的应收节点。`delete_receivable` 查 `PaymentRecord` 不看状态，只要有 `receivable_plan_id` 指向该期就拒绝；`PaymentAllocation` 也同理。
- **后果**：一条被驳回的回款会让该应收节点永久不可删；`serialize_plan.has_payment_record` 也会让前端隐藏删除按钮。用户只能改金额/备注，无法清理错误计划，长期积累脏节点。
- **证据代码**：
  ```python
  paid = (await session.execute(
      select(PaymentRecord.id).where(PaymentRecord.receivable_plan_id == plan_id)
  )).first()
  allocated = (await session.execute(select(PaymentAllocation.id).where(
      PaymentAllocation.plan_id == plan_id).limit(1))).first()
  if paid or allocated:
      raise AppError(..., "该应收节点已有回款记录或收款分配，不能删除...", 422)
  ```
- **建议**：只拦 `pending/confirmed` 回款；`rejected` 回款允许在“作废/删除回款”入口处理后释放节点，或提供“作废回款（保留审计、不参与金额）”动作。删除节点时把 `rejected` 回款的 `receivable_plan_id` 置空并留审计。

### B05-10｜P2｜`plan_payments` 未过滤回款 `order_id`，脏数据可跨订单展示回款

- **位置**：`backend/app/modules/payment/router.py:1224-1233`
- **触发条件**：历史/迁移数据里 `PaymentRecord.receivable_plan_id` 指向订单 A 的期，但 `PaymentRecord.order_id` 是订单 B（该列无外键，`payment/model.py:112`）。有权看订单 A 的用户打开 A 期的回款列表。
- **后果**：查询用 `or_(PaymentRecord.receivable_plan_id == plan_id, PaymentRecord.id.in_(有效分配...))`，没有 `PaymentRecord.order_id == plan.order_id`，会把 B 单的回款金额、日期、方式、凭证信息展示给 A 单的可见者。`get_visible_payment` 在单条入口会冻结这种脏数据，但列表入口没有同样的闸门。
- **证据代码**：
  ```python
  rows = (await session.execute(
      select(PaymentRecord)
      .where(or_(PaymentRecord.receivable_plan_id == plan_id,
                 PaymentRecord.id.in_(select(effective.c.payment_id).where(effective.c.plan_id == plan_id))))
      .order_by(PaymentRecord.received_date.asc())
  )).scalars().all()
  ```
- **建议**：加 `PaymentRecord.order_id == plan.order_id`；发现不一致时记日志并跳过/冻结，和 `get_visible_payment` 的 422 口径一致。补一条脏数据回归。

### B05-11｜P3｜`order_finance_summary` 对取消订单仍返回未收金额，超收会显示负数

- **位置**：`backend/app/modules/payment/service.py:1009`；对照 `backend/app/modules/order/service.py:82-89`。
- **触发条件**：订单已取消且没有确认回款；或订单被超收（`received > total`）。调用 `GET /orders/{id}/finance-summary`。
- **后果**：取消订单的 `unreceived_amount` 仍是 `total - received`（例如取消单仍显示“未收 17 万”），而订单详情 `serialize_order` 对取消单已归零；两者对同一事实两个说法。超收时 `unreceived_amount` 为负数，若接入页面会显示“负待收”。当前前端未调用 `finance-summary`（`rg` 无 `getFinanceSummary`），所以是接口语义隐患而非现网展示。
- **证据代码**：
  ```python
  "unreceived_amount": (total or ZERO) - Decimal(received),
  ```
  ```python
  # order/service.py:84-89
  "unreceived_amount_exact": str(
      Decimal(0) if order.status == "cancelled" else (order.total_amount or Decimal(0)) - received
  ),
  ```
- **建议**：与 `serialize_order` 同口径：订单 `cancelled` 或全部计划 `cancelled` 时 `unreceived_amount=0`；超收单独返回 `overpaid_amount`，不要把负数塞进“未收”。

### B05-12｜P2｜`allocate_payment` 人工分支不校验分配合计 ≤ 回款金额

- **位置**：`backend/app/modules/payment/service.py:202-204`
- **触发条件**：`allocation_mode="manual"` 的回款存在人工分配行合计大于 `received_amount`。路由 `update_payment_allocations` 会校验，但迁移数据、直接 service 调用或历史脏数据可以绕过。
- **后果**：`allocation_totals` 里 `allocated_amount` 大于 `received_amount`，`unallocated_amount` 被 `max(cash-allocated,0)` 压成 0；相关应收期被虚增为已收。现金总额没变，但“这笔钱分到哪期”超过实际到账，对账无法解释。
- **证据代码**：
  ```python
  if record.allocation_mode == "manual":
      return Decimal((await session.execute(select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
          .where(PaymentAllocation.payment_id == record.id))).scalar_one())
  ```
- **建议**：在 service 内也做 `total <= record.received_amount` 的断言，超出时抛 422/裁剪；迁移脚本增加一条“人工分配合计不得大于回款金额”的校验。

### B05-13｜P3｜自动分配不校验应收节点币种与回款币种一致

- **位置**：`backend/app/modules/payment/service.py:236-258`
- **触发条件**：同一订单存在不同币种的应收节点（正常新建/生成/转单都继承订单币种，只有历史混合数据/直接改库能造出）。一笔 USD 回款确认后，`allocate_payment` 遍历该订单所有非取消节点，只给空币种填值，不拒绝 CNY 节点。
- **后果**：USD 金额被 1:1 分配到 CNY 应收期，节点“已收”虚增（或反之），人民币口径汇总错误。正常 API 路径不可达，属历史数据防御缺口。
- **证据代码**：
  ```python
  for plan in plans:
      ...
      plan.currency = plan.currency or record.currency
      # 没有 if plan.currency != record.currency: raise/continue
  ```
- **建议**：分配前统一校验 `plan.currency == record.currency`；不一致的节点跳过并记日志/冻结，等数据修复。不要靠“正常路径都一样”来保证。

### B05-14｜P3｜`require_confirmation` 是死代码，各路由自行拼确认文案

- **位置**：`backend/app/core/confirmation.py:33-45`；调用点见 `order/router.py:252-281`、`quote/router.py:985-1030`、`approval/rules_router.py:416-432`、`erp/service.py:445-467`、`sample/router.py:1168-1190`。
- **触发条件**：新增一个“点一下就覆盖金额/切换依据/写外部系统”的接口，开发者只复制 `confirmation_message` 而不写 `if not confirm`。
- **后果**：文件声称“判据只有这一份”，实际 `require_confirmation` 没有调用点，确认闸门靠各路由自觉；漏一处就没有 `CONFIRM_REQUIRED`。这是可维护性/流程风险，不是当前金额漏洞。
- **证据代码**：
  ```python
  def require_confirmation(confirm: bool, *, action: str, detail: str = "") -> None:
      if confirm:
          return
      raise AppError(ErrorCode.CONFIRM_REQUIRED, ...)
  # rg 只命中定义本身，没有业务调用
  ```
- **建议**：所有确认动作统一调用 `require_confirmation`；或删掉该函数、把文件说明改成“只提供文案”，避免虚假的统一入口。加一条静态检查：确认类路由必须出现 `require_confirmation`。

### B05-15｜P3｜上传回款凭证在持有订单/节点/回款锁期间做磁盘写入

- **位置**：`backend/app/modules/payment/router.py:641-659`
- **触发条件**：上传较大的凭证文件（或存储后端慢）。`get_visible_payment(for_update=True)` 已锁订单→节点→回款，随后 `await storage.save_upload(file)` 才写 `FileRecord` 并 commit。
- **后果**：同一订单的取消、确认、改应收等操作会在订单行锁上等待磁盘/对象存储写完；大文件或慢存储会把财务操作卡住，超时后客户端重试又可能重复上传。不是金额错误，但是可观测的锁持有时间问题。
- **证据代码**：
  ```python
  record = await svc.get_visible_payment(session, user, payment_id, for_update=True)
  ...
  object_key, size, checksum = await storage.save_upload(file)
  voucher = FileRecord(...)
  ```
- **建议**：先在锁外完成文件落盘/校验，再短事务锁记录并绑定 `voucher_file_id`；或把上传改成“先拿临时对象，确认时再绑定”。至少给上传设置大小/超时上限。

## 三、待确认（不作为结论）

- **B05-P1（交 B06）**：`JushuitanAdapter.push_order` 在响应 `data` 缺失时会返回 `external_id=""`、`external_code` 退回本地 `so_id`（`erp/adapter.py:567-591`）。`erp/service.py` 是否把这个组合判成“成功/已同步”，决定是否存在“没有外部单号却标已推送”的问题；本块未读 service，不能下结论。
  - **B06 复核（2026-11-21）关闭**：`erp/service.py:321-343` 的 `_require_external_credential` 对空 `external_id` 和“等于本地 `so_id`”都抛 `ErpResultUnknown`，不会标成功；本条关闭。
- **B05-P2（B03-03 的口径）**：`core/trade_mode.py:153-157` 的 `CURRENCY_GATE_SITES` 明确只列三个入口，注释把“订单继承报价版本、回款继承应收节点”等继承路径排除；同一文件又承认历史外币可能来自 export/ERP。按项目规则“非人民币应明确拒绝”，这是规则缺口；但代码作者写明“有意不拦继承”。需要业务确认：历史外币单据是允许继续流转，还是必须在转单/收款前强制改成 CNY。B03-03 仍按 P1 保留，状态改为“待决策”，不在 B05 重复编号。
- **B05-P3（前端是否总带键）**：`POST /payments` 的无键路径已确认可重复建行；前端 `shared/api/requestKey.ts` 是否存在、回款表单是否实际使用，需在 F02/F14 核对。若前端始终带键，现网触发面会小很多，但 API 层风险仍在。

## 四、本块结论

- **P0：0 条**。本块没有发现“无人工介入就自动重复入账/丢钱”的路径；重复入账需要财务对两笔 pending 都确认（B05-04），或 ERP 重试且对方未去重（B05-05），因此按 P1 记录。
- **P1：5 条**（B05-01 至 B05-05）。核心是“改应收金额静默改历史回款归属且无逐笔审计”“手工应收/回款入口缺幂等或额度上限”“ERP 成功信封异常被误判为明确失败”。
- **P2：6 条**（B05-06 至 B05-10、B05-12）。集中在 ERP 回读不校验单号、幂等占位无超时、取消节点可改、驳回回款卡死删除、脏数据跨订单展示、人工分配合计无服务层校验。
- **P3：4 条**（B05-11、B05-13、B05-14、B05-15）。
- **待确认：2 条**（B05-P2、B05-P3；B05-P1 已由 B06 复核关闭）。
- **未读完：无**。9 个文件均逐行读完；`payment/model.py`、`payment/schema.py` 作为辅助证据读了全文。
