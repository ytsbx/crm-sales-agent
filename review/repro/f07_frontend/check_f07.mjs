/**
 * F07 打样页 + 分析页复核脚本。
 * 只读源码 + 数值验证，不改业务代码。
 * 运行：node review/repro/f07_frontend/check_f07.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  sampleList: 'frontend/src/modules/sample/SampleListPage.tsx',
  sampleModal: 'frontend/src/modules/sample/SampleDingTalkModal.tsx',
  sampleSource: 'frontend/src/modules/sample/SampleFromSourceModal.tsx',
  sampleApi: 'frontend/src/shared/api/sample.ts',
  analytics: 'frontend/src/modules/analytics/AnalyticsPage.tsx',
  analyticsApi: 'frontend/src/shared/api/analytics.ts',
}
const src = Object.fromEntries(Object.entries(F).map(([k, rel]) => [k, readFileSync(resolve(ROOT, rel), 'utf8')]))
const results = []
const check = (id, name, verdict, ok, evidence) => results.push({ id, name, verdict, ok: Boolean(ok), evidence })
function between(text, start, end) {
  const i = text.indexOf(start)
  if (i < 0) return ''
  const j = text.indexOf(end, i + start.length)
  return j < 0 ? text.slice(i) : text.slice(i, j)
}
const matches = (text, re) => re.test(text)

check('F07-01', '分析页查询失败静默为零/空', 'CONFIRMED',
  (src.analytics.match(/useQuery\(/g) || []).length >= 18
    && (src.analytics.match(/isError/g) || []).length === 1
    && /const money = \(value\?: number\) => `¥\$\{Math\.round\(value \?\? 0\)/.test(src.analytics)
    && /value: money\(receivable\?\.unreceived_amount\)/.test(src.analytics)
    && /deliverySummary\?\.late_count \?\? 0/.test(src.analytics)
    && /dataSource=\{\(productQuery\.data \?\? \[\]\)/.test(src.analytics)
    && /empty=\{keyword \? noMatches : '还没有报价数据'\}/.test(src.analytics),
  `${(src.analytics.match(/useQuery\(/g) || []).length} 个 useQuery，只有 ${(src.analytics.match(/isError/g) || []).length} 个 isError（targets）；money(undefined)=¥0，表失败显示“还没有报价数据”`,
)

check('F07-02', '新建打样无请求键', 'CONFIRMED',
  /const createMutation = useMutation/.test(src.sampleList)
    && /return createSample\(\{/.test(src.sampleList)
    && !/request_key/.test(between(src.sampleList, 'const createMutation', 'const approveMutation'))
    && /export function createSample\(payload: Record<string, unknown>\)/.test(src.sampleApi),
  '前端 createSample 无键；后端 SampleCreate 没有 request_key，create_sample 直接 SampleRequest+commit',
)

check('F07-03', '追加打样明细无请求键', 'CONFIRMED',
  /mutationFn: \(\) => addSampleItem\(detailId!, draftPayload\(newItem\)\)/.test(src.sampleList)
    && /export function addSampleItem\(id: number, payload: Record<string, unknown>\)/.test(src.sampleApi)
    && !/request_key/.test(between(src.sampleApi, 'export function addSampleItem', 'export function updateSampleItem')),
  '明细 POST 无键；后端 add_sample_item 仅按 sample 行锁，超时重试仍会再插一条',
)

check('F07-04', '来源打样每次编辑重置请求键', 'CONFIRMED',
  /const requestKey = useRef\(newRequestKey\(\)\)/.test(src.sampleSource)
    && /function change\(id: number, patch: Partial<Draft>\) \{[\s\S]*?requestKey\.current = newRequestKey\(\)/.test(src.sampleSource)
    && /request_key: requestKey\.current/.test(src.sampleSource),
  'change() 任何字段改动都换新键；后端 create_from_source 按 key+hash 幂等，换键就是新请求',
)

check('F07-05', '运费/打样费用/工时 Number 化', 'CONFIRMED',
  /const fee = raw === '' \? 0 : Number\(raw\)/.test(src.sampleList)
    && /const fee = raw === '' \? null : Number\(raw\)/.test(src.sampleList)
    && /const hours = rawHours === '' \? null : Number\(rawHours\)/.test(src.sampleList)
    && /shipping_fee: number/.test(src.sampleApi)
    && Number('99999999999999.99') === 99999999999999.98,
  `Number('99999999999999.99') -> ${Number('99999999999999.99')}；三个输入都先变 float 再 POST`,
)

check('F07-06', '制作依据附件失败显示“没有附件”', 'CONFIRMED',
  /const madeFilesQuery = useQuery\(\{[\s\S]*?queryFn: \(\) => listBusinessFiles\('sample', detailId!\)/.test(src.sampleList)
    && /!madeFilesQuery\.isLoading && \(madeFilesQuery\.data \?\? \[\]\)\.length === 0/.test(src.sampleList)
    && /这张单子上还没有附件/.test(src.sampleList)
    && !/madeFilesQuery\.isError/.test(src.sampleList),
  '只有 isLoading/data 两个分支，没有 isError；失败 data=undefined 落“还没有附件”',
)

check('F07-07', '重提/制作/修订未透出请求键', 'CONFIRMED',
  /export function resubmitSample\(id: number\)/.test(src.sampleApi)
    && /export function madeSample\([\s\S]{0,120}?remark\?: string,[\s\S]{0,80}?basisFileIds\?: number\[\],[\s\S]{0,30}?\) \{/.test(src.sampleApi)
    && /export function reviseSample\(id: number, remark\?: string\)/.test(src.sampleApi)
    && /request_key/.test(between(readFileSync(resolve(ROOT, 'backend/app/modules/sample/schema.py'), 'utf8'), 'class SampleResubmit', 'class SampleRevise'))
    && !/request_key/.test(between(readFileSync(resolve(ROOT, 'backend/app/modules/sample/schema.py'), 'utf8'), 'class SampleRevise', 'class SampleConfirm')),
  '后端 SampleResubmit/SampleMade 有 request_key，前端都没传；SampleRevise 本身无键，修订只能靠父版锁防重',
)

check('F07-08', '拒绝原因只靠后端 422', 'CONFIRMED',
  /approveSample\(detailId!, approved, approved \? undefined : rejectReason\)/.test(src.sampleList)
    && /placeholder="拒绝原因（拒绝时必填）"/.test(src.sampleList)
    && !/rejectReason\.trim\(\).*disabled/.test(src.sampleList)
    && /if not payload\.approved and not \(payload\.reject_reason or ""\)\.strip\(\)/.test(readFileSync(resolve(ROOT, 'backend/app/modules/sample/router.py'), 'utf8')),
  '前端不校验，拒绝按钮始终可点；后端 approve 路由才 422',
)

check('F07-09', '工时×10 用浮点直出', 'CONFIRMED',
  /Number\(prodForm\.estimated_hours\) \* 10/.test(src.sampleList)
    && /const points = raw\.trim\(\) && Number\.isFinite\(n\) \? n \* 10 : null/.test(src.sampleModal)
    && Number('0.07') * 10 !== 0.7,
  `Number('0.07') * 10 -> ${Number('0.07') * 10}；两处都直接乘 10 后显示`,
)

check('F07-10', '运费/打样费用展示对 number 再格式化', 'CONFIRMED',
  /render: \(v: number\) => `¥\$\{v\.toFixed\(2\)\}`/.test(src.sampleList)
    && /formatMoney\(toTwoDecimals\(detail\.sample_fee\), 'CNY'\)/.test(src.sampleList)
    && !/shipping_fee_exact|sample_fee_exact/.test(src.sampleApi),
  'API 只给 number，没有 *_exact；展示端 toFixed/toTwoDecimals 二次舍入',
)

check('F07-11', '分析页金额取整/二次舍入', 'CONFIRMED',
  /const money = \(value\?: number\) => `¥\$\{Math\.round\(value \?\? 0\)/.test(src.analytics)
    && /value: Math\.round\(row\.value\)/.test(src.analytics)
    && /合计 ¥\{Math\.round\(drilldownQuery\.data\?\.total \?\? 0\)/.test(src.analytics)
    && /formatMoney\(toTwoDecimals\(v \?\? 0\), 'CNY'\)/.test(src.analytics)
    && /total: number/.test(src.analyticsApi),
  '概览/KPI 取整到元；环图 value Math.round；下钻 total Math.round；明细对 number 再 toTwoDecimals',
)

check('F07-12', '目标表单只判 NaN，不拦负数/上限', 'CONFIRMED',
  /function targetNumber[\s\S]*?Number\.isNaN\(n\)[\s\S]*?return n \?\? 0/.test(src.analytics)
    && /function targetMoney[\s\S]*?Number\.isNaN\(value\)[\s\S]*?return value \?\? 0/.test(src.analytics)
    && !/targetMoney[\s\S]{0,120}< 0/.test(src.analytics)
    && !/sales_target[\s\S]{0,120}maxLength/.test(src.analytics),
  '两个函数只判 NaN；负数、1e20、3 位小数都直接提交，后端 422 才拦',
)

check('F07-13', '来源数量只用 Number 判正（含 1e3 说明修正）', 'CONFIRMED',
  src.sampleSource.includes('!Number.isFinite(Number(draft.quantity)) || !(Number(draft.quantity) > 0)')
    && !src.sampleSource.includes('decimal_places'),
  '只判有限且 >0，不判整数位/3 位小数。修正：1e3 后端 Decimal 会当 1000 接受，不是必然 422；1.0001 才会 422',
)

check('F07-14', '新建打样不校验客户/商机必填', 'CONFIRMED',
  /if \(!form\.items\.length \|\| form\.items\.some\(\(item\) => !draftReady\(item\)\)\)/.test(src.sampleList)
    && /if not customer_id and not opportunity:/.test(readFileSync(resolve(ROOT, 'backend/app/modules/sample/router.py'), 'utf8'))
    && !/form\.opportunity_id[\s\S]{0,80}form\.customer_id[\s\S]{0,80}throw/.test(src.sampleList),
  '前端只校验产品名称；客户/商机为空时后端才报“必须指定商机或客户”',
)

check('F07-15', '目标保存无请求键', 'CONFIRMED',
  /const targetSaveMutation = useMutation\(\{[\s\S]*?mutationFn: \(\) =>[\s\S]*?upsertSalesTarget\(\{/.test(src.analytics)
    && !/request_key/.test(between(src.analytics, 'const targetSaveMutation', 'const targetYears')),
  '保存只带 expected_updated_at/唯一键，无 request_key；409 时无法确认上次是否已保存',
)

check('F07-16', 'OA 打样费用自由文本，无格式校验', 'CONFIRMED',
  /<Field label="打样费用（元）">[\s\S]{0,220}<Input[\s\S]{0,220}value=\{draft\.fields\['_sample_fee'\]\|\|''\}/.test(src.sampleModal)
    && !/_sample_fee[\s\S]{0,120}(test|Number|parse|error)/.test(src.sampleModal),
  'Input 直接写 fields._sample_fee，无负数/2 位小数/16 位前端校验，等提交后后端 Decimal 拦',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed, ${results.filter((r) => !r.ok).length} missing`)
