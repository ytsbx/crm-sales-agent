import { api } from './client'
import type { Contact, Customer, PageResult } from '../types'

export interface CustomerQuery {
  keyword?: string
  level?: string
  status?: string
  source?: string
  owner_id?: number
  pool_status?: string
  page?: number
  page_size?: number
}

export interface CustomerPayload {
  name: string
  short_name?: string | null
  region?: string | null
  address?: string | null
  source?: string | null
  level?: string | null
  remark?: string | null
}

export function listCustomers(query: CustomerQuery) {
  return api.get<PageResult<Customer>>('/customers', query)
}

export function getCustomer(id: number) {
  return api.get<Customer>(`/customers/${id}`)
}

export function createCustomer(payload: CustomerPayload) {
  return api.post<Customer>('/customers', payload)
}

export function updateCustomer(id: number, payload: Partial<CustomerPayload>) {
  return api.patch<Customer>(`/customers/${id}`, payload)
}

export function listContacts(customerId: number) {
  return api.get<Contact[]>(`/customers/${customerId}/contacts`)
}

export function createContact(customerId: number, payload: Partial<Contact>) {
  return api.post<Contact>(`/customers/${customerId}/contacts`, payload)
}

export function transferCustomer(customerId: number, payload: { owner_id: number | null; reason?: string }) {
  return api.post<Customer>(`/customers/${customerId}/transfer`, payload)
}

export function releaseCustomerToPool(customerId: number) {
  return api.post<Customer>(`/customers/${customerId}/release-to-pool`)
}

export function claimCustomer(customerId: number) {
  return api.post<Customer>(`/customers/${customerId}/claim`)
}

// ---------------------------------------------------------------- 标签与合并
// 对应 03-API §7 里此前缺失的路径

export interface TagRow {
  id: number
  name: string
  type: string
  status: string
  sort_no: number
}

export interface DuplicateMatch {
  id: number
  name: string
  level?: string | null
  region?: string | null
  owner_name?: string | null
  score: number
  reasons: string[]
}

export function listTags(onlyActive?: boolean) {
  return api.get<TagRow[]>('/tags', onlyActive === undefined ? undefined : { only_active: onlyActive })
}

export function createTag(payload: { name: string; type?: string; sort_no?: number }) {
  return api.post<TagRow>('/tags', payload)
}

export function updateTag(id: number, payload: Partial<TagRow>) {
  return api.patch<TagRow>(`/tags/${id}`, payload)
}

export function deleteTag(id: number) {
  return api.delete<null>(`/tags/${id}`)
}

export function attachCustomerTags(customerId: number, tagIds: number[]) {
  return api.post<{ added: number }>(`/customers/${customerId}/tags`, { tag_ids: tagIds })
}

export function detachCustomerTag(customerId: number, tagId: number) {
  return api.delete<null>(`/customers/${customerId}/tags/${tagId}`)
}

export function batchTagCustomers(payload: {
  customer_ids: number[]
  tag_ids: number[]
  mode?: 'add' | 'replace' | 'remove'
}) {
  return api.post<{ affected: number }>('/customers/batch-tag', payload)
}

export function batchTransferCustomers(payload: {
  customer_ids: number[]
  owner_id: number | null
  reason?: string
}) {
  return api.post<{ affected: number }>('/customers/batch-transfer', payload)
}

export function deduplicateCustomers(payload: {
  customer_id?: number
  name?: string
  mobile?: string
  tax_no?: string
  domain?: string
  address?: string
}) {
  return api.post<{ matches: DuplicateMatch[]; count: number }>('/customers/deduplicate', payload)
}

export function mergeCustomers(payload: {
  source_customer_id: number
  target_customer_id: number
  reason?: string
}) {
  return api.post<{
    target_customer_id: number
    source_customer_id: number
    moved: Record<string, number>
    merge_log_id: number
  }>('/customers/merge', payload)
}

export function listCustomerMergeLogs(customerId: number) {
  return api.get<
    {
      id: number
      source_customer_id: number
      target_customer_id: number
      operator_id?: number | null
      moved?: Record<string, number> | null
      reason?: string | null
      created_at?: string | null
    }[]
  >(`/customers/${customerId}/merge-logs`)
}
