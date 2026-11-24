# B20–B23 P2 逐条复核（证伪）

- 范围：B20-01..06（6）、B21-01..04（4）、B22-04..06（3）、B23-02..03（2），共 15 条。
- 方法：逐条重读路由、schema、服务层写入路径和模型列宽；每条一个源码/模型断言。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b20_b23_p2.py -q`
- 结果：`15 failed, 0 passed`。没有发现误报。
- 2026-11-23 复测更正：`B20-05`、`B21-02`、`B22-04`、`B23-03` 已被当前代码修掉，仍成立 11 条；旧测试断言失败只说明用例还按旧代码写。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B20-01 | **确认** | 漏斗/看板 `float(amount or 0)`；360 概览 `version_amounts[...] = float(amount)`、`item_quantity` 用 `float(sum(...))`，全链路无 exact |
| B20-02 | **确认** | `delete_opportunity` 只 `get_visible` + 软删 + 审计；不查 `SalesOrder`/报价/合同。对照 `lose_opportunity` 明确查 `live_orders` 并 422 |
| B20-03 | **确认** | `OpportunityAssign.owner_id: int | None = None`；`assert_owner_active(None)` 直接返回 None；清空后 `data_scope` 对 `owner_id is None` 默认 403 |
| B20-04 | **确认** | `copy_items_from` 只校验两个商机各自可见，不校验 `target.customer_id == source.customer_id`；来源 `target_price/specification` 原样写入目标 |
| B20-05 | **不成立（已修）** | 当前 `sku_fields_from_row` 走 `normalize_numeric`/`parse_numeric_field`，按 `NUMERIC_RULES` 校验 12 位、3/4 位小数、非负、整数；13 位长度、5 位小数、负数、1.9 装箱数均被拒 |
| B20-06 | **确认（已实测）** | `normalize_field_value` 文本字段只 `strip()`；300 字 `name/specification/color/material/package_type/unit` 全部通过，列宽分别 200/200/64/64/64/16，超长到 `flush()` 才 500 |
| B21-01 | **确认** | `sample/service._f = round(float(value),4)` 用于 `sample_fee/shipping_fee/quantity/craft_points`，无 exact |
| B21-02 | **不成立（已修）** | 当前 `SampleItemInput.item_name`(200)、`SampleConfirm.remark`(255)、`SampleApprove.reject_reason`(255)、`SampleShip.carrier/tracking_no`(64) 均已有 `max_length`；300 字全部被 Pydantic 拒 |
| B21-03 | **确认** | 产品导入 `io_router:179`、SKU 导入 `:421` 都是 `except Exception` → `f"写入失败：{str(exc)[:160]}"`；DB 表名/列名/约束名会进失败清单 |
| B21-04 | **确认** | `product_insight.router` 的 `price_assumption` 转 float；`_content_snapshot` 把 Decimal 转 float；转需求描述 `float(row.price_assumption)` |
| B22-04 | **不成立（已修）** | 当前 `revise_sample` 已复制 `sample_type/product_category/maker_unit/estimated_hours/craft_points/sample_purpose/urgency/sample_situation/deliver_date/sample_maker` 等全部单头字段；300 字长度问题另算 |
| B22-05 | **确认** | `task/router._active_owner` 只判存在 + `status == active`，不判 `scoped_owner_ids`；创建/`PATCH`/`/assign`/`/transfer`/批量交接都用它 |
| B22-06 | **确认** | `batch_handover_tasks` 在 `notify(...)` 后直接 `commit` 返回；没有 `dispatch_after_commit`/`dispatch_pending`；对照 `/assign` 有投递 |
| B23-02 | **确认** | `_notify_recycle_candidate` 只落 `Notification`；`run_recycle` 和 `run_public_pool_recycle_job` 都没有 `dispatch_pending`。定时场景要等下一次 `run_auto_tasks_job` 的投递步骤，手动触发当天已跑过就收不到 |
| B23-03 | **不成立（已修）** | 当前 `finalize` 捕获 `commit` 异常后只记 `logger.warning`（类型名）并抛统一文案「暂时无法确认导入是否完成…」，不把 `str(exc)` 放进响应 |

## 需要单独说明的三条

### B20-05：不是“CSV 少校验”，是整条 SKU 导入绕过了统一数字规则

常规 `SkuCreate` 有 `max_digits/decimal_places`，`field_rules.NUMERIC_RULES` 也定义了 `Numeric(12,3/4)`。但 SKU 导入路径是：

```python
fields = io_util.sku_fields_from_row(row)   # _decimal 只判 is_finite
...
sku = Sku(product_id=product_id, **fields)   # 不经过 SkuCreate
await session.flush()
```

所以 13 位整数、5 位小数的物理量不会在解析阶段被拦，直接到 `Numeric(12,3)` 列。PostgreSQL 会报 `numeric field overflow` 或按列精度舍入；错误发生在 `flush()`，不是“用户填错了第几行”。修的时候不要在 `_decimal` 里只补一个 `max_digits`，应把 `sku_fields_from_row` 的每个字段都走 `field_rules.parse_numeric_field`，与常规接口共用一份规则。

### B22-04：不是“漏几个格”，是修订版没有一份完整的单头快照

`revise_sample` 的 `SampleRequest(...)` 只复制了一部分单头字段。漏掉的 `estimated_hours` 还会让 `craft_points` 派生为空；`sample_type/product_category/maker_unit/sample_purpose/urgency/sample_situation/deliver_date/sample_maker` 则直接缺失。结果是 V2 的《打样申请单》缺格，车间拿到的资料不如 V1 完整。

这不是“再加几个 `parent.xxx`”能长期解决的：字段清单会继续增长。应把 `SampleRequest` 的“单头资料字段”抽成常量，创建/修订/导入共用；`craft_points` 要么复制原值，要么按新工时重算并写审计。

### B22-06 / B23-02：通知不是“没生成”，是“生成了但没投递”

两条是同一个模式：`notify()` 只写 `Notification.wecom_status="pending"`，真正投递在 `dispatch_pending`/`dispatch_after_commit`。`batch_handover_tasks` 和 `run_recycle` 都在 `commit` 后直接返回，没有调用投递。

- 批量交接：接收人要等下一次任务中心刷新，停用员工交接的时效性丢失；
- 公海回收：手动触发当天若 `run_auto_tasks_job` 已跑过，预告通知要等第二天；定时任务本身也不投递。

修复时统一成“先 commit、再独立会话投递”，并在响应里区分“已生成/已投递”，不要把 `pending` 当成已送达。

## 本批最大漏项：打样修订链路没有“唯一版本 + 完整快照”保证

审查员把 B22-04（漏复制字段）定为 P2、B22-07（无唯一约束/无 request_key）定为 P3，分开看都像“资料缺格”和“双击重复”。放在一起看，问题更大：

- 修订版是**替换原版的正式版本**，不是普通草稿；`revise_sample` 却只复制部分单头字段；
- 同一 `parent_id` 没有 `(parent_id, version)` 唯一约束，也没有 `request_key`，双击/重试可以生成两份同号 V2；
- 两份 V2 都可能缺 `estimated_hours` 等字段，且都可能进入审批/制作；`superseded_by`/`revision_locked` 只能指向其中一条，另一条成为“同版本孤儿”；
- 原版制作/寄送事实冻结保留，但新版的申请单资料不完整，车间和客户看到的两份 V2 还不一样。

正确做法是把“开修订版”当成一个原子操作：锁父单、校验 `parent` 未被修订、分配 `version`、复制**全部**单头字段和明细、写 `request_key` 幂等、加 `(parent_id, version)` 唯一约束；不要等双击出两份再靠人工判断哪份是正式版。B22-07 也不应停留在 P3，它和 B22-04 是同一个入口的两半。

## 残余

- B20-02/03/04、B22-05 的跨范围/下游关系用例未在隔离库搭；当前是路由路径 + 服务层缺少校验的源码证据。
- B20-05/06、B21-02 的 DB 500 未实际打 PostgreSQL 验证；列宽和 Pydantic 字段元数据是确定事实。
- B22-06/B23-02 的投递缺失是源码事实，未起 scheduler/worker 观察实际延迟。


## 2026-11-23 真实补验

`review/verification/p2_b20_b21_schema.py`：

```text
[B20-03] owner_id=None 通过
[B21-02-item_name] 被拒 string_too_long
[B21-02-confirm] 被拒 string_too_long
[B21-02-approve] 被拒 string_too_long
[B21-02-ship] 被拒 string_too_long
```

结论：B20-03 成立；B21-02 当前代码已不成立，应从确认清单移除。


## 2026-11-23 B21-01 补验

```text
[B21-01] _f(Decimal('1.00005'))=1.0001
[B21-01] _f(Decimal('123456789.123456'))=123456789.1235
```

打样金额与数量走 `round(float(value),4)`，B21-01 成立。


## 2026-11-23 B20-05 补验

`backend/.venv/bin/python - <<'PY'` 直接调 `sku_fields_from_row`：

```text
长=1234567890123 -> 被拒 ValueError: SKU 导入：长(mm)（length）最多 12 位（含 3 位小数），收到 1234567890123
宽=1.23456 -> 被拒 ValueError: SKU 导入：宽(mm)（width）最多 12 位（含 3 位小数），收到 1.23456
重量=-5 -> 被拒 ValueError: SKU 导入：单重(kg)（weight）不能是负数（收到 -5）...
装箱数=1.9 -> 被拒 ValueError: SKU 导入：装箱数（carton_qty）必须是整数，收到 1.9...
```

结论：SKU 导入已复用 `field_rules`，B20-05 不成立，从确认清单移除。


## 2026-11-23 B20-06 补验

```text
[B20-06] name 长度300 -> 通过 len=300 列宽=200
[B20-06] specification 长度300 -> 通过 len=300 列宽=200
[B20-06] color 长度300 -> 通过 len=300 列宽=64
[B20-06] material 长度300 -> 通过 len=300 列宽=64
[B20-06] package_type 长度300 -> 通过 len=300 列宽=64
[B20-06] unit 长度300 -> 通过 len=300 列宽=16
```

B20-06 成立：文本核定不校验列宽。


## 2026-11-23 B22-04、B23-03 复核

- `sample/router.py:791-807`：修订单头已复制 `sample_type/product_category/maker_unit/estimated_hours/craft_points/sample_purpose/urgency/sample_situation/deliver_date/sample_maker`，B22-04 的“漏字段”描述已过期，不成立。
- `core/importing.py:696-705`：`commit` 异常只 `logger.warning("...异常类型=%s", type(exc).__name__)`，抛 `AppError(500, "暂时无法确认导入是否完成，请先核对导入记录，再决定是否重试。")`，不再拼接 `str(exc)`，B23-03 不成立。


## 2026-11-23 B21-04、B22-05 补验

`review/verification/p2_b21_b22_misc.py`：

```text
[B21-04] price_assumption=123456789.12 type=float
[B22-05] _active_owner(202) -> id=202 name=外部门员工 dept=20 status=active
```

B21-04 洞察价格走 float、B22-05 任务负责人只判存在/在岗，均成立。
