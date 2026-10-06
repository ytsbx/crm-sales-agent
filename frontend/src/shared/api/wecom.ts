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
  /** 逐项接手人：键 `"{kind}:{business_id}"`，不指定的项跟 takeover_user_id */
  item_assignees?: Record<string, number>
}) {
  return api.post<WeComSyncJob>('/integrations/wecom/transfer', payload)
}

/** 交接清单里的一项（预览与执行读的是同一份数据）。 */
export interface WeComTransferScopeItem {
  kind: string
  business_id: number
  label: string
  status: string
  /** 不能交接时的原因（撞单争议冻结）；能交接则为空 */
  blocked_reason?: string | null
}

export interface WeComTransferPreview {
  handover: { id: number; name: string }
  takeover: { id: number; name: string }
  sections: {
    kind: string
    label: string
    items: WeComTransferScopeItem[]
  }[]
  totals: Record<string, number>
  total: number
  /** 这次交接动不了的项（撞单争议冻结） */
  blocked: WeComTransferScopeItem[]
  sample_count: number
  note: string
}

export function getWeComTransferPreview(handoverUserId: number, takeoverUserId: number) {
  return api.get<WeComTransferPreview>('/integrations/wecom/transfer-preview', {
    handover_user_id: handoverUserId,
    takeover_user_id: takeoverUserId,
  })
}

/** 交接的逐项结果：CRM 侧与企微侧分别记状态。 */
export interface WeComTransferItem {
  id: number
  job_id: number
  kind: string
  kind_label: string
  business_id?: number | null
  label?: string | null
  from_owner_name?: string | null
  to_owner_name?: string | null
  crm_status: string
  crm_status_label: string
  crm_error?: string | null
  wecom_status: string
  wecom_status_label: string
  wecom_error?: string | null
  attempts: number
  updated_at?: string | null
}

export function listWeComTransferItems(
  jobId: number,
  query: { kind?: string; pending_only?: boolean; page?: number; page_size?: number },
) {
  return api.get<PageResult<WeComTransferItem>>(
    `/integrations/wecom/transfer/${jobId}/items`,
    query,
  )
}

/** 按逐项状态重试没办完的项（已完成的外部转接不会重发）。 */
/**
 * 按逐项状态重试没办完的项。
 *
 * 返回里带上重算后的**任务汇总**（返修单第六批第 11 条）：重试会改动
 * 成功数/失败数/剩余数，页面要跟着更新，否则会一边显示"还剩 0 项"、
 * 一边挂着旧的失败数，两边对不上。
 */
export function retryWeComTransfer(jobId: number) {
  return api.post<{
    job_id: number
    retried: number
    remaining: number
    succeeded: number
    failed: number
  }>(`/integrations/wecom/transfer/${jobId}/retry`)
}

export function listWeComSyncJobs(query: {
  job_type?: string
  status?: string
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<WeComSyncJob>>('/integrations/wecom/sync-jobs', query)
}
