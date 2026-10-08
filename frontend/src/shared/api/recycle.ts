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

/**
 * 合并去向的引用。**不可见时 id 与 name 都是 null**（后端刻意不下发，避免泄露）。
 *
 * `id` 只在"在数据范围内、且这个客户还活着"时才给 —— 它是要拿去做跳转的，
 * 指向一个打不开的页面比不给更糟。`loop` / `truncated` 这两种状态下同样不给 id：
 * 那时候连"终点是谁"都还没确定，给出去必然是个错的链接。
 */
export interface MergeRef {
  id: number | null
  name: string | null
  visible: boolean
  /**
   * ok=可跳转 / forbidden=不在数据范围 / gone=已不存在或被删 /
   * loop=合并链条成环 / truncated=链条比追踪上限还长，没追到底
   */
  state: 'ok' | 'forbidden' | 'gone' | 'loop' | 'truncated'
}

export interface RecycleCustomer {
  id: number
  name: string
  short_name: string | null
  /**
   * 客户**当前**的负责人。成对下发 —— 被合并掉的客户在合并那一刻负责人就清空了，
   * 所以这里对合并来源必然是 (null, null)。想在回收站看负责人请看 `original_owner_*`。
   */
  owner_id: number | null
  owner_name: string | null
  /**
   * **原**负责人：合并来源取"合并前那张快照"，直接删除的取当时字段。也是成对下发。
   * 从前 `owner_id` 取当前字段、`owner_name` 取快照，两个字段说的不是同一件事。
   */
  original_owner_id: number | null
  original_owner_name: string | null
  /**
   * 原负责人"待核实"：这是一条**合并来源**记录，但合并留痕里没留下当时的负责人，
   * 于是不知道该归谁看 —— 后端只把它给管理员。界面照实说明，别显示成"未分配"。
   */
  owner_pending: boolean
  deleted_at: string | null
  created_at: string | null
  /** 直接合并历史：并进了哪个客户。 */
  merged_into: MergeRef | null
  /** 最终去处：A→B→C 时的 C。与 `merged_into` 相同时后端不下发（为 null）。 */
  final_target: MergeRef | null
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
