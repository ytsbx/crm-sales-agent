# B11 权限与数据范围·钉钉 OA 服务 / 打样提交 / 登录会话 / 用户角色（12 文件，4,864 行）

> 范围：`dingtalk/service.py`（1760）、`sample/dingtalk.py`（516）、`sample/oa_content.py`（77）、`auth/model.py`（72）、`auth/schema.py`（54）、`auth/session.py`（149）、`auth/router.py`（530）、`user/model.py`（92）、`user/schema.py`（138）、`user/usernames.py`（33）、`user/service.py`（799）、`user/router.py`（644）。
> 12 个文件均读完；只读，未改业务代码。测试未运行，覆盖结论来自 `backend/tests` 定向阅读。

## 一、逐文件检查记录

### 1. `backend/app/modules/dingtalk/service.py`（1760 行，已读全文）

**我检查了什么**：OA 发起/重发/认领/作废状态机；`idempotency_key`、`claim_external_call`、`resolve_request_key`、僵死占用；`create_inquiry_instance` 先占键后调外部；`_call_create_and_record` 三种后果；`sync_pending_instances` 批量同步与状态回写；合同/打样/询价的状态映射；钉钉扫码登录 `state`/`ticket`/`resolve_login_user`/`_signup_login_user`；通讯录 `sync_users` 的绑定判据；`first_image_attachment`；`serialize`。

**结果如何**：
- **幂等/锁**：`create_inquiry_instance` 同轮重试复用同一行、`claim_external_call` 用 `FOR UPDATE` + 条件转换 + 先 commit 再调外部；`_call_create_and_record` 按 `not_sent/failed/needs_review` 落状态；`resolve_reviewed_instance` 对 `resend/adopt/abandon` 有占用与同键回放。未发现“页面重复点击直接多建单”的主路径。
- **状态机风险**：路由在 `resolve_oa_instance` 里先判 `RESOLVABLE_STATUSES`（`router.py:594`），但服务层 `resolve_reviewed_instance` 拿锁后没有复判状态（`service.py:604`、`626-655`）；同步并发把行改成 approved 后仍可能 resend，见 B11-07。
- **金额**：OA 服务本身不计算金额，表单值由调用方 `build_form_component_values` 转字符串；`first_image_attachment` 不碰金额。未发现 float 参与。
- **扫码登录**：`state`/`ticket` 只是进程内字典，`issue_login_state` 生成随机串、`consume_login_state` 全局消费；**没有绑定浏览器 cookie/session**（`service.py:1601-1619`），见 B11-01。
- **首次建号**：`_signup_login_user` 自动建 `salesperson`、`data_scope=self` 的账号，密码硬编码 `"123456"`，只把 `initial_password_pending=True` 交给前端提示（`service.py:1745-1747`），见 B11-02。
- **通讯录绑定**：`sync_users` 只按“已绑定→手机号→姓名”匹配，绑定后写 `users.dingtalk_userid`；**没有逐人审计，也没有手工解绑/改绑接口**（`service.py:1462-1558` + `user/schema.py:49-57`），见 B11-05。
- **批量同步**：`sync_pending_instances` 对钉钉路径的单条查询失败有隔离；但合同结果通知 `_notify_contract_review_outcome` 没有 try，某个合同的待办/通知失败会打断整批（`service.py:1159-1161`），见 B11-08。
- **异常/日志**：单条查询错误只记 `type`/`str` 截 500 到 `row.error`；`resolve` 的 `except BaseException` 会补偿释放占用，未吞原始异常。没有发现 token/secret 写日志。

### 2. `backend/app/modules/sample/dingtalk.py`（516 行，已读全文）

**我检查了什么**：独立打样/关联询价两条路径；`template()` 拉 schema；`save()` 草稿落列；`submit()` 的 `previous`/`retry`/`open_new_round` 判定、快照锁定、字段校验、附件上传、OA 发起；`detail()` 回填；`inquiry_candidates` 来源校验；审计位置。

**结果如何**：
- **金额/数量**：`sample_fee` 经 `validated_sample_details` → `SampleUpdate` 校验（非负、2 位小数、金额范围）；`score` 经 `collect_template_values` 的 `Decimal` 校验（0–99990、有限）；`estimated_hours=Decimal(score)/10`，无 float。
- **幂等/重复**：`previous` 已提交时：`pending/approved` 直接返回；`failed/not_sent/skipped` 同轮重试；`rejected/withdrawn` 才允许 `open_new_round`；独立打样与关联询价不能切换。主路径防重成立。
- **快照**：`retry` 时用 `snapshot_edit_detail` 只比“两边都有”的字段，允许后端现算字段不带入比较；但 `failed`（明确拒绝）也走“原样重发”，用户不能修正表单，见 B11-10。
- **模板解析**：`template()` 对 `schemaContent` 直接 `json.loads`，对 `props['id']` 直接取键；坏 JSON/缺 id 会 500（`sample/dingtalk.py:95-101、111`），见 B11-09。
- **附件**：申请表 + 图片 + 其他附件逐个上传；单个失败会给“打样资料未能传入钉钉，尚未发起审批”并带外部文案；已上传的前几个没有清理（与 B10-08 同源，本块不重复编号）。
- **状态**：`submit` 在 OA 发起前把 `sample.status='pending'`、`submitted=True`；OA 创建返回 approved/rejected 时本地仍 pending，属于 B10-09，本块不重复。
- **审计**：发起成功路径写 `update`，失败/未知结果路径无审计（与 B10-10 同源）。

### 3. `backend/app/modules/sample/oa_content.py`（77 行，已读全文）

**我检查了什么**：`validated_sample_details` 的 `sample_maker`/`sample_fee` 校验；`submission_summary` 单头/逐条明细；`dingtalk_requirement_note` 文本。

**结果如何**：
- `sample_fee` 走 `SampleUpdate`（Decimal、非负、2 位小数、上限），没有 `float()`；`sample_maker` 长度由 `SampleUpdate` 限制。
- `submission_summary` 只做字符串拼接，金额按库里的 Decimal 原样显示；不参与计算。
- 未发现金额/舍入/负数新入口；本文件不单列编号。

### 4. `backend/app/modules/auth/model.py`（72 行，已读全文）

**我检查了什么**：`LoginSession.sid/token_id` 长度、唯一约束、`user_id` 外键级联、`status`/`revoked_reason`、索引。

**结果如何**：
- `sid` String(64)、`token_id` String(32)，UUID hex 32 位；`sid` 有唯一约束，`user_id` 有索引、外键 `CASCADE`。
- `status` 是 String(32) 自由字符串，但代码只写 active/revoked；没有 CHECK，属于结构宽松，非本块可复现风险。
- 未发现密码/令牌列；本文件不单列编号。

### 5. `backend/app/modules/auth/schema.py`（54 行，已读全文）

**我检查了什么**：登录用户名/密码长度；企微回调 shape；钉钉 ticket 长度；个人账号修改的用户名 pattern、密码字节上限。

**结果如何**：
- `LoginRequest` 用户名 1–64、密码 1–128；`PersonalAccountUpdate` 用户名小写字母开头、密码最少 6、最多 72 字节；`new_password` 的 bcrypt 字节上限有校验。
- `DingTalkExchange.ticket` 1–128；`WeComSsoCallback` 没有 `wecom_userid`，接口本身也拒绝签发。
- 未发现入参绕过；本文件不单列编号。

### 6. `backend/app/modules/auth/session.py`（149 行，已读全文）

**我检查了什么**：`open_session`、`load_active_session`（sid/user_id/status/token_id/deadline）、`issue_session_token`、`revoke_session`、`revoke_user_sessions`；刷新与普通鉴权是否共用同一判据。

**结果如何**：
- 普通鉴权与刷新都走 `load_active_session`；没有 sid、会话不存在、revoked、token_id 不匹配、超过最长有效期都 401；`for_refresh` 用 `FOR UPDATE`，并发刷新串行。
- `revoke_user_sessions` 用一条 UPDATE 按 `status=active` 收口，改密与“旧会话全废”同一事务；未发现漏吊销。
- `revoke_session` 不加行锁，但登出与刷新并发时最终只会留下 revoked 或 401，不会产生新可用令牌；不单列。
- 本文件没有“停用账号时吊销会话”的入口，问题在 `user/service.set_user_status`，见 B11-04。

### 7. `backend/app/modules/auth/router.py`（530 行，已读全文）

**我检查了什么**：账号密码登录（行锁、bcrypt、限流、会话/审计同提交）、refresh、企微 SSO 关闭、钉钉扫码 authorize/callback/exchange、logout、me/permissions、个人账号修改。

**结果如何**：
- **登录**：先按（用户名小写+IP）查限流，再 `SELECT ... FOR UPDATE` 读用户并校验密码；登录、改密抢同一把账号行锁；会话与审计同一次 commit；disabled 用户拒绝。未发现明文密码/哈希回显。
- **refresh/logout**：refresh 走 `load_active_session(for_refresh=True)`；logout 只吊销当前 sid；密码/用户名修改吊销该用户全部会话。未发现旧 token 绕过。
- **钉钉登录**：`/authorize` 生成 state，`/callback` 消费 state 后换 identity，`/exchange` 用 ticket 换 token；**state 只存在全局内存，不绑定发起浏览器**（`router.py:306-309、334-338`；`dingtalk/service.py:1601-1619`），见 B11-01。
- **回调跳转**：`_external_base` 用请求 `Host` 拼 `target` 和 `redirect_uri`（`router.py:255-275`），未做部署域名白名单，见 B11-11。
- **个人账号**：`PATCH /auth/account` 只改当前用户；先 `lock_usernames` 再 `lock_user_row`，校验当前密码、用户名冲突、会话仍有效；改密/改名后吊销全部会话；审计不写密码。未发现越权改别人。
- **me/permissions**：只返回自己的权限；`list_permissions` 只是用于取中文名，不下发目录。未发现泄露。

### 8. `backend/app/modules/user/model.py`（92 行，已读全文）

**我检查了什么**：`Department`、`User`、`Role`、`Permission` 的列宽/唯一/外键；`user_roles`/`role_permissions` 联合主键；`dingtalk_userid` 没有唯一约束。

**结果如何**：
- `username` 唯一；`Role.code` 唯一；`Department` 没有 `(parent_id,name)` 唯一约束（与 B11-06 的并发重复有关）。
- `dingtalk_userid` 是 String(128) 可空，没有唯一约束；`resolve_login_user` 用 `limit(2)` 处理重复并返回 ambiguous，不是静默取第一个。
- `data_scope/status` 是自由字符串，没有 DB CHECK；API 层有枚举校验的只有 `RoleDataScopeUpdate`，`RoleUpdate` 仍是字符串，见 B11-12。
- 未发现密码列外泄；本文件不单列编号。

### 9. `backend/app/modules/user/schema.py`（138 行，已读全文）

**我检查了什么**：`CreateModel`/`PatchModel` 的长度与空值保护；`UserCreate/UserUpdate` 的密码字节上限；`UserUpdate.role_ids` 省略语义；`RoleUpdate.data_scope/status` 类型；`DepartmentUpdate`。

**结果如何**：
- 这些类继承 `CreateModel`/`PatchModel`，长度上限从 ORM 列定义自动读取（`user`/`departments`/`roles` 均在表映射内）；超长会在 422 而不是 500，未发现长度漏项。
- `UserUpdate.role_ids` 用 `exclude_unset` 区分“省略=保留/空列表=清空”，服务层按 `model_dump(exclude_unset=True)` 处理，语义成立。
- **`RoleCreate.data_scope: str`、`RoleUpdate.data_scope: str | None`、`RoleUpdate.status: str`** 没有枚举约束；`RoleDataScopeUpdate` 才有 `Literal`。见 B11-12。
- `UserCreate/Update` 没有 `dingtalk_userid` 字段，管理员无法通过用户接口解绑/改绑，见 B11-05。

### 10. `backend/app/modules/user/usernames.py`（33 行，已读全文）

**我检查了什么**：拼音登录名生成、非法字符过滤、空值兜底、`user` 前缀、54 字符截断、`lock_usernames` 的 PostgreSQL advisory lock、`allocate_username` 的冲突递增。

**结果如何**：
- 生成值只留 `a-z0-9_`，空或非字母开头加 `user`；截 54 位给后缀留空间；`lock_usernames` 只在 PostgreSQL 加锁，SQLite 测试跳过。
- `create_user` 和钉钉首次建号都先拿同一把 `username-allocation` 锁，未发现并发同名主路径。
- 未发现新的 P0/P1；本文件不单列编号。

### 11. `backend/app/modules/user/service.py`（799 行，已读全文）

**我检查了什么**：运行态角色/权限过滤、`resolve_data_scope` 最大范围、用户/部门/角色 CRUD、密码重置与会话吊销、最后管理员保护、部门成环/同级重名、角色权限覆盖、删除前引用检查。

**结果如何**：
- **权限**：`get_user_roles`/`get_user_permission_codes` 都 join `Role.status=active`；`resolve_data_scope` 再过滤停用角色，多角色取最大。停用角色不会渗进权限/范围。
- **最后管理员**：停用自己、摘 admin、停用 admin 角色都走 `_lock_admin_guard` + `count_active_admins`；停用账号/移除角色两个并发路径有全局 advisory lock。未发现“0 管理员”路径。
- **改密**：`update_user` 在写新哈希前 `lock_user_row`，同事务 `revoke_user_sessions`；未发现“密码改了旧令牌还能用”。
- **停用**：`set_user_status` 只改 `status`，**没有吊销该用户会话**（`service.py:296-306`）；重新启用后旧 token 又可用，见 B11-04。
- **并发创建**：`create_department` 先查重再插入，没有 `_lock_department_tree`，`Department` 也没有唯一约束；`create_role` 先查 code 再插入，没有锁，唯一冲突会走 `IntegrityError` 未捕获，见 B11-06。
- **数据范围**：`RoleUpdate` 的任意 `data_scope` 会落库；`resolve_data_scope` 只认四个合法值，非法值静默按 self，见 B11-12。

### 12. `backend/app/modules/user/router.py`（644 行，已读全文）

**我检查了什么**：20 个用户/部门/角色接口的权限依赖、分页、审计、`GET /users` 的放宽、`serialize_user` 下发字段、角色权限/数据范围接口、最后管理员相关错误。

**结果如何**：
- 写接口统一 `require_permission("user:manage")`；`GET /users` 额外允许 `customer:assign`/`lead:assign`（`router.py:52-53`）。**同一接口直接返回 `mobile`、`email`、`dingtalk_userid`、角色与 data_scope**（`service.serialize_user:112-121`），见 B11-03。
- `update_user` 先保存角色再改资料，角色变更单独审计；`before` 在角色变更后取，角色 old 值在 `_save_user_roles` 审计里，未重复记录。
- `disable_user` 只改状态，不吊销会话（同 B11-04）；`enable_user` 不吊销。`set_role_data_scope` 用 `RoleDataScopeUpdate` 枚举，安全。
- `get_user_data_scope` 用 `roles_of_user`（含停用）展示，再用 `resolve_data_scope` 过滤；展示与实际生效可能不同，但接口本身无越权。
- 未发现 `user_id` 参数越权读别人（所有写/读都要求 `user:manage`；`GET /users` 的放宽是本次发现的风险点）。
- `GET /departments/{id}/users`、`GET /users/{id}` 都要求 `user:manage`；未发现直接按 ID 看别人资料的低权限路径。

## 二、发现清单

| 编号 | 严重度 | 位置 | 一句话 | 测试覆盖 |
|---|---|---|---|---|
| B11-01 | P1 | `auth/router.py:306-309、334-338` + `dingtalk/service.py:1601-1619` | 钉钉扫码 state 全局有效、不绑定浏览器，存在登录 CSRF（把受害者登进攻击者账号） | 无 |
| B11-02 | P1 | `dingtalk/service.py:1745-1747` + `core/deps.py:69-78` | 首次钉钉登录自动建号，初始密码固定 `123456`，后端不强制改密，只靠前端弹窗 | 有用例，但把默认密码写成期望 |
| B11-03 | P2 | `user/router.py:52-53、69-73` + `user/service.py:112-121` | `customer:assign`/`lead:assign` 可读全量员工，接口下发手机号/邮箱/钉钉 ID/角色范围 | 权限用例覆盖可读，未覆盖最小化 |
| B11-04 | P2 | `user/service.py:296-306` + `core/deps.py:69-78` | 停用账号不吊销会话，重新启用后旧 token 恢复可用 | 无 |
| B11-05 | P2 | `dingtalk/service.py:1462-1558` + `dingtalk/router.py:855-867` + `user/schema.py:49-57` | 通讯录自动绑定无逐人审计，且没有解绑/改绑接口，绑错无法在界面纠正 | 有用例覆盖匹配，无审计/解绑用例 |
| B11-06 | P2 | `user/service.py:531-548、716-736` | 部门/角色创建“先查后插”无锁：部门可并发出重复，角色并发撞唯一约束 500 | 无 |
| B11-07 | P2 | `dingtalk/router.py:594-606` + `dingtalk/service.py:604、626-655、866-880` | 路由判完状态后服务层不复判；同步期间状态变 approved 仍可能 resend 建第二张单 | 状态机用例非并发，无该窗口 |
| B11-08 | P2 | `dingtalk/service.py:1159-1161、1217-1225` | 合同审批结果通知没有单行 try；一个通知/待办失败会打断整批同步 | 无 |
| B11-09 | P2 | `sample/dingtalk.py:95-101、111` | 打样模板 schema 直接 `json.loads`/`props['id']`，坏 JSON/缺 id 直接 500 | 只测合法 schema |
| B11-10 | P2 | `sample/dingtalk.py:355、374-378` + `dingtalk/service.py:108-115` | `failed`（明确拒绝）也按“原样重发”锁快照，用户无法按提示“修正后重试” | 有重试用例，未覆盖改字段 |
| B11-11 | P2 | `auth/router.py:255-275、325` | 回调 target/redirect_uri 用请求 `Host` 拼，未做部署域名白名单；代理未固定 Host 时可被开放重定向 | 无 |
| B11-12 | P3 | `user/schema.py:100-137` + `user/service.py:750-752` + `user/service.py:100` | `RoleCreate/RoleUpdate` 的 `data_scope/status` 只是字符串，非法值落库后静默按 self | 无 |

## 三、逐条证据与建议

### B11-01（P1）钉钉扫码 state 不绑定浏览器，登录 CSRF

**位置**：`backend/app/modules/auth/router.py:306-309`（生成 state）、`334-338`（消费 state）；`backend/app/modules/dingtalk/service.py:1601-1619`（`issue_login_state`/`consume_login_state`）。

**触发条件**：攻击者自己发起 `/auth/dingtalk/authorize` 拿到一个合法 state，用自己的钉钉完成授权拿到 code，然后把 `.../auth/dingtalk/callback?code=...&state=...` 发给受害者；受害者的浏览器打开该回调。

**后果**：`consume_login_state` 只检查“这个 state 是否由本进程发出过、是否未过期”，不检查它是否由当前浏览器发起；回调把受害者的浏览器登录成**攻击者的 CRM 账号**（login CSRF / session swapping）。受害者随后在“自己的”页面录入的客户、报价、跟进会落到攻击者账号，或攻击者借受害者浏览器完成审批；事后审计只显示一次正常的 `dingtalk_login`。

**证据代码**：
```python
state = dingtalk_service.issue_login_state()
url = dingtalk_service.get_client().build_login_url(... state=state)
...
if not dingtalk_service.consume_login_state(state):
    return back("state")
...
login_session = await open_session(session, user.id)
token = issue_session_token(login_session, user.name)
```
`issue_login_state` 只是 `_login_states[state] = now + TTL`；没有任何 cookie/session 绑定。

**附带问题**：`_login_states`/`_login_tickets` 在进程内存里，部署多 worker/多实例时，authorize 和 callback 可能落到不同进程，合法登录也会随机失败；注释假设单实例。

**测试覆盖**：没有 authorize/callback/state 的测试（`test_dingtalk_signup.py` 只测 `_signup_login_user`）。

**建议**：authorize 时下发一个 `HttpOnly`、`SameSite=Lax`、带签名的浏览器 nonce cookie，callback 同时校验 state 与 cookie；state 里绑定 nonce 摘要；ticket 同样绑定同一浏览器或至少绑定 nonce。多实例时把 state/ticket 放 Redis/DB。

### B11-02（P1）首次钉钉登录自动建号，默认密码 123456 且后端不强制改密

**位置**：`backend/app/modules/dingtalk/service.py:1745-1747`；`backend/app/core/deps.py:69-78`；前端提示 `frontend/src/app/layout/AppLayout.tsx:291`。

**触发条件**：通讯录里一个没有 CRM 账号的员工首次扫码登录；`_signup_login_user` 用姓名全拼生成用户名、`hash_password("123456")`、`initial_password_pending=True`。

**后果**：账号创建后即可用 `123456` 登录，`initial_password_pending` 只在 `/auth/me` 返回并触发前端弹窗；`get_current_user` 不检查该标记，直接发权限。任何知道该员工姓名/拼音用户名的人，在其改密前都能登录；新员工自己也可能长期不修改。属于可预测初始凭据导致的账号接管风险。

**证据代码**：
```python
user = User(username=username, name=name, mobile=mobile or None,
            password_hash=hash_password("123456"), initial_password_pending=True,
            status="active", dingtalk_userid=userid, department_id=None)
```
`backend/tests/test_dingtalk_signup.py:51-52` 断言 `initial_password_pending` 且 `verify_password('123456', ...)`，说明默认密码是既定业务口径，但没有任何“未改密不能访问业务接口”的后端闸门。

**测试覆盖**：有用例，且把默认密码写成期望；没有“未改密访问业务接口应被拒”的用例。

**建议**：保留既定初始密码口径的话，至少在 `get_current_user`/关键业务依赖里对 `initial_password_pending=True` 只放行 `/auth/me`、`/auth/account`、logout，其他接口 403；或改为一次性随机初始密码/首次登录强制改密。不能在只有前端弹窗的情况下认为“已告知本人”。

### B11-03（P2）`GET /users` 对分配权限下发全量员工 PII

**位置**：`backend/app/modules/user/router.py:52-53、69-73`；`backend/app/modules/user/service.py:112-121`。

**触发条件**：账号只有 `customer:assign` 或 `lead:assign`（没有 `user:manage`），调用 `GET /users`。

**后果**：接口按 `require_permission("user:manage", "customer:assign", "lead:assign")` 放行，返回每个员工的 `mobile`、`email`、`wecom_userid`、`dingtalk_userid`、角色和 `data_scope`。分配客户/线索只需要选人，不需要这些联系方式与登录身份；这等于把全公司通讯录和绑定关系开放给所有有分配权的账号，属于数据最小化/隐私问题。

**证据代码**：
```python
_: CurrentUser = Depends(require_permission("user:manage", "customer:assign", "lead:assign")),
...
svc.serialize_user(user, department_name=..., roles=...)
```
`serialize_user` 固定返回 `mobile/email/dingtalk_userid`。

**测试覆盖**：`test_lead_assignee_user_access.py` 覆盖了“有 assign 权限可读用户列表”，但没有覆盖“返回字段应最小化”；按当前测试，这个行为被当成预期。

**建议**：`GET /users` 在非 `user:manage` 时只返回 `id/name/department/status`（最多加岗位），不返回手机号、邮箱、钉钉 ID、角色/data_scope；新增一个 `assignee-picker` 专用返回结构，避免以后再把字段加回全量。

### B11-04（P2）停用账号不吊销会话，重新启用后旧 token 复活

**位置**：`backend/app/modules/user/service.py:296-306`；`backend/app/core/deps.py:69-78`。

**触发条件**：管理员停用一个账号（`POST /users/{id}/disable`），该用户已有登录会话；之后管理员重新启用。

**后果**：停用期间 `get_current_user` 因 `user.status != "active"` 拒绝，表面上“停用生效”；但 `login_sessions` 行仍是 active，旧 token 没有被吊销。重新启用后，所有旧 token（包括泄漏出去的）立即恢复可用，不需要重新登录，也没有审计说明“这些会话在停用期间被保留”。

**证据代码**：
```python
async def set_user_status(...):
    if status == "disabled":
        if operator_id is not None and user.id == operator_id:
            raise ...
        await _assert_not_last_admin(...)
    user.status = status
    await session.flush()
```
没有 `revoke_user_sessions`。

**测试覆盖**：`test_auth_session_expiry.py` 覆盖会话到期/刷新，未覆盖“停用→启用后旧 token”的组合。

**建议**：停用时同事务 `revoke_user_sessions(session, user.id, reason="admin_disable")`；启用后要求重新登录。若业务需要“临时停用后恢复原会话”，至少要在审计里明确记录并单独开关，不能默认保留。

### B11-05（P2）通讯录自动绑定无逐人审计，且绑错无法在界面纠正

**位置**：`backend/app/modules/dingtalk/service.py:1462-1558`（已绑定直接 `continue`、按手机号/姓名绑定）；`dingtalk/router.py:855-867`（只写聚合计数）；`user/schema.py:49-57`（`UserUpdate` 没有 `dingtalk_userid`）。

**触发条件**：`POST /dingtalk/sync-users` 自动按手机号/姓名匹配；某个 CRM 用户的姓名在钉钉里对应了另一个人（同名/手机号录错/人员交接），或匹配结果本身错了。

**后果**：`users.dingtalk_userid` 被写成错误编号；之后该钉钉号扫码登录直接进入这个 CRM 账号。`sync_users` 对已绑定的一律 `continue`，没有改绑；`user` 模块也没有 `dingtalk_userid` 字段或解绑接口，管理员无法在 CRM 内纠正，只能改库。路由审计只记 `total/bound/already_bound/unmatched`，没有记录“哪个 CRM 用户绑了哪个钉钉号”，事后无法还原。

**证据代码**：
```python
if row.dingtalk_userid:
    bound[str(row.dingtalk_userid)] = row
    continue
...
candidates = free_by_mobile.get(mobile, []) if mobile else []
if len(candidates) == 1:
    _bind(candidates[0], dingtalk_userid)
    continue
...
return {"total": ..., "bound": newly_bound, "already_bound": ..., "unmatched": ...}
```
`sync_dingtalk_users` 的审计 `after` 只有计数。

**测试覆盖**：`test_dingtalk_signup.py` 覆盖同名/手机号冲突拒绝，但没有覆盖“同步绑定审计/解绑接口”。

**建议**：为每次绑定写一条 `business_type="user"` 的审计（before `dingtalk_userid`、after、匹配依据 mobile/name）；提供 `PUT /users/{id}/dingtalk-binding`（需 `user:manage`）用于管理员显式解绑/改绑；禁止同步自动覆盖已绑定，但允许管理员纠正。

### B11-06（P2）部门/角色创建“先查后插”无锁

**位置**：`backend/app/modules/user/service.py:531-548`（`create_department`）、`716-736`（`create_role`）。

**触发条件**：两个请求同时创建同一父部门下的同名部门；或两个请求同时创建同一个 `role.code`。

**后果**：
- 部门：`_assert_sibling_name_free` 只是 `SELECT`，随后 `INSERT`；`departments` 没有 `(parent_id,name)` 唯一约束，也没有 `_lock_department_tree`。两个请求都能通过检查，最终出现两个同名同级部门，部门树/数据范围出现分裂。
- 角色：`Role.code` 有 DB 唯一约束，但 `create_role` 只在应用层先查；并发时后一个在 `flush` 撞 `IntegrityError`，路由没捕获，返回 500（而正常应 409“角色编码已存在”）。

**证据代码**：
```python
await _assert_sibling_name_free(session, name, parent_id)
dept = Department(name=name, parent_id=parent_id, ...)
session.add(dept); await session.flush()
```
`create_role` 同样：
```python
existing = (await session.execute(select(Role).where(Role.code == code))).scalar_one_or_none()
if existing is not None: raise AppError(...)
role = Role(code=code, ...); session.add(role); await session.flush()
```

**测试覆盖**：没有并发创建部门/角色的用例；串行重复有覆盖，但锁窗口没有。

**建议**：`create_department` 复用 `_lock_department_tree`（或给 `departments` 加 `(parent_id,name)` 唯一约束）；`create_role` 捕获 `IntegrityError` 转 409，或复用一把角色创建 advisory lock。

### B11-07（P2）核定路由判完状态后，服务层不复判

**位置**：`backend/app/modules/dingtalk/router.py:594-606`（路由状态闸门）；`backend/app/modules/dingtalk/service.py:604、626-655`（`claim_external_call` 后直接按 action 分发）；`service.py:866-880`（`resend_instance` 不校验状态）。

**触发条件**：用户提交 `resolve action=resend`；请求在路由 594 行看到 `needs_review`，随后另一条同步/事件把这一行改成 `approved`（已有 instance_id），再进入服务层 `claim_external_call`。

**后果**：`claim_external_call` 只校验 `resolve_state` 是否 processing，不校验 `status`；`resend_instance` 把行改成 `submitting` 并再次调用外部创建。结果是钉钉里可能多出第二张审批单，而本地只保留一行；后面 `sync` 只会认一个 instance_id，另一张变成隐形挂单。`adopt` 同窗口也可能把已 approved 行改成 pending 再覆盖。

**证据代码**：
```python
if row.status not in RESOLVABLE_STATUSES:
    raise AppError(...)  # router.py:594
...
row = await svc.resolve_reviewed_instance(...)  # router.py:609
```
服务层：
```python
claimed, was_stale = await claim_external_call(...)
...
return await resend_instance(session, row, request_key=key)
```
`resend_instance` 没有 `if row.status != "needs_review"`。

**测试覆盖**：`test_dingtalk_oa_state_machine.py` 覆盖顺序状态，没有“路由判完到 claim 之间同步改状态”的并发用例。

**建议**：`claim_external_call` 拿到行后（或 `resolve_reviewed_instance` 在 action 分发前）重新检查 `status in RESOLVABLE_STATUSES` 且 `instance_id` 一致；不一致就释放占用并 409，不能只看路由前置检查。

### B11-08（P2）合同结果通知失败会打断整批 OA 同步

**位置**：`backend/app/modules/dingtalk/service.py:1159-1161`（调用 `_notify_contract_review_outcome`），`1217-1225`（`notification_service.notify`）。

**触发条件**：某条合同 OA 状态变成 approved/rejected，`_notify_contract_review_outcome` 里的 `settings_service.get_number('countersign_days')` 返回非法值、待办更新失败、或 `notification_service.notify` 抛异常。

**后果**：`sync_pending_instances` 的 for 循环没有 try；异常直接冒出，当前行及本批后续行不再处理，事务未提交（此前 `query_oa_without_locks` 的查询提交只保存了前一行）。定时任务/手动同步返回 500，下一轮重跑；审批状态更新被延迟，且没有单条错误记录。对合同追回签的待办/通知也会漏发。

**证据代码**：
```python
if new_status in ('approved', 'rejected') and ...:
    await _notify_contract_review_outcome(session, linked_contract, new_status)
    linked_contract.filled_data = {**linked_contract.filled_data, '_oa_notified': marker}
```
没有 `try/except`，也没有把失败写进 `row.error` 后继续。

**测试覆盖**：`test_contract_dingtalk_oa.py` 覆盖正常通知，没有通知失败/非法 countersign_days 用例。

**建议**：把通知段落包在 try 里，失败写 `row.error`/审计并继续；或把通知改成 outbox/后台任务，不阻塞审批状态同步。

### B11-09（P2）打样模板 schema 解析裸奔，坏响应 500

**位置**：`backend/app/modules/sample/dingtalk.py:95-101`（`get_process_schema` / `json.loads`）、`111`（`props['id']`）。

**触发条件**：钉钉返回的 `schemaContent` 是坏 JSON 字符串，或某个 item 的 `props` 没有 `id`；或 `get_display_process_schema` 抛非 `DingTalkError/httpx` 的异常。

**后果**：`template()` 只捕 `DingTalkError,httpx.HTTPError`；`JSONDecodeError`/`KeyError`/`AttributeError` 会穿透。`detail()` 只捕 `AppError`（`sample/dingtalk.py:195`），`save_draft` 只捕 `AppError`（`322-325`），最终 500。打样弹窗打不开、草稿保存失败，用户看不到字段，也无法改配置绕过。

**证据代码**：
```python
schema = await (client.get_display_process_schema(code) if for_display else client.get_process_schema(code))
content = schema.get('schemaContent') or {}
if isinstance(content,str):content=json.loads(content)
...
fields.append({'id':props['id'],...})
```

**测试覆盖**：`test_sample_dingtalk_fields.py` 只喂合法 schema；没有坏 JSON/缺 id/超时用例。

**建议**：`template()` 统一捕获 `ValueError/KeyError/httpx`，转 502“打样模板读取失败，可稍后重试/联系管理员”，并记录哪一项缺失；不要把原始异常文本直接回前端。

### B11-10（P2）`failed` 被当成“原样重发”，用户无法修正表单

**位置**：`backend/app/modules/sample/dingtalk.py:355`（`retry=previous.status in ('failed','not_sent','skipped')`）、`374-378`（快照差异拒绝）；`backend/app/modules/dingtalk/service.py:108-115`（`OUTCOME_REJECTED` 文案“可以修正后重试”）。

**触发条件**：上一次 `create_inquiry_instance` 被钉钉**明确拒绝**（`OUTCOME_REJECTED`，落 `failed`），用户在 CRM 改了模板字段后点重试。

**后果**：`meta.submitted=True` 且 `retry=True`，`submit` 不调用 `save`，并用 `snapshot_edit_detail` 比较用户填的字段；任何改动都返回 409“不能改写审批快照”。与此同时 `_OUTCOME_HINT` 明确告诉用户“钉钉明确拒绝了这次发起，可以修正后重试”，形成不可执行的指引。用户只能改数据库或新建一张打样单。

**证据代码**：
```python
retry = previous is not None and previous.status in ('failed', 'not_sent', 'skipped')
...
if retry and not open_new_round:
    _detail = snapshot_edit_detail(meta.get("fields") or {}, payload.fields)
    if _detail:
        raise AppError(..., '重试必须使用已提交的原始资料，不能改写审批快照', 409)
```

**测试覆盖**：`test_sample_dingtalk.py` 有快照重试用例，但只验证“不改字段能重发”，没有覆盖“明确拒绝后改字段”的合法需求。

**建议**：区分 `not_sent`（本地错误，原样重发）与 `failed`（钉钉明确拒绝，允许修正后重发）；对 `failed` 放开快照比较或允许开新一轮，至少不要让文案承诺一个被代码禁止的动作。

### B11-11（P2）回调 target/redirect_uri 信任请求 Host

**位置**：`backend/app/modules/auth/router.py:255-275`（`_external_base`/`_dingtalk_redirect_uri`）、`325`（callback `target`）。

**触发条件**：应用端口可被直接访问，或反向代理没有固定/校验 `Host`，请求带 `Host: evil.example`。

**后果**：`_external_base` 直接返回 `proto://Host`；`dingtalk_callback` 的 303 `target` 和 `/authorize` 的 `redirect_uri` 都基于它。若代理把恶意 Host 透传，回调成功后会 303 到 `https://evil.example/login?dingtalk_ticket=...`，把一次性 ticket 带到攻击者域名（60 秒内可换 token）；同时可能构成开放重定向/钓鱼。代码注释承认“Host 的部署白名单由反向代理负责”，但后端没有兜底。

**证据代码**：
```python
host = request.headers.get("host") or request.url.netloc
...
return f"{proto}://{host}"
...
target = f"{_external_base(request)}/login"
return RedirectResponse(f"{target}?dingtalk_ticket=...", status_code=303)
```

**测试覆盖**：没有 Host 伪造/多域名用例。

**建议**：从配置读取 `public_base_url`（或允许的 host 列表）生成 redirect_uri/target；`Host` 不在白名单时用配置值或 400，不要把请求头直接拼进 Location。

### B11-12（P3）角色 `data_scope/status` 在 PATCH/CREATE 里只是字符串

**位置**：`backend/app/modules/user/schema.py:100-137`；`backend/app/modules/user/service.py:726-736、750-752`；`backend/app/modules/user/service.py:100`。

**触发条件**：管理员通过 `POST /roles` 或 `PATCH /roles/{id}` 传 `data_scope="ALL"`/`"all "`/`status=""` 等非法值。

**后果**：`PatchModel/CreateModel` 只校验长度，不校验枚举；非法 `data_scope` 落库后，`resolve_data_scope` 的 `if scope in order` 不命中，静默退回 `self`；非法 `status` 让角色不再 active，权限消失。没有提权（非法值不会变成 all），但会让角色“看起来配了全部、实际只看自己”，属于配置型故障，排查成本高。

**证据代码**：
```python
class RoleUpdate(PatchModel):
    data_scope: str | None = None
    status: str | None = None
```
`resolve_data_scope` 只接受 `self/department/department_and_sub/all`。

**测试覆盖**：`set_role_data_scope` 的 `RoleDataScopeUpdate` 有枚举用例；`POST/PATCH /roles` 没有非法 scope/status 用例。

**建议**：`RoleCreate/RoleUpdate` 复用 `DataScope`/`Literal["active","disabled"]`；服务层再兜一道 `if data_scope not in DATA_SCOPES: 422`。

## 四、补充维度检查

### 1. 负数 / 0 / 超大值 / 小数位

- 打样 `sample_fee` 走 `SampleUpdate`（Decimal、非负、2 位、范围）；`score` 走 `Decimal` 0–99990、`is_finite`。未发现 float。
- 用户/角色 `role_ids`、`department_id` 是整数 ID；`page/page_size` 有边界；密码有 bcrypt 字节上限。
- `allocate_username` 的后缀增长理论上可超过 64，但需要同名前缀 10^10 量级用户，不构成现实入口，不单列。
- OA 服务不计算金额；`first_image_attachment` 只读文件。
- 没发现 B11 新增的负数/0/超大金额入口。

### 2. 事务边界

- 登录：`open_session` + token + 审计同一次 commit；改密：新哈希 + 全量吊销同事务；refresh：行锁 + 新 token + 审计同事务。
- 钉钉首次建号：`begin_nested` 插入 user + user_roles，外层审计与 commit；失败回滚。
- `_signup_login_user` 在外部通讯录请求之后才取 advisory lock，并在锁内重读；未发现“检查用旧快照”建两个同钉钉号。
- **停用账号**：状态与“吊销会话”没有放在同一事务（根本没调用），见 B11-04。
- **部门/角色创建**：预检与插入之间无锁，见 B11-06。
- **OA 核定**：路由状态检查与服务层 claim 之间无事务保护，见 B11-07。
- **打样提交**：附件上传在 OA 创建之前，失败会留外部文件（B10-08 同源）；`sample.status='pending'` 与 `create_inquiry_instance` 的 commit 边界由后者提交，见逐文件记录。

### 3. 异常 / 日志 / 敏感信息

- `auth/router` 登录失败统一“用户名或密码错误”，不区分用户不存在；`/auth/permissions` 只返回自己的权限；`/auth/me` 不返回 `password_hash`。
- `dingtalk/service` 单条 OA 错误 `str(error)[:500]` 会落 `row.error` 并 `serialize` 给前端；可能带外部请求细节，但没有 token/secret；`_signup` 审计不写密码。
- `user/service.serialize_user` 不返回 `password_hash`，但返回 `mobile/email/dingtalk_userid`（B11-03）。
- `sample/dingtalk` 上传失败把 `str(error)` 和 HTTP 状态回前端（B10 同类），不含 token；日志 `warning` 记录 sample/filename/异常栈。
- 没发现把 appSecret/token/password 写日志的路径。

### 4. 审计

- 登录/refresh/logout、用户创建/更新/启停/角色、部门/角色/权限、打样提交成功、OA 发起/核定/撤回成功都有审计。
- **缺口**：停用账号不吊销会话、不写“会话被保留”；通讯录绑定只有聚合计数，没有逐人 before/after（B11-05）；OA 同步/手动 sync 不写审计（状态会变，但属于刷新）；打样/OA 外部调用失败路径无审计（B10-10 同源）。
- 审计不含密码/令牌；`personal_account` 的 before/after 只记 username 与 `password_changed`。

### 5. 测试覆盖

- 已读：`test_dingtalk_signup.py`、`test_personal_account.py`、`test_auth_session_expiry.py`、`test_dingtalk_oa_state_machine.py`、`test_dingtalk_submission_contract.py`、`test_sample_dingtalk.py`、`test_sample_dingtalk_fields.py`、`test_oa_sample_inquiry_flow.py`、`test_lead_assignee_user_access.py`。
- **未运行**：项目要求隔离库，未授权改测试；覆盖结论来自阅读。
- 未覆盖的重点：钉钉 state 绑定/CSRF、停用后会话、通讯录绑定审计/解绑、部门/角色并发创建、核定路由与服务层之间的并发窗口、合同通知失败、打样坏 schema、`failed` 后改字段、Host 伪造。
- 现有用例反而固化：`test_dingtalk_signup.py:51-52` 把 `123456` 默认密码写成期望；`test_lead_assignee_user_access.py` 把 assign 权限读全量用户当成预期。

## 五、本块没有看的部分及原因

| 文件/范围 | 状态 | 原因 |
|---|---|---|
| 前端 `frontend/src` | 未审 | 属于 F 系列块；本块只引用 `AppLayout.tsx:291` 证明“初始密码只有前端弹窗” |
| `backend/app/core/security.py` | 未读 | B11 只调用 `hash_password/verify_password/decode`，未审 bcrypt/JWT 实现；属于公共安全基础，不在 B11 清单 |
| `backend/app/modules/wecom/service.py` | 未读 | 只作为 `sync_users` 的“同一套纪律”参照，未做逐行对照 |
| `backend/app/modules/sample/application_form.py` | 未读 | Excel 生成器属于 B08/其他块；B11 只调用 `render_application_form` |
| `backend/app/modules/oa/service.py` | 已在 B10 审 | B11 的 `review_stuck_submission` 只调用，不重复审内部 |
| `backend/app/modules/dingtalk/client.py` | 已在 B10 审 | B11 登录/同步复用其接口；新发现只在服务/路由层 |
| `backend/tests` 全量运行 | 未运行 | 项目要求隔离库，未授权改测试 |
| 隔离库并发/故障注入 | 未执行 | B11-06/B11-07/B11-08 的并发结论来自代码路径，未做运行复现 |

## 六、结论

- P0：0；P1：2（B11-01 登录 CSRF、B11-02 默认密码无后端强制改密）；P2：9；P3：1。
- 最高优先级：B11-01、B11-02 都是“拿到登录身份”的问题；B11-03 是全量员工 PII 暴露；B11-04/05 是停用与绑定这两个身份生命周期缺口。
- B11-06/07 属并发/状态边界，B11-08/09/10 属批处理与外部模板健壮性；B11-12 是配置校验整洁度。
