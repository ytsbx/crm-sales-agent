# B01–B05 高严重度复核（证伪）

- 范围：B01/B02/B03 的 P1/P2 + B05 的 P1；B04、B24 已有单独报告。
- 方法：重读报价/定价/草稿/收款/ERP 代码，检查闸门与调用链；可纯函数验证的进 `review/repro/test_b01_b05_highlights.py`。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b01_b05_highlights.py -q`
- 结果：`4 failed, 5 passed`。失败项是“修复后应有”的断言（缺 request_key、缺 decimal_places、转单缺币种闸）；通过项包含“B01-01 的示例其实不翻档”的控制证据。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B01-01 P1 | **误报（原触发不成立）** | 审查员例子 10000×7.123456=71234.56 与档位相等，`<=` 正确进档；float 翻档需档位到 10^10 级，默认 5 万档不触发。应降 P3/配置防御 |
| B01-02 P2 | **确认** | `serialize_version` 只有 `total_amount_exact`，其余汇总仍是 float |
| B01-03 P2 | **确认** | `quantize(0.01)` 默认 HALF_EVEN：94.125→94.12，HALF_UP 为 94.13 |
| B02-01 P1 | **当前不成立（2026-11-23 复测）** | 接受后改运费已被 `accepted_at` 闸门 42204 拦住，`sent_at` 不会清；报价总额不再与客户 PDF 分叉 |
| B02-02 P1 | **确认** | 改负责人/复制只查存在+active，无 `ensure_in_scope` |
| B02-03 P2 | **确认** | `update_quote` 直接 `setattr(contact_id)`，无存在/归属校验 |
| B02-04 P2 | **确认** | 软删只查是否已转订单，不拦已发送/已接受 |
| B02-05 P2 | **确认** | 利润率取 min（最宽松），折扣上限也取 min（最严格），方向相反 |
| B02-07 P2 | **确认** | 外币无汇率时仍拿人民币底价与外币报价比大小 |
| B02-12 P2 | **确认，但入口写错** | `PATCH /quote-versions` 显式 null 实际被忽略；`PATCH /quotes` 的 `valid_until:null` 才会把主单+快照清空 |
| B02-13 P3 | **确认** | 同日成本重复只到核价才 409 |
| B03-01 P1 | **确认** | `price_permissions.role_id` 无唯一约束，并发 PUT 可出两条；之后 `scalar_one_or_none` 命中多行 500 |
| B03-02 P1 | **确认** | `OrderDraftUpdate` 的 `unit_price` 可缺省为 None，`drafts.update` 无条件覆盖，保存成功但价格被清 |
| B03-03 P1 | **证伪（当前已过闸）** | `quote/service.py`、`order/service.py`、`order/drafts.py` 均调用 `ensure_currency_allowed`，固定只允许 CNY |
| B03-04 P2 | **确认** | 页面新增/改成本不查同日冲突，脏数据让核价 409 |
| B03-05 P2 | **确认（极端金额）** | 运费 `float(amount)` → `Decimal(str(float))` 往返；1e14 级会丢分 |
| B03-06 P2 | **确认** | 试算 `quantity` 无 decimal_places，重量/金额按原值算，落库 Numeric(16,3) |
| B03-07 P2 | **确认** | `POST /price-rules` 不判 SKU 软删/停用，规则建完不参与取价 |
| B05-01 P1 | **行为确认（审计缺口另计）** | 超收按到期日自动抵后续期，`test_payment_rebalance_order.py` 8 passed；仍缺逐笔“原期/新期/金额”审计 |
| B05-02 P1 | **部分证伪（降 P2）** | 后端 `POST /orders/{id}/receivables` 已支持 `request_key`/`X-Request-Key` 并回放；前端无手工建应收入口，仅裸调 API 才重复 |
| B05-03 P1 | **证伪（当前已有硬校验）** | 新建 `payment/router.py:150`、调高 `:452` 均调 `assert_receivable_amount_fits`，订单行锁内校验有效应收合计 |
| B05-04 P1 | **确认** | `PaymentCreate.request_key` 可选；不带键重试可出两笔 pending |
| B05-05 P1 | **确认** | HTTP 200 缺 `code`/非整数 → `ErpError`（可重试），不是 `ErpResultUnknown` |

## 三条需要单独说明

### B01-01：例子本身不翻档

`Decimal("10000.00") * Decimal("7.123456") == Decimal("71234.56")`，float 两边也都是 `71234.56`，`amount <= max` 成立，进入该档是正确的。要出现“实际超了、float 判没超”，需要档位大到 float 的分辨率吞掉 1e-8 的差（实测 10^10 档位：`30274865503.91 × 0.330307 = 10000000000.00000037`，float 后两边都成 1e10）。默认 `approval_levels` 第一档 50000，不触发。结构上确实该改 Decimal，但 P1 的“边界单会掉档”在当前配置下不成立。

### B02-01 / B03-03：钱和币种

- B02-01 的旧结论“客户接受后改运费确实会发生”已不适用于当前代码：`ensure_charge_change_allowed` 在 `quote/service.py:130-135` 先判 `accepted_at`，接受后新增/修改/删除运费都返回 42204，`sent_at` 不会被清。当前只剩“转单没有客户接受金额快照”的设计缺口（B04-01，待确认），不是可触发的报价/订单 P1。
- B03-03 当前代码已过闸：建报价 `quote/service.py:746`、转单 `order/service.py:234`、订单草稿 `order/drafts.py:96` 都调用 `ensure_currency_allowed`，固定只允许 CNY。原“未过闸”结论证伪。

### B05-03：证伪，当前已有“应收合计 ≤ 订单总额”硬校验

`payment/router.py:150` 手工新增应收先调 `svc.assert_receivable_amount_fits(session, order, payload.amount)`；`:452` 调高应收时带 `replacing=plan` 再调同一个函数。函数在订单行锁内汇总同币种未取消应收，`planned + amount > order.total_amount` 直接 422，并给出“本期最多可填”金额。本轮执行 `tests/test_payment_consistency.py`，**26 passed**。原“无校验、可超额”结论证伪。

### B05-01：已收口，自动抵后续期是现行实现

`allocate_payment` 按 `due_date asc, id asc` 逐期填缺口，填满后的余额不硬塞、挂“待分配”；`rebalance_order_allocations` 在应收变化后重排全部已确认回款。`payment/router.py:464-470` 明确写“主人定的口径是超收按到期日自动抵后续期”。本轮执行 `test_payment_rebalance_order.py`，**8 passed**，行为不是意外。原“无法确定”收口为“行为确认”；独立缺口只剩逐笔分配审计：只写一条 `receivable_plan` 审计，没有“原期/新期/金额/操作人”，对账无法还原。

## 本批最大误判

**本批最大更正是 B03-03 已不符合当前代码。** `create_order_from_quote` 先 `ensure_currency_allowed(version.currency)`，`drafts.create` 也先过 `ensure_currency_allowed(data['currency'])`，历史 USD 无法继续继承成 USD 草稿/订单/应收。旧结论按现状证伪。

## 残余/未覆盖

- 本批只覆盖 P1/P2；B01/B02/B03 的 P3 与 B05 的 P2/P3 尚未逐条复核。
- B05-01 已用 `test_payment_rebalance_order.py` 实跑 8 passed 收口；B05-03 已用 `test_payment_consistency.py` 实跑 26 passed 收口；B05-02 后端已支持请求键、前端无手工入口，降 P2；B05-04 的复现仍需数据库/并发。
- B03-05（float 往返）与 B01-02（float 输出）属同一类精度问题：正常金额不显现，1e14 级才丢分。
