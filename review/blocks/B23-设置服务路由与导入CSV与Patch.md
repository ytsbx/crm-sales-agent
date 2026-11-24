# B23 设置服务/路由 + 导入/CSV/patch

> 审查范围：`backend/app/modules/settings/service.py` 2,204 行、`settings/router.py` 1,011 行、`backend/app/core/importing.py` 703 行、`core/csvio.py` 181 行、`core/patch_schema.py` 828 行，合计 4,927 行。
>
> 审查方式：只读业务代码，只写 `review/`；未运行测试，未连接生产库，未改任何业务代码。本块是设置配置、自动任务/公海回收编排和通用导入骨架，不直接写收款/订单金额，但配置错误会影响报价审批分档和公海通知。

## 一、本块结论

| 严重度 | 数量 | 编号 |
|---|---|---|
| P0 | 0 | — |
| P1 | 1 | B23-01 |
| P2 | 2 | B23-02、B23-03 |
| P3 | 6 | B23-04 至 B23-09 |
| 待确认 | 2 | B23-P1、B23-P2 |

一句话：**没有发现直接改钱的代码错误；最重的是审批分级配置的角色码不校验——空角色列表会让该档的角色限制失效，任何有 `quote:approve` 的人都能批这一档。其次是公海回收通知只落库不投递、导入提交失败把数据库异常原文回显给前端。**

## 二、逐文件检查记录

### `backend/app/modules/settings/service.py`（2,204 行，已读完）

我检查了什么：配置默认值与数值校验（`_strict_int`/`_strict_amount`/`validate_setting_value`）、报价审批分级 `approval_levels`、公海规则冲突、回收预告扫描、候选复核/执行/恢复、自动任务规则校验与执行、`get_number` 取值、`notify_due_followups`、`confirmed_not_paid_count`。

结果：

- `approval_levels` 校验了“至少一档”、`max_amount` 不能空在中间档、金额递增、最多两位小数；但**没有校验 `role_codes` 里的角色码是否存在、是否为空列表，也没有金额上限**。`approval/access.py:38-40` 用 `if allowed_roles and ...` 做角色闸门，空列表等于跳过角色限制；未知角色码则除管理员外没人能批。`quote/service.py:2768` 又用 `float(max_amount)` 比金额，超大值可变成 `inf`，所有报价落第一档。见 B23-01。
- 公海回收：`run_public_pool_recycle` 内部 `commit`，`_notify_recycle_candidate` 只创建 `Notification`（`wecom_status='pending'`），没有投递；`settings/router.py:337` 直接返回，`core/scheduler.py:33-51` 的定时任务也没有 `dispatch_pending`。手动触发后通知会停在 pending，只有等下一次 `run_auto_tasks_job` 的“通知投递”步骤才可能补发。见 B23-02。
- `run_auto_tasks` 先 `lock_task_scan`，再批量把 `customer.next_followup_at` 改成扫描时查到的 `min(due_at)`，这一步**没有按客户加行锁**；`customer/service.py:48-89` 的正式重算则在 `lock_customer_row` 里做。并发下扫描可能把用户刚约定好的未来跟进时间覆盖成旧值/空值。见 B23-06。
- `protection_detail` 支持 `customer_id` 定向查询（`663-672`），但 `decide_candidate`（`1617`）和 `assert_no_protection`（`1810`）都不传，单客户复核/释放会扫全库有效报价、在途订单、未结应收、在途打样。见 B23-07。
- `_protected_customer_ids`（`796-802`）没有调用点，属于死代码；`assert_no_protection` 仍走全量 `protection_detail`。归入 B23-07 的整洁度/性能说明。
- `get_number` 仍返回 `float`（`607-619`），但本块没有新增金额计算；`approval_levels.max_amount` 的 `float` 比较与 B01-01 同源，不另编号。
- `run_auto_tasks` 创建任务后不 `notify`，路由也不 dispatch（`router.py:623-630`）；这是否设计要求未能从代码/测试确认，列为本块“未看/待确认”的额外事项，不单独编号，也不当 bug。

### `backend/app/modules/settings/router.py`（1,011 行，已读完）

我检查了什么：配置保存、公海规则 CRUD、候选列表/单条/批量复核、自动任务规则 CRUD/立即执行、编号规则 CRUD、字典/客户等级 CRUD、未分级规则、公共配置。

结果：

- `upsert_setting` 只对 `hard_floor/export/dedup_scoring/notification_retry` 四个 merge 字段 `with_for_update`（`173-177`）；`approval_levels` 不在其中，两个管理员并发保存会后写覆盖先写，且没有冲突提示。见 B23-05。
- 编号规则创建先查重再 `flush`（`677-690`），没有锁；并发双击可同时通过预检，最后靠 `numbering_rules.code` 唯一索引兜底，后一个变 500 而不是 409。`update_numbering_rule`（`718-747`）也没有行锁，两个 PATCH 可丢更新。见 B23-05。
- 自动任务规则创建调用了 `lock_task_scan`，但 `TaskRule.code` 的唯一性没有预检；`reject_duplicate_task_rule` 比的是标题/触发类型，不是 code，重复 code 会撞唯一索引 500。见 B23-05。
- 字典/客户等级创建先查重再插入（`806-819`、`890-909`），没有锁；`(type, code)` 唯一索引在并发下同样会 500。见 B23-05。
- `SettingInput`、`DictionaryItemCreate`、`CustomerLevelCreate` 是裸 `BaseModel`，路由直接 `model_dump()` 入库（`188-218`、`817`、`900`），没有长度校验；超长 `key/description/type/code/label/name/remark` 会在 DB 层 500。B22-P1 已由此确认，归 B23-04。
- `batch_decide_recycle_candidates` 的 `candidate_ids` 没有条数上限（`377-386`、`443-448`），逐条 `decide_candidate` 内部还会 commit；大列表会长时间占请求和行锁。见 B23-08。
- `run_recycle`（`325-342`）只调用 service 并返回，没有投递通知；见 B23-02。
- `create_numbering_rule`/`update_numbering_rule` 已校验 `seq_length` 1–12、`reset_period` 枚举；`date_format` 的未知指令在 Python `strftime` 只输出字面字符，不会 500。B22-11 的“`seq_length` 无下界/`date_format` 会 500”两条据此修正，剩余问题是 `prefix` 里的 `%/_` 参与 `LIKE`，见 B23 末尾“对 B22-11 的修正”。

### `backend/app/core/importing.py`（703 行，已读完）

我检查了什么：通用导入的行迭代、`ImportReport` 去重/失败明细、`row_savepoint` 嵌套事务、`make_preview_token/read_preview_token`、`diff_preview`、`finalize` 的提交/回滚/审计、模块注册。

结果：

- 逐行 `savepoint` 失败后 `session.expunge` 新对象，异常转成失败行继续；最终 `finalize` 在一个事务里 `commit`，失败时 `rollback`，不会返回成功统计。事务边界基本正确。
- `finalize` 的 `commit` 失败把 `str(exc)[:120]` 拼进 500 文案（`684-692`）；数据库约束名、表名、SQL 片段可能直接回显。见 B23-03。
- `read_preview_token` 对 `zlib.decompress(compressed)` 没有解压后大小上限，token 由已登录用户提交；构造超大压缩 token 可造成内存/CPU 消耗。见 B23-09。
- `row_savepoint` 嵌套回滚后，已修改的既有对象是否被 SQLAlchemy 恢复成数据库状态，代码只 `expunge` 了 `session.new`；这一点没有实测，列 B23-P2 待确认，不当结论。
- 正式导入允许 `preview_token=None` 直接提交（`finalize` 的 `preview_token` 参数可选），`test_seventh_round_imports.py` 明确用 `preview=False, preview_token=None`；这是既有设计路径，不报 bug。

### `backend/app/core/csvio.py`（181 行，已读完）

我检查了什么：CSV 编码识别、BOM、表头规范化/重复检测、公式注入防护、CSV 序列化。

结果：`_FORMULA_PREFIXES` 覆盖 `= + - @ Tab CR`；`_safe_csv_text` 只在非空字符串前加 `'`，不误伤数字/日期/空值；`parse_csv_bytes` 检测重复表头并给出可读错误；`csv_bytes` 用 UTF-8 BOM，中文 Excel 打开正常。本文件未发现金额/权限/事务问题，也没有新增发现。

### `backend/app/core/patch_schema.py`（828 行，已读完）

我检查了什么：`CreateModel`/`PatchModel` 的空白/长度校验、PATCH 字段解析、未知字段/嵌套字段、表模型缓存。

结果：`PatchModel` 对“显式提交的空字符串/空白字符串”按列宽和 `nullable` 判断，`append_detail` 给出字段路径；`CreateModel` 只做长度兜底，不做非空/枚举，这是设计的一半。**设置模块的三个裸 `BaseModel` 没有继承 `CreateModel`，所以 `CreateModel` 的长度兜底对它们不生效**，这不是 `patch_schema` 的 bug，而是 B23-04 的调用方问题。`verify_patch_registry` 只校验 PatchModel，不校验 CreateModel，属可接受的边界。

## 三、发现清单

### B23-01 P1 审批分级只校验金额格式，不校验角色码是否存在/非空，也不限制金额上限

**位置**
- `backend/app/modules/settings/service.py:467-508`（`approval_levels` 校验）
- `backend/app/modules/settings/service.py:483-490`（`role_codes` 只校验“如果是 list”）
- `backend/app/modules/settings/service.py:491-508`（`max_amount` 只校验格式和递增，无上限）
- `backend/app/modules/approval/access.py:36-40`（空 `allowed_roles` 跳过角色限制）
- `backend/app/modules/quote/service.py:2760-2775`（按档位取 `node_role_codes`，`float(max_amount)`）

**触发条件**：管理员保存 `approval_levels` 时，某一档的 `role_codes` 为空列表、缺字段，或填了不存在的角色码；或把 `max_amount` 填成极大数字（如 100 位整数，尤其第一档）。

**后果**
- `role_codes` 为空/缺字段：`summary["node_role_codes"]` 为空，`approval/access.py:39` 的 `if allowed_roles and ...` 直接跳过角色校验；任何同时有 `quote:approve` 和价格审批权限的人都能批这一档，原本“总监/财务”档的角色限制失效。
- `role_codes` 填了不存在的角色码：除管理员外没人能批，报价卡在该档，流程走不下去。
- `max_amount` 极大：`quote/service.py:2768` 的 `float(max_amount)` 可变成 `inf`，`amount <= inf` 对所有金额成立，所有报价都落进第一档，后面的高档永远不会被使用。

**证据代码**
```python
role_codes = item.get("role_codes")
if role_codes is not None and not isinstance(role_codes, list):
    ...  # 只挡“不是 list”，不挡空 list / 未知角色码

raw = item.get("max_amount")
...
_strict_amount(raw, f"第 {index} 档的金额上限")  # 只校验非负/两位小数，无上限
cap = Decimal(str(raw).strip())
```
```python
allowed_roles = summary.get("node_role_codes") or []
if allowed_roles and not admin and not (set(user.roles) & set(allowed_roles)):
    return f"该审批需要「{summary.get('node_label', '上级')}」处理，你的角色无权批准"
```
```python
max_amount = level.get("max_amount")
if max_amount is None or amount <= float(max_amount):
```

**建议**：保存 `approval_levels` 时校验 `role_codes` 是非空 list、每个 code 都存在于角色表（是否允许 `admin` 单独决定）；`max_amount` 用 `Decimal` 校验，并设一个合理上限（如 `1e12`），拒绝 `inf`/超长数字；对 `node/label` 也做唯一性和非空校验。修复前，前端不能把空角色列表当成“不限角色”。

**测试覆盖**：未覆盖。`backend/tests/test_config_rule_conflicts.py:32-37` 只测 `max_amount` 递增，没有空 `role_codes`、未知角色码、超大 `max_amount` 的用例；`test_settings_number_bounds.py:166-167` 只测 `approval_levels` 的 `levels` 为空。

### B23-02 P2 手动公海回收/定时回收只创建 pending 通知，不投递

**位置**
- `backend/app/modules/settings/service.py:1175-1179`（`_notify_recycle_candidate` 只 `notify`）
- `backend/app/modules/settings/router.py:325-342`（`run_recycle` 直接返回，没有 `dispatch_pending`）
- `backend/app/core/scheduler.py:33-51`（`run_public_pool_recycle_job` 也没有投递）
- `backend/app/modules/notification/service.py:483-530`（`dispatch_pending` 是实际投递入口）

**触发条件**：管理员点“立即执行公海回收扫描”，或定时任务跑完；本轮有候选命中并生成预告通知。

**后果**：`_notify_recycle_candidate` 只写 `Notification` 行，`wecom_status` 为 `pending`；`run_recycle` 和定时 job 都没有调用 `dispatch_pending`。手动触发时，如果当天 `run_auto_tasks_job` 已经跑过、或调度器未启用，原负责人/复核主管收不到企微/即时通知，只看到“提名 N 个”的响应。定时场景最多等下一次 `run_auto_tasks_job` 的“通知投递”步骤（`scheduler.py:95`），不是立即送达。

**证据代码**
```python
notified += await _notify_recycle_candidate(
    session, candidate=candidate, customer=customer, notice_days=notice_days
)
```
```python
result = await svc.run_public_pool_recycle(session, user.id)
return ok(result, ...)  # 没有 notification_service.dispatch_pending
```

**建议**：service 内部 `commit` 之后，由路由和定时 job 显式调用 `notification_service.dispatch_pending(session)`（用独立会话），与批量交接（B22-06）统一“先 commit 再投递”的模式；至少在响应里说明“通知待投递”。

**测试覆盖**：未覆盖。`test_pool_protection_filter` 只断言候选/保护结果，不断言通知投递；`test_daily_task_isolation` 里 monkeypatch 了 `dispatch_pending`，但与本路径无关。

### B23-03 P2 导入提交失败把数据库异常原文回显给前端

**位置**：`backend/app/core/importing.py:684-692`

**触发条件**：正式导入 `commit` 失败（唯一约束、死锁、连接中断、磁盘/权限等）。

**后果**：500 文案包含 `str(exc)[:120]`，可能泄露表名、约束名、SQL 片段、数据库版本等内部信息；普通用户看到无法理解的英文错误，也无法据此重试。审计已经写入同一事务，回滚后审计也丢，问题只能靠日志，但日志没有在这里补。

**证据代码**
```python
try:
    await session.commit()
except Exception as exc:
    await session.rollback()
    raise AppError(
        ErrorCode.SYSTEM_ERROR,
        f"导入提交失败，本次没有任何数据写入（{str(exc)[:120]}），请重试或联系管理员",
        500,
    ) from exc
```

**建议**：对外只返回“导入提交失败，本次未写入，请重试或联系管理员”，附一个可检索的错误编号；完整异常 `logger.exception` 落服务端日志（注意日志里不要打客户敏感字段/整行数据）。

**测试覆盖**：未覆盖。`test_seventh_round_imports.py` 覆盖预览 token、文件变化、baseline 变化、行失败，但没有注入 `commit` 失败的用例。

### B23-04 P3 设置/字典/客户等级入参无长度校验，超长直接 DB 500

**位置**
- `backend/app/modules/settings/schema.py:6-9`（`SettingInput.key/description` 无 max）
- `backend/app/modules/settings/schema.py:95-112`（`DictionaryItemCreate.type/code/label/remark` 无 max）
- `backend/app/modules/settings/schema.py:124-137`（`CustomerLevelCreate.code/name/remark` 无 max）
- `backend/app/modules/settings/router.py:212-218、799-819、883-909`（直接 `model_dump()` / 构造入库）

**触发条件**：管理员提交超过列宽的 `key/description/type/code/label/name/remark`。对应列宽：`system_settings.key` 64、`description` 255；`dictionary_items.type` 32、`code` 64、`label` 128、`remark` 255。

**后果**：Pydantic 放行，`flush` 时数据库报 `StringDataRightTruncation`/类似错误，接口 500，不是可读的 422。B22-P1 已确认；本块补充路由证据。

**证据代码**
```python
class SettingInput(BaseModel):
    key: str
    value: dict | None = None
    description: str | None = None
```
```python
row = SystemSetting(key=payload.key, description=payload.description)
...
row = DictionaryItem(**payload.model_dump())
...
row = DictionaryItem(type=CUSTOMER_LEVEL_TYPE, code=payload.code, label=payload.name, ...)
```

**建议**：让这三个模型继承 `CreateModel` 或显式 `Field(max_length=...)`；`CustomerLevelCreate` 的 `name` 映射到 `label`，长度按 `String(128)`；空 `code/type` 也应拒绝，不能只靠 `String` 非空。

**测试覆盖**：未覆盖。`test_settings_number_bounds.py` 只测数值，不测字符串长度；`test_rule_patch.py` 只测 PATCH。

### B23-05 P3 配置写入口并发：唯一键冲突 500、审批分级可丢更新

**位置**
- `backend/app/modules/settings/router.py:173-177`（`approval_levels` 不 `with_for_update`）
- `backend/app/modules/settings/router.py:677-690`（编号规则先查后插，无锁）
- `backend/app/modules/settings/router.py:718-747`（编号规则更新无行锁）
- `backend/app/modules/settings/router.py:529-538`（自动任务规则无 `code` 预检）
- `backend/app/modules/settings/router.py:806-819、890-909`（字典/等级先查后插，无锁）

**触发条件**：两个管理员同时保存同一 `approval_levels`，或同时双击创建相同 `code` 的编号规则/自动任务规则/字典项/客户等级；两个 PATCH 同时改同一条编号规则。

**后果**：`approval_levels` 后写覆盖先写，没有任何冲突提示；创建相同唯一键时一个请求撞 `IntegrityError` 变 500（不是 409）；编号规则更新可丢更新。`create_task_rule` 虽然拿了 `lock_task_scan`，但只防自动任务扫描，不防 `code` 重复，唯一索引仍会 500。

**证据代码**
```python
merge_fields = payload.key in (...)
stmt = select(SystemSetting).where(SystemSetting.key == payload.key)
if merge_fields:
    stmt = stmt.with_for_update()
```
```python
existing = ... where(NumberingRule.code == payload.code)
if existing is not None: raise 409
row = NumberingRule(**payload.model_dump())
session.add(row); await session.flush()  # 并发时唯一索引 500
```
```python
await svc.lock_task_scan(session)
row = TaskRule(**data)
svc.validate_task_rule(row)
await svc.reject_duplicate_task_rule(session, row)  # 比标题，不比 code
```

**建议**：`upsert_setting` 对所有 key 统一 `with_for_update`；创建/更新唯一键时加行锁或捕获 `IntegrityError` 转 409；编号规则更新 `SELECT ... FOR UPDATE`；给创建接口加 `request_key`/幂等键，避免双击。

**测试覆盖**：未覆盖。`test_config_rule_conflicts.py` 是顺序调用，不测并发；`test_rule_patch.py` 不测唯一键冲突。

### B23-06 P3 自动任务扫描写 `customer.next_followup_at` 不加客户行锁，可能覆盖用户刚约定的未来跟进

**位置**
- `backend/app/modules/settings/service.py:2013-2021`（批量把 `next_followup_at` 改成扫描时查到的 `next_due`）
- `backend/app/modules/customer/service.py:48-89`（正式重算在 `lock_customer_row` 内做）

**触发条件**：`run_auto_tasks` 扫描客户时，另一个请求同时给该客户创建/完成/改期跟进任务，触发 `refresh_next_followup_at`。

**后果**：扫描先读到旧的 `min(due_at)`，随后用户的新任务把 `next_followup_at` 写成未来时间；扫描的 `UPDATE` 在用户提交后落库，把未来时间覆盖成旧值或 `NULL`。该客户随后会被判“未约定跟进”，可能收到冷落提醒；如果扫描是在公海回收前，也可能影响“约定跟进”的展示口径（注：公海回收本身不依赖这个字段，见 `service.py:2030-2034`）。

**证据代码**
```python
next_due = dict((await session.execute(...)).all()) if rows else {}
for customer in rows:
    customer.next_followup_at = next_due.get(customer.id)
```
```python
await lock_customer_row(session, customer_id)
due = (...).scalar_one_or_none()
await session.execute(update(Customer).where(...).values(next_followup_at=due))
```

**建议**：不要在扫描里直接写 `next_followup_at`；需要校准时逐客户调用 `refresh_next_followup_at`（它自带客户锁），或在批量更新前统一 `SELECT ... FOR UPDATE` 并保持“客户→任务”的锁序。至少对生成任务的客户，扫描后统一重算已经做了（`2107-2109`），但被跳过的客户仍会留下旧值。

**测试覆盖**：未覆盖。没有并发/锁序用例。

### B23-07 P3 单客户复核/释放调用 `protection_detail` 不传 `customer_id`，每次扫全库

**位置**
- `backend/app/modules/settings/service.py:663-672`（`protection_detail` 支持 `customer_id`）
- `backend/app/modules/settings/service.py:1617`（`decide_candidate` 不传）
- `backend/app/modules/settings/service.py:1810`（`assert_no_protection` 不传）
- `backend/app/modules/settings/service.py:796-802`（`_protected_customer_ids` 无调用点）

**触发条件**：主管批准/提前回收一条候选，或把客户人工释放进公海。

**后果**：每次只处理一个客户，却执行 `protection_detail(session)` 的全量查询：所有有效报价、在途订单、未结应收、在途打样。客户/单据量大时单次操作明显变慢；`decide_candidate` 还持有候选/客户行锁，期间做全表扫描会拉长锁持有时间。功能结果不变，但性能和并发体验差。

**证据代码**
```python
reasons = (await protection_detail(session)).get(customer.id, [])
...
reasons = (await protection_detail(session)).get(customer_id, [])
```
`protection_detail` 内部已经有 `Quote.customer_id == customer_id if customer_id is not None else True` 的条件，调用方却传 `None`。

**建议**：两处都传 `customer_id=...`；`_protected_customer_ids` 要么删掉，要么改成调用定向查询，避免死代码继续误导。

**测试覆盖**：未覆盖。测试只断言保护结果，不测查询范围/性能。

### B23-08 P3 批量复核 `candidate_ids` 无条数上限，逐条 commit 拉长请求

**位置**：`backend/app/modules/settings/router.py:377-386`（`candidate_ids: list[int]` 无 `max_length`）、`443-448`（循环 + 逐条 `decide_candidate` 内部 commit）

**触发条件**：批量复核提交超大 `candidate_ids` 列表（例如几千/几万条）。

**后果**：请求长时间占用连接，逐条 `decide_candidate` 各自 commit，期间持有候选/客户行锁；前端超时后用户重试，已成功的条目会重复处理（`decide_candidate` 对非 pending 状态报错，但会产生大量失败明细）。

**证据代码**
```python
class BatchCandidateDecision(CandidateDecision):
    candidate_ids: list[int]

for cid in dict.fromkeys(candidate_ids):
    ...
    result = await svc.decide_candidate(...)
```

**建议**：`candidate_ids` 加 `Field(max_length=200)`（或按页面上限），超过 422；批量接口改为单事务或分批，明确“部分成功”语义。

**测试覆盖**：未覆盖。没有大批量/重试用例。

### B23-09 P3 预览 token 解压无上限，可被构造为内存/CPU 消耗

**位置**：`backend/app/core/importing.py:466-476`

**触发条件**：已登录用户提交一个超大、高压缩比的 `preview_token`（base64 + zlib）。

**后果**：`zlib.decompress(compressed)` 在解码前没有限制解压后字节数；构造的 token 可以在内存里膨胀到很大，造成请求内存/CPU 消耗，甚至影响同进程其他请求。端点有登录/权限，但仍是 DoS 面。

**证据代码**
```python
compressed = base64.urlsafe_b64decode(encoded.encode())
...
data = json.loads(zlib.decompress(compressed).decode("utf-8"))
```

**建议**：先限制 `compressed` 长度（例如 4 KB），再用 `zlib.decompressobj` 设置 `max_length`（例如 1 MB），超过直接 422；或把预览快照改存服务端短 ID，不把大 JSON 放 token。

**测试覆盖**：未覆盖。`test_preview_token_*` 只测篡改/模块不匹配，不测大小。

### B23-P1 待确认：定时公海回收的“防重入锁”注释与实现不一致

**位置**：`backend/app/core/scheduler.py:33-51`（注释说靠 `lock_task_scan`，函数内没有调用）；`backend/app/modules/settings/service.py:973-1246`（service 也没有 `lock_task_scan`）。

**触发条件**：定时 job 与管理员手动 `run-recycle` 同时执行，或两个调度实例同时跑。

**后果**：同一轮可能重复扫描；代码已有 `Customer ... with_for_update(skip_locked=True)`（`service.py:1054`）和 `uq_pool_candidate_open` 唯一索引兜底，候选本身不会重复，但通知是否重复、扫描资源是否重复消耗，没有实测证据。**暂列待确认，不当结论。**

**建议**：要么在 `run_public_pool_recycle` 入口加 `lock_task_scan`，要么删掉注释并说明“靠客户行锁+唯一索引防重”；补并发用例后再定严重度。

**测试覆盖**：未覆盖。没有双扫描并发用例。

### B23-P2 待确认：`row_savepoint` 嵌套回滚只 `expunge` 新对象，已修改的既有对象是否恢复未实测

**位置**：`backend/app/core/importing.py:92-100`

**触发条件**：导入行在 `row_savepoint` 内先修改一个已存在的 ORM 对象，后续步骤抛异常，嵌套 `SAVEPOINT` 回滚。

**后果**：事务回滚会撤销数据库写入，但 SQLAlchemy Session 里已修改对象的属性可能仍处于“脏”状态；代码只 `expunge` 了 `session.new` 的新对象，没有 `expire_all`/恢复既有对象。若该对象在后续行/最终 `commit` 中被重新 flush，可能把失败行的修改写进去，或污染后续行判断。**没有实测证据，暂列待确认。**

**证据代码**
```python
except Exception as exc:
    failed_n += 1
    for obj in list(session.new):
        session.expunge(obj)
    ...
```

**建议**：补隔离库用例：行内修改既有对象后抛异常，确认后续行/最终 commit 不会带上该修改；若不能保证，改用 `session.expire_all()` 或把整行改为独立 Session。**测试覆盖**：`test_seventh_round_imports.py:238` 只测“报错行里 add 过但没 flush 的新对象”，没有测已修改既有对象。

## 四、补充维度核对

- **金额 Decimal**：本块 5 个文件没有新的金额计算；`approval_levels` 的 `max_amount` 仍是“配置存 JSON、报价侧 `float` 比较”，与 B01-01 同源。`settings/service.get_number` 仍返回 `float`，但下游定价用 `str()` 转 Decimal，本块不另编号。
- **负数/0/超大值/小数位**：`_strict_amount` 已挡负数、`1e5`、超过两位小数、bool；但**没有上限**，B23-01 已记。`_strict_int` 对天数/比率有 `low/high`，本块没发现新的越界入口。
- **事务边界**：`importing.finalize` 是单事务，失败回滚且不返回成功统计，基本正确；`row_savepoint` 的嵌套回滚对象状态列 B23-P2 待确认。`settings` 的 `run_public_pool_recycle`/`run_auto_tasks` 自己 commit，调用方无法再回滚，属于既有设计，未发现半成品写入。
- **异常是否吞掉/泄露**：`importing.finalize` 的 commit 异常回显底层 `str(exc)`（B23-03）；`row_savepoint` 的逐行异常转成失败明细，属于预期；`run_auto_tasks` 对配置错误 422 跳过并写 `rule_errors`，不是静默吞掉。
- **审计**：`upsert_setting` 有 before/after；编号规则/字典/等级创建有 after；`run_public_pool_recycle` 和 `run_auto_tasks` 都有审计。本块未发现“收款/改价/作废”这类钱操作；关键配置保存的审计字段基本完整，但 `approval_levels` 的 role_codes 配置错误不会在审计里提示“角色不存在”，因为根本没校验。
- **测试覆盖**：本块 9 条确认发现中，B23-01/02/03/04/05/06/07/08/09 均**未覆盖**；B23-P1/P2 也未覆盖。已读用例：`test_config_rule_conflicts.py`（只测金额递增）、`test_settings_number_bounds.py`（只测数值边界）、`test_numbering_length.py`（合法长度/周期）、`test_seventh_round_imports.py`（预览/行失败/直接导入）、`test_rule_patch.py`（PATCH 只提交改动字段）。未运行测试。

## 五、本块没有看的部分及原因

- **未运行测试**：项目要求测试只能在隔离库跑；本块没有执行 `backend/tests`，所有“覆盖/未覆盖”只是阅读用例后的判断，不代表测试全绿。
- **未做并发/故障注入**：B23-02 的投递、B23-05 的唯一键竞争、B23-06 的 `next_followup_at` 覆盖、B23-P1 的双扫描、B23-P2 的嵌套回滚对象状态，都只有静态代码路径，没有真实并发/故障复现；因此 B23-06/P1/P2 的严重度保留 P3/待确认。
- **未重读 B22 文件**：`settings/model.py`、`settings/numbering.py` 属 B22 已审，本块只按 B22 结论引用；`settings/schema.py` 因 B23-04 重读了相关段，没有重读全文。`numbering.py` 的 `period_max_number` 只在 B22 结论里引用，没有重新逐行复核。
- **未做数据库方言实测**：`with_for_update` / `skip_locked` / 部分唯一索引在 SQLite 与 PostgreSQL 的行为不同，本块没有起 PG 实例验证；B23-05/P1 的并发结论依赖生产 PG。
- **未审前端**：设置页、公海复核页、导入页的按钮防双击、错误提示是否会展示 `str(exc)`，属于 F 系列，本块未看。
- **未审外部投递**：`notification_service.dispatch_pending` 只读了入口和锁，没有读企微发送/失败重试全文；B23-02 的“通知是否最终送达”依赖 B15/B16 已有结论。
- **未审 `settings/service.py` 的每一行业务语义**：本块按“配置校验、公海回收、自动任务、通知、候选复核”分段读完全文，但没有对每个 `serialize_*` 字段做逐字段前端映射核对。

## 六、对 B22-11 的修正（不重写 B22 正文，只补台账/报告）

B23 读 `settings/router.py:669-689、729-744` 后确认：

- `seq_length` 已在创建/更新路由校验 1–12，B22-11 原写“无下界”不成立；
- `date_format="%"` 在 Python `strftime` 返回 `%`，不会抛 `ValueError`，B22-11 原写“保存/取号 500”不成立；
- 仍成立的是 `prefix` 可含 `%/_`，`numbering.py:309-336` 的 `LIKE f"{head}%"` 未转义，可能播种错号段。

B22-11 已在 `review/blocks/B22-打样路由任务时间线与设置模型.md` 和 `review/LEDGER.md` 收窄为“编号前缀参与最大号探测时未转义 LIKE 通配符”，仍为 P3。B22-P1 已由 B23-04 确认，不再单列待确认。
