import { api } from './client'
import type { PageResult } from '../types'

/** 企微集成（03-API §10）。 */

export interface WeComReadiness {
  configured: {
    corp_id: boolean
    agent_id: boolean
    contact_secret: boolean
    external_contact_secret: boolean
    callback: boolean
  }
  can_sync_contact: boolean
  can_sync_external: boolean
  counts: {
    wecom_users: number
    external_contacts: number
    follow_relations: number
    unbound_contacts: number
    unmatched_users: number
  }
}

export interface WeComFollower {
  wecom_userid: string
  add_time?: string | null
  add_way?: string | null
  remark?: string | null
  status: string
}

export interface WeComUnboundContact {
  id: number
  external_userid: string
  name?: string | null
  corp_name?: string | null
  avatar?: string | null
  type?: string | null
  normalize_status: string
  normalize_status_label: string
  last_sync_at?: string | null
  followers: WeComFollower[]
}

export interface WeComCandidate {
  id: number
  name: string
  level?: string | null
  region?: string | null
  owner_id?: number | null
  pool_status?: string | null
  score: number
  reasons: string[]
}

export interface WeComCandidates {
  contact: {
    id: number
    external_userid: string
    name?: string | null
    corp_name?: string | null
    avatar?: string | null
    normalize_status: string
  }
  candidates: WeComCandidate[]
}

export interface WeComSyncJob {
  id: number
  job_type: string
  job_type_label: string
  status: string
  status_label: string
  operator_id?: number | null
  started_at: string
  finished_at?: string | null
  success_count: number
  fail_count: number
  error_message?: string | null
  detail?: Record<string, unknown> | null
}

export function getWeComReadiness() {
  return api.get<WeComReadiness>('/integrations/wecom/readiness')
}

export function syncWeComDepartments() {
  return api.post<WeComSyncJob>('/integrations/wecom/sync-departments')
}

export function syncWeComUsers() {
  return api.post<WeComSyncJob>('/integrations/wecom/sync-users')
}

export function syncWeComExternalContacts() {
  return api.post<WeComSyncJob>('/integrations/wecom/sync-external-contacts')
}

export function syncWeComFollowRelations() {
  return api.post<WeComSyncJob>('/integrations/wecom/sync-follow-relations')
}

export function listUnboundContacts(query: { keyword?: string; page?: number; page_size?: number }) {
  return api.get<PageResult<WeComUnboundContact>>('/integrations/wecom/unbound-contacts', query)
}

export function getUnboundCandidates(contactId: number) {
  return api.get<WeComCandidates>(
    `/integrations/wecom/unbound-contacts/${contactId}/candidates`,
  )
}

export function bindUnboundContact(
  contactId: number,
  payload: { customer_id: number; is_primary?: boolean | null },
) {
  return api.post<{ contact_id: number; customer_id: number; customer_name: string }>(
    `/integrations/wecom/unbound-contacts/${contactId}/bind-customer`,
    payload,
  )
}

export function createCustomerFromContact(
  contactId: number,
  payload: { name: string; region?: string | null; level?: string | null; remark?: string | null },
) {
  return api.post<{ customer_id: number; customer_name: string; contact_id: number }>(
    `/integrations/wecom/unbound-contacts/${contactId}/create-customer`,
    payload,
  )
}

export function ignoreUnboundContact(contactId: number, reason?: string) {
  return api.post<{ id: number; normalize_status: string }>(
    `/integrations/wecom/unbound-contacts/${contactId}/ignore`,
    { reason },
  )
}

export function transferWeComRelations(payload: {
  handover_user_id: number
  takeover_user_id: number
  transfer_wecom: boolean
}) {
  return api.post<WeComSyncJob>('/integrations/wecom/transfer', payload)
}

export function listWeComSyncJobs(query: {
  job_type?: string
  status?: string
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<WeComSyncJob>>('/integrations/wecom/sync-jobs', query)
}
