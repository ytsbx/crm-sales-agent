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

export interface QuoteItemRow {
  id: number
  sku_id: number
  sku_code?: string | null
  sku_name?: string | null
  specification?: string | null
  quantity: number
  cost_snapshot: number
  logistics_cost_snapshot: number
  standard_price_snapshot?: number | null
  recommended_price_snapshot?: number | null
  minimum_price_snapshot?: number | null
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
}

export interface ApprovalRecordRow {
  id: number
  action: string
  comment?: string | null
  approver_name?: string | null
  created_at: string
}

export interface QuoteVersionDetail {
  version: QuoteVersion
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
  id: number
  business_type: string
  business_id: number
  status: string
  status_label: string
  applicant_id?: number | null
  applicant_name?: string | null
  summary?: {
    quote_no?: string
    version_no?: number
    reason?: string
    offending?: Array<{ sku_code: string; quoted_price: number; minimum_price: number; profit_rate: number }>
    authorized_min_margin?: number
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

export function createQuote(payload: { opportunity_id?: number; customer_id?: number; valid_until?: string }) {
  return api.post<{ quote_id: number; version_id: number }>('/quotes', payload)
}

export function listQuoteVersions(quoteId: number) {
  return api.get<QuoteVersion[]>(`/quotes/${quoteId}/versions`)
}

export function createQuoteVersion(quoteId: number) {
  return api.post<QuoteVersion>(`/quotes/${quoteId}/versions`)
}

/** 报价多方案对比（What-if）：逐版本汇总 + 与上一版的差异明细。 */
export function compareQuoteVersions(quoteId: number) {
  return api.get<VersionComparison>(`/quotes/${quoteId}/version-comparison`)
}

export function getQuoteVersion(versionId: number) {
  return api.get<QuoteVersionDetail>(`/quote-versions/${versionId}`)
}

export function updateQuoteVersion(versionId: number, payload: Record<string, unknown>) {
  return api.patch<QuoteVersion>(`/quote-versions/${versionId}`, payload)
}

export function updateQuoteItem(itemId: number, payload: Record<string, unknown>) {
  return api.patch<QuoteItemRow>(`/quote-items/${itemId}`, payload)
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

export function markSent(versionId: number, payload: { channel: string; receiver?: string }) {
  return api.post<QuoteVersion>(`/quote-versions/${versionId}/mark-sent`, payload)
}

export function acceptQuote(versionId: number) {
  return api.post<QuoteVersion>(`/quote-versions/${versionId}/accept`)
}

export function declineQuote(versionId: number, reason?: string) {
  return api.post<QuoteVersion>(`/quote-versions/${versionId}/reject`, { reason })
}

export function listApprovals(query: { status?: string; mine?: boolean; page?: number; page_size?: number }) {
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
