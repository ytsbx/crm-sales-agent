import { api } from './client'
import type { PageResult } from '../types'

export interface CustomInquiryRow {
  id: number
  title: string
  description?: string | null
  customer_id?: number | null
  customer_name?: string | null
  contact_id?: number | null
  opportunity_id?: number | null
  quantity?: number | null
  target_price?: number | null
  status: string
  status_label: string
  remark?: string | null
  // 修订链（§3.3）：第几版、链条首版、本版改了什么、投产后关联 SKU
  version?: number
  root_id?: number | null
  revision_note?: string | null
  converted_sku_id?: number | null
  created_by?: number | null
  creator_name?: string | null
  created_at: string
}

export interface CustomInquiryPayload {
  title: string
  description?: string | null
  customer_id?: number | null
  quantity?: number | null
  target_price?: number | null
  remark?: string | null
  status?: string
}

export interface InquiryStatusCount {
  status: string
  label: string
  count: number
}

export function listCustomInquiries(query: {
  status?: string
  keyword?: string
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<CustomInquiryRow>>('/custom-inquiries', query)
}

export function customInquiryStatusSummary() {
  return api.get<InquiryStatusCount[]>('/custom-inquiries/status-summary')
}

export function createCustomInquiry(payload: CustomInquiryPayload) {
  return api.post<CustomInquiryRow>('/custom-inquiries', payload)
}

export function updateCustomInquiry(id: number, payload: Partial<CustomInquiryPayload>) {
  return api.patch<CustomInquiryRow>(`/custom-inquiries/${id}`, payload)
}

export function deleteCustomInquiry(id: number) {
  return api.delete<null>(`/custom-inquiries/${id}`)
}

export function reviseCustomInquiry(
  id: number,
  payload: {
    revision_note?: string | null
    title?: string
    description?: string | null
    quantity?: number | null
    target_price?: number | null
    remark?: string | null
  },
) {
  return api.post<CustomInquiryRow>(`/custom-inquiries/${id}/revise`, payload)
}

export function customInquiryHistory(id: number) {
  return api.get<CustomInquiryRow[]>(`/custom-inquiries/${id}/history`)
}
