import { showDownloadResult } from '../../shared/download-feedback'
import { requiredQuantityText } from '../../shared/number-input'
import { IconEditStroked, IconExport, IconFile, IconMail } from '@douyinfe/semi-icons'
import { formatMoney, formatUnitPrice, toTwoDecimals } from '../../shared/components/money'
import { skuLabel } from '../../shared/sku-label'
import { formatBeijingDate, formatBeijingTime } from '../../shared/beijing-time'
import { useTabLabel } from '../../shared/hooks/useTabLabel'
import { cloneElement, useState, type ReactElement } from 'react'
import SampleFromSourceModal from '../sample/SampleFromSourceModal'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import FormLabel from '../../shared/components/FormLabel'
import HelpTip from '../../shared/components/HelpTip'
import TermHelp from '../../shared/components/TermHelp'
import { ConfirmRequiredModal, isConfirmRequired, confirmMessageOf } from '../../shared/components/ConfirmRequiredModal'
import BlockedHint from '../../shared/components/BlockedHint'
import StatusDot from '../../shared/components/StatusDot'
import GuardedButton from '../../shared/components/GuardedButton'
import {
  SECONDARY_ACTIONS,
  STAGE_LABEL,
  currentStage,
  quoteActions,
  quoteTodos,
  stageProgress,
  type QuoteTodo,
} from '../../shared/quote-todo'
import ReceivablePlanPicker, {
  defaultPlanRows,
  planBlockedReasons,
  toPlanPayload,
  type ReceivablePlanRow,
} from '../../shared/components/ReceivablePlanPicker'
import { Button, Collapse, Dropdown, Input, Modal, Popconfirm, Select, Table, Tag, TextArea, Toast } from '@douyinfe/semi-ui'

import {
  linkMasterData,
  getQuoteMasterRefreshPreview,
  acceptQuote,
  addQuoteCharge,
  createQuoteVersion,
  declineQuote,
  deleteQuote,
  voidQuote,
  deleteQuoteCharge,
  downloadQuotePdf,
  getPriceDrift,
  getQuoteVersion,
  listQuoteVersions,
  markSent,
  refreshPrices,
  refreshQuoteCosts,
  setQuoteVersionItems,
  submitApproval,
  updateQuoteCharge,
  updateQuoteItem,
  updateQuoteVersion,
  withdrawApproval,
  type QuoteChargeRow,
  type QuoteVersion,
  type QuoteItemRow,
} from '../../shared/api/quote'
import { listContacts } from '../../shared/api/customer'
import { useAuthStore } from '../../shared/store/auth'
import { listCustomInquiries, type CustomInquiryRow } from '../../shared/api/inquiry'
import { listInquiryApprovals } from '../../shared/api/dingtalk'
import AutoOaSync from '../../shared/components/AutoOaSync'
import { approvalProgress } from '../knowledge/approval-progress'
import SyncOaButton from '../../shared/components/SyncOaButton'
import { listSkusForPricing } from '../../shared/api/pricing'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import { convertToOrder } from '../../shared/api/order'
import DetailHeader from '../../shared/components/DetailHeader'
import SectionCard from '../../shared/components/SectionCard'
import ContractDocsPanel from '../../shared/components/ContractDocsPanel'
import QuoteSendLogsPanel from './QuoteSendLogsPanel'
import BizDocPanel from '../../shared/components/BizDocPanel'
import EmptyState from '../../shared/components/EmptyState'
import PageLoading from '../../shared/components/PageLoading'
import WhatIfPanel from './WhatIfPanel'
import DecisionMakerCard from '../common/DecisionMakerCard'
import { optionMatcher } from '../../shared/components/optionMatch'
import RecordSearchSelect from '../../shared/components/RecordSearchSelect'
import { otherOption } from '../../shared/components/otherOption'
import {
  orderedChannels,
  rememberChannel,
  rememberedChannel,
  sendLogsQueryKey,
} from './sendChannel'

const STATUS_TONE: Record<string, TagTone> = {
  draft: 'grey',
  pending_approval: 'orange',
  approved: 'blue',
  sent: 'cyan',
  accepted: 'green',
  declined: 'red',
  approval_rejected: 'red',
  voided: 'grey',
}

const PAYMENT_TERMS_MAX_LENGTH = 128

/**
 * 收件人下拉里"不用联系人、手动填"这一项的哨兵值。
 *
 * 用一个不可能等于真实姓名的串（`\u0000` 开头）而不是「其他」两个字：
 * 客户真的有个联系人叫"其他"时，选人不会被误判成"手动填写"。
 */
const MANUAL_RECEIVER = '\u0000manual'

const CHARGE_TYPES = [
  { value: 'logistics', label: '物流' },
  { value: 'packaging', label: '包装' },
  { value: 'tax', label: '税费' },
  { value: 'service', label: '服务费' },
  { value: 'discount', label: '折扣' },
  { value: 'other', label: '其他' },
]

/**
 * 附加费用金额的输入校验 —— **新增与修改共用这一份**（别各写一遍）。
 *
 * ⚠️ 不能写 `Number(raw || 0)`：空串会被静默转成 0，而 0 元在运费上意味着
 * "明确确认零运费"，于是一条"还没填金额"的费用会以 0 元落库、还被当成已确认，
 * 正式发送也放行。所以空输入必须显式拦住。
 */
function parseChargeAmount(raw: string, isDiscount: boolean): string {
  if (!raw) {
    throw new Error(
      isDiscount
        ? '请填写优惠金额（空输入不等于 0 元）'
        : '请填写金额；确属零运费也要显式填 0，不能留空当作已确认',
    )
  }
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) {
    throw new Error('金额必须是非负数字，最多两位小数')
  }
  return raw
}

/**
 * 批量录入的一行草稿。现货给 sku_id；定制件给 inquiry_id + 成本 + 报价
 * （定制件没有 SKU，也就没有价格规则可用，成本和报价必须人工给）。
 */
type ItemDraftRow = {
  mode: 'sku' | 'custom'
  sku_id?: number
  inquiry_id?: number
  quantity: string
  quoted_price: string
  unit_cost: string
  remark: string
}

export default function QuoteDetailPage() {
  const params = useParams()
  const quoteId = Number(params.id)
  // What-if 折叠区是否展开（折叠触发器是文字键，需要状态）
  const [whatifOpen, setWhatifOpen] = useState<string[]>([])
  // 报价明细哪几行展开了「核算依据」：触发器挪进「操作」列，展开状态由本页管理
  // （Semi 自带的图标位只有十几像素宽，塞不进「展开 / 收起」两个字，实拍会被切成一条）
  const [expandedItemIds, setExpandedItemIds] = useState<(string | number)[]>([])
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const currentUser = useAuthStore((state) => state.user)
  const { can } = usePermissions()
  const canManage = can('quote:manage')
  const [paymentTermsDraft, setPaymentTermsDraft] = useState<{ versionId: number; text: string } | null>(null)
  const [flashPaymentTerms, setFlashPaymentTerms] = useState(false)
  const savePaymentTerms = useMutation({
    mutationFn: ({ versionId, text }: { versionId: number; text: string }) =>
      updateQuoteVersion(versionId, { payment_terms: text }),
    onSuccess: (_, { versionId }) => {
      setPaymentTermsDraft(null)
      // 改完这一行会重绘成新文案，亮一下让"改的是这里"有落点
      setFlashPaymentTerms(true)
      window.setTimeout(() => setFlashPaymentTerms(false), 1500)
      void queryClient.invalidateQueries({ queryKey: ['quote-version', versionId] })
      void queryClient.invalidateQueries({ queryKey: ['quote-versions', quoteId] })
      Toast.success('付款条件已保存')
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const [deleteVisible, setDeleteVisible] = useState(false)
  const deleteMutation = useMutation({
    mutationFn: () => deleteQuote(quoteId),
    onSuccess: () => {
      setDeleteVisible(false)
      Toast.success('报价单已删除')
      void queryClient.invalidateQueries({ queryKey: ['quotes'] })
      void queryClient.invalidateQueries({ queryKey: ['customer-quotes'] })
      navigate('/quotes', { replace: true })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const [voidVisible, setVoidVisible] = useState(false)
  const [voidReason, setVoidReason] = useState('')
  const voidMutation = useMutation({
    mutationFn: () => voidQuote(quoteId, voidReason.trim()),
    onSuccess: () => {
      setVoidVisible(false)
      setVoidReason('')
      Toast.success('报价已作废（记录保留）')
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['quotes'] })
      void queryClient.invalidateQueries({ queryKey: ['customer-quotes'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const versionsQuery = useQuery({
    queryKey: ['quote-versions', quoteId],
    queryFn: () => listQuoteVersions(quoteId),
    enabled: Number.isFinite(quoteId),
  })

  const requestedVersionId = searchParams.get('version') ? Number(searchParams.get('version')) : null
  /**
   * 这份报价的**所有**版本 id。
   *
   * 面板与"登记成功后失效缓存"必须用**同一个数组** —— 失效的键是按这个列表拼的，
   * 两处算法不一致（一处过滤、一处不过滤）就又会失效不到。
   */
  const allVersionIds = (versionsQuery.data ?? []).map((item) => item.id)
  const versionId = requestedVersionId !== null && allVersionIds.includes(requestedVersionId)
    ? requestedVersionId : allVersionIds[0]

  const detailQuery = useQuery({
    queryKey: ['quote-version', versionId],
    queryFn: () => getQuoteVersion(versionId!),
    enabled: Boolean(versionId),
  })
  // 页签标题用报价单号，避免多个「报价详情」分不清
  useTabLabel(detailQuery.data?.quote?.quote_no)

  const [draftVisible, setDraftVisible] = useState(false)
  const [sampleVisible, setSampleVisible] = useState(false)
  const [priceTarget, setPriceTarget] = useState<QuoteItemRow | null>(null)
  const [newPrice, setNewPrice] = useState('')
  const [chargeVisible, setChargeVisible] = useState(false)
  const [chargeForm, setChargeForm] = useState({ charge_type: 'logistics', description: '', amount: '' })
  /**
   * 正在改的那条费用（`null` = 没在改）。
   *
   * 为什么要单独一个：锁版之后「附加费用」里只有运费还能动，而从前那一列整个挂在
   * `editable` 上 —— 已审批通过的版本连「改」「删」都看不到，只剩「补填运费」一个
   * 按钮。用户想录真实运费就**只能再加一条**，而多条运费会被 `recalc_version`
   * 累加进总额（2026-10-14 那张「订单 501 / 报价 581」就是这么来的）。
   */
  const [editCharge, setEditCharge] = useState<QuoteChargeRow | null>(null)
  const [editChargeForm, setEditChargeForm] = useState({ description: '', amount: '' })
  const [sendVisible, setSendVisible] = useState(false)
  /**
   * 发送渠道默认值：**这个人上次用过的那个**（主人 2026-10-12 拍板）。
   *
   * 原来写死 `'邮件'`，于是"我连着 20 单都走企业微信"的人每单都要重选一次 ——
   * 默认值在教用户做错事。这里先读本地记忆，没有才用兜底值。
   */
  const [sendForm, setSendForm] = useState(() => ({
    channel: rememberedChannel(currentUser?.id),
    receiver: '',
    request_key: '',
  }))
  /** 收件人留空时的提示（必填，但点确认才提示，别一打开就红一片）。 */
  const [sendTouched, setSendTouched] = useState(false)
  /**
   * 收件人是不是"手动填写"这一支。
   *
   * ⚠️ **用显式状态，不要从值反推**。我第一版按"值不在联系人列表里"来判，
   * 结果**刚打开弹窗就露出一个空输入框**（那时的值就是空的、也不在任何列表里）——
   * 看起来像"默认要你手填"。显式状态才能区分"还没选"和"选了手动填写"。
   */
  const [manualReceiver, setManualReceiver] = useState(false)

  // 发送渠道的「其他」：选中后多一个输入框写具体渠道（不填就保持「其他」），
  // 写的内容直接存进 channel。选中即记住 —— 下次开这个弹窗就是它。
  const channelOption = otherOption({
    options: orderedChannels(currentUser?.id),
    value: sendForm.channel,
    onChange: (v) => {
      rememberChannel(currentUser?.id, v)
      setSendForm({ ...sendForm, channel: v })
    },
  })

  const [declineVisible, setDeclineVisible] = useState(false)
  const [declineReason, setDeclineReason] = useState('')
  const [submitVisible, setSubmitVisible] = useState(false)
  const [submitReason, setSubmitReason] = useState('')
  // 批量录入整版明细
  const [itemsVisible, setItemsVisible] = useState(false)
  const [itemRows, setItemRows] = useState<ItemDraftRow[]>([])

  const skusQuery = useQuery({
    queryKey: ['skus-for-pricing'],
    queryFn: listSkusForPricing,
    enabled: itemsVisible,
  })
  /**
   * 可选定制需求：**只列这张报价所属客户的需求**（2026-10-12 主人提问核实）。
   *
   * 核实结果：这里原来拉的是 `listCustomInquiries({ page, page_size })`，
   * 把**全部客户**的需求都铺进下拉 —— 而需求列表接口本来就支持 `customer_id`
   * （`inquiry/router.py` 有那个参数），是这里没传。于是界面上能把 A 客户的需求
   * 选进 B 客户的报价，落库后需求编号/标题还会进快照、跟着对客文件流出去。
   * 后端已同步补上"需求与报价必须同一客户"的判据（`quote/service._build_custom_item_snapshot`），
   * 这里把下拉也收窄 —— 两层一起，界面上就不该出现选得动的错误选项。
   *
   * `customer_id` 还没读到时不发请求（enabled 里判），避免先拉一份"全部客户"的
   * 列表闪一下再换。
   */
  const quoteCustomerId = detailQuery.data?.quote?.customer_id
  // 只取本客户需求的**总数**，用于空态说明；选项本身改为按需搜索（见下方下拉），
  // 不能再拿"第一页 100 条"充当全部。
  const inquiriesCountQuery = useQuery({
    queryKey: ['inquiries-for-quote-count', quoteCustomerId],
    queryFn: () => listCustomInquiries({ customer_id: quoteCustomerId, page: 1, page_size: 1 }),
    enabled: itemsVisible && Boolean(quoteCustomerId),
  })
  const customerHasInquiries = (inquiriesCountQuery.data?.total ?? 0) > 0
  /** 与原来本地筛的判据一致：本报价关联过的需求，或未被取代/归档且未挂别家报价的需求。 */
  const inquiryAccepted = (row: { linked_quotes?: { id: number }[]; is_superseded?: boolean; status?: string }) =>
    Boolean(row.linked_quotes?.some((link) => link.id === quoteId)) ||
    (!row.linked_quotes?.length && !row.is_superseded && row.status !== 'archived')

  const patchRow = (index: number, patch: Partial<ItemDraftRow>) => {
    setItemRows((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  const openItemsEditor = () => {
    const rows = (detail?.items ?? []).map<ItemDraftRow>((item) => ({
      mode: item.is_custom ? 'custom' : 'sku',
      sku_id: item.sku_id ?? undefined,
      inquiry_id: item.inquiry_id ?? undefined,
      quantity: item.quantity_exact ?? String(item.quantity ?? ''),
      quoted_price: item.quoted_price_exact ?? String(item.quoted_price ?? ''),
      // 定制件的成本快照就是当初人工填的核价成本，回填出来方便改
      unit_cost: item.is_custom ? String(item.cost_snapshot_exact ?? item.cost_snapshot ?? '') : '',
      remark: item.remark ?? '',
    }))
    setItemRows(rows.length ? rows : [{ mode: 'sku', quantity: '1', quoted_price: '', unit_cost: '', remark: '' }])
    setItemsVisible(true)
  }

  const itemsMutation = useMutation({
    mutationFn: () =>
      setQuoteVersionItems(
        version!.id,
        itemRows.map((row, index) => {
          const quantity = requiredQuantityText(row.quantity, `第 ${index + 1} 行数量`)
          const price = row.quoted_price.trim() === '' ? null : row.quoted_price.trim()
          if (row.mode === 'custom') {
            return {
              inquiry_id: row.inquiry_id,
              quantity,
              quoted_price: price,
              unit_cost: row.unit_cost.trim() === '' ? null : row.unit_cost.trim(),
              remark: row.remark.trim() || null,
            }
          }
          return {
            sku_id: row.sku_id,
            quantity,
            quoted_price: price,
            remark: row.remark.trim() || null,
          }
        }),
      ),
    onSuccess: (updated) => {
      Toast.success('明细已保存（整版替换）')
      // §8.14 复审：明细保存后如实提示"哪些 SKU 的主数据还没确认"
      // （详情页另有一条常驻提示，刷新也在）
      for (const warning of updated?.master_warnings ?? []) {
        Toast.warning({ content: warning, duration: 8 })
      }
      setItemsVisible(false)
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['price-drift', updated?.id] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 转订单 / 新建版本都需要先确认：后端不带 confirm 会返回 42206 并给出说明，
  // 这里把它弹出来，用户确认后**带 confirm 重发**（onSuccess 才会照常触发）。
  const [gateMessage, setGateMessage] = useState('')
  const [gateRetry, setGateRetry] = useState<(() => void) | null>(null)
  const gate = (error: unknown, retry: () => unknown): boolean => {
    if (!isConfirmRequired(error)) return false
    setGateMessage(confirmMessageOf(error))
    // 包一层：`retry` 的返回值是 MutationResult，而 state 存的是 `() => void`
    setGateRetry(() => () => {
      retry()
    })
    return true
  }

  /**
   * 转单弹窗（2026-10-13 改动三）：转单时**直接选收款方式**。
   *
   * 从前这里是"后端要求确认（42206）→ 弹一个只有确定/取消的信息框"，
   * 应收写死一条「全款」；想拆定金/尾款得转完单再去订单页删了重建。
   * 现在把收款计划放进这个弹窗，默认仍是全款（不动老习惯）。
   */
  const [convertVisible, setConvertVisible] = useState(false)
  const [planMode, setPlanMode] = useState<'full' | 'installments'>('full')
  const [planRows, setPlanRows] = useState<ReceivablePlanRow[]>(defaultPlanRows)

  const convertMutation = useMutation<{ order_id: number; order_no: string }, Error, boolean>({
    mutationFn: (confirm) =>
      convertToOrder(versionId!, { receivable_plan: toPlanPayload(planMode, planRows) }, confirm),
    onSuccess: (data) => {
      setConvertVisible(false)
      void queryClient.invalidateQueries({ queryKey: ['quote-version'] })
      Toast.success(`已生成订单 ${data.order_no}，可在订单详情查看应收计划。`)
      navigate(`/orders/${data.order_id}`)
    },
    onError: (error: Error) => {
      if (!gate(error, () => convertMutation.mutate(true))) Toast.error(error.message)
    },
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['opportunity'] })
    void queryClient.invalidateQueries({ queryKey: ['opportunities'] })
    void queryClient.invalidateQueries({ queryKey: ['funnel'] })
    void queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] })
    void queryClient.invalidateQueries({ queryKey: ['quote-version'] })
    void queryClient.invalidateQueries({ queryKey: ['quote-versions'] })
    void queryClient.invalidateQueries({ queryKey: ['quotes'] })
    void queryClient.invalidateQueries({ queryKey: ['approvals'] })
    void queryClient.invalidateQueries({ queryKey: ['customer'] })
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
    void queryClient.invalidateQueries({ queryKey: ['customer-quotes'] })
    void queryClient.invalidateQueries({ queryKey: ['timeline'] })
    void queryClient.invalidateQueries({ queryKey: ['followups'] })
    void queryClient.invalidateQueries({ queryKey: ['notifications'] })
    void queryClient.invalidateQueries({ queryKey: ['notifications-unread'] })
  }

  const detail = detailQuery.data
  const hasActiveOrder = detail?.has_active_order ?? (detail?.quote?.status === 'converted')
  const version = detail?.version
  const quote = detail?.quote
  /**
   * 收件人下拉：这个客户的联系人（后端已按权限脱敏，姓名/职位不受影响）。
   *
   * ⚠️ 这两个查询必须放在 `quote` / `versionId` 声明**之后** —— 放前面会
   * "use before declaration"（我第一版就放错了位置，tsc 直接报出来）。
   */
  const contactsQuery = useQuery({
    queryKey: ['customer-contacts', quote?.customer_id],
    queryFn: () => listContacts(quote!.customer_id),
    enabled: sendVisible && Boolean(quote?.customer_id),
  })

  /**
   * 收件人下拉的选项：这个客户的联系人。
   *
   * 值是**姓名**：后端 `receiver` 是 `String(128)` 的自由文本，存姓名既能对上人，
   * 又不会把脱敏过的手机号/企微号（带星号）写进库 —— 那种值一旦落库就永久残了，
   * 而且审计里看起来像真号码。
   */
  const contactOptions = (contactsQuery.data ?? []).map((contact) => ({
    value: contact.name,
    label: [contact.name, contact.title].filter(Boolean).join(' · '),
  }))

  /**
   * 关联询价的同序 OA 审批。
   *
   * 定制明细上记着 `inquiry_id`，那层「核价 / 报价复核」是在询价单上走的。
   * 报价页只说自己这版"还没批"，用户会以为流程卡死；把询价那层的轮次和状态
   * 摆出来，才知道到底堵在哪一层。只读展示 + 各自一个「立即同步」，
   * 判断权仍在后端，前端不替任何人放行。
   */
  const linkedInquiries = (() => {
    const seen = new Map<number, string | null>()
    for (const item of detail?.items ?? []) {
      if (item.inquiry_id) seen.set(item.inquiry_id, item.inquiry_no ?? null)
    }
    return Array.from(seen, ([id, inquiryNo]) => ({ id, inquiryNo }))
  })()
  const linkedInquiryKey = linkedInquiries.map((row) => row.id).join(',')
  const inquiryApprovalsQuery = useQuery({
    queryKey: ['quote-linked-inquiry-approvals', linkedInquiryKey],
    queryFn: async () => Promise.all(linkedInquiries.map(async (row) => ({
      inquiry: row,
      instances: await listInquiryApprovals(row.id),
    }))),
    enabled: linkedInquiries.length > 0,
  })
  const inquiryApprovalRows = (inquiryApprovalsQuery.data ?? []).map(({ inquiry, instances }) => {
    // 同一条需求可能被驳回重提多次：最新一轮先看轮次号，再看记录 id
    const latest = [...instances].sort((a, b) => (b.submit_round ?? 0) - (a.submit_round ?? 0) || b.id - a.id)[0]
    /**
     * 这一层到底走到哪个阶段：核价、报价复核是同序 OA 询价单里的**节点**，
     * 不单独占一张审批实例。所以阶段名从同步回来的 tasks 里取，
     * 不去按模板猜顺序 —— 模板改了，猜的那套就错。
     *   · 待审：还没办完的节点（待处理/处理中）；
     *   · 已通过：走完的节点全列出来；
     *   · 没同步过结果：留空，不编。
     */
    const stageText = (() => {
      if (!latest?.result) return ''
      const tasks = approvalProgress(latest.result).filter((entry) => entry.kind === 'task')
      if (tasks.length === 0) return ''
      const open = tasks.filter((entry) => entry.status.includes('待处理') || entry.status.includes('处理中'))
      const nodes = (open.length > 0 ? open : tasks).map((entry) => entry.node)
      return [...new Set(nodes)].join(' / ')
    })()
    /**
     * 报价这一关单独看 `business_stages.quotation`：
     * 整张询价单可能是 pending（后面还有打样、备货节点在办），
     * 但核价/报价复核这一关已经过了 —— 那就能正式报价。
     * 只看整单状态会把"已可报价"误显示成"待审批"，所以这里单独取。
     */
    const quotation = (() => {
      const stages = (latest?.result as { business_stages?: Record<string, unknown> } | null | undefined)?.business_stages
      const node = stages?.quotation as { status?: string; label?: string } | undefined
      if (!node || typeof node !== 'object') return null
      return { approved: node.status === 'approved' }
    })()
    return { key: inquiry.id, inquiry, latest, stageText, quotation }
  })

  const isCurrentVersion = Boolean(version && quote?.current_version_id === version.id)
  const rejection = version?.approval_status === 'rejected'
  const rejectionRecord = rejection
    ? detail?.approval?.records.findLast((record) => record.action === 'reject')
    : undefined
  const approvalAssignee = detail?.approval?.summary?.current_assignee_name
  const awaitingFinance = detail?.approval?.current_node === 'co_sign'
  const assignedApprover = typeof approvalAssignee === 'string' ? approvalAssignee.trim() : ''
  const editable = isCurrentVersion && version?.approval_status === 'not_submitted' && !version?.sent_at
  /**
   * 锁版之后能不能**补/改运费**（主人 2026-10-12 拍板，方案 A+B）。
   *
   * 后端的判据是 `ensure_charge_change_allowed`：只有「物流」类费用放行。
   * 前端这里只是把入口露出来 —— 真正的拒绝在后端。
   */
  const canEditFreightOnly = Boolean(
    canManage && isCurrentVersion && version
    && (version.approval_status === 'approved' || version.sent_at),
  )
  const canRespond = isCurrentVersion && quote?.status === 'sent' && Boolean(version?.sent_at)
    && version?.approval_status === 'approved' && !version.accepted_at && !version.declined_at
  /**
   * 这一版**已经有的那条运费**（没有就是 `undefined`）。
   *
   * 后端 `ensure_single_logistics_charge` 只允许一个版本有一条运费 —— 多条会被
   * 静默累加进总额。这里拿它把「补填运费」提前挡住并**说清去哪改**，
   * 而不是让用户填完一整个弹窗再吃一个拒绝。
   */
  const existingFreight = (detail?.charges ?? []).find((item) => item.is_logistics)

  // 动作消失也要给理由（主人 2026-10-12 原话：「这种我点又点不了，又不给提示为什么点不了，
  // 这种体验很不好，类似的情况都要修」）。「提交审批 / 批量录入 / 添加费用」三个入口都挂在
  // `canManage && editable` 上（判据见上面 `const editable`）：切到历史版本、版本已发送、审批通过之后
  // 它们**整块不渲染**，页面上一个字都不说 —— 用户只看到按钮没了。
  // pending / rejected 那两种情形页面上已有 Tag 说明（该版本正在审批中 / 审批未通过），
  // 这里只补没人说的这几种，能不能改的判据一个字没动。理由块见 shared/components/BlockedHint.tsx。
  const versionEditBlockedReason = !version || !canManage
    ? ''
    : !isCurrentVersion
      ? `历史版本（V${version.version_no}）不能再提交审批、录入明细或加费用；要改请切到当前版本或新建一版。`
      : version.sent_at
        ? '已发送给客户，明细和费用不可改（运费例外）；要改请新建一版。'
        : version.approval_status === 'approved'
          ? '已审批通过，明细和费用不可改（运费例外）；要改请新建一版。'
          : ''

  // A09 后半：草稿版本检测"价格已有更新"（系统带价的明细与当前适用价比对）
  const driftQuery = useQuery({
    queryKey: ['price-drift', version?.id],
    queryFn: () => getPriceDrift(version!.id),
    enabled: Boolean(editable && version),
  })

  // 主数据刷新：**先看预览再确认**（issue 建议第 5 条）。
  // 名称/规格/单位是印给客户的，刷新会改掉它们 —— 让人先看清"从什么变成什么"。
  const [masterPreviewOpen, setMasterPreviewOpen] = useState(false)
  const masterPreviewQuery = useQuery({
    queryKey: ['quote-master-preview', version?.id],
    queryFn: () => getQuoteMasterRefreshPreview(version!.id),
    enabled: masterPreviewOpen && Boolean(version),
  })

  const refreshMutation = useMutation({
    // 方案二：一次动作做两件事 ——
    //   ① 接主数据（**不依赖价格**，手工价/查不到价的明细也能接上）
    //   ② 重新核价（把系统带价的明细刷到当前适用价）
    // 顺序：先接主数据。它只动名称/规格/单位和版本号，不会因为查不到价而失败；
    // 放在后面的话，②一旦抛错就会把①一起回滚，用户又回到"点了没反应"。
    mutationFn: async () => {
      const id = version!.id
      const link = await linkMasterData(id)
      try {
        return { id, link, price: await refreshPrices(id) }
      } catch (error) {
        const reason = error instanceof Error ? error.message : '请稍后重试'
        return { id, link, reason }
      }
    },
    onSuccess: ({ id, link, price, reason }) => {
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['price-drift', id] })
      void queryClient.invalidateQueries({ queryKey: ['quote-master-preview', id] })
      if (!price) {
        Toast.warning({ content: `${link.message} 重新核价未完成：${reason}`, duration: 8 })
        return
      }
      Toast.success(link.message)
      if (price.refreshed || price.skipped) {
        Toast.info(`重新核价：${price.refreshed} 条已刷新、${price.skipped} 条未动（手工价）`)
      }
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const costRefreshMutation = useMutation({
    mutationFn: () => refreshQuoteCosts(versionId!),
    onSuccess: () => { Toast.success('草稿成本已更新，报价单价未改变'); refresh() },
    onError: (error: Error) => Toast.error(error.message),
  })

  const priceMutation = useMutation({
    mutationFn: () => updateQuoteItem(priceTarget!.id, { quoted_price: newPrice.trim() }),
    onSuccess: (item) => {
      Toast.success(
        item.approval_required ? '已改价：该明细超出你的价格权限，提交后会走审批' : '已改价',
      )
      // §8.14 复审：改价会重算明细快照，主数据未确认的提醒要跟着出来
      // （详情页另有一条常驻提示，不依赖这次 Toast）
      for (const warning of item.master_warnings ?? []) {
        Toast.warning({ content: warning, duration: 8 })
      }
      setPriceTarget(null)
      setNewPrice('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const versionMutation = useMutation<QuoteVersion, Error, boolean>({
    mutationFn: (confirm) => createQuoteVersion(quoteId, confirm),
    onSuccess: (created) => {
      Toast.success(`已创建 V${created.version_no}`)
      setSearchParams({ version: String(created.id) })
      refresh()
    },
    onError: (error: Error) => {
      if (!gate(error, () => versionMutation.mutate(true))) Toast.error(error.message)
    },
  })

  const submitMutation = useMutation({
    mutationFn: () => submitApproval(versionId!, submitReason),
    onSuccess: (data) => {
      Toast.success(data.approval_required ? '已提交审批' : '未超出权限，报价已通过，可以发送')
      setSubmitVisible(false)
      setSubmitReason('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const withdrawMutation = useMutation({
    mutationFn: () => withdrawApproval(versionId!),
    onSuccess: () => {
      Toast.success('已撤回审批')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const sendMutation = useMutation({
    mutationFn: () => markSent(versionId!, sendForm),
    onSuccess: () => {
      Toast.success(`已登记：通过${sendForm.channel}发给${sendForm.receiver}`)
      rememberChannel(currentUser?.id, sendForm.channel)
      setSendVisible(false)
      setSendTouched(false)
      refresh()
      // 刚登记的那条要立刻出现在下面的发送记录里
      void queryClient.invalidateQueries({
        // 键从这里取，别在页面里再写一遍字面量 —— 两个键对不上就永远失效不到
        queryKey: sendLogsQueryKey(allVersionIds),
      })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /** 确认登记：收件人必填（留空的话事后无从查证发给了谁）。 */
  function submitSend() {
    setSendTouched(true)
    if (!sendForm.receiver.trim()) {
      Toast.error('请填写发给了谁（可从客户联系人里选，也可以手填）')
      return
    }
    sendMutation.mutate()
  }

  const acceptMutation = useMutation({
    mutationFn: () => acceptQuote(versionId!),
    onSuccess: () => {
      Toast.success('客户已接受')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const declineMutation = useMutation({
    mutationFn: () => declineQuote(versionId!, declineReason.trim() || undefined),
    onSuccess: () => {
      Toast.success('已记录客户拒绝')
      setDeclineVisible(false)
      setDeclineReason('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const chargeMutation = useMutation({
    mutationFn: () => {
      const isDiscount = chargeForm.charge_type === 'discount'
      const amount = parseChargeAmount(chargeForm.amount.trim(), isDiscount)
      return addQuoteCharge(versionId!, {
        charge_type: chargeForm.charge_type,
        description: chargeForm.description,
        amount,
        // 折扣：前端传**正数**，后端负责归一成负数入库（库里恒为负数）
        is_discount: isDiscount,
      })
    },
    onSuccess: () => {
      Toast.success('已添加附加费用')
      setChargeVisible(false)
      setChargeForm({ charge_type: 'logistics', description: '', amount: '' })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /**
   * 改一条已有的费用（后端 `PATCH /quote-charges/{id}`）。
   *
   * 只传说明与金额：分类码在锁版之后不能改（后端 `ensure_charge_change_allowed`
   * 会拒），而在草稿态想改分类，直接删了重加更直白 —— 所以这里不暴露分类下拉。
   */
  const updateChargeMutation = useMutation({
    mutationFn: () => {
      if (!editCharge) throw new Error('没有选中要修改的费用')
      const amount = parseChargeAmount(editChargeForm.amount.trim(), editCharge.is_discount)
      return updateQuoteCharge(editCharge.id, {
        description: editChargeForm.description,
        amount,
      })
    },
    onSuccess: () => {
      Toast.success('已保存并重算合计')
      setEditCharge(null)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /**
   * 打开「改这条费用」的弹窗。
   *
   * 折扣在库里恒为**负数**（`recalc_version` 直接代数相加），而界面上一直让用户
   * 填正数、由后端归一 —— 回填时要把符号转回来，否则编辑框里是 `-50`，
   * 一点保存就被"金额最多两位小数"拦下（看着像校验坏了）。
   */
  const openChargeEditor = (record: QuoteChargeRow) => {
    const amount = record.amount_exact ?? String(record.amount)
    setEditCharge(record)
    setEditChargeForm({
      description: record.description ?? '',
      amount: record.is_discount ? amount.replace(/^-/, '') : amount,
    })
  }

  // 折叠区收起时要显示「本版均价」：接口的 QuoteVersion 上没有 unit_price，
  // 按明细的加权均价算（金额 ÷ 数量），和 WhatIfPanel 里 baseline.unitPrice 同一口径。
  const whatifUnitPrice = (() => {
    const rows = detail?.items ?? []
    const qty = rows.reduce((sum, item) => sum + (item.quantity ?? 0), 0)
    const amount = rows.reduce((sum, item) => sum + (item.quantity ?? 0) * (item.quoted_price ?? 0), 0)
    return qty > 0 ? amount / qty : 0
  })()

  // 总额传进去：金额太小时「分不出正数期」要在弹窗里就拦住并说明（第九批 ①⑤）
  const planTotal = version?.total_amount_exact ?? version?.total_amount ?? 0
  // 灰键必须说得出为什么（项目口径）：判据与组件内部同源。
  const planBlocked = planBlockedReasons(planMode, planRows, planTotal)
  // 「能不能点」与「为什么不能点」**必须是同一个判据**（2026-10-15 统一）。
  // 原来 `!isPlanValid(...)` 在 okButtonProps 与按钮上重复算了三次，
  // 而原因用的是 `planBlocked`（另一个函数）—— 两处一旦不同步，就会出现
  // 「按钮能点，旁边却写着不能点」这种自相矛盾的界面。
  // `planBlockedReasons` 非空 ⟺ `isPlanValid` 为假（逐例比对过），所以这里等价且更稳。
  const planNotReady = planBlocked.length > 0

  // ⚠️ `planBlocked` / `planTotal` 必须在**任何 early return 之前**算出来（2026-10-14 审查修）。
  //
  // 原来这两行放在下面 `version` 就绪之后（也就是两个 early return 之后），
  // 于是"加载中"与"报价单不存在"这两条渲染路径上少算了一次，各处的 hook 数量
  // 与顺序也不一致。React 的 Hooks 规则要求每次渲染完全一致，违反时会抛
  // "Rendered more hooks than during the previous render"。
  // 提到这里之后，`planBlocked` 用的仍是真实原因，灰键该弹的提示一条不少。
  // （2026-10-15：`useBlockedNotify` 那一行已随 `GuardedButton` 一起去掉，
  //   这里不再有 hook，但**位置要求不变** —— `planNotReady` 要在 early return 前算好。）
  if (versionsQuery.isLoading || detailQuery.isLoading) {
    return <PageLoading />
  }
  if (!detail || !version || !quote) {
    return (
      <div className="page-container">
        <EmptyState
          title="报价单不存在或无权查看"
          hint="可能链接里的编号有误，或这条报价属于你数据范围之外的人；回报价中心重新选择即可。"
        />
        <div style={{ display: 'flex', justifyContent: 'center', paddingBottom: 24 }}>
          <Button type="primary" theme="solid" onClick={() => navigate('/quotes')}>返回报价中心</Button>
        </div>
      </div>
    )
  }

  /**
   * 这一版是不是**旧口径**（`base_cost` 含运费）。
   *
   * 2026-10-09「产品价格与运费分离」：新报价的产品单价不含运费、运费代收代付，
   * 所以成本列不再加单件运费、单价要标"不含运费"。历史版本留在老口径上，
   * 页面显示也必须跟着它自己的口径走 —— 否则会用今天的公式解释当时的数字。
   *
   * 判据取自后端返回的 `pricing_basis`（版本上冻结的），**不在前端猜**。
   */
  const legacyBasis = version.pricing_basis === 'legacy'
  /** 后端算好的金额汇总；老后端没这个键时退回用 version 上的字段，保证不白屏 */
  const summary = detail.summary ?? {
    goods_amount: version.subtotal_amount,
    logistics_amount: version.logistics_amount ?? 0,
    other_charge_amount: version.other_charge_amount ?? 0,
    charge_amount: version.charge_amount,
    discount_amount: version.discount_amount,
    total_amount: version.total_amount,
    pricing_basis: version.pricing_basis,
    pricing_basis_label: version.pricing_basis_label,
  }
  const cny = (value: number | string) => formatMoney(toTwoDecimals(value), 'CNY')

  /** 运费未确认的人话原因（后端给）；有值就要一直显示 */
  const freightUnconfirmed = detail.freight_unconfirmed_reason ?? null


  // 「这一步还差什么」的清单（判据在 shared/quote-todo.ts，单一真源）
  const quoteTodoList = quoteTodos({
    costProblems: detail.cost_problems?.map((p) => `${p.label}：${p.reason}`),
    masterWarnings: detail?.master_warnings,
    freightUnconfirmedReason: freightUnconfirmed,
    approvalStatus: version?.approval_status,
    sentAt: version?.sent_at,
    acceptedAt: version?.accepted_at,
    declinedAt: version?.declined_at,
    converted: hasActiveOrder,
  })
  // 有没有"必须处理完才能往下走"的项（主数据/费用）——决定标签用橙色还是蓝色
  const quoteTodosBlocked = quoteTodoList.some(
    (todo) => todo.stage === 'master' || todo.stage === 'cost',
  )


  const itemColumns = [
    {
      title: '产品名称 / 需求',
      dataIndex: 'sku_code',
      // 1024 下七列合计 870px，卡内只有 699px：表格只能横向滚动，
      // 右端「审批 / 操作」被切成半个字。七列按"够用"重排为
      // 145/60/105/85/110/85/100 = 690，1024 正好放下。
      // 宽屏（2026-11-18）：其余六列合计 545 是死的，首列写 145 时 Semi 把
      // 多出来的 400 多像素平摊给所有列 —— 结果别的列变宽、产品名称还挤在
      // 145 里折三行。首列改成吃剩余宽度：1024 下仍是 154（和原来一样能放下），
      // 1440 下变成 500+，名称一行说完。
      width: '100%',
      // Semi 的单元格默认 `nowrap + ellipsis + overflow:hidden`：文案超过列宽就被裁成
      // 一个「…」，只在 render 里写 whiteSpace 不够（格子自己的 nowrap 仍会接管）。
      // 这一列是识别列，宁可折成两行也不能只剩省略号（2026-11-17 视觉审视 V2-06）。
      onCell: () => ({ style: { whiteSpace: 'normal' as const, textOverflow: 'clip', overflow: 'visible' } }),
      // 定制项（场景09）没有 SKU：显示需求编号 + 定制标记，
      // 否则这一格会是空的，看的人不知道这条是什么
      // 名称在上、编码辅助展示；规格仍保留独立列。
      // 从前这里只渲染 `sku_code`，而接口早就返回了 `sku_name`
      // （`quote_items.sku_name_snapshot`）—— 满屏 `TP-1210-ST` 这种编码，
      // 看的人不知道是哪件东西（主人 2026-10-10 指出）。
      render: (v: string | null, record: QuoteItemRow) => (
        <div style={{ lineHeight: 1.5 }}>
          {/* 名称在窄屏被挤时不再只剩「…」：允许折行，鼠标停上去给完整名称
              （2026-11-17 视觉审视 V2-06：首列是识别列，不能只剩省略号） */}
          {/* 表格单元格默认 `nowrap + ellipsis`，只写 overflowWrap 不生效 ——
              必须显式 whiteSpace: normal 才能折行（实测：内层 div 宽 474 被 190 宽的格子裁成「…」） */}
          <div
            title={record.sku_name || v || '未命名产品'}
            style={{ overflowWrap: 'anywhere', whiteSpace: 'normal' }}
          >
            {record.sku_name || v || '未命名产品'}
            {record.is_custom && (
              <Tag size="small" type="light" style={{ marginLeft: 6 }}>
                定制
              </Tag>
            )}
          </div>
          {/* 规格并进这一格，不再单占一列：它和产品是「同一条东西」的两行字，
              分两列反而要多横着看一次（十二列挤在 1130 里就是这个代价）。 */}
          {(record.specification || (v && record.sku_name)) && (
            /* 副行（规格 · 编码）在 1024 下会跟着名称一起折，把行高撑到五行：
               143px 的首列放不下「来源未记录采购数量 - XQ202610120002」。
               名称照旧折行（识别列不能只剩省略号），副行改为单行省略 + 悬停看全，
               行高回落到三行（2026-11-19）。 */
            <div
              title={[record.specification || '', v && record.sku_name ? v : ''].filter(Boolean).join(' · ')}
              style={{
                fontSize: 12,
                color: 'var(--crm-text-3)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {[record.specification || '', v && record.sku_name ? v : ''].filter(Boolean).join(' · ')}
            </div>
          )}
        </div>
      ),
    },
    /* 52 → 72：52 那会儿实测单元格只剩三十来像素，数量「1」被渲染成「1...」，
       一个数字后面挂省略号（2026-11-19 实拍）。72 够放四位数量加千分位逗号。 */
    { title: '数量', dataIndex: 'quantity', width: 72, render: (v: number) => v.toLocaleString('zh-CN') },
    {
      // 单价口径必须在列头写清"不含运费"：运费分离之后，客户与业务员都容易
      // 按含运费的旧口径理解，列头不写就会各理解一套。
      //
      // ⚠️ 但**只在本版确实是"单价不含运费"口径时才写**（2026-10-09 修）：
      // 从前这里硬编码，历史口径的报价也会显示"（不含运费）"，而那一版的单价
      // 里其实是含运费的 —— 等于在界面上改写已发报价的口径。判据用后端返回的
      // `pricing_basis`（版本上冻结的），不在前端猜。
      title: legacyBasis ? '实际报价（含运费）' : '实际报价（不含运费）',
      dataIndex: 'quoted_price',
      // 这一列的表头最长（「实际报价（不含运费）」约 154px），但 1024 下七列
      // 只剩 699px 可用。110 宽 + 表头允许折两行：口径不丢，列也不再被裁。
      width: 105,
      onHeaderCell: () => ({ style: { whiteSpace: 'normal', lineHeight: 1.35 } }),
      className: 'cell-money',
      render: (value: number, record: QuoteItemRow) => {
        // 方案 §4.1"显示价格来源"：拟报价的依据（客户专属价/等级价/通用指导价/手工）
        const sourceLabel =
          record.price_source === 'customer_specific'
            ? '客户专属价'
            : record.price_source === 'level'
              ? '客户等级价'
              : record.price_source === 'general'
                ? '通用指导价'
                : record.price_source
                  ? '手工价'
                  : ''
        return (
          <div style={{ lineHeight: 1.5 }}>
            {/* 统一两位小数：quoted_price_exact 是 Decimal 序列化出来的（如 150.0000），
                直接渲染会和同表的 ¥100.00 形成两种精度 */}
            <span
              style={{
                fontWeight: 600,
                color: record.approval_required ? 'var(--crm-error)' : 'var(--crm-text)',
              }}
            >
              {/* 单价保真（2026-11-17）：曾经这里用 toFixed(2)，33.3333 会被四舍五入成 33.33，
                  和金额对不上。统一走 formatUnitPrice。 */}
              {formatUnitPrice(record.quoted_price_exact ?? value, 'CNY')}
            </span>
            {/* 价格来源并进这一格：它解释的就是「这个价从哪来」，与金额同一行读最顺 */}
            {sourceLabel && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                {sourceLabel}
                {record.customer_level_snapshot ? `（${record.customer_level_snapshot}）` : ''}
              </div>
            )}
          </div>
        )
      },
    },
    {
      title: (
        <span className="crm-help-label">
          利润率
          <HelpTip forLabel="利润率" text="单件毛利 ÷ 单件报价（不含运费）。头部的毛利率是整单合计口径，两者算法相同、取数范围不同。" />
        </span>
      ),
      dataIndex: 'profit_rate_snapshot',
      /* 85 装不下「100.00%」（实测被裁成「100.0...」）——
         明细表七列合计超出内容区时各列一起被压，这一列正好压到小数点后。
         96 才够，产品名称是弹性列，会自然让出这 11px。 */
      width: 96,
      render: (v: number | null) => v == null ? '-' : `${(v * 100).toFixed(2)}%`,
    },
    {
      title: '金额',
      dataIndex: 'amount',
      /* 110 装不下「¥105,600.00」——六位数带小数被裁成「¥105,600.…」。
         132 才够（数量/单价两列不动，产品名称弹性列让出 22px）。 */
      width: 132,
      /* 金额右对齐，与列表页、回款中心同一口径（.cell-right 见 index.css）。 */
      className: 'cell-right',
      render: (v: number, record: QuoteItemRow) => formatMoney(record.amount_exact ?? v, 'CNY'),
    },
    {
      title: '审批',
      dataIndex: 'approval_required',
      // 80 时「权限内」连状态点一起只剩约 62px，尾字被省略号吃掉；
      // 85 才能在 1024 下完整显示，所以从首列让 5px 过来。
      width: 85,
      render: (v: boolean) => (
        <StatusDot label={v ? '需审批' : '权限内'} tone={v ? 'orange' : 'green'} bold={v} />
      ),
    },
    {
      title: '操作',
      // 明细行的「核算依据」触发器（展开/收起）挪到这一列：
      // Semi 自带的图标位只有十几像素宽，两个字塞不进去（2026-11-17 实拍被切成一条竖线）；
      // 放在操作列里和其他动作同高同字号，「改价 · 展开」一眼就能看到。
      width: 100,
      render: (_: unknown, record: QuoteItemRow) => {
        const open = expandedItemIds.includes(record.id)
        const toggle = () =>
          setExpandedItemIds((keys) =>
            open ? keys.filter((k) => k !== record.id) : [...keys, record.id],
          )
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, whiteSpace: 'nowrap' }}>
            {canManage && editable && (
              <button type="button" className="crm-text-action"
                style={{ color: 'var(--crm-primary)' }}
                onClick={() => {
                  setPriceTarget(record)
                  setNewPrice(record.quoted_price_exact ?? String(record.quoted_price))
                }}
              >
                改价
              </button>
            )}
            <button type="button" className="crm-text-action" style={{ color: 'var(--crm-text-2)' }} onClick={toggle}>
              {open ? '收起' : '展开'}
            </button>
          </span>
        )
      },
    },
  ]

  const skuOptions = (skusQuery.data ?? []).map((sku) => ({
    value: sku.id,
    // 下拉里带上 SKU 名称：只有 `编码 · 规格` 时，一个产品下几个 SKU 认不出区别
    label: skuLabel(sku),
  }))
  return (
    <div className="page-container">
      {/* 与客户/商机详情页同排布：标题 + 标签一行，关键信息行在标题下方左对齐 */}
      <DetailHeader
        title={quote.quote_no}
        extra={(
          // 页头右半原来是空的：信息行左对齐、删除按钮贴最右，中间一大片白。
          // 把这一版的三个关键数字（总额 / 毛利率 / 明细条数）放在右侧当"摘要"，
          // 视线从左边的身份信息自然滑到右边的钱，空白变成了信息位。
          <div className="quote-head-aside">
            <div className="quote-head-kpi">
              <div className="qhk-item">
                <span className="qhk-label">报价总额</span>
                <span className="qhk-value">
                  {formatMoney(version?.total_amount_exact ?? version?.total_amount ?? 0, 'CNY')}
                </span>
              </div>
              <div className="qhk-item">
                <span className="qhk-label crm-help-label">
                  毛利率
                  <HelpTip forLabel="毛利率" text="整单毛利 ÷ 整单货款（不含运费）。明细表里的「利润率」是逐行口径。" />
                </span>
                <span className="qhk-value">
                  {version?.total_profit == null || !version?.subtotal_amount
                    ? '-'
                    : `${((version.total_profit / version.subtotal_amount) * 100).toFixed(2)}%`}
                </span>
              </div>
              <div className="qhk-item">
                <span className="qhk-label">明细</span>
                <span className="qhk-value">{(detail?.items ?? []).length} 条</span>
              </div>
            </div>
            {/* 已发送/已接受/已作废只能作废保留，不出现删除入口（后端同样拦） */}
            {canManage && !hasActiveOrder && !['sent', 'accepted', 'voided'].includes(quote.status) && (
              // 删除是低频且不可逆的动作，红框按钮摆页头最显眼的位置会抢掉
              // 「转销售订单」的注意力。改成无边框次级按钮，仍可点、仍二次确认。
              <Button theme="borderless" type="danger" onClick={() => setDeleteVisible(true)}>
                删除报价
              </Button>
            )}
            {canManage && (quote.status === 'sent' || quote.status === 'accepted') && (
              <Button theme="borderless" type="danger" onClick={() => {
                setVoidReason('')
                setVoidVisible(true)
              }}>
                作废报价
              </Button>
            )}
          </div>
        )}
        tags={
          <>
            <Tag color={STATUS_TONE[hasActiveOrder ? 'converted' : quote.status] ?? 'grey'}>
              {hasActiveOrder ? '已转订单' : quote.status_label}
            </Tag>
            {/* 版本号原来是个实心蓝标签，跟主按钮抢眼、也把页头压得很重。
                版本号是次要信息，用描边款就够了（2026-11-16）。 */}
            <Tag className="tag-plain">V{version.version_no}</Tag>
            {version.approval_required && <Tag color="orange">超出价格权限</Tag>}
          </>
        }
        meta={
          <>
            <span>
              客户：
              <Link to={`/customers/${quote.customer_id}`} style={{ color: 'var(--crm-primary)' }}>
                {quote.customer_name}
              </Link>
            </span>
            <span>
              商机：
              {quote.opportunity_id ? (
                <Link to={`/opportunities/${quote.opportunity_id}`} style={{ color: 'var(--crm-primary)' }}>
                  {quote.opportunity_title}
                </Link>
              ) : (
                '-'
              )}
            </span>
            <span>负责人：{quote.owner_name ?? '-'}</span>
            {detail.active_order && (
              <Link to={`/orders/${detail.active_order.id}`}>
                查看订单 {detail.active_order.order_no}
              </Link>
            )}
            <span>有效期至：{quote.valid_until ?? '-'}</span>
            <span className={`quote-payment-terms${flashPaymentTerms ? ' flash-updated' : ''}`}>
              <span>付款条件：{version.payment_terms || '未填写'}</span>
              {canManage && editable && (
                <button type="button" className="quote-payment-terms-edit" aria-label="修改付款条件"
                  onClick={() => setPaymentTermsDraft({ versionId: version.id, text: version.payment_terms ?? '' })}>
                  <IconEditStroked size="small" aria-hidden="true" />
                  修改
                </button>
              )}
            </span>
          </>
        }
      >
        {/*
          动作分层（2026-10-13，主人：**每个阶段突出一个主要动作**，
          "新建版本、下载、历史操作"放到次要位置）。

          判据来自 `shared/quote-todo.ts` 的 `quoteActions(当前阶段)` —— 与上面
          待办区**同源**，所以不会出现"待办说去发送、最显眼的却是新建版本"。

          ⚠️ 分组靠**遍历顺序**，不是每个按钮自己写 `primary/secondary` 注释：
          我第一版就是给每个 Button 手加注释，结果 `登记再次发送` 与 `标记已发送`
          被分到两组、按钮出现了两次。现在按 `actionLabel` 的顺序归组，
          谁来改都只动一个地方。
        */}
        {(() => {
          const stage = currentStage(quoteTodoList)
          const { primary, secondary } = quoteActions(stage)

          // 每个动作的"能不能露"（判据一个字没动，仍是原来那些）
          const conditions: Record<string, boolean> = {
            '新建版本': Boolean(canManage && !rejection && !hasActiveOrder),
            '提交审批': Boolean(canManage && editable),
            '撤回审批': Boolean(canManage && isCurrentVersion && version.approval_status === 'pending'),
            '标记已发送': Boolean(
              canManage && isCurrentVersion && version.approval_status === 'approved'
              && (quote.status === 'approved' || quote.status === 'sent'),
            ),
            '登记再次发送': Boolean(
              canManage && isCurrentVersion && version.approval_status === 'approved'
              && (quote.status === 'approved' || quote.status === 'sent'),
            ),
            '客户接受': Boolean(canManage && canRespond),
            '客户拒绝': Boolean(canManage && canRespond),
            // 判据只看"这一版发出去过、客户也接受了"——不再要求整单 status 恰好是
            // accepted：整单状态可能因为别的环节（如后续跟进）先变了，那一版的接受
            // 事实并没有消失。已转成订单（converted）就不再给这个入口。
            '转销售订单': Boolean(
              can('order:manage') && isCurrentVersion && version.sent_at && version.accepted_at
              && !version.declined_at && !hasActiveOrder && !detail.version_order_cancelled,
            ),
            '下载 PDF': Boolean(version),
            // ⚠️ 这里只放**原本就在这一行里**的动作。「批量录入」「添加费用」
            // 分别在「报价明细」「附加费用」两个区块内（我第一版把它们搬上来，
            // 等于同一个入口出现两次），所以**不列在这里**。
            // 没检测到价差就**不给入口**（菜单里直接不出现）；
            // 有价差但这一版已锁定（已发送/已审批/历史版本）也不能按 —— 那是另一种说法，
            // 见下面 moreNotes 里那句具体原因，不再用笼统的"当前版本/状态不可用"。
            '一键刷新到最新适用价': Boolean(
              canManage && editable && driftQuery.data?.items?.some((it) => it.drift),
            ),
          }

          // 页面上的动作（顺序即渲染顺序）。不在表里的（例如弹窗内部按钮）不参与分层。
          const actions: { label: string; element: ReactElement }[] = [
            {
              label: '新建版本',
              element: (
                <Button onClick={() => versionMutation.mutate(false)} loading={versionMutation.isPending}>
                  新建版本
                </Button>
              ),
            },
            {
              label: '提交审批',
              element: (
                <Button type="primary" theme="outline" onClick={() => setSubmitVisible(true)}>
                  提交审批
                </Button>
              ),
            },
            {
              label: '撤回审批',
              element: (
                <Popconfirm
                  title="确认撤回当前版本的报价审批？"
                  content="撤回后本次审批结束，报价回到草稿；需要审批时须重新提交。"
                  onConfirm={() => withdrawMutation.mutate()}
                  disabled={withdrawMutation.isPending}
                >
                  <Button loading={withdrawMutation.isPending}>撤回审批</Button>
                </Popconfirm>
              ),
            },
            {
              label: '标记已发送',
              element: (
                <Button
                  theme="outline"
                  type="primary"
                  onClick={() => {
                    setSendForm({
                      ...sendForm,
                      request_key: Array.from(
                        crypto.getRandomValues(new Uint8Array(16)),
                        (v) => v.toString(16).padStart(2, '0'),
                      ).join(''),
                    })
                    setSendVisible(true)
                  }}
                >
                  标记已发送
                </Button>
              ),
            },
            {
              // 同阶段里的第二次发送（补发/重发）
              label: '登记再次发送',
              element: (
                <Button
                  theme="outline"
                  type="primary"
                  onClick={() => {
                    setSendForm({
                      ...sendForm,
                      request_key: Array.from(
                        crypto.getRandomValues(new Uint8Array(16)),
                        (v) => v.toString(16).padStart(2, '0'),
                      ).join(''),
                    })
                    setSendVisible(true)
                  }}
                >
                  登记再次发送
                </Button>
              ),
            },
            {
              // 客户接受不可逆（转订单的前置），点一下生效太轻
              label: '客户接受',
              element: (
                <Popconfirm
                  title="登记客户已接受这份报价？"
                  content="登记后这份报价进入「已接受」，是转销售订单的前置条件。"
                  onConfirm={() => acceptMutation.mutate()}
                >
                  <Button loading={acceptMutation.isPending}>客户接受</Button>
                </Popconfirm>
              ),
            },
            {
              label: '客户拒绝',
              element: (
                <Button type="danger" onClick={() => setDeclineVisible(true)}>
                  客户拒绝
                </Button>
              ),
            },
            {
              label: '转销售订单',
              element: (
                <Button type="primary"
                  theme="outline"
                  loading={convertMutation.isPending}
                  onClick={() => {
                    // 每次打开都回到默认：全款 + 默认两行（定金 30% / 尾款 70%）
                    setPlanMode('full')
                    setPlanRows(defaultPlanRows())
                    setConvertVisible(true)
                  }}
                >
                  转销售订单
                </Button>
              ),
            },
            {
              label: '下载 PDF',
              element: (
                <Button
                  onClick={() => void showDownloadResult(downloadQuotePdf(version.id, `${quote.quote_no}-V${version.version_no}.pdf`))}
                >
                  下载 PDF
                </Button>
              ),
            },
            {
              // 原文案与判据照抄（它在"价格有漂移"那条提示里，动作是直接刷新）
              label: '一键刷新到最新适用价',
              element: (
                <Button loading={refreshMutation.isPending} onClick={() => refreshMutation.mutate()}>
                  一键刷新到最新适用价
                </Button>
              ),
            },
          ]

          const visible = actions.filter((action) => conditions[action.label] !== false)
          const byLabel = new Map(visible.map((action) => [action.label, action]))

          const rendered: ReactElement[] = []
          const used = new Set<string>()
          // solid=true 才给实心：这一阶段**唯一**的主推动作。其余一律无底文字按钮，
          // 否则一排按钮全是实心块，等于没有主操作（新建版本/下载 PDF 就是踩过的坑）。
          const take = (label: string, solid = false): ReactElement | null => {
            // `标记已发送` / `登记再次发送` 是同一个位置的两副文案：
            // 发送过就显示"登记再次发送"，否则"标记已发送"
            if (label === '登记再次发送' && !byLabel.has('登记再次发送')) return null
            const item = byLabel.get(label)
            if (!item || used.has(label)) return null
            used.add(label)
            // ⚠️ 这些元素是**放进数组**渲染的，必须有 key ——
            // 少了它会报 "Each child in a list should have a unique key prop"，
            // CI 的 UI 冒烟把控制台报错当失败（我第一版就漏了）。
            // 主操作实心；危险动作保留红色、只去掉描边；其余统一成 type=tertiary
            // （Semi 里 theme="borderless" 对 primary 型按钮仍带一层浅底，tertiary 才是纯文字）。
            const isDanger = (item.element.props as { type?: string }).type === 'danger'
            return cloneElement(
              item.element as ReactElement<{ theme?: 'solid' | 'borderless'; type?: string }>,
              solid
                ? { key: label, theme: 'solid' }
                : isDanger
                  ? { key: label, theme: 'borderless' }
                  : { key: label, type: 'tertiary' },
            )
          }

          // ⚠️ 阶段由待办列表第一条决定。主数据/运费没确认时，第一条是"准备明细"
          // 或"确认费用"，`quoteActions(master)` 的主动作是 null —— 于是客户已经
          // 接受了、有订单权限，右上角却一个「转销售订单」都没有（2026-11-18 复验）。
          // 转单是这一版已经挣到的下一步，不能被"明细还没补全"的待办挤掉：
          // 只要条件成立就钉在最前，主数据警告照旧在页面下方单独展示。
          const convertReady = conditions['转销售订单'] === true && stage !== 'converted'

          const primaryLabel = convertReady
            ? '转销售订单'
            : primary
              ? (primary.handler === 'markSent' && version.sent_at ? '登记再次发送' : primary.label)
              : null
          if (primaryLabel) {
            const element = take(primaryLabel, true)
            if (element) rendered.push(element)
          }
          for (const item of secondary) {
            const element = take(item.label)
            if (element) rendered.push(element)
          }
          // 阶段相关的危险动作（客户拒绝）跟在次要动作后面，不放进折叠区
          if (conditions['客户拒绝'] && stage === 'sent') {
            const element = take('客户拒绝')
            if (element) rendered.push(element)
          }

          // 「新建版本」「下载 PDF」不再收进"更多操作"（2026-11-18 主人：
          // "这里也没必要折叠到更多操作里"）。它们是常用、可预期的动作，
          // 藏进下拉反而多一次点击；只有"一键刷新到最新适用价"这种低频、
          // 且要带原因说明的，才留在折叠区。
          for (const label of ['新建版本', '下载 PDF']) {
            const element = take(label)
            if (element) rendered.push(element)
          }

          // 折叠区：与当前阶段推进无关的低频入口
          const moreOrder = [...SECONDARY_ACTIONS]
          const more = moreOrder
            .filter((label) => byLabel.has(label) && !used.has(label))
            .map((label) => byLabel.get(label) as { label: string; element: ReactElement })
          // 缺项说明只给"用户会问为什么没有"的那一个：
          // 有价差、但这一版已经锁定（已发送/审批中/历史版本）时，直接说清下一步，
          // 不再笼统地写"当前版本/状态不可用"（2026-11-18 主人圈掉的就是这句）。
          const hasDrift = Boolean(driftQuery.data?.items?.some((it) => it.drift))
          const moreNotes: string[] = []
          if (hasDrift && !editable && !hasActiveOrder) moreNotes.push('当前报价版本已锁定，如需改价，请新建版本')

          // 折叠区没有可点的动作、也没有要说明的缺项时，整个「更多操作」不出现
          const hasMore = more.length > 0 || moreNotes.length > 0

          return (
            <div
              className="quote-actions"
              style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}
            >
              {rendered}
              {hasMore && (
                <Dropdown
                  trigger="click"
                  position="bottomRight"
                  render={
                    <Dropdown.Menu>
                      {more.map((action) => (
                        <Dropdown.Item key={action.label}>
                          {cloneElement(action.element, { key: action.label })}
                        </Dropdown.Item>
                      ))}
                      {/* 缺项说明照 `KnowledgePage` 的写法：不套 Item、不给 onClick */}
                      {moreNotes.map((note) => (
                        <li
                          key={note}
                          style={{
                            listStyle: 'none',
                            padding: '6px 12px',
                            fontSize: 12,
                            color: 'var(--crm-text-3)',
                            maxWidth: 260,
                            whiteSpace: 'normal',
                          }}
                        >
                          {note}
                        </li>
                      ))}
                    </Dropdown.Menu>
                  }
                >
                  <Button>更多操作</Button>
                </Dropdown>
              )}
            </div>
          )
        })()}

        {/* 版本行：左边是版本切换，右边放这一版的事实（什么时候发的、客户接没接）。
            原来这一行只有几个 V1/V2 按钮贴着左边，右边整条空着 —— 白白浪费一行高度。 */}
        <div className="quote-version-row">
          <div className="quote-version-tabs">
            <span style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>版本：</span>
            {(versionsQuery.data ?? []).map((item) => (
              <Button
                key={item.id}
                size="small"
                // 当前版本原来是一块实心主色按钮，在页头下面又压一个深色块。
                // 换成浅色底（light）：仍是选中态，但不跟「转销售订单」抢。
                theme={item.id === version.id ? 'light' : 'borderless'}
                onClick={() => setSearchParams({ version: String(item.id) })}
              >
                V{item.version_no}
              </Button>
            ))}
          </div>
          <div className="quote-version-meta">
            {version.approval_status === 'approved' && <span className="qvm-chip">审批通过</span>}
            {version.sent_at && (
              <span>已发送 {formatBeijingDate(version.sent_at)}</span>
            )}
            {version.accepted_at && (
              <span className="qvm-chip qvm-chip-ok">
                客户已接受 {formatBeijingDate(version.accepted_at)}
              </span>
            )}
            {version.declined_at && (
              <span className="qvm-chip qvm-chip-bad">客户已拒绝</span>
            )}
          </div>
        </div>

        {isCurrentVersion && detail.version_order_cancelled && (
          <p className="workflow-guide">此版的订单已取消。请新建报价版本，等客户重新确认后再下单。</p>
        )}
        {rejection && (
          <div className="workflow-next quote-approval-rejection">
            <div>
              <strong>报价审批已拒绝</strong>
              <p className="quote-approval-opinion">{rejectionRecord?.comment?.trim() || '审批人未填写意见。'}</p>
              {rejectionRecord && (
                <p className="workflow-guide">
                  {[rejectionRecord.approver_name, formatBeijingTime(rejectionRecord.created_at)].filter(Boolean).join(' · ')}
                </p>
              )}
              {isCurrentVersion && canManage && !hasActiveOrder && <p className="workflow-guide">新建版本后，在新版修改并重新提交审批。</p>}
            </div>
            {isCurrentVersion && canManage && !hasActiveOrder && (
              <Button type="primary" theme="outline" loading={versionMutation.isPending}
                onClick={() => versionMutation.mutate(false)}>新建版本</Button>
            )}
            {!isCurrentVersion && quote.current_version_id && allVersionIds.includes(quote.current_version_id) && (
              <Button theme="outline"
                onClick={() => setSearchParams({ version: String(quote.current_version_id) })}>打开当前版本</Button>
            )}
          </div>
        )}

        {/* 版本页签下面正是「按钮突然全没了」发生的地方：切到历史版本、或这一版已经发送/审批通过时，
            提交审批、批量录入、添加费用都会整块消失（判据 `canManage && editable`）。
            常驻一行说明它们为什么不在这儿、下一步该怎么做（规矩见 shared/components/BlockedHint.tsx）。 */}
        {versionEditBlockedReason && (
          /* 这块原来是橙底 + 项目符号 + 内边距的整块警示，在页头占掉整整一行高。
             它本质是一句「为什么改不了」，压成一行灰字就够了 —— 放在工具栏下面，
             不抢「现在这一步」的注意力。 */
          <BlockedHint
            compact
            reasons={[versionEditBlockedReason]}
            action="改这一版报价"
            // 紧凑模式默认封顶 260px（那是给表单行、弹窗底部这种窄地方定的），
            // 放在页头这条整宽的分隔线上就把一句话压成两行、右边空一大片。
            // 这里不封顶：整宽一行说完。
            style={{ maxWidth: 'none' }}
          />
        )}

      {/*
        「这一步还差什么」（2026-10-13 主人：把阻塞原因收成一个待办区）。
        ⚠️ 2026-11-16：原来它是页头下面**单独一张白卡**，页头一张、它一张，
        两张白卡贴着，中间还夹着锁版说明，读起来像页头被切成两半。
        现在收进页头卡内部，用一条分隔线接在动作行下面 ——
        "单号 + 状态 + 元信息 + 动作 + 差什么" 全在一步之内看全。
        判据全部来自 `shared/quote-todo.ts`（单一真源），页面只负责渲染与跳转 ——
        别在这里自己判一遍，否则两处会说法不一。
      */}
      {!detail.version_order_cancelled && quoteTodoList.some((todo) => todo.key !== 'approval_rejected') && (
        <div
          style={{
            marginTop: 16,
            paddingTop: 14,
            borderTop: '1px solid var(--crm-border, #E5E6EB)',
            fontSize: 13,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ fontWeight: 600 }}>现在这一步</span>
            <Tag color={quoteTodosBlocked ? 'orange' : 'blue'} size="small">
              {awaitingFinance && currentStage(quoteTodoList) === 'approving'
                ? '待财务会签'
                : currentStage(quoteTodoList) ? STAGE_LABEL[currentStage(quoteTodoList)!] : ''}
            </Tag>
            {stageProgress(currentStage(quoteTodoList)) && (
              <span style={{ color: 'var(--crm-text-3)' }}>
                第 {stageProgress(currentStage(quoteTodoList))!.step} 步 / 共{' '}
                {stageProgress(currentStage(quoteTodoList))!.total} 步
              </span>
            )}
          </div>
          {/* 运费那条从「现在这一步」里摘掉：页面下面紧跟着一条「待补运费 + 去补填 →」
              （带动作按钮、滚到附加费用），这里再列一遍同一句「尚未填写运费…」，
              两句话相隔 60px 说同一件事（2026-11-20 截图）。流程上与运费无关的
              其它处置项照旧列。 */}
          {quoteTodoList
            .filter((todo: QuoteTodo) => todo.key !== 'approval_rejected' && !(freightUnconfirmed && todo.key === 'freight_unconfirmed'))
            .map((todo: QuoteTodo, index: number) => (
            <div
              key={todo.key}
              className={todo.key === 'approval_pending' ? 'quote-todo-row quote-approval-stage' : 'quote-todo-row'}
              // 原来「去处理」「下一步：…」直接挂在长句末尾，句子一长就把两个标签
              // 顶到最右边、和正文挤在同一条基线上。现在说明独占左侧、可换行，
              // 两个标签收进右侧固定列，永远贴右边对齐。
              style={{ color: index === 0 ? 'var(--crm-text-1)' : 'var(--crm-text-3)' }}
            >
              <span className="quote-todo-dot">·</span>
              <span className="quote-todo-reason">
                {todo.key === 'approval_pending' && (awaitingFinance || assignedApprover) ? (
                  <>
                    {awaitingFinance ? '业务审批已通过，等待财务会签。' : ''}
                    {assignedApprover ? `当前由「${assignedApprover}」处理。` : ''}
                    全部审批通过后才能发给客户。
                  </>
                ) : todo.reason}
              </span>
              <span className="quote-todo-side">
                {todo.action && index === 0 && todo.key !== 'approval_pending' && (
                  <span className="qt-next">下一步：{todo.action}</span>
                )}
                {/* 能点的必须给去处：只说明"差什么"而不给入口，等于把问题换个地方放着 */}
                {todo.anchor && (
                  <button type="button"
                    className="crm-text-action qt-link"
                    onClick={() => {
                      document.getElementById(todo.anchor as string)?.scrollIntoView({
                        behavior: 'smooth',
                        block: 'center',
                      })
                    }}
                  >
                    去处理
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      </DetailHeader>

      <Modal
        title="修改付款条件"
        visible={Boolean(canManage && editable && paymentTermsDraft?.versionId === version.id)}
        onCancel={() => {
          if (!savePaymentTerms.isPending) setPaymentTermsDraft(null)
        }}
        onOk={() => {
          const text = paymentTermsDraft?.text.trim()
          if (!paymentTermsDraft || !text || [...text].length > PAYMENT_TERMS_MAX_LENGTH) return
          savePaymentTerms.mutate({ versionId: paymentTermsDraft.versionId, text })
        }}
        okText="保存"
        confirmLoading={savePaymentTerms.isPending}
        okButtonProps={{
          disabled: !paymentTermsDraft?.text.trim()
            || [...paymentTermsDraft.text.trim()].length > PAYMENT_TERMS_MAX_LENGTH,
        }}
      >
        <FormLabel required><label htmlFor="quote-payment-terms">付款条件</label></FormLabel>
        <TextArea
          id="quote-payment-terms"
          aria-required
          aria-describedby="quote-payment-terms-help"
          rows={2}
          value={paymentTermsDraft?.text ?? ''}
          disabled={savePaymentTerms.isPending}
          placeholder="例如：先付 30% 定金，发货前付清尾款"
          onChange={(text) => setPaymentTermsDraft((draft) => draft ? { ...draft, text } : null)}
        />
        <p className="settings-help" id="quote-payment-terms-help">
          仅修改这版报价，所填内容会出现在对客报价单上。最多 {PAYMENT_TERMS_MAX_LENGTH} 个字。
        </p>
      </Modal>
      <Modal
        title={`确认删除报价「${quote.quote_no}」？`}
        visible={deleteVisible}
        okText="确认删除"
        cancelText="取消"
        okType="danger"
        confirmLoading={deleteMutation.isPending}
        cancelButtonProps={{ disabled: deleteMutation.isPending }}
        closable={!deleteMutation.isPending}
        maskClosable={false}
        onCancel={() => { if (!deleteMutation.isPending) setDeleteVisible(false) }}
        onOk={() => { if (!deleteMutation.isPending) deleteMutation.mutate() }}
      >
        删除的是整张报价单，包含所有版本，不是仅删除当前查看的版本。
        删除后将从报价列表移除，历史版本和审计记录仍保留。已关联订单的报价不能删除。
      </Modal>

      {(detail.cost_problems ?? []).length > 0 && (
        <div id="quote-cost-status">
        <SectionCard title="成本未完整确认：当前只能保存草稿" style={{ marginBottom: 16 }}>
          <p>补齐成本前不能提交审批、正式发送、对客导出或转订单；未填写不等于成本为零。</p>
          {detail.cost_problems!.map((problem) => (
            <div key={problem.item_id} style={{ marginBottom: 8 }}>
              {problem.label}：{problem.reason}。
              {problem.sku_id && can('price:manage') ? (
                <Link to={`/prices?tab=costs&sku_id=${problem.sku_id}`}>去维护成本</Link>
              ) : problem.sku_id ? '请联系价格管理员补齐成本。' : '请核对定制明细的成本。'}
            </div>
          ))}
          {canManage && editable ? (
            <>
              <p>在价格中心保存完整成本后，点击下方更新本草稿；报价单价保持不变。</p>
              <Popconfirm title="用当前成本更新草稿？报价单价、数量保持不变。"
                onConfirm={() => costRefreshMutation.mutate()}>
                <Button loading={costRefreshMutation.isPending}>更新草稿成本</Button>
              </Popconfirm>
            </>
          ) : <p>已锁定或历史版本不会自动改写，请新建版本后核对成本。</p>}
        </SectionCard>
        </div>
      )}

      {/* §8.14 复审：主数据未确认要**常驻显示** —— 不是一闪而过的 Toast。
          刷新页面、隔天再打开、换个人来看，都该看到"这几条不能用做正式报价"。
          数据来自版本详情接口附带的 `master_warnings`（后端按"明细引用的那一版
          快照里有没有名称/规格/单位"算出来的）。 */}
      {(detail.master_warnings ?? []).length > 0 && (
        <div className="quote-master-warning">
          <div className="qmw-main">
            <div className="qmw-title">这几条明细的主数据还没确认，不能用来做正式报价</div>
            {/* 每一条明细在这里原样复述一遍，和上方「现在这一步」第一条是一字不差的两段长句，
                读完两遍才知道说的是同一件事（2026-11-20 截图）。这里只留处数和去哪看。 */}
            <div className="qmw-item">
              {(detail.master_warnings ?? []).length} 处不一致，逐条说明见上方「现在这一步」。
            </div>
          </div>
          {/* 入口放到右侧垂直居中：原来按钮压在文案最后一行下面，整条看着又长又挤。
              现在"说明在左、动作在右"，一眼知道下一步点哪儿。 */}
          <div className="qmw-side">
            <Button type="primary" size="small" theme="outline" onClick={() => setMasterPreviewOpen(true)}>
              已确认？重新核价接入
            </Button>
          </div>
        </div>
      )}


      {/*
        主数据刷新预览（issue 建议第 5 条）：**先看再刷**。
        名称/规格/单位是印给客户的，刷新会改掉它们 —— 直接刷完再发现问题
        比先看一眼贵得多。已发送/已审批的版本不给刷（内容是对客承诺），
        这里如实说明并引导新建版本。
      */}
      {/*
        转单确认 + 收款方式（2026-10-13 改动三）。
        `gateMessage` 那条路保留：后端返回 42206 时（例如别的入口触发）仍会弹它。
      */}
      <Modal
        title="转销售订单"
        className="receivable-plan-modal"
        visible={convertVisible}
        onCancel={() => setConvertVisible(false)}
        onOk={() => convertMutation.mutate(true)}
        confirmLoading={convertMutation.isPending}
        okText="生成订单"
        okButtonProps={{ disabled: planNotReady }}
        width={680}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        footer={
          <div>
            <div className="workflow-modal-actions">
              <Button onClick={() => setConvertVisible(false)}>取消</Button>
              <GuardedButton
                theme="solid"
                loading={convertMutation.isPending}
                disabled={planNotReady}
                blockedReasons={planBlocked}
                action="生成订单"
                hintBelow
                onClick={() => convertMutation.mutate(true)}
              >
                生成订单
              </GuardedButton>
            </div>
          </div>
        }
      >
        <div className="receivable-picker">
          <p className="workflow-guide">
            将按当前报价版本的全部明细生成正式订单，金额为{' '}
            <strong>{formatMoney(version?.total_amount_exact ?? version?.total_amount ?? 0, 'CNY')}</strong>。
          </p>
          <ReceivablePlanPicker
            totalAmount={version?.total_amount_exact ?? version?.total_amount ?? 0}
            currency="CNY"  /* 口径：系统只做人民币（版本上没有 currency 字段，别猜） */
            mode={planMode}
            onModeChange={setPlanMode}
            rows={planRows}
            onRowsChange={setPlanRows}
          />
          <p className="workflow-guide">确认收款安排后点「生成订单」。系统会打开订单详情，下一步到「应收计划」核对各期金额和到期日。</p>
        </div>
      </Modal>

      <ConfirmRequiredModal
        visible={Boolean(gateMessage)}
        message={gateMessage}
        onCancel={() => {
          setGateMessage('')
          setGateRetry(null)
        }}
        onConfirm={() => {
          const retry = gateRetry
          setGateMessage('')
          setGateRetry(null)
          retry?.()
        }}
      />

      <Modal
        visible={masterPreviewOpen}
        footer={null}
        onCancel={() => setMasterPreviewOpen(false)}
        width={680}
        style={{ maxWidth: 'calc(100vw - 48px)' }}
        header={
          <div className="crm-mp-header">
            <span className="crm-mp-icon" aria-hidden="true">⟳</span>
            <div>
              <div className="crm-mp-title">接入主数据：会变什么</div>
              <div className="crm-mp-sub">只换名称 / 规格 / 单位和引用的版本；手工价不动</div>
            </div>
          </div>
        }
      >
        {masterPreviewQuery.isLoading && <div className="crm-mp-loading">正在比对…</div>}
        {masterPreviewQuery.data && (
          <div className="crm-mp-body">
            <div className="crm-mp-summary">
              <span className="crm-mp-count">
                {masterPreviewQuery.data.changed_count > 0
                  ? `${masterPreviewQuery.data.changed_count} 条明细会因刷新而改变`
                  : '没有需要变化的明细'}
              </span>
              {/* 后端 message 和上面这条胶囊是同一句话（"1 条明细会因刷新而改变"），
                  两遍并排出现，看起来像重复渲染。只有 message 说了别的事才显示，
                  口径不变、只是不重复。 */}
              {masterPreviewQuery.data.message
                && !masterPreviewQuery.data.message.includes('会因刷新而改变')
                && (
                  <span className="crm-mp-summary-hint">{masterPreviewQuery.data.message}</span>
                )}
            </div>
            {!masterPreviewQuery.data.refreshable && (
              <div className="crm-mp-blocked">
                <span className="crm-mp-blocked-icon" aria-hidden="true">!</span>
                <span>{masterPreviewQuery.data.blocked_reason}</span>
              </div>
            )}
            {masterPreviewQuery.data.items
              .filter((it) => it.changes.length > 0 || it.never_referenced)
              .map((it, index) => (
                <div
                  key={it.item_id}
                  className="crm-mp-item"
                  style={{ animationDelay: `${index * 60}ms` }}
                >
                  <div className="crm-mp-item-head">
                    <span className="crm-mp-sku">{it.sku_code}</span>
                    <span className="crm-mp-item-tag">明细</span>
                  </div>
                  {it.never_referenced && (
                    <div className="crm-mp-note">
                      这条明细还没有引用过任何已确认版本（刷新会引用最新已确认版本）
                    </div>
                  )}
                  {it.changes.length > 0 && (
                    <div className="crm-mp-rows">
                      {it.changes.map((c) => (
                        <div key={c.field} className="crm-mp-row">
                          <span className="crm-mp-label">{c.label}</span>
                          {/* 旧值划一道线，新值用主色 + 一次高亮扫过：一眼看出"换成什么" */}
                          <span className={c.before ? 'crm-mp-before crm-mp-strike' : 'crm-mp-before'}>
                            {c.before ?? '（空）'}
                          </span>
                          <span className="crm-mp-arrow" aria-hidden="true">→</span>
                          <span className="crm-mp-after">{c.after ?? '（空）'}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            {masterPreviewQuery.data.changed_count === 0 && (
              <div className="crm-mp-empty">没有需要变化的明细。</div>
            )}
            <div className="crm-mp-footer">
              <Button onClick={() => setMasterPreviewOpen(false)}>取消</Button>
              <Popconfirm
                title="确认接入？"
                content="会把明细的名称/规格/单位换成上面显示的新值、引用最新的已确认主数据版本；系统带价的明细同时刷到当前适用价（手工价不动）。"
                onConfirm={() => {
                  refreshMutation.mutate()
                  setMasterPreviewOpen(false)
                }}
              >
                <Button
                  type="primary"
                  disabled={!masterPreviewQuery.data.refreshable}
                  loading={refreshMutation.isPending}
                >
                  确认刷新
                </Button>
              </Popconfirm>
            </div>
          </div>
        )}
      </Modal>

      {version.approval_status === 'pending' && (
        <div style={{ marginBottom: 16 }}>
          <Tag color="orange" size="large">
            该版本正在审批中，审批通过前不能发送
          </Tag>
        </div>
      )}
      {editable && driftQuery.data?.any_drift && (
        <div
          style={{
            marginBottom: 16,
            background: 'var(--crm-warning-soft)',
            border: '1px solid var(--crm-warning, #fa8c16)',
            borderRadius: 6,
            padding: '10px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <span style={{ flex: 1 }}>
            价格已有更新：{driftQuery.data.items.filter((it) => it.drift).length} 条明细的适用价与当前拟报价不一致
            （手工改价的明细不会被自动覆盖）
          </span>
          <Button
            size="small"
            theme="outline"
            type="primary"
            loading={refreshMutation.isPending}
            onClick={() => refreshMutation.mutate()}
          >
            一键刷新到最新适用价
          </Button>
        </div>
      )}
      {/* 运费未确认要**持续显示**（与主数据未确认同一性质）：草稿允许没填运费，
          但正式发送会被拦下 —— 等到点"标记已发送"才报错就太晚了。 */}
      {/* 运费未确认要**持续显示**（与主数据未确认同一性质）：草稿允许没填运费，
          但正式发送会被拦下 —— 等到点「标记已发送」才报错就太晚了。
          2026-11-20：原来这里是一枚橙色 Tag，内容是后端给的那句
          "尚未填写运费：请在「附加费用」里以「物流」类型填写…" ——
          和页顶「现在这一步」的第二条**一字不差**，同一句话隔着 120px 出现两遍，
          而且贴着明细区、挨着又没动作，看完还得自己往下找「附加费用」。
          现在压成一条：短句说清后果 + 右边直接给「去补填」（滚到附加费用并开弹窗）。 */}
      {freightUnconfirmed && (
        <div
          style={{
            marginBottom: 16,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '10px 14px',
            borderRadius: 10,
            background: 'var(--crm-surface-low)',
            borderLeft: '3px solid var(--crm-warning)',
            fontSize: 12.5,
            color: 'var(--crm-text-2)',
          }}
        >
          <Tag size="small" type="light" color="orange">待补运费</Tag>
          <span>这版还没填运费，正式发送前必须补上。</span>
          <span style={{ flex: 1 }} />
          {canManage && (editable || canEditFreightOnly) && (
            <Button
              size="small"
              type="tertiary"
              theme="borderless"
              onClick={() => {
                if (!editable) setChargeForm((form) => ({ ...form, charge_type: 'logistics' }))
                setChargeVisible(true)
                document.getElementById('quote-charges')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
              }}
            >
              去补填 →
            </Button>
          )}
        </div>
      )}

      <SectionCard
        id="quote-items"
        title="报价明细"
        style={{ marginBottom: 16 }}
        extra={
          <div className="quote-actions" style={{ display: 'flex', gap: 8 }}>
          {/* 明细这块的推进动作是「按此版本建订单草稿」，给它实心；
              打样、批量录入是并列入口，收成文字按钮（2026-11-19）。 */}
          {can('order:manage') && (
            <Button theme="solid" size="small" onClick={() => setDraftVisible(true)}>
              按此版本建订单草稿
            </Button>
          )}
          {can('sample:manage') && (
            <Button size="small" type="tertiary" onClick={() => setSampleVisible(true)}>
              按此版本申请打样
            </Button>
          )}
          {canManage &&
          editable && (
            <Button size="small" type="tertiary" onClick={openItemsEditor}>
              批量录入
            </Button>
          )}
          </div>
        }
      >
        {/* 窄屏（681px）下让滚动发生在表格自己身上，而不是把列压扁到只剩省略号 */}
        <div className="table-x-scroll">
        <Table<QuoteItemRow>
          columns={itemColumns}
          dataSource={detail.items}
          rowKey="id"
          pagination={false}
          className="quote-items-table"
          /* 成本 / 建议价 / 最低保护价 是内部核价参考，不是对客户的报价字段。
             它们在列表里常驻要占三列（110 + 100 + 110），十列一起把整张表顶出
             容器、右下角「操作」被裁掉。挪进点行展开：要核对毛利的人点左侧箭头，
             成本口径、建议价、保护价、成本异常说明都在里面，列表只留报价字段。 */
          /* 展开触发器不在图标位，而是「操作」列里的「展开 / 收起」（2026-11-17 主人：
             不喜欢小三角）。所以把 Semi 那列藏掉，展开状态由本页的 expandedItemIds 控制。 */
          hideExpandedColumn
          expandedRowKeys={expandedItemIds}
          onExpandedRowsChange={(keys) =>
            // 这里没有分组行，keys 一定是 rowKey（明细 id）
            setExpandedItemIds(keys as unknown as (string | number)[])
          }
          expandedRowRender={(record?: QuoteItemRow) => {
            if (!record) return null
            const costValue =
              record.cost_snapshot == null
                ? null
                : legacyBasis
                  ? record.cost_snapshot + (record.logistics_cost_snapshot ?? 0)
                  : record.cost_snapshot
            const problem = detail.cost_problems?.find((p) => p.item_id === record.id)
            // 展开区原来是「整行灰底 + 三行字挤在中间」，左右各有大半屏空白：
            // 看着像页面没加载完（主人 2026-11-17 截图指出「折叠很丑」）。
            // 改成横向的一条核算依据：小标题在左，三组「标签 + 金额」并排，
            // 数字用等宽的感觉（右对齐不需要，这里是一句话不是账表）。
            const facts: { label: string; value: string }[] = [
              {
                label: legacyBasis ? '成本快照（含运费）' : '商品成本',
                value: costValue == null ? '-' : formatUnitPrice(costValue, 'CNY'),
              },
              {
                label: '建议价',
                value:
                  record.recommended_price_snapshot == null
                    ? '-'
                    : formatUnitPrice(record.recommended_price_snapshot_exact ?? record.recommended_price_snapshot, 'CNY'),
              },
              {
                label: '最低保护价',
                value:
                  record.minimum_price_snapshot == null
                    ? '-'
                    : formatUnitPrice(record.minimum_price_snapshot, 'CNY'),
              },
            ]
            return (
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'baseline',
                  justifyContent: 'flex-start',
                  // 宽度必须写满：单元格默认 text-align center，不写 width 时
                  // 这行字会飘在整行灰带的中间（主人说像没加载完）
                  width: '100%',
                  gap: '4px 32px',
                  padding: '2px 0 2px 2px',
                  textAlign: 'left',
                  fontSize: 12.5,
                  lineHeight: 1.8,
                }}
              >
                <span style={{ color: 'var(--crm-text-3)' }}>这条怎么核出来的</span>
                {facts.map((f) => (
                  <span key={f.label} style={{ whiteSpace: 'nowrap' }}>
                    <span style={{ color: 'var(--crm-text-3)' }}>{f.label} </span>
                    {f.label === '最低保护价' && <TermHelp term="最低保护价" />}
                    <span style={{ color: 'var(--crm-text)', fontWeight: 600 }}>{f.value}</span>
                  </span>
                ))}
                {problem && (
                  <span style={{ color: 'var(--crm-error)' }}>
                    <span style={{ color: 'var(--crm-text-3)' }}>成本问题 </span>
                    {problem.reason}
                  </span>
                )}
              </div>
            )
          }}
          rowExpandable={() => true}
          empty="没有明细"
        />
        </div>
      </SectionCard>

      {draftVisible && <SampleFromSourceModal mode="order" source={{ quote_version_id: version.id }} onClose={() => setDraftVisible(false)} />}
      {sampleVisible && <SampleFromSourceModal source={{ quote_version_id: version.id }} onClose={() => setSampleVisible(false)} />}

      {/* What-if：版本方案对比 + 边际测算（设计稿 Sales Copilot 右栏那个滑杆）。
          这两块是「准备改价、要比较几套方案」时才看的工具，不是每天都要看的
          主流程信息；原来常驻展开，详情页一屏就叠了七块（状态、步骤、警示、
          明细、两套 what-if、附加费用……）。收进折叠区，默认收起，
          需要时点开 —— 内容一个字没减。 */}
      {/* 上边距交给上一张卡自己的 margin-bottom，这里不再加：
          两边各加 16 会叠成 32，比其它卡片之间的 16 空一倍（2026-11-17 实测）。 */}
      {/* 折叠触发器换成文字（2026-11-17 主人：不喜欢小三角，不是不喜欢折叠）。
          Semi 的默认触发器是一个小三角，收起/展开只靠它转向来区分，太小、也不
          说明"点了会发生什么"。这里改成右侧一颗「展开 / 收起」的文字键：
          收起时写「展开」，展开时写「收起」，按下就是换一边。
          为了知道当前状态，Collapse 改成受控（activeKey + onChange）。 */}
      <Collapse
        className="quote-whatif-collapse"
        activeKey={whatifOpen}
        onChange={(keys) => {
          const arr = Array.isArray(keys) ? keys : keys ? [keys] : []
          setWhatifOpen(arr as string[])
        }}
      >
        <Collapse.Panel
          itemKey="whatif"
          /* 收起时别做"盲盒"（2026-11-17 主人说「折叠之后什么都看不见，
             却还可以点，不就是盲点吗」）—— 把里面最关心的几个数摆到标题行右侧：
             几个版本、本版均价、毛利率。收起状态下先看到数，再决定要不要点开；
             点开是为了对比和试算，不是为了看这几个数。
             ⚠️ 不能把数字放进 `extra`：Semi 这个版本的 `renderHeader` 只在
             `typeof header === 'string'` 时才渲染 `extra`，传 ReactNode 会被直接丢掉
             （读 node_modules/@douyinfe/semi-ui/lib/es/collapse/item.js 确认）。
             所以这里在 header 内部自己排：左边标题、右边数字，用 flex 两端对齐。 */
          header={
            /* 窄屏（461px）下左侧标题被右侧「3 个版本 / 均价 / 毛利率」挤成一字一行。
               这里允许换行、标题不折字：标题占一行，那几个数整组掉到下一行。 */
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: 12, flexWrap: 'wrap' }}>
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'baseline',
                  flexWrap: 'wrap',
                  columnGap: 8,
                  rowGap: 2,
                  fontSize: 14,
                  fontWeight: 600,
                  minWidth: 0,
                }}
              >
                <span style={{ whiteSpace: 'nowrap' }}>版本与方案对比 · 边际测算（What-if）</span>
                <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--crm-text-3)' }}>
                  要改价、要比较几套方案时点开看
                </span>
              </span>
              <span
                className="quote-whatif-summary"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 16,
                  fontSize: 12,
                  color: 'var(--crm-text-3)',
                  flexShrink: 0,
                  // 右侧紧挨着「展开 / 收起」文字键，留一点呼吸
                  marginRight: 4,
                }}
              >
                <span>
                  <b style={{ fontWeight: 600, color: 'var(--crm-text-2)' }}>{(versionsQuery.data ?? []).length}</b>
                  {' 个版本'}
                </span>
                <span>
                  {'均价 '}
                  {/* 均价是单价，同样保真：33.3333 的均价不能显示成 33.33 */}
                  <b style={{ fontWeight: 600, color: 'var(--crm-text-2)' }}>{formatUnitPrice(whatifUnitPrice, 'CNY')}</b>
                </span>
                <span>
                  {'毛利率 '}
                  <b
                    style={{
                      fontWeight: 600,
                      color:
                        version.total_profit == null || !version.subtotal_amount
                          ? 'var(--crm-text-3)'
                          : version.total_profit / version.subtotal_amount < 0
                            ? 'var(--crm-error, #e34d59)'
                            : 'var(--crm-success, #00b42a)',
                    }}
                  >
                    {/* 分母必须用**货款**（subtotal_amount），不能用含运费的 total_amount：
                        运费是代收代付，不进利润口径。用 total_amount 算出来是 29.94%，
                        而上方明细表里同一版显示 30.00%，同屏两个毛利率会让人以为哪个算错了。 */}
                    {version.total_profit == null || !version.subtotal_amount
                      ? '-'
                      : `${((version.total_profit / version.subtotal_amount) * 100).toFixed(2)}%`}
                  </b>
                </span>
              </span>
              {/* 折叠触发器：文字键代替小三角。收起时是「展开」，展开时是「收起」。 */}
              <span
                style={{
                  flexShrink: 0,
                  fontSize: 12,
                  fontWeight: 600,
                  color: 'var(--crm-primary)',
                  cursor: 'pointer',
                  padding: '2px 6px',
                  borderRadius: 4,
                  whiteSpace: 'nowrap',
                }}
                className="expand-toggle"
              >
                {whatifOpen.includes('whatif') ? '收起' : '展开'}
              </span>
            </div>
          }
        >
          <WhatIfPanel quoteId={quoteId} versionId={version.id} items={detail.items} />
        </Collapse.Panel>
      </Collapse>

      {/* 三张卡不等宽：附加费用那张表有 4 列（类型/说明/金额/操作），
          等宽 333 塞不下 —— 表体 516 撑出卡片，底下挂一条横向滚动条，
          「金额」整列被推到卡片外面（2026-11-16 发现）。按 1.7 : 1 : 1 分，
          附加费用拿到约 490，另外两张各约 288（金额汇总都只有"标签 + 数字"、
          决策关系图此时是空态，288 足够）。minmax(0, …) 是防止内容把列顶宽。 */}
      {/* 三张卡改成**两列**：左边两卡上下叠（附加费用 + 客户决策关系图），
          右边一卡（金额汇总）。
          原来三张并排、强制等高：内容少的附加费用和决策图下方各留一大块空白；
          改成 alignItems:start 让各自收高，底边又参差不齐、下方同样一条空白带。
          这两个毛病都是"三张卡内容量差太多还硬要并排"造成的 ——
          左列两张叠起来总高约 320，跟右列金额汇总（约 290）自然接近，
          空白没了，也不用手动补内容。
          （2026-11-16 主人圈出这块问"这个改了吗"。） */}
      {/* 列宽交给 index.css 的 .quote-money-block：宽屏 1.7:1.0、窄屏（≤760px）单列。
          原来列宽写死在内联样式里，窄屏也不会塌成单列，两栏各剩约 180px，
          右栏「应付合计」被压成逐字竖排（2026-11-17 461px 实拍）。 */}
      <div className="quote-money-block">
        <SectionCard
          id="quote-charges"
          title="附加费用"
          /* 撑满整卡高度，让「费用合计」落在这栏的底边 ——
             两栏并排时右栏（金额汇总）通常更高，左栏若按内容收高，
             底下会空出一截（2026-11-16 主人说"那一块突出来了"）。 */
          /* 不再撑满行高：两张卡各自按内容收高，费用合计自然落在自己卡内，
             不会压到卡底边；两卡底边不齐也没关系，比"贴边"好看。 */
          style={{ display: 'flex', flexDirection: 'column' }}
          extra={
            canManage &&
            (editable || canEditFreightOnly) && (
              // ⚠️ 锁版之后这个入口**照旧露出来**，只改说法（2026-10-12）：
              // 从前它挂在 `editable` 上，于是一旦版本锁死入口就整块消失，
              // 而"补运费"恰恰是锁版之后唯一还允许、也常常必须做的事 ——
              // 藏掉它，用户就只能新建版本重走审批（那个死胡同）。
              <Button
                size="small"
                onClick={() => {
                  // 锁版之后只可能填运费，直接预选上 —— 别让人先选一次分类
                  if (!editable) setChargeForm((form) => ({ ...form, charge_type: 'logistics' }))
                  setChargeVisible(true)
                }}
              >
                {editable ? '添加费用' : '补填运费'}
              </Button>
            )
          }
        >
          <Table<QuoteChargeRow>
            columns={[
              { title: '类型', dataIndex: 'type_label', width: 90 },
              {
                // 运费要能一眼看出"确认了没有"：金额 0 不等于已确认零运费，
                // 空输入与明确零运费必须分得开（正式发送要求后者）。
                title: '说明',
                dataIndex: 'description',
                // 写死宽度：这一列原来不写 width，靠内容自适应，「演示运费」后面
                // 还挂「运费已确认」标签，一撑就把 4 列推到 516（卡内只有 ~486），
                // 底下挂横滚条。90 + 165 + 120 + 100 = 475，卡内约 505，留 30px 余量
                // （Semi 摊平列宽时会吃掉几个像素，「改 删除」也要 100 才不把"除"切掉）。
                width: 165,
                render: (v: string | null, record: QuoteChargeRow) => (
                  <span>
                    {v ?? '-'}
                    {record.is_logistics &&
                      (record.logistics_confirmed_at ? (
                        <Tag size="small" type="light" color="green" style={{ marginLeft: 6 }}>
                          运费已确认
                        </Tag>
                      ) : (
                        <Tag size="small" type="light" color="orange" style={{ marginLeft: 6 }}>
                          待确认运费
                        </Tag>
                      ))}
                  </span>
                ),
              },
              {
                title: '金额',
                dataIndex: 'amount',
                width: 120,
                // 金额右对齐（与明细表、报价列表同一口径）：这一列漏了 .cell-right，
                // ¥30.00 与 ¥1,200.00 各站在格子中间，两行比不出大小（2026-11-19）。
                align: 'right' as const,
                className: 'cell-right',
                // 「（代收代付）」原来跟在金额后面一行写：「¥30.00（代收代付）」要 154px，
                // 把 120 的列宽撑到 154，整张表超出卡片、底下挂滚动条，
                // 「删除」按钮被推到卡片外面看不见。拆成两行，口径说明用灰色小字放下面。
                render: (v: number, record: QuoteChargeRow) =>
                  record.is_logistics ? (
                    <div style={{ lineHeight: 1.35 }}>
                      <div>{cny(record.amount_exact ?? v)}</div>
                      <div style={{ fontSize: 11, color: 'var(--crm-text-3)' }}>代收代付</div>
                    </div>
                  ) : (
                    cny(record.amount_exact ?? v)
                  ),
              },
              {
                // 锁版之后这一列**照旧露出运费的那两个动作**（2026-10-14 修）。
                //
                // 后端一直允许"改运费 / 删运费"（`ensure_charge_change_allowed`
                // 只要改前改后都是运费就放行），而从前这一列整个挂在 `editable` 上：
                // 版本一审批通过就只剩「补填运费」，用户想录真实运费**只能再加一条**，
                // 多条运费会被累加进总额 —— 那张「订单 501 / 报价 581」就是这么来的。
                // 判据与「补填运费」按钮同源（`canEditFreightOnly`），没另立一套。
                // 表头原来留空（title: ''）—— 一张表的最后一列没有列名，
                // 只能靠"改/删除"这两个词猜它是操作列；补上「操作」。
                title: '操作',
                width: 100,
                render: (_: unknown, record: QuoteChargeRow) => {
                  if (!canManage) return null
                  const touchable = editable || (Boolean(record.is_logistics) && canEditFreightOnly)
                  if (!touchable) return null
                  return (
                    <span style={{ display: 'inline-flex', gap: 10 }}>
                      <button type="button" className="crm-text-action" onClick={() => openChargeEditor(record)}>改</button>
                      <Popconfirm
                        title="删除这条费用？"
                        onConfirm={() => deleteQuoteCharge(record.id).then(refresh)}
                      >
                        <button type="button" className="crm-text-action" style={{ color: 'var(--crm-error)' }}>删除</button>
                      </Popconfirm>
                    </span>
                  )
                },
              },
              {
                // 弹性空列：四列都是写死宽度（90+165+120+100=475），
                // 宽屏下 Semi 会把剩余宽度**平均摊到每一列**，于是「类型」一列
                // 拉到 220px、表头和"物流"两个字浮在中间，四列之间全是空档。
                // 补一个不写宽度的空列吃掉剩余空间，四个真实列保持原宽、
                // 内容从左边排起；窄屏（卡内 <475）时它自动归零，不产生横滚条。
                title: '',
                dataIndex: '__spacer',
                render: () => null,
              }
            ]}
            dataSource={detail.charges}
            rowKey="id"
            // 列宽合计 306 + 说明列，卡片内宽 500 左右时会差几个像素，
            // 底下就挂一条横向滚动条。scroll.x='100%' 让各列按卡片宽度摊平。
            scroll={{ x: '100%' }}
            pagination={false}
            empty="没有附加费用"
          />
          {/* 表格下方补一行合计：这张卡比右边「金额汇总」矮一截，底边对齐后
              会留一块空白；把"费用合计"摆在末尾，既填了空，也是个该有的数字。 */}
          {/* 口径说明（2026-10-09 运费分离）：产品单价不含运费，运费按已确认的
              实际金额代收代付。从右边"金额汇总"挪到这一栏 ——
              它讲的就是运费口径，跟运费明细放一起才讲得通；
              顺带把左右两栏的内容高度拉平（2026-11-16）。 */}
          {!legacyBasis && (
            <div
              style={{
                color: 'var(--crm-text-3)',
                fontSize: 12,
                lineHeight: 1.6,
                marginTop: 10,
              }}
            >
              产品单价不含运费。运费单列，按已确认的实际金额由本公司代收代付
              （公司不赚不赔，产品利润不受运费影响）。
            </div>
          )}
          {detail.charges.length > 0 && (
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'baseline',
                marginTop: 'auto',
                paddingTop: 12,
                borderTop: '1px solid var(--crm-border)',
                fontSize: 13,
              }}
            >
              <span style={{ color: 'var(--crm-text-3)' }}>费用合计</span>
              <span style={{ fontWeight: 600 }}>
                {cny(detail.summary?.net_charge_amount_exact ?? summary.charge_amount + summary.discount_amount)}
              </span>
            </div>
          )}
        </SectionCard>

        {/* 两列等高（格子默认 stretch）：这一卡撑满整行高度，多出来的空间
            分给"明细"和"合计"之间 —— 明细在上、合计贴底。
            原来 alignItems:start，右列按内容收高，比左列矮 50px，
            底部齐不了，看起来就是"那一块突出来了"（2026-11-16 主人原话）。
            现在空白落在分隔线附近，读起来是呼吸，不是漏了一块。 */}
        <SectionCard title="金额汇总">
          <div style={{ display: 'grid', gap: 10, fontSize: 14 }}>
            <div style={{ display: 'grid', gap: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--crm-text-3)' }}>产品货款</span>
                <span>{cny(detail.summary?.goods_amount_exact ?? summary.goods_amount)}</span>
              </div>
              {/* 运费从"附加费用"里**单独一行**列具体金额，不再混在一起让客户自己猜。
                  注意：下面这几行都**已经含在**应付合计里，只是拆分展示，不再相加。 */}
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span className="crm-help-label" style={{ color: 'var(--crm-text-3)' }}>
                  运费（代收代付）
                  <HelpTip forLabel="运费" text="客户承担、公司代收代付的运费。按行单列给客户看，已计入下面的应付合计，不计入利润。" />
                </span>
                <span>{cny(detail.summary?.logistics_amount_exact ?? summary.logistics_amount)}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--crm-text-3)' }}>其他费用</span>
                <span>{cny(detail.summary?.other_charge_amount_exact ?? summary.other_charge_amount)}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--crm-text-3)' }}>优惠</span>
                <span>{cny(detail.summary?.discount_amount_exact ?? summary.discount_amount)}</span>
              </div>
            </div>
            {/* 合计贴底：marginTop:auto 吃掉多出来的高度 */}
            {/* 合计不再是一行裸字：浅灰底托住"应付合计 + 预计利润"两行，
                让整张卡有个明确的落点。原来四行明细和合计长得一样，
                扫一眼分不出哪个是"最终要付的钱"。 */}
            <div className="quote-summary-total">
              <div className="qst-main">
                <span className="qst-label">应付合计</span>
                <span className="qst-amount">{cny(version.total_amount_exact ?? summary.total_amount)}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--crm-text-3)', flexShrink: 0 }}>预计利润合计</span>
                {/* ⚠️ 无 `price:manage` 时后端会把 `total_profit` 置 null
                    （2026-10-14 审查修）—— 原来写 `?? 0` 会显示成「¥0.00」，
                    看着像"这单不赚钱"，而不是"你没权限看"。 */}
                <span style={{ textAlign: 'right', minWidth: 0, flex: '1 1 auto' }}>
                  {version.total_profit == null
                    ? can('price:manage') && (detail.cost_problems ?? []).length > 0
                      ? '成本未完整确认，暂不能计算利润'
                      : '无价格权限，不显示利润'
                    : cny(version.total_profit)}
                </span>
              </div>
            </div>
          </div>
        </SectionCard>

      </div>

      {/* 客户决策关系图：报价要发给谁、谁能拍板都在这。
          客户还没登记联系人时（0 位）收成一行注脚，不再占一整张卡 —
          "一张卡只装一句话"正是这块反复空着的原因（2026-11-16）。 */}
      <DecisionMakerCard
        customerId={quote.customer_id}
        title="客户决策关系图"
        footnoteOnEmpty
        // 上面那排「附加费用 / 金额汇总」是栅格，卡片自己不带外边距；
        // 这里补 16，和其余 SectionCard 的节奏一致（原来贴着底边）。
        marginTop={16}
      />

      {detail.approval && (
        <SectionCard title="审批记录" style={{ marginTop: 16 }}>
          <Table
            columns={[
              { title: '动作', dataIndex: 'action', width: 120,
                render: (v: string) =>
                  ({ submit: '提交申请', approve: '同意', reject: '拒绝', withdraw: '撤回', transfer: '转交' })[v] ?? '审批操作' },
              { title: '处理人', dataIndex: 'approver_name', width: 120, render: (v: string | null) => v ?? '-' },
              { title: '意见', dataIndex: 'comment', render: (v: string | null) => v ?? '-' },
              {
                title: '审批时间',
                dataIndex: 'created_at',
                width: 190,
                render: (v: string) => formatBeijingTime(v),
              },
            ]}
            dataSource={detail.approval.records}
            rowKey="id"
            pagination={false}
          />
        </SectionCard>
      )}

      {/* 关联询价的审批：只有明细里带 inquiry_id 才出现，纯现货报价看不到这张卡 */}
      {linkedInquiries.length > 0 && (
        <SectionCard title={<span className="crm-help-label">关联询价的审批进度
          <HelpTip forLabel="关联询价的审批进度" text="正式发送前，关联询价须完成核价和报价复核。后续打样、采购、备货可以继续进行。" />
        </span>} className="oa-inquiry-card" style={{ marginTop: 16 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 12 }}>
            「核价 / 报价复核」在同序 OA 的询价单上办理；这一层没通过时，正式对外发送仍会被拦。
          </div>
          {inquiryApprovalsQuery.isError && (
            <div className="inline-note" role="status">
              暂时无法确认关联询价的审批进度；这不影响保存草稿，请稍后重试。
            </div>
          )}
          {/* 不用表格：这里最多一两条，五列表格会把两三个词摊到一屏宽，
              两头都贴着卡片边，看着像被挤出来的一排字。
              改成"左主信息 + 右状态/动作"的两栏行，行与行之间一条细线。 */}
          <div className="oa-inquiry-list">
            {inquiryApprovalRows.map((row) => (
              <div className="oa-inquiry-row" key={row.key}>
                {/* 左边是一段"审批概况"，右边只有一个按钮：
                    状态贴编号，不再单独飘到右上角；按钮垂直居中，
                    不和状态标签在右上角上下挤成一对。 */}
                <div className="oa-inquiry-main">
                  <div className="oa-inquiry-head">
                    <span className="oa-inquiry-no">{row.inquiry.inquiryNo ?? '未编号需求'}</span>
                    {row.latest ? (
                      <Tag
                        color={row.latest.status === 'approved' ? 'green'
                          : row.latest.status === 'pending' ? 'orange'
                            : row.latest.status === 'rejected' ? 'red' : 'grey'}
                        type="light"
                      >
                        {row.latest.status_label}
                      </Tag>
                    ) : (
                      <Tag color="grey" type="light">未发起</Tag>
                    )}
                  </div>
                  <span className="oa-inquiry-meta">
                    {row.latest ? (
                      <>
                        {'当前阶段 '}
                        <strong>{row.stageText || (row.latest.status === 'pending' ? '同步后可见' : '—')}</strong>
                        {` · 第 ${row.latest.submit_round ?? 1} 轮 · 最近同步 ${row.latest.synced_at ? formatBeijingTime(row.latest.synced_at) : '从未同步'}`}
                      </>
                    ) : (
                      '还没有发起同序 OA 审批'
                    )}
                  </span>
                  {row.quotation?.approved && (
                    <span className="oa-stage-approved crm-help-label">
                      ✓ 核价 / 报价复核已通过
                      <HelpTip forLabel="核价与报价复核" text="同序 OA 里核价和报价复核两个节点都通过了；之后这份报价才能正式发给客户。" />
                    </span>
                  )}
                </div>
                {row.latest?.provider === 'self_oa' && canManage && (
                  <span className="oa-inquiry-action">
                    {<AutoOaSync
                      row={row.latest}
                      invalidateKeys={[
                        ['quote-linked-inquiry-approvals', linkedInquiryKey],
                        ['quote-version', versionId],
                        ['quote-versions', quoteId],
                      ]}
                    />}
                    <SyncOaButton
                      oaId={row.latest.id}
                      invalidateKeys={[
                        ['quote-linked-inquiry-approvals', linkedInquiryKey],
                        ['quote-version', versionId],
                        ['quote-versions', quoteId],
                      ]}
                    />
                  </span>
                )}              </div>
            ))}
          </div>
        </SectionCard>
      )}

      <Modal
        width={520}
        title={`改价：${priceTarget?.sku_code ?? ''}`}
        visible={Boolean(priceTarget)}
        onCancel={() => setPriceTarget(null)}
        onOk={() => {
          if (!newPrice.trim() || Number(newPrice) <= 0) {
            Toast.warning('请输入有效价格')
            return
          }
          priceMutation.mutate()
        }}
        confirmLoading={priceMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          {/* 三个参考价原来挤成一行「成本 ｜建议价 ｜最低保护价」，用竖线硬分隔；
              拆成三小段固定宽度，数字加粗，扫一眼就知道哪个是底线。 */}
          <div style={{ display: 'flex', gap: 20, fontSize: 12, color: 'var(--crm-text-3)' }}>
            <span>
              成本{' '}
              <strong style={{ color: 'var(--crm-text-2)' }}>
                {priceTarget?.cost_snapshot == null || priceTarget.logistics_cost_snapshot == null
                  ? '-'
                  : formatUnitPrice(priceTarget.cost_snapshot + priceTarget.logistics_cost_snapshot, 'CNY')}
              </strong>
            </span>
            <span>
              建议价{' '}
              <strong style={{ color: 'var(--crm-text-2)' }}>
                {/* 建议价原来直接印库里的四位小数（¥142.8571），和左右两栏的两位小数不一样 */}
                {priceTarget?.recommended_price_snapshot == null
                  ? '-'
                  : formatUnitPrice(priceTarget.recommended_price_snapshot, 'CNY')}
              </strong>
            </span>
            <span>
              最低保护价{' '}
              <strong style={{ color: 'var(--crm-text-2)' }}>
                {priceTarget?.minimum_price_snapshot == null
                  ? '-'
                  : formatUnitPrice(priceTarget.minimum_price_snapshot, 'CNY')}
              </strong>
            </span>
          </div>
          {/* 输入框只有 placeholder 自我介绍，点进去字就没了 —— 补固定标签 */}
          <div className="form-rows">
            <div className="form-row">
              <label className="form-row-label">实际报价</label>
              <div className="form-row-control">
                <Input value={newPrice} onChange={setNewPrice} placeholder="填数字，单位元" />
              </div>
            </div>
          </div>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            保存后会按你的价格权限重新判断是否需要审批。
          </div>
        </div>
      </Modal>

      <Modal
        title="批量录入明细"
        visible={itemsVisible}
        onCancel={() => setItemsVisible(false)}
        onOk={() => itemsMutation.mutate()}
        confirmLoading={itemsMutation.isPending}
        okText="保存整版明细"
        /* 固定 900 在 681px 视口下会顶出屏幕，改成跟着视口收窄 */
        width={Math.min(900, window.innerWidth - 24)}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            保存会「整版替换」这张报价版本的明细（上面那张表会按下面这份重建）。
            现货给 SKU 即可，系统用适用价；定制件必须填成本与报价——没成本算不出毛利，
            也判断不了低价审批。
          </div>
          {itemRows.map((row, index) => (
            <div
              key={index}
              style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}
            >
              <ItemField label="录入方式">
              <Select
                /* 96 时「现货 SKU」被截成「现货 S…」—— 这一格的选择决定了
                   后面那个下拉是选产品还是选定制需求，截断了就看不懂自己在选什么。
                   110 刚好放下五个字 + 箭头 + 内边距。 */
                style={{ width: 110 }}
                value={row.mode}
                onChange={(value) =>
                  patchRow(index, {
                    mode: value as ItemDraftRow['mode'],
                    sku_id: undefined,
                    inquiry_id: undefined,
                  })
                }
                optionList={[
                  { value: 'sku', label: '现货 SKU' },
                  { value: 'custom', label: '定制需求' },
                ]}
              />
              </ItemField>
              {row.mode === 'custom' ? (
                <>
                  <ItemField label="定制需求">
                  <RecordSearchSelect<CustomInquiryRow>
                    key={`quote-inquiry-${quoteId}-${index}-${quoteCustomerId}`}
                    scope={`quote-inquiry-${quoteCustomerId}`}
                    enabled={itemsVisible && Boolean(quoteCustomerId)}
                    placeholder={customerHasInquiries ? '输入需求标题或编号搜索' : '本客户暂无可选定制需求'}
                    value={row.inquiry_id}
                    fallbackLabel={row.inquiry_id ? `原关联需求 编号 ${row.inquiry_id}` : undefined}
                    list={(input) => listCustomInquiries({ ...input, customer_id: quoteCustomerId })}
                    labelOf={(item) => `${item.inquiry_no ?? `编号 ${item.id}`} · ${item.title}`}
                    accept={inquiryAccepted}
                    onChange={(item) => patchRow(index, { inquiry_id: item?.id })}
                    style={{ width: 240 }}
                  />
                  </ItemField>
                  {/* 下拉只列**本客户**的需求（2026-10-12 主人提问核实）：空的时候要说清
                      是"这个客户还没有需求"，而不是让用户以为系统里没数据、或者去别处找。
                      改成服务端搜索后，依据总数判断空态，不再看"第一页有没有"。 */}
                  {!inquiriesCountQuery.isLoading && !customerHasInquiries && !row.inquiry_id && (
                    <span style={{ fontSize: 12, color: 'var(--crm-text-3)', alignSelf: 'center' }}>
                      请到「定制需求」选择客户的当前版本，或打开该需求已经关联的原报价。
                    </span>
                  )}
                </>
              ) : (
                <ItemField label="现货 SKU">
                <Select
                  style={{ width: 240 }}
                  placeholder="选择 SKU"
                  filter={optionMatcher}
                  value={row.sku_id}
                  onChange={(value) => patchRow(index, { sku_id: value as number })}
                  optionList={skuOptions}
                />
                </ItemField>
              )}
              <ItemField label="数量">
              <Input
                style={{ width: 84 }}
                placeholder="数量"
                value={row.quantity}
                onChange={(value) => patchRow(index, { quantity: value })}
              />
              </ItemField>
              <ItemField label="报价单价（元）">
              <Input
                style={{ width: 104 }}
                placeholder="报价单价"
                value={row.quoted_price}
                onChange={(value) => patchRow(index, { quoted_price: value })}
              />
              </ItemField>
              {row.mode === 'custom' && (
                <ItemField label="成本（元，必填）">
                <Input
                  style={{ width: 104 }}
                  placeholder="成本(必填)"
                  value={row.unit_cost}
                  onChange={(value) => patchRow(index, { unit_cost: value })}
                />
                </ItemField>
              )}
              <ItemField label="备注">
              <Input
                style={{ width: 160 }}
                placeholder="备注"
                value={row.remark}
                onChange={(value) => patchRow(index, { remark: value })}
              />
              </ItemField>
              <Button
                theme="borderless"
                type="danger"
                onClick={() => setItemRows((rows) => rows.filter((_, i) => i !== index))}
              >
                删除
              </Button>
            </div>
          ))}
          {/* 「+ 添加一行」原来被撑成一条通栏白框，看着像又一行空记录。
              收成一个靠左的小字按钮：它是"往列表里加东西"的动作，不是一行数据。 */}
          <Button
            theme="borderless"
            /* 父容器是 display:grid —— 子项默认会被拉满整列，
               alignSelf 管不了这种拉伸，必须用 justifySelf: 'start'。 */
            style={{
              justifySelf: 'start',
              width: 'auto',
              padding: '0 4px',
              border: 'none',
              background: 'transparent',
              justifyContent: 'flex-start',
              color: 'var(--crm-primary)',
            }}
            onClick={() =>
              setItemRows((rows) => [
                ...rows,
                { mode: 'sku', quantity: '1', quoted_price: '', unit_cost: '', remark: '' },
              ])
            }
          >
            + 添加一行
          </Button>
        </div>
      </Modal>

      <Modal
        width={600}
        title={
          <div className="modal-head">
            <span className="modal-head-icon" aria-hidden>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M4 7h16v10H4z" /><path d="M4 11h16" /><path d="M8 15h3" /></svg>
            </span>
            <div>
              {editable ? '添加附加费用' : '补填运费'}
              <div className="modal-subtitle">{editable ? '费用会加进这份报价的总额' : '运费不进产品价，补它不用重走审批'}</div>
            </div>
          </div>
        }
        visible={chargeVisible}
        onCancel={() => setChargeVisible(false)}
        onOk={() => chargeMutation.mutate()}
        confirmLoading={chargeMutation.isPending}
        okText={editable ? '添加' : '保存运费'}
        // 已经有一条运费时**不给提交**（后端也会按同一条判据拒），先把去处说清楚
        okButtonProps={{ disabled: !editable && Boolean(existingFreight) }}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          {/* 锁版之后只允许运费（后端 `ensure_charge_change_allowed` 也是这个判据）：
              这里把选项直接收敛成一项，而不是让用户选完再被拒 —— 少一次白填。
              顺带说明**为什么只有它**、以及补完之后还要做什么。 */}
          {!editable && (existingFreight ? (
            // 已经有一条了：说清"不该再加"，并指出"去哪里改" —— 这一支正是
            // 2026-10-14 那张「订单 501 / 报价 581」踩的坑：当时没有这一支，
            // 用户只能再加一条，两条被累加进总额。
            <div className="send-context">
              <div className="send-context-line">
                这一版已经有一条运费 ¥{existingFreight.amount.toFixed(2)}
                {existingFreight.description ? `（${existingFreight.description}）` : '（没写说明）'}
                ，不能再新增第二条。
              </div>
              <div className="send-context-hint">
                一个版本只能有一条运费（两条会各自累加进总额）；改金额请去上面「附加费用」点「改」，补错了点「删除」。
              </div>
            </div>
          ) : (
            <div className="send-context">
              <div className="send-context-line">
                这一版已经{version?.sent_at ? '发送给客户' : '审批通过'}，只有「物流」类费用还能补填。
              </div>
              <div className="send-context-hint">
                运费是代收代付、不进产品价，补它不用重走审批；其他费用和折扣要改，请新建版本。
                {version?.sent_at ? '补完运费后，这一版的「已发送」会被清掉，需要重新登记发送。' : ''}
              </div>
            </div>
          ))}
          {/* 三个框原来全靠 placeholder 自我介绍：一点进去字就没了，
              三个空格子长得一模一样，填完也看不出这格是"说明"还是"金额"。
              每格上面配一个固定小标签（和数据字典、回收节奏同一套做法），
              placeholder 只留"怎么填"的例子。 */}
          <div className="form-rows">
            <div className="form-row">
              <label className="form-row-label">类型</label>
              <div className="form-row-control">
                <Select
                  value={chargeForm.charge_type}
                  onChange={(value) => setChargeForm({ ...chargeForm, charge_type: value as string })}
                  optionList={editable ? CHARGE_TYPES : CHARGE_TYPES.filter((item) => item.value === 'logistics')}
                  disabled={!editable}
                  style={{ width: '100%' }}
                />
              </div>
            </div>
            <div className="form-row">
              <label className="form-row-label">说明</label>
              <div className="form-row-control">
                <Input
                  value={chargeForm.description}
                  onChange={(value) => setChargeForm({ ...chargeForm, description: value })}
                  placeholder="例如：宁波到苏州运费"
                  disabled={!editable && Boolean(existingFreight)}
                />
              </div>
            </div>
            <div className="form-row">
              <label className="form-row-label">金额</label>
              <div className="form-row-control">
                <Input
                  value={chargeForm.amount}
                  onChange={(value) => setChargeForm({ ...chargeForm, amount: value })}
                  placeholder={
                    chargeForm.charge_type === 'discount'
                      ? '填正数，系统自动扣减'
                      : '确属零运费请显式填 0，不要留空'
                  }
                  disabled={!editable && Boolean(existingFreight)}
                />
                {chargeForm.charge_type === 'logistics' && (
                  <div className="form-row-hint" style={{ lineHeight: 1.6 }}>
                    运费的支出与向客户收取<b>共用这一份数据</b>：客户全额承担、公司原额代收代付，
                    <b>留空不等于零运费</b> —— 确实没有运费请显式填 0。
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </Modal>

      {/* 改一条已有费用（2026-10-14 新增入口）。
          后端 `PATCH /quote-charges/{id}` 一直存在，只是前端从来没接过 ——
          于是"运费填错了"在锁版之后只能"再加一条"，而多条会被累加进总额。
          这里**只暴露说明与金额**：分类码锁版后不能改（后端会拒），草稿态想换分类
          直接删了重加更直白，给它一个下拉反而像支持切换分类。 */}
      <Modal
        width={520}
        title={editCharge?.is_logistics ? '改这条运费' : '改这条费用'}
        visible={Boolean(editCharge)}
        onCancel={() => setEditCharge(null)}
        onOk={() => updateChargeMutation.mutate()}
        confirmLoading={updateChargeMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          {editCharge?.is_logistics && (
            <div className="send-context">
              <div className="send-context-line">
                运费的支出与向客户收取的金额<b>共用这一份数据</b>：客户全额承担、公司原额代收代付。
              </div>
              <div className="send-context-hint">
                保存即视为「已确认的实际运费」；金额留空不等于零运费，确实没有请显式填 0。
                {version?.sent_at ? '另外：改完这一版的「已发送」会被清掉，需要重新登记一次发送。' : ''}
              </div>
            </div>
          )}
          <Input
            value={editChargeForm.description}
            onChange={(value) => setEditChargeForm({ ...editChargeForm, description: value })}
            placeholder="说明，例如：宁波到苏州运费"
          />
          <Input
            value={editChargeForm.amount}
            onChange={(value) => setEditChargeForm({ ...editChargeForm, amount: value })}
            placeholder={
              editCharge?.is_discount
                ? '优惠金额（填正数，系统自动扣减）'
                : '金额；确属零运费请显式填 0，不要留空'
            }
          />
        </div>
      </Modal>

      <Modal
        title="提交审批"
        visible={submitVisible}
        onCancel={() => setSubmitVisible(false)}
        onOk={() => {
          // 运费没确认时**不让提交**（后端也会拒，这里先说清为什么）。
          // 判据与后端同一份：`freightUnconfirmed` 来自 detail.freight_unconfirmed_reason。
          if (freightUnconfirmed) {
            Toast.error(freightUnconfirmed)
            return
          }
          submitMutation.mutate()
        }}
        confirmLoading={submitMutation.isPending}
        okText="提交"
        okButtonProps={{ disabled: Boolean(freightUnconfirmed) }}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          {/* ⚠️ 运费闸**前置到提交审批**（2026-10-12）。从前它只在"标记已发送"那一刻
              才拦，而那时版本已经审批通过、费用入口锁死、撤回也救不了 ——
              用户只能新建版本重走一遍审批。现在报批前就说清楚。 */}
          {freightUnconfirmed && (
            <div className="send-context" style={{ borderLeft: '3px solid var(--crm-warning)' }}>
              <div className="send-context-line">
                <Tag size="small" type="light" color="orange" style={{ marginRight: 6 }}>
                  待补运费
                </Tag>
                先补上运费，再提交审批
              </div>
              <div className="send-context-hint">
                {freightUnconfirmed} 原因是：版本一旦提交审批就会被锁定，
                那时再发现缺运费就只能新建版本、重走一遍审批 —— 所以这一步要前置。
              </div>
            </div>
          )}
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            提交后系统会按你的价格权限逐条检查：全部在权限内则直接通过，否则转给主管审批。
          </div>
          <Input
            value={submitReason}
            onChange={setSubmitReason}
            placeholder="申请说明（可不填），例如：客户年度大单要求让价"
          />
        </div>
      </Modal>

      <Modal
        width={520}
        title="作废报价"
        visible={voidVisible}
        onCancel={() => { if (!voidMutation.isPending) setVoidVisible(false) }}
        onOk={() => voidMutation.mutate()}
        confirmLoading={voidMutation.isPending}
        okText="确认作废"
        okButtonProps={{ disabled: !voidReason.trim(), type: 'danger' }}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            作废后报价仍保留在列表（状态“已作废”），不能再发送、改版或转订单；删除只对草稿等内部状态开放。
          </div>
          <TextArea
            value={voidReason}
            onChange={setVoidReason}
            maxLength={255}
            placeholder="填写作废原因（必填，例如：客户取消合作 / 价格作废重报）"
          />
        </div>
      </Modal>

      <Modal
        width={520}
        title="记录客户拒绝"
        visible={declineVisible}
        onCancel={() => setDeclineVisible(false)}
        onOk={() => declineMutation.mutate()}
        confirmLoading={declineMutation.isPending}
        okText="确认记录"
      >
        <TextArea value={declineReason} onChange={setDeclineReason}
          placeholder="填写客户实际拒绝原因（可不填）" />
      </Modal>

      {/* 「标记已发送」（2026-10-12 主人反馈"可模糊"后重做）。
          原来只有一个渠道下拉 + 一个自由文本输入框，**没有任何一句话说清这是在登记什么**：
          发给客户还是发给主管、登记完会发生什么、系统到底发没发出去 —— 全靠猜。 */}
      <Modal
        title={version?.sent_at ? '登记再次发送' : '标记已发送'}
        visible={sendVisible}
        onCancel={() => {
          setSendVisible(false)
          setSendTouched(false)
        }}
        onOk={submitSend}
        confirmLoading={sendMutation.isPending}
        okText="确认：我已经发给客户了"
        width={520}
      >
        <div style={{ display: 'grid', gap: 14 }}>
          {/* ① 先说是哪一份、发给谁 —— 原来的弹窗连报价单号都不显示 */}
          <div className="send-context">
            <div className="send-context-line">
              报价单 <strong>{quote?.quote_no}</strong>
              <Tag size="small" style={{ marginLeft: 6 }}>V{version?.version_no}</Tag>
            </div>
            <div className="send-context-line">
              这是「已发给客户」的记录：
              <strong>{quote?.customer_name || '（未取到客户名）'}</strong>
            </div>
            <div className="send-context-hint">
              系统不会替你发送文件 —— 请先用「下载 PDF」或企业微信把报价发给客户，
              再回来登记这次发送。登记后这一版会锁定（不能再改），商机会推进到「已报价」。
            </div>
          </div>

          {/* ② 渠道：默认是这个人上次用过的，不是写死的邮件 */}
          <div>
            <div className="send-field-label">通过什么发给客户的</div>
            <Select
              value={channelOption.selectValue}
              onChange={channelOption.onSelect}
              optionList={orderedChannels(currentUser?.id).map((value) => ({ value, label: value }))}
              style={{ width: '100%' }}
            />
            {channelOption.showInput && (
              <Input
                value={channelOption.inputValue}
                onChange={channelOption.onInput}
                placeholder="请说明是什么渠道（可不填）"
                style={{ marginTop: 8 }}
              />
            )}
          </div>

          {/* ③ 收件人：必填 + 从客户联系人里选（原来可留空，事后无从查证） */}
          <div>
            {/* 用共用件标必填，不要手写星号：全站必填标记只有 `.required-mark`
                一个来源（手写的星号 CSS 选不中、颜色也不随主题走 —— 这是
                `FormLabel` 存在的理由）。这里原来手写了一个红 `*`，
                `ops/check_form_required_marks.py` 顺手扫出来的。 */}
            <FormLabel required className="send-field-label" style={{ marginBottom: 4 }}>
              发给了谁
            </FormLabel>
            <Select
              value={
                // 三态：没选 → 占位；选了手动填写 → 显示那一项；选了联系人 → 显示联系人
                manualReceiver ? MANUAL_RECEIVER : (sendForm.receiver || undefined)
              }
              onChange={(picked) => {
                const next = String(picked ?? '')
                setSendTouched(true)
                const manual = next === MANUAL_RECEIVER
                setManualReceiver(manual)
                setSendForm({
                  ...sendForm,
                  // 切到「手动填写」时先清空：留着一个联系人姓名会让人以为已经填好了
                  receiver: manual ? '' : next,
                })
              }}
              optionList={[
                ...contactOptions,
                { value: MANUAL_RECEIVER, label: '其他（手动填写）' },
              ]}
              style={{ width: '100%' }}
              placeholder="选这个客户的联系人"
            />
            {manualReceiver && (
              <Input
                value={sendForm.receiver}
                onChange={(value) => {
                  setSendTouched(true)
                  setSendForm({ ...sendForm, receiver: value })
                }}
                placeholder="填姓名或称呼，例如：李经理"
                style={{ marginTop: 8 }}
                validateStatus={sendTouched && !sendForm.receiver.trim() ? 'error' : 'default'}
              />
            )}
            {sendTouched && !sendForm.receiver.trim() && (
              <div className="send-field-error">
                必须填"发给了谁" —— 这条记录是将来对账"到底发出去没有"的依据。
              </div>
            )}
            {contactOptions.length === 0 && (
              <div className="send-field-hint">
                这个客户还没有登记联系人，直接手填姓名也可以。
              </div>
            )}
          </div>
        </div>
      </Modal>

      {/* ===== 对外与归档：三块合成一张卡 =====
          原来是三张独立的卡（发送记录 / 合同文档 / 对外单据），每张里
          往往只有**一句话**：一条发送记录、一句"还没有关联合同"、一行导出说明。
          一张卡一句话，四周还要留白、带标题、占一整行 —— 三张卡约 480px 高，
          拆开看每条信息都不到 20 个字。这就是"内容少却占大块地方"的来源
          （2026-11-17 主人认可这个思路）。

          三件事本来就同类：这份报价对客户那边发生过什么、留下了什么文件。
          收进一张卡，段与段用分隔线隔开，标题降级成 13px 小标题 ——
          整块从 480px 压到约 220px，一条也没丢。 */}
      <SectionCard title="对外与归档" style={{ marginTop: 16 }}>
        {/* minWidth:0 是必须的：grid 子项默认 min-width:auto，内层表格/按钮组的
            固有宽度会把卡片撑出容器（实测两个按钮各被切掉一截）。 */}
        {/* 桌面宽度下拆成两列：左边发送记录（一条记录也要占住左半），
            右边合同 + 对客单据上下叠。原来三块全宽竖排，每块右边空出半屏
            （发送记录表只有三列，却铺满 1100px）。窄屏回到单列。 */}
        <div className="quote-archive-grid">
          <div className="qa-col">
            {/* 发过给谁：按整单看 —— V1 发给客户、现在走到 V3，那份发送事实仍要看得见 */}
            <div className="quote-archive-head">
              <span className="quote-archive-icon" style={{ background: '#e8f1fd', color: '#1f6fe0' }}>
                <IconMail size="small" />
              </span>
              发送记录
            </div>
            <QuoteSendLogsPanel versionIds={allVersionIds} />
          </div>

          <div className="qa-col">
            {/* 签了什么：合同会钉死"依据的是哪一版"，所以也按整单筛 */}
            <div className="quote-archive-head">
              <span className="quote-archive-icon" style={{ background: '#e6f6f2', color: '#0d9488' }}>
                <IconFile size="small" />
              </span>
              合同文档
            </div>
            <ContractDocsPanel
              quoteId={quoteId}
              boxed={false}
              empty="这份报价还没有关联的合同文档"
            />
          </div>

          <div className="qa-col qa-col-last">
            {/* 场景10：对客 Excel 报价单——金额取自当前选中版本的快照，
                之后改价或改模板都不会影响已导出的那份 */}
            <div className="quote-archive-head">
              <span className="quote-archive-icon" style={{ background: 'var(--crm-secondary-soft)', color: 'var(--crm-secondary)' }}>
                <IconExport size="small" />
              </span>
              对外单据（对客报价 Excel）
            </div>
            <BizDocPanel
              docType="quote_sheet"
              quoteId={quoteId}
              quoteVersionId={versionId}
              canManage={canManage}
              compact
              compactEmptyText="还没有生成过对客报价 Excel —— 生成的是这一刻的快照，之后改价或改模板不影响已出的文件。"
            />
          </div>
        </div>
      </SectionCard>
    </div>
  )
}


/**
 * 批量录入里「标签 + 控件」的小竖排（2026-11-17 视觉审视 V2-07：输入框一旦有值、
 * placeholder 就消失，用户只能凭数字猜这是数量还是单价）。
 * 标签常驻在控件上方，窄屏换行、横向滚动都不影响「标签跟控件在一起」。
 */
function ItemField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 11, color: 'var(--crm-text-3)', lineHeight: 1 }}>{label}</span>
      {children}
    </div>
  )
}
