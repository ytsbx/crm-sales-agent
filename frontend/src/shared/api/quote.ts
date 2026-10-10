import { api } from './client'
import type { PageResult } from '../types'

export interface Quote {
  id: number
  quote_no: string
  opportunity_id?: number | null
  opportunity_title?: string | null
  customer_id: number
  customer_name?: string | null
  owner_id?: number | null
  owner_name?: string | null
  status: string
  status_label: string
  current_version_id?: number | null
  current_version_no?: number | null
  current_version_amount?: number | null
  /** 当前版本的币种（第九批 §9.9）：金额不能假设是人民币 */
  currency?: string | null
  approval_status?: string | null
  approval_required?: boolean
  valid_until?: string | null
  created_at: string
}

export interface QuoteVersion {
  id: number
  quote_id: number
  version_no: number
  subtotal_amount: number
  charge_amount: number
  discount_amount: number
  total_amount: number
  /**
   * 金额拆分（2026-10-09「产品价格与运费分离」）。
   *
   * ⚠️ 这两个值**已经含在** `total_amount` 里了（`charge_amount` 也等于两者之和）。
   * 页面只能拿它们做**分项展示**，绝对不要再加到总额上——那是重复计费。
   * 恒等式：`charge_amount === logistics_amount + other_charge_amount`
   */
  logistics_amount: number
  other_charge_amount: number
  /** 本版产品核价口径：legacy（成本含运费）/ actual_pass_through（运费代收代付） */
  pricing_basis: string
  pricing_basis_label?: string
  payment_terms?: string | null
  delivery_terms?: string | null
  remark?: string | null
  approval_status: string
  approval_required: boolean
  created_at: string
  sent_at?: string | null
  accepted_at?: string | null
  declined_at?: string | null
  total_profit?: number | null
}

/**
 * 金额汇总 —— **后端算好的那一份**（`quote.service.amount_summary`）。
 *
 * 页面优先用它，不要自己按明细再算一遍：页面、对客 Excel/PDF、订单、应收
 * 必须共用同一份公式，两套公式迟早对不上，而这里对不上就是钱对不上。
 */
export interface QuoteAmountSummary {
  goods_amount: number
  /** 运费（代收代付）：向客户收取的金额，同时也是公司付给承运商的金额 */
  logistics_amount: number
  other_charge_amount: number
  charge_amount: number
  /** 优惠：**负数**（与库内约定一致） */
  discount_amount: number
  total_amount: number
  currency?: string | null
  pricing_basis: string
  pricing_basis_label?: string
}

export interface QuoteItemRow {
  id: number
  /** 定制项（场景09）没有 SKU，此时靠 inquiry_no 溯源 */
  sku_id: number | null
  sku_code?: string | null
  sku_name?: string | null
  inquiry_id?: number | null
  inquiry_no?: string | null
  is_custom?: boolean
  specification?: string | null
  quantity: number
  cost_snapshot: number
  logistics_cost_snapshot: number
  standard_price_snapshot?: number | null
  recommended_price_snapshot?: number | null
  minimum_price_snapshot?: number | null
  /** 价格来源快照（方案 §4.1）：customer_specific/level/general，空=手工价 */
  price_source?: string | null
  customer_level_snapshot?: string | null
  quoted_price: number
  amount: number
  profit_snapshot: number
  profit_rate_snapshot: number
  approval_required: boolean
  approval_reason?: string | null
  remark?: string | null
}

export interface QuoteChargeRow {
  id: number
  charge_type: string
  type_label?: string | null
  description?: string | null
  amount: number
  is_discount: boolean
  /** 后端判定：这条费用是不是"向客户收取的运费"（只认分类码物流，不看说明文字） */
  is_logistics?: boolean
  /**
   * 运费被业务确认的时刻。
   *
   * `null` = **尚未确认**。注意金额为 0 **不等于**已确认零运费：
   * 空输入与"明确是零运费"必须分得开，正式发送要求后者。
   */
  logistics_confirmed_at?: string | null
}

export interface ApprovalRecordRow {
  id: number
  action: string
  comment?: string | null
  approver_name?: string | null
  created_at: string
}

/**
 * 后端在**写入口与版本详情**的响应体里附带的主数据提醒（§8.14 复审）。
 *
 * 内容是"哪个 SKU 的哪些字段还没确认"（例如「ZX-6040 的「规格、单位」主数据尚未确认」）。
 * 为什么放在 `data` 里而不是 `message`：统一客户端只把 `data` 交给页面，
 * `message` 到不了界面（`_with_master_warnings` 的注释里写着这个坑）。
 *
 * 前端必须**接住并持续显示**：只闪一次 Toast 的话，刷新页面、换个人打开就看不到了，
 * 用户往往到"发送被拒"时才知道是主数据的问题。
 */
export type WithMasterWarnings<T> = T & { master_warnings?: string[] }

export interface QuoteVersionDetail {
  version: QuoteVersion
  /** 后端统一的金额汇总（货款/运费/其他费用/优惠/合计），页面直接用它 */
  summary?: QuoteAmountSummary
  /**
   * 运费尚未确认时的人话原因（后端给）；`null`/缺省 = 已确认可以正式发送。
   * 与 `master_warnings` 同一性质：要**持续显示**，不能只闪一次 Toast。
   */
  freight_unconfirmed_reason?: string | null
  quote: Quote | null
  items: QuoteItemRow[]
  charges: QuoteChargeRow[]
  approval: {
    id: number
    status: string
    current_node?: string | null
    applicant_id?: number | null
    summary?: Record<string, unknown> | null
    created_at: string
    finished_at?: string | null
    records: ApprovalRecordRow[]
  } | null
}

export interface VersionComparisonRow {
  version_id: number
  version_no: number
  approval_status: string
  currency: string
  item_count: number
  sku_count: number
  quantity: number
  amount_total: number
  charge_amount: number
  discount_amount: number
  total_amount: number
  cost_total: number
  profit_total: number
  margin: number
  avg_price: number | null
  avg_cost: number | null
  /** 单件成本口径（= 商品成本 + 运费；与核价 base_cost 一致）。 */
  unit_cost?: number | null
  unit_price?: number | null
  unit_profit?: number | null
  /** 整版加权的基准线（单 SKU 的价没法直接和整版均价比，所以按数量加权）。 */
  unit_floor?: number | null
  unit_standard?: number | null
  unit_recommended?: number | null
  approval_required: boolean
  created_at: string
  sent_at?: string | null
  accepted_at?: string | null
  trade_terms?: string | null
  payment_terms?: string | null
  charges: QuoteChargeRow[]
}

export interface VersionComparisonChange {
  sku_id: number | null
  sku_name?: string | null
  field: 'added' | 'removed' | 'quantity' | 'quoted_price' | 'charge_amount' | 'discount_amount'
  before: number | null
  after: number | null
}

export interface VersionComparisonDiff {
  from_version_no: number
  to_version_no: number
  amount_delta: number
  profit_delta: number
  margin_delta: number
  changes: VersionComparisonChange[]
}

export interface VersionComparison {
  versions: VersionComparisonRow[]
  diffs: VersionComparisonDiff[]
  latest_version_id: number | null
}

export interface ApprovalRow {
  can_approve: boolean
  id: number
  business_type: string
  business_id: number
  status: string
  status_label: string
  current_node?: string | null
  applicant_id?: number | null
  applicant_name?: string | null
  summary?: {
    quote_no?: string
    version_no?: number
    reason?: string
    offending?: Array<{ sku_code: string; quoted_price: number; minimum_price: number; profit_rate: number }>
    authorized_min_margin?: number
    co_sign?: { label?: string; role_codes?: string[]; status?: string } | null
    auto_passed?: boolean
  } | null
  created_at: string
  finished_at?: string | null
  quote_id?: number | null
  quote_no?: string | null
  version_id?: number | null
  version_no?: number | null
  total_amount?: number | null
  customer_name?: string | null
}

export function listQuotes(query: {
  keyword?: string
  status?: string
  opportunity_id?: number
  customer_id?: number
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<Quote>>('/quotes', query)
}

export function createQuote(payload: {
  opportunity_id?: number
  customer_id?: number
  valid_until?: string
  /**
   * 请求幂等键（第八批 8.15）：同一份表单的多次提交必须带**同一把**键，
   * 弱网重试才不会建出两条报价。键由调用方在"打开表单时"生成并持有，
   * 成功后清空；不要在这里现生成 —— 那样每次重试都是新键，等于没有幂等。
   */
  request_key?: string
}) {
  return api.post<WithMasterWarnings<{ quote_id: number; version_id: number; warnings?: string[] }>>('/quotes', payload)
}

export function listQuoteVersions(quoteId: number) {
  return api.get<QuoteVersion[]>(`/quotes/${quoteId}/versions`)
}

/** A09：草稿版本"价格已有更新"检测 */
export function getPriceDrift(versionId: number) {
  return api.get<{
    any_drift: boolean
    items: Array<{
      item_id: number
      sku_code: string | null
      quoted_price: number | null
      current_applicable: number | null
      source: string | null
      hand_priced: boolean
      drift: boolean
    }>
  }>(`/quote-versions/${versionId}/price-drift`)
}

/** 一键把系统带价的明细刷新到当前适用价（手工价不动） */
/** 报价草稿刷新主数据前的预览：会变什么、能不能刷。 */
export interface QuoteMasterRefreshPreview {
  item_id: number
  sku_id: number
  sku_code: string
  /** 明细当前引用的主数据版本（空 = 生成时还没有可引用的已确认版本） */
  master_version_no: number | null
  /** 从未引用过任何已确认版本 —— 它最需要刷新（刷新才会把版本号钉上去） */
  never_referenced: boolean
  confirmed_version_no: number | null
  unconfirmed: string[]
  unconfirmed_labels: string[]
  changes: { field: string; label: string; before: string | null; after: string | null }[]
}

export interface QuoteMasterRefreshState {
  items: QuoteMasterRefreshPreview[]
  changed_count: number
  never_referenced_count: number
  item_count: number
  display_field_labels: string[]
  /** 已发送 / 已审批的版本为 false（内容是对客承诺，只能新建版本） */
  refreshable: boolean
  sent: boolean
  blocked_reason?: string
  message: string
}

export function getQuoteMasterRefreshPreview(versionId: number) {
  return api.get<QuoteMasterRefreshState>(`/quote-versions/${versionId}/master-refresh-preview`)
}

export function refreshPrices(versionId: number) {
  return api.post<{ refreshed: number; skipped: number }>(
    `/quote-versions/${versionId}/price-refresh`,
  )
}

export function createQuoteVersion(quoteId: number) {
  return api.post<QuoteVersion>(`/quotes/${quoteId}/versions`)
}

/** 报价多方案对比（What-if）：逐版本汇总 + 与上一版的差异明细。 */
export function compareQuoteVersions(quoteId: number) {
  return api.get<VersionComparison>(`/quotes/${quoteId}/version-comparison`)
}

export function getQuoteVersion(versionId: number) {
  // 详情一直带 `master_warnings`：明细页据此**持续显示**未确认状态（§8.14 复审）
  return api.get<WithMasterWarnings<QuoteVersionDetail>>(`/quote-versions/${versionId}`)
}

export function updateQuoteVersion(versionId: number, payload: Record<string, unknown>) {
  return api.patch<QuoteVersion>(`/quote-versions/${versionId}`, payload)
}

export function updateQuoteItem(itemId: number, payload: Record<string, unknown>) {
  return api.patch<WithMasterWarnings<QuoteItemRow>>(`/quote-items/${itemId}`, payload)
}

/** 追加一条明细（逐条录需求时用）。 */
export function addQuoteVersionItem(versionId: number, payload: Record<string, unknown>) {
  return api.post<WithMasterWarnings<QuoteItemRow>>(`/quote-versions/${versionId}/items`, payload)
}

/**
 * 整版替换明细（界面"保存整版"用）。
 *
 * body 是**裸数组**（后端签名是 `items: list[QuoteItemInput]`），不是 `{items: [...]}`。
 */
export function setQuoteVersionItems(versionId: number, items: Record<string, unknown>[]) {
  // 返回的是**重算后的版本**（后端 ok(serialize_version(...))），不是明细数组，
  // 并附带 `master_warnings`（哪些明细的主数据还没确认）
  return api.post<WithMasterWarnings<QuoteVersion>>(`/quote-versions/${versionId}/items/batch`, items)
}

export function addQuoteCharge(versionId: number, payload: Record<string, unknown>) {
  return api.post<QuoteChargeRow>(`/quote-versions/${versionId}/charges`, payload)
}

export function deleteQuoteCharge(chargeId: number) {
  return api.delete<null>(`/quote-charges/${chargeId}`)
}

export function submitApproval(versionId: number, reason?: string) {
  return api.post<{ approval_required: boolean; approval_id: number | null }>(
    `/quote-versions/${versionId}/submit-approval`,
    { reason },
  )
}

export function withdrawApproval(versionId: number) {
  return api.post<null>(`/quote-versions/${versionId}/withdraw-approval`)
}

export function markSent(versionId: number, payload: { channel: string; receiver?: string; request_key?: string }) {
  return api.post<QuoteVersion>(`/quote-versions/${versionId}/mark-sent`, payload)
}

export function acceptQuote(versionId: number) {
  return api.post<QuoteVersion>(`/quote-versions/${versionId}/accept`)
}

export function declineQuote(versionId: number, reason?: string) {
  return api.post<QuoteVersion>(`/quote-versions/${versionId}/reject`, { reason })
}

/** 转交待审批单给同事（03-API §23）：转的是处理权，不是权限。 */
export function transferApproval(id: number, to_user_id: number, comment?: string) {
  return api.post<ApprovalRow>(`/approvals/${id}/transfer`, { to_user_id, comment })
}

export function listApprovals(query: { status?: string; mine?: boolean; pending_for_me?: boolean; page?: number; page_size?: number }) {
  return api.get<PageResult<ApprovalRow>>('/approvals', query)
}

export function approveApproval(id: number) {
  return api.post<null>(`/approvals/${id}/approve`)
}

export function rejectApproval(id: number, comment?: string) {
  return api.post<null>(`/approvals/${id}/reject?comment=${encodeURIComponent(comment ?? '')}`)
}

/** 下载报价单 PDF：接口要带 token，所以先用 axios 取 blob 再触发浏览器下载。 */
export function downloadQuotePdf(versionId: number, filename: string) {
  return api.download(`/quote-versions/${versionId}/pdf`, filename)
}
