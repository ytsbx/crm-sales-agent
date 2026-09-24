import { useState } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import {
  confirmPayment,
  listOrders,
  listPayments,
  listReceivables,
  rejectPayment,
  type Order,
  type Payment,
  type Receivable,
} from '../../shared/api/order'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'

const TABS = [
  { tab: '销售订单', itemKey: 'orders' },
  { tab: '应收计划', itemKey: 'receivables' },
  { tab: '回款记录', itemKey: 'payments' },
]

const ORDER_TONE: Record<string, TagTone> = {
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

export default function OrderListPage({ initialTab = 'orders' }: { initialTab?: string }) {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [activeKey, setActiveKey] = useState(initialTab)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const ordersQuery = useQuery({
    queryKey: ['orders', { keyword, status, page, pageSize }],
    queryFn: () => listOrders({ keyword, status, page, page_size: pageSize }),
    enabled: activeKey === 'orders',
  })
  const receivablesQuery = useQuery({
    queryKey: ['receivables'],
    queryFn: () => listReceivables({ page: 1, page_size: 100 }),
    enabled: activeKey === 'receivables',
  })
  const paymentsQuery = useQuery({
    queryKey: ['payments'],
    queryFn: () => listPayments({ page: 1, page_size: 100 }),
    enabled: activeKey === 'payments',
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['orders'] })
    void queryClient.invalidateQueries({ queryKey: ['receivables'] })
    void queryClient.invalidateQueries({ queryKey: ['payments'] })
  }

  const confirmMutation = useMutation({
    mutationFn: (id: number) => confirmPayment(id, '财务核对通过'),
    onSuccess: () => {
      Toast.success('回款已确认')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const rejectMutation = useMutation({
    mutationFn: (id: number) => rejectPayment(id, '金额或凭证有误'),
    onSuccess: () => {
      Toast.success('已驳回')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const orderColumns = [
    {
      title: '订单号',
      dataIndex: 'order_no',
      width: 170,
      render: (text: string, record: Order) => (
        <Link to={`/orders/${record.id}`} style={{ color: 'var(--crm-primary)' }}>
          {text}
        </Link>
      ),
    },
    { title: '客户', dataIndex: 'customer_name', width: 220, render: (v: string | null) => v ?? '-' },
    {
      title: '订单金额',
      dataIndex: 'total_amount',
      width: 140,
      render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
    },
    {
      title: '已回款',
      dataIndex: 'received_amount',
      width: 130,
      render: (v: number, record: Order) => (
        <span style={{ color: v >= record.total_amount ? 'var(--crm-success)' : undefined }}>
          ¥{v.toLocaleString('zh-CN')}
        </span>
      ),
    },
    {
      title: '待回款',
      dataIndex: 'unreceived_amount',
      width: 130,
      render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
    },
    {
      title: '履约状态',
      dataIndex: 'status_label',
      width: 120,
      render: (value: string, record: Order) => (
        <Tag color={ORDER_TONE[record.status] ?? 'grey'}>{value}</Tag>
      ),
    },
    {
      title: '交期',
      dataIndex: 'delivery_date',
      width: 120,
      render: (v: string | null) => v ?? '-',
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' },
  ]

  const receivableColumns = [
    { title: '订单号', dataIndex: 'order_no', width: 170, render: (v: string | null) => v ?? '-' },
    { title: '节点', dataIndex: 'plan_name', width: 120 },
    { title: '应收日期', dataIndex: 'due_date', width: 130 },
    {
      title: '应收金额',
      dataIndex: 'amount',
      width: 130,
      render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
    },
    {
      title: '已收',
      dataIndex: 'received_amount',
      width: 130,
      render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
    },
    {
      title: '未收',
      dataIndex: 'remaining_amount',
      width: 130,
      render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
    },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: 110,
      render: (value: string, record: Receivable) => (
        <Tag color={PLAN_TONE[record.status] ?? 'grey'}>{value}</Tag>
      ),
    },
  ]

  const paymentColumns = [
    { title: '订单号', dataIndex: 'order_no', width: 170, render: (v: string | null) => v ?? '-' },
    { title: '应收节点', dataIndex: 'plan_name', width: 120, render: (v: string | null) => v ?? '-' },
    { title: '收款日期', dataIndex: 'received_date', width: 130 },
    {
      title: '金额',
      dataIndex: 'received_amount',
      width: 130,
      render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
    },
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
      width: 150,
      render: (_: unknown, record: Payment) =>
        record.status === 'pending' && can('payment:manage') ? (
          <div style={{ display: 'flex', gap: 10 }}>
            <a style={{ color: 'var(--crm-success)' }} onClick={() => confirmMutation.mutate(record.id)}>
              确认
            </a>
            <a style={{ color: 'var(--crm-error)' }} onClick={() => rejectMutation.mutate(record.id)}>
              驳回
            </a>
          </div>
        ) : (
          '-'
        ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="订单中心"
        subtitle="成交报价转成订单后，履约由 ERP/MES 负责、回款由财务确认；CRM 只保留关键节点"
      />

      <div className="card-block">
        <Tabs type="line" activeKey={activeKey} onChange={setActiveKey} tabList={TABS} />

        <div style={{ marginTop: 16 }}>
          {activeKey === 'orders' && (
            <>
              <div className="toolbar">
                <Input
                  placeholder="搜索订单号"
                  value={keywordInput}
                  onChange={setKeywordInput}
                  onEnterPress={() => {
                    setKeyword(keywordInput.trim())
                    setPage(1)
                  }}
                  style={{ width: 220 }}
                  showClear
                />
                <Select
                  placeholder="履约状态"
                  value={status}
                  onChange={(value) => {
                    setStatus(value as string | undefined)
                    setPage(1)
                  }}
                  optionList={[
                    { value: 'pending', label: '待生产' },
                    { value: 'in_production', label: '生产中' },
                    { value: 'shipped', label: '已发货' },
                    { value: 'delivered', label: '已签收' },
                    { value: 'completed', label: '已完成' },
                    { value: 'cancelled', label: '已取消' },
                  ]}
                  style={{ width: 150 }}
                  showClear
                />
                <Button
                  onClick={() => {
                    setKeyword(keywordInput.trim())
                    setPage(1)
                  }}
                >
                  查询
                </Button>
              </div>
              <Table<Order>
                columns={orderColumns}
                dataSource={ordersQuery.data?.items ?? []}
                loading={ordersQuery.isLoading}
                rowKey="id"
                empty="还没有订单，去报价中心把成交的报价转成订单"
                pagination={{
                  currentPage: page,
                  pageSize,
                  total: ordersQuery.data?.total ?? 0,
                  showSizeChanger: true,
                  onPageChange: (next: number) => setPage(next),
                  onPageSizeChange: (size: number) => {
                    setPageSize(size)
                    setPage(1)
                  },
                }}
              />
            </>
          )}

          {activeKey === 'receivables' && (
            <Table<Receivable>
              columns={receivableColumns}
              dataSource={receivablesQuery.data?.items ?? []}
              loading={receivablesQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty="还没有应收计划"
            />
          )}

          {activeKey === 'payments' && (
            <Table<Payment>
              columns={paymentColumns}
              dataSource={paymentsQuery.data?.items ?? []}
              loading={paymentsQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty="还没有回款记录"
            />
          )}
        </div>
      </div>
    </div>
  )
}
