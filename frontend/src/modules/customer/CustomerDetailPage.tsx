import { formatBeijingTime } from '../../shared/beijing-time'
import { UNRATED_OPTION, customerLevelValue, customerLevelPayload, customerLevelLabel } from '../../shared/customer-level'
import { displayCustomerSource } from '../../shared/customer-source'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Checkbox, Dropdown, Input, Modal, Popconfirm, Select, Table, Tabs, Tag, TextArea, Toast } from '@douyinfe/semi-ui'
import { IconAIEditLevel1, IconFile, IconHistogram, IconMore } from '@douyinfe/semi-icons'

import {
  attachCustomerTags,
  claimCustomer,
  createContact,
  deduplicateContacts,
  listCustomers,
  deleteContact,
  deleteCustomer,
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
  addCustomerFollowers,
  removeCustomerFollower,
  updateContact,
  updateCustomer,
  type ContactDuplicateMatch,
  type CustomerPayload,
  type DocumentTransfer,
} from '../../shared/api/customer'
import { listUsers, type SystemUser } from '../../shared/api/system'
import { ApiError } from '../../shared/api/client'
import { listDictionaries } from '../../shared/api/settings'
import { getCustomerTimeline, listFollowups, type FollowUp } from '../../shared/api/followup'
import { listOpportunities, type Opportunity } from '../../shared/api/opportunity'
import { listOrders, type Order } from '../../shared/api/order'
import { listQuotes, type Quote } from '../../shared/api/quote'
import UserSearchSelect from '../../shared/components/UserSearchSelect'
import { usePermissions } from '../../shared/hooks/permissions'
import { useTabLabel } from '../../shared/hooks/useTabLabel'
import { useAuthStore } from '../../shared/store/auth'
import type { Contact } from '../../shared/types'
import MaskedContact from '../../shared/components/MaskedContact'
import { useNotePrompt } from '../../shared/components/NotePromptModal'
import DetailHeader from '../../shared/components/DetailHeader'
import HelpTip from '../../shared/components/HelpTip'
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
import GuardedButton from '../../shared/components/GuardedButton'
// 金额一律带**实际币种**（第九批 §9.9）：这几张表原来都写死 `¥`，
// 同一客户同时有 CNY / USD 订单时，旁边那张表显示的就是错的。
import { formatMoney } from '../../shared/components/money'
import EmptyState from '../../shared/components/EmptyState'
import PageLoading from '../../shared/components/PageLoading'

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

function Field({
  label,
  value,
  hideIfEmpty,
}: {
  label: string
  value: string
  hideIfEmpty?: boolean
}) {
  // 空值不渲染：客户卡片有十几个字段，实测一半都是「-」，
  // 全铺出来既让人以为资料残缺，也把真有内容的字段淹掉了。
  // 只有名称/类型/国家这类**总该有值**的字段才不加这个开关。
  if (hideIfEmpty && (value == null || value === '' || value === '-')) return null
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
  const currentUser = useAuthStore((state) => state.user)
  const requestedTab = searchParams.get('tab')
  const activeKey = TABS.some((tab) => tab.itemKey === requestedTab) ? requestedTab! : 'overview'
  const openTab = (key: string) => setSearchParams((previous) => {
    const next = new URLSearchParams(previous)
    next.set('tab', key)
    return next
  })
  // 详情页四张业务表都改服务端分页：50 条一页，第 51 条在第 2 页、第 101 条在第 3 页。
  // 切换客户必须回到第 1 页，否则会停在上一家客户的页码上，看到空页。
  const LIST_PAGE_SIZE = 50
  const [oppPage, setOppPage] = useState(1)
  const [followupPage, setFollowupPage] = useState(1)
  const [quotePage, setQuotePage] = useState(1)
  const [orderPage, setOrderPage] = useState(1)
  useEffect(() => {
    setOppPage(1)
    setFollowupPage(1)
    setQuotePage(1)
    setOrderPage(1)
  }, [customerId])
  // 跟进表七列是这套详情页里最宽的一张表：1440 卡内 1130 放得下，1024 卡内 699 放不下，
  // 不做窄屏分档就会在 1024 弹一条横向滚动条、「附件」只剩半个字（2026-11-19 实拍）。
  // 这里只做"读窗口宽度"，不做路由级适配：改宽度时重算列宽即可。
  const [viewportWidth, setViewportWidth] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth))
  useEffect(() => {
    if (typeof window === 'undefined') return
    const onResize = () => setViewportWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  /* 公海「释放」被履约拦住时那次例外释放，原来用 window.prompt 追问原因 */
  const notePrompt = useNotePrompt()
  const narrowScreen = viewportWidth < 1200
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
  const [transferCustomerId, setTransferCustomerId] = useState<number | null>(null)
  const [followersVisible, setFollowersVisible] = useState(false)
  const [followerToAdd, setFollowerToAdd] = useState<number | undefined>(undefined)
  const transferModal = transferCustomerId === customerId
  const [transferTo, setTransferTo] = useState<SystemUser | null>(null)
  const [transferFrom, setTransferFrom] = useState<{ user_id: number; name: string } | null>(null)
  const [transferSearch, setTransferSearch] = useState('')
  const [transferKeyword, setTransferKeyword] = useState('')
  const [transferReceipt, setTransferReceipt] = useState<{ customerId: number; ownerName: string; documents?: DocumentTransfer } | null>(null)
  useEffect(() => {
    const timer = setTimeout(() => setTransferKeyword(transferSearch.trim()), 250)
    return () => clearTimeout(timer)
  }, [transferSearch])
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

  // 人工选择合并目标仍可用姓名搜索个人客户。
  const mergeCandidatesQuery = useQuery({
    queryKey: ['merge-candidates', mergeKeyword],
    queryFn: () => listCustomers({ keyword: mergeKeyword.trim(), page: 1, page_size: 20 }),
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
  const unresolvedConflicts = (mergePreview?.conflicts ?? []).filter(
    (conflict) => mergePreview?.blocking.includes(conflict.key) &&
      !conflict.options.some((option) => option.value === mergeResolutions[conflict.key]),
  )

  const customerQuery = useQuery({
    queryKey: ['customer', customerId],
    queryFn: () => getCustomer(customerId),
    enabled: Number.isFinite(customerId),
  })
  // 页签标题用客户名，而不是所有客户都叫「客户详情」
  useTabLabel(customerQuery.data?.name)
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
    mutationFn: (owner: SystemUser) => transferCustomer(customerId, {
      owner_id: owner.id,
      from_user_id: transferFrom?.user_id,
    }),
    onSuccess: (result, owner) => {
      setTransferReceipt({ customerId: result.id, ownerName: owner.name, documents: result.document_transfer })
      setTransferCustomerId(null)
      setTransferFrom(null)
      invalidateCustomer()
      for (const key of ['customer-opportunities', 'customer-orders', 'customer-quotes']) {
        void queryClient.invalidateQueries({ queryKey: [key, result.id] })
      }
    },
  })

  const addFollowerMutation = useMutation({
    mutationFn: () => addCustomerFollowers(customerId, [followerToAdd!]),
    onSuccess: () => {
      Toast.success('已添加跟进人')
      setFollowerToAdd(undefined)
      invalidateCustomer()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const removeFollowerMutation = useMutation({
    mutationFn: (userId: number) => removeCustomerFollower(customerId, userId),
    onSuccess: () => {
      Toast.success('已移除跟进人')
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
    onError: async (error: Error, { action }) => {
      // 还在履约中会被后端拦下（返工单 6.3）：提示里已经说清是哪张单拦住的。
      // 这里再加一步引导 —— 主管确需释放就填原因走例外，而不是让人卡住。
      if (action === 'release' && error.message.includes('履约中')) {
        // 先 checkout 后端那句拦截原因，再问"确需例外就写清依据" —— 一句话就够，
        // 不必把整段错误原文再抄进输入框占位
        const reason = await notePrompt.ask({
          title: '例外释放这个客户',
          description: error.message,
          label: '释放原因',
          placeholder: '例如：合同已终止，剩余应收改走线下结算',
          required: true,
          tone: 'warning',
          okText: '确认释放',
        })
        if (reason) {
          poolMutation.mutate({ action: 'release', reason })
          return
        }
        return
      }
      Toast.error(error.message)
    },
  })

  /**
   * 软删客户（03-API §7）：只把客户本人置删，名下联系人/商机/报价/订单/跟进
   * 都保留、不改挂；删完可在回收站恢复。
   *
   * 谁能看到这个按钮：`customer:delete`（主管/管理员，按数据范围）或
   * `customer:delete_own` 且负责人是本人（业务员）。真正的拦截在后端，
   * 前端只负责别把"点了必 403"的按钮摆出来。
   */
  const deleteMutation = useMutation({
    mutationFn: () => deleteCustomer(customerId),
    onSuccess: () => {
      Toast.success('客户已删除')
      // 列表 / 阶段分布 / 工作台都按"未删除"过滤：删完立刻刷掉缓存，
      // 否则退回列表还会看到刚删掉的客户。
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
      void queryClient.invalidateQueries({ queryKey: ['customer-stage-distribution'] })
      void queryClient.invalidateQueries({ queryKey: ['workbench'] })
      navigate('/customers')
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
    queryKey: ['assignable-users', 'customer-transfer', transferKeyword],
    queryFn: () => listUsers({ status: 'active', keyword: transferKeyword, page: 1, page_size: 100 }),
    enabled: transferModal && can('customer:assign'),
  })

  const opportunitiesQuery = useQuery({
    queryKey: ['customer-opportunities', customerId, oppPage],
    queryFn: () => listOpportunities({ customer_id: customerId, page: oppPage, page_size: LIST_PAGE_SIZE }),
    enabled: Number.isFinite(customerId),
  })
  const followupsQuery = useQuery({
    queryKey: ['followups', { customer_id: customerId, page: followupPage }],
    queryFn: () => listFollowups({ customer_id: customerId, page: followupPage, page_size: LIST_PAGE_SIZE }),
    enabled: Number.isFinite(customerId) && activeKey === 'followups' && can('followup:view'),
  })
  const timelineQuery = useQuery({
    queryKey: ['timeline', 'customer', customerId],
    queryFn: () => getCustomerTimeline(customerId),
    enabled: Number.isFinite(customerId) && activeKey === 'logs',
  })

  const quotesQuery = useQuery({
    queryKey: ['customer-quotes', customerId, quotePage],
    queryFn: () => listQuotes({ customer_id: customerId, page: quotePage, page_size: LIST_PAGE_SIZE }),
    // 概览页签也要读它的 total（业务全景四个数字之一），所以 overview 时同样要取
    enabled: Number.isFinite(customerId) && (activeKey === 'quotes' || activeKey === 'overview'),
  })
  const ordersQuery = useQuery({
    queryKey: ['customer-orders', customerId, orderPage],
    queryFn: () => listOrders({ customer_id: customerId, page: orderPage, page_size: LIST_PAGE_SIZE }),
    enabled: Number.isFinite(customerId) && (activeKey === 'orders' || activeKey === 'overview'),
  })

  const customerError = customerQuery.error
  const customerUnavailable = customerError instanceof ApiError && (customerError.status === 403 || customerError.status === 404)
  const customer = customerUnavailable ? undefined : customerQuery.data
  const transferUsers = (usersQuery.data?.items ?? []).filter((row) => row.id !== transferFrom?.user_id)
  if (transferTo && transferTo.id !== transferFrom?.user_id && !transferUsers.some((row) => row.id === transferTo.id)) {
    transferUsers.unshift(transferTo)
  }
  const receipt = transferReceipt?.customerId === customerId ? transferReceipt : null
  const handoverNotice = receipt && (
    <Banner fullMode={false} type={receipt.documents?.skipped_total ? 'warning' : 'success'}
      onClose={() => setTransferReceipt(null)}
      description={<div>
        <strong>已转交给{receipt.ownerName}</strong>
        {receipt.documents && <div>
          {receipt.documents.moved_total > 0
            ? `已交接 ${receipt.documents.moved_total} 项单据或生成文件。`
            : '本次没有需要转交的单据或生成文件。'}
          {receipt.documents.skipped_total > 0 && `另有 ${receipt.documents.skipped_total} 项（${receipt.documents.skipped_labels.join('、')}）已由其他同事接手，保留其归属。`}
        </div>}
      </div>} />
  )

  // 第九批 §9.1：板块权限改由服务端**逐块**判（没权限就不返回、不查）。
  // 这里把"请求失败（多半是没有该模块权限）"与"确实没有数据"分开显示——
  // 否则用户会看到"该客户还没有订单"，把"无权查看"误读成"真的没有"。
  const emptyHint = (failed: boolean, normal: string, error?: unknown) =>
    failed
      ? ((error as Error | undefined)?.message || '无权查看该板块（也可能是加载失败），请确认账号权限')
      : normal

  if (customerQuery.isLoading) {
    return <PageLoading />
  }
  if (!customer) {
    /* 原来是一行裸字挂在整页白底（2026-11-20 巡检）：链接错了或没权限时像页面崩了。 */
    return (
      <div className="page-container">
        {handoverNotice}
        <EmptyState
          title={receipt ? '这位客户已离开你的查看范围' : '客户不存在或无权查看'}
          hint="可能链接里的编号有误，或这条客户属于你数据范围之外的人；回客户中心重新选择即可。"
        />
        <div style={{ display: 'flex', justifyContent: 'center', paddingBottom: 24 }}>
          <Button type="primary" theme="solid" onClick={() => navigate('/customers')}>返回客户中心</Button>
        </div>
      </div>
    )
  }

  const canEditContact = can('customer:update')
  // 删除入口：主管/管理员按数据范围；业务员只能删**只有自己一位跟进人**的客户。
  const followerIds = (customer.followers ?? []).map((item) => item.user_id)
  const canDeleteCustomer =
    can('customer:delete') ||
    (can('customer:delete_own') &&
      followerIds.length === 1 &&
      followerIds[0] === currentUser?.id)
  /* 联系人表在「左表 + 右决策人卡」的窄容器里（1440 时左栏约 830），
     2026-11-19 实拍底部多出一条横向滚动条：列宽是写的，但 auto 布局下
     邮箱那列按内容自然宽度撑开、外加单元格边距，五列合起来超了容器。
     改成 fixed 布局（按写的宽度分配，不再让内容反推），并把手机列从 150
     收到 132 —— 打码后 `138****3633` 只需要 88，展开的 11 位也只要 88。
     1024 再隐掉「职位」：职位在右边决策人卡里本来就写着，不必占一列位置。 */
  const contactColumns = [
    {
      title: '姓名',
      dataIndex: 'name',
      width: 150,
      render: (v: string, row: Contact) => (
        <>
          {v}
          {/* 主联系人一眼可见：决策人卡片、简称、后续对外沟通都以它为准 */}
          {row.is_primary && <Tag color="green" size="small" style={{ marginLeft: 6 }}>主</Tag>}
        </>
      ),
    },
    ...(narrowScreen
      ? []
      : [{ title: '职位', dataIndex: 'title', width: 112, render: (v: string | null) => v ?? '-' }]),
    // 脱敏时**照原样显示**（星号）并给一句话，别让用户以为库里存的就是星号
    {
      title: '手机',
      dataIndex: 'mobile',
      width: 132,
      render: (v: string | null, row: Contact) => <MaskedContact value={v} masked={row.contact_masked} />,
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      render: (v: string | null, row: Contact) => <MaskedContact value={v} kind="email" masked={row.contact_masked} />,
    },
    {
      title: '操作',
      /* 桌面 196：主联系人那行有「设为主联系人 / 编辑 / 删除」三个链接并排，
         实测 84 + 26 + 26 + 两处 10 的间距 = 156，加左右单元格边距 32 → 188，
         留 8 给"主"标签行；窄屏只有「编辑 / 删除」，收到 140。 */
      width: narrowScreen ? 140 : 196,
      render: (_: unknown, row: Contact) =>
        canEditContact ? (
          <span style={{ display: 'inline-flex', gap: 10, alignItems: 'center' }}>
            {!row.is_primary && (
              <button type="button" className="crm-text-action" onClick={() => contactPrimaryMutation.mutate(row.id)}>设为主联系人</button>
            )}
            <button type="button" className="crm-text-action"
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
            </button>
            <Popconfirm
              title="删除这个联系人？"
              content="删除后不再出现在列表与决策人卡片里；历史跟进记录会保留。"
              onConfirm={() => contactDeleteMutation.mutate(row.id)}
            >
              <button type="button" className="crm-text-action" style={{ color: 'var(--crm-error)' }}>删除</button>
            </Popconfirm>
          </span>
        ) : null,
    },
  ]

  return (
    <div className="page-container">
      {notePrompt.node}
      {/* 头部按设计稿排：标题 + 状态标签一行，关键信息行在标题下方左对齐，按钮贴右 */}
      <DetailHeader
        title={customer.name}
        tags={
          <>
            {/* 未分级（level 为空）用描边，和列表页一致：它是「还没评」，
                不是一种等级，实心灰既和 C 级撞脸、又让头部一片脏灰 */}
            <Tag
              color={customer.level === 'A' ? 'green' : customer.level === 'B' ? 'blue' : 'grey'}
              type={customer.level ? 'light' : 'ghost'}
            >
              {customerLevelLabel(customer.level)}
            </Tag>
            <Tag color={customer.pool_status === 'public' ? 'orange' : 'blue'}>
              {customer.pool_status === 'public' ? '公海' : '私海'}
            </Tag>
            {/* 国籍只在「不是中国」时才有信息量：国内客户是绝大多数，
                默认值挂在这儿既是噪音，又和下面「国家 / 地区」字段重复 */}
            {customer.country && customer.country !== '中国' && <Tag>{customer.country}</Tag>}
          </>
        }
        meta={
          <>
            <span>
              跟进人：
              {customer.pool_status === 'public'
                ? '公海'
                : (customer.followers ?? []).map((item) => item.name).join('、') || '-'}
            </span>
            <span>地区：{customer.region ?? '-'}</span>
            <span>来源：{displayCustomerSource(customer.source) || '-'}</span>
            <span>联系人：{customer.contact_count}</span>
          </>
        }
        extra={
          <>
            {/* 台面只留日常最高频的三个动作 */}
            {can('customer:update') && (
              <Button
                type="tertiary"
                onClick={() => {
                  setEditForm({
                    name: customer.name,
                    short_name: customer.short_name ?? '',
                    region: customer.region ?? '',
                    address: customer.address ?? '',
                    tax_no: customer.tax_no ?? '',
                    source: displayCustomerSource(customer.source),
                    level: customer.level ?? null,
                    remark: customer.remark ?? '',
                  })
                  setEditModal(true)
                }}
              >
                编辑客户
              </Button>
            )}
            {can('customer:assign') && (
              <Button type="tertiary" onClick={() => setFollowersVisible(true)}>
                跟进人（{customer.follower_count ?? 0}）
              </Button>
            )}
            {can('followup:create') && (
              // 「记录跟进」是日常最高频的动作，给它实心主色；
              // 公海客户时「领取」才是主动作，这里让位成次要样式。
              <Button
                theme={customer.pool_status === 'public' ? 'light' : 'solid'}
                onClick={() => setFollowupVisible(true)}
              >
                记录跟进
              </Button>
            )}
            {/* 页头原来并着三个白底描边按钮（记录跟进 / 问 Agent / 更多 ▾），
                主次分不出来。问 Agent 是"顺带一问"的次要入口，降成纯文字（2026-11-20）。 */}
            {can('agent:use') && (
              <Button type="tertiary" theme="borderless" onClick={() => navigate(`/agent?context=customer&id=${customerId}`)}>
                问 Agent
              </Button>
            )}
            {/* 公海客户的主动作就是「领取」，不能藏进菜单 */}
            {customer.pool_status === 'public' && (
              <Button type="primary"
                theme="solid"
                loading={poolMutation.isPending}
                onClick={() =>
                  Modal.confirm({
                    title: `确认领取客户「${customer.name}」？`,
                    content: '领取后客户将归到你名下，由你负责跟进。',
                    onOk: () => poolMutation.mutate({ action: 'claim' }),
                  })
                }
              >
                领取到我名下
              </Button>
            )}
            {/* 其余低频 / 危险动作收进「更多」：台面从 9 个降到 4 个。
                危险项仍走确认弹窗 —— 藏进菜单不等于少了二次确认。 */}
            <Dropdown
              trigger="click"
              position="bottomRight"
              render={
                <Dropdown.Menu>
                  {can('customer:update') && (
                    <Dropdown.Item
                      onClick={() => {
                        setRating(customerLevelValue(customer.level))
                        setRatingModal(true)
                      }}
                    >
                      {customer.level ? '重新评级' : '评定等级'}
                    </Dropdown.Item>
                  )}
                  {can('customer:assign') && (
                    <Dropdown.Item
                      disabled={duplicateCaseMutation.isPending}
                      onClick={() => duplicateCaseMutation.mutate()}
                    >
                      撞单检查
                    </Dropdown.Item>
                  )}
                  {can('customer:update') && (
                    <Dropdown.Item
                      onClick={() => {
                        setMergeTarget(null)
                        setMergeKeyword('')
                        setMergeReason('')
                        setMergeModal(true)
                      }}
                    >
                      合并到其他客户
                    </Dropdown.Item>
                  )}
                  {(can('customer:assign') || canDeleteCustomer) && <Dropdown.Divider />}
                  {can('customer:assign') && customer.pool_status !== 'public' && (
                    <Dropdown.Item
                      type="danger"
                      onClick={() =>
                        Modal.confirm({
                          title: '放入公海后跟进人会全部清空，确认？',
                          onOk: () => poolMutation.mutate({ action: 'release' }),
                        })
                      }
                    >
                      放入公海
                    </Dropdown.Item>
                  )}
                  {canDeleteCustomer && (
                    <Dropdown.Item
                      type="danger"
                      onClick={() =>
                        Modal.confirm({
                          title: `删除客户「${customer.name}」？`,
                          content:
                            '删除后客户从列表和详情中消失，名下的联系人、商机、报价、订单等资料会保留；可在回收站恢复。',
                          okText: '删除',
                          okButtonProps: { type: 'danger' },
                          onOk: () => deleteMutation.mutate(),
                        })
                      }
                    >
                      删除客户
                    </Dropdown.Item>
                  )}
                </Dropdown.Menu>
              }
            >
              {/* 更多 ▾：低频动作的收纳盒，同样不该与主动作抢描边 */}
              <Button icon={<IconMore />} type="tertiary" theme="borderless" style={{ color: 'var(--crm-primary)' }}>
                更多
              </Button>
            </Dropdown>
          </>
        }
      />

      {handoverNotice}
      <Modal
        width={520}
        title="撞单检查结果"
        visible={duplicateCaseCount > 0}
        okText="查看撞单裁定"
        cancelText="关闭"
        onCancel={() => setDuplicateCaseCount(0)}
        onOk={() => {
          setDuplicateCaseCount(0)
          navigate(`/duplicate-cases?customer_id=${customerId}`)
        }}
      >
        <p>该客户有 {duplicateCaseCount} 条疑似撞单待裁定。重复检查会复用已有的待裁定记录。</p>
        <p>客户归属保持原样，由有权限的人员核对证据后裁定。</p>
      </Modal>

      <SectionCard>
        <Tabs
          type="line"
          activeKey={activeKey}
          onChange={openTab}
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
              {/* 可空字段一律 hideIfEmpty：空值不占格子，有内容的字段才显眼。
                  客户名称 / 客户类型 / 国家地区是「总该有值」的，不加这个开关。 */}
              <Field label="客户简称" value={customer.short_name ?? '-'} hideIfEmpty />
              <Field label="客户类型" value={customer.customer_type ?? '-'} />
              {/* 来源原来只在头部信息行里，概览字段看不到；新建/编辑填了来源后
                  用户找不到落点，所以在概览里补一个固定字段。 */}
              <Field label="客户来源" value={displayCustomerSource(customer.source) || '-'} />
              <Field label="国家 / 地区" value={`${customer.country ?? '-'} / ${customer.region ?? '-'}`} />
              <Field label="统一社会信用代码" value={customer.tax_no ?? '-'} hideIfEmpty />
              <Field label="官网域名" value={customer.domain ?? '-'} hideIfEmpty />
              <Field label="详细地址" value={customer.address ?? '-'} hideIfEmpty />
              {/* 三个时钟分开显示（文档 §2.3）：联系过 ≠ 业务有进展 ≠ 约好了下次。
                  合成一个时间字段就回答不了"到底哪一样断了" */}
              <Field
                label="最近跟进"
                hideIfEmpty
                value={
                  customer.last_followup_at
                    ? formatBeijingTime(customer.last_followup_at)
                    : '-'
                }
              />
              <Field
                label="最近业务进展"
                hideIfEmpty
                value={
                  customer.last_progress_at
                    ? formatBeijingTime(customer.last_progress_at)
                    : '-'
                }
              />
              <Field
                label="约定下次跟进"
                hideIfEmpty
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
              <Field label="备注" value={customer.remark ?? '-'} hideIfEmpty />
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
                      // 灰底色块一多，整块区域就糊成一片（主人反复反馈过色块泛滥）。
                      // 用描边款：只留一圈细线，文字退到正文色，靠形状而不是底色区分。
                      className="tag-plain"
                      closable={can('customer:update')}
                      onClose={() => detachTagMutation.mutate(tag.id)}
                    >
                      {tag.name}
                    </Tag>
                  ))}
                  {can('customer:update') && (
                    <Button
                      size="small"
                      type="tertiary"
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

              {/* 业务全景：原来概览里只有七八条静态字段，而这家客户其实有 5 个商机、
                  3 份报价、2 张订单 —— 全藏在别的页签里，一进详情看不出它走到哪一步，
                  卡片又矮，页面下半截空着一大片（2026-11-17 主人选了"把空白换成信息"）。
                  四个数字各自可点，点一下直接跳到对应页签。 */}
              <div style={{ gridColumn: '1 / -1', borderTop: '1px solid var(--crm-border)', paddingTop: 14 }}>
                <div className="customer-info-head">
                  <span className="customer-info-icon" style={{ background: '#eaf1fe', color: '#2f6df6' }}>
                    <IconHistogram size="small" />
                  </span>
                  业务全景
                  <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 400, color: 'var(--crm-text-3)' }}>
                    点数字直接跳到对应页签
                  </span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
                  {/* 四张数字卡各给一个色带（蓝=联系人 / 紫=商机 / 橙=报价 / 绿=订单）：
                      以前四块全是灰底，主人说"全是蓝白，太单调"。颜色只落在左边缘
                      一条细线 + 极淡底，不铺满整块（那种"彩色块"上一版被否过）。 */}
                  {([
                    { key: 'contacts', label: '联系人', value: (contactsQuery.data ?? []).length, tone: 'tone-primary' },
                    { key: 'opportunities', label: '商机', value: opportunitiesQuery.data?.total, tone: 'tone-secondary' },
                    { key: 'quotes', label: '报价', value: quotesQuery.data?.total, tone: 'tone-caution' },
                    { key: 'orders', label: '订单', value: ordersQuery.data?.total, tone: 'tone-success' },
                  ] as const).map((tile) => {
                    const jump = () => openTab(tile.key)
                    return (
                      <button
                        key={tile.key}
                        type="button"
                        className={`cust-pano-tile ${tile.value ? tile.tone : 'tone-neutral'}`}
                        onClick={jump}
                        title={`跳到「${tile.label}」页签`}
                      >
                        <span className="cust-pano-label">{tile.label}</span>
                        <span className="cust-pano-value">
                          {/* 查不到就是查不到：没权限时接口会 403，这里显示「—」
                              而不是 0 —— 0 会被读成"这家客户没有订单"。 */}
                          {tile.value === undefined || tile.value === null ? '-' : tile.value}
                          {tile.value !== undefined && tile.value !== null && (
                            <span className="cust-pano-unit">条</span>
                          )}
                        </span>
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* 合同文档：和上面那些字段同属「这家客户的基础事实」，所以并进同一张卡，
                  用一条分隔线隔开。原来是独立一张卡，空态只有一句话却占 100px 高。 */}
              <div style={{ gridColumn: '1 / -1', borderTop: '1px solid var(--crm-border)', paddingTop: 14 }}>
                <div className="customer-info-head">
                  <span className="customer-info-icon" style={{ background: '#e6f6f2', color: '#0d9488' }}>
                    <IconFile size="small" />
                  </span>
                  合同文档
                </div>
                <ContractDocsPanel
                  customerId={customerId}
                  boxed={false}
                  empty="这家客户还没有合同文档——去合同台账上传合同"
                />
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
                      theme="outline"
                      type="primary"
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
                  /* fixed + 上面那组写死的列宽：列不会再被长邮箱内容撑开 */
                  tableLayout="fixed"
                  /* 邮箱列没有固定宽 —— fixed 布局下它拿剩下的空间，长邮箱这里截断。 */

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
            <>
            {can('opportunity:view') && can('opportunity:manage') && (
              <div className="workflow-next">
                <strong>客户有新的采购需求？</strong>
                <p className="workflow-guide">为这次采购建一条商机，后续需求商品、报价和订单可以在同一处查看。</p>
                <Button type="primary" theme="outline" onClick={() => navigate(`/opportunities?new_for_customer=${customerId}`)}>
                  为该客户新建商机
                </Button>
              </div>
            )}
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
                  className: 'cell-money',
                  render: (value: number | null, record: Opportunity) =>
                    formatMoney(record.expected_amount_exact ?? value, record.currency),
                },
                { title: '预计成交日', dataIndex: 'expected_close_date', width: 130, render: (v: string | null) => v ?? '-' },
                { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' },
              ]}
              dataSource={opportunitiesQuery.data?.items ?? []}
              loading={opportunitiesQuery.isLoading}
              rowKey="id"
              pagination={{
                currentPage: oppPage,
                pageSize: LIST_PAGE_SIZE,
                total: opportunitiesQuery.data?.total ?? 0,
                showSizeChanger: false,
                onPageChange: (nextPage: number) => setOppPage(nextPage),
              }}
              empty={emptyHint(opportunitiesQuery.isError, '该客户还没有商机', opportunitiesQuery.error)}
            />
            </>
          )}

          {activeKey === 'followups' && (
            <>
              <div className="toolbar">
                <div style={{ flex: 1 }} />
                <GuardedButton
                  disabled={!can('followup:create')}
                  blockedReasons={['需要「记录跟进」权限。']}
                  onClick={() => setFollowupVisible(true)}
                >记录跟进</GuardedButton>
              </div>
              <Table<FollowUp>
                /* 这张表列多、每列内容又长，Semi 默认 table-layout:auto 会按内容把
                   列撑开（1024 实测 714 > 卡内 699，多出一条横向滚动条）。
                   用 .followup-table 强制 fixed，列宽才真的按我们写的值走。 */
                className="followup-table"
                columns={[
                  {
                    title: '跟进时间',
                    dataIndex: 'created_at',
                    /* 1024：时间列内容本身就是 149 宽（nowrap 才不截断），其余列收窄，
                       并隐藏「记录时约定」—— 七列硬塞进 699 只会全变省略号。
                       列宽之和压到 149+56+130+130+94+60 = 630；实测单元格边距会给每列再
                       加 6~28px，所以按 690 而不是 699 收（714 时仍会多出 15px 横向滚动条）。 */
                    width: narrowScreen ? 149 : 146,
                    render: (v: string) => formatBeijingTime(v),
                  },
                  {
                    title: '方式',
                    dataIndex: 'followup_type',
                    width: 80,
                    /* 窄屏整列隐藏：六列实测仍需 714（单元格边距让每列比设定宽 6~28），
                       699 装不下；「方式」「记录时约定」是附加元信息，先让位。 */
                    className: 'followup-col-minor',
                    onHeaderCell: () => ({ className: 'followup-col-minor' }),
                    /* 方式给底色（电话蓝 / 微信绿 / 拜访紫 / 邮件橙）：整列原本都是黑字，
                       一排跟进记录看起来"灰扑扑"，分类信息用颜色标出来才好扫。 */
                    render: (v: string | null) => {
                      if (!v) return '-'
                      const tone = /电话|call/i.test(v)
                        ? 'blue'
                        : /微信|wechat|企微/i.test(v)
                          ? 'green'
                          : /拜访|上门|见面|visit/i.test(v)
                            ? 'purple'
                            : /邮件|mail/i.test(v)
                              ? 'orange'
                              : 'grey'
                      return <Tag size="small" color={tone} type="light">{v}</Tag>
                    },
                  },
                  /* 七列全部定宽：原来内容/客户反馈/下一步都不给宽度，按长文本撑开，
                     表宽 1221 > 卡内 1130，底部多出一条横向滚动条、「附件」只剩半个字。
                     现在 146+80+300+190+150+150+80 = 1096，表格 width:100% 会自己撑满剩余空间。
                     （内容列写 width:'100%' 反而会被 Semi 当成额外整宽 + 固定列，仍会溢出。） */
                  /* 只写 width 不够：表格是 table-layout:auto，长文本会把 300 撑到 370，
                     表宽 1171 仍超卡内 1130。单元格显式允许折行，列宽才真的守住。 */
                  {
                    title: '内容',
                    dataIndex: 'content',
                    width: narrowScreen ? 130 : 300,
                    onCell: () => ({ style: { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } }),
                  },
                  {
                    title: '客户反馈',
                    dataIndex: 'customer_feedback',
                    width: narrowScreen ? 130 : 190,
                    onCell: () => ({ style: narrowScreen ? { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } : { whiteSpace: 'nowrap' as const } }),
                    render: (v: string | null) => v ?? '-',
                  },
                  {
                    title: '下一步',
                    dataIndex: 'next_action',
                    width: narrowScreen ? 94 : 150,
                    onCell: () => ({ style: narrowScreen ? { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } : { whiteSpace: 'nowrap' as const } }),
                    render: (v: string | null, row: FollowUp) => v ?? (({ customer_declined: '客户明确拒绝', business_closed: '业务已关闭', waiting_external: '等待外部固定节点' } as Record<string, string>)[row.exemption_reason ?? ''] ?? '-'),
                  },
                  {
                    title: '记录时约定',
                    dataIndex: 'task_due_at',
                    width: 150,
                    /* 窄屏整列隐藏（.followup-col-due 见 index.css）：七列塞进 699 只会全部变省略号 */
                    className: 'followup-col-due',
                    onHeaderCell: () => ({ className: 'followup-col-due' }),
                    render: (v: string | null) => v ? formatBeijingTime(v) : '-',
                  },
                  {
                    title: '附件',
                    width: narrowScreen ? 60 : 80,
                    render: (_: unknown, record: FollowUp) => (
                      <FollowUpAttachmentsButton followupId={record.id} />
                    ),
                  },
                ]}
                dataSource={can('followup:view') ? followupsQuery.data?.items ?? [] : []}
                loading={followupsQuery.isLoading}
                rowKey="id"
                pagination={can('followup:view') ? {
                  currentPage: followupPage,
                  pageSize: LIST_PAGE_SIZE,
                  total: followupsQuery.data?.total ?? 0,
                  showSizeChanger: false,
                  onPageChange: (nextPage: number) => setFollowupPage(nextPage),
                } : false}
                empty={can('followup:view')
                  ? emptyHint(followupsQuery.isError, '还没有跟进记录', followupsQuery.error)
                  : '需要「查看跟进」权限才能查看已有记录。'}
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
                  className: 'cell-right',
                  render: (v: number | null, record: Quote) => formatMoney(record.current_version_amount_exact ?? v, record.currency),
                },
                { title: '状态', dataIndex: 'status_label', width: 110 },
                { title: '有效期', dataIndex: 'valid_until', width: 120, render: (v: string | null) => v ?? '-' },
              ]}
              dataSource={quotesQuery.data?.items ?? []}
              loading={quotesQuery.isLoading}
              rowKey="id"
              pagination={{
                currentPage: quotePage,
                pageSize: LIST_PAGE_SIZE,
                total: quotesQuery.data?.total ?? 0,
                showSizeChanger: false,
                onPageChange: (nextPage: number) => setQuotePage(nextPage),
              }}
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
                  className: 'cell-right',
                  render: (v: number, record: Order) => formatMoney(record.total_amount_exact ?? v, record.currency),
                },
                {
                  title: (
                    <span className="crm-help-label">
                      已回款
                      <HelpTip forLabel="已回款" text="这张订单已经确认到账的金额。客户分批付款时，它会小于订单金额。" />
                    </span>
                  ),
                  dataIndex: 'received_amount',
                  width: 130,
                  className: 'cell-money',
                  render: (v: number, record: Order) => formatMoney(record.received_amount_exact ?? v, record.currency),
                },
                {
                  title: (
                    <span className="crm-help-label">
                      履约状态
                      <HelpTip forLabel="履约状态" text="订单从备货到发货、完成的进度，由实际业务动作推进，不用手改。" />
                    </span>
                  ),
                  dataIndex: 'status_label',
                  width: 120,
                },
                { title: '交期', dataIndex: 'delivery_date', width: 120, render: (v: string | null) => v ?? '-' },
              ]}
              dataSource={ordersQuery.data?.items ?? []}
              loading={ordersQuery.isLoading}
              rowKey="id"
              pagination={{
                currentPage: orderPage,
                pageSize: LIST_PAGE_SIZE,
                total: ordersQuery.data?.total ?? 0,
                showSizeChanger: false,
                onPageChange: (nextPage: number) => setOrderPage(nextPage),
              }}
              empty={emptyHint(ordersQuery.isError, '该客户还没有订单', ordersQuery.error)}
            />
          )}

          {activeKey === 'files' && (
            <AttachmentPanel businessType="customer" businessId={customerId} />
          )}
        </div>
      </SectionCard>

      {activeKey === 'overview' && can('agent:use') && (
        <SectionCard
          title={
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <span className="customer-info-icon" style={{ background: '#efe9fd', color: '#7a4fd3' }}>
                <IconAIEditLevel1 size="small" />
              </span>
              AI 分析
            </span>
          }
          // 去掉原来的紫色斜向渐变 + 紫色左边条（2026-11-17）：
          // 全站主色是蓝，一块淡紫铺在灰白页面里像另一个系统的模块；
          // 而且这张卡多数时候只有一行空态文案，不值得用"特殊皮肤"强调。
          // 现在与普通卡片同底色，靠标题前那个小图标做识别。
          style={{ marginTop: 16 }}
          extra={
            <>
              {/* 2026-11-20 二稿：一稿把「客户总结」做成白底描边、把「跟进建议」降成文字，
                  结果变成"一个带框、一个光着"（主人贴图问"为什么单独给客户总结加个框"）。
                  根子是我照搬了「一行只留一个主按钮」那条规则 —— 但这两个动作是**平级**的：
                  都是"点一下看一眼 AI 分析"，没有谁更主要。硬分主次，等于给界面编了个
                  不存在的层级。现在两个都是文字按钮，和商机详情那三个「运行」一致。 */}
              <Button
                size="small"
                type="tertiary"
                theme="borderless"
                loading={aiMutation.isPending}
                onClick={() => aiMutation.mutate('summary')}
              >
                客户总结
              </Button>
              {/* ⚠ 只写 theme="borderless" 不够：Semi 的 Button 不写 type 时默认
                  type="primary"，borderless + primary 仍然是浅蓝底药丸 —— 这就是
                  截图里两个按钮"一个白底一个浅蓝"的原因。必须显式 tertiary。 */}
              <Button
                size="small"
                type="tertiary"
                theme="borderless"
                loading={aiMutation.isPending}
                onClick={() => aiMutation.mutate('followup')}
              >
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
        width={680}
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
              '要改联系方式：请让客户跟进人本人操作，或请管理员为你开通「客户联系方式完整查看」权限后重试。',
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
                      {match.mobile && !match.contact_masked ? (
                        <>
                          {' · '}
                          <MaskedContact value={match.mobile} />
                        </>
                      ) : (
                        ''
                      )}
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

      <Modal
        width={520} title="客户评级" visible={ratingModal} onCancel={() => setRatingModal(false)}
        onOk={() => ratingMutation.mutate()} confirmLoading={ratingMutation.isPending}>
        <div style={{ marginBottom: 12 }}>了解客户后再评级；也可以恢复为未分级。评级会影响后续等级定价和管理规则。</div>
        <Select value={rating} onChange={(value) => setRating(String(value))} style={{ width: '100%' }}
          optionList={[UNRATED_OPTION, ...(customerLevelsQuery.data ?? []).map((item) => ({ value: item.code, label: item.label }))]} />
      </Modal>

      <Modal
        width={520}
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
            <div style={{ marginBottom: 4 }}>统一社会信用代码</div>
            <Input
              value={editForm.tax_no ?? ''}
              placeholder="例：91440300708461136T"
              onChange={(v) => setEditForm({ ...editForm, tax_no: v })}
            />
            <div style={{ marginTop: 4, fontSize: 12, color: 'var(--crm-text-3)' }}>
              18 位统一社会信用代码（营业执照右上角，最后一位是校验码）；个人、境外客户可留空
            </div>
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
        title="转交跟进人"
        className="workflow-scroll-modal"
        width="min(480px, calc(100vw - 24px))"
        visible={transferModal}
        onCancel={() => { if (!transferMutation.isPending) { setTransferCustomerId(null); setTransferFrom(null) } }}
        onOk={() => {
          if (transferTo && !transferMutation.isPending) {
            transferMutation.mutate(transferTo)
          }
        }}
        okButtonProps={{ disabled: !transferTo }}
        confirmLoading={transferMutation.isPending}
        okText="确认转交"
      >
        <div className="customer-handover">
          <div className="customer-handover-context">
            <strong>{customer.name}</strong>
            <div>转交 {transferFrom?.name ?? '我'} 的跟进</div>
          </div>
          <div>
            <FormLabel required>接手人</FormLabel>
            <Select
              aria-label="接手人"
              placeholder="输入姓名或登录名查找在职员工"
              value={transferTo?.id}
              onChange={(value) => setTransferTo(transferUsers.find((row) => row.id === value) ?? null)}
              disabled={transferMutation.isPending}
              filter
              remote
              onSearch={setTransferSearch}
              optionList={transferUsers.map((row) => ({
                value: row.id,
                label: `${row.name}（${row.username}） · ${row.department ?? '未分配部门'}`,
              }))}
              loading={usersQuery.isFetching}
              style={{ width: '100%' }}
            />
            {usersQuery.isError && <div role="alert">
              暂时无法读取员工。<Button size="small" theme="borderless" onClick={() => void usersQuery.refetch()}>重新读取</Button>
            </div>}
            {usersQuery.isSuccess && transferUsers.length === 0 && <div>没有符合条件的在职员工。</div>}
          </div>
          <div className="customer-handover-context">
            <strong>随客户交接</strong>
            <div>{transferFrom?.name ?? '你'} 名下未完成的待办、商机、报价、打样、订单和生成文件会跟着交给接手人。</div>
            <div>其他同事负责的单据、已完成的待办和历史业绩归属保留。</div>
          </div>
          {transferMutation.isError && <Banner type="warning" fullMode={false} closeIcon={null} description={transferMutation.error.message} />}
        </div>
      </Modal>

      {/* 添加标签 */}
      <Modal
        width={520}
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
        okButtonProps={{ disabled: !mergeTarget || !mergePreviewQuery.isSuccess || unresolvedConflicts.length > 0 }}
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
              (mergeCandidatesQuery.data?.items ?? []).filter((m) => m.id !== customerId)
                .length === 0 && (
                <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                  没有找到匹配的客户，换个关键词试试。
                </div>
              )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(mergeCandidatesQuery.data?.items ?? [])
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
                      {[match.customer_type, match.region, match.address].filter(Boolean).join(' · ')}
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
                        {conflict.options.length > 0 && <Select
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
                        />}
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

      <Modal
        title="跟进人"
        visible={followersVisible}
        onCancel={() => setFollowersVisible(false)}
        footer={<Button onClick={() => setFollowersVisible(false)}>关闭</Button>}
        width={520}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            所有跟进人都能查看客户、记录跟进；客户没有默认负责人。
          </div>
          {(customer.followers ?? []).map((follower) => (
            <div key={follower.user_id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>{follower.name}</span>
              {can('customer:assign') && (
                <Button size="small" theme="borderless"
                  onClick={() => {
                    setTransferFrom({ user_id: follower.user_id, name: follower.name })
                    setTransferTo(null)
                    setTransferSearch('')
                    setTransferKeyword('')
                    transferMutation.reset()
                    setTransferCustomerId(customerId)
                  }}>
                  转交
                </Button>
              )}
              {can('customer:assign') && (
                <Button size="small" theme="borderless" type="danger"
                  loading={removeFollowerMutation.isPending}
                  onClick={() => removeFollowerMutation.mutate(follower.user_id)}>
                  移除
                </Button>
              )}
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
            <UserSearchSelect
              queryScope="customer-follower"
              enabled={followersVisible}
              placeholder="搜索同事添加为跟进人"
              value={followerToAdd}
              onChange={(value) => setFollowerToAdd(value)}
              style={{ flex: 1 }}
            />
            <Button
              theme="solid"
              disabled={!followerToAdd}
              loading={addFollowerMutation.isPending}
              onClick={() => addFollowerMutation.mutate()}
            >
              添加
            </Button>
          </div>
        </div>
      </Modal>

      <FollowUpModal
        key={customerId}
        visible={followupVisible}
        onClose={() => setFollowupVisible(false)}
        target={{ customerId }}
        onCreated={invalidateCustomer}
      />
    </div>
  )
}
