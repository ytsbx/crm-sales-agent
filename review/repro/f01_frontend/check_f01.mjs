/**
 * F01 报价页的静态/数值证伪脚本。
 *
 * 用法：
 *   node review/repro/f01_frontend/check_f01.mjs
 *
 * 输出 CONFIRMED = 该条缺陷在代码里仍然成立；MISSING = 断言没命中，需要人工复核
 * （可能是审查员写错、代码已修、或本脚本定位过时）。
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  detail: 'frontend/src/modules/quote/QuoteDetailPage.tsx',
  list: 'frontend/src/modules/quote/QuoteListPage.tsx',
  logs: 'frontend/src/modules/quote/QuoteSendLogsPanel.tsx',
  whatif: 'frontend/src/modules/quote/WhatIfPanel.tsx',
  channel: 'frontend/src/modules/quote/sendChannel.ts',
  api: 'frontend/src/shared/api/quote.ts',
  quoteSvc: 'backend/app/modules/quote/service.py',
  orderSvc: 'backend/app/modules/order/service.py',
  orderModel: 'backend/app/modules/order/model.py',
}

const src = Object.fromEntries(
  Object.entries(F).map(([key, rel]) => [key, readFileSync(resolve(ROOT, rel), 'utf8')]),
)

const results = []
function check(id, name, condition, evidence) {
  results.push({ id, name, ok: Boolean(condition), evidence })
}

/** 截取两个标记之间的代码，找不到标记就返回空串。 */
function between(text, start, end) {
  const i = text.indexOf(start)
  if (i < 0) return ''
  const j = text.indexOf(end, i + start.length)
  return j < 0 ? text.slice(i) : text.slice(i, j)
}

// ---------- F01-01 ----------
const convertBody = between(src.orderSvc, 'async def create_order_from_quote', '\nasync def ')
const versionButton = between(src.detail, "const conditions: Record<string, boolean> = {", "const actions:")
check(
  'F01-01',
  '已转订单后仍可新建版本，转单只按 quote_version_id 查重',
  /SalesOrder\.quote_version_id == version\.id/.test(convertBody)
    && !/SalesOrder\.quote_id ==/.test(convertBody)
    && /'新建版本': Boolean\(canManage && !rejection\)/.test(versionButton),
  'order/service.py 只查 quote_version_id；QuoteDetailPage 的“新建版本”只判 canManage/!rejection',
)

// ---------- F01-02 ----------
const versionResolve = between(src.detail, 'const requestedVersionId', 'const detailQuery')
check(
  'F01-02',
  'URL 的 version 不校验是否属于当前 quoteId',
  /const versionId = requestedVersionId \?\? allVersionIds\[0\]/.test(versionResolve)
    && !/allVersionIds\.includes/.test(versionResolve),
  versionResolve.trim().split('\n').slice(0, 4).join(' | '),
)

// ---------- F01-03 ----------
const parseCharge = between(src.detail, 'function parseChargeAmount', '/**\n * 批量录入')
const chargeTable = between(src.detail, "title: '金额'", "title: '操作'")
const chargeSummary = between(src.detail, '费用合计', 'SectionCard')
const simulated = Number('99999999999999.99')
check(
  'F01-03',
  '费用金额走 JS number，exact 被 Number() 化',
  /const amount = Number\(raw\)/.test(parseCharge)
    && /return amount/.test(parseCharge)
    && /Number\(charge\.amount \?\? 0\)/.test(chargeSummary)
    && /const n = Number\(v\)/.test(src.detail)
    && simulated === 99999999999999.98,
  `Number('99999999999999.99') -> ${simulated}；费用合计仍 reduce(Number(charge.amount))`,
)

// ---------- F01-04 ----------
check(
  'F01-04',
  '发送记录查询失败被当成空态',
  /rows\.length === 0/.test(src.logs)
    && !/queries\.isError/.test(src.logs)
    && /Promise\.all\(versionIds\.map/.test(src.logs),
  'QuoteSendLogsPanel 只判 isLoading / rows.length，无 isError 分支',
)

// ---------- F01-05 ----------
const deleteCharge = between(src.detail, 'onConfirm={() => deleteQuoteCharge(record.id)', '</Popconfirm>')
check(
  'F01-05',
  '删除费用失败无提示、不刷新',
  /deleteQuoteCharge\(record\.id\)\.then\(refresh\)/.test(deleteCharge)
    && !/\.catch\(/.test(deleteCharge),
  'Popconfirm onConfirm 是 fire-and-forget，同页其他 mutation 都有 onError',
)

// ---------- F01-06 ----------
const whatIfMount = between(src.detail, '<WhatIfPanel', '/>')
check(
  'F01-06',
  'What-if 方案状态跨版本不重置',
  /useState<number \| null>\(null\)/.test(src.whatif)
    && /useState<Array<number \| null>>\(\[null, null, null\]\)/.test(src.whatif)
    && !/useEffect\(.+setScenarios/.test(src.whatif)
    && !/key=/.test(whatIfMount),
  'WhatIfPanel 的 targetPrice/scenarios 是内部 state，挂载处没有 key',
)

// ---------- F01-07 ----------
const openCreate = src.list
check(
  'F01-07',
  '关闭重开新建报价会换幂等键',
  /createRequestKeyRef\.current = newRequestKey\(\)/.test(openCreate)
    && /onCancel=\{\(\) => setCreateVisible\(false\)\}/.test(src.list)
    && /createRequestKeyRef\.current = ''/.test(src.list),
  '每次打开弹窗生成新键，onCancel 只关闭不清/不保留旧键，成功后才清空',
)

// ---------- F01-08 ----------
check(
  'F01-08',
  '新建版本接口没有 request_key',
  /export function createQuoteVersion\(quoteId: number, confirm = false\)/.test(src.api)
    && !/createQuoteVersion[\s\S]{0,120}request_key/.test(src.api)
    && !/request_key/.test(between(src.detail, 'const versionMutation', 'const submitMutation')),
  'createQuoteVersion 只有 confirm 参数；mutation 没有幂等键',
)

// ---------- F01-09 ----------
const freightFlag = between(src.detail, 'const canEditFreightOnly', 'const canRespond')
check(
  'F01-09',
  '客户已接受/已转单仍暴露改运费入口',
  /approval_status === 'approved' \|\| version\.sent_at/.test(freightFlag)
    && !/accepted_at/.test(freightFlag)
    && !/declined_at/.test(freightFlag)
    && !/quote\.status === 'converted'/.test(freightFlag),
  'canEditFreightOnly 不看 accepted_at / declined_at / converted',
)

// ---------- F01-10 ----------
check(
  'F01-10',
  '改价等输入校验不完整',
  /if \(!newPrice\.trim\(\) \|\| Number\(newPrice\) <= 0\)/.test(src.detail)
    && !/Number\.isFinite\(Number\(newPrice\)\)/.test(src.detail)
    && /\\d\{1,2\}/.test(parseCharge)
    && Number('1e309') === Infinity,
  "Number('1e309') = Infinity，`Infinity <= 0` 为 false，前端放行",
)

// ---------- F01-11 ----------
check(
  'F01-11',
  '自定义发送渠道记不住',
  /if \(saved && \(SEND_CHANNELS as readonly string\[\]\)\.includes\(saved\)\) return saved/.test(src.channel)
    && /window\.localStorage\.setItem\(key, channel\)/.test(src.channel),
  '读取只接受白名单，写入接受任意渠道',
)

// ---------- F01-12 ----------
check(
  'F01-12',
  '发送记录按版本逐个请求',
  /Promise\.all\(versionIds\.map\(\(id\) => getSendLogs\(id\)\)\)/.test(src.logs),
  '一个版本一个 GET，没有按报价聚合或缓存合并',
)

// ---------- F01-13 ----------
check(
  'F01-13',
  'What-if 全用 JS number 估算',
  /const amountTotal = basePrice \* estimateQuantity/.test(src.whatif)
    && /const profitTotal = unitProfit === null \? null : unitProfit \* estimateQuantity/.test(src.whatif)
    && /const quantity = items\.reduce\(\(sum, item\) => sum \+ item\.quantity, 0\)/.test(src.whatif),
  '试算金额/利润/数量都是 number，没有定点/十进制',
)

// ---------- F01-14 ----------
const emptyState = between(src.detail, 'if (!detail || !version || !quote)', '返回报价中心')
check(
  'F01-14',
  '详情加载失败统一伪装成“不存在或无权查看”',
  /报价单不存在或无权查看/.test(emptyState)
    && !/detailQuery\.isError/.test(emptyState)
    && !/retry/.test(emptyState),
  'early return 只看 !detail，不看 isError；没有重试入口',
)

for (const r of results) {
  console.log(`${r.ok ? 'CONFIRMED' : 'MISSING  '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
const missing = results.filter((r) => !r.ok)
console.log(`\n${results.length - missing.length}/${results.length} confirmed`)
if (missing.length) process.exitCode = 1
