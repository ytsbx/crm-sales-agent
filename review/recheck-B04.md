# B04 复核（专门证伪）

- 复核对象：`review/blocks/B04-金额核心-订单.md`（P0×1、P1×2、P2×5、P3×7）
- 环境：Python 3.12（`backend/.venv`）
- 方法：重读报价锁版/费用闸、订单转单/状态机/取消、交期变更、ERP 回写、发货批次；能落的进 `review/repro/test_b04_p0_p1.py`；不连数据库、不改业务代码。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b04_p0_p1.py -q`
- 结果：`5 failed, 1 passed`；通过的 1 条正是“P0 路径被 sent_at 挡住”的控制项。

## 结论表

| 编号 | 结论 | 一句话 |
|---|---|---|
| B04-01（P0） | **误报（升级不成立）** | 改运费会清 `sent_at`，转单要求 `sent_at` 非空，`mark_sent` 又拒绝已接受版本；改完转不了单。金额不一致仍停在报价层，属 B02-01 P1 |
| B04-02（P1） | **确认** | `OrderCreate` 无 `request_key`，手工建单无幂等 |
| B04-03（P1） | **确认** | `pending → shipped`、`shipped → pending` 都放行，无流转矩阵/批次闸 |
| B04-04（P2） | **确认** | 转移负责人只校验 active，没有 `ensure_in_scope` |
| B04-05（P2） | **确认** | 交期变更只拦 `cancelled`，`completed` 可改 |
| B04-06（P2） | **确认** | ERP 拉取回写只要 `order:view` |
| B04-07（P2） | **误报** | `items` 缺省=计划量全发是 schema 明写的契约；前端 `OrderDetailPage` 每次传全行，不存在“以为只改 A” |
| B04-08（P2） | **确认** | `old_ship=None → delta=None`，批次计划日静默跳过 |
| B04-09（P3） | **确认** | 取消接口无 `reason`，`completed` 订单取消会被 422 卡死 |
| B04-10（P3） | **确认** | 取消订单只置批次 `cancelled`，不 `drop_batch_node` |
| B04-11（P3） | **确认** | `items` 只有 `min_length=1`，无上限 |
| B04-12（P3） | **确认** | 手工单不生成应收计划，“收款”节点永不完成 |
| B04-13（P3） | **确认** | 转单重试 409，不是幂等回放 |
| B04-14（P3/B05） | **无法确定** | 模型缺唯一约束/外键属实；是否可被服务层兜住需 B05 实查 |
| B04-15（P3） | **确认已关闭** | 唯一约束与迁移都在，函数无调用点 |
| B04-16（P3） | **确认（影响有限）** | `except` 里 `session.rollback()` 回滚整个事务；当前路由此前无写 |

## B04-01：P0 为什么是误报

复现链路：

1. 客户接受后，`version.accepted_at`、`sent_at` 都有值，`quote.status=accepted`。
2. 改运费时 `ensure_charge_change_allowed` 允许（锁版后只放行物流），但会把 `version.sent_at = None`（`quote/service.py:155-160`）。
3. 转单 `create_order_from_quote` 硬性要求 `Version.sent_at is not None`（`order/service.py:226-228`）→ 422。
4. 想重新发送补回 `sent_at`：`mark_version_sent` 明确拒绝 `accepted_at is not None`（`quote/lifecycle.py:73-76`）→ 422。
5. 想重新接受：`ensure_customer_response_allowed` 拒绝 `accepted_at is not None` → 422。

所以“接受后改运费 → 直接转单”这条 P0 路径走不通。金额不一致确实存在，但它停在报价档案层，对应 B02-01 的 P1，不会流进正式订单。审查员把 B02-01 的 P1 重复计成了 B04 的 P0，严重度需要降。

控制项 `test_b04_01_p0_control_...` 通过（改运费后 `sent_at=None`，`mark_sent` 抛错）；`test_b04_01_accepted_version_freight_change_should_be_rejected` 失败（改运费本身仍被允许），证明底层问题真实但等级不是 P0。

## B04-02 / B04-03：两条 P1 都成立

- B04-02：`OrderCreate` 字段里没有 `request_key`，`POST /orders` 路由也没有 `idempotency.reserve`；`order_no` 唯一只能防重号，不能防重复建单。草稿接口 `drafts_router.py` 反而有 `request_key`，口径不一致。
- B04-03：`change_status` 只做“状态名在白名单、未取消、completed 的未发量闸门”，`shipped/delivered` 没有任何批次或实发量判断。测试里 `pending → shipped` 与 `shipped → pending` 都成功，说明状态机确实可被手工改写。

## B04-07：误报，不是 P2

`schema.py:193-194` 的 docstring 明写“items 缺省 = 本批计划量全发；给出时逐明细覆盖”。这是公开契约，不是“未传默认全发”的意外。

前端 `OrderDetailPage.tsx:625-650` 的实发提交是 `actualShipmentRows.map(...)`，**每次把本批所有行都传上去**，不会只传 A 行。审查员说的“前端以为只改了 A，实际 B/C 全发”没有触发入口。直接调 API 的第三方要按契约省略，也不算被误导。若仍想加固，可改成“items 非空必须覆盖全批”，但那是契约变更，不是现有缺陷。

## B04-05 / B04-06 / B04-08：确认

- B04-05：`schedule.create_change` 与 `confirm_change` 都只拦 `cancelled`，`completed` 不在判据里；而 `planning.before != after` 必然成立（交期变了），预览不会把它挡掉。已完成订单的 `delivery_date` 与批次计划日可被重写。
- B04-06：`erp/router.py:348-356` 的 `GET /integrations/erp/orders/{id}/status` 只要求 `order:view`，内部却调用 `order_service.change_status` 写状态与 history。B06 复核也已确认。
- B04-08：`preview` 里 `delta = ... if old_ship else None`，批次循环 `if batch.planned_date is None or delta is None: continue`；旧交期为空时批次计划日完全不平移，静默跳过。

## P3 简述

- B04-09：取消接口无 body/reason，`completed` 订单能走到 `change_status` 的“回退必须填理由”而 422；接口本身无解。
- B04-10：取消订单只把 `OrderShipmentBatch.status` 置 `cancelled`；`drop_batch_node` 只在 `cancel_shipment_batch` 调用，动态节点残留。
- B04-11：`OrderCreate.items` 只有 `min_length=1`；`OrderDraftCreate` 有 200 上限，两个入口不一致。
- B04-12：手工 `create_order` 没有 `ReceivablePlan`；`sync_payment_milestones` 的 `settled` 要求 `planned > 0`，所以“收款”节点永远不完成。
- B04-13：同一版本重复转单直接 409；订单已生成但用户看到失败，提示与事实相反。
- B04-14：`receivable_plans` 只有普通索引；`payment_records.receivable_plan_id` 无外键。服务层是否强制 `plan.order_id == payment.order_id` 未查，交 B05。
- B04-15：模型与迁移的唯一约束已存在，函数无调用点，关闭成立。
- B04-16：`except IntegrityError` 里 `await session.rollback()` 回滚外层事务；当前转单路由在调用前没写别的，实际影响有限，但库函数不应替调用方定事务边界。

## 漏项：审查员漏掉的最大风险

**已发货/已签收的订单可以被静默取消，未回款应收全部作废，已发货物却不退回、不冲减、不要审批。**

`cancel_order`（`order/router.py:911-1035`）只拦三件事：已确认回款、已签合同、已取消订单；没有检查“已有实发批次/已签收”。随后：

- 所有 `status != paid` 的应收计划直接置 `cancelled`（停止催收/逾期提醒）；
- `planned` 批次置 `cancelled`，`shipped` 批次原样保留；
- 订单状态走 `change_status(..., allow_cancel=True)`，`delivered → cancelled` 没有任何拦截（`completed` 才会因为缺 reason 被 422）。

触发场景很普通：货已签收、客户还没付款、也没签合同，主管点“取消订单”——货在客户手里，系统里的应收和催收全被抹掉，成交额也随之消失。这不是“已发货能否取消”的业务口味问题，而是当前没有任何退货/冲减/审批兜底的财务控制缺口，至少应要求退货单或主管审批并留原因。

测试 `test_b04_missed_delivered_order_cancel_should_require_return_or_approval` 当前失败（`delivered → cancelled` 成功），证明状态闸确实没拦。

## 复现文件

- `review/repro/test_b04_p0_p1.py`
- 输出：`5 failed, 1 passed`；失败项分别是：接受后改运费未拒绝、手工单无幂等键、`pending→shipped`、`shipped→pending`、`delivered→cancelled`；通过项是“改运费后 sent_at 被清空、mark_sent 拒绝已接受版本”的控制项。


---

## 2026-11-23 复测更正（以当前代码为准）

本节覆盖上文旧结论中与当前代码不一致的部分。

- 当前 `quote/service.py:130-135` 已在 `ensure_charge_change_allowed` 最前面判断 `version.accepted_at`；客户已接受后，新增/修改/删除运费均直接 42204，不会清 `sent_at`，也不会 `recalc_version`。
- 因此 `repro/test_b04_p0_p1.py::test_b04_01_accepted_version_freight_change_should_be_rejected` 现在通过，`test_b04_01_p0_control_change_clears_sent_at_then_conversion_is_blocked` 因“预期放行”而失败；旧“5 failed, 1 passed”结果与旧代码对应，已过期。
- B04-01 的 P0 升级仍不成立，且比旧结论更彻底：连“接受后改运费”本身都被拦。保留的只是“转单没有客户接受金额快照”的设计缺口，标待确认，不再按 P0 计。
- B04-02/B04-03 及“已签收订单可静默取消”等结论不受影响，仍按上文。
