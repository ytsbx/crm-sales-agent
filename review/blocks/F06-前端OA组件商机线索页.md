# F06 前端·OA 组件 + 商机/线索页

> 审查范围：14 个前端文件，4,010 行（逐文件行数与 `review/LEDGER.md` 一致）。
> 审查方式：逐文件全文读完；只读业务代码，只写 `review/`。
> 结论统计：P0 0 / P1 1 / P2 8 / P3 10 / 待确认 0。

## 一、逐文件检查

| 文件 | 行数 | 我检查了什么 | 结果 |
|---|---:|---|---|
| `frontend/src/shared/components/ResolveOaActions.tsx` | 176 | 重发/作废的请求键、按钮 pending、Popconfirm、错误提示、认领入口 | 全文读完。认领走 `AdoptOaInstanceModal`，没有从页面伪造实例号；重发/作废每次点击重新生成请求键，失败重试不是同一次核定，且链接没有按 pending 禁用 → F06-10（P3）。未发现越权或金额问题。 |
| `frontend/src/shared/components/OaFormSnapshot.tsx` | 15 | 是否把 OA 字段当 HTML 渲染、空值/复杂值如何展示 | 全文读完。只把字段值当纯文本节点渲染，未知复杂结构显示“复杂内容请在同序 OA 查看”，没有 `dangerouslySetInnerHTML`，未发现 XSS。 |
| `frontend/src/shared/components/oa-form-fields.ts` | 40 | 字段扁平化、循环/深度上限、是否拼 HTML | 全文读完。只做字段名/值提取，深度上限 10；不生成 HTML，不执行用户字符串。未发现注入或敏感字段泄露。 |
| `frontend/src/shared/components/ApplicantIdentity.tsx` | 19 | 姓名/部门是否可编辑、是否把当前用户身份写进表单 | 全文读完。只读展示，值来自 `user` 或 OA prefill，没有可编辑身份字段。未发现冒用身份入口。 |
| `frontend/src/shared/components/AutoOaSync.tsx` | 25 | 轮询方式、是否把 POST 当 query、后台轮询、错误提示 | 全文读完。`syncOaInstance` 是写/查询外部 OA 的 POST，被包在 `useQuery` 里每 10 秒执行一次，页面切后台仍轮询（`refetchIntervalInBackground: true`）；后端有 3 秒快照复用，但仍是持续外部请求 → F06-11（P3）。 |
| `frontend/src/shared/components/SyncOaButton.tsx` | 16 | 手动同步的 pending、错误提示、能否重复点 | 全文读完。`loading={sync.isPending}`，失败走 `oaSyncErrorMessage`；未发现把底层异常原文直接上屏。没有额外请求键，但后端有快照/状态兜底，本块不单列。 |
| `frontend/src/modules/opportunity/OpportunityDetailPage.tsx` | 1,223 | 需求明细创建/数量/目标价、成交建单、AI 分析、列表错误态、权限按钮、金额展示 | 全文读完。目标价用了 `parseOptionalDecimal`，正确；成交建单走 `ReceivablePlanPicker` 的服务端校验。发现需求明细数量 `Number` 精度损失（F06-02）、明细创建无请求键（F06-05）、明细/跟进/任务列表失败当空态（F06-07）、AI 结果跨页签串段（F06-08）。金额 `0` 显示成 `-` 另见 F06-19（P3）。 |
| `frontend/src/modules/opportunity/OpportunityBoard.tsx` | 378 | 看板阶段查询错误、列内错误、阶段金额、推进动作、权限 | 全文读完。列内加载失败有“这一列加载失败”和重试，这点做得对；但阶段查询失败没有独立错误态，统计有数据时看板会渲染空白列（F06-09）；推进阶段无请求键/无 pending，弱网双击只生效一次（F06-17）。阶段金额/卡片金额走 `toTwoDecimals` 和 number（F06-03）。 |
| `frontend/src/modules/opportunity/OpportunityListPage.tsx` | 510 | 新建商机预计金额、漏斗金额、创建请求键、辅助查询错误、表格空态 | 全文读完。预计金额用 `parseOptionalNumber` 转 JS number（F06-01 P1）；漏斗 `Math.round` 抹掉分（F06-03）；新建商机无请求键（F06-04）；阶段/漏斗查询失败没有错误态（F06-16）。表格空态用 `emptyNode(query)` 能区分错误，这点正确。 |
| `frontend/src/modules/opportunity/OpportunityRecords.tsx` | 190 | 定制询价创建、数量、订单金额、请求键、错误态 | 全文读完。定制询价数量 `Number`、无请求键（F06-14）；订单金额 `toTwoDecimals`（F06-15）。生成报价草稿使用稳定 `quoteRequestKeyRef`，成功才清空，失败不换键，这一点正确。 |
| `frontend/src/shared/api/opportunity.ts` | 172 | 商机/明细金额类型、创建接口是否有请求键、成交接口 | 全文读完。`expected_amount?: number`、明细 `quantity: number` 响应均为 number，没有 `*_exact`；`createOpportunity`/`createItem` 没有请求键参数（F06-01/04/05）。`confirmWin` 无显式键，但后端成交按状态幂等，本块不单独计问题。 |
| `frontend/src/shared/api/inquiry.ts` | 140 | 询价创建/报价金额类型、请求键、数量/价格边界 | 全文读完。`createQuoteFromInquiry` 的 `unit_cost`/`quoted_price` 是 number，响应 `quoted_price` 也是 number（F06-06）；`createCustomInquiry` 无请求键（F06-18）。 |
| `frontend/src/modules/lead/LeadListPage.tsx` | 992 | 新建/转化/分配/释放线索、预计金额、请求键、错误提示、权限 | 全文读完。预计金额用 `parseOptionalDecimal`，比商机页正确；但只校验 `Number` 非负，没有 16 位/2 位小数上限（F06-13 的一部分）。新建/转化无请求键（F06-12）；分配不传 reason、释放不传 reason，分配失败只 Banner/refetch（F06-13）。 |
| `frontend/src/shared/api/lead.ts` | 114 | 线索创建/分配/释放/转化请求形状 | 全文读完。`createLead`/`convertLead` 没有请求键参数；`releaseLead` 没有 reason 参数；`assignLead` 虽支持可选 `reason`，页面没传（F06-12/13）。 |

## 二、发现清单

### F06-01 P1：新建商机“预计金额”Decimal → JS number，16 位边界会差 1 分

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityListPage.tsx:104-110`、`frontend/src/shared/api/opportunity.ts:14`、`frontend/src/modules/opportunity/OpportunityDetailPage.tsx:536/640`、`frontend/src/modules/opportunity/OpportunityBoard.tsx:166-168`；后端 `backend/app/modules/opportunity/schema.py:16-20`、`service.py:202`。
- **触发条件**：用户在新建商机里填 `99999999999999.99`（或任何超过 JS 安全整数精度的 16 位两位小数金额），`parseOptionalNumber` 把文本转成 `number` 再 POST。
- **后果**：金额在进入后端前已被改写，商机预计金额、漏斗/看板汇总、后续报价草稿的参考金额都会从错值出发。Node 实测：`Number('99999999999999.99').toFixed(2) -> '99999999999999.98'`，少 1 分；再叠加后端 `_number` 出参 float，展示和汇总会继续漂移。
- **证据代码**：
  ```ts
  // OpportunityListPage.tsx:104-110
  expected_amount: (() => {
    const v = parseOptionalNumber(form.expected_amount)
    ...
    return v
  })(),
  ```
  ```ts
  // opportunity.ts:14
  expected_amount?: number | null
  ```
  ```python
  # opportunity/schema.py:16-20
  expected_amount: Optional[Decimal] = Field(default=None, max_digits=16, decimal_places=2)
  # opportunity/service.py:202（同类出参）
  "expected_amount": _number(opportunity.expected_amount),
  ```
- **建议**：表单改用 `parseOptionalDecimal`（返回原始十进制字符串），API 标 `expected_amount?: string | null`，列表/详情/看板优先用后端 `expected_amount_exact`；没有 exact 字段就让后端补，不要在前端 `Number` 后再 `toFixed`。同时前端加 16 位整数位/2 位小数校验，避免把 422 当唯一防线。
- **测试覆盖**：未覆盖。`backend/tests/test_external_review_confirmed.py:145-160` 只证明后端 `Decimal("100.25")` 能汇总成 100.25，没有前端 16 位输入用例；无前端测试。

### F06-02 P2：需求明细数量用 `Number`，超过 3 位小数会丢精度

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityDetailPage.tsx:281-284`；后端 `backend/app/modules/opportunity/schema.py:81`。
- **触发条件**：添加需求商品时填 `9999999999999.999`（后端 `Decimal(16,3)` 边界附近）或任何超过 JS 安全整数精度的 3 位小数数量。
- **后果**：提交前数量已被 JS number 改写；Node 实测 `Number('9999999999999.999').toFixed(3) -> '9999999999999.998'`。数量后续进入核价/报价明细，可能带出数量差和金额差。
- **证据代码**：
  ```ts
  // OpportunityDetailPage.tsx:281-284
  quantity: itemForm.quantity ? Number(itemForm.quantity) : 1,
  ```
  ```python
  # opportunity/schema.py:81
  quantity: Decimal = Field(..., max_digits=16, decimal_places=3)
  ```
- **建议**：数量输入保留字符串，API 标 `quantity: string`；前端用 `parseOptionalDecimal` 并校验 `decimal_places<=3`、整数位不超过 13。展示仍可用格式化，但不要用 number 回写。
- **测试覆盖**：部分覆盖。`backend/tests/test_opportunity_item_validation.py:31-34` 证明后端接受 `"2.125"` 这样的 Decimal 字符串，但没有前端 number 精度用例。

### F06-03 P2：漏斗/看板金额按 number 汇总并 `Math.round`，分位被抹掉

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityListPage.tsx:270`、`frontend/src/modules/opportunity/OpportunityBoard.tsx:37-38、83、166-168`；后端 `backend/app/modules/opportunity/service.py:202`。
- **触发条件**：漏斗金额含分位（例如 `1234.56`），或 16 位大额汇总。
- **后果**：`Math.round(row.amount)` 直接把金额变成整数元，页面显示 1235，少 0.44；大额时后端 `_number` 先把 Decimal 转 float，前端再四舍五入，分位和末位都可能不准。看板 `money` 虽保留两位，但源值已经是 float。
- **证据代码**：
  ```tsx
  // OpportunityListPage.tsx:270
  ¥{Math.round(row.amount).toLocaleString('zh-CN')}
  ```
  ```tsx
  // OpportunityBoard.tsx:37-38
  const amount = stats?.amount ?? 0
  ...
  // :166-168 附近
  money(amount)
  ```
- **建议**：漏斗/看板接口返回 `amount_exact` 字符串；前端统一用 `formatMoneyExact` 两位小数展示，不做 `Math.round`。若后端短期不能加 exact，至少不要取整，保留两位并注明来源精度。
- **测试覆盖**：未覆盖。无前端汇总金额测试；后端只有 `test_external_review_confirmed.py:160` 的单条 100.25 断言，没有漏斗/看板分位用例。

### F06-04 P2：新建商机无请求键，响应丢失后重试会多一条商机

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityListPage.tsx:98-126`、`frontend/src/shared/api/opportunity.ts:99-101`；后端 `backend/app/modules/opportunity/router.py:480-519`。
- **触发条件**：用户点“创建”后请求已落库，但网络超时/响应丢失；用户再点一次创建。
- **后果**：后端创建路由直接 `Opportunity(...) + commit`，没有 `request_key`/唯一约束；同一客户同一标题会多一条商机，后续看板和漏斗重复计数。
- **证据代码**：
  ```ts
  // OpportunityListPage.tsx:98-101
  const createMutation = useMutation({
    mutationFn: () =>
      createOpportunity({ ... }),
  ```
  ```ts
  // opportunity.ts:99-101
  export function createOpportunity(payload: Record<string, unknown>) {
    return api.post<Opportunity>('/opportunities', payload)
  }
  ```
- **建议**：创建弹窗打开时生成 `request_key`，提交带上；后端加唯一键或请求键表/幂等回放。失败不清键，成功才清；不要靠用户手速。
- **测试覆盖**：未覆盖。`test_opportunity_links.py` 只测联系人/复制/范围，没有商机创建幂等。

### F06-05 P2：添加需求明细无请求键，超时重试会多一条明细

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityDetailPage.tsx:277-312`、`frontend/src/shared/api/opportunity.ts:150-152`；后端 `backend/app/modules/opportunity/router.py:1262-1288`。
- **触发条件**：添加需求商品请求已成功但响应丢失，用户再点保存。
- **后果**：同一 SKU/规格/数量出现两条需求明细；后续报价若按明细生成，数量翻倍或客户看到重复行。
- **证据代码**：
  ```ts
  // OpportunityDetailPage.tsx:277-286
  const itemMutation = useMutation({
    mutationFn: () => createItem(opportunityId, {
      sku_id: itemForm.sku_id,
      quantity: ...,
  ```
  ```python
  # opportunity/router.py:1262-1276
  item = OpportunityItem(**payload.model_dump(), opportunity_id=opportunity_id)
  session.add(item)
  await session.flush()
  ```
- **建议**：同 F06-04，弹窗打开生成请求键，提交带上；后端对 `(opportunity_id, request_key)` 唯一或回放。注意不要在失败时换键。
- **测试覆盖**：未覆盖。无创建明细幂等用例。

### F06-06 P2：从定制询价直接生成报价，金额入参是 JS number，没有 exact

- **文件:行号**：`frontend/src/shared/api/inquiry.ts:120-139`、`frontend/src/modules/knowledge/KnowledgePage.tsx:184-187、239-242、1538-1559`（调用点在 F11，本块只审 API 契约）。
- **触发条件**：用户在知识库定制询价里用 `InputNumber` 填成本/报价/数量，超过 JS 安全整数精度或 4 位小数边界。
- **后果**：报价草稿的 `unit_cost/quoted_price/quantity` 在请求前已经是 number；Node 实测 16 位两位小数会差 1 分，4 位单价边界可能差最后一位。生成的是正式报价草稿，后续审批/转订单都从错值开始。
- **证据代码**：
  ```ts
  // inquiry.ts:120-125
  export function createQuoteFromInquiry(id: number, payload: {
    unit_cost: number
    quoted_price: number
    quantity?: number | null
  ```
  ```tsx
  // KnowledgePage.tsx:184-187
  const [quoteForm, setQuoteForm] = useState({
    unit_cost: null as number | null,
    quoted_price: null as number | null,
    quantity: null as number | null,
  })
  ```
- **建议**：API 改字符串，表单改用 Decimal 文本/`number-input` 的 string 模式；后端 schema 保持 Decimal，并校验单价 4 位、数量 3 位、总额 2 位。F11 复审时要连同调用点一起验收。
- **测试覆盖**：部分覆盖。B19 有询价链路测试，但没有前端 number→Decimal 精度用例；本块未运行测试。

### F06-07 P2：详情页明细/跟进/任务列表失败被显示成“还没有”

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityDetailPage.tsx:157-171`、`725-731`、`755-782`、`800-814`。
- **触发条件**：`itemsQuery`/`followupsQuery`/`tasksQuery` 返回 500、超时或解析失败，而页面其他查询正常。
- **后果**：表格 `dataSource={data ?? []}` 且 `loading` 结束，`empty` 显示“还没有需求明细/还没有跟进记录/还没有任务”。用户会把请求失败当成数据为空，可能重复添加需求、重复记跟进，或误判客户没跟进。
- **证据代码**：
  ```tsx
  dataSource={itemsQuery.data ?? []}
  loading={itemsQuery.isLoading}
  empty={emptyLine('还没有需求明细', ...)}
  ```
  ```tsx
  dataSource={can('followup:view') ? followupsQuery.data?.items ?? [] : []}
  empty={can('followup:view') ? emptyLine('还没有跟进记录', ...) : ...}
  ```
- **建议**：三个表都改成 `isError ? <ListError onRetry={refetch}/> : emptyLine(...)`；错误态不要用空态文案。跟进/任务还要区分“无权限”（已有）和“请求失败”。
- **测试覆盖**：未覆盖。无前端错误态测试。

### F06-08 P2：AI 分析结果不绑定请求类型，切换页签会串段/显示旧结果

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityDetailPage.tsx:106-120`、`852-910`。
- **触发条件**：先运行“商机分析”，未返回时点“产品推荐”；或先有一次分析结果，再点推荐；旧请求后到/旧 envelope 未清空。
- **后果**：`onSuccess` 只 `setAiEnvelope(data)`，不记录结果属于哪一项；`aiKind` 已切成 recommend，旧分析结果仍会显示在推荐区，或者切换页签后立刻把上一项结果显示在新页签下面。用户会把商机分析当成产品推荐/报价草稿，业务判断被误导。`aiMutation.error` 也没有在三个结果区渲染。
- **证据代码**：
  ```ts
  const aiMutation = useMutation({
    mutationFn: (kind) => ...,
    onSuccess: (data) => setAiEnvelope(data),
    onError: (error: Error) => Toast.error(error.message),
  })
  ```
  ```tsx
  {aiKind === 'recommend' && <AgentInsight envelope={aiEnvelope} empty="正在推荐…" />}
  ```
- **建议**：把结果存成 `{kind, data}`，渲染前判 `result.kind === aiKind`；mutation 开始时清空或保留对应 kind 的缓存；错误也按 kind 渲染。`AgentInsight` 只接收属于当前 kind 的 envelope。
- **测试覆盖**：未覆盖。无前端 AI 并发/切页签测试。

### F06-09 P2：看板阶段查询失败时，统计有数据但看板空白

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityBoard.tsx:250-255`、`313-326`、`334-352`。
- **触发条件**：`stagesQuery` 失败，`statsQuery` 成功且 `totalCount > 0`。
- **后果**：代码只处理 `statsQuery.isError`，没有处理 `stagesQuery.isError`；`stages=[]`，看板渲染一个空横向容器，用户看不到任何列，也没有“阶段加载失败/重试”。会以为商机被清空。
- **证据代码**：
  ```tsx
  const stages = useMemo(() => [...(stagesQuery.data ?? [])]..., [stagesQuery.data])
  ...
  if (statsQuery.isError) return <...看板统计加载失败...>
  ...
  {stages.map((stage) => <BoardColumn ... />)}
  ```
- **建议**：在 `statsQuery.isError` 之前或之后加 `stagesQuery.isError` 分支，显示“阶段加载失败”+重试；`totalCount > 0 && stages.length === 0` 时不要渲染空看板。
- **测试覆盖**：未覆盖。无前端看板阶段错误测试。

### F06-10 P3：OA 重发/作废每次点击新请求键，失败重试不是同一次核定

- **文件:行号**：`frontend/src/shared/components/ResolveOaActions.tsx:60-95`；后端 `backend/app/modules/dingtalk/router.py:562-590`、`service.py:576-670`。
- **触发条件**：用户点“重发”/“作废”，请求超时或返回“结果不明”，再点一次。
- **后果**：`newRequestKey()` 每次点击都生成新键，后端把第二次当新的一次核定，不能按同键回放“上次到底成没成”；虽然状态闸门通常能挡住重复建外部单，但用户看到的是“又核定了一次”，没有确定反馈。链接也没有 `resolve.isPending` 禁用，弱网可连点。
- **证据代码**：
  ```ts
  onConfirm={() => resolve.mutate({ action: 'resend', requestKey: newRequestKey() })}
  ...
  onConfirm={() => resolve.mutate({ action: 'abandon', requestKey: newRequestKey() })}
  ```
- **建议**：每行/每个动作在第一次点击时生成并保存键，失败重试复用；成功后清空。`Popconfirm` 的确认按钮或链接加 pending 禁用。
- **测试覆盖**：部分覆盖。`test_dingtalk_oa_state_machine.py` 覆盖后端同键/状态；无前端键复用/连点测试。

### F06-11 P3：自动同步把 POST 当 query，每 10 秒请求外部 OA 且后台不停

- **文件:行号**：`frontend/src/shared/components/AutoOaSync.tsx:7-20`；后端 `backend/app/modules/dingtalk/router.py:655-720`。
- **触发条件**：详情页停留、切到后台标签页。
- **后果**：`syncOaInstance` 是查询/同步外部 OA 的 POST，却放在 `useQuery` 的 `queryFn`；`refetchIntervalInBackground: true` + 10 秒间隔会在后台持续打外部接口。后端 3 秒快照复用降低了压力，但不能消除持续外部请求和日志/限流风险。
- **证据代码**：
  ```ts
  const sync = useQuery({
    queryFn: async () => { const latest = await syncOaInstance(row!.id) ... },
    refetchIntervalInBackground: true,
    refetchInterval: query => ... 10000,
  })
  ```
- **建议**：改成显式“同步”动作或把轮询降为只读本地快照接口；如果必须自动同步，至少关闭后台轮询、加可见的“自动同步中/上次同步时间”，并在页面卸载时停止。
- **测试覆盖**：部分覆盖。`test_oa_live_sync.py` 测后端同步/快照；无前端轮询行为测试。

### F06-12 P3：线索新建/转化无请求键，响应丢失后前端无法判断是否已成功

- **文件:行号**：`frontend/src/modules/lead/LeadListPage.tsx:231-240、291-303`、`frontend/src/shared/api/lead.ts:49-56、81-90`；后端 `backend/app/modules/lead/router.py:60-115、340-405`。
- **触发条件**：新建线索或转客户请求已落库，但响应超时；用户重试。
- **后果**：新建线索后端直接 `Lead(...)+commit`，没有请求键，重试会多一条线索。转化有 `converted` 状态和行锁，后端能挡第二次，但前端无法区分“已成功未回执”和“确实失败”，只能看到 409/错误，操作体验不确定。
- **证据代码**：
  ```ts
  const createMutation = useMutation({ mutationFn: () => createLead(form) })
  const convertMutation = useMutation({ mutationFn: () => convertLead(convertTarget!.id, {...}) })
  ```
  ```python
  # lead/router.py:83-85
  lead = Lead(**data, created_by=user.id, status="pending")
  session.add(lead)
  await session.flush()
  ```
- **建议**：创建/转化弹窗打开生成请求键并传给后端；转化即使后端有状态闸门，也应带键让前端能拿到“上次已成功”的确定结果。成功后清键，失败保留。
- **测试覆盖**：部分覆盖。转化幂等有后端锁/状态测试；新建线索幂等未覆盖；无前端测试。

### F06-13 P3：线索分配/释放不传原因，分配失败只 Banner/refetch，金额边界只靠后端

- **文件:行号**：`frontend/src/modules/lead/LeadListPage.tsx:133-136、257-267、269-277、833-834`、`frontend/src/shared/api/lead.ts:61-69`。
- **触发条件**：分配/释放线索；或在转商机时填超大/超 2 位小数预计金额。
- **后果**：`assignLead` 不传 `reason`、`releaseLead` 没有 reason 参数，后端审计无法解释为何改派/释放。分配失败 `onError` 只 `usersQuery.refetch()`，错误靠 `Banner` 显示，没有 Toast/分类；用户可能没注意到分配失败。预计金额只判 `Number` 非负，没有 16 位/2 位小数上限，`1e20` 会通过前端校验再由后端 422。
- **证据代码**：
  ```ts
  const assignMutation = useMutation({
    mutationFn: ({ leadId, ownerId }) => assignLead(leadId, ownerId),
    onError: () => { void usersQuery.refetch() },
  })
  ```
  ```ts
  const invalidOpportunityAmount = opportunityAmount !== null
    && (Number.isNaN(Number(opportunityAmount)) || Number(opportunityAmount) < 0)
  ```
- **建议**：分配/释放在弹窗里要求或默认填原因并传给后端；分配失败给明确 Toast/错误态；预计金额加 16 位/2 位小数校验，输入保留字符串。
- **测试覆盖**：部分覆盖。`test_lead_batch_reason.py` 覆盖批量原因，不覆盖单条分配/释放前端；无前端金额边界测试。

### F06-14 P3：定制询价记录无请求键，数量用 `Number`

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityRecords.tsx:74-79`、`frontend/src/shared/api/inquiry.ts:50-55、84-86`；后端 `backend/app/modules/inquiry/router.py:112-148`。
- **触发条件**：在商机记录页记录定制询价，请求超时重试；或数量超过 JS 安全精度。
- **后果**：后端 `create_inquiry` 直接 `CustomInquiry(...)+commit`，无请求键，重试会多一条定制询价；数量 `Number` 转 number 后进 `Decimal`，边界会差最后一位。后续报价/打样按这条需求走，重复需求会重复生成报价。
- **证据代码**：
  ```ts
  mutationFn: () => createCustomInquiry({
    ...
    quantity: inquiryForm.quantity.trim() ? Number(inquiryForm.quantity) : null,
  })
  ```
- **建议**：同 F06-04，加请求键；数量/目标价改字符串/Decimal 文本，前端校验 3 位小数/16 位总长。
- **测试覆盖**：未覆盖。无定制询价创建幂等/前端精度测试。

### F06-15 P3：商机记录里的订单金额走 `toTwoDecimals`，大额分位可能不准

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityRecords.tsx:167`；后端订单金额出参 `number`（B04/B17 已记录）。
- **触发条件**：订单金额为 16 位两位小数边界。
- **后果**：`toTwoDecimals(row.total_amount)` 对已经是 number 的金额再做一次四舍五入，末位可能差 1 分；用户在同一商机记录里看到的订单金额与订单详情/财务不一致。
- **证据代码**：
  ```tsx
  { title: '金额', render: (_, row: Order) => formatMoney(toTwoDecimals(row.total_amount), 'CNY') }
  ```
- **建议**：订单 API 提供 `total_amount_exact`，这里直接用 exact 格式化；没有 exact 时不要二次舍入。
- **测试覆盖**：未覆盖。无前端订单金额展示测试。

### F06-16 P3：商机列表的阶段/漏斗查询失败没有错误态

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityListPage.tsx:77-78、262-276`。
- **触发条件**：`stagesQuery` 或 `funnelQuery` 失败，主列表成功。
- **后果**：阶段筛选下拉为空、漏斗条不渲染，页面看起来像“没有阶段/没有漏斗”，没有重试或错误提示；用户可能误以为配置丢失。
- **证据代码**：
  ```ts
  const stagesQuery = useQuery({ queryKey: ['stages'], queryFn: listStages })
  const funnelQuery = useQuery({ queryKey: ['funnel'], queryFn: getFunnel })
  ```
- **建议**：两个辅助查询分别加 `isError` 小错误条/重试；不要把失败静默成空数组。
- **测试覆盖**：未覆盖。无前端辅助查询错误测试。

### F06-17 P3：看板推进阶段无请求键、无 pending，连点只生效一次但不提示

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityBoard.tsx:295-310`、`frontend/src/shared/api/opportunity.ts:105-107`。
- **触发条件**：用户快速连点同一卡片的推进箭头。
- **后果**：两次 `changeStage` 都带同一个目标 `stage_id`；后端第一次成功后第二次会因阶段不同/状态闸门报错，前端会弹一次成功、一次失败，用户不确定是否推进了两次。没有请求键，也没有卡片级 pending 禁用。
- **证据代码**：
  ```ts
  await changeStage(row.id, { stage_id: next.id })
  Toast.success(...)
  ```
- **建议**：`move` 加 in-flight 集合或按钮 pending 禁用；请求键可选，但至少要保证同一卡片同时只有一个推进请求。
- **测试覆盖**：未覆盖。无前端连点/并发测试。

### F06-18 P3：定制询价创建 API 无请求键

- **文件:行号**：`frontend/src/shared/api/inquiry.ts:84-86`；后端 `backend/app/modules/inquiry/router.py:112-148`。
- **触发条件**：创建定制询价成功但响应丢失，用户重试。
- **后果**：多一条 `CustomInquiry`，编号不同；后续可能各自生成报价/打样，同一需求重复流转。
- **证据代码**：
  ```ts
  export function createCustomInquiry(payload: CustomInquiryPayload) {
    return api.post<CustomInquiryRow>('/custom-inquiries', payload)
  }
  ```
- **建议**：与 F06-14 一起加 `request_key`，后端建唯一键/回放。
- **测试覆盖**：未覆盖。

### F06-19 P3：金额 0 被显示成 `-`，无法区分“0 元”和“未填”

- **文件:行号**：`frontend/src/modules/opportunity/OpportunityListPage.tsx:193-218`、`OpportunityDetailPage.tsx:512/536/640`、`OpportunityBoard.tsx:37-38`。
- **触发条件**：商机预计金额/需求目标价为 `0`。
- **后果**：`record.expected_amount ? ... : null`、`v ? ... : '-'` 把 0 当空值；用户看到“-”，无法判断是 0 元还是未填，影响报价/审批判断。
- **证据代码**：
  ```tsx
  const amount = record.expected_amount ? formatMoney(...) : null
  ```
  ```tsx
  {v ? `¥${v}` : '-'}
  ```
- **建议**：用 `v == null` 判空，0 显示 `¥0.00`；看板统计 0 也应显示 0，不要显示 `-`。
- **测试覆盖**：未覆盖。

## 三、补充维度核对

### 1. 负数、0、超大值、小数位

- F06-01：预计金额无 16 位/2 位小数前端校验；`parseOptionalNumber` 接受 `1e20`、超过 2 位小数，最终靠后端 422。
- F06-02：需求数量无 3 位/16 位前端校验，`Number` 先损精度。
- F06-06：报价成本/报价/数量是 number，无 4 位/3 位前端校验。
- F06-13：线索转商机预计金额只判非负，无 16 位/2 位上限。
- F06-14：定制询价数量无 3 位/16 位校验。
- F06-19：0 被当空值。
- **未发现**：本块没有收款/退款/负金额入口；没有“负数直接入账”的前端路径。

### 2. 事务边界

- F06 的写操作都是单次 HTTP + 后端单事务：商机创建、需求明细创建、定制询价创建、线索创建/转化/分配/释放。
- 后端 `create_item` 是 `add → flush → write_audit → commit`，如果审计写失败会整体回滚，不会只留明细；但这是代码路径判断，未做故障注入。
- 前端没有“先建订单再建明细”的多步编排；成交建单由 `ReceivablePlanPicker` 服务端校验，页面只提交一次。
- **未验证**：真实 PG 下审计失败、网络中断、后端 commit 后响应丢失；本块未构造。

### 3. 异常吞掉 / 泄露 / 日志敏感信息

- `OpportunityDetail` 的明细/跟进/任务查询失败被空态吞掉（F06-07）；`OpportunityBoard` 阶段失败被空看板吞掉（F06-09）；`OpportunityList` 阶段/漏斗失败静默（F06-16）。
- 写操作 `onError` 多处直接 `Toast.error(error.message)`/`Banner` 展示后端 message；后端 `AppError` 是业务文案，本块没有看到 SQL/堆栈/路径直出，但没有按 403/404/409/422/5xx 分类（F06-13 等）。
- `ResolveOaActions` 使用 `error.message`，没有把外部 OA 原始报文写到页面；`SyncOaButton` 用 `oaSyncErrorMessage` 做了收敛，这点正确。
- 日志：本块前端不写日志；未检查后端日志是否含成本/金额，属后端块。

### 4. 审计

- F06 没有改价、收款、作废等关键钱操作；唯一“生成钱”的动作是从定制询价生成报价草稿（F06-06），前端只传金额，审计由后端报价模块负责。
- 商机/需求/线索创建、分配、释放的后端审计不在本块重审；本块只确认前端是否传请求键/原因：
  - 商机/需求/询价/线索创建不带请求键（F06-04/05/14/18/12）。
  - 线索分配/释放不传 reason（F06-13）。
- **未验证**：后端这些创建是否都写 before/after、金额是否字符串精确；属 B19/B20 已审范围，本块不重复下结论。

### 5. 对照 `backend/tests`

- `backend/tests/test_opportunity_item_validation.py:31-34`：后端接受 Decimal 字符串 `"2.125"`、`"1.2345"`；**不覆盖**前端 `Number`。
- `backend/tests/test_opportunity_links.py:15-58`：联系人归属/复制/范围；**不覆盖**商机创建幂等。
- `backend/tests/test_external_review_confirmed.py:145-160`：商机 `Decimal("100.25")` 汇总；**不覆盖**前端 16 位金额。
- `backend/tests/test_oa_live_sync.py`、`test_dingtalk_oa_state_machine.py`：后端同步/核定状态；**不覆盖**前端每次新键、无 pending、10 秒后台轮询。
- `backend/tests/test_lead_assignee_user_access.py`、`test_lead_batch_reason.py`：后端范围/批量原因；**不覆盖**单条分配/释放前端不传 reason。
- 本块没有运行测试，也没有前端自动化测试；所有“覆盖”只指用例是否覆盖触发条件，不是“测试全绿”。

## 四、本块没有看的部分及原因

- `frontend/src/shared/components/AdoptOaInstanceModal.tsx`：F06 只审 `ResolveOaActions` 的调用入口，认领弹窗内部实例校验/权限未逐行读；不在 14 文件清单。
- `frontend/src/shared/components/AgentInsight.tsx`：只按 `OpportunityDetailPage` 调用点判断 envelope 渲染，未审内部 Markdown/HTML/链接安全；属后续共享组件块。
- `frontend/src/shared/components/UserSearchSelect.tsx`、`GuardedButton.tsx`、`money.ts`、`number-input.ts`、`requestKey.ts`：只按调用点引用；未全文复核，不能替代 F13。
- `frontend/src/modules/knowledge/KnowledgePage.tsx`：只读 `184-187、239-242、1538-1559` 证明 F06-06 调用点；整页属 F11，未逐行审。
- `backend/app/modules/opportunity/{router,service,schema,model}.py`、`lead/{router,service}`、`inquiry/{router,service}`、`dingtalk/{router,service}`：只定向读创建/幂等/金额/状态相关段，用于证明 F06-01~18；不重审 B19/B20 全模块，也不替代后端全文审查结论。
- `backend/tests` 全量：只读/检索上述相关段，未运行；没有构造并发、弱网、故障注入。
- 浏览器/E2E：未执行；F06-01/02/04/05/06/10/12/14 的触发都需要弱网“已提交但响应丢失”或 16 位输入，未实机复现。
- OA 真实外部系统：未连接同序 OA；F06-10/11 只按前端调用和后端路由/状态机判断，未验证外部单实际创建/回放。
- `OpportunityListPage`/`OpportunityBoard`/`OpportunityDetailPage` 的 CSS/响应式布局：本块只看逻辑与金额/错误态，未按视觉审查标准逐屏检查；属 UI 验收。
