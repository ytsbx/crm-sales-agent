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
  owner_id: number | null
  owner_name: string | null
  status: string
  status_label: string
  reviewer_id: number | null
  reviewed_at: string | null
  review_note: string | null
  converted_inquiry_id: number | null
  created_at: string
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

export function submitProductInsight(id: number) {
  return api.post<ProductInsightRow>(`/product-insights/${id}/submit`)
}

export function reviewProductInsight(id: number, payload: { approve: boolean; note?: string | null }) {
  return api.post<ProductInsightRow>(`/product-insights/${id}/review`, payload)
}

export function convertProductInsight(id: number) {
  return api.post<{ insight_id: number; inquiry_id: number }>(`/product-insights/${id}/convert`)
}

export function deleteProductInsight(id: number) {
  return api.delete<null>(`/product-insights/${id}`)
}
