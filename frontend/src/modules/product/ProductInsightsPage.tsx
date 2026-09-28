import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Select, Table, Tag, Toast, TextArea } from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import SectionCard from '../../shared/components/SectionCard'
import {
  convertProductInsight,
  createProductInsight,
  listProductInsights,
  reviewProductInsight,
  submitProductInsight,
  updateProductInsight,
  type ProductInsightRow,
} from '../../shared/api/insight'

const SOURCES = ['展会', '1688/阿里', '客户反馈', '竞品调研', '社媒', '供应商推荐', '其他']
const STATUS_TONE: Record<string, 'grey' | 'orange' | 'green' | 'red' | 'blue'> = {
  draft: 'grey',
  under_review: 'orange',
  approved: 'green',
  rejected: 'red',
  converted: 'blue',
}

const EMPTY_FORM = {
  title: '',
  source: undefined as string | undefined,
  target_customer: '',
  direction: '',
  selling_points: '',
  price_assumption: '',
  conclusion: '',
}

/** 新品洞察（§3.3 第三类：运营日常选品 → 评审 → 转定制询价线索）。 */
export default function ProductInsightsPage() {
  const queryClient = useQueryClient()
  const { can, isReviewer } = usePermissions()
  const canManage = can('product:manage')
  const [status, setStatus] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)

  const query = useQuery({
    queryKey: ['product-insights', { status, keyword, page }],
    queryFn: () => listProductInsights({ status, keyword, page, page_size: 20 }),
  })

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['product-insights'] })

  const [editVisible, setEditVisible] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)

  const openCreate = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setEditVisible(true)
  }
  const openEdit = (row: ProductInsightRow) => {
    setEditingId(row.id)
    setForm({
      title: row.title,
      source: row.source ?? undefined,
      target_customer: row.target_customer ?? '',
      direction: row.direction ?? '',
      selling_points: row.selling_points ?? '',
      price_assumption: row.price_assumption != null ? String(row.price_assumption) : '',
      conclusion: row.conclusion ?? '',
    })
    setEditVisible(true)
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        ...form,
        price_assumption: form.price_assumption ? Number(form.price_assumption) : null,
      }
      return editingId
        ? updateProductInsight(editingId, payload)
        : createProductInsight(payload)
    },
    onSuccess: () => {
      Toast.success('已保存')
      setEditVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const submitMutation = useMutation({
    mutationFn: (id: number) => submitProductInsight(id),
    onSuccess: () => {
      Toast.success('已提交评审')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const reviewMutation = useMutation({
    mutationFn: ({ id, approve }: { id: number; approve: boolean }) =>
      reviewProductInsight(id, { approve, note: approve ? undefined : '方向不明确，暂缓' }),
    onSuccess: () => {
      Toast.success('评审完成')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const convertMutation = useMutation({
    mutationFn: (id: number) => convertProductInsight(id),
    onSuccess: (data) => {
      Toast.success(`已转为定制询价线索 #${data.inquiry_id}`)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <div className="page-container">
      <SectionCard>
        <div className="toolbar" style={{ marginBottom: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            运营选品沉淀：市场来源 → 产品方向 → 评审通过 → 转定制询价线索。
            价格只是内部假设，未经确认不会成为正式指导价
          </span>
          <div style={{ flex: 1 }} />
          <Input
            style={{ width: 200 }}
            placeholder="搜标题"
            value={keyword}
            onChange={setKeyword}
            onEnterPress={() => setPage(1)}
          />
          <Select
            style={{ width: 130 }}
            placeholder="状态"
            showClear
            value={status}
            onChange={(v) => {
              setStatus(v as string | undefined)
              setPage(1)
            }}
            optionList={[
              { value: 'draft', label: '记录中' },
              { value: 'under_review', label: '待评审' },
              { value: 'approved', label: '已通过' },
              { value: 'rejected', label: '已否决' },
              { value: 'converted', label: '已转线索' },
            ]}
          />
          {canManage && (
            <Button theme="solid" onClick={openCreate}>
              记录新品洞察
            </Button>
          )}
        </div>
        <Table<ProductInsightRow>
          columns={[
            {
              title: '产品方向',
              dataIndex: 'title',
              render: (text: string, record: ProductInsightRow) => (
                <div>
                  <div style={{ fontWeight: 600 }}>{text}</div>
                  {record.selling_points && (
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      假设卖点：{record.selling_points}
                    </div>
                  )}
                </div>
              ),
            },
            { title: '来源', dataIndex: 'source', width: 110, render: (v: string | null) => v ?? '-' },
            {
              title: '目标客户',
              dataIndex: 'target_customer',
              width: 130,
              render: (v: string | null) => v ?? '-',
            },
            {
              title: '价格假设',
              dataIndex: 'price_assumption',
              width: 100,
              render: (v: number | null) =>
                v == null ? '-' : <span style={{ color: 'var(--crm-text-3)' }}>¥{v}（假设）</span>,
            },
            { title: '负责人', dataIndex: 'owner_name', width: 90, render: (v: string | null) => v ?? '-' },
            {
              title: '状态',
              dataIndex: 'status_label',
              width: 100,
              render: (v: string, record: ProductInsightRow) => (
                <Tag color={STATUS_TONE[record.status] ?? 'grey'}>{v}</Tag>
              ),
            },
            {
              title: '操作',
              width: 190,
              render: (_: unknown, record: ProductInsightRow) => (
                <span style={{ display: 'inline-flex', gap: 10 }}>
                  {canManage && record.status !== 'converted' && (
                    <a onClick={() => openEdit(record)}>编辑</a>
                  )}
                  {canManage && (record.status === 'draft' || record.status === 'rejected') && (
                    <a onClick={() => submitMutation.mutate(record.id)}>提交评审</a>
                  )}
                  {isReviewer && record.status === 'under_review' && (
                    <>
                      <a style={{ color: 'var(--crm-success)' }} onClick={() => reviewMutation.mutate({ id: record.id, approve: true })}>
                        通过
                      </a>
                      <a style={{ color: 'var(--crm-error)' }} onClick={() => reviewMutation.mutate({ id: record.id, approve: false })}>
                        否决
                      </a>
                    </>
                  )}
                  {canManage && record.status === 'approved' && (
                    <a onClick={() => convertMutation.mutate(record.id)}>转询价线索</a>
                  )}
                  {record.converted_inquiry_id && (
                    <span style={{ color: 'var(--crm-text-3)' }}>线索 #{record.converted_inquiry_id}</span>
                  )}
                </span>
              ),
            },
          ]}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          pagination={{
            currentPage: page,
            pageSize: 20,
            total: query.data?.total ?? 0,
            onPageChange: setPage,
          }}
          empty="还没有新品洞察——运营看到好方向就记进来"
        />
      </SectionCard>

      <Modal
        title={editingId ? '编辑新品洞察' : '记录新品洞察'}
        visible={editVisible}
        onCancel={() => setEditVisible(false)}
        onOk={() => saveMutation.mutate()}
        confirmLoading={saveMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={620}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <Input placeholder="产品方向标题 *" value={form.title} onChange={(v) => setForm({ ...form, title: v })} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
            <Select
              placeholder="市场来源"
              showClear
              value={form.source}
              onChange={(v) => setForm({ ...form, source: v as string })}
              optionList={SOURCES.map((s) => ({ value: s, label: s }))}
            />
            <Input placeholder="目标客户" value={form.target_customer} onChange={(v) => setForm({ ...form, target_customer: v })} />
            <Input
              placeholder="价格假设（仅内部参考）"
              value={form.price_assumption}
              onChange={(v) => setForm({ ...form, price_assumption: v })}
            />
          </div>
          <TextArea rows={2} placeholder="产品方向（做给谁、解决什么）" value={form.direction} onChange={(v) => setForm({ ...form, direction: v })} />
          <TextArea rows={2} placeholder="假设卖点" value={form.selling_points} onChange={(v) => setForm({ ...form, selling_points: v })} />
          <TextArea rows={2} placeholder="评估结论（能否开发、卡在哪）" value={form.conclusion} onChange={(v) => setForm({ ...form, conclusion: v })} />
        </div>
      </Modal>
    </div>
  )
}
