import { cloneElement, useState, type ReactElement } from 'react'
import SampleFromSourceModal from '../sample/SampleFromSourceModal'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import FormLabel from '../../shared/components/FormLabel'
import { ConfirmRequiredModal, isConfirmRequired, confirmMessageOf } from '../../shared/components/ConfirmRequiredModal'
import BlockedHint from '../../shared/components/BlockedHint'
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
import { Button, Dropdown, Input, Modal, Popconfirm, Select, Table, Tag, TextArea, Toast } from '@douyinfe/semi-ui'

import {
  linkMasterData,
  getQuoteMasterRefreshPreview,
  acceptQuote,
  addQuoteCharge,
  createQuoteVersion,
  declineQuote,
  deleteQuoteCharge,
  downloadQuotePdf,
  getPriceDrift,
  getQuoteVersion,
  listQuoteVersions,
  markSent,
  refreshPrices,
  setQuoteVersionItems,
  submitApproval,
  updateQuoteCharge,
  updateQuoteItem,
  withdrawApproval,
  type QuoteChargeRow,
  type QuoteVersion,
  type QuoteItemRow,
} from '../../shared/api/quote'
import { listContacts } from '../../shared/api/customer'
import { useAuthStore } from '../../shared/store/auth'
import { listCustomInquiries } from '../../shared/api/inquiry'
import { listSkusForPricing } from '../../shared/api/pricing'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import { convertToOrder } from '../../shared/api/order'
import DetailHeader from '../../shared/components/DetailHeader'
import SectionCard from '../../shared/components/SectionCard'
import ContractDocsPanel from '../../shared/components/ContractDocsPanel'
import QuoteSendLogsPanel from './QuoteSendLogsPanel'
import BizDocPanel from '../../shared/components/BizDocPanel'
import WhatIfPanel from './WhatIfPanel'
import DecisionMakerCard from '../common/DecisionMakerCard'
import { optionMatcher } from '../../shared/components/optionMatch'
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
}

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
function parseChargeAmount(raw: string, isDiscount: boolean): number {
  if (!raw) {
    throw new Error(
      isDiscount
        ? '请填写优惠金额（空输入不等于 0 元）'
        : '请填写金额；确属零运费也要显式填 0，不能留空当作已确认',
    )
  }
  const amount = Number(raw)
  if (!Number.isFinite(amount) || amount < 0) {
    throw new Error('金额必须是不小于 0 的有效数字')
  }
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) {
    throw new Error('金额最多两位小数')
  }
  return amount
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
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const currentUser = useAuthStore((state) => state.user)
  const { can } = usePermissions()
  const canManage = can('quote:manage')

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
  const versionId = requestedVersionId ?? allVersionIds[0]

  const detailQuery = useQuery({
    queryKey: ['quote-version', versionId],
    queryFn: () => getQuoteVersion(versionId!),
    enabled: Boolean(versionId),
  })

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
  const inquiriesQuery = useQuery({
    queryKey: ['inquiries-for-quote', quoteCustomerId],
    queryFn: () => listCustomInquiries({ customer_id: quoteCustomerId, page: 1, page_size: 100 }),
    enabled: itemsVisible && Boolean(quoteCustomerId),
  })

  const patchRow = (index: number, patch: Partial<ItemDraftRow>) => {
    setItemRows((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  const openItemsEditor = () => {
    const rows = (detail?.items ?? []).map<ItemDraftRow>((item) => ({
      mode: item.is_custom ? 'custom' : 'sku',
      sku_id: item.sku_id ?? undefined,
      inquiry_id: item.inquiry_id ?? undefined,
      quantity: String(item.quantity ?? ''),
      quoted_price: String(item.quoted_price ?? ''),
      // 定制件的成本快照就是当初人工填的核价成本，回填出来方便改
      unit_cost: item.is_custom ? String(item.cost_snapshot ?? '') : '',
      remark: item.remark ?? '',
    }))
    setItemRows(rows.length ? rows : [{ mode: 'sku', quantity: '1', quoted_price: '', unit_cost: '', remark: '' }])
    setItemsVisible(true)
  }

  const itemsMutation = useMutation({
    mutationFn: () =>
      setQuoteVersionItems(
        version!.id,
        itemRows.map((row) => {
          const quantity = Number(row.quantity) || 1
          const price = row.quoted_price.trim() === '' ? null : Number(row.quoted_price)
          if (row.mode === 'custom') {
            return {
              inquiry_id: row.inquiry_id,
              quantity,
              quoted_price: price,
              unit_cost: row.unit_cost.trim() === '' ? null : Number(row.unit_cost),
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
      Toast.success(`已生成订单 ${data.order_no}`)
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

  const isCurrentVersion = Boolean(version && quote?.current_version_id === version.id)
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
      ? `这是历史版本（V${version.version_no}），历史版本不能再提交审批、录入明细或加费用；要改动请切到当前版本，或新建一版。`
      : version.sent_at
        ? '该版本已经发送给客户，明细和费用不能再改（运费例外，可以补或改）；需要改动请新建一版。'
        : version.approval_status === 'approved'
          ? '该版本已审批通过，明细和费用不能再改（运费例外，可以补或改）；需要改动请新建一版。'
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
      const link = await linkMasterData(version!.id)
      const price = await refreshPrices(version!.id)
      return { link, price }
    },
    onSuccess: ({ link, price }) => {
      Toast.success(link.message)
      if (price.refreshed || price.skipped) {
        Toast.info(`重新核价：${price.refreshed} 条已刷新、${price.skipped} 条未动（手工价）`)
      }
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['price-drift', version?.id] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const priceMutation = useMutation({
    mutationFn: () => updateQuoteItem(priceTarget!.id, { quoted_price: Number(newPrice) }),
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
    setEditCharge(record)
    setEditChargeForm({
      description: record.description ?? '',
      amount: String(record.is_discount ? Math.abs(record.amount) : record.amount),
    })
  }

  // 总额传进去：金额太小时「分不出正数期」要在弹窗里就拦住并说明（第九批 ①⑤）
  const planTotal = Number(version?.total_amount ?? 0)
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
    return <div className="page-container">加载中…</div>
  }
  if (!detail || !version || !quote) {
    return <div className="page-container">报价单不存在或无权查看</div>
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
  /** 运费未确认的人话原因（后端给）；有值就要一直显示 */
  const freightUnconfirmed = detail.freight_unconfirmed_reason ?? null


  // 「这一步还差什么」的清单（判据在 shared/quote-todo.ts，单一真源）
  const quoteTodoList = quoteTodos({
    masterWarnings: detail?.master_warnings,
    freightUnconfirmedReason: freightUnconfirmed,
    approvalStatus: version?.approval_status,
    sentAt: version?.sent_at,
    acceptedAt: version?.accepted_at,
    declinedAt: version?.declined_at,
    converted: quote?.status === 'converted',
  })
  // 有没有"必须处理完才能往下走"的项（主数据/费用）——决定标签用橙色还是蓝色
  const quoteTodosBlocked = quoteTodoList.some(
    (todo) => todo.stage === 'master' || todo.stage === 'cost',
  )


  const itemColumns = [
    {
      title: 'SKU / 需求',
      dataIndex: 'sku_code',
      width: 170,
      // 定制项（场景09）没有 SKU：显示需求编号 + 定制标记，
      // 否则这一格会是空的，看的人不知道这条是什么
      // 编码在上、**名称在下**（与订单明细同一口径）。
      // 从前这里只渲染 `sku_code`，而接口早就返回了 `sku_name`
      // （`quote_items.sku_name_snapshot`）—— 满屏 `TP-1210-ST` 这种编码，
      // 看的人不知道是哪件东西（主人 2026-10-10 指出）。
      render: (v: string | null, record: QuoteItemRow) => (
        <div>
          <div>
            {v ?? '-'}
            {record.is_custom && (
              <Tag size="small" type="light" style={{ marginLeft: 6 }}>
                定制
              </Tag>
            )}
          </div>
          {record.sku_name && (
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{record.sku_name}</div>
          )}
        </div>
      ),
    },
    { title: '规格', dataIndex: 'specification', width: 180, render: (v: string | null) => v ?? '-' },
    { title: '数量', dataIndex: 'quantity', width: 90, render: (v: number) => v.toLocaleString('zh-CN') },
    {
      // 产品核价成本（2026-10-09 运费分离）：新口径下**就是商品成本**，
      // 运费不参与产品定价与产品利润，所以这一格不再加 `logistics_cost_snapshot`。
      // 历史版本（legacy）沿用老口径"商品成本 + 单件运费"，与当时的审批结论一致。
      title: legacyBasis ? '成本快照（含运费）' : '商品成本',
      dataIndex: 'cost_snapshot',
      width: 130,
      render: (v: number, record: QuoteItemRow) =>
        `¥${(legacyBasis ? v + record.logistics_cost_snapshot : v).toFixed(2)}`,
    },
    {
      title: '建议价',
      dataIndex: 'recommended_price_snapshot',
      width: 100,
      render: (v: number | null) => (v === null ? '-' : `¥${v}`),
    },
    {
      title: '最低保护价',
      dataIndex: 'minimum_price_snapshot',
      width: 120,
      render: (v: number | null) => (v === null ? '-' : `¥${v.toFixed(2)}`),
    },
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
      width: 150,
      render: (value: number, record: QuoteItemRow) => (
        <span style={{ fontWeight: 600, color: record.approval_required ? 'var(--crm-error)' : 'var(--crm-text)' }}>
          ¥{value}
        </span>
      ),
    },
    {
      // 方案 §4.1"显示价格来源"：拟报价的依据（客户专属价/等级价/通用指导价/手工）
      title: '价格来源',
      dataIndex: 'price_source',
      width: 110,
      render: (v: string | null, record: QuoteItemRow) => {
        const label =
          v === 'customer_specific'
            ? '客户专属价'
            : v === 'level'
              ? '客户等级价'
              : v === 'general'
                ? '通用指导价'
                : '手工价'
        return (
          <Tag type="light" color={v ? 'blue' : 'grey'} size="small">
            {label}
            {record.customer_level_snapshot ? `（${record.customer_level_snapshot}）` : ''}
          </Tag>
        )
      },
    },
    {
      title: '利润率',
      dataIndex: 'profit_rate_snapshot',
      width: 100,
      render: (v: number) => `${(v * 100).toFixed(2)}%`,
    },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 130,
      render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
    },
    {
      title: '审批',
      dataIndex: 'approval_required',
      width: 110,
      render: (v: boolean) => (v ? <Tag color="orange">需审批</Tag> : <Tag color="green">权限内</Tag>),
    },
    {
      title: '操作',
      width: 90,
      render: (_: unknown, record: QuoteItemRow) =>
        canManage && editable ? (
          <a
            style={{ color: 'var(--crm-primary)' }}
            onClick={() => {
              setPriceTarget(record)
              setNewPrice(String(record.quoted_price))
            }}
          >
            改价
          </a>
        ) : (
          '-'
        ),
    },
  ]

  const skuOptions = (skusQuery.data ?? []).map((sku) => ({
    value: sku.id,
    // 下拉里带上 SKU 名称：只有 `编码 · 规格` 时，一个产品下几个 SKU 认不出区别
    label: [sku.sku_code, sku.name, sku.specification].filter(Boolean).join(' · '),
  }))
  const inquiryOptions = (inquiriesQuery.data?.items ?? []).map((row) => ({
    value: row.id,
    label: `${row.inquiry_no} · ${row.title}`,
  }))

  return (
    <div className="page-container">
      {/* 与客户/商机详情页同排布：标题 + 标签一行，关键信息行在标题下方左对齐 */}
      <DetailHeader
        title={quote.quote_no}
        tags={
          <>
            <Tag color={STATUS_TONE[quote.status] ?? 'grey'}>{quote.status_label}</Tag>
            <Tag>V{version.version_no}</Tag>
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
            <span>有效期至：{quote.valid_until ?? '-'}</span>
            <span>付款条件：{version.payment_terms ?? '-'}</span>
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
            '新建版本': Boolean(canManage),
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
            '转销售订单': Boolean(
              can('order:manage') && isCurrentVersion && version.sent_at && version.accepted_at
              && quote.status === 'accepted',
            ),
            '下载 PDF': Boolean(version),
            // ⚠️ 这里只放**原本就在这一行里**的动作。「批量录入」「添加费用」
            // 分别在「报价明细」「附加费用」两个区块内（我第一版把它们搬上来，
            // 等于同一个入口出现两次），所以**不列在这里**。
            '一键刷新到最新适用价': Boolean(driftQuery.data?.items?.some((it) => it.drift)),
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
                <Button theme="solid" onClick={() => setSubmitVisible(true)}>
                  提交审批
                </Button>
              ),
            },
            {
              label: '撤回审批',
              element: <Button onClick={() => withdrawMutation.mutate()}>撤回审批</Button>,
            },
            {
              label: '标记已发送',
              element: (
                <Button
                  theme="solid"
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
                  theme="solid"
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
                <Button
                  theme="solid"
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
                  onClick={() => downloadQuotePdf(version.id, `${quote.quote_no}-V${version.version_no}.pdf`)}
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
          const take = (label: string): ReactElement | null => {
            // `标记已发送` / `登记再次发送` 是同一个位置的两副文案：
            // 发送过就显示"登记再次发送"，否则"标记已发送"
            if (label === '登记再次发送' && !byLabel.has('登记再次发送')) return null
            const item = byLabel.get(label)
            if (!item || used.has(label)) return null
            used.add(label)
            // ⚠️ 这些元素是**放进数组**渲染的，必须有 key ——
            // 少了它会报 "Each child in a list should have a unique key prop"，
            // CI 的 UI 冒烟把控制台报错当失败（我第一版就漏了）。
            return cloneElement(item.element, { key: label })
          }

          const primaryLabel = primary
            ? (primary.handler === 'markSent' && version.sent_at ? '登记再次发送' : primary.label)
            : null
          if (primaryLabel) {
            const element = take(primaryLabel)
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

          // 折叠区：与当前阶段推进无关的低频入口
          const moreOrder = [...SECONDARY_ACTIONS]
          const more = moreOrder
            .filter((label) => byLabel.has(label) && !used.has(label))
            .map((label) => byLabel.get(label) as { label: string; element: ReactElement })
          // 想收但这一刻不能用的：列成灰字说明，**不是一个能按的动作**
          const moreMissing = moreOrder.filter((label) => !byLabel.has(label) && conditions[label] === false)

          return (
            <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              {rendered}
              {more.length > 0 && (
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
                      {moreMissing.length > 0 && (
                        <li
                          style={{
                            listStyle: 'none',
                            padding: '6px 12px',
                            fontSize: 12,
                            color: 'var(--crm-text-3)',
                            maxWidth: 260,
                            whiteSpace: 'normal',
                          }}
                        >
                          {moreMissing.join('、')}：当前版本/状态不可用（原因见页面上的提示）
                        </li>
                      )}
                    </Dropdown.Menu>
                  }
                >
                  <Button>更多操作</Button>
                </Dropdown>
              )}
            </div>
          )
        })()}

        <div
          style={{
            marginTop: 12,
            paddingTop: 12,
            borderTop: '1px solid var(--crm-surface-high)',
            display: 'flex',
            gap: 8,
            flexWrap: 'wrap',
            alignItems: 'center',
          }}
        >
          <span style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>版本：</span>
          {(versionsQuery.data ?? []).map((item) => (
            <Button
              key={item.id}
              size="small"
              theme={item.id === version.id ? 'solid' : 'borderless'}
              onClick={() => setSearchParams({ version: String(item.id) })}
            >
              V{item.version_no}
            </Button>
          ))}
        </div>

        {/* 版本页签下面正是「按钮突然全没了」发生的地方：切到历史版本、或这一版已经发送/审批通过时，
            提交审批、批量录入、添加费用都会整块消失（判据 `canManage && editable`）。
            常驻一行说明它们为什么不在这儿、下一步该怎么做（规矩见 shared/components/BlockedHint.tsx）。 */}
        {versionEditBlockedReason && (
          <BlockedHint reasons={[versionEditBlockedReason]} action="改这一版报价" />
        )}
      </DetailHeader>

      {/* §8.14 复审：主数据未确认要**常驻显示** —— 不是一闪而过的 Toast。
          刷新页面、隔天再打开、换个人来看，都该看到"这几条不能用做正式报价"。
          数据来自版本详情接口附带的 `master_warnings`（后端按"明细引用的那一版
          快照里有没有名称/规格/单位"算出来的）。 */}
      {(detail.master_warnings ?? []).length > 0 && (
        <div
          style={{
            marginBottom: 16,
            padding: '10px 14px',
            borderRadius: 6,
            background: 'var(--crm-warning-bg, #FFF7E6)',
            border: '1px solid var(--crm-warning-border, #FFD591)',
            fontSize: 13,
            lineHeight: 1.8,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 2 }}>
            这几条明细的主数据还没确认，不能用来做正式报价
          </div>
          {(detail.master_warnings ?? []).map((warning) => (
            <div key={warning}>· {warning}</div>
          ))}
          {/* 刷新入口就放在这条提示里：用户看到"不能用做正式报价"时，
              下一步动作就在眼前，不用去别处找。 */}
          <div style={{ marginTop: 8 }}>
            <Button size="small" theme="solid" onClick={() => setMasterPreviewOpen(true)}>
              已确认？重新核价接入
            </Button>
          </div>
        </div>
      )}

      {/*
        「这一步还差什么」（2026-10-13 主人：把阻塞原因收成一个待办区）。
        判据全部来自 `shared/quote-todo.ts`（单一真源），页面只负责渲染与跳转 ——
        别在这里自己判一遍，否则两处会说法不一。
      */}
      {quoteTodoList.length > 0 && (
        <div
          style={{
            marginBottom: 16,
            padding: '12px 16px',
            borderRadius: 6,
            background: 'var(--crm-surface, #fff)',
            border: '1px solid var(--crm-border, #E5E6EB)',
            fontSize: 13,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ fontWeight: 600 }}>现在这一步</span>
            <Tag color={quoteTodosBlocked ? 'orange' : 'blue'} size="small">
              {currentStage(quoteTodoList) ? STAGE_LABEL[currentStage(quoteTodoList)!] : ''}
            </Tag>
            {stageProgress(currentStage(quoteTodoList)) && (
              <span style={{ color: 'var(--crm-text-3)' }}>
                第 {stageProgress(currentStage(quoteTodoList))!.step} 步 / 共{' '}
                {stageProgress(currentStage(quoteTodoList))!.total} 步
              </span>
            )}
          </div>
          {quoteTodoList.map((todo: QuoteTodo, index: number) => (
            <div
              key={todo.key}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 8,
                padding: '4px 0',
                color: index === 0 ? 'var(--crm-text-1)' : 'var(--crm-text-3)',
              }}
            >
              <span style={{ flexShrink: 0 }}>{index === 0 ? '·' : '·'}</span>
              <span style={{ flex: 1 }}>{todo.reason}</span>
              {/* 能点的必须给去处：只说明"差什么"而不给入口，等于把问题换个地方放着 */}
              {todo.anchor && (
                <a
                  style={{ flexShrink: 0, color: 'var(--crm-primary)' }}
                  onClick={() => {
                    document.getElementById(todo.anchor as string)?.scrollIntoView({
                      behavior: 'smooth',
                      block: 'center',
                    })
                  }}
                >
                  去处理
                </a>
              )}
              {todo.action && index === 0 && (
                <span style={{ flexShrink: 0, color: 'var(--crm-text-3)' }}>
                  下一步：{todo.action}
                </span>
              )}
            </div>
          ))}
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
        visible={convertVisible}
        onCancel={() => setConvertVisible(false)}
        onOk={() => convertMutation.mutate(true)}
        confirmLoading={convertMutation.isPending}
        okText="生成订单"
        okButtonProps={{ disabled: planNotReady }}
        width={620}
        footer={
          <div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <Button onClick={() => setConvertVisible(false)}>取消</Button>
              <GuardedButton
                theme="solid"
                loading={convertMutation.isPending}
                disabled={planNotReady}
                blockedReasons={planBlocked}
                action="生成订单"
                onClick={() => convertMutation.mutate(true)}
              >
                生成订单
              </GuardedButton>
            </div>
          </div>
        }
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            将把本报价版本转为销售订单（金额 ¥
            {Number(version?.total_amount ?? 0).toLocaleString('zh-CN')}
            ），并「复制全部明细」。订单是正式履约依据，生成后要改需走订单自己的流程。
          </div>
          <ReceivablePlanPicker
            totalAmount={Number(version?.total_amount ?? 0)}
            currency="CNY"  /* 口径：系统只做人民币（版本上没有 currency 字段，别猜） */
            mode={planMode}
            onModeChange={setPlanMode}
            rows={planRows}
            onRowsChange={setPlanRows}
          />
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
        title="接入主数据：会变什么"
        visible={masterPreviewOpen}
        footer={null}
        onCancel={() => setMasterPreviewOpen(false)}
        width={640}
        style={{ maxWidth: 'calc(100vw - 48px)' }}
      >
        {masterPreviewQuery.isLoading && <div>正在比对…</div>}
        {masterPreviewQuery.data && (
          <div style={{ display: 'grid', gap: 16, paddingBottom: 20 }}>
            <div style={{ fontSize: 14, lineHeight: 1.8 }}>{masterPreviewQuery.data.message}</div>
            {!masterPreviewQuery.data.refreshable && (
              <div
                style={{
                  padding: '12px 16px',
                  borderRadius: 'var(--crm-radius)',
                  background: 'var(--crm-warning-bg, #FFF7E6)',
                  border: '1px solid var(--crm-warning-border, #FFD591)',
                  fontSize: 13,
                  lineHeight: 1.7,
                }}
              >
                {masterPreviewQuery.data.blocked_reason}
              </div>
            )}
            {masterPreviewQuery.data.items
              .filter((it) => it.changes.length > 0 || it.never_referenced)
              .map((it) => (
                <div
                  key={it.item_id}
                  style={{
                    border: '1px solid var(--semi-color-border)',
                    borderRadius: 'var(--crm-radius)',
                    padding: '16px 20px',
                    display: 'grid',
                    gap: 12,
                    background: 'var(--crm-surface)',
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{it.sku_code}</div>
                  {it.never_referenced && (
                    <div
                      style={{
                        padding: '8px 12px',
                        borderRadius: 'var(--crm-radius-sm)',
                        background: 'var(--crm-warning-bg, #FFF7E6)',
                        fontSize: 12,
                        lineHeight: 1.7,
                        color: 'var(--semi-color-warning)',
                      }}
                    >
                      这条明细还没有引用过任何已确认版本（刷新会引用最新已确认版本）
                    </div>
                  )}
                  {it.changes.length > 0 && (
                    <div style={{ display: 'grid', gap: 10 }}>
                      {it.changes.map((c) => (
                        <div
                          key={c.field}
                          style={{
                            display: 'flex',
                            alignItems: 'baseline',
                            gap: 12,
                            fontSize: 13,
                            lineHeight: 1.7,
                          }}
                        >
                          <span style={{ flex: '0 0 64px', color: 'var(--crm-text-2)' }}>
                            {c.label}
                          </span>
                          <span style={{ color: 'var(--crm-text-3)' }}>{c.before ?? '（空）'}</span>
                          <span style={{ color: 'var(--crm-text-3)' }}>→</span>
                          <span style={{ fontWeight: 600 }}>{c.after ?? '（空）'}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            {masterPreviewQuery.data.changed_count === 0 && (
              <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>
                没有需要变化的明细。
              </div>
            )}
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 4 }}>
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
                  theme="solid"
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
            background: 'var(--crm-warning-soft, #fff7e6)',
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
            theme="solid"
            loading={refreshMutation.isPending}
            onClick={() => refreshMutation.mutate()}
          >
            一键刷新到最新适用价
          </Button>
        </div>
      )}
      {version.approval_status === 'rejected' && (
        <div style={{ marginBottom: 16 }}>
          <Tag color="red" size="large">
            审批未通过，请调整价格后新建版本重新提交
          </Tag>
        </div>
      )}
      {/* 运费未确认要**持续显示**（与主数据未确认同一性质）：草稿允许没填运费，
          但正式发送会被拦下 —— 等到点"标记已发送"才报错就太晚了。 */}
      {freightUnconfirmed && (
        <div style={{ marginBottom: 16 }}>
          <Tag color="orange" size="large">
            {freightUnconfirmed}
          </Tag>
        </div>
      )}

      <SectionCard
        id="quote-items"
        title="报价明细"
        style={{ marginBottom: 16 }}
        extra={
          <div style={{ display: 'flex', gap: 8 }}>
          {can('order:manage') && <Button size="small" onClick={() => setDraftVisible(true)}>按此版本建订单草稿</Button>}
          {can('sample:manage') && <Button size="small" onClick={() => setSampleVisible(true)}>按此版本申请打样</Button>}
          {canManage &&
          editable && (
            <Button size="small" onClick={openItemsEditor}>
              批量录入
            </Button>
          )}
          </div>
        }
      >
        <Table<QuoteItemRow>
          columns={itemColumns}
          dataSource={detail.items}
          rowKey="id"
          pagination={false}
          scroll={{ x: 'max(100%, 1300px)' }}
          empty="没有明细"
        />
      </SectionCard>

      {draftVisible && <SampleFromSourceModal mode="order" source={{ quote_version_id: version.id }} onClose={() => setDraftVisible(false)} />}
      {sampleVisible && <SampleFromSourceModal source={{ quote_version_id: version.id }} onClose={() => setSampleVisible(false)} />}

      {/* What-if：版本方案对比 + 边际测算（设计稿 Sales Copilot 右栏那个滑杆） */}
      <WhatIfPanel quoteId={quoteId} versionId={version.id} items={detail.items} />

      {/* 两张卡里各有一张表，列宽不能写死 1fr（会被表的固有宽度顶住、整页横向滚动） */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
        <SectionCard
          id="quote-charges"
          title="附加费用"
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
              { title: '类型', dataIndex: 'type_label', width: 100 },
              {
                // 运费要能一眼看出"确认了没有"：金额 0 不等于已确认零运费，
                // 空输入与明确零运费必须分得开（正式发送要求后者）。
                title: '说明',
                dataIndex: 'description',
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
                render: (v: number, record: QuoteChargeRow) =>
                  record.is_logistics ? `¥${v.toFixed(2)}（代收代付）` : `¥${v.toFixed(2)}`,
              },
              {
                // 锁版之后这一列**照旧露出运费的那两个动作**（2026-10-14 修）。
                //
                // 后端一直允许"改运费 / 删运费"（`ensure_charge_change_allowed`
                // 只要改前改后都是运费就放行），而从前这一列整个挂在 `editable` 上：
                // 版本一审批通过就只剩「补填运费」，用户想录真实运费**只能再加一条**，
                // 多条运费会被累加进总额 —— 那张「订单 501 / 报价 581」就是这么来的。
                // 判据与「补填运费」按钮同源（`canEditFreightOnly`），没另立一套。
                title: '',
                width: 96,
                render: (_: unknown, record: QuoteChargeRow) => {
                  if (!canManage) return null
                  const touchable = editable || (Boolean(record.is_logistics) && canEditFreightOnly)
                  if (!touchable) return null
                  return (
                    <span style={{ display: 'inline-flex', gap: 10 }}>
                      <a onClick={() => openChargeEditor(record)}>改</a>
                      <Popconfirm
                        title="删除这条费用？"
                        onConfirm={() => deleteQuoteCharge(record.id).then(refresh)}
                      >
                        <a style={{ color: 'var(--crm-error)' }}>删除</a>
                      </Popconfirm>
                    </span>
                  )
                },
              },
            ]}
            dataSource={detail.charges}
            rowKey="id"
            pagination={false}
            empty="没有附加费用"
          />
        </SectionCard>

        <SectionCard title="金额汇总">
          <div style={{ display: 'grid', gap: 10, fontSize: 14 }}>
            {/* 口径说明（2026-10-09 运费分离）：产品单价不含运费，运费按已确认的
                实际金额代收代付。写在汇总最上方，避免客户/业务员按旧口径理解。
                ⚠️ 同样只在"单价不含运费"口径下显示（见列头的说明）：历史口径的
                单价里含运费，写这句话就是替它改口径。 */}
            {!legacyBasis && (
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, lineHeight: 1.6 }}>
                产品单价不含运费。运费单列，按已确认的实际金额由本公司代收代付
                （公司不赚不赔，产品利润不受运费影响）。
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>产品货款</span>
              <span>¥{summary.goods_amount.toLocaleString('zh-CN')}</span>
            </div>
            {/* 运费从"附加费用"里**单独一行**列具体金额，不再混在一起让客户自己猜。
                注意：下面这几行都**已经含在**应付合计里，只是拆分展示，不再相加。 */}
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>运费（代收代付）</span>
              <span>¥{summary.logistics_amount.toFixed(2)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>其他费用</span>
              <span>¥{summary.other_charge_amount.toFixed(2)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>优惠</span>
              <span>¥{summary.discount_amount.toFixed(2)}</span>
            </div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                paddingTop: 10,
                borderTop: '1px solid var(--crm-surface-high)',
                fontWeight: 600,
                fontSize: 16,
              }}
            >
              <span>应付合计</span>
              <span style={{ color: 'var(--crm-primary)' }}>¥{summary.total_amount.toLocaleString('zh-CN')}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>预计利润合计</span>
              {/* ⚠️ 无 `price:manage` 时后端会把 `total_profit` 置 null
                  （2026-10-14 审查修）—— 原来写 `?? 0` 会显示成「¥0.00」，
                  看着像"这单不赚钱"，而不是"你没权限看"。 */}
              <span>
                {version.total_profit == null
                  ? '无价格权限，不显示利润'
                  : `¥${Number(version.total_profit).toFixed(2)}`}
              </span>
            </div>
          </div>
        </SectionCard>

        {/* 客户决策关系图（设计稿报价详情右栏），报价要发给谁、谁能拍板都在这 */}
        <DecisionMakerCard customerId={quote.customer_id} title="客户决策关系图" />
      </div>

      {detail.approval && (
        <SectionCard title="审批记录" style={{ marginTop: 16 }}>
          <Table
            columns={[
              { title: '动作', dataIndex: 'action', width: 120,
                render: (v: string) =>
                  ({ submit: '提交申请', approve: '同意', reject: '拒绝', withdraw: '撤回' })[v] ?? v },
              { title: '处理人', dataIndex: 'approver_name', width: 120, render: (v: string | null) => v ?? '-' },
              { title: '意见', dataIndex: 'comment', render: (v: string | null) => v ?? '-' },
              {
                title: '时间',
                dataIndex: 'created_at',
                width: 190,
                render: (v: string) => new Date(v).toLocaleString('zh-CN'),
              },
            ]}
            dataSource={detail.approval.records}
            rowKey="id"
            pagination={false}
          />
        </SectionCard>
      )}

      <Modal
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
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            成本 ¥{(priceTarget ? priceTarget.cost_snapshot + priceTarget.logistics_cost_snapshot : 0).toFixed(2)}
            ｜建议价 ¥{priceTarget?.recommended_price_snapshot}
            ｜最低保护价 ¥{priceTarget?.minimum_price_snapshot?.toFixed(2)}
          </div>
          <Input value={newPrice} onChange={setNewPrice} placeholder="实际报价（元）" />
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
        width={900}
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
              <Select
                style={{ width: 96 }}
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
              {row.mode === 'custom' ? (
                <>
                  <Select
                    style={{ width: 240 }}
                    placeholder={inquiryOptions.length > 0 ? '选择定制需求' : '本客户还没有定制需求'}
                    filter={optionMatcher}
                    value={row.inquiry_id}
                    onChange={(value) => patchRow(index, { inquiry_id: value as number })}
                    optionList={inquiryOptions}
                  />
                  {/* 下拉只列**本客户**的需求（2026-10-12 主人提问核实）：空的时候要说清
                      是"这个客户还没有需求"，而不是让用户以为系统里没数据、或者去别处找。 */}
                  {inquiryOptions.length === 0 && (
                    <span style={{ fontSize: 12, color: 'var(--crm-text-3)', alignSelf: 'center' }}>
                      这个客户还没有定制需求：请先到「定制需求」里为「{quote.customer_name ?? '本客户'}」记录一条，再回来报价
                    </span>
                  )}
                </>
              ) : (
                <Select
                  style={{ width: 240 }}
                  placeholder="选择 SKU"
                  filter={optionMatcher}
                  value={row.sku_id}
                  onChange={(value) => patchRow(index, { sku_id: value as number })}
                  optionList={skuOptions}
                />
              )}
              <Input
                style={{ width: 84 }}
                placeholder="数量"
                value={row.quantity}
                onChange={(value) => patchRow(index, { quantity: value })}
              />
              <Input
                style={{ width: 104 }}
                placeholder="报价单价"
                value={row.quoted_price}
                onChange={(value) => patchRow(index, { quoted_price: value })}
              />
              {row.mode === 'custom' && (
                <Input
                  style={{ width: 104 }}
                  placeholder="成本(必填)"
                  value={row.unit_cost}
                  onChange={(value) => patchRow(index, { unit_cost: value })}
                />
              )}
              <Input
                style={{ width: 160 }}
                placeholder="备注"
                value={row.remark}
                onChange={(value) => patchRow(index, { remark: value })}
              />
              <Button
                theme="borderless"
                type="danger"
                onClick={() => setItemRows((rows) => rows.filter((_, i) => i !== index))}
              >
                删除
              </Button>
            </div>
          ))}
          <Button
            theme="borderless"
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
        title={editable ? '添加附加费用' : '补填运费'}
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
                {existingFreight.description ? `（说明：${existingFreight.description}）` : '（没有写说明）'}
                ，不能再新增第二条。
              </div>
              <div className="send-context-hint">
                一个版本只能有一条运费 —— 两条会各自累加进总额，看起来像「多收了一笔」。
                请到上面的「附加费用」里点那一条的「改」修正金额；补错了可以点「删除」。
              </div>
            </div>
          ) : (
            <div className="send-context">
              <div className="send-context-line">
                这一版已经{version?.sent_at ? '发送给客户' : '审批通过'}，只有「物流」类费用还能补填。
              </div>
              <div className="send-context-hint">
                运费是代收代付、不进产品价，所以补它「不需要重新走审批」；
                其他费用与折扣要改动，请新建版本。
                {version?.sent_at ? '另外：补完运费这一版的「已发送」会被清掉，需要重新登记一次发送。' : ''}
              </div>
            </div>
          ))}
          <Select
            value={chargeForm.charge_type}
            onChange={(value) => setChargeForm({ ...chargeForm, charge_type: value as string })}
            optionList={editable ? CHARGE_TYPES : CHARGE_TYPES.filter((item) => item.value === 'logistics')}
            disabled={!editable}
            style={{ width: '100%' }}
          />
          <Input
            value={chargeForm.description}
            onChange={(value) => setChargeForm({ ...chargeForm, description: value })}
            placeholder="说明，例如：宁波到苏州运费"
            disabled={!editable && Boolean(existingFreight)}
          />
          <Input
            value={chargeForm.amount}
            onChange={(value) => setChargeForm({ ...chargeForm, amount: value })}
            placeholder={
              chargeForm.charge_type === 'discount'
                ? '优惠金额（填正数，系统自动扣减）'
                : '金额；确属零运费请显式填 0，不要留空'
            }
            disabled={!editable && Boolean(existingFreight)}
          />
          {chargeForm.charge_type === 'logistics' && (
            <div style={{ color: 'var(--crm-text-3)', fontSize: 12, lineHeight: 1.6 }}>
              运费的支出金额与向客户收取的金额<b>共用这一份数据</b>：客户全额承担、
              公司原额代收代付，不赚不赔。填进来即视为已确认的实际运费；
              <b>留空不等于零运费</b>——确实没有运费请显式填 0。
            </div>
          )}
        </div>
      </Modal>

      {/* 改一条已有费用（2026-10-14 新增入口）。
          后端 `PATCH /quote-charges/{id}` 一直存在，只是前端从来没接过 ——
          于是"运费填错了"在锁版之后只能"再加一条"，而多条会被累加进总额。
          这里**只暴露说明与金额**：分类码锁版后不能改（后端会拒），草稿态想换分类
          直接删了重加更直白，给它一个下拉反而像支持切换分类。 */}
      <Modal
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

      {/* 这份报价**发过给谁**（主人 2026-10-12：登记完就再也看不到自己登记了什么）。
          与合同一样按整单看：V1 发给客户、现在走到 V3，那份发送事实仍要看得见。 */}
      <SectionCard title="发送记录" style={{ marginTop: 16 }}>
        <QuoteSendLogsPanel versionIds={allVersionIds} />
      </SectionCard>

      {/* 这份报价签了什么：合同会钉死"依据的是哪一版"，所以按报价单整单筛，
          不是只看当前版本 —— 早期按 V1 签、现在报价走到 V3，那份合同仍要看得见 */}
      <ContractDocsPanel
        quoteId={quoteId}
        style={{ marginTop: 16 }}
        empty="这份报价还没有关联的合同文档"
      />

      {/* 场景10：对客 Excel 报价单——金额取自当前选中版本的快照，
          之后改价或改模板都不会影响已导出的那份 */}
      <SectionCard title="对外单据（对客报价 Excel）" style={{ marginTop: 16 }}>
        <BizDocPanel
          docType="quote_sheet"
          quoteId={quoteId}
          quoteVersionId={versionId}
          canManage={canManage}
        />
      </SectionCard>
    </div>
  )
}
