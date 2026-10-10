import { api } from './client'
import type { PageResult, Product, Sku } from '../types'

export interface ProductQuery {
  keyword?: string
  status?: string
  category?: string
  page?: number
  page_size?: number
}

export interface ProductPayload {
  name: string
  product_line?: string | null
  category?: string | null
  brand?: string | null
  description?: string | null
  knowledge?: string | null
}

export interface SkuPayload {
  sku_code: string
  name?: string | null
  specification?: string | null
  color?: string | null
  material?: string | null
  length?: number | null
  width?: number | null
  height?: number | null
  weight?: number | null
  carton_qty?: number | null
  carton_volume?: number | null
  moq?: number | null
  package_type?: string | null
  unit?: string | null
}

export function listProducts(query: ProductQuery) {
  return api.get<PageResult<Product>>('/products', query)
}

export function getProduct(id: number) {
  return api.get<Product>(`/products/${id}`)
}

export function createProduct(payload: ProductPayload) {
  return api.post<Product>('/products', payload)
}

export function updateProduct(id: number, payload: Partial<ProductPayload>) {
  return api.patch<Product>(`/products/${id}`, payload)
}

export function deleteProduct(id: number) {
  return api.delete<null>(`/products/${id}`)
}

export function listProductSkus(productId: number) {
  return api.get<Sku[]>(`/products/${productId}/skus`)
}

export function createSku(productId: number, payload: SkuPayload) {
  return api.post<Sku>(`/products/${productId}/skus`, payload)
}

export function updateSku(skuId: number, payload: Partial<SkuPayload> & { status?: string }) {
  return api.patch<Sku>(`/skus/${skuId}`, payload)
}

export function disableSku(skuId: number) {
  return api.post<null>(`/skus/${skuId}/disable`)
}

export function enableSku(skuId: number) {
  return api.post<null>(`/skus/${skuId}/enable`)
}

export function listSkus(query: { keyword?: string; product_id?: number; page?: number; page_size?: number }) {
  return api.get<PageResult<Sku>>('/skus', query)
}

// ---------------------------------------------------------------- 产品附件与导入导出

/** 给产品挂附件（API §14）。file_id 来自 POST /files/upload。 */
export function attachProductFile(
  productId: number,
  payload: { file_id: number; category?: string; remark?: string },
) {
  const params = new URLSearchParams({ file_id: String(payload.file_id) })
  if (payload.category) params.set('category', payload.category)
  if (payload.remark) params.set('remark', payload.remark)
  return api.post<{ business_file_id: number; file_id: number; name: string }>(
    `/products/${productId}/files?${params.toString()}`,
  )
}

/** 按筛选条件导出产品 CSV（API §14）。 */
export function exportProductsFiltered(
  payload: { keyword?: string },
  filename = '产品列表.csv',
) {
  return api.downloadPost('/products/export', payload, filename)
}

/** 按筛选条件导出 SKU CSV（API §15）。 */
export function exportSkusFiltered(
  payload: { keyword?: string; product_id?: number },
  filename = 'SKU列表.csv',
) {
  return api.downloadPost('/skus/export', payload, filename)
}

// ---------------------------------------------------------------- SKU 主数据权威（§8.14）

/** 一个关键字段的来源、更新时间、外部身份与人工确认版本。 */
export interface SkuMasterField {
  field_name: string
  field_label: string
  local_value: string | number | null
  source_system: string | null
  source_verified: boolean
  /**
   * 后端算好的中文结论，**三种取值**：
   * `已核实`（外部来源已核实）/ `待核实`（**有**外部来源但未核实）/
   * `无外部来源`（压根没有外部来源 —— 本地自建 SKU）。
   * 「无外部来源」不再写成「待核实」：那会让人以为还差一步外部核对，而它永远等不到。
   */
  source_status: string
  external_identity: string | null
  source_updated_at: string | null
  source_value: string | number | null
  authority: string | null
  /** 空归属显示「未拍板」——不默认任一系统为主。 */
  authority_label: string
  confirmed_version: number
  /** 上次确认的值。与 `local_value` 不同时说明"本地改了、还没重新确认" */
  confirmed_value: string | number | null
  confirmed_at: string | null
  /**
   * 本地值是否与已确认值不同。
   *
   * 显式区分两种状态：`false` + `confirmed_version === 0` 是**从没确认过**；
   * `true` 是**确认过、后来本地又改了**（前端据此提示
   * 「本地值已修改，尚未重新确认」）。
   */
  local_differs_from_confirmed: boolean
  status: string
}

export interface SkuMasterDiff {
  id: number
  diff_type: string
  field_name: string | null
  field_label: string | null
  current_value: unknown
  incoming_value: unknown
  status: string
  allowed_resolutions: string[]
  requires_note: boolean
  evidence: Record<string, unknown> | null
}

export interface SkuMasterOverview {
  sku_id: number
  sku_code: string
  fields: SkuMasterField[]
  pending_diffs: SkuMasterDiff[]
  pending_diff_count: number
  latest_confirmed_version: number
  notes: string[]
}

export function getSkuMaster(skuId: number) {
  return api.get<SkuMasterOverview>(`/sku-master/skus/${skuId}`)
}

/** 本地直接确认的结果。 */
export interface SkuLocalConfirmResult {
  sku_id: number
  sku_code: string
  confirmed_fields: string[]
  confirmed_labels: string[]
  values: Record<string, unknown>
  changed_fields: string[]
  changed_labels: string[]
  version_no: number | null
  version_id: number | null
  confirmed_by: number | null
  confirmed_at: string | null
  note: string | null
  /** false = 值与已确认版本一致，**没有**新增快照（避免重复点确认垒版本）。 */
  snapshot_created: boolean
  /**
   * true = 这次只是"又核对了一遍"，没有任何确认值发生变化。
   * 此时**不动确认时间与次数**，只写一条 `sku_master_recheck` 审计。
   */
  recheck: boolean
  message: string
}

/**
 * **本地直接确认**主数据（不依赖差异记录）。
 *
 * 补的是这条链缺的那一段：本地自建的 SKU 既没有确认记录、也没有差异记录，
 * 唯一的确认动作又挂在差异上 —— 于是正式报价永久被拦、无路可走。
 * 「从外部导入一次」也不是可靠出口：来源值与本地一致时零差异，仍然没有入口。
 *
 * 确认的是**当前本地值**（与差异核定的「保留本地」同一口径），
 * 记录操作人与时间并生成一版快照，供正式报价引用。
 */
export function confirmLocalSkuMaster(
  skuId: number,
  payload: { fields?: string[]; note?: string } = {},
) {
  return api.post<SkuLocalConfirmResult>(`/sku-master/skus/${skuId}/confirm-local`, {
    sku_id: skuId,
    ...payload,
  })
}

/** 核定一条主数据差异。resolution 由后端按差异类型白名单校验。 */
export function confirmSkuMasterDiff(diffId: number, payload: { resolution: string; note?: string }) {
  return api.post<{ applied_to_local: boolean; message: string }>(
    `/sku-master/diffs/${diffId}/confirm`,
    payload,
  )
}

