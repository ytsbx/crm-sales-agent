# 后端 P2 复核（第四批：审计 + 外部依赖）

> 日期 2026-11-24，只读当前代码。只审查，未改业务代码。

## 一、结果

本批 14 条：

- **3 条已修**：B14-05、B10-06、B15-04
- **3 条部分修**：B18-08、B22-09、B22-10
- **8 条仍成立**：B05-01、B07-06、B07-15、B13-08、B19-10、B20-12、B10-04、B11-08

## 二、审计 10 条

| 编号 | 当前代码 | 结论 |
|---|---|---|
| B05-01 | `payment/service.py:281-340` `rebalance_order_allocations` 重排全部已确认回款，但 `allocate_payment`（`:184-240`）不写逐笔“原期/新期/金额”审计；路由只审计 `receivable_plan` 的 before/after（`payment/router.py:491-499`） | **仍成立**：回款分配变化没有独立审计 |
| B07-06 | `erp/router.py:771-795` 售后核定审计 `before` 只有状态/处理/备注/类型，`after` 只有状态/处理/备注；`financial_adjustment_type/amount` 进了 `confirm_diff` 结果，但没进审计 | **仍成立**：审计不能复算调整金额/类型 |
| B07-15 | `contract/router.py:711-735` 签署审计 `before` 只有状态，`after` 只有合同号/文件 id/复用原因；没有金额、报价版本、OA 实例 | **仍成立** |
| B13-08 | `customer/router.py:399-415` 转移审计仍是客户 before/after；服务返回的 `documents` 只回给前端，不写审计 | **仍成立**：审计缺单据 ids/数量 |
| B14-05 | `customer/tags_router.py:428-449` 批量转移审计已带 `affected_customer_ids`、`already_assigned_customer_ids`、`unavailable_customer_ids`、`documents_moved`、`documents_skipped` | 已修 |
| B18-08 | `cases/router.py:126-134` 创建审计仍只有 `after={"title"}`；更新 `:237-245` 已有 before/after | 部分修（P3）：创建/提交/评审/删除仍不完整 |
| B19-10 | `inquiry/router.py:301-309` 更新审计只有 `after`；删除 `:342-349` 无 before/明细 | **仍成立**（P3） |
| B20-12 | `opportunity/router.py` 删除商机仍不记下游关系；整批替换/复制只记 count，删明细不记报价引用，失单只有 after | **仍成立**（P3） |
| B22-09 | `task/router.py:715-725` 完成审计已有 `before={"status"}`；取消/批量完成/改期是否逐条 before 与原截止日仍不齐 | 部分修（P3） |
| B22-10 | `sample/router.py:863-882` 寄样、`:926-940` 签收已有 before 状态 + after 详情；审批/反馈/制作的金额与依据快照仍不完整 | 部分修（P3） |

## 三、外部依赖 4 条

| 编号 | 当前代码 | 结论 |
|---|---|---|
| B10-04 | `dingtalk/client.py:413-434` `get_process_schema` 仍裸 `resp.json()`，`httpx` 超时/非 JSON 未包；`:442-465` `upload_media` 同样裸解析；`:560/598/629` 等读接口仍直接 `resp.json()` | **仍成立**：读接口故障仍可能 500 |
| B10-06 | `oa/service.py:61-105` 发起 OA 前先 commit 预约；网络返回后 `lock_receipt()` 重新锁业务行与 `oa_instances` 行，核对 `status/attempt_count/business_status/superseded_at`，变了就置 `not_sent` 409 | 已修 |
| B11-08 | `dingtalk/service.py:1159-1161` 合同审批终态直接 `await _notify_contract_review_outcome(...)`；`:1217` 直接 `notification_service.notify`，没有单条 try；一条通知失败仍会打断整批回写 | **仍成立** |
| B15-04 | `wecom/service.py:1920/1928/1936/1952/1960/1977/1992` 对商机/任务/打样/订单/报价/草稿都判断 `owner_id is None` 为“已被清空负责人”并抛冲突，不再当成仍属离职人 | 已修 |

## 四、结论

- 审计类多数仍只记“谁动了”，不记“改了哪些钱/哪些单据/哪些证据”，B05-01、B07-06、B07-15、B13-08 仍是有实际对账价值的缺口。
- 外部依赖里 B10-06、B15-04 已修；B10-04 裸解析、B11-08 单条通知打断整批仍成立。
- 下一批：金额/精度剩余条目 + 4 条建议降 P3 的展示类。
