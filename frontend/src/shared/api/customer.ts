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
