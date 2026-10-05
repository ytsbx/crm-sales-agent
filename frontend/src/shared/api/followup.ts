import { api } from './client'
import type { PageResult } from '../types'

export interface FollowUp {
  id: number
  customer_id?: number | null
  contact_id?: number | null
  lead_id?: number | null
  opportunity_id?: number | null
  owner_id?: number | null
  owner_name?: string | null
  followup_type: string
  content: string
  customer_feedback?: string | null
  next_action?: string | null
  task_due_at?: string | null
  exemption_reason?: string | null
  next_task_id?: number | null
  created_at: string
}

export interface TimelineEvent {
  source?: { type: 'sample' | 'order' | 'quote' | 'opportunity'; id: number } | null
  kind: string
  title: string
  detail?: string | null
  operator_name: string
  at: string
}

export function listFollowups(query: {
  customer_id?: number
  opportunity_id?: number
  lead_id?: number
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<FollowUp>>('/followups', query)
}

export function createFollowUp(payload: Record<string, unknown>) {
  return api.post<{ followup: FollowUp; task_id: number | null }>('/followups', payload)
}

export function deleteFollowUp(id: number) {
  return api.delete<null>(`/followups/${id}`)
}

export function getCustomerTimeline(customerId: number) {
  return api.get<TimelineEvent[]>(`/customers/${customerId}/timeline`)
}

export function getOpportunityTimeline(opportunityId: number) {
  return api.get<TimelineEvent[]>(`/opportunities/${opportunityId}/timeline`)
}

export function getLeadTimeline(leadId: number) {
  return api.get<TimelineEvent[]>(`/leads/${leadId}/timeline`)
}
