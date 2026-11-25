# B06 对账·ERP 采集与适配（3 文件，3,268 行）

> 审查范围：`backend/app/modules/erp/collection.py`（1,603 行）、`backend/app/modules/erp/service.py`（1,559 行）、`backend/app/modules/erp/schema.py`（106 行）。
> 3 个文件全部逐行读完；辅助核对了 `integration/model.py`、`order/model.py`、`order/service.py:1167-1178`、`core/trade_mode.py`、`erp/router.py` 的相关入口与迁移 `f7b8c9d0e1a2_external_mapping_unique.py`。只读，未改业务代码。
>
> 补充审查（2026-11-22）：边界值、事务边界、异常/日志、审计与测试覆盖见 `review/blocks/B06-补充审查-边界事务异常审计与测试覆盖.md`；新增 B06-15（采集错误原文回前端）、B06-16（映射重放无审计）、B06-17（失败落盘被吞）、B06-18（推单/核对审计提交边界）及待确认 B06-P4（负数/0 采集值）。原 B06-01..14 未重写。

## 一、逐文件检查记录

### 1. `backend/app/modules/erp/collection.py`（1,603 行，已读全文）

**我检查了什么**：采集水位/断点/租约；分页续拉与“至少一次 + 幂等落库”；`external_records` 唯一键；金额/数量/币种规范化；订单/客户/SKU/履约明细的匹配规则；待匹配队列与人工映射；全局重放；采集状态与数据范围。

**结果如何**：
- 分页断点设计是正确的：`_claim_cursor` 抢租约、每页 `commit` 后才推进 `page_token`、失败时 `_fail_quietly` 回滚当前页并保留断点；`external_records` 的 `(system_type, shop_id, object_type, dedupe_key)` 唯一约束保证重拉不重复。没有发现“崩溃后漏一页”或“重复落一行”的确定性缺陷。
- 金额入库是 `Decimal`：`_as_decimal` 走 `Decimal(str(value))`，`_external_decimal` 限制 18 位/4 位小数；没有 float 参与落库。`serialize_record` 出参用 `float`，只影响展示（B06-11）。
- **外币口径自相矛盾**：`normalize_item` 对每条记录调用 `reject_foreign_currency`，非 CNY 直接抛 422；`collect_once` 捕获异常后整页回滚并置 `failed`。而 `core/trade_mode.py` 的边界说明明确写“外部同步（ERP）不拦：拒收会让整批同步失败，而外部数据不是我们说了算”。结果是**一条外币记录会把整页/整轮采集拖死**，不是“统计页提醒”，而是“采集永远停在这一页”（B06-03）。
- **缺币种默认 CNY**：`currency = (_text(item.get("currency")) or CNY).upper()`。对方未给币种时不会标“未知”，而是按人民币落库并通过 `reject_foreign_currency`。如果外部记录实际是外币，这就是静默错币种（B06-07）。
- **订单映射的 `parent_key` 被写成自己**：`normalize_item` 对订单设置 `order_key = dedupe_key`，`_ensure_mapping` 对所有对象（包括 order）执行 `mapping.parent_key = record.order_key or record.parent_key`。于是订单映射的 `parent_key` 等于它自己的 `external_id`。路由的人工匹配 `_ensure_mapping_target_scope` 看到 `mapping.parent_key` 非空，就去找“父订单映射”，找到的是它自己；此时它自己的 `internal_id` 还是空，直接 409“请先匹配父订单”。**未匹配的订单映射无法人工匹配**（B06-01）。
- **`assign_mapping` 会顺手全局重放**：人工匹配成功后调 `replay_pending_mappings(session, system_type=..., shop_id=...)`，没有传 `object_type`，也没有按操作人的数据范围限制。显式重放路由 `POST /external-mappings/replay` 反而明确拒绝非全量范围用户；人工匹配这条路径把同一系统/店铺下所有待匹配对象（订单、客户、SKU、履约明细）一起改了。一个 `order:manage` 用户只要拿到一个 mapping_id，就能触发全局映射变更（B06-02）。
- **`replay_pending_mappings` 没有行锁**：查询 pending/conflict 后直接 `resolve_mapping` 并覆盖 `internal_id/match_status`。`get_mapping` 只锁单条人工匹配行；两个管理员并发“人工匹配 + 重放”时，重放可能把人工刚写的 `matched` 覆盖回自动结果或 pending（B06-08）。
- 订单匹配按 `erp_order_id` 用 `.first()`：`SalesOrder.erp_order_id` 没有唯一约束（`order/model.py:85`），历史重复/手工写入时会把外部订单挂到第一条，而不是报冲突。`service.py:_load_order_by_external` 同样 `.first()`（B06-06）。
- `list_mappings` 对有限范围用户只保留 `object_type=="order"` 且 `internal_id in scoped_orders`；待匹配订单的 `internal_id` 是 `NULL`，所以**销售看不到自己范围内的待匹配订单**，也就无法发起人工匹配（B06-10，P3）。
- `collection_status` 返回全公司来源核实台账、全部水位、全量记录数/待匹配数；路由只要求 `order:view`，没有按数据范围过滤（B06-08，P2）。

### 2. `backend/app/modules/erp/service.py`（1,559 行，已读全文）

**我检查了什么**：推单幂等与“结果未知”；订单行锁与独立会话日志；外部单号可信性；主表/映射双写；人工对账登记；拉回状态的锁与守卫；webhook 双编号同源、事件键、去重、重放；日志/异常队列/就绪度的数据范围。

**结果如何**：
- 推单主链路是扎实的：`_lock_order` + `populate_existing` 串行化同一订单；`_persist_push_request` 用独立会话先落 `sending`；`_resolve_existing_push` 区分“已同步/映射补主表/上次结果未知”；`_require_external_credential` 拒绝空 `external_id` 和“把本地 `so_id` 当外部单号”，**B05-P1 的“空 external_id 被判成功”在本块关闭**。没有发现“两个连接同时推同一单”的确定性重复建单。
- **推单报文金额全部转 float**：`build_order_payload` 的 `pay_amount`、`qty`、`price`、`amount` 都是 `float(...)`。外部 ERP 收到的不是 Decimal 精确值；大额/4 位单价在 JSON 序列化时可能变成二进制近似值，和 CRM 金额对不上（B06-04）。
- **报文只给总额和行金额，不给运费/其他费用/折扣**：`SalesOrder` 的 `total_amount` 含 `logistics_amount`、`other_charge_amount`、`discount_amount`（`order/model.py:77-80`），但 payload 只有 `pay_amount` 和 items；任何有运费/折扣的订单，行金额合计都不等于 `pay_amount`。ERP 若按行汇总或校验，会得到另一个总额（B06-05）。
- **外部单号唯一性只靠 `external_mappings` 唯一约束**：模型与迁移 `f7b8c9d0e1a2` 已有 `(system_type,business_type,internal_id)` 和 `(system_type,business_type,external_id)` 两条唯一约束，B04-15 可以关闭；但 `sales_orders.erp_order_id` 本身仍无唯一约束，`_load_order_by_external` 和 `resolve_mapping` 都 `.first()`，历史重复会把回调/采集挂到错误订单（B06-06）。
- **人工对账 `reconcile_push` 不校验外部单号是否已被别的订单占用**：它只校验本单主表/映射是否与本次输入不一致；若输入的是另一张订单已登记的外部单号，依靠 `external_mappings` 唯一约束会在 commit 时撞唯一键（500），而不是给出“该外部单号已属于订单 X”的可操作提示。数据库不会静默双绑，但错误呈现不友好（P3，不单列）。
- **`refresh_status` 被 `order:view` 触发写状态**：路由 `erp/router.py:351-361` 只要求 `order:view`，服务在拉取成功后调用 `order_service.change_status` 并写 `OrderStatusHistory`。B04-06 复核成立：只读权限用户能通过“刷新 ERP 状态”间接改订单状态。
- **webhook 的双编号同源、事件键、成功事件部分唯一索引、并发冲突回放**设计完整；未发现同一事件重复写历史。**但未知 raw_status 会被记成成功**：`new_status` 为空时 `changed=False`，最后仍 `log.status=SUCCESS`、`processing_status=processed`。`_find_processed_event` 只认 success，后续即使补齐 `STATUS_MAP` 再重放同一事件，也会被当成“已处理”直接返回（B06-09）。
- **异常队列不含 `failed`**：状态守卫拦下的回调写 `LOG_STATUS_FAILED`，但 `EXCEPTION_QUEUE_STATUS` 只有 conflict/unmatched/unknown/skipped/not_sent；这类“匹配上了但被业务规则拒绝”的事件不会进异常队列（B06-12，P3）。
- `sync_logs`/`exception_queue` 的订单范围过滤已补；有限范围用户看不到无归属的未匹配/冲突事件，这是有意的“看不到归属就不给”。`readiness` 的 `fault_reason` 仍是全局最近系统性失败，未按范围过滤（P3，不单列）。

### 3. `backend/app/modules/erp/schema.py`（106 行，已读全文）

**我检查了什么**：采集请求的页大小/页数上限、映射匹配目标、webhook 字段长度/必填、人工对账输入。

**结果如何**：`CollectRequest.page_size` 限 1-200、`max_pages` 限 1-1000；`MappingMatchRequest.internal_id` 必填，具体对象类型/归属由路由 `_ensure_mapping_target_scope` 校验；`StatusWebhookRequest` 的 `status` 没有 `min_length`，空字符串可进入 `apply_status_webhook`，最终被当作“未知状态→成功但不改状态”（与 B06-09 同源）。`ReconcilePushRequest.external_id` 有长度上限；未发现金额字段或 float 入参。

## 二、本块发现清单

### B06-01｜P1｜未匹配的订单映射无法人工匹配：父单校验查到的是它自己

- **位置**：`backend/app/modules/erp/collection.py:280-283`、`:908-910`；`backend/app/modules/erp/router.py:190-206`
- **触发条件**：采集到一张本地没有对应 `order_no`、也没有登记 `erp_order_id` 的外部订单；`resolve_mapping` 将它落成 `pending`。操作员在“待匹配”队列点“人工匹配”，选择本地订单。
- **后果**：`normalize_item` 把订单的 `order_key` 设为 `dedupe_key`，`_ensure_mapping` 无条件把 `mapping.parent_key` 写成该 `order_key`。路由 `_ensure_mapping_target_scope` 看到 `parent_key` 非空，就去查 `object_type=order、external_id=parent_key` 的父映射——查到的是这条映射自己；它的 `internal_id` 仍为空，于是 409“请先匹配该外部对象的父订单”。**订单永远匹配不上**，待匹配队列无法清空，外部订单事实对有限范围用户不可见，对账无法开始。
- **证据代码**：
  ```python
  # collection.py:280-283
  if object_type == OBJECT_ORDER:
      order_key = order_key or dedupe_key
  # collection.py:908-910
  mapping.parent_key = record.order_key or record.parent_key or mapping.parent_key
  ```
  ```python
  # router.py:192-204
  if mapping.parent_key:
      parent = ... where(object_type == OBJECT_ORDER,
                         external_id == mapping.parent_key)
      if parent is None or parent.internal_id is None:
          raise AppError(..., "请先匹配该外部对象的父订单", 409)
  ```
- **建议**：只对非主对象（明细/发货/售后）设置 `parent_key`；`object_type=="order"` 的映射 `parent_key=None`。或在 `_ensure_mapping_target_scope` 中显式跳过 `object_type==OBJECT_ORDER` 的父单校验。补一条“未知订单 → pending → 人工匹配成功”的回归。

### B06-02｜P1｜人工匹配一条映射会顺手全局重放，绕过“重放仅限全量范围”的限制

- **位置**：`backend/app/modules/erp/collection.py:1515-1521`；`backend/app/modules/erp/router.py:576-583`、`:609-614`
- **触发条件**：任何有 `order:manage` 的用户（包括数据范围有限的销售）调用 `POST /external-mappings/{id}/match`。`assign_mapping` 在写完这一条后，调用 `replay_pending_mappings`，只传 `system_type/shop_id`，不传 `object_type`，也不看操作人范围。
- **后果**：同一系统/店铺下的**所有**待匹配/冲突对象（订单、客户、SKU、发货、售后、明细）都会被重新自动匹配并写库；可能把别的销售负责的订单、全局客户/SKU 映射改掉。显式重放接口明确拒绝非全量范围用户，但人工匹配这条路径绕过了该限制；审计也只记了被点的那一条 mapping，被顺带改掉的其它行没有审计。
- **证据代码**：
  ```python
  # collection.py:1515-1521
  replayed = await replay_pending_mappings(
      session, system_type=mapping.system_type, shop_id=mapping.shop_id, now=moment,
  )
  ```
  ```python
  # router.py:609-614：显式重放要求全量范围
  if await scoped_owner_ids(session, user) is not None:
      raise AppError(ErrorCode.DATA_SCOPE_DENIED,
                     "映射重放会处理全局待匹配记录，只能由全量数据范围用户执行", 403)
  ```
- **建议**：`assign_mapping` 只重放与该映射相关的子行（同 `object_type` 的明细/同父单），或要求全量范围才允许全局重放；对每一条被自动改写的 mapping 写审计（至少记录 mapping_id、原/新 internal_id、basis、操作来源）。补一条“有限范围用户人工匹配不改变他人映射”的用例。

### B06-03｜P1｜一条外币记录会让整页采集永久失败，与“ERP 外部同步不拦”的口径直接冲突

- **位置**：`backend/app/modules/erp/collection.py:261-262`、`:971-1180`；对照 `backend/app/core/trade_mode.py:34-38`
- **触发条件**：ERP 返回的任意一条订单/明细带 `currency != "CNY"`（例如测试单、跨境单、对方把币种写成 `USD`）。`normalize_item` 直接 `reject_foreign_currency` 抛 422；`collect_once` 的页级 `try` 捕获后 `_fail_quietly` 回滚整页、把 cursor 置 `failed`、保留 `page_token`。
- **后果**：这一页的所有记录（包括同页的正常 CNY 单）都不落库；下一轮从同一 `page_token` 重试，仍然同一批外币记录、同样失败。采集会**永久卡在这一页**，不是“跳过一条外币继续”，也不是“统计页提示含外币”。`trade_mode.py` 明确写“外部同步（ERP）不拦：拒收会让整批同步失败，而外部数据不是我们说了算”；代码行为正好相反。
- **证据代码**：
  ```python
  # collection.py:261-262
  currency = (_text(item.get("currency")) or CNY).upper()
  reject_foreign_currency(currency, label=f"采集第 {position} 条")
  ```
  ```python
  # trade_mode.py:34-38
  - **外部同步（ERP）不拦**：拒收会让整批同步失败，而外部数据不是我们说了算。
    它靠统计页那句「含外币、未折算」的提醒兜底 —— 最坏情况是"看得见的不准"，
    而不是静默的错数。
  ```
- **建议**：按 `trade_mode` 的口径处理 ERP 外币——采集层不要抛 422；把该条落库并标记 `currency`/`needs_fx_review`，或跳过并在 `external_records`/异常队列留一条“外币未折算”记录。至少不能让一条外币阻断整页；若要按项目规则“非人民币明确拒绝”，也应逐条拒绝并继续下一页，而不是整批失败。

### B06-04｜P1｜推单报文把金额/数量/单价全部转 float

- **位置**：`backend/app/modules/erp/service.py:166-193`
- **触发条件**：任意订单推送 ERP。`pay_amount`、`qty`、`price`、`amount` 都经过 `float(...)` 再进 JSON。
- **后果**：CRM 内部是 `Decimal`（总额 2 位、单价 4 位、数量 3 位），出站时变成二进制浮点数。大额订单或 4 位单价在 JSON 里可能出现 `123456789012.3456` 的近似值（或与 CRM 差 1 分/1 个最小单位），ERP 按报文建单后金额与 CRM 不一致；后续按外部单金额对账会一直差这一点，且很难定位是“传输精度”而不是业务错。
- **证据代码**：
  ```python
  "pay_amount": float(order.total_amount),
  ...
  "qty": float(item.quantity),
  "price": float(item.unit_price),
  "amount": float(item.amount),
  ```
- **建议**：出站金额统一转**定长字符串**（总额 `f"{value:.2f}"`、单价 `f"{value:.4f}"`、数量 `f"{value:.3f}"`），或给 JSON encoder 注册 Decimal 序列化；不要在金额边界用 float。补一条“总额 999999999999.99 + 单价 0.0001”的报文快照用例。

### B06-05｜P1｜推单报文有总额和行金额，却没有运费/其他费用/折扣，行合计与总额不等

- **位置**：`backend/app/modules/erp/service.py:166-193`；对照 `backend/app/modules/order/model.py:72-80`
- **触发条件**：订单含非零 `logistics_amount`、`other_charge_amount` 或 `discount_amount`（这三种都很常见）。payload 只发 `pay_amount`（订单总额）和 items 的 `amount`（行金额），没有这三个字段。
- **后果**：ERP 收到的明细行合计 = 商品金额，不等于 `pay_amount`。如果 ERP 按行汇总、校验总额或把差额当异常，会拒单/拆错；如果 ERP 直接采用 `pay_amount`，则运费/折扣没有落到任何行，后续按行对账、退货按行冲减都会错。对账系统看到“CRM 总额 100、外部行合计 90”，无法判断是折扣还是漏行。
- **证据代码**：
  ```python
  # order/model.py:72-80：四个金额都已含在 total_amount 里
  goods_amount / logistics_amount / other_charge_amount / discount_amount
  ```
  ```python
  # service.py:172-188：payload 只有 pay_amount 和 items
  "pay_amount": float(order.total_amount),
  "items": [{"qty": ..., "price": ..., "amount": ...}],
  ```
- **建议**：在 payload 中补齐 `goods_amount/logistics_amount/other_charge_amount/discount_amount`（或至少补 `discount_amount`/`freight_amount`），并明确 ERP 口径“行金额合计 + 费用 - 折扣 = 总额”；如果 ERP 只接受行金额，则把费用/折扣按规则摊到行或单独一行，确保合计可复算。

### B06-06｜P2｜`sales_orders.erp_order_id` 无唯一约束，回读/匹配用 `.first()`，历史重复会把事实挂到错误订单

- **位置**：`backend/app/modules/order/model.py:85`；`backend/app/modules/erp/service.py:963-965`；`backend/app/modules/erp/collection.py:701-707`
- **触发条件**：历史数据/人工直写/旧 `record_external_order_id` 让两张订单的 `erp_order_id` 相同（该列没有唯一约束）。webhook 按外部单号定位、采集按 `erp_order_id` 匹配订单时都 `select(...).first()`。
- **后果**：同一外部单号可能命中两张 CRM 订单中的任意一张；回调状态、采集事实、对账归属可能写到非实际订单。`ExternalMapping` 的唯一约束只能保证映射表不双绑，不能保证主表 `erp_order_id` 不重复。
- **证据代码**：
  ```python
  # service.py:963-965
  stmt = select(SalesOrder).where(SalesOrder.erp_order_id == erp_order_id)
  order = (await session.execute(stmt)).scalars().first()
  ```
  ```python
  # collection.py:701-707
  row = (await session.execute(
      select(SalesOrder).where(SalesOrder.erp_order_id == external_id)
  )).scalars().first()
  if row is not None: return row.id, "erp_order_id", ...
  ```
- **建议**：给 `sales_orders.erp_order_id` 加部分唯一索引（`WHERE erp_order_id IS NOT NULL`，若只接一家 ERP）；或在回读/匹配时先 `count` 检查多条，命中多条直接 `conflict`/异常队列，不要 `.first()`。迁移前先查历史重复。

### B06-07｜P2｜采集缺币种时默认 CNY，外币缺失会被静默当人民币入账

- **位置**：`backend/app/modules/erp/collection.py:261-262`
- **触发条件**：适配器返回的记录没有 `currency` 字段（schema 里 currency 是可选）。代码用 `(_text(...) or CNY)` 补齐，然后 `reject_foreign_currency` 通过。
- **后果**：如果这条记录实际是 USD/EUR，系统会以 CNY 落库、以 CNY 参与对账；不会报错、不会进异常队列，也不会在统计页标“外币未折算”。这比直接拒绝更危险：错数被当成正常数。项目规则是“非人民币明确拒绝”，缺币种不应等同于 CNY。
- **证据代码**：
  ```python
  currency = (_text(item.get("currency")) or CNY).upper()
  reject_foreign_currency(currency, label=f"采集第 {position} 条")
  ```
- **建议**：把“缺币种”作为独立状态（`currency=None`/`unknown`）落库并标记待核实；或要求适配器必须给出币种，缺失即该条进异常队列。不要用默认值把未知变成 CNY。

### B06-08｜P2｜`replay_pending_mappings` 无行锁，可能覆盖并发的人工匹配

- **位置**：`backend/app/modules/erp/collection.py:1465-1492`；`get_mapping` 的锁在 `:1392-1406`
- **触发条件**：管理员 A 打开一条 pending mapping，人工指定本地对象；几乎同时管理员 B/定时任务跑 `POST /external-mappings/replay`（或另一条 `assign_mapping` 触发的重放）。重放查询在 A 提交前读到 pending，A 提交后重放仍按旧结果覆盖 `internal_id/match_status`。
- **后果**：人工确认的 `matched_by` 被自动结果覆盖，`match_basis` 从 `manual` 变回自动/`pending`；人工指定是“证据”，却被静默改掉。没有版本号/更新时间条件，也没有 `FOR UPDATE`。
- **证据代码**：
  ```python
  # replay：只 select，没有 with_for_update
  stmt = select(ExternalObjectMapping).where(
      ExternalObjectMapping.match_status.in_((MATCH_PENDING, MATCH_CONFLICT)))
  rows = (await session.execute(stmt...)).scalars().all()
  for row in rows:
      internal_id, basis, note, status = await resolve_mapping(session, row)
      row.internal_id = internal_id
      row.match_status = status
  ```
- **建议**：重放查询加 `with_for_update(skip_locked=True)`，并在写入前重查 `match_status` 仍为 pending/conflict 且 `matched_by is None`；或引入 `updated_at/version` 乐观锁。人工匹配事务提交后，重放必须看到 `matched` 并跳过。

### B06-09｜P2｜未知 raw_status 被记成“成功”，后续补齐映射也无法重放

- **位置**：`backend/app/modules/erp/service.py:1168-1170`、`:1214-1224`；`_find_processed_event` `:987-997`
- **触发条件**：ERP 回调的 `status` 不在 `adapter.STATUS_MAP` 中（对方新增状态、字段大小写/翻译不一致、适配器映射表不全）。`new_status=None`，不调用 `change_status`，但最终仍写 `log.status=SUCCESS`、`processing_status=processed`。
- **后果**：事件被标记为“已处理”，订单状态没变；之后即使部署补了 `STATUS_MAP`，同一事件重放时 `_find_processed_event` 命中 success，直接返回“此前已处理”，永远不会再改状态。异常队列也不会出现（状态是 success），只能靠人翻日志。
- **证据代码**：
  ```python
  new_status = adapter.STATUS_MAP.get(raw_status) ...
  if new_status and new_status in VALID_STATUS and new_status != order.status:
      ...
  log.status = LOG_STATUS_SUCCESS
  log.response_data = {"processing_status": EVENT_PROCESSED, "mapped": bool(new_status), ...}
  ```
- **建议**：`new_status is None` 时记 `LOG_STATUS_UNMATCHED`/`failed` 或 `processing_status="unmapped"`，保留事件可重放；异常队列展示 raw_status 与“映射缺失”。补一条“未知状态 → 补映射 → 重放成功”的用例。

### B06-10｜P2｜`collection_status` 对 `order:view` 返回全公司采集水位、来源证据和计数

- **位置**：`backend/app/modules/erp/collection.py:1525-1575`；`backend/app/modules/erp/router.py:409-420`
- **触发条件**：任意只有 `order:view` 的用户调用 `GET /integrations/erp/collection/status`。服务没有接收 `owner_ids`，直接 `count(*)` 全表、列出全部 `ExternalSourceRegistry` 和 `list_cursors`。
- **后果**：有限范围用户能看到其它店铺/其它负责人范围的采集记录数、待匹配数、水位/断点、来源核实证据和授权备注。这不是订单金额，但属于集成运营数据，和 `sync_logs` 已按订单范围过滤的口径不一致。
- **证据代码**：
  ```python
  rows = select(ExternalSourceRegistry).all()
  pending_mappings = count(ExternalObjectMapping)  # 全表
  records = count(ExternalRecord)                  # 全表
  ```
  ```python
  @router.get("/integrations/erp/collection/status")
  user = Depends(require_permission("order:view"))
  return ok(await collect_svc.collection_status(session))
  ```
- **建议**：按 `owner_ids` 过滤计数和来源/水位，或把该接口提升到 `settings:manage`/集成管理员；至少把“全公司统计”只给全量范围。

### B06-11｜P3｜`serialize_record` 把数量/金额转 float 出参

- **位置**：`backend/app/modules/erp/collection.py:1236-1250`
- **触发条件**：查看外部记录列表/详情。
- **后果**：`Decimal(…,4)` 的金额/数量经 `float()` 后可能显示成 `123.45670000000001` 或丢失末位；原始事实本身在库里是精确的，但页面/导出/下游拿到的展示值不精确。与 B06-04 同源，只是展示层。
- **证据代码**：
  ```python
  "quantity": None if row.quantity is None else float(row.quantity),
  "amount": None if row.amount is None else float(row.amount),
  ```
- **建议**：同时返回 `amount_exact`/`quantity_exact` 字符串（4 位/3 位），前端需要数值时再用；不要只给 float。

### B06-12｜P3｜异常队列不含 `failed`，被状态守卫拦下的回调不会进队列

- **位置**：`backend/app/modules/erp/service.py:94-100`、`:1186-1210`、`:1327-1365`
- **触发条件**：ERP 回调匹配到订单，但 `change_status` 被订单状态守卫拦下（例如已取消订单收到“已发货”、未发完收到“已完成”）；服务写 `LOG_STATUS_FAILED`。
- **后果**：这条事件在 `sync_logs` 里按 `status=failed` 能查到，但 `exception_queue` 的默认状态集合没有 `failed`，运维在“异常队列”看不到它；而它恰恰是需要人工判断“是 ERP 错了还是 CRM 状态错了”的事件。
- **证据代码**：
  ```python
  EXCEPTION_QUEUE_STATUS = (CONFLICT, UNMATCHED, UNKNOWN, SKIPPED, NOT_SENT)
  # 没有 FAILED
  ```
- **建议**：把 `failed`（至少“匹配到订单但被守卫拦下”的子类）纳入异常队列，或在 `response_data` 加 `needs_review=true` 并让队列按它筛。

### B06-13｜P3｜推单报文 `shop_id` 固定为 None，多店铺时外部无法区分

- **位置**：`backend/app/modules/erp/service.py:170`
- **触发条件**：同一 ERP 账号下有多店铺，订单需要按店铺建单。payload 永远 `shop_id=None`。
- **后果**：ERP 无法判断订单属于哪个店铺，可能落到默认店铺/拒绝；后续按店铺对账时外部记录与 CRM 店铺对不上。当前如果只接单店铺，是预留字段；但代码没有从订单/配置取 shop_id。
- **建议**：订单增加店铺归属或从配置取默认店铺，明确 `shop_id` 口径；如果确实单店铺，在注释/校验里写死并在 readiness 标“单店铺”。

### B06-14｜P3｜`list_mappings` 对有限范围用户隐藏所有待匹配订单（`internal_id=NULL`）

- **位置**：`backend/app/modules/erp/collection.py:1421-1428`
- **触发条件**：有限范围用户打开“待匹配”队列。过滤条件是 `object_type=="order"` 且 `internal_id.in_(scoped_orders)`；待匹配订单的 `internal_id` 还是 `NULL`，不在集合里。
- **后果**：销售看不到自己范围内的未匹配订单，无法发起人工匹配；只有全量范围管理员能看到并处理。如果业务希望销售自己补客户/订单映射，这个队列对他们是空的。安全上“宁可不给”可以接受，但和“人工匹配出口”的设计意图有落差。
- **建议**：对待匹配订单按 `external_code`/订单号尝试匹配本范围订单，或明确该队列只对全量范围开放并在前端提示；不要把 `internal_id=NULL` 的行直接过滤掉而不解释。

## 三、待确认（不作为结论）

- **B06-P1（B05-05 的服务侧确认）**：`push_order` 捕获 `ErpError` 后按 `error.kind` 落 `failed/unknown`；适配器对“200 缺 code/非整数 code”抛的是 `ErpError(kind="peer_error")`，服务会写 `failed` 并允许下一次重推。B05-05 的“明确失败误判”在服务侧成立，仍按 P1；真正是否重复建单取决于对方是否按 `idempotency_key` 去重（尚未验收）。
- **B06-P2（B04-15 关闭）**：`ExternalMapping` 的模型和迁移 `f7b8c9d0e1a2` 都有 `(system_type,business_type,internal_id)` 与 `(system_type,business_type,external_id)` 唯一约束，B04-15 的“重复推送可多行映射”已不成立，建议关闭；剩余风险是 `sales_orders.erp_order_id` 主表无唯一（B06-06）。
- **B06-P3（B03-18）**：`sales_orders.erp_order_id` 仍无唯一索引；正常推单路径靠 `ExternalMapping` 唯一约束兜底，历史/直写仍可重复。B03-18 维持 P3，状态“B06 复核：约束仍缺”。

## 四、本块结论

- **P0：0 条**。没有发现“无人工介入就自动重复入账/丢钱”的路径；推单同一订单有行锁 + 独立发送日志，采集有唯一键 + 租约。
- **P1：5 条**（B06-01 至 B06-05）。核心是“未匹配订单无法人工匹配”“人工匹配绕过全局重放范围”“外币一条拖死整页”“推单金额 float”“报文行合计与总额不等”。
- **P2：5 条**（B06-06 至 B06-10）。集中在 `erp_order_id` 重复/`.first()`、缺币种默认 CNY、重放无锁覆盖人工、未知状态记成功、采集总览越范围。
- **P3：4 条**（B06-11 至 B06-14）。
- **待确认：3 条**（B06-P1 至 B06-P3）。
- **未读完：无**。3 个文件逐行读完；相关模型、迁移、路由入口作为证据已核对。
