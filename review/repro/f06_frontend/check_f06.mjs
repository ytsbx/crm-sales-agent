/**
 * F06 OA 组件 + 商机/线索页静态证伪脚本。
 * 用法：node review/repro/f06_frontend/check_f06.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  oppList: 'frontend/src/modules/opportunity/OpportunityListPage.tsx',
  oppDetail: 'frontend/src/modules/opportunity/OpportunityDetailPage.tsx',
  oppBoard: 'frontend/src/modules/opportunity/OpportunityBoard.tsx',
  oppRecords: 'frontend/src/modules/opportunity/OpportunityRecords.tsx',
  oppApi: 'frontend/src/shared/api/opportunity.ts',
  inquiryApi: 'frontend/src/shared/api/inquiry.ts',
  lead: 'frontend/src/modules/lead/LeadListPage.tsx',
  leadApi: 'frontend/src/shared/api/lead.ts',
  resolveOa: 'frontend/src/shared/components/ResolveOaActions.tsx',
  autoSync: 'frontend/src/shared/components/AutoOaSync.tsx',
}
const src = Object.fromEntries(Object.entries(F).map(([k, rel]) => [k, readFileSync(resolve(ROOT, rel), 'utf8')]))
const results = []
const check = (id, name, verdict, condition, evidence) => results.push({ id, name, verdict, ok: Boolean(condition), evidence })
function between(text, start, end) {
  const i = text.indexOf(start)
  if (i < 0) return ''
  const j = text.indexOf(end, i + start.length)
  return j < 0 ? text.slice(i) : text.slice(i, j)
}

check('F06-01', '新建商机预计金额 Number 化', 'CONFIRMED',
  /expected_amount: \(\(\) => \{[\s\S]*?parseOptionalNumber\(form\.expected_amount\)/.test(src.oppList)
    && /expected_amount\?: number \| null/.test(src.oppApi)
    && Number('99999999999999.99') === 99999999999999.98,
  `Number('99999999999999.99') -> ${Number('99999999999999.99')}；API 类型是 number`,
)

check('F06-02', '需求明细数量 Number 化', 'CONFIRMED',
  /quantity: itemForm\.quantity \? Number\(itemForm\.quantity\) : 1/.test(src.oppDetail)
    && /quantity: number/.test(src.oppApi)
    && Number('9999999999999.999') === 9999999999999.998,
  `Number('9999999999999.999') -> ${Number('9999999999999.999')}`,
)

check('F06-03', '漏斗 Math.round 抹分，看板金额走 number', 'CONFIRMED',
  /Math\.round\(row\.amount\)\.toLocaleString/.test(src.oppList)
    && /const money = \(value\?: number \| null\) =>[\s\S]*?formatMoney\(toTwoDecimals\(value\)/.test(src.oppBoard)
    && !/amount_exact/.test(src.oppList + src.oppBoard),
  '漏斗取整到元；看板 money 用 toTwoDecimals(number)，无 exact',
)

check('F06-04', '新建商机无请求键', 'CONFIRMED',
  /createOpportunity\(\{/.test(src.oppList)
    && !/request_key/.test(between(src.oppList, 'const createMutation', 'onSuccess'))
    && !/request_key/.test(between(src.oppApi, 'export function createOpportunity', 'export function')),
  '前端 payload 无键，API 也不收；后端直接 Opportunity+commit',
)

check('F06-05', '添加需求明细无请求键', 'CONFIRMED',
  /createItem\(opportunityId, \{/.test(src.oppDetail)
    && !/request_key/.test(between(src.oppDetail, 'const itemMutation', 'onSuccess'))
    && !/request_key/.test(between(src.oppApi, 'export function createItem', 'export function')),
  '明细创建只传 sku/quantity/target_price，无键',
)

check('F06-06', '定制询价生成报价金额入参 number', 'CONFIRMED',
  /unit_cost: number/.test(between(src.inquiryApi, 'export function createQuoteFromInquiry', 'quoted_price: number'))
    && /quoted_price: number/.test(src.inquiryApi)
    && /quantity\?: number \| null/.test(src.inquiryApi),
  'createQuoteFromInquiry 的 unit_cost/quoted_price/quantity 都是 number',
)

check('F06-07', '明细/跟进/任务失败显示“还没有”', 'CONFIRMED',
  /dataSource=\{itemsQuery\.data \?\? \[\]\}/.test(src.oppDetail)
    && /empty=\{emptyLine\('还没有需求明细'/.test(src.oppDetail)
    && /dataSource=\{can\('followup:view'\) \? followupsQuery\.data\?\.items \?\? \[\] : \[\]\}/.test(src.oppDetail)
    && /dataSource=\{can\('task:view'\) \? tasksQuery\.data\?\.items \?\? \[\] : \[\]\}/.test(src.oppDetail)
    && !/itemsQuery\.isError/.test(src.oppDetail),
  '三个表都只看 data/isLoading，失败落空数组 + “还没有”',
)

check('F06-08', 'AI 结果不绑定 kind，切页签串段', 'CONFIRMED',
  /onSuccess: \(data\) => setAiEnvelope\(data\)/.test(src.oppDetail)
    && !/setAiResult\(\{ kind/.test(src.oppDetail)
    && /aiKind === 'recommend' && <AgentInsight envelope={aiEnvelope}/.test(src.oppDetail),
  '只存 envelope，不存 kind；渲染只判当前 aiKind，旧结果会显示到新页签',
)

check('F06-09', '看板阶段查询失败无错误态', 'CONFIRMED',
  /const stagesQuery = useQuery\(\{ queryKey: \['stages'\]/.test(src.oppBoard)
    && !/stagesQuery\.isError/.test(src.oppBoard)
    && /if \(statsQuery\.isError\)/.test(src.oppBoard)
    && /stages\.map\(/.test(src.oppBoard),
  '只处理 statsQuery 错误；stages 为空直接画空看板',
)

check('F06-10', 'OA 重发/作废每次新键、无 pending 禁用', 'CONFIRMED',
  /resolve\.mutate\(\{ action: 'resend', requestKey: newRequestKey\(\) \}\)/.test(src.resolveOa)
    && /resolve\.mutate\(\{ action: 'abandon', requestKey: newRequestKey\(\) \}\)/.test(src.resolveOa)
    && !/resolve\.isPending[\s\S]{0,80}disabled/.test(src.resolveOa),
  '每次点击生成新键，失败重试不是同一次核定；触发链接无 pending 禁用',
)

check('F06-11', '自动同步 POST 当 query，后台 10 秒轮询', 'CONFIRMED',
  /const sync = useQuery/.test(src.autoSync)
    && /syncOaInstance\(row!\.id\)/.test(src.autoSync)
    && /refetchIntervalInBackground: true/.test(src.autoSync)
    && /10000/.test(src.autoSync),
  '写/同步外部 OA 的 POST 放进 queryFn；后台标签页仍 10 秒请求',
)

check('F06-12', '线索新建/转化无请求键', 'CONFIRMED',
  /mutationFn: \(\) => createLead\(form\)/.test(src.lead)
    && /convertLead\(convertTarget!\.id, \{/.test(src.lead)
    && !/request_key/.test(src.leadApi),
  'createLead/convertLead API 都不收键；新建后端直接 add+commit',
)

check('F06-13', '分配/释放无原因，金额边界只判非负', 'CONFIRMED',
  /assignLead\(leadId, ownerId\)/.test(src.lead)
    && /releaseLead\(id\)/.test(src.lead)
    && !/reason/.test(between(src.lead, 'const assignMutation', 'const convertMutation'))
    && /Number\(opportunityAmount\) < 0/.test(src.lead),
  '单条分配/释放不传 reason；预计金额只判非负，无 16 位/2 位校验',
)

check('F06-14', '定制询价数量 Number 且无键', 'CONFIRMED',
  /quantity: inquiryForm\.quantity\.trim\(\) \? Number\(inquiryForm\.quantity\) : null/.test(src.oppRecords)
    && !/request_key/.test(between(src.inquiryApi, 'export function createCustomInquiry', 'export function updateCustomInquiry')),
  '数量 Number 化；createCustomInquiry 无键',
)

check('F06-15', '商机记录订单金额 toTwoDecimals(number)', 'CONFIRMED',
  /formatMoney\(toTwoDecimals\(row\.total_amount\), 'CNY'\)/.test(src.oppRecords)
    && !/total_amount_exact/.test(src.oppRecords),
  '订单金额出参已是 number，再 toTwoDecimals 二次舍入',
)

check('F06-16', '商机列表阶段/漏斗失败无错误态', 'CONFIRMED',
  /const stagesQuery = useQuery\(\{ queryKey: \['stages'\], queryFn: listStages \}\)/.test(src.oppList)
    && /const funnelQuery = useQuery\(\{ queryKey: \['funnel'\], queryFn: getFunnel \}\)/.test(src.oppList)
    && !/stagesQuery\.isError/.test(src.oppList)
    && !/funnelQuery\.isError/.test(src.oppList),
  '两个辅助查询都无 isError/重试，失败静默为空',
)

check('F06-17', '看板推进无 pending/并发闸门', 'CONFIRMED',
  /const move = async \(row: Opportunity, direction: 1 \| -1\) =>/.test(src.oppBoard)
    && /await changeStage\(row\.id, \{ stage_id: next\.id \}\)/.test(src.oppBoard)
    && !/moving/.test(src.oppBoard)
    && !/request_key/.test(between(src.oppApi, 'export function changeStage', 'export function')),
  '无卡片级 in-flight/pending，连点两次都发；第二次 409 后弹失败',
)

check('F06-18', '定制询价创建 API 无请求键', 'CONFIRMED',
  /export function createCustomInquiry\(payload: CustomInquiryPayload\)/.test(src.inquiryApi)
    && /api\.post<CustomInquiryRow>\('\/custom-inquiries', payload\)/.test(src.inquiryApi),
  '创建接口直接 POST，无 request_key',
)

check('F06-19', '金额 0 被显示成 -', 'CONFIRMED',
  /value=\{opportunity\.expected_amount \? formatMoney/.test(src.oppDetail)
    && /render: \(v: number \| null\) => \(v \? `¥\$\{v\}` : '-'\)/.test(src.oppDetail)
    && /const money = \(value\?: number \| null\) =>[\s\S]{0,60}value \? formatMoney/.test(src.oppBoard),
  '详情预计金额/明细目标价/看板统计都用 ?. 的 truthy 判断，0 元走 "-" 或"未填金额"',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed, ${results.filter((r) => !r.ok).length} missing`)
