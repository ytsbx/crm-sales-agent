import { api } from './client'
import type { PageResult } from '../types'

export interface Order {
  id: number
  order_no: string
  customer_id: number
  customer_name?: string | null
  opportunity_id?: number | null
  quote_id?: number | null
  quote_version_id?: number | null
  owner_id?: number | null
  owner_name?: string | null
  total_amount: number
  received_amount: number
  unreceived_amount: number
  status: string
  status_label: string
  erp_order_id?: string | null
  delivery_date?: string | null
  payment_terms?: string | null
  remark?: string | null
  item_count: number
  created_at: string
}

export interface OrderItem {
  id: number
  order_id: number
  sku_id: number
  sku_code?: string | null
  sku_snapshot?: string | null
  specification?: string | null
  quantity: number
  unit_price: number
  amount: number
  remark?: string | null
}

export interface OrderStatusRow {
  id: number
  old_status?: string | null
  old_status_label?: string | null
  new_status: string
  new_status_label: string
  source: string
  operator_name?: string | null
  remark?: string | null
  created_at: string
}

export interface Receivable {
  id: number
  order_id: number
  order_no?: string | null
  plan_name: string
  due_date: string
  amount: number
  received_amount: number
  remaining_amount: number
  status: string
  status_label: string
  remark?: string | null
}

export interface Payment {
  id: number
  order_id: number
  order_no?: string | null
  receivable_plan_id?: number | null
  plan_name?: string | null
  received_date: string
  received_amount: number
  payment_method?: string | null
  voucher_note?: string | null
  status: string
  status_label: string
  confirmed_by_name?: string | null
  confirmed_at?: string | null
  created_at: string
}

export interface FinanceSummary {
  order_amount: number
  planned_amount: number
  received_amount: number
  pending_confirm_amount: number
  unreceived_amount: number
  plan_count: number
  overdue_count: number
}

export function listOrders(query: {
  keyword?: string
  status?: string
  customer_id?: number
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<Order>>('/orders', query)
}

export function getOrder(id: number) {
  return api.get<Order>(`/orders/${id}`)
}

export function convertToOrder(versionId: number, payload: Record<string, unknown> = {}) {
  return api.post<{ order_id: number; order_no: string }>(
    `/quote-versions/${versionId}/convert-to-order`,
    payload,
  )
}

export function listOrderItems(orderId: number) {
  return api.get<OrderItem[]>(`/orders/${orderId}/items`)
}

export function listOrderStatusHistory(orderId: number) {
  return api.get<OrderStatusRow[]>(`/orders/${orderId}/status-history`)
}

export function changeOrderStatus(orderId: number, status: string, remark?: string) {
  return api.post<Order>(`/orders/${orderId}/status`, { status, remark })
}

export function cancelOrder(orderId: number) {
  return api.post<Order>(`/orders/${orderId}/cancel`)
}

export function syncErp(orderId: number) {
  return api.post<{
    pushed: boolean
    already_synced?: boolean
    erp_order_id?: string | null
    message?: string
  }>(`/orders/${orderId}/sync-erp`)
}

/** 拉取并回写履约状态（API §28 GET /integrations/erp/orders/{id}/status）。 */
export function refreshErpStatus(orderId: number) {
  return api.get<{
    status: string
    status_label: string
    changed: boolean
    raw_status?: string | null
    shipped_at?: string | null
  }>(`/integrations/erp/orders/${orderId}/status`)
}

export function repurchase(orderId: number) {
  return api.post<{ opportunity_id: number }>(`/orders/${orderId}/repurchase`)
}

export function listOrderReceivables(orderId: number) {
  return api.get<Receivable[]>(`/orders/${orderId}/receivables`)
}

export function generateReceivables(orderId: number, payload: Record<string, unknown>) {
  return api.post<Receivable[]>(`/orders/${orderId}/receivables/generate`, payload)
}

export function createReceivable(orderId: number, payload: Record<string, unknown>) {
  return api.post<Receivable>(`/orders/${orderId}/receivables`, payload)
}

export function listOrderPayments(orderId: number) {
  return api.get<Payment[]>(`/orders/${orderId}/payments`)
}

export function orderFinanceSummary(orderId: number) {
  return api.get<FinanceSummary>(`/orders/${orderId}/finance-summary`)
}

export function listReceivables(query: { status?: string; page?: number; page_size?: number }) {
  return api.get<PageResult<Receivable>>('/receivables', query)
}

export function listPayments(query: { status?: string; page?: number; page_size?: number }) {
  return api.get<PageResult<Payment>>('/payments', query)
}

export function createPayment(payload: Record<string, unknown>) {
  return api.post<Payment>('/payments', payload)
}

export function confirmPayment(id: number, comment?: string) {
  return api.post<Payment>(`/payments/${id}/confirm`, { comment })
}

export function rejectPayment(id: number, comment?: string) {
  return api.post<Payment>(`/payments/${id}/reject`, { comment })
}
