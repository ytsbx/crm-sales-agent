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
