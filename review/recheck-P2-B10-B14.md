# B10–B14 P2 逐条复核（证伪）

- 范围：B10-02..07（6）、B11-03..11（9）、B12-01/02/04/05（4）、B13-03..09（7）、B14-03/04/05（3），共 29 条。
- 方法：逐条重读触发路径、路由依赖、模型约束和服务层锁；能静态断言的进测试。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b10_b14_p2.py -q`
- 结果：`13 failed, 1 passed`。通过的是 B10-04 的控制项：`userid_by_unionid` 已经包了 httpx/JSON，报告把它算进“裸解析”是子项误报。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B10-02 | **确认** | `oa/inquiry._attachments` 写死 `BusinessFile.business_type == "inquiry"`，不读 `inquiry_file_business_type`；配置非默认值时自研 OA 取不到附件 |
| B10-03 | **确认** | `quote_owner = payload or inquiry.oa_quote_user_id`；只有显式传参才校验 `allowed_owners`，省略时存量非法 owner 直接进 OA |
| B10-04 | **部分误报** | `get_process_schema`/`upload_media`/`get_user_dept_ids`/`list_sub_departments`/`list_department_users`/`_submission_fields` 确实裸解析；但 `userid_by_unionid` 已包 `httpx.HTTPError`/`ValueError`/dict 校验，报告把它列为裸解析不成立 |
| B10-05 | **确认** | 图片 URL 写死 `https://crm.mandla.cn/...`，不读 `public_base_url`；非生产域名必现钉钉取不到图 |
| B10-06 | **确认** | `send_prepared_approval` 外部返回后只 `lock_receipt` + 校验 `current.status=="submitting"`，不再比 `business_status`/`superseded_at`；期间取消/归档仍会回写 |
| B10-07 | **确认** | `search_user_id_by_name` 无精确姓名匹配时仍 `return candidates[0][0] if len(candidates)==1`；姓名不同也会绑 |
| B11-03 | **确认** | `GET /users` 对 `customer:assign`/`lead:assign` 放行，`serialize_user` 固定带 `mobile/email/dingtalk_userid/roles/data_scope` |
| B11-04 | **确认** | `set_user_status` 只改 `user.status`，不 `revoke_user_sessions`；重新启用后旧 token 复活 |
| B11-05 | **确认** | `sync_users` 已绑定直接 `continue`，`UserUpdate` 无 `dingtalk_userid`；审计只记计数，绑错无法在界面纠正 |
| B11-06 | **确认** | `create_department` 先查后插、表无 `(parent_id,name)` 唯一约束；`create_role` 并发撞 `Role.code` 唯一约束未转 409 |
| B11-07 | **确认** | 路由先判 `row.status in RESOLVABLE_STATUSES`，服务层 `claim_external_call` 只看 `resolve_state`；窗口内变 approved 仍会重发 |
| B11-08 | **确认** | `sync_pending_instances` 的 `_notify_contract_review_outcome` 无 try；通知失败冒到整批，后续行不处理 |
| B11-09 | **确认** | `sample/dingtalk.template()` 只捕 `DingTalkError/httpx`，`json.loads`/`props['id']` 裸奔；坏 schema 500 |
| B11-10 | **确认** | `retry` 包含 `failed`，但 `open_new_round` 只认 `rejected/withdrawn`；钉钉明确拒绝后改字段重试仍 409“不能改写快照” |
| B11-11 | **确认** | `_external_base` 直接 `proto://Host`，回调 `target` 和 `redirect_uri` 都基于它；恶意 Host 可把 ticket 303 到攻击者域名 |
| B12-01 | **确认** | `core/audit.json_safe` 对 `Decimal` 统一 `float(value)`；审计里的金额是二进制近似 |
| B12-02 | **确认** | `_validation_error` 每个错误都带 `"input": str(error.get("input"))`；密码超长会在响应 `details` 里回显原文 |
| B12-04 | **确认** | `delete_file` 只在取锁前 `can_access_file`，`FOR UPDATE` 重读后不再复查；并发改派后可删范围外文件 |
| B12-05 | **确认** | `public_inquiry_image` 只验签名/过期/MIME/存在 `BusinessFile`，不查 `CustomInquiry.deleted_at/status`；7 天内已删询价图片仍可读 |
| B13-03 | **确认** | `MERGE_TARGETS` 不含 `CustomerDuplicateCase`；来源软删后 `resolve_case` 的 `get_visible_customer` 404，pending 案件卡死 |
| B13-04 | **确认** | 目标无负责人时直接继承 `source.owner_id`，不查 `User.status`，也不写 `CustomerOwnerHistory` |
| B13-05 | **确认** | 单行失败 `f"写入失败：{str(exc)[:160]}"`；`finalize` commit 失败也 `str(exc)[:120]`，DB 约束/字段值会进响应 |
| B13-06 | **确认** | `/customers/import` 无 `request_key`、无 `idempotency.reserve`；`file_sha256` 只做审计/预览配对 |
| B13-07 | **确认** | `expected_amount` 只有 `_f`、`current_version_amount` 只有 `float`；订单总额有 exact，但商机/报价没有 |
| B13-08 | **确认** | 转移审计 `after` 只 `serialize_customer`，`document_transfer` 只回响应；审计没有实际搬走的单据 ids/数量 |
| B13-09 | **确认** | `move_contact_to_customer` 只锁两个客户，不锁 `Contact`、不重读 `customer_id`；并发 bind/change 可覆盖归属 |
| B14-03 | **确认** | `merge-preview` 只要 `customer:view`，`_price_conflicts` 返回 `source_price/target_price` 明文，不要求 `price:manage` |
| B14-04 | **确认（模型/迁移漂移）** | 模型 `WeComSyncJob.__table_args__` 只有普通索引，唯一索引只在 Alembic；`create_all` 建库会丢保护，生产迁移不受影响 |
| B14-05 | **确认** | 批量转移审计 `after` 只有 `customer_ids/owner_id`，`affected/already_assigned/unavailable/documents_moved/skipped` 只回响应 |

## 四条需要单独说明

### B10-04：清单里混进了一个已经修好的函数

`get_process_schema`、`upload_media`、`get_user_dept_ids`、`list_sub_departments`、`list_department_users` 确实没有包 `httpx.HTTPError`/`ValueError`，`_submission_fields` 的 `json.loads(content)` 也一样裸奔。这些成立。

但 `userid_by_unionid` 在 `client.py:753-783` 已经：

```python
try:
    async with httpx.AsyncClient(...) as http:
        resp = await http.post(...)
except httpx.HTTPError as exc:
    raise DingTalkError(...)
try:
    data = resp.json() if resp.content else {}
except ValueError as exc:
    raise DingTalkError("钉钉成员查询响应不是合法 JSON", ...)
if not isinstance(data, dict):
    raise DingTalkError(...)
```

它不在“裸解析”名单里。报告把 6 个函数写成同一类，实际是 5 个 + 1 个已修。修复时不要重复包 `userid_by_unionid`。

### B10-06：外部返回后缺的是“业务状态”这一道，不是锁

`send_prepared_approval` 在发起前已经校验 `fresh_business.status == business_status` 和 `superseded_at`，外部调用返回后只：

```python
fresh_business, current = await lock_receipt()
return fresh_business, current, summary
```

`lock_receipt` 只查 `current.status=="submitting"` 和 `attempt_count`。如果这 15 秒里合同被取消、打样被撤回、询价归档，`start_contract_approval` 仍会因为 `doc.deleted_at is None` 把 `_dingtalk_oa` 写进已取消的合同。修的时候要在返回后再比一次 `business_status`/`deleted_at`/`superseded_at`，不一致时把 `row.status` 落 `needs_review`，不要回写业务。

### B13-03 + B13-04：合并后的“治理尸体”

`merge_customers` 只搬 `MERGE_TARGETS` 里的业务表，不碰 `CustomerDuplicateCase`；来源软删、`owner_id=None` 之后，`resolve_case` 在锁内 `get_visible_customer` 对已删来源直接 404，那条 pending 撞单案永远裁不了。

同一段还没有“继承负责人是否在职”的检查：`target.owner_id is None` 时直接 `target.owner_id = source.owner_id`，`pool_status="private"`，不查 `User.status`，也不写 `CustomerOwnerHistory`。如果来源负责人已停用，目标客户会挂在一个不能登录的账号上，既不在公海也不在在职员工名下。

两条都是“合并不可逆”的后遗症，但审查员分开写，没有指出合并前需要一次完整性检查（pending 案件 + 负责人 active）。

### B14-04：生产不是已确认缺陷，模型漂移才是

`WeComSyncJob.__table_args__` 只有 `Index("ix_wecom_sync_jobs_type", ...)`；唯一索引在 `alembic/versions/d1e4f7a9c2b6...py` 里。生产走 Alembic 时有保护，`Base.metadata.create_all`（测试/新环境/某些部署）没有。报告写“两个相同回调会各落一条”在生产不成立，准确说法是“模型与迁移不一致，非 Alembic 建库会丢保护”。修的时候在模型声明同一个部分唯一索引，并加一条 metadata/migration 一致性检查。

## 本批最大漏项（B13-03 + B13-04）

**合并前没有“治理完整性”检查：pending 撞单案和停用负责人都会在合并后变成不可修复的孤儿状态。**

审查员分别列了“合并后 pending 案件卡死”和“继承负责人未校验在职”，但没有把它们当成同一个入口的同一类问题：

- 合并是**不可逆**的，来源客户软删后没有恢复入口；
- `CustomerDuplicateCase` 的裁定依赖来源客户可见，软删即 404；
- 停用负责人没有登录能力，客户既不在公海也不在任何在职员工名下；
- 两者都不会被合并审计单独记录，事后只能翻 `CustomerMergeLog` 快照。

正确做法是在 `merge_customers` 的锁内、迁移任何关联之前统一检查：`source/target` 是否有 pending case；`source.owner_id`/`target.owner_id` 是否 active；不满足就 409，要求先裁定/先转交。不要等合并完再让用户去修一个已经没有入口的状态。

## 残余

- B11-05/07/08/10、B12-05、B13-03/05/08/09、B14-05 的真实并发/DB 用例未搭；当前是源码路径 + 锁/约束证据。
- B10-04 只对 `get_user_dept_ids` 写了失败测试；其余 4 个裸解析函数是同类静态证据。
- B14-04 的 `create_all` 环境未实际建库验证；模型 metadata 无唯一索引是确定的事实。


## 2026-11-23 真实补验

`review/verification/p2_b11_b12_b14.py`：

```text
[B11-11] Host=evil.example.com -> https://evil.example.com
[B12-01] Decimal 12.30 -> 12.3 type=float
[B12-02] status=400 原文是否回显=True input 长度=200
[B14-04] WeComSyncJob indexes=[('ix_wecom_sync_jobs_type', False, ['job_type', 'status'])]
```

结论：B11-11、B12-01、B12-02、B14-04 均按当前代码成立。B14-04 是模型与 Alembic 漂移：模型层只有普通索引。B12-02 回显的是完整密码原文，不只是字段名。


## 2026-11-23 B13 补验

`review/verification/p2_b13_import_precision.py`：

```text
[B13-06] import_customers 参数=['request', 'file', 'preview', 'preview_token', 'user', 'session']
[B13-06] 是否含 request_key=False
[B13-07] _f(Decimal('123456789.1234'))=123456789.1234 type=float
```

B13-06 入口无幂等、B13-07 金额走 float 均成立；B13-04/05/09 仍为源码级。
