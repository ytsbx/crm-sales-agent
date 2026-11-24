# F05 合同/单据/审批页复核：20 条确认 + 2 条证伪

- 范围：`DocumentsPage.tsx`、`ContractOaForm.tsx`、`CustomerApproval.tsx`、`BizDocPanel.tsx`、`ContractDocsPanel.tsx`、`AttachmentPanel.tsx`、`ImageGallery.tsx`、`file.ts`、`contract.ts`、`approval.ts`、`ApprovalPage.tsx`、`RulesPanel.tsx`。
- 方式：逐条重读当前工作区代码与后端 `contract/service.py`、`file/model.py`，用 `review/repro/f05_frontend/check_f05.mjs` 静态复现。
- 命令：`node review/repro/f05_frontend/check_f05.mjs`
- 结果：**20 confirmed，0 pending，2 refuted（含 1 条部分证伪），0 missing**。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F05-01 | P1 | 确认 | 签署件上传时就挂 `category=signed`，取消也进“签署原件”；确认签署再挂一次，同一文件两条。 |
| F05-02 | P2 | 确认 | 合同上传键关闭弹窗后重开即换，首次已建成但响应丢失会多一份草稿。 |
| F05-03 | P2 | 确认 | BizDocPanel 的请求键不随目标单据/报价版本变化重置，失败后切单复用旧键会 409。 |
| F05-04 | P2 | 确认 | 审批规则新建/更新无请求键，超时重试会多一条同内容草稿。 |
| F05-05 | P2 | 确认 | 合同下拉/审批总额/沙盒版本金额仍走 number 或 raw float，16 位边界显示错分。 |
| F05-06 | P2 | 确认 | 合同台账、客户/订单/报价/版本下拉查询失败都显示空表/空选项。 |
| F05-07 | P2 | 确认 | ContractDocsPanel / BizDocPanel 列表失败显示空态。 |
| F05-08 | P2 | 确认 | AttachmentPanel / ImageGallery 列表失败显示“没有附件/没有图片”。 |
| F05-09 | P2 | 确认 | 大图切换失败仍渲染上一张 objectURL，标题却是新文件。 |
| F05-10 | P2 | **部分确认** | 规则表、版本历史、沙盒报价失败仍空；沙盒版本已补 `versionsFailed`，这一子项已修。 |
| F05-11 | P3 | 确认 | 上传合同按钮禁用条件缺客户/来源/原文件，mutation 内才抛 Toast。 |
| F05-12 | P3 | 确认 | 签署/作废/删除/撤回 OA 等 API 都没有前端请求键。 |
| F05-13 | P3 | 确认 | 详情常开 5 秒轮询、错误原文上屏、到期日列只看当前页、“系统默认规则”是伪选择。 |
| F05-14 | P3 | 确认 | 拒绝原因可空、转交候选人错误原文、审批动作无请求键。 |
| F05-15 | P3 | 确认 | BizDoc 作废原因固定“业务作废”，目标 id 缺失仍可点生成。 |
| F05-16 | P3 | 确认 | 附件上传/改名/删除无请求键，`BusinessFile` 无唯一约束。 |
| F05-17 | P3 | 确认 | 图片批量上传中途失败留部分文件且不刷新。 |
| F05-18 | P3 | 确认（已接线） | ContractOaForm 已在 `DocumentsPage:1175` 使用；`initialized` 不随 docId 重置，附件 `Promise.all` 部分成功留孤儿。 |
| F05-19 | P3 | 确认（未接线） | CustomerApproval 仍无调用点；10 秒轮询、错误原文、手动关联无键。 |
| F05-20 | P3 | 确认 | 通用 `uploadFile` 无请求键/checksum 去重，所有上传入口弱网重试都可能重复。 |
| F05-P1 | — | **证伪（非缺陷）** | 后端把整份已发送/已接受报价当作正式依据；UI 要求“补充正式依据”，不是“必须选版本”，两边不矛盾。 |
| F05-P2 | — | **证伪（部分）** | 原说 ContractOaForm/CustomerApproval 都未接线；实际 ContractOaForm 已接线，只有 CustomerApproval 仍无调用点。 |

## 二、P1：F05-01 签署件被“预挂”

前端 `DocumentsPage.tsx` 上传扫描件时：

```tsx
const row = await uploadFile(file, {
  businessType: 'contract',
  businessId: startedFor,
  category: 'signed',
})
```

这一步已经把 `BusinessFile(business_type='contract', business_id=doc.id, category='signed')` 写进库。用户不点“确认签署”直接关弹窗，文件仍然是 `signed`：

- `list_signed_files` 只按 `business_id + category='signed'` 查，不看 `doc.status`；
- 草稿合同详情会把它显示成“签署原件”；
- `signedFiles.length===0` 的“还没有登记签署”分支被绕过。

确认签署时 `sign_document` 又无条件 `session.add(BusinessFile(... category="signed"))`，没有按 `file_id` 去重。同一份扫描件出现两条签署记录，证据链和“签的是哪一版”被污染。

修复方向：上传时不要挂 `signed`（先临时类别或只存 FileRecord）；`sign_document` 成功时在同一事务里挂 `signed`，并按 `(business_id, file_id, category)` 去重；列表只对 `doc.status='signed'` 展示，或至少按 `file_id` 去重。

## 三、P2 复核要点

### F05-02 合同上传键

`openUploadModal` 每次 `setUploadRequestKey(newRequestKey())`；`onCancel` 只 `setUploadVisible(false)`，不清也不保留。首次已 commit 但响应超时，关闭重开再提交是新键，后端 `ContractDocument.request_key` 幂等回放不了，会多一份同内容草稿和一套“上传客户回签文件”待办。

### F05-03 BizDocPanel 键不随目标重置

组件只有一个 `requestKeyRef`，`if (!requestKeyRef.current) requestKeyRef.current = newRequestKey()`，没有 `useEffect` 在 `sampleRequestId/orderId/quoteVersionId` 变化时清空。报价详情切换版本时组件不卸载，旧键带到新版本；后端同 action 下 payload 指纹不同会返回 409，新版本反而生成不了。

### F05-04 审批规则无键

`RulesPanel` 保存走 `draft.id ? updateApprovalRule(...) : createApprovalRule(payload)`；`shared/api/approval.ts` 两个函数都没有 `request_key`。后端 `create_rule` 直接 `ApprovalRule(...)` + `session.add`，没有唯一名称/内容约束。超时重试会多一条同条件同动作草稿。

### F05-05 金额展示

- 合同下拉：`` `¥${o.total_amount}` ``、`` `¥${v.total_amount}` ``，直接用 float；
- 审批页：`formatMoney(toTwoDecimals(v), 'CNY')`，`v` 是 number；
- 沙盒：`Number(v.total_amount).toLocaleString()`。

后端 order/quote 已有 `*_exact`，这三处没用。接近 `Numeric(16,2)` 上限时，审批人看到的总金额可能差 1 分。

### F05-06 / 07 / 08 查询失败当空

- `DocumentsPage`：`dataSource={documents}`、`empty="还没有合同..."`，`documentsQuery` 没有 `isError`；客户/订单/报价/版本下拉也没有错误态。
- `ContractDocsPanel`：`rows = query.data?.items ?? []`，`isLoading` 后直接 `rows.length===0` 空态。
- `BizDocPanel`：`compactEmpty = !isLoading && rows.length===0`，没有 `isError`。
- `AttachmentPanel`/`ImageGallery`：`query.data ?? []`，失败直接画“还没有附件/没有图片”的空态引导。

### F05-09 大图串图

`ImageGallery` 的 `catch` 只 `Toast.error`，没有 `setViewerUrl(null)`；`finally` 把 `viewerLoading=false` 后，渲染条件 `viewerUrl && !viewerLoading` 重新显示上一张的 objectURL，而标题已经换成新文件名。用户会把 A 图当 B 图。

### F05-10 部分确认

`RulesPanel` 的规则表 `empty="还没有规则"`、版本历史 `还没有发布过版本`、沙盒报价 `quotesQuery` 都没有 `isError`，失败仍显示空。**沙盒版本这一子项已经修了**：有 `versionsFailed = Boolean(quoteId) && versionsQuery.isError`，并进入 `blockedReasons`。所以原条目不是全错，按“部分确认”记。

## 四、P3 复核要点

- **F05-11**：`okButtonProps` 只判标题/我方/对方，未判 `customer_id`、`contractOrigin`、`originalFile`；mutation 内才抛“请选择合同所属客户/请选择合同来源”。
- **F05-12**：`signContractDocument`、`deleteContractDocument`、`withdrawContractDingTalkApproval`、`autoLinkContractDingTalkApproval`、`startContractDingTalkApproval`、`startContractOaApproval` 都没有 `request_key` 参数。后端有状态锁，通常不会重复改状态，但前端无法区分“已成功未回执”。
- **F05-13**：`refetchInterval: 5000` 常开；`hasExpiry = documents.some(...)` 只看当前页；编号规则分支固定 `<Input value="系统默认规则" readOnly />`，实际没有规则可选；多处 `Toast.error(error.message)`。
- **F05-14**：拒绝弹窗 `onOk={() => rejectMutation.mutate()}`，没有 `disabled={!rejectComment.trim()}`；`usersQuery.error.message` 原样上屏；审批动作无请求键。
- **F05-15**：`voidBizDoc(id, '业务作废')` 原因固定；生成按钮只看 `canManage`，目标 id 缺失仍可用 `as number` 把 `undefined` 传后端。
- **F05-16**：`AttachmentPanel` 直接 `uploadFile`；`BusinessFile` 只有普通索引 `(business_type, business_id)`，没有 `(business_type,business_id,file_id,category)` 唯一约束。
- **F05-17**：`for (const file of list) await uploadFile(...)`，所有成功后才 `refresh()`；第 N 张失败时前 N-1 张已挂但界面不显示，用户再选一次会重复传。
- **F05-18**：这次要修正原报告。`ContractOaForm` **已经接线**（`DocumentsPage.tsx:1175`），不再是“未接线组件”。它的 `initialized` 只置 true 一次，不随 `docId` 重置；换合同会沿用上一份默认值。附件 `Promise.all(files.map(uploadFile))`，部分成功时已挂文件但 `set(field.id, ids)` 没执行，留下未引用附件。
- **F05-19**：`CustomerApproval` 仍无调用点；`setInterval(() => autoLink.mutate(), 10000)` 无退避/停止条件；`entry.error.message`、`Toast.error(e.message)` 原文；`approval-link` 无请求键。
- **F05-20**：`uploadFile` 只拼 FormData；后端 `upload_file` 直接 `FileRecord` + `BusinessFile`，没有 `request_key`/checksum 去重。所有调用方都受影响。

## 五、待确认结论收口

### F05-P1 证伪：不算缺陷

后端 `_ensure_signable_source` 对：

```python
if doc.quote_id is not None:
    ...
    if doc.quote_version_id is None:
        return
```

即“合同只关联报价、不关联版本”时，只要报价状态是 `sent/accepted` 就放行签署。代码注释明确写“没钉版本的（先备条款时可以不选）沿用老口径：整份报价是正式依据”。

UI 文案是 `DocumentsPage.tsx:1067`“不选也能先备条款，但登记签署前必须补上正式依据”，`:1291`“未绑定——登记签署前必须补上正式依据”。**要求的是正式依据，不是版本**；整份已发送/已接受报价就是后端定义的正式依据。

因此原“UI 要求补版本、后端放行”的冲突不成立，**F05-P1 证伪，不计缺陷**。若业务要强制钉版本，那是新规则，不是现有实现与 UI 自相矛盾。

### F05-P2 部分证伪

- `ContractOaForm`：`DocumentsPage.tsx:6` 导入、`:1175` 使用，**已接线**；
- `CustomerApproval`：只有 `ApprovalHistory`/`ContractBasis` 被导入，默认组件没有调用点。

所以“两个都未接线”不成立。`CustomerApproval` 是否废弃仍需主人确认；如果不用，应删掉或标 deprecated，避免以后误接线 F05-19 的轮询/无键问题。

## 六、本块最大漏项

1. **F05-01**：签署证据污染是本块唯一 P1，且不依赖弱网——上传后直接关闭就会发生。
2. **F05-02 / F05-03 / F05-04**：三条都是“请求键生命周期”没覆盖“失败后换上下文”，会多草稿、生成不了新版、多规则。
3. **F05-18**：原报告把它当“未接线所以不急”，实际已接线；换合同沿用默认值和附件孤儿现在是活路径。

## 七、残余

- 静态断言复现，没有起浏览器做“上传签署件后取消/再签”“合同上传超时后重开”“生成失败后切版本”“规则保存超时重试”。
- F05-10 只确认剩余三处；沙盒版本错误态已修，不再计入。
- F05-P1 已证伪：整份报价就是正式依据，UI 未强制要求版本；未查生产库历史合同分布。
- 未改业务代码；F05 的 20 条确认项均未修复。
