import { api } from './client'
import type { PageResult } from '../types'

export interface ProductInsightRow {
  id: number
  title: string
  source: string | null
  target_customer: string | null
  direction: string | null
  selling_points: string | null
  price_assumption: number | null
  conclusion: string | null
  /** 参考图 URL 列表（第五批 §6.1(6)：字段一直有，四个环节都没打通） */
  images: string[] | null
  owner_id: number | null
  owner_name: string | null
  status: string
  status_label: string
  reviewer_id: number | null
  reviewed_at: string | null
  review_note: string | null
  /** 审批轮次（第五批 §6.1(1)）：驳回重提、通过后改内容都会加一 */
  review_round: number
  converted_inquiry_id: number | null
  created_at: string
}

/** 转换结果（第五批 §6.1(3)）：带编号与来源类型，前端能直接给深链和提示。 */
export interface InsightConvertResult {
  insight_id: number
  inquiry_id: number
  inquiry_no?: string | null
  origin?: string
  origin_label?: string
  customer_id?: number | null
}

export function listProductInsights(query: {
  status?: string
  keyword?: string
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<ProductInsightRow>>('/product-insights', query)
}

export function createProductInsight(payload: Record<string, unknown>) {
  return api.post<ProductInsightRow>('/product-insights', payload)
}

export function updateProductInsight(id: number, payload: Record<string, unknown>) {
  return api.patch<ProductInsightRow>(`/product-insights/${id}`, payload)
}

export function submitProductInsight(id: number, requestKey?: string) {
  // request_key：弱网重试带同一个键时后端认幂等，不会多开一轮评审
  return api.post<ProductInsightRow>(`/product-insights/${id}/submit`, {
    request_key: requestKey,
  })
}

export function reviewProductInsight(
  id: number,
  payload: { approve: boolean; note?: string | null; request_key?: string },
) {
  return api.post<ProductInsightRow>(`/product-insights/${id}/review`, payload)
}

/**
 * 转成需求（第五批 §6.1(3)）。
 *
 * **填了客户** → 走正常的客户询价；
 * **不填客户** → 转成"内部开发需求"（明确标识来源，不冒充客户已提出采购需求）。
 * 传 `request_key` 时，网络重试不会建出第二条。
 */
export function convertProductInsight(
  id: number,
  payload: {
    customer_id?: number | null
    opportunity_id?: number | null
    contact_id?: number | null
    quantity?: number | null
    target_price?: number | null
    remark?: string | null
    request_key?: string
  } = {},
) {
  return api.post<InsightConvertResult>(`/product-insights/${id}/convert`, payload)
}

export function deleteProductInsight(id: number) {
  return api.delete<null>(`/product-insights/${id}`)
}
