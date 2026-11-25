# 待确认项收口：前端 4 条与后端 3 条

结论：前端原 4 条“待确认/待业务/无法确定”已全部收口，**0 条待确认**；后端 B05-01 行为已实测确认，B03-03 当前代码已过人民币闸门，B05-03 当前应收上限硬校验已生效，两条旧结论按现状证伪。

## 一、F05-P1：证伪，不是缺陷

原说法：合同只关联 `quote_id`、`quote_version_id` 为空时，后端按整份报价放行签署，UI 却要求“登记前必须补版本”。

当前代码：

- `contract/service.py:_ensure_signable_source` 对 `quote_id` 有值、`quote_version_id is None` 的分支明确 `return`；
- 文档字符串写明“没钉版本的（先备条款时可以不选）沿用老口径：整份报价是正式依据”；
- UI 文案是 `DocumentsPage.tsx:1067`“不选也能先备条款，但登记签署前必须补上正式依据”，`:1291`“未绑定——登记签署前必须补上正式依据”。

UI 要求的是“正式依据”，不是“报价版本”。整份已发送/已接受的报价就是后端定义的正式依据，两边没有矛盾。**F05-P1 证伪，不计缺陷。**

## 二、F10-10：证伪，跨部门转交是已验收行为

原说法：恢复客户时指定新负责人可跨部门，是否允许待业务定。

`customer/service.py:transfer_customer` 对目标只校验“存在 + 在职”，不限部门/数据范围；`restore_customer` 指定新人时直接复用 `transfer_customer`。`QUALITY_PROGRESS.md:150` 已记录：

> 销售主管真实登录验证转交外部门后仍看到成功回执，并显示客户已离开查看范围。

说明跨部门转交是已知并验收的行为，不是漏检。**F10-10 证伪，不计缺陷。** 仍建议在 UI 上明确提示“将转出本部门”，但这不是权限缺陷。

## 三、F11-02：证伪，0 元成本是明确允许的业务规则

原说法：核价成本 0 元可提交，是否合法无法确定。

后端测试 `tests/test_quote_cost_gate.py::test_explicit_complete_cost_including_zero_can_submit` 参数化 `amount=["0", "10"]`，两者都断言 `approval_status == "approved"`；`tests/test_cost_completeness.py` 也写明“显式填 0 意思是「不花钱」，不是「没填」”。

本轮执行：

```text
PYTHONPATH=. .venv/bin/python -m pytest tests/test_quote_cost_gate.py -q -k zero
..  [100%]
```

**2 passed。** 0 元成本在四项成本都显式填写时合法；缺成本仍被拒。**F11-02 证伪，不计缺陷。**

## 四、F13-T01：证伪，当前没有可达的 payment/approval 通知源

原说法：通知铃铛把 `payment` 映射到 `/orders/{id}`、`approval` 映射到 `/quotes/{id}`，但未找到实际通知来源。

实际发送口径：

- `payment/router.py:1190-1199`：回款确认通知 `type_="payment"`，但 `business_type="order"`、`business_id=order.id`；
- `approval/router.py:360-377、406-415、461-469、577-584`：审批通知 `type_="approval"`，`business_type="quote"`、`business_id=quote.id`；
- `NotificationBell.tsx:20-31` 按 `business_type` 映射：`order → /orders/{id}`、`quote → /quotes/{id}`。

`payment`/`approval` 只是 `type`，不参与链接映射。当前没有 `business_type="payment"` 或 `"approval"` 的通知，因此这两条前端映射是死分支，不会打开错记录。**F13-T01 证伪（不可达），不计缺陷。** 若未来新增该类型通知，需要同时补映射。

## 五、B05-01：超收自动抵后续期，行为已实测确认

原结论：代码注释写“超收按到期日自动抵后续期”，审查员称主人要求不得擅改；缺原始需求记录，无法确定。

当前实现：

- `payment/service.py:allocate_payment` 先保留人工分配，清掉旧自动分配，再按 `due_date asc, id asc` 逐期填缺口；全部填满后的余额**不硬塞**，挂“待分配”；
- `payment/service.py:rebalance_order_allocations` 在应收金额变化后，把整单已确认回款按到账顺序重新分配；
- `payment/router.py:464-470` 明确写“主人定的口径是超收按到期日自动抵后续期”。

本轮执行：

```text
PYTHONPATH=. .venv/bin/python -m pytest tests/test_payment_rebalance_order.py -q
........  [100%]
```

**8 passed。** 行为是刻意实现且被测试固定，不是漏检。仍保留一个独立缺口：重排只写一条 `receivable_plan` 审计，没有逐笔记录“原期/新期/金额/操作人”，对账无法还原（原报告已单列，不下调）。

## 六、B05-03：证伪，应收合计上限已有硬校验

原结论：新建/调高应收都没有“合计 ≤ 订单总额”校验，可超额。

当前代码：

- `payment/router.py:150` 手工新增应收先调 `svc.assert_receivable_amount_fits(session, order, payload.amount)`；
- `payment/router.py:452` 调高应收时带 `replacing=plan` 再调同一个函数；
- `payment/service.py:824-846` 在订单行锁内汇总同币种未取消应收，`planned + amount > order.total_amount` 直接 422，并提示“本期最多可填 ¥X”。

本轮执行：

```text
PYTHONPATH=. .venv/bin/python -m pytest tests/test_payment_consistency.py -q
..........................  [100%]
```

**26 passed。** 原“无校验、可超额”结论证伪。

## 七、B03-03：当前代码已过人民币闸门，旧结论证伪

原结论：`create_order_from_quote` 直接 `currency=version.currency`、`drafts.create` 直接落 `data['currency']`，历史 USD 报价可一路变成 USD 订单/应收。

当前代码：

- `order/service.py:234`：`currency = await ensure_currency_allowed(session, version.currency, label="报价币种")`；
- `order/drafts.py:96`：`currency = await ensure_currency_allowed(session, data['currency'], label='订单草稿币种')`；
- `quote/service.py:746`：建报价/版本同样过 `ensure_currency_allowed`；
- `core/trade_mode.py` 固定只允许 `CNY`，没有 `export` 开关。

原报告描述的“直接继承、未过闸”在当前工作区不成立。**B03-03 按现状证伪**；若以旧提交为审查基准，则属于已修复项，不是现行缺陷。

## 八、最终口径

- 前端：206 confirmed（P0 2、P1 19）、**0 pending**、8 refuted（含 1 条部分证伪）。
- 后端：B05-01 从“无法确定”改为“行为确认、审计缺口另计”；B03-03 从“规则缺失”改为“当前已过闸”；B05-03 从“可超额”改为“当前已有硬校验、26 passed 证伪”。
- 本轮只读代码、只写 `review/`，未改业务代码。
