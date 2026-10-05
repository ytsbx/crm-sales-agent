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
  /** 签单归属（文档 :61）：与 owner 不同时说明这张单的业绩算谁 */
  sales_owner_id?: number | null
  sales_owner_name?: string | null
  currency: string
  total_amount: number
  received_amount: number
  unreceived_amount: number
  status: string
  status_label: string
  erp_order_id?: string | null
  delivery_date?: string | null
  delivery_kind?: 'shipping' | 'arrival' | null
  transit_days?: number | null
  plan_offsets?: Record<string, number> | null
  shipment_date?: string | null
  payment_terms?: string | null
  remark?: string | null
  item_count: number
  created_at: string
}

export interface OrderItem {
  id: number
  order_id: number
  sku_id: number | null
  inquiry_id?: number | null
  inquiry_no_snapshot?: string | null
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
  voucher_file_id?: number | null
  voucher_file_name?: string | null
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
  opportunity_id?: number
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

export function uploadPaymentVoucher(id: number, file: File) {
  const form = new FormData()
  form.append('file', file)
  return api.upload<{ voucher_file_id: number; voucher_file_name: string }>(
    `/payments/${id}/voucher`,
    form,
  )
}

export function deletePaymentVoucher(id: number) {
  return api.delete<null>(`/payments/${id}/voucher`)
}

export function downloadPaymentVoucher(id: number, filename: string) {
  return api.download(`/payments/${id}/voucher`, filename)
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
  status: 'done' | 'overdue' | 'pending' | 'skipped'
  status_label: string
  skip_reason?: string | null
  skipped_by?: number | null
  skipped_at?: string | null
  // 方案 :103 要求节点记录责任人、来源证据、逾期原因
  owner_id?: number | null
  evidence?: string | null
  overdue_reason?: string | null
  remark?: string | null
}

export function listOrderMilestones(orderId: number) {
  return api.get<OrderMilestoneRow[]>(`/orders/${orderId}/milestones`)
}

export function updateOrderMilestone(
  orderId: number,
  milestoneId: number,
  payload: {
    skipped?: boolean
    skip_reason?: string | null
    planned_date?: string | null
    actual_date?: string | null
    owner_id?: number | null
    evidence?: string | null
    overdue_reason?: string | null
    remark?: string | null
  },
) {
  return api.patch<OrderMilestoneRow>(`/orders/${orderId}/milestones/${milestoneId}`, payload)
}

export function replanOrderMilestones(orderId: number) {
  return api.post<{ changed: number }>(`/orders/${orderId}/milestones/replan`)
}

// ---- 发货批次（§3.5/场景13：分批发货，首批不结束整单）----

export interface ShipmentBatchRow {
  id: number
  batch_no: number
  status: string
  status_label: string
  planned_date?: string | null
  actual_ship_date?: string | null
  logistics_company?: string | null
  tracking_no?: string | null
  remark?: string | null
  items: Array<{ order_item_id: number; sku: string | null; planned_qty: number; shipped_qty: number }>
}

export interface ShipmentOverview {
  items: Array<{
    order_item_id: number
    sku: string
    specification?: string | null
    ordered: number
    planned: number
    shipped: number
    remaining: number
    unplanned: number
  }>
  batches: ShipmentBatchRow[]
  summary: { ordered: number; planned: number; shipped: number; remaining: number; all_shipped: boolean }
}

export function listOrderShipments(orderId: number) {
  return api.get<ShipmentOverview>(`/orders/${orderId}/shipments`)
}

export function createOrderShipment(
  orderId: number,
  payload: { planned_date?: string | null; remark?: string | null; items: Array<{ order_item_id: number; planned_qty: number }> },
) {
  return api.post<{ batch_id: number; batch_no: number }>(`/orders/${orderId}/shipments`, payload)
}

export function shipOrderShipment(
  orderId: number,
  batchId: number,
  payload: {
    actual_ship_date?: string | null
    logistics_company?: string | null
    tracking_no?: string | null
    remark?: string | null
    items?: Array<{ order_item_id: number; shipped_qty: number }> | null
  },
) {
  return api.post<{ order_status: string; summary: ShipmentOverview['summary'] }>(
    `/orders/${orderId}/shipments/${batchId}/ship`,
    payload,
  )
}

export function cancelOrderShipment(orderId: number, batchId: number) {
  return api.delete<null>(`/orders/${orderId}/shipments/${batchId}`)
}

// ---------------------------------------------------------------- 交期变更（方案 :105）

export interface DeliveryPlanningInput {
  new_delivery_date: string
  delivery_kind: 'shipping' | 'arrival'
  transit_days: number
  plan_offsets: Record<string, number>
  reason?: string | null
}
export interface PlanningSnapshot {
  delivery_date: string | null
  delivery_kind: 'shipping' | 'arrival' | null
  transit_days: number | null
  plan_offsets: Record<string, number> | null
}
export interface ScheduleChangeAffected {
  planning?: { before: PlanningSnapshot; after: PlanningSnapshot }
  new_shipment_date?: string
  old_shipment_date?: string | null
  nodes: { node: string; label: string; before: string | null; after: string | null }[]
  batches: { batch_id: number; batch_no: number; before: string | null; after: string | null }[]
  applied?: ScheduleChangeAffected | null
}

export interface ScheduleChangeRow {
  id: number
  order_id: number
  old_delivery_date: string | null
  new_delivery_date: string | null
  reason: string | null
  status: 'pending' | 'confirmed' | string
  status_label: string
  owner_id: number | null
  owner_name: string | null
  confirmed_by: number | null
  confirmed_by_name: string | null
  confirmed_at: string | null
  confirm_remark: string | null
  affected: ScheduleChangeAffected | null
  created_at: string | null
}

export function previewScheduleChange(orderId: number, payload: DeliveryPlanningInput) {
  return api.post<{
    new_shipment_date: string
    order_id: number
    old_delivery_date: string | null
    new_delivery_date: string
    shift_days: number | null
    nodes: ScheduleChangeAffected['nodes']
    batches: ScheduleChangeAffected['batches']
  }>(`/orders/${orderId}/schedule-changes/preview`, payload)
}

export function createScheduleChange(
  orderId: number,
  payload: DeliveryPlanningInput,
) {
  return api.post<ScheduleChangeRow>(`/orders/${orderId}/schedule-changes`, payload)
}

export function listScheduleChanges(orderId: number) {
  return api.get<ScheduleChangeRow[]>(`/orders/${orderId}/schedule-changes`)
}

export function confirmScheduleChange(orderId: number, changeId: number, remark?: string) {
  return api.post<ScheduleChangeRow>(
    `/orders/${orderId}/schedule-changes/${changeId}/confirm`,
    { remark: remark ?? null },
  )
}

/** 作废待确认的交期变更单。没有它，一张没人确认的单会永久堵死后续变更。 */
export function cancelScheduleChange(orderId: number, changeId: number, reason?: string) {
  return api.post<ScheduleChangeRow>(
    `/orders/${orderId}/schedule-changes/${changeId}/cancel`,
    { reason: reason ?? null },
  )
}

export interface OrderDraftLine {
  id: number; source_item_id: number; name: string; quantity: number; unit_price: number | null
  specification?: string | null; remark?: string | null
  source_snapshot: { source_item_id: number; original_quantity: string | null; specification?: string | null; remark?: string | null; unit_price?: string | null }
}
export interface OrderDraft {
  id: number; customer_id: number; opportunity_id?: number | null; status: string; revision: number
  source_context: { type: string; id: number; no: string; version: number; quote_id?: number }
  currency: string; delivery_date?: string | null; payment_terms?: string | null; remark?: string | null
  order_id?: number | null; items: OrderDraftLine[]
}
export function listOrderDrafts(query: { opportunity_id?: number; page?: number; page_size?: number } = {}) { return api.get<PageResult<OrderDraft>>('/order-drafts', query) }
export function getOrderDraft(id: number) { return api.get<OrderDraft>(`/order-drafts/${id}`) }
export function getOrderDraftSource(source: { quote_version_id?: number; inquiry_id?: number }) { return api.get<import('./sample').SampleSourcePreview>('/order-drafts/source', source) }
export function createOrderDraft(payload: Record<string, unknown>) { return api.post<OrderDraft>('/order-drafts', payload) }
export function updateOrderDraft(id: number, payload: Record<string, unknown>) { return api.patch<OrderDraft>(`/order-drafts/${id}`, payload) }
export function confirmOrderDraft(id: number, revision: number, versionId: number) { return api.post<{ order_id: number; order_no: string }>(`/order-drafts/${id}/confirm`, { revision, quote_version_id: versionId }) }
export function generateOrderDraftDocument(id: number) { return api.post<import('./bizdoc').BizDocRow>(`/order-drafts/${id}/documents`, {}) }
