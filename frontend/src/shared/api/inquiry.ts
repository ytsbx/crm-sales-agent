import { api } from './client'
import type { PageResult } from '../types'

export interface CustomInquiryRow {
  id: number
  /** 需求编号（场景09）：报价/打样明细引用它溯源，同一条需求的各版本共用一个号 */
  inquiry_no?: string | null
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
  /** 已被新版取代（取代是版本属性，不覆盖业务 status） */
  superseded_at?: string | null
  is_superseded?: boolean
  version_state_label?: string
  converted_sku_id?: number | null
  created_by?: number | null
  creator_name?: string | null
  created_at: string
}

export interface CustomInquiryPayload {
  title: string
  description?: string | null
  customer_id?: number | null
  opportunity_id?: number | null
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
  opportunity_id?: number
  customer_id?: number
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

/**
 * 从定制需求直接发起报价（§3.1/场景09）。
 *
 * 定制件投产前没有 SKU，报价中心按 SKU 选品选不到它，所以这里给一个
 * "填两个数就成单"的出口：核价成本 + 报价，其余（客户/商机/明细快照）系统接。
 */
export function createQuoteFromInquiry(
  id: number,
  payload: {
    unit_cost: number
    quoted_price: number
    quantity?: number | null
    item_name?: string | null
    valid_until?: string | null
  },
) {
  return api.post<{
    quote_id: number
    quote_no: string
    version_id: number
    opportunity_id: number
    inquiry_no: string | null
    quoted_price: number
    minimum_price: number | null
    approval_required: boolean
  }>(`/custom-inquiries/${id}/create-quote`, payload)
}
