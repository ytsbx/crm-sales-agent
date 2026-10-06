import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Table, TextArea, Toast } from '@douyinfe/semi-ui'
import { createCustomInquiry, listCustomInquiries, type CustomInquiryRow } from '../../shared/api/inquiry'
import { createQuote, listQuotes, type Quote } from '../../shared/api/quote'
import { listSamples, type SampleRequestRow } from '../../shared/api/sample'
import { listOrders, listOrderDrafts, type OrderDraft, type Order } from '../../shared/api/order'
import { usePermissions } from '../../shared/hooks/permissions'
import { emptyText } from '../../shared/hooks/emptyText'
import FormLabel from '../../shared/components/FormLabel'

/** 一条商机承载一次独立采购需求，各单据保留自己的业务状态。 */
export default function OpportunityRecords({ opportunityId, customerId, title }: {
  opportunityId: number; customerId: number; title: string
}) {
  const { can } = usePermissions()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [inquiryVisible, setInquiryVisible] = useState(false)
  const [inquiryForm, setInquiryForm] = useState({ title, description: '', quantity: '' })
  const [pages, setPages] = useState({ inquiries: 1, quotes: 1, samples: 1, orders: 1, drafts: 1 })
  const inquiries = useQuery({
    queryKey: ['custom-inquiries', { opportunityId, page: pages.inquiries }],
    queryFn: () => listCustomInquiries({ opportunity_id: opportunityId, page: pages.inquiries, page_size: 10 }),
    enabled: can('quote:view'),
  })
  const quotes = useQuery({
    queryKey: ['quotes', { opportunityId, page: pages.quotes }],
    queryFn: () => listQuotes({ opportunity_id: opportunityId, page: pages.quotes, page_size: 10 }),
    enabled: can('quote:view'),
  })
  const samples = useQuery({
    queryKey: ['samples', { opportunityId, page: pages.samples }],
    queryFn: () => listSamples({ opportunity_id: opportunityId, page: pages.samples, page_size: 10 }),
    enabled: can('sample:view'),
  })
  const orders = useQuery({
    queryKey: ['orders', { opportunityId, page: pages.orders }],
    queryFn: () => listOrders({ opportunity_id: opportunityId, page: pages.orders, page_size: 10 }),
    enabled: can('order:view'),
  })
  const drafts = useQuery({ queryKey: ['order-drafts', { opportunityId, page: pages.drafts }], queryFn: () => listOrderDrafts({ opportunity_id: opportunityId, page: pages.drafts, page_size: 10 }), enabled: can('order:view') })
  const pagination = (key: keyof typeof pages, total: number) => ({
    currentPage: pages[key], pageSize: 10, total, showSizeChanger: false,
    onPageChange: (page: number) => setPages((current) => ({ ...current, [key]: page })),
  })
  const inquiryMutation = useMutation({
    mutationFn: () => createCustomInquiry({
      title: inquiryForm.title.trim(), description: inquiryForm.description.trim() || null,
      quantity: inquiryForm.quantity.trim() ? Number(inquiryForm.quantity) : null,
      customer_id: customerId, opportunity_id: opportunityId,
    }),
    onSuccess: () => {
      setInquiryVisible(false)
      void queryClient.invalidateQueries({ queryKey: ['custom-inquiries'] })
      void queryClient.invalidateQueries({ queryKey: ['custom-inquiry-summary'] })
      Toast.success('定制询价已记录并关联当前商机')
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const quoteMutation = useMutation({
    mutationFn: () => createQuote({ opportunity_id: opportunityId }),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['quotes'] })
      navigate(`/quotes/${data.quote_id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  return <div style={{ display: 'grid', gap: 24 }}>
    <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
      当前商机对应本次采购需求。下面只展示关联这条商机的单据；客户的其他采购独立管理。
    </div>
    {can('quote:view') && <>
      <div>
        <div className="toolbar"><strong>定制询价及修订</strong><div style={{ flex: 1 }} />
          {can('quote:manage') && <Button onClick={() => {
            setInquiryForm({ title, description: '', quantity: '' }); setInquiryVisible(true)
          }}>记录定制询价</Button>}
        </div>
        <Table<CustomInquiryRow> rowKey="id" loading={inquiries.isLoading}
          dataSource={inquiries.data?.items ?? []} empty={emptyText(inquiries, '暂无关联定制询价')}
          pagination={pagination('inquiries', inquiries.data?.total ?? 0)} columns={[
            { title: '需求编号', dataIndex: 'inquiry_no' },
            { title: '需求', render: (_: unknown, row: CustomInquiryRow) => <Link to={`/knowledge?opportunity_id=${opportunityId}`}>{row.title}</Link> },
            { title: '版本', render: (_: unknown, row: CustomInquiryRow) => `V${row.version ?? 1} · ${row.version_state_label ?? '当前版'}` },
            { title: '状态', dataIndex: 'status_label' },
          ]} />
      </div>
      <div>
        <div className="toolbar"><strong>报价</strong><div style={{ flex: 1 }} />
          {can('quote:manage') && <Button loading={quoteMutation.isPending} onClick={() => quoteMutation.mutate()}>生成报价草稿</Button>}
        </div>
        <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 8 }}>
          草稿带入需求商品；无 SKU 的定制件请从上方定制询价转报价。正式发送后才自动推进到“已报价”。
        </div>
        <Table<Quote> rowKey="id" loading={quotes.isLoading} dataSource={quotes.data?.items ?? []}
          empty={emptyText(quotes, '暂无关联报价')} pagination={pagination('quotes', quotes.data?.total ?? 0)} columns={[
            { title: '报价编号', render: (_: unknown, row: Quote) => <Link to={`/quotes/${row.id}`}>{row.quote_no}</Link> },
            { title: '当前版本', render: (_: unknown, row: Quote) => row.current_version_no ? `V${row.current_version_no}` : '-' },
            { title: '状态', dataIndex: 'status_label' },
          ]} />
      </div>
    </>}
    {can('sample:view') && <div>
      <div className="toolbar"><strong>打样</strong><div style={{ flex: 1 }} />
        {can('sample:manage') && <Button onClick={() => navigate(`/samples?create_opportunity_id=${opportunityId}`)}>申请打样</Button>}
      </div>
      <Table<SampleRequestRow> rowKey="id" loading={samples.isLoading} dataSource={samples.data?.items ?? []}
        empty={emptyText(samples, '暂无关联打样')} pagination={pagination('samples', samples.data?.total ?? 0)} columns={[
          { title: '申请', render: (_: unknown, row: SampleRequestRow) => <Link to={`/samples/${row.id}`}>打样申请 #{row.id}</Link> },
          { title: '状态', dataIndex: 'status_label' },
          { title: '客户确认', dataIndex: 'confirm_status_label' },
        ]} />
    </div>}
    {can('order:view') && <div><strong>订单草稿（准备资料）</strong><Table<OrderDraft> rowKey="id" loading={drafts.isLoading} dataSource={drafts.data?.items ?? []} empty={emptyText(drafts, '暂无关联订单草稿')} pagination={pagination('drafts', drafts.data?.total ?? 0)} columns={[{ title: '草稿', render: (_: unknown, row: OrderDraft) => <Link to={`/order-drafts/${row.id}`}>草稿 #{row.id}</Link> }, { title: '状态', render: (_: unknown, row: OrderDraft) => row.status === 'converted' ? '已转正式订单' : '准备中' }]} /></div>}
    {can('order:view') && <div>
      <div className="toolbar"><strong>订单</strong></div>
      <Table<Order> rowKey="id" loading={orders.isLoading} dataSource={orders.data?.items ?? []}
        empty={emptyText(orders, '暂无关联订单')} pagination={pagination('orders', orders.data?.total ?? 0)} columns={[
          { title: '订单编号', render: (_: unknown, row: Order) => <Link to={`/orders/${row.id}`}>{row.order_no}</Link> },
          { title: '状态', dataIndex: 'status_label' },
          { title: '金额', render: (_: unknown, row: Order) => row.total_amount.toLocaleString('zh-CN') },
        ]} />
    </div>}
    <Modal title="记录本次需求的定制询价" visible={inquiryVisible} onCancel={() => setInquiryVisible(false)}
      confirmLoading={inquiryMutation.isPending} okText="保存" onOk={() => {
        if (!inquiryForm.title.trim()) { Toast.warning('需求标题必填'); return }
        if (inquiryForm.quantity.trim() && (!Number.isFinite(Number(inquiryForm.quantity)) || Number(inquiryForm.quantity) <= 0)) {
          Toast.warning('需求数量须大于 0'); return
        }
        inquiryMutation.mutate()
      }}>
      <div style={{ display: 'grid', gap: 12 }}>
        <div>关联商机：{title}（客户自动带入）</div>
        <div>
          <FormLabel required>需求标题</FormLabel>
          <Input value={inquiryForm.title} onChange={(value) => setInquiryForm({ ...inquiryForm, title: value })} />
        </div>
        <div>需求描述<TextArea value={inquiryForm.description} onChange={(value) => setInquiryForm({ ...inquiryForm, description: value })} placeholder="材料、尺寸、图纸要求等" /></div>
        <div>需求数量<Input value={inquiryForm.quantity} onChange={(value) => setInquiryForm({ ...inquiryForm, quantity: value })} /></div>
      </div>
    </Modal>
  </div>
}
