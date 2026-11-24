/**
 * F02 订单页的静态/数值证伪脚本。
 *
 * 用法：node review/repro/f02_frontend/check_f02.mjs
 * CONFIRMED = 缺陷成立；REFUTED = 证伪；MISSING = 脚本定位失效，需人工看。
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  detail: 'frontend/src/modules/order/OrderDetailPage.tsx',
  list: 'frontend/src/modules/order/OrderListPage.tsx',
  alloc: 'frontend/src/modules/order/AllocationAdjustModal.tsx',
  drafts: 'frontend/src/modules/order/OrderDraftsPage.tsx',
  api: 'frontend/src/shared/api/order.ts',
  orderSvc: 'backend/app/modules/order/service.py',
  orderRouter: 'backend/app/modules/order/router.py',
  orderSchema: 'backend/app/modules/order/schema.py',
  payRouter: 'backend/app/modules/payment/router.py',
  paySvc: 'backend/app/modules/payment/service.py',
}

const src = Object.fromEntries(
  Object.entries(F).map(([k, rel]) => [k, readFileSync(resolve(ROOT, rel), 'utf8')]),
)
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

// F02-01 回款键重开即换
const paymentOpen = between(src.detail, '登记回款', 'setPaymentForm({')
check('F02-01', '回款登记关闭重开换幂等键', 'CONFIRMED',
  /paymentKeyRef\.current = newRequestKey\(\)/.test(src.detail)
    && /onCancel=\{\(\) => setPaymentTarget\(null\)\}/.test(src.detail)
    && /paymentKeyRef\.current = ''/.test(src.detail),
  '打开时 newRequestKey，onCancel 只清 target，成功才清键',
)

// F02-02 手工建单 Number 化
check('F02-02', '手工建单数量/单价先 Number() 丢精度', 'CONFIRMED',
  /quantity: Number\(item\.quantity\)/.test(src.list)
    && /unit_price: Number\(item\.unit_price\)/.test(src.list)
    && /max_digits=16, decimal_places=3/.test(src.orderSchema)
    && /max_digits=16, decimal_places=4/.test(src.orderSchema)
    && Number('9999999999999.999') === 9999999999999.998,
  `Number('9999999999999.999') -> ${Number('9999999999999.999')}`,
)

// F02-03 手工建单无请求键
const createApi = between(src.api, 'export function createOrder', 'export function')
check('F02-03', '手工建单前端不传 request_key', 'CONFIRMED',
  !/request_key/.test(createApi)
    && /request_key: str \| None/.test(src.orderSchema)
    && /idempotency\.request_key_from\(request, payload\.request_key\)/.test(src.orderRouter),
  '后端支持可选 request_key，但前端 API/调用都不传，重试按新请求处理',
)

// F02-04 shipped/delivered 无闸门
check('F02-04', '人工履约可直接选已发货/已签收，绕过批次', 'CONFIRMED',
  /value: 'shipped'/.test(src.detail)
    && /value: 'delivered'/.test(src.detail)
    && /if new_status == "completed":/.test(src.orderSvc)
    && !/new_status in \("shipped", "delivered"\)/.test(src.orderSvc),
  'change_status 只在 completed 时查 all_shipped；shipped/delivered 直接落',
)

// F02-05 取消后仍可删应收
const deleteUx = between(src.detail, 'const canDelete =', 'if (!canRegister && !canDelete)')
check('F02-05', '订单取消后仍可删除应收节点', 'CONFIRMED',
  /const canDelete = canPayment && !hasPayment/.test(deleteUx)
    && !/planCancelled/.test(deleteUx)
    && /async def delete_receivable/.test(src.payRouter)
    && !/plan\.status.*cancelled/.test(between(src.payRouter, 'async def delete_receivable', 'await session.delete')),
  'canDelete 不看 planCancelled/orderCancelled；后端删除只查回款/分配',
)

// F02-06 生成应收键每次新建
check('F02-06', '生成应收每次提交新键，绕过同键回放', 'CONFIRMED',
  /request_key: newRequestKey\(\)/.test(src.detail),
  '键写在 mutationFn 内，重试必换；后端已有“已有计划”闸门，故后果是误报失败而非重复落库',
)

// F02-07 发货批次键重开即换
check('F02-07', '排发货批次关闭重开换键，可重复排计划', 'CONFIRMED',
  /shipmentKeyRef\.current = newRequestKey\(\)/.test(src.detail)
    && /onCancel=\{\(\) => setCreateShipmentVisible\(false\)\}/.test(src.detail)
    && /shipmentKeyRef\.current = ''/.test(src.detail),
  '打开时换键、关闭不清、成功才清；部分批次可再次通过未计划量校验',
)

// F02-08 completed 回退固定理由
check('F02-08', '已完成回退理由固定占位，架空必填闸门', 'CONFIRMED',
  /changeOrderStatus\(orderId, targetStatus!, '在订单详情页更新'\)/.test(src.detail)
    && /if order\.status == "completed" and new_status != "completed"/.test(src.orderSvc)
    && /if not \(remark or ""\)\.strip\(\)/.test(src.orderSvc),
  '前端固定文案永远非空，后端只判 strip 非空',
)

// F02-09 回款金额只判 finite/>0
check('F02-09', '回款金额无 2 位小数/16 位上限', 'CONFIRMED',
  /Number\.isFinite\(Number\(paymentForm\.amount\)\)/.test(src.detail)
    && /Number\(paymentForm\.amount\) <= 0/.test(src.detail)
    && !/\\d\{1,2\}/.test(between(src.detail, 'paymentForm.amount', 'paymentMutation.mutate')),
  '前端只判 finite/&gt;0，小数位与 max_digits 推给后端 422',
)

// F02-10 错误态
check('F02-10', '详情 500/超时伪装成“不存在或无权查看”', 'CONFIRMED',
  /if \(!order\)/.test(src.detail)
    && /订单不存在或无权查看/.test(src.detail)
    && !/orderQuery\.isError/.test(src.detail)
    && /Toast\.error\(error\.message\)/.test(src.detail),
  'early return 只看 !order；多处直接回显 error.message',
)

// F02-11 固定审计说明
check('F02-11', '确认/驳回/状态变更用固定审计文案', 'CONFIRMED',
  /confirmPayment\(id, '财务核对通过'\)/.test(src.detail)
    && /confirmPayment\(id, '财务核对通过'\)/.test(src.list)
    && /rejectPayment\(id, '金额或凭证有误'\)/.test(src.list)
    && /'在订单详情页更新'/.test(src.detail),
  '钱操作原因不可区分，状态变更无真实理由',
)

// F02-12 草稿无键
check('F02-12', '订单草稿保存/确认无 request_key', 'CONFIRMED',
  /updateOrderDraft\(draftId, \{ revision/.test(src.drafts)
    && /confirmOrderDraft\(id, revision, versionId\)/.test(src.drafts)
    && !/request_key/.test(between(src.drafts, 'const save = useMutation', 'const confirm = useMutation')),
  '只靠 revision 乐观锁，成功但响应丢失后重试 409',
)

// F02-13 数量展示 number
check('F02-13', '订单明细数量无 quantity_exact，toLocaleString 展示', 'CONFIRMED',
  /dataIndex: 'quantity'/.test(src.detail)
    && /v\.toLocaleString\('zh-CN'\)/.test(src.detail)
    && /quantity: number/.test(src.api)
    && !/quantity_exact/.test(between(src.api, 'export interface OrderItem', 'export interface')),
  '金额有 exact，数量没有；大数量展示依赖 number',
)

// F02-P1 分配隐藏 cancelled — 证伪
check('F02-P1', '已取消期旧分配留在隐藏状态会导致错账', 'REFUTED',
  /plan\.status == "cancelled"/.test(src.payRouter)
    && /已取消.*不能分配/.test(src.payRouter)
    && /confirmed.*不能直接取消/.test(src.orderRouter),
  '后端对 cancelled plan 直接 422；订单取消又要求无已确认回款，正常路径无法同时存在',
)

// F02-P2 复购无键 — 确认
check('F02-P2', '复购开新商机无幂等键，重试可多建', 'CONFIRMED',
  /export function repurchase\(orderId: number\)/.test(src.api)
    && !/request_key/.test(between(src.api, 'export function repurchase', 'export function'))
    && /opportunity = Opportunity\(/.test(src.orderRouter)
    && !/idempotency/.test(between(src.orderRouter, 'async def repurchase', 'return ok')),
  '每次 POST 都新建 Opportunity，无键/无按订单去重',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
const missing = results.filter((r) => !r.ok)
console.log(`\n${results.filter((r) => r.verdict === 'CONFIRMED').length} confirmed, ${results.filter((r) => r.verdict === 'REFUTED').length} refuted, ${missing.length} missing`)
if (missing.length) process.exitCode = 1
