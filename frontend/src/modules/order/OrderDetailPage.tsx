import { formatBeijingTime } from '../../shared/beijing-time'
import { useTabLabel } from '../../shared/hooks/useTabLabel'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ConfirmRequiredModal,
  isConfirmRequired,
  confirmMessageOf,
} from '../../shared/components/ConfirmRequiredModal'
import type { ReactNode } from 'react'
import { Banner, Button, DatePicker, Dropdown, Input, InputNumber, Modal, Popconfirm, Select, Table, Tabs, Tag, TextArea, Toast } from '@douyinfe/semi-ui'
import AllocationAdjustModal from './AllocationAdjustModal'
import FormLabel from '../../shared/components/FormLabel'
import { emptyText } from '../../shared/hooks/emptyText'
import GuardedButton from '../../shared/components/GuardedButton'
import { blockedReasons } from '../../shared/components/blocked-hint-reasons'
import type { TagTone } from '../../shared/types'

import {
  cancelOrder,
  restoreOrder,
  cancelOrderShipment,
  changeOrderStatus,
  confirmPayment,
  deleteReceivable,
  createOrderShipment,
  createPayment,
  generateReceivables,
  updateReceivable,
  getOrder,
  listOrderItems,
  listOrderMilestones,
  listOrderPayments,
  listOrderReceivables,
  listOrderShipments,
  listOrderStatusHistory,
  orderFinanceSummary,
  refreshStatus,
  confirmScheduleChange,
  cancelScheduleChange,
  createScheduleChange,
  listScheduleChanges,
  previewScheduleChange,
  repurchase,
  rescheduleOrderShipment,
  shipOrderShipment,
  syncErp,
  getErpReadiness,
  updateOrderMilestone,
  type OrderItem,
  type OrderMilestoneRow,
  type OrderStatusRow,
  type Payment,
  type Receivable,
  type ShipmentBatchRow,
  type ShipmentOverview,
  voidPayment,
} from '../../shared/api/order'
import {
  cancelReturn,
  createReturn,
  deleteReturnRefundAttachment,
  downloadReturnRefundAttachment,
  getOrderReturns,
  getReturnable,
  refundReturn,
  reviewReturn,
  settleReturn,
  updateReturnAmount,
  uploadReturnRefundAttachment,
  voidReturn,
  type ReturnableItem,
  type ReturnCreatePayload,
  type SalesReturn,
} from '../../shared/api/returns'
import UserSearchSelect from '../../shared/components/UserSearchSelect'
import { usePermissions } from '../../shared/hooks/permissions'
import { useAuthStore } from '../../shared/store/auth'
import DetailHeader from '../../shared/components/DetailHeader'
import KpiStrip, { type KpiItem } from '../../shared/components/KpiStrip'
import SectionCard from '../../shared/components/SectionCard'
import { IconFile, IconAIEditLevel1 } from '@douyinfe/semi-icons'
import ContractDocsPanel from '../../shared/components/ContractDocsPanel'
import AgentInsight from '../../shared/components/AgentInsight'
import BizDocPanel from '../../shared/components/BizDocPanel'
import { agentRiskAnalysis, type AnalysisEnvelope } from '../../shared/api/agent'
import { otherOption } from '../../shared/components/otherOption'
import { formatMoney, formatUnitPrice, toTwoDecimals } from '../../shared/components/money'

/** 收款方式。值是中文本身（后端 `payment_method` 是自由文本），
 *  选中「其他」后写的内容可以直接存回该字段。 */
const PAYMENT_METHODS = ['银行转账', '承兑汇票', '现金', '支票', '其他']
import PaymentVoucherControl from '../common/PaymentVoucherControl'
import { newRequestKey } from '../../shared/api/requestKey'
import { calendarDay, calendarDayOrToday } from '../../shared/calendar-day'
import { requiredQuantityText } from '../../shared/number-input'
import ReceivablePlanPicker, { defaultPlanRows, planBlockedReasons } from '../../shared/components/ReceivablePlanPicker'
import EmptyState from '../../shared/components/EmptyState'
import PageLoading from '../../shared/components/PageLoading'

/** 数量按十进制原文显示，沿用原来不显示尾零的格式。 */
function shipmentQuantity(value: number | string): string {
  return String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1')
}

function shipmentQuantityUnits(value: string): bigint {
  const [whole, fraction = ''] = value.replace(/^\+/, '').split('.')
  return BigInt(whole || '0') * 1000n + BigInt(fraction.padEnd(3, '0').slice(0, 3))
}

function shipmentDifference(planned: string, actual: string): string {
  const delta = shipmentQuantityUnits(planned) - shipmentQuantityUnits(actual)
  if (delta === 0n) return '与计划一致'
  const units = delta > 0n ? delta : -delta
  const amount = shipmentQuantity(`${units / 1000n}.${String(units % 1000n).padStart(3, '0')}`)
  return delta > 0n ? `少发 ${amount}，可重新排批` : `比计划多发 ${amount}`
}

/**
 * 整单交期结论（§9.8 复审）。
 *
 * 口径（业务 2026-10-07 拍板）：**全部发完**才给「最后一批 vs 交期」的最终结论；
 * 没发完就如实说"未完成 · 还剩几件"，不要把中间状态当结论 ——
 * 原来订购 10、已发 6、剩 4 件又没排新批次时，照样显示"-2 天"。
 */
function ShipmentVerdict({ summary }: { summary?: ShipmentOverview['summary'] }) {
  if (!summary) return null
  const base = summary.last_batch_vs_delivery_basis === 'shipping' ? '建议发货日' : '客户交期'
  const box: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 12,
    color: 'var(--crm-text-2)',
    padding: '8px 10px',
    marginBottom: 10,
    borderRadius: 6,
    background: 'var(--crm-fill-0, rgba(0,0,0,0.03))',
  }

  if (!summary.all_shipped) {
    return (
      <div style={box}>
        <Tag color="orange" style={{ flex: 'none' }}>未完成</Tag>
        <span>
          整单还没发完，还剩 <strong>{shipmentQuantity(summary.remaining_exact ?? summary.remaining)}</strong> 件未发
          {summary.remaining > 0 && summary.pending_batch_count === 0
            ? '（当前也没有待发批次，请先「排发货批次」）'
            : ''}
          。发完才会给出「最后一批 vs 交期」的最终结论。
        </span>
      </div>
    )
  }

  const days = summary.last_batch_vs_delivery_days
  return (
    <div style={box}>
      <Tag color="green" style={{ flex: 'none' }}>已发完</Tag>
      <span>
        {days === null ? (
          '这单没有可比的交期或发货日，算不出偏差。'
        ) : (
          <>
            按{base}
            {summary.suggested_ship_date ? `（${summary.suggested_ship_date}）` : ''}算，最后一批
            {days > 0 ? (
              <strong style={{ color: 'var(--crm-danger, #d45)' }}>晚了 {days} 天</strong>
            ) : days < 0 ? (
              <strong>提前了 {Math.abs(days)} 天</strong>
            ) : (
              <strong>正好准时</strong>
            )}
            。
          </>
        )}
      </span>
    </div>
  )
}

const TABS = [
  { tab: '订单明细', itemKey: 'items' },
  { tab: '跟单节点', itemKey: 'milestones' },
  { tab: '发货批次', itemKey: 'shipments' },
  { tab: '退货记录', itemKey: 'returns' },
  { tab: '履约状态', itemKey: 'status' },
  { tab: '应收计划', itemKey: 'receivables' },
  { tab: '回款记录', itemKey: 'payments' },
  // 场景12：下单文件按模板出图，来源报价与本次差异一起落快照
  { tab: '对外单据', itemKey: 'bizdocs' },
]

// 跟单里程碑状态（模块⑤）：完成/逾期/待办，逾期由计划日期与当天比较自动判定
const MILESTONE_TONE: Record<string, TagTone> = {
  done: 'green',
  overdue: 'red',
  pending: 'grey',
  skipped: 'grey',
}

const STATUS_TONE: Record<string, TagTone> = {
  pending: 'grey',
  in_production: 'blue',
  shipped: 'cyan',
  delivered: 'violet',
  completed: 'green',
  cancelled: 'grey',
}

const PLAN_TONE: Record<string, TagTone> = {
  pending: 'grey',
  partial: 'orange',
  paid: 'green',
  overdue: 'red',
}

const RETURN_TONE: Record<string, TagTone> = {
  reviewing: 'orange',
  pending: 'orange',
  completed: 'blue',
  refunded: 'green',
  settled: 'green',
  rejected: 'red',
  cancelled: 'grey',
  voided: 'grey',
}

function formatAttachmentSize(bytes?: number | null) {
  if (bytes === null || bytes === undefined) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export default function OrderDetailPage() {
  const params = useParams()
  const orderId = Number(params.id)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const currentUser = useAuthStore((state) => state.user)
  const canManage = can('order:manage')
  const canPayment = can('payment:manage')
  const canViewPayment = can('payment:view')
  const canReviewReturn = can('order:assign')

  const [activeKey, setActiveKey] = useState('items')
  const orderTabsRef = useRef<HTMLDivElement>(null)
  // 订单详情的几个 tab 都是宽表（回款记录 10 列、跟单节点 8 列）：1024 卡内只有 699，
  // 不做窄屏分档就会顶出横向滚动条、最后一两列整列被切（2026-11-19 实拍）。
  const [viewportWidth, setViewportWidth] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth))
  useEffect(() => {
    if (typeof window === 'undefined') return
    const onResize = () => setViewportWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const narrowScreen = viewportWidth < 1200
  const [statusVisible, setStatusVisible] = useState(false)
  const [targetStatus, setTargetStatus] = useState<string | null>(null)
  // 完成交付但还有未收款时，必须填原因（写进状态历史与审计）
  const [statusRemark, setStatusRemark] = useState('')
  const [orderMoreVisible, setOrderMoreVisible] = useState(false)
  //: 取消订单是**不可逆的终态动作**，单独一个确认弹窗把影响讲清楚
  //: （见下面「取消订单」那个 Modal）。
  const [cancelVisible, setCancelVisible] = useState(false)
  //: 「已完成」订单取消属于纠错，后端要求写理由；普通订单选填。
  const [cancelReason, setCancelReason] = useState('')
  const [generateVisible, setGenerateVisible] = useState(false)
  const [generateRows, setGenerateRows] = useState(defaultPlanRows)
  const [dueEdit, setDueEdit] = useState<{ plan: Receivable; date: Date | null } | null>(null)
  const newDueDay = calendarDay(dueEdit?.date)
  const [paymentTarget, setPaymentTarget] = useState<Receivable | null>(null)
  //: 已确认回款作废（2026-11-25）：普通 Modal，原因必填。
  const [voidTarget, setVoidTarget] = useState<Payment | null>(null)
  const [voidReason, setVoidReason] = useState('')
  //: 销售退货（2026-11-26）。登记 / 主管复核 / 财务改金额 / 退款登记 / 作废各有独立弹窗，
  //: 状态机不允许一步跳过去，弹窗也只做当前一步。
  const [returnVisible, setReturnVisible] = useState(false)
  const [returnReasonCode, setReturnReasonCode] = useState<'customer_rejected' | 'quality_issue' | 'wrong_item' | 'other'>('customer_rejected')
  const [returnReason, setReturnReason] = useState('')
  const [returnRemark, setReturnRemark] = useState('')
  const [returnDate, setReturnDate] = useState<Date | null>(new Date())
  const [returnQty, setReturnQty] = useState<Record<number, string>>({})
  const [returnRefundAmount, setReturnRefundAmount] = useState('')
  const [reviewTarget, setReviewTarget] = useState<SalesReturn | null>(null)
  const [reviewDecision, setReviewDecision] = useState<'approve' | 'reject'>('approve')
  const [reviewNote, setReviewNote] = useState('')
  const [amountTarget, setAmountTarget] = useState<SalesReturn | null>(null)
  const [amountDraft, setAmountDraft] = useState('')
  const [amountNote, setAmountNote] = useState('')
  const [refundTarget, setRefundTarget] = useState<SalesReturn | null>(null)
  const [refundReference, setRefundReference] = useState('')
  const [settleTotalDraft, setSettleTotalDraft] = useState('')
  const [settleOffsetDraft, setSettleOffsetDraft] = useState('0')
  const [settleNote, setSettleNote] = useState('')
  const refundAttachmentInputRef = useRef<HTMLInputElement>(null)
  const [cancelReturnTarget, setCancelReturnTarget] = useState<SalesReturn | null>(null)
  const [cancelReturnReason, setCancelReturnReason] = useState('')
  const [voidReturnTarget, setVoidReturnTarget] = useState<SalesReturn | null>(null)
  const [voidReturnReason, setVoidReturnReason] = useState('')
  //: 「调整回款分配」弹窗要调的那笔回款（第十批）。null = 关着。
  const [allocationPayment, setAllocationPayment] = useState<Payment | null>(null)
  const [paymentForm, setPaymentForm] = useState({ amount: '', date: new Date(), method: '银行转账' })

  // 收款方式的「其他」：选中后多一个输入框写具体方式（不填就保持「其他」）。
  const methodOption = otherOption({
    options: PAYMENT_METHODS,
    value: paymentForm.method,
    onChange: (v) => setPaymentForm({ ...paymentForm, method: v }),
  })
  // 登记回款的请求键（第七批 7.9）：**一份表单一把键**，打开表单时生成、
  // 成功后才换新的。弱网重试（服务端已建好、响应没回来，用户再点一次"登记"）
  // 带的是同一把键，后端据此只建一行；在提交时才现生成等于每点一次换一把键，
  // 服务端照样会建第二条 —— 那就不叫幂等。
  const paymentKeyRef = useRef('')
  // 跟单里程碑（模块⑤）：编辑弹窗状态
  const [milestoneEdit, setMilestoneEdit] = useState<OrderMilestoneRow | null>(null)
  //: 交期变更弹窗（方案 :105）：先预览受影响面，再生成变更单
  const [scheduleVisible, setScheduleVisible] = useState(false)
  const [scheduleForm, setScheduleForm] = useState({
    new_delivery_date: '', reason: '', delivery_kind: 'shipping' as 'shipping' | 'arrival', transit_days: 0,
    plan_offsets: { contract: 30, deposit: 28, pre_sample_sent: 20, pre_sample_confirmed: 15, first_shipment: 0, payment: -15 } as Record<string, number>,
  })
  const [schedulePreview, setSchedulePreview] = useState<{
    new_shipment_date: string
    shift_days: number | null
    nodes: { node: string; label: string; before: string | null; after: string | null }[]
    batches: { batch_id: number; batch_no: number; before: string | null; after: string | null }[]
    /** 旧交期没有可换算的发货日时，这几批要人工改计划日（系统不猜） */
    batches_to_confirm?: { batch_id: number; batch_no: number; planned_date: string }[]
  } | null>(null)
  const [milestoneForm, setMilestoneForm] = useState<{
    skipped: boolean
    skip_reason: string
    planned_date: string | null
    actual_date: string | null
    owner_id: number | null
    evidence: string
    overdue_reason: string
    remark: string
  }>({ skipped: false, skip_reason: '', planned_date: null, actual_date: null, owner_id: null, evidence: '', overdue_reason: '', remark: '' })

  // AI 回款风险分析（API §37 专用接口，需 agent:use）
  const [aiEnvelope, setAiEnvelope] = useState<AnalysisEnvelope | null>(null)
  const aiMutation = useMutation({
    mutationFn: () => agentRiskAnalysis({ order_id: orderId }),
    onSuccess: (data) => setAiEnvelope(data),
    onError: (error: Error) => Toast.error(error.message),
  })

  const orderQuery = useQuery({
    queryKey: ['order', orderId],
    queryFn: () => getOrder(orderId),
    enabled: Number.isFinite(orderId),
  })
  // 页签标题用订单号，避免多个「订单详情」分不清
  useTabLabel(orderQuery.data?.order_no)
  const itemsQuery = useQuery({
    queryKey: ['order-items', orderId],
    queryFn: () => listOrderItems(orderId),
    enabled: Number.isFinite(orderId),
  })
  // 跟单里程碑（模块⑤）：首次打开自动初始化六个节点
  const milestonesQuery = useQuery({
    queryKey: ['order-milestones', orderId],
    queryFn: () => listOrderMilestones(orderId),
    enabled: Number.isFinite(orderId) && activeKey === 'milestones',
  })
  const statusQuery = useQuery({
    queryKey: ['order-status', orderId],
    queryFn: () => listOrderStatusHistory(orderId),
    enabled: Number.isFinite(orderId) && activeKey === 'status',
  })
  const receivablesQuery = useQuery({
    queryKey: ['order-receivables', orderId],
    queryFn: () => listOrderReceivables(orderId),
    enabled: Number.isFinite(orderId),
  })
  const paymentsQuery = useQuery({
    queryKey: ['order-payments', orderId],
    queryFn: () => listOrderPayments(orderId),
    // 底部「订单全景」要报收款笔数，所以不再只等页签激活
    enabled: Number.isFinite(orderId),
  })
  const summaryQuery = useQuery({
    queryKey: ['order-finance', orderId],
    queryFn: () => orderFinanceSummary(orderId),
    enabled: Number.isFinite(orderId),
  })
  // 发货批次（§3.5/场景13）：计划与实发分开，未发量决定整单能否完成
  const shipmentsQuery = useQuery({
    queryKey: ['order-shipments', orderId],
    queryFn: () => listOrderShipments(orderId),
    // 同上：全景要报发货批次数
    enabled: Number.isFinite(orderId),
  })
  // 销售退货：列表常开（页签角标/操作都要用），可退明细只在页签或弹窗打开时拉
  const returnsQuery = useQuery({
    queryKey: ['order-returns', orderId],
    queryFn: () => getOrderReturns(orderId),
    enabled: Number.isFinite(orderId),
  })
  const returnableQuery = useQuery({
    queryKey: ['order-returnable', orderId],
    queryFn: () => getReturnable(orderId),
    enabled: Number.isFinite(orderId) && (activeKey === 'returns' || returnVisible),
  })

  const milestonesRefresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['timeline', 'customer'] })
    void queryClient.invalidateQueries({ queryKey: ['order-milestones', orderId] })
  }
  // 交期变更（方案 :105）：入口在「跟单节点」页，确认后才重排
  const scheduleChangesQuery = useQuery({
    queryKey: ['order-schedule-changes', orderId],
    queryFn: () => listScheduleChanges(orderId),
    enabled: Number.isFinite(orderId) && activeKey === 'milestones',
  })
  // 节点责任人按需搜索（姓名 / 登录账号），不再只取前 100 个用户：
  // 人员多时，第 101 位以后的人既不进下拉、也反查不到姓名。
  const schedulePreviewMutation = useMutation({
    mutationFn: () => previewScheduleChange(orderId, scheduleForm),
    onSuccess: (data) => setSchedulePreview(data),
    onError: (error: Error) => Toast.error(error.message),
  })
  const createScheduleMutation = useMutation({
    mutationFn: () =>
      createScheduleChange(orderId, {
        ...scheduleForm,
        reason: scheduleForm.reason || null,
      }),
    onSuccess: () => {
      Toast.success('已生成交期变更单，确认后才重排计划')
      setScheduleVisible(false)
      setSchedulePreview(null)
      void queryClient.invalidateQueries({ queryKey: ['order-schedule-changes', orderId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const confirmScheduleMutation = useMutation({
    mutationFn: (changeId: number) => confirmScheduleChange(orderId, changeId),
    onSuccess: () => {
      Toast.success('已确认，节点与批次计划日已重排')
      milestonesRefresh()
      void queryClient.invalidateQueries({ queryKey: ['order', orderId] })
      void queryClient.invalidateQueries({ queryKey: ['order-shipments', orderId] })
      void queryClient.invalidateQueries({ queryKey: ['order-schedule-changes', orderId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const cancelScheduleMutation = useMutation({
    mutationFn: (changeId: number) => cancelScheduleChange(orderId, changeId),
    onSuccess: () => {
      Toast.success('已作废，可以重新发起交期变更')
      void queryClient.invalidateQueries({ queryKey: ['order-schedule-changes', orderId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const milestoneSaveMutation = useMutation({
    mutationFn: () =>
      updateOrderMilestone(orderId, milestoneEdit!.id, {
        skipped: milestoneForm.skipped,
        skip_reason: milestoneForm.skipped ? milestoneForm.skip_reason : undefined,
        planned_date: milestoneForm.planned_date,
        actual_date: milestoneForm.actual_date,
        owner_id: milestoneForm.owner_id,
        evidence: milestoneForm.evidence || null,
        overdue_reason: milestoneForm.overdue_reason || null,
        remark: milestoneForm.remark || null,
      }),
    onSuccess: () => {
      Toast.success('里程碑已更新')
      setMilestoneEdit(null)
      milestonesRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const openMilestoneEdit = (row: OrderMilestoneRow) => {
    setMilestoneEdit(row)
    setMilestoneForm({
      skipped: row.status === 'skipped',
      skip_reason: row.skip_reason ?? '',
      planned_date: row.planned_date,
      actual_date: row.actual_date,
      owner_id: row.owner_id ?? null,
      evidence: row.evidence ?? '',
      overdue_reason: row.overdue_reason ?? '',
      remark: row.remark ?? '',
    })
  }

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['timeline', 'customer'] })
    void queryClient.invalidateQueries({ queryKey: ['order', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order-receivables', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order-payments', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order-status', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order-finance', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order-returns', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order-returnable', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['orders'] })
    void queryClient.invalidateQueries({ queryKey: ['receivables'] })
    void queryClient.invalidateQueries({ queryKey: ['payments'] })
  }

  const statusMutation = useMutation({
    mutationFn: () => changeOrderStatus(
      orderId, targetStatus!,
      targetStatus === 'completed' && statusRemark.trim()
        ? statusRemark.trim()
        : '在订单详情页更新',
    ),
    onSuccess: (result) => {
      // 文案要说清**是什么对象**变了，不能只写"已更新为「已发货」"——
      // 提示是全局浮层，用户切到别的页面它还会挂几秒，没有主语就完全对不上号。
      Toast.success(`${result.order_no} 的履约状态已更新为「${result.status === 'completed' ? '交付已完成' : result.status_label}」`)
      setStatusVisible(false)
      refresh()
    },
  })
  const openStatusEditor = (status: string | null = null) => {
    statusMutation.reset()
    setStatusRemark('')
    setTargetStatus(status)
    setStatusVisible(true)
  }

  /**
   * **取消订单**（不可逆的终态动作）。
   *
   * 这里必须走 `cancelOrder`（`POST /orders/{id}/cancel`）而不是把履约状态
   * 改成 `cancelled` —— 两者后果差很远：专用接口会同步处理回款与应收
   * （已有确认回款直接拒绝取消、待确认回款随单驳回、未回清的应收计划置为
   * 已取消以停止催收、未发货批次随单取消）。走"更新履约状态"那条路，
   * 上面这些**一件都不会发生**，会留下"订单已取消但催收照发"的脏账。
   */
  const cancelMutation = useMutation({
    mutationFn: (reason: string) => cancelOrder(orderId, reason || undefined),
    onSuccess: (result) => {
      Toast.success(`${result.order_no} 已取消`)
      setCancelVisible(false)
      setCancelReason('')
      refresh()
      shipmentsRefresh()
      void queryClient.invalidateQueries({ queryKey: ['quote-version'] })
      void queryClient.invalidateQueries({ queryKey: ['order-schedule-changes', orderId] })
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ['order-payments', orderId] })
    },
  })

  // 恢复已取消订单：不重建、保留原编号与明细；应收与批次需重新生成/重排
  const restoreMutation = useMutation({
    mutationFn: () => restoreOrder(orderId),
    onSuccess: (result) => {
      Toast.success(`${result.order_no} 已恢复`)
      refresh()
      shipmentsRefresh()
      void queryClient.invalidateQueries({ queryKey: ['order-payments', orderId] })
      void queryClient.invalidateQueries({ queryKey: ['order-schedule-changes', orderId] })
      void queryClient.invalidateQueries({ queryKey: ['quote-version'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ---- 销售退货：登记（含退货总额） → 主管复核 → 财务结算（抵扣/退现）/ 作废 ----
  const createReturnMutation = useMutation({
    mutationFn: (payload: ReturnCreatePayload) => createReturn(orderId, payload),
    onSuccess: (row) => {
      Toast.success(`退货单 ${row.return_no} 已登记，退货总额 ${formatMoney(row.total_amount_exact, row.currency)}，等主管复核`)
      setReturnVisible(false)
      setReturnQty({})
      setReturnRefundAmount('')
      setReturnReason('')
      setReturnRemark('')
      refresh()
    },
  })
  const cancelReturnMutation = useMutation({
    mutationFn: (payload: { id: number; reason: string }) => cancelReturn(payload.id, payload.reason),
    onSuccess: (row) => {
      Toast.success(`退货单 ${row.return_no} 已取消`)
      setCancelReturnTarget(null)
      setCancelReturnReason('')
      refresh()
    },
  })
  const reviewReturnMutation = useMutation({
    mutationFn: (payload: { id: number; decision: 'approve' | 'reject'; note?: string }) =>
      reviewReturn(payload.id, payload.decision, payload.note),
    onSuccess: (row) => {
      Toast.success(
        row.status === 'completed'
          ? `退货单 ${row.return_no} 已通过复核，待财务结算`
          : row.status === 'pending'
            ? `退货单 ${row.return_no} 已通过复核，等填退货总额`
            : `退货单 ${row.return_no} 已驳回，可退数量已释放`,
      )
      setReviewTarget(null)
      setReviewNote('')
      refresh()
    },
  })
  const updateReturnAmountMutation = useMutation({
    mutationFn: (payload: { id: number; amount: number; note?: string }) =>
      updateReturnAmount(payload.id, payload.amount, payload.note),
    onSuccess: (row) => {
      Toast.success(
        `退货单 ${row.return_no} 退货总额已更新为 ${formatMoney(row.total_amount_exact, row.currency)}`,
      )
      setAmountTarget(null)
      setAmountDraft('')
      setAmountNote('')
      refresh()
    },
  })
  const refundReturnMutation = useMutation({
    mutationFn: (payload: { id: number; reference: string }) => refundReturn(payload.id, payload.reference),
    onSuccess: (row) => {
      Toast.success(`退货单 ${row.return_no} 已登记退款`)
      setRefundTarget(null)
      setRefundReference('')
      refresh()
    },
  })
  const settleReturnMutation = useMutation({
    mutationFn: (payload: {
      id: number
      total: number
      offset: number
      reference?: string
      note?: string
    }) => settleReturn(payload.id, {
      total_amount: payload.total,
      offset_amount: payload.offset,
      refund_reference: payload.reference,
      note: payload.note,
    }),
    onSuccess: (row) => {
      const offset = Number(row.offset_amount_exact)
      const cash = Number(row.refund_amount_exact)
      const extra = offset > 0
        ? `，抵扣客户未付款 ${formatMoney(offset, row.currency)}`
        : ''
      const tail = cash > 0
        ? `，现金退还 ${formatMoney(cash, row.currency)}`
        : '，无现金退还'
      Toast.success(`退货单 ${row.return_no} 已结算${extra}${tail}`)
      setRefundTarget(null)
      setRefundReference('')
      setSettleTotalDraft('')
      setSettleOffsetDraft('0')
      setSettleNote('')
      refresh()
    },
  })
  const uploadRefundAttachmentMutation = useMutation({
    mutationFn: (payload: { id: number; file: File }) =>
      uploadReturnRefundAttachment(payload.id, payload.file),
    onSuccess: (row) => {
      Toast.success('退款附件已上传')
      setRefundTarget(row)
      refresh()
    },
    onError: (error: Error) => {
      Toast.error(error.message || '退款附件上传失败')
    },
  })
  const deleteRefundAttachmentMutation = useMutation({
    mutationFn: (id: number) => deleteReturnRefundAttachment(id),
    onSuccess: (row) => {
      Toast.success('退款附件已删除')
      setRefundTarget(row)
      refresh()
    },
    onError: (error: Error) => {
      Toast.error(error.message || '退款附件删除失败')
    },
  })
  const voidReturnMutation = useMutation({
    mutationFn: (payload: { id: number; reason: string }) => voidReturn(payload.id, payload.reason),
    onSuccess: (row) => {
      const restoredOldOffset = Number(row.offset_amount_exact) > 0
      Toast.success(
        restoredOldOffset
          ? `退货单 ${row.return_no} 已作废，原抵扣的客户未付款已恢复`
          : `退货单 ${row.return_no} 已作废，可退数量已释放`,
      )
      setVoidReturnTarget(null)
      setVoidReturnReason('')
      refresh()
    },
  })

  const generateMutation = useMutation({    mutationFn: async () => {
      const dueOf = (row: { due: Date | null }) => calendarDayOrToday(row.due)
      // **整份计划一次提交**（第九批问题 ⑥）。
      //
      // 从前是"先 POST 生成（后端只有前两期的名称/日期槽位）→ 再逐条 PATCH
      // 补第 3 期起"。中途任何一条被拒，就会留下**占位名 + 第一期日期**的
      // 半成品，而重试又撞"该订单已有应收计划，请先删除再重新生成" ——
      // 用户卡在中间没有出路。
      //
      // 现在 `POST /receivables/generate` 支持按期的 `names` / `due_dates`
      // （长度与 ratios 对齐），由后端在**同一个事务**里校验并落库：
      // 要么整份成功，要么整份回滚，不会留下半成品。
      const created = await generateReceivables(orderId, {
        ratios: generateRows.map((row) => (row.percent ?? 0) / 100),
        first_due_date: dueOf(generateRows[0]),
        names: generateRows.map((row, index) => row.name.trim() || `第 ${index + 1} 期`),
        due_dates: generateRows.map((row) => dueOf(row)),
        request_key: newRequestKey(),
      })
      return { count: created.length }
    },
    onSuccess: ({ count }) => {
      Toast.success(`已生成 ${count} 期应收计划。客户实际打款后，在对应期登记回款。`)
      setGenerateVisible(false)
      refresh()
    },
  })

  const dueMutation = useMutation({
    mutationFn: ({ planId, day }: { planId: number; day: string }) =>
      updateReceivable(planId, { due_date: day }),
    onSuccess: () => {
      setDueEdit(null)
      Toast.success('应收到期日已更新')
      refresh()
      milestonesRefresh()
    },
  })

  const paymentMutation = useMutation({
    mutationFn: () => {
      // 兜底：极端情况下（比如将来多开了一个入口忘了生成键）也要保证同一份
      // 表单的重试复用同一把键，所以这里只在为空时补一把。
      if (!paymentKeyRef.current) paymentKeyRef.current = newRequestKey()
      return createPayment({
        receivable_plan_id: paymentTarget!.id,
        // ⚠️ 必须用共享件，不能 `toISOString().slice(0,10)`（2026-10-14 审查修）。
        // 那个写法转的是 **UTC**，而日历上点的是**当地那一天**：北京（UTC+8）
        // 选 10 月 1 日、这个 Date 又是当地时间零点（Semi 的 DatePicker 内部
        // `startOfDay` 就是这么归一的）→ 截出来是 **09-30，整整早一天**。
        // `shared/calendar-day.ts` 的注释就是为这件事写的，且**本文件第 68 行
        // 早就 import 了 `calendarDay`** —— 只有这一处漏用（同文件其它日期都走了它）。
        // 后果不小：收款日期、回款归月/对账、账期与逾期判定全错一天，
        // 而界面只显示"回款已登记"，看不出来。
        received_date: calendarDay(paymentForm.date),
        received_amount: paymentForm.amount.trim(),
        payment_method: paymentForm.method,
        request_key: paymentKeyRef.current,
      })
    },
    onSuccess: (payment) => {
      Toast.success(payment.status === 'pending' ? '回款已登记，待财务确认。可在「回款记录」跟进。' : '可在「回款记录」查看这笔款项的最新状态。')
      setPaymentTarget(null)
      setActiveKey('payments')
      setPaymentForm({ amount: '', date: new Date(), method: '银行转账' })
      // 成功才换键：下一次登记是新的一笔，不能复用上一笔的键
      // （否则会被后端判成"同键不同内容"直接拒绝）。
      paymentKeyRef.current = ''
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const confirmMutation = useMutation({
    mutationFn: (id: number) => confirmPayment(id, '财务核对通过'),
    onSuccess: () => {
      Toast.success('回款已确认，已收金额和各期分配已更新。')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 作废已确认回款（2026-11-25 主人拍板 A 方案）：钱确实到过账，记录/分配/凭证
  // 全留；只是不再计入已收。作废后订单若已无已确认回款，就可以走取消。
  const voidMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => voidPayment(id, reason),
    onSuccess: (payment) => {
      Toast.success(
        `${formatMoney(payment.received_amount_exact ?? payment.received_amount, payment.currency ?? 'CNY')} 的回款已作废，已收金额与应收期状态已回退。`,
      )
      setVoidTarget(null)
      setVoidReason('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 删应收节点（2026-10-13）：解开"自动生成的全款删不掉 → 生成按钮不出现 → 分期进不去"。
  // 后端带锁、并拦"已有回款记录"的节点；前端把这类节点的入口先藏起来（点了必然失败的东西不该给）。
  const deleteReceivableMutation = useMutation({
    mutationFn: (id: number) => deleteReceivable(id),
    onSuccess: () => {
      Toast.success('应收节点已删除；跟单节点会按剩余应收自动同步')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ---- 发货批次（§3.5/场景13）----
  const [createShipmentVisible, setCreateShipmentVisible] = useState(false)
  const shipmentKeyRef = useRef('')
  const [shipmentPlanDate, setShipmentPlanDate] = useState<string | null>(null)
  const [plannedQtys, setPlannedQtys] = useState<Record<number, string>>({})
  const plannedShipmentRows = (shipmentsQuery.data?.items ?? []).flatMap((item) => {
    const raw = plannedQtys[item.order_item_id]?.trim()
    if (!raw) return []
    try {
      return [{ order_item_id: item.order_item_id, planned_qty: requiredQuantityText(raw), error: '' }]
    } catch (error) {
      return [{ order_item_id: item.order_item_id, planned_qty: raw, error: (error as Error).message }]
    }
  })
  const invalidPlannedRow = plannedShipmentRows.find((row) => row.error)
  const [shipTarget, setShipTarget] = useState<ShipmentBatchRow | null>(null)
  // 改「计划中」批次的计划发货日（交期变更后人工校准）
  const [rescheduleTarget, setRescheduleTarget] = useState<ShipmentBatchRow | null>(null)
  const [rescheduleForm, setRescheduleForm] = useState<{ date: string | null; reason: string }>({
    date: null,
    reason: '',
  })
  const [shipForm, setShipForm] = useState<{ date: string | null; company: string; tracking: string; quantities: Record<number, string> }>({
    date: null,
    company: '',
    tracking: '',
    quantities: {},
  })
  const actualShipmentRows = (shipTarget?.items ?? []).map((item) => {
    const planned = shipmentQuantity(item.planned_qty_exact ?? item.planned_qty)
    const raw = shipForm.quantities[item.order_item_id] ?? ''
    try {
      const quantity = requiredQuantityText(raw, '实发数量', true)
      return { item, planned, raw, quantity, difference: shipmentDifference(planned, quantity), error: '' }
    } catch (error) {
      return { item, planned, raw, quantity: '', difference: '', error: (error as Error).message }
    }
  })
  const openShipment = (batch: ShipmentBatchRow) => {
    const quantities = Object.fromEntries(batch.items.map((item) => [
      item.order_item_id,
      shipmentQuantity(item.planned_qty_exact ?? item.planned_qty),
    ]))
    setShipForm({ date: calendarDay(new Date()), company: '', tracking: '', quantities })
    setShipTarget(batch)
  }
  const closeShipment = () => {
    setShipTarget(null)
    setShipForm({ date: null, company: '', tracking: '', quantities: {} })
  }

  const shipmentsRefresh = () => {
    milestonesRefresh()
    void queryClient.invalidateQueries({ queryKey: ['order-shipments', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order-status', orderId] })
  }
  const createShipmentMutation = useMutation({
    mutationFn: () => {
      if (invalidPlannedRow) throw new Error('请先改正标红的发货数量')
      if (!plannedShipmentRows.length) throw new Error('请至少填写一项本批发货数量')
      return createOrderShipment(orderId, {
        request_key: shipmentKeyRef.current || (shipmentKeyRef.current = newRequestKey()),
        planned_date: shipmentPlanDate,
        items: plannedShipmentRows.map(({ order_item_id, planned_qty }) => ({ order_item_id, planned_qty })),
      })
    },
    onSuccess: () => {
      Toast.success('发货批次已排')
      setCreateShipmentVisible(false)
      setPlannedQtys({})
      setShipmentPlanDate(null)
      shipmentKeyRef.current = ''
      shipmentsRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const shipShipmentMutation = useMutation({
    mutationFn: (batchId: number) => {
      if (actualShipmentRows.some((row) => row.error)) throw new Error('请先改正标红的实发数量')
      if (!actualShipmentRows.some((row) => shipmentQuantityUnits(row.quantity) > 0n)) {
        throw new Error('本批至少要有一项实际发出')
      }
      return shipOrderShipment(orderId, batchId, {
        actual_ship_date: shipForm.date,
        logistics_company: shipForm.company || null,
        tracking_no: shipForm.tracking || null,
        items: actualShipmentRows.map(({ item, quantity }) => ({
          order_item_id: item.order_item_id,
          shipped_qty: quantity,
        })),
      })
    },
    onSuccess: (result) => {
      Toast.success(
        result.summary.all_shipped
          ? '已全部发完，可以确认完成交付'
          : `已登记发货，剩余未发 ${shipmentQuantity(result.summary.remaining_exact ?? result.summary.remaining)}`,
      )
      closeShipment()
      shipmentsRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const cancelShipmentMutation = useMutation({
    mutationFn: (batchId: number) => cancelOrderShipment(orderId, batchId),
    onSuccess: () => {
      Toast.success('批次已取消，计划数量可以重新安排')
      shipmentsRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const rescheduleShipmentMutation = useMutation({
    mutationFn: () => {
      if (!rescheduleTarget) throw new Error('请选择要调整的批次')
      if (!rescheduleForm.date) throw new Error('请选择新的计划发货日')
      const reason = rescheduleForm.reason.trim()
      if (!reason) throw new Error('请填写调整原因')
      return rescheduleOrderShipment(orderId, rescheduleTarget.id, {
        planned_date: rescheduleForm.date,
        reason,
      })
    },
    onSuccess: (result) => {
      Toast.success(
        `第 ${result.batch_no} 批计划发货日已改为 ${result.planned_date}`,
      )
      setRescheduleTarget(null)
      setRescheduleForm({ date: null, reason: '' })
      shipmentsRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 推送 ERP/MES 是**写外部系统**的动作（配置齐全时真的在对方系统建单，
  // 本系统撤回不了）。后端不带 confirm 返回 42206 + 订单号/客户/金额说明，
  // 这里弹出来，用户确认后带 confirm 重发。
  const [erpGate, setErpGate] = useState('')

  const syncMutation = useMutation({
    mutationFn: (confirm: boolean) => syncErp(orderId, confirm),
    onSuccess: (data) => {
      // already_synced 是幂等命中：订单早就推过了，不是失败
      if (data.already_synced) {
        Toast.info(data.message ?? '该订单已推送过，未重复建单')
      } else {
        Toast.success(data.message ?? '已推送到 ERP/MES')
      }
      void queryClient.invalidateQueries({ queryKey: ['order', orderId] })
    },
    onError: (error: Error) => {
      if (isConfirmRequired(error)) setErpGate(confirmMessageOf(error))
      else Toast.error(error.message)
    },
  })

  const statusSyncMutation = useMutation({
    mutationFn: () => refreshStatus(orderId),    onSuccess: (data) => {
      if (data.changed) {
        Toast.success(`履约状态已更新为「${data.status_label}」`)
      } else {
        Toast.info(
          data.raw_status
            ? `状态没有变化（对方回传：${data.raw_status}）`
            : '状态没有变化',
        )
      }
      void queryClient.invalidateQueries({ queryKey: ['order', orderId] })
      void queryClient.invalidateQueries({ queryKey: ['order-status-history', orderId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const repurchaseMutation = useMutation({
    mutationFn: () => repurchase(orderId),
    onSuccess: (data) => {
      Toast.success('已生成复购商机')
      navigate(`/opportunities/${data.opportunity_id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const order = orderQuery.data
  const erpReadiness = useQuery({
    queryKey: ['erp-readiness'],
    queryFn: getErpReadiness,
    enabled: canManage && Boolean(order),
    staleTime: 30_000,
  })
  const erpUnavailable = erpReadiness.data?.configured === false
  const erpUnavailableHint = '当前还未接入 ERP/MES。请联系管理员确认接入状态。'

  // 同步失败前先说明缺接入还是缺单号，按钮使用同一个判据。
  const statusSyncReasons = erpUnavailable
    ? [erpUnavailableHint]
    : order?.erp_order_id
    ? []
    : ['要先有 ERP/MES 单号：点「推送 ERP/MES」等对方回单号']

  /**
   * 「交期与跟单计划」里「预览受影响面」的受阻原因。
   *
   * 与「从来源建立订单草稿」完全同型：7 个数字框（运输天数 + 6 个节点提前天数）
   * 只要有一个被清空就变成 NaN → 按钮直接灰掉，而**不说是哪一个**；客户要求日期
   * 取自订单的计划交期，那是个可空列 —— 历史订单没排过期，弹窗一打开按钮就是灰的，
   * 而按钮在 720px 弹窗的页脚、出问题的字段在正文里，视线根本扫不到。
   * 所以这里逐项点名，写完贴在页脚正上方。
   */
  const planOffsetLabels: Record<string, string> = { contract: '签订合同', deposit: '付定金', pre_sample_sent: '产前样发出', pre_sample_confirmed: '产前样确认', first_shipment: '首批发货', payment: '收款' }
  const scheduleBlocked = [
    { ok: Boolean(scheduleForm.new_delivery_date), reason: '请先填「客户要求日期」（本单原来没有计划交期，所以这里是空的）。' },
    { ok: scheduleForm.delivery_kind !== 'arrival' || Number.isFinite(scheduleForm.transit_days), reason: '请填「预计运输天数」（客户要求的是到货日，必须知道路上几天）。' },
    { ok: Object.values(scheduleForm.plan_offsets).every((v) => Number.isFinite(v)), reason: '下面这些「提前天数」不能空着：' + Object.entries(scheduleForm.plan_offsets).filter(([, v]) => !Number.isFinite(v)).map(([key]) => planOffsetLabels[key] ?? key).join('、') + '（可以填 0，但不能空）。' },
  ]
  const scheduleBlockedReasons = blockedReasons(scheduleBlocked)
  // ⚠️ 「提交待确认计划」的禁用**不是"填错了"**，而是"还没走完上一步"：
  // 提交前必须先生成预览（`disabled={!schedulePreview}`），否则用户只看到一个
  // 灰按钮、不知道卡在哪（主人 2026-10-13 就是卡在这里："不知道怎么进行下去"）。
  // 所以这条与 `scheduleBlocked` 分开写 —— 两者的"下一步该做什么"完全不同：
  // 一个是"把表填完整"，一个是"先点旁边的预览"。
  const submitBlockedReasons = schedulePreview
    ? []
    : ['顺序要求：先点上一步的「预览受影响面」，看到影响面之后才能提交。']

  if (orderQuery.isLoading) return <PageLoading />
  if (!order) {
    /* 原来是一行裸字挂在整页白底（2026-11-20 巡检）：链接错了或没权限时像页面崩了。
       统一成空态 + 返回列表。 */
    return (
      <div className="page-container">
        <EmptyState
          title="订单不存在或无权查看"
          hint="可能链接里的编号有误，或这条记录属于你数据范围之外的人；回订单列表重新选择即可。"
        />
        <div style={{ display: 'flex', justifyContent: 'center', paddingBottom: 24 }}>
          <Button type="primary" theme="solid" onClick={() => navigate('/orders')}>返回订单列表</Button>
        </div>
      </div>
    )
  }

  const summary = summaryQuery.data
  const receivables = receivablesQuery.data
  const payments = paymentsQuery.data
  const pendingPayments = payments?.filter((payment) => payment.status === 'pending').length ?? 0
  const confirmedPayments = payments?.filter((payment) => payment.status === 'confirmed').length ?? 0
  const cancellationBlocked = confirmedPayments > 0
    ? `有 ${confirmedPayments} 笔已确认回款。请财务先在「回款记录」作废，再回来取消订单。`
    : ''
  //: 已完成订单回退为已取消，后端要求必须留理由（进状态变更留痕）。
  const cancelReasonRequired = order.status === 'completed'
  const cancelReasonMissing = cancelReasonRequired && cancelReason.trim().length < 2
  const cancelPlanCount = receivables?.filter((plan) => !['paid', 'cancelled'].includes(plan.status)).length
  const orderCancelled = order.status === 'cancelled'
  const unreceivedAmount = order.unreceived_amount_exact ?? order.unreceived_amount
  const hasUnreceived = Number(unreceivedAmount) > 0
  const waitingForPayment = `待收款 ${formatMoney(unreceivedAmount, order.currency)}`
  const canRegisterPayment = canManage || canPayment
  const receivableTotal = order.total_amount_exact ?? order.total_amount
  const generateBlocked = planBlockedReasons('installments', generateRows, receivableTotal)
  const canGenerateReceivables = canPayment && !orderCancelled && receivablesQuery.isSuccess && !receivables?.length
  const hasOpenReceivables = receivables?.some((plan) => !['paid', 'cancelled'].includes(plan.status))
  const showPaymentRetry = !orderCancelled && canRegisterPayment && pendingPayments === 0 &&
    payments?.some((payment) => payment.status === 'rejected' && receivables?.some((plan) =>
      plan.id === payment.receivable_plan_id && !['paid', 'cancelled'].includes(plan.status)))
  const shipment = shipmentsQuery.data
  const returns = returnsQuery.data ?? []
  const returnable = returnableQuery.data
  // 退货只按订单明细（产品/规格）汇总，前端不再拆发货批次，也不让业务员看批次。
  const returnableRows: ReturnableItem[] = returnable?.items ?? []
  const returnReviewingCount = returns.filter((row) => row.status === 'reviewing').length
  const returnPendingCount = returns.filter((row) => row.status === 'pending').length
  const returnRefundPendingCount = returns.filter(
    (row) => row.status === 'completed' && Number(row.refund_amount_exact) > 0,
  ).length
  const returnRequestedItems = returnableRows.flatMap((row) => {
    const qty = Number(returnQty[row.order_item_id] || 0)
    if (!Number.isFinite(qty) || qty <= 0) return []
    return [{ order_item_id: row.order_item_id, quantity: Number(qty.toFixed(3)) }]
  })
  // 登记参考金额：只用来提示业务员“按单价大概是多少”，最终退货总额以上方手填为准。
  const returnReferenceTotal = returnableRows.reduce((sum, row) => {
    const qty = Number(returnQty[row.order_item_id] || 0)
    const unit = Number(row.unit_price_exact ?? row.unit_price ?? 0)
    if (!Number.isFinite(qty) || qty <= 0 || !Number.isFinite(unit)) return sum
    return sum + qty * unit
  }, 0)
  const overReturnRow = returnableRows.find(
    (row) => Number(returnQty[row.order_item_id] || 0) > Number(row.remaining_quantity_exact),
  )
  const returnRefundValid = /^\d+(?:\.\d{1,2})?$/.test(returnRefundAmount.trim())
    && Number(returnRefundAmount) > 0
  const returnSubmitBlocked = returnReason.trim().length < 2
    ? '请填写退货原因（至少 2 个字）'
    : !returnRefundValid
      ? '请填写退货总额（大于 0，最多两位小数；含货款和运费）'
      : overReturnRow
        ? `${overReturnRow.sku || '该明细'} 本次最多还能退 ${overReturnRow.remaining_quantity_exact}`
        : returnRequestedItems.length === 0
          ? '请至少填一项本次退货数量'
          : ''
  const amountDraftValid = /^\d+(?:\.\d{1,2})?$/.test(amountDraft.trim())
    && Number(amountDraft) > 0
  const reviewNoteValid = reviewDecision === 'approve' || reviewNote.trim().length >= 2
  const refundReferenceValid = refundReference.trim().length >= 2
    && refundReference.trim().length <= 128
  // 财务结算：新退货可以改总额、手动填抵扣；历史抵扣单只补登记现金退款。
  const refundIsLegacyOffset = Boolean(refundTarget && Number(refundTarget.offset_amount_exact) > 0)
  const refundEditsSettlement = Boolean(refundTarget && !refundIsLegacyOffset)
  const settleTotalValid = /^\d+(?:\.\d{1,2})?$/.test(settleTotalDraft.trim())
    && Number(settleTotalDraft) > 0
  const settleOffsetNumber = Number(settleOffsetDraft || 0)
  const settleOffsetShapeValid = /^\d+(?:\.\d{1,2})?$/.test(settleOffsetDraft.trim())
    && Number.isFinite(settleOffsetNumber)
    && settleOffsetNumber >= 0
  const settleMaxOffset = Math.max(
    0,
    Math.min(Number(settleTotalDraft || 0), Number(unreceivedAmount || 0)),
  )
  const settleCashAmount = refundEditsSettlement
    ? Math.max(0, Number(settleTotalDraft || 0) - settleOffsetNumber)
    : Number(refundTarget?.refund_amount_exact ?? 0)
  const settleBlockedReason = !refundTarget
    ? ''
    : refundIsLegacyOffset
      ? (Number(refundTarget.refund_amount_exact) > 0 && !refundReferenceValid
          ? '请填写退款流水/凭证说明（至少 2 个字）'
          : '')
      : !settleTotalValid
        ? '请填写退货总额（大于 0，最多两位小数）'
        : !settleOffsetShapeValid
          ? '抵扣金额要填 0 或正数，最多两位小数'
          : settleOffsetNumber > settleMaxOffset
            ? `最多可抵扣 ${formatMoney(settleMaxOffset, order.currency)}`
            : settleCashAmount > 0 && !refundReferenceValid
              ? '有现金退还时，请填写退款流水/凭证说明（至少 2 个字）'
              : ''
  const refundAttachmentBusy = uploadRefundAttachmentMutation.isPending
    || deleteRefundAttachmentMutation.isPending
  const cancelReturnReasonValid = cancelReturnReason.trim().length >= 2
  const voidReturnReasonValid = voidReturnReason.trim().length >= 2
  const remainingToShip = shipment?.summary.remaining_exact ?? shipment?.summary.remaining
  const completingDelivery = targetStatus === 'completed'
  const completionBlocked = completingDelivery && remainingToShip != null && Number(remainingToShip) > 0
    ? `还有 ${shipmentQuantity(remainingToShip)} 件未发。请先登记发货，再确认完成交付。`
    : ''
  const needsShipment = targetStatus === 'shipped' || targetStatus === 'delivered'
  const shippedAmount = shipment?.summary.shipped_exact ?? shipment?.summary.shipped
  const shipmentBlocked = needsShipment && shippedAmount != null && Number(shippedAmount) <= 0
    ? '请先到「发货批次」登记实际发货，再更新状态。' : ''
  const completionNeedsReason = completingDelivery && hasUnreceived
  const completionReasonBlocked = completionNeedsReason && !statusRemark.trim()
    ? `还有未收款 ${waitingForPayment.replace('待收款 ', '')}，请填写原因（例如：月结客户，按合同账期收款）。`
    : ''
  const statusBlocked = targetStatus
    ? completionBlocked || shipmentBlocked || completionReasonBlocked
    : '先选择要更新的履约状态。'
  const shipmentPlanBlocked = orderCancelled
    ? '订单已取消，不能再排发货批次。'
    : shipment?.summary.all_shipped
      ? '这张订单已全部发完。'
      : shipment?.items.length && shipment.items.every((item) => Number(item.unplanned_exact ?? item.unplanned) <= 0)
        ? '剩余数量已安排到待发批次。实际发出后，请在对应批次点「登记发货」。'
        : ''
  const financeNext = !orderCancelled && !receivablesQuery.isError && !paymentsQuery.isError && receivables && payments
    ? pendingPayments > 0
      ? { tab: 'payments', label: '打开回款记录', hint: `有 ${pendingPayments} 笔回款待财务确认。确认后才计入已回款。` }
      : !receivables.length
        ? { tab: 'receivables', label: '查看应收计划', hint: canPayment
            ? '还没有应收计划。先按约定的收款时间和比例生成各期应收。'
            : '还没有应收计划。请财务先按约定的收款时间建立应收计划。' }
        : hasOpenReceivables
          ? { tab: 'receivables', label: '查看应收计划', hint: canRegisterPayment
              ? '客户实际打款后，到「应收计划」的对应期登记回款。'
              : '可在「应收计划」查看各期金额、到期日和已收金额。' }
          : null
    : null

  // §9.9 复审：详情页原来只取"币种前缀"再自己拼，币种为空（历史订单）时就成了
  // 一个**裸金额** —— 而列表页早就用 `formatMoney`（空币种会写「币种待核实」）。
  // 同一个数在两个页面口径不同，这里统一成 formatMoney。
  const itemColumns = [
    /* 1024 下六列合计 787 > 卡内 699，订单明细一进来就顶出横向滚动条、
       「金额」被切掉半截（2026-11-19 实拍）。首列改成吃剩余宽度，
       其余五列按"够用"收窄：规格 170 / 数量 64 / 单价 104 / 金额 124 / 备注 120，
       1440 下首列自然变宽，两档都不再需要横向滚动。 */
    { title: '产品名称 / 需求', dataIndex: 'sku_code', width: '100%', render: (code: string | null, row: OrderItem) => <div>{row.sku_snapshot || code || row.inquiry_no_snapshot || '未命名产品'}<div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{row.sku_snapshot ? code || row.inquiry_no_snapshot : ''}</div></div> },
    { title: '规格', dataIndex: 'specification', width: 170, render: (v: string | null) => v ?? '-' },
    { title: '数量', dataIndex: 'quantity', width: 64, render: (v: number) => v.toLocaleString('zh-CN') },
    // 单价走 formatUnitPrice：保真（最多 4 位）、最少两位、尾零去掉。
     // 曾经在这里截成两位，结果 33.3333 变成 33.33，"单价×数量" 和金额对不上，
     // CI 的 confirmation_browser_smoke 直接红了（2026-11-17）。
    {
      title: '单价',
      dataIndex: 'unit_price',
      width: 104,
      className: 'cell-right',
      render: (v: number, row: OrderItem) => formatUnitPrice(row.unit_price_exact ?? v, order.currency),
    },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 124,
      className: 'cell-right',
      render: (v: number, row: OrderItem) => formatMoney(row.amount_exact ?? v, order.currency),
    },
    /* 120 时表宽 700、卡内 699 —— 差这 1px 就够弹出横向滚动条，收到 116。 */
    { title: '备注', dataIndex: 'remark', width: 116, render: (v: string | null) => v ?? '-' },
  ]

  // 应收计划属于「要跟财务对账」的单据数据 → 金额一律两位小数（主人 2026-11-17 口径）。
  // formatMoney 只做千分位，不补位，所以先过 toTwoDecimals。
  const receivableColumns = [
    { title: '节点', dataIndex: 'plan_name', width: 110 },
    {
      title: '应收日期',
      dataIndex: 'due_date',
      width: 130,
      render: (day: string | null, plan: Receivable) => (
        <div>
          {day ?? '未填写'}
          {canPayment && !orderCancelled && plan.status !== 'cancelled' && (
            <div>
              <Button size="small" theme="borderless" onClick={() => {
                dueMutation.reset()
                setDueEdit({ plan, date: day ? new Date(`${day}T00:00:00`) : null })
              }}>修改到期日</Button>
            </div>
          )}
        </div>
      ),
    },
    // 行自带币种就优先用它（历史数据里可能与订单币种不一致），没有才退回订单币种
    { title: '应收金额', dataIndex: 'amount', width: 130, className: 'cell-right', render: (v: number, r: Receivable) => formatMoney(toTwoDecimals(r.amount_exact ?? v), r.currency ?? order.currency) },
    { title: '已收', dataIndex: 'received_amount', width: 130, className: 'cell-right col-hide-narrow', render: (v: number, r: Receivable) => formatMoney(toTwoDecimals(r.received_amount_exact ?? v), r.currency ?? order.currency) },
    { title: '未收', dataIndex: 'remaining_amount', width: 130, className: 'cell-right col-hide-narrow', render: (v: number, r: Receivable) => formatMoney(toTwoDecimals(r.remaining_amount_exact ?? v), r.currency ?? order.currency) },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: 110,
      render: (value: string, record: Receivable) => (
        <Tag color={PLAN_TONE[record.status] ?? 'grey'}>{value}</Tag>
      ),
    },
    {
      title: '操作',
      width: 150,
      render: (_: unknown, record: Receivable) => {
        // ⚠️ 「登记回款」还要看**生命周期**（2026-10-14 审查修）：
        // 订单取消时后端只把**非 paid** 的节点置 `cancelled`
        // （`order/router.py` 的 cancel_order），而这些行仍以「已取消（随订单）」
        // 渲染，`status !== 'paid'` 成立 —— 于是入口照旧露出，点开填完必得 422
        // 「该应收节点已随订单取消，不能再登记回款」（`assert_plan_accepts_payment`）。
        // 同文件对"取消是终态、失败入口不给"已经处理过（隐藏「更新履约状态/取消订单」），
        // 这一列漏了。
        const planCancelled = record.status === 'cancelled' || order.status === 'cancelled'
        const canRegister = record.status !== 'paid' && !planCancelled && canRegisterPayment
        // ⚠️ 「删除」的判据必须是**后端的真实闸门**（2026-10-14 审查修）：
        // 删除时后端判的是"该期下有没有**任何**回款行"（待确认/已驳回都算，
        // 不带 status 过滤），而原来前端用的是 `received_amount > 0`
        // —— 那只统计**已确认**的分配。于是"登记了一笔但财务还没确认"或
        // "财务驳回了那笔"时，界面显示能删、点下去必然 422。
        // 现在后端把真实判据作为 `has_payment_record` 返回，两边同一把尺子。
        const hasPayment = Boolean(record.has_payment_record)
        const canDelete = canPayment && !hasPayment && !planCancelled
        // 点不了就得说清为什么（主人反复提过的口径）：一个都不给时不留空白，
        // 而是给出原因，别让用户对着空操作列猜。
        if (!canRegister && !canDelete) {
          if (planCancelled) {
            return (
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                订单已取消，这一期不再收款或删除
              </span>
            )
          }
          // 已收齐的行本来就有回款记录，原来先命中下面的 hasPayment 分支，
          // 于是「已回款」的行上写着「请先处理回款」—— 自相矛盾（2026-11-20 截图）。
          // 先判 paid：收齐了就只说收齐，让用户去「回款记录」页签看明细。
          if (record.status === 'paid') {
            return (
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                本期已收齐{hasPayment ? '，明细见「回款记录」' : ''}
              </span>
            )
          }
          if (hasPayment) {
            return (
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                该期已有回款记录（含待确认/已驳回），请先处理回款
              </span>
            )
          }
          return '请由订单负责人或财务登记'
        }
        return (
          <div style={{ display: 'grid', gap: 4 }}>
            <div style={{ display: 'flex', gap: 10 }}>
              {canRegister && (
                <Button size="small" theme="borderless"
                  onClick={() => {
                    setPaymentTarget(record)
                    setPaymentForm({
                      amount: record.remaining_amount_exact ?? String(record.remaining_amount),
                      date: new Date(),
                      method: '银行转账',
                    })
                    // 打开表单就定下这一笔的请求键；中途提交失败重试仍用它。
                    paymentKeyRef.current = newRequestKey()
                  }}
                >
                  登记回款
                </Button>
              )}
              {canDelete && (
                <Popconfirm
                  title={`删除「${record.plan_name ?? '这一期'}」这个应收节点？`}
                  content="删除后这期应收消失、订单应收合计随之变小，「收款」跟单节点会按剩余应收自动同步。已经收过款的节点不能删。"
                  onConfirm={() => deleteReceivableMutation.mutate(record.id)}
                >
                  <button type="button" className="crm-text-action" style={{ color: 'var(--crm-error, #ba1a1a)' }}>删除</button>
                </Popconfirm>
              )}
            </div>
            {hasPayment && !canDelete && (
              <span style={{ fontSize: 11, color: 'var(--crm-text-3)', lineHeight: 1.5 }}>
                该期已有回款，不能删除；
                <button type="button" className="crm-text-action"
                  style={{ marginLeft: 2 }}
                  onClick={() => {
                    setActiveKey('payments')
                    orderTabsRef.current?.scrollIntoView({ block: 'start' })
                    orderTabsRef.current?.focus({ preventScroll: true })
                  }}
                >
                  去「回款记录」调整分配
                </button>
              </span>
            )}
          </div>
        )
      },
    },
  ]

  const openSettlementModal = (row: SalesReturn) => {
    settleReturnMutation.reset()
    refundReturnMutation.reset()
    setRefundReference('')
    setRefundTarget(row)
    // 历史抵扣单只补现金退款；新退货进结算弹窗，可改总额和抵扣。
    if (Number(row.offset_amount_exact) <= 0) {
      setSettleTotalDraft(row.settled_goods_amount_exact ?? row.goods_amount_exact ?? '')
      setSettleOffsetDraft('0')
      setSettleNote(row.settlement_note ?? '')
    } else {
      setSettleTotalDraft('')
      setSettleOffsetDraft('0')
      setSettleNote('')
    }
  }

  const returnColumns = [
    {
      title: '退货单',
      width: 190,
      render: (_: unknown, row: SalesReturn) => (
        <div>
          <strong>{row.return_no}</strong>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            {row.return_date} · 共 {row.items.length} 项
          </div>
        </div>
      ),
    },
    {
      title: '退货金额',
      width: 190,
      className: 'cell-right',
      render: (_: unknown, row: SalesReturn) => {
        return (
          <div>
            <div>
              {row.settled_goods_amount_exact
                ? formatMoney(row.settled_goods_amount_exact, row.currency)
                : '待填'}
            </div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              登记参考：{formatMoney(row.goods_amount_exact, row.currency)}
              {row.settled_by_name ? ` · 经办：${row.settled_by_name}` : ''}
            </div>
          </div>
        )
      },
    },
    {
      title: '结算',
      width: 230,
      render: (_: unknown, row: SalesReturn) => {
        const offset = Number(row.offset_amount_exact)
        const cash = Number(row.refund_amount_exact)
        let main = '—'
        if (row.status === 'settled') {
          main = `已抵扣未付款 ${formatMoney(row.offset_amount_exact, row.currency)}`
        } else if (row.status === 'refunded') {
          main = offset > 0
            ? `抵扣 ${formatMoney(row.offset_amount_exact, row.currency)} · 现金退 ${formatMoney(row.refund_amount_exact, row.currency)}`
            : `已退现 ${formatMoney(row.refund_amount_exact, row.currency)}`
        } else if (row.status === 'completed') {
          main = offset > 0
            ? `已抵扣 ${formatMoney(row.offset_amount_exact, row.currency)} · 待登记现金退款 ${formatMoney(row.refund_amount_exact, row.currency)}`
            : '待财务结算：抵扣或退现'
        }
        return (
          <div>
            <div>{main}</div>
            {row.status === 'completed' && offset === 0 && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                可抵扣上限 {formatMoney(Math.max(0, Number(unreceivedAmount)), row.currency)}
              </div>
            )}
            {row.status === 'refunded' && cash > 0 && row.refund_reference && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                流水：{row.refund_reference}
              </div>
            )}
            {row.status === 'settled' && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>无现金退款</div>
            )}
            {row.refund_attachment_file_id && canViewPayment && (
              <Button
                theme="borderless"
                size="small"
                style={{ padding: 0, height: 'auto', fontSize: 12 }}
                title={row.refund_attachment_file_name ?? '退款附件'}
                onClick={() => {
                  void downloadReturnRefundAttachment(
                    row.id,
                    row.refund_attachment_file_name ?? `退款附件-${row.return_no}`,
                  ).catch((error: Error) => Toast.error(error.message || '退款附件下载失败'))
                }}
              >
                {row.refund_attachment_file_name ?? '退款附件'}
              </Button>
            )}
          </div>
        )
      },
    },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: 140,
      render: (value: string, row: SalesReturn) => (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          <Tag color={RETURN_TONE[row.status] ?? 'grey'}>{value}</Tag>
          {row.self_reviewed && <Tag color="orange">本人复核</Tag>}
        </div>
      ),
    },
    {
      title: '原因',
      dataIndex: 'reason',
      width: 150,
      render: (value: string, row: SalesReturn) => (
        <div>
          <div>{row.reason_label}</div>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{value}</div>
        </div>
      ),
    },
    {
      title: '操作',
      width: 230,
      render: (_: unknown, row: SalesReturn) => {
        const actions: ReactNode[] = []
        // 主管/管理员有 order:assign 就能复核；自己登记的也允许，靠「本人复核」留痕。
        // 业务员没有 order:assign，前端不会出现复核按钮，后端也会再挡一次。
        const mayReviewThis = canReviewReturn
        if (row.status === 'reviewing' && mayReviewThis) {
          actions.push(
            <Button key="approve" size="small" onClick={() => {
              reviewReturnMutation.reset()
              setReviewDecision('approve')
              setReviewNote('')
              setReviewTarget(row)
            }}>通过复核</Button>,
            <Button key="reject" size="small" theme="borderless" onClick={() => {
              reviewReturnMutation.reset()
              setReviewDecision('reject')
              setReviewNote('')
              setReviewTarget(row)
            }}>驳回</Button>,
          )
        }
        if (row.status === 'reviewing' && canPayment) {
          actions.push(
            <Button key="amount" size="small" theme="borderless" onClick={() => {
              updateReturnAmountMutation.reset()
              setAmountDraft(row.settled_goods_amount_exact ?? row.goods_amount_exact)
              setAmountNote('')
              setAmountTarget(row)
            }}>改金额</Button>,
          )
        }
        if (row.status === 'reviewing' && canManage) {
          actions.push(
            <Button key="cancel" size="small" theme="borderless" onClick={() => {
              cancelReturnMutation.reset()
              setCancelReturnReason('')
              setCancelReturnTarget(row)
            }}>取消登记</Button>,
          )
        }
        if (row.status === 'reviewing' && !mayReviewThis) {
          actions.push(
            <span key="review-wait" style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              等主管复核
            </span>,
          )
        }
        if (row.status === 'pending' && canManage) {
          actions.push(
            <Button key="cancel" size="small" theme="borderless" onClick={() => {
              cancelReturnMutation.reset()
              setCancelReturnReason('')
              setCancelReturnTarget(row)
            }}>取消登记</Button>,
          )
        }
        if (row.status === 'pending' && canPayment) {
          actions.push(
            <Button key="amount" size="small" onClick={() => {
              updateReturnAmountMutation.reset()
              setAmountDraft(row.settled_goods_amount_exact ?? row.goods_amount_exact)
              setAmountNote('')
              setAmountTarget(row)
            }}>填退货总额</Button>,
          )
        }
        if (row.status === 'completed' && canPayment) {
          actions.push(
            <Button key="amount" size="small" theme="borderless" onClick={() => {
              updateReturnAmountMutation.reset()
              setAmountDraft(row.settled_goods_amount_exact ?? row.goods_amount_exact)
              setAmountNote(row.settlement_note ?? '')
              setAmountTarget(row)
            }}>修改退货金额</Button>,
          )
          const legacyOffset = Number(row.offset_amount_exact) > 0
          if (legacyOffset && Number(row.refund_amount_exact) > 0) {
            actions.push(
              <Button key="refund" size="small" theme="outline" onClick={() => openSettlementModal(row)}>
                登记退款
              </Button>,
            )
          } else if (!legacyOffset) {
            actions.push(
              <Button key="settle" size="small" onClick={() => openSettlementModal(row)}>
                财务结算
              </Button>,
            )
          }
          actions.push(
            <Button key="void" size="small" theme="borderless" onClick={() => {
              voidReturnMutation.reset()
              setVoidReturnReason('')
              setVoidReturnTarget(row)
            }}>作废退货</Button>,
          )
        }
        if (row.status === 'completed' && !canPayment) {
          actions.push(
            <span key="refund-wait" style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              {Number(row.offset_amount_exact) > 0 ? '待财务登记现金退款' : '待财务结算'}
            </span>,
          )
        }
        if (row.status === 'rejected') {
          actions.push(
            <span key="review-note" style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              {row.review_note || '主管已驳回'}
            </span>,
          )
        }
        if (row.status === 'cancelled' || row.status === 'voided') {
          actions.push(
            <span key="reason" style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              {row.void_reason || row.cancel_reason || '已结束'}
            </span>,
          )
        }
        return actions.length ? <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>{actions}</div> : '-'
      },
    },
  ]

  // 指标卡数据（放在 return 之前，好让上面的 <KpiStrip> 按张数选列数）。
  // 「有异常才有意义」的三张只在有值时出现：0 的时候摆出来就是几张 ¥0.00 的空卡。
  const orderKpis: KpiItem[] = [
    { label: '订单金额', value: formatMoney(toTwoDecimals(order.total_amount_exact ?? order.total_amount), order.currency) },
    {
      label: '客户已付款（财务已确认）',
      value: formatMoney(toTwoDecimals(order.received_amount_exact ?? order.received_amount), order.currency),
      /* 2026-11-20：这枚 tone 原来是写死的 success —— 一分钱没收到时
         那张卡也是一个绿色的「¥0.00」，绿色在这里等于说"收到钱了"。
         改成收到钱才绿。 */
      tone: Number(order.received_amount_exact ?? order.received_amount ?? 0) > 0 ? 'success' : 'default',
    },
    {
      label: '客户未付款余额',
      value: formatMoney(toTwoDecimals(order.unreceived_amount_exact ?? order.unreceived_amount), order.currency),
      /* 2026-11-20：尚未到账不是异常 —— 刚下的单本来就一分钱没收。
         原来只要 >0 就染橙，等于每张新订单都亮一个橙灯，警告用滥了。
         真正的逾期由「回款中心」的逾期红字承担。 */
      tone: 'default',
    },
    {
      label: '已分配到应收期',
      help: '到账的钱已经分配到具体应收期的金额。剩下的会留在「已到账待分配」里。',
      value: formatMoney(toTwoDecimals(summary?.allocated_amount_exact ?? summary?.allocated_amount ?? 0), order.currency),
    },
    // 这两张也是「有异常才有意义」：钱都分配完、没有待确认的，
    // 它们就是两个 ¥0.00 的空卡，把 4+2 的第二行撑出来。
    ...((summary?.returned_amount ?? 0) > 0
      ? [
          {
            label: '已退货金额',
            help: '已确认退货总额（含货款和运费，按登记/财务确认后的金额）；系统不自动抵扣，财务结算时手动选择抵扣或退现。明细见「退货记录」。',
            value: formatMoney(
              toTwoDecimals(summary?.returned_amount_exact ?? summary?.returned_amount ?? 0),
              order.currency,
            ),
            tone: 'warning' as const,
          },
        ]
      : []),
    ...((summary?.unallocated_amount ?? 0) > 0
      ? [
          {
            label: '已到账待分配',
            help: '客户已经付款、但还没分到具体收款期的钱。分配后才能抵掉对应期客户欠款。',
            value: formatMoney(
              summary?.unallocated_amount_exact ?? summary?.unallocated_amount ?? 0,
              order.currency,
            ),
            tone: 'warning' as const,
          },
        ]
      : []),
    ...((summary?.pending_confirm_amount ?? 0) > 0
      ? [
          {
            label: '待确认回款',
            value: formatMoney(
              summary?.pending_confirm_amount_exact ?? summary?.pending_confirm_amount ?? 0,
              order.currency,
            ),
          },
        ]
      : []),
    // 逾期是「异常指标」：没有逾期时这张卡永远是 0，白占一个位置，
    // 还让 7 张卡排成 4+3 的第二行只剩三张。只有真逾期了才显示。
    ...(summary?.overdue_count
      ? [
          {
            label: '逾期应收节点',
            // 这一张是「几个节点」不是金额。只写一个「1」跟旁边四张 ¥ 金额
            // 并排，会被读成 ¥1（2026-11-16 订单详情巡检）。
            value: `${summary.overdue_count} 个`,
            tone: 'error' as const,
          },
        ]
      : []),
  ]

  return (
    <div className="page-container">
      <ConfirmRequiredModal
        visible={Boolean(erpGate)}
        message={erpGate}
        onCancel={() => setErpGate('')}
        onConfirm={() => {
          setErpGate('')
          syncMutation.mutate(true)
        }}
        confirmText="确认推送"
      />
      {/* 与客户/商机/报价详情页同排布：标题 + 标签一行，关键信息行在标题下方左对齐 */}
      <DetailHeader
        title={order.order_no}
        tags={
          <>
            <Tag color={STATUS_TONE[order.status] ?? 'grey'}>{order.status === 'completed' ? '交付已完成' : order.status_label}</Tag>
            {order.status === 'completed' && (
              <Tag color={hasUnreceived ? 'orange' : 'green'}>{hasUnreceived ? waitingForPayment : '已收清'}</Tag>
            )}
            {order.erp_order_id ? <Tag color="blue">ERP/MES：{order.erp_order_id}</Tag> : <Tag>未推送 ERP/MES</Tag>}
          </>
        }
        metaFull
        meta={
          <>
            <span>
              客户：
              <Link to={`/customers/${order.customer_id}`} style={{ color: 'var(--crm-primary)' }}>
                {order.customer_name}
              </Link>
            </span>
            <span>负责人：{order.owner_name ?? '-'}</span>
            {/* 交接过的单子：当前负责人与签单人不是同一个，得写清楚业绩算谁
                （文档 :61「交接后保留历史业绩归属」） */}
            {order.sales_owner_name && order.sales_owner_name !== order.owner_name && (
              <span style={{ color: 'var(--crm-text-3)' }}>
                业绩归属：{order.sales_owner_name}（签单时的负责人，交接不改）
              </span>
            )}
            {/* 原来 delivery_kind 没填时会渲染成「交期（类型待确认）」——
                "类型"指的是"发货日还是到货日"，是内部字段状态，摆在客户名旁边
                没人看得懂。分不清是哪种就一律叫「交期」，只在有明确类型时才细分。 */}
            <span>客户要求{order.delivery_kind === 'arrival' ? '到货日' : order.delivery_kind === 'shipping' ? '发货日' : '交期'}：{order.delivery_date ?? '-'}</span>
            {order.shipment_date && <span>计划发货日：{order.shipment_date}{order.delivery_kind === 'arrival' ? `（运输 ${order.transit_days} 天）` : ''}</span>}
            <span>付款条件：{order.payment_terms ?? '-'}</span>
            {order.quote_id ? (
              <span>
                来源报价：
                <Link
                  to={`/quotes/${order.quote_id}?version=${order.quote_version_id}`}
                  style={{ color: 'var(--crm-primary)' }}
                >
                  查看
                </Link>
              </span>
            ) : (
              // 手工建单没有报价来源。以前整项静默不渲染，看着像"数据丢了"。
              <span style={{ color: 'var(--crm-text-3)' }}>来源报价：手工建单（无报价单）</span>
            )}
          </>
        }
        extra={
          canManage && (
            <>
              {/* 主按钮随履约阶段走（2026-11-17 视觉审视 D-02）：原来 5 个按钮一样大、
                  一样显眼，其中「取消订单」是不可逆的，却和「推送 ERP/MES」并排站。
                  现在按状态只突出一个，其余进「更多操作」；每个按钮原来的权限判据一个字没动。 */}
              {(() => {
                const stage = order.status
                // 该做的那一件事
                const primary =
                  stage === 'cancelled'
                    ? null
                    : !order.erp_order_id && !erpUnavailable
                      ? 'push_erp'
                      : stage === 'pending' || stage === 'in_production'
                        ? 'update_status'
                        : stage === 'shipped'
                          ? 'update_status'
                          : 'repurchase'

                // 「更多」里的长尾动作（顺序即展示顺序）
                const more: { key: string; label: string; node: ReactNode }[] = []

                if (!primary || primary !== 'push_erp') {
                  more.push({
                    key: 'push_erp',
                    label: '推送 ERP/MES',
                    node: (
                      <Button onClick={() => syncMutation.mutate(false)} loading={syncMutation.isPending}>
                        推送 ERP/MES
                      </Button>
                    ),
                  })
                }
                if (primary !== 'update_status' && stage !== 'cancelled') {
                  more.push({
                    key: 'update_status',
                    label: '人工更新履约状态',
                    node: <Button onClick={() => openStatusEditor()}>人工更新</Button>,
                  })
                }
                more.push({
                  key: 'sync_erp',
                  label: '从 ERP 同步',
                  node: (
                    <GuardedButton
                      onClick={() => statusSyncMutation.mutate()}
                      loading={statusSyncMutation.isPending}
                      disabled={statusSyncReasons.length > 0}
                      blockedReasons={statusSyncReasons}
                      action="从 ERP 同步"
                    >
                      从 ERP 同步
                    </GuardedButton>
                  ),
                })
                if (primary !== 'repurchase') {
                  more.push({
                    key: 'repurchase',
                    label: '复购开新商机',
                    node: (
                      <Popconfirm
                        title="确认创建复购商机？"
                        content="将按本订单生成一条新的商机记录，避免误点造成重复商机。"
                        disabled={repurchaseMutation.isPending}
                        onConfirm={() => repurchaseMutation.mutate()}
                      >
                        <Button loading={repurchaseMutation.isPending}>复购开新商机</Button>
                      </Popconfirm>
                    ),
                  })
                }
                if (stage === 'cancelled' && canManage) {
                  more.push({
                    key: 'restore',
                    label: '恢复订单',
                    node: (
                      <Popconfirm
                        title="恢复这张已取消的订单？"
                        content="恢复后订单编号与明细保留；原应收计划与发货批次仍为已取消，需要重新生成或重排。已发生发货或已有已确认回款的订单不能恢复。"
                        onConfirm={() => restoreMutation.mutate()}
                      >
                        <Button loading={restoreMutation.isPending}>恢复订单</Button>
                      </Popconfirm>
                    ),
                  })
                }
                if (stage !== 'cancelled') {
                  more.push({
                    key: 'cancel',
                    label: '取消订单',
                    node: (
                      <Button type="danger" onClick={() => {
                        setOrderMoreVisible(false)
                        cancelMutation.reset()
                        setCancelReason('')
                        setCancelVisible(true)
                      }}>
                        取消订单
                      </Button>
                    ),
                  })
                }

                return (
                  <>
                    {primary === 'push_erp' && (
                      <Button type="primary" theme="solid" onClick={() => syncMutation.mutate(false)}
                        loading={syncMutation.isPending}>
                        推送 ERP/MES
                      </Button>
                    )}
                    {primary === 'update_status' && (
                      <Button type="primary" theme="solid" onClick={() => openStatusEditor()}>
                        人工更新履约状态
                      </Button>
                    )}
                    {primary === 'repurchase' && (
                      <Popconfirm
                        title="确认创建复购商机？"
                        content="将按本订单生成一条新的商机记录，避免误点造成重复商机。"
                        disabled={repurchaseMutation.isPending}
                        onConfirm={() => repurchaseMutation.mutate()}
                      >
                        <Button type="primary" theme="solid" loading={repurchaseMutation.isPending}>
                          复购开新商机
                        </Button>
                      </Popconfirm>
                    )}
                    {more.length > 0 && (
                      <Dropdown
                        trigger="click"
                        position="bottomRight"
                        visible={orderMoreVisible}
                        onVisibleChange={setOrderMoreVisible}
                        clickToHide
                        render={
                          <Dropdown.Menu>
                            {more.map((item) => (
                              <Dropdown.Item key={item.key}>
                                <div onClick={(e) => e.stopPropagation()}>{item.node}</div>
                              </Dropdown.Item>
                            ))}
                          </Dropdown.Menu>
                        }
                      >
                        <Button type="tertiary" style={{ marginLeft: 8 }}>
                          更多操作 ▾
                        </Button>
                      </Dropdown>
                    )}
                  </>
                )
              })()}
            </>
          )
        }
      >
        {/* 订单上已经有 ERP/MES 单号（历史导入或人工录入）时，再说"当前还未接入"
            自相矛盾：头部徽标写着 ERP-00000013，下面却说没接入。
            有单号就只提示"可手动更新履约进度"，没单号才说缺接入。 */}
        {canManage && order.status !== 'cancelled' && erpUnavailable && !order.erp_order_id && (
          <div className="detail-hint">{erpUnavailableHint} 履约进度可在本页手动更新。</div>
        )}
        {canManage && order.status !== 'cancelled' && erpUnavailable && order.erp_order_id && (
          <div className="detail-hint">ERP/MES 目前是手动同步：单号 {order.erp_order_id} 由人工登记，履约进度可在本页手动更新。</div>
        )}
        {!erpUnavailable && !order.erp_order_id && canManage && order.status !== 'cancelled' && (
          <div className="detail-hint">
            本单还没有 ERP/MES 单号：先点「推送 ERP/MES」，等对方回单号后才能同步状态。
          </div>
        )}
        <KpiStrip
          // 指标个数是浮动的：订单金额 / 实际到账 / 尚未到账 / 已分配到应收期 这 4 张常在，
          // 再加上逾期、已到账待分配、待确认回款，最多 7 张。
          // kpi-grid 的最小卡宽是 240px，1130 可用宽度下只排得下 4 列 ——
          // 5 张时第 5 张被甩到第二行、右边空出四分之三（2026-11-17 巡检）。
          // 5 张走 kpi-cols-5、6 张走 kpi-cols-6；7 张仍按 4+3，再挤数字就认不出来了。
          // 这两个类自带窄屏断点：≤1370 按 3 列，≤1000 按 230/240px 最小卡宽自动收列，
          // 5 张的末张在窄屏横跨整行（2+2+整行），不会剩半行空白。
          className={`kpi-grid${orderKpis.length === 5 ? ' kpi-cols-5' : orderKpis.length === 6 ? ' kpi-cols-6' : ''}`}
          style={{ marginTop: 16, marginBottom: 0 }}
          items={orderKpis}
        />
        {financeNext && activeKey !== financeNext.tab && (
          <div className="workflow-next" style={{ marginTop: 12 }}>
            <strong>收款下一步</strong>
            <p className="workflow-guide">{financeNext.hint}</p>
            {/* 原来是默认 primary 的浅蓝实底药丸 —— 主人不喜欢蓝色整块填充，
                这里推进到主按钮语言：白底 + 主色描边 + 主色字。 */}
            <Button size="small" type="primary" theme="outline" onClick={() => {
              setActiveKey(financeNext.tab)
              orderTabsRef.current?.scrollIntoView({ block: 'start' })
              orderTabsRef.current?.focus({ preventScroll: true })
            }}>{financeNext.label}</Button>
          </div>
        )}
        {/* 订单金额组成（2026-10-09「产品价格与运费分离」）：订单页要能解释
            "这个总额是怎么来的"，不能只给一个数。运费是代收代付的钱 ——
            客户全额承担、公司原额付给承运商，所以它既不是收入也不是成本。
            ⚠️ 这几行都已含在订单金额里，只是拆分展示，不再相加。 */}
        {order.goods_amount != null ? (
          <div
            style={{
              marginTop: 12,
              display: 'flex',
              flexWrap: 'wrap',
              gap: 16,
              fontSize: 13,
              color: 'var(--crm-text-3)',
            }}
          >
            <span>金额组成：</span>
            <span>产品货款 {formatMoney(order.goods_amount_exact ?? order.goods_amount, order.currency)}</span>
            <span>+ 运费（代收代付） {formatMoney(order.logistics_amount_exact ?? order.logistics_amount ?? 0, order.currency)}</span>
            <span>+ 其他费用 {formatMoney(order.other_charge_amount_exact ?? order.other_charge_amount ?? 0, order.currency)}</span>
            <span>+ 优惠 {formatMoney(order.discount_amount_exact ?? order.discount_amount ?? 0, order.currency)}</span>
            <span style={{ color: 'var(--crm-text)' }}>
              = {formatMoney(order.total_amount_exact ?? order.total_amount, order.currency)}
            </span>
          </div>
        ) : (
          // 历史订单没有留存组成：如实说"待核实"，不回查报价拿今天的数冒充当时那一单
          <div style={{ marginTop: 12, fontSize: 13, color: 'var(--crm-text-3)' }}>
            金额组成：待核实（该订单转单时未留存货款/运费/其他费用的拆分）
          </div>
        )}
      </DetailHeader>

      <SectionCard>
        <div ref={orderTabsRef} tabIndex={-1}>
          <Tabs type="line" activeKey={activeKey} onChange={setActiveKey} tabList={TABS} />
        </div>

        <div style={{ marginTop: 16 }}>
          {activeKey === 'items' && (
            <Table<OrderItem>
              columns={itemColumns}
              dataSource={itemsQuery.data ?? []}
              loading={itemsQuery.isLoading}
              rowKey="id"
              pagination={false}
            />
          )}

          {activeKey === 'bizdocs' && (
            /* 一份都没生成过时不画那张 9 列空表头（列表头 + 一行"还没有生成过"
               就占 90px、信息量为零），只留一行说明 */
            <BizDocPanel
              docType="order_sheet"
              orderId={orderId}
              canManage={canManage}
              compact
              emptyInline
              compactEmptyText="还没有生成过对外单据 —— 点右上「生成一份」，按当前订单内容出一份 PDF。"
            />
          )}

          {activeKey === 'status' && (
            <Table<OrderStatusRow>
              columns={[
                { title: '变更', width: 240, render: (_: unknown, r: OrderStatusRow) =>
                    `${r.old_status_label ?? '新建'} → ${r.new_status_label}` },
                { title: '来源', dataIndex: 'source', width: 110 },
                { title: '操作人', dataIndex: 'operator_name', width: 110, render: (v: string | null) => v ?? '系统' },
                { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
                {
                  title: '变更时间',
                  dataIndex: 'created_at',
                  width: 190,
                  render: (v: string) => formatBeijingTime(v),
                },
              ]}
              dataSource={statusQuery.data ?? []}
              loading={statusQuery.isLoading}
              rowKey="id"
              pagination={false}
            />
          )}

          {activeKey === 'receivables' && (
            <>
              <div className="toolbar">
                <p className="workflow-guide">
                  {orderCancelled ? '订单已取消，不能新增应收或登记回款。'
                    : !receivablesQuery.isSuccess ? '应收计划记录每期应收多少钱、什么时候收。'
                      : !receivables?.length ? (canPayment ? '先按客户约定生成各期应收。' : '请财务先建立应收计划。')
                        : '客户打款后，在对应期点「登记回款」。同一期可以分多次收，财务确认后才计入已收金额。'}
                </p>
                <div style={{ flex: 1 }} />
                {canGenerateReceivables && (
                  <Button type="primary"
                    theme="outline"
                    onClick={() => {
                      setGenerateRows(defaultPlanRows())
                      generateMutation.reset()
                      setGenerateVisible(true)
                    }}
                  >
                    生成应收计划
                  </Button>
                )}
              </div>
              <Table<Receivable>
                columns={receivableColumns}
                dataSource={receivablesQuery.data ?? []}
                loading={receivablesQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={emptyText(receivablesQuery, '还没有应收计划')}
              />
            </>
          )}

          {activeKey === 'payments' && (
            <>
            {paymentsQuery.isSuccess && (showPaymentRetry ? (
              <div className="workflow-next" style={{ marginBottom: 12 }}>
                <strong>已驳回的回款</strong>
                <p className="workflow-guide">驳回的记录不计入已收金额。如需重新登记，请先核对到账金额和凭证。</p>
                <Button size="small" type="primary" theme="outline" onClick={() => setActiveKey('receivables')}>
                  去应收计划
                </Button>
              </div>
            ) : <p className="workflow-guide">
              {orderCancelled ? '订单已取消，这里保留原回款记录。' : pendingPayments > 0
                ? canPayment ? '请核对银行到账和凭证，再确认待处理的回款。' : '待确认的回款由财务核对，确认后计入已收金额。需要补凭证时，请交给财务处理。'
                : '已确认的款项计入已收金额。要查看客户还欠哪一期，可切到「应收计划」。'}
            </p>)}
            <Table<Payment>
              className="order-tab-table"
              columns={[
                /* 这一排列宽加起来原来 1150，卡片只有 1130 —— 最后一列「操作」
                   （调整分配）被右边缘直接切掉一半（2026-11-20 截图）。
                   同时「应收节点」110 放不下「尾款（70%）」，一律截成「尾款（70%…」。
                   重新分：节点给到 150，凭证 190→160，其余各收 10~20。 */
                { title: '应收节点', dataIndex: 'plan_name', width: narrowScreen ? 130 : 150 },
                { title: '收款日期', dataIndex: 'received_date', width: narrowScreen ? 112 : 114 },
                { title: '整笔到账', dataIndex: 'received_amount', width: narrowScreen ? 112 : 114, className: 'cell-money', render: (v: number, r: Payment) => formatMoney(r.received_amount_exact ?? v, r.currency ?? order.currency) },
                /* 108：¥94,500.00 是这里最长的一档金额，100 宽会被截成「¥94,500.…」 */
                { title: '已分配', dataIndex: 'allocated_amount', width: 108, className: 'cell-right col-hide-narrow', render: (v: number, r: Payment) => r.status === 'confirmed' ? formatMoney(r.allocated_amount_exact ?? v, r.currency ?? order.currency) : '-' },
                { title: '待分配', dataIndex: 'unallocated_amount', width: 108, className: 'cell-right col-hide-narrow', render: (v: number, r: Payment) => r.status === 'confirmed' ? formatMoney(r.unallocated_amount_exact ?? v, r.currency ?? order.currency) : '-' },
                { title: '方式', dataIndex: 'payment_method', width: 90, className: 'col-hide-narrow', render: (v: string | null) => v ?? '-' },
                {
                  title: '回款凭证',
                  width: narrowScreen ? 130 : 160,
                  render: (_: unknown, record: Payment) => (
                    <PaymentVoucherControl
                      payment={record}
                      onChanged={() => {
                        void queryClient.invalidateQueries({ queryKey: ['order-payments', orderId] })
                        void queryClient.invalidateQueries({ queryKey: ['payments'] })
                      }}
                    />
                  ),
                },
                {
                  title: '状态',
                  dataIndex: 'status_label',
                  width: narrowScreen ? 110 : 130,
                  render: (value: string, record: Payment) => (
                    <div>
                      <Tag color={value === '已确认' ? 'green' : value === '已驳回' ? 'red' : value === '已作废' ? 'grey' : 'orange'}>{value}</Tag>
                      {record.status === 'voided' && (
                        <div
                          title={record.void_reason ?? ''}
                          style={{ fontSize: 11, color: 'var(--crm-text-3)', marginTop: 4, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                        >
                          {record.void_reason ?? '未填写原因'}
                        </div>
                      )}
                    </div>
                  ),
                },
                /* 全部回款都没有确认人时这一列整列挂「-」，白占 90 宽 ——
                   按「整列全空就不渲染」的口径收掉；有数据时原样出现。 */
                ...((paymentsQuery.data ?? []).some((p) => p.confirmed_by_name)
                  ? [
                      {
                        title: '确认人',
                        dataIndex: 'confirmed_by_name',
                        width: 90,
                        className: 'col-hide-narrow',
                        onHeaderCell: () => ({ className: 'col-hide-narrow' }),
                        render: (v: string | null) => v ?? '-',
                      },
                    ]
                  : []),
                {
                  title: '操作',
                  width: narrowScreen ? 120 : 150,
                  render: (_: unknown, record: Payment) => (
                    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                      {record.status === 'pending' && canPayment && !orderCancelled && (
                        <Popconfirm
                          title="确认收到这笔回款？"
                          content={`请核对到账日期和金额 ${formatMoney(record.received_amount_exact ?? record.received_amount, record.currency ?? order.currency)}。确认后会计入实收，并按规则分配到应收期。`}
                          onConfirm={() => confirmMutation.mutate(record.id)}
                        >
                          <Button size="small" theme="borderless">确认回款</Button>
                        </Popconfirm>
                      )}
                      {record.status === 'confirmed' && canPayment && (
                        <>
                          {/* 只有已确认的回款才谈得上"这笔钱分到哪几期"。
                              列表上把**实际分配**也带出来（超收时与登记的期不同），
                              所以入口旁边直接写出"分了 N 期"，不用点开才知道。 */}
                          <button type="button" className="crm-text-action" onClick={() => setAllocationPayment(record)}>
                            调整分配
                            {(record.allocations?.length ?? 0) > 1
                              ? `（${record.allocations!.length} 期）`
                              : ''}
                          </button>
                          {/* 钱已入账后客户退单/我方放弃：作废是唯一"能把已收金额退回去"的入口。
                              不删记录、不删凭证，原因必填。 */}
                          <button
                            type="button"
                            className="crm-text-action"
                            style={{ color: 'var(--crm-danger, #c0392b)' }}
                            onClick={() => { setVoidReason(''); setVoidTarget(record) }}
                          >
                            作废回款
                          </button>
                        </>
                      )}
                      {record.status !== 'pending' && record.status !== 'confirmed' && '-'}
                    </div>
                  ),
                },
              ]}
              dataSource={paymentsQuery.data ?? []}
              loading={paymentsQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty={emptyText(paymentsQuery, '还没有回款记录', orderCancelled ? undefined : '客户打款后，可在「应收计划」找到对应期登记。')}
            />
            </>
          )}

          {activeKey === 'milestones' && (
            <>
              <div className="toolbar" style={{ marginBottom: 10 }}>
                <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                  通过「交期与计划」预览自然日倒排建议，责任人确认后生效。实际完成另行登记。
                </span>
                <div style={{ flex: 1 }} />
                {can('order:manage') && (
                  <Button type="primary"
                    size="small"
                    theme="outline"
                    style={{ marginLeft: 8 }}
                    onClick={() => {
                      setScheduleForm({
                        new_delivery_date: order?.delivery_date ?? '', reason: '',
                        delivery_kind: order.delivery_kind ?? 'shipping', transit_days: order.transit_days ?? 0,
                        plan_offsets: order.plan_offsets ?? { contract: 30, deposit: 28, pre_sample_sent: 20, pre_sample_confirmed: 15, first_shipment: 0, payment: -15 },
                      })
                      setSchedulePreview(null)
                      setScheduleVisible(true)
                    }}
                  >
                    交期与计划
                  </Button>
                )}
              </div>
              {/* 交期变更历史（方案 :105「展示受影响节点及批次」「保留修改前后版本」）：
                  待确认的单要能在这里确认，已确认的单留着当时的前后对比 */}
              {(scheduleChangesQuery.data?.length ?? 0) > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 6 }}>
                    交期变更记录（最新在上）
                  </div>
                  {scheduleChangesQuery.data!.map((row) => (
                    <div
                      key={row.id}
                      style={{
                        border: '1px solid var(--crm-border, #e5e5e5)',
                        borderRadius: 6,
                        padding: '8px 10px',
                        marginBottom: 6,
                        fontSize: 12,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Tag color={row.status === 'confirmed' ? 'green' : 'orange'}>
                          {row.status_label}
                        </Tag>
                        <span>
                          客户要求日期 {row.old_delivery_date ?? '未设'} → <b>{row.new_delivery_date}</b>
                        </span>
                        <span style={{ color: 'var(--crm-text-3)' }}>
                          受影响：节点 {row.affected?.nodes?.length ?? 0} 个、批次{' '}
                          {row.affected?.batches?.length ?? 0} 个
                        </span>
                        <div style={{ flex: 1 }} />
                        {row.status === 'pending' && can('order:manage') && (
                          <>
                            {/* 重排会真的改计划交期，同排的「作废」早有确认，这个也该有 */}
                            <Popconfirm
                              title="确认并重排交期？"
                              content="确认后这张变更单会生效，相关批次的计划日期会被改写。"
                              onConfirm={() => confirmScheduleMutation.mutate(row.id)}
                            >
                              <Button type="primary"
                                size="small"
                                theme="outline"
                                loading={confirmScheduleMutation.isPending}
                              >
                                确认并重排
                              </Button>
                            </Popconfirm>
                            {/* 作废出口：库上"一单只允许一张待确认"，
                                没有它这张单会永久堵死该订单之后的交期变更 */}
                            <Popconfirm
                              title="作废这张交期变更单？"
                              content="不会改动任何计划日期，之后可以重新发起。"
                              onConfirm={() => cancelScheduleMutation.mutate(row.id)}
                            >
                              <button type="button" className="crm-text-action" style={{ marginLeft: 8, color: 'var(--crm-text-3)' }}>作废</button>
                            </Popconfirm>
                          </>
                        )}
                      </div>
                      {row.affected?.planning && <div style={{ marginTop: 4 }}>
                        {row.affected.planning.after.delivery_kind === 'arrival' ? '到货日' : '发货日'}；运输 {row.affected.planning.after.transit_days} 天；
                        建议发货日 {row.affected.new_shipment_date}（自然日）
                      </div>}
                      <details style={{ marginTop: 6 }}><summary>查看计划前后及实际生效记录</summary>
                        {[['提交时建议', row.affected], ['确认时生效', row.affected?.applied]].map(([label, value]) => {
                          const snapshot = value as typeof row.affected
                          if (!snapshot) return null
                          return <div key={String(label)} style={{ marginTop: 6 }}><b>{String(label)}</b>
                            {snapshot.nodes.map(n => <div key={n.node}>{n.label}：{n.before ?? '未排期'} → {n.after ?? '未排期'}</div>)}
                            {snapshot.batches.map(b => <div key={b.batch_id}>第 {b.batch_no} 批：{b.before ?? '未排期'} → {b.after ?? '未排期'}</div>)}
                            {snapshot.batches_to_confirm?.map(b => <div key={`confirm-${b.batch_id}`} style={{ color: 'var(--crm-text-3)' }}>第 {b.batch_no} 批：计划 {b.planned_date}，系统未自动调整，需人工改计划日</div>)}
                          </div>
                        })}
                      </details>
                      {row.reason && <div style={{ marginTop: 4 }}>原因：{row.reason}</div>}
                      {row.confirmed_at && (
                        <div style={{ marginTop: 2, color: 'var(--crm-text-3)' }}>
                          由 {row.confirmed_by_name ?? row.confirmed_by} 于{' '}
                          {formatBeijingTime(row.confirmed_at)} 确认
                          {row.confirm_remark ? `（${row.confirm_remark}）` : ''}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              <Table<OrderMilestoneRow>
                /* 八列在 1024 装不下（卡内 699，实测顶出横向滚动条、「操作」整列被切）。
                   fixed 布局 + 窄屏隐藏「责任人」「逾期原因」两列（.col-hide-narrow），
                   「备注 / 跳过原因」吃剩余宽度。 */
                className="order-tab-table"
                columns={[
                  { title: '节点', dataIndex: 'label', width: 140 },
                  {
                    title: '计划日期',
                    dataIndex: 'planned_date',
                    width: 130,
                    render: (v: string | null) => v ?? '-',
                  },
                  {
                    title: '实际日期',
                    dataIndex: 'actual_date',
                    width: 130,
                    render: (v: string | null) => v ?? '-',
                  },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    width: 100,
                    render: (v: string, record: OrderMilestoneRow) => (
                      <Tag color={MILESTONE_TONE[v] ?? 'grey'}>{record.status_label}</Tag>
                    ),
                  },
                  // 这三列一直是打横杠的：备注/责任人/逾期原因在多数订单里根本没填，
                  // 全空还占掉 280px，把「节点 / 计划 / 实际 / 状态」挤得很紧（2026-11-20 截图）。
                  // 整列都没值就不画这一列，有值时自动回来。
                  ...((milestonesQuery.data ?? []).some((row) => (row.status === 'skipped' ? row.skip_reason : row.remark))
                    ? [{ title: '备注 / 跳过原因', dataIndex: 'remark', render: (v: string | null, row: OrderMilestoneRow) => (row.status === 'skipped' ? row.skip_reason : v ?? '-') }]
                    : []),
                  ...((milestonesQuery.data ?? []).some((row) => row.owner_id)
                    ? [{
                        // 方案 :103：责任人 / 来源证据 / 逾期原因要能被看到，否则填了也没人用
                        title: '责任人',
                        dataIndex: 'owner_id',
                        width: 100,
                        className: 'col-hide-narrow',
                        onHeaderCell: () => ({ className: 'col-hide-narrow' }),
                        render: (v: number | null, row: OrderMilestoneRow) =>
                          v ? (row.owner_name ?? '-') : '-',
                      }]
                    : []),
                  ...((milestonesQuery.data ?? []).some((row) => row.overdue_reason)
                    ? [{
                        title: '逾期原因',
                        dataIndex: 'overdue_reason',
                        width: 180,
                        className: 'col-hide-narrow',
                        onHeaderCell: () => ({ className: 'col-hide-narrow' }),
                        render: (v: string | null) => v ?? '-',
                      }]
                    : []),
                  ...(canManage
                    ? [
                        {
                          title: '操作',
                          width: 80,
                          render: (_: unknown, record: OrderMilestoneRow) => (
                            <button type="button" className="crm-text-action" onClick={() => openMilestoneEdit(record)}>登记</button>
                          ),
                        },
                      ]
                    : []),
                ]}
                dataSource={milestonesQuery.data ?? []}
                loading={milestonesQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty="暂无里程碑"
              />
            </>
          )}

          {activeKey === 'shipments' && (
            <>
              <div className="toolbar order-shipment-toolbar">
                <span className="order-shipment-quantities">
                  订购 {shipmentQuantity(shipmentsQuery.data?.summary.ordered_exact ?? shipmentsQuery.data?.summary.ordered ?? 0)} ｜ 已发 {shipmentQuantity(shipmentsQuery.data?.summary.shipped_exact ?? shipmentsQuery.data?.summary.shipped ?? 0)} ｜
                  {' '}未发 <strong>{shipmentQuantity(shipmentsQuery.data?.summary.remaining_exact ?? shipmentsQuery.data?.summary.remaining ?? 0)}</strong>
                </span>
                {canManage && (
                  <Button type="primary" size="small" theme="outline" disabled={Boolean(shipmentPlanBlocked)} onClick={() => {
                    shipmentKeyRef.current = newRequestKey()
                    setPlannedQtys({})
                    setShipmentPlanDate(null)
                    setCreateShipmentVisible(true)
                  }}>
                    排发货批次
                  </Button>
                )}
              </div>
              {canManage && shipmentPlanBlocked && <p className="workflow-guide">{shipmentPlanBlocked}</p>}
              {/* 整单交期结论（§9.8 复审）：发完才给最终数字；没发完如实说"未完成 · 还剩几件" */}
              {(!orderCancelled || shipment?.summary.all_shipped) && <ShipmentVerdict summary={shipment?.summary} />}
              {canManage && shipment?.summary.all_shipped && !orderCancelled && order.status !== 'completed' && (
                <div className="workflow-next" style={{ marginBottom: 12 }}>
                  <strong>发货已登记完毕</strong>
                  <p className="workflow-guide">确认交付完成后，财务可继续按约定收款。</p>
                  <Button size="small" theme="outline" onClick={() => openStatusEditor('completed')}>完成交付</Button>
                </div>
              )}
              <Table<ShipmentBatchRow>
                className="order-tab-table"
                columns={[
                  { title: '批次', dataIndex: 'batch_no', width: 80, render: (v: number) => `第 ${v} 批` },
                  {
                    title: '状态',
                    dataIndex: 'status_label',
                    width: 100,
                    render: (v: string, record: ShipmentBatchRow) => (
                      <Tag color={record.status === 'shipped' ? 'green' : record.status === 'cancelled' ? 'grey' : 'orange'}>{v}</Tag>
                    ),
                  },
                  { title: '计划发货', dataIndex: 'planned_date', width: 120, render: (v: string | null) => v ?? '-' },
                  { title: '实际发货', dataIndex: 'actual_ship_date', width: 120, render: (v: string | null) => v ?? '-' },
                  {
                    title: '物流',
                    width: 170,
                    className: 'col-hide-narrow',
                    onHeaderCell: () => ({ className: 'col-hide-narrow' }),
                    render: (_: unknown, record: ShipmentBatchRow) =>
                      record.tracking_no ? `${record.logistics_company ?? ''} ${record.tracking_no}` : '-',
                  },
                  {
                    title: '本批偏差',
                    width: 110,
                    className: 'col-hide-narrow',
                    onHeaderCell: () => ({ className: 'col-hide-narrow' }),
                    render: (_: unknown, record: ShipmentBatchRow) => {
                      const d = record.deviation_days
                      if (d === null || d === undefined) return '-'
                      // 这是**批次自己的**偏差，和上面的"整单结论"不是一回事：
                      // 未发的批次按"今天 − 计划"算已拖几天，也算在这里。
                      return (
                        <span style={{ color: record.late ? 'var(--crm-danger, #d45)' : undefined }}>
                          {d > 0 ? `晚 ${d} 天` : d < 0 ? `早 ${Math.abs(d)} 天` : '准时'}
                        </span>
                      )
                    },
                  },
                  {
                    title: '本批明细',
                    // 按**批次状态**选数量（C4-05 补修，2026-10-10）：
                    //
                    // 原来写的是 `item.shipped_qty ?? item.planned_qty`。
                    // 但 `shipped_qty` 后端**总是返回数字**（未发货时是 0），
                    // 而 `??` 只对 null/undefined 兜底 —— **0 不触发回退**：
                    //   待发批次  planned=10, shipped=0  → 显示「×0」❌（应为计划量 10）
                    //   已发批次  planned=10, shipped=0  → 显示「×0」✅（这一行确实没发）
                    // **同一个 0 在两种状态下意思完全相反**，所以判据只能是状态：
                    //   已发货 → 实发量（保留那个 0，它是事实）；其余（待发/在途）→ 计划量。
                    // 顺带把前缀写清是"计划"还是"实发"，免得两个数字看起来一样却含义不同。
                    render: (_: unknown, record: ShipmentBatchRow) => {
                      const shipped = record.status === 'shipped'
                      // 同一个 SKU 在一批里往往拆成两行（分两次生产/入库），
                      // 原样拼出来就是「周转箱03-1 实发 1200；周转箱03-1 实发 1800」，
                      // 而列宽只有 232，后半截被截成省略号（2026-11-20 截图）。
                      // 同 SKU 先合并再加总，一行说完。
                      // 合并按「千分之一」整数加：先 Number() 会把 9999999999999.999
                      // 变成 9999999999999.998，大数量下末位就漂了。
                      const bySku = new Map<string, bigint>()
                      record.items.forEach((item) => {
                        const qty = shipped
                          ? item.shipped_qty_exact ?? item.shipped_qty
                          : item.planned_qty_exact ?? item.planned_qty
                        const key = item.sku ?? String(item.order_item_id)
                        bySku.set(key, (bySku.get(key) ?? 0n) + shipmentQuantityUnits(String(qty ?? 0)))
                      })
                      const tag = shipped ? '实发' : '计划'
                      const text = [...bySku]
                        .map(([sku, units]) => {
                          const qty = `${units / 1000n}.${String(units % 1000n).padStart(3, '0')}`
                          return `${sku} ${tag} ${shipmentQuantity(qty)}`
                        })
                        .join('；')
                      return text ? <span title={text}>{text}</span> : '-'
                    },
                  },
                  ...(can('order:manage')
                    ? [
                        {
                          title: '操作',
                          width: 260,
                          render: (_: unknown, record: ShipmentBatchRow) =>
                            record.status === 'planned' ? (
                              <span style={{ display: 'inline-flex', gap: 12 }}>
                                <button type="button" className="crm-text-action" onClick={() => openShipment(record)}>登记发货</button>
                                <button
                                  type="button"
                                  className="crm-text-action"
                                  onClick={() => {
                                    setRescheduleTarget(record)
                                    setRescheduleForm({ date: record.planned_date ?? null, reason: '' })
                                  }}
                                >
                                  改计划日
                                </button>
                                <Popconfirm
                                  title={`取消第 ${record.batch_no} 批发货安排？`}
                                  content="这一批会从发货列表移出，计划数量可以重新安排发货。"
                                  disabled={cancelShipmentMutation.isPending}
                                  onConfirm={() => cancelShipmentMutation.mutate(record.id)}
                                >
                                  <button type="button" className="crm-text-action" style={{ color: 'var(--crm-error)' }}>取消批次</button>
                                </Popconfirm>
                              </span>
                            ) : (
                              '-'
                            ),
                        },
                      ]
                    : []),
                ]}
                dataSource={shipmentsQuery.data?.batches ?? []}
                loading={shipmentsQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={emptyText(shipmentsQuery, '还没有发货批次',
                  canManage && !shipmentPlanBlocked ? '可用「排发货批次」安排这一单。' : undefined)}
              />
            </>
          )}

          {activeKey === 'returns' && (
            <>
              <div className="toolbar">
                <p className="workflow-guide">
                  只退已经发出去的产品，按产品汇总数量，不再选发货批次。登记后先由主管复核，
                  登记时填本次退货总额（含货款和运费），主管复核后由财务核对/修改金额，并手动选择抵扣未付款或现金退还；系统不自动抵扣。
                  {returnReviewingCount > 0 ? ` 当前有 ${returnReviewingCount} 张待主管复核。` : ''}
                  {returnPendingCount > 0 ? ` 当前有 ${returnPendingCount} 张历史单待补退货总额。` : ''}
                  {returnRefundPendingCount > 0 ? ` 当前有 ${returnRefundPendingCount} 张待财务结算。` : ''}
                </p>
                <div style={{ flex: 1 }} />
                {canManage && (
                  <Button
                    type="primary"
                    theme="outline"
                    disabled={returnableRows.length === 0}
                    onClick={() => {
                      createReturnMutation.reset()
                      setReturnReasonCode('customer_rejected')
                      setReturnReason('')
                      setReturnRemark('')
                      setReturnRefundAmount('')
                      setReturnDate(new Date())
                      setReturnQty({})
                      setReturnVisible(true)
                    }}
                  >
                    登记退货
                  </Button>
                )}
              </div>
              {canManage && returnableQuery.isSuccess && returnableRows.length === 0 && (
                <p className="workflow-guide">
                  {returnableRows.length
                    ? '这张订单的已实发数量已经全部退完，暂无可退数量。'
                    : '这张订单还没有已发出去的产品，暂时不能登记退货。'}
                </p>
              )}
              <Table<SalesReturn>
                columns={returnColumns}
                dataSource={returns}
                loading={returnsQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={emptyText(returnsQuery, '还没有退货登记',
                  canManage && returnableRows.length ? '客户拒收/退错货时，点「登记退货」发起。' : undefined)}
              />
            </>
          )}
        </div>
      </SectionCard>

      <Modal
        width={520}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title={rescheduleTarget ? `改第 ${rescheduleTarget.batch_no} 批计划发货日` : '改计划发货日'}
        visible={Boolean(rescheduleTarget)}
        onCancel={() => {
          setRescheduleTarget(null)
          setRescheduleForm({ date: null, reason: '' })
        }}
        onOk={() => rescheduleShipmentMutation.mutate()}
        confirmLoading={rescheduleShipmentMutation.isPending}
        okText="保存计划日"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            只改这一批的<strong>计划</strong>发货日，不动任何已经发生的实际发货记录。
            保存后审计会保留改前、改后和原因。
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>新的计划发货日</div>
            <DatePicker
              type="date"
              format="yyyy-MM-dd"
              style={{ width: '100%' }}
              disabled={rescheduleShipmentMutation.isPending}
              value={rescheduleForm.date ? new Date(rescheduleForm.date) : undefined}
              onChange={(_, dateStr) =>
                setRescheduleForm({ ...rescheduleForm, date: (dateStr as string) || null })
              }
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>调整原因（必填）</div>
            <Input
              aria-label="调整计划日原因"
              placeholder="例如：客户改交期，批次顺延"
              disabled={rescheduleShipmentMutation.isPending}
              value={rescheduleForm.reason}
              onChange={(value) => setRescheduleForm({ ...rescheduleForm, reason: value })}
            />
          </div>
        </div>
      </Modal>

      <Modal
        width={520}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title="排发货批次"
        visible={createShipmentVisible}
        onCancel={() => setCreateShipmentVisible(false)}
        onOk={() => createShipmentMutation.mutate()}
        confirmLoading={createShipmentMutation.isPending}
        okText="保存批次"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            填本批准备发出的数量，其他产品可留空。实际发出后，再回到批次登记发货。
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>计划发货日期（选填）</div>
            <DatePicker
              aria-label="计划发货日期"
              type="date"
              format="yyyy-MM-dd"
              value={shipmentPlanDate ?? undefined}
              onChange={(_, day) => setShipmentPlanDate((day as string) || null)}
              style={{ width: '100%' }}
              placeholder="日期未定可留空"
            />
          </div>
          {(shipmentsQuery.data?.items ?? []).map((item) => {
            const error = plannedShipmentRows.find((row) => row.order_item_id === item.order_item_id)?.error
            return (
              <div key={item.order_item_id} className="order-shipment-item">
                <label htmlFor={`planned-quantity-${item.order_item_id}`}>
                  <strong>{item.sku}</strong>
                  <span>订购 {shipmentQuantity(item.ordered_exact ?? item.ordered)} · 还可安排 {shipmentQuantity(item.unplanned_exact ?? item.unplanned)}</span>
                </label>
                <Input
                  id={`planned-quantity-${item.order_item_id}`}
                  inputMode="decimal"
                  placeholder="本批数量"
                  value={plannedQtys[item.order_item_id] ?? ''}
                  validateStatus={error ? 'error' : 'default'}
                  aria-invalid={Boolean(error)}
                  aria-describedby={error ? `planned-error-${item.order_item_id}` : undefined}
                  onChange={(value) => setPlannedQtys({ ...plannedQtys, [item.order_item_id]: value })}
                />
                {error && <span id={`planned-error-${item.order_item_id}`} style={{ color: 'var(--crm-error)', gridColumn: '1 / -1' }}>{error}</span>}
              </div>
            )
          })}
        </div>
      </Modal>

      <Modal
        width={520}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title={`第 ${shipTarget?.batch_no ?? ''} 批登记发货`}
        visible={Boolean(shipTarget)}
        onCancel={() => { if (!shipShipmentMutation.isPending) closeShipment() }}
        onOk={() => {
          if (shipTarget) shipShipmentMutation.mutate(shipTarget.id)
        }}
        confirmLoading={shipShipmentMutation.isPending}
        okText="确认发货"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            实发数量预填计划量，请按这次实际发出的数量修改。某项没发可填 0，剩余数量可以再排下一批。
          </div>
          {actualShipmentRows.map(({ item, planned, raw, difference, error }) => (
            <div key={item.order_item_id} className="order-shipment-item">
              <label htmlFor={`shipped-quantity-${item.order_item_id}`}>
                <strong>{item.sku ?? '订单商品'}</strong>
                <span>计划 {planned}{difference ? ` · ${difference}` : ''}</span>
              </label>
              <Input
                id={`shipped-quantity-${item.order_item_id}`}
                inputMode="decimal"
                placeholder="实发数量"
                value={raw}
                aria-invalid={Boolean(error)}
                aria-describedby={error ? `shipped-error-${item.order_item_id}` : undefined}
                validateStatus={error ? 'error' : 'default'}
                onChange={(quantity) => setShipForm((form) => ({
                  ...form, quantities: { ...form.quantities, [item.order_item_id]: quantity },
                }))}
              />
              {error && <span id={`shipped-error-${item.order_item_id}`} style={{ color: 'var(--crm-error)', gridColumn: '1 / -1' }}>{error}</span>}
            </div>
          ))}
          <div>
            <div style={{ marginBottom: 4 }}>实际发货日期</div>
            <DatePicker
              aria-label="实际发货日期"
              type="date"
              // 这里原来写的是 `disabled={milestoneForm.skipped}` —— **拿错了变量**：
              // milestoneForm 是「登记里程碑」那个弹窗的状态，用户在那边勾过一次
              // 「不适用，跳过此节点」之后就一直是 true（关掉里程碑弹窗时不清），
              // 于是这个「实际发货日期」在本单后续所有批次里都点不动，而用户完全
              // 不可能联想到是另一个弹窗之前的操作（主人 2026-10-12：「点又点不了，
              // 又不给提示为什么点不了」）。实际发货日期本来就允许留空（空值落库为
              // null），所以直接放开，是正确的行为而不是放宽口径。
              format="yyyy-MM-dd"
              style={{ width: '100%' }}
              value={shipForm.date ?? undefined}
              onChange={(_, dateStr) =>
                setShipForm({ ...shipForm, date: (dateStr as string) || null })
              }
            />
          </div>
          <div>
            <label htmlFor="shipment-company" style={{ display: 'block', marginBottom: 4 }}>承运商</label>
            <Input id="shipment-company" value={shipForm.company} onChange={(v) => setShipForm({ ...shipForm, company: v })} />
          </div>
          <div>
            <label htmlFor="shipment-tracking" style={{ display: 'block', marginBottom: 4 }}>运单号</label>
            <Input id="shipment-tracking" value={shipForm.tracking} onChange={(v) => setShipForm({ ...shipForm, tracking: v })} />
          </div>
        </div>
      </Modal>

      <Modal
        title="交期与跟单计划"
        visible={scheduleVisible}
        onCancel={() => setScheduleVisible(false)}
        width={680}
        footer={
          <div>
            {/* 页脚正上方写清「预览受影响面」为什么点不动（7 个数字框 + 客户要求
                日期，逐项点名）——不写的话用户只看到一个灰按钮，不知道差在哪。 */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <Button onClick={() => setScheduleVisible(false)}>取消</Button>
              <GuardedButton
                  loading={schedulePreviewMutation.isPending}
                  disabled={scheduleBlockedReasons.length > 0}
                  blockedReasons={scheduleBlockedReasons}
                  action="预览受影响面"
                  onClick={() => schedulePreviewMutation.mutate()}
                >
                  预览受影响面
                </GuardedButton>
              <GuardedButton
                  theme="solid"
                  loading={createScheduleMutation.isPending}
                  disabled={submitBlockedReasons.length > 0}
                  blockedReasons={submitBlockedReasons}
                  action="提交待确认计划"
                  onClick={() => createScheduleMutation.mutate()}
                >
                  提交待确认计划
                </GuardedButton>
            </div>
          </div>
        }
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div>
            <div style={{ marginBottom: 4 }}>客户要求日期</div>
            <DatePicker
              type="date"
              format="yyyy-MM-dd"
              showClear
              style={{ width: '100%' }}
              disabled={schedulePreviewMutation.isPending || createScheduleMutation.isPending}
              value={scheduleForm.new_delivery_date ? new Date(scheduleForm.new_delivery_date) : undefined}
              placeholder={order?.delivery_date ?? '选择日期'}
              onChange={(_, dateStr) => {
                setScheduleForm({ ...scheduleForm, new_delivery_date: (dateStr as string) || '' })
                // 改了交期，之前的预览就过期了——必须重新预览，不能拿旧结果去生成单子
                setSchedulePreview(null)
              }}
            />
          </div>
          <div style={{ display: 'flex', gap: 16 }}>
            <div><div>客户要求的是</div><Select aria-label="交期类型" disabled={schedulePreviewMutation.isPending || createScheduleMutation.isPending} value={scheduleForm.delivery_kind} style={{ width: 180 }}
              optionList={[{ value: 'shipping', label: '发货日' }, { value: 'arrival', label: '到货日' }]}
              onChange={(v) => { setScheduleForm({ ...scheduleForm, delivery_kind: v as 'shipping' | 'arrival', transit_days: 0 }); setSchedulePreview(null) }} /></div>
            {scheduleForm.delivery_kind === 'arrival' && <div><div>预计运输天数（自然日）</div>
              <InputNumber aria-label="预计运输天数" disabled={schedulePreviewMutation.isPending || createScheduleMutation.isPending} min={0} max={365} precision={0} value={scheduleForm.transit_days}
                onChange={(v) => { setScheduleForm({ ...scheduleForm, transit_days: typeof v === 'number' ? v : NaN }); setSchedulePreview(null) }} /></div>}
          </div>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>按自然日倒排。以下为建议参数，需责任人核对；正数表示发货前，负数表示发货后。</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: 10 }}>
            {Object.entries({ contract: '签订合同', deposit: '付定金', pre_sample_sent: '产前样发出', pre_sample_confirmed: '产前样确认', first_shipment: '首批发货', payment: '收款' }).map(([key, label]) =>
              <div key={key}><div>{label}提前天数</div><InputNumber aria-label={`${label}提前天数`} disabled={schedulePreviewMutation.isPending || createScheduleMutation.isPending} min={-365} max={365} precision={0}
                value={scheduleForm.plan_offsets[key]} onChange={(v) => {
                  setScheduleForm({ ...scheduleForm, plan_offsets: { ...scheduleForm.plan_offsets, [key]: typeof v === 'number' ? v : NaN } }); setSchedulePreview(null)
                }} /></div>)}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>变更原因（客户改期 / 样品未通过 / 生产延期…）</div>
            <Input
              value={scheduleForm.reason}
              onChange={(value) => setScheduleForm({ ...scheduleForm, reason: value })}
            />
          </div>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            提交后仍是待确认计划，责任人确认后才生效；计划不代表实际完成或对客承诺。
          </div>
          {schedulePreview && (
            <>
              <div style={{ fontSize: 12 }}>
                建议发货日：{schedulePreview.new_shipment_date}；受影响：节点 {schedulePreview.nodes.length} 个、批次{' '}
                {schedulePreview.batches.length} 个
                {schedulePreview.shift_days != null && `（整体平移 ${schedulePreview.shift_days} 天）`}
              </div>
              {(schedulePreview.batches_to_confirm?.length ?? 0) > 0 && (
                <div
                  style={{
                    marginTop: 4,
                    padding: '8px 10px',
                    borderRadius: 6,
                    background: 'var(--crm-warning-light, #FFF7E6)',
                    fontSize: 12,
                    lineHeight: 1.6,
                  }}
                >
                  这单原来没有可换算的交期基准，系统<strong>不猜</strong>批次该挪几天：
                  {schedulePreview.batches_to_confirm!.map((b) => `第 ${b.batch_no} 批（现计划 ${b.planned_date}）`).join('、')}
                  需要人工改计划日。
                  <button
                    type="button"
                    className="crm-text-action"
                    style={{ marginLeft: 8 }}
                    onClick={() => {
                      setScheduleVisible(false)
                      setActiveKey('shipments')
                    }}
                  >
                    去调整
                  </button>
                </div>
              )}
              {schedulePreview.nodes.length > 0 && (
                <Table
                  size="small"
                  pagination={false}
                  rowKey="node"
                  dataSource={schedulePreview.nodes}
                  columns={[
                    { title: '节点', dataIndex: 'label', width: 160 },
                    { title: '原计划日', dataIndex: 'before', width: 140 },
                    {
                      title: '调整后',
                      dataIndex: 'after',
                      width: 140,
                      render: (v: string | null, r: { before: string | null }) => (
                        <span style={{ color: v !== r.before ? 'var(--crm-primary)' : undefined }}>
                          {v ?? '-'}
                        </span>
                      ),
                    },
                  ]}
                />
              )}
              {schedulePreview.batches.length > 0 && (
                <Table
                  size="small"
                  pagination={false}
                  rowKey="batch_id"
                  dataSource={schedulePreview.batches}
                  columns={[
                    {
                      title: '批次',
                      dataIndex: 'batch_no',
                      width: 160,
                      render: (v: number) => `第 ${v} 批`,
                    },
                    { title: '原计划发货', dataIndex: 'before', width: 140 },
                    { title: '调整后', dataIndex: 'after', width: 140 },
                  ]}
                />
              )}
            </>
          )}
        </div>
      </Modal>

      <Modal
        width={520}
        title={`登记里程碑：${milestoneEdit?.label ?? ''}`}
        visible={Boolean(milestoneEdit)}
        onCancel={() => setMilestoneEdit(null)}
        onOk={() => milestoneSaveMutation.mutate()}
        confirmLoading={milestoneSaveMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div><div>节点适用性</div><Select aria-label="节点适用性" value={milestoneForm.skipped ? 'skip' : 'apply'}
            disabled={Boolean(milestoneEdit?.actual_date)} style={{ width: 220 }}
            optionList={[{ value: 'apply', label: '适用，继续跟进' }, { value: 'skip', label: '不适用，跳过此节点' }]}
            onChange={(v) => setMilestoneForm({ ...milestoneForm, skipped: v === 'skip' })} /></div>
          {milestoneForm.skipped && <div><FormLabel required>跳过原因</FormLabel><Input aria-label="跳过原因" value={milestoneForm.skip_reason}
            onChange={(v) => setMilestoneForm({ ...milestoneForm, skip_reason: v })} /></div>}

          <div>
            <div style={{ marginBottom: 4 }}>计划日期（交期倒推，可手工调整）</div>
            <DatePicker
              type="date"
              disabled={milestoneForm.skipped}
              format="yyyy-MM-dd"
              style={{ width: '100%' }}
              value={milestoneForm.planned_date ? new Date(milestoneForm.planned_date) : undefined}
              onChange={(_, dateStr) =>
                setMilestoneForm({ ...milestoneForm, planned_date: (dateStr as string) || null })
              }
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>实际日期（登记后该节点标记完成）</div>
            <DatePicker
              type="date"
              disabled={milestoneForm.skipped}
              format="yyyy-MM-dd"
              style={{ width: '100%' }}
              value={milestoneForm.actual_date ? new Date(milestoneForm.actual_date) : undefined}
              onChange={(_, dateStr) =>
                setMilestoneForm({ ...milestoneForm, actual_date: (dateStr as string) || null })
              }
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注</div>
            <Input
              value={milestoneForm.remark}
              onChange={(value) => setMilestoneForm({ ...milestoneForm, remark: value })}
              placeholder="分批发货、延期原因等"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>责任人</div>
            <UserSearchSelect
              queryScope="order-milestone-owner"
              enabled={Boolean(milestoneEdit)}
              placeholder="按姓名或登录账号搜索（逾期时催他）"
              value={milestoneForm.owner_id ?? undefined}
              fallbackLabel={milestoneEdit?.owner_name ?? undefined}
              onChange={(value) =>
                setMilestoneForm({ ...milestoneForm, owner_id: value ?? null })
              }
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>来源证据（当初凭什么这么排）</div>
            <Input
              value={milestoneForm.evidence}
              placeholder="例：客户 9/20 邮件确认 10/12 交货"
              onChange={(value) => setMilestoneForm({ ...milestoneForm, evidence: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>逾期原因（晚了才填）</div>
            <Input
              value={milestoneForm.overdue_reason}
              placeholder="例：客户改期 / 生产排产冲突 / 分批导致"
              onChange={(value) => setMilestoneForm({ ...milestoneForm, overdue_reason: value })}
            />
          </div>
        </div>
      </Modal>

      {/* 合同文档 + AI 回款风险：原来是两张"一句话卡"——
          每张都占一整块白卡，里面只有一行字加一个按钮，
          底下还是大片空白（2026-11-17 订单详情巡检）。
          两件事都是"这单的外部凭据 / 这单的风险怎么看"，合成一张卡：
          段间用 1px 分隔线，每段一个 13px 小标题配一枚 22px 圆角小图标，
          和报价详情「对外与归档」同一套节奏。内容一个字没减。 */}
      {/* 订单全景：这页到下面那张卡结束，底部还空三百来像素；
          而"这单几行明细、排了几期应收、收了几笔、发了几批"散在五个页签里。
          四个数字摆一条，点一下跳过去 —— 和客户/商机详情同一套做法。 */}
      <SectionCard title="订单全景" style={{ marginTop: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
          {([
            /* 色带：明细蓝 / 应收橙 / 回款绿 / 发货紫 —— 和业务口径对齐（钱进来了是绿） */
            { key: 'items', label: '订单明细', value: itemsQuery.data?.length, tone: 'tone-primary' },
            { key: 'receivables', label: '应收计划', value: receivablesQuery.data?.length, tone: 'tone-caution' },
            { key: 'payments', label: '回款记录', value: paymentsQuery.data?.length, tone: 'tone-success' },
            { key: 'shipments', label: '发货批次', value: shipmentsQuery.data?.batches?.length, tone: 'tone-secondary' },
          ] as const).map((tile) => (
            <button
              key={tile.label}
              type="button"
              /* 0 条不染色：四条色线（蓝橙绿紫）全亮着，等于四个业务模块都"有事发生"，
                 实际是空单。数量为 0 时退回中性灰线，有数才用语义色。 */
              className={`cust-pano-tile ${tile.value ? tile.tone : 'tone-neutral'}`}
              onClick={() => setActiveKey(tile.key)}
              title={`跳到「${TABS.find((t) => t.itemKey === tile.key)?.tab ?? tile.key}」页签`}
            >
              <span className="cust-pano-label">{tile.label}</span>
              <span className="cust-pano-value">
                {tile.value === undefined || tile.value === null ? '-' : tile.value}
                {tile.value !== undefined && tile.value !== null && (
                  <span className="cust-pano-unit">条</span>
                )}
              </span>
            </button>
          ))}
        </div>
      </SectionCard>

      {(can('order:view') || can('agent:use')) && (
        <SectionCard style={{ marginTop: 16 }}>
          {can('order:view') && (
            <>
              {/* 这单签了什么，在订单页就能看到：合同向来是照着报价/订单出的，
                  以前只有合同台账那一头能查，从订单倒着找不回去 */}
              <div className="card-sub-head">
                <span className="card-sub-icon" style={{ background: '#e6f6f2', color: '#0d9488' }}>
                  <IconFile size="small" />
                </span>
                合同文档
              </div>
              <ContractDocsPanel orderId={orderId} boxed={false} empty="这单还没有关联的合同文档" />
            </>
          )}

          {can('agent:use') && (
            <>
              {can('order:view') && <div className="card-sub-divider" />}
              <div className="card-sub-head">
                <span className="card-sub-icon" style={{ background: '#f1ecfe', color: '#7a4fd3' }}>
                  <IconAIEditLevel1 size="small" />
                </span>
                AI 回款风险
                <span style={{ marginLeft: 'auto' }}>
                  <Button
                    size="small"
                    theme="outline"
                    type="primary"
                    loading={aiMutation.isPending}
                    onClick={() => aiMutation.mutate()}
                  >
                    运行分析
                  </Button>
                </span>
              </div>
              <AgentInsight
                envelope={aiEnvelope}
                empty="点右上角「运行分析」：统计逾期应收节点、金额与集中度，没配模型也能给出风险等级"
              />
            </>
          )}
        </SectionCard>
      )}

      <Modal
        width={520}
        title="人工更新履约状态"
        visible={statusVisible}
        onCancel={() => setStatusVisible(false)}
        footer={
          <div className="workflow-modal-actions">
            <Button onClick={() => setStatusVisible(false)}>取消</Button>
            <GuardedButton
              theme="outline"
              action={completingDelivery ? '确认完成交付' : '更新状态'}
              disabled={Boolean(statusBlocked)}
              blockedReasons={statusBlocked ? [statusBlocked] : []}
              loading={statusMutation.isPending}
              hintBelow
              onClick={() => statusMutation.mutate()}
            >
              {completingDelivery ? '确认完成交付' : '更新'}
            </GuardedButton>
          </div>
        }
      >
        <div style={{ marginBottom: 12, color: 'var(--crm-text-3)', fontSize: 13 }}>
          MES 未接入前由人工维护；接入后这一步会由 MES 回传自动完成。
        </div>
        <Select
          placeholder="选择状态"
          value={targetStatus ?? undefined}
          onChange={(value) => {
            statusMutation.reset()
            setTargetStatus(value as string)
          }}
          optionList={[
            { value: 'pending', label: '待生产' },
            { value: 'in_production', label: '生产中' },
            { value: 'shipped', label: '已发货' },
            { value: 'delivered', label: '已签收' },
            { value: 'completed', label: '交付已完成' },
            // 「已取消」**故意不在这里**：取消订单是不可逆的终态动作，
            // 还要连带处理回款与应收，必须走右上角那个专门的「取消订单」。
            // 放在这个下拉里，用户看不到后果，规则也会被绕过。
          ]}
          style={{ width: '100%' }}
        />
        {completionNeedsReason && !completionBlocked && (
          <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
            <p className="workflow-guide" style={{ margin: 0 }}>
              {waitingForPayment}。完成是交付事实，不代表钱已收齐；请说明为什么可以先完成，
              原因会写进状态历史与审计。
            </p>
            <FormLabel required>完成原因</FormLabel>
            <TextArea
              aria-label="完成原因"
              rows={2}
              placeholder="例如：月结客户，按合同账期收款"
              value={statusRemark}
              onChange={setStatusRemark}
            />
          </div>
        )}
        {completionBlocked && (
          <Button size="small" theme="borderless" style={{ marginTop: 8 }} onClick={() => {
            setStatusVisible(false)
            setActiveKey('shipments')
            orderTabsRef.current?.scrollIntoView({ block: 'start' })
            orderTabsRef.current?.focus({ preventScroll: true })
          }}>
            去发货批次
          </Button>
        )}
        {statusMutation.error && (
          <Banner type="danger" fullMode={false} closeIcon={null} description={statusMutation.error.message} />
        )}
      </Modal>

      {/* 取消订单：把"取消到底会影响什么"写在确认之前 */}
      <Modal
        width={520}
        className="order-cancel-modal"
        title="取消订单"
        visible={cancelVisible}
        onCancel={() => { if (!cancelMutation.isPending) setCancelVisible(false) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={cancelMutation.isPending} onClick={() => setCancelVisible(false)}>保留订单</Button>
            <GuardedButton
              type="danger"
              theme="outline"
              action="取消订单"
              disabled={Boolean(cancellationBlocked) || cancelReasonMissing}
              blockedReasons={
                cancellationBlocked
                  ? [cancellationBlocked]
                  : cancelReasonMissing
                    ? ['请填写取消理由（至少 2 个字）：已完成订单回退属于纠错动作']
                    : []
              }
              loading={cancelMutation.isPending}
              hintBelow
              onClick={() => cancelMutation.mutate(cancelReason.trim())}
            >
              确认取消订单
            </GuardedButton>
          </div>
        }
      >
        <div className="order-cancel-details">
          <p className="workflow-guide">
            <strong>{order.order_no}</strong>
            {order.customer_name ? ` · ${order.customer_name}` : ''}
            {' · '}{formatMoney(order.total_amount_exact ?? order.total_amount, order.currency)}
          </p>
          {cancellationBlocked ? (
            <Banner type="warning" fullMode={false} closeIcon={null} title="已有确认回款">
              <p className="workflow-guide" style={{ marginBottom: 8 }}>
                钱已经入账，不能随订单静默作废。请财务打开「回款记录」，对这笔回款点「作废回款」并填原因；作废后这里会自动放行。
              </p>
              <Button size="small" theme="borderless" onClick={() => {
                setCancelVisible(false)
                setActiveKey('payments')
                orderTabsRef.current?.scrollIntoView({ block: 'start' })
                orderTabsRef.current?.focus({ preventScroll: true })
              }}>查看回款记录</Button>
            </Banner>
          ) : (
            <>
              <strong>取消后无法恢复</strong>
              <dl className="order-cancel-impact">
                <div>
                  <dt>{receivablesQuery.isSuccess ? `${cancelPlanCount} 期未收清应收` : '未收清的应收计划'}</dt>
                  <dd>作废并停止催收</dd>
                </div>
                <div>
                  <dt>{paymentsQuery.isSuccess ? `${pendingPayments} 笔待确认回款` : '待确认的回款'}</dt>
                  <dd>随单驳回</dd>
                </div>
                <div>
                  <dt>{shipmentsQuery.isSuccess && shipment ? `${shipment.summary.pending_batch_count} 个待发批次` : '待发的批次'}</dt>
                  <dd>随单取消</dd>
                </div>
              </dl>
              <p className="workflow-guide">已签合同和已确认回款须先处理。未生效的交期变更会随订单作废。</p>
              {order.opportunity_id && <p className="workflow-guide">如需撤销商机成交，请另到商机核对并处理。</p>}
              <div className="order-cancel-reason">
                <p className="workflow-guide" style={{ marginBottom: 6 }}>
                  取消原因{cancelReasonRequired ? '（已完成订单必填，会记入状态变更留痕）' : '（选填）'}
                </p>
                <TextArea
                  rows={3}
                  maxLength={255}
                  placeholder={cancelReasonRequired ? '例如：客户退单，财务已核实并作废回款' : '写清原因，方便后续查账'}
                  value={cancelReason}
                  onChange={(value) => setCancelReason(value)}
                />
              </div>
            </>
          )}
          {cancelMutation.error && !cancellationBlocked && (
            <Banner type="danger" fullMode={false} closeIcon={null} title="取消未完成" description={cancelMutation.error.message} />
          )}
        </div>
      </Modal>

      {/* 作废已确认回款：钱已经到账，不能"删掉当没发生"；只把它从已收里剔除。 */}
      <Modal
        width={520}
        title="作废这笔回款"
        visible={Boolean(voidTarget)}
        onCancel={() => { if (!voidMutation.isPending) { setVoidTarget(null); setVoidReason('') } }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={voidMutation.isPending} onClick={() => { setVoidTarget(null); setVoidReason('') }}>先不作废</Button>
            <GuardedButton
              type="danger"
              theme="outline"
              action="作废回款"
              disabled={voidReason.trim().length < 2}
              blockedReasons={voidReason.trim().length < 2 ? ['请填写作废原因（至少 2 个字）'] : []}
              loading={voidMutation.isPending}
              hintBelow
              onClick={() => {
                if (voidTarget) voidMutation.mutate({ id: voidTarget.id, reason: voidReason.trim() })
              }}
            >
              确认作废
            </GuardedButton>
          </div>
        }
      >
        {voidTarget && (
          <div className="order-cancel-details">
            <p className="workflow-guide">
              <strong>{formatMoney(voidTarget.received_amount_exact ?? voidTarget.received_amount, voidTarget.currency ?? order.currency)}</strong>
              {' · '}{voidTarget.received_date}
              {voidTarget.plan_name ? ` · ${voidTarget.plan_name}` : ''}
            </p>
            <p className="workflow-guide">
              作废后这笔钱不再计入「已收」，对应应收期会退回未收/部分回款；
              <strong>原记录、分配和凭证都会保留</strong>，审计能查到谁在什么时候、为什么作废。
            </p>
            {voidTarget.allocations && voidTarget.allocations.length > 0 && (
              <p className="workflow-guide">
                这笔钱原本分到 {voidTarget.allocations.length} 期：
                {voidTarget.allocations
                  .map((a) => `${a.plan_name} ${formatMoney(a.amount_exact ?? a.amount, voidTarget.currency ?? order.currency)}`)
                  .join('；')}
                。作废会把已经分配出去的钱退回未分配。
              </p>
            )}
            <p className="workflow-guide">
              订单取消还要看已签合同和已发货批次，作废回款只解开"钱"这一道。
            </p>
            <TextArea
              rows={3}
              value={voidReason}
              placeholder="例：客户退单 / 金额录错，财务核实后作废"
              maxLength={200}
              onChange={setVoidReason}
            />
            {voidMutation.error && (
              <Banner type="danger" fullMode={false} closeIcon={null} title="作废未完成" description={voidMutation.error.message} />
            )}
          </div>
        )}
      </Modal>

      <Modal
        width={600}
        className="receivable-plan-modal"
        title="生成应收计划"
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        visible={generateVisible}
        onCancel={() => { if (!generateMutation.isPending) setGenerateVisible(false) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={generateMutation.isPending} onClick={() => setGenerateVisible(false)}>取消</Button>
            <GuardedButton
              theme="outline"
              action="生成应收计划"
              disabled={generateBlocked.length > 0}
              blockedReasons={generateBlocked}
              loading={generateMutation.isPending}
              hintBelow
              onClick={() => generateMutation.mutate()}
            >
              生成应收计划
            </GuardedButton>
          </div>
        }
      >
        <div className="receivable-picker">
          <p className="receivable-picker-guide">
            按订单总额 <strong>{formatMoney(receivableTotal, order.currency)}</strong> 划分各期应收。
            请核对每期名称、比例和到期日，确认后一次保存全部期次。
          </p>
          {generateMutation.error && (
            <Banner type="danger" fullMode={false} closeIcon={null} title="应收计划未生成" description={generateMutation.error.message} />
          )}
          <ReceivablePlanPicker
            mode="installments"
            totalAmount={receivableTotal}
            rows={generateRows}
            onRowsChange={setGenerateRows}
            currency={order.currency}
          />
        </div>
      </Modal>

      {dueEdit && (
        <Modal
          width={420}
          title="修改应收到期日"
          visible
          onCancel={() => { if (!dueMutation.isPending) setDueEdit(null) }}
          footer={
            <div className="workflow-modal-actions">
              <Button disabled={dueMutation.isPending} onClick={() => setDueEdit(null)}>取消</Button>
              <GuardedButton
                theme="outline"
                action="保存到期日"
                disabled={!newDueDay || newDueDay === dueEdit.plan.due_date}
                blockedReasons={[newDueDay ? '请选择不同的到期日。' : '请选择新的到期日。']}
                loading={dueMutation.isPending}
                hintBelow
                onClick={() => { if (newDueDay) dueMutation.mutate({ planId: dueEdit.plan.id, day: newDueDay }) }}
              >
                保存到期日
              </GuardedButton>
            </div>
          }
        >
          <div className="receivable-picker">
            <p className="receivable-picker-guide">
              <strong>{dueEdit.plan.plan_name}</strong> · 原到期日 {dueEdit.plan.due_date ?? '未填写'}
            </p>
            <div>
              <FormLabel required><span id="receivable-new-due-label">新到期日</span></FormLabel>
              <DatePicker
                aria-labelledby="receivable-new-due-label"
                style={{ width: '100%' }}
                value={dueEdit.date ?? undefined}
                disabled={dueMutation.isPending}
                onChange={date => setDueEdit(current => current ? { ...current, date: date as Date | null } : current)}
              />
            </div>
            <p className="receivable-picker-guide">系统会按新日期判断是否逾期，已分到各期的回款会保留原归属。</p>
            {dueMutation.error && (
              <Banner type="danger" fullMode={false} closeIcon={null} description={dueMutation.error.message} />
            )}
          </div>
        </Modal>
      )}

      {/* 调整回款分配（第十批）：整份替换式提交，原因会进审计 */}
      <AllocationAdjustModal
        // ⚠️ `key` 让"每打开一次就是一次全新挂载"：弹窗内部的编辑状态用惰性初值，
        // 不必在 effect 里 setState（那会多跑一轮渲染，且触发
        // react-hooks/set-state-in-effect）。换一笔回款也会重建，不会串数据。
        key={allocationPayment?.id ?? 'none'}
        visible={allocationPayment !== null}
        payment={allocationPayment}
        plans={receivablesQuery.data ?? []}
        currency={order?.currency}
        canEdit={canPayment}
        onClose={() => setAllocationPayment(null)}
        onSaved={() => {
          paymentsQuery.refetch()
          receivablesQuery.refetch()
        }}
      />

      <Modal
        width={520}
        title={`登记回款：${paymentTarget?.plan_name ?? ''}`}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        visible={Boolean(paymentTarget)}
        onCancel={() => setPaymentTarget(null)}
        onOk={() => {
          if (!paymentForm.amount.trim() || !Number.isFinite(Number(paymentForm.amount)) || Number(paymentForm.amount) <= 0) {
            Toast.warning('请填写大于 0 的实际到账金额。')
            return
          }
          paymentMutation.mutate()
        }}
        confirmLoading={paymentMutation.isPending}
        okText="提交财务确认"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            该节点未收{' '}
            {formatMoney(
              paymentTarget?.remaining_amount_exact ?? paymentTarget?.remaining_amount ?? 0,
              paymentTarget?.currency ?? order.currency,
            )}
          </div>
          <div>
            <FormLabel required>实际到账金额</FormLabel>
            <Input aria-label="实际到账金额" inputMode="decimal" value={paymentForm.amount}
              onChange={(value) => setPaymentForm({ ...paymentForm, amount: value })} />
          </div>
          <div>
            <FormLabel required>实际到账日期</FormLabel>
            <DatePicker aria-label="实际到账日期" value={paymentForm.date}
              onChange={(date) => setPaymentForm({ ...paymentForm, date: (date as Date) ?? new Date() })}
              style={{ width: '100%' }} />
          </div>
          <div>
            <FormLabel>收款方式</FormLabel>
            <Select aria-label="收款方式" value={methodOption.selectValue} onChange={methodOption.onSelect}
              optionList={PAYMENT_METHODS.map((value) => ({ value, label: value }))} style={{ width: '100%' }} />
          </div>
          {methodOption.showInput && (
            <Input
              value={methodOption.inputValue}
              onChange={methodOption.onInput}
              placeholder="请说明是什么收款方式（可不填）"
            />
          )}
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            请按实际到账填写。{canPayment ? '提交后可在「回款记录」补传凭证，再确认到账。' : '提交后可在「回款记录」查看进度，财务确认后才计入已收金额。'}
          </div>
        </div>
      </Modal>

      {/* 登记销售退货：填退几件、原因和退货总额；不再等主管通过后另填金额。 */}
      <Modal
        width={780}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title="登记销售退货"
        visible={returnVisible}
        onCancel={() => { if (!createReturnMutation.isPending) setReturnVisible(false) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={createReturnMutation.isPending} onClick={() => setReturnVisible(false)}>先不登记</Button>
            <GuardedButton
              type="danger"
              theme="outline"
              action="登记退货"
              disabled={Boolean(returnSubmitBlocked)}
              blockedReasons={returnSubmitBlocked ? [returnSubmitBlocked] : []}
              loading={createReturnMutation.isPending}
              hintBelow
              onClick={() => createReturnMutation.mutate({
                reason_code: returnReasonCode,
                reason: returnReason.trim(),
                refund_amount: Number(returnRefundAmount),
                return_date: calendarDay(returnDate) ?? undefined,
                remark: returnRemark.trim() || undefined,
                items: returnRequestedItems,
              })}
            >
              确认登记
            </GuardedButton>
          </div>
        }
      >
        <div style={{ display: 'grid', gap: 12 }}>
          {createReturnMutation.error && (
            <Banner type="danger" fullMode={false} closeIcon={null} title="登记未完成" description={createReturnMutation.error.message} />
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 12 }}>
            <div>
              <FormLabel required>退货原因分类</FormLabel>
              <Select
                value={returnReasonCode}
                onChange={(value) => setReturnReasonCode(value as typeof returnReasonCode)}
                optionList={[
                  { value: 'customer_rejected', label: '客户拒收' },
                  { value: 'quality_issue', label: '质量问题' },
                  { value: 'wrong_item', label: '发错货' },
                  { value: 'other', label: '其他' },
                ]}
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <FormLabel>退货日期</FormLabel>
              <DatePicker
                type="date"
                format="yyyy-MM-dd"
                value={returnDate ?? undefined}
                onChange={(date) => setReturnDate((date as Date) ?? new Date())}
                style={{ width: '100%' }}
              />
            </div>
          </div>
          <div>
            <FormLabel required>本次退货总额（含货款和运费）</FormLabel>
            <Input
              value={returnRefundAmount}
              onChange={setReturnRefundAmount}
              inputMode="decimal"
              placeholder="例如：1000.00"
              suffix="元"
            />
            <p className="workflow-guide" style={{ margin: '6px 0 0' }}>
              按当前退货数量折算的登记参考金额：<strong>{formatMoney(returnReferenceTotal.toFixed(2), order.currency)}</strong>。
              这只是提示，最终以你填的金额为准；财务后续可以核对修改，改动会写审计。
            </p>
          </div>
          <div>
            <FormLabel required>退货原因</FormLabel>
            <TextArea
              rows={2}
              maxLength={255}
              placeholder="例如：客户验收发现外箱破损，拒收 3 件"
              value={returnReason}
              onChange={setReturnReason}
            />
          </div>
          <div>
            <FormLabel>备注（选填）</FormLabel>
            <Input value={returnRemark} onChange={setReturnRemark} placeholder="补充说明，可不填" />
          </div>
          <div>
            <FormLabel required>本次退货数量</FormLabel>
            <p className="workflow-guide" style={{ marginBottom: 6 }}>
              这里只填退哪个产品、几件；退货总额在上方填。系统按订单明细汇总已发/已退数量，不再拆发货批次。
              登记后先交主管复核，通过后由财务核对/修改金额并登记实际退款。
            </p>
            <Table<ReturnableItem>
              size="small"
              pagination={false}
              rowKey="order_item_id"
              dataSource={returnableRows}
              empty="这张订单还没有可退的已发产品"
              columns={[
                {
                  title: '产品 / 规格',
                  render: (_: unknown, row: ReturnableItem) => (
                    <div>
                      {row.sku || '定制件'}
                      <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{row.specification || '-'}</div>
                    </div>
                  ),
                },
                { title: '已发', width: 78, render: (_: unknown, row: ReturnableItem) => row.shipped_quantity_exact },
                { title: '已退', width: 78, render: (_: unknown, row: ReturnableItem) => row.returned_quantity_exact },
                { title: '可退', width: 78, render: (_: unknown, row: ReturnableItem) => row.remaining_quantity_exact },
                {
                  title: '本次数量',
                  width: 130,
                  render: (_: unknown, row: ReturnableItem) => (
                    <InputNumber
                      min={0}
                      max={Number(row.remaining_quantity_exact)}
                      precision={3}
                      value={returnQty[row.order_item_id] ?? ''}
                      disabled={Number(row.remaining_quantity_exact) <= 0}
                      onChange={(value) => setReturnQty((prev) => ({
                        ...prev,
                        [row.order_item_id]: value === null || value === undefined ? '' : String(value),
                      }))}
                      style={{ width: '100%' }}
                    />
                  ),
                },
              ]}
            />
          </div>
        </div>
      </Modal>

      {/* 主管复核退货：通过后直接进入待财务登记退款；驳回释放可退数量，登记人可改后重提。 */}
      <Modal
        width={520}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title={`主管复核退货：${reviewTarget?.return_no ?? ''}`}
        visible={Boolean(reviewTarget)}
        onCancel={() => { if (!reviewReturnMutation.isPending) setReviewTarget(null) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={reviewReturnMutation.isPending} onClick={() => setReviewTarget(null)}>先不复核</Button>
            <GuardedButton
              type={reviewDecision === 'reject' ? 'danger' : 'primary'}
              action={reviewDecision === 'reject' ? '驳回退货' : '通过复核'}
              disabled={!reviewNoteValid}
              blockedReasons={reviewNoteValid ? [] : ['驳回必须写原因（至少 2 个字）']}
              loading={reviewReturnMutation.isPending}
              hintBelow
              onClick={() => {
                if (!reviewTarget) return
                reviewReturnMutation.mutate({
                  id: reviewTarget.id,
                  decision: reviewDecision,
                  note: reviewNote.trim() || undefined,
                })
              }}
            >
              {reviewDecision === 'reject' ? '确认驳回' : '确认通过'}
            </GuardedButton>
          </div>
        }
      >
        {reviewTarget && (
          <div style={{ display: 'grid', gap: 12 }}>
            {reviewReturnMutation.error && (
              <Banner type="danger" fullMode={false} closeIcon={null} title="复核未完成" description={reviewReturnMutation.error.message} />
            )}
            {reviewTarget.created_by != null && reviewTarget.created_by === currentUser?.id && (
              <Banner
                type="warning"
                fullMode={false}
                closeIcon={null}
                title="这是你自己登记的退货"
                description="可以自己复核；通过后列表会标「本人复核」，审计里也会记录，方便事后查。"
              />
            )}
            <div className="workflow-guide">
              这张退货单有 <strong>{reviewTarget.items.length}</strong> 项退货，
              登记参考 <strong>{formatMoney(reviewTarget.goods_amount_exact, reviewTarget.currency)}</strong>，
              登记退货总额 <strong>{formatMoney(reviewTarget.settled_goods_amount_exact, reviewTarget.currency)}</strong>。
              这里确认“退几件、什么原因、登记金额”是否属实；通过后由财务核对金额并登记实际退款。
            </div>
            <div>
              <FormLabel required>复核结果</FormLabel>
              <Select
                value={reviewDecision}
                onChange={(value) => setReviewDecision(value as 'approve' | 'reject')}
                optionList={[
                  { label: '通过，待财务登记退款', value: 'approve' },
                  { label: '驳回，退给登记人修改', value: 'reject' },
                ]}
              />
            </div>
            <div>
              <FormLabel required={reviewDecision === 'reject'}>复核意见</FormLabel>
              <TextArea rows={2} maxLength={255} value={reviewNote} onChange={setReviewNote}
                placeholder={reviewDecision === 'reject' ? '驳回必须写原因，例如：货还没收到' : '选填，例如：已核对退货照片'} />
            </div>
            <p className="workflow-guide">
              通过后进入“待财务结算”，财务可以核对/修改退货总额，再手动选择抵扣未付款或现金退还；驳回后这张单结束，但占用的可退数量会释放，可以重新登记。
            </p>
          </div>
        )}
      </Modal>

      {/* 财务修改退货总额：登记时业务员先填；财务可核对修改，每次改动写审计；不自动抵扣客户未付款。 */}
      <Modal
        width={520}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title={`${amountTarget?.status === 'pending' ? '填写' : '修改'}退货总额：${amountTarget?.return_no ?? ''}`}
        visible={Boolean(amountTarget)}
        onCancel={() => { if (!updateReturnAmountMutation.isPending) setAmountTarget(null) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={updateReturnAmountMutation.isPending} onClick={() => setAmountTarget(null)}>暂不处理</Button>
            <GuardedButton
              type="primary"
              action="保存退货总额"
              disabled={!amountDraftValid}
              blockedReasons={
                !amountDraftValid
                  ? ['请填写退货总额（大于 0，最多两位小数；含货款和运费）']
                  : []
              }
              loading={updateReturnAmountMutation.isPending}
              hintBelow
              onClick={() => {
                if (!amountTarget) return
                updateReturnAmountMutation.mutate({
                  id: amountTarget.id,
                  amount: Number(amountDraft),
                  note: amountNote.trim() || undefined,
                })
              }}
            >
              保存退货总额
            </GuardedButton>
          </div>
        }
      >
        {amountTarget && (
          <div style={{ display: 'grid', gap: 12 }}>
            {updateReturnAmountMutation.error && (
              <Banner type="danger" fullMode={false} closeIcon={null} title="金额未保存" description={updateReturnAmountMutation.error.message} />
            )}
            <div className="workflow-guide">
              当前退货总额：<strong>{formatMoney(amountTarget.settled_goods_amount_exact, amountTarget.currency)}</strong>，
              登记参考金额：<strong>{formatMoney(amountTarget.goods_amount_exact, amountTarget.currency)}</strong>。
              修改后会立即写审计，保留改前/改后金额、操作人和说明。
            </div>
            <div>
              <FormLabel required>退货总额（含货款和运费）</FormLabel>
              <Input
                inputMode="decimal"
                value={amountDraft}
                onChange={setAmountDraft}
                placeholder="例如 1000.00"
                suffix="元"
              />
            </div>
            <div>
              <FormLabel>修改说明（选填）</FormLabel>
              <TextArea rows={2} maxLength={255} value={amountNote} onChange={setAmountNote}
                placeholder="例如：财务核对后按客户确认金额调整" />
            </div>
            <p className="workflow-guide">
              这里只改退货总额，不自动抵扣；财务结算时再手动选抵扣未付款或现金退还。已退款/已结算后不能再改。
            </p>
          </div>
        )}
      </Modal>

      {/* 财务结算：新退货手动选抵扣/退现；历史抵扣单只补登记现金退款。 */}
      <Modal
        width={560}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title={refundIsLegacyOffset
          ? `登记现金退款：${refundTarget?.return_no ?? ''}`
          : `财务结算：${refundTarget?.return_no ?? ''}`}
        visible={Boolean(refundTarget)}
        onCancel={() => {
          if (!settleReturnMutation.isPending && !refundReturnMutation.isPending) setRefundTarget(null)
        }}
        footer={
          <div className="workflow-modal-actions">
            <Button
              disabled={settleReturnMutation.isPending || refundReturnMutation.isPending || refundAttachmentBusy}
              onClick={() => setRefundTarget(null)}
            >
              暂不处理
            </Button>
            {refundIsLegacyOffset ? (
              <GuardedButton
                type="danger"
                theme="outline"
                action="登记退款"
                disabled={Boolean(settleBlockedReason) || refundAttachmentBusy}
                blockedReasons={
                  settleBlockedReason
                    ? [settleBlockedReason]
                    : refundAttachmentBusy
                      ? ['退款附件正在处理，请稍候']
                      : []
                }
                loading={refundReturnMutation.isPending}
                hintBelow
                onClick={() => {
                  if (refundTarget) {
                    refundReturnMutation.mutate({
                      id: refundTarget.id,
                      reference: refundReference.trim(),
                    })
                  }
                }}
              >
                登记实际退款
              </GuardedButton>
            ) : (
              <GuardedButton
                action="确认退货结算"
                disabled={Boolean(settleBlockedReason) || refundAttachmentBusy}
                blockedReasons={
                  settleBlockedReason
                    ? [settleBlockedReason]
                    : refundAttachmentBusy
                      ? ['退款附件正在处理，请稍候']
                      : []
                }
                loading={settleReturnMutation.isPending}
                hintBelow
                onClick={() => {
                  if (!refundTarget) return
                  settleReturnMutation.mutate({
                    id: refundTarget.id,
                    total: Number(settleTotalDraft),
                    offset: settleOffsetNumber,
                    reference: settleCashAmount > 0 ? refundReference.trim() : undefined,
                    note: settleNote.trim() || undefined,
                  })
                }}
              >
                确认结算
              </GuardedButton>
            )}
          </div>
        }
      >
        {refundTarget && (
          <div style={{ display: 'grid', gap: 12 }}>
            {refundIsLegacyOffset
              ? refundReturnMutation.error && (
                  <Banner type="danger" fullMode={false} closeIcon={null} title="登记未完成" description={refundReturnMutation.error.message} />
                )
              : settleReturnMutation.error && (
                  <Banner type="danger" fullMode={false} closeIcon={null} title="结算未完成" description={settleReturnMutation.error.message} />
                )}
            {refundIsLegacyOffset ? (
              <div className="workflow-guide">
                这张是历史“已抵扣客户未付款”的退货单：已抵扣
                <strong>{formatMoney(refundTarget.offset_amount_exact, refundTarget.currency)}</strong>，
                本次现金退款
                <strong>{formatMoney(refundTarget.refund_amount_exact, refundTarget.currency)}</strong>。
                仅在款项实际支付后登记；登记后不能再改金额，也不能作废。
              </div>
            ) : (
              <>
                <div className="workflow-guide">
                  客户未付款余额
                  <strong>{formatMoney(Math.max(0, Number(unreceivedAmount)), refundTarget.currency)}</strong>，
                  本次最多可抵扣
                  <strong>{formatMoney(settleMaxOffset, refundTarget.currency)}</strong>。
                  抵扣多少由财务手动填；不填抵扣就是全部现金退还。只有填了抵扣，才会冲减应收计划并写审计。
                </div>
                <div>
                  <FormLabel required>本次退货总额（含货款和运费）</FormLabel>
                  <Input
                    inputMode="decimal"
                    value={settleTotalDraft}
                    onChange={setSettleTotalDraft}
                    placeholder="例如 1000.00"
                    suffix="元"
                  />
                </div>
                <div>
                  <FormLabel required>抵扣客户未付款</FormLabel>
                  <Input
                    inputMode="decimal"
                    value={settleOffsetDraft}
                    onChange={setSettleOffsetDraft}
                    placeholder="0.00"
                    suffix="元"
                  />
                  <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                    <Button size="small" theme="borderless" onClick={() => setSettleOffsetDraft('0')}>
                      全部退现
                    </Button>
                    <Button
                      size="small"
                      theme="borderless"
                      disabled={settleMaxOffset <= 0}
                      onClick={() => setSettleOffsetDraft(settleMaxOffset.toFixed(2))}
                    >
                      优先抵扣未付款
                    </Button>
                  </div>
                </div>
                <div className="workflow-guide">
                  本次现金退还：
                  <strong>{formatMoney(settleCashAmount, refundTarget.currency)}</strong>
                  {settleCashAmount > 0 ? '（需要填退款流水/凭证说明）' : '（全额抵扣，无现金退款）'}
                </div>
              </>
            )}
            <div>
              <FormLabel required={settleCashAmount > 0}>退款流水 / 凭证说明</FormLabel>
              <Input
                value={refundReference}
                onChange={setRefundReference}
                maxLength={128}
                placeholder="例如：银行转账 20261125001"
              />
            </div>
            {refundEditsSettlement && (
              <div>
                <FormLabel>结算说明（选填）</FormLabel>
                <TextArea
                  rows={2}
                  maxLength={255}
                  value={settleNote}
                  onChange={setSettleNote}
                  placeholder="例如：客户未付尾款，退货金额直接抵扣"
                />
              </div>
            )}
            <div>
              <FormLabel>退款附件（选填）</FormLabel>
              <input
                ref={refundAttachmentInputRef}
                type="file"
                accept="image/*,.heic,.heif,.pdf,.ofd,.doc,.docx,.xls,.xlsx,.csv,.txt,.zip"
                style={{ display: 'none' }}
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  // 清空 value：否则连续选同一个文件不会再触发 onChange。
                  event.target.value = ''
                  if (!file) return
                  if (file.size > 20 * 1024 * 1024) {
                    Toast.warning('退款附件不能超过 20 MB')
                    return
                  }
                  uploadRefundAttachmentMutation.mutate({ id: refundTarget.id, file })
                }}
              />
              {refundTarget.refund_attachment_file_id ? (
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  flexWrap: 'wrap',
                  padding: '8px 10px',
                  border: '1px solid var(--crm-border)',
                  borderRadius: 6,
                  background: 'var(--crm-bg-2)',
                }}>
                  <div style={{ minWidth: 0, flex: '1 1 220px' }}>
                    <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {refundTarget.refund_attachment_file_name ?? '退款附件'}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      {formatAttachmentSize(refundTarget.refund_attachment_file_size)}
                    </div>
                  </div>
                  <Button
                    size="small"
                    theme="borderless"
                    onClick={() => {
                      void downloadReturnRefundAttachment(
                        refundTarget.id,
                        refundTarget.refund_attachment_file_name ?? `退款附件-${refundTarget.return_no}`,
                      ).catch((error: Error) => Toast.error(error.message || '退款附件下载失败'))
                    }}
                  >
                    下载
                  </Button>
                  <Button
                    size="small"
                    theme="borderless"
                    type="danger"
                    loading={deleteRefundAttachmentMutation.isPending}
                    onClick={() => deleteRefundAttachmentMutation.mutate(refundTarget.id)}
                  >
                    删除
                  </Button>
                </div>
              ) : (
                <Button
                  theme="outline"
                  loading={uploadRefundAttachmentMutation.isPending}
                  onClick={() => refundAttachmentInputRef.current?.click()}
                >
                  选择并上传
                </Button>
              )}
              <p className="workflow-guide" style={{ marginTop: 6 }}>
                可上传银行回单、转账截图、客户签收单等；支持图片、PDF、Word、Excel，单个文件不超过 20 MB。
                附件仅用于核对，不改变结算金额。
              </p>
            </div>
          </div>
        )}
      </Modal>

      {/* 取消还没处理的退货登记：释放可退数量，不动钱。 */}
      <Modal
        width={480}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title={`取消退货登记：${cancelReturnTarget?.return_no ?? ''}`}
        visible={Boolean(cancelReturnTarget)}
        onCancel={() => { if (!cancelReturnMutation.isPending) setCancelReturnTarget(null) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={cancelReturnMutation.isPending} onClick={() => setCancelReturnTarget(null)}>保留</Button>
            <GuardedButton
              type="danger"
              theme="outline"
              action="取消退货登记"
              disabled={!cancelReturnReasonValid}
              blockedReasons={cancelReturnReasonValid ? [] : ['请填写取消原因（至少 2 个字）']}
              loading={cancelReturnMutation.isPending}
              hintBelow
              onClick={() => {
                if (cancelReturnTarget) cancelReturnMutation.mutate({ id: cancelReturnTarget.id, reason: cancelReturnReason.trim() })
              }}
            >
              确认取消
            </GuardedButton>
          </div>
        }
      >
        {cancelReturnTarget && (
          <div style={{ display: 'grid', gap: 12 }}>
            {cancelReturnMutation.error && (
              <Banner type="danger" fullMode={false} closeIcon={null} title="取消失败" description={cancelReturnMutation.error.message} />
            )}
            <div className="workflow-guide">取消后这批数量会释放，可以重新登记；财务已处理的不能走这里，只能作废。</div>
            <div>
              <FormLabel required>取消原因</FormLabel>
              <TextArea rows={2} maxLength={255} value={cancelReturnReason} onChange={setCancelReturnReason}
                placeholder="例如：客户又把货留下了" />
            </div>
          </div>
        )}
      </Modal>

      {/* 作废已处理的退货：新流程只释放可退数量；历史抵扣单按原快照恢复应收；已退款不能作废。 */}
      <Modal
        width={520}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title={`作废退货：${voidReturnTarget?.return_no ?? ''}`}
        visible={Boolean(voidReturnTarget)}
        onCancel={() => { if (!voidReturnMutation.isPending) setVoidReturnTarget(null) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={voidReturnMutation.isPending} onClick={() => setVoidReturnTarget(null)}>保留</Button>
            <GuardedButton
              type="danger"
              theme="outline"
              action="作废退货"
              disabled={!voidReturnReasonValid}
              blockedReasons={voidReturnReasonValid ? [] : ['请填写作废原因（至少 2 个字）']}
              loading={voidReturnMutation.isPending}
              hintBelow
              onClick={() => {
                if (voidReturnTarget) voidReturnMutation.mutate({ id: voidReturnTarget.id, reason: voidReturnReason.trim() })
              }}
            >
              确认作废
            </GuardedButton>
          </div>
        }
      >
        {voidReturnTarget && (
          <div style={{ display: 'grid', gap: 12 }}>
            {voidReturnMutation.error && (
              <Banner type="danger" fullMode={false} closeIcon={null} title="作废未完成" description={voidReturnMutation.error.message} />
            )}
            <div className="workflow-guide">
              作废后释放本单占用的可退数量；历史“抵扣客户未付款”的退货单会按原记录恢复应收。若退款已实际支付，不允许作废，需线下处理。
            </div>
            <div>
              <FormLabel required>作废原因</FormLabel>
              <TextArea rows={2} maxLength={255} value={voidReturnReason} onChange={setVoidReturnReason}
                placeholder="例如：退错订单，重新登记" />
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
