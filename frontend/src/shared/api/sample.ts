import { api } from './client'
import type { PageResult } from '../types'

/** 样品（PRD §19 / 03-API §26）。 */

export interface SampleItem {
  id: number
  sample_request_id: number
  sku_id: number
  sku_code?: string | null
  sku_name?: string | null
  specification?: string | null
  unit?: string | null
  quantity: number
  remark?: string | null
}

export interface SampleShipment {
  id: number
  sample_request_id: number
  carrier?: string | null
  tracking_no?: string | null
  shipping_fee: number
  shipped_at?: string | null
  signed_at?: string | null
}

export interface SampleRequestRow {
  id: number
  opportunity_id?: number | null
  opportunity_title?: string | null
  customer_id?: number | null
  customer_name?: string | null
  contact_id?: number | null
  owner_id?: number | null
  owner_name?: string | null
  status: string
  status_label: string
  remark?: string | null
  reject_reason?: string | null
  requested_at?: string | null
  approved_at?: string | null
  shipped_at?: string | null
  signed_at?: string | null
  feedback?: string | null
  // ---- 生产打样资料（文档 §3.5）----
  purpose?: string | null
  craft?: string | null
  material?: string | null
  drawing_version?: string | null
  target_completion_date?: string | null
  acceptance_criteria?: string | null
  sample_fee?: number | null
  production_owner_id?: number | null
  made_at?: string | null
  // 客户确认与签收分开：签收是物流事实，确认是业务事实
  confirm_status: string
  confirm_status_label: string
  customer_confirmed_at?: string | null
  confirm_remark?: string | null
  created_at?: string | null
  items: SampleItem[]
  shipments: SampleShipment[]
}

export function listSamples(query: {
  keyword?: string
  status?: string
  customer_id?: number
  opportunity_id?: number
  owner_id?: number
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<SampleRequestRow>>('/samples', query)
}

export function getSample(id: number) {
  return api.get<SampleRequestRow>(`/samples/${id}`)
}

export function createSample(payload: Record<string, unknown>) {
  return api.post<SampleRequestRow>('/samples', payload)
}

export function updateSample(id: number, payload: Record<string, unknown>) {
  return api.patch<SampleRequestRow>(`/samples/${id}`, payload)
}

export function approveSample(id: number, approved: boolean, rejectReason?: string) {
  return api.post<SampleRequestRow>(`/samples/${id}/approve`, {
    approved,
    reject_reason: rejectReason,
  })
}

export function shipSample(id: number, payload: Record<string, unknown>) {
  return api.post<SampleRequestRow>(`/samples/${id}/ship`, payload)
}

export function signSample(id: number, signedAt?: string) {
  return api.post<SampleRequestRow>(`/samples/${id}/sign`, { signed_at: signedAt })
}

export function feedbackSample(id: number, feedback: string) {
  return api.post<SampleRequestRow>(`/samples/${id}/feedback`, { feedback })
}

/** 登记制作完成（文档 §3.5；CRM 管不到车间，这里只记事实、不当闸门）。 */
export function madeSample(id: number, remark?: string) {
  return api.post<SampleRequestRow>(`/samples/${id}/made`, { remark: remark ?? null })
}

/**
 * 登记客户确认结果。
 *
 * 后端规则：**必须先签收**才能确认——客户收到样品才谈得上接受。
 */
export function confirmSample(id: number, accepted: boolean, remark?: string) {
  return api.post<SampleRequestRow>(`/samples/${id}/confirm`, {
    accepted,
    remark: remark ?? null,
  })
}

export function listSampleItems(id: number) {
  return api.get<SampleItem[]>(`/samples/${id}/items`)
}

export function addSampleItem(id: number, payload: Record<string, unknown>) {
  return api.post<SampleRequestRow>(`/samples/${id}/items`, payload)
}
