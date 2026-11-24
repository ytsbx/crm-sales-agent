# B15–B19 P2 逐条复核（证伪）

- 范围：B15-04..06（3）、B16-02..06（5）、B17-02..07（6）、B18-01..04（4）、B19-01..05（5），共 23 条 P2。
- 方法：逐条重读触发路径、路由依赖、模型约束、服务层锁和异常边界；每条至少落到一个源码断言，能构造的直接构造。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b15_b19_p2.py -q`
- 结果：`24 failed, 0 passed`（23 条 P2，B19-01 拆成创建/修订两个断言，所以 24 个用例）。没有发现误报。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B15-04 | **确认** | `_retry_move_crm` 非 customer 分支是 `if row.owner_id is not None and row.owner_id != handover.id`；NULL 负责人不触发冲突，会直接改给接手人。customer 分支已把公海当冲突，两条口径不一致 |
| B15-05 | **确认** | `preview_transfer` 只警告 handover 没绑企微；`transfer_relations` 用 `relation_owner.wecom_userid or ""` 发外部转接，接管人未绑不阻断 |
| B15-06 | **确认（模型/迁移漂移）** | `IntegrationLog.event_key` 模型只有普通列，无 `UniqueConstraint`/部分唯一索引；Alembic `a1b2c3d4e5f6` 有 `uq_integration_logs_erp_success_event`。生产走迁移有保护，`create_all` 建库会丢 |
| B16-02 | **确认** | `insights._f` 是 `round(float(value), 2)`；`quantity`/`weight` 也走 `_f`（780、790）；`quote_draft` 的 `suggested_total` 用 float 汇总 |
| B16-03 | **确认** | `resolved_customer = customer_id or opportunity.customer_id`，只校验该客户可见，不校验与商机客户一致；核价用乙客户、明细来自甲商机 |
| B16-04 | **确认** | `_visible_open_tasks` 返回 `None` 表示无 `task:view`，followup/opportunity 用 `or []` 压成空列表，输出 `open_task_count=0` 并可能给“没有逾期任务”的结论 |
| B16-05 | **确认** | `delete_session` 只删 `AgentMessage`/pending `AgentAction`，不删 `AgentExecution`；`get_execution` 只校验 `AgentSession.user_id`，不校验 `deleted_at`；`list_executions` join 也不滤 `deleted_at` |
| B16-06 | **确认** | L1 工具块只 `except AppError`（479）；`retry_tool_call`/`execute_action` 同样只捕 `AppError`；非 AppError 冒到 `run_turn` 的 `except Exception`，统一转 `model_error_message`，不落失败执行记录 |
| B17-02 | **确认** | `_money` 除金额外还用于 `"quantity": _money(item.quantity)`（1631）、`"weight": _money(sku.weight)`（1473）；数量/重量被 round 到 2 位 |
| B17-03 | **确认** | `product_stats` 的 `acc["profit"] += float(profit or 0)`、`acc["quantity"] += float(quantity or 0)`；`order_payment_trend` 用 `0.0 + _f(amount)`；`pricing_stats` 用 float 折扣/均价，出口无 exact |
| B17-04 | **确认** | `currency_note` 只探 `SalesOrder/PaymentRecord/ReceivablePlan/Opportunity` 四张表，不探 `Quote/QuoteVersion`；`product_stats`/`pricing_stats`/`sales_user_stats` 不取币种、不分组 |
| B17-05 | **确认** | `_product_bucket` 返回 `sid` 或 `f"n:{nm}"`，不拼 `spec_snapshot`；同名不同规格的无 SKU 明细落同一桶，`name_fallback` 只留第一条 |
| B17-06 | **确认** | `product_stats` 的 `quote_rows` 只过滤 `Quote.deleted_at`，不调 `draft_scope`/`private_draft`；同文件 `quote_stats`/`pricing_stats` 都有 |
| B17-07 | **确认** | `calculate_logistics` 的 `quantity: float` 直接传 `logistics.prepare(quantity: Decimal)`；`compute_weight_and_volume` 里 `unit_weight * quantity` 是 Decimal × float，必 TypeError |
| B18-01 | **确认（需历史外币数据）** | `target_bases`/`targets` 全文无 `currency`；签单/发货/回款/复购都按期间和归属人直接 `sum`。只有历史外币单/回款才触发，新写入被 CNY 闸门挡住 |
| B18-02 | **确认** | `target_snapshot` 的 `sales_target/repeat_customer_target` 转 float；`sales_variance`/`repeat_customer_variance` 用 `float(actual)-float(target)`；`router` 保存响应也只 `float(row.sales_target)` |
| B18-03 | **部分确认（设计取舍 + 标志矛盾）** | 冻结期缺键回落实时值是代码注释明确写的“宁可显示实时值”；问题是回落后仍 `row["actual_frozen"] = frozen_row`，用户会以为这一期不会再变 |
| B18-04 | **确认（流程半闭环）** | `revise_case` 固定 `author_id=case.author_id`；`ensure_case_draft_access` 对 draft/rejected 只放行 `author_id`。主管能开修订稿，但 PATCH/submit/delete 都 404，除非原作者处理 |
| B19-01 | **确认** | 创建只把 `oa_draft` 塞进 `extra`，不写 `oa_quote_user_id/name`；修订构造只复制客户/联系人/商机/origin/extra，不复制两个链级列；提交选项只读列 |
| B19-02 | **确认** | `delete_followup` 重算 `last_followup_at` 后直接审计/commit，不调 `refresh_next_followup_at`；`next_followup_at` 仍指向已删记录 |
| B19-03 | **确认（精度口径）** | `inquiry.serialize` 的 `quantity/target_price` 转 float；`opportunity._number` 统一 float；`quoted_price/minimum_price` 也从 Decimal 转 float，出口无 exact |
| B19-04 | **确认** | `lead/io_router` 的 `ensure_permission(user,"lead:assign")` 在逐行 try 内；`except Exception` 回显 `f"写入失败：{str(exc)[:160]}"`，403 被吞成单行失败，底层异常原文进响应 |
| B19-05 | **确认** | `lock_quote_source` 不接收 `viewer_id`，`linked_quotes_for(session,[inquiry.id])` 不带 `draft_scope`；别人的私密草稿会被算成“已关联报价”，409 文案还带 `quote_no` |

## 需要单独说明的五条

### B15-06：生产不是“已确认缺陷”，是模型与迁移漂移

模型 `IntegrationLog` 只有 `event_key` 普通列：

```python
event_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

迁移 `a1b2c3d4e5f6_integration_log_event_key.py` 建了：

```python
op.create_index(
    "uq_integration_logs_erp_success_event",
    "integration_logs", ["event_key"],
    unique=True,
    postgresql_where=sa.text("status = 'success' AND event_key IS NOT NULL"),
)
```

生产走 Alembic 时有这层保护；`Base.metadata.create_all` 建测试库/新环境时没有。准确说法是“模型缺约束、建库入口不一致”，不是“生产已经会重复落成功日志”。修复时把同一个部分唯一索引写进 `__table_args__`，并加一条 metadata/migration 一致性检查；不要只补迁移。

### B18-03：回落实时值是明确设计，真正的问题是 `actual_frozen`

代码注释写得很清楚：

```python
# 逐项取、缺了就回落实时值：结账永远写满四项，但万一历史数据不全，
# 宁可显示实时值也不要 KeyError 把整张报表打挂。
def frozen_value(metric: str, live):
    key = (scope_key, metric)
    if key not in snap:
        return live
```

所以“缺键回落实时”本身是设计取舍，不是审查员说的“静默错误”。但回落后仍然：

```python
row["actual_frozen"] = frozen_row
```

`frozen_row` 只按 `(scope_key, "sales")` 是否存在判定，缺 `repeat_net`/`new_customer` 时这一行仍标“已冻结”，数字却在跟实时数据变。结论应写成“回落实时是设计，`actual_frozen=True` 是标志矛盾”；修复要么缺项按 0、要么显式返回 `missing_frozen_metric` 且不标冻结，不要只改注释。

### B18-04：主管能开稿、不能改稿，是入口半闭环

`revise_case`：

```python
revision = SalesCase(
    title=case.title,
    author_id=case.author_id,   # 不是当前操作者
    status="draft",
    ...
)
```

`ensure_case_draft_access`：

```python
if case.status in ("draft", "rejected") and case.author_id != user.id:
    raise AppError(ErrorCode.NOT_FOUND, "案例不存在", 404)
```

路由允许 `author` 或 `reviewer` 开修订稿，但开出来的是“原作者的草稿”。主管随后 PATCH/submit/delete 都会在 `ensure_case_draft_access` 404。若设计就是“修订必须原作者确认”，应在开稿响应/界面明确写“已代开，需原作者编辑提交”，并给主管一个转交入口；否则就是半可用入口。

### B17-07：不是“可能 500”，是 `Decimal * float` 必炸

`pricing/logistics.py` 的签名是 `quantity: Decimal`，`compute_weight_and_volume` 里：

```python
actual_weight = (unit_weight * quantity).quantize(Decimal("0.0001"))
```

`calculate_logistics` 传的是 `quantity: float`，没有 `Decimal(str(quantity))`。只要走到有单件重量/体积的 SKU，`unit_weight` 是 Decimal、`quantity` 是 float，直接 `TypeError`。这不是概率问题，是类型不匹配；修的时候在工具入口统一 `Decimal(str(quantity))`，并在 `prepare` 内再做一次防御。

### B19-05：409 不只是“泄露编号”，它会把询价锁死

`lock_quote_source` 不接收用户：

```python
async def lock_quote_source(
    session: AsyncSession, inquiry: CustomInquiry, *, quote_id: int | None = None
) -> ...
```

`linked_quotes_for(session, [inquiry.id])` 不带 `viewer_id`，所以 `draft_scope` 不生效。只要有一条别人的私密草稿引用了这条询价，当前用户点“从需求发起报价”就会：

```python
raise AppError(
    ErrorCode.DUPLICATE_CONVERT,
    f"这条需求已关联报价 {links[0]['quote_no']}，请打开原报价调整或新增报价版本",
    409,
)
```

后果有两层：一是回显别人的 `quote_no`；二是当前用户没有打开那张私密报价的权限，409 给的“打开原报价”路径他也走不通，这条询价对该用户实际不可报价。修复不能只改文案，必须把 `viewer_id` 传进 `locked_quote_source`/`linked_quotes_for`，私密草稿不参与“已关联”判定。

## 本批最大漏项：Agent 的“权限缺失/对象错配/工具异常”都被包装成正常结论

审查员把 B16-03、B16-04、B16-06 分开列了，但没有把它们当成同一个“Agent 输出可信度”问题：

- B16-03：`quote_draft` 允许把商机甲的明细按客户乙的价格算，返回却是一份“正常报价草稿”；
- B16-04：没有 `task:view` 时 `None` 被 `or []` 压成 0，返回“没有逾期任务”；
- B16-06：工具抛非 `AppError` 时统一报“AI 服务暂时不可用”，不落失败执行记录。

三条的共同点是：**Agent 入口没有把“我不知道/我没权限/我坏了”与“业务事实是 0/没有”分开**。业务员看到的是一个语气正常、结构完整的回答，无法判断这是模型推断、权限缺失，还是工具崩溃。尤其 `quote_draft` 和 `followup_suggestion` 的输出会被直接拿去做报价/跟进，错配的价格和“没有逾期任务”的结论比 500 更危险，因为 500 至少会被发现。

正确做法是在 Agent 入口统一三类标记：`restricted`（权限不足，相关计数给 `None` 并说明）、`mismatch`（对象不一致直接 422，不做“取另一个客户”的兜底）、`tool_error`（落 `AgentExecution(status='failed')`，返回受控错误而不是模型故障文案）。不要让任何一条走“成功响应 + 看似正常的数字”。

## 残余

- B15-04/05、B16-05/06、B19-02 的并发/数据库用例未搭；当前是源码路径 + 模型约束 + 异常分支证据。
- B15-06 的 `create_all` 环境未实际建库验证；模型 metadata 无唯一索引是确定事实。
- B17-07 的 `Decimal * float` 已用纯 Python 证明会 TypeError，但没有在隔离库跑完整 `calculate_logistics` 请求。
- B18-01 的跨币种需要历史外币数据；新写入的 CNY 闸门不覆盖“历史/ERP 导入”这条前提。


## 2026-11-23 真实补验

`review/verification/p2_b15_b17_batch.py`：

```text
[B15-06] IntegrationLog indexes=[]
[B16-02] insights._f(1.005)=1.0
[B17-02] tools._money(Decimal('1.005'))=1.0
[B17-07] float quantity -> TypeError: unsupported operand type(s) for *: 'decimal.Decimal' and 'float'
```

B15-06 模型缺索引、B16-02/B17-02 金额舍入、B17-07 Decimal×float 均成立。


## 2026-11-23 B18/B19 精度补验

`review/verification/p2_b18_b19_precision.py`：

```text
[B18-02] sales_target=123456789.12345679 type=float
[B18-02] repeat_customer_target=987654321.9876543 type=float
[B19-03] _number(...)=123456789.12345679 type=float
```

B18-02、B19-03 均成立：目标和商机金额出口走 float，大额丢位。
