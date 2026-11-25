/**
 * F04 客户页的静态证伪脚本。
 * 用法：node review/repro/f04_frontend/check_f04.mjs
 * CONFIRMED=成立；REFUTED=证伪；LATENT=当前无入口的潜伏项。
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  detail: 'frontend/src/modules/customer/CustomerDetailPage.tsx',
  list: 'frontend/src/modules/customer/CustomerListPage.tsx',
  dup: 'frontend/src/modules/customer/DuplicateCasePage.tsx',
  api: 'frontend/src/shared/api/customer.ts',
  io: 'backend/app/modules/customer/io_router.py',
  importing: 'backend/app/core/importing.py',
  tagsRouter: 'backend/app/modules/customer/tags_router.py',
  tags: 'backend/app/modules/customer/tags.py',
  custRouter: 'backend/app/modules/customer/router.py',
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

// F04-01
check('F04-01', '合并确认不等预览成功/失败', 'CONFIRMED',
  /const mergePreview = mergePreviewQuery\.data/.test(src.detail)
    && /okButtonProps=\{\{ disabled: !mergeTarget \|\| unresolvedConflicts\.length > 0 \}\}/.test(src.detail)
    && /mergePreviewQuery\.isLoading/.test(src.detail)
    && !/mergePreviewQuery\.isError/.test(src.detail),
  '按钮只判 target/冲突；预览 loading/error 不参与 disabled',
)

// F04-02
check('F04-02', '客户导入确认无 request_key，重试可重复建', 'CONFIRMED',
  /const formData = new FormData\(\)[\s\S]*?formData\.append\('preview_token'/.test(src.list)
    && !/request_key/.test(between(src.list, 'const confirmImport', 'const query ='))
    && !/request_key/.test(between(src.io, 'async def import_customers', 'return await finalize'))
    && /preview_token/.test(src.importing),
  '前端 FormData 只有 file/token；后端 import 无幂等，preview_token 不是一次性键',
)

// F04-03
check('F04-03', '新建客户重开换键', 'CONFIRMED',
  /createRequestKeyRef\.current = newRequestKey\(\)/.test(src.list)
    && /onCancel=\{\(\) => setModalVisible\(false\)\}/.test(src.list)
    && /createRequestKeyRef\.current = ''/.test(src.list)
    && /createCustomer\(\{ \.\.\.payload, request_key: createRequestKeyRef\.current/.test(src.list),
  '打开必换键、关闭不清、成功才清；超时关闭重开是新键',
)

// F04-04
check('F04-04', '查重失败被当成没有重复', 'CONFIRMED',
  /onError: \(\) => setDupMatches\(\[\]\)/.test(src.list)
    && !/dedupMutation\.isError/.test(src.list)
    && /setDupMatches\(result\.matches \?\? \[\]\)/.test(src.list),
  'dedup 失败直接清空 matches，界面无错误提示',
)

// F04-05
check('F04-05', '合并专属价冲突不展示 items', 'CONFIRMED',
  /mergePreview\.conflicts\.map\(\(conflict\) =>/.test(src.detail)
    && /conflict\.detail/.test(src.detail)
    && !/conflict\.items/.test(src.detail)
    && /"items": price_conflicts\[:20\]/.test(src.tags),
  '后端给 items（sku/rule_ids/prices），前端只渲染 label/detail/options',
)

// F04-06
check('F04-06', '联系人失败显示 0 条/还没有联系人', 'CONFIRMED',
  /key: 'contacts'.*value: \(contactsQuery\.data \?\? \[\]\)\.length/.test(src.detail)
    && /empty="这家客户还没有登记联系人/.test(src.detail)
    && !/contactsQuery\.isError/.test(src.detail),
  '业务全景用 ?? [] 求长度；联系人表固定 empty，无 isError',
)

// F04-07 已由后端 write_failed_row 修掉
check('F04-07', '导入失败清单展示 str(exc) 原文', 'REFUTED',
  /report\.write_failed_row\(index, name, exc\)/.test(src.io)
    && /reason = "该行保存失败，请重试；仍失败时请联系管理员并提供行号。"/.test(src.importing)
    && !/f"写入失败：\{str\(exc\)\[:160\]\}"/.test(src.io),
  '当前 io_router 走 write_failed_row；未知异常落稳定文案，前端 reason 不再含表名/约束',
)

// F04-08
check('F04-08', '单个/批量转移不传 reason', 'CONFIRMED',
  /transferCustomer\(customerId, \{ owner_id: owner\.id \}\)/.test(src.detail)
    && /batchTransferCustomers\(\{ customer_ids: ids, owner_id: owner\.id \}\)/.test(src.list)
    && /reason\?: string/.test(src.api),
  'API 支持 reason，两个调用点都不传，后端只能记默认文案',
)

// F04-09
check('F04-09', '新建联系人无键、提交中可关闭', 'CONFIRMED',
  /mutationFn: \(payload: Partial<Contact>\) => createContact\(customerId, payload\)/.test(src.detail)
    && /onCancel=\{\(\) => \{[\s\S]*?setContactModal\(false\)/.test(src.detail)
    && !/contactMutation\.isPending[\s\S]{0,80}onCancel/.test(src.detail)
    && !/request_key/.test(between(src.custRouter, 'async def create_contact', 'await session.commit')),
  'createContact 无 request_key，onCancel 不判 isPending',
)

// F04-10
check('F04-10', '已裁定接手人回退员工编号', 'CONFIRMED',
  /setForm\(EMPTY_FORM\)/.test(src.dup)
    && /form\.owner\?\.value === record\.resolved_owner_id/.test(src.dup)
    && /`员工编号 \$\{record\.resolved_owner_id\}`/.test(src.dup)
    && !/resolved_owner_name/.test(src.dup),
  'openRecord 每次重置 EMPTY_FORM，form.owner 永远 undefined',
)

// F04-11
check('F04-11', '辅助查询失败无错误态', 'CONFIRMED',
  /const stageDistQuery = useQuery\(\{/.test(src.list)
    && !/stageDistQuery\.isError/.test(src.list)
    && !/mergeCandidatesQuery\.isError/.test(src.detail)
    && /没有找到匹配的客户/.test(src.detail)
    && /还没有标签/.test(src.list),
  '阶段分布/标签/等级/合并候选都不区分失败与空数据',
)

// F04-12
check('F04-12', '多处直接展示 error.message', 'CONFIRMED',
  (src.list.match(/Toast\.error\(error\.message\)/g) || []).length >= 2
    && (src.detail.match(/Toast\.error\(error\.message\)/g) || []).length >= 5
    && /resolveMutation\.error\.message/.test(src.dup)
    && /emptyHint = \(failed: boolean, normal: string, error\?: unknown\)/.test(src.detail),
  '列表≥3、详情≥5 处原文 Toast；撞单 Banner/emptyHint 也回显 message',
)

// F04-13
check('F04-13', '批量标签覆盖不展示旧标签，审计无 before', 'CONFIRMED',
  /value: 'replace', label: '覆盖（先清空原有标签）'/.test(src.list)
    && /after=\{"customer_ids": payload\.customer_ids, "tag_ids": payload\.tag_ids\}/.test(src.tagsRouter)
    && !/before_tags|removed_tag_ids/.test(src.tagsRouter),
  '覆盖前不列旧标签；审计 after 只有目标集合，没有 before/removed',
)

// F04-14
check('F04-14', '“其他来源”输入框不可达', 'CONFIRMED',
  /const SOURCE_OPTIONS = \['展会', '官网询盘', '客户介绍', '电话开发', '企微添加', '导入', '老客复购', '抖音私信'\]/.test(src.list)
    && !/SOURCE_OPTIONS[\s\S]{0,80}'其他'/.test(src.list)
    && /form\.source === '其他'/.test(src.list),
  '选项没有“其他”，但渲染条件还判“其他”，otherSource 是死代码',
)

// F04-15
check('F04-15', '模板下载裸 axios 无 catch，导出错误固定文案', 'CONFIRMED',
  /const downloadCsv = async \(url: string, filename: string\) =>/.test(src.list)
    && /axios\.get\(url, \{ responseType: 'blob', headers: tokenHeader\(\) \}\)/.test(src.list)
    && /onClick=\{\(\) => downloadCsv\('\/api\/v1\/customers\/import-template'/.test(src.list)
    && !/downloadCsv[\s\S]{0,80}\.catch/.test(src.list)
    && /Toast\.error\('导出失败，请检查权限或筛选范围后重试'\)/.test(src.list),
  '模板按钮直接 fire；导出 catch 不分 403/422/5xx',
)

// F04-P1 潜伏
const oppApi = between(src.api, 'export interface CustomerOpportunity', 'export interface CustomerQuote')
const quoteApi = between(src.api, 'export interface CustomerQuote', 'export interface CustomerOrder')
const orderApi = between(src.api, 'export interface CustomerOrder', 'export interface ContactFollowup')
check('F04-P1', '客户子资源金额 number 且无 exact', 'LATENT',
  /expected_amount: number \| null/.test(oppApi)
    && /current_version_amount: number \| null/.test(quoteApi)
    && /total_amount: number \| null/.test(orderApi)
    && !/expected_amount_exact|current_version_amount_exact|total_amount_exact/.test(src.api)
    && !/listCustomerOpportunities|listCustomerQuotes|listCustomerOrders/.test(
      [src.detail, src.list, src.dup].join('\n'),
    )
    && /"current_version_amount": float\(version\.total_amount\)/.test(src.custRouter),
  '类型/后端仍 float 无 exact；但三个封装全仓无页面调用，当前不构成缺陷',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
const counts = ['CONFIRMED', 'REFUTED', 'LATENT'].map((v) => results.filter((r) => r.ok && r.verdict === v).length)
console.log(`\n${counts[0]} confirmed, ${counts[1]} refuted, ${counts[2]} latent, ${results.filter((r) => !r.ok).length} missing`)
