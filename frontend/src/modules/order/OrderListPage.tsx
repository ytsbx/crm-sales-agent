import { useState } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Button, DatePicker, Input, Modal, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import {
  confirmPayment,
  createOrder,
  listOrders,
  listPayments,
  listReceivables,
  rejectPayment,
  type Order,
  type Payment,
  type Receivable,
} from '../../shared/api/order'
import { listCustomers } from '../../shared/api/customer'
import { listSkusForPricing } from '../../shared/api/pricing'
import { listUsers } from '../../shared/api/system'
import { useAuthStore } from '../../shared/store/auth'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import PaymentVoucherControl from '../common/PaymentVoucherControl'
import FormLabel from '../../shared/components/FormLabel'

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

/** 手工建单一行明细的空白形态。 */
const EMPTY_ITEM = { sku_id: null as number | null, quantity: '', unit_price: '', specification: '' }

export default function OrderListPage({ initialTab = 'orders' }: { initialTab?: string }) {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const currentUser = useAuthStore((state) => state.user)
  const [activeKey, setActiveKey] = useState(initialTab)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [createVisible, setCreateVisible] = useState(false)
  const [createForm, setCreateForm] = useState({
    customer_id: null as number | null,
    owner_id: null as number | null,
    delivery_date: '',
    payment_terms: '',
    remark: '',
    items: [{ ...EMPTY_ITEM }],
  })

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
  // 手工建单的下拉数据：客户 / SKU / 负责人
  const createCustomersQuery = useQuery({
    queryKey: ['customers-for-select'],
    queryFn: () => listCustomers({ page: 1, page_size: 100 }),
    enabled: createVisible,
  })
  const createSkusQuery = useQuery({
    queryKey: ['skus-for-pricing'],
    queryFn: listSkusForPricing,
    enabled: createVisible,
  })
  const createUsersQuery = useQuery({
    queryKey: ['users-for-select'],
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
    enabled: createVisible,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['timeline', 'customer'] })
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

  const validItems = createForm.items.filter(
    (item) => item.sku_id && Number(item.quantity) > 0 && Number(item.unit_price) >= 0,
  )
  const createMutation = useMutation({
    mutationFn: () =>
      createOrder({
        customer_id: createForm.customer_id!,
        items: validItems.map((item) => ({
          sku_id: item.sku_id!,
          quantity: Number(item.quantity),
          unit_price: Number(item.unit_price),
          specification: item.specification.trim() || undefined,
        })),
        owner_id: createForm.owner_id ?? undefined,
        delivery_date: createForm.delivery_date.trim() || undefined,
        payment_terms: createForm.payment_terms.trim() || undefined,
        remark: createForm.remark.trim() || undefined,
      }),
    onSuccess: (data) => {
      Toast.success(`订单 ${data.order_no} 已创建（¥${data.total_amount.toLocaleString('zh-CN')}）`)
      setCreateVisible(false)
      setCreateForm({
        customer_id: null,
        owner_id: null,
        delivery_date: '',
        payment_terms: '',
        remark: '',
        items: [{ ...EMPTY_ITEM }],
      })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const submitCreate = () => {
    if (!createForm.customer_id) {
      Toast.warning('请选择客户')
      return
    }
    if (!validItems.length) {
      Toast.warning('至少要有一条完整明细（SKU、数量、单价）')
      return
    }
    createMutation.mutate()
  }

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
      title: '回款凭证',
      width: 220,
      render: (_: unknown, record: Payment) => (
        <PaymentVoucherControl
          payment={record}
          onChanged={() => void queryClient.invalidateQueries({ queryKey: ['payments'] })}
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
        extra={<Link to="/order-drafts"><Button>订单草稿</Button></Link>}
        subtitle="成交报价转成订单后，履约由 ERP/MES 负责、回款由财务确认；CRM 只保留关键节点"
      />

      <SectionCard>
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
                {/* 手工建单不需要"客户已接受报价"却计入业绩，口径是**只有主管能用**：
                    后端按 order:assign 把守，按钮跟着同一权限点走（不能只靠前端隐藏，
                    但也不该让业务员点到一个必然 403 的按钮）。 */}
                {can('order:assign') && (
                  <Button theme="solid" onClick={() => setCreateVisible(true)}>
                    手工建单
                  </Button>
                )}
              </div>
              <Table<Order>
                columns={orderColumns}
                dataSource={ordersQuery.data?.items ?? []}
                loading={ordersQuery.isLoading}
                rowKey="id"
                empty={emptyText(ordersQuery, '还没有订单，去报价中心把成交的报价转成订单')}
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
      </SectionCard>

      <Modal
        title="手工建单（线下签约 / 补录历史单）"
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={submitCreate}
        confirmLoading={createMutation.isPending}
        okText="创建订单"
        width={720}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 12 }}>
            <div>
              <FormLabel required>客户</FormLabel>
              <Select
                placeholder="选择客户"
                value={createForm.customer_id ?? undefined}
                onChange={(value) => setCreateForm({ ...createForm, customer_id: value as number })}
                optionList={(createCustomersQuery.data?.items ?? []).map((item) => ({
                  value: item.id,
                  label: item.name,
                }))}
                filter
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>负责人（默认自己）</div>
              <Select
                placeholder="选择负责人"
                value={(createForm.owner_id ?? currentUser?.id) as number}
                onChange={(value) => setCreateForm({ ...createForm, owner_id: value as number })}
                optionList={(createUsersQuery.data?.items ?? []).map((item) => ({
                  value: item.id,
                  label: item.name,
                }))}
                filter
                style={{ width: '100%' }}
              />
            </div>
          </div>

          <div>
            <FormLabel required style={{ fontWeight: 600 }}>订单明细</FormLabel>
            <div style={{ display: 'grid', gap: 8 }}>
              {createForm.items.map((item, index) => (
                <div key={index} style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) 90px 110px minmax(0, 1.2fr) auto', gap: 8 }}>
                  <Select
                    placeholder="选择 SKU"
                    value={item.sku_id ?? undefined}
                    onChange={(value) => {
                      const items = [...createForm.items]
                      items[index] = { ...item, sku_id: value as number }
                      setCreateForm({ ...createForm, items })
                    }}
                    optionList={(createSkusQuery.data ?? []).map((sku) => ({
                      value: sku.id,
                      label: `${sku.sku_code} · ${sku.product_name ?? ''} ${sku.specification ?? ''}`,
                    }))}
                    filter
                    style={{ width: '100%' }}
                  />
                  <Input
                    placeholder="数量"
                    value={item.quantity}
                    onChange={(value) => {
                      const items = [...createForm.items]
                      items[index] = { ...item, quantity: value }
                      setCreateForm({ ...createForm, items })
                    }}
                  />
                  <Input
                    placeholder="单价"
                    value={item.unit_price}
                    onChange={(value) => {
                      const items = [...createForm.items]
                      items[index] = { ...item, unit_price: value }
                      setCreateForm({ ...createForm, items })
                    }}
                  />
                  <Input
                    placeholder="规格（可空）"
                    value={item.specification}
                    onChange={(value) => {
                      const items = [...createForm.items]
                      items[index] = { ...item, specification: value }
                      setCreateForm({ ...createForm, items })
                    }}
                  />
                  <Button
                    type="danger"
                    disabled={createForm.items.length === 1}
                    onClick={() =>
                      setCreateForm({
                        ...createForm,
                        items: createForm.items.filter((_, i) => i !== index),
                      })
                    }
                  >
                    删
                  </Button>
                </div>
              ))}
            </div>
            <Button
              style={{ marginTop: 8 }}
              onClick={() => setCreateForm({ ...createForm, items: [...createForm.items, { ...EMPTY_ITEM }] })}
            >
              加一行
            </Button>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 12 }}>
            <div>
              <div style={{ marginBottom: 4 }}>交期（可空）</div>
              <DatePicker
                type="date"
                format="yyyy-MM-dd"
                showClear
                style={{ width: '100%' }}
                placeholder="选择交期"
                value={createForm.delivery_date ? new Date(createForm.delivery_date) : undefined}
                onChange={(_, dateStr) =>
                  setCreateForm({ ...createForm, delivery_date: (dateStr as string) || '' })
                }
              />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>付款条件（可空）</div>
              <Input
                placeholder="款到发货"
                value={createForm.payment_terms}
                onChange={(value) => setCreateForm({ ...createForm, payment_terms: value })}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注（可空）</div>
            <Input
              value={createForm.remark}
              onChange={(value) => setCreateForm({ ...createForm, remark: value })}
            />
          </div>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            金额由明细自动算出，不需要手填；创建后可在订单详情里生成应收计划。
          </div>
        </div>
      </Modal>
    </div>
  )
}
