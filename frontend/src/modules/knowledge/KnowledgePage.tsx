import { useState, type ComponentProps } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Input,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tag,
  TextArea,
  Toast,
} from '@douyinfe/semi-ui'

import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { emptyText } from '../../shared/hooks/emptyText'
import {
  createCustomInquiry,
  customInquiryStatusSummary,
  deleteCustomInquiry,
  listCustomInquiries,
  updateCustomInquiry,
  customInquiryHistory,
  reviseCustomInquiry,
  type CustomInquiryPayload,
  type CustomInquiryRow,
} from '../../shared/api/inquiry'
import { listCustomers } from '../../shared/api/customer'

type TagColor = ComponentProps<typeof Tag>['color']
const STATUS_TONE: Record<string, TagColor> = {
  open: 'orange',
  developing: 'blue',
  converted: 'green',
  archived: 'grey',
}

const STATUS_OPTIONS = [
  { value: 'open', label: '待评估' },
  { value: 'developing', label: '开发中' },
  { value: 'converted', label: '已转商机' },
  { value: 'archived', label: '已归档' },
]

interface InquiryForm {
  title: string
  description: string
  customer_id?: number | null
  quantity: string
  target_price: string
  remark: string
  status?: string
}

const EMPTY_FORM: InquiryForm = {
  title: '',
  description: '',
  customer_id: undefined,
  quantity: '',
  target_price: '',
  remark: '',
}

/** 产品知识库 · 定制询价类（领导模块③）：客户问了但没有标准产品的需求沉淀。 */
export default function KnowledgePage() {
  const queryClient = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [editVisible, setEditVisible] = useState(false)
  const [editing, setEditing] = useState<CustomInquiryRow | null>(null)
  const [form, setForm] = useState<InquiryForm>(EMPTY_FORM)
  // 修订（§3.3）：客户改了要求 → 新增一版并留说明，旧版保留
  const [reviseTarget, setReviseTarget] = useState<CustomInquiryRow | null>(null)
  const [reviseForm, setReviseForm] = useState({
    revision_note: '',
    description: '',
    quantity: '',
    target_price: '',
  })
  const [historyTarget, setHistoryTarget] = useState<CustomInquiryRow | null>(null)
  const historyQuery = useQuery({
    queryKey: ['inquiry-history', historyTarget?.id],
    queryFn: () => customInquiryHistory(historyTarget!.id),
    enabled: Boolean(historyTarget),
  })

  const summaryQuery = useQuery({
    queryKey: ['custom-inquiry-summary'],
    queryFn: () => customInquiryStatusSummary(),
  })
  const query = useQuery({
    queryKey: ['custom-inquiries', { statusFilter, keyword, page }],
    queryFn: () =>
      listCustomInquiries({ status: statusFilter, keyword, page, page_size: 20 }),
  })
  const customersQuery = useQuery({
    queryKey: ['customers-for-inquiry'],
    queryFn: () => listCustomers({ page: 1, page_size: 100 }),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['custom-inquiries'] })
    void queryClient.invalidateQueries({ queryKey: ['custom-inquiry-summary'] })
  }

  const saveMutation = useMutation({
    mutationFn: (payload: CustomInquiryPayload) =>
      editing ? updateCustomInquiry(editing.id, payload) : createCustomInquiry(payload),
    onSuccess: () => {
      Toast.success(editing ? '已保存' : '定制询价已记录')
      setEditVisible(false)
      setEditing(null)
      setForm(EMPTY_FORM)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const reviseMutation = useMutation({
    mutationFn: () =>
      reviseCustomInquiry(reviseTarget!.id, {
        revision_note: reviseForm.revision_note || null,
        description: reviseForm.description || null,
        quantity: reviseForm.quantity ? Number(reviseForm.quantity) : null,
        target_price: reviseForm.target_price ? Number(reviseForm.target_price) : null,
      }),
    onSuccess: (row) => {
      Toast.success(`已生成 v${row.version ?? ''}，旧版保留`)
      setReviseTarget(null)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const openRevise = (row: CustomInquiryRow) => {
    setReviseTarget(row)
    setReviseForm({
      revision_note: '',
      description: row.description ?? '',
      quantity: row.quantity != null ? String(row.quantity) : '',
      target_price: row.target_price != null ? String(row.target_price) : '',
    })
  }

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteCustomInquiry(id),
    onSuccess: () => {
      Toast.success('已删除')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const openCreate = () => {
    setEditing(null)
    setForm(EMPTY_FORM)
    setEditVisible(true)
  }

  const openEdit = (row: CustomInquiryRow) => {
    setEditing(row)
    setForm({
      title: row.title,
      description: row.description ?? '',
      customer_id: row.customer_id ?? undefined,
      quantity: row.quantity != null ? String(row.quantity) : '',
      target_price: row.target_price != null ? String(row.target_price) : '',
      remark: row.remark ?? '',
      status: row.status,
    })
    setEditVisible(true)
  }

  const changeStatus = (row: CustomInquiryRow, status: string) => {
    updateCustomInquiry(row.id, { status })
      .then(refresh)
      .catch((error: Error) => Toast.error(error.message))
  }

  const columns = [
    {
      title: '需求',
      dataIndex: 'title',
      render: (text: string, record: CustomInquiryRow) => (
        <div>
          <div style={{ fontWeight: 600 }}>
            {text}
            {record.version && record.version > 1 && (
              <Tag size="small" style={{ marginLeft: 6 }}>{`v${record.version}`}</Tag>
            )}
          </div>
          {record.revision_note && (
            <div style={{ fontSize: 12, color: 'var(--crm-warning, #d97706)' }}>
              本次修改：{record.revision_note}
            </div>
          )}
          {record.description && (
            <div
              style={{
                fontSize: 12,
                color: 'var(--crm-text-3)',
                maxWidth: 420,
                whiteSpace: 'pre-wrap',
              }}
            >
              {record.description}
            </div>
          )}
        </div>
      ),
    },
    {
      title: '客户',
      dataIndex: 'customer_name',
      width: 180,
      render: (v: string | null) => v ?? '-',
    },
    { title: '数量', dataIndex: 'quantity', width: 90, render: (v: number | null) => v ?? '-' },
    {
      title: '目标价',
      dataIndex: 'target_price',
      width: 100,
      render: (v: number | null) => (v != null ? `¥${v}` : '-'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => (
        <Tag color={STATUS_TONE[v] ?? 'grey'}>{STATUS_OPTIONS.find((o) => o.value === v)?.label ?? v}</Tag>
      ),
    },
    { title: '记录人', dataIndex: 'creator_name', width: 100, render: (v: string | null) => v ?? '-' },
    {
      title: '记录时间',
      dataIndex: 'created_at',
      width: 160,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      width: 150,
      render: (_: unknown, record: CustomInquiryRow) => (
        <span style={{ display: 'inline-flex', gap: 10 }}>
          <a onClick={() => openEdit(record)}>编辑</a>
          <a onClick={() => openRevise(record)}>修订</a>
          {(record.version ?? 1) > 1 && <a onClick={() => setHistoryTarget(record)}>历史</a>}
          {record.status !== 'developing' && record.status !== 'converted' && (
            <a onClick={() => changeStatus(record, 'developing')}>转开发中</a>
          )}
          {record.status !== 'archived' && (
            <a style={{ color: 'var(--crm-text-3)' }} onClick={() => changeStatus(record, 'archived')}>
              归档
            </a>
          )}
          <Popconfirm title="删除这条定制询价？" onConfirm={() => deleteMutation.mutate(record.id)}>
            <a style={{ color: 'var(--crm-error)' }}>删除</a>
          </Popconfirm>
        </span>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="产品知识库 · 定制询价"
        subtitle="客户问了但我们还没有标准产品的需求都沉淀在这里——这是找开发方向的原料"
      />

      <SectionCard>
        <div className="toolbar">
          {summaryQuery.data?.map((item) => (
            <Tag key={item.status} color={STATUS_TONE[item.status] ?? 'grey'} type="light">
              {item.label} {item.count}
            </Tag>
          ))}
          <div style={{ flex: 1 }} />
          <Input
            placeholder="搜索需求标题"
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
            placeholder="状态"
            value={statusFilter}
            onChange={(value) => {
              setStatusFilter(value as string | undefined)
              setPage(1)
            }}
            optionList={STATUS_OPTIONS}
            style={{ width: 130 }}
            showClear
          />
          <Button theme="solid" onClick={openCreate}>
            记录定制询价
          </Button>
        </div>

        <Table<CustomInquiryRow>
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          size="middle"
          empty={emptyText(query, '还没有定制询价记录')}
          pagination={{
            currentPage: page,
            pageSize: 20,
            total: query.data?.total ?? 0,
            onPageChange: (nextPage: number) => setPage(nextPage),
          }}
        />
      </SectionCard>

      <Modal
        title={editing ? '编辑定制询价' : '记录定制询价'}
        visible={editVisible}
        onCancel={() => setEditVisible(false)}
        onOk={() => {
          if (!form.title.trim()) {
            Toast.warning('需求标题必填')
            return
          }
          saveMutation.mutate({
            title: form.title.trim(),
            description: form.description.trim() || null,
            customer_id: form.customer_id ?? null,
            quantity: form.quantity.trim() ? Number(form.quantity) : null,
            target_price: form.target_price.trim() ? Number(form.target_price) : null,
            remark: form.remark.trim() || null,
            status: editing ? (form.status ?? editing.status) : undefined,
          })
        }}
        confirmLoading={saveMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={560}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>需求标题 *</div>
            <Input
              value={form.title}
              onChange={(value) => setForm({ ...form, title: value })}
              placeholder="例如：客户想要带磁吸翻盖的礼品盒"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>需求描述</div>
            <TextArea
              value={form.description}
              onChange={(value) => setForm({ ...form, description: value })}
              rows={3}
              placeholder="材质、工艺、尺寸、使用场景……"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>客户（可选）</div>
            <Select
              value={form.customer_id ?? undefined}
              onChange={(value) => setForm({ ...form, customer_id: (value as number) ?? null })}
              optionList={(customersQuery.data?.items ?? []).map((item) => ({
                value: item.id,
                label: item.name,
              }))}
              filter
              style={{ width: '100%' }}
              showClear
              placeholder="关联客户"
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>需求数量</div>
              <Input value={form.quantity} onChange={(value) => setForm({ ...form, quantity: value })} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户目标价（元/件）</div>
              <Input
                value={form.target_price}
                onChange={(value) => setForm({ ...form, target_price: value })}
              />
            </div>
          </div>
          {editing && (
            <div>
              <div style={{ marginBottom: 4 }}>状态</div>
              <Select
                value={form.status ?? editing.status}
                onChange={(value) => setForm({ ...form, status: value as string })}
                optionList={STATUS_OPTIONS}
                style={{ width: '100%' }}
              />
            </div>
          )}
          <div>
            <div style={{ marginBottom: 4 }}>备注</div>
            <Input value={form.remark} onChange={(value) => setForm({ ...form, remark: value })} />
          </div>
        </div>
      </Modal>

      <Modal
        title={`修订：${reviseTarget?.title ?? ''}`}
        visible={Boolean(reviseTarget)}
        onCancel={() => setReviseTarget(null)}
        onOk={() => reviseMutation.mutate()}
        confirmLoading={reviseMutation.isPending}
        okText="生成新版"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            修订会新增一版（旧版原样保留，可在「历史」里对照），新一版回到「待评估」
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>本次改了什么（建议填，方便回看）</div>
            <Input
              placeholder="如：客户把烫金改成 UV，数量降到 800"
              value={reviseForm.revision_note}
              onChange={(v) => setReviseForm({ ...reviseForm, revision_note: v })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>需求描述</div>
            <TextArea
              rows={3}
              value={reviseForm.description}
              onChange={(v) => setReviseForm({ ...reviseForm, description: v })}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div>
              <div style={{ marginBottom: 4 }}>数量</div>
              <Input
                value={reviseForm.quantity}
                onChange={(v) => setReviseForm({ ...reviseForm, quantity: v })}
              />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>目标价</div>
              <Input
                value={reviseForm.target_price}
                onChange={(v) => setReviseForm({ ...reviseForm, target_price: v })}
              />
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        title={`版本历史：${historyTarget?.title ?? ''}`}
        visible={Boolean(historyTarget)}
        onCancel={() => setHistoryTarget(null)}
        footer={null}
        width={680}
      >
        <Table<CustomInquiryRow>
          columns={[
            { title: '版本', dataIndex: 'version', width: 70, render: (v: number) => `v${v ?? 1}` },
            { title: '需求描述', dataIndex: 'description', render: (v: string | null) => v ?? '-' },
            { title: '数量', dataIndex: 'quantity', width: 80, render: (v: number | null) => v ?? '-' },
            { title: '目标价', dataIndex: 'target_price', width: 90, render: (v: number | null) => v ?? '-' },
            { title: '本版说明', dataIndex: 'revision_note', render: (v: string | null) => v ?? '（原始要求）' },
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 150,
              render: (v: string) => new Date(v).toLocaleString('zh-CN'),
            },
          ]}
          dataSource={historyQuery.data ?? []}
          loading={historyQuery.isLoading}
          rowKey="id"
          pagination={false}
        />
      </Modal>
    </div>
  )
}
