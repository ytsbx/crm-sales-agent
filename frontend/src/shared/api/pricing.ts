import { api } from './client'
import type { PageResult } from '../types'

export interface CostRecord {
  id: number
  sku_id: number
  sku_code?: string | null
  purchase_cost: number
  production_cost: number
  package_cost: number
  processing_cost: number
  total_cost: number
  currency: string
  effective_from: string
  effective_to?: string | null
  remark?: string | null
}

export interface PriceRuleRow {
  id: number
  sku_id: number
  sku_code?: string | null
  customer_level?: string | null
  min_qty: number
  max_qty?: number | null
  standard_price?: number | null
  guide_price?: number | null
  minimum_price?: number | null
  target_margin?: number | null
  status: string
  effective_from?: string | null
  remark?: string | null
}

export interface CustomerPriceRow {
  id: number
  customer_id: number
  customer_name?: string | null
  sku_id: number
  sku_code?: string | null
  min_qty: number
  max_qty?: number | null
  agreed_price: number
  minimum_price?: number | null
  remark?: string | null
}

export interface PricePermissionRow {
  id: number
  role_id: number
  role_name?: string | null
  role_code?: string | null
  minimum_margin: number
  discount_limit?: number | null
  can_approve: boolean
  status: string
}

export interface LogisticsRateRow {
  id: number
  provider: string
  origin_region?: string | null
  destination_region?: string | null
  shipping_method: string
  unit_price_per_kg: number
  unit_price_per_volume?: number | null
  min_charge: number
  eta_days?: number | null
  eta_days_max?: number | null
  status: string
  remark?: string | null
}

/** 物流试算：单件体积/重量与计费重（PRD §14 要求的输出项）。 */
export interface LogisticsMeasures {
  actual_weight: number
  volume: number
  volumetric_weight: number
  volumetric_enabled: boolean
  chargeable_weight: number
  chargeable_basis: string
  volume_source?: string | null
  volumetric_ratio: number
  warnings: string[]
}

export interface LogisticsOption {
  provider: string
  rate_id: number
  shipping_method: string
  origin_region?: string | null
  destination_region?: string | null
  amount: number
  currency: string
  by_weight_amount: number
  by_volume_amount: number
  pricing_basis: string
  above_minimum: boolean
  min_charge?: number | null
  eta_days?: number | null
  eta_days_max?: number | null
  eta_text?: string | null
  unit_price_per_kg?: number | null
  unit_price_per_volume?: number | null
}

export interface LogisticsCalculateResult {
  sku: { id: number; sku_code: string; name?: string | null; specification?: string | null; unit?: string | null; package_type?: string | null }
  quantity: number
  origin?: string | null
  destination?: string | null
  shipping_method?: string | null
  package_type?: string | null
  measures: LogisticsMeasures
  options: LogisticsOption[]
  selected?: LogisticsOption | null
  quoted_id?: number | null
  warnings: string[]
}

export interface LogisticsCompareResult {
  measures: LogisticsMeasures
  options: LogisticsOption[]
  option_count: number
  cheapest_provider?: string | null
  fastest_provider?: string | null
  warnings: string[]
}

export interface LogisticsQuoteRow {
  id: number
  customer_id?: number | null
  opportunity_id?: number | null
  sku_id?: number | null
  quantity?: number | null
  origin?: string | null
  destination?: string | null
  shipping_method: string
  chargeable_weight: number
  actual_weight: number
  volume: number
  currency: string
  amount: number
  unit_price?: number | null
  eta_days?: number | null
  provider?: string | null
  created_at?: string | null
}

export interface LogisticsRouteRow {
  origin?: string | null
  destination?: string | null
  shipping_method: string
  providers: string[]
  eta_days_min?: number | null
  eta_days_max?: number | null
}

export interface PricingSkuOption {
  id: number
  sku_code: string
  specification?: string | null
  product_name?: string | null
  moq?: number | null
  unit?: string | null
  weight?: number | null
}

export interface PricingResult {
  sku: { id: number; sku_code: string; name?: string | null; specification?: string | null; unit?: string | null; moq?: number | null }
  quantity: number
  customer_id?: number | null
  customer_name?: string | null
  customer_level?: string | null
  cost: {
    purchase_cost: number
    production_cost: number
    package_cost: number
    processing_cost: number
    goods_cost: number
    logistics_cost: number | null
    base_cost: number
    source: string
  }
  price_rule?: PriceRuleRow | null
  customer_price_rule?: CustomerPriceRow | null
  target_margin: number
  standard_price: number
  recommended_price: number
  recommended_range: [number, number]
  /** 公司口径的最低价（保护价）：低于它必须走审批，与下面的授权底价分开看。 */
  protection_price?: number | null
  /** 当前用户权限内的底价。 */
  minimum_price: number
  authorized_min_margin: number
  can_approve: boolean
  quoted_price: number
  currency: string
  exchange_rate?: number | null
  cost_in_quote_currency?: number | null
  tax_refund_rate?: number | null
  tax_refund?: number | null
  profit_with_refund?: number | null
  profit_rate_with_refund?: number | null
  profit: number
  profit_rate: number
  approval_required: boolean
  approval_triggers?: {
    below_protection_price: boolean
    below_authorized_price: boolean
    negative_profit: boolean
    below_authorized_margin: boolean
  }
  warnings: string[]
}

export function listCosts(skuId: number) {
  return api.get<CostRecord[]>(`/skus/${skuId}/costs`)
}

export function createCost(skuId: number, payload: Record<string, unknown>) {
  return api.post<CostRecord>(`/skus/${skuId}/costs`, payload)
}

export function listPriceRules(query: { sku_id?: number; keyword?: string; page?: number; page_size?: number }) {
  return api.get<PageResult<PriceRuleRow>>('/price-rules', query)
}

export function createPriceRule(payload: Record<string, unknown>) {
  return api.post<PriceRuleRow>('/price-rules', payload)
}

export function disablePriceRule(id: number) {
  return api.delete<null>(`/price-rules/${id}`)
}

export function listCustomerPriceRules(query: { customer_id?: number; page?: number; page_size?: number }) {
  return api.get<PageResult<CustomerPriceRow>>('/customer-price-rules', query)
}

export function createCustomerPriceRule(payload: Record<string, unknown>) {
  return api.post<CustomerPriceRow>('/customer-price-rules', payload)
}

/** 改客户特殊价 —— 部分更新，只传要改的字段（API §17）。 */
export function updateCustomerPriceRule(id: number, payload: Record<string, unknown>) {
  return api.patch<CustomerPriceRow>(`/customer-price-rules/${id}`, payload)
}

export function deleteCustomerPriceRule(id: number) {
  return api.delete<null>(`/customer-price-rules/${id}`)
}

export function listPricePermissions() {
  return api.get<PricePermissionRow[]>('/price-permissions')
}

export function savePricePermission(roleId: number, payload: Record<string, unknown>) {
  return api.put<PricePermissionRow>(`/price-permissions/${roleId}`, payload)
}

export function listLogisticsRates() {
  return api.get<LogisticsRateRow[]>('/logistics/rates')
}

export function createLogisticsRate(payload: Record<string, unknown>) {
  return api.post<LogisticsRateRow>('/logistics/rates', payload)
}

// ---------------------------------------------------------------- 物流试算
// 对应 03-API §19 的 6 个接口；页面在 modules/logistics/LogisticsPage.tsx

export function listLogisticsProviders() {
  return api.get<{ provider: string }[]>('/logistics/providers')
}

export function listLogisticsRoutes() {
  return api.get<LogisticsRouteRow[]>('/logistics/routes')
}

export function calculateLogistics(payload: Record<string, unknown>) {
  return api.post<LogisticsCalculateResult>('/logistics/calculate', payload)
}

export function compareLogistics(payload: Record<string, unknown>) {
  return api.post<LogisticsCompareResult>('/logistics/compare', payload)
}

export function listLogisticsQuotes(params?: Record<string, unknown>) {
  return api.get<{ items: LogisticsQuoteRow[]; total: number; page: number; page_size: number }>(
    '/logistics/quotes',
    { params },
  )
}

export function getLogisticsQuote(id: number) {
  return api.get<LogisticsQuoteRow>(`/logistics/quotes/${id}`)
}

export function listSkusForPricing() {
  return api.get<PricingSkuOption[]>('/pricing/sku-options')
}

export function calculatePrice(payload: Record<string, unknown>) {
  return api.post<PricingResult>('/pricing/calculate', payload)
}

// ---------------------------------------------------------------- 询价权限 / 模拟 / 历史（API §18）

export interface PricePermissionVerdict {
  sku_id: number
  quantity: number | null
  quoted_price: number | null
  currency: string
  allowed: boolean
  approval_required: boolean
  can_approve: boolean
  reasons: string[]
  minimum_price: number | null
  protection_price: number | null
  authorized_min_margin: number | null
  standard_price: number | null
  recommended_price: number | null
  recommended_range: [number | null, number | null]
  profit: number | null
  profit_rate: number | null
  cost_in_quote_currency: number | null
  approval_triggers: Record<string, boolean>
  my_roles: string[]
}

export interface PricingScenario {
  quoted_price: number | null
  profit: number | null
  profit_rate: number | null
  amount: number | null
  approval_required: boolean
  reasons: string[]
}

export interface PricingSimulationResult {
  sku: Record<string, unknown>
  quantity: number | null
  currency: string
  cost_in_quote_currency: number | null
  standard_price: number | null
  recommended_price: number | null
  recommended_range: [number | null, number | null]
  minimum_price: number | null
  protection_price: number | null
  scenarios: PricingScenario[]
}

export interface PricingHistoryRow {
  id: number
  business_type: string | null
  business_id: number | null
  /**
   * 业务对象的人话描述，如「报价单 Q202610060001 · ZX-6040-B」。
   * 后端反查出来的（审计表本身只有内部编号，看不出是哪张单）。
   * 查不到时为 null —— 前端退回显示编号，不要因此把那一格变空白。
   */
  target_label?: string | null
  /** 能跳转就跳（目前只有报价类有页面），跳不了给 null。 */
  target_link?: string | null
  action: string
  operator_id: number | null
  operator_name: string | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  created_at: string
}

/** 问"这个价我能不能报"（API §18）。 */
export function checkPricePermission(payload: Record<string, unknown>) {
  return api.post<PricePermissionVerdict>('/pricing/check-permission', payload)
}

/** 一次算多个候选价，看清让步空间（API §18）。 */
export function simulatePricing(payload: Record<string, unknown>) {
  return api.post<PricingSimulationResult>('/pricing/simulate', payload)
}

/** 核价历史，取自审计日志（API §18）。 */
export function listPricingHistory(query: {
  sku_id?: number
  customer_id?: number
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<PricingHistoryRow>>('/pricing/history', query)
}

/** 统一查价（产品报价中心 · 第一批）：客户专属价 → 等级价 → 通用指导价 → 待定价。 */
export interface PriceLookupResult {
  status: 'ok' | 'pending'
  source: 'customer_specific' | 'level' | 'general' | null
  source_label: string | null
  unit_price: number | null
  standard_price?: number | null
  minimum_price?: number | null
  currency: string
  rule_id?: number | null
  effective_from?: string | null
  effective_to?: string | null
  fallback_note?: string | null
  customer: { id: number; name: string; level?: string | null }
  sku: { id: number; sku_code: string; name?: string | null }
  quantity: number
  can_see_cost?: boolean
  cost?: number | null
  cost_note?: string | null
}

export function lookupPrice(query: {
  customer_id: number
  sku_id: number
  quantity: number | string
}) {
  return api.get<PriceLookupResult>('/pricing/lookup', query)
}

