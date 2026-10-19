import { formatBeijingTime } from '../../shared/beijing-time'
import { UNRATED_OPTION, customerLevelValue, customerLevelPayload, customerLevelLabel } from '../../shared/customer-level'
import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, Input, Modal, Popconfirm, Select, Table, Tabs, Tag, TextArea, Toast } from '@douyinfe/semi-ui'

import {
  attachCustomerTags,
  claimCustomer,
  createContact,
  deduplicateContacts,
  deduplicateCustomers,
  deleteContact,
  detachCustomerTag,
  getCustomer,
  getCustomerMergePreview,
  listContacts,
  listTags,
  mergeCustomers,
  openDuplicateCases,
  releaseCustomerToPool,
  setPrimaryContact,
  transferCustomer,
  updateContact,
  updateCustomer,
  type ContactDuplicateMatch,
  type CustomerPayload,
} from '../../shared/api/customer'
import { listUsers } from '../../shared/api/system'
import { optionMatcher } from '../../shared/components/optionMatch'
import { listDictionaries } from '../../shared/api/settings'
import { getCustomerTimeline, listFollowups, type FollowUp } from '../../shared/api/followup'
import { listOpportunities, type Opportunity } from '../../shared/api/opportunity'
import { listOrders, type Order } from '../../shared/api/order'
import { listQuotes, type Quote } from '../../shared/api/quote'
import { usePermissions } from '../../shared/hooks/permissions'
import type { Contact } from '../../shared/types'
import DetailHeader from '../../shared/components/DetailHeader'
import SectionCard from '../../shared/components/SectionCard'
import ContractDocsPanel from '../../shared/components/ContractDocsPanel'
import AgentInsight from '../../shared/components/AgentInsight'
import { agentCustomerSummary, agentFollowupSuggestion, type AnalysisEnvelope } from '../../shared/api/agent'
import FollowUpModal from '../common/FollowUpModal'
import FollowUpAttachmentsButton from '../common/FollowUpAttachmentsButton'
import Timeline from '../common/Timeline'
import AttachmentPanel from '../common/AttachmentPanel'
import DecisionMakerCard from '../common/DecisionMakerCard'
import FormLabel from '../../shared/components/FormLabel'
import BlockedHint from '../../shared/components/BlockedHint'
// 金额一律带**实际币种**（第九批 §9.9）：这几张表原来都写死 `¥`，
// 同一客户同时有 CNY / USD 订单时，旁边那张表显示的就是错的。
import { formatMoney } from '../../shared/components/money'

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
  /** 正在编辑的联系人；null = 新建。标题与提交目标都按它切换。 */
  const [contactEditing, setContactEditing] = useState<Contact | null>(null)
  // 新建联系人时的查重结果；null = 还没查过
  const [contactDupes, setContactDupes] = useState<ContactDuplicateMatch[] | null>(null)
  const [ratingModal, setRatingModal] = useState(false)
  const [rating, setRating] = useState<string>(UNRATED_OPTION.value)
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
  //: 冲突处理口径：`{customer_price: 'keep_target'}`。合并不可逆，
  //: 这种"两边各有一个价"的事必须人先选，不能由代码默默挑一个。
  const [mergeResolutions, setMergeResolutions] = useState<Record<string, string>>({})

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

  // 选定目标客户后自动拉"会跟着走什么、有什么冲突"——合并前必须让人看到这一页
  const mergePreviewQuery = useQuery({
    queryKey: ['merge-preview', customerId, mergeTarget],
    queryFn: () => getCustomerMergePreview(customerId, mergeTarget!),
    enabled: mergeModal && Boolean(mergeTarget),
  })
  const mergePreview = mergePreviewQuery.data
  /** 还有冲突没给口径 → 不允许提交 */
  const unresolvedConflicts = (mergePreview?.blocking ?? []).filter(
    (key) => !mergeResolutions[key],
  )

  const customerQuery = useQuery({
    queryKey: ['customer', customerId],
    queryFn: () => getCustomer(customerId),
    enabled: Number.isFinite(customerId),
  })
  const customerLevelsQuery = useQuery({
    queryKey: ['dictionaries', 'customer_level'],
    queryFn: () => listDictionaries('customer_level'),
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

  /**
   * 改联系人。
   *
   * ⚠️ **脱敏字段不提交**：对没有"看完整号码"权限的人，列表里拿到的就是
   * `138****8000` 这种星号形状（响应上的 `contact_masked`）。以前前端根本没有
   * 编辑入口，这条路走不到；现在有了，如果不加判断，"只改个职位"就会把星号存回去，
   * **真实号码永久丢失**。所以这里按 masked 把 mobile/phone/email/wechat 挑掉，
   * 界面上那几个框也是锁住的。后端另有一道兜底（收到星号形状直接 403 并说明）。
   */
  const contactEditMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<Contact> }) => updateContact(id, payload),
    onSuccess: () => {
      Toast.success('联系人已保存')
      setContactModal(false)
      void queryClient.invalidateQueries({ queryKey: ['contacts', customerId] })
      void queryClient.invalidateQueries({ queryKey: ['customer', customerId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const contactDeleteMutation = useMutation({
    mutationFn: (contactId: number) => deleteContact(contactId),
    onSuccess: () => {
      Toast.success('联系人已删除')
      void queryClient.invalidateQueries({ queryKey: ['contacts', customerId] })
      void queryClient.invalidateQueries({ queryKey: ['customer', customerId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const contactPrimaryMutation = useMutation({
    mutationFn: (contactId: number) => setPrimaryContact(contactId),
    onSuccess: () => {
      Toast.success('已设为主联系人')
      void queryClient.invalidateQueries({ queryKey: ['contacts', customerId] })
      // 核心决策人卡片也按主联系人/职位排序，一起刷
      void queryClient.invalidateQueries({ queryKey: ['contacts-decision', customerId] })
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

  const ratingMutation = useMutation({
    mutationFn: () => updateCustomer(customerId, { level: customerLevelPayload(rating) }),
    onSuccess: () => {
      Toast.success('客户评级已保存')
      setRatingModal(false)
      invalidateCustomer()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const transferMutation = useMutation({
    mutationFn: (ownerId: number | null) => transferCustomer(customerId, { owner_id: ownerId }),
    onSuccess: (result) => {
      // 客户名下的单据会跟着走；但**恰好被同事先接走**的那些会留在同事手里。
      // 后端把张数与类别带了回来，这里如实提示一句 —— 否则操作者看到
      // "客户名下怎么少了一张订单"会莫名其妙（主人口径：要提示，不要静默）。
      const skipped = result.document_transfer?.skipped_total ?? 0
      if (skipped > 0) {
        const kinds = result.document_transfer?.skipped_labels ?? []
        Toast.warning(
          `负责人已变更；有 ${skipped} 张单据${kinds.length ? `（${kinds.join('、')}）` : ''}` +
            '因已被其他同事接手，未跟着转',
        )
      } else {
        Toast.success('负责人已变更')
      }
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
    mutationFn: ({ action, reason }: { action: 'release' | 'claim'; reason?: string }) =>
      action === 'release'
        ? releaseCustomerToPool(customerId, reason)
        : claimCustomer(customerId),
    onSuccess: (_data, { action }) => {
      Toast.success(action === 'release' ? '已放入公海' : '领取成功')
      invalidateCustomer()
    },
    onError: (error: Error, { action }) => {
      // 还在履约中会被后端拦下（返工单 6.3）：提示里已经说清是哪张单拦住的。
      // 这里再加一步引导 —— 主管确需释放就填原因走例外，而不是让人卡住。
      if (action === 'release' && error.message.includes('履约中')) {
        const reason = window.prompt(
          `${error.message}\n\n确需例外释放请填写原因（会记入审计）：`,
        )
        if (reason && reason.trim()) {
          poolMutation.mutate({ action: 'release', reason: reason.trim() })
          return
        }
        return
      }
      Toast.error(error.message)
    },
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
        // 冲突口径原样带上；没有冲突时是空对象（后端也不要求）
        resolutions: Object.keys(mergeResolutions).length ? mergeResolutions : undefined,
      }),
    onSuccess: (result) => {
      const moved = Object.entries(result.moved).filter(([, count]) => count > 0)
      const movedText = moved
        .slice(0, 6)
        .map(([label, count]) => `${label} ${count}`)
        .join('、')
      const more = moved.length > 6 ? ` 等 ${moved.length} 类` : ''
      Toast.success(`已合并，迁移：${movedText ? movedText + more : '无关联资料'}`)
      setMergeModal(false)
      setMergeResolutions({})
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

  // 第九批 §9.1：板块权限改由服务端**逐块**判（没权限就不返回、不查）。
  // 这里把"请求失败（多半是没有该模块权限）"与"确实没有数据"分开显示——
  // 否则用户会看到"该客户还没有订单"，把"无权查看"误读成"真的没有"。
  const emptyHint = (failed: boolean, normal: string, error?: unknown) =>
    failed
      ? ((error as Error | undefined)?.message || '无权查看该板块（也可能是加载失败），请确认账号权限')
      : normal

  if (customerQuery.isLoading) {
    return <div className="page-container">加载中…</div>
  }
  if (!customer) {
    return <div className="page-container">客户不存在或无权查看</div>
  }

  const canEditContact = can('customer:update')
  const contactColumns = [
    {
      title: '姓名',
      dataIndex: 'name',
      width: 140,
      render: (v: string, row: Contact) => (
        <>
          {v}
          {/* 主联系人一眼可见：决策人卡片、简称、后续对外沟通都以它为准 */}
          {row.is_primary && <Tag color="green" size="small" style={{ marginLeft: 6 }}>主</Tag>}
        </>
      ),
    },
    { title: '职位', dataIndex: 'title', width: 130, render: (v: string | null) => v ?? '-' },
    // 脱敏时**照原样显示**（星号）并给一句话，别让用户以为库里存的就是星号
    {
      title: '手机',
      dataIndex: 'mobile',
      width: 150,
      render: (v: string | null, row: Contact) => (
        <span title={row.contact_masked ? '你对这条联系人的联系方式没有完整查看权限' : undefined}>
          {v ?? '-'}
        </span>
      ),
    },
    { title: '邮箱', dataIndex: 'email', render: (v: string | null) => v ?? '-' },
    {
      title: '操作',
      width: 170,
      render: (_: unknown, row: Contact) =>
        canEditContact ? (
          <span style={{ display: 'inline-flex', gap: 10, alignItems: 'center' }}>
            {!row.is_primary && (
              <a onClick={() => contactPrimaryMutation.mutate(row.id)}>设为主联系人</a>
            )}
            <a
              onClick={() => {
                setContactEditing(row)
                // 脱敏时不把星号塞进输入框：那几个框会显示"（无权限查看）"并锁住
                setContactForm({
                  name: row.name,
                  title: row.title ?? '',
                  department: row.department ?? '',
                  mobile: row.contact_masked ? '' : (row.mobile ?? ''),
                  phone: row.contact_masked ? '' : (row.phone ?? ''),
                  email: row.contact_masked ? '' : (row.email ?? ''),
                  wechat: row.contact_masked ? '' : (row.wechat ?? ''),
                  is_primary: row.is_primary,
                  remark: row.remark ?? '',
                })
                setContactDupes(null)
                setContactModal(true)
              }}
            >
              编辑
            </a>
            <Popconfirm
              title="删除这个联系人？"
              content="删除后不再出现在列表与决策人卡片里；历史跟进记录会保留。"
              onConfirm={() => contactDeleteMutation.mutate(row.id)}
            >
              <a style={{ color: 'var(--crm-error)' }}>删除</a>
            </Popconfirm>
          </span>
        ) : null,
    },
  ]

  return (
    <div className="page-container">
      {/* 头部按设计稿排：标题 + 状态标签一行，关键信息行在标题下方左对齐，按钮贴右 */}
      <DetailHeader
        title={customer.name}
        tags={
          <>
            <Tag color={customer.level === 'A' ? 'green' : customer.level === 'B' ? 'blue' : 'grey'}>
              {customerLevelLabel(customer.level)}
            </Tag>
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
                    level: customer.level ?? null,
                    remark: customer.remark ?? '',
                  })
                  setEditModal(true)
                }}
              >
                编辑客户
              </Button>
            )}
            {can('customer:update') && (
              <Button onClick={() => { setRating(customerLevelValue(customer.level)); setRatingModal(true) }}>
                {customer.level ? '重新评级' : '评定等级'}
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
                onConfirm={() => poolMutation.mutate({ action: 'release' })}
              >
                <Button>放入公海</Button>
              </Popconfirm>
            )}
            {customer.pool_status === 'public' && (
              <Popconfirm
                title={`确认领取客户「${customer.name}」？`}
                content="领取后客户将归到你名下，由你负责跟进。"
                onConfirm={() => poolMutation.mutate({ action: 'claim' })}
                disabled={poolMutation.isPending}
              >
                <Button theme="solid" loading={poolMutation.isPending}>领取到我名下</Button>
              </Popconfirm>
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
                    ? formatBeijingTime(customer.last_followup_at)
                    : '-'
                }
              />
              <Field
                label="最近业务进展"
                value={
                  customer.last_progress_at
                    ? formatBeijingTime(customer.last_progress_at)
                    : '-'
                }
              />
              <Field
                label="约定下次跟进"
                value={
                  customer.next_followup_at
                    ? `${formatBeijingTime(customer.next_followup_at)}${
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
            <div className="detail-split">
              <div className="split-main">
                <div className="toolbar">
                  {/* 说清"这是谁的联系人"（主人 2026-10-13：「我都不知道是填自己公司的
                      联系人还是填客户那边的联系人」）。
                      事实依据：`contacts.customer_id → customers` 是外键，
                      库里还有"一个客户最多一个主联系人"的约束，
                      决策人卡片叫「客户决策关系图」—— 这里记的是**客户单位的人**。
                      自己公司的人在这套系统里是账号（users），不走这张表。 */}
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginRight: 12 }}>
                    这里记的是「客户单位」的联系人（对方公司的采购、技术、老板等），不是你同事
                  </div>
                  <div style={{ flex: 1 }} />
                  {/* ⚠️ 与后端同码（2026-10-14 审查修）：创建联系人是
                      `require_permission("customer:update")`（customer/router.py），
                      而同文件的行内「编辑 / 删除」本来就判了 `canEditContact`，
                      **只有"新建"漏了** —— 财务这类只有 `customer:view` 的账号
                      填完一屏才吃 403。
                      另外这也把「按已填内容查重」那个入口关在了权限内
                      （查重会回显数据范围内联系人的信息）。 */}
                  {canEditContact && (
                    <Button
                      theme="solid"
                      onClick={() => {
                        setContactEditing(null)
                        setContactForm({ name: '', is_primary: false })
                        setContactDupes(null)
                        setContactModal(true)
                      }}
                    >
                      新建联系人
                    </Button>
                  )}
                </div>
                <Table<Contact>
                  columns={contactColumns}
                  dataSource={contactsQuery.data ?? []}
                  loading={contactsQuery.isLoading}
                  rowKey="id"
                  pagination={false}
                  empty="这家客户还没有登记联系人（加的是对方公司的人）"
                  // 行上带出联系人 id：UI 取证脚本要能**精确定位某一行**去点
                  // 编辑/删除（按"第一条"或按姓名找会误伤别人的数据，实测踩过）
                  onRow={(row) => ({ 'data-contact-id': String((row as Contact).id) })}
                />
              </div>
              {/* 核心决策人卡片（设计稿客户详情右栏）：窄屏时整块落到下方 */}
              <div className="split-side">
                <DecisionMakerCard customerId={customerId} boxed={false} />
              </div>
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
                  render: (value: number | null, record: Opportunity) =>
                    formatMoney(value, record.currency),
                },
                { title: '预计成交日', dataIndex: 'expected_close_date', width: 130, render: (v: string | null) => v ?? '-' },
                { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' },
              ]}
              dataSource={opportunitiesQuery.data?.items ?? []}
              loading={opportunitiesQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty={emptyHint(opportunitiesQuery.isError, '该客户还没有商机', opportunitiesQuery.error)}
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
                    render: (v: string) => formatBeijingTime(v),
                  },
                  { title: '方式', dataIndex: 'followup_type', width: 100 },
                  { title: '内容', dataIndex: 'content' },
                  { title: '客户反馈', dataIndex: 'customer_feedback', render: (v: string | null) => v ?? '-' },
                  { title: '下一步', dataIndex: 'next_action', render: (v: string | null, row: FollowUp) => v ?? (({ customer_declined: '客户明确拒绝', business_closed: '业务已关闭', waiting_external: '等待外部固定节点' } as Record<string, string>)[row.exemption_reason ?? ''] ?? '-') },
                  { title: '记录时约定', dataIndex: 'task_due_at', width: 170, render: (v: string | null) => v ? formatBeijingTime(v) : '-' },
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
                empty={emptyHint(followupsQuery.isError, '还没有跟进记录', followupsQuery.error)}
              />
            </>
          )}

          {activeKey === 'logs' && (
            <Timeline
              events={timelineQuery.data ?? []}
              loading={timelineQuery.isLoading}
              currentType="customer"
              currentId={customerId}
            />
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
                  render: (v: number | null, record: Quote) => formatMoney(v, record.currency),
                },
                { title: '状态', dataIndex: 'status_label', width: 110 },
                { title: '有效期', dataIndex: 'valid_until', width: 120, render: (v: string | null) => v ?? '-' },
              ]}
              dataSource={quotesQuery.data?.items ?? []}
              loading={quotesQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty={emptyHint(quotesQuery.isError, '该客户还没有报价', quotesQuery.error)}
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
                  render: (v: number, record: Order) => formatMoney(v, record.currency),
                },
                {
                  title: '已回款',
                  dataIndex: 'received_amount',
                  width: 130,
                  render: (v: number, record: Order) => formatMoney(v, record.currency),
                },
                { title: '履约状态', dataIndex: 'status_label', width: 120 },
                { title: '交期', dataIndex: 'delivery_date', width: 120, render: (v: string | null) => v ?? '-' },
              ]}
              dataSource={ordersQuery.data?.items ?? []}
              loading={ordersQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty={emptyHint(ordersQuery.isError, '该客户还没有订单', ordersQuery.error)}
            />
          )}

          {activeKey === 'files' && (
            <AttachmentPanel businessType="customer" businessId={customerId} />
          )}
        </div>
      </SectionCard>

      {activeKey === 'overview' && (
        <ContractDocsPanel
          customerId={customerId}
          style={{ marginTop: 16 }}
          empty="这家客户还没有合同文档——去合同台账按模板生成"
        />
      )}

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

      {/* 新建 / 编辑共用一个弹窗：字段完全一样，分成两个弹窗只会让两边慢慢走散 */}
      <Modal
        title={
          contactEditing
            ? `编辑联系人${contactEditing.name ? `：${contactEditing.name}` : ''}`
            : '新建联系人（客户单位的人）'
        }
        visible={contactModal}
        onCancel={() => {
          setContactModal(false)
          setContactEditing(null)
        }}
        onOk={() => {
          if (!contactForm.name?.trim()) {
            Toast.warning('联系人姓名必填')
            return
          }
          // 脱敏字段（星号）绝不提交：提交了会把真实号码覆盖成星号串。
          // 编辑时按 masked 挑掉这几个键；新建时本来就没有脱敏一说。
          const masked = Boolean(contactEditing?.contact_masked)
          const payload: Partial<Contact> = masked
            ? {
                name: contactForm.name,
                title: contactForm.title ?? null,
                department: contactForm.department ?? null,
                is_primary: contactForm.is_primary,
                remark: contactForm.remark ?? null,
              }
            : contactForm
          if (contactEditing) contactEditMutation.mutate({ id: contactEditing.id, payload })
          else contactMutation.mutate(payload)
        }}
        confirmLoading={contactMutation.isPending || contactEditMutation.isPending}
        okText="保存"
        width={640}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>姓名</FormLabel>
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
                placeholder="客户那边的职位，例如：采购经理"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>部门</div>
              <Input
                value={contactForm.department ?? ''}
                onChange={(value) => setContactForm({ ...contactForm, department: value })}
                placeholder="采购部"
              />
            </div>
          </div>
          {/* 联系方式整块：脱敏时**锁住并说明**，而不是显示星号让人以为库里就是星号 */}
          {contactEditing?.contact_masked && (
            <BlockedHint reasons={[
              '你对这条联系人的手机、座机、邮箱、微信号没有完整查看权限（显示为星号），所以这里不能改，也不会把这几个字段提交上去。',
              '要改联系方式：请让客户负责人本人操作，或请管理员为你开通「客户联系方式完整查看」权限后重试。',
            ]} action="修改联系方式" />
          )}
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>手机</div>
              <Input
                value={contactForm.mobile ?? ''}
                disabled={Boolean(contactEditing?.contact_masked)}
                placeholder={contactEditing?.contact_masked ? '（无权限查看，不可修改）' : undefined}
                onChange={(value) => setContactForm({ ...contactForm, mobile: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>座机</div>
              <Input
                value={contactForm.phone ?? ''}
                disabled={Boolean(contactEditing?.contact_masked)}
                placeholder={contactEditing?.contact_masked ? '（无权限查看，不可修改）' : '0571-88888888'}
                onChange={(value) => setContactForm({ ...contactForm, phone: value })}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>邮箱</div>
              <Input
                value={contactForm.email ?? ''}
                disabled={Boolean(contactEditing?.contact_masked)}
                placeholder={contactEditing?.contact_masked ? '（无权限查看，不可修改）' : undefined}
                onChange={(value) => setContactForm({ ...contactForm, email: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>微信号</div>
              <Input
                value={contactForm.wechat ?? ''}
                disabled={Boolean(contactEditing?.contact_masked)}
                placeholder={contactEditing?.contact_masked ? '（无权限查看，不可修改）' : undefined}
                onChange={(value) => setContactForm({ ...contactForm, wechat: value })}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注</div>
            <TextArea
              rows={2}
              value={contactForm.remark ?? ''}
              onChange={(value) => setContactForm({ ...contactForm, remark: value })}
              placeholder="例如：只有上午在、只认邮件沟通…"
            />
          </div>
          {/* 主联系人：以前这个勾选框**根本不存在**，表单固定传 false，
              于是"谁拍板"只能靠职位关键词猜。后端一直支持，这里补上。 */}
          <div>
            <Checkbox
              checked={Boolean(contactForm.is_primary)}
              onChange={(e) => setContactForm({ ...contactForm, is_primary: Boolean(e.target.checked) })}
            >
              设为主联系人
            </Checkbox>
            <div className="label-hint" style={{ marginLeft: 0 }}>
              一个客户只能有一个主联系人，勾上之后原来那位会自动取消；核心决策人卡片会优先显示它。
            </div>
          </div>
          {/* 查重只在**新建**时有意义：编辑自己时查重会把这条本身查出来。
              所以整块（标题行 + 结果）都跟着 `!contactEditing` 走。 */}
          {!contactEditing && (
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
                      {/* 脱敏时不回显号码：只保留「手机号一致」这类证据。
                          知道"为什么判它疑似"是有用的，看到号码本身不是
                          （2026-10-14 审查修）。 */}
                      {match.mobile && !match.contact_masked ? ` · ${match.mobile}` : ''}
                      {match.reasons.length ? `（${match.reasons.join('、')}）` : ''}
                    </div>
                  ))}
                  <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                    如确认是同一人，建议取消后到已有客户下补录；确实不同再继续保存。
                  </div>
                </div>
              ))}
          </div>
          )}
        </div>
      </Modal>

      <Modal title="客户评级" visible={ratingModal} onCancel={() => setRatingModal(false)}
        onOk={() => ratingMutation.mutate()} confirmLoading={ratingMutation.isPending}>
        <div style={{ marginBottom: 12 }}>了解客户后再评级；也可以恢复为未分级。评级会影响后续等级定价和管理规则。</div>
        <Select value={rating} onChange={(value) => setRating(String(value))} style={{ width: '100%' }}
          optionList={[UNRATED_OPTION, ...(customerLevelsQuery.data ?? []).map((item) => ({ value: item.code, label: item.label }))]} />
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
            <FormLabel required>客户名称</FormLabel>
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
                value={customerLevelValue(editForm.level)}
                onChange={(v) => setEditForm({ ...editForm, level: customerLevelPayload(v) })}
                optionList={[UNRATED_OPTION, ...(customerLevelsQuery.data ?? []).map((item) => ({
                  value: item.code,
                  label: item.label,
                }))]}
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
          filter={optionMatcher}
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
        onCancel={() => {
          setMergeModal(false)
          setMergeResolutions({})
        }}
        onOk={() => mergeMutation.mutate()}
        confirmLoading={mergeMutation.isPending}
        okText="确认合并"
        okButtonProps={{ disabled: !mergeTarget || unresolvedConflicts.length > 0 }}
        width={680}
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
          合并不可逆：「{customer.name}」的联系人、商机、报价、订单、跟进、任务、标签、
          定制需求、打样单、订单草稿、合同、案例、物流报价、专属价格、企微映射与附件
          会全部转移到目标客户，然后本客户被删除。
          <br />
          历史报价、合同签署文件、打样图纸与确认记录的<b>内容</b>不会改写，只换档案归属。
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
                    onClick={() => {
                      setMergeTarget(match.id)
                      // 换了目标客户，之前选的口径就不作数了
                      setMergeResolutions({})
                    }}
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

        {/* 影响清单：选中目标客户后自动展开 */}
        {mergeTarget && (
          <div style={{ marginTop: 14 }}>
            {mergePreviewQuery.isLoading && (
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>正在统计影响…</div>
            )}
            {mergePreview && (
              <>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
                  这次会跟着走 {mergePreview.total_links} 条资料
                </div>
                <div
                  style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 6,
                    marginBottom: 10,
                  }}
                >
                  {mergePreview.targets
                    .filter((item) => item.count > 0)
                    .map((item) => (
                      <Tag key={item.key} size="small">
                        {item.label} {item.count}
                      </Tag>
                    ))}
                  {mergePreview.total_links === 0 && (
                    <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      没有关联资料需要迁移
                    </span>
                  )}
                </div>

                {mergePreview.conflicts.length > 0 && (
                  <div
                    style={{
                      border: '1px solid var(--crm-warning)',
                      borderRadius: 4,
                      padding: '10px 12px',
                      marginBottom: 10,
                      display: 'grid',
                      gap: 10,
                    }}
                  >
                    <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--crm-warning)' }}>
                      合并前要先拿定主意（{mergePreview.conflicts.length} 处）
                    </div>
                    {mergePreview.conflicts.map((conflict) => (
                      <div key={conflict.key}>
                        <div style={{ fontSize: 13 }}>{conflict.label}</div>
                        <div
                          style={{
                            fontSize: 12,
                            color: 'var(--crm-text-3)',
                            margin: '2px 0 6px',
                          }}
                        >
                          {conflict.detail}
                        </div>
                        <Select
                          value={mergeResolutions[conflict.key]}
                          placeholder="请选择处理方式"
                          onChange={(value) =>
                            setMergeResolutions({
                              ...mergeResolutions,
                              [conflict.key]: value as string,
                            })
                          }
                          optionList={conflict.options.map((option) => ({
                            value: option.value,
                            label: option.label,
                          }))}
                          style={{ width: '100%' }}
                        />
                      </div>
                    ))}
                  </div>
                )}

                <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{mergePreview.note}</div>
              </>
            )}
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
