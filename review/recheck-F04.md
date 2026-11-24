# F04 客户页复核：15 条 + 1 条待确认

- 范围：`CustomerDetailPage.tsx`、`CustomerListPage.tsx`、`DuplicateCasePage.tsx`、`shared/api/customer.ts` 与后端客户创建/导入/合并/标签路由。
- 方式：逐条重读当前工作区代码，用 `review/repro/f04_frontend/check_f04.mjs` 静态复现。
- 命令：`node review/repro/f04_frontend/check_f04.mjs`
- 结果：**14 confirmed，1 refuted，1 latent，0 missing**。F04-07 已被当前后端修掉；F04-P1 只有潜伏风险。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F04-01 | P1 | 确认 | 合并确认键不看预览 `isLoading/isError`，预览未回或失败也能提交不可逆合并。 |
| F04-02 | P2 | 确认 | 客户导入确认没有 `request_key`，超时后重新预览再导入会重复建客户、重复开撞单。 |
| F04-03 | P2 | 确认 | 新建客户每次打开换键，关闭不清；首次已建成但响应丢失，重开会再建一条。 |
| F04-04 | P2 | 确认 | 新建前查重失败 `setDupMatches([])`，把“查重失败”显示成“没有重复”。 |
| F04-05 | P2 | 确认 | 合并专属价冲突只显示“N 对”，后端给了 `items` 但前端不渲染具体 SKU/价格。 |
| F04-06 | P2 | 确认 | 联系人接口失败时业务全景显示 0、联系人表显示“还没有联系人”。 |
| F04-07 | P2 | **证伪** | 当前 `io_router` 已改走 `report.write_failed_row`，未知异常落稳定文案，不再展示 `str(exc)`。 |
| F04-08 | P3 | 确认 | 单个/批量转移都不传 `reason`，审计只有归属变化。 |
| F04-09 | P3 | 确认 | 新建联系人无 `request_key`，提交中也能关闭弹窗，超时重开可多一条。 |
| F04-10 | P3 | 确认 | 已裁定撞单接手人只能显示“员工编号 N”，因为 `openRecord` 把表单清空。 |
| F04-11 | P3 | 确认 | 阶段分布/标签/等级/合并候选查询失败都没有错误态，失败显示成“没有”。 |
| F04-12 | P3 | 确认 | 多处 `Toast.error(error.message)`、撞单 Banner、`emptyHint` 原样展示后端错误。 |
| F04-13 | P3 | 确认 | 批量标签“覆盖”不列旧标签，后端审计 `after` 只有目标 tag_ids，无 before。 |
| F04-14 | P3 | 确认 | 新建客户“其他来源”输入框永远打不开：选项没有“其他”，条件却判“其他”。 |
| F04-15 | P3 | 确认 | 模板下载裸 `axios` 无 catch；导出失败统一吞成“检查权限或筛选范围”。 |
| F04-P1 | — | **潜伏，不算当前缺陷** | 客户子资源金额仍是 number 且无 exact，但三个封装全仓无页面调用。 |

## 二、P1：F04-01 合并门禁

`CustomerDetailPage.tsx`：

```tsx
const mergePreview = mergePreviewQuery.data
const unresolvedConflicts = (mergePreview?.blocking ?? []).filter(...)
...
okButtonProps={{ disabled: !mergeTarget || unresolvedConflicts.length > 0 }}
```

按钮只看“有没有目标、有没有未决议冲突”。预览还在加载、或请求 403/500/网络失败时：

- `mergePreview` 是 `undefined`；
- `blocking` 取 `[]`；
- `unresolvedConflicts.length === 0`；
- 确认键可用。

用户可以在没看到“会跟着走多少单据、专属价冲突有几对”的情况下提交。合并不可逆：来源客户软删、联系人/商机/报价/订单/跟进全改挂。预览失败但实际无冲突时，后端会直接合并，页面也不会补影响清单。

修复：`disabled` 加 `mergePreviewQuery.isPending || isError || !mergePreview`；预览失败时弹窗内给错误和重试；目标客户变更时清空旧状态。

## 三、六条 P2 复核

- **F04-02**：`confirmImport` 只往 FormData 放 `file` 和 `preview_token`；后端 `import_customers` 没有 `request_key`、没有“同文件已完成”记录。第一次 commit 后响应丢失，用户重新选文件、重新预览、再确认，会再建客户并再开撞单。`preview_token` 是文件计划签名，不是一次性幂等键。
- **F04-03**：打开新建弹窗必执行 `createRequestKeyRef.current = newRequestKey()`；`onCancel` 只 `setModalVisible(false)`，不清键也不保留；`onSuccess` 才清键。首次已 commit 但超时，关闭重开就是新键，后端幂等回放不了。
- **F04-04**：`onError: () => setDupMatches([])`。查重 500/403/网络失败时 matches 清空，重复提示块不出现；后端 `create_customer` 不拦截重复，客户直接落库，只能事后靠撞单队列发现。
- **F04-05**：后端 `tags.py` 的 `price_conflicts` 已返回 `sku_id/rule_ids/prices`，冲突对象也带 `items`；前端只渲染 `conflict.label`、`conflict.detail` 和两个选项，没有 `conflict.items`。用户在“保留目标/改用来源”之间盲选，看不到差多少、涉及哪些 SKU。
- **F04-06**：业务全景 `value: (contactsQuery.data ?? []).length`；联系人表 `empty="这家客户还没有登记联系人..."`。没有 `contactsQuery.isError` 分支，接口失败和“真没有联系人”长得一样，还可能和头部 `contact_count` 矛盾。
- **F04-07 证伪**：当前后端已从
  ```python
  report.failed_row(index, name, f"写入失败：{str(exc)[:160]}")
  ```
  改成
  ```python
  report.write_failed_row(index, name, exc)
  ```
  `write_failed_row` 对非 `AppError<500` 只记 `logger.warning`，返回“该行保存失败，请重试；仍失败时请联系管理员并提供行号。” 前端 `item.reason` 仍在展示，但内容已是稳定文案，不再泄露表名/约束/数据值。**这条按当前代码应关闭。**

## 四、P3 复核要点

- **F04-08**：API `transferCustomer(..., { owner_id })`、`batchTransferCustomers({ customer_ids, owner_id })` 都可带 `reason`，两个调用点都不传；后端归属历史只能写默认“负责人转移/批量转移”。
- **F04-09**：`createContact(customerId, payload)` 没有 `request_key`；弹窗 `onCancel` 不判 `contactMutation.isPending`，提交中可直接关。超时重开再建，可能同名两条；两次都勾主联系人时后一次会清掉前一次的主位。
- **F04-10**：`openRecord` 每次 `setForm(EMPTY_FORM)`，所以已裁定记录的 `form.owner` 永远是 `undefined`；渲染条件 `form.owner?.value === record.resolved_owner_id` 永远假，只能回退 `员工编号 ${id}`。后端也没有 `resolved_owner_name`。
- **F04-11**：`stageDistQuery` 没有 `isError`，失败时整条阶段栏不出现；`tagsQuery` 失败显示“还没有标签”；`mergeCandidatesQuery` 失败仍显示“没有找到匹配的客户”。
- **F04-12**：列表 2 处、详情 14 处 `Toast.error(error.message)`；撞单页 `description={resolveMutation.error.message}`；`emptyHint` 失败时直接返回 `error.message`。403/409/422/5xx 不分类，技术原文上屏。
- **F04-13**：覆盖模式选项目前只写“先清空原有标签”，不列每个客户现有标签；后端审计 `after={"customer_ids", "tag_ids"}`，没有 before/removed，误覆盖无法还原。
- **F04-14**：`SOURCE_OPTIONS` 是 8 个固定值，不含“其他”；但渲染条件 `form.source === '其他'` 还在，`otherSource` 是死代码。
- **F04-15**：模板按钮 `onClick={() => downloadCsv(...)}` 直接 fire，`downloadCsv` 内 `axios.get` 没有 catch；导出 `catch {}` 固定提示“检查权限或筛选范围”，403/422/5xx 不分。

## 五、F04-P1 为什么只是潜伏

`shared/api/customer.ts` 的 `CustomerOpportunity.expected_amount`、`CustomerQuote.current_version_amount`、`CustomerOrder.total_amount/received_amount/unreceived_amount` 都是 `number | null`，没有 `*_exact`；后端 `customer/router.py` 的 `current_version_amount` 也仍是 `float(version.total_amount)`。

但全仓检索 `listCustomerOpportunities/listCustomerQuotes/listCustomerOrders` 只有定义、没有页面调用；客户详情实际走 `listOpportunities/listQuotes/listOrders`，后两者有 exact。

所以现在不构成缺陷。风险是“类型层留了一个金额就是 number 的坑”，以后谁启用这三个封装就会丢分位精度。建议要么删掉未使用封装，要么补 exact 字段后再开放。

## 六、本块最大漏项

1. **F04-01**：合并是不可逆操作，预览门禁没兜住，是本块唯一 P1。
2. **F04-02 + F04-03**：客户创建/导入两条入口的幂等键生命周期都没有覆盖“结果未知后关闭重开”，会重复建客户、重复开撞单。
3. **F04-05**：合并专属价冲突让用户盲选“保留哪边”，选错会归档掉该保留的价，且不可逆。

## 七、残余

- 本批复现是静态断言，没有起浏览器做“预览未回就点确认”“提交超时后关闭重开”“导入重复提交”。
- F04-02 的重复导入依赖后端已 commit 但响应丢失；没有在隔离库上真实重放。
- F04-07 按当前代码证伪；如果后端 `AppError<500` 的 `message` 里带数据值，仍可能经 `item.reason` 上屏，但这已不是 `str(exc)` 原文问题。
- 未改业务代码；F04 的 14 条确认项均未修复。
