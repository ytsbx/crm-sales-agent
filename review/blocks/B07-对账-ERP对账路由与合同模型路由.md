# B07 对账·ERP 对账/路由 + 合同模型与路由（4,639 行）

审查时间：2026-11-21
范围：`backend/app/modules/erp/{reconcile,router}.py`、`backend/app/modules/contract/{model,schema,oa_fields,visibility,pdf,customer_flow,router}.py`
读取情况：9 个文件全部逐行读完，无“未读完”。

> 本块同时按补充要求检查：负数/0/超大值/小数位校验、事务边界、异常是否被吞/错误信息是否泄露内部细节/日志敏感信息、关键钱操作审计、以及 `backend/tests` 是否已有覆盖。
> 未运行测试套件（项目要求隔离库，且本块只审不改）；测试覆盖结论来自 `backend/tests` 定向检索与相关用例阅读，不是“跑过全绿”。

## 一、逐文件：我检查了什么、结果如何

### 1. `backend/app/modules/erp/reconcile.py`（1,802 行）— 已读完

检查内容：期间对账的金额口径、聚合规则、差异去重、自动结案、人工核定、售后调整台账、数据范围、锁与事务、金额校验。
结果：
- 金额比较用 `Decimal` 与 `AMOUNT_TOLERANCE=0.01`，没有 float 参与比较；期间边界按北京时间日历日，月初 8 小时归月错误已修（第 210-229 行）。
- `run_key=(system,shop,period)` 幂等，run 行锁，采集 cursor 锁后读事实；`open_by_key` 只锁自动结案类型的 open diff（第 760-810 行）。这部分未发现重复入账/重复建批。
- `build_order_aggregates` 按订单+SKU 聚合 ordered/shipped/returned/exchanged，数量缺失不默认 0；换货不冲减净交付（第 560-670 行）。
- 售后只入队、不改回款；`confirm_diff` 售后 manual 只生成 `FinancialAdjustment(pending)`，不覆盖原始回款（第 1600-1757 行）。这个口径是正确的。
- 发现的问题集中在：有限范围的跨对象 ID 碰撞、售后调整金额可为 NULL、`missing_external` 误判、核定范围检查与加锁之间的 TOCTOU、run 内部提前 commit。
- 负数/0/超大值/小数位：`_ADJUSTMENT_AMOUNT` 用 `ge=0, max_digits=16, decimal_places=2`（第 101-103 行），但 `raw_amount is None` 时不校验、直接落 NULL（B07-05）。`AMOUNT_TOLERANCE` 对负数没有业务语义，但这里只用于“本地总额 vs 外部总额”的绝对差，不涉及退款金额。
- 事务：`run_reconciliation` 内部 `commit`（第 1401-1404 行），路由随后写审计再 commit（B07-11）；`confirm_diff` 本身不 commit，由路由统一提交，这一点是对的。
- 异常/日志：本文件未发现把原始报文全文写日志；`diff_evidence` 只按证据键回查。未发现错误信息把内部堆栈直接抛给前端。
- 审计：本文件不写审计，核定路由写；但核定的金融调整金额没有进审计（B07-06）。

结论：P0 0 条、P2 4 条、P3 1 条。金额主链路（Decimal 比较、售后不自动冲回款）成立；B07-01 的原“跨表 ID 碰撞”候选经复核不成立（未匹配差异的 `internal_id` 均为 None），已关闭；金额为空和误报是本文件主要问题。

### 2. `backend/app/modules/erp/router.py`（891 行）— 已读完

检查内容：对账/差异/映射/推单/回调路由的权限码、数据范围、审计、异常出口、回调鉴权。
结果：
- `readiness` 已按用户范围统计；`push_by_order_no` 按单号锁订单、`ensure_in_scope`、`ensure_push_confirmed`；`sync_order`/`reconcile_order` 用 `get_visible_order(for_update)`。这三个写入口的权限与确认闸门成立。
- `_ensure_mapping_target_scope` 对 customer/sku/order/明细分别校验；但 order 映射的 `parent_key` 校验会触发 B06-01（订单映射 `parent_key` 写成自己，查到自身 `internal_id=NULL`，永久 409），本块复核维持 B06-01，不重复单列。
- `_ensure_diff_scope` 只按 `diff.internal_id` 去 `SalesOrder`，没有按 `object_type` 分支；`list_diffs` 也用同一套 `internal_id in SalesOrder.id`。原 B07-01 候选复核后不成立：未匹配 customer/SKU 差异的 `internal_id` 都是 None，会被 403/排除；保留为结构改进建议（P3）。
- `collection_status` 仍只要求 `order:view` 且服务全量返回，B06-10 维持；`list_reconcile_runs` 也完全没有数据范围（B07-07）。
- `collection_run` 要 `settings:manage`，失败也写审计；但 `collect_once` 抛 `AppError`（如外币 422）不在 `(ErpNotConfigured, ErpError)` 捕获范围，该失败尝试不写审计（B06-03 的相邻问题，本块不重复升级）。
- `confirm_reconcile_diff` 先范围检查、后 `confirm_diff` 锁行；范围检查和锁行之间没有重新读 `diff.internal_id`，存在 TOCTOU（B07-09）。
- `confirm_reconcile_diff` 审计只记 `status/resolution/note`，不记 `financial_adjustment_amount/type`；售后退款金额在审计里查不到（B07-06）。
- 回调 `_verify_webhook_secret` 未配置密钥就 fail closed、用 `compare_digest`，这是对的；但只有共享密钥，没有 HMAC/时间戳/事件签名（B07-13）。
- 异常/错误信息：推单/同步/对账失败都走 `AppError` 通用文案；没有把内部异常原文返回。`collection_run` 的审计缺口是“失败尝试不落审计”，不是信息泄露。
- 负数/0/超大值/小数位：`DiffConfirmRequest.financial_adjustment_amount` 是 `Decimal | None, ge=0, max_digits=16, decimal_places=2`（`erp/schema.py:104`），入参层校验正确；但 service 允许 None 落库（B07-05）。

结论：P0 0 条（原 B07-01 已关闭）、P2 3 条、P3 2 条。B06-01/B06-02/B06-10 在本块复核仍成立，不再重复编号。

### 3. `backend/app/modules/contract/model.py`（96 行）— 已读完

检查内容：合同表结构、金额列、状态、幂等键、外键、唯一约束。
结果：
- `ContractTemplate` 有 `(doc_type,name,version)` 唯一约束，版本可追溯。
- `ContractDocument` 只有 `doc_no unique`；`request_key` 在 ORM 里没有唯一约束（第 93 行），迁移 `c7e3a9f5b2d8_contract_document_request_key.py` 有 `uq_contract_document_request_key`。生产库靠迁移，测试用 `Base.metadata.create_all` 建表时没有这条约束（B07-12）。
- `quote_id` 没有 `ForeignKey("quotes.id")`（第 63 行）；`generated_file_id` 也没有外键。当前写入路径都先校验对象，但结构上允许悬挂引用（P3，并入 B07-12）。
- **合同没有结构化金额列**：金额只存在 `content_snapshot/filled_data` 的 JSON/文本里（第 73-74 行）。这直接导致 OA 金额只能从当前报价版本/订单临时取（B07-03）。
- `status` 是 `String(16)`，没有 CHECK/Enum，只靠 service 写 `draft/signed/void`；当前没有发现能写入非法状态的写入口，但结构不防直写（P3，并入 B07-12）。
- 负数/0/超大值：模型层金额缺失，无法校验；合同金额由上游报价/订单保证。`doc_no/title` 有长度限制，`content_snapshot` 是 Text 无长度限制（合同正文可能很大，属正常业务）。

结论：P1 1 条（金额无结构化落库，见 B07-03）、P2 1 条（幂等键模型/迁移不一致，见 B07-12）、P3 1 条（外键/状态约束，并入 B07-12）。

### 4. `backend/app/modules/contract/schema.py`（100 行）— 已读完

检查内容：合同创建/签署/作废/OA 发起入参的长度、必填、幂等键、金额/主体校验。
结果：
- `DocumentCreate.request_key` **可选**（第 58 行），注释自己写着“不传也能用，但那样重复提交就防不住——所以前端必须带”。这是 B07-04 的直接入口。
- `original_file_id gt=0`、`title max_length=200`、`contract_number/our_company/counterparty_name` 都有长度；`extra_fields: dict[str,str]` 没有总大小/字段数上限（P3，未单列）。
- `DocumentSign.file_id` 没有 `gt=0`，但 service `lock_file_row` 查不到会 404，不构成越权。
- `DocumentVoid.reason` 去空白后必填，作废原因能落审计，合格。
- `ContractOaStart.our_company/counterparty_name` 去空白后必填；`extra_fields: dict[str, Any]` 没有大小上限（P3）。
- `source_type` 用 pattern 限定 `crm_template|customer_drafted`；`contract_origin` 用 Literal 限定，非法值会在 Pydantic 层拒绝。
- 负数/0/超大值：合同本身没有金额入参；`file_id` 允许 0/负数但对象查询会拒绝。没有发现“金额小数位超限”的入口，因为合同金额不接收结构化入参。

结论：P1 1 条（request_key 可选，见 B07-04）；其余未见金额校验漏洞。

### 5. `backend/app/modules/contract/oa_fields.py`（46 行）— 已读完

检查内容：OA 表单主体字段组装、JSON 注入、系统字段清理。
结果：
- 只从模板 schema 里找 `TableField` 的 `contract_mine_info/contract_opposite_info`，把主体名称用 `json.dumps` 写入；名称已经过 schema 的 `strip + min_length`，不会把裸引号拼坏 JSON。
- 主动移除 `contract_biz_id/contract_operation_status/contract_performance_status` 三个钉钉系统字段，避免 CRM 编号/状态覆盖钉钉生成值。
- 没有金额计算，也没有把成本/利润写进 OA 字段。
- 未发现异常吞掉或日志泄露；`walk` 对非法 JSON 字符串静默跳过，属容错，不构成金额问题。

结论：未发现 P0/P1/P2；无新增发现。

### 6. `backend/app/modules/contract/visibility.py`（60 行）— 已读完

检查内容：合同详情的统一可见性判据、客户/订单/创建人三级回退、公海场景。
结果：
- 判据是“客户当前负责人 → 订单负责人 → `created_by`”，与 `service.list_documents` 的 SQL `COALESCE` 对齐；客户进公海后合同不会谁都打不开，这是有意的业务口径。
- `contract_in_scope` 先 `ensure_draft_access`，再 `scoped_owner_ids`；不判权限码，权限码由各入口自己判，职责分离正确。
- 未发现“改 ID 看别人合同”的直接路径：`ensure_draft_access` 失败会返回 false，随后按 owner 范围判。
- 潜在口径：客户/订单负责人都为空时，`created_by` 可见。若创建人已离职但数据范围仍包含他，合同仍可见；这是三级回退的既定语义，不作为缺陷。

结论：未发现 P0/P1/P2；无新增发现。

### 7. `backend/app/modules/contract/pdf.py`（143 行）— 已读完

检查内容：合同 PDF 渲染的注入、金额展示、状态展示、历史快照。
结果：
- 所有文本先 `escape` 再进 `Paragraph`，并对空格做 `&nbsp;` 处理；公司名/客户名/正文里的 `<`、`&` 不会让 reportlab 抛异常。这是正确的防注入写法。
- 正文取 `content_snapshot`，不实时读客户资料；合同编号/客户/订单/报价版本/状态/到期日都上纸；报价号带版本号，能说明合同依据哪一版。
- **PDF 没有单独打印合同金额字段**；金额只在正文快照里（上传原稿合同正文是“以随附原文件为准”，金额在原文件里）。对“系统生成的历史合同”，如果正文没写金额，PDF 就没有金额。是否需要在抬头补“合同金额”取决于业务，当前不作为 P1；归入 B07-15 的“合同金额无结构化来源”观察。
- 未发现异常吞掉；`escape` 已处理 reportlab 的 XML 解析风险。

结论：未发现 P0/P1/P2；P3 观察并入 B07-15。

### 8. `backend/app/modules/contract/customer_flow.py`（452 行）— 已读完

检查内容：客户原稿合同与钉钉审批关联、审批核验、状态同步、作废前撤回、回签提醒、业务依据绑定。
结果：
- `verify_original` 锁文件行、校验 `can_access_file`、扩展名、文件大小与 sha256；原件被改动/丢失返回 410，不合格文件不能进合同流程。这部分是稳的。
- `associate_instance`/`link_approval` 先查 `OaInstance.instance_id` 全局唯一，再读钉钉实例、核验发起人/流程/编号/名称/附件；重复关联会 409。幂等与归属校验基本成立。
- **`verify_instance` 不校验合同金额、客户/签约主体、附件内容/哈希**：只校验 `processCode`、发起人、合同编号、合同名称、附件“有 fileId”（第 139-167 行）。同编号同名的另一份审批（金额不同）可以被关联为已通过；`service.sign_document` 只要求 `approval.status == "approved"`（`contract/service.py:1221-1225`），于是能签下去（B07-02）。
- `sync_approval` 没有 `write_audit`：手工同步会把 OA 行状态和 `doc.filled_data._dingtalk_oa` 改掉，但审计里没有“谁在什么时候同步、从什么状态到什么状态”（B07-10）。
- `sync_approval` 先用 `visible_doc`（不加锁）确认范围，再 `lock_oa_business_then_instance`；doc 本身没有 `for_update`，并发签署/作废可能让同步把 `_dingtalk_oa` 写到已签/已作废文档上（B07-14，P3）。
- `store_snapshot` 在审批通过/驳回时调用 `oa._notify_contract_review_outcome`，通知发生在路由 commit 之前；如果 commit 失败，外部通知已发出、本地没落库（P3 观察，未单列）。
- `withdraw_before_void` 要求操作人就是 OA 发起人，撤回结果未知时拒绝作废；`auto_link` 只查当前用户自己的钉钉实例。这两条口径正确。
- `BasisInput` 的 `order_id/quote_id` 有 `gt=0`；`bind_basis` 校验客户归属、数据范围、`_ensure_signable_source`，并写审计。未发现金额校验漏洞，因为金额由 quote version/订单决定。
- `remind_countersigns` 一次锁所有 draft 文档，发送通知后写 `_return_reminded_day`；提醒不是钱操作，未发现重复入账；但全表锁范围较大（P3 观察，未单列）。

结论：P1 1 条（B07-02 审批金额/主体未核验）、P2 1 条（B07-10 同步无审计）、P3 1 条（B07-14 无文档锁）。

### 9. `backend/app/modules/contract/router.py`（1,049 行）— 已读完

检查内容：合同生成/详情/签署/作废/删除/恢复/下载/OA 发起的权限、数据范围、幂等、金额、审计、事务。
结果：
- `generate_document` 只接受“客户原稿上传”，禁用系统模板生成；校验 `contract_origin`、`title`、`parent/supersede` 的主管权限；客户/订单/报价都做归属与范围校验；`verify_original` 在生成前执行。入口闸门是对的。
- `create_document_record` 的幂等完全依赖 `request_key`；不传键时两次请求会生成两份合同、两份 `BusinessFile` 原件引用、两条回签待办（B07-04）。PDF 落盘/DB/审计/commit 用 `try` 包住，失败走 `_cancel_safe(cleanup_pending_objects)`，事务边界这块做得比较完整。
- `sign_document` 用 `for_update=True, protect_transfer=True` 锁文档，校验文件访问、`_ensure_signable_source`、OA 已通过、不能拿原稿/生成稿当签署件；并发双击会被状态闸门挡住。签署本身不重复入账。
- **签署审计只记 `doc_no/file_id`**，不记合同金额、报价版本、OA 实例/审批金额；出事后无法从审计复算“签的是哪一版、多少钱”（B07-15，P2）。
- `start_dingtalk_contract_approval` 用 **当前** `quote_version.total_amount` 或 `order.total_amount` 作为 OA 金额（第 204-207 行），不是合同生成时的金额快照；合同正文/`_header` 是生成时冻结的，报价版本后来改过总额（B02-01 已确认接受后仍可改运费）就会让“合同正文金额”和“OA 审批金额”不一致（B07-03）。
- `start_dingtalk` 先读 `doc` 不加锁，再判状态/已有审批；并发下可能在文档刚被签署后仍发起一张 OA（P3 观察，未单列）。
- `void_document` 对已签合同要求主管，先 `withdraw_before_void` 再重新取文档复核主管权限；作废原因进审计和客户时间线。作废审计字段足够。
- `delete_document` 拦已签、拦 pending/submitting/needs_review 审批，软删并取消回签待办；`restore` 只恢复由删除自动取消的待办。生命周期处理正确。
- `download_document` 原件分支校验 size/sha256，生成稿分支校验 checksum，校验失败单独 commit 审计后 410；不静默重新渲染。下载审计记 `source/status`，对外文件可追溯。合格。
- 负数/0/超大值/小数位：合同入口没有金额参数；`DocumentSign.file_id` 负数会 404；`DocumentVoid.reason` 有 255 上限。没有金额校验入口。
- 异常/错误信息：`start_dingtalk` 对钉钉/httpx/OSError 统一转“未成功上传/尚未发起审批”，不把底层原文返回；`sync_approval` 对查询失败统一 502。未发现内部细节泄露。
- 日志：本文件未发现打印合同金额/客户敏感信息；PDF 渲染走线程池。

结论：P1 2 条（B07-03 OA 金额取实时值、B07-04 合同创建可重复）、P2 2 条（B07-15 签署审计不完整、B07-05 金额 NULL 的合同侧无结构化金额）、P3 1 条（start OA 无文档锁）。

## 二、本块发现清单

### B07-01｜已关闭（原候选不成立）｜customer/SKU 的 internal_id 不会以“订单 id”形式出现在未匹配差异里

- **原候选**：`list_diffs`/`_ensure_diff_scope` 把 `diff.internal_id` 一律当 `SalesOrder.id`，customer/SKU 的自增 id 与订单 id 数字碰撞时会越权可见。
- **复核过程**：`DIFF_UNMATCHED_CUSTOMER/SKU` 只在 `match_status in (MATCH_PENDING, MATCH_CONFLICT)` 的映射上创建（`reconcile.py:1217-1220`）；`resolve_mapping` 在 customer/SKU 未命中或冲突时返回 `(None, None, note, MATCH_PENDING/MATCH_CONFLICT)`（`collection.py:716-762`），所以这些差异的 `internal_id` 是 `None`，不是 Customer/SKU 的主键。
- **复核结论**：`_ensure_diff_scope` 对 `internal_id is None` 直接 403（`erp/router.py:210-214`），`list_diffs` 也不会命中；原 P0 越权结论不成立，已关闭。没有证据表明“匹配成功后又被改回 pending 但 internal_id 未清空”的路径；`assign_mapping` 只把映射写成 matched。
- **仍建议保留的结构改进（P3，不单列）**：差异表增加 `internal_object_type`，避免以后新增“客户/SKU 差异也带 internal_id”的写法时再次踩坑；当前不是缺陷，只是防御性设计。
- **测试覆盖**：无相关用例，但也无触发路径；不需要为这个已关闭候选补 P0 回归。

### B07-02｜P1｜关联钉钉审批只验编号/名称/附件存在，不验金额与主体，错金额审批可被签成“已通过”

- **位置**：`backend/app/modules/contract/customer_flow.py:139-167`；`backend/app/modules/contract/router.py:232-269`（link）/`204-230`（auto-link）；`backend/app/modules/contract/service.py:1221-1225`
- **触发条件**：操作人拿一条 `processCode` 正确、发起人是自己、`合同编号 == doc.doc_no`、`合同名称 == doc.title`、带任意附件的钉钉审批实例；该审批的金额/客户主体/附件内容与 CRM 这份合同不一致（例如同编号合同改过价、或另一份同编号模板）。
- **后果**：`verify_instance` 通过，`store_snapshot` 把该 OA 行写成 `approved`，`doc.filled_data._dingtalk_oa` 也标 approved；随后 `sign_document` 只检查 `approval.status == "approved"`，合同被登记为已签署。对外合同金额/主体与审批依据不一致，审批证据链失真。
- **证据代码**：
  ```python
  # customer_flow.py:144-167
  fields = data.get("formComponentValues") or data.get("formValueVOS") or []
  ...
  if value(number_id, ("合同编号", "contract_number")) != doc.doc_no:
      fail(...)
  if value(title_id, ("合同名称", "contract_name")) != doc.title:
      fail(...)
  attachments = [...]
  if not any(has_file(f) for f in attachments):
      fail("钉钉审批没有合同正文附件，请先上传客户原合同")
  ```
  这里没有 `amount` / `customer_name` / `our_company` / 附件 `fileId`/哈希 的比较。
- **建议**：`verify_instance` 至少增加三项硬校验：① OA `MoneyField` 金额（按 `field_map` 取）与合同冻结金额/`quote_version.total_amount` 一致（按分比较）；② OA 客户名称/我方主体与 `doc`/客户一致；③ 附件 `fileId` 与 CRM 原件/生成稿的 file id 或 sha256 对应。若钉钉模板不提供金额字段，则不允许自动关联为 approved，要求人工在 CRM 做金额核对并留说明。
- **测试覆盖**：`backend/tests/test_customer_contract_flow.py:24-38` 的 `test_association_verifies_contract_number_name_actor_and_file` 只覆盖编号/名称/发起人/附件；`test_real_instance_aliases_are_supported` 覆盖字段别名；没有金额/主体不一致的用例。也就是说，现有测试固定了“只验这四项”的当前行为。

### B07-03｜P1｜合同 OA 金额取自“当前”报价版本/订单，合同正文却是生成时快照，二者可漂移

- **位置**：`backend/app/modules/contract/router.py:204-207`（金额取值）；`backend/app/modules/contract/service.py:634-641、675-678`（正文/抬头快照）；`backend/app/modules/contract/model.py:73-74`（无结构化金额列）
- **触发条件**：合同先生成（正文/`_header` 冻结当时金额），之后报价版本总额变化（B02-01 已确认“已接受报价仍可改运费”，或订单总额被改），再发起钉钉/OA 审批。
- **后果**：`start_dingtalk_contract_approval` 用实时 `quote_version.total_amount` 或 `order.total_amount` 作为 OA 的 `amount`，而合同正文里印的是生成时的金额；审批通过的金额与最终签署合同金额不一致。`sign_document` 不比较这两个金额，错账可以一路签完。
- **证据代码**：
  ```python
  # router.py:204-207
  quote_version = await session.get(QuoteVersion, doc.quote_version_id) if doc.quote_version_id else None
  amount = (getattr(quote_version, "total_amount", None) if quote_version else None) or (
      getattr(order, "total_amount", None) if order else None
  )
  ...
  "amount": str(amount) if amount not in (None, "") else None,
  ```
  `ContractDocument` 没有金额列，无法用“合同生成时金额”作为唯一依据。
- **建议**：生成合同时把金额（分）写入 `filled_data._contract_amount` 或新增 `amount` 列（`Numeric(16,2)`），OA/签署/下载都只读这个冻结值；若业务允许合同金额随报价变，必须显式走“重新生成合同/补充协议”，不能静默漂移。
- **测试覆盖**：未覆盖。`backend/tests/test_contract_oa_form.py` 只测 OA 表单字段填写/金额为空；没有“生成后报价改总额、再发起审批”的用例。

### B07-04｜P1｜合同创建 `request_key` 可选，双击/重试可生成两份合同

- **位置**：`backend/app/modules/contract/schema.py:57-58`；`backend/app/modules/contract/service.py:401-493`；`backend/app/modules/contract/router.py:477-512`
- **触发条件**：客户端不传 `request_key`（API 允许），或重试时没带上同一把键；同一份客户/订单/原件连续提交两次。
- **后果**：`generate_document` 只有在 `if payload.request_key:` 时才做 advisory lock + 查重；没有键就直接走新建，生成两个不同 `doc_no` 的合同、两条 `contract_original` 的 `BusinessFile` 引用、两条 `contract:signed-upload:{doc.id}` 待办。虽然合同不直接入账，但会造成重复对外文件、重复审批/回签，属于流程错误。
- **证据代码**：
  ```python
  # schema.py:57-58
  #: 幂等键：前端打开生成弹窗时生成一个，重复提交 / 网络重试带同一个值。
  #: 不传也能用，但那样重复提交就防不住——所以前端必须带。
  request_key: str | None = Field(default=None, max_length=64)
  ```
  ```python
  # service.py:402-435
  if payload.request_key:
      ... pg_advisory_xact_lock ...
      replayed = ... where request_key == payload.request_key ...
      if replayed is not None: ... return replayed, True
  # 没有 request_key 时直接继续到 ContractDocument(...)，无查重
  ```
- **建议**：服务端把 `request_key` 变成创建合同的必填（或由服务端按 `customer_id + order_id + quote_version_id + original_file_id + sha256(original_file)` 生成确定性键）；前端即使漏传也不会重复。`document` 表的唯一约束也要在 ORM 里声明，避免测试/其他建表路径没有约束。
- **测试覆盖**：未覆盖。`test_customer_contract_flow.py` 只测“同一 request_key 的 origin 指纹”，没有“不传 key 连点两次”的用例；`test_contract_upload_entry_guard.py` 只测入口拒绝模板。

### B07-05｜P2｜售后差异金额缺失时仍生成 `amount=NULL` 的待处理退款台账

- **位置**：`backend/app/modules/erp/reconcile.py:1678-1681、1730-1741`；`backend/app/modules/integration/model.py:446`（`amount` 可空）
- **触发条件**：售后头 `amount` 为空（外部报文缺金额），或调用方 `financial_adjustment_amount` 不传；`confirm_diff` 走 `manual`。
- **后果**：`FinancialAdjustment` 以 `amount=None`、`status=pending` 落库；财务端看到一条“待处理退款/应收调整”但没有金额，无法执行，也无法判断是零退款还是漏填。`serialize_financial_adjustment` 会把它序列化成 `"amount": null`（第 1768-1771 行）。
- **证据代码**：
  ```python
  raw_amount = financial_adjustment_amount
  if raw_amount is None and incoming.get("amount") not in (None, ""):
      raw_amount = incoming["amount"]
  if raw_amount is not None:
      raw_amount = _ADJUSTMENT_AMOUNT.validate_python(raw_amount)
  ...
  adjustment = FinancialAdjustment(
      ...
      amount=raw_amount,   # 仍可能是 None
      ...
  )
  ```
- **建议**：售后 `manual` 核定必须要求金额（`raw_amount is None` 直接 422，提示“请填写本次退款/应收调整金额，0 也要显式填 0”）；或者把“金额未知”做成独立状态（`needs_amount`），不要混进 `pending` 让财务误以为可处理。
- **测试覆盖**：未覆盖。`backend/tests/test_batch13_erp_repairs.py:267-278` 只测金额 50 的正常路径；没有 `amount=None` 的用例。

### B07-06｜P2｜售后核定生成退款/应收调整台账，但路由审计不含调整金额/类型

- **位置**：`backend/app/modules/erp/router.py:771-800`；`backend/app/modules/erp/reconcile.py:1745-1757`
- **触发条件**：任意 `DIFF_AFTERSALE_REVIEW` 走 `manual` 并生成 `FinancialAdjustment`。
- **后果**：`write_audit` 的 `before/after` 只有 `status/resolution/note`，没有 `financial_adjustment.id/type/amount/currency/order_id`。审计能证明“谁核定了差异”，但不能证明“生成了多少钱的退款/应收调整”；金额证据只存在 `financial_adjustments` 表，若该表被清理/迁移，审计链断开。
- **证据代码**：
  ```python
  after={
      "status": result["diff"]["status"],
      "resolution": payload.resolution,
      "note": payload.note,
  },
  ```
  `result["financial_adjustment"]` 未进审计。
- **建议**：审计 `after` 增加 `financial_adjustment` 的 `id/order_id/adjustment_type/amount/currency/status`；如需脱敏，至少写 `amount` 的字符串精确值（不要 float）。
- **测试覆盖**：未覆盖。现有重构测试只断言 `financial_adjustment` 返回值，没有断言审计内容。

### B07-07｜P2｜对账批次列表对 `order:view` 全量返回，无数据范围

- **位置**：`backend/app/modules/erp/router.py:702-713`；`backend/app/modules/erp/reconcile.py:1423-1439`
- **触发条件**：任意只有 `order:view` 的销售/客服打开“对账批次”。
- **后果**：`list_runs` 只按 `system_type` 过滤，不按用户订单范围过滤，返回全公司所有店铺/期间的 `counters`（外部订单数、缺失数、差异数等）。这泄露公司整体交易量与店铺经营情况，和 `readiness` 已按范围统计的口径不一致。
- **证据代码**：
  ```python
  # router.py:710-712
  items, total = await recon_svc.list_runs(
      session, system_type=system_type, page=page, page_size=page_size
  )
  # reconcile.py:1426-1438
  stmt: Select = select(ReconciliationRun)
  if system_type:
      stmt = stmt.where(ReconciliationRun.system_type == system_type)
  ```
  没有 `owner_ids` 参数。
- **建议**：与 `collection_status` 一起收口：`list_reconcile_runs` 对有限范围用户只返回与其订单相关的批次聚合，或至少要求 `settings:manage` 才能看全局批次；前端不要把全量计数当“本人范围”。
- **测试覆盖**：未覆盖。`backend/tests/test_erp_callback_and_push.py` 只覆盖 `readiness` 的范围，没有对账批次路由的范围断言。

### B07-08｜P2｜外部订单缺 `occurred_at` 时被误判为“本期缺失”，且该差异不会自动关

- **位置**：`backend/app/modules/erp/reconcile.py:1090-1101`；`load_period_facts` 的 `_period_orders` 在 `469-474` 行按 `occurred_at` 过滤；`AUTO_CLOSE_TYPES` 在 `96-99` 行不含 `DIFF_MISSING_EXTERNAL`
- **触发条件**：本地已有 `ExternalObjectMapping`（`internal_id` 非空），外部订单在库里存在但 `occurred_at IS NULL`（采集缺时间），且本地订单 `created_at` 落在本期。
- **后果**：`seen_external` 只收 `facts["orders"]`（本期有 `occurred_at` 的订单），该外部单不在集合里；`missing_external` 判它“本期采集没有它”，生成 `DIFF_MISSING_EXTERNAL`。由于该类型不在自动结案集合，下一期/重跑还会持续产生/保留，形成永久误报，增加人工核对。
- **证据代码**：
  ```python
  seen_external = {(order.shop_id, order.external_id) for order in facts["orders"] if order.external_id}
  for mapping in mappings:
      if not mapping.external_id or (mapping.shop_id, mapping.external_id) in seen_external:
          continue
      ...
      counts["missing_external"] += 1
  ```
  `load_period_facts` 的订单查询带 `ExternalRecord.occurred_at >= start AND < end`。
- **建议**：`seen_external` 改为按“外部单号在库中存在”判定（不受本期 `occurred_at` 限制）；对 `occurred_at IS NULL` 的外部订单单独计数/提示“时间缺失，无法归期”，不要判成缺失。或者把 `DIFF_MISSING_EXTERNAL` 纳入可自动结案，但必须先区分“真缺失”和“无时间”。
- **测试覆盖**：部分覆盖。`test_batch13_erp_repairs.py:320-337` 覆盖“待匹配 SKU 用父单/来源时间归期”，但没有“映射存在、外部订单 `occurred_at=None`、本地订单本期创建”的 `missing_external` 用例。

### B07-09｜P2｜核定差异先做范围检查、后锁行，期间 `diff.internal_id` 可被并发对账改写（TOCTOU）

- **位置**：`backend/app/modules/erp/router.py:769-770`；`backend/app/modules/erp/reconcile.py:1608-1618`
- **触发条件**：用户 A 对差异 D 发起 `confirm`；`_ensure_diff_scope` 用当时的 `D.internal_id` 判为“自己的订单”；在 `confirm_diff` 拿到 `FOR UPDATE` 之前，另一个对账事务（同一 `diff_key`）把 D 的 `internal_id` 更新为另一张订单（售后晚匹配、订单归属更正等）。
- **后果**：`confirm_diff` 锁后读到新的 `internal_id`，按新归属生成 `FinancialAdjustment(order_id=新订单)`，但范围检查是按旧订单做的。若新订单属于别人，等于把退款/应收调整挂到他人订单上；至少也是“核定对象与权限检查对象不一致”。
- **证据代码**：
  ```python
  # router.py:769-770
  diff = await recon_svc.get_diff(session, diff_id)
  await _ensure_diff_scope(session, user, diff)
  ...
  result = await recon_svc.confirm_diff(session, diff, ...)
  # reconcile.py:1608-1618
  locked = (await session.execute(
      select(IntegrationDiff).where(IntegrationDiff.id == diff.id)
      .with_for_update().execution_options(populate_existing=True)
  )).scalars().first()
  diff = locked
  ```
- **建议**：把范围检查放进 `confirm_diff` 的锁内，或在路由先 `SELECT ... FOR UPDATE` 取 diff，再做 `_ensure_diff_scope`，然后把这个已锁对象传给 `confirm_diff`（`confirm_diff` 不再重复锁/重读）；锁内还要重新校验 `object_type` 与归属。
- **测试覆盖**：未覆盖。现有测试没有并发交错；`test_batch13_erp_repairs.py` 的核定是单线程顺序调用。

### B07-10｜P2｜手工同步 OA 审批不写审计

- **位置**：`backend/app/modules/contract/customer_flow.py:272-311`
- **触发条件**：用户在合同详情点“同步审批”，走 `sync_approval`（钉钉或自研 OA 分支）。
- **后果**：`store_snapshot` 会改 `OaInstance.status/result/synced_at` 和 `doc.filled_data._dingtalk_oa`，但路由没有 `write_audit`。谁在什么时候把审批从 pending 同步成 approved/rejected、同步前后是什么状态，审计里没有记录；如果随后据此签署，签署审计也只看得到 `file_id`（B07-15）。
- **证据代码**：
  ```python
  @router.post("/contract-documents/{doc_id}/approval-sync")
  async def sync_approval(...):
      doc = await visible_doc(session, user, doc_id)
      row = await oa.get_by_business_key(...)
      ...
      await store_snapshot(session, doc, row, data)
      await session.commit()
      return ok(oa.serialize(row), "已同步钉钉审批")
  ```
  函数内没有 `from app.core.audit import write_audit`，也没有调用。
- **建议**：同步成功后写 `write_audit(action="sync_contract_oa", before={"status": old_status}, after={"status": row.status, "instance_id": row.instance_id})`；自动轮询可只记业务事件，人工触发必须记操作人。
- **测试覆盖**：未覆盖。`test_contract_dingtalk_oa.py` 测的是 `_contract_oa_resubmit/_contract_oa_result` 文案；`test_contract_oa_notification.py` 测通知去重，没有断言同步审计。

### B07-11｜P3｜`run_reconciliation` 内部先 commit，路由再写审计；审计失败会留下“无审计的对账批次”

- **位置**：`backend/app/modules/erp/reconcile.py:1401-1404`；`backend/app/modules/erp/router.py:681-698`
- **触发条件**：`run_reconciliation` 已把 run/diff 提交；随后 `write_audit` 或第二次 `commit` 失败（审计表锁、磁盘、连接中断）。
- **后果**：对账批次和差异已经生效，但审计没有记录“谁触发了这次对账”；接口返回 500，用户可能重试，重试命中 `run_key` 返回 reused，仍没有首次审计。事后无法证明批次来源。
- **证据代码**：
  ```python
  # reconcile.py:1401-1404
  run.status = "finished"
  run.finished_at = _now()
  run.counters = counts
  await session.commit()
  # router.py:688-698
  await write_audit(... action="erp_reconcile_run" ...)
  await session.commit()
  ```
- **建议**：把审计写入放在 service 的同一个事务里（service 接收 `operator_id`，在 commit 前写审计），或由路由在 service 前用独立的 `request_keys` 记录“开始”，成功后补“完成”；不要跨两个事务。
- **测试覆盖**：未覆盖。现有测试没有审计失败注入。

### B07-12｜P3｜合同/调整台账的关键唯一约束只在迁移里，ORM/测试建表路径没有

- **位置**：`backend/app/modules/contract/model.py:63、93`；迁移 `backend/alembic/versions/c7e3a9f5b2d8_contract_document_request_key.py:32-35`；`backend/app/modules/integration/model.py:438-446`
- **触发条件**：测试/本地用 `Base.metadata.create_all` 建表（合同测试就是这么做的），或新环境只跑 ORM 建表；并发创建合同/售后调整。
- **后果**：`contract_documents.request_key` 在 ORM 没有 `UniqueConstraint`，SQLite 测试库不会拦重复键；`financial_adjustments` 的 `source_key` 部分唯一索引是 PostgreSQL-only（`postgresql_where`），SQLite 下语义不同。生产迁移有约束，但测试覆盖不到真实并发防线，容易“测试全绿、生产才 409/重复”。
- **证据代码**：
  ```python
  # contract/model.py:93
  request_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
  # migration c7e3...
  op.create_unique_constraint("uq_contract_document_request_key", "contract_documents", ["request_key"])
  ```
  `test_customer_contract_flow.py` 的 `sql_session` 用 `Base.metadata.create_all(...)` 建 `ContractDocument`，没有迁移约束。
- **建议**：在 ORM `__table_args__` 同步声明唯一约束（与迁移同名），测试建表也能覆盖；`FinancialAdjustment` 的 pending 唯一索引在 SQLite 下用普通唯一索引近似，或测试显式创建索引，确保并发用例真的能触发约束。
- **测试覆盖**：反向覆盖——测试建表路径缺约束，现有测试无法覆盖 B07-04 的并发重复。

### B07-13｜P3｜ERP 回调只用共享密钥，没有 HMAC/时间戳/事件签名

- **位置**：`backend/app/modules/erp/router.py:807-831`
- **触发条件**：`ERP_WEBHOOK_SECRET` 泄露（日志、配置导出、人员离职），攻击者构造新的回调事件。
- **后果**：`X-ERP-Secret` 正确即可调用回调，改写订单状态；服务层有事件键幂等，能防同一事件重放，但防不了“用同一密钥构造一个新事件/改 remark”。共享密钥也没有时间戳，无法限制旧请求重放窗口。
- **证据代码**：
  ```python
  secret = (app_settings.erp_webhook_secret or "").strip()
  if not secret: raise ...
  provided = (request.headers.get("X-ERP-Secret") or "").strip()
  if not compare_digest(provided, secret): raise ...
  ```
- **建议**：升级为 HMAC-SHA256（`timestamp + nonce + body` 签名），服务端校验时间窗与 nonce；共享密钥只作为过渡，至少在网关侧限制来源 IP/双向 TLS。
- **测试覆盖**：部分覆盖。`test_erp_callback_and_push.py` 覆盖密钥错误/未配置，但没有签名/时间窗用例。

### B07-14｜P3｜`sync_approval` 检查文档范围时不锁文档，可能把审批快照写到已签/已作废合同

- **位置**：`backend/app/modules/contract/customer_flow.py:275-309`
- **触发条件**：`sync_approval` 先 `visible_doc`（无锁）确认 draft/范围；与此同时另一个请求 `sign_document` 或 `void_document` 锁文档并提交；同步随后 `store_snapshot` 写 `filled_data`。
- **后果**：已签/已作废合同的 `_dingtalk_oa` 被更新为最新审批状态；不会改合同 status，但会让详情里的“审批结果”与“签署时看到的审批结果”不一致，事后追溯困难。
- **证据代码**：
  ```python
  doc = await visible_doc(session, user, doc_id)      # 无 for_update
  row = await oa.get_by_business_key(...)
  row = await oa.lock_oa_business_then_instance(...)
  ...
  doc = await visible_doc(session, user, doc_id)      # 仍无 for_update
  ...
  await store_snapshot(session, doc, row, data)
  ```
- **建议**：`sync_approval` 在 `store_snapshot` 前用 `get_doc_or_404(..., for_update=True)` 重新取文档并确认 `status == "draft"`；若已签/已作废，只更新 OA 行状态、不写文档快照，或返回“文档已结束，审批快照不再回写”。
- **测试覆盖**：未覆盖。没有“同步与签署并发”的用例。

### B07-15｜P2｜合同签署审计不记金额/报价版本/OA 实例，签署证据链不完整

- **位置**：`backend/app/modules/contract/router.py:687-695`；`backend/app/modules/contract/service.py:1204-1275`
- **触发条件**：任意合同登记签署。
- **后果**：`write_audit(after={"doc_no": doc.doc_no, "file_id": payload.file_id})` 只记了两个字段。审计无法回答“签的是哪一版报价、多少钱、依据哪条 OA 审批”。合同金额本来就没有结构化列（B07-03），审计再缺金额，事后只能翻 PDF/正文人工比对。
- **证据代码**：
  ```python
  await write_audit(
      session, operator_id=user.id, action="sign",
      business_type="contract", business_id=doc.id,
      after={"doc_no": doc.doc_no, "file_id": payload.file_id},
      ip=client_ip(request),
  )
  ```
- **建议**：签署审计补 `quote_id/quote_version_id/order_id/contract_amount(字符串)/currency/oa_instance_id/approval_status/signed_file_checksum`；金额用 `format(Decimal, "f")`，不要 float。
- **测试覆盖**：未覆盖。`test_approved_contract_archive_completes_real_return_task` 只断言 `doc.status`/task 状态，没有断言审计字段。

## 三、本块结论

- P0：0 条（B07-01 原候选经复核不成立，已关闭）。
- P1：3 条（B07-02 审批金额/主体未核验、B07-03 OA 金额实时漂移、B07-04 合同创建可重复）。
- P2：7 条（B07-05、06、07、08、09、10、15）。
- P3：4 条（B07-11、12、13、14）。
- 待确认：0 条（本块没有把无证据的静态怀疑写成结论）。
- 未读完：无（9 个文件全部读完）。

本块最优先处理顺序：B07-02/B07-03（合同金额/审批证据链）→ B07-04（重复合同）→ B07-05/B07-06（售后调整金额与审计）→ 其余 P2/P3。

## 四、本块没有看的部分及原因

| 文件/范围 | 情况 | 原因 |
|---|---|---|
| `backend/app/modules/contract/service.py` | 只定向读了 `generate_document`、`sign_document`、`_ensure_signable_source`、`list_documents`、`list_signed_files` 相关段，未逐行全文 | 它属于 B08，不在 B07 的 9 个文件里；本块只用它验证路由调用的金额/审批行为，不能替代 B08 全文审查 |
| `backend/app/modules/oa/service.py` | 未读 | 属于 B09/B10；B07 只看到 `customer_flow`/`router` 调用 `latest_contract_row`、`start_contract_approval`、`withdraw_contract_row`，自研 OA 的状态机与金额校验需在 B09/B10 复核 |
| `backend/app/modules/dingtalk/service.py`、`client.py` | 未读 | 属于 B10/B11；B07 只验证了 `get_process_instance`/`create_inquiry_instance` 的调用参数与返回处理，未核对这些函数内部的幂等/未知结果处理 |
| `backend/app/modules/integration/model.py` | 只读了 `FinancialAdjustment`/`IntegrationDiff` 相关段 | 不属于 B07 文件清单；用于确认 `amount` 可空、部分唯一索引的数据库方言差异 |
| `backend/tests` 全量 | 未逐文件读完，只按关键词定向检索并读了 `test_customer_contract_flow.py`、`test_batch13_erp_repairs.py`、`test_contract_dingtalk_oa.py`、`test_erp_external_sync.py` 的相关段 | 本块只审不改，未运行测试；测试覆盖结论是“现有用例是否覆盖该触发条件”，不是“跑过全绿” |
| 前端合同/对账页面 | 未读 | 属于 F05/F09 等前端块；B07 只审后端接口契约，前端是否展示金额、是否绕过确认不在本块 |
| 隔离库回归/并发注入 | 未执行 | 项目要求测试只能在隔离数据库运行；本块没有授权改测试，也没有构造并发夹具，因此 B07-09/B07-14 的并发结论来自代码路径，未做运行复现 |

> 说明：B07-01 原候选“ID 碰撞越权”经复核不成立：未匹配 customer/SKU 的映射在 `match_status=pending/conflict` 时 `internal_id=None`，`_ensure_diff_scope` 会 403，`list_diffs` 也排除；因此本块没有 P0。结构上仍建议差异表补 `internal_object_type`，但这是防御性改进，不按当前缺陷计。
