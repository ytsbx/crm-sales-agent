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

/** 手工建订单（API §27 POST /orders）。线下签约/补录历史单用；
 * 正常订单走 convertToOrder（报价版本转订单）。金额由后端按明细算，不用传。 */
export function createOrder(payload: {
  customer_id: number
  items: Array<{
    sku_id: number
    quantity: number
    unit_price: number
    specification?: string
    remark?: string
  }>
  opportunity_id?: number
  quote_id?: number
  owner_id?: number
  currency?: string
  delivery_date?: string
  payment_terms?: string
  remark?: string
}) {
  return api.post<{ order_id: number; order_no: string; total_amount: number }>('/orders', payload)
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

/** 拉取并回写履约状态（API §27 POST /orders/{id}/refresh-status）。
 *
 * 与 `GET /integrations/erp/orders/{id}/status` 同一实现；订单详情页用这个
 * "就地刷新"更自然。
 */
export function refreshStatus(orderId: number) {
  return api.post<{
    status: string
    status_label: string
    changed: boolean
    raw_status?: string | null
    shipped_at?: string | null
  }>(`/orders/${orderId}/refresh-status`)
}

/** @deprecated 用 refreshStatus() —— 文档定义的是 POST /orders/{id}/refresh-status。 */
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

/** 建应收节点（API §29 POST /receivables，order_id 放在 body 里）。 */
export function createReceivableStandalone(payload: Record<string, unknown>) {
  return api.post<Receivable>('/receivables', payload)
}

/** 单个应收节点（API §29 GET /receivables/{id}）。 */
export function getReceivable(id: number) {
  return api.get<Receivable>(`/receivables/${id}`)
}

/** 改应收节点 —— 部分更新，只传要改的字段（API §29 PATCH /receivables/{id}）。 */
export function updateReceivable(id: number, payload: Record<string, unknown>) {
  return api.patch<Receivable>(`/receivables/${id}`, payload)
}

/** 单个回款（API §30 GET /payments/{id}）。 */
export function getPayment(id: number) {
  return api.get<Payment>(`/payments/${id}`)
}

/** 改回款登记 —— 只有待确认的能改（API §30 PATCH /payments/{id}）。 */
export function updatePayment(id: number, payload: Record<string, unknown>) {
  return api.patch<Payment>(`/payments/${id}`, payload)
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

// ---------------------------------------------------------------- 跟单里程碑（模块⑤）

export interface OrderMilestoneRow {
  id: number
  node: string
  label: string
  planned_date: string | null
  actual_date: string | null
  status: 'done' | 'overdue' | 'pending'
  status_label: string
  remark?: string | null
}

export function listOrderMilestones(orderId: number) {
  return api.get<OrderMilestoneRow[]>(`/orders/${orderId}/milestones`)
}

export function updateOrderMilestone(
  orderId: number,
  milestoneId: number,
  payload: { planned_date?: string | null; actual_date?: string | null; remark?: string | null },
) {
  return api.patch<OrderMilestoneRow>(`/orders/${orderId}/milestones/${milestoneId}`, payload)
}

export function replanOrderMilestones(orderId: number) {
  return api.post<{ changed: number }>(`/orders/${orderId}/milestones/replan`)
}
