import { skuLabel } from '../../shared/sku-label'
import { requiredQuantityText } from '../../shared/number-input'
import { humanizeBackendText } from '../../shared/humanize-text'
import { formatBeijingTime, formatBeijingDate } from '../../shared/beijing-time'
import SampleDingTalkModal from './SampleDingTalkModal'
import FormLabel from '../../shared/components/FormLabel'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import {
  ConfirmRequiredModal,
  isConfirmRequired,
  confirmMessageOf,
} from '../../shared/components/ConfirmRequiredModal'
import EmptyState, { emptyNode } from '../../shared/components/EmptyState'
import StatusDot from '../../shared/components/StatusDot'
import {
  AutoComplete,
  Button,
  DatePicker,
  Input,
  Modal,
  Popconfirm,
  Select,
  SideSheet,
  Table,
  Tag,
  TextArea,
  Toast,
  Tooltip,
} from '@douyinfe/semi-ui'
import { IconHelpCircle } from '@douyinfe/semi-icons'

import { getCustomer, listCustomers } from '../../shared/api/customer'
import { listCustomInquiries, type CustomInquiryRow } from '../../shared/api/inquiry'
import { getOpportunity, listOpportunities } from '../../shared/api/opportunity'
import { listSkusForPricing } from '../../shared/api/pricing'
import {
  addSampleItem,
  applySampleShipApproval,
  confirmSample,
  createSample,
  deleteSample,
  feedbackSample,
  getSample,
  listSamples,
  listSampleTransferOptions,
  madeSample,
  restoreSample,
  reviseSample,
  shipSample,
  signSample,
  terminateSample,
  transferSample,
  type BasisFile,
  type SampleItem,
  type SampleRequestRow,
  updateSample,
  updateSampleItem,
} from '../../shared/api/sample'
import { listBusinessFiles } from '../../shared/api/file'
import PageHeader from '../../shared/components/PageHeader'
import HelpTip from '../../shared/components/HelpTip'
import SectionCard from '../../shared/components/SectionCard'
import AttachmentPanel from '../common/AttachmentPanel'
import { formatMoney, toTwoDecimals } from '../../shared/components/money'
import { usePermissions } from '../../shared/hooks/permissions'
import BizDocPanel from '../../shared/components/BizDocPanel'
import type { TagTone } from '../../shared/types'
import { optionMatcher } from '../../shared/components/optionMatch'
import RecordSearchSelect from '../../shared/components/RecordSearchSelect'
import GuardedButton from '../../shared/components/GuardedButton'

/** 样品（PRD §19 / 03-API §26）。 */

const STATUS_COLOR: Record<string, TagTone> = {
  pending: 'orange',
  approved: 'blue',
  rejected: 'red',
  shipped: 'cyan',
  signed: 'green',
  terminated: 'grey',
}

/**
 * 「已删除」这个筛选项**不是一个真的状态值**（状态里没有"已删除"）：
 * 选中它时不传 `status`，改传 `include_deleted=true`，由后端按
 * `deleted_at IS NOT NULL` 过滤出被软删的那些（2026-10-15 加软删时补）。
 *
 * 用一个一眼认得出的哨兵值，别拿 `'deleted'` 冒充状态 —— 那会和真状态混起来。
 */
const DELETED_STATUS_OPTION = '__deleted__'
/** 「寄样待审批」也不是状态值：选中时按 `ship_approval_status=pending` 筛。 */
const SHIP_PENDING_STATUS_OPTION = '__ship_pending__'

const STATUS_OPTIONS = [
  { value: 'pending', label: '待审批' },
  { value: 'approved', label: '已批准' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'shipped', label: '已寄样' },
  { value: 'signed', label: '已签收' },
  { value: 'terminated', label: '已终止' },
  { value: DELETED_STATUS_OPTION, label: '已删除' },
]

/**
 * 明细草稿。两条路径（场景09）：现货选 SKU；定制件尚无 SKU，选需求编号。
 * 定制件本来就要先打样再定 SKU，强制先建档等于把顺序反过来。
 */
type ItemDraft = {
  /** 选了产品库里的某个产品 → 关联 sku_id */
  sku_id?: number
  /** 选了定制需求 → 关联 inquiry_id（那条路还在，只是不再要用户先"选模式"） */
  inquiry_id?: number
  /** **手填的产品名称**：产品库和定制需求里都还没有它（新品往往打完样才建档）。
   *  2026-10-16 主人：申请单上就一格「产品名称」，没有"现货还是定制"这个区分。 */
  item_name?: string
  quantity: string
}

type SampleProductChoice = {
  value: string | number
  label: string
  inquiry?: CustomInquiryRow
}

/**
 * 产品名称随输入写入草稿，已有产品 / 定制需求按需搜索后选择。
 *
 * 原来定制需求只拉第一页 100 条、在本地过滤：第 101 条以后的需求，
 * 无论输编号还是标题都选不中，只能当自由文本新产品打进去（关联就丢了）。
 * 现在 SKU 仍用完整选项（`/pricing/sku-options` 不分页），
 * 定制需求改成服务端关键词 + 翻页，输入即查。
 */
function SampleProductName({ item, skuChoices, scope, enabled, onChange }: {
  item: ItemDraft
  skuChoices: { value: number; label: string }[]
  scope: string
  enabled: boolean
  onChange: (fields: Pick<ItemDraft, 'sku_id' | 'inquiry_id' | 'item_name'>) => void
}) {
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [open, setOpen] = useState(false)
  const [selectedInquiry, setSelectedInquiry] = useState<{ id: number; label: string } | null>(null)
  useEffect(() => {
    const timer = setTimeout(() => setKeyword(search.trim()), 250)
    return () => clearTimeout(timer)
  }, [search])
  const inquiryQuery = useInfiniteQuery({
    queryKey: ['sample-inquiry-search', scope, keyword],
    enabled: enabled && open,
    initialPageParam: 1,
    queryFn: ({ pageParam }) => listCustomInquiries({ keyword, page: pageParam, page_size: 20 }),
    getNextPageParam: (last) => last.page * last.page_size < last.total ? last.page + 1 : undefined,
  })
  const inquiryRows = inquiryQuery.data?.pages.flatMap((page) => page.items) ?? []
  const currentSearch = search.trim()
  const choices: SampleProductChoice[] = [
    ...skuChoices
      .filter((choice) => optionMatcher(currentSearch, choice))
      .map((choice) => ({ value: choice.value, label: choice.label })),
    ...inquiryRows
      .filter((row) => optionMatcher(currentSearch, {
        value: row.inquiry_no ?? row.id,
        label: `${row.inquiry_no ?? ''} ${row.title}`,
      }))
      .map((row) => ({
        value: `custom:${row.id}`,
        label: `定制需求 · ${row.inquiry_no ?? `编号 ${row.id}`} · ${row.title}`,
        inquiry: row,
      })),
  ]
  // 哨兵项：选中它们只触发翻页 / 重试，不当成业务值写进草稿。
  if (inquiryQuery.isError) choices.push({ value: '__retry__', label: '需求搜索失败，点击重试' })
  else if (inquiryQuery.hasNextPage) choices.push({ value: '__more__', label: '加载更多需求' })

  const selectedRow = item.inquiry_id
    ? inquiryRows.find((row) => row.id === item.inquiry_id)
    : undefined
  // 已选项换关键词/翻页后可能不在当前结果里：用记住的标签，不能显示成空。
  const text = item.inquiry_id
    ? (selectedInquiry?.id === item.inquiry_id
        ? selectedInquiry.label
        : selectedRow
          ? `定制需求 · ${selectedRow.inquiry_no ?? `编号 ${selectedRow.id}`} · ${selectedRow.title}`
          : `定制需求 编号 ${item.inquiry_id}`)
    : item.sku_id
      ? (skuChoices.find((choice) => choice.value === item.sku_id)?.label ?? `产品 编号 ${item.sku_id}`)
      : item.item_name ?? ''
  const typeName = (name: string) => {
    setSearch(name)
    onChange({ sku_id: undefined, inquiry_id: undefined, item_name: name })
  }
  return <AutoComplete
    style={{ flex: 1 }}
    aria-label="产品名称"
    placeholder="产品名称：能搜到就选，搜不到就直接打字"
    value={text}
    data={choices}
    showClear
    loading={inquiryQuery.isFetching || currentSearch !== keyword}
    defaultActiveFirstOption={false}
    onFocus={() => { setSearch(''); setOpen(true) }}
    onDropdownVisibleChange={setOpen}
    onSearch={typeName}
    onClear={() => typeName('')}
    onSelectWithObject
    renderSelectedItem={(choice) => choice.label}
    onSelect={(choice) => {
      const raw = choice.value
      if (raw === '__more__') {
        if (!inquiryQuery.isFetching && currentSearch === keyword) void inquiryQuery.fetchNextPage()
        return
      }
      if (raw === '__retry__') {
        if (inquiryQuery.isFetchNextPageError) void inquiryQuery.fetchNextPage()
        else void inquiryQuery.refetch()
        return
      }
      const inquiry = choice.inquiry
      setSearch('')
      if (typeof raw === 'number') {
        setSelectedInquiry(null)
        onChange({ sku_id: raw, inquiry_id: undefined, item_name: undefined })
      } else if (inquiry) {
        setSelectedInquiry({ id: inquiry.id, label: choice.label })
        onChange({ sku_id: undefined, inquiry_id: inquiry.id, item_name: undefined })
      }
    }}
    emptyContent={inquiryQuery.isFetching ? '搜索中…' : '没有匹配的产品或需求；继续输入将作为新产品名称'}
  />
}

function draftReady(item: ItemDraft): boolean {
  // 三条路任一成立就能加：选到产品 / 选到定制需求 / **自己填了名字**
  return Boolean(item.sku_id ?? item.inquiry_id ?? item.item_name?.trim())
}

function draftPayload(item: ItemDraft, index?: number): Record<string, unknown> {
  const quantity = requiredQuantityText(item.quantity, index === undefined ? '打样数量' : `第 ${index + 1} 行打样数量`)
  if (item.sku_id) return { sku_id: item.sku_id, quantity }
  if (item.inquiry_id) {
    return {
      inquiry_id: item.inquiry_id,
      item_name: item.item_name?.trim() || null,
      quantity,
    }
  }
  // 只写了产品名称：后端 `_add_item` 有一条"只有名字"的路径（2026-10-16 加）
  return { item_name: item.item_name?.trim() || null, quantity }
}

function fmt(value?: string | null) {
  return value ? formatBeijingTime(value) : '-'
}

/**
 * 保存资料后给一句提示，只在状态**真的变了**的时候才说。
 *
 * 不能拿返回后的 status === 'pending' 当判断依据：待审批的单子改完状态还是
 * 「待审批」，那样会弹「已退回待审批」—— 明明什么都没退。要对比保存前的状态。
 */
function reopenedHint(before: string | undefined, after: string): string | null {
  if (after !== 'pending' || (before !== 'approved' && before !== 'rejected')) return null
  return before === 'rejected'
    ? '已保存；单据已重新提交「待审批」，等待重新审批'
    : '已保存；因修改车间依据，单据已退回「待审批」'
}

/** 生产资料这类"可有可无"的字段统一显示成 —，而不是一片空白。 */
/** 详情里的分区卡片：标题条 + 内容，层次清楚，不折叠。 */
function DetailCard({
  title,
  extra,
  action,
  children,
}: {
  title: string
  extra?: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section
      style={{
        border: '1px solid var(--crm-border)',
        borderRadius: 10,
        background: 'var(--crm-surface, #fff)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 14px',
          borderBottom: '1px solid var(--crm-border)',
          background: 'var(--crm-surface-low)',
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--crm-text-1)' }}>{title}</div>
        {extra && (
          <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{extra}</span>
        )}
        <div style={{ flex: 1 }} />
        {action}
      </div>
      <div style={{ padding: '12px 14px' }}>{children}</div>
    </section>
  )
}

/** 详情顶部「关键信息」小格：标签在上、值在下，比一串「负责人：…」更好扫。 */
function MetaItem({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 13, color: 'var(--crm-text-1)' }}>{children}</div>
    </div>
  )
}

function sampleNextStep(row: SampleRequestRow) {
  const oa = (row.source_context as unknown as { dingtalk_oa?: { submitted?: boolean; requires_self_oa?: boolean } } | null)?.dingtalk_oa
  if (row.revision_locked || row.superseded_by) {
    return { hint: row.superseded_by
      ? '原版的制作与寄送记录已保留。审批和后续操作请到新版本办理。'
      : '原版记录已锁定，可查看制作与寄送记录。如需继续办理，请联系负责人。' }
  }
  if (row.status === 'pending') {
    if (row.is_custom === false) {
      if (row.ship_approval_status === 'pending') {
        return { hint: '寄样申请已提交，等主管批准后即可登记寄样。', action: 'work', label: '查看寄样审批' }
      }
      if (row.ship_approval_status === 'rejected') {
        return { hint: '寄样申请被驳回，按驳回原因修改后可重新提交。', action: 'work', label: '重新提交寄样申请' }
      }
      return { hint: '现有产品寄样需要主管批准：先提交寄样申请（承运商、运费），批准后登记寄样。', action: 'work', label: '提交寄样申请' }
    }
    return oa?.submitted
      ? { hint: '打开同序 OA 进度确认当前审批阶段。审批通过后，再登记制作完成。', action: 'oa', label: '查看 OA 进度' }
      : { hint: oa?.requires_self_oa
          ? '这个修订版需要单独审批。补齐本版打样资料后，提交同序 OA。'
          : '草稿还未送审。补齐资料后，单独发起审批，或选择本单的原询价审批继续办理。',
        action: 'oa', label: '填写或提交 OA 申请' }
  }
  if (row.status === 'rejected') return {
    hint: '先查看同序 OA 的处理意见，再按页面提供的操作修改或重提。',
    action: 'oa',
    label: '查看 OA 审批意见',
  }
  if (row.status === 'approved') {
    if (row.is_custom === false) {
      return row.ship_approval_status === 'approved'
        ? { hint: '寄样已批准。寄出后登记承运商与快递单号（运费按批准金额记录）。', action: 'work', label: '去登记寄样' }
        : { hint: '现有产品寄样需要主管批准：先提交寄样申请并等待批准。', action: 'work', label: '提交寄样申请' }
    }
    return row.made_at
      ? { hint: '样品寄出后，填写承运商、快递单号及运费，登记寄样。', action: 'work', label: '去登记寄样' }
      : { hint: '等车间完成实物制作后，登记制作完成并选取实际使用的图纸或确认件。', action: 'work', label: '去登记制作' }
  }
  if (row.status === 'shipped') return { hint: '客户收到样品后，登记签收。客户对样品的最终决定在「客户确认」另记。', action: 'work', label: '去登记签收' }
  if (row.status === 'signed' && row.confirm_status === 'pending') {
    return { hint: '样品已经签收。等客户明确接受或未通过，到「客户确认」登记结论。', action: 'confirm', label: '去登记客户确认' }
  }
  if (row.confirm_status === 'accepted') return { hint: '已登记客户接受。可核对本版的制作、寄送和确认记录。' }
  if (row.confirm_status === 'rejected') return { hint: '已登记客户未通过。若需修改打样要求，可从生产资料处开新修订版。' }
  return null
}

export default function SampleListPage() {
  const queryClient = useQueryClient()
  const { can, isReviewer } = usePermissions()
  const canManage = can('sample:manage')
  const workActionsRef = useRef<HTMLDivElement>(null)
  const customerConfirmRef = useRef<HTMLDivElement>(null)

  const [status, setStatus] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const params = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const rawCreateOpportunityId = Number(searchParams.get('create_opportunity_id'))
  const createOpportunityId = Number.isSafeInteger(rawCreateOpportunityId) && rawCreateOpportunityId > 0 ? rawCreateOpportunityId : undefined
  const createOpportunityQuery = useQuery({
    queryKey: ['opportunity', createOpportunityId],
    queryFn: () => getOpportunity(createOpportunityId!), enabled: Boolean(createOpportunityId) && canManage,
  })
  const [oaTarget, setOaTarget] = useState<SampleRequestRow | null>(null)
  const [createVisible, setCreateVisible] = useState(false)
  const [form, setForm] = useState<{
    opportunity_id?: number
    customer_id?: number
    remark: string
    items: ItemDraft[]
  }>({ remark: '', items: [{ quantity: '1' }] })
  const selectedOpportunityQuery = useQuery({
    queryKey: ['opportunity', form.opportunity_id],
    queryFn: () => getOpportunity(form.opportunity_id!),
    enabled: createVisible && Boolean(form.opportunity_id),
  })

  useEffect(() => {
    if (!createOpportunityId || !createOpportunityQuery.data || !canManage) return
    setForm({ opportunity_id: createOpportunityId, customer_id: createOpportunityQuery.data.customer_id,
      remark: '', items: [{ quantity: '1' }] })
    setCreateVisible(true)
    const next = new URLSearchParams(searchParams)
    next.delete('create_opportunity_id')
    setSearchParams(next, { replace: true })
  }, [createOpportunityId, createOpportunityQuery.data, canManage, searchParams, setSearchParams])

  useEffect(() => {
    if (createOpportunityQuery.error) Toast.error(createOpportunityQuery.error.message)
  }, [createOpportunityQuery.error])

  const routeId = Number(params.id)
  const detailId = Number.isInteger(routeId) && routeId > 0 ? routeId : null
  const [transferVisible, setTransferVisible] = useState(false)
  const [transferKeyword, setTransferKeyword] = useState('')
  const [transferOwner, setTransferOwner] = useState<{ id: number; name: string } | null>(null)
  const [shipForm, setShipForm] = useState({ carrier: '', tracking_no: '', shipping_fee: '' })
  const [feedback, setFeedback] = useState('')
  // 终止打样（K3）：二次确认弹窗 + 必填原因
  const [terminateVisible, setTerminateVisible] = useState(false)
  const [terminateReason, setTerminateReason] = useState('')
  // 现有产品寄样审批：申请表单与驳回原因
  const [shipApprovalForm, setShipApprovalForm] = useState({
    recipient: '',
    payer: '' as '' | 'company' | 'customer',
    carrier: '',
    shipping_fee: '',
    note: '',
  })

  const [newItem, setNewItem] = useState<ItemDraft>({ quantity: '1' })
  // 生产打样资料（文档 §3.5）：跟单在这一栏把车间要的东西补全，
  // 打样需求单出图时逐项带给车间。
  // 材质 / 工艺 / 图纸版本**不在这里**：它们逐行不同，在明细行上改（见 itemEdit）。
  // 打样积分＝预计工时 × 10（同序 OA 表上的口径），**不让手填** —— 手填必然出现
  // 两个人填出两个数。这里只存工时，积分由后端算、只读显示。
  const [prodForm, setProdForm] = useState({
    purpose: '',
    target_completion_date: '',
    acceptance_criteria: '',
    sample_fee: '',
    // ---- 同序 OA《打样申请单》上的格 ----
    sample_type: '',
    product_category: '',
    maker_unit: '',
    sample_purpose: '',
    urgency: '',
    sample_situation: '',
    estimated_hours: '',
  })
  const [prodEditing, setProdEditing] = useState(false)
  // 改某一条明细的车间依据（材质 / 工艺 / 图纸版本）
  const [itemEdit, setItemEdit] = useState<{
    id: number
    craft: string
    material: string
    drawing_version: string
  } | null>(null)
  const [confirmRemark, setConfirmRemark] = useState('')

  // 选了「已删除」就换一套参数：走 `include_deleted`，而不是拿它当状态传给后端
  const deletedOnly = status === DELETED_STATUS_OPTION
  const shipPendingOnly = status === SHIP_PENDING_STATUS_OPTION
  const query = useQuery({
    queryKey: ['samples', { status, keyword, page, pageSize }],
    queryFn: () =>
      listSamples({
        status: deletedOnly || shipPendingOnly ? undefined : status,
        include_deleted: deletedOnly || undefined,
        ship_approval_status: shipPendingOnly ? 'pending' : undefined,
        keyword: keyword.trim() || undefined,
        page,
        page_size: pageSize,
      }),
  })

  useEffect(() => {
    setTransferVisible(false)
    setShipForm({ carrier: '', tracking_no: '', shipping_fee: '' })
    setFeedback('')
    setNewItem({ quantity: '1' })
    setProdEditing(false)
    setItemEdit(null)
    setConfirmRemark('')
  }, [detailId])

  const closeDetail = () => navigate('/samples')

  // 详情优先复用列表数据里的完整对象（列表已返回 items/shipments），避免多打一次接口；
  // 深链打开的那条如果不在当前列表页，再用 getSample 补一次。
  const listDetail: SampleRequestRow | undefined = (query.data?.items ?? []).find(
    (row) => row.id === detailId,
  )
  const detailQuery = useQuery({
    queryKey: ['sample-detail', detailId],
    queryFn: () => getSample(detailId!),
    enabled: detailId !== null && listDetail === undefined && !query.isLoading,
  })
  const detail: SampleRequestRow | undefined = listDetail ?? detailQuery.data
  // 生产打样资料有没有填过：全空时只显示一句「还没有填写」，不铺一排短横线
  const hasProdInfo = Boolean(
    detail && (detail.purpose || detail.target_completion_date || detail.acceptance_criteria
      || detail.sample_fee != null || detail.sample_type || detail.made_at
      || (detail.basis_files && detail.basis_files.length)
      || (detail.made_events && detail.made_events.length)),
  )

  const startProdEditing = () => {
    if (!detail) return
    setProdForm({
      purpose: detail.purpose ?? '',
      target_completion_date: detail.target_completion_date ?? '',
      acceptance_criteria: detail.acceptance_criteria ?? '',
      sample_fee: String(detail.sample_fee_exact ?? detail.sample_fee ?? ''),
      sample_type: detail.sample_type ?? '',
      product_category: detail.product_category ?? '',
      // 历史钉钉单写的是「外协厂」，同序模板是「外协厂打样」；打开编辑时按同序写法显示。
      maker_unit: detail.maker_unit === '外协厂' ? '外协厂打样' : detail.maker_unit ?? '',
      sample_purpose: detail.sample_purpose ?? '',
      urgency: detail.urgency ?? '',
      sample_situation: detail.sample_situation ?? '',
      estimated_hours: detail.estimated_hours != null ? String(detail.estimated_hours) : '',
    })
    setProdEditing(true)
  }
  const nextStep = detail ? sampleNextStep(detail) : null
  const revisionLocked = Boolean(detail?.revision_locked || detail?.superseded_by)
  const canUpdateSample = canManage && !revisionLocked

  const selectedCustomerQuery = useQuery({
    queryKey: ['customer', form.customer_id],
    queryFn: () => getCustomer(form.customer_id!),
    enabled: createVisible && Boolean(form.customer_id) && !form.opportunity_id,
  })
  const skusQuery = useQuery({ queryKey: ['skus-for-pricing'], queryFn: listSkusForPricing })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['timeline', 'customer'] })
    // 深链打开（不在当前列表页）时详情走独立的 ['sample-detail'] 查询，
    // 只刷 ['samples'] 它不会重新取，登记完制作/寄样页面还停在旧状态。
    void queryClient.invalidateQueries({ queryKey: ['sample-detail'] })
    return queryClient.invalidateQueries({ queryKey: ['samples'] })
  }
  const onError = (error: Error) => Toast.error(error.message)

  // 软删 / 恢复（2026-10-15）。两个动作都会改动列表，成功后统一 refresh 一遍：
  // 删掉的那条要从列表里消失，「已删除」里恢复的那条要回到正常列表。
  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteSample(id),
    onSuccess: () => {
      Toast.success('样品申请已移入回收站，可在“回收站 → 样品”中恢复')
      void queryClient.invalidateQueries({ queryKey: ['recycle', 'samples'] })
      void refresh()
    },
    onError,
  })

  const restoreMutation = useMutation({
    mutationFn: (id: number) => restoreSample(id),
    onSuccess: () => {
      Toast.success('样品申请已恢复')
      void refresh()
    },
    onError,
  })

  const createMutation = useMutation({
    mutationFn: () => {
      if (!form.items.length || form.items.some((item) => !draftReady(item))) {
        throw new Error('请填写每一行的产品名称')
      }
      return createSample({
        use_dingtalk: true,
        opportunity_id: form.opportunity_id ?? null,
        customer_id: form.customer_id ?? null,
        remark: form.remark.trim() || null,
        items: form.items.map(draftPayload),
      })
    },
    onSuccess: (created) => {
      Toast.success('打样草稿已创建，请选择同序 OA 流程并补充申请资料')
      setOaTarget(created)
      setCreateVisible(false)
      setForm({ remark: '', items: [{ quantity: '1' }] })
      void refresh()
    },
    onError,
  })

  const transferOptions = useQuery({
    queryKey: ['sample-transfer-options', detailId, transferKeyword],
    queryFn: () => listSampleTransferOptions(detailId!, transferKeyword),
    enabled: transferVisible && detailId !== null,
  })
  const transferMutation = useMutation({
    mutationFn: ({ sampleId, ownerId }: { sampleId: number; ownerId: number }) =>
      transferSample(sampleId, ownerId),
    onSuccess: () => {
      Toast.success('打样单已转交')
      setTransferVisible(false)
      closeDetail()
      void refresh()
    },
  })

  const shipMutation = useMutation({
    mutationFn: () =>
      // ⚠️ 运费必须**先校验再提交**（2026-10-14 审查）：原来直接 `Number(...)`，
      // 输入 `abc` 得到 `NaN`，经 axios/JSON.stringify 变成 `null` 打到
      // `shipping_fee: Decimal = Field(default=0, ge=0, ...)`（**非 Optional**），
      // 用户只会看到「参数校验失败：shipping_fee」，不知道该怎么改。
      // 与同文件 `saveProdMutation`（打样费用）同一口径，别再写第三个版本。
      shipSample(detailId!, (() => {
        // 现有产品寄样：运费以审批确定的金额为准（后端也会校验），这里只读显示
        const approvedFee = detail?.ship_approval?.status === 'approved'
          ? String(detail.ship_approval.shipping_fee ?? '0')
          : null
        const raw = approvedFee ?? shipForm.shipping_fee.trim()
        const fee = raw === '' ? 0 : Number(raw)
        if (!Number.isFinite(fee) || fee < 0) {
          throw new Error('运费要填一个不小于 0 的数字（留空按 0 算）')
        }
        if (raw !== '' && !/^\d+(\.\d{1,2})?$/.test(raw)) {
          throw new Error('运费最多两位小数')
        }
        return {
          carrier: shipForm.carrier.trim() || null,
          tracking_no: shipForm.tracking_no.trim() || null,
          shipping_fee: raw || '0',
        }
      })()),
    onSuccess: () => {
      Toast.success('已登记寄样，客户收到后可登记签收。')
      setShipForm({ carrier: '', tracking_no: '', shipping_fee: '' })
      void refresh()
    },
    onError,
  })

  const signMutation = useMutation({
    mutationFn: () => signSample(detailId!),
    onSuccess: () => {
      Toast.success('已登记签收，等客户给出结论后再登记客户确认。')
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

  const terminateMutation = useMutation({
    mutationFn: () => {
      const reason = terminateReason.trim()
      if (!reason) throw new Error('请填写终止原因')
      return terminateSample(detailId!, reason)
    },
    onSuccess: () => {
      Toast.success('打样已终止，制作与寄送记录保留')
      setTerminateVisible(false)
      setTerminateReason('')
      void refresh()
    },
    onError,
  })

  const applyShipApprovalMutation = useMutation({
    mutationFn: () => {
      const recipient = shipApprovalForm.recipient.trim()
      if (!recipient) throw new Error('请填写收件信息（姓名 / 电话 / 详细地址）')
      if (!shipApprovalForm.payer) throw new Error('请选择运费由谁承担')
      const raw = shipApprovalForm.shipping_fee.trim()
      if (raw !== '' && !/^\d+(\.\d{1,2})?$/.test(raw)) {
        throw new Error('运费最多两位小数')
      }
      return applySampleShipApproval(detailId!, {
        recipient,
        payer: shipApprovalForm.payer,
        carrier: shipApprovalForm.carrier.trim() || null,
        shipping_fee: raw || '0',
        note: shipApprovalForm.note.trim() || null,
      })
    },
    onSuccess: () => {
      Toast.success('寄样申请已提交，等待主管审批')
      setShipApprovalForm({ recipient: '', payer: '', carrier: '', shipping_fee: '', note: '' })
      void refresh()
    },
    onError,
  })

  // 生产打样资料（文档 §3.5）：跟单在这一栏把车间要的东西补全，
  // 打样需求单出图时逐项带给车间
  const saveProdMutation = useMutation({
    mutationFn: () => {
      // 「空着 = 未填」是业务确认过的口径：留空就发 null，不要偷偷当 0 存。
      // 负数和非法数字在前端先挡一道，省得跑一趟后端才看到 422。
      const raw = prodForm.sample_fee.trim()
      const fee = raw === '' ? null : Number(raw)
      if (fee !== null && (!Number.isFinite(fee) || fee < 0)) {
        throw new Error('打样费用要填一个不小于 0 的数字（留空表示「未填」）')
      }
      // 预计工时：留空 = 未填；填了就必须是不小于 0 的数字
      const rawHours = prodForm.estimated_hours.trim()
      const hours = rawHours === '' ? null : Number(rawHours)
      if (hours !== null && (!Number.isFinite(hours) || hours < 0)) {
        throw new Error('预计工时要填一个不小于 0 的数字（留空表示「未填」）')
      }
      // 空串一律发 null：与"没填"同一口径（后端也按 null 处理）
      const blankToNull = (v: string) => (v.trim() ? v.trim() : null)
      return updateSample(detailId!, {
        purpose: prodForm.purpose || null,
        target_completion_date: prodForm.target_completion_date || null,
        acceptance_criteria: prodForm.acceptance_criteria || null,
        sample_fee: raw || null,
        // ---- 同序 OA《打样申请单》上的格 ----
        sample_type: blankToNull(prodForm.sample_type),
        product_category: blankToNull(prodForm.product_category),
        maker_unit: blankToNull(prodForm.maker_unit),
        sample_purpose: blankToNull(prodForm.sample_purpose),
        urgency: blankToNull(prodForm.urgency),
        sample_situation: blankToNull(prodForm.sample_situation),
        estimated_hours: rawHours || null,
        // ⚠️ `product_name` 与那 6 项「打样具体要求」**刻意不在这里发**：
        // 它们已从本页表单移除（改到「同序 OA 打样申请」弹窗里填），若在这里发 null
        // 就会**把弹窗里填好的值擦掉**。PATCH 只改传了的字段，不发就保持原值。
      })
    },
    onSuccess: (row: SampleRequestRow) => {
      // 后端口径：改「车间依据」（目标完成日 / 验收标准）会让已批准的单子退回待审批；
      // 已驳回的单子改任何一项都算重新提交。提示里必须说清楚，否则用户只会看到
      // 状态自己变了，以为系统出错。
      Toast.success(reopenedHint(detail?.status, row.status) ?? '生产资料已保存')
      setProdEditing(false)
      void refresh()
    },
    onError,
  })

  // 改明细的车间依据：与单头资料同一套闸门（后端 _gate_part_lock）
  const saveItemMutation = useMutation({
    mutationFn: () =>
      updateSampleItem(detailId!, itemEdit!.id, {
        // 留空 = 没填，发 null 把它清掉；这是「空着=未填」口径的一部分
        craft: itemEdit!.craft.trim() || null,
        material: itemEdit!.material.trim() || null,
        drawing_version: itemEdit!.drawing_version.trim() || null,
      }),
    onSuccess: (row: SampleRequestRow) => {
      Toast.success(reopenedHint(detail?.status, row.status) ?? '车间依据已保存')
      setItemEdit(null)
      void refresh()
    },
    onError,
  })

  // 登记制作完成（含**制作依据**，第一批返修 §3.5）。
  // 为什么必须弹窗而不是直接提交：依据是"这次照哪几份图纸做的"，事后出了质量问题
  // 拿什么比对全看这一笔。原来页面只发 remark、连 remark 都没传，
  // 快照永远是空的 —— 后端字段白做了（返工单 P2-7）。
  const [madeModal, setMadeModal] = useState(false)
  const [madeForm, setMadeForm] = useState<{ remark: string; fileIds: number[] }>({
    remark: '',
    fileIds: [],
  })
  // 这张打样单上已挂的附件：只有挂在本单上的文件才能作为它的制作依据
  // （后端会校验这一点，前端不列出来等于让人猜文件名）
  const madeFilesQuery = useQuery({
    queryKey: ['sample-files', detailId],
    queryFn: () => listBusinessFiles('sample', detailId!),
    enabled: madeModal && Boolean(detailId),
  })

  const madeMutation = useMutation({
    mutationFn: () =>
      madeSample(detailId!, madeForm.remark.trim() || undefined, madeForm.fileIds),
    onSuccess: () => {
      Toast.success(
        madeForm.fileIds.length
          ? `已记录 ${madeForm.fileIds.length} 份制作依据，样品寄出后可登记寄样。`
          : '已登记制作完成，样品寄出后可登记寄样。',
      )
      setMadeModal(false)
      setMadeForm({ remark: '', fileIds: [] })
      void refresh()
    },
    onError,
  })

  // 开新修订版（§3.3）：已制作/已寄出之后改车间依据的唯一出路。
  // 后端会拒绝原地改并点名这个接口，界面没有入口就等于把正常操作堵死。
  // 备注可选，这里不额外开弹窗（是否要写原因的交互留给人自己决定）
  const reviseMutation = useMutation({
    mutationFn: () => reviseSample(detailId!),
    onSuccess: (row: SampleRequestRow) => {
      Toast.success(`已开第 ${row.version ?? '?'} 版待审批（原版冻结保留）`)
      navigate(`/samples/${row.id}`)
      void refresh()
    },
    onError,
  })

  // 客户确认与签收分开：客户收到样品 ≠ 客户接受（后端也会拦"未签收就确认"）
  // 登记客户确认结果是**客户侧商务事实**，后端不带 confirm 返回 42206。
  // 「客户接受」与「未通过」都走这道确认（主人 2026-10-10 拍板：两个都加）。
  const [sampleGate, setSampleGate] = useState<{ message: string; accepted: boolean } | null>(
    null,
  )

  const confirmMutation = useMutation({
    mutationFn: ({ accepted, confirm }: { accepted: boolean; confirm: boolean }) =>
      confirmSample(detailId!, accepted, confirmRemark, confirm),
    onSuccess: (_row: SampleRequestRow, variables) => {
      Toast.success(variables.accepted ? '已登记：客户接受' : '已登记：客户未通过')
      setConfirmRemark('')
      void refresh()
    },
    onError: (error: Error, variables) => {
      if (isConfirmRequired(error)) {
        setSampleGate({ message: confirmMessageOf(error), accepted: variables.accepted })
      } else {
        onError(error)
      }
    },
  })

  const addItemMutation = useMutation({
    mutationFn: () => addSampleItem(detailId!, draftPayload(newItem)),
    onSuccess: (row: SampleRequestRow) => {
      // 往已批准 / 已驳回的单子里加明细也会触发重批：新明细带进来的材质工艺图纸版本
      // 同样是车间依据（后端走同一道闸门）。这里也要说清楚，别让用户以为状态自己变了。
      Toast.success(reopenedHint(detail?.status, row.status) ?? '明细已添加')
      setNewItem({ quantity: '1' })
      void refresh()
    },
    onError,
  })

  /* 1024 宽内容区约 700px，样品表七列合计 780px+（两列还是自适应），
     表头「客户 / 商机」和「样品明细」被压到互相重叠、操作列挤成两行。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const columns = [
    {
      title: '客户 / 商机',
      width: compact ? 186 : undefined,
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
      title: (
        <span className="crm-help-label">
          状态
          <HelpTip forLabel="打样状态" text="已寄样 = 样品寄出、等客户签收；已签收 = 客户收到但还没表态；客户接受或未通过，在「客户确认」单独登记；已终止 = 客户不要了/项目取消，流程结束，制作与寄送记录保留。" />
        </span>
      ),
      dataIndex: 'status_label',
      width: compact ? 96 : 100,
      render: (value: string, record: SampleRequestRow) => (
        // 十二行彩色 Tag 一屏下来像一面色块墙（打样草稿橙、已寄样青、已签收绿…），
        // 眼睛先被颜色吃掉。改成「圆点 + 状态文字」，与产品中心「● 在售」同一套语言。
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <StatusDot label={value} tone={STATUS_COLOR[record.status] ?? 'grey'} />
          {record.ship_approval_status === 'pending' && (
            <Tag size="small" color="orange">寄样待审批</Tag>
          )}
        </span>
      ),
    },
    {
      title: compact ? '样品明细 / 快递' : '样品明细',
      dataIndex: 'items',
      /* 非紧凑（列表页）也要给宽度：不给的话宽度被「客户 / 商机」抢走，
         明细只剩 ~200，「中空板箱 5mm（汽配线） · A ×3」第一行就被截。
         编码单独一行后 250 够放一整条最长品名。 */
      width: compact ? 186 : 240,
      render: (items: SampleRequestRow['items'], record: SampleRequestRow) =>
        items.length === 0 ? (
          <span style={{ color: 'var(--crm-text-3)' }}>无明细</span>
        ) : (
          // 列宽固定后长明细会省略号截断；把全文挂到 title，
          // 鼠标停下能看全，不用为此再开一列。
          // 2026-11-19：改「最多两行」—— 单行时整列都是「【验收草稿】预计工时提示 202…」，
          // 一列全是省略号；两行能把产品名和编码都露出来，悬停仍可看全。
          <span
            style={{
              fontSize: 12,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
              /* 必须显式 normal：全站给表格文字挂了 nowrap，
                 不覆盖掉的话 line-clamp 根本不换行（实测两行不生效）。 */
              whiteSpace: 'normal',
              lineHeight: 1.5,
            }}
            title={items
              .map((item) => `${skuLabel({ name: item.sku_name, sku_code: item.sku_code ?? (item.sku_id != null ? String(item.sku_id) : undefined) })} ×${item.quantity}`)
              .join('、')}
          >
            {/* 编码缺失时不要拼出「（编码：null）」：旧写法拿 String(sku_id) 兜底，
                而演示数据里 sku_id 也是空的，于是列表上明晃晃写着 null（2026-11-19 实拍）。 */}
            {/* 两行结构：第一行「产品名 ×数量」，第二行灰字只放编码。
                原来挤成一行「EU 斜插周转箱 · A（编码：ZZX-0031） ×3」，
                折行正好落在「（编 / 码：」中间，读起来像错字（2026-11-20 实拍）。
                多明细时第一行接「等 N 项」，全文仍在 title 里。 */}
            {(() => {
              const first = items[0]
              /* 第一行只要品名：skuLabel 会把「（编码：…）」一起拼进来，
                 那样第一行被截、第二行又重复一遍编码（2026-11-20 r321 实拍）。
                 没有品名时才退回编码当名字。 */
              const label = first.sku_name?.trim() || first.sku_code || '-'
              const code = first.sku_code ?? (first.sku_id != null ? String(first.sku_id) : null)
              const more = items.length > 1 ? ` 等 ${items.length} 项` : ''
              return (
                <>
                  <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {label} ×{first.quantity}
                    {more}
                  </div>
                  {code && (
                    <div
                      style={{
                        fontSize: 11,
                        color: 'var(--crm-text-3)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      编码 {code}
                    </div>
                  )}
                </>
              )
            })()}
            {compact && record.shipments[0] && (
              <div
                title={`${record.shipments[0].carrier ?? '-'} ${record.shipments[0].tracking_no ?? ''}`}
                style={{
                  marginTop: 2,
                  color: 'var(--crm-text-3)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {record.shipments[0].carrier ?? '-'}
                {record.shipments[0].tracking_no ? ` · ${record.shipments[0].tracking_no}` : ''}
              </div>
            )}
          </span>
        ),
    },
    ...(compact
      ? []
      : [{
      title: '快递',
      width: 192,
      render: (_: unknown, record: SampleRequestRow) => {
        const shipment = record.shipments[0]
        if (!shipment) return <span style={{ color: 'var(--crm-text-3)' }}>-</span>
        // 原来承运商和单号各占一行，整表每行被这两行撑到 75px 左右，
        // 一屏只放得下 12 条。改成一行「承运商 · 单号」+ 省略号（全号放 title 里），
        // 行高跟着「客户 / 商机」的两行回到 56（2026-11-21 巡检）。
        return (
          <span
            title={`${shipment.carrier ?? '-'} ${shipment.tracking_no ?? ''}`.trim()}
            style={{
              fontSize: 12,
              display: 'block',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {shipment.carrier ?? '-'}
            {shipment.tracking_no ? ` · ${shipment.tracking_no}` : ''}
          </span>
        )
      },
    }]),
    ...(compact
      ? []
      : [{ title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' }]),
    {
      title: '申请时间',
      dataIndex: 'requested_at',
      width: compact ? 78 : 170,
      /* 窄屏拆两行：上行「11-18」、下行「14:14」，78px 才放得下整套时分 */
      render: compact
        ? (v: string) =>
            v ? (
              <span title={formatBeijingTime(v)} style={{ display: 'grid', gap: 2, justifyItems: 'center' }}>
                <span>{formatBeijingTime(v).slice(5, 10)}</span>
                <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                  {formatBeijingTime(v).slice(11, 16)}
                </span>
              </span>
            ) : (
              '-'
            )
        : fmt,
    },
    {
      // 这一列十行里有九行是"不能删"：与其每行挂一句灰字「不可删」，把那条规则
      // 收到表头一个问号上，行内只在真能删的时候出现红字「删除」。
      title: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          操作
          <Tooltip content="只有待审批（且没提交过审批）、已驳回的打样单能删；已批准 / 已寄出的连审批留痕，不能删">
            <IconHelpCircle style={{ fontSize: 13, color: 'var(--crm-text-3)', cursor: 'help' }} />
          </Tooltip>
        </span>
      ),
      width: compact ? 108 : 118,
      render: (_: unknown, record: SampleRequestRow) => {
        // 已删除的行只给「恢复」：后端对已删的单子查详情一律 404，
        // 摆一个「详情」也是死的，点了只会报错。
        if (record.is_deleted) {
          // 恢复要走 `sample:manage`（与删除共用同一把钥匙），没权限就不摆按钮
          return canManage && !record.revision_locked ? (
            <Popconfirm
              title="恢复这张打样单？恢复后会重新出现在列表里。"
              onConfirm={() => restoreMutation.mutate(record.id)}
            >
              <Button theme="borderless" size="small">
                恢复
              </Button>
            </Popconfirm>
          ) : null
        }
        return (
          <>
            <Button
              theme="borderless"
              size="small"
              onClick={() => navigate(`/samples/${record.id}`)}
            >
              详情
            </Button>
            {/* 能不能删由后端下发（`can_delete`）：只有还没走流程的（待审批 / 已驳回）
                才给「删除」—— 已批准之后的单子连着审批留痕与车间依据。判据别在前端再写一份。 */}
            {/* 不能删的行什么也不摆（主人 2026-11-21「这个难道没有删除的地方吗」：
                十几行里十一行都是灰字「不可删」，远看像整列都被拒绝掉了）。
                规则挪到表头问号，行内只剩「详情」，能删的加一个红字「删除」。 */}
            {canManage && record.can_delete && (
              <Popconfirm
                title="这张打样单将移入回收站，可在“回收站 → 样品”中恢复。"
                onConfirm={() => deleteMutation.mutate(record.id)}
              >
                <Button theme="borderless" size="small" type="danger">
                  删除
                </Button>
              </Popconfirm>
            )}
          </>
        )
      },
    },
  ]

  const skuOptions = (skusQuery.data ?? []).map((sku) => ({
    value: sku.id,
    // 下拉里带上 SKU 名称：只有 `编码 · 规格` 时，一个产品下几个 SKU 认不出区别
    label: skuLabel(sku),
  }))

  useEffect(() => {
    if (detail && searchParams.get('oa') === '1') {
      setOaTarget(detail)
      setSearchParams((current) => { const next = new URLSearchParams(current); next.delete('oa'); return next }, { replace: true })
    }
  }, [detail, searchParams, setSearchParams])

  return (
    <div className="page-container">
      {oaTarget && (
        <SampleDingTalkModal
          key={oaTarget.id}
          sampleId={oaTarget.id}
          customerName={oaTarget.customer_name || (oaTarget.customer_id ? '客户名称暂不可显示' : '未关联客户')}
          onClose={() => { setOaTarget(null); void refresh() }}
        />
      )}
      <ConfirmRequiredModal
        visible={sampleGate !== null}
        message={sampleGate?.message ?? ''}
        onCancel={() => setSampleGate(null)}
        onConfirm={() => {
          const accepted = sampleGate?.accepted
          setSampleGate(null)
          if (accepted !== undefined) {
            confirmMutation.mutate({ accepted, confirm: true })
          }
        }}
        confirmText="确认登记"
      />
      <PageHeader
        title="样品管理"
        subtitle="样品申请、寄样、签收与反馈；样品进展会回写到商机的下一步动作"
        extra={
          canManage && (
            <Button
              theme="outline"
              type="primary"
              onClick={() => {
                setCreateVisible(true)
              }}
            >
              新建样品申请
            </Button>
          )
        }
      />

      {/* 一条样品都没有时，卡片不该跟着撑满整屏（样式里单卡页面会自动拉满）：
          空态是一句话的事，给一张正好装下这句话的卡，底下露页面底色。 */}
      <SectionCard
        style={
          !query.isLoading && (query.data?.items?.length ?? 0) === 0 ? { flex: '0 0 auto' } : undefined
        }
      >
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
            optionList={isReviewer
              ? [
                  ...STATUS_OPTIONS.slice(0, -1),
                  { value: SHIP_PENDING_STATUS_OPTION, label: '寄样待审批' },
                  STATUS_OPTIONS[STATUS_OPTIONS.length - 1],
                ]
              : STATUS_OPTIONS}
          />
        </div>

        <Table
          size="small"
          className="samples-table"
          tableLayout="fixed"
          rowKey="id"
          loading={query.isLoading}
          columns={columns}
          dataSource={query.data?.items ?? []}
          empty={emptyNode(query, {
            title: '还没有样品申请',
            hint: '点右上角「新建样品申请」，登记寄样、签收与反馈，进展会回写到商机的下一步动作。',
          })}
          pagination={{
            currentPage: page,
            pageSize,
            total: query.data?.total ?? 0,
            onPageChange: setPage,
            showSizeChanger: true,
            pageSizeOpts: [10, 20, 50, 100],
            onPageSizeChange: (size: number) => {
              setPageSize(size)
              setPage(1)
            },
          }}
        />
      </SectionCard>

      {/* 终止打样（K3）：二次确认 + 原因；只改状态，制作/寄送事实保留 */}
      <Modal
        title="终止打样"
        visible={terminateVisible}
        onCancel={() => {
          if (!terminateMutation.isPending) setTerminateVisible(false)
        }}
        onOk={() => terminateMutation.mutate()}
        confirmLoading={terminateMutation.isPending}
        okText="确认终止"
        cancelText="取消"
        okButtonProps={{ disabled: !terminateReason.trim(), type: 'danger' }}
        width={480}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)', lineHeight: 1.7 }}>
            客户不要了或项目取消时用。终止后这条单不能再往下办理；
            <strong>已经发生的制作、寄送、签收记录都会保留</strong>，
            不会删除，也不会生成费用记录。终止后不能改回，请确认。
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>终止原因（必填）</div>
            <TextArea
              aria-label="终止原因"
              rows={3}
              placeholder="例如：客户项目取消 / 客户改用其他产品"
              value={terminateReason}
              onChange={setTerminateReason}
              disabled={terminateMutation.isPending}
            />
          </div>
        </div>
      </Modal>

      {/* 新建申请 */}
      <Modal
        title="新建样品申请"
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={() => createMutation.mutate()}
        confirmLoading={createMutation.isPending}
        okText="保存草稿并填写同序 OA 申请"
        width={560}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>关联商机</div>
            <RecordSearchSelect
              key={String(createVisible)}
              scope="sample-opportunity"
              enabled={createVisible}
              placeholder="按商机名称、客户名称或商机编号搜索"
              value={form.opportunity_id}
              fallbackLabel={selectedOpportunityQuery.data?.title}
              list={(input) => listOpportunities({ ...input, status: 'open', search_customer_name: true })}
              lookup={async (id) => {
                const row = await getOpportunity(id)
                return row.status === 'open' ? row : undefined
              }}
              labelOf={(row) => row.customer_name ? `${row.title} · ${row.customer_name}` : row.title}
              onChange={(row) => setForm({ ...form, opportunity_id: row?.id, customer_id: row?.customer_id ?? form.customer_id })}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              客户（不选商机时必填）
            </div>
            <RecordSearchSelect
              key={String(createVisible)}
              scope="sample-customer"
              enabled={createVisible}
              placeholder="按客户名称或编号搜索"
              value={form.customer_id}
              disabled={Boolean(form.opportunity_id)}
              fallbackLabel={selectedOpportunityQuery.data?.customer_name ?? selectedCustomerQuery.data?.name
                ?? (form.customer_id ? `客户编号 ${form.customer_id}` : undefined)}
              list={listCustomers}
              lookup={getCustomer}
              labelOf={(customer) => customer.name}
              onChange={(customer) => setForm({ ...form, customer_id: customer?.id })}
            />
          </div>

          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>样品明细（产品名称 *，打样数量 *）</div>
            {form.items.map((item, index) => (
              <div key={index} style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                {/* **一个框**（2026-10-16 主人：「我不明白为什么还要选择这个」）：
                    申请单原件上就一格「产品名称」，没有"现货还是定制"这个区分 ——
                    那个模式下拉是我们在把自己的数据结构推给业务员。
                    现在：能搜到产品库里的就选（仍关联 SKU）；搜不到就**直接打名字**
                    （当新品，后端有一条"只有名字"的路径，打完样才可能建档）。 */}
                <SampleProductName
                  item={item}
                  skuChoices={skuOptions}
                  scope={`sample-create-${index}`}
                  enabled={createVisible}
                  onChange={(fields) => setForm((current) => ({
                    ...current,
                    items: current.items.map((row, rowIndex) => rowIndex === index ? { ...row, ...fields } : row),
                  }))}
                />
                <Input
                  style={{ width: 96 }}
                  placeholder="打样数量"
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
              onClick={() =>
                setForm({ ...form, items: [...form.items, { quantity: '1' }] })
              }
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
      <Modal
        title="转交打样单"
        visible={transferVisible}
        onCancel={() => { if (!transferMutation.isPending) setTransferVisible(false) }}
        onOk={() => {
          if (detailId !== null && transferOwner) {
            transferMutation.mutate({ sampleId: detailId, ownerId: transferOwner.id })
          }
        }}
        okText="确认转交"
        okButtonProps={{ disabled: !transferOwner, loading: transferMutation.isPending }}
      >
        <p className="workflow-guide">接手人可查看和跟进这张打样单，客户归属和同序审批照常。</p>
        <Select
          aria-label="接手人"
          placeholder="输入姓名查找接手人"
          filter
          remote
          onSearch={setTransferKeyword}
          value={transferOwner?.id}
          onChange={value => setTransferOwner(transferOptions.data?.find(row => row.id === value) ?? null)}
          optionList={(transferOptions.data ?? []).filter(row => row.id !== detail?.owner_id)
            .map(row => ({ value: row.id, label: row.name }))}
          loading={transferOptions.isFetching}
          disabled={transferMutation.isPending}
          style={{ width: '100%' }}
        />
        {transferOptions.isError && <p role="alert">{transferOptions.error.message}</p>}
        {transferMutation.isError && <p role="alert">{transferMutation.error.message}</p>}
      </Modal>

      <SideSheet
        title={detail ? `样品申请编号 ${detail.id}` : '样品申请'}
        visible={detailId !== null}
        onCancel={closeDetail}
        width="min(760px, calc(100vw - 24px))"
      >
        {/* 读不到详情时这里原来只有一个浅蓝药丸按钮挂在左上角，下面整片白 ——
            看不出是"加载完了没数据"还是"接口挂了"（2026-11-20）。
            改成空态 + 一句失败原因，重读降为文字按钮。 */}
        {!detail && detailQuery.isError && (
          <div style={{ paddingTop: 20 }}>
            <EmptyState
              title="这份样品申请没读出来"
              hint={detailQuery.error instanceof Error ? detailQuery.error.message : '接口没有返回数据'}
            />
            <div style={{ textAlign: 'center' }}>
              <Button type="tertiary" theme="borderless" onClick={() => void detailQuery.refetch()}>
                重新读取
              </Button>
            </div>
          </div>
        )}
        {!detail && !detailQuery.isError && (
          <div style={{ color: 'var(--crm-text-3)' }}>正在读取样品申请…</div>
        )}
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
            {nextStep && <div className="workflow-next">
              <strong>{revisionLocked ? '原版只读' : '下一步'}</strong>
              <p className="workflow-guide">{nextStep.hint}</p>
              {detail.superseded_by && <Button size="small" type="primary" theme="outline"
                onClick={() => navigate(`/samples/${detail.superseded_by}`)}>打开下一版</Button>}
              {canUpdateSample && nextStep.action && <Button size="small" onClick={() => {
                if (nextStep.action === 'oa') setOaTarget(detail)
                else {
                  const section = (nextStep.action === 'confirm' ? customerConfirmRef : workActionsRef).current
                  section?.scrollIntoView({ block: 'start' })
                  section?.focus({ preventScroll: true })
                }
              }}>{nextStep.label}</Button>}
            </div>}

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
                gap: 10,
                padding: '10px 12px',
                border: '1px solid var(--crm-border)',
                borderRadius: 8,
                background: 'var(--crm-surface-low)',
              }}
            >
              <MetaItem label="负责人">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  {detail.owner_name ?? '-'}
                  {canUpdateSample && (
                    <Button size="small" theme="borderless" onClick={() => {
                      setTransferKeyword('')
                      setTransferOwner(null)
                      transferMutation.reset()
                      setTransferVisible(true)
                    }}>转交</Button>
                  )}
                </div>
              </MetaItem>
              <MetaItem label="申请时间">{fmt(detail.requested_at)}</MetaItem>
              {detail.approved_at && <MetaItem label="审批时间">{fmt(detail.approved_at)}</MetaItem>}
              {detail.shipped_at && <MetaItem label="寄样时间">{fmt(detail.shipped_at)}</MetaItem>}
              {detail.signed_at && <MetaItem label="签收时间">{fmt(detail.signed_at)}</MetaItem>}
              {detail.terminated_at && <MetaItem label="终止时间">{fmt(detail.terminated_at)}</MetaItem>}
            </div>

            {detail.reject_reason && (
              <div
                style={{
                  padding: '8px 10px',
                  borderRadius: 6,
                  background: 'var(--crm-error-bg, #FFF1F0)',
                  color: 'var(--crm-error)',
                  fontSize: 13,
                  lineHeight: 1.7,
                }}
              >
                {detail.status === 'rejected' ? '拒绝原因' : '上次驳回原因'}：{detail.reject_reason}
              </div>
            )}
            {detail.terminated_at && detail.terminate_reason && (
              <div style={{ fontSize: 13, color: 'var(--crm-error)' }}>
                终止原因：{detail.terminate_reason}
              </div>
            )}
            {detail.remark && (
              <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>备注：{detail.remark}</div>
            )}

            <DetailCard title="样品明细" extra="产品名称 *，打样数量 *">
              {detail.items.length === 0 && (
                <div style={{ padding: '10px 0', color: 'var(--crm-text-3)', fontSize: 13 }}>
                  还没有样品明细；在下面添加产品名称和打样数量。
                </div>
              )}
              {detail.items.length > 0 && (
              <Table
                size="small"
                tableLayout="fixed"
                rowKey="id"
                pagination={false}
                dataSource={detail.items}
                empty="暂无明细"
                columns={[
                  {
                    title: '产品名称 / 需求',
                    dataIndex: 'sku_code',
                    // 编码在上、名称在下（同报价明细的口径）
                    render: (v: string | null, row: SampleRequestRow['items'][number]) => (
                      <div style={{ whiteSpace: 'normal', wordBreak: 'break-word' }}>
                        <div>
                          {row.sku_name || v || '未命名产品'}
                          {row.is_custom && (
                            <Tag size="small" style={{ marginLeft: 6 }}>
                              定制
                            </Tag>
                          )}
                        </div>
                        {v && row.sku_name && (
                          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                            {v}
                          </div>
                        )}
                      </div>
                    ),
                  },
                  { title: '原数量', dataIndex: 'original_quantity', width: 64, render: (v: number | null) => v == null ? <span style={{ color: 'var(--crm-text-3)' }}>未记录</span> : v.toLocaleString('zh-CN') },
                  {
                    /* 抽屉只有 716 宽。原来「本次数量 / 车间依据 / 本次备注 / 本次规格」四列
                       各占一格，「本次规格」被抽屉右边缘直接切掉（2026-11-20 截图，
                       只剩「600×400×320 蓝…」）。数量、规格、备注本来就是同一件事的三个方面，
                       合成一格按行堆着，一行一行读反而更顺。 */
                    title: '本次样品',
                    render: (_: unknown, row: SampleItem) => {
                      const oldSpec = row.source_snapshot?.specification
                      const oldRemark = row.source_snapshot?.remark
                      const specChanged = row.source_snapshot && row.specification !== oldSpec
                      const remarkChanged = row.source_snapshot && (row.remark || '') !== (oldRemark || '')
                      return (
                        <div style={{ whiteSpace: 'normal', wordBreak: 'break-word' }}>
                          <div>{row.quantity} 件{row.specification ? ` · ${row.specification}` : ''}</div>
                          {specChanged && <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>原规格：{oldSpec || '未记录'}</div>}
                          {row.remark && <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>备注：{row.remark}</div>}
                          {remarkChanged && <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>原备注：{oldRemark || '未记录'}</div>}
                        </div>
                      )
                    },
                  },
                  {
                    // 车间依据逐行不同：一单里两个盒子可能材质、工艺、图纸都不一样，
                    // 所以显示在明细行上，而不是单头一栏。
                    title: (
                      <span className="crm-help-label">
                        车间依据
                        <HelpTip forLabel="车间依据" text="做这件样品时交给车间的依据：材质、工艺和图纸版本。同一单里不同明细可能不一样。" />
                      </span>
                    ),
                    width: 150,
                    render: (_: unknown, row: SampleItem) => {
                      const spec = [row.material, row.craft].filter(Boolean).join(' / ')
                      const text = !row.drawing_version
                        ? (spec || '-')
                        : spec ? `${spec}（图纸 ${row.drawing_version}）` : `图纸 ${row.drawing_version}`
                      return <div style={{ whiteSpace: 'normal', wordBreak: 'break-word' }}>{text}</div>
                    },
                  },
                  // 已寄样/已签收不给改：后端也会拒，这里不显示入口省得点了才报错
                  ...(canUpdateSample && !['shipped', 'signed'].includes(detail.status)
                    ? [
                        {
                          title: '操作',
                          width: 56,
                          render: (_: unknown, row: SampleItem) => (
                            <button type="button" className="crm-text-action"
                              onClick={() =>
                                setItemEdit({
                                  id: row.id,
                                  craft: row.craft ?? '',
                                  material: row.material ?? '',
                                  drawing_version: row.drawing_version ?? '',
                                })
                              }
                            >
                              改依据
                            </button>
                          ),
                        },
                      ]
                    : []),
                ]}
              />
              )}
              {canUpdateSample && !['shipped', 'signed'].includes(detail.status) && (
                <div className="sample-add-item">
                  {/* 追加明细同样只用**一个框**（与上面新建时一致，
                      2026-10-16 主人：「我不明白为什么还要选择这个」） */}
                  <SampleProductName
                    item={newItem}
                    skuChoices={skuOptions}
                    scope={`sample-add-${detail?.id ?? 'new'}`}
                    enabled={Boolean(detail)}
                    onChange={(fields) => setNewItem((current) => ({ ...current, ...fields }))}
                  />
                  <Input
                    aria-label="新增打样数量"
                    placeholder="打样数量"
                    value={newItem.quantity}
                    onChange={(value) => setNewItem({ ...newItem, quantity: value })}
                  />
                  <GuardedButton
                    disabled={!draftReady(newItem)}
                    action="添加明细"
                    loading={addItemMutation.isPending}
                    onClick={() => addItemMutation.mutate()}
                  >
                    添加
                  </GuardedButton>
                </div>
              )}
            </DetailCard>

            {detail.shipments.length > 0 && (
              <DetailCard title="寄样记录">
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
                      className: 'cell-right',
                      render: (v: number, shipment: { shipping_fee_exact?: string | null }) =>
                        formatMoney(toTwoDecimals(shipment.shipping_fee_exact ?? v), 'CNY'),
                    },
                  ]}
                />
              </DetailCard>
            )}

            {canManage && nextStep?.action !== 'oa' && (
              <div style={{ display: 'flex' }}>
                <Button theme="borderless" size="small" style={{ alignSelf: 'flex-start' }}
                  onClick={() => setOaTarget(detail)}>查看同序 OA 记录</Button>
              </div>
            )}
            {canManage && (
              <div ref={workActionsRef} tabIndex={-1} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {/* 已批准后的下一步按**业务顺序**给，不同时摆出来：
                    先「登记制作完成」（车间做完这一笔事实），登记过之后才轮到「登记寄样」。
                    原来寄样按钮和制作登记一起出现，可以跳过制作直接寄样，
                    记录里就少了一笔"什么时候做完的"，后续追质量问题没有锚点。 */}
                {canUpdateSample && detail.is_custom !== false && detail.status === 'approved' && (detail.made_at ? (
                  <>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      已登记制作完成，下一步登记寄样（承运商 / 单号 / 运费）。
                    </div>
                    <div className="sample-shipping-fields">
                      <label htmlFor="sample-carrier">承运商
                        <Input id="sample-carrier" placeholder="承运商" value={shipForm.carrier}
                          onChange={(value) => setShipForm({ ...shipForm, carrier: value })} />
                      </label>
                      <label htmlFor="sample-tracking">快递单号
                        <Input id="sample-tracking" placeholder="快递单号" value={shipForm.tracking_no}
                          onChange={(value) => setShipForm({ ...shipForm, tracking_no: value })} />
                      </label>
                      <label htmlFor="sample-shipping-fee">运费（元）
                        <Input id="sample-shipping-fee" inputMode="decimal" placeholder="留空按 0 元"
                          value={shipForm.shipping_fee}
                          onChange={(value) => setShipForm({ ...shipForm, shipping_fee: value })} />
                      </label>
                    </div>
                    <Popconfirm title="确认登记寄样？" content="请在样品实际寄出后登记。客户收到样品时，再登记签收。" onConfirm={() => shipMutation.mutate()}>
                      <Button theme="outline" type="primary" loading={shipMutation.isPending}>登记寄样</Button>
                    </Popconfirm>
                  </>
                ) : (
                  <>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      打样已批准，下一步登记制作完成；登记后才能登记寄样。
                    </div>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <Button
                        theme="outline"
                        type="primary"
                        onClick={() => {
                          setMadeForm({ remark: '', fileIds: [] })
                          setMadeModal(true)
                        }}
                      >
                        登记制作完成
                      </Button>
                    </div>
                  </>
                ))}

                {canUpdateSample && detail.is_custom === false && ['pending', 'approved'].includes(detail.status) && (
                  <DetailCard title="寄样审批" extra="现有产品">
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)', lineHeight: 1.7 }}>
                      现有产品从库存寄样，寄出前需主管批准；承运商与运费在审批时确定，登记寄样时按批准金额记录。
                    </div>
                    {detail.ship_approval?.status === 'pending' && (
                      <div style={{ background: 'var(--crm-warning-bg, #FFF7E6)', border: '1px solid var(--crm-warning-border, #FFE7BA)', borderRadius: 4, padding: 10, fontSize: 13, display: 'grid', gap: 8 }}>
                        <div>
                          寄样申请待主管审批：收件 {detail.ship_approval.recipient || '-'}；
                          付款方 {detail.ship_approval.payer_label || '-'}；
                          承运商 {detail.ship_approval.carrier || '待定'}；
                          运费 {formatMoney(toTwoDecimals(String(detail.ship_approval.shipping_fee ?? '0')), 'CNY')}
                          {detail.ship_approval.note ? `；说明：${detail.ship_approval.note}` : ''}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                            主管在「审批中心」处理；批准后这里会显示可登记寄样。
                          </span>
                          <Link to="/approvals"><Button size="small" theme="outline">去审批中心</Button></Link>
                        </div>
                      </div>
                    )}
                    {detail.ship_approval?.status === 'rejected' && (
                      <div style={{ color: 'var(--crm-error)', fontSize: 13 }}>
                        上次申请被驳回：{detail.ship_approval.comment || '未填原因'}。修改后可重新提交。
                      </div>
                    )}
                    {detail.ship_approval?.status === 'approved' ? (
                      <>
                        <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                          已由主管批准，运费 {formatMoney(toTwoDecimals(String(detail.ship_approval.shipping_fee ?? '0')), 'CNY')}
                          （{detail.ship_approval.payer_label || '-'}）；收件 {detail.ship_approval.recipient || '-'}；寄出后登记单号即可。
                        </div>
                        <div className="sample-shipping-fields">
                          <label htmlFor="ship-approved-carrier">承运商
                            <Input id="ship-approved-carrier" placeholder="承运商" value={shipForm.carrier}
                              onChange={(value) => setShipForm({ ...shipForm, carrier: value })} />
                          </label>
                          <label htmlFor="ship-approved-tracking">快递单号
                            <Input id="ship-approved-tracking" placeholder="快递单号" value={shipForm.tracking_no}
                              onChange={(value) => setShipForm({ ...shipForm, tracking_no: value })} />
                          </label>
                          <label htmlFor="ship-approved-fee">运费（元）
                            <Input id="ship-approved-fee" value={String(detail.ship_approval.shipping_fee ?? '0')} disabled />
                          </label>
                        </div>
                        <Popconfirm title="确认登记寄样？" content="请在样品实际寄出后登记。客户收到样品时，再登记签收。" onConfirm={() => shipMutation.mutate()}>
                          <Button theme="outline" type="primary" loading={shipMutation.isPending}>登记寄样</Button>
                        </Popconfirm>
                      </>
                    ) : detail.ship_approval?.status !== 'pending' && (
                      <>
                        <div>
                          <div style={{ marginBottom: 4 }}>
                            收件信息（姓名 / 电话 / 详细地址）
                            <span style={{ color: 'var(--crm-error)' }}> *</span>
                          </div>
                          <Input
                            aria-label="收件信息"
                            placeholder="例如：张三 13800000000 宁波市江北区XX路88号"
                            value={shipApprovalForm.recipient}
                            onChange={(value) => setShipApprovalForm({ ...shipApprovalForm, recipient: value })}
                          />
                        </div>
                        <div>
                          <div style={{ marginBottom: 4 }}>
                            运费承担
                            <span style={{ color: 'var(--crm-error)' }}> *</span>
                          </div>
                          <Select
                            aria-label="运费承担方"
                            style={{ width: '100%' }}
                            placeholder="请选择运费由谁承担"
                            value={shipApprovalForm.payer || undefined}
                            optionList={[
                              { value: 'company', label: '公司承担' },
                              { value: 'customer', label: '客户承担（客户付费寄样）' },
                            ]}
                            onChange={(value) => setShipApprovalForm({
                              ...shipApprovalForm,
                              payer: value as 'company' | 'customer',
                            })}
                          />
                        </div>
                        <div className="sample-shipping-fields">
                          <label htmlFor="ship-apply-carrier">承运商
                            <Input id="ship-apply-carrier" placeholder="承运商" value={shipApprovalForm.carrier}
                              onChange={(value) => setShipApprovalForm({ ...shipApprovalForm, carrier: value })} />
                          </label>
                          <label htmlFor="ship-apply-fee">运费（元）
                            <Input id="ship-apply-fee" inputMode="decimal" placeholder="留空按 0 元" value={shipApprovalForm.shipping_fee}
                              onChange={(value) => setShipApprovalForm({ ...shipApprovalForm, shipping_fee: value })} />
                          </label>
                        </div>
                        <TextArea rows={2} placeholder="给主管的说明（选填）" value={shipApprovalForm.note}
                          onChange={(value) => setShipApprovalForm({ ...shipApprovalForm, note: value })} />
                        <Button theme="outline" type="primary" style={{ alignSelf: 'flex-start', justifySelf: 'start', width: 'auto' }} loading={applyShipApprovalMutation.isPending} onClick={() => applyShipApprovalMutation.mutate()}>提交寄样申请</Button>
                      </>
                    )}
                  </DetailCard>
                )}

                {/* 页顶「下一步」条上的「去登记签收」只是把视线带到这一段，
                    真正的登记动作在这里执行 —— 所以按钮必须留着。 */}
                {canUpdateSample && detail.status === 'shipped' && (
                  <Popconfirm title="确认登记客户已签收？" content="这里只记录客户收到样品。客户接受或未通过，请在「客户确认」登记。" onConfirm={() => signMutation.mutate()}>
                    <Button theme="borderless" type="primary" style={{ alignSelf: 'flex-start' }} loading={signMutation.isPending}>登记签收</Button>
                  </Popconfirm>
                )}

                {canUpdateSample && ['shipped', 'signed'].includes(detail.status) && (
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
                      style={{ alignSelf: 'flex-start' }}
                      onClick={() => feedbackMutation.mutate()}
                    >
                      登记反馈
                    </Button>
                    {!feedback.trim() && (
                      <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                        先在上面写一句客户怎么说，再登记反馈。
                      </div>
                    )}
                  </>
                )}

                {canUpdateSample && detail.can_terminate && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <Button
                      theme="borderless"
                      type="danger"
                      style={{ alignSelf: 'flex-start' }}
                      onClick={() => {
                        setTerminateReason('')
                        setTerminateVisible(true)
                      }}
                    >
                      终止打样
                    </Button>
                    <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      客户不要了/项目取消时用；制作与寄送记录会保留。
                    </span>
                  </div>
                )}

                {detail.feedback && (
                  <div style={{ background: 'var(--crm-surface-low)', padding: 12, borderRadius: 4 }}>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>客户反馈</div>
                    <div>{detail.feedback}</div>
                  </div>
                )}

                {/* 只挂了 `dingtalk_oa` 而没有单号时（例如只登记了 OA 提交状态），
                    原来会印出「打样来源： V」—— 两个值都是 undefined。
                    没有来源单号就整行不显示。 */}
                {detail.source_context?.no && <p>打样来源：{detail.source_context.no} V{detail.source_context.version}{detail.source_context.is_historical ? '（取用时为历史版本）' : ''}</p>}
                {/* 生产打样资料（文档 §3.5）：车间照着这张单子干活——
                    这里放的是**整单属性**（用途 / 交期 / 验收标准 / 费用）；
                    材质、工艺、图纸版本逐行不同，在上面的明细行里改 */}
                <DetailCard
                  title="生产打样资料"
                  extra={hasProdInfo ? undefined : '未填写'}
                  action={canUpdateSample && !prodEditing ? (
                    <button type="button" className="crm-text-action" onClick={startProdEditing}>
                      编辑
                    </button>
                  ) : null}
                >

                  {prodEditing ? (
                    <div style={{ display: 'grid', gap: 8 }}>
                      <Input
                        placeholder="用途（如：客户新品打样确认）"
                        value={prodForm.purpose}
                        onChange={(v) => setProdForm({ ...prodForm, purpose: v })}
                      />
                      <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))' }}>
                        <div style={{ minWidth: 0 }}>
                          <FormLabel>目标完成日</FormLabel>
                          <DatePicker
                            type="date"
                            format="yyyy-MM-dd"
                            showClear
                            style={{ width: '100%' }}
                            placeholder="选择日期"
                            value={
                              prodForm.target_completion_date
                                ? new Date(prodForm.target_completion_date)
                                : undefined
                            }
                            onChange={(_, dateStr) =>
                              setProdForm({
                                ...prodForm,
                                target_completion_date: (dateStr as string) || '',
                              })
                            }
                          />
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <FormLabel>打样费用（元）</FormLabel>
                          <Input
                            style={{ width: '100%' }}
                            placeholder="请输入金额"
                            value={prodForm.sample_fee}
                            onChange={(v) => setProdForm({ ...prodForm, sample_fee: v })}
                          />
                          <div style={{ marginTop: 4, fontSize: 12, color: 'var(--crm-text-3)' }}>
                            留空表示未填；免费请填 0
                          </div>
                        </div>
                      </div>
                      <TextArea
                        placeholder="验收标准"
                        rows={2}
                        value={prodForm.acceptance_criteria}
                        onChange={(v) => setProdForm({ ...prodForm, acceptance_criteria: v })}
                      />
                      {/* ---- 同序 OA《打样申请单》上的格（2026-10-15）----
                          选项**照抄同序 OA 下拉**（同序 OA 那边是下拉，写错一个字导出的单子
                          就和同序 OA 对不上，而人眼发现不了）。填好这几项之后，
                          点下面的「导出打样申请单」就能拿到填好的 Excel，直接传同序 OA。 */}
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <Select
                          style={{ width: 170 }}
                          placeholder="打样类型"
                          value={prodForm.sample_type || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, sample_type: (v as string) ?? '' })}
                          optionList={[
                            { value: '设计研发内部打样', label: '设计研发内部打样' },
                            { value: '销售客户打样', label: '销售客户打样' },
                          ]}
                        />
                        <Select
                          style={{ width: 170 }}
                          placeholder="打样产品大类"
                          value={prodForm.product_category || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, product_category: (v as string) ?? '' })}
                          optionList={[
                            { value: '高频类产品', label: '高频类产品' },
                            { value: '车缝类产品', label: '车缝类产品' },
                          ]}
                        />
                        <Select
                          style={{ width: 170 }}
                          placeholder="样品制作单位"
                          value={prodForm.maker_unit || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, maker_unit: (v as string) ?? '' })}
                          optionList={[
                            { value: '义乌打样部', label: '义乌打样部' },
                            { value: '山东生产部', label: '山东生产部' },
                            // 必须与同序 OA 模板选项逐字一致（OA 里是「外协厂打样」）。
                            { value: '外协厂打样', label: '外协厂打样' },
                          ]}
                        />
                      </div>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <Select
                          style={{ width: 170 }}
                          placeholder="打样目的"
                          value={prodForm.sample_purpose || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, sample_purpose: (v as string) ?? '' })}
                          optionList={[
                            { value: '客审确认样', label: '客审确认样' },
                            { value: '报价核算样', label: '报价核算样' },
                            { value: '定制改款样', label: '定制改款样' },
                            { value: '送检上架样', label: '送检上架样' },
                          ]}
                        />
                        <Select
                          style={{ width: 130 }}
                          placeholder="紧急程度"
                          value={prodForm.urgency || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, urgency: (v as string) ?? '' })}
                          optionList={[
                            { value: '普通', label: '普通' },
                            { value: '急', label: '急' },
                            { value: '紧急', label: '紧急' },
                          ]}
                        />
                        <Select
                          style={{ width: 170 }}
                          placeholder="样品情况"
                          value={prodForm.sample_situation || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, sample_situation: (v as string) ?? '' })}
                          optionList={[
                            { value: '首次设计新品', label: '首次设计新品' },
                            { value: '设计更改', label: '设计更改' },
                            { value: '客户首次要求', label: '客户首次要求' },
                            { value: '客户更改', label: '客户更改' },
                            { value: '其他', label: '其他' },
                          ]}
                        />
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <Input
                          style={{ width: 160 }}
                          placeholder="预计工时（小时）"
                          value={prodForm.estimated_hours}
                          onChange={(v) => setProdForm({ ...prodForm, estimated_hours: v })}
                        />
                        <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                          打样积分＝工时 × 10 =
                          <b>
                            {prodForm.estimated_hours.trim() &&
                            Number.isFinite(Number(prodForm.estimated_hours))
                              ? ` ${Number(prodForm.estimated_hours) * 10} `
                              : ' — '}
                          </b>
                          分（系统算，不手填）
                        </span>
                      </div>
                      {/* 2026-10-16：申请单上的**产品名称 + 打样具体要求 6 项**在这里
                          删掉了 —— 它们与「同序 OA 打样申请」弹窗里那一组是**同一批字段**
                          （弹窗从库里回填，两处读写同源），两套输入框只会让人不知道该填哪、
                          或者以为要填两遍。现在只在弹窗那一处填。
                          下面「用途 / 目标完成日 / 费用 / 验收标准」以及
                          「打样类型 / 产品大类 / 制作单位 / 预计工时」是**这里独有**的
                          （同序 OA 模板上没有对应格），所以留在本页。 */}
                      <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                        材质、工艺、图纸版本在「样品明细」里逐行填——它们每个商品可能都不一样。
                        改这几项（或这里的交期、验收标准）会让已批准的单子退回「待审批」。
                      </div>
                      <div style={{ display: 'flex', gap: 8 }}>
                        <Button
                          theme="outline"
                          type="primary"
                          loading={saveProdMutation.isPending}
                          onClick={() => saveProdMutation.mutate()}
                        >
                          保存
                        </Button>
                        <Button onClick={() => setProdEditing(false)}>取消</Button>
                      </div>
                    </div>
                  ) : hasProdInfo ? (
                    <div style={{ display: 'grid', gap: 12 }}>
                      <div
                        style={{
                          display: 'grid',
                          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                          gap: 10,
                        }}
                      >
                        {detail.purpose && <MetaItem label="用途">{detail.purpose}</MetaItem>}
                        {detail.target_completion_date && (
                          <MetaItem label="目标完成日">{detail.target_completion_date}</MetaItem>
                        )}
                        {detail.acceptance_criteria && (
                          <MetaItem label="验收标准">{detail.acceptance_criteria}</MetaItem>
                        )}
                        {detail.sample_fee != null && (
                          <MetaItem label="打样费用">
                            {formatMoney(toTwoDecimals(detail.sample_fee_exact ?? detail.sample_fee), 'CNY')}
                          </MetaItem>
                        )}
                        {detail.made_at && (
                          <MetaItem label="制作完成">{formatBeijingDate(detail.made_at)}</MetaItem>
                        )}
                        <MetaItem label="版本">
                          第 {detail.version ?? 1} 版
                          {revisionLocked && '（只读）'}
                        </MetaItem>
                      </div>
                      {/* 制作依据（第一批返修 §3.5）：这次照哪几份文件做的。
                          三种情况必须分开说（返工单 P2-7）：
                            · 还没登记制作 → 不适用；
                            · 这条记录早于本功能 → **明确标注"未登记"**，
                              绝不能显示成"已确认没有依据"（那是把"不知道"说成了"没有"）；
                            · 登记过但当时没选 → 如实说"登记时未指定"。 */}
                      {detail.made_at && (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          制作依据：
                          {!detail.basis_files ? (
                            <span style={{ color: 'var(--crm-warning)' }}>
                              这条记录早于「制作依据」功能，当时没有登记（不代表没有依据）
                            </span>
                          ) : detail.basis_files.length === 0 ? (
                            <span style={{ color: 'var(--crm-text-3)' }}>登记时未指定</span>
                          ) : (
                            detail.basis_files.map((file: BasisFile) => (
                              <div key={file.file_id} style={{ marginLeft: 8 }}>
                                · {file.file_name || `文件编号 ${file.file_id}`}
                                <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                                  （第 {file.sample_version ?? 1} 版依据，校验值{' '}
                                  {file.checksum ? `${file.checksum.slice(0, 12)}…` : '未记录'}）
                                </span>
                              </div>
                            ))
                          )}
                        </div>
                      )}
                      {detail.made_events && detail.made_events.length > 0 && (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          制作记录：
                          {detail.made_events.map((event, index) => (
                            <div key={event.key ?? index} style={{ marginLeft: 8 }}>
                              · {event.at ? formatBeijingDate(event.at) : '-'}
                              {event.note ? `：${event.note}` : ''}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div style={{ fontSize: 13, color: 'var(--crm-text-3)' }}>
                      还没有填写生产打样资料。
                    </div>
                  )}

                  {/* 「登记制作完成」入口已挪到详情顶部动作区（跟「下一步」放在一起）：
                      这一栏只留资料展示，同一个人不用滚到页面底部再找按钮。 */}

                  {/* 已制作 / 已寄出之后改车间依据的**唯一出路**（§3.3）：
                      原地改会被后端拒绝（同一行上留着旧制作时间却写新资料，和已做出来的
                      实物对不上）。这里开新修订版：原版连同制作/寄送事实冻结保留。
                      已被取代的那一版不再给入口——它已经冻结只读。 */}
                  {canUpdateSample
                    && (detail.made_at || detail.status === 'shipped'
                        || detail.status === 'signed') && (
                    <Popconfirm title="确认开新修订版？原版将冻结只读。" onConfirm={() => reviseMutation.mutate()}>
                      <Button style={{ marginTop: 8, marginLeft: 8 }} loading={reviseMutation.isPending}>
                        开新修订版（改车间依据）
                      </Button>
                    </Popconfirm>
                  )}
                </DetailCard>

                {/* 客户确认：签收是物流事实、确认是业务事实，两者分开（文档 §3.5） */}
                <DetailCard
                  title="客户确认"
                  extra={`${detail.confirm_status_label}${detail.customer_confirmed_at
                    ? ` · ${formatBeijingDate(detail.customer_confirmed_at)}`
                    : ''}`}
                >
                  <div ref={customerConfirmRef} tabIndex={-1}>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 8 }}>
                      客户收到样品不等于样品被接受，结果以客户明确表态为准。
                    </div>
                  {canUpdateSample && detail.status === 'signed' && detail.confirm_status === 'pending' && (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <Input
                        placeholder="客户反馈（可不填）"
                        value={confirmRemark}
                        onChange={setConfirmRemark}
                      />
                      <Button
                        type="primary"
                        loading={confirmMutation.isPending}
                        onClick={() => confirmMutation.mutate({ accepted: true, confirm: false })}
                      >
                        客户接受
                      </Button>
                      <Button
                        type="danger"
                        loading={confirmMutation.isPending}
                        onClick={() => confirmMutation.mutate({ accepted: false, confirm: false })}
                      >
                        未通过
                      </Button>
                    </div>
                  )}
                  </div>
                </DetailCard>

                {/* 附件区（2026-10-08 补）。
                    这里原本**没有上传入口**：而「登记制作完成」那个弹窗写着
                    「请先在详情的附件区上传图纸/确认件」—— 指向的正是这块不存在的地方，
                    于是「制作依据」永远只能选一个空列表，等于那个功能在界面上用不了。
                    后端一直是完整的：能上传、能登记依据（连文件校验值一起存快照）、
                    制作/寄出之后只许标「后续补充资料」。

                    锁定之后上传的**自动**按「后续补充资料」归类，不用用户选 ——
                    锁定与否由后端下发（`attachment_locked` / `attachment_lock`），
                    前端不自己按 made_at / status 推一遍（推出来的那份迟早和后端分叉）。 */}
                <DetailCard title="附件" extra={detail.attachment_locked ? '已锁定' : '可上传'}>
                  {detail.attachment_locked && (
                    <div
                      style={{
                        marginBottom: 8,
                        fontSize: 12,
                        color: 'var(--crm-warning)',
                      }}
                    >
                      {/* 锁定原因由后端下发，原文里有「钉钉」这种旧平台名
                          （`file/access.py`：「打样申请已提交或关联钉钉，制作资料已经冻结」）。
                          过一层 humanize：平台名按现在实际用的「同序 OA」显示，
                          冒号也换成句号，别让两句话粘成一句。 */}
                      {humanizeBackendText(detail.attachment_lock)}。新上传的附件自动归入「后续补充资料」，与制作依据分开记录。
                    </div>
                  )}
                  {/* 默认说明是「合同、图纸、回款凭证」—— 那是合同页的语境，
                      放在打样申请里等于让人传回款凭证，明显错位（2026-11-17 视觉审视 V2-11 同类） */}
                  <AttachmentPanel
                    businessType="sample"
                    businessId={detail.id}
                    writePermission="sample:manage"
                    showCategory
                    compact
                    hint="放样品实物照片、尺寸图、客户确认截图，单个文件不超过 20 MB"
                    category={detail.attachment_locked ? 'supplement' : undefined}
                  />
                </DetailCard>

                {/* 场景12：从这张打样申请出打样需求单，来源询价与本次差异随文件落快照 */}
                <DetailCard title="打样需求单">
                  <BizDocPanel
                    docType="sample_request"
                    sampleRequestId={detail.id}
                    canManage={canManage}
                    compact
                    emptyInline
                    compactEmptyText="还没有生成过打样需求单 —— 点右上「生成一份」，按本次申请内容出一份 PDF。"
                  />
                </DetailCard>
              </div>
            )}
          </div>
        )}
      </SideSheet>

      {/* 改一条明细的车间依据。单独开弹窗而不是行内编辑：这三个字段是「审批批的
          那一版资料」，改它们会触发退回重审，值得一次明确的确认动作 */}
      {/* 登记制作完成（含制作依据，第一批返修 §3.5 / 返工单 P2-7）。
          「照哪几份文件做的」必须在这里点一次：不点，事后出了质量问题连
          比对对象都没有；而"制作依据"这个字段后端早就存了，只是页面一直没接。 */}
      <Modal
        width={520}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title="登记制作完成"
        visible={madeModal}
        onCancel={() => setMadeModal(false)}
        onOk={() => madeMutation.mutate()}
        confirmLoading={madeMutation.isPending}
        okText="确认制作完成"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <p className="workflow-guide">请在样品实际制作完成后登记。保存后可填写寄样信息。</p>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-2)', marginBottom: 4 }}>
              制作说明（可选）
            </div>
            <TextArea
              rows={2}
              value={madeForm.remark}
              onChange={(value) => setMadeForm({ ...madeForm, remark: value })}
              placeholder="如：按第 2 版图纸做，色差已和客户确认"
            />
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
              这次照哪几份文件做的？
            </div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 6 }}>
              从本单附件里选取实际使用的图纸或确认件，便于日后核对。也可以不选，直接登记制作完成。
            </div>
            {madeFilesQuery.isLoading && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>正在读附件…</div>
            )}
            {!madeFilesQuery.isLoading && (madeFilesQuery.data ?? []).length === 0 && (
              <div style={{ fontSize: 12, color: 'var(--crm-warning)' }}>
                这张单子上还没有附件。请先在上面的「附件」区上传图纸/确认件，再回来登记依据。
              </div>
            )}
            <div style={{ display: 'grid', gap: 4 }}>
              {(madeFilesQuery.data ?? []).map((file) => {
                const checked = madeForm.fileIds.includes(file.id)
                return (
                  <label
                    key={file.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: '6px 8px',
                      border: `1px solid ${checked ? 'var(--crm-primary)' : 'var(--crm-outline)'}`,
                      borderRadius: 4,
                      cursor: 'pointer',
                      background: checked ? 'var(--crm-primary-soft)' : 'transparent',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setMadeForm({
                          ...madeForm,
                          fileIds: checked
                            ? madeForm.fileIds.filter((id) => id !== file.id)
                            : [...madeForm.fileIds, file.id],
                        })
                      }
                    />
                    <span style={{ fontSize: 13 }}>
                      {file.file_name}
                      {file.category ? `（${file.category}）` : ''}
                    </span>
                  </label>
                )
              })}
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        width={520}
        title="修改车间依据"
        visible={itemEdit !== null}
        onCancel={() => setItemEdit(null)}
        onOk={() => saveItemMutation.mutate()}
        confirmLoading={saveItemMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        {itemEdit && (
          <div style={{ display: 'grid', gap: 8 }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <Input
                placeholder="材质"
                value={itemEdit.material}
                onChange={(v) => setItemEdit({ ...itemEdit, material: v })}
              />
              <Input
                placeholder="工艺"
                value={itemEdit.craft}
                onChange={(v) => setItemEdit({ ...itemEdit, craft: v })}
              />
            </div>
            <Input
              placeholder="图纸版本"
              value={itemEdit.drawing_version}
              onChange={(v) => setItemEdit({ ...itemEdit, drawing_version: v })}
            />
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              留空表示「未填」。这三项是车间干活的依据，改完已批准的单子会退回「待审批」，
              需要主管重新审批；已寄样 / 已签收的单子不能再改。
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
