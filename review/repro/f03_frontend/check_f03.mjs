/**
 * F03 价格中心的静态/数值证伪脚本。
 * 用法：node review/repro/f03_frontend/check_f03.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  center: 'frontend/src/modules/pricing/PriceCenterPage.tsx',
  pricing: 'frontend/src/modules/pricing/PricingPage.tsx',
  api: 'frontend/src/shared/api/pricing.ts',
  money: 'frontend/src/shared/components/money.ts',
  numberInput: 'frontend/src/shared/number-input.ts',
  bizdoc: 'frontend/src/shared/api/bizdoc.ts',
  bizdocPanel: 'frontend/src/shared/components/BizDocPanel.tsx',
  service: 'backend/app/modules/pricing/service.py',
  router: 'backend/app/modules/pricing/router.py',
  model: 'backend/app/modules/pricing/model.py',
  schema: 'backend/app/modules/pricing/schema.py',
}
const src = Object.fromEntries(Object.entries(F).map(([k, rel]) => [k, readFileSync(resolve(ROOT, rel), 'utf8')]))
const results = []
function check(id, name, verdict, condition, evidence) {
  results.push({ id, name, verdict, ok: Boolean(condition), evidence })
}
function between(text, start, end) {
  const i = text.indexOf(start)
  if (i < 0) return ''
  const j = text.indexOf(end, i + start.length)
  return j < 0 ? text.slice(i) : text.slice(i, j)
}

// F03-01
check('F03-01', '价格表单用 parseOptionalNumber 转 number', 'CONFIRMED',
  /purchase_cost: parseOptionalNumber\(costForm\.purchase_cost\)/.test(src.center)
    && /standard_price: parseOptionalNumber\(ruleForm\.standard_price\)/.test(src.center)
    && /agreed_price: parseOptionalNumber\(customerPriceForm\.agreed_price\)/.test(src.center)
    && /unit_price_per_kg: requiredNumberOrThrow\(rateForm\.unit_price_per_kg/.test(src.center)
    && /export function parseOptionalNumber/.test(src.numberInput)
    && Number('999999999999.9997') === 999999999999.9998
    && Number('9999999999999.999') === 9999999999999.998
    && !/parseOptionalDecimal/.test(src.center),
  `Number('999999999999.9997') -> ${Number('999999999999.9997')}；PriceCenter 不 import parseOptionalDecimal`,
)

// F03-02
check('F03-02', '编辑从 _f float 回填，原样保存会改价', 'CONFIRMED',
  /def _f\(value: Decimal \| None\) -> float \| None/.test(src.service)
  && /"agreed_price": _f\(rule\.agreed_price\)/.test(src.service)
  && /"unit_price_per_kg": _f\(rate\.unit_price_per_kg\)/.test(src.service)
  && /agreed_price: String\(row\.agreed_price \?\? ''\)/.test(src.center)
  && /unit_price_per_kg: String\(row\.unit_price_per_kg \?\? ''\)/.test(src.center),
  'serializer 转 float，编辑 String(row.agreed_price) 回填，保存再 parseOptionalNumber',
)

// F03-03
check('F03-03', '查价数量走 number，加入商机写 number', 'CONFIRMED',
  /const lookupQuantity = parseQuantity\(lookupQty\)/.test(src.center)
    && /quantity: lookupQuantity/.test(src.center)
    && /quantity: lookupResult\.quantity/.test(src.center)
    && /target_price: lookupResult\.unit_price/.test(src.center)
    && /export function parseQuantity/.test(src.numberInput),
  'checkQuantity 返回 number；createItem 用 lookupResult.quantity/unit_price',
)

// F03-04
check('F03-04', '成本表强制两位小数，4 位成本被截', 'CONFIRMED',
  /const money = \(value\?: number \| null\) =>/.test(src.center)
    && /formatMoney\(toTwoDecimals\(value\)/.test(src.center)
    && /dataIndex: 'purchase_cost'/.test(src.center)
    && /dataIndex: 'total_cost'/.test(src.center)
    && /if \(typeof value === 'number'\)/.test(src.money)
    && /return value\.toFixed\(2\)/.test(src.money),
  '成本列全走 money→toTwoDecimals→toFixed(2)，成本列是 Numeric(14,4)',
)

// F03-05
check('F03-05', '新增成本无 request_key，后端无唯一约束', 'CONFIRMED',
  /mutationFn: \(\) => \{[\s\S]*?createCost\(costSkuId!/.test(between(src.center, 'const costMutation', 'onSuccess'))
    && !/request_key/.test(between(src.center, 'const costMutation', 'const expireMutation'))
    && !/idempotency/.test(between(src.router, 'async def create_cost', 'await session.commit()'))
    && !/UniqueConstraint/.test(between(src.model, 'class ProductCost', 'class ')),
  'create_cost 直接 session.add；ProductCost 只有普通索引 (sku_id, effective_from)',
)

// F03-06
check('F03-06', '停用/恢复/删除客户价无 catch', 'CONFIRMED',
  /disablePriceRule\(record\.id\)\.then\(refreshAll\)/.test(src.center)
    && /restorePriceRule\(record\.id\)\.then\(refreshAll\)/.test(src.center)
    && /deleteCustomerPriceRule\(record\.id\)\.then\(refreshAll\)/.test(src.center)
    && !/disablePriceRule\(record\.id\)\.then\(refreshAll\)\.catch/.test(src.center),
  '三处都是 .then(refreshAll) 的 fire-and-forget',
)

// F03-07
check('F03-07', '价格表单缺 0..1/负数/位数校验', 'CONFIRMED',
  /const margin = parseOptionalNumber\(permissionForm\.minimum_margin\)/.test(src.center)
    && /if \(typeof margin === 'number' && Number\.isNaN\(margin\)\)/.test(src.center)
    && !/margin > 1/.test(between(src.center, 'const margin', 'return savePricePermission'))
    && /minimum_margin: Decimal.*ge=0, le=1/.test(src.schema)
    && /只能填数字（最多 2 位小数）/.test(src.center),
  '只判 NaN；利润率 0..1、16 位/4 位靠后端；成本提示写 2 位但后端 4 位',
)

// F03-08
check('F03-08', '列表/查价失败显示空表或原始 error', 'CONFIRMED',
  /dataSource=\{rulesQuery\.data\?\.items \?\? \[\]\}/.test(src.center)
    && !/rulesQuery\.isError/.test(src.center)
    && /还没有价格规则/.test(src.center)
    && /查价失败。\{lookupMutation\.error\.message\}/.test(src.center),
  '规则列表无 isError 分支，失败时 dataSource 空；查价直接展示 error.message',
)

// F03-09
check('F03-09', 'toTwoDecimals number 四舍五入/字符串截断，单价截第5位', 'CONFIRMED',
  /return value\.toFixed\(2\)/.test(src.money)
    && /return `\$\{whole\}\.\$\{fraction\.slice\(0, 2\)\}`/.test(src.money)
    && /const trimmed = fraction\.slice\(0, 4\)/.test(src.money)
    && Number((2.346).toFixed(2)) === 2.35,
  `number 2.346→2.35，string '2.346'→2.34；formatUnitPrice slice(0,4) 是截断`,
)

// F03-10
const pricingResult = between(src.api, 'export interface PricingResult', 'export interface PricePermissionVerdict')
const verdict = between(src.api, 'export interface PricePermissionVerdict', 'export interface PricingScenario')
check('F03-10', '核价结果/权限 verdict/深链价格走 float', 'CONFIRMED',
  /const paramNumber = \(key: string\) =>/.test(src.pricing)
    && /Number\.isFinite\(Number\(raw\)\) \? Number\(raw\)/.test(src.pricing)
    && /报 \{verdict\.quoted_price/.test(src.pricing)
    && !/quoted_price_exact/.test(pricingResult)
    && !/quoted_price_exact/.test(verdict),
  '深链 Number 回填；verdict 直接输出 quoted_price；PricingResult/verdict 均无 exact',
)

// F03-P1
check('F03-P1', 'bizdoc 生成键可选导致重复出文档', 'REFUTED',
  /requestKey\?: string/.test(src.bizdoc)
    && /const requestKeyRef = useRef<string \| null>\(null\)/.test(src.bizdocPanel)
    && /if \(!requestKeyRef\.current\) requestKeyRef\.current = newRequestKey\(\)/.test(src.bizdocPanel)
    && /generateQuoteDoc\(quoteVersionId as number, undefined, key\)/.test(src.bizdocPanel),
  '全仓唯一调用点 BizDocPanel 每次都传稳定 key；可选参数只是 API 设计，不是现网缺陷',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
const confirmed = results.filter((r) => r.verdict === 'CONFIRMED' && r.ok).length
const refuted = results.filter((r) => r.verdict === 'REFUTED' && r.ok).length
console.log(`\n${confirmed} confirmed, ${refuted} refuted, ${results.filter((r) => !r.ok).length} missing`)
