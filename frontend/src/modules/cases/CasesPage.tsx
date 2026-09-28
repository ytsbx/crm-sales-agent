import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Input,
  Modal,
  Select,
  Table,
  Tag,
  Toast,
  TextArea,
} from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import SectionCard from '../../shared/components/SectionCard'
import {
  createCase,
  listCases,
  reviewCase,
  submitCase,
  updateCase,
  type CaseRow,
} from '../../shared/api/cases'
import { listQuotes } from '../../shared/api/quote'
import { listOrders } from '../../shared/api/order'
import { listSamples } from '../../shared/api/sample'
import { listCustomers } from '../../shared/api/customer'

const STAGES = ['understanding', 'quote', 'sample', 'first_order', 'repeat', 'stable']
const STAGE_LABEL: Record<string, string> = {
  understanding: '了解', quote: '报价', sample: '打样',
  first_order: '首单', repeat: '返单', stable: '稳定复购',
}
const STATUS_TONE: Record<string, 'green' | 'grey' | 'orange' | 'red'> = {
  published: 'green', draft: 'grey', pending_review: 'orange', rejected: 'red',
}

const EMPTY_FORM = {
  title: '',
  customer_id: undefined as number | undefined,
  quote_id: undefined as number | undefined,
  order_id: undefined as number | undefined,
  sample_id: undefined as number | undefined,
  customer_label: '',
  industry: '',
  product_line: '',
  stage_reached: undefined as string | undefined,
  problem_tags: '',
  background: '',
  goal: '',
  key_actions: '',
  objection_handling: '',
  process: '',
  result: '',
  lessons: '',
}

export default function CasesPage() {
  const queryClient = useQueryClient()
  const { can, isReviewer } = usePermissions()
  const [filters, setFilters] = useState({ keyword: '', status: undefined as string | undefined })

  const casesQuery = useQuery({
    queryKey: ['cases', filters],
    queryFn: () => listCases(filters),
  })
  const customersQuery = useQuery({
    queryKey: ['case-customers'],
    queryFn: () => listCustomers({ page: 1, page_size: 200 }),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['cases'] })
  }

  const [editVisible, setEditVisible] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [detail, setDetail] = useState<CaseRow | null>(null)

  // 证据单据（§3.7）：选了客户才去拉这个客户的报价/订单/打样
  const evidenceCustomerId = editVisible ? form.customer_id : undefined
  const quotesQuery = useQuery({
    queryKey: ['case-quotes', evidenceCustomerId],
    queryFn: () => listQuotes({ customer_id: evidenceCustomerId, page_size: 100 }),
    enabled: Boolean(evidenceCustomerId),
  })
  const ordersQuery = useQuery({
    queryKey: ['case-orders', evidenceCustomerId],
    queryFn: () => listOrders({ customer_id: evidenceCustomerId, page_size: 100 }),
    enabled: Boolean(evidenceCustomerId),
  })
  const samplesQuery = useQuery({
    queryKey: ['case-samples', evidenceCustomerId],
    queryFn: () => listSamples({ customer_id: evidenceCustomerId, page_size: 100 }),
    enabled: Boolean(evidenceCustomerId),
  })

  const openCreate = () => {
    setEditId(null)
    setForm(EMPTY_FORM)
    setEditVisible(true)
  }
  const openEdit = (row: CaseRow) => {
    setEditId(row.id)
    setForm({
      ...EMPTY_FORM,
      title: row.title,
      customer_id: row.customer_id ?? undefined,
      quote_id: row.quote_id ?? undefined,
      order_id: row.order_id ?? undefined,
      sample_id: row.sample_id ?? undefined,
      customer_label: row.customer_label ?? '',
      industry: row.industry ?? '',
      product_line: row.product_line ?? '',
      stage_reached: row.stage_reached ?? undefined,
      problem_tags: (row.problem_tags ?? []).join('，'),
      background: row.background ?? '',
      goal: row.goal ?? '',
      key_actions: row.key_actions ?? '',
      objection_handling: row.objection_handling ?? '',
      process: row.process ?? '',
      result: row.result ?? '',
      lessons: row.lessons ?? '',
    })
    setEditVisible(true)
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        ...form,
        problem_tags: form.problem_tags
          .split(/[,，]/)
          .map((tag) => tag.trim())
          .filter(Boolean),
      }
      return editId ? updateCase(editId, payload) : createCase(payload)
    },
    onSuccess: () => {
      Toast.success('案例已保存')
      setEditVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const submitMutation = useMutation({
    mutationFn: (id: number) => submitCase(id),
    onSuccess: () => {
      Toast.success('已提交审核')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const reviewMutation = useMutation({
    mutationFn: ({ id, approve }: { id: number; approve: boolean }) =>
      reviewCase(id, { approve, note: approve ? undefined : '请补充关键动作与可复用做法' }),
    onSuccess: (result) => {
      Toast.success(result.status === 'published' ? '已发布' : '已驳回')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <div style={{ padding: 20, maxWidth: 1200, margin: '0 auto' }}>
      <SectionCard>
        <div className="toolbar" style={{ marginBottom: 10 }}>
          <Input
            style={{ width: 220 }}
            placeholder="搜标题 / 做法 / 关键动作"
            value={filters.keyword}
            onChange={(v) => setFilters({ ...filters, keyword: v })}
          />
          <Select
            style={{ width: 130 }}
            placeholder="状态"
            value={filters.status}
            onChange={(v) => setFilters({ ...filters, status: v as string | undefined })}
            optionList={[
              { value: 'published', label: '已发布' },
              { value: 'pending_review', label: '待审核' },
              { value: 'draft', label: '编写中' },
              { value: 'rejected', label: '已驳回' },
            ]}
            showClear
          />
          <div style={{ flex: 1 }} />
          {can('quote:view') && (
            <Button theme="solid" onClick={openCreate}>
              新建案例
            </Button>
          )}
        </div>
        <Table<CaseRow>
          columns={[
            { title: '标题', dataIndex: 'title' },
            { title: '作者', dataIndex: 'author_name', width: 100 },
            { title: '行业', dataIndex: 'industry', width: 100, render: (v: string | null) => v ?? '-' },
            { title: '产品线', dataIndex: 'product_line', width: 100, render: (v: string | null) => v ?? '-' },
            {
              title: '阶段',
              dataIndex: 'stage_reached',
              width: 90,
              render: (v: string | null) => (v ? STAGE_LABEL[v] ?? v : '-'),
            },
            {
              title: '客户',
              width: 150,
              render: (_: unknown, record: CaseRow) => record.customer_name ?? record.customer_label ?? '-',
            },
            {
              title: '状态',
              dataIndex: 'status_label',
              width: 90,
              render: (v: string, record: CaseRow) => (
                <Tag color={STATUS_TONE[record.status] ?? 'grey'}>{v}</Tag>
              ),
            },
            {
              title: '操作',
              width: 200,
              render: (_: unknown, record: CaseRow) => (
                <span style={{ display: 'inline-flex', gap: 10 }}>
                  <a onClick={() => setDetail(record)}>详情</a>
                  {(record.status === 'draft' || record.status === 'rejected') && (
                    <>
                      <a onClick={() => openEdit(record)}>编辑</a>
                      <a onClick={() => submitMutation.mutate(record.id)}>提交审核</a>
                    </>
                  )}
                  {isReviewer && record.status === 'pending_review' && (
                    <>
                      <a style={{ color: 'var(--crm-success)' }} onClick={() => reviewMutation.mutate({ id: record.id, approve: true })}>
                        通过
                      </a>
                      <a style={{ color: 'var(--crm-danger, #d45)' }} onClick={() => reviewMutation.mutate({ id: record.id, approve: false })}>
                        驳回
                      </a>
                    </>
                  )}
                </span>
              ),
            },
          ]}
          dataSource={casesQuery.data ?? []}
          loading={casesQuery.isLoading}
          rowKey="id"
          pagination={false}
          empty="还没有案例——把做得好的单子沉淀下来"
        />
      </SectionCard>

      <Modal
        title={editId ? '编辑案例' : '新建案例'}
        visible={editVisible}
        onCancel={() => setEditVisible(false)}
        onOk={() => saveMutation.mutate()}
        confirmLoading={saveMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={680}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <Input placeholder="标题 *" value={form.title} onChange={(v) => setForm({ ...form, title: v })} />
          <Select
            style={{ width: '100%' }}
            placeholder="关联客户（选真实客户，看案例的人可按权限跳转）"
            filter
            showClear
            value={form.customer_id}
            onChange={(v) =>
              setForm({
                ...form,
                customer_id: v as number | undefined,
                // 换客户时清掉旧证据，避免挂到别的客户的单据上
                quote_id: undefined,
                order_id: undefined,
                sample_id: undefined,
              })
            }
            optionList={(customersQuery.data?.items ?? []).map((c) => ({ value: c.id, label: c.name }))}
          />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <Input placeholder="客户代称（脱敏展示，如：某包装厂）" value={form.customer_label} onChange={(v) => setForm({ ...form, customer_label: v })} />
            <Input placeholder="行业" value={form.industry} onChange={(v) => setForm({ ...form, industry: v })} />
            <Input placeholder="产品线" value={form.product_line} onChange={(v) => setForm({ ...form, product_line: v })} />
            <Select
              placeholder="推进到的阶段"
              value={form.stage_reached}
              onChange={(v) => setForm({ ...form, stage_reached: v as string })}
              optionList={STAGES.map((s) => ({ value: s, label: STAGE_LABEL[s] }))}
              showClear
            />
          </div>
          <Input placeholder="问题标签（逗号分隔，如：价格异议，交期紧）" value={form.problem_tags} onChange={(v) => setForm({ ...form, problem_tags: v })} />
          {form.customer_id && (
            <div
              style={{
                display: 'grid',
                gap: 8,
                padding: 10,
                background: 'var(--crm-surface-low)',
                borderRadius: 8,
              }}
            >
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                证据单据（§3.7：从已有时间线和单据里挑证据，看案例的人可跳转查看）
              </div>
              <Select
                style={{ width: '100%' }}
                placeholder="关联报价单（可空）"
                showClear
                value={form.quote_id}
                onChange={(v) => setForm({ ...form, quote_id: v as number | undefined })}
                optionList={(quotesQuery.data?.items ?? []).map((q) => ({
                  value: q.id,
                  label: `${q.quote_no} · ${q.status_label ?? q.status}`,
                }))}
              />
              <Select
                style={{ width: '100%' }}
                placeholder="关联订单（可空）"
                showClear
                value={form.order_id}
                onChange={(v) => setForm({ ...form, order_id: v as number | undefined })}
                optionList={(ordersQuery.data?.items ?? []).map((o) => ({
                  value: o.id,
                  label: `${o.order_no} · ${o.status_label ?? o.status} · ¥${o.total_amount ?? 0}`,
                }))}
              />
              <Select
                style={{ width: '100%' }}
                placeholder="关联打样单（可空）"
                showClear
                value={form.sample_id}
                onChange={(v) => setForm({ ...form, sample_id: v as number | undefined })}
                optionList={(samplesQuery.data?.items ?? []).map((sp) => ({
                  value: sp.id,
                  label: `打样单 #${sp.id} · ${sp.status_label ?? sp.status}`,
                }))}
              />
            </div>
          )}
          <TextArea rows={2} placeholder="客户背景" value={form.background} onChange={(v) => setForm({ ...form, background: v })} />
          <TextArea rows={2} placeholder="客户目标" value={form.goal} onChange={(v) => setForm({ ...form, goal: v })} />
          <TextArea rows={2} placeholder="关键动作 *" value={form.key_actions} onChange={(v) => setForm({ ...form, key_actions: v })} />
          <TextArea rows={2} placeholder="异议处理" value={form.objection_handling} onChange={(v) => setForm({ ...form, objection_handling: v })} />
          <TextArea rows={2} placeholder="报价/打样经过" value={form.process} onChange={(v) => setForm({ ...form, process: v })} />
          <TextArea rows={2} placeholder="结果" value={form.result} onChange={(v) => setForm({ ...form, result: v })} />
          <TextArea rows={2} placeholder="可复用做法 *" value={form.lessons} onChange={(v) => setForm({ ...form, lessons: v })} />
        </div>
      </Modal>

      <Modal
        title={detail?.title ?? ''}
        visible={Boolean(detail)}
        onCancel={() => setDetail(null)}
        footer={null}
        width={680}
      >
        {detail && (
          <div style={{ display: 'grid', gap: 10, fontSize: 14 }}>
            <div>
              {detail.customer_name ?? detail.customer_label ?? '客户未填'}
              {detail.industry ? ` · ${detail.industry}` : ''}
              {detail.product_line ? ` · ${detail.product_line}` : ''}
              {detail.stage_reached ? ` · ${STAGE_LABEL[detail.stage_reached] ?? detail.stage_reached}` : ''}
            </div>
            {([
              ['客户背景', detail.background],
              ['客户目标', detail.goal],
              ['关键动作', detail.key_actions],
              ['异议处理', detail.objection_handling],
              ['报价/打样经过', detail.process],
              ['结果', detail.result],
              ['可复用做法', detail.lessons],
            ] as const)
              .filter(([, text]) => text)
              .map(([label, text]) => (
                <div key={label}>
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>{label}</div>
                  <div style={{ whiteSpace: 'pre-wrap', color: 'var(--crm-text-2)' }}>{text}</div>
                </div>
              ))}
            {(detail.quote_id || detail.order_id || detail.sample_id) && (
              <div style={{ display: 'flex', gap: 12, fontSize: 13, flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--crm-text-3)' }}>证据单据：</span>
                {detail.quote_id && <Link to={`/quotes/${detail.quote_id}`}>报价单 #{detail.quote_id}</Link>}
                {detail.order_id && <Link to={`/orders/${detail.order_id}`}>订单 #{detail.order_id}</Link>}
                {detail.sample_id && <span>打样单 #{detail.sample_id}</span>}
              </div>
            )}
            {(detail.problem_tags ?? []).length > 0 && (
              <div>
                {(detail.problem_tags ?? []).map((tag) => (
                  <Tag key={tag}>{tag}</Tag>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
