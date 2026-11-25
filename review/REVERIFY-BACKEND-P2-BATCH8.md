# 后端 P2 复核（第八批：B10–B14 剩余条目）

> 日期 2026-11-24，只读当前代码。只审查，未改业务代码。

## 一、结果

本批 16 条：

- **4 条已修**：B12-01、B13-05、B13-09、B14-04
- **12 条仍成立**：B10-02、B10-03、B10-05、B10-07、B11-04、B11-05、B11-06、B11-07、B11-10、B11-11、B12-02、B12-05
- **0 条部分修**

不重复统计：B10-04、B11-08 仍成立，B11-03 已收窄，B11-09/B12-04/B13-03/B13-04/B13-07/B14-03/B14-05 已修（见前批）。

## 二、已修

| 编号 | 当前代码 | 说明 |
|---|---|---|
| B12-01 | `core/audit.py:44-50` 注释与实现明确：Decimal 默认按原值存字符串；`decimal_as_str=False` 的 float 模式已拆除 | 审计金额不再转 float，16 位边界不会差一分 |
| B13-05 | `core/importing.py:317-323` `write_failed_row` 对 5xx/未知异常统一返回“该行保存失败，请重试…”，原异常只写日志；`customer/io_router.py:442-443` 走这套 | 客户导入不再把表名/约束/数据值原文返前端 |
| B13-09 | `customer/service.py:1219-1245` 先锁旧客户，`:1246-1254` 再 `SELECT ... FOR UPDATE` 重读联系人并核对 `customer_id`；不一致 409 | 并发下不能把联系人再改走 |
| B14-04 | `wecom/model.py:134-145` `WeComSyncJob.__table_args__` 已有 `uq_wecom_sync_event_fingerprint` 唯一索引（非空条件），与迁移一致；`:159` 有 `event_fingerprint` 列 | 模型建库/新环境也会兜底唯一，不再只靠迁移 |

## 三、仍成立（逐条证据）

| 编号 | 当前代码事实 | 结论 |
|---|---|---|
| B10-02 | `oa/inquiry.py:214-216` `_attachments()` 仍硬编码 `BusinessFile.business_type == "inquiry"`；`dingtalk/router.py:207/321` 已读 `inquiry_file_business_type`，但自研 OA 这条没读 | 配置不同则自研 OA 看不到附件/图片 |
| B10-03 | `dingtalk/router.py:246-247` `quote_owner = payload.quote_owner_user_id or oa_inquiry.saved_quote_owner(inquiry)`；白名单校验只在 `if payload.quote_owner_user_id:` 里执行 | 省略参数时，存量 `oa_quote_user_id` 绕过报价员白名单 |
| B10-05 | `dingtalk/router.py:340` 图片 URL 仍写死 `https://crm.mandla.cn/api/v1/public/inquiry-images/...`，没有配置项 | 换域名/环境即取不到图 |
| B10-07 | `dingtalk/client.py:539-545` 精确姓名匹配失败后，仅一个候选就直接返回 `candidates[0][0]` | 同名不同人/唯一模糊候选仍可能挂错发起人 |
| B11-04 | `user/service.py:313-334` `set_user_status` 只在 `archived` 时 `revoke_user_sessions`；`disabled` 只改状态，不吊销会话。`core/deps.py:69-75` 校验 `status=active` | 停用期间旧 token 被挡，但重新启用后旧 token 又可用 |
| B11-05 | `dingtalk/service.py:1655-1705` `sync_users` 只返回绑定计数/未匹配；`dingtalk/router.py:832-842` 审计只记 total/bound/already_bound/unmatched 数，无逐人 id；且路由无解绑/改绑接口 | 绑错无法在界面纠正，审计也追不到具体人 |
| B11-06 | `user/service.py:590-607` `create_department` 先 `_assert_sibling_name_free` 再 `session.add`，无锁；`:775-795` `create_role` 先 `select(Role.code)` 再插入，无锁/无 IntegrityError 翻译 | 部门可并发出重复，角色并发撞唯一约束仍 500 |
| B11-07 | `dingtalk/router.py:594-605` 路由先判 `row.status`；`dingtalk/service.py:257-310` `claim_external_call` 只锁行并检查 `resolve_state=processing`，**不复判业务状态**；`:852-880` 重发直接复用快照 | 状态在路由判断后变 approved，仍可能 resend 建第二张单 |
| B11-10 | `dingtalk/service.py:862-880` `resend_instance` 只取 `row.form_snapshot["formComponentValues"]` 原样重发；`failed` 与 `not_sent` 共用 `RESOLVABLE_STATUSES`，没有“修正字段后重试”的入参 | 明确拒绝后仍按旧快照锁死，用户无法改字段重试 |
| B11-11 | `auth/router.py:257-270` `_external_base` 用请求 `Host` + `X-Forwarded-Proto` 拼；注释明说 Host 白名单“由反向代理负责”，代码无白名单；`:325` 回调 target 同源 | 代理未固定 Host 时仍是开放重定向风险 |
| B12-02 | `core/errors.py:191-210` 参数校验 details 仍逐条返回 `"input": str(error.get("input"))`；登录密码超长时原值回显 | 密码等敏感输入仍可能出现在 400 响应 |
| B12-05 | `file/router.py:46-60` 公共询价图片只校验签名/过期 + `FileRecord` 是图片 + `BusinessFile.business_type=="inquiry"`；不查 `CustomInquiry.deleted_at/withdrawn` | 询价删除/撤回后，7 天内签名 URL 仍可读 |

## 四、结论

- 本批真修的四条都有明确落点：审计 Decimal、导入异常脱敏、联系人改挂锁后重读、企微任务模型唯一索引。
- 仍成立的 12 条里，最该先处理的是：
  1. B11-04 停用不吊销会话（重新启用旧 token 复活）；
  2. B11-07 resend 服务层不复判状态（可能建第二张审批单）；
  3. B10-03 存量报价员绕过白名单；
  4. B12-02 密码回显；
  5. B12-05 已删除询价图片仍可读。
- 下一批：B15–B19 剩余 P2。
