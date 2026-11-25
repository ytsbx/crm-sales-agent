# 后端 P2 复核（第二批：权限/范围 + 数据完整性）

> 只看当前代码；日期 2026-11-24。只审查，未改业务代码。

## 一、结果

本批 12 条：

- **9 条已修**
- **2 条仍成立**：B06-10、B07-07
- **1 条收窄**：B11-03

## 二、权限/范围 7 条

| 编号 | 旧结论 | 当前代码 | 结论 |
|---|---|---|---|
| B06-10 | 采集总览越范围 | `erp/router.py:408-419` 只要求 `order:view`，直接 `collect_svc.collection_status(session)`；`collection.py:1598-1640` 查全表 `ExternalSourceRegistry`、全表 `ExternalObjectMapping`、全表 `ExternalRecord`，返回全局水位/来源/待匹配计数 | **仍成立** |
| B07-07 | 对账批次列表越范围 | `erp/router.py:703-714` `list_reconcile_runs` 只要求 `order:view`，调 `recon_svc.list_runs(...)` 不传 `owner_ids`；同文件 `list_diffs` 已按 `scoped_owner_ids` 过滤，批次列表没有 | **仍成立** |
| B11-03 | `GET /users` 全量 PII | `user/router.py:54` 已从“任意登录”改为 `user:manage / customer:assign / lead:assign` 三选一；但 `serialize_user`（`user/service.py:107-126`）仍返回 `mobile/email/wecom_userid/dingtalk_userid`。代码注释写明“用户确认复用完整员工列表”，分配角色可读属于已确认口径 | **原越权已修；剩余为已确认的数据最小化项，降 P3** |
| B12-04 | 删文件锁后不复查范围 | `file/router.py:500-525`：锁前 `can_access_file`、`with_for_update()` 取行、锁后再 `can_access_file` 一次；引用/历史证据检查均在锁内 | 已修 |
| B14-03 | 合并预览泄露专属价 | `customer/tags_router.py:181-199` 预览先 `get_visible_customer` 两次，再按 `admin` 或 `product:view` 决定是否保留 `customer_price` 冲突的 `items`；无权限时清空明细 | 已修 |
| B17-06 | 产品统计含私密草稿 | `analytics/service.py:1250-1251` 报价侧查询已加 `draft_scope(Quote, user.id)` 与 `or_(~private_draft(QuoteVersion), ...)`，私密草稿不再进入产品统计 | 已修 |
| B20-03 | 商机 assign 允许 owner=null | `opportunity/schema.py:131-132` `owner_id: int = Field(gt=0)`，注释“用户已确认拒空”；`router.py:1478` 再 `assert_owner_active` | 已修 |

## 三、数据完整性/流程 5 条

| 编号 | 旧结论 | 当前代码 | 结论 |
|---|---|---|---|
| B13-03 | 合并后 pending 撞单卡死 | `customer/tags.py:703-706` 合并前对 `_pending_merge_cases(...)` 取行锁，若仍有第三家客户的待裁定记录直接 409，并给出处理提示；预览 `:545-551` 也会把它列为 blocking | 已修 |
| B13-04 | 继承负责人未校验在职 | `customer/tags.py:848-861` 合并时先查 `source.owner_id` 是否 `status='active'`；在职才继承，否则保留公海并写归属历史 `kept_public_inactive_source_owner` | 已修 |
| B18-03 | 冻结缺键回落实时却仍标 frozen | `analytics/targets.py:725-740` 缺键时把 metric 记入 `live_metrics`，`actual_frozen = month in frozen and not live_metrics`，并返回 `live_actual_metrics`；不再把缺项说成冻结值 | 已修 |
| B20-02 | 删商机不查下游 | `opportunity/router.py:1234-1250` 删除前统计未取消订单、有效报价，有任一即 409；注释“用户已确认阻断” | 已修 |
| B20-04 | 跨客户复制需求 | `opportunity/router.py:1413-1418` 目标与来源 `customer_id` 不一致直接 422，禁止把 A 客户价格/规格带进 B 客户 | 已修 |

## 四、累计 P2 状态

- 第一批“已修”声明 13 条：12 条确修、B20-06 仍成立。
- 第二批 12 条：9 条新确认已修、2 条仍成立、1 条收窄。
- 因此 P2 不能笼统说“全修好了”：至少 **B20-06、B06-10、B07-07** 仍是当前代码可复现的问题。

## 五、下一批

- 幂等/重复 12 条：B04-02、B04-13、B05-04、B07-04、B13-06、B13-10、B19-09、B20-09、B21-06/07、B22-07/13。
- 审计 10 条与外部依赖 4 条随后。
