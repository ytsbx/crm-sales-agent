import { api } from './client'
import type { PageResult } from '../types'

/** 回收站里的线索（后端只返回"已被软删"的、且在当前用户数据范围内）。 */
export interface RecycleLead {
  id: number
  name: string
  company_name: string | null
  contact_name: string | null
  mobile: string | null
  status: string
  status_label: string
  owner_id: number | null
  owner_name: string | null
  deleted_at: string | null
  created_at: string | null
}

/** 回收站里的产品。`deleted_sku_count` = 恢复它时会连带捡回来的 SKU 数。 */
export interface RecycleProduct {
  id: number
  name: string
  product_line: string | null
  category: string | null
  brand: string | null
  status: string
  deleted_sku_count: number
  deleted_at: string | null
  created_at: string | null
}

/** 回收站里的 SKU。 */
export interface RecycleSku {
  id: number
  sku_code: string
  name: string | null
  specification: string | null
  product_id: number
  product_name: string | null
  /** 产品也在回收站 → 单独恢复会变成孤儿，得先恢复产品。 */
  product_deleted: boolean
  /** 编码被别的 SKU 占着（当前库里不会发生，留作纵深防御）。 */
  code_occupied: boolean
  deleted_at: string | null
  created_at: string | null
}

export interface RecycleCustomer {
  id: number
  name: string
  short_name: string | null
  owner_id: number | null
  owner_name: string | null
  deleted_at: string | null
  created_at: string | null
  /** 被合并掉的才有值：并进了哪个客户（拿它做跳转）。 */
  merged_into: { id: number; name: string | null } | null
  merge_reason: string | null
}

export interface RestoreProductResult {
  restored_skus: Array<{ id: number; sku_code: string; name: string | null }>
  skipped_skus: Array<{ id: number; sku_code: string; reason: string }>
}

interface RecycleQuery {
  page?: number
  page_size?: number
}

export function listRecycleLeads(query: RecycleQuery) {
  return api.get<PageResult<RecycleLead>>('/recycle-bin/leads', query)
}

export function listRecycleProducts(query: RecycleQuery) {
  return api.get<PageResult<RecycleProduct>>('/recycle-bin/products', query)
}

export function listRecycleSkus(query: RecycleQuery) {
  return api.get<PageResult<RecycleSku>>('/recycle-bin/skus', query)
}

export function listRecycleCustomers(query: RecycleQuery) {
  return api.get<PageResult<RecycleCustomer>>('/recycle-bin/customers', query)
}

/** 恢复线索（复用线索模块现成的入口）。 */
export function restoreLead(leadId: number) {
  return api.post<null>(`/leads/${leadId}/restore`)
}

/** 恢复产品：会把该产品下被删的 SKU 一起捡回来。 */
export function restoreProduct(productId: number) {
  return api.post<RestoreProductResult>(`/products/${productId}/restore`)
}

/** 恢复单个 SKU（它所属的产品必须已经恢复）。 */
export function restoreSku(skuId: number) {
  return api.post<null>(`/skus/${skuId}/restore`)
}
