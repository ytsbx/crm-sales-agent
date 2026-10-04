import { useRef, useState, type ComponentProps } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Input,
  InputNumber,
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
  createQuoteFromInquiry,
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
import {
  listInquiryApprovals,
  resolveOaInstance,
  startInquiryApproval,
  type OaApprovalInstance,
} from '../../shared/api/dingtalk'
import { reportOperationTiming } from '../../shared/api/analytics'

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
  const navigate = useNavigate()
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
  // 钉钉审批记录：查看历次提交；"结果不明"的单子在这里转人工处理
  const [approvalTarget, setApprovalTarget] = useState<CustomInquiryRow | null>(null)
  const [adoptTarget, setAdoptTarget] = useState<OaApprovalInstance | null>(null)
  const [adoptInstanceId, setAdoptInstanceId] = useState('')
  // 转报价（§3.1/场景09）：定制件投产前没有 SKU，报价中心选不到它，
  // 这里给一条"填两个数就成单"的出口
  const [quoteTarget, setQuoteTarget] = useState<CustomInquiryRow | null>(null)
  /** 转报价的计时起点（场景18 操作耗时埋点） */
  const quoteStartedAt = useRef<number | null>(null)
  const [quoteForm, setQuoteForm] = useState({
    unit_cost: null as number | null,
    quoted_price: null as number | null,
    quantity: null as number | null,
  })
  const historyQuery = useQuery({
    queryKey: ['inquiry-history', historyTarget?.id],
    queryFn: () => customInquiryHistory(historyTarget!.id),
    enabled: Boolean(historyTarget),
  })
  const approvalsQuery = useQuery({
    queryKey: ['inquiry-approvals', approvalTarget?.id],
    queryFn: () => listInquiryApprovals(approvalTarget!.id),
    enabled: Boolean(approvalTarget),
  })
  const resolveMutation = useMutation({
    mutationFn: ({
      oaId,
      action,
      instanceId,
    }: {
      oaId: number
      action: 'adopt' | 'resend' | 'abandon'
      instanceId?: string
    }) =>
      resolveOaInstance(oaId, {
        action,
        instance_id: instanceId,
        note: action === 'abandon' ? '人工核对确认钉钉未建单，作废本轮' : undefined,
      }),
    onSuccess: (row) => {
      Toast.success(`已处理：${row.status_label}`)
      setAdoptTarget(null)
      setAdoptInstanceId('')
      void queryClient.invalidateQueries({ queryKey: ['inquiry-approvals'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const quoteMutation = useMutation({
    mutationFn: () =>
      createQuoteFromInquiry(quoteTarget!.id, {
        unit_cost: quoteForm.unit_cost!,
        quoted_price: quoteForm.quoted_price!,
        quantity: quoteForm.quantity ?? undefined,
      }),
    onSuccess: (data) => {
      // 计时上报（场景18）：失败也不该挡住业务——埋点只是量尺，不能成为新故障点
      const startedAt = quoteStartedAt.current
      quoteStartedAt.current = null
      if (startedAt) {
        const duration = Date.now() - startedAt
        // 超过 8 小时当作"中途离开"，不报（服务端也会挡，这里先拦一道避免噪音）
        if (duration > 0 && duration <= 8 * 3600 * 1000) {
          void reportOperationTiming({
            operation: 'quote_from_inquiry',
            duration_ms: duration,
            business_type: 'quote',
            business_id: data.quote_id,
            // 这一单用户实际手输了几个字段（成本/报价/数量）
            typed_fields: [quoteForm.unit_cost, quoteForm.quoted_price, quoteForm.quantity]
              .filter((v) => v !== null && v !== undefined).length,
          }).catch(() => undefined)
        }
      }
      setQuoteTarget(null)
      refresh()
      Toast.success(
        data.approval_required
          ? `报价 ${data.quote_no} 已生成（低于保护价 ${data.minimum_price}，需审批）`
          : `报价 ${data.quote_no} 已生成`,
      )
      navigate(`/quotes/${data.quote_id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
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
            {/* 需求编号（场景09）：报价/打样明细引用它溯源，比自增 id 可读 */}
            {record.inquiry_no && (
              <span
                style={{
                  fontFamily: 'monospace',
                  fontSize: 12,
                  color: 'var(--crm-primary)',
                  marginRight: 6,
                }}
              >
                {record.inquiry_no}
              </span>
            )}
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
      width: 250,
      render: (_: unknown, record: CustomInquiryRow) => (
        <span style={{ display: 'inline-flex', gap: 10 }}>
          <a onClick={() => openEdit(record)}>编辑</a>
          <a onClick={() => openRevise(record)}>修订</a>
          {/* 场景11：从需求发起钉钉询价审批。
              注意这不是"随便试试"——真发起会通知审批人（真人）。
              后端有推送总闸，关着时只会记一条"未发起"，不会打扰任何人。 */}
          <a
            onClick={async () => {
              try {
                const row = await startInquiryApproval(record.id)
                if (row.status === 'skipped') {
                  Toast.info(`未发起：${row.error ?? '推送已关闭'}`)
                } else {
                  Toast.success(`钉钉审批：${row.status_label}`)
                }
              } catch (error) {
                Toast.error((error as Error).message)
              }
            }}
          >
            发起审批
          </a>
          {/* 历次审批 + "结果不明"的人工处理入口（认领/重发/作废） */}
          <a onClick={() => setApprovalTarget(record)}>审批记录</a>
          {/* 定制件没有 SKU，报价中心选不到它——这里直接转报价（场景09） */}
          <a
            onClick={() => {
              setQuoteForm({
                unit_cost: null,
                quoted_price: null,
                quantity: record.quantity ?? null,
              })
              // 耗时埋点（场景18）：起点在这里——用户点开"转报价"那一刻。
              // 服务端只看得到单据落库时间，那是流程跨度，不是他真正花的工夫。
              quoteStartedAt.current = Date.now()
              setQuoteTarget(record)
            }}
          >
            转报价
          </a>
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
            {
              title: '状态',
              dataIndex: 'version_state_label',
              width: 110,
              render: (v: string | null, row: CustomInquiryRow) => (
                <Tag color={row.is_superseded ? 'grey' : 'green'}>{v ?? '当前版'}</Tag>
              ),
            },
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

      <Modal
        title={`钉钉审批记录：${approvalTarget?.title ?? ''}`}
        visible={Boolean(approvalTarget)}
        onCancel={() => setApprovalTarget(null)}
        footer={null}
        width={720}
      >
        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 10 }}>
          发起过程中断时钉钉那边可能已经建了单，而钉钉接口没有"只许建一次"的开关，
          所以「不会自动重发」。请先到钉钉确认，再选「认领 / 重发 / 作废」。
        </div>
        <Table<OaApprovalInstance>
          columns={[
            { title: '轮次', dataIndex: 'submit_round', width: 60, render: (v: number) => v ?? 1 },
            {
              title: '状态',
              dataIndex: 'status_label',
              width: 130,
              render: (v: string, row: OaApprovalInstance) => (
                <Tag color={row.status === 'needs_review' ? 'orange' : 'grey'}>{v}</Tag>
              ),
            },
            {
              title: '钉钉单号 / 说明',
              dataIndex: 'instance_id',
              render: (v: string | null, row: OaApprovalInstance) => (
                <div style={{ fontSize: 12 }}>
                  <div>{v ?? '-'}</div>
                  {row.error && <div style={{ color: 'var(--crm-text-3)' }}>{row.error}</div>}
                </div>
              ),
            },
            {
              title: '操作',
              width: 210,
              render: (_: unknown, row: OaApprovalInstance) =>
                row.status !== 'needs_review' ? (
                  <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>—</span>
                ) : (
                  <span style={{ display: 'inline-flex', gap: 10 }}>
                    <a
                      onClick={() => {
                        setAdoptTarget(row)
                        setAdoptInstanceId('')
                      }}
                    >
                      认领
                    </a>
                    <Popconfirm
                      title="确认钉钉那边没有这张单？"
                      content="重发会再向钉钉发起一次；若其实已经建过，就会多出一张审批单。"
                      onConfirm={() => resolveMutation.mutate({ oaId: row.id, action: 'resend' })}
                    >
                      <a>重发</a>
                    </Popconfirm>
                    <Popconfirm
                      title="作废本轮记录？"
                      content="仅在本系统里作废，不会动钉钉那边。"
                      onConfirm={() => resolveMutation.mutate({ oaId: row.id, action: 'abandon' })}
                    >
                      <a>作废</a>
                    </Popconfirm>
                  </span>
                ),
            },
          ]}
          dataSource={approvalsQuery.data ?? []}
          loading={approvalsQuery.isLoading}
          rowKey="id"
          pagination={false}
        />
      </Modal>

      <Modal
        title="认领钉钉审批单"
        visible={Boolean(adoptTarget)}
        onCancel={() => setAdoptTarget(null)}
        onOk={() => {
          if (!adoptTarget || !adoptInstanceId.trim()) {
            Toast.error('请填写钉钉那边已有的审批单号')
            return
          }
          resolveMutation.mutate({
            oaId: adoptTarget.id,
            action: 'adopt',
            instanceId: adoptInstanceId.trim(),
          })
        }}
        confirmLoading={resolveMutation.isPending}
        okText="认领"
        width={460}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            到钉钉里找到这张审批单，把它的单号填进来——系统会接着它回收审批结果。
          </div>
          <Input
            placeholder="钉钉审批单号（instanceId）"
            value={adoptInstanceId}
            onChange={(value) => setAdoptInstanceId(value)}
          />
        </div>
      </Modal>

      {/* 转报价（§3.1/场景09）：只填两个数——核价成本与报价。
          成本必填不是啰嗦：按 0 记成本会算出 100% 毛利、低价审批永不触发 */}
      <Modal
        title={`转报价：${quoteTarget?.title ?? ''}`}
        visible={Boolean(quoteTarget)}
        onCancel={() => setQuoteTarget(null)}
        onOk={() => {
          if (quoteForm.unit_cost == null || quoteForm.quoted_price == null) {
            Toast.warning('请填写核价成本与报价')
            return
          }
          quoteMutation.mutate()
        }}
        confirmLoading={quoteMutation.isPending}
        okText="生成报价"
        width={520}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            {quoteTarget?.customer_name
              ? `客户：${quoteTarget.customer_name}`
              : '这条需求还没关联客户——先去「编辑」补上客户再转报价。'}
            {quoteTarget?.inquiry_no ? ` · 需求编号 ${quoteTarget.inquiry_no}` : ''}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>核价成本（元/件，不含运费）*</div>
            <InputNumber
              style={{ width: '100%' }}
              min={0}
              value={quoteForm.unit_cost ?? undefined}
              onChange={(value) => setQuoteForm({ ...quoteForm, unit_cost: (value as number) ?? null })}
            />
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 2 }}>
              定制件没有系统成本可查，成本由核价环节给出。系统用它算毛利，
              并按「最低毛利率」推出保护价——低于保护价会转审批。
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>报价（元/件）*</div>
            <InputNumber
              style={{ width: '100%' }}
              min={0}
              value={quoteForm.quoted_price ?? undefined}
              onChange={(value) =>
                setQuoteForm({ ...quoteForm, quoted_price: (value as number) ?? null })
              }
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>数量</div>
            <InputNumber
              style={{ width: '100%' }}
              min={0}
              value={quoteForm.quantity ?? undefined}
              onChange={(value) => setQuoteForm({ ...quoteForm, quantity: (value as number) ?? null })}
            />
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 2 }}>
              不填就用需求上记的数量
            </div>
          </div>
        </div>
      </Modal>
    </div>
  )
}
