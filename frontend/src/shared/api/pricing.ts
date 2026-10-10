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
  /** 人工"立即停用"的时刻（第十一批 11.5）。非空即已停用 —— 与 `effective_to` 是两回事：
   *  前者是"人点失效"，后者是"自然到期"，界面上必须分得开。 */
  stopped_at?: string | null
  remark?: string | null
}

export interface PriceRuleRow {
  id: number
  sku_id: number
  sku_code?: string | null
  /** SKU 名称：列表接口现在会返回（价目表上只有编码认不出是哪个产品） */
  sku_name?: string | null
  customer_level?: string | null
  min_qty: number
  max_qty?: number | null
  standard_price?: number | null
  guide_price?: number | null
  minimum_price?: number | null
  target_margin?: number | null
  status: string
  effective_from?: string | null
  /** 后端一直有返回（`serialize_price_rule`），此前类型漏了它 —— 编辑弹窗要回填 */
  effective_to?: string | null
  remark?: string | null
}

export interface CustomerPriceRow {
  id: number
  customer_id: number
  customer_name?: string | null
  sku_id: number
  sku_code?: string | null
  /** SKU 名称：列表接口现在会返回 */
  sku_name?: string | null
  min_qty: number
  max_qty?: number | null
  agreed_price: number
  minimum_price?: number | null
  /** 后端一直有返回（`serialize_customer_price`），此前类型漏了 —— 编辑弹窗要回填 */
  effective_from?: string | null
  effective_to?: string | null
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
  /** SKU 名称。下拉选项用它区分同一产品下的不同 SKU（与 product_name 不同层） */
  name?: string | null
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

/**
 * 人工停用一条成本（第十一批 11.5）。
 *
 * **点了当天立即退出核价**，且**不能撤销** —— 后端把「什么时候被人停用的」单独
 * 记在一列里，取价时加一条"没被人工停用"的判据；`effective_to` 一个字不动，
 * 免得把"人停用的"和"自然到期"混成一种。要改回来就再新增一条成本：
 * 成本本来就是按日期分版本的，历史报价用的是它当时那份快照，不受影响。
 *
 * 重复点不会改原时刻（幂等），所以列表里给「已停用」的行不再显示按钮也没关系。
 */
export function expireCost(costId: number) {
  return api.post<CostRecord>(`/costs/${costId}/expire`)
}

export function listPriceRules(query: { sku_id?: number; keyword?: string; page?: number; page_size?: number }) {
  return api.get<PageResult<PriceRuleRow>>('/price-rules', query)
}

export function createPriceRule(payload: Record<string, unknown>) {
  return api.post<PriceRuleRow>('/price-rules', payload)
}

/**
 * 恢复启用一条被停用的价格规则（2026-10-09 审查建议）。
 *
 * 后端本来就走 `PATCH /price-rules/{id}` 的 `status` 字段（**没有**单独的
 * "启用"端点），所以这里只是把这条既有能力接到界面上：
 * 误点「停用」之后可以自己恢复，不必再新增一条。
 *
 * 恢复时后端**仍会做区间冲突校验**：与某条启用规则撞了区间会被拒（40901），
 * 状态保持停用 —— 这一点由后端保证，界面不自行判断。
 */
export function restorePriceRule(id: number) {
  return api.patch<PriceRuleRow>(`/price-rules/${id}`, { status: 'active' })
}

export function disablePriceRule(id: number) {
  return api.delete<null>(`/price-rules/${id}`)
}

/**
 * 改价格规则 —— 部分更新，只传要改的字段（API §16）。
 *
 * ⚠️ **能改与不能改是刻意的**（口径 2026-10-09 与主人确认）：
 * - 能改：数量区间 / 有效期 / 客户等级 / 备注 / 状态 —— 即「这条规则在什么条件下适用」；
 * - **不能改价钱**（标准价 / 指导价 / 最低保护价 / 目标利润率）：改价钱等于换了一套定价，
 *   正确做法是「停用旧的 + 新增一条」，这样生效时序在单据上看得见。
 *   后端会**明确拒绝并点名是哪个字段**（400），不是静默忽略 ——
 *   所以传了不该传的字段会被拦下，别指望它被吞掉。
 */
export function updatePriceRule(id: number, payload: Record<string, unknown>) {
  return api.patch<PriceRuleRow>(`/price-rules/${id}`, payload)
}

export function listCustomerPriceRules(query: {
  customer_id?: number
  /** 编码 / SKU 名称 / 规格 / 产品名 / 产品线 / 品牌 / 客户名 都能搜到 */
  keyword?: string
  page?: number
  page_size?: number
}) {
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

/**
 * 修改运费费率（只传要改的字段，**不传的保持原值**）。
 *
 * 要注意两处与"新增"不同的地方：
 * - 库里非空的列（承运方式 / 运输方式 / 公斤单价 / 最低收费 / 状态）**传 `null` 会被拒**；
 * - 两个地区字段要"清空成不限"时，**必须显式传 `null`**（不传 = 保持原值，改不掉）。
 */
export function updateLogisticsRate(rateId: number, payload: Record<string, unknown>) {
  return api.patch<LogisticsRateRow>(`/logistics/rates/${rateId}`, payload)
}

/** 删除运费费率。已发出的报价不受影响（运费当时已存进报价明细）。 */
export function deleteLogisticsRate(rateId: number) {
  return api.delete<null>(`/logistics/rates/${rateId}`)
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

