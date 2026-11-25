/**
 * F11 案例 / 知识 / 新品洞察复核脚本。
 * 运行：node review/repro/f11_frontend/check_f11.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  knowledge: 'frontend/src/modules/knowledge/KnowledgePage.tsx',
  inquiryApi: 'frontend/src/shared/api/inquiry.ts',
  inquirySchema: 'backend/app/modules/inquiry/schema.py',
  numberInput: 'frontend/src/shared/number-input.ts',
  cases: 'frontend/src/modules/cases/CasesPage.tsx',
  casesApi: 'frontend/src/shared/api/cases.ts',
  casesSchema: 'backend/app/modules/cases/schema.py',
  casesService: 'backend/app/modules/cases/service.py',
  insight: 'frontend/src/modules/product/ProductInsightsPage.tsx',
  insightApi: 'frontend/src/shared/api/insight.ts',
  insightSchema: 'backend/app/modules/product_insight/schema.py',
  insightRouter: 'backend/app/modules/product_insight/router.py',
  quoteService: 'backend/app/modules/quote/service.py',
  costTest: 'backend/tests/test_quote_cost_gate.py',
}
const src = Object.fromEntries(Object.entries(F).map(([k, rel]) => [k, readFileSync(resolve(ROOT, rel), 'utf8')]))
const results = []
const check = (id, name, verdict, ok, evidence) => results.push({ id, name, verdict, ok: Boolean(ok), evidence })
const has = (text, s) => text.includes(s)
const between = (text, start, end) => {
  const i = text.indexOf(start)
  if (i < 0) return ''
  const j = text.indexOf(end, i + start.length)
  return j < 0 ? text.slice(i) : text.slice(i, j)
}

check('F11-01', '数量/目标价非法输入静默变 null', 'CONFIRMED',
  has(src.numberInput, "const n = Number(text)")
    && has(src.numberInput, 'return Number.isFinite(n) ? n : NaN')
    && has(src.knowledge, 'quantity: form.quantity.trim() ? parseOptionalNumber(form.quantity) : null')
    && has(src.knowledge, 'target_price: form.target_price.trim() ? parseOptionalNumber(form.target_price) : null')
    && has(src.knowledge, 'quantity: reviseForm.quantity ? Number(reviseForm.quantity) : null')
    && !/saveForm[\s\S]{0,700}?Number\.isNaN/.test(src.knowledge)
    && !/reviseMutation[\s\S]{0,300}?Number\.isNaN/.test(src.knowledge),
  'parseOptionalNumber("abc") 返回 NaN；saveForm/reviseMutation 不检查；JSON.stringify(NaN) 变 null',
)

check('F11-02', '核价成本 0 元可提交（业务口径未定）', 'PENDING',
  has(src.knowledge, 'min={0}')
    && has(src.inquirySchema, 'unit_cost: Decimal = Field(ge=0')
    && has(src.costTest, 'test_explicit_complete_cost_including_zero_can_submit')
    && has(src.costTest, 'assert version.approval_status == "approved"')
    && has(src.quoteService, '成本未知时若按 0 记，会算出 100% 毛利'),
  '0 成本当前被后端/测试显式允许；代码只说"不能缺成本"，没说"不能为 0"。若业务认为 0 非法，是规则缺失；按现状不能算实现漏检',
)

check('F11-03', '转报价成本/报价/数量走 JS number', 'CONFIRMED',
  ['unit_cost', 'quoted_price', 'quantity'].every((k) => has(src.knowledge, `${k}: null as number | null`))
    && has(src.knowledge, 'unit_cost: quoteForm.unit_cost!')
    && has(src.knowledge, 'quoted_price: quoteForm.quoted_price!')
    && has(src.knowledge, 'quantity: quoteForm.quantity ?? undefined')
    && has(src.inquirySchema, 'max_digits=16, decimal_places=4')
    && has(src.inquirySchema, 'max_digits=16, decimal_places=3')
    && String(Number('999999999999.9997')) !== '999999999999.9997',
  `16 位 4 位小数合法值 999999999999.9997 实测变 ${Number('999999999999.9997')}；JSON 发出去已不是原值`,
)

check('F11-04', '新建案例无请求键', 'CONFIRMED',
  has(src.casesApi, 'export function createCase(payload: Record<string, unknown>)')
    && has(src.casesApi, "api.post<CaseRow>('/cases', payload)")
    && !/request_key/.test(between(src.casesSchema, 'class CaseCreate', 'class CaseUpdate'))
    && has(src.cases, 'return editId ? updateCase(editId, payload) : createCase(payload)'),
  'CaseCreate/前端 createCase 都没有请求键；响应丢失重试会多建草稿',
)

check('F11-05', '编辑案例无版本冲突校验', 'CONFIRMED',
  has(src.casesSchema, 'class CaseUpdate(PatchModel)')
    && !/expected_(updated_at|version)/.test(between(src.casesSchema, 'class CaseUpdate', 'class CaseReview'))
    && has(src.cases, 'const payload = {')
    && has(src.cases, '...form')
    && has(src.cases, 'editId ? updateCase(editId, payload)'),
  '前端提交整份 ...form；CaseUpdate 无 expected 字段；后端行锁只串行，不比较版本，后保存覆盖先保存',
)

check('F11-06', '换客户不清证据，保存 422', 'CONFIRMED',
  has(src.cases, 'customer_id: customer?.id,')
    && has(src.cases, 'quote_id: undefined,')
    && has(src.cases, 'order_id: undefined,')
    && has(src.cases, 'sample_id: undefined,')
    && !/customer_id: customer\?\.id,[\s\S]{0,180}?evidences: \[\]/.test(src.cases)
    && has(src.casesService, '证据不属于当前客户') === false
    && has(src.casesService, 'validate_evidences'),
  '换客户 only 清 quote/order/sample；form.evidences 原样保留；后端 validate_evidences 校验同客户',
)

check('F11-07', '证据/时间线失败当没有单据', 'CONFIRMED',
  ['quotesQuery', 'ordersQuery', 'samplesQuery', 'formTimelineQuery'].every((n) => !new RegExp(`${n}\\\\.isError`).test(src.cases))
    && has(src.cases, '{(formTimelineQuery.data ?? []).length === 0 && (')
    && has(src.cases, '这个客户还没有时间线事件')
    && has(src.cases, '(quotesQuery.data?.items ?? [])'),
  '四个查询都没有 isError；失败 data 为空，证据下拉无选项、时间线显示“还没有时间线事件”',
)

check('F11-08', '审核人看到他人草稿编辑/提交/删除', 'CONFIRMED',
  has(src.cases, "{(record.status === 'draft' || record.status === 'rejected') && (")
    && has(src.cases, 'onClick={() => openEdit(record)}')
    && has(src.cases, 'submitMutation.mutate(record.id)')
    && has(src.cases, "(isReviewer || record.author_id === currentUserId) && (")
    && has(src.casesService, 'if case.status in ("draft", "rejected") and case.author_id != user.id:')
    && has(src.casesService, 'raise AppError(ErrorCode.NOT_FOUND, "案例不存在", 404)'),
  '编辑/提交对任何 draft/rejected 显示；删除对 reviewer 或作者显示；后端 ensure_case_draft_access 对非作者 404',
)

check('F11-09', '新建询价无请求键', 'CONFIRMED',
  has(src.inquiryApi, 'export function createCustomInquiry(payload: CustomInquiryPayload)')
    && !/request_key/.test(between(src.inquirySchema, 'class CustomInquiryCreate', 'class CustomInquiryUpdate'))
    && has(src.knowledge, 'await createCustomInquiry(data)')
    && has(src.knowledge, 'savedInquiry.current'),
  'CustomInquiryCreate/前端 create 都无键；savedInquiry 只在成功响应后赋值，响应丢失防不了',
)

check('F11-10', '编辑询价无版本冲突校验', 'CONFIRMED',
  has(src.inquirySchema, 'class CustomInquiryUpdate(PatchModel)')
    && !/expected_(updated_at|version)/.test(between(src.inquirySchema, 'class CustomInquiryUpdate', 'class CustomInquiryQuoteRequest'))
    && has(src.knowledge, 'updateCustomInquiry(existing.id, data)'),
  'update 只 PATCH 整份 data，无 expected；后端行锁只串行，后保存覆盖先保存',
)

check('F11-11', '审批记录/版本历史失败显示暂无数据', 'CONFIRMED',
  ['historyQuery', 'approvalsQuery'].every((n) => !new RegExp(`${n}\\\\.isError`).test(src.knowledge))
    && has(src.knowledge, 'dataSource={historyQuery.data ?? []}')
    && has(src.knowledge, 'dataSource={approvalRows}')
    && has(src.knowledge, 'approvalsQuery.data ?? []'),
  '两个查询无 isError；数据空直接空表格，用户以为没有审批/版本',
)

check('F11-12', '洞察价格假设/转换目标价 number/float 往返', 'CONFIRMED',
  has(src.insightRouter, 'float(row.price_assumption)')
    && has(src.insightApi, 'price_assumption: number | null')
    && has(src.insight, 'price_assumption: (() => {')
    && has(src.insight, 'parseOptionalNumber(form.price_assumption)')
    && has(src.insight, 'target_price: (() => {')
    && has(src.insight, 'parseOptionalNumber(convertForm.target_price)')
    && has(src.insightSchema, 'max_digits=16, decimal_places=2')
    && String(Number('99999999999999.99')) !== '99999999999999.99',
  `后端 Decimal 转 float 下发；前端再 parseOptionalNumber 提交；99999999999999.99 实测变 ${Number('99999999999999.99')}`,
)

check('F11-13', '新建洞察无请求键', 'CONFIRMED',
  has(src.insightApi, 'export function createProductInsight(payload: Record<string, unknown>)')
    && has(src.insight, 'createProductInsight(payload)')
    && !/request_key/.test(between(src.insightSchema, 'class InsightCreate', 'class InsightUpdate'))
    && has(src.insightSchema, 'model_config = ConfigDict(extra="forbid")'),
  'InsightCreate 没有 request_key 且 extra=forbid；前端创建 payload 不带键',
)

check('F11-14', '提交洞察评审未传已有请求键', 'CONFIRMED',
  has(src.insightApi, 'export function submitProductInsight(id: number, requestKey?: string)')
    && has(src.insightApi, 'request_key: requestKey')
    && has(src.insight, 'mutationFn: (id: number) => submitProductInsight(id)')
    && !/submitProductInsight\(id, /.test(src.insight),
  'API 支持 requestKey，页面只传 id；响应丢失后再点报“当前状态不能提交”',
)

check('F11-15', '评审历史失败显示“还没有评审记录”', 'CONFIRMED',
  has(src.insight, 'const roundsQuery = useQuery')
    && !/roundsQuery\.isError/.test(src.insight)
    && has(src.insight, '!roundsQuery.isLoading && (roundsQuery.data ?? []).length === 0')
    && has(src.insight, '还没有评审记录'),
  'roundsQuery 无 isError；失败时 data=[] 落“还没有评审记录”',
)

check('F11-16', '案例详情失败静默、旧版详情无竞态守卫', 'CONFIRMED',
  has(src.cases, 'const openDetail = async (row: CaseRow) => {')
    && /catch \{\s*\/\/ 拉详情失败就保持列表那份/.test(src.cases)
    && has(src.cases, 'setDetail(await getCase(id))')
    && !/requestId|seq|abort/i.test(between(src.cases, 'const openDetailById', '// 证据单据')),
  'openDetail catch 静默；openDetailById 无请求序号/当前 id 守卫，先发旧请求后返回会覆盖',
)

check('F11-17', '证据单据全量分页拉取', 'CONFIRMED',
  has(src.cases, 'const first = await load(1)')
    && has(src.cases, 'for (let page = 2; all.length < first.total; page += 1)')
    && has(src.cases, 'page_size: 100')
    && has(src.cases, 'fetchAll((page) => listQuotes')
    && has(src.cases, 'fetchAll((page) => listOrders')
    && has(src.cases, 'fetchAll((page) => listSamples'),
  '打开证据选择即循环拉完该客户全部报价/订单/打样，上限 100/页；客户单据多时串行多请求',
)

check('F11-18', '报价/数量前端 min=0，无小数位/上限', 'CONFIRMED',
  has(src.knowledge, 'min={0}')
    && has(src.knowledge, 'value={quoteForm.quoted_price ?? undefined}')
    && has(src.knowledge, 'value={quoteForm.quantity ?? undefined}')
    && has(src.inquirySchema, 'quoted_price: Decimal = Field(gt=0')
    && has(src.inquirySchema, 'quantity: Decimal | None = Field(default=None, gt=0'),
  '前端允许 0、无 precision；后端 quoted_price/quantity 都是 gt=0，提交才 422',
)

check('F11-19', '汇总失败静默、商机错误原文上屏', 'CONFIRMED',
  has(src.knowledge, 'const summaryQuery = useQuery')
    && !/summaryQuery\.isError/.test(src.knowledge)
    && has(src.knowledge, 'linkedOpportunity.data?.title ?? linkedOpportunity.error?.message'),
  'summary 无 isError，失败徽章不显示；商机读取失败把 error.message 直接拼到页头',
)

check('F11-20', '转需求客户搜索失败下拉为空', 'CONFIRMED',
  has(src.insight, 'const convertCustomers = useQuery')
    && !/convertCustomers\.isError/.test(src.insight)
    && has(src.insight, 'convertCustomers.data?.items ?? []'),
  'convertCustomers 无 isError；失败 optionList 为空，像没有客户',
)

check('F11-21', '案例驳回原因无 255 字上限', 'CONFIRMED',
  has(src.cases, '<TextArea\n            rows={4}\n            value={reviewNote}')
    && !/maxCount=\{255\}/.test(between(src.cases, '<TextArea\n            rows={4}', '</TextArea>'))
    && /note: str \| None = Field\(None, max_length=255\)/.test(src.casesSchema),
  '前端 TextArea 无 maxCount；后端 CaseReview.note max_length=255，提交才 422',
)


check('F11-22', '漏项：洞察编辑无版本冲突校验', 'CONFIRMED',
  has(src.insight, 'const payload = {')
    && has(src.insight, 'title: form.title,')
    && has(src.insight, 'updateProductInsight(editingId, payload)')
    && !/expected_(updated_at|version)/.test(between(src.insightSchema, 'class InsightUpdate', 'class InsightSubmit'))
    && has(src.insightRouter, 'for_update=True')
    && has(src.insightRouter, 'changes = payload.model_dump(exclude_unset=True)'),
  '前端整份 payload 更新；InsightUpdate 无 expected；后端行锁只串行，B 基于旧值提交会把 A 的修改盖回去',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed/pending, ${results.filter((r) => !r.ok).length} missing`)
