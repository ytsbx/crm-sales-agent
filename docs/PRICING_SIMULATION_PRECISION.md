# 报价模拟精度（2026-11-17）

现有 `POST /api/v1/pricing/simulate`，权限、请求及外层响应格式不变。仅支持 CNY。

- `candidates` 显式单价保留原有最多四位校验。
- 从 `margins` 或默认价格锚点生成候选价时，按四位小数、ROUND_HALF_UP 取整。
- 每个 `scenarios` 行的金额复用正式报价 `line_amount(quantity, price)`，逐行到分，不从浮点响应中的单价反算。
- 兼容保留数字字段 `quoted_price`、`amount`；新增 `quoted_price_exact`、`amount_exact` 十进制字符串，建议前端优先读取精确字段。

例如数量 `3`、候选单价 `1.2345`：

```json
{
  "quoted_price": 1.2345,
  "quoted_price_exact": "1.2345",
  "amount": 3.7,
  "amount_exact": "3.70"
}
```

`profit` 仍为原有的单件利润，不是整行利润；本次不改变该字段定义。无 `price:manage` 的用户仍拿不到成本/利润，按利润率反推价格仍受原权限约束。

此接口是单 SKU 价格模拟；报价详情 WhatIfPanel 目前在前端本地计算，没有调用本接口。本次没有新增“按整版均价分配到各条明细”的规则，也没有改变历史报价和正式金额算法。前端类型报错及组件接线由另一会话处理。
