# B10 对账·OA 业务适配 + 钉钉客户端/路由（8 文件，3,376 行）

> 审查范围：`oa/inquiry.py`（331）、`oa/sample.py`（325）、`oa/service.py`（515）、`dingtalk/attachments.py`（79）、`dingtalk/client.py`（818）、`dingtalk/model.py`（228）、`dingtalk/oa_stream.py`（132）、`dingtalk/router.py`（948）。
> 8 个文件均按本块清单读完；`oa/inquiry.py`、`oa/sample.py`、`oa/service.py` 的前半段在本块前次已读，本次续读后把 8 个文件收口。只读，未改业务代码。
> 未纳入本块：`backend/app/modules/dingtalk/service.py`（1,760 行，台账划入 B11）。本块路由/业务适配会调用它的 `create_inquiry_instance`、`sync_pending_instances`、`resolve_reviewed_instance`、`lock_oa_business_then_instance`、`sync_users`，这些函数内部的事务与幂等本块**没有审**，相关结论只到调用边界为止。

## 一、逐文件检查记录

### 1. `backend/app/modules/oa/inquiry.py`（331 行，已读全文）

**我检查了什么**：定制询价 OA 的发起/同步/撤回；`_attachments` 的图片与附件筛选、文件归属、大小校验；`start_inquiry_approval` 的业务行锁、最新 OaInstance 锁、轮次/幂等键、OA payload 金额；`apply_inquiry_summary` 的回写；审计位置；异常与本地文件读取。

**结果如何**：
- **金额**：`target_price` 由 `inquiry/schema.py` 约束为 `Decimal`、`ge=0`、2 位小数；OA payload 用 `str(inquiry.target_price or 0)`（`inquiry.py:283`），没有 float 参与，未发现负数/超大值新入口。
- **幂等/锁**：首次发起前先 `session.get(CustomInquiry, ..., with_for_update=True)`，再锁最新 `OaInstance`（`inquiry.py:193-195`），两个首次并发提交会被业务行串行化；失败/`not_sent`/`skipped` 复用同一行并递增 `attempt_count`；`needs_review` 拒绝重发。未发现新的重复发起路径。
- **附件**：`_attachments` 校验文件属于该询价、大小与库记录一致、图片仅 PNG/JPEG；上传走 `ApprovalUploads.stage`，真正外部上传延后到 `send_prepared_approval`。**但 business_type 写死为 `"inquiry"`（`inquiry.py:139-141`）**，与 DingTalk 路由可配置的 `inquiry_file_business_type` 不一致，见 B10-02。
- **状态**：发起前 `require_submittable` 限制 `open/developing`；已通过/在办/待核对均拒绝；`apply_inquiry_summary` 只写 `extra.oa_approval`，不改询价业务状态。
- **审计**：路由在 `start_inquiry_approval` 成功后写 `start_oa_approval`（`dingtalk/router.py:371-384`）；未知结果抛错时没有审计行，见 B10-10 的审计缺口说明。
- **异常**：本地文件缺失/大小不符转 422；OA 外部异常由 `send_prepared_approval` 分类。未发现异常原文直接回前端的路径（`OaError.message` 是 OA 侧文案）。

### 2. `backend/app/modules/oa/sample.py`（325 行，已读全文）

**我检查了什么**：打样 OA 发起/同步/撤回；`_attachments` 的打样申请表渲染、图片/附件分类、积分换算；`start_sample_approval` 的最新 OaInstance 锁、轮次/幂等、payload 金额、附件；`_apply_sample_summary` 的业务状态落库与阶段快照；审计位置。

**结果如何**：
- **金额/数值**：`score` 先经 `collect_template_values` 校验（测试覆盖空、`NaN`、`99991`），再 `Decimal(str(score)) / 10` 存 `estimated_hours`；没有 float 参与。OA payload 金额固定 `"0"`（`sample.py:260`），打样不走金额。
- **锁/幂等**：服务只锁最新 `OaInstance`（`sample.py:211`），但调用方 `sample/dingtalk.py:335` 已对 `SampleRequest` 做 `for_update`，API 路径不会被两个首次提交同时穿过去；这一点与 `oa/inquiry.py` 的显式业务行锁是两条实现，当前调用边界成立，不另立 P 级。
- **阶段快照**：`_apply_sample_summary` 在 `business_stages.sample` 缺失时回退 `new_status == "approved"`（`sample.py:300-301`），并把 `sample.status` 落成 approved（`sample.py:303-304`）。这是 B09-01 在“同步实际改业务状态”路径上的具体后果，本块单列 B10-01。
- **发起后状态**：OA 创建返回后无条件把 `pending/rejected` 的打样单改成 `pending`（`sample.py:274-275`），即使 OA 返回的是 approved/rejected/withdrawn；见 B10-09。
- **附件**：打样申请表 + 图片 + 其他附件逐个 `stage`；失败在 `send_prepared_approval` 统一处理；本地文件缺失转 422。
- **审计**：发起审计在调用方 `sample/dingtalk.py:395-397`，撤回/同步审计在 `dingtalk/router.py`；失败路径无审计，见 B10-10。

### 3. `backend/app/modules/oa/service.py`（515 行，已读全文）

**我检查了什么**：`ApprovalUploads.stage/fill`；`send_prepared_approval` 的“先 commit 预约、再上传、再锁回、再调 OA”；合同 OA 的 `_build_payload`/`_contract_amount`/`start_contract_approval`/`apply_contract_summary`/撤回/同步；`OaUnknownOutcome` 的 `needs_review` 分类；`apply_adopted_receipt`；审计与异常。

**结果如何**：
- **金额**：`_contract_amount` 用 `Decimal(order.total_amount)` / `Decimal(version.total_amount)`（`service.py:210-221`），OA payload 的 `contract_amount`、`amount` 都用 `f"{amount:.2f}"`（`service.py:316、330`）。金额源仍是“当前订单/报价总额”，B07-03 的“合同正文快照与 OA 金额不一致”在本块未关闭；不重复编号。
- **事务边界**：`send_prepared_approval` 先 `commit` 预约（`service.py:65`），上传附件，再 `lock_receipt` 校验业务状态，写 `form_snapshot` 后再 `commit`，然后调外部 OA（`service.py:76-88`）。上传期间状态变化的 `not_sent` 分支正确，但**外部调用返回后没有再次校验业务状态**（`service.py:105-112`），见 B10-06；上传失败时已上传的 OA 文件没有清理，见 B10-08。
- **未知结果**：`OaUnknownOutcome` 才落 `needs_review`（`service.py:94-100`）；`OaNotConfigured` 落 `not_sent`；其余 `OaError` 落 `failed`。分类本身与 B09 的台账口径一致。
- **异常**：只 `except client.OaError`（`service.py:94`）。`uploads.fill` 的 OSError 已被包成 OaError，但 `lock_receipt` 抛出的 `AppError`、或未被包装的意外异常会绕过状态落库，见 B10-10。
- **合同回写**：`apply_contract_summary` 锁 `ContractDocument` 后只写 `filled_data._dingtalk_oa`，不改合同状态；`start_contract_approval` 调外部成功后写 `doc.filled_data`（`service.py:418-447`），没有再次检查 `doc.status`，与 B10-06 同一根因。
- **审计**：本文件不写审计；成功路径由各路由补写，失败/未知结果路径没有审计，见 B10-10。

### 4. `backend/app/modules/dingtalk/attachments.py`（79 行，已读全文）

**我检查了什么**：官方 workflow/storage 1.0 附件上传三步（初始化→签名 PUT→提交）；access_token 与 unionId 获取；签名 URL 的 scheme/host 白名单；PUT 不跟随重定向；提交后的 spaceId/fileId/size 校验；异常文案。

**结果如何**：
- **越权/SSRF**：只允许 `https` 且 host 以 `.aliyuncs.com` / `.dingtalk.com` 结尾，拒绝带用户名密码的 URL，PUT 不跟随重定向、不附带 access_token（`attachments.py:56-72`）；未发现把 access_token 发到签名地址的路径。
- **完整性**：commit 后校验 `file.id`、`spaceId`、`size == len(content)`（`attachments.py:75-78`），不完整直接报“尚未发起审批”。
- **异常**：HTTP/JSON 错误多被包成 `DingTalkError`；`post` 先解析 JSON 再判状态，非 JSON 响应会抛 `DingTalkError`。未发现敏感信息写日志；错误文案只带钉钉的 code/message。
- **测试覆盖**：`backend/tests/test_inquiry_oa_attachments.py` 覆盖恶意 resourceUrl、成功上传、总闸关闭；未覆盖 commit 大小不符、PUT 5xx、非 JSON 响应。
- 未发现新的 P0/P1；本文件不单列编号。

### 5. `backend/app/modules/dingtalk/client.py`（818 行，已读全文）

**我检查了什么**：access_token 缓存与闸门；发起审批的 `create_process_instance` 及三种后果分类；查询审批 `get_process_instance` 的异常包装；模板 schema；附件 media 上传；按姓名搜人；部门/通讯录读取；扫码登录换 token/userinfo/unionId→userid；日志与错误文案。

**结果如何**：
- **发起侧**：`create_process_instance` 对取 token 失败转 `DingTalkError(http_status=400)`，明确归“没发出”；请求阶段 `httpx.HTTPError` 转 `DingTalkUnknownOutcome`；200 无 instanceId 带 `http_status=200`，归“明确拒绝”（`client.py:238-292`）。分类与 `classify_outcome` 一致。
- **查询侧**：`get_process_instance` 已把 httpx/JSON 异常包成 `DingTalkUnknownOutcome`，并修正了 `result` 包层与字段别名（`client.py:318-410`）。但 `get_process_schema`、`upload_media`、`get_user_dept_ids`、`list_sub_departments`、`list_department_users`、`userid_by_unionid` 仍是裸 `resp.json()`，异常会以 `JSONDecodeError`/`httpx` 原样穿透，见 B10-04。
- **姓名匹配**：`search_user_id_by_name` 精确匹配优先；无精确匹配且只有一个候选时直接返回该候选（`client.py:535-545`），候选姓名可能不等于请求姓名，见 B10-07。
- **日志/敏感**：错误文案带 `resp.text[:200]`、requestId，不带 token/appSecret；`oa_stream` 只记异常类型。未发现 token 写日志。
- **测试覆盖**：`test_inquiry_oa_submission.py` 用 mock 的 schema/search；`test_dingtalk_oa_state_machine.py` 覆盖状态机；未覆盖 schema 超时/非 JSON、姓名模糊匹配、部门接口失败。

### 6. `backend/app/modules/dingtalk/model.py`（228 行，已读全文）

**我检查了什么**：`OaInstance` 的列长度、状态集合、`idempotency_key`/`instance_id`/`resolve_request_key` 三层唯一性、`resolve_state` 占用、轮次与尝试时间；`OA_STATUS_ACTIONS` 与前端共用口径。

**结果如何**：
- `idempotency_key` 128、`instance_id` 128、`resolve_request_key` 128；`RequestKey.request_key` 也是 128 且 `reserve` 有长度校验，未发现超长键落库 500。
- `instance_id`、`resolve_request_key` 均是 PostgreSQL 部分唯一索引；`(inquiry_id,inquiry_version,oa_type)` 只做普通索引，允许驳回后重提开新一轮。设计自洽，未发现新的重复关联入口。
- 状态是 String(16) 自由字符串，写入值都在 `OA_STATUS_ACTIONS` 内；没有数据库 CHECK，属于结构层宽松，非本块新增风险。
- 未发现金额字段；OA 金额只在 payload/result 里，模型不存。

### 7. `backend/app/modules/dingtalk/oa_stream.py`（132 行，已读全文）

**我检查了什么**：Stream 连接建立与鉴权；事件类型过滤；只处理 CRM 已关联实例；`processCode` 不匹配即忽略；同步失败不 ACK 让钉钉重投；日志是否带事件正文/票据；断线重连。

**结果如何**：
- 连接只接受 `wss` 且 host 以 `.dingtalk.com` 结尾，ticket 经 `quote` 拼 URL（`oa_stream.py:104-108`）；未发现把 ticket 写日志。
- `process_event` 只按 `instance_id` 查 `OaInstance`，不把事件正文里的通过/驳回直接当结论，而是调 `sync_pending_instances` 重新查询（`oa_stream.py:33-53`）；`processCode` 与行不一致直接忽略。
- 失败时 ACK 返回 `STATUS_SYSTEM_EXCEPTION`，让钉钉重投；日志只记异常类型（`oa_stream.py:75-83`）。未发现吞异常后假成功。
- 未发现新的 P0/P1；本文件不单列编号。

### 8. `backend/app/modules/dingtalk/router.py`（948 行，已读全文）

**我检查了什么**：询价 OA 发起（自研 OA/旧钉钉两条路径）；提交必填项；补充字段与受保护字段；报价员选择；提报人/部门；产品参考图片签名 URL；附件选择与上传；人工核定 adopt/resend/abandon；同步/撤回；OA 列表范围；健康检查；用户同步；提交选项。

**结果如何**：
- **权限/范围**：`start_inquiry_approval` 用 `inquiry_service.get_visible_or_404`；`ensure_oa_type_scope` 按 `oa_type` 分别走 inquiry/sample/contract 的可见性；未知类型 404。未发现“改 oa_id 看别人审批”的新路径。
- **金额**：`INQUIRY_FIELDS.target_price` 用 `str(...)`；OA payload 不含 float 计算。图片签名 URL 用 `jwt_secret` HMAC，但域名写死（B10-05）。
- **幂等**：旧钉钉路径的 `svc.create_inquiry_instance` 内部幂等未审（B11）；自研 OA 路径由 `oa/inquiry.py` 的业务行锁 + 最新 OaInstance 锁兜住。人工核定用 `request_key` + 行级占用，`resend` 对自研 OA 直接 409。
- **报价员**：只在 `payload.quote_owner_user_id` 显式传入时校验白名单；省略时直接使用 `inquiry.oa_quote_user_id`，见 B10-03。
- **审计**：发起/撤回/核定/用户同步成功路径写审计；`send_prepared_approval` 抛错时没有审计，见 B10-10。
- **异常**：旧钉钉路径的附件上传把 `DingTalkError/httpx/OSError/ValueError` 转 422；但 `_submission_fields` 拉 schema 的异常未包装（B10-04），会 500。
- **未发现的新问题**：`ensure_oa_type_scope` 对 contract 的 `contract_scope_owner_id` 判据、`resolve` 对 self_oa adopt 的 `external_ref` 校验、`sync` 的 3 秒快照复用，本块未找到可复现的越权/重复入账证据。

## 二、发现清单

| 编号 | 严重度 | 位置 | 一句话 | 状态 |
|---|---|---|---|---|
| B10-01 | P1 | `oa/sample.py:300-304` | 打样 OA 同步缺 `business_stages.sample` 时按整体 approved 落成打样 approved | 已确认（测试反而固化该回退） |
| B10-02 | P2 | `oa/inquiry.py:139-141`、`dingtalk/router.py:203、317、946` | 自研 OA 附件写死 `business_type="inquiry"`，忽略可配置的 `inquiry_file_business_type` | 已确认（测试只覆盖默认值） |
| B10-03 | P2 | `dingtalk/router.py:240-245` | 省略 `quote_owner_user_id` 时，历史 `inquiry.oa_quote_user_id` 绕过报价员白名单 | 已确认（测试只覆盖显式传参） |
| B10-04 | P2 | `dingtalk/client.py:413-434、442-465、550-566、582-604、607-635、753-780` + `router.py:51-54` | 多个钉钉读接口裸 `resp.json()`，超时/非 JSON 直接 500 | 已确认（无故障注入测试） |
| B10-05 | P2 | `dingtalk/router.py:331-338` | 产品参考图片 URL 写死 `https://crm.mandla.cn`，换域名/环境即取不到图 | 已确认（无多环境测试） |
| B10-06 | P2 | `oa/service.py:76-88、105-112、418-447` | 外部 OA 调用返回后不再校验业务状态，期间取消/改状态仍会回写审批结果 | 已确认（无并发测试） |
| B10-07 | P2 | `dingtalk/client.py:535-545` | 姓名搜索无精确匹配且仅一个候选时直接采用，候选姓名可能不是本人 | 已确认（无模糊匹配测试） |
| B10-08 | P3 | `oa/service.py:74-83` | 附件已上传 OA 后业务状态变化/后续失败，无清理，留下孤儿文件 | 已确认（无清理测试） |
| B10-09 | P3 | `oa/sample.py:274-278` | 打样 OA 创建返回 approved/rejected/withdrawn 时仍把本地打样单置 pending | 已确认（测试只断言 sync 后状态） |
| B10-10 | P3 | `oa/service.py:94-100` + `dingtalk/router.py:371-384` + `contract/router.py:130-140` | 只捕 `OaError`；意外异常会留下 `submitting`，失败/未知结果无审计 | 已确认（无故障注入/审计断言） |
| B10-P1 | 待确认 | `oa/inquiry.py:269-285` | 非 native 询价也无条件要求产品参考图片，并硬编码 `DDPhotoField_1U7CNXCKQNS00`；是否所有模板都要求图片待确认 | 待确认（缺模板契约） |

## 三、逐条证据与建议

### B10-01（P1）打样 OA 缺阶段快照时把打样单落成 approved

**位置**：`backend/app/modules/oa/sample.py:300-304`。

**触发条件**：OA 返回 `status="approved"`，但 `summary["business_stages"]["sample"]` 缺失（旧实例、第三方 OA 不返回阶段、或阶段字段被裁剪）。

**后果**：`_apply_sample_summary` 走到 `released = new_status == "approved"`，随后把 `sample.status` 从 `pending` 改成 `approved`。打样业务状态没有“打样阶段已通过”的证据却被放行，后续依赖打样 approved 的闸门/发货/生产节点会继续；这是 B09-01（读取闸门降级）在“实际落库”路径上的放大版。

**证据代码**：
```python
milestone = (summary.get("business_stages") or {}).get("sample")
released = (new_status in {"pending", "approved"} and milestone.get("status") == "approved") if isinstance(milestone, dict) else new_status == "approved"
...
if sample.status == "pending" and sample_status in {"approved", "rejected", "withdrawn"}:
    sample.status = "rejected" if sample_status == "withdrawn" else sample_status
```

**测试覆盖**：`backend/tests/test_oa_sample_inquiry_flow.py:156-159` 直接调用 `_apply_sample_summary(session, new, {"id": "new-approval", "status": "approved"})`（不带 `business_stages`），并断言 `child.status == "approved"`。也就是说，**测试覆盖了这个回退，但把“缺阶段也批准”写成了期望行为**；没有覆盖“缺阶段应转待核对/不落 approved”的反向用例。

**建议**：`business_stages.sample` 缺失时不要释放业务状态，改为把行落 `needs_review`（或至少不改 `sample.status`），并把“缺阶段”作为人工核对项；同时把现有测试的期望改成“缺阶段不批准”。

### B10-02（P2）自研 OA 附件写死 business_type，忽略可配置项

**位置**：`backend/app/modules/oa/inquiry.py:139-141`；配置使用点在 `backend/app/modules/dingtalk/router.py:203、317、946`。

**触发条件**：设置项 `dingtalk_oa.inquiry_file_business_type` 不是默认 `"inquiry"`（例如按客户/租户分目录），且走自研 OA 询价路径。

**后果**：`start_inquiry_approval` 调 `_attachments` 时只查 `BusinessFile.business_type == "inquiry"`，看不到实际业务类型下的附件/图片；图片分支会直接报“请先上传产品参考图片”，附件分支会报“所选附件不属于这条询价”。用户在当前配置下无法发起自研 OA 询价，且错误文案指向“没上传”，不是“类型配置不匹配”。

**证据代码**：
```python
stmt = select(FileRecord).join(BusinessFile, ...).where(
    BusinessFile.business_type == "inquiry", BusinessFile.business_id == inquiry.id)
```
而旧钉钉路由同一件事用的是：
```python
BusinessFile.business_type == ((cfg or {}).get("inquiry_file_business_type") or "inquiry")
```

**测试覆盖**：`test_inquiry_oa_submission.py` 的 fixture 默认 `inquiry`，没有自定义 `inquiry_file_business_type` 的自研 OA 用例。

**建议**：把 `_attachments` 的 business_type 作为参数传入（自研 OA 路径从配置/模板取），或在 `oa/inquiry` 统一读 `settings`/模板的 `file_business_type`；补一个非默认类型的用例。

### B10-03（P2）省略报价员参数时历史 `oa_quote_user_id` 绕过白名单

**位置**：`backend/app/modules/dingtalk/router.py:240-245`。

**触发条件**：`quote_owner_component` 已配置；请求不传 `quote_owner_user_id`；`inquiry.oa_quote_user_id` 是历史值或已被改成不在当前 `quote_owners` 列表里的人。

**后果**：代码只在 `if payload.quote_owner_user_id:` 分支校验 `allowed_owners`；省略参数时 `quote_owner = inquiry.oa_quote_user_id` 直接写进 OA 表单。审批会按一个当前配置之外的“对接报价员”流转，轻则审批卡住/被拒，重则把询价单送到错误的人；而前端提交界面此时会显示 `selected_quote_owner`（另一条路径也直接回原值，不校验）。

**证据代码**：
```python
quote_owner = payload.quote_owner_user_id or inquiry.oa_quote_user_id
if payload.quote_owner_user_id:
    allowed_owners = {...}
    if quote_owner not in allowed_owners:
        raise AppError(...)
```

**测试覆盖**：`test_inquiry_oa_submission.py:85` 覆盖“显式传非法 owner”被拒；没有“省略参数 + 存量 owner 非法”的用例。

**建议**：无论 owner 来自请求还是存量，都统一与 `allowed_owners` 比对；存量非法时要求重新选择，不要静默发送。

### B10-04（P2）钉钉读接口裸解析响应，异常直接 500

**位置**：
- `backend/app/modules/dingtalk/client.py:413-434`（`get_process_schema`）
- `client.py:442-465`（`upload_media`，当前无调用点）
- `client.py:550-566`（`get_user_dept_ids`）
- `client.py:582-604`（`list_sub_departments`）
- `client.py:607-635`（`list_department_users`）
- `client.py:753-780`（`userid_by_unionid`）
- 入口：`backend/app/modules/dingtalk/router.py:51-54`（`_submission_fields` 对 `schemaContent` 再做 `json.loads`）

**触发条件**：钉钉/WAF 返回 HTML 错误页、网关超时、空 body 或非 JSON；或 `schemaContent` 是坏 JSON 字符串。

**后果**：`httpx.TimeoutException`/`json.JSONDecodeError`/`AttributeError` 不会被包成 `DingTalkError`，路由层 `except DingTalkError` 接不住，最终 500。具体到询价发起：`start_inquiry_approval` 会两次调 `_submission_fields`，第一次 schema 超时就直接 500，用户看不到“外部暂不可用/可先保存草稿”的可操作提示；部门接口失败同理会把发起流程打成 500。

**证据代码**：
```python
async with httpx.AsyncClient(timeout=20) as http:
    resp = await http.get(...)
data = resp.json() if resp.content else {}
if resp.status_code != 200: ...
```
没有 `try/except httpx.HTTPError`，也没有 `try/except ValueError`；`_submission_fields` 的 `content = json.loads(content)` 同样裸奔。

**测试覆盖**：`test_inquiry_oa_submission.py` 全部 mock `get_process_schema` 成功；`test_inquiry_oa_attachments.py` 只覆盖附件上传。没有超时/非 JSON/部门接口失败的故障注入。

**建议**：把读接口统一成和 `get_process_instance` 相同的包装（httpx→`DingTalkUnknownOutcome` 或带 http_status 的 `DingTalkError`，JSON→`DingTalkError`）；`_submission_fields` 对坏 schema 抛明确 502/422，而不是 500。

### B10-05（P2）产品参考图片 URL 写死生产域名

**位置**：`backend/app/modules/dingtalk/router.py:331-338`。

**触发条件**：部署域名不是 `crm.mandla.cn`（测试环境、内网、客户私有化、端口/路径变化），或该域名对钉钉不可达。

**后果**：旧钉钉询价模板的“产品参考图片”是必填图片控件；代码把 `https://crm.mandla.cn/api/v1/public/inquiry-images/...` 写进表单，钉钉侧取不到图，提交被拒或图片控件为空。用户会看到“已上传图片但提交失败”，且换环境必现。

**证据代码**：
```python
image_url = f"https://crm.mandla.cn/api/v1/public/inquiry-images/{image_file_id}?expires={expires}&sig={signature}"
```

**测试覆盖**：没有对 `image_component`/URL 域名的测试；`test_signature_input_boundaries.py` 只测公开图片接口的签名边界。

**建议**：域名从 `settings`（如 `public_base_url`/`crm_base_url`）取，拼 URL 时做 `urljoin`；补一个非默认域名的用例。另确认 7 天签名 URL 的公开范围是否符合产品图保密要求。

### B10-06（P2）外部 OA 返回后不再校验业务状态

**位置**：`backend/app/modules/oa/service.py:76-88`（发送前校验）、`105-112`（外部返回后只 `lock_receipt`，不再比对 `business_status`）、`418-447`（合同发起成功后写 `doc.filled_data`）。

**触发条件**：`create_approval` 外部调用期间（最长 15 秒+），另一请求把合同取消/删除、把打样单撤回、把询价归档；或另一管理员改了业务状态。

**后果**：发送前 `fresh_business.status == business_status` 的检查通过；但外部调用返回后，`send_prepared_approval` 只重新锁行、检查 `current.status=="submitting"` 和 `attempt_count`，**不再比业务状态**。随后 `start_contract_approval` 只要 `doc.deleted_at is None` 就把 `_dingtalk_oa` 写进已取消/已变更的合同；OA 里已通过的审批会挂到一个不再有效的业务对象上，后续签署/回签/通知按错误状态走。打样/询价同理：状态被并发改掉后仍会写 `source_context`/`extra`。

**证据代码**：
```python
fresh_business, current = await lock_receipt()
if (fresh_business is None or fresh_business.deleted_at is not None
        or fresh_business.status != business_status ...):
    ...
summary = await client.create_approval(payload)
...
fresh_business, current = await lock_receipt()
return fresh_business, current, summary
```
返回后没有 `fresh_business.status != business_status` 的判断。

**测试覆盖**：没有并发/状态变更注入用例；`test_oa_contract_flow.py` 只覆盖顺序发起。

**建议**：外部返回后再次比对 `business_status`/`superseded_at`/`deleted_at`；不一致时把 `row.status` 落 `needs_review`（OA 可能已建单），不要回写业务；同时给合同/打样/询价各补一个“外部调用期间取消”的并发用例。

### B10-07（P2）姓名搜索只有一个候选时可能绑错人

**位置**：`backend/app/modules/dingtalk/client.py:535-545`。

**触发条件**：`search_user_id_by_name(name)` 的响应没有姓名匹配（例如接口只回 userid，或模糊搜索只回一个姓名不同的人），且候选数为 1。

**后果**：函数返回这个唯一候选的 userid，调用方把它当发起人/部门查询/审批人使用。审批单会挂在错误的人名下，后续按发起人做的撤回、权限判断、通知都会错位；这比“找不到人”更隐蔽，因为接口看似成功。

**证据代码**：
```python
for userid, user_name in candidates:
    if user_name == requested:
        return userid
return candidates[0][0] if len(candidates) == 1 else None
```

**测试覆盖**：`test_inquiry_oa_submission.py` 把 `search_user_id_by_name` mock 成固定 `searched-id`；没有“单候选姓名不匹配”的用例。

**建议**：无精确姓名匹配时，即使只有一个候选也返回 None（要求管理员绑定/人工确认）；若确实要兼容“接口只回 userid”的老响应，至少要求 `user_name is None` 才允许单候选。

### B10-08（P3）附件先上传后状态变化，孤儿文件无清理

**位置**：`backend/app/modules/oa/service.py:74-83`。

**触发条件**：`uploads.fill` 已把附件逐个上传到 OA；随后 `lock_receipt` 发现业务状态变化/删除，走 `current.status="not_sent"` 分支；或上传到一半失败。

**后果**：OA 侧已产生文件（占空间、可能被管理员在 OA 文件库看到），CRM 没有对应审批单，也没有清理/补偿逻辑；重试会再上传一份。不是金额错误，但属于外部副作用半成品。

**证据代码**：
```python
payload = await uploads.fill(payload)      # 逐个 upload_file，已产生外部文件
fresh_business, current = await lock_receipt()
if (... status != business_status ...):
    current.status = "not_sent"
    current.error = "上传期间原单状态已变化，本次未发起 OA"
    await session.commit()
    raise AppError(...)
```
`except client.OaError` 也没有对已上传文件做删除。

**测试覆盖**：没有“上传后状态变化”的用例；`test_oa_*` 多用 mock `_attachments`，不触发真实上传。

**建议**：记录本次已上传的 file id，失败时调用 OA 删除/至少登记待清理；或在状态检查通过后再上传（但要注意长事务/锁）。若 OA 不支持删除，至少在错误文案里说明“附件可能已上传，请管理员核对”。

### B10-09（P3）OA 创建返回非 pending 时本地打样单仍置 pending

**位置**：`backend/app/modules/oa/sample.py:274-278`。

**触发条件**：`create_approval` 返回 `status="approved"/"rejected"/"withdrawn"`（例如 OA 自动通过、模板无审批人、外部即时拒绝），且本地 `sample.status` 为 `pending` 或 `rejected`。

**后果**：`start_sample_approval` 无条件把 `sample.status` 改成 `pending`，与 `row.status`（approved/rejected/withdrawn）不一致；用户看到“审批中”，实际 OA 已结束，要等下一次同步才纠正。若同步失败/未调度，打样单会长期卡在 pending。

**证据代码**：
```python
if sample is not None and sample.deleted_at is None and sample.status in {"pending", "rejected"}:
    sample.status = "pending"
```

**测试覆盖**：`test_oa_sample_inquiry_flow.py:247` 让 `create` 返回 `status="approved"`，但断言发生在 `sync_sample_row` 之后（`292`），没有断言“发起返回 approved 时本地立即是什么状态”。

**建议**：按 `summary["status"]` 映射本地状态：approved→approved（或至少 needs_review 等同步确认），rejected/withdrawn→对应状态；不要无条件 pending。

### B10-10（P3）只捕 OaError；意外异常留下 submitting 且失败路径无审计

**位置**：`backend/app/modules/oa/service.py:94-100`；审计在 `dingtalk/router.py:371-384`、`contract/router.py:130-140`、`sample/dingtalk.py:395-397`。

**触发条件**：`uploads.fill`/`lock_receipt`/`create_approval` 之间出现非 `OaError` 的异常（例如 `lock_receipt` 的 `AppError`、第三方库的 `KeyError`/`TypeError`），或 `write_audit` 在外部调用成功后失败。

**后果**：`send_prepared_approval` 只处理 `client.OaError`；意外异常直接抛给路由，`row.status` 可能停在已 commit 的 `submitting`，要等 10 分钟僵死降级才能重试；同时路由的 `write_audit` 在 `start_*` 返回后才执行，外部 OA 已建单但审计缺失，事后无法从审计表还原“谁在何时发起了哪张单”。

**证据代码**：
```python
except client.OaError as exc:
    ...
fresh_business, current = await lock_receipt()
return fresh_business, current, summary
```
路由：
```python
row, message = await oa_inquiry.start_inquiry_approval(...)
await write_audit(...)
await session.commit()
```

**测试覆盖**：没有非 `OaError` 故障注入；没有“审计写失败但外部已发起”的用例。

**建议**：`send_prepared_approval` 加 `except BaseException` 的补偿分支（至少把当前 row 落 `needs_review`/`failed` 并保留原始异常）；审计改为在 service 内、外部调用前写“attempt”或在外部返回后立即写，失败路径也写一条带 `error_class` 的审计。

### B10-P1（待确认）非 native 询价也无条件要求产品参考图片

**位置**：`backend/app/modules/oa/inquiry.py:269-270`、`283-285`。

**触发条件**：走自研 OA 但模板不是 native 询价模板（`uses_native_inquiry()` 为 false），且该模板没有“产品参考图片”必填控件；或模板控件 id 不是硬编码的 `DDPhotoField_1U7CNXCKQNS00`。

**后果**：`_attachments(..., images=True)` 没有图片就抛“请先上传产品参考图片”；非 native 分支又把图片 id 写进硬编码字段 `DDPhotoField_1U7CNXCKQNS00`。如果模板不要求图片/字段 id 不同，用户会被强制上传无用图片，或图片写不进正确控件。

**为什么是待确认**：仓库里没有非 native 自研 OA 模板的字段契约（`oa/inquiry.py` 的 `OA_FIELDS` 与 native 模板 schema 只覆盖当前模板）；无法仅凭代码断定所有自研 OA 模板都要求该控件。需要模板配置/接口文档确认。

**证据代码**：
```python
image_ids = [str(item["id"]) for item in await _attachments(
    session, user, inquiry, [], images=True, ...)]
...
else:
    fields["DDPhotoField_1U7CNXCKQNS00"] = image_ids
```

**建议**：按模板字段定义决定是否要求图片、用哪个 component id；native 用 `reference_images`，非 native 用配置项，不要硬编码。

## 四、补充维度检查

### 1. 负数 / 0 / 超大值 / 小数位

- 询价 `target_price` 已在 `inquiry/schema.py:33、54、103` 约束 `ge=0`、`max_digits=16`、`decimal_places=2`；B10 未发现新绕过（OA payload 只是 `str()`）。
- 打样 `score` 先经 `collect_template_values` 校验，测试覆盖 `""`、`NaN`、`99991`；`estimated_hours` 模型 `Numeric(8,2)`，没有负数/超大小数新入口。
- 合同 OA 金额来自 `_contract_amount` 的 `Decimal`；没有把用户输入直接转 float。`f"{amount:.2f}"` 的舍入口径仍受 B01-03 约束，本块不重复。
- 钉钉 `size` 用 `len(content)` 比对；附件数量有 `max_count`/`max_length`；未发现“超大附件绕过”。
- **未覆盖**：OA payload 的字段长度（如 `contract_description` 包含整份合同正文、`ResolveOa.note` 无上限）没有与 OA 接口/DB 列对齐；属于潜在 413/500，未形成可复现证据，记入待确认清单。

### 2. 事务边界

- `send_prepared_approval` 是“先 commit 预约 → 上传 → 锁回 → commit 快照 → 外部调用 → 锁回”的两段式；避免了长事务持锁，但带来 B10-06（外部返回后不校验状态）与 B10-08（孤儿附件）。
- `send_prepared_approval` 只 commit 自己的状态；调用方路由再 commit 审计。外部调用成功、审计失败时会留下“OA 已建单、CRM 无审计”的半成品（B10-10）。
- 打样/询价发起前都先锁业务行（询价显式、打样在调用方），没有发现“订单建了但明细没建”式的半成品；OA 侧没有附件清理例外，见 B10-08。

### 3. 异常 / 日志 / 敏感信息

- `get_process_instance`、`attachments.py` 的异常包装较好；`get_process_schema`/部门/通讯录接口未包装（B10-04）。
- `oa_stream` 失败只记异常类型、不 ACK，避免吞异常；未发现事件正文/ticket 入日志。
- `DingTalkError` 文案带 `resp.text[:200]`、requestId，不带 appSecret/token；`send_prepared_approval` 的 `current.error = exc.message` 会落库，若 OA 返回内容含内部字段，会进 CRM 错误列（当前 OaError 文案由 OA 客户端生成，未发现 token）。
- **错误信息泄露**：`oa/sample.py:194` 把 `exc` 原样拼进“打样资料未能上传到 OA”返回 502，`OaError` 文案可能含 OA 侧内部信息；`dingtalk/router.py` 附件失败只给固定文案，较好。样本这条未形成高危，只记 P3 观察。

### 4. 审计

- 成功路径：询价发起 `dingtalk/router.py:371-384`；合同发起 `contract/router.py:130-140`；打样发起 `sample/dingtalk.py:395-397`；核定 `dingtalk/router.py:628-640`；撤回 `dingtalk/router.py:771-782`。
- **失败/未知结果路径没有审计**：`send_prepared_approval` 落 `needs_review`/`failed`/`not_sent` 后抛错，路由不写审计；外部已建单但审计缺失时无法复算。已并入 B10-10。
- 审计内容只有 status/instance_id/idempotency_key，不含金额/附件/模板版本；OA 不是资金操作，暂不单列 P 级，但合同 OA 金额与 B07-03 相关，建议在合同审计里补金额快照。

### 5. 测试覆盖

- 已读：`test_inquiry_oa_submission.py`、`test_inquiry_oa_attachments.py`、`test_oa_sample_inquiry_flow.py`、`test_oa_business_gates.py`、`test_oa_contract_flow.py`、`test_oa_live_sync.py`、`test_oa_withdrawal_completion.py`、`test_dingtalk_oa_state_machine.py`、`test_signature_input_boundaries.py`。
- **未运行测试**：项目要求隔离库，未授权改测试；所有“覆盖/未覆盖”是读用例后的判断，不是“跑过全绿”。
- 覆盖情况：
  - B10-01：有用例，但用例把回退写成期望（反向缺口）。
  - B10-02：无自定义 business_type 用例。
  - B10-03：只覆盖显式非法 owner。
  - B10-04：mock 成功路径，无故障注入。
  - B10-05：无多域名用例。
  - B10-06：无并发/外部期间取消用例。
  - B10-07：无单候选姓名不匹配用例。
  - B10-08/B10-10：无清理/非 OaError 注入用例。
  - B10-09：有 approved 返回，但断言在 sync 后。

## 五、本块没有看的部分及原因

| 文件/范围 | 状态 | 原因 |
|---|---|---|
| `backend/app/modules/dingtalk/service.py`（1,760 行） | 未读 | 台账按 5,000 行上限划入 B11；B10 只审调用边界，`create_inquiry_instance`/`sync_pending_instances`/`resolve_reviewed_instance`/`sync_users` 的内部事务、幂等、范围过滤未在本块结论内 |
| `backend/app/modules/sample/dingtalk.py`（516 行） | 只读发起调用段（330-400） | 属于 B11；本块只用它确认 SampleRequest 业务行锁和发起审计位置，未审其保存/重试/模板逻辑 |
| `backend/app/modules/oa/contract_form.py`（142 行） | 未重读 | 属于 B09，已审；本块只通过 `apply_extra_fields` 调用，未复核其字段白名单 |
| `backend/app/modules/dingtalk/attachments.py` 的真实 OA 附件库 | 未实测 | 没有 OA 测试环境/凭据；只按代码与 mock 测试判断 |
| `backend/tests` 全量运行 | 未运行 | 项目要求隔离库，未授权改测试；覆盖结论来自用例阅读 |
| 隔离库并发/故障注入 | 未执行 | B10-06/B10-08/B10-10 的并发与异常结论来自代码路径，未做运行复现 |

## 六、结论

- 本块 P0：0；P1：1（B10-01，打样缺阶段快照被落成 approved）；P2：6；P3：3；待确认：1。
- 最优先处理 B10-01（业务状态被无证据放行），其次 B10-02/B10-03（配置与报价员绕过后直接卡住/送错审批），再做 B10-04（外部读接口 500）与 B10-06（外部返回后状态回写竞态）。
- B10-05 是环境相关但会必现的配置硬编码；B10-07 是“静默绑错人”，建议一并修。
