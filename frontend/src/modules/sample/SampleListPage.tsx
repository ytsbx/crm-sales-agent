import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import {
  Button,
  Input,
  Modal,
  Select,
  SideSheet,
  Table,
  Tag,
  TextArea,
  Toast,
} from '@douyinfe/semi-ui'

import { listCustomers } from '../../shared/api/customer'
import { listOpportunities } from '../../shared/api/opportunity'
import { listSkusForPricing } from '../../shared/api/pricing'
import {
  addSampleItem,
  approveSample,
  createSample,
  feedbackSample,
  listSamples,
  shipSample,
  signSample,
  type SampleRequestRow,
} from '../../shared/api/sample'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'

/** 样品（PRD §19 / 03-API §26）。 */

const STATUS_COLOR: Record<string, TagTone> = {
  pending: 'orange',
  approved: 'blue',
  rejected: 'red',
  shipped: 'cyan',
  signed: 'green',
}

const STATUS_OPTIONS = [
  { value: 'pending', label: '待审批' },
  { value: 'approved', label: '已批准' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'shipped', label: '已寄样' },
  { value: 'signed', label: '已签收' },
]

function fmt(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '-'
}

export default function SampleListPage() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canManage = can('sample:manage')

  const [status, setStatus] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const [createVisible, setCreateVisible] = useState(false)
  const [form, setForm] = useState<{
    opportunity_id?: number
    customer_id?: number
    remark: string
    items: { sku_id?: number; quantity: string }[]
  }>({ remark: '', items: [{ quantity: '1' }] })

  const [detailId, setDetailId] = useState<number | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [shipForm, setShipForm] = useState({ carrier: '', tracking_no: '', shipping_fee: '' })
  const [feedback, setFeedback] = useState('')
  const [newItem, setNewItem] = useState<{ sku_id?: number; quantity: string }>({ quantity: '1' })

  const query = useQuery({
    queryKey: ['samples', { status, keyword, page, pageSize }],
    queryFn: () =>
      listSamples({
        status,
        keyword: keyword.trim() || undefined,
        page,
        page_size: pageSize,
      }),
  })

  // 详情直接复用列表数据里的完整对象（列表已返回 items/shipments），避免多打一次接口
  const detail: SampleRequestRow | undefined = (query.data?.items ?? []).find(
    (row) => row.id === detailId,
  )

  const customersQuery = useQuery({
    queryKey: ['customers-for-select'],
    queryFn: () => listCustomers({ page: 1, page_size: 100 }),
    enabled: createVisible,
  })
  const opportunitiesQuery = useQuery({
    queryKey: ['opportunities-for-select'],
    queryFn: () => listOpportunities({ page: 1, page_size: 100 }),
    enabled: createVisible,
  })
  const skusQuery = useQuery({ queryKey: ['skus-for-pricing'], queryFn: listSkusForPricing })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['samples'] })
  const onError = (error: Error) => Toast.error(error.message)

  const createMutation = useMutation({
    mutationFn: () =>
      createSample({
        opportunity_id: form.opportunity_id ?? null,
        customer_id: form.customer_id ?? null,
        remark: form.remark.trim() || null,
        items: form.items
          .filter((item) => item.sku_id)
          .map((item) => ({
            sku_id: item.sku_id,
            quantity: Number(item.quantity) || 1,
          })),
      }),
    onSuccess: () => {
      Toast.success('样品申请已创建')
      setCreateVisible(false)
      setForm({ remark: '', items: [{ quantity: '1' }] })
      void refresh()
    },
    onError,
  })

  const approveMutation = useMutation({
    mutationFn: (approved: boolean) =>
      approveSample(detailId!, approved, approved ? undefined : rejectReason),
    onSuccess: (_data, approved) => {
      Toast.success(approved ? '样品已批准' : '样品已拒绝')
      setRejectReason('')
      void refresh()
    },
    onError,
  })

  const shipMutation = useMutation({
    mutationFn: () =>
      shipSample(detailId!, {
        carrier: shipForm.carrier.trim() || null,
        tracking_no: shipForm.tracking_no.trim() || null,
        shipping_fee: shipForm.shipping_fee.trim() ? Number(shipForm.shipping_fee) : 0,
      }),
    onSuccess: () => {
      Toast.success('已登记寄样')
      setShipForm({ carrier: '', tracking_no: '', shipping_fee: '' })
      void refresh()
    },
    onError,
  })

  const signMutation = useMutation({
    mutationFn: () => signSample(detailId!),
    onSuccess: () => {
      Toast.success('已登记签收')
      void refresh()
    },
    onError,
  })

  const feedbackMutation = useMutation({
    mutationFn: () => feedbackSample(detailId!, feedback),
    onSuccess: () => {
      Toast.success('反馈已登记')
      setFeedback('')
      void refresh()
    },
    onError,
  })

  const addItemMutation = useMutation({
    mutationFn: () =>
      addSampleItem(detailId!, {
        sku_id: newItem.sku_id,
        quantity: Number(newItem.quantity) || 1,
      }),
    onSuccess: () => {
      Toast.success('明细已添加')
      setNewItem({ quantity: '1' })
      void refresh()
    },
    onError,
  })

  const columns = [
    {
      title: '客户 / 商机',
      dataIndex: 'customer_name',
      render: (value: string | null, record: SampleRequestRow) => (
        <div>
          <div style={{ fontWeight: 600 }}>{value ?? '-'}</div>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            {record.opportunity_title ?? '未关联商机'}
          </div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: 100,
      render: (value: string, record: SampleRequestRow) => (
        <Tag size="small" color={STATUS_COLOR[record.status] ?? 'grey'}>
          {value}
        </Tag>
      ),
    },
    {
      title: '样品明细',
      dataIndex: 'items',
      render: (items: SampleRequestRow['items']) =>
        items.length === 0 ? (
          <span style={{ color: 'var(--crm-text-3)' }}>无明细</span>
        ) : (
          <span style={{ fontSize: 12 }}>
            {items.map((item) => `${item.sku_code ?? item.sku_id} ×${item.quantity}`).join('、')}
          </span>
        ),
    },
    {
      title: '快递',
      width: 170,
      render: (_: unknown, record: SampleRequestRow) => {
        const shipment = record.shipments[0]
        if (!shipment) return <span style={{ color: 'var(--crm-text-3)' }}>-</span>
        return (
          <span style={{ fontSize: 12 }}>
            {shipment.carrier ?? '-'}
            <br />
            {shipment.tracking_no ?? '-'}
          </span>
        )
      },
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' },
    { title: '申请时间', dataIndex: 'requested_at', width: 170, render: fmt },
    {
      title: '操作',
      width: 90,
      render: (_: unknown, record: SampleRequestRow) => (
        <Button theme="borderless" size="small" onClick={() => setDetailId(record.id)}>
          详情
        </Button>
      ),
    },
  ]

  const skuOptions = (skusQuery.data ?? []).map((sku) => ({
    value: sku.id,
    label: `${sku.sku_code}${sku.specification ? ` · ${sku.specification}` : ''}`,
  }))

  return (
    <div className="page-container">
      <PageHeader
        title="样品管理"
        subtitle="样品申请、寄样、签收与反馈；样品进展会回写到商机的下一步动作"
        extra={
          canManage && (
            <Button theme="solid" type="primary" onClick={() => setCreateVisible(true)}>
              新建样品申请
            </Button>
          )
        }
      />

      <SectionCard>
        <div className="toolbar">
          <Input
            style={{ width: 220 }}
            placeholder="搜索客户或商机"
            value={keyword}
            onChange={setKeyword}
            showClear
          />
          <Select
            style={{ width: 150 }}
            placeholder="全部状态"
            showClear
            value={status}
            onChange={(value) => setStatus(value as string | undefined)}
            optionList={STATUS_OPTIONS}
          />
        </div>

        <Table
          size="small"
          rowKey="id"
          loading={query.isLoading}
          columns={columns}
          dataSource={query.data?.items ?? []}
          empty={emptyText(query, '还没有样品申请')}
          pagination={{
            currentPage: page,
            pageSize,
            total: query.data?.total ?? 0,
            onPageChange: setPage,
            onPageSizeChange: (size: number) => {
              setPageSize(size)
              setPage(1)
            },
          }}
        />
      </SectionCard>

      {/* 新建申请 */}
      <Modal
        title="新建样品申请"
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={() => createMutation.mutate()}
        confirmLoading={createMutation.isPending}
        okText="创建"
        width={560}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>关联商机</div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择商机（会自动带出客户）"
              filter
              showClear
              value={form.opportunity_id}
              onChange={(value) => setForm({ ...form, opportunity_id: value as number | undefined })}
              optionList={(opportunitiesQuery.data?.items ?? []).map((opp) => ({
                value: opp.id,
                label: `${opp.title}${opp.customer_name ? ` · ${opp.customer_name}` : ''}`,
              }))}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              客户（不选商机时必填）
            </div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择客户"
              filter
              showClear
              value={form.customer_id}
              onChange={(value) => setForm({ ...form, customer_id: value as number | undefined })}
              optionList={(customersQuery.data?.items ?? []).map((customer) => ({
                value: customer.id,
                label: customer.name,
              }))}
            />
          </div>

          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>样品明细</div>
            {form.items.map((item, index) => (
              <div key={index} style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                <Select
                  style={{ flex: 1 }}
                  placeholder="选择 SKU"
                  filter
                  value={item.sku_id}
                  onChange={(value) => {
                    const items = [...form.items]
                    items[index] = { ...items[index], sku_id: value as number }
                    setForm({ ...form, items })
                  }}
                  optionList={skuOptions}
                />
                <Input
                  style={{ width: 90 }}
                  value={item.quantity}
                  onChange={(value) => {
                    const items = [...form.items]
                    items[index] = { ...items[index], quantity: value }
                    setForm({ ...form, items })
                  }}
                />
                {form.items.length > 1 && (
                  <Button
                    theme="borderless"
                    type="danger"
                    onClick={() =>
                      setForm({ ...form, items: form.items.filter((_, i) => i !== index) })
                    }
                  >
                    删除
                  </Button>
                )}
              </div>
            ))}
            <Button
              theme="borderless"
              onClick={() => setForm({ ...form, items: [...form.items, { quantity: '1' }] })}
            >
              + 添加一行
            </Button>
          </div>

          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>备注</div>
            <TextArea
              value={form.remark}
              onChange={(value) => setForm({ ...form, remark: value })}
              rows={2}
            />
          </div>
        </div>
      </Modal>

      {/* 详情与流转 */}
      <SideSheet
        title={detail ? `样品申请 #${detail.id}` : '样品申请'}
        visible={detailId !== null}
        onCancel={() => setDetailId(null)}
        width={520}
      >
        {detail && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <Tag size="small" color={STATUS_COLOR[detail.status] ?? 'grey'}>
                {detail.status_label}
              </Tag>
              <span style={{ marginLeft: 8, color: 'var(--crm-text-2)' }}>
                {detail.customer_name ?? '-'}
                {detail.opportunity_title ? ` · ${detail.opportunity_title}` : ''}
              </span>
            </div>

            <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>
              <div>申请人：{detail.owner_name ?? '-'}</div>
              <div>申请时间：{fmt(detail.requested_at)}</div>
              {detail.approved_at && <div>审批时间：{fmt(detail.approved_at)}</div>}
              {detail.shipped_at && <div>寄样时间：{fmt(detail.shipped_at)}</div>}
              {detail.signed_at && <div>签收时间：{fmt(detail.signed_at)}</div>}
              {detail.reject_reason && (
                <div style={{ color: 'var(--crm-error)' }}>拒绝原因：{detail.reject_reason}</div>
              )}
              {detail.remark && <div>备注：{detail.remark}</div>}
            </div>

            <div>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>样品明细</div>
              <Table
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={detail.items}
                empty="暂无明细"
                columns={[
                  { title: 'SKU', dataIndex: 'sku_code', render: (v: string | null) => v ?? '-' },
                  { title: '规格', dataIndex: 'specification', render: (v: string | null) => v ?? '-' },
                  { title: '数量', dataIndex: 'quantity', width: 80 },
                ]}
              />
              {canManage && !['shipped', 'signed'].includes(detail.status) && (
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <Select
                    style={{ flex: 1 }}
                    placeholder="追加 SKU"
                    filter
                    value={newItem.sku_id}
                    onChange={(value) => setNewItem({ ...newItem, sku_id: value as number })}
                    optionList={skuOptions}
                  />
                  <Input
                    style={{ width: 80 }}
                    value={newItem.quantity}
                    onChange={(value) => setNewItem({ ...newItem, quantity: value })}
                  />
                  <Button
                    disabled={!newItem.sku_id}
                    loading={addItemMutation.isPending}
                    onClick={() => addItemMutation.mutate()}
                  >
                    添加
                  </Button>
                </div>
              )}
            </div>

            {detail.shipments.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>寄样记录</div>
                <Table
                  size="small"
                  rowKey="id"
                  pagination={false}
                  dataSource={detail.shipments}
                  columns={[
                    { title: '承运商', dataIndex: 'carrier', render: (v: string | null) => v ?? '-' },
                    {
                      title: '快递单号',
                      dataIndex: 'tracking_no',
                      render: (v: string | null) => v ?? '-',
                    },
                    {
                      title: '运费',
                      dataIndex: 'shipping_fee',
                      width: 90,
                      render: (v: number) => `¥${v.toFixed(2)}`,
                    },
                  ]}
                />
              </div>
            )}

            {canManage && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {detail.status === 'pending' && (
                  <>
                    <Input
                      placeholder="拒绝原因（拒绝时必填）"
                      value={rejectReason}
                      onChange={setRejectReason}
                    />
                    <div style={{ display: 'flex', gap: 8 }}>
                      <Button
                        theme="solid"
                        type="primary"
                        loading={approveMutation.isPending}
                        onClick={() => approveMutation.mutate(true)}
                      >
                        批准
                      </Button>
                      <Button
                        type="danger"
                        loading={approveMutation.isPending}
                        onClick={() => approveMutation.mutate(false)}
                      >
                        拒绝
                      </Button>
                    </div>
                  </>
                )}

                {detail.status === 'approved' && (
                  <>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <Input
                        placeholder="承运商"
                        value={shipForm.carrier}
                        onChange={(value) => setShipForm({ ...shipForm, carrier: value })}
                      />
                      <Input
                        placeholder="快递单号"
                        value={shipForm.tracking_no}
                        onChange={(value) => setShipForm({ ...shipForm, tracking_no: value })}
                      />
                      <Input
                        style={{ width: 100 }}
                        placeholder="运费"
                        value={shipForm.shipping_fee}
                        onChange={(value) => setShipForm({ ...shipForm, shipping_fee: value })}
                      />
                    </div>
                    <Button
                      theme="solid"
                      type="primary"
                      loading={shipMutation.isPending}
                      onClick={() => shipMutation.mutate()}
                    >
                      登记寄样
                    </Button>
                  </>
                )}

                {detail.status === 'shipped' && (
                  <Button
                    theme="solid"
                    type="primary"
                    loading={signMutation.isPending}
                    onClick={() => signMutation.mutate()}
                  >
                    登记签收
                  </Button>
                )}

                {['shipped', 'signed'].includes(detail.status) && (
                  <>
                    <TextArea
                      placeholder="客户反馈"
                      value={feedback}
                      onChange={setFeedback}
                      rows={2}
                    />
                    <Button
                      disabled={!feedback.trim()}
                      loading={feedbackMutation.isPending}
                      onClick={() => feedbackMutation.mutate()}
                    >
                      登记反馈
                    </Button>
                  </>
                )}

                {detail.feedback && (
                  <div style={{ background: 'var(--crm-surface-low)', padding: 12, borderRadius: 4 }}>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>客户反馈</div>
                    <div>{detail.feedback}</div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </SideSheet>
    </div>
  )
}
