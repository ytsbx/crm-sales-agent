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
  /** 后端算好的中文结论：未核实的来源一律是「待核实」。 */
  source_status: string
  external_identity: string | null
  source_updated_at: string | null
  source_value: string | number | null
  authority: string | null
  /** 空归属显示「未拍板」——不默认任一系统为主。 */
  authority_label: string
  confirmed_version: number
  confirmed_value: string | number | null
  confirmed_at: string | null
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

/** 核定一条主数据差异。resolution 由后端按差异类型白名单校验。 */
export function confirmSkuMasterDiff(diffId: number, payload: { resolution: string; note?: string }) {
  return api.post<{ applied_to_local: boolean; message: string }>(
    `/sku-master/diffs/${diffId}/confirm`,
    payload,
  )
}

