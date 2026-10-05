import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Popconfirm, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import {
  attachCustomerTags,
  claimCustomer,
  createContact,
  deduplicateContacts,
  deduplicateCustomers,
  detachCustomerTag,
  getCustomer,
  listContacts,
  listTags,
  mergeCustomers,
  openDuplicateCases,
  releaseCustomerToPool,
  transferCustomer,
  updateCustomer,
  type ContactDuplicateMatch,
  type CustomerPayload,
} from '../../shared/api/customer'
import { listUsers } from '../../shared/api/system'
import { getCustomerTimeline, listFollowups, type FollowUp } from '../../shared/api/followup'
import { listOpportunities, type Opportunity } from '../../shared/api/opportunity'
import { listOrders, type Order } from '../../shared/api/order'
import { listQuotes, type Quote } from '../../shared/api/quote'
import { usePermissions } from '../../shared/hooks/permissions'
import type { Contact } from '../../shared/types'
import DetailHeader from '../../shared/components/DetailHeader'
import SectionCard from '../../shared/components/SectionCard'
import AgentInsight from '../../shared/components/AgentInsight'
import { agentCustomerSummary, agentFollowupSuggestion, type AnalysisEnvelope } from '../../shared/api/agent'
import FollowUpModal from '../common/FollowUpModal'
import FollowUpAttachmentsButton from '../common/FollowUpAttachmentsButton'
import Timeline from '../common/Timeline'
import AttachmentPanel from '../common/AttachmentPanel'
import DecisionMakerCard from '../common/DecisionMakerCard'

const TABS = [
  { tab: '概览', itemKey: 'overview' },
  { tab: '联系人', itemKey: 'contacts' },
  { tab: '商机', itemKey: 'opportunities' },
  { tab: '跟进', itemKey: 'followups' },
  { tab: '报价', itemKey: 'quotes' },
  { tab: '订单', itemKey: 'orders' },
  { tab: '文件', itemKey: 'files' },
  { tab: '日志', itemKey: 'logs' },
]

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ color: 'var(--crm-text-3)', fontSize: 13, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 14 }}>{value}</div>
    </div>
  )
}

export default function CustomerDetailPage() {
  const params = useParams()
  const customerId = Number(params.id)
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [activeKey, setActiveKey] = useState(searchParams.get('tab') ?? 'overview')
  const [contactModal, setContactModal] = useState(false)
  const [contactForm, setContactForm] = useState<Partial<Contact>>({ name: '', is_primary: false })
  // 新建联系人时的查重结果；null = 还没查过
  const [contactDupes, setContactDupes] = useState<ContactDuplicateMatch[] | null>(null)
  const [editModal, setEditModal] = useState(false)
  const [editForm, setEditForm] = useState<CustomerPayload>({ name: '' })
  const [transferModal, setTransferModal] = useState(false)
  const [transferTo, setTransferTo] = useState<number | null>(null)
  const [followupVisible, setFollowupVisible] = useState(false)
  const [duplicateCaseCount, setDuplicateCaseCount] = useState(0)

  // 标签与合并（03-API §7）
  const [tagPickerOpen, setTagPickerOpen] = useState(false)
  const [pendingTagIds, setPendingTagIds] = useState<number[]>([])
  const [mergeModal, setMergeModal] = useState(false)
  const [mergeTarget, setMergeTarget] = useState<number | null>(null)
  const [mergeKeyword, setMergeKeyword] = useState('')
  const [mergeReason, setMergeReason] = useState('')

  const tagsQuery = useQuery({ queryKey: ['tags'], queryFn: () => listTags(false) })

  // 合并目标候选：按关键词搜其他客户；顺带展示查重打分，优先合并疑似重复的
  const mergeCandidatesQuery = useQuery({
    queryKey: ['merge-candidates', mergeKeyword],
    queryFn: () =>
      mergeKeyword.trim()
        ? deduplicateCustomers({ name: mergeKeyword.trim() })
        : Promise.resolve({ matches: [], count: 0 }),
    enabled: mergeModal && mergeKeyword.trim().length > 0,
  })

  const customerQuery = useQuery({
    queryKey: ['customer', customerId],
    queryFn: () => getCustomer(customerId),
    enabled: Number.isFinite(customerId),
  })

  const contactsQuery = useQuery({
    queryKey: ['contacts', customerId],
    queryFn: () => listContacts(customerId),
    enabled: Number.isFinite(customerId),
  })

  const contactMutation = useMutation({
    mutationFn: (payload: Partial<Contact>) => createContact(customerId, payload),
    onSuccess: () => {
      Toast.success('联系人已添加')
      setContactModal(false)
      setContactForm({ name: '', is_primary: false })
      setContactDupes(null)
      void queryClient.invalidateQueries({ queryKey: ['contacts', customerId] })
      void queryClient.invalidateQueries({ queryKey: ['customer', customerId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 联系人查重（API §8 POST /contacts/deduplicate）：按当前弹窗里已填的姓名/手机/邮箱查
  const contactDupMutation = useMutation({
    mutationFn: () =>
      deduplicateContacts({
        name: contactForm.name ?? undefined,
        mobile: contactForm.mobile ?? undefined,
        email: contactForm.email ?? undefined,
      }),
    onSuccess: (data) => setContactDupes(data.matches),
    onError: (error: Error) => Toast.error(error.message),
  })

  // AI 分析（API §37 专用接口，需 agent:use）：概览页可一键运行
  const [aiEnvelope, setAiEnvelope] = useState<AnalysisEnvelope | null>(null)
  const aiMutation = useMutation({
    mutationFn: (kind: 'summary' | 'followup') =>
      kind === 'summary'
        ? agentCustomerSummary({ customer_id: customerId })
        : agentFollowupSuggestion({ customer_id: customerId }),
    onSuccess: (data) => setAiEnvelope(data),
    onError: (error: Error) => Toast.error(error.message),
  })

  const invalidateCustomer = () => {
    void queryClient.invalidateQueries({ queryKey: ['customer', customerId] })
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
    void queryClient.invalidateQueries({ queryKey: ['workbench'] })
  }

  const editMutation = useMutation({
    mutationFn: (payload: CustomerPayload) => updateCustomer(customerId, payload),
    onSuccess: () => {
      Toast.success('客户资料已保存')
      setEditModal(false)
      invalidateCustomer()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const transferMutation = useMutation({
    mutationFn: (ownerId: number | null) => transferCustomer(customerId, { owner_id: ownerId }),
    onSuccess: () => {
      Toast.success('负责人已变更')
      setTransferModal(false)
      invalidateCustomer()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const duplicateCaseMutation = useMutation({
    mutationFn: () => openDuplicateCases(customerId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['duplicate-cases'] })
      if (result.opened > 0) {
        setDuplicateCaseCount(result.opened)
      } else {
        Toast.info('未发现疑似重复客户')
      }
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const poolMutation = useMutation({
    mutationFn: (action: 'release' | 'claim') =>
      action === 'release' ? releaseCustomerToPool(customerId) : claimCustomer(customerId),
    onSuccess: (_data, action) => {
      Toast.success(action === 'release' ? '已放入公海' : '领取成功')
      invalidateCustomer()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const attachTagsMutation = useMutation({
    mutationFn: (tagIds: number[]) => attachCustomerTags(customerId, tagIds),
    onSuccess: (result) => {
      Toast.success(result.added > 0 ? `已添加 ${result.added} 个标签` : '标签已存在，未重复添加')
      setTagPickerOpen(false)
      setPendingTagIds([])
      invalidateCustomer()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const detachTagMutation = useMutation({
    mutationFn: (tagId: number) => detachCustomerTag(customerId, tagId),
    onSuccess: () => {
      Toast.success('标签已移除')
      invalidateCustomer()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const mergeMutation = useMutation({
    mutationFn: () =>
      mergeCustomers({
        source_customer_id: customerId,
        target_customer_id: mergeTarget!,
        reason: mergeReason.trim() || undefined,
      }),
    onSuccess: (result) => {
      const movedText = Object.entries(result.moved)
        .filter(([, count]) => count > 0)
        .map(([label, count]) => `${label} ${count}`)
        .join('、')
      Toast.success(`已合并，迁移：${movedText || '无关联数据'}`)
      setMergeModal(false)
      // 来源客户已被软删，跳去目标客户继续操作
      navigate(`/customers/${result.target_customer_id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const usersQuery = useQuery({
    queryKey: ['assignable-users'],
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
    enabled: transferModal && can('customer:assign'),
  })

  const opportunitiesQuery = useQuery({
    queryKey: ['customer-opportunities', customerId],
    queryFn: () => listOpportunities({ customer_id: customerId, page_size: 50 }),
    enabled: Number.isFinite(customerId),
  })
  const followupsQuery = useQuery({
    queryKey: ['followups', { customer_id: customerId }],
    queryFn: () => listFollowups({ customer_id: customerId, page_size: 100 }),
    enabled: Number.isFinite(customerId) && activeKey === 'followups',
  })
  const timelineQuery = useQuery({
    queryKey: ['timeline', 'customer', customerId],
    queryFn: () => getCustomerTimeline(customerId),
    enabled: Number.isFinite(customerId) && activeKey === 'logs',
  })

  const quotesQuery = useQuery({
    queryKey: ['customer-quotes', customerId],
    queryFn: () => listQuotes({ customer_id: customerId, page_size: 50 }),
    enabled: Number.isFinite(customerId) && activeKey === 'quotes',
  })
  const ordersQuery = useQuery({
    queryKey: ['customer-orders', customerId],
    queryFn: () => listOrders({ customer_id: customerId, page_size: 50 }),
    enabled: Number.isFinite(customerId) && activeKey === 'orders',
  })

  const customer = customerQuery.data

  if (customerQuery.isLoading) {
    return <div className="page-container">加载中…</div>
  }
  if (!customer) {
    return <div className="page-container">客户不存在或无权查看</div>
  }

  const contactColumns = [
    { title: '姓名', dataIndex: 'name', width: 120 },
    { title: '职位', dataIndex: 'title', width: 140, render: (v: string | null) => v ?? '-' },
    { title: '手机', dataIndex: 'mobile', width: 150, render: (v: string | null) => v ?? '-' },
    { title: '邮箱', dataIndex: 'email', render: (v: string | null) => v ?? '-' },
    {
      title: '主要联系人',
      dataIndex: 'is_primary',
      width: 110,
      render: (v: boolean) => (v ? <Tag color="green">是</Tag> : '-'),
    },
  ]

  return (
    <div className="page-container">
      {/* 头部按设计稿排：标题 + 状态标签一行，关键信息行在标题下方左对齐，按钮贴右 */}
      <DetailHeader
        title={customer.name}
        tags={
          <>
            {customer.level && (
              <Tag color={customer.level === 'A' ? 'green' : customer.level === 'B' ? 'blue' : 'grey'}>
                {customer.level} 级客户
              </Tag>
            )}
            <Tag color={customer.pool_status === 'public' ? 'orange' : 'blue'}>
              {customer.pool_status === 'public' ? '公海' : '私海'}
            </Tag>
            {customer.country && <Tag>{customer.country}</Tag>}
          </>
        }
        meta={
          <>
            <span>负责人：{customer.owner_name ?? '未分配'}</span>
            <span>地区：{customer.region ?? '-'}</span>
            <span>来源：{customer.source ?? '-'}</span>
            <span>联系人：{customer.contact_count}</span>
          </>
        }
        extra={
          <>
            {can('customer:update') && (
              <Button
                onClick={() => {
                  setEditForm({
                    name: customer.name,
                    short_name: customer.short_name ?? '',
                    region: customer.region ?? '',
                    address: customer.address ?? '',
                    source: customer.source ?? '',
                    level: customer.level ?? '',
                    remark: customer.remark ?? '',
                  })
                  setEditModal(true)
                }}
              >
                编辑客户
              </Button>
            )}
            {can('followup:create') && (
              <Button onClick={() => setFollowupVisible(true)}>记录跟进</Button>
            )}
            {can('agent:use') && (
              <Button onClick={() => navigate(`/agent?context=customer&id=${customerId}`)}>
                问 Agent
              </Button>
            )}
            {can('customer:assign') && (
              <Button onClick={() => setTransferModal(true)}>转移负责人</Button>
            )}
            {can('customer:assign') && (
              <Button
                loading={duplicateCaseMutation.isPending}
                disabled={duplicateCaseMutation.isPending}
                onClick={() => duplicateCaseMutation.mutate()}
              >
                撞单检查
              </Button>
            )}
            {can('customer:update') && (
              <Button
                onClick={() => {
                  setMergeTarget(null)
                  setMergeKeyword('')
                  setMergeReason('')
                  setMergeModal(true)
                }}
              >
                合并到其他客户
              </Button>
            )}
            {can('customer:assign') && customer.pool_status !== 'public' && (
              <Popconfirm
                title="放入公海后负责人会清空，确认？"
                onConfirm={() => poolMutation.mutate('release')}
              >
                <Button>放入公海</Button>
              </Popconfirm>
            )}
            {customer.pool_status === 'public' && (
              <Button theme="solid" loading={poolMutation.isPending} onClick={() => poolMutation.mutate('claim')}>
                领取到我名下
              </Button>
            )}
          </>
        }
      />

      <Modal
        title="撞单检查结果"
        visible={duplicateCaseCount > 0}
        okText="查看撞单裁定"
        cancelText="关闭"
        onCancel={() => setDuplicateCaseCount(0)}
        onOk={() => {
          setDuplicateCaseCount(0)
          navigate('/duplicate-cases')
        }}
      >
        <p>该客户有 {duplicateCaseCount} 条疑似撞单待裁定。重复检查会复用已有的待裁定记录。</p>
        <p>客户归属保持原样，由有权限的人员核对证据后裁定。</p>
      </Modal>

      <SectionCard>
        <Tabs
          type="line"
          activeKey={activeKey}
          onChange={(key) => {
            setActiveKey(key)
            setSearchParams({ tab: key })
          }}
          tabList={TABS}
        />

        <div style={{ marginTop: 16 }}>
          {activeKey === 'overview' && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                gap: 20,
              }}
            >
              <Field label="客户名称" value={customer.name} />
              <Field label="客户简称" value={customer.short_name ?? '-'} />
              <Field label="客户类型" value={customer.customer_type ?? '-'} />
              <Field label="国家 / 地区" value={`${customer.country ?? '-'} / ${customer.region ?? '-'}`} />
              <Field label="统一社会信用代码" value={customer.tax_no ?? '-'} />
              <Field label="官网域名" value={customer.domain ?? '-'} />
              <Field label="详细地址" value={customer.address ?? '-'} />
              {/* 三个时钟分开显示（文档 §2.3）：联系过 ≠ 业务有进展 ≠ 约好了下次。
                  合成一个时间字段就回答不了"到底哪一样断了" */}
              <Field
                label="最近跟进"
                value={
                  customer.last_followup_at
                    ? new Date(customer.last_followup_at).toLocaleString('zh-CN')
                    : '-'
                }
              />
              <Field
                label="最近业务进展"
                value={
                  customer.last_progress_at
                    ? new Date(customer.last_progress_at).toLocaleString('zh-CN')
                    : '-'
                }
              />
              <Field
                label="约定下次跟进"
                value={
                  customer.next_followup_at
                    ? `${new Date(customer.next_followup_at).toLocaleString('zh-CN')}${
                        new Date(customer.next_followup_at).getTime() < Date.now()
                          ? '（已到约定时间）'
                          : ''
                      }`
                    : '-'
                }
              />
              <Field label="备注" value={customer.remark ?? '-'} />
              <div style={{ gridColumn: '1 / -1' }}>
                <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 6 }}>
                  客户标签
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                  {(customer.tags ?? []).length === 0 && (
                    <span style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>还没有标签</span>
                  )}
                  {(customer.tags ?? []).map((tag) => (
                    <Tag
                      key={tag.id}
                      closable={can('customer:update')}
                      onClose={() => detachTagMutation.mutate(tag.id)}
                    >
                      {tag.name}
                    </Tag>
                  ))}
                  {can('customer:update') && (
                    <Button
                      size="small"
                      theme="borderless"
                      onClick={() => {
                        setPendingTagIds([])
                        setTagPickerOpen(true)
                      }}
                    >
                      + 添加标签
                    </Button>
                  )}
                </div>
              </div>
            </div>
          )}

          {activeKey === 'contacts' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: 16 }}>
              <div>
                <div className="toolbar">
                  <div style={{ flex: 1 }} />
                  <Button
                    theme="solid"
                    onClick={() => {
                      setContactForm({ name: '', is_primary: false })
                      setContactDupes(null)
                      setContactModal(true)
                    }}
                  >
                    新建联系人
                  </Button>
                </div>
                <Table<Contact>
                  columns={contactColumns}
                  dataSource={contactsQuery.data ?? []}
                  loading={contactsQuery.isLoading}
                  rowKey="id"
                  pagination={false}
                  empty="还没有联系人"
                />
              </div>
              {/* 核心决策人卡片（设计稿客户详情右栏） */}
              <DecisionMakerCard customerId={customerId} boxed={false} />
            </div>
          )}

          {activeKey === 'opportunities' && (
            <Table<Opportunity>
              columns={[
                {
                  title: '商机名称',
                  dataIndex: 'title',
                  render: (text: string, record: Opportunity) => (
                    <Link to={`/opportunities/${record.id}`} style={{ color: 'var(--crm-primary)' }}>
                      {text}
                    </Link>
                  ),
                },
                { title: '阶段', dataIndex: 'stage_name', width: 110 },
                {
                  title: '预计金额',
                  dataIndex: 'expected_amount',
                  width: 130,
                  render: (value: number | null) => (value ? `¥${value.toLocaleString('zh-CN')}` : '-'),
                },
                { title: '预计成交日', dataIndex: 'expected_close_date', width: 130, render: (v: string | null) => v ?? '-' },
                { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' },
              ]}
              dataSource={opportunitiesQuery.data?.items ?? []}
              loading={opportunitiesQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty="该客户还没有商机"
            />
          )}

          {activeKey === 'followups' && (
            <>
              <div className="toolbar">
                <div style={{ flex: 1 }} />
                <Button onClick={() => setFollowupVisible(true)}>记录跟进</Button>
              </div>
              <Table<FollowUp>
                columns={[
                  {
                    title: '时间',
                    dataIndex: 'created_at',
                    width: 180,
                    render: (v: string) => new Date(v).toLocaleString('zh-CN'),
                  },
                  { title: '方式', dataIndex: 'followup_type', width: 100 },
                  { title: '内容', dataIndex: 'content' },
                  { title: '客户反馈', dataIndex: 'customer_feedback', render: (v: string | null) => v ?? '-' },
                  { title: '下一步', dataIndex: 'next_action', render: (v: string | null, row: FollowUp) => v ?? (({ customer_declined: '客户明确拒绝', business_closed: '业务已关闭', waiting_external: '等待外部固定节点' } as Record<string, string>)[row.exemption_reason ?? ''] ?? '-') },
                  { title: '记录时约定', dataIndex: 'task_due_at', width: 170, render: (v: string | null) => v ? new Date(v).toLocaleString('zh-CN') : '-' },
                  {
                    title: '附件',
                    width: 90,
                    render: (_: unknown, record: FollowUp) => (
                      <FollowUpAttachmentsButton followupId={record.id} />
                    ),
                  },
                ]}
                dataSource={followupsQuery.data?.items ?? []}
                loading={followupsQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty="还没有跟进记录"
              />
            </>
          )}

          {activeKey === 'logs' && (
            <Timeline events={timelineQuery.data ?? []} loading={timelineQuery.isLoading} />
          )}

          {activeKey === 'quotes' && (
            <Table<Quote>
              columns={[
                {
                  title: '报价单号',
                  dataIndex: 'quote_no',
                  width: 170,
                  render: (text: string, record: Quote) => (
                    <Link to={`/quotes/${record.id}`} style={{ color: 'var(--crm-primary)' }}>
                      {text}
                    </Link>
                  ),
                },
                { title: '版本', dataIndex: 'current_version_no', width: 80, render: (v: number | null) => (v ? `V${v}` : '-') },
                {
                  title: '金额',
                  dataIndex: 'current_version_amount',
                  width: 140,
                  render: (v: number | null) => (v === null || v === undefined ? '-' : `¥${v.toLocaleString('zh-CN')}`),
                },
                { title: '状态', dataIndex: 'status_label', width: 110 },
                { title: '有效期', dataIndex: 'valid_until', width: 120, render: (v: string | null) => v ?? '-' },
              ]}
              dataSource={quotesQuery.data?.items ?? []}
              loading={quotesQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty="该客户还没有报价"
            />
          )}

          {activeKey === 'orders' && (
            <Table<Order>
              columns={[
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
                  render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
                },
                { title: '履约状态', dataIndex: 'status_label', width: 120 },
                { title: '交期', dataIndex: 'delivery_date', width: 120, render: (v: string | null) => v ?? '-' },
              ]}
              dataSource={ordersQuery.data?.items ?? []}
              loading={ordersQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty="该客户还没有订单"
            />
          )}

          {activeKey === 'files' && (
            <AttachmentPanel businessType="customer" businessId={customerId} />
          )}
        </div>
      </SectionCard>

      {activeKey === 'overview' && can('agent:use') && (
        <SectionCard
          title="AI 分析"
          style={{ marginTop: 16 }}
          extra={
            <>
              <Button size="small" loading={aiMutation.isPending} onClick={() => aiMutation.mutate('summary')}>
                客户总结
              </Button>
              <Button size="small" loading={aiMutation.isPending} onClick={() => aiMutation.mutate('followup')}>
                跟进建议
              </Button>
            </>
          }
        >
          <AgentInsight
            envelope={aiEnvelope}
            empty="点右上角按钮运行：客户总结汇总商机、报价、订单与回款全貌；跟进建议按规则给出下一步动作"
          />
        </SectionCard>
      )}

      <Modal
        title="新建联系人"
        visible={contactModal}
        onCancel={() => setContactModal(false)}
        onOk={() => {
          if (!contactForm.name?.trim()) {
            Toast.warning('联系人姓名必填')
            return
          }
          contactMutation.mutate(contactForm)
        }}
        confirmLoading={contactMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>姓名 *</div>
            <Input
              value={contactForm.name ?? ''}
              onChange={(value) => setContactForm({ ...contactForm, name: value })}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>职位</div>
              <Input
                value={contactForm.title ?? ''}
                onChange={(value) => setContactForm({ ...contactForm, title: value })}
                placeholder="采购经理"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>手机</div>
              <Input
                value={contactForm.mobile ?? ''}
                onChange={(value) => setContactForm({ ...contactForm, mobile: value })}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>邮箱</div>
            <Input
              value={contactForm.email ?? ''}
              onChange={(value) => setContactForm({ ...contactForm, email: value })}
            />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>疑似重复检查</span>
              <Button
                size="small"
                loading={contactDupMutation.isPending}
                onClick={() => contactDupMutation.mutate()}
              >
                按已填内容查重
              </Button>
            </div>
            {contactDupes !== null &&
              (contactDupes.length === 0 ? (
                <div style={{ color: 'var(--crm-success)', fontSize: 13 }}>没有发现疑似重复的联系人</div>
              ) : (
                <div style={{ display: 'grid', gap: 6 }}>
                  {contactDupes.map((match) => (
                    <div
                      key={match.id}
                      style={{
                        fontSize: 13,
                        padding: '6px 10px',
                        border: '1px solid var(--crm-warning-soft)',
                        borderRadius: 'var(--crm-radius-sm)',
                        background: 'var(--crm-surface-low)',
                      }}
                    >
                      相似度 {match.score}%：{match.name}
                      {match.mobile ? ` · ${match.mobile}` : ''}
                      {match.reasons.length ? `（${match.reasons.join('、')}）` : ''}
                    </div>
                  ))}
                  <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                    如确认是同一人，建议取消后到已有客户下补录；确实不同再继续保存。
                  </div>
                </div>
              ))}
          </div>
        </div>
      </Modal>

      <Modal
        title="编辑客户"
        visible={editModal}
        onCancel={() => setEditModal(false)}
        onOk={() => {
          if (!editForm.name.trim()) {
            Toast.warning('客户名称必填')
            return
          }
          editMutation.mutate(editForm)
        }}
        confirmLoading={editMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>客户名称 *</div>
            <Input value={editForm.name} onChange={(v) => setEditForm({ ...editForm, name: v })} />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户简称</div>
              <Input
                value={editForm.short_name ?? ''}
                onChange={(v) => setEditForm({ ...editForm, short_name: v })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户等级</div>
              <Select
                value={editForm.level ?? undefined}
                onChange={(v) => setEditForm({ ...editForm, level: v as string })}
                optionList={['A', 'B', 'C', 'D'].map((value) => ({ value, label: `${value} 级` }))}
                style={{ width: '100%' }}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>省份 / 地区</div>
              <Input
                value={editForm.region ?? ''}
                onChange={(v) => setEditForm({ ...editForm, region: v })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户来源</div>
              <Input
                value={editForm.source ?? ''}
                onChange={(v) => setEditForm({ ...editForm, source: v })}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>详细地址</div>
            <Input
              value={editForm.address ?? ''}
              onChange={(v) => setEditForm({ ...editForm, address: v })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注</div>
            <Input
              value={editForm.remark ?? ''}
              onChange={(v) => setEditForm({ ...editForm, remark: v })}
            />
          </div>
        </div>
      </Modal>

      <Modal
        title="转移负责人"
        visible={transferModal}
        onCancel={() => setTransferModal(false)}
        onOk={() => transferMutation.mutate(transferTo)}
        confirmLoading={transferMutation.isPending}
        okText="确认转移"
      >
        <div style={{ marginBottom: 8, color: 'var(--crm-text-2)', fontSize: 13 }}>
          当前负责人：{customer.owner_name ?? '未分配'}
        </div>
        <Select
          placeholder="选择新的负责人"
          value={transferTo ?? undefined}
          onChange={(value) => setTransferTo(value as number)}
          optionList={(usersQuery.data?.items ?? []).map((item) => ({
            value: item.id,
            label: `${item.name}（${item.department ?? '未分配部门'}）`,
          }))}
          loading={usersQuery.isLoading}
          style={{ width: '100%' }}
        />
        <div style={{ marginTop: 12, color: 'var(--crm-text-3)', fontSize: 12 }}>
          转移会记录历史负责人；不选人直接确认 = 放入公海。
        </div>
      </Modal>

      {/* 添加标签 */}
      <Modal
        title="添加客户标签"
        visible={tagPickerOpen}
        onCancel={() => setTagPickerOpen(false)}
        onOk={() => attachTagsMutation.mutate(pendingTagIds)}
        confirmLoading={attachTagsMutation.isPending}
        okText="添加"
        okButtonProps={{ disabled: pendingTagIds.length === 0 }}
      >
        <div style={{ marginBottom: 8, color: 'var(--crm-text-3)', fontSize: 12 }}>
          标签是受控字典，可在「系统设置 › 客户标签」里维护。
        </div>
        <Select
          multiple
          placeholder="选择一个或多个标签"
          value={pendingTagIds}
          onChange={(value) => setPendingTagIds((value as number[]) ?? [])}
          loading={tagsQuery.isLoading}
          style={{ width: '100%' }}
          optionList={(tagsQuery.data ?? [])
            // 已经打过的就不列出来，避免重复选择
            .filter((tag) => !(customer.tags ?? []).some((own) => own.id === tag.id))
            .map((tag) => ({ value: tag.id, label: `${tag.name}（${tag.type}）` }))}
        />
        {(tagsQuery.data ?? []).length === 0 && !tagsQuery.isLoading && (
          <div style={{ marginTop: 8, color: 'var(--crm-text-3)', fontSize: 12 }}>
            还没有任何标签，先去「系统设置 › 客户标签」建一个。
          </div>
        )}
      </Modal>

      {/* 合并客户 */}
      <Modal
        title="合并到其他客户"
        visible={mergeModal}
        onCancel={() => setMergeModal(false)}
        onOk={() => mergeMutation.mutate()}
        confirmLoading={mergeMutation.isPending}
        okText="确认合并"
        okButtonProps={{ disabled: !mergeTarget }}
      >
        <div
          style={{
            background: 'var(--crm-warning-soft)',
            color: 'var(--crm-warning)',
            padding: 10,
            borderRadius: 4,
            fontSize: 13,
            marginBottom: 12,
          }}
        >
          合并不可逆：「{customer.name}」的联系人、商机、报价、订单、跟进、任务、标签
          会全部转移到目标客户，然后本客户被删除。
        </div>

        <Input
          placeholder="搜索要保留的客户（按名称）"
          value={mergeKeyword}
          onChange={setMergeKeyword}
        />

        {mergeKeyword.trim() && (
          <div style={{ marginTop: 8 }}>
            {mergeCandidatesQuery.isLoading && (
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>查询中…</div>
            )}
            {!mergeCandidatesQuery.isLoading &&
              (mergeCandidatesQuery.data?.matches ?? []).filter((m) => m.id !== customerId)
                .length === 0 && (
                <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                  没有找到匹配的客户，换个关键词试试。
                </div>
              )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(mergeCandidatesQuery.data?.matches ?? [])
                .filter((m) => m.id !== customerId)
                .map((match) => (
                  <div
                    key={match.id}
                    onClick={() => setMergeTarget(match.id)}
                    style={{
                      padding: '8px 10px',
                      border: `1px solid ${
                        mergeTarget === match.id ? 'var(--crm-primary)' : 'var(--crm-outline)'
                      }`,
                      borderRadius: 4,
                      cursor: 'pointer',
                      background:
                        mergeTarget === match.id ? 'var(--crm-primary-soft)' : 'var(--crm-surface)',
                    }}
                  >
                    <div style={{ fontWeight: 600 }}>{match.name}</div>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      相似度 {match.score}%（{match.reasons.join('、')}）
                    </div>
                  </div>
                ))}
            </div>
          </div>
        )}

        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
            合并原因（可选，会记入合并日志）
          </div>
          <Input value={mergeReason} onChange={setMergeReason} placeholder="如：重复录入" />
        </div>
      </Modal>

      <FollowUpModal
        visible={followupVisible}
        onClose={() => setFollowupVisible(false)}
        target={{ customerId }}
        onCreated={invalidateCustomer}
      />
    </div>
  )
}
