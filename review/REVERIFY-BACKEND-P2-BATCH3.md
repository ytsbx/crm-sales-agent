# 后端 P2 复核（第三批：幂等/重复）

> 日期 2026-11-24，只读当前代码。只审查，未改业务代码。

## 一、结果

本批 12 个编号中，旧台账实际能定位到 11 条（`B22-13` 在 `LEDGER.md` 无对应行，不虚增结论）：

- **0 条完全修好**
- **3 条部分缓解**：B04-02、B05-04、B07-04，后端支持请求键但键仍非必填
- **8 条仍成立**：B04-13、B13-06、B13-10、B19-09、B20-09、B21-06、B21-07、B22-07

## 二、逐条证据

| 编号 | 当前代码 | 结论 |
|---|---|---|
| B04-02 | `order/schema.py:142` `request_key: str | None`；`order/router.py:118-126` 只有 `if request_key:` 才 `reserve`，无键直接建单 | 部分缓解；裸调 API/无键双击仍出两张 |
| B04-13 | `order/service.py:221-231` 已有有效订单时抛 `DUPLICATE_CONVERT 409`，并尽量返回订单号；不是“回放原订单” | 仍成立（P3）：重试得到 409，不是幂等成功回放 |
| B05-04 | `payment/schema.py:143` 键可选；`payment/router.py:1028-1036` 仅带键才 `reserve` | 部分缓解；页面有稳定键，裸调 API 无键仍重复 |
| B07-04 | `contract/schema.py:58` 键可选；`contract/service.py:402-430` 仅 `if payload.request_key` 才查旧文档 | 部分缓解；页面稳定键，旧调用无键仍两份 |
| B13-06 | `customer/io_router.py:266` 只有 `preview_token`；`:283` 用文件 SHA 做预览比对，但执行不按文件幂等；重复导入仍逐行查重后“疑似重复也建档并开待裁定单”（`:363-437`） | 仍成立：同一文件重试会再建客户、再开撞单 |
| B13-10 | `customer/schema.py:29` 键可选；`customer/router.py:141-149` 仅带键才 `reserve`；无键响应会提示“没有幂等保护” | 仍成立（P3） |
| B19-09 | `lead`、`inquiry` 模块全量搜 `request_key` 无命中；线索/询价创建、询价修订均无幂等键 | 仍成立 |
| B20-09 | `opportunity` 模块全量搜 `request_key` 无命中；创建商机、添加明细、跨商机复制、克隆均无幂等键 | 仍成立 |
| B21-06 | `product_insight/schema.py:153` 定义了 `InsightReview.request_key`；`router.py:621-665` `review_insight` 未读取、未落 `review_request_key`，第二次同键仍是“状态不在待评审”422 | 仍成立：字段定义了但没用 |
| B21-07 | `product_insight/router.py:442-476` 创建洞察直接 `session.add`，无 `request_key`、无唯一约束 | 仍成立 |
| B22-07 | `sample/router.py:711-735` 修订入口无 `request_key`；模型无 `(parent_id, version)` 唯一约束，双击可出两份 V2 | 仍成立 |
| B22-13 | `LEDGER.md` 无对应行，旧汇总里的编号无法定位，未编造结论 | 不计数 |

## 三、结论

- “后端全部修好了”在幂等这一组不成立：三个入口只是“支持键”，没有把键变成必填，也没有服务端去重兜底；其余八个入口连键都没有。
- 页面侧有稳定请求键的（回款/合同/建单）可以把实际双击风险降低，但接口契约仍允许无键重复，不能按“已修”记账。
- 下一批：审计 10 条 + 外部依赖 4 条。
