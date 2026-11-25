# 后端实际代码复核（第一批：P0 + P1 全量）

> 口径：不看 `QUALITY_PROGRESS.md` 的“已修复”，只看当前 `backend/app` 代码与可执行测试。
> 报告日期：2026-11-24。只审查，未改业务代码。

## 一、总数

后端 P1 原 40 条，按当前代码复核后：

- **13 条仍确认**
- **8 条降级**
- **19 条证伪**

另有原 P0 B04-01 属于业务口径（转单是否保存客户接受金额快照），不是可复现代码越权，维持“待业务拍板”，不计入 P1。

## 二、P1 当前状态总表

| 编号 | 当前状态 | 实际代码证据 |
|---|---|---|
| B01-01 | 证伪 | 审批金额边界用 Decimal/字符串精度比较，默认档位不触发 float 翻档 |
| B02-01 | 证伪 | `quote/service.py` 的 `ensure_charge_change_allowed` 先判 `accepted_at`，接受后改运费/清 `sent_at` 被拒 |
| B02-02 | **证伪（本轮新）** | `quote/router.py:388` `_require_quote_recipient` 对目标负责人取角色权限，再 `assert_customer_visible` 校验目标客户；PATCH `:427`、复制 `:706` 都调用 |
| B03-01 | 证伪 | 价格权限写入口先按角色加锁再读改写；并发双写第二个 409 |
| B03-02 | 证伪 | 草稿 PATCH 省略单价保留原值，显式清空才置空 |
| B03-03 | 证伪 | 建报价、转单、订单草稿均过 `ensure_currency_allowed`，固定 CNY |
| B04-02 | 降 P2 | 后端建单支持请求键回放；前端无手工建单入口，仅裸调 API 无键 |
| B04-03 | 降 P2 | 手工切“已发货”要求正数实发量；计划批次不能当发货凭据 |
| B05-01 | 确认（行为口径） | 超收金额按到期日自动抵后续期；逐笔分配审计仍缺失 |
| B05-02 | 降 P2 | 后端手工应收支持请求键；前端已走整份分期生成，无手工入口 |
| B05-03 | 证伪 | 新建/调高应收均调 `assert_receivable_amount_fits` |
| B05-04 | **降 P2（本轮调整）** | 后端 `request_key` 仍可选；但 CRM 登记回款页面已按表单生成稳定键，缺口只剩“直接调接口且不带键” |
| B05-05 | 证伪 | ERP 缺结果码/布尔/小数/非数字判“结果未知”，走推单核对 |
| B06-01 | 证伪 | 新采集不写自身为父订单，人工匹配兼容旧自指 |
| B06-02 | 证伪 | 人工匹配只重放同系统/店铺/父订单号的待匹配记录 |
| B06-03 | 证伪 | 外币记录逐条拒收并记日志，人民币正常落库，分页继续 |
| B06-04 | 确认 | `erp/service.py:172-186` 仍把 `total_amount/quantity/unit_price/amount` 转 `float` 再上报 |
| B06-05 | 确认 | ERP 订单报文只推 `pay_amount` 与行金额，未逐项推费用/税额口径，对账依赖总额 |
| B07-02 | 确认 | `verify_instance` 只验流程码/发起人/合同编号/名称/有附件，不验金额、客户主体、附件 fileId/哈希 |
| B07-03 | 确认 | 合同正文/抬头是生成时快照，OA `amount` 取实时报价版本或订单总额，合同无冻结金额列 |
| B07-04 | 降 P2 | 页面上传表单打开即生成稳定请求键；后端同键防重，只有旧调用不带键才重复 |
| B09-01 | 确认 | 传 `release_stage` 但 `business_stages` 缺该阶段时，回退 `row.status == approved` |
| B09-02 | 降 P3 | 默认 5 万档、金额 2 位/毛利 4 位，float 翻不了档；结构上仍应统一 Decimal |
| B10-01 | 确认 | `_apply_sample_summary` 缺 `business_stages.sample` 时用整体 `approved` 释放打样单 |
| B11-01 | 确认 | 钉钉 `state` 只做一次性消费，存在进程内字典，不与浏览器会话/用户绑定；登录 CSRF 仍可构造 |
| B11-02 | 确认 | 默认密码 `123456` 建号后仅置 `initial_password_pending`，业务接口未拦截未改密用户 |
| B12-03 | 证伪 | 通用上传把旧 `contract + signed` 映射为 `CONTRACT_PENDING_SIGNATURE`，正式登记仍需审批/原件/权限 |
| B13-01 | 降 P2 | 客户 360 汇总含取消单只影响展示，不参与结算，属展示口径 |
| B13-02 | 确认 | 客户税号只做非空字符串比较，不校验格式/校验位；两家不同主体可带任意“税号”合并，仅弹确认 |
| B14-01 | 确认（并发窗口） | `transfer_customer` 行锁内只复核 `only_from_owner_id`；普通转移不传，锁后不复查数据范围 |
| B14-02 | 确认（并发窗口） | 价格权限只在路由锁外预检查；`merge_customers` 锁内新增 `customer_price` 冲突仍按调用方 resolution 归档 |
| B15-01 | **证伪（本轮新）** | `wecom/router.py:267` 关联客户追加 `customer:update`；`service.py:593` 改 `get_visible_customer(..., for_update=True)`；建客户路由 `:291` 追加 `customer:create` |
| B15-02 | **证伪（本轮新）** | 候选路由 `:251` 追加 `customer:view`；`unbound_candidates` 传 `scoped_owner_ids(session, user)`，只返回范围内客户 |
| B15-03 | 证伪 | 企微异常原文不再落库，客户端网络失败转安全提示，请求链接不进失败记录 |
| B16-01 | 证伪 | 补投不再把 `wecom_error` 原文写审计/回显 |
| B17-01 | 证伪 | 无 `order:view` 时 AI 不给依赖订单额的待回款参考值 |
| B22-01 | 确认 | `sample/router.py:1422 _assert_active_owner` 只判存在 + `active`，不判数据范围；`sample:manage` 可把负责人指到范围外用户 |
| B22-02 | **证伪（本轮新）** | `POST /samples/{id}/approve` 在 `:701-707` 直接 409，统一走同序 OA；本地自批入口已关闭 |
| B22-03 | **证伪（本轮新）** | 已批准单改费用/工时/制作负责人进 `REVIEW_LOCK_FIELDS`，经 `_gate_part_lock` 退回待审批；车间依据字段也纳入重审 |
| B23-01 | **降 P2（本轮调整）** | `settings/service.py:502` 用 `_strict_amount` 拒绝超大值，角色码校验存在；但 `role_codes=[]` 仍合法，运行时空角色集跳过角色限制，只剩 `quote:approve` 兜底 |

## 三、本轮新推翻的五条

### B02-02 报价负责人/复制不校验数据范围

`_require_quote_recipient` 不再只查用户存在：

1. 取目标负责人当前角色与权限；
2. 构造 `CurrentUser`，带 `resolve_data_scope(roles)`；
3. 要求 `admin` 或 `customer:view`；
4. `get_customer_or_404` 后调用 `assert_customer_visible`，范围外转 `DATA_SCOPE_DENIED`。

改负责人 `:427`、复制换负责人/换客户 `:706` 都走同一函数。旧结论“只要 `quote:manage` 就能把报价挂到范围外负责人”已不能复现。

### B15-01 企微绑客/建客绕过客户权限

关联已有客户：路由要求 `wecom:manage` + `customer:update`，服务用 `get_visible_customer(..., for_update=True)`，目标客户不可见直接拒。
新建客户：路由要求 `wecom:manage` + `customer:create`，不再只靠 `wecom:manage` 建客户。

### B15-02 企微候选返回全库客户

候选路由追加 `customer:view`，服务把 `scoped_owner_ids(session, user)` 传给 `find_duplicate_customers`。主管只拿本部门范围，业务员只拿自己范围，`all` 角色才全量。

### B22-02 打样本地自建自批

`POST /samples/{sample_id}/approve` 当前固定返回 409，提示统一在同序 OA 办理。本地审批入口已关闭，“非管理员自建自批”不再可达。

### B22-03 已批准打样单改费用/工时

`REVIEW_LOCK_FIELDS = ("sample_fee", "estimated_hours", "production_owner_id")`，与 `PART_LOCK_FIELDS` 分开处理：已批准未制作时改动会 `_reopen` 回待审批，已制作/已寄样直接拒绝并引导开修订版。

## 四、附带发现：F10-01 的越权链已断

F10-01 原 P0 依赖两条：

- 候选只要求 `wecom:view`，服务不过滤数据范围；
- 绑客只要求 `wecom:manage`，服务用 `get_customer_or_404` 不校验范围。

当前代码两条都已改：

- 候选：`wecom/router.py:251-258` 追加 `customer:view`，`wecom/service.py:561-573` 传 `scoped_owner_ids`；
- 绑客：`wecom/router.py:267-268` 追加 `customer:update`，`wecom/service.py:593` 用 `get_visible_customer(..., for_update=True)`。

结论：**F10-01 作为 P0 已不再成立**；前端页面即使仍只按 `activeId` 查询，也拿不到范围外候选、绑不了范围外客户。前端台账需把 F10-01 从 P0 改为证伪/已修，另行复核前端展示层是否还有残留。

## 五、下一批

- P2 全量逐条读当前代码（按 B01–B05、B06–B10、B11–B15、B16–B20、B21–B25 分批）。
- P3 只做抽样与“已声称修复”的项，不重复已证伪结论。
- 每批都落一份本文件同结构的证据表，不引用 `QUALITY_PROGRESS.md` 当证据。
