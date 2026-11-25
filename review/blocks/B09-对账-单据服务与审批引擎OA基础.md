# B09 对账·单据服务 + 审批引擎 + OA 基础（10 文件，4,713 行）

> 审查范围：`bizdoc/service.py`（2,112）、`approval/model.py`（99）、`approval/schema.py`（48）、`approval/access.py`（43）、`approval/router.py`（743）、`approval/rules_engine.py`（768）、`approval/rules_router.py`（516）、`oa/business_gates.py`（62）、`oa/client.py`（180）、`oa/contract_form.py`（142）。
> 10 个文件全部逐行读完；辅助核对 `bizdoc/router.py` 的生成/下载入口、`quote/service.py` 的审批提交调用、`payment/service.py` 的逾期口径、`approval` 迁移 `a5c8e2f7b9d1` 与 `test_oa_business_gates.py`/`test_approval_rules_engine.py`。只读，未改业务代码。

## 一、逐文件检查记录

### 1. `backend/app/modules/bizdoc/service.py`（2,112 行，已读全文）

**我检查了什么**：来源身份键与版本号分配；请求幂等（`run_idempotent_generation`）；模板默认/版本锁；快照组装（打样单/下单文件/报价单）；报价单的 `frozen` 抬头、条款、单位与 gaps；金额小计/费用/运费拆分；`_persist` 的模板闸门、草稿状态、标题截断、唯一冲突；`_archive_original` 的落盘/登记/补偿；`_cancel_safe`、`commit_doc_generation`；`plan_download` 的原件/重建/状态副本；`void_doc`；列表范围。

**结果如何**：
- **金额口径**：报价单明细 `line_amount` 取自版本快照，合计取 `version.total_amount`，费用行是同一笔钱的拆分，不额外加总；未发现“按当前价格规则重算”的新路径。明细逐行 HALF_UP 与版本合计的舍入一致性仍受 B01-03 约束，本块不重复。
- **幂等/版本**：`source_key` 按单据类型+来源；咨询锁在版本读取前；唯一冲突翻 409；请求键指纹含来源/模板/附加字段；生成失败释放占位。未发现新的“双击覆盖旧文件”路径；B07-04 的合同侧 request_key 可选不在本文件。
- **快照冻结**：`build_quote_doc` 的客户/联系人/有效期/条款/单位只读版本快照，缺项进 gaps；`_snapshot_for_storage` 对 `customer_name` 用“有没有 frozen 块”判回退，不再拿当前客户名补历史。未发现新的“历史文件被当前资料改写”。
- **事务/磁盘**：`_archive_original` 先落盘再登记，失败删本次 key；`cleanup_pending_objects` 先 rollback 再按 object_key 查 files，查得到保留；`commit_doc_generation` 提交失败精确删本次对象。**`_cancel_safe` 的重试缺陷与 B08-04 同源，本文件 `bizdoc/service.py:1625` 也有同一实现**，不另立编号。
- **状态**：`_persist` 区分 `allow_draft`（模板缺变量）与 `draft_reason`（报价未过审批/主数据/运费），两者独立；草稿不可作废；`void_doc` 不改内容与校验值。未发现“已作废还能出正式件”的新路径。
- **范围/序列化**：`list_docs` 按 `doc_type` 权限 + 来源草稿范围 + 负责人范围过滤；`serialize_doc` 不回传完整 `input_snapshot`，只给摘要、gaps、draft 原因。未发现新的越权读。

### 2. `backend/app/modules/approval/model.py`（99 行，已读全文）

**我检查了什么**：审批定义/实例/记录的列长度、状态默认值、索引、规则版本表、`ApprovalRule` 的草稿/发布字段。

**结果如何**：
- 实例 `status` 是 16 字符自由字符串，业务写入固定 pending/approved/rejected/withdrawn；记录 `action` 也是 16 字符自由字符串，写入 approve/reject/transfer/withdraw/submit。
- `ApprovalDefinition` 的 `name` 128、`business_type` 64、`status` 16；`ApprovalRule.description` 255；`ApprovalRuleVersion` **只有普通索引，没有 `(rule_id, version_no)` 唯一约束**（B09-04）。
- 未发现金额列（审批不存金额，金额取报价版本）。状态机由 router 控制，模型层没有数据库 CHECK；不是新增问题。

### 3. `backend/app/modules/approval/schema.py`（48 行，已读全文）

**我检查了什么**：转交/撤回/定义创建/定义更新的字段长度、状态枚举、`to_user_id` 边界。

**结果如何**：
- `ApprovalTransfer.to_user_id` 没有 `gt=0`，负 id 会走到 `session.get` 返回 404；不是越权，只是错误文案。
- `ApprovalDefinitionCreate` 的 `code/name/business_type` 有长度上限，`status` 没有；`ApprovalDefinitionUpdate` 的 `name/business_type/status` **全部没有长度上限**，与模型 128/64/16 不对齐（B09-03）。
- `config_json` 是无结构 dict，未做 schema 校验；审批定义配置可以存任意 JSON；不是金额错误，暂不单列。

### 4. `backend/app/modules/approval/access.py`（43 行，已读全文）

**我检查了什么**：当前节点资格；转交接收人；会签权限；申请人回避；管理员绕过；`current_assignee_id` 类型异常。

**结果如何**：
- 会签节点只认 `quote:finance_approve` 或 admin，申请人不能批自己；普通节点要求 `quote:approve` + 价格权限 + `node_role_codes`；`current_assignee_id` 非本人时拒绝，由 `approval_denial` 统一返回原因。
- `check_assignee=False` 只用于“拟转交接收人”校验，避免当前指派把候选人排除；这里仍保留角色/价格/申请人检查。
- 未发现“转交后原审批人还能批”或“会签角色快照绕过”的新路径；B07 的 OA 审批关联问题不在此文件。

### 5. `backend/app/modules/approval/router.py`（743 行，已读全文）

**我检查了什么**：审批列表/详情范围；待办筛选；审批/驳回/转交/撤回；审批定义创建/修改；审计；金额出参；旧版本/新版本竞态。

**结果如何**：
- `list_approvals` 先按报价单 `draft_scope` + 负责人范围 + 私有草稿创建人过滤，再按 `approval_denial` 逐单筛待办；详情 `_ensure_approval_visible` 复用报价可见性。未发现“只凭 approval_id 看别人审批”的新路径。
- `_load_pending` 先锁报价版本、再锁审批实例，检查 `quote.current_version_id == version.id`；旧版本审批会被 422 挡住。审批/驳回/转交/撤回均写审计，转交还写接收人校验。
- **金额出参**：`_serialize` 的 `total_amount` 用 `float(version.total_amount)`（B09-05）；这与 B01-02 的“金额出参 float”同根，但这是审批列表/详情的独立出口，仍按本块位置记录。
- **配置边界**：`update_definition` 只 `setattr`，没有状态枚举/长度校验；与 B09-03 合并。
- 未发现“审批通过后还能改价”的新路径：审批通过只改版本/报价状态，改价仍走报价版本重新提交；B02-01 的“接受后改运费”仍是原问题。

### 6. `backend/app/modules/approval/rules_engine.py`（768 行，已读全文）

**我检查了什么**：条件字段目录；`evaluate_conditions` 的 AND/空值/类型错误；规则重叠；账期文本解析；`build_context` 的人民币口径、成本缺失、毛利率/最低毛利率、让价、逾期、客户等级；规则加载；成本脱敏。

**结果如何**：
- **安全设计基本到位**：缺成本/成本不完整/产品收入≤0 时毛利率为 None，规则不命中；外币报价在 `build_context` 直接拒绝；逾期按余额+到期日；成本敏感字段有 `strip_cost_context` 和 `strip_approval_summary` 两条出口。
- **但规则上下文仍把金额/毛利/让价转 float**：`total_cny = float(...)`、`gross_margin = float(...)`、`concession = float(...)`，随后与 JSON 阈值比较。这会把“金额阈值/毛利阈值/让价阈值”的边界判断交给二进制浮点，和全项目 Decimal 口径不一致；`auto_pass` 命中会直接免审（B09-02）。
- **版本加载**：`load_active_rules` 逐条查 `ApprovalRuleVersion`，N+1，但规则数量小；不是金额错误，不单列。
- **账期解析**：注释里记录了三轮返修，当前实现覆盖“账期2个月”“余款60天后付清”“预付款5000元”等反例；未发现新的绕过。`conditions_overlap` 对数值只处理 gte/gt/lte/lt，目录里数值也没有 eq/in，口径一致。

### 7. `backend/app/modules/approval/rules_router.py`（516 行，已读全文）

**我检查了什么**：规则 CRUD/发布/启停/删除；条件与操作符校验；规则冲突；沙盒数据范围与成本脱敏；版本快照；审计；并发锁。

**结果如何**：
- 规则发布用 PG advisory lock + 新版本快照；草稿不生效；启用前校验已发布版本；发布前 `confirm` 明示“之后提交的报价按新规则”；冲突检测覆盖“条件重叠且审批结果不同”。
- 沙盒先走 `get_visible_version/get_visible_quote`，再按 `price:manage` 擦除 `gross_margin/min_item_margin` 与 `conditions_detail.actual`；未发现新的成本反推出口。
- **边界缺口**：`RulePayload.description` 没有长度上限，模型是 `String(255)`；`description` 超长会在 create/update flush 时 500（B09-03）。
- **版本唯一性**：`ApprovalRuleVersion` 没有 `(rule_id, version_no)` 唯一约束，发布只靠 PG 咨询锁；非 PG/锁失效时可能写出重复版本号，`load_active_rules` 的 `limit(1)` 取哪条不确定（B09-04）。
- 删除规则先删版本再删父表，审计保留 before；未发现“删规则影响历史审批”的新问题（历史 summary 存的是名称/版本快照）。

### 8. `backend/app/modules/oa/business_gates.py`（62 行，已读全文）

**我检查了什么**：正式动作必须拿最新同序审批；询价报价阶段；打样阶段；合同阶段；`release_stage` 与整体状态的关系；无 OA 配置时的降级。

**结果如何**：
- `require_latest_approved` 先按 `submit_round desc, id desc` 取最新同序审批，再按 `inquiry_version`/provider 过滤，能挡住“旧版本审批解锁新版本”。
- **阶段缺失时降级为“整体 approved”**：`milestone` 不是 dict 时 `allowed = bool(row and row.status == 'approved')`；`require_inquiry_items_approved` 同样在 `quotation` 不是 dict 时回退到 `row.status == 'approved'`。这会让“整体已通过、但某阶段快照缺失/未同步”的单子通过该阶段闸门（B09-01）。
- 无 OA 配置时，普通 SKU 报价直接放行；定制询价只有在“已有 OaInstance 或配置了模板”时才强制，口径与注释一致。

### 9. `backend/app/modules/oa/client.py`（180 行，已读全文）

**我检查了什么**：HMAC 签名串（时间戳/方法/路径/body 哈希）；请求体字节与签名一致；超时/连接断/5xx 的“未知结果”分类；4xx 业务错误；历史审批连接；multipart 上传；模板列表。

**结果如何**：
- 签名路径含 `/api/v1/integrations/crm` 前缀与 query，body 用紧凑 JSON 原字节；时间戳+HMAC，不用用户密码；超时/网络/5xx 抛 `OaUnknownOutcome`，不会当“没发出去”；4xx 返回 OA message。
- **multipart 文件名的 `"`/换行未转义**（`_multipart` 直接拼 `filename="{file_name}"`），文件名来自上传记录；若上游未清洗，可能破坏 multipart 边界/头部。属于集成层输入边界，暂未定级（见待确认 B09-P1）。
- 未发现密钥/签名写入日志；`OaUnknownOutcome` 只带异常类名，不带 URL/密钥。

### 10. `backend/app/modules/oa/contract_form.py`（142 行，已读全文）

**我检查了什么**：合同 OA 模板选择；锁定字段；默认值映射；双方主体联系人；附件选择与归属校验；外部上传；字段白名单。

**结果如何**：
- `locked_fields` 固定申请人/部门/合同名/金额/主体/合同编号等，`apply_extra_fields` 只允许模板定义中未锁定的字段 + 双方联系人；附件必须是当前合同 `BusinessFile` 下的 `FileRecord`，并校验磁盘存在与大小。
- `submission_form` 的金额取 `service._contract_amount`，无金额时类型“无金额”；主体、联系人、合同编号默认值按模板映射；未发现新的金额口径错误。
- **附件逐个上传外部 OA，没有批次回滚**：第 2 个失败时第 1 个已上传，OA 侧留下孤儿附件；属外部副作用，见待确认 B09-P2（是否由 OA 侧暂存/清理承担需确认）。
- `active_template` 对 OA 模板字段 `version` 直接 `max`；外部返回缺 `version` 会 500，属外部契约边界，暂不单列。

## 二、本块发现清单

### B09-01｜P1｜OA 阶段快照缺失时，阶段闸门降级成“整体审批已通过”，可绕过阶段审批

- **位置**：`backend/app/modules/oa/business_gates.py:15-16`（`require_latest_approved`）、`:37-38`（`require_inquiry_items_approved`）
- **触发条件**：调用方传了 `release_stage`（打样/合同），但最新 `OaInstance.result` 里没有 `business_stages[release_stage]`，或该值为 None/非 dict；同时 `OaInstance.status == "approved"`。典型是老数据同步、OA 侧只回整体状态、或阶段字段被清空。
- **后果**：`milestone` 不是 dict 时直接走 `bool(row and row.status == "approved")`，阶段闸门变成整体闸门；打样可以在“sample 阶段未通过”时放行，合同可以在“合同阶段未通过”时放行。询价同理：`quotation` 不是 dict 时回退 `row.status == approved`，定制询价的报价阶段未通过也能正式报价。测试只覆盖了“`business_stages` 存在且 milestone 为 pending/approved”的情况，没有覆盖缺失。
- **证据代码**：
  ```python
  milestone = (...).get(release_stage) if row and release_stage else None
  allowed = (row.status in {'pending', 'approved'} and milestone.get('status') == 'approved') \
      if isinstance(milestone, dict) else bool(row and row.status == 'approved')
  ```
  ```python
  quotation = ((row.result or {}).get('business_stages') or {}).get('quotation') if row else None
  approved = (quotation.get('status') == 'approved') if isinstance(quotation, dict) \
      else bool(row and row.status == 'approved')
  ```
- **建议**：`release_stage` 一旦传入，阶段快照缺失就应**拒绝**（或显式标记“老数据兼容”并记录告警），不能静默降级；把 `milestone is None` 与 `milestone.status` 分开处理。补“result 无 business_stages / stages 缺 key / stage 非 dict”三类测试。
- **测试覆盖**：未覆盖。`backend/tests/test_oa_business_gates.py:88-105` 只测 `business_stages.sample` 存在时的 pending/approved；`test_oa_sample_inquiry_flow.py:444` 也总是给 stage 字典。

### B09-02｜P1｜审批规则上下文把总额/毛利率/让价转 float，阈值边界可误命中免审

- **位置**：`backend/app/modules/approval/rules_engine.py:439`（`total_cny = float(...)`）、`:495`（`gross_margin = float(...)`）、`:502-503`（`min_item_margin` float）、`:536-539`（`concession = float(...)`）
- **触发条件**：提交审批时 `route()` 调 `build_context`，规则条件是金额/毛利率/最低毛利率/让价阈值（如“总额 ≤ 100000 免审”“毛利率 ≥ 30% 免审”“让价 ≤ 1000 走极速通道”）。
- **后果**：规则上下文把 Decimal 金额和比率转成二进制 float 再与 JSON 阈值比较，可能在不同数量级/边界上产生 `99999.999999`、`30.0000000001` 之类的表示误差。命中 `auto_pass` 会**直接免审**，命中 `express` 会跳过高层级；这不是展示精度问题，是审批路由错误。项目口径是金额全程 Decimal，审批规则属于金额决策链，必须同口径。
- **证据代码**：
  ```python
  total_cny = float(to_cny(version.total_amount or Decimal(0)))
  ...
  gross_margin = float((revenue_for_margin - total_cost) / revenue_for_margin * 100)
  ...
  concession = max(0.0, float((prev_version.total_amount or 0) - (version.total_amount or 0)))
  ```
  规则求值处 `hit = actual >= expected` / `<=`，`actual` 已是 float。
- **建议**：`build_context` 保留 Decimal（或统一用“分”整数）到 `evaluate_conditions`；毛利率按明确的小数位 quantize 后再比较；阈值也转 Decimal。补边界测试：`99999.99/100000.00`、`29.995/30.00`、`999.99/1000.00`，断言路由结果不受浮点尾差影响。
- **测试覆盖**：未覆盖边界。`backend/tests/test_approval_rules_engine.py:28-39` 只用 `50000`/`24.9` 这类安全值；没有 Decimal 边界/免审断言。

### B09-03｜P2｜审批定义与规则描述的入参长度没有和数据库列对齐，超长直接 500

- **位置**：`backend/app/modules/approval/schema.py:42-47`（`ApprovalDefinitionUpdate`）、`:28-31`（`ApprovalDefinitionCreate.status`）；`backend/app/modules/approval/rules_router.py:33-40`（`RulePayload.description`）；列定义 `approval/model.py:15-17`（128/64/16）、`:75`（255）
- **触发条件**：`settings:manage` 用户 PATCH 审批定义，`name` 129+、`business_type` 65+、`status` 17+；或创建/更新规则时 `description` 256+。Pydantic 没有 `max_length`，flush 时数据库 `String(128/64/16/255)` 抛截断/超长错误。
- **后果**：接口 500，配置未保存；审批定义/规则的审计也不会写（flush 失败）。用户看到的是数据库错误，不是“最多 128/64/16/255 字”。`status` 还是自由字符串，可以写入任意值（目前只影响列表展示，不影响已建实例）。
- **证据代码**：
  ```python
  class ApprovalDefinitionUpdate(PatchModel):
      name: str | None = None
      business_type: str | None = None
      status: str | None = None
  class RulePayload(BaseModel):
      name: str = Field(min_length=1, max_length=128)
      description: str | None = None
  ```
- **建议**：给 `name/business_type/status/description` 加与列一致的 `Field(max_length=...)`；`status` 改成 `Literal["active","disabled"]`（或项目实际枚举）；补 129/65/17/256 字的 422 测试。
- **测试覆盖**：未覆盖。`backend/tests/test_approval_rule_conflicts.py` 只测条件冲突；`test_cosign_permission.py` 只测权限；没有长度边界。

### B09-04｜P3｜规则发布快照没有 `(rule_id, version_no)` 唯一约束，只靠 PG 咨询锁防重

- **位置**：`backend/app/modules/approval/model.py:80-90`；`backend/app/modules/approval/rules_router.py:91-93`（锁）、`:430-456`（发布写版本）
- **触发条件**：两个发布请求并发，或数据库不是 PostgreSQL / 咨询锁未生效；两个事务都读到同一个 `published_version_no`，各自插入 `V(n+1)`。
- **后果**：`approval_rule_versions` 出现同一 `(rule_id, version_no)` 两行；`load_active_rules` 按 `rule_id + version_no` 查询并 `limit(1)`，取哪条不确定；审批留痕里的 `rule_version_no` 相同但 payload 可能不同，事后无法回答“当时按哪版规则走的”。当前生产 PG 有事务级咨询锁，实际触发概率低，但数据库层没有兜底。
- **证据代码**：
  ```python
  __table_args__ = (Index("ix_approval_rule_versions_rule", "rule_id"),)
  ...
  new_no = rule.published_version_no + 1
  session.add(ApprovalRuleVersion(rule_id=rule.id, version_no=new_no, payload=_snapshot(rule), ...))
  ```
- **建议**：加 `UniqueConstraint("rule_id", "version_no", name="uq_approval_rule_version")`；发布冲突时翻 409，而不是靠锁“应该不会撞”。补并发发布测试（至少唯一约束建表断言）。
- **测试覆盖**：未覆盖。没有并发发布/唯一约束测试；迁移 `a5c8e2f7b9d1` 也只建普通索引。

### B09-05｜P2｜审批列表/详情把报价总额转 float 输出（与 B01-02 同根，独立出口）

- **位置**：`backend/app/modules/approval/router.py:96`
- **触发条件**：`GET /approvals` 或 `GET /approvals/{id}` 序列化含报价版本的实例。
- **后果**：`total_amount` 以 JSON number 返回，前端/导出拿到的是 float；例如 `Decimal("0.10")` 在部分客户端显示为 `0.1`，大额加总会出现尾差。审批详情是业务确认金额的入口，金额应保持十进制字符串/精确值。B01-02 已报报价侧同类问题，本条是审批模块的独立出口，不重复其结论但需一并修。
- **证据代码**：
  ```python
  "total_amount": float(version.total_amount) if version else None,
  ```
- **建议**：输出 `format(value, "f")` 或 `str(Decimal)`，与报价接口统一“金额字符串 + 分位”的契约；前端不要再 `Number()` 后做金额计算。
- **测试覆盖**：未覆盖精确金额断言；审批测试多用整数金额，未检查 `0.10`/大额。

## 三、待确认（不作为结论）

### B09-P1｜OA multipart 上传的文件名未转义

- **位置**：`backend/app/modules/oa/client.py:150-166`
- **证据**：`_multipart` 直接拼 `filename="{file_name}"`，没有替换 `"`、CR/LF；`upload_file` 的 `file_name` 来自 `FileRecord.file_name`（用户上传的文件名）。若上游文件服务没有清洗，恶意文件名可以注入额外的 multipart 头/字段，改变 OA 侧收到的表单内容。
- **需要确认**：文件上传入口是否已统一拒绝 `"`/CR/LF（属 B12 文件模块）；若已清洗，这里只是纵深防御缺口；若未清洗，应改成 RFC 5987/转义并限制文件名长度。当前只记待确认，不定级。

### B09-P2｜合同附件逐个上传 OA，失败时前面已上传的附件没有回滚

- **位置**：`backend/app/modules/oa/contract_form.py:120-135`
- **证据**：`for row in rows:` 逐个 `client.upload_file` / `uploads.stage`，任一失败抛 `AppError`；前面已上传到 OA 的附件 ID 没有撤销/清理逻辑，`payload['attachment_ids']` 已追加。重试会再上传一遍，OA 侧可能出现重复附件。
- **需要确认**：OA 的 `upload_file` 是否只是暂存、未随审批提交就自动过期；`uploads.stage` 是否有批次清理。若都没有，应记录已上传 ID 并在失败时尽力撤回，或改成一次性批量上传。当前只记待确认。

## 四、本块结论

- **P0：0 条**。
- **P1：2 条**（B09-01 阶段闸门缺失降级、B09-02 规则引擎金额/毛利 float）。
- **P2：2 条**（B09-03 审批配置长度未对齐、B09-05 审批金额出参 float）。
- **P3：1 条**（B09-04 规则版本无唯一约束）。
- **待确认：2 条**（B09-P1 multipart 文件名、B09-P2 合同附件批量上传无回滚）。
- **未读完：无**。10 个文件逐行读完；`quote/service.py`、`payment/service.py` 只读与审批提交/逾期口径相关段作证据，不替代其所属块的全文审查。

## 五、本块没有看的部分及原因

| 范围 | 情况 | 原因 |
|---|---|---|
| `backend/app/modules/quote/service.py` | 只读 `submit_approval`/价格权限/版本提交相关段 | 属 B01/B02；本块只核对规则引擎被调用时的上下文来源 |
| `backend/app/modules/payment/service.py` | 只读 `plan_overdue_expression` 相关段 | 属 B05；本块只核对“客户有逾期应收”条件口径 |
| `backend/app/modules/oa/inquiry.py`/`sample.py`/`service.py` | 未读 | 属 B10；本块只审 OA 基础客户端、合同表单和阶段闸门 |
| `backend/app/modules/dingtalk/*` | 未读 | 属 B10/B11；本块只读 `OaInstance` 字段作为闸门证据 |
| `backend/tests` 全量 | 只读 `test_oa_business_gates.py`、`test_oa_sample_inquiry_flow.py`、`test_approval_rules_engine.py`、`test_approval_rule_conflicts.py`、`test_cosign_permission.py` 相关段 | 未运行测试；覆盖结论是“用例是否覆盖触发条件” |
| 前端审批中心/规则编辑器 | 未读 | 属 F 系列；本块只审后端接口与序列化 |
