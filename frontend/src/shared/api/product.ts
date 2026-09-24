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
