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
  business_pending?: number
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
    business_events?: { processed: number; failed: number; notifications: number }
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
  /** 团队目标才有；有值时 user_name 是部门名 */
  department_id?: number | null
  user_name: string
  new_customer_target: number
  sales_target: number
  new_customer_actual: number
  /** 签单额（展示口径，**不进差额**） */
  sales_actual: number
  /** 发货额（展示口径，**不进差额**） */
  shipped_actual?: number
  /** 确认回款（**考核口径**，差额与达成率都用它） */
  received_actual?: number
  assess_actual?: number
  assess_basis?: string
  assess_basis_label?: string
  /** true = 这一期已结账，数字是存档值，不会因为后来的退货变小 */
  actual_frozen?: boolean
  /** 差额与达成率（场景17）：文档要求"显示确认口径下的实际、差额和来源" */
  sales_variance: number
  /** null = 零基期（没设目标），**不给百分比**——文档明确要求零基期不产生错误增长率 */
  sales_achievement: number | null
  achievement_note?: string | null
  new_customer_variance: number
  new_customer_achievement: number | null
  /** 复购（老客净额）目标与实际：口径见后端 analytics/target_bases.py，以前只存不算 */
  repeat_customer_target: number
  repeat_customer_actual: number
  repeat_customer_variance: number
  repeat_customer_achievement: number | null
  remark?: string | null
}

export interface SalesTargetList {
  year: number
  rows: SalesTargetRow[]
  assess_basis?: string
  assess_basis_label?: string
  /** 归属口径说明：本页是**业绩口径**（签单归属），与应收/账龄页的责任口径不同 */
  attribution_note?: string
  metric_basis_version?: string
  /** 已确认、却没记确认时间的回款笔数（这些钱没进任何金额，需要去补录） */
  missing_confirmed_at_count?: number
  missing_confirmed_at_note?: string | null
  sources?: Record<string, string>
  computed_at?: string
}

export function listSalesTargets(year: number) {
  return api.get<SalesTargetList>('/sales-targets', { year })
}

// ---------------------------------------------------------------- 结账 / 重算 / 可追溯明细
// 第三批 §4.1.5 后半 + §4.3。此前后端三个接口都有，前端**一个都没接通**——
// 于是"结账"这件事在界面上根本不存在，实绩永远在实时漂移（返工单第 4 条）。

/**
 * 结账：把这一期已经过完的实绩（**含构成它的明细**）抄一份存档。
 *
 * 之后这一期的数字不再随订单状态变——年底发奖金拿的那份报表，过几个月再看还是这个数。
 * 需要全公司范围权限（只冻自己看得到的那部分，等于把半张报表当账结了）。
 */
export function freezeSalesActuals(period: string, note?: string) {
  return api.post<{ period: string; rows: number; scopes: number; items: number }>(
    '/sales-targets/actuals/freeze',
    undefined,
    { params: { period, note } },
  )
}

/**
 * 重算已结账期间的实绩。**必须填原因**——改历史数字是要有人担责的事。
 *
 * 返回里的 `removed` 是"上一版有、这一版没有"被清掉的陈旧汇总行数：
 * 不报出来的话，事后没人知道某人的历史数字是被移除、还是从来没算过。
 */
export function refreezeSalesActuals(period: string, reason: string) {
  return api.post<{
    period: string
    rows: number
    removed: number
    items: number
    reason: string
  }>('/sales-targets/actuals/refreeze', undefined, { params: { period, reason } })
}

export interface DrilldownItem {
  record_type: string
  id: number
  label?: string | null
  owner_id?: number | null
  amount: number
  /** 已格式化的可读时间（后端统一格式化，前端直接显示） */
  date?: string | null
}

export interface DrilldownResult {
  period: string
  metric: string
  metric_label: string
  scope: string
  scope_user_ids?: number[] | null
  count: number
  total: number
  items: DrilldownItem[]
  truncated: boolean
  /** snapshot = 读的结账存档（不会变）；live = 实时算的 */
  source: 'snapshot' | 'live'
  actual_frozen: boolean
  metric_basis_version?: string
  sources?: string
  computed_at?: string
}

/** 可追溯明细（§4.3）：把某个指标的某期某作用域拆到具体单据。 */
export function getSalesTargetDrilldown(params: {
  period: string
  metric: string
  user_id?: number | null
  department_id?: number | null
}) {
  return api.get<DrilldownResult>('/sales-targets/drilldown', params)
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
  /** 复购（老客净额）目标，口径见后端 target_bases.py */
  repeat_customer_target?: number
  remark?: string | null
}) {
  return api.post<{ target_id: number }>('/sales-targets/upsert', payload)
}

/** 操作耗时埋点（场景18）：计时只能前端做，服务端只校验与聚合。 */
export function reportOperationTiming(payload: {
  operation: 'quote_from_inquiry' | 'sample_create'
  duration_ms: number
  business_type?: string | null
  business_id?: number | null
  typed_fields?: number
  rework_count?: number
}) {
  return api.post<{ id: number }>('/usage/timings', payload)
}

export function getOperationTimingSummary(days = 30) {
  return api.get<{
    days: number
    operations: { value: string; label: string }[]
    summary: {
      operation: string
      operation_label: string
      samples: number
      avg_ms: number | null
      median_ms: number | null
      p90_ms: number | null
      avg_typed_fields: number | null
      avg_rework_count: number | null
    }[]
    by_user: { user_id: number; user_name: string; samples: number; avg_ms: number }[]
    note: string
  }>('/usage/timings/summary', { days })
}
