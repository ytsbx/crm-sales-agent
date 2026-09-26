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

