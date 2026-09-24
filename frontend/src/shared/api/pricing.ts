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
  destination_region?: string | null
  shipping_method: string
  unit_price_per_kg: number
  min_charge: number
  eta_days?: number | null
  status: string
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

export function listSkusForPricing() {
  return api.get<PricingSkuOption[]>('/pricing/sku-options')
}

export function calculatePrice(payload: Record<string, unknown>) {
  return api.post<PricingResult>('/pricing/calculate', payload)
}
