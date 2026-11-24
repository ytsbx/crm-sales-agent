/**
 * F12 产品 / 物流 / 通用件复核脚本。
 * 运行：node review/repro/f12_frontend/check_f12.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  productDetail: 'frontend/src/modules/product/ProductDetailPage.tsx',
  productList: 'frontend/src/modules/product/ProductListPage.tsx',
  productApi: 'frontend/src/shared/api/product.ts',
  productSchema: 'backend/app/modules/product/schema.py',
  fieldRules: 'backend/app/modules/product/field_rules.py',
  logisticsPage: 'frontend/src/modules/logistics/LogisticsPage.tsx',
  logistics: 'backend/app/modules/pricing/logistics.py',
  logisticsRouter: 'backend/app/modules/pricing/logistics_router.py',
  csvButtons: 'frontend/src/shared/components/CsvImportButtons.tsx',
  skuImage: 'frontend/src/modules/product/SkuImageCell.tsx',
  guarded: 'frontend/src/shared/components/GuardedButton.tsx',
  confirm: 'frontend/src/shared/components/ConfirmRequiredModal.tsx',
  quoteTodo: 'frontend/src/shared/quote-todo.ts',
  fileApi: 'frontend/src/shared/api/file.ts',
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

check('F12-01', 'SKU 物理字段非数字被静默清空', 'CONFIRMED',
  has(src.productDetail, 'const toNumber = (value: string): number | null => {')
    && has(src.productDetail, 'return Number.isFinite(parsed) ? parsed : null')
    && has(src.productDetail, 'length: toNumber(skuForm.length),')
    && has(src.productDetail, 'weight: toNumber(skuForm.weight),')
    && has(src.productDetail, 'carton_volume: toNumber(skuForm.carton_volume),')
    && has(src.productSchema, 'class SkuUpdate(PatchModel)')
    && has(src.productSchema, 'length: Decimal | None = _num_field("length")'),
  'toNumber("12cm") 返回 null；编辑 payload 把 null 发 PATCH；SkuUpdate 允许 null = 清空',
)

check('F12-02', 'SKU 0 单重/尺寸进入运费', 'CONFIRMED',
  has(src.fieldRules, 'minimum: Decimal = Decimal(0)')
    && has(src.logistics, 'unit_weight = weight_override if weight_override is not None else sku.weight')
    && has(src.logistics, 'if unit_weight is None:')
    && has(src.logistics, 'unit_weight = ZERO')
    && has(src.logistics, 'if weight_override is not None and weight_override <= 0:')
    && has(src.logistics, '单件重量必须大于 0'),
  'SKU 0 合法落库；计算只在 None 时告警，0 当有效单重；手工 override <=0 却拒绝。同一“0 单重”两条口径',
)

check('F12-03', '新建产品无请求键', 'CONFIRMED',
  has(src.productList, 'mutationFn: (payload: ProductPayload) => createProduct(payload)')
    && has(src.productApi, 'export function createProduct(payload: ProductPayload)')
    && !/request_key/.test(between(src.productSchema, 'class ProductCreate', 'class ProductUpdate')),
  'ProductCreate/前端 createProduct 都没有请求键；响应丢失重试多建产品',
)

check('F12-04', '产品详情失败显示“产品不存在/没有 SKU”', 'CONFIRMED',
  has(src.productDetail, 'const product = productQuery.data')
    && has(src.productDetail, 'if (!product) {')
    && has(src.productDetail, '产品不存在或已下线')
    && has(src.productDetail, 'dataSource={skuQuery.data ?? []}')
    && has(src.productDetail, 'empty="还没有 SKU，先加一个"')
    && !/productQuery\.isError/.test(src.productDetail)
    && !/skuQuery\.isError/.test(src.productDetail),
  'productQuery/skuQuery 都没有 isError；失败 data 空，直接落“产品不存在”/“还没有 SKU”',
)

check('F12-05', '产品/SKU 编辑无版本冲突', 'CONFIRMED',
  has(src.productSchema, 'class ProductUpdate(PatchModel)')
    && has(src.productSchema, 'class SkuUpdate(PatchModel)')
    && !/expected_(updated_at|version)/.test(between(src.productSchema, 'class ProductUpdate', 'class SkuCreate'))
    && !/expected_(updated_at|version)/.test(between(src.productSchema, 'class SkuUpdate', 'class SkuStandaloneCreate'))
    && has(src.productDetail, 'updateMutation.mutate(productForm)')
    && has(src.productDetail, 'await updateSku(editingSku.id, payload)'),
  '两个 Update 都无 expected；前端提交整份 productForm/SKU payload，后保存覆盖先保存',
)

check('F12-06', '新建 SKU 无请求键', 'CONFIRMED',
  has(src.productDetail, 'const created = await createSku(productId, payload)')
    && has(src.productSchema, 'class SkuCreate(CreateModel)')
    && !/request_key/.test(between(src.productSchema, 'class SkuCreate', 'class SkuUpdate')),
  'SkuCreate/前端 createSku 都没有请求键；编码留空自动生成，重试多建一条',
)

check('F12-07', '物流保存方案无请求键', 'CONFIRMED',
  has(src.logisticsPage, 'save: true,')
    && has(src.logisticsPage, 'expected_amount: option.amount')
    && has(src.logisticsRouter, 'payload.expected_amount is not None')
    && !/request_key/.test(src.logisticsRouter)
    && !/request_key/.test(between(src.logistics, 'async def save_quote', 'async def ')),
  '保存只靠 expected_amount 防费率变化，无请求键/唯一约束；重试或双击落两条',
)

check('F12-08', '物流金额/数量 number', 'CONFIRMED',
  has(src.logisticsPage, 'function money(value?: number | null)')
    && has(src.logisticsPage, 'value.toFixed(2)')
    && has(src.logisticsPage, 'expected_amount: option.amount')
    && has(src.logistics, '"chargeable_weight": _f(quote.chargeable_weight)')
    && String(Number('99999999999999.99')) !== '99999999999999.99',
  `后端 _f 转 float；前端 money toFixed(2)、expected_amount number；99999999999999.99 -> ${Number('99999999999999.99')}`,
)

check('F12-09', 'CSV 模板/导出下载失败无提示', 'CONFIRMED',
  has(src.productList, 'const downloadCsv = async (url: string, filename: string) => {')
    && has(src.productList, 'axios.get(url, { responseType: \'blob\'')
    && !/downloadCsv[\s\S]{0,400}?catch/.test(src.productList)
    && has(src.csvButtons, 'const downloadCsv = async (url: string, filename: string) => {')
    && !/downloadCsv[\s\S]{0,400}?catch/.test(src.csvButtons),
  '两处 downloadCsv 都没有 try/catch；401/500/断网 Promise reject，页面无 Toast',
)

check('F12-10', 'SKU 图片查询/预览失败静默', 'CONFIRMED',
  has(src.productDetail, 'const skuImagesQuery = useQuery')
    && !/skuImagesQuery\.isError/.test(src.productDetail)
    && has(src.skuImage, '} catch {')
    && has(src.skuImage, 'setFailed(true)'),
  '批量查询无 isError，data 空显示“无图”；单图失败只 setFailed 显示 !',
)

check('F12-11', 'GuardedButton 禁用态键盘不可达', 'CONFIRMED',
  has(src.guarded, '<span')
    && has(src.guarded, 'className="blocked-hint-click"')
    && has(src.guarded, 'onClick={() => {')
    && !/role="button"/.test(src.guarded)
    && !/tabIndex/.test(src.guarded)
    && !/onKeyDown/.test(src.guarded),
  '外层 span 无 role/tabIndex/onKeyDown；内层 Button disabled 不可聚焦，键盘用户无法触发提示',
)

check('F12-12', 'useConfirmGate 不自动补 confirm（潜伏）', 'CONFIRMED',
  has(src.confirm, 'export function useConfirmGate()')
    && has(src.confirm, 'async function proceed()')
    && has(src.confirm, 'return action()')
    && !/action\(true\)/.test(src.confirm)
    && !/request_key/.test(src.confirm),
  'proceed 重发原 action，不补 confirm；文档示例 someApi({...}) 也没参数。当前无生产调用，属共用件潜伏问题',
)

check('F12-13', 'splitAmountsLikeBackend 仍返回 number（潜伏）', 'CONFIRMED',
  has(src.quoteTodo, 'export function splitAmountsLikeBackend(total: number, ratios: readonly number[]): number[] | null')
    && has(src.quoteTodo, '.map(Number)')
    && /splitAmountsLikeBackend/.test(readFileSync(resolve(ROOT, 'ops/quote_todo.test.mjs'), 'utf8')),
  'exact 字符串再 map(Number)；当前只被 ops 测试调用，生产未检索到，属未来误用风险',
)

check('F12-14', '漏项：SKU 图片上传无幂等，响应丢失补传出重复图', 'CONFIRMED',
  has(src.productDetail, 'for (const file of pendingImages)')
    && has(src.productDetail, 'await uploadFile(file, { businessType: \'sku\', businessId: skuId })')
    && has(src.productDetail, 'failed.push(file.name)')
    && !/request_key/.test(between(src.fileApi, 'export function uploadFile', 'export function '))
    && !/request_key/.test(src.fileApi),
  '上传成功但响应丢失会落 failed；用户按提示补传会再挂一张，图片列表出现重复',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed, ${results.filter((r) => !r.ok).length} missing`)
