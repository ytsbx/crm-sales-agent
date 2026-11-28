# B11–B15 高严重度复核（证伪）

> 2026-11-28 更新：**B11-01 已修**（state 绑定 HttpOnly + SameSite=Lax cookie，回调同时校验）；**B11-02 按主人决定关闭、不处理**。下表保留复核时点结论，当前状态以 `review/LEDGER.md` 为准。

- 范围：B11/B12/B13/B14/B15 的 P1；P2 未逐条复核。
- 方法：重读登录 state、文件 category、客户 360/合并、转移锁、企微归一与候选、异常落库；能静态断言的进测试，异常原文泄露用 httpx 真异常做控制。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b11_b15_highlights.py -q`
- 结果：`10 failed, 1 passed`。通过的是控制项“HTTPStatusError 字符串确实带 access_token”；其余失败均对应“修复后应有”的结构条件。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B11-01 P1 | **确认** | `issue_login_state` 只写进程字典，`consume_login_state` 只判存在/过期，不绑定浏览器；攻击者用自己的 state+code 可把受害者浏览器登录成攻击者账号 |
| B11-02 P1 | **确认（初始密码是既定口径，缺后端闸门）** | 自动建号 `hash_password("123456")`，`initial_password_pending` 只在 `/auth/me` 返回；`get_current_user` 不检查，前端弹窗挡不住直接调 API |
| B12-03 P1 | **确认** | `POST /files/upload` 与挂载都直接落客户端 `category`；可把普通附件标成 `signed`，`list_signed_files` 就把它当签署原件，绕过 `sign_document` 的 OA/原稿/生成稿校验 |
| B13-01 P1 | **确认行为，建议降 P2** | 客户 360 的 `order_base` 只有 `customer_id`，`func.sum(total_amount)` 把 `cancelled` 单算进累计成交；但这是展示汇总，不参与结算/审批，`pending_receivable` 已正确排除 |
| B13-02 P1 | **确认** | 合并冲突只判 `given.get(key)` 真值；税号分支唯一合法值是 `confirm`，却接受任意非空字符串，能把两家不同法律主体合并且不可逆 |
| B14-01 P1 | **确认（并发窗口）** | 路由先 `get_visible_customer`/`assert_customer_visible`，`transfer_customer` 拿行锁后只按 `only_from_owner_id` 复核（正常转移不传），不复查数据范围；并发转手后可越权改归属 |
| B14-02 P1 | **确认（并发窗口）** | `price:manage` 只在路由锁外预检查；`merge_customers` 不接收 user/价格权限，锁内新出现 `customer_price` 冲突仍按调用方给的 resolution 归档 |
| B15-01 P1 | **确认** | `bind-customer`/`create-customer` 只要求 `wecom:manage`；`bind_customer` 用 `get_customer_or_404`（非可见范围），`create_customer_from_contact` 直接调不校验 `customer:create` 的服务 |
| B15-02 P1 | **确认** | `unbound_candidates` 路由只要求 `wecom:view`，service 注释明写“故意不传 owner_ids”；候选返回全库客户的名称/等级/地区/负责人/公海状态 |
| B15-03 P1 | **确认** | `client._call` 的 `raise_for_status()` 抛 httpx 异常，字符串含带 `access_token` 的 URL；单条同步/转接/通知失败把 `str(error)` 落进 job/detail/notification，整批 `_translate` 不覆盖这些路径 |

## 四条需要单独说明

### B11-01：state 是“一次性”但不是“同一个浏览器”

`_login_states` 只存 `state -> 过期时刻`，`issue_login_state` 不读任何浏览器标识，`consume_login_state` 也不比较。攻击者走一遍 `/auth/dingtalk/authorize` 拿到合法 state，用自己的钉钉授权拿到 code，把回调链接发给受害者；受害者浏览器打开后，`consume_login_state` 通过，`resolve_login_user` 按 code 换出攻击者身份，随后 `open_session` + `issue_session_token` 在受害者浏览器落成攻击者的会话。审计只记一条正常的 `dingtalk_login`。

### B11-02：默认密码是主人定的，但“未改密”没有后端后果

`_signup_login_user` 的注释写“用户明确指定：姓名全拼登录名、初始密码 123456，首次进入告知本人”。所以 123456 本身不是审查员臆造，是既定口径。缺陷在后端只把 `initial_password_pending` 当展示字段：`get_current_user` 不检查，任何知道姓名拼音的人都能在员工首次扫码前用 123456 登录，并拿到 `salesperson` 的 `self` 范围权限。既定口径可以保留，但“未改密只能访问 `/auth/me`、改密、登出”这道闸门必须加在服务端。

### B13-02：税号冲突的“确认”没有值校验

`unresolved` 只看 `given.get(row["key"])` 是否真；税号冲突的 `options` 只有 `confirm`，但代码没有像 `customer_price` 那样校验 `choice` 白名单。`resolutions={"tax_no":"anything"}` 就能通过，合并继续，来源税号丢失，`conflicts_applied` 还把 `anything` 写进审计。价格冲突有白名单、税号没有，这是同一张表里的不对称，不是“业务允许模糊确认”。

### B15-03：整批失败做了脱敏，单条失败没有

`_translate` 只包住整批 job 的异常，测试 `test_wecom_sync_failure.py` 也断言整批错误不含 secret。但 `sync_follow_relations` 的单条 `except` 直接 `str(error)[:80]`，`transfer_relations`/`retry_transfer` 直接 `str(error)[:500]`，`notification` 直接 `f"{type}: {error}"`。httpx 的 `HTTPStatusError.__str__` 会带请求 URL，而 `_call` 把 `access_token` 放在 query 上；控制测试已证明 `SECRET-TOKEN` 会出现在异常字符串里。`wecom:view` 能读 `sync-jobs` 的 `detail/error_message`，这条链是实的。

## 本批最大漏项（B15-01 + B15-02 的组合）

**只有 `wecom:view` 的人可以先把全公司客户枚举出来，再用只有 `wecom:manage` 的账号把企微联系人挂到任意客户上；两步都不需要任何 `customer:*` 权限。**

审查员把这两条分开写成“候选跨范围泄露”和“归一写接口绕开客户权限”，但没有把它们当成一条完整攻击链：

1. `GET /unbound-contacts/{id}/candidates` 只要求 `wecom:view`，service 不传 `owner_ids`，返回全库客户 `id/name/level/region/owner_id/pool_status`；
2. `POST /unbound-contacts/{id}/bind-customer` 只要求 `wecom:manage`，`get_customer_or_404` 不判范围，可以把联系人写进上一步枚举到的任意私有客户，还能设 `is_primary`；
3. 两步的审计都记在 `business_type=integration`，按客户审计查不到这条挂接。

单看每步都像“信息泄露”或“权限粒度粗”，合起来是跨数据范围的客户写入。修的时候不能只给 candidates 加 `owner_ids` 或只给 bind 加 `customer:view`，得让“候选可见范围”和“绑定目标范围”用同一个 `scoped_owner_ids` 口径。

## 残余/未覆盖

- B11-03..12、B12-01/02/04..06、B13-03..11、B14-03..08、B15-04..08 的 P2 未逐条复核。
- B13-01 的行为确认，但严重度建议从 P1 降 P2（展示口径，不参与结算）。
- B14-01/B14-02 是并发窗口，静态断言只能证明“锁内没有复查”，不能证明实际竞态必现；要跑真并发需 PostgreSQL 两连接，本批未搭。
- B15-03 的修复点分散在 wecom/service 与 notification/service 两处，单改 `_translate` 不够。
