import { useRef, useState, type CSSProperties } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Input, InputNumber, Modal, Popconfirm, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'
import FormLabel from '../../shared/components/FormLabel'
import type { TagTone } from '../../shared/types'

import {
  cancelOrder,
  cancelOrderShipment,
  changeOrderStatus,
  confirmPayment,
  createOrderShipment,
  createPayment,
  generateReceivables,
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
  shipOrderShipment,
  syncErp,
  updateOrderMilestone,
  type OrderItem,
  type OrderMilestoneRow,
  type OrderStatusRow,
  type Payment,
  type Receivable,
  type ShipmentBatchRow,
  type ShipmentOverview,
} from '../../shared/api/order'
import { listUsers } from '../../shared/api/system'
import { usePermissions } from '../../shared/hooks/permissions'
import DetailHeader from '../../shared/components/DetailHeader'
import KpiStrip from '../../shared/components/KpiStrip'
import SectionCard from '../../shared/components/SectionCard'
import ContractDocsPanel from '../../shared/components/ContractDocsPanel'
import AgentInsight from '../../shared/components/AgentInsight'
import BizDocPanel from '../../shared/components/BizDocPanel'
import { agentRiskAnalysis, type AnalysisEnvelope } from '../../shared/api/agent'
import { otherOption } from '../../shared/components/otherOption'
import { formatMoney } from '../../shared/components/money'

/** 收款方式。值是中文本身（后端 `payment_method` 是自由文本），
 *  选中「其他」后写的内容可以直接存回该字段。 */
const PAYMENT_METHODS = ['银行转账', '承兑汇票', '现金', '支票', '其他']
import PaymentVoucherControl from '../common/PaymentVoucherControl'
import { newRequestKey } from '../../shared/api/requestKey'

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
        <Tag color="orange">未完成</Tag>
        <span>
          整单还没发完，还剩 <strong>{summary.remaining}</strong> 件未发
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
      <Tag color="green">已发完</Tag>
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

export default function OrderDetailPage() {
  const params = useParams()
  const orderId = Number(params.id)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canManage = can('order:manage')
  const canPayment = can('payment:manage')

  const [activeKey, setActiveKey] = useState('items')
  const [statusVisible, setStatusVisible] = useState(false)
  const [targetStatus, setTargetStatus] = useState<string | null>(null)
  //: 取消订单是**不可逆的终态动作**，单独一个确认弹窗把影响讲清楚
  //: （见下面「取消订单」那个 Modal）。
  const [cancelVisible, setCancelVisible] = useState(false)
  const [generateVisible, setGenerateVisible] = useState(false)
  const [generateDates, setGenerateDates] = useState<{ first?: Date; second?: Date }>({})
  const [paymentTarget, setPaymentTarget] = useState<Receivable | null>(null)
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
    enabled: Number.isFinite(orderId) && activeKey === 'payments',
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
    enabled: Number.isFinite(orderId) && activeKey === 'shipments',
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
  // 节点责任人从用户里选（方案 :103）：手填人名对不上人，逾期了也不知道催谁
  const milestoneUsersQuery = useQuery({
    queryKey: ['assignable-users'],
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
    // 表格里也要把责任人显示成人名（不只是弹窗里），所以跟着页签加载
    enabled: Number.isFinite(orderId) && activeKey === 'milestones',
  })
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
    void queryClient.invalidateQueries({ queryKey: ['orders'] })
    void queryClient.invalidateQueries({ queryKey: ['receivables'] })
    void queryClient.invalidateQueries({ queryKey: ['payments'] })
  }

  const statusMutation = useMutation({
    mutationFn: () => changeOrderStatus(orderId, targetStatus!, '在订单详情页更新'),
    onSuccess: (result) => {
      // 文案要说清**是什么对象**变了，不能只写"已更新为「已发货」"——
      // 提示是全局浮层，用户切到别的页面它还会挂几秒，没有主语就完全对不上号。
      Toast.success(`${result.order_no} 的履约状态已更新为「${result.status_label}」`)
      setStatusVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

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
    mutationFn: () => cancelOrder(orderId),
    onSuccess: (result) => {
      Toast.success(
        `${result.order_no} 已取消；未回清的应收计划已一并作废，不再发催收提醒`,
      )
      setCancelVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const generateMutation = useMutation({
    mutationFn: () =>
      generateReceivables(orderId, {
        ratios: [0.3, 0.7],
        first_due_date: (generateDates.first ?? new Date()).toISOString().slice(0, 10),
        second_due_date: (generateDates.second ?? new Date()).toISOString().slice(0, 10),
        first_name: '定金',
        second_name: '尾款',
      }),
    onSuccess: () => {
      Toast.success('已生成 30% 定金 + 70% 尾款')
      setGenerateVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const paymentMutation = useMutation({
    mutationFn: () => {
      // 兜底：极端情况下（比如将来多开了一个入口忘了生成键）也要保证同一份
      // 表单的重试复用同一把键，所以这里只在为空时补一把。
      if (!paymentKeyRef.current) paymentKeyRef.current = newRequestKey()
      return createPayment({
        receivable_plan_id: paymentTarget!.id,
        received_date: paymentForm.date.toISOString().slice(0, 10),
        received_amount: Number(paymentForm.amount),
        payment_method: paymentForm.method,
        request_key: paymentKeyRef.current,
      })
    },
    onSuccess: () => {
      Toast.success('回款已登记，等待财务确认')
      setPaymentTarget(null)
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
      Toast.success('回款已确认')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ---- 发货批次（§3.5/场景13）----
  const [createShipmentVisible, setCreateShipmentVisible] = useState(false)
  const [plannedQtys, setPlannedQtys] = useState<Record<number, string>>({})
  const [shipTarget, setShipTarget] = useState<ShipmentBatchRow | null>(null)
  const [shipForm, setShipForm] = useState<{ date: Date | undefined; company: string; tracking: string }>({
    date: new Date(),
    company: '',
    tracking: '',
  })

  const shipmentsRefresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['timeline', 'customer'] })
    void queryClient.invalidateQueries({ queryKey: ['order-shipments', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order', orderId] })
    void queryClient.invalidateQueries({ queryKey: ['order-status', orderId] })
  }
  const createShipmentMutation = useMutation({
    mutationFn: () =>
      createOrderShipment(orderId, {
        items: Object.entries(plannedQtys)
          .filter(([, qty]) => Number(qty) > 0)
          .map(([orderItemId, qty]) => ({ order_item_id: Number(orderItemId), planned_qty: Number(qty) })),
      }),
    onSuccess: () => {
      Toast.success('发货批次已排')
      setCreateShipmentVisible(false)
      setPlannedQtys({})
      shipmentsRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const shipShipmentMutation = useMutation({
    mutationFn: (batchId: number) =>
      shipOrderShipment(orderId, batchId, {
        actual_ship_date: shipForm.date ? shipForm.date.toISOString().slice(0, 10) : null,
        logistics_company: shipForm.company || null,
        tracking_no: shipForm.tracking || null,
        // 缺省按计划量全发；后端校验累计不超订购量
        items: null,
      }),
    onSuccess: (result) => {
      Toast.success(
        result.summary.all_shipped
          ? '已全部发完，整单可以标记完成了'
          : `已登记发货，剩余未发 ${result.summary.remaining}`,
      )
      setShipTarget(null)
      shipmentsRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const cancelShipmentMutation = useMutation({
    mutationFn: (batchId: number) => cancelOrderShipment(orderId, batchId),
    onSuccess: () => {
      Toast.success('批次已取消')
      shipmentsRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const syncMutation = useMutation({
    mutationFn: () => syncErp(orderId),
    onSuccess: (data) => {
      // already_synced 是幂等命中：订单早就推过了，不是失败
      if (data.already_synced) {
        Toast.info(data.message ?? '该订单已推送过，未重复建单')
      } else {
        Toast.success(data.message ?? '已推送到 ERP/MES')
      }
      void queryClient.invalidateQueries({ queryKey: ['order', orderId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const statusSyncMutation = useMutation({
    mutationFn: () => refreshStatus(orderId),
    onSuccess: (data) => {
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
  if (orderQuery.isLoading) return <div className="page-container">加载中…</div>
  if (!order) return <div className="page-container">订单不存在或无权查看</div>

  const summary = summaryQuery.data

  // §9.9 复审：详情页原来只取"币种前缀"再自己拼，币种为空（历史订单）时就成了
  // 一个**裸金额** —— 而列表页早就用 `formatMoney`（空币种会写「币种待核实」）。
  // 同一个数在两个页面口径不同，这里统一成 formatMoney。
  const itemColumns = [
    { title: 'SKU / 需求', dataIndex: 'sku_code', width: 180, render: (code: string | null, row: OrderItem) => <div>{code || row.inquiry_no_snapshot || '—'}<div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{row.sku_snapshot || ''}</div></div> },
    { title: '规格', dataIndex: 'specification', width: 200, render: (v: string | null) => v ?? '-' },
    { title: '数量', dataIndex: 'quantity', width: 100, render: (v: number) => v.toLocaleString('zh-CN') },
    { title: '单价', dataIndex: 'unit_price', width: 110, render: (v: number) => formatMoney(v, order.currency) },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 140,
      render: (v: number) => formatMoney(v, order.currency),
    },
    { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
  ]

  const receivableColumns = [
    { title: '节点', dataIndex: 'plan_name', width: 110 },
    { title: '应收日期', dataIndex: 'due_date', width: 130 },
    // 行自带币种就优先用它（历史数据里可能与订单币种不一致），没有才退回订单币种
    { title: '应收金额', dataIndex: 'amount', width: 130, render: (v: number, r: Receivable) => formatMoney(v, r.currency ?? order.currency) },
    { title: '已收', dataIndex: 'received_amount', width: 130, render: (v: number, r: Receivable) => formatMoney(v, r.currency ?? order.currency) },
    { title: '未收', dataIndex: 'remaining_amount', width: 130, render: (v: number, r: Receivable) => formatMoney(v, r.currency ?? order.currency) },
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
      width: 110,
      render: (_: unknown, record: Receivable) =>
        record.status !== 'paid' && (canManage || canPayment) ? (
          <a
            style={{ color: 'var(--crm-primary)' }}
            onClick={() => {
              setPaymentTarget(record)
              setPaymentForm({
                amount: String(record.remaining_amount),
                date: new Date(),
                method: '银行转账',
              })
              // 打开表单就定下这一笔的请求键；中途提交失败重试仍用它。
              paymentKeyRef.current = newRequestKey()
            }}
          >
            登记回款
          </a>
        ) : (
          '-'
        ),
    },
  ]

  return (
    <div className="page-container">
      {/* 与客户/商机/报价详情页同排布：标题 + 标签一行，关键信息行在标题下方左对齐 */}
      <DetailHeader
        title={order.order_no}
        tags={
          <>
            <Tag color={STATUS_TONE[order.status] ?? 'grey'}>{order.status_label}</Tag>
            {order.erp_order_id ? <Tag color="blue">ERP/MES：{order.erp_order_id}</Tag> : <Tag>未推送 ERP/MES</Tag>}
          </>
        }
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
            <span>客户要求{order.delivery_kind === 'arrival' ? '到货日' : order.delivery_kind === 'shipping' ? '发货日' : '交期（类型待确认）'}：{order.delivery_date ?? '-'}</span>
            {order.shipment_date && <span>计划发货日：{order.shipment_date}{order.delivery_kind === 'arrival' ? `（运输 ${order.transit_days} 天）` : ''}</span>}
            <span>付款条件：{order.payment_terms ?? '-'}</span>
            {order.quote_id && (
              <span>
                来源报价：
                <Link
                  to={`/quotes/${order.quote_id}?version=${order.quote_version_id}`}
                  style={{ color: 'var(--crm-primary)' }}
                >
                  查看
                </Link>
              </span>
            )}
          </>
        }
        extra={
          canManage && (
            <>
              {/* 已取消是**终态**，状态不能再改、也不能再取消：
                  入口留着只会让人点了必然失败（后端会拒），所以两个都不给。
                  改状态一直要给，是因为履约状态本来就是逐段推进的。 */}
              {order.status !== 'cancelled' && (
                <>
                  <Button onClick={() => setStatusVisible(true)}>更新履约状态</Button>
                  {/* 取消订单**单独一个入口**，不混在"更新履约状态"的下拉里：
                      它牵动回款与应收（作废应收计划、驳回待确认回款、取消计划批次），
                      混进一个通用下拉，用户既不知道选下去会发生什么，
                      也会绕过取消规则（已收到确认回款的订单本不该能取消）。 */}
                  <Button type="danger" onClick={() => setCancelVisible(true)}>
                    取消订单
                  </Button>
                </>
              )}
              <Button onClick={() => syncMutation.mutate()} loading={syncMutation.isPending}>
                推送 ERP/MES
              </Button>
              <Button
                onClick={() => statusSyncMutation.mutate()}
                loading={statusSyncMutation.isPending}
                disabled={!order.erp_order_id}
              >
                同步履约状态
              </Button>
              <Button onClick={() => repurchaseMutation.mutate()} loading={repurchaseMutation.isPending}>
                复购开新商机
              </Button>
            </>
          )
        }
      >
        <KpiStrip
          style={{ marginTop: 16, marginBottom: 0 }}
          items={[
            { label: '订单金额', value: formatMoney(order.total_amount, order.currency) },
            {
              label: '已回款（财务已确认）',
              value: formatMoney(order.received_amount, order.currency),
              tone: 'success',
            },
            {
              label: '待回款',
              value: formatMoney(order.unreceived_amount, order.currency),
              tone: order.unreceived_amount > 0 ? 'warning' : 'default',
            },
            {
              label: '待确认回款',
              value: formatMoney(summary?.pending_confirm_amount ?? 0, order.currency),
            },
            {
              label: '逾期应收节点',
              value: summary?.overdue_count ?? 0,
              tone: (summary?.overdue_count ?? 0) > 0 ? 'error' : 'default',
            },
          ]}
        />
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
            <span>产品货款 {formatMoney(order.goods_amount, order.currency)}</span>
            <span>+ 运费（代收代付） {formatMoney(order.logistics_amount ?? 0, order.currency)}</span>
            <span>+ 其他费用 {formatMoney(order.other_charge_amount ?? 0, order.currency)}</span>
            <span>+ 优惠 {formatMoney(order.discount_amount ?? 0, order.currency)}</span>
            <span style={{ color: 'var(--crm-text)' }}>
              = {formatMoney(order.total_amount, order.currency)}
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
        <Tabs type="line" activeKey={activeKey} onChange={setActiveKey} tabList={TABS} />

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
            <BizDocPanel docType="order_sheet" orderId={orderId} canManage={canManage} />
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
                  title: '时间',
                  dataIndex: 'created_at',
                  width: 190,
                  render: (v: string) => new Date(v).toLocaleString('zh-CN'),
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
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
                  一个应收节点可以分多次收款，已收金额只统计财务确认过的回款
                </div>
                <div style={{ flex: 1 }} />
                {canPayment && !(receivablesQuery.data ?? []).length && (
                  <Button theme="solid" onClick={() => setGenerateVisible(true)}>
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
                empty="还没有应收计划"
              />
            </>
          )}

          {activeKey === 'payments' && (
            <Table<Payment>
              columns={[
                { title: '应收节点', dataIndex: 'plan_name', width: 120, render: (v: string | null) => v ?? '-' },
                { title: '收款日期', dataIndex: 'received_date', width: 130 },
                { title: '金额', dataIndex: 'received_amount', width: 130, render: (v: number, r: Payment) => formatMoney(v, r.currency ?? order.currency) },
                { title: '方式', dataIndex: 'payment_method', width: 120, render: (v: string | null) => v ?? '-' },
                {
                  title: '回款凭证',
                  width: 220,
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
                  width: 130,
                  render: (value: string) => (
                    <Tag color={value === '已确认' ? 'green' : value === '已驳回' ? 'red' : 'orange'}>{value}</Tag>
                  ),
                },
                {
                  title: '确认人',
                  dataIndex: 'confirmed_by_name',
                  width: 110,
                  render: (v: string | null) => v ?? '-',
                },
                {
                  title: '操作',
                  width: 90,
                  render: (_: unknown, record: Payment) =>
                    record.status === 'pending' && canPayment ? (
                      <Popconfirm
                        title="确认收到这笔回款？"
                        content="确认后会记入回款并影响业绩口径，请核对金额后再点。"
                        onConfirm={() => confirmMutation.mutate(record.id)}
                      >
                        <a style={{ color: 'var(--crm-success)' }}>确认</a>
                      </Popconfirm>
                    ) : (
                      '-'
                    ),
                },
              ]}
              dataSource={paymentsQuery.data ?? []}
              loading={paymentsQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty="还没有回款记录"
            />
          )}

          {activeKey === 'milestones' && (
            <>
              <div className="toolbar" style={{ marginBottom: 10 }}>
                <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                  通过「交期与计划」预览自然日倒排建议，责任人确认后生效。实际完成另行登记。
                </span>
                <div style={{ flex: 1 }} />
                {can('order:manage') && (
                  <Button
                    size="small"
                    theme="solid"
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
                              <Button
                                size="small"
                                theme="solid"
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
                              <a style={{ marginLeft: 8, color: 'var(--crm-text-3)' }}>作废</a>
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
                          </div>
                        })}
                      </details>
                      {row.reason && <div style={{ marginTop: 4 }}>原因：{row.reason}</div>}
                      {row.confirmed_at && (
                        <div style={{ marginTop: 2, color: 'var(--crm-text-3)' }}>
                          由 {row.confirmed_by_name ?? row.confirmed_by} 于{' '}
                          {row.confirmed_at.slice(0, 16).replace('T', ' ')} 确认
                          {row.confirm_remark ? `（${row.confirm_remark}）` : ''}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              <Table<OrderMilestoneRow>
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
                  { title: '备注 / 跳过原因', dataIndex: 'remark', render: (v: string | null, row: OrderMilestoneRow) => row.status === 'skipped' ? row.skip_reason : v ?? '-' },
                  {
                    // 方案 :103：责任人 / 来源证据 / 逾期原因要能被看到，否则填了也没人用
                    title: '责任人',
                    dataIndex: 'owner_id',
                    width: 100,
                    render: (v: number | null, record: OrderMilestoneRow) =>
                      v
                        ? (milestoneUsersQuery.data?.items ?? []).find((u) => u.id === v)?.name ??
                          `#${v}`
                        : record.owner_id
                          ? `#${record.owner_id}`
                          : '-',
                  },
                  {
                    title: '逾期原因',
                    dataIndex: 'overdue_reason',
                    width: 180,
                    render: (v: string | null) => v ?? '-',
                  },
                  ...(canManage
                    ? [
                        {
                          title: '操作',
                          width: 80,
                          render: (_: unknown, record: OrderMilestoneRow) => (
                            <a onClick={() => openMilestoneEdit(record)}>登记</a>
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
              <div className="toolbar" style={{ marginBottom: 10 }}>
                <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                  订购 {shipmentsQuery.data?.summary.ordered ?? 0} ｜ 已发 {shipmentsQuery.data?.summary.shipped ?? 0} ｜
                  {' '}未发 <strong>{shipmentsQuery.data?.summary.remaining ?? 0}</strong>
                  ——首批/分批发货只推进到「已发货」，全部发完才能整单完成
                </span>
                <div style={{ flex: 1 }} />
                {can('order:manage') && (
                  <Button size="small" theme="solid" onClick={() => setCreateShipmentVisible(true)}>
                    排发货批次
                  </Button>
                )}
              </div>
              {/* 整单交期结论（§9.8 复审）：发完才给最终数字；没发完如实说"未完成 · 还剩几件" */}
              <ShipmentVerdict summary={shipmentsQuery.data?.summary} />
              <Table<ShipmentBatchRow>
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
                    render: (_: unknown, record: ShipmentBatchRow) =>
                      record.tracking_no ? `${record.logistics_company ?? ''} ${record.tracking_no}` : '-',
                  },
                  {
                    title: '本批偏差',
                    width: 110,
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
                      return (
                        record.items
                          .map((item) => {
                            const qty = shipped ? item.shipped_qty : item.planned_qty
                            const tag = shipped ? '实发' : '计划'
                            return `${item.sku ?? item.order_item_id} ${tag} ${qty}`
                          })
                          .join('；') || '-'
                      )
                    },
                  },
                  ...(can('order:manage')
                    ? [
                        {
                          title: '操作',
                          width: 130,
                          render: (_: unknown, record: ShipmentBatchRow) =>
                            record.status === 'planned' ? (
                              <span style={{ display: 'inline-flex', gap: 12 }}>
                                <a onClick={() => setShipTarget(record)}>登记发货</a>
                                <Popconfirm
                                  title="取消该批次？"
                                  onConfirm={() => cancelShipmentMutation.mutate(record.id)}
                                >
                                  <a style={{ color: 'var(--crm-danger, #d45)' }}>取消</a>
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
                empty="还没有发货批次——点「排发货批次」从剩余未发量里排"
              />
            </>
          )}
        </div>
      </SectionCard>

      <Modal
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
            每行填本批计划发货数量（可留空），不能超过该行的未计划量
          </div>
          {(shipmentsQuery.data?.items ?? []).map((item) => (
            <div key={item.order_item_id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ flex: 1 }}>
                {item.sku}（订购 {item.ordered}，未计划 {item.unplanned}）
              </span>
              <Input
                style={{ width: 120 }}
                placeholder="本批数量"
                value={plannedQtys[item.order_item_id] ?? ''}
                onChange={(value) => setPlannedQtys({ ...plannedQtys, [item.order_item_id]: value })}
              />
            </div>
          ))}
        </div>
      </Modal>

      <Modal
        title={`第 ${shipTarget?.batch_no ?? ''} 批登记发货`}
        visible={Boolean(shipTarget)}
        onCancel={() => setShipTarget(null)}
        onOk={() => {
          if (shipTarget) shipShipmentMutation.mutate(shipTarget.id)
        }}
        confirmLoading={shipShipmentMutation.isPending}
        okText="确认发货"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            默认按计划量全发；批次发货后整单推进到「已发货」，全部发完才能标记完成
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>实际发货日期</div>
            <DatePicker
              type="date"
              disabled={milestoneForm.skipped}
              format="yyyy-MM-dd"
              style={{ width: '100%' }}
              value={shipForm.date}
              onChange={(_, dateStr) =>
                setShipForm({ ...shipForm, date: dateStr ? new Date(dateStr as string) : undefined })
              }
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>承运商</div>
            <Input value={shipForm.company} onChange={(v) => setShipForm({ ...shipForm, company: v })} />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>运单号</div>
            <Input value={shipForm.tracking} onChange={(v) => setShipForm({ ...shipForm, tracking: v })} />
          </div>
        </div>
      </Modal>

      <Modal
        title="交期与跟单计划"
        visible={scheduleVisible}
        onCancel={() => setScheduleVisible(false)}
        width={720}
        footer={
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <Button onClick={() => setScheduleVisible(false)}>取消</Button>
            <Button
              loading={schedulePreviewMutation.isPending}
              disabled={!scheduleForm.new_delivery_date || !Number.isFinite(scheduleForm.transit_days) || Object.values(scheduleForm.plan_offsets).some((v) => !Number.isFinite(v))}
              onClick={() => schedulePreviewMutation.mutate()}
            >
              预览受影响面
            </Button>
            <Button
              theme="solid"
              loading={createScheduleMutation.isPending}
              disabled={!schedulePreview}
              onClick={() => createScheduleMutation.mutate()}
            >
              提交待确认计划
            </Button>
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
            <Select
              style={{ width: '100%' }}
              value={milestoneForm.owner_id ?? undefined}
              placeholder="选一个具体的人（逾期时催他）"
              onChange={(value) =>
                setMilestoneForm({ ...milestoneForm, owner_id: (value as number) ?? null })
              }
              optionList={(milestoneUsersQuery.data?.items ?? []).map((u) => ({
                value: u.id,
                label: u.name,
              }))}
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

      {/* 这单签了什么，在订单页就能看到：合同向来是照着报价/订单出的，
          以前只有合同台账那一头能查，从订单倒着找不回去 */}
      <ContractDocsPanel
        orderId={orderId}
        style={{ marginTop: 16 }}
        empty="这单还没有关联的合同文档"
      />

      {can('agent:use') && (
        <SectionCard
          title="AI 分析"
          style={{ marginTop: 16 }}
          extra={
            <Button size="small" loading={aiMutation.isPending} onClick={() => aiMutation.mutate()}>
              回款风险
            </Button>
          }
        >
          <AgentInsight
            envelope={aiEnvelope}
            empty="点右上角按钮运行：统计逾期应收节点、金额与集中度，没配模型也能给出风险等级"
          />
        </SectionCard>
      )}

      <Modal
        title="更新履约状态"
        visible={statusVisible}
        onCancel={() => setStatusVisible(false)}
        onOk={() => {
          if (!targetStatus) {
            Toast.warning('请选择状态')
            return
          }
          statusMutation.mutate()
        }}
        confirmLoading={statusMutation.isPending}
        okText="更新"
      >
        <div style={{ marginBottom: 12, color: 'var(--crm-text-3)', fontSize: 13 }}>
          MES 未接入前由人工维护；接入后这一步会由 MES 回传自动完成。
        </div>
        <Select
          placeholder="选择状态"
          value={targetStatus ?? undefined}
          onChange={(value) => setTargetStatus(value as string)}
          optionList={[
            { value: 'pending', label: '待生产' },
            { value: 'in_production', label: '生产中' },
            { value: 'shipped', label: '已发货' },
            { value: 'delivered', label: '已签收' },
            { value: 'completed', label: '已完成' },
            // 「已取消」**故意不在这里**：取消订单是不可逆的终态动作，
            // 还要连带处理回款与应收，必须走右上角那个专门的「取消订单」。
            // 放在这个下拉里，用户看不到后果，规则也会被绕过。
          ]}
          style={{ width: '100%' }}
        />
      </Modal>

      {/* 取消订单：把"取消到底会影响什么"写在确认之前 */}
      <Modal
        title="取消订单"
        visible={cancelVisible}
        onCancel={() => setCancelVisible(false)}
        onOk={() => cancelMutation.mutate()}
        confirmLoading={cancelMutation.isPending}
        okText="确认取消订单"
        cancelText="再想想"
        okButtonProps={{ type: 'danger' }}
      >
        <div style={{ display: 'grid', gap: 10, fontSize: 13 }}>
          <div>
            即将取消订单 <b>{order.order_no}</b>
            {order.customer_name ? `（${order.customer_name}）` : ''}， 金额{' '}
            {formatMoney(order.total_amount, order.currency)}。
            <b>取消不可撤销。</b>
          </div>
          <div>取消会连带做这几件事：</div>
          <ul style={{ margin: 0, paddingLeft: 20, color: 'var(--crm-text-2)' }}>
            <li>
              未回清的应收计划
              {(() => {
                const open = (receivablesQuery.data ?? []).filter(
                  (row) => row.status !== 'paid' && row.status !== 'cancelled',
                ).length
                return open ? `（当前 ${open} 笔）` : ''
              })()}
              → 一并作废，<b>不再发催收和逾期提醒</b>
            </li>
            <li>还在「待确认」的回款 → 随订单驳回</li>
            <li>还没发货的发货批次 → 随订单取消（已发货的保留，那是既成事实）</li>
          </ul>
          <div style={{ color: 'var(--crm-caution, #b26a00)' }}>
            这单如果已经收到过「已确认」的回款，系统会<b>直接拒绝取消</b> ——
            钱不能跟着订单静默消失，请先人工处理回款。
          </div>
          <div style={{ color: 'var(--crm-text-3)' }}>
            商机成交状态不会自动回退；确需撤销请走失单流程由人工评估。
          </div>
        </div>
      </Modal>

      <Modal
        title="生成应收计划"
        visible={generateVisible}
        onCancel={() => setGenerateVisible(false)}
        onOk={() => generateMutation.mutate()}
        confirmLoading={generateMutation.isPending}
        okText="生成"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            按订单金额 30% 定金 + 70% 尾款生成两个应收节点（合计{' '}
            {formatMoney(order.total_amount, order.currency)}）
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>定金到期日</div>
            <DatePicker
              value={generateDates.first}
              onChange={(date) => setGenerateDates({ ...generateDates, first: date as Date })}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>尾款到期日</div>
            <DatePicker
              value={generateDates.second}
              onChange={(date) => setGenerateDates({ ...generateDates, second: date as Date })}
              style={{ width: '100%' }}
            />
          </div>
        </div>
      </Modal>

      <Modal
        title={`登记回款：${paymentTarget?.plan_name ?? ''}`}
        visible={Boolean(paymentTarget)}
        onCancel={() => setPaymentTarget(null)}
        onOk={() => {
          if (!paymentForm.amount.trim() || Number(paymentForm.amount) <= 0) {
            Toast.warning('请输入有效金额')
            return
          }
          paymentMutation.mutate()
        }}
        confirmLoading={paymentMutation.isPending}
        okText="登记"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            该节点未收{' '}
            {formatMoney(
              paymentTarget?.remaining_amount ?? 0,
              paymentTarget?.currency ?? order.currency,
            )}
          </div>
          <Input
            value={paymentForm.amount}
            onChange={(value) => setPaymentForm({ ...paymentForm, amount: value })}
            placeholder="收款金额"
          />
          <DatePicker
            value={paymentForm.date}
            onChange={(date) => setPaymentForm({ ...paymentForm, date: (date as Date) ?? new Date() })}
            style={{ width: '100%' }}
          />
          <Select
            value={methodOption.selectValue}
            onChange={methodOption.onSelect}
            optionList={PAYMENT_METHODS.map((value) => ({ value, label: value }))}
            style={{ width: '100%' }}
          />
          {methodOption.showInput && (
            <Input
              value={methodOption.inputValue}
              onChange={methodOption.onInput}
              placeholder="请说明是什么收款方式（可不填）"
            />
          )}
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            登记后需要财务确认，确认后才会计入已回款。
          </div>
        </div>
      </Modal>
    </div>
  )
}
