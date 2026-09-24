import { api } from './client'
import type { PageResult } from '../types'

export interface Lead {
  id: number
  name: string
  company_name?: string | null
  contact_name?: string | null
  mobile?: string | null
  email?: string | null
  source?: string | null
  source_detail?: string | null
  country?: string | null
  region?: string | null
  status: string
  status_label: string
  owner_id?: number | null
  owner_name?: string | null
  converted_customer_id?: number | null
  converted_contact_id?: number | null
  converted_opportunity_id?: number | null
  invalid_reason?: string | null
  remark?: string | null
  last_followup_at?: string | null
  created_at: string
}

export interface DuplicateCandidate {
  id: number
  name: string
  level?: string | null
  region?: string | null
  owner_id?: number | null
  pool_status: string
}

export function listLeads(query: {
  keyword?: string
  status?: string
  source?: string
  owner_id?: number
  unassigned?: boolean
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<Lead>>('/leads', query)
}

export function createLead(payload: Record<string, unknown>) {
  return api.post<Lead>('/leads', payload)
}

export function getLead(id: number) {
  return api.get<Lead>(`/leads/${id}`)
}

export function updateLead(id: number, payload: Record<string, unknown>) {
  return api.patch<Lead>(`/leads/${id}`, payload)
}

export function assignLead(id: number, ownerId: number | null, reason?: string) {
  return api.post<Lead>(`/leads/${id}/assign`, { owner_id: ownerId, reason })
}

export function claimLead(id: number) {
  return api.post<Lead>(`/leads/${id}/claim`)
}

export function releaseLead(id: number) {
  return api.post<Lead>(`/leads/${id}/release`)
}

export function discardLead(id: number, reason: string) {
  return api.post<null>(`/leads/${id}/discard`, { reason })
}

export function deduplicateLead(id: number) {
  return api.post<{ candidates: DuplicateCandidate[] }>(`/leads/${id}/deduplicate`)
}

export function convertLead(id: number, payload: Record<string, unknown>) {
  return api.post<{
    customer_id: number
    contact_id: number | null
    opportunity_id: number | null
  }>(`/leads/${id}/convert`, payload)
}
