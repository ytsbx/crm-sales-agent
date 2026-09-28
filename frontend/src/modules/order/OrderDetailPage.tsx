import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Input, Modal, Popconfirm, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'
import type { TagTone } from '../../shared/types'

import {
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
  replanOrderMilestones,
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
} from '../../shared/api/order'
import { usePermissions } from '../../shared/hooks/permissions'
import DetailHeader from '../../shared/components/DetailHeader'
import KpiStrip from '../../shared/components/KpiStrip'
import SectionCard from '../../shared/components/SectionCard'
import AgentInsight from '../../shared/components/AgentInsight'
import { agentRiskAnalysis, type AnalysisEnvelope } from '../../shared/api/agent'

const TABS = [
  { tab: '订单明细', itemKey: 'items' },
  { tab: '跟单节点', itemKey: 'milestones' },
  { tab: '发货批次', itemKey: 'shipments' },
  { tab: '履约状态', itemKey: 'status' },
  { tab: '应收计划', itemKey: 'receivables' },
  { tab: '回款记录', itemKey: 'payments' },
]

// 跟单里程碑状态（模块⑤）：完成/逾期/待办，逾期由计划日期与当天比较自动判定
const MILESTONE_TONE: Record<string, TagTone> = {
  done: 'green',
  overdue: 'red',
  pending: 'grey',
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
  const [generateVisible, setGenerateVisible] = useState(false)
  const [generateDates, setGenerateDates] = useState<{ first?: Date; second?: Date }>({})
  const [paymentTarget, setPaymentTarget] = useState<Receivable | null>(null)
  const [paymentForm, setPaymentForm] = useState({ amount: '', date: new Date(), method: '银行转账' })
  // 跟单里程碑（模块⑤）：编辑弹窗状态
  const [milestoneEdit, setMilestoneEdit] = useState<OrderMilestoneRow | null>(null)
  const [milestoneForm, setMilestoneForm] = useState<{
    planned_date: string | null
    actual_date: string | null
    remark: string
  }>({ planned_date: null, actual_date: null, remark: '' })

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
    void queryClient.invalidateQueries({ queryKey: ['order-milestones', orderId] })
  }
  const milestoneSaveMutation = useMutation({
    mutationFn: () =>
      updateOrderMilestone(orderId, milestoneEdit!.id, {
        planned_date: milestoneForm.planned_date,
        actual_date: milestoneForm.actual_date,
        remark: milestoneForm.remark || null,
      }),
    onSuccess: () => {
      Toast.success('里程碑已更新')
      setMilestoneEdit(null)
      milestonesRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const replanMutation = useMutation({
    mutationFn: () => replanOrderMilestones(orderId),
    onSuccess: (data) => {
      Toast.success(`已按交期重排 ${data.changed} 个节点`)
      milestonesRefresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const openMilestoneEdit = (row: OrderMilestoneRow) => {
    setMilestoneEdit(row)
    setMilestoneForm({
      planned_date: row.planned_date,
      actual_date: row.actual_date,
      remark: row.remark ?? '',
    })
  }

  const refresh = () => {
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
      Toast.success(`已更新为「${result.status_label}」`)
      setStatusVisible(false)
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
    mutationFn: () =>
      createPayment({
        receivable_plan_id: paymentTarget!.id,
        received_date: paymentForm.date.toISOString().slice(0, 10),
        received_amount: Number(paymentForm.amount),
        payment_method: paymentForm.method,
      }),
    onSuccess: () => {
      Toast.success('回款已登记，等待财务确认')
      setPaymentTarget(null)
      setPaymentForm({ amount: '', date: new Date(), method: '银行转账' })
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

  const itemColumns = [
    { title: 'SKU', dataIndex: 'sku_code', width: 140 },
    { title: '规格', dataIndex: 'specification', width: 200, render: (v: string | null) => v ?? '-' },
    { title: '数量', dataIndex: 'quantity', width: 100, render: (v: number) => v.toLocaleString('zh-CN') },
    { title: '单价', dataIndex: 'unit_price', width: 110, render: (v: number) => `¥${v}` },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 140,
      render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
    },
    { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
  ]

  const receivableColumns = [
    { title: '节点', dataIndex: 'plan_name', width: 110 },
    { title: '应收日期', dataIndex: 'due_date', width: 130 },
    { title: '应收金额', dataIndex: 'amount', width: 130, render: (v: number) => `¥${v.toLocaleString('zh-CN')}` },
    { title: '已收', dataIndex: 'received_amount', width: 130, render: (v: number) => `¥${v.toLocaleString('zh-CN')}` },
    { title: '未收', dataIndex: 'remaining_amount', width: 130, render: (v: number) => `¥${v.toLocaleString('zh-CN')}` },
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
            <span>交期：{order.delivery_date ?? '-'}</span>
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
              <Button onClick={() => setStatusVisible(true)}>更新履约状态</Button>
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
            { label: '订单金额', value: `¥${order.total_amount.toLocaleString('zh-CN')}` },
            {
              label: '已回款（财务已确认）',
              value: `¥${order.received_amount.toLocaleString('zh-CN')}`,
              tone: 'success',
            },
            {
              label: '待回款',
              value: `¥${order.unreceived_amount.toLocaleString('zh-CN')}`,
              tone: order.unreceived_amount > 0 ? 'warning' : 'default',
            },
            {
              label: '待确认回款',
              value: `¥${(summary?.pending_confirm_amount ?? 0).toLocaleString('zh-CN')}`,
            },
            {
              label: '逾期应收节点',
              value: summary?.overdue_count ?? 0,
              tone: (summary?.overdue_count ?? 0) > 0 ? 'error' : 'default',
            },
          ]}
        />
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
                { title: '金额', dataIndex: 'received_amount', width: 130, render: (v: number) => `¥${v.toLocaleString('zh-CN')}` },
                { title: '方式', dataIndex: 'payment_method', width: 120, render: (v: string | null) => v ?? '-' },
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
                      <a style={{ color: 'var(--crm-success)' }} onClick={() => confirmMutation.mutate(record.id)}>
                        确认
                      </a>
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
                  计划日期按客户交期倒推；登记实际日期后该节点标记完成。交期变了？点「按交期重排」
                </span>
                <div style={{ flex: 1 }} />
                {canManage && (
                  <Button
                    size="small"
                    loading={replanMutation.isPending}
                    onClick={() => replanMutation.mutate()}
                  >
                    按交期重排
                  </Button>
                )}
              </div>
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
                  { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
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
                    title: '本批明细',
                    render: (_: unknown, record: ShipmentBatchRow) =>
                      record.items.map((item) => `${item.sku ?? item.order_item_id} ×${item.shipped_qty || item.planned_qty}`).join('；') || '-',
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
        title={`登记里程碑：${milestoneEdit?.label ?? ''}`}
        visible={Boolean(milestoneEdit)}
        onCancel={() => setMilestoneEdit(null)}
        onOk={() => milestoneSaveMutation.mutate()}
        confirmLoading={milestoneSaveMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>计划日期（交期倒推，可手工调整）</div>
            <DatePicker
              type="date"
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
        </div>
      </Modal>

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
            { value: 'cancelled', label: '已取消' },
          ]}
          style={{ width: '100%' }}
        />
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
            按订单金额 30% 定金 + 70% 尾款生成两个应收节点（合计 ¥
            {order.total_amount.toLocaleString('zh-CN')}）
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
            该节点未收 ¥{paymentTarget?.remaining_amount.toLocaleString('zh-CN')}
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
            value={paymentForm.method}
            onChange={(value) => setPaymentForm({ ...paymentForm, method: value as string })}
            optionList={['银行转账', '承兑汇票', '现金', '支票', '其他'].map((value) => ({ value, label: value }))}
            style={{ width: '100%' }}
          />
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            登记后需要财务确认，确认后才会计入已回款。
          </div>
        </div>
      </Modal>
    </div>
  )
}
