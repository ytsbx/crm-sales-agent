import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Input, Modal, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'
import type { TagTone } from '../../shared/types'

import {
  changeOrderStatus,
  confirmPayment,
  createPayment,
  generateReceivables,
  getOrder,
  listOrderItems,
  listOrderPayments,
  listOrderReceivables,
  listOrderStatusHistory,
  orderFinanceSummary,
  repurchase,
  syncErp,
  type OrderItem,
  type OrderStatusRow,
  type Payment,
  type Receivable,
} from '../../shared/api/order'
import { usePermissions } from '../../shared/hooks/permissions'
import DetailHeader from '../../shared/components/DetailHeader'

const TABS = [
  { tab: '订单明细', itemKey: 'items' },
  { tab: '履约状态', itemKey: 'status' },
  { tab: '应收计划', itemKey: 'receivables' },
  { tab: '回款记录', itemKey: 'payments' },
]

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

  const syncMutation = useMutation({
    mutationFn: () => syncErp(orderId),
    onSuccess: (data) => Toast.warning(data.reason),
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
              <Button onClick={() => repurchaseMutation.mutate()} loading={repurchaseMutation.isPending}>
                复购开新商机
              </Button>
            </>
          )
        }
      >
        <div className="kpi-grid" style={{ marginTop: 16, marginBottom: 0 }}>
          <div className="kpi-card">
            <div className="kpi-label">订单金额</div>
            <div className="kpi-value">¥{order.total_amount.toLocaleString('zh-CN')}</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">已回款（财务已确认）</div>
            <div className="kpi-value" style={{ color: 'var(--crm-success)' }}>
              ¥{order.received_amount.toLocaleString('zh-CN')}
            </div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">待回款</div>
            <div className="kpi-value" style={{ color: order.unreceived_amount > 0 ? 'var(--crm-caution)' : undefined }}>
              ¥{order.unreceived_amount.toLocaleString('zh-CN')}
            </div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">待确认回款</div>
            <div className="kpi-value">¥{(summary?.pending_confirm_amount ?? 0).toLocaleString('zh-CN')}</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">逾期应收节点</div>
            <div className="kpi-value" style={{ color: (summary?.overdue_count ?? 0) > 0 ? 'var(--crm-error)' : undefined }}>
              {summary?.overdue_count ?? 0}
            </div>
          </div>
        </div>
      </DetailHeader>

      <div className="card-block">
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
        </div>
      </div>

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
