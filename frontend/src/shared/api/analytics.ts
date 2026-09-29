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
  /** PRD §23「活跃 / 沉睡 / 复购」 */
  active_count: number
  dormant_count: number
  repeat_customer_count: number
  active_days: number
  dormant_days: number
  by_source: NameValue[]
  by_level: NameValue[]
}

export interface ProductStat {
  sku_id?: number
  sku_code: string
  specification?: string | null
  product_name?: string | null
  /** PRD §23「询盘 / 报价 / 成交 / 失单 / 利润」 */
  inquiry_times: number
  quote_times: number
  quote_quantity: number
  won_times: number
  lost_times: number
  profit_amount: number
}

export interface SalesUserStat {
  user_id: number
  name: string
  customer_count: number
  opportunity_count: number
  quote_count: number
  order_amount: number
  followup_count: number
  received_amount: number
}

export interface ReceivableStats {
  plan_amount: number
  received_amount: number
  unreceived_amount: number
  overdue_count: number
  by_status: NameValue[]
}

export interface StageConversionRow {
  stage_id: number
  stage_name: string
  sequence: number
  reached_count: number
  conversion_from_previous: number | null
}

export interface OpportunityCycle {
  won_with_history: number
  average_days: number | null
  median_days?: number | null
  min_days: number | null
  max_days: number | null
}

export interface OpportunityStats {
  won_count: number
  loss_count: number
  win_rate: number
  loss_rate: number
  funnel: FunnelRow[]
  /** PRD §23「阶段转化 / 周期」 */
  stage_conversion?: StageConversionRow[]
  cycle?: OpportunityCycle
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
  /** 企微投递状态；null 表示这条没走企微渠道（不等于"发失败了"）。 */
  wecom_status?: string | null
  wecom_status_label?: string | null
  wecom_error?: string | null
  /** 试过几次（文档 §六 投递可靠性）。 */
  wecom_attempts?: number
  /** 下次自动重试时间；null 表示已停止自动重试，等人工补投。 */
  wecom_next_retry_at?: string | null
  /** 失败 / 未投递的可人工补投。 */
  can_redispatch?: boolean
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

// ---------------------------------------------------------------- 主管工作台
// PRD §4.2。数据范围是 self 的用户会拿到 is_team_view=false 且没有 members，
// 前端据此隐藏整块（不是拿到数据再靠前端隐藏）。

export interface TeamMemberRow {
  user_id: number
  name: string
  todo_count: number
  overdue_count: number
  won_amount_this_month: number
  stale_customer_count: number
}

export interface TeamSummary {
  is_team_view: boolean
  data_scope: string
  member_count?: number
  team_task_count?: number
  team_overdue_count?: number
  pending_approval_count?: number
  unassigned_customer_count?: number
  team_won_count_this_month?: number
  team_won_amount_this_month?: number
  members?: TeamMemberRow[]
  risky_opportunities?: RiskOpportunity[]
  funnel?: FunnelRow[]
}

export function getTeamSummary() {
  return api.get<TeamSummary>('/dashboard/team')
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

// ---------------------------------------------------------------- 新增分析接口
// 03-API §35 里列出、此前缺失的三个整接口

export interface LeadStats {
  total: number
  converted_count: number
  conversion_rate: number
  average_conversion_days: number | null
  by_source: NameValue[]
  by_status: NameValue[]
  invalid_reasons: NameValue[]
}

export interface PricingStats {
  item_count: number
  approval_required_count: number
  low_price_approval_rate: number
  average_discount_rate: number
  max_discount_rate: number
  average_quoted_price_by_level: {
    level: string
    item_count: number
    average_price: number
  }[]
}

export interface PaymentStats {
  plan_count: number
  overdue_node_count: number
  overdue_amount: number
  aging: NameValue[]
  by_payment_method: NameValue[]
}

export function getLeadStats() {
  return api.get<LeadStats>('/analytics/leads')
}

export function getPricingStats() {
  return api.get<PricingStats>('/analytics/pricing')
}

export function getPaymentStats() {
  return api.get<PaymentStats>('/analytics/payments')
}

// ---------------------------------------------------------------- 交期与履约
// 交期这一维此前在分析层是空的：跟单里程碑与发货批次**只写不读**。
//
// 口径（后端 delivery_stats 的注释是同一份说法）：
// - 准时 = 首批发货日期 ≤ 客户交期；一单多批次取最早的实际发货日；
// - **没填交期的已发货单不进准时率分母**——判不了，既不算准时也不算延迟，
//   单独报 undated_delivered_count，不能让它把准时率算虚；
// - 逾期节点与销售每天收到的逾期提醒同口径。

export interface DeliverySummary {
  open_order_count: number
  no_due_date_open_count: number
  due_soon_order_count: number
  risk_order_count: number
  overdue_node_count: number
  overdue_order_count: number
  delivered_order_count: number
  undated_delivered_count: number
  on_time_count: number
  late_count: number
  on_time_rate: number
  average_delay_days: number | null
  max_delay_days: number | null
  window_months: number
  due_soon_days: number
}

export interface DeliveryOwnerRow {
  owner_id: number | null
  owner_name: string
  order_count: number
  on_time_count: number
  late_count: number
  on_time_rate: number
  average_delay_days: number | null
}

export interface DeliveryTrendRow {
  month: string
  label: string
  on_time: number
  late: number
}

export interface DeliveryRiskOrder {
  order_id: number
  order_no: string
  customer_name?: string | null
  owner_id?: number | null
  owner_name?: string | null
  delivery_date: string
  days_overdue: number
  status: string
  status_label: string
}

export interface DeliveryStats {
  summary: DeliverySummary
  by_owner: DeliveryOwnerRow[]
  overdue_nodes: NameValue[]
  trend: DeliveryTrendRow[]
  risk_orders: DeliveryRiskOrder[]
}

export function getDeliveryStats(riskLimit = 20) {
  return api.get<DeliveryStats>('/analytics/delivery', { risk_limit: riskLimit })
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

/**
 * 补投单条通知（文档 §六「发送失败保留业务记录并重试通知」）。
 *
 * 后端走的是同一行通知、不重跑业务动作，所以不会被业务事件去重挡住。
 */
export function redispatchNotification(id: number) {
  return api.post<{
    id: number
    wecom_status: string | null
    wecom_error: string | null
    attempted: number
    sent: number
    skipped: number
    failed: number
  }>(`/notifications/${id}/redispatch`)
}

/** 投递失败概览：多少条没出去、其中多少条还会自动重试。 */
export interface DeliveryFailureSummary {
  pending: number
  sent: number
  failed: number
  skipped: number
  retrying: number
  max_attempts: number
  auto_retry_enabled: boolean
}

export function getDeliveryFailures() {
  return api.get<DeliveryFailureSummary>('/notifications/delivery-failures')
}

/** 批量补投失败与未投递的通知。 */
export function retryFailedNotifications(limit = 200) {
  return api.post<{
    requeued: number
    attempted: number
    sent: number
    skipped: number
    failed: number
  }>('/notifications/retry-failed', { limit })
}

export function listAuditLogs(query: {
  business_type?: string
  action?: string
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<AuditLogRow>>('/audit-logs', query)
}

// ---------------------------------------------------------------- 目标管理（模块⑧）

export interface SalesTargetRow {
  target_id: number | null
  period: string
  user_id: number | null
  user_name: string
  new_customer_target: number
  sales_target: number
  new_customer_actual: number
  sales_actual: number
  remark?: string | null
}

export function listSalesTargets(year: number) {
  return api.get<{ year: number; rows: SalesTargetRow[] }>('/sales-targets', { year })
}

// ---------------------------------------------------------------- 目标口径（场景17）
// 文档要求"销售额分别显示签单、发货、回款"且"系统分别保存计算口径与数据来源"。

export interface TargetBasisRow {
  month: string
  value: number
}

export interface SalesTargetBases {
  year: number
  signed: TargetBasisRow[]
  shipped: TargetBasisRow[]
  received: TargetBasisRow[]
  repeat_net: TargetBasisRow[]
  new_by_created: TargetBasisRow[]
  new_by_first_deal: TargetBasisRow[]
  /** 六个口径各自的算法说明——随结果返回，业务能看到"这个数字怎么来的" */
  basis_note: Record<string, string>
  source_note: string
}

export function getSalesTargetBases(year: number) {
  return api.get<SalesTargetBases>('/sales-targets/bases', { year })
}

export function upsertSalesTarget(payload: {
  period: string
  user_id: number | null
  /** 团队目标（文档 §六 :121）：填了就按部门设，与 user_id 互斥 */
  department_id?: number | null
  new_customer_target: number
  sales_target: number
  remark?: string | null
}) {
  return api.post<{ target_id: number }>('/sales-targets/upsert', payload)
}
