import { api } from './client'
import type { PageResult } from '../types'

export interface DashboardSummary {
  todo_count: number
  overdue_task_count: number
  stale_customer_count: number
  open_opportunity_count: number
  open_opportunity_amount: number
  pending_approval_count: number
  month_won_amount: number
  month_received_amount: number
  pending_receivable_amount: number
  overdue_receivable_count: number
  open_lead_count: number
}

export interface TrendRow {
  month: string
  label: string
  order_amount: number
  received_amount: number
}

export interface ActivityRow {
  id: number
  operator: string
  title: string
  business_type?: string | null
  business_id?: number | null
  source: string
  at: string
}

export interface DashboardTask {
  id: number
  title: string
  priority: string
  due_at?: string | null
  opportunity_id?: number | null
  customer_id?: number | null
  lead_id?: number | null
  overdue: boolean
}

export interface RiskOpportunity {
  id: number
  title: string
  customer_name?: string | null
  stage_name?: string | null
  expected_amount: number
  expected_close_date?: string | null
  days_left?: number | null
  stale: boolean
  risk_reason: string
}

export interface FunnelRow {
  stage_id: number
  stage_name: string
  sequence: number
  count: number
  amount: number
}

export interface NameValue {
  name: string
  value: number
}

export interface QuoteStats {
  quote_count: number
  average_versions: number
  average_discount: number
  accept_rate: number
  approval_rate: number
  accepted_count: number
  approval_required_count: number
}

export interface CustomerStats {
  total: number
  new_this_month: number
  by_source: NameValue[]
  by_level: NameValue[]
}

export interface ProductStat {
  sku_code: string
  specification?: string | null
  product_name?: string | null
  quote_times: number
  quote_quantity: number
}

export interface SalesUserStat {
  user_id: number
  name: string
  customer_count: number
  opportunity_count: number
  quote_count: number
  order_amount: number
}

export interface ReceivableStats {
  plan_amount: number
  received_amount: number
  unreceived_amount: number
  overdue_count: number
  by_status: NameValue[]
}

export interface OpportunityStats {
  won_count: number
  loss_count: number
  win_rate: number
  loss_rate: number
  funnel: FunnelRow[]
}

export interface NotificationRow {
  id: number
  type: string
  title: string
  content?: string | null
  business_type?: string | null
  business_id?: number | null
  read: boolean
  created_at: string
}

export interface AuditLogRow {
  id: number
  operator_id?: number | null
  operator_name?: string | null
  source: string
  business_type?: string | null
  business_id?: number | null
  action: string
  before_data?: Record<string, unknown> | null
  after_data?: Record<string, unknown> | null
  ip?: string | null
  created_at: string
}

export function getDashboardSummary() {
  return api.get<DashboardSummary>('/dashboard/summary')
}

export function getDashboardTasks() {
  return api.get<DashboardTask[]>('/dashboard/tasks')
}

export function getDashboardRisks() {
  return api.get<RiskOpportunity[]>('/dashboard/risks')
}

export function getDashboardTrend(months = 6) {
  return api.get<TrendRow[]>('/dashboard/trend', { months })
}

export function getDashboardActivities(limit = 8) {
  return api.get<ActivityRow[]>('/dashboard/activities', { limit })
}

export function getOpportunityStats() {
  return api.get<OpportunityStats>('/analytics/opportunities')
}

export function getQuoteStats() {
  return api.get<QuoteStats>('/analytics/quotes')
}

export function getCustomerStats() {
  return api.get<CustomerStats>('/analytics/customers')
}

export function getProductStats() {
  return api.get<ProductStat[]>('/analytics/products')
}

export function getSalesUserStats() {
  return api.get<SalesUserStat[]>('/analytics/sales-users')
}

export function getReceivableStats() {
  return api.get<ReceivableStats>('/analytics/receivables')
}

export function getLossStats() {
  return api.get<NameValue[]>('/analytics/losses')
}

export function listNotifications(unreadOnly = false) {
  return api.get<PageResult<NotificationRow>>('/notifications', { unread_only: unreadOnly, page_size: 50 })
}

export function getUnreadCount() {
  return api.get<{ count: number }>('/notifications/unread-count')
}

export function markNotificationRead(id: number) {
  return api.post<null>(`/notifications/${id}/read`)
}

export function markAllNotificationsRead() {
  return api.post<null>('/notifications/read-all')
}

export function listAuditLogs(query: {
  business_type?: string
  action?: string
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<AuditLogRow>>('/audit-logs', query)
}
