import AutoOaSync from '../../shared/components/AutoOaSync'
import ApplicantIdentity from '../../shared/components/ApplicantIdentity'
import { formatBeijingDate, formatBeijingTime } from '../../shared/beijing-time'
import { useEffect, useRef, useState, type ComponentProps, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Banner,
  Button,
  Dropdown,
  DatePicker,
  Input,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tag,
  TextArea,
  Toast,
  Upload,
} from '@douyinfe/semi-ui'

import type { FileItem } from '@douyinfe/semi-ui/lib/es/upload/interface'
import { listBusinessFiles, uploadFile } from '../../shared/api/file'
import { approvalProgress, formatApprovalTime, formatApprovalEventTime } from './approval-progress'
import AttachmentPanel from '../common/AttachmentPanel'
import SampleFromSourceModal from '../sample/SampleFromSourceModal'
import { optionalQuantityText, parseOptionalDecimal, requiredDecimalOrThrow } from '../../shared/number-input'
import { usePermissions } from '../../shared/hooks/permissions'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import StatusDot from '../../shared/components/StatusDot'
import type { TagTone } from '../../shared/types'
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
import { getCustomer, listCustomers } from '../../shared/api/customer'
import { getOpportunity, listOpportunities } from '../../shared/api/opportunity'
import {
  listInquiryApprovals,
  getInquirySubmissionOptions,
  resolveOaInstance,
  withdrawOaInstance,
  startInquiryApproval,
  type OaApprovalInstance,
} from '../../shared/api/dingtalk'
import { newRequestKey } from '../../shared/api/requestKey'
import FormLabel from '../../shared/components/FormLabel'
import OaFormSnapshot from '../../shared/components/OaFormSnapshot'
import SyncOaButton from '../../shared/components/SyncOaButton'
import { useBlockedNotify } from '../../shared/components/BlockedHint'
import RecordSearchSelect from '../../shared/components/RecordSearchSelect'

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
  opportunity_id?: number | null
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

/** 定制需求（文档 §3.3「产品知识库」三态里的定制询价那一类）：客户问了但没有标准产品的需求沉淀。
 *  标签此前叫「知识库」，与文档里的伞概念重名，2026-10-06 统一改成「定制需求」。 */
export default function KnowledgePage() {
  /* 1024 宽内容区约 700px，定制询价表八列合计 1103px，「记录人 / 记录时间 /
     操作 / 状态」整块被裁到卡片外、底部常驻横向滚动条。窄屏切紧凑档：
     数量与目标价并一列两行，记录时间并进记录人第二行。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const rawOpportunityId = Number(searchParams.get('opportunity_id'))
  const opportunityFilter = Number.isSafeInteger(rawOpportunityId) && rawOpportunityId > 0 ? rawOpportunityId : undefined
  const rawInquiryId = Number(searchParams.get('inquiry_id'))
  const inquiryFilter = Number.isSafeInteger(rawInquiryId) && rawInquiryId > 0 ? rawInquiryId : undefined
  const { can } = usePermissions()
  const canManage = can('quote:manage')
  const [moreTargetId, setMoreTargetId] = useState<number | null>(null)
  const [photoTarget, setPhotoTarget] = useState<CustomInquiryRow | null>(null)
  const [submitTarget, setSubmitTarget] = useState<CustomInquiryRow | null>(null)
  const [submitAttachments, setSubmitAttachments] = useState<number[]>([])
  const [submitFields, setSubmitFields] = useState<Record<string, string>>({})
  const [submitResubmit, setSubmitResubmit] = useState(false)
  const [submitQuoteOwner, setSubmitQuoteOwner] = useState<string | undefined>()
  const [draftSourceId, setDraftSourceId] = useState<number | null>(null)
  const [sampleSourceId, setSampleSourceId] = useState<number | null>(null)
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  // 深链（第五批 §6.2(6)）：`?keyword=需求编号` 直接从别处跳进来就能筛出那一条。
  // 用 keyword 而不是新增一个参数：列表本来就有编号检索，共用同一套逻辑，
  // 少一个"只在深链时才生效"的分支。
  const [keywordInput, setKeywordInput] = useState(() => searchParams.get('keyword') ?? '')
  const [keyword, setKeyword] = useState(() => searchParams.get('keyword') ?? '')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const searchInquiries = () => {
    setKeyword(keywordInput.trim())
    setPage(1)
  }
  const resetInquiryFilters = (includeScope = false) => {
    setKeywordInput('')
    setKeyword('')
    setStatusFilter(undefined)
    setPage(1)
    setSearchParams((params) => {
      const next = new URLSearchParams(params)
      next.delete('keyword')
      if (includeScope) {
        next.delete('inquiry_id')
        next.delete('opportunity_id')
      }
      return next
    }, { replace: true })
  }
  const [editVisible, setEditVisible] = useState(false)
  const [editing, setEditing] = useState<CustomInquiryRow | null>(null)
  const [form, setForm] = useState<InquiryForm>(EMPTY_FORM)
  const [pendingPhotos, setPendingPhotos] = useState<FileItem[]>([])
  const [pendingAttachments, setPendingAttachments] = useState<FileItem[]>([])
  const saveIntent = useRef(false)
  const [saveIntentState, setSaveIntentState] = useState(false)
  const savedInquiry = useRef<CustomInquiryRow | null>(null)
  // 修订（§3.3）：客户改了要求 → 新增一版并留说明，旧版保留
  const [reviseTarget, setReviseTarget] = useState<CustomInquiryRow | null>(null)
  const [reviseForm, setReviseForm] = useState({
    revision_note: '',
    description: '',
    quantity: '',
    target_price: '',
  })
  const [historyTarget, setHistoryTarget] = useState<CustomInquiryRow | null>(null)
  // 同序 OA 审批记录：查看历次提交；"结果不明"的单子在这里转人工处理
  const [approvalTarget, setApprovalTarget] = useState<CustomInquiryRow | null>(null)
  const [adoptTarget, setAdoptTarget] = useState<OaApprovalInstance | null>(null)
  const [adoptInstanceId, setAdoptInstanceId] = useState('')
  /** 当前这次人工核定的请求键：打开对话框时生成，成功后清掉（重试复用同一个） */
  const resolveKeyRef = useRef<string | null>(null)
  // 转报价（§3.1/场景09）：定制件投产前没有 SKU，报价中心选不到它，
  // 这里给一条"填两个数就成单"的出口
  const [quoteTarget, setQuoteTarget] = useState<CustomInquiryRow | null>(null)
  const [quoteForm, setQuoteForm] = useState({
    unit_cost: '',
    quoted_price: '',
    quantity: '',
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
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  })
  const resolveMutation = useMutation({
    mutationFn: ({
      oaId,
      action,
      instanceId,
      requestKey,
    }: {
      oaId: number
      action: 'adopt' | 'resend' | 'abandon'
      instanceId?: string
      requestKey?: string
    }) =>
      resolveOaInstance(oaId, {
        action,
        instance_id: instanceId,
        note: action === 'abandon' ? '人工核对确认同序 OA 未建单，作废本轮' : undefined,
        // 同一次核定带同一个请求键：重试只生效一次并回放同一份结果。
        // 换新键等于告诉后端"这是一次新的核定"（就会再打一次同序 OA）
        request_key: requestKey,
      }),
    onSuccess: (row) => {
      Toast.success(`已处理：${row.status_label}`)
      setAdoptTarget(null)
      setAdoptInstanceId('')
      resolveKeyRef.current = null
      void queryClient.invalidateQueries({ queryKey: ['inquiry-approvals'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const approvalRows = approvalsQuery.data ?? []
  // 只有**最新一轮**才谈得上重提/重试：旧轮次即使状态允许，服务端也会按最新一轮拒绝
  const latestApprovalRound = approvalRows.reduce(
    (max, row) => Math.max(max, row.submit_round ?? 1),
    0,
  )

  const quoteMutation = useMutation({
    mutationFn: () =>
      createQuoteFromInquiry(quoteTarget!.id, {
        unit_cost: requiredDecimalOrThrow(quoteForm.unit_cost, '核价成本'),
        quoted_price: requiredDecimalOrThrow(quoteForm.quoted_price, '报价'),
        quantity: optionalQuantityText(quoteForm.quantity) ?? undefined,
      }),
    onSuccess: (data) => {
      setQuoteTarget(null)
      refresh()
      Toast.success(`报价草稿 ${data.quote_no} 已生成`)
      navigate(`/quotes/${data.quote_id}`)
    },
  })

  const summaryQuery = useQuery({
    queryKey: ['custom-inquiry-summary'],
    queryFn: () => customInquiryStatusSummary(),
  })
  const query = useQuery({
    queryKey: ['custom-inquiries', { statusFilter, keyword, page, pageSize, opportunityFilter, inquiryFilter }],
    refetchInterval: 15000,
    queryFn: () =>
      listCustomInquiries({ status: statusFilter, keyword, page, page_size: pageSize,
        opportunity_id: opportunityFilter, inquiry_id: inquiryFilter }),
  })
  const linkedOpportunity = useQuery({
    queryKey: ['opportunity', opportunityFilter],
    queryFn: () => getOpportunity(opportunityFilter!), enabled: Boolean(opportunityFilter),
  })
  const selectedOpportunity = useQuery({
    queryKey: ['opportunity', form.opportunity_id],
    queryFn: () => getOpportunity(form.opportunity_id!), enabled: editVisible && Boolean(form.opportunity_id),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['custom-inquiries'] })
    void queryClient.invalidateQueries({ queryKey: ['custom-inquiry-summary'] })
  }

  const saveMutation = useMutation({
    mutationFn: async (payload: CustomInquiryPayload) => {
      const existing = editing ?? savedInquiry.current
      const draft = { fields: submitFields, quote_owner_user_id: selectedAllowedQuoteOwner ?? null, attachment_file_ids: submitAttachments }
      const data = { ...payload, oa_draft: draft }
      const row = existing ? await updateCustomInquiry(existing.id, data) : await createCustomInquiry(data)
      // 创建成功即记住真实 ID；照片失败后重试只能更新这条需求，不能再创建一条。
      savedInquiry.current = row
      setEditing(row)
      refresh()
      for (const photo of pendingPhotos) {
        if (!photo.fileInstance) throw new Error('照片内容已失效，请移除后重新选择')
        try {
          await uploadFile(photo.fileInstance, { businessType: 'inquiry', businessId: row.id })
        } catch (error) {
          throw new Error(`询价已保存，照片尚未全部上传：${error instanceof Error ? error.message : '上传失败'}。请在当前窗口重试保存。`)
        }
        setPendingPhotos((current) => current.filter((item) => item.uid !== photo.uid))
        void queryClient.invalidateQueries({ queryKey: ['business-files', 'inquiry', row.id] })
      }
      const ids = [...submitAttachments]
      for (const file of pendingAttachments) {
        if (!file.fileInstance) throw new Error('附件内容已失效，请重新选择')
        const uploaded = await uploadFile(file.fileInstance, { businessType: 'inquiry', businessId: row.id, category: 'oa_attachment' }).catch((error: Error) => {
          throw new Error(`草稿已保存，附件「${file.name}」上传失败：${error.message}。未发起审批，请在当前窗口重试。`)
        })
        ids.push(uploaded.id)
        setSubmitAttachments([...ids])
        setPendingAttachments((current) => current.filter((item) => item.uid !== file.uid))
        void queryClient.invalidateQueries({ queryKey: ['business-files', 'inquiry', row.id] })
      }
      await updateCustomInquiry(row.id, { oa_draft: { ...draft, attachment_file_ids: ids } })
      if (saveIntent.current) {
        const result = await startInquiryApproval(row.id, false, draft.quote_owner_user_id || undefined, submitFields, ids)
        if (result.status !== 'pending' || !result.instance_id) {
          setApprovalTarget(row)
          throw new Error(`询价已保存；同序 OA 状态：${result.status_label}。请查看审批记录，结果不明时不要重复提交。`)
        }
        Toast.success('已提交到同序 OA，生产部将在同序 OA 处理')
        setApprovalTarget(row)
      }
      return row
    },
    onSuccess: () => {
      const wasSubmit = saveIntent.current
      setSaveIntentState(false)
      if (!wasSubmit) Toast.success('草稿已保存，尚未发送同序 OA 审批')
      setEditVisible(false)
      setEditing(null)
      savedInquiry.current = null
      setPendingPhotos([])
      setPendingAttachments([])
      setForm(EMPTY_FORM)
      refresh()
    },
    onError: (error: Error) => { setSaveIntentState(false); Toast.error(error.message) },
  })

  const reviseMutation = useMutation({
    mutationFn: () => {
      const quantity = optionalQuantityText(reviseForm.quantity)
      const targetPrice = parseOptionalDecimal(reviseForm.target_price)
      if (Number.isNaN(targetPrice)) throw new Error('目标价只能填数字')
      return reviseCustomInquiry(reviseTarget!.id, {
        revision_note: reviseForm.revision_note || null,
        description: reviseForm.description || null,
        quantity,
        target_price: targetPrice,
      })
    },
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
      quantity: String(row.quantity_exact ?? row.quantity ?? ''),
      target_price: String(row.target_price_exact ?? row.target_price ?? ''),
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
    savedInquiry.current = null
    setPendingPhotos([])
    setPendingAttachments([])
    setSubmitFields({})
    setSubmitQuoteOwner(undefined)
    setSubmitAttachments([])
    setEditing(null)
    setForm(opportunityFilter && linkedOpportunity.data ? {
      ...EMPTY_FORM, customer_id: linkedOpportunity.data.customer_id, opportunity_id: opportunityFilter,
    } : EMPTY_FORM)
    setEditVisible(true)
  }

  const openEdit = (row: CustomInquiryRow) => {
    savedInquiry.current = null
    setPendingPhotos([])
    setPendingAttachments([])
    setSubmitFields(row.extra?.oa_draft?.fields ?? {})
    setSubmitQuoteOwner(row.extra?.oa_draft?.quote_owner_user_id ?? undefined)
    setSubmitAttachments(row.extra?.oa_draft?.attachment_file_ids ?? [])
    setEditing(row)
    setForm({
      title: row.title,
      description: row.description ?? '',
      customer_id: row.customer_id ?? undefined,
      opportunity_id: row.opportunity_id ?? undefined,
      quantity: String(row.quantity_exact ?? row.quantity ?? ''),
      target_price: String(row.target_price_exact ?? row.target_price ?? ''),
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

  const submissionOptions = useQuery({
    queryKey: ['inquiry-oa-submission', submitTarget?.id ?? editing?.id ?? 'new'],
    queryFn: () => getInquirySubmissionOptions(submitTarget?.id ?? editing?.id),
    enabled: true,
  })
  const requestedQuoteOwner = submitQuoteOwner ?? submissionOptions.data?.selected_quote_owner
  const selectedAllowedQuoteOwner = submissionOptions.data?.quote_owners?.some((person) => person.user_id === requestedQuoteOwner)
    ? requestedQuoteOwner ?? undefined
    : undefined
  // OA 模板里 CRM 本来就有的资料（客户名称/联系人/电话、产品名称或编号、备注）直接带入，
  // 只补空白格：已保存的草稿和用户手填的值优先，不被覆盖。
  useEffect(() => {
    const prefill = submissionOptions.data?.prefill_fields
    if (!prefill) return
    setSubmitFields((current) => {
      const missing = Object.entries(prefill).filter(([key, value]) => value && !current[key]?.trim())
      return missing.length ? { ...current, ...Object.fromEntries(missing) } : current
    })
  }, [submissionOptions.data, submitTarget?.id, editing?.id])
  const submissionFiles = useQuery({
    queryKey: ['business-files', submissionOptions.data?.file_business_type || 'inquiry', submitTarget?.id ?? editing?.id],
    queryFn: () => listBusinessFiles(submissionOptions.data?.file_business_type || 'inquiry', (submitTarget?.id ?? editing?.id)!),
    enabled: Boolean(submitTarget || (editVisible && editing)) && can('file:view'),
  })
  const submitMutation = useMutation({
    mutationFn: ({ inquiryId, quoteOwner, resubmit, fields }: { inquiryId: number; quoteOwner?: string; resubmit: boolean; fields: Record<string, string> }) =>
      startInquiryApproval(inquiryId, resubmit, quoteOwner, fields, submitAttachments),
    onSuccess: (result) => {
      if (result.status === 'pending' && result.instance_id) {
        Toast.success('询价已提交到同序 OA，后续由生产部在同序 OA 处理')
      } else if (['needs_review', 'submitting'].includes(result.status)) {
        Toast.warning('提交结果尚未确定，请查看审批记录并核对同序 OA，不要重复提交')
      } else if (['skipped', 'not_sent', 'failed'].includes(result.status)) {
        Toast.warning(`尚未提交成功：${result.error || result.status_label}`)
      } else Toast.info(`当前同序 OA 审批状态：${result.status_label}，请查看审批记录`)
      setApprovalTarget(submitTarget)
      setSubmitTarget(null)
      void queryClient.invalidateQueries({ queryKey: ['inquiry-approvals'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const withdrawMutation = useMutation({
    mutationFn: (oaId: number) => withdrawOaInstance(oaId),
    onSuccess: () => {
      Toast.success('已撤回同序 OA 审批')
      void queryClient.invalidateQueries({ queryKey: ['inquiry-approvals'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const startApproval = (row: CustomInquiryRow, resubmit = false) => {
    setSubmitResubmit(resubmit)
    setSubmitFields(row.extra?.oa_draft?.fields ?? {})
    setSubmitAttachments(row.extra?.oa_draft?.attachment_file_ids ?? [])
    setSubmitQuoteOwner(row.extra?.oa_draft?.quote_owner_user_id ?? undefined)
    setSubmitTarget(row)
  }

  /** 场景09：定制件没有 SKU，报价中心选不到它，这里给一条"填两个数就成单"的出口。
   *  耗时埋点（场景18）的起点在"用户点开转报价那一刻"——服务端只看得到单据落库
   *  时间，那是流程跨度，不是他真正花的工夫。 */
  const openQuote = (row: CustomInquiryRow) => {
    if (row.linked_quotes?.length) {
      navigate(`/quotes/${row.linked_quotes[0].id}`)
      return
    }
    if (row.is_deleted) return
    if (row.is_superseded) {
      setHistoryTarget(row)
      return
    }
    if (!can('quote:manage')) return
    if (row.status === 'archived') {
      openRevise(row)
      return
    }
    quoteMutation.reset()
    setQuoteForm({
      unit_cost: '',
      quoted_price: '',
      quantity: String(row.quantity_exact ?? row.quantity ?? ''),
    })
    setQuoteTarget(row)
  }

  // 「关联商机」没选客户时是灰的，判据照旧（`disabled={!form.customer_id}`，没动）。
  // 灰控件点不动、又不说为什么，正是主人 2026-10-12 说的那类毛病，所以这条下面
  // 常驻一行灰字；灰着的时候点它还会弹同一句话（看得见 + 点得动，见 BlockedHint.tsx）。
  // ⚠️ 灰着的 Semi Select 要加 pointerEvents:'none'：点击否则落在它内部的 input 上，
  // 而被禁用的表单控件**根本不派发事件**，外层容器的 onClick 收不到。
  const notifyOpportunityBlocked = useBlockedNotify(
    form.customer_id ? [] : ['先选客户，才能关联该客户的商机'],
  )

  const columns = [
    {
      title: '产品名称',
      dataIndex: 'title',
      /* 237 → 225：给「客户 / 状态 / 记录人」各让出几个像素，
         八列合计压在 1130 以内，桌面版不再冒出横向滚动条。 */
      width: compact ? 156 : 240,
      // 这一列原来不设宽，标题一长就把整表顶到 1320、横滑才能看到操作列。
      // 定宽 320，标题正文超过 14 字截断（悬停看全），编号仍然完整显示 ——
      // 编号是报价 / 打样明细回溯用的，一个字都不能少。
      render: (text: string, record: CustomInquiryRow) => (
        <div>
          {/* 编号从第一行挪到第二行（和说明并列）：
              原来编号 + 标题挤在一行，13 位的等宽编号占掉 90px，
              标题只剩 10 来字就被截。现在标题独占一整行。 */}
          <div
            style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={text}
          >
            {text}
            {record.version && record.version > 1 && (
              <Tag size="small" style={{ marginLeft: 6 }}>{`v${record.version}`}</Tag>
            )}
          </div>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)', display: 'flex', gap: 8, marginTop: 2 }}>
            {record.inquiry_no && (
              <span style={{ fontFamily: 'monospace', flexShrink: 0 }}>{record.inquiry_no}</span>
            )}
            {record.revision_note && (
              <span style={{ color: 'var(--crm-warning)', flexShrink: 0 }}>
                本次修改：{record.revision_note}
              </span>
            )}
            {record.description && (
              <span
                title={record.description}
                style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}
              >
                {record.description}
              </span>
            )}
          </div>
        </div>
      ),
    },
    {
      title: '客户',
      dataIndex: 'customer_name',
      /* 190 → 202：客户名带「【全场景演示】」前缀时 190 差两三个字就被截，
         客户是这一页判断「给谁做的」，整列全是省略号等于没有客户列。
         窄屏同理：102 只剩「【全场景…」，实测六列合计 700、容器 740，
         还有 38px 余量，索性给到 140。 */
      width: compact ? 140 : 190,
      // 交给 Semi 按列宽省略（ellipsis）—— 不再按字数硬截。
      // 主人 2026-11-16：「还有这么多空间怎么还是省略呢」：宽屏下这一列
      // 会跟着拉宽，按字数截就永远只显示 13 个字。
      ellipsis: true,
      // 客户名带场景后缀能到 12 字（"宁波家电01厂【演示造数】"），
      // 这一列给不了 180 —— 整表要塞进 1130，一张卡片看全不许横向拖。
      // 超过 10 字截断，鼠标悬停看全称。
      render: (v: string | null) => v || '-',
    },
    ...(compact
      ? [{
          title: '数量 / 目标价',
          width: 74,
          className: 'cell-money',
          render: (_: unknown, record: CustomInquiryRow) => (
            <div style={{ display: 'grid', gap: 2, justifyItems: 'center' }}>
              <span>{record.quantity ?? '-'}</span>
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                {record.target_price != null ? `¥${record.target_price}` : '-'}
              </span>
            </div>
          ),
        }]
      : [
          {
            title: '数量',
            dataIndex: 'quantity',
            /* 60px 连「3,000」都放不下，被裁成「3,...」——六位数直接「500,0...」。
               96 才装得下「500,000」，八列合计 1184 仍在 1224 内容区内、不出横向滚动条。 */
            width: 96,
            render: (v: number | null) => (v == null ? '-' : v.toLocaleString()),
          },
          {
            title: '目标价',
            dataIndex: 'target_price',
            width: 84,
            className: 'cell-money',
            render: (v: number | null) => (v != null ? `¥${v}` : '-'),
          },
        ]),
    {
      title: '状态',
      dataIndex: 'status',
      /* 90 放不下「● 已转商机」四个字 + 圆点，一律截成「已转商机…」——
         状态这一列被截，等于看不出这条需求走到哪一步。104 刚好。 */
      width: compact ? 96 : 102,
      render: (v: string) => (
        <StatusDot
          label={STATUS_OPTIONS.find((o) => o.value === v)?.label ?? v}
          tone={(STATUS_TONE[v] ?? 'grey') as TagTone}
        />
      ),
    },
    {
      title: compact ? '记录人 / 时间' : '记录人',
      dataIndex: 'creator_name',
      /* 96 放在「系统管理员」五个字上差两三个像素，
         结果这一列永远是「系统管理…」。108 才装得下。 */
      width: compact ? 96 : 100,
      render: (v: string | null, record: CustomInquiryRow) =>
        compact ? (
          <div style={{ display: 'grid', gap: 2, justifyItems: 'center' }}>
            <span style={{ fontSize: 12 }}>{v ?? '-'}</span>
            <span style={{ fontSize: 11, color: 'var(--crm-text-3)' }}>
              {formatBeijingDate(record.created_at)}
            </span>
          </div>
        ) : (
          v ?? '-'
        ),
    },
    ...(compact
      ? []
      : [{
          title: '记录时间',
          dataIndex: 'created_at',
          /* 102 时日期被截成 "2026-11-…" —— 这一列等于白给。118 刚好。 */
          width: 110,
          // 列表里精确到秒没有意义，反而把这一列撑到 160。
          // 只给日期，具体时分秒在详情里看。
          render: (v: string) => formatBeijingDate(v),
        }]),
    {
      title: '操作',
      width: compact ? 176 : 220,
      render: (_: unknown, record: CustomInquiryRow) => {
        // 次要动作收进「更多」下拉。原来 9~11 个操作全铺在这一格里，容器是
        // inline-flex 且 flex-wrap: nowrap —— 挤不下时每个链接被压扁成
        // 17px 宽、100px 高（"申请打样"四个字竖着一字一行，2026-10-06 实测）。
        // 只把最常用的留在外面，其余进下拉；容器补上 flex-wrap 兜底。
        const moreActions: { key: string; label: string; onClick: () => void }[] = []
        if (can('order:manage')) {
          moreActions.push({ key: 'draft', label: '建订单草稿', onClick: () => setDraftSourceId(record.id) })
        }
        if (can('sample:manage')) {
          moreActions.push({ key: 'sample', label: '申请打样', onClick: () => setSampleSourceId(record.id) })
        }
        if (can('quote:manage')) moreActions.push({ key: 'photo', label: '上传照片', onClick: () => setPhotoTarget(record) })
    // 「上传照片」和「修订」原来铺在外面，六个动作一个格塞不下，换行成两排；
    // 它们都不是常用动作，收进「更多」，外边只留 编辑 / 转报价 / 删除。
        if (canManage) {
          moreActions.push({ key: 'revise', label: '修订', onClick: () => openRevise(record) })
          moreActions.push({ key: 'oa-start', label: '提交询价到同序 OA', onClick: () => startApproval(record) })
        }
        // 历次审批 + "结果不明"的人工处理入口（认领/重发/作废）
        moreActions.push({ key: 'oa-log', label: '审批记录', onClick: () => setApprovalTarget(record) })
        if ((record.version ?? 1) > 1) {
          moreActions.push({ key: 'history', label: '历史', onClick: () => setHistoryTarget(record) })
        }
        if (canManage && record.status !== 'developing' && record.status !== 'converted') {
          moreActions.push({ key: 'developing', label: '转开发中', onClick: () => changeStatus(record, 'developing') })
        }
        if (canManage && record.status !== 'archived' && !record.is_superseded) {
          moreActions.push({ key: 'archived', label: '归档', onClick: () => changeStatus(record, 'archived') })
        }
        // 菜单项会因为**权限**和**单据状态**默默消失（上面那几个 if）。用户明明记得
        // 这里有「申请打样」，换个人或换个状态就没了，界面上一个字都不说 —— 主人
        // 2026-10-12：「点又点不了，又不给提示为什么点不了，类似的情况都要修」。
        // 所以把"缺的这一项为什么缺"写进菜单末尾（灰色小字、不可点，见下面渲染）。
        // ⚠️ 只是解释：上面哪些项出现、哪些不出现的判据一个字没动。
        const statusLabel = STATUS_OPTIONS.find((item) => item.value === record.status)?.label ?? record.status
        const moreMissing: string[] = []
        if (!can('order:manage')) moreMissing.push('缺少「订单管理」权限，不能建订单草稿')
        if (!can('sample:manage')) moreMissing.push('缺少「打样管理」权限，不能申请打样')
        if (!((record.version ?? 1) > 1)) moreMissing.push('这一条还是第 1 版，修订过之后才会有「历史」')
        if (record.status === 'developing') moreMissing.push(`当前状态是「${statusLabel}」，不用再转开发中`)
        if (record.status === 'converted') moreMissing.push(`当前状态是「${statusLabel}」，不能转开发中`)
        if (record.status === 'archived') moreMissing.push(`当前状态是「${statusLabel}」，不能再归档`)
        if (record.source_insight_id != null && record.status !== 'archived') {
          moreMissing.push(record.is_superseded
            ? '这是新品洞察转出的原询价，请在最新版本归档'
            : '这是新品洞察转出的原询价，请用「归档」结束处理')
        }
  return (
          <span
            style={{
              display: 'inline-flex',
              gap: compact ? 6 : 10,
              flexWrap: 'wrap',
              alignItems: 'center',
            }}
          >
            {canManage && <button type="button" className="crm-text-action" onClick={() => openEdit(record)}>编辑</button>}
            {/* 定制件没有 SKU，报价中心选不到它——这里直接转报价（场景09） */}
            {(record.linked_quotes?.length || record.is_superseded || can('quote:manage')) ? (
              <button type="button" className="crm-text-action" onClick={() => openQuote(record)} title={record.status === 'archived' && !record.linked_quotes?.length ? '已归档，先修订为新版再报价' : '生成一份报价草稿，价格需按核价复核后才对客'}>
                {record.linked_quotes?.length ? '查看原报价'
                  : record.is_superseded ? '查看新版'
                  : record.status === 'archived' ? '修订后报价'
                  : '转报价'}
              </button>
            ) : null}
            {canManage && record.source_insight_id == null && <Popconfirm title="删除这条定制询价？" onConfirm={() => deleteMutation.mutate(record.id)}>
              <button type="button" className="crm-text-action" style={{ color: 'var(--crm-error)' }}>删除</button>
            </Popconfirm>}
            <Dropdown
              trigger="click"
              position="bottomRight"
              visible={moreTargetId === record.id}
              onVisibleChange={(visible) => setMoreTargetId(visible ? record.id : null)}
              render={
                <Dropdown.Menu>
                  {moreActions.map((action) => (
                    <Dropdown.Item key={action.key} onClick={() => { setMoreTargetId(null); action.onClick() }}>
                      {action.label}
                    </Dropdown.Item>
                  ))}
                  {/* 缺项说明：故意**不套 Dropdown.Item、也不给 onClick** —— 它不是
                      一个能按的动作，只是一段灰字，免得用户点了以为要发生什么。
                      菜单里没有缺项时整段不出现。 */}
                  {moreMissing.length > 0 && (
                    <li
                      style={{
                        listStyle: 'none',
                        maxWidth: 240,
                        marginTop: 4,
                        padding: '6px 12px 4px',
                        borderTop: '1px solid var(--crm-outline)',
                        fontSize: 12,
                        lineHeight: 1.7,
                        whiteSpace: 'normal',
                        color: 'var(--crm-text-3)',
                      }}
                    >
                      {moreMissing.map((text) => (
                        <div key={text}>{text}</div>
                      ))}
                    </li>
                  )}
                </Dropdown.Menu>
              }
            >
              <button type="button" className="crm-text-action" title="更多操作">更多</button>
            </Dropdown>
          </span>
        )
      },
    },
  ]

  const nativeFields = submissionOptions.data?.template_fields
  const renderSubmissionFields = (target: CustomInquiryRow | null, only?: string) => <>
    {!only && submissionOptions.isPending && <div>正在读取同序 OA 询价表单……保存草稿不受影响。</div>}
    {!only && submissionOptions.isError && <div style={{ color: 'var(--crm-error)' }}>同序 OA 表单读取失败，仍可保存草稿；请稍后重试提交。</div>}
    {!only && submissionOptions.data?.message && <div style={{ color: 'var(--crm-text-3)' }}>{submissionOptions.data.message}</div>}
    {(!only || only === 'quoter') && submissionOptions.data?.quote_owner_required && <div>
      <FormLabel required>对接报价员</FormLabel>
      <Select style={{ width: '100%' }} placeholder="选择同序 OA 对接报价员"
        disabled={submitMutation.isPending || saveMutation.isPending}
        value={selectedAllowedQuoteOwner}
        onChange={(value) => setSubmitQuoteOwner(value as string)}
        optionList={submissionOptions.data.quote_owners.map((item) => ({ value: item.user_id, label: item.name }))}
      />
    </div>}
    {submissionOptions.data?.fields.filter((field) => only
      ? field.id === only
      : !editVisible || field.id !== 'customer_contact').map((field) => <div key={field.id}>
      <FormLabel required={field.required}>
        {field.id === 'customer_contact' ? '同序审批用的客户名称、联系人、联系方式' : field.label}
      </FormLabel>
      {field.type === 'DDAttachment' ? (
        <section style={{ display: 'grid', gap: 18, padding: 18, border: '1px solid var(--crm-border)', borderRadius: 10, background: 'var(--crm-surface-low)', minWidth: 0 }}>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12, lineHeight: 1.7 }}>
            图纸、PDF、表格等补充资料 · 单个文件不超过 20 MB<br />
            与上方产品参考照片分开提交，最多 10 个附件。
          </div>
          {editVisible ? <div>
            <Upload action="" uploadTrigger="custom" multiple fileList={pendingAttachments}
              disabled={saveMutation.isPending || !can('file:manage')} maxSize={20 * 1024}
              onSizeError={() => Toast.warning('单个附件不能超过20 MB')}
              onChange={({ fileList }) => setPendingAttachments(fileList)}>
              <Button disabled={saveMutation.isPending || !can('file:manage')}>＋ 选择附件</Button>
            </Upload>
            <div style={{ marginTop: 8, fontSize: 12, color: 'var(--crm-text-3)', lineHeight: 1.7 }}>
              {can('file:manage') ? '所选文件会在保存草稿或提交时上传，并随本次申请提交。' : '暂无上传权限，请联系管理员开通文件管理权限。'}
            </div>
          </div> : target && <AttachmentPanel businessType={submissionOptions.data!.file_business_type} businessId={target.id}
            category="oa_attachment" onlyCategory="oa_attachment" writePermission="quote:manage"
            compact hint="先上传文件，再选择本次随申请提交的附件。" />}
          {editVisible && target && (submissionFiles.data ?? []).some(file => file.category === 'oa_attachment') && <details>
            <summary style={{ cursor: 'pointer', color: 'var(--crm-primary)', fontSize: 13 }}>查看或管理已上传文件</summary>
            <div style={{ marginTop: 12 }}><AttachmentPanel businessType={submissionOptions.data!.file_business_type} businessId={target.id}
              category="oa_attachment" onlyCategory="oa_attachment" writePermission="quote:manage" compact showUpload={false} /></div>
          </details>}
          {(submissionFiles.data ?? []).some(file => file.category === 'oa_attachment') && <div style={{ borderTop: '1px solid var(--crm-border)', paddingTop: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 9, fontSize: 13 }}>
              <span>本次随申请提交</span><span style={{ color: 'var(--crm-text-3)' }}>已选 {submitAttachments.length} / 10</span>
            </div>
            <Select multiple style={{ width: '100%' }} placeholder="选择已上传的附件" maxTagCount={2}
              value={submitAttachments} disabled={submitMutation.isPending || saveMutation.isPending}
              optionList={(submissionFiles.data ?? []).filter(file => file.category === 'oa_attachment').map(file => ({ value: file.id, label: file.file_name }))}
              onChange={value => {
                const ids = value as number[]
                if (ids.length > 10) { Toast.warning('单次最多提交10个附件'); return }
                setSubmitAttachments(ids)
              }} />
          </div>}
          {submissionFiles.isError && <div style={{ color: 'var(--crm-error)', fontSize: 12 }}>附件列表读取失败，请重试。</div>}
        </section>
      ) : field.type === 'DDSelectField' ? (
        <Select style={{ width: '100%' }} placeholder="请选择" disabled={submitMutation.isPending || saveMutation.isPending}
          value={submitFields[field.id] || undefined}
          optionList={field.options.map((value) => ({ value, label: value }))}
          onChange={(value) => setSubmitFields((current) => ({ ...current, [field.id]: value as string }))} />
      ) : field.type === 'DDDateField' ? (
        <DatePicker type={nativeFields ? "date" : "dateTime"} format={nativeFields ? "yyyy-MM-dd" : "yyyy-MM-dd HH:mm"} style={{ width: '100%' }}
          disabled={submitMutation.isPending || saveMutation.isPending} placeholder="请选择期望回复时间" showClear
          value={submitFields[field.id] || undefined}
          onChange={(_, value) => setSubmitFields((current) => ({ ...current, [field.id]: (value as string) || '' }))} />
      ) : nativeFields && !field.multiline ? (
        <Input value={submitFields[field.id] || ''} disabled={submitMutation.isPending || saveMutation.isPending}
          placeholder={field.hint} onChange={(value) => setSubmitFields((current) => ({ ...current, [field.id]: value }))} />
      ) : (
        <TextArea value={submitFields[field.id] || ''} rows={2} disabled={submitMutation.isPending || saveMutation.isPending}
          placeholder={field.hint || `填写${field.label}`}
          onChange={(value) => setSubmitFields((current) => ({ ...current, [field.id]: value }))} />
      )}
      {field.help_text && <small>{field.help_text}</small>}
      {field.id === 'customer_contact' && (
        <div style={{ marginTop: 4, fontSize: 12, color: 'var(--crm-text-3)' }}>
          这里填写的真实名称只用于本次审批。询价仍关联到所选的 CRM 客户。
          要修改档案名称，请到客户中心打开该客户，点「编辑客户」。
        </div>
      )}
    </div>)}
  </>

  const renderEditPhotos = (label = '产品参考图片', required = true, help?: string, maxFiles?: number) => (
    <div>
      <FormLabel required={required}>{label}</FormLabel>
      {help && <small>{help}</small>}
      {(!editing || pendingPhotos.length > 0) && <>
        <Upload action="" uploadTrigger="custom" multiple accept="image/jpeg,image/png"
          maxSize={20 * 1024} onSizeError={() => Toast.warning('单张照片不能超过 20 MB')}
          fileList={pendingPhotos} disabled={saveMutation.isPending || !can('file:manage') || !can('quote:manage')}
          onChange={({ fileList }) => {
          if (maxFiles && fileList.length > maxFiles) {
            Toast.warning(`最多选择 ${maxFiles} 张产品参考图片`)
            return
          }
          setPendingPhotos(fileList)
        }}
          onAcceptInvalid={() => Toast.warning('请选择 JPG 或 PNG 图片')}
          prompt="先选择照片；点「保存草稿」仅保存，点「提交到同序 OA」才会发出审批。">
          <Button theme="solid" disabled={saveMutation.isPending || !can('file:manage') || !can('quote:manage')}>选择照片（可多选）</Button>
        </Upload>
        {(!can('file:manage') || !can('quote:manage')) && <div>上传照片需要文件管理和报价管理权限，请联系管理员。</div>}
      </>}
      {editing && (
        <AttachmentPanel
          businessType="inquiry"
          businessId={editing.id}
          writePermission="quote:manage"
          excludeCategory="oa_attachment"
          uploadLabel="上传照片" maxFiles={maxFiles}
          hint="放产品照片、图纸或参考截图，单张不超过 20 MB（JPG / PNG）"
          accept="image/jpeg,image/png"
        />
      )}
    </div>
  )

  const renderNativeFields = (target: CustomInquiryRow | null, editable: boolean) => nativeFields?.map(field => {
    const key = field.id
    const label = <FormLabel required={field.required}>{field.label}</FormLabel>
    const aliases: Record<string, string> = {
      customer: 'customer_contact', specification: 'product_model', delivery_time: 'delivery_date', notes: 'special_note',
    }
    switch (key) {
      case 'customer':
        return editable ? null : <div key={key}>{renderSubmissionFields(target, 'customer_contact')}</div>
      case 'instructions':
        return <div key={key}>{label}<div style={{ color: 'var(--crm-text-3)', whiteSpace: 'pre-line' }}>{field.hint}</div></div>
      case 'reporter':
      case 'report_department': {
        const identity = submissionOptions.data?.prefill
        return <div key={key}>{label}<Input readOnly value={(key === 'reporter' ? identity?.applicant_name : identity?.department) ?? ''}/></div>
      }
      case 'product_name':
      case 'quantity':
      case 'requirements': {
        const index = key === 'product_name' ? 'title' : key === 'quantity' ? 'quantity' : 'description'
        const value = String((editable ? form[index] : target?.[index]) ?? '')
        const change = (next: string) => setForm(current => ({ ...current, [index]: next }))
        return <div key={key}>{label}{index === 'description'
          ? <TextArea rows={3} value={value} readOnly={!editable} placeholder={field.hint} onChange={change} />
          : <Input value={value} readOnly={!editable} onChange={change} />}</div>
      }
      case 'reference_images':
        return <div key={key}>{editable
          ? renderEditPhotos(field.label, Boolean(field.required), field.help_text, field.max_files)
          : target && <>{label}<AttachmentPanel businessType="inquiry" businessId={target.id}
              writePermission="quote:manage" excludeCategory="oa_attachment" uploadLabel="上传照片"
              accept="image/jpeg,image/png" maxFiles={field.max_files} hint={field.help_text} compact /></>}</div>
      default:
        return <div key={key}>{renderSubmissionFields(target, aliases[key] ?? key)}</div>
    }
  })

  const saveForm = (submit: boolean) => {
    if (saveMutation.isPending) return
    if (!form.title.trim()) { Toast.warning('产品名称必填'); return }
    if (submitAttachments.length + pendingAttachments.length > 10) { Toast.warning('单次最多选择10个附件，请移除多余文件'); return }
    if (submit) {
      const options = submissionOptions.data
      if (!options?.configured || !options.enabled) { Toast.warning('询价审批暂未启用，可先保存需求和内部报价草稿；正式对外报价仍需通过核价及报价复核'); return }
      if (options.quote_owner_required && !selectedAllowedQuoteOwner) { Toast.warning('请选择对接报价员'); return }
      const missing = options.fields.find((field) => field.required && (field.type === 'DDAttachment'
        ? !submitAttachments.length && !pendingAttachments.length : !submitFields[field.id]?.trim()))
      if (missing) { Toast.warning(`请填写${missing.label}`); return }
      if (!form.description.trim() || !form.quantity.trim()) { Toast.warning('提交同序 OA 时还需要填写材质及工艺要求、预估订购数量'); return }
      if (options.image_required && !pendingPhotos.length && !(submissionFiles.data ?? []).some((file) => file.category !== 'oa_attachment' && file.mime_type?.startsWith('image/'))) {
        Toast.warning('提交前请选择产品参考照片'); return
      }
    }
    const quantity = parseOptionalDecimal(form.quantity)
    const targetPrice = parseOptionalDecimal(form.target_price)
    if (Number.isNaN(quantity) || Number.isNaN(targetPrice)) {
      Toast.warning(Number.isNaN(quantity) ? '数量只能填数字' : '目标价只能填数字')
      return
    }
    saveIntent.current = submit
    setSaveIntentState(submit)
    saveMutation.mutate({ title: form.title.trim(), description: form.description.trim() || null,
      customer_id: form.customer_id ?? null, opportunity_id: form.opportunity_id ?? null,
      quantity, target_price: targetPrice,
      remark: form.remark.trim() || null, status: editing ? (form.status ?? editing.status) : undefined })
  }

  return (
    <div className="page-container">
      <PageHeader
        title="定制需求"
        subtitle="客户想做的规格在现有产品里找不到时，可以先在这里记录需求，再准备询价和报价。"
      />

      {/* 审批暂停要**在页面上先说**，不能等点了提交失败才弹一句 toast。
          两种情况分开写：
          · 明确暂停（configured/enabled 由后端下发）→ 说清"能做什么、不能做什么"；
          · 接口取不到 → 只说"不确定"，不擅自判定暂停，也不挡草稿保存。 */}
      {submissionOptions.isError && (
        <div className="inline-note" role="status">
          暂时无法确认询价审批是否可用；保存需求和内部报价草稿不受影响，请稍后重试。
        </div>
      )}
      {submissionOptions.data && !submissionOptions.data.configured && (
        <div className="inline-note" role="status">
          {submissionOptions.data.message || '同序 OA 询价模板尚未配置，可先保存需求和内部报价草稿'}；正式对外报价仍需通过核价及报价复核。
        </div>
      )}
      {submissionOptions.data && submissionOptions.data.configured && !submissionOptions.data.enabled && (
        <div className="inline-note" role="status">
          {submissionOptions.data.message || '询价审批暂未启用，可先保存需求和内部报价草稿'}；正式对外报价仍需通过核价及报价复核。
        </div>
      )}

      <SectionCard>
        {inquiryFilter && <div style={{ marginBottom: 12 }}>
          当前查看新品洞察转出的询价及其修订版。
          <Button theme="borderless" onClick={() => resetInquiryFilters(true)}>查看全部询价</Button>
        </div>}
        {opportunityFilter && <div style={{ marginBottom: 12 }}>
          当前商机：{linkedOpportunity.data?.title ?? linkedOpportunity.error?.message ?? `商机编号 ${opportunityFilter}`} ·
          <Button theme="borderless" onClick={() => resetInquiryFilters(true)}>查看全部询价</Button>
          <Link to={`/opportunities/${opportunityFilter}`}>返回商机</Link>
        </div>}
        <div className="toolbar">
          {summaryQuery.data?.map((item) => (
            <Tag key={item.status} color={STATUS_TONE[item.status] ?? 'grey'} type="light">
              {item.label} {item.count}
            </Tag>
          ))}
          <div style={{ flex: 1 }} />
          <Input
            placeholder="输入需求名称或编号"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={searchInquiries}
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
          <Button theme="borderless" onClick={searchInquiries}>搜索</Button>
          {canManage && <Button theme="solid" onClick={openCreate}>
            记录定制询价
          </Button>}
        </div>

        <Table<CustomInquiryRow>
          className="inquiries-table"
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          size="middle"
          empty={emptyText(query,
            keyword || statusFilter ? '没有符合筛选条件的定制询价'
              : inquiryFilter ? '找不到这条询价，可能已删除或没有查看权限'
                : opportunityFilter ? '这笔商机还没有定制询价' : '还没有定制询价记录',
            keyword || statusFilter ? undefined : canManage && !inquiryFilter ? '可以先记录客户要的规格和数量。' : undefined,
            keyword || statusFilter ? <Button size="small" onClick={() => resetInquiryFilters()}>清除筛选</Button>
              : canManage && !inquiryFilter ? <Button size="small" onClick={openCreate}>记录定制询价</Button> : undefined,
          )}
          pagination={((query.data?.total ?? 0) <= pageSize) ? false : {
            currentPage: page,
            pageSize,
            total: query.data?.total ?? 0,
            onPageChange: (nextPage: number) => setPage(nextPage),
            showSizeChanger: true,
            pageSizeOpts: [10, 20, 50, 100],
            onPageSizeChange: (size: number) => { setPageSize(size); setPage(1) },
          }}
        />
      </SectionCard>

      {draftSourceId != null && <SampleFromSourceModal mode="order" source={{ inquiry_id: draftSourceId }} onClose={() => setDraftSourceId(null)} />}
      {sampleSourceId != null && <SampleFromSourceModal source={{ inquiry_id: sampleSourceId }} onClose={() => setSampleSourceId(null)} />}
      <Modal title="询价照片与附件" visible={Boolean(photoTarget)} width={720}
        bodyStyle={{ maxHeight: '65vh', overflowY: 'auto' }}
        onCancel={() => setPhotoTarget(null)} footer={<Button onClick={() => setPhotoTarget(null)}>完成</Button>}>
        <div style={{ marginBottom: 12 }}>{photoTarget?.title}：上传的照片会用于这条询价的同序 OA 审批。</div>
        {photoTarget && <AttachmentPanel businessType="inquiry" businessId={photoTarget.id} writePermission="quote:manage" excludeCategory="oa_attachment" uploadLabel="上传照片" accept="image/jpeg,image/png,image/gif,image/webp,image/bmp" />}
      </Modal>
      <Modal
        title="提交询价到同序 OA"
        visible={Boolean(submitTarget)}
        width={720}
        bodyStyle={{ maxHeight: '65vh', overflowY: 'auto' }}
        onCancel={() => { if (!submitMutation.isPending) setSubmitTarget(null) }}
        okText="确认提交到同序 OA"
        confirmLoading={submitMutation.isPending}
        okButtonProps={{ disabled: submissionOptions.isPending || submissionOptions.isError || !submissionOptions.data?.configured || !submissionOptions.data?.enabled }}
        onOk={() => {
          if (!submitTarget || !submissionOptions.data) return
          const quoteOwner = selectedAllowedQuoteOwner
          if (submissionOptions.data.quote_owner_required && !quoteOwner) { Toast.warning('请选择对接报价员'); return }
          const missing = submissionOptions.data.fields.find((field) => field.required && (field.type === 'DDAttachment' ? !submitAttachments.length : !submitFields[field.id]?.trim()))
          if (missing) { Toast.warning(`请填写${missing.label}`); return }
          submitMutation.mutate({ inquiryId: submitTarget.id, quoteOwner, resubmit: submitResubmit, fields: submitFields })
        }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 12 }}>
          {submitTarget && <div className="inline-note" style={{ margin: 0, overflowWrap: 'anywhere' }}>
            {submitTarget.customer_id
              ? <>CRM 关联客户 <strong>{submitTarget.customer_name || '客户名称暂不可显示'}</strong></>
              : '尚未关联 CRM 客户。审批只挂在这条询价上，填写同序表单里的客户名称不会自动关联 CRM 客户档案。'}
          </div>}
          {nativeFields && submissionOptions.data?.message && (
            <div className="inline-note" role="status">{submissionOptions.data.message}</div>
          )}
          {nativeFields ? renderNativeFields(submitTarget, false) : <>
            <ApplicantIdentity prefill={submissionOptions.data?.prefill} />
            <div>提交后按同序 OA 模板流转，可在 CRM 查看审批进度。</div>
            <div><strong>{submitTarget?.title}</strong>（第 {submitTarget?.version ?? 1} 版）</div>
            <div style={{ whiteSpace: 'pre-wrap' }}>{submitTarget?.description || '尚未填写需求描述'}</div>
            <div>数量：{submitTarget?.quantity ?? '未填写'}</div>
            {submissionOptions.isError && <div style={{ color: 'var(--crm-error)' }}>{submissionOptions.error.message}</div>}
            {submissionOptions.data && !submissionOptions.data.configured && <div>尚未配置同序 OA 询价模板，请联系管理员。</div>}
            {submissionOptions.data && !submissionOptions.data.enabled && <div>同序 OA 询价提交暂未开启，请联系管理员。</div>}
            {submitTarget && submissionOptions.data && <div>
              <FormLabel required={submissionOptions.data.image_required}>产品参考图片</FormLabel>
              <div style={{ marginBottom: 8, color: 'var(--crm-text-3)', fontSize: 12 }}>上传真实产品参考图片后再提交；审批结果可在「审批记录」查看，可点击「立即同步」查询最新结果。</div>
              <AttachmentPanel businessType={submissionOptions.data.file_business_type} businessId={submitTarget.id}
                writePermission="quote:manage" excludeCategory="oa_attachment" uploadLabel="上传照片" accept="image/jpeg,image/png,image/gif,image/webp,image/bmp" />
            </div>}
            {renderSubmissionFields(submitTarget)}
          </>}

        </div>
      </Modal>

      <Modal
        title={editing ? '编辑定制询价' : '记录定制询价'}
        visible={editVisible}
        onCancel={() => { if (!saveMutation.isPending) setEditVisible(false) }}
        cancelButtonProps={{ disabled: saveMutation.isPending }}
        footer={<div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <Button disabled={saveMutation.isPending} onClick={() => setEditVisible(false)}>取消</Button>
          <Button loading={saveMutation.isPending && !saveIntentState} disabled={saveMutation.isPending} onClick={() => saveForm(false)}>保存草稿</Button>
          <Button theme="solid" loading={saveMutation.isPending && saveIntentState}
            disabled={saveMutation.isPending || submissionOptions.isPending || submissionOptions.isError} onClick={() => saveForm(true)}>提交到同序 OA</Button>
        </div>}
        width={760}
        bodyStyle={{ maxHeight: '65vh', overflowY: 'auto' }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 12 }}>
          {nativeFields && submissionOptions.data?.message && (
            <div className="inline-note" role="status">{submissionOptions.data.message}</div>
          )}
          {nativeFields ? renderNativeFields(editing, true) : <>
            <ApplicantIdentity prefill={submissionOptions.data?.prefill} />
            {renderEditPhotos()}
            <div>
              <FormLabel required>产品名称</FormLabel>
              <Input
                value={form.title}
                onChange={(value) => setForm({ ...form, title: value })}
              />
            </div>
            <div>
              <FormLabel required>材质及工艺要求</FormLabel>
              <TextArea
                value={form.description}
                onChange={(value) => setForm({ ...form, description: value })}
                rows={3}
                placeholder="材质、工艺、尺寸、使用场景……"
              />
            </div>
          </>}
          {nativeFields && <div style={{ borderTop: '1px solid var(--crm-border)', paddingTop: 12, fontWeight: 600 }}>CRM 关联资料</div>}
          <div>
            <div style={{ marginBottom: 4 }}>CRM 关联客户（可选）</div>
            <RecordSearchSelect
              key={`inquiry-customer-${editVisible}`}
              scope="inquiry-customer"
              enabled={editVisible}
              placeholder="按客户名称或编号搜索"
              value={form.customer_id ?? undefined}
              fallbackLabel={selectedOpportunity.data?.customer_name ?? undefined}
              list={listCustomers}
              lookup={getCustomer}
              labelOf={(customer) => customer.name}
              onChange={(customer) => {
                setForm({ ...form, customer_id: customer?.id ?? null, opportunity_id: null })
                // 同序模板要「客户名称、联系人、联系方式」；选了客户先把名称带进去，
                // 联系人和电话仍可在同序表单里补。老钉钉控件 id 不是这个键，不做。
                if (nativeFields && customer?.name) {
                  setSubmitFields((current) => current.customer_contact?.trim()
                    ? current : { ...current, customer_contact: customer.name })
                }
              }}
              style={{ width: '100%' }}
            />
            {!form.customer_id && !form.opportunity_id && (
              <div className="inline-note" style={{ margin: '8px 0 0' }}>
                可以先送审。审批只挂在这条询价上，填写同序表单里的客户名称不会自动关联 CRM 客户档案。
              </div>
            )}
          </div>
          {renderSubmissionFields(editing, 'customer_contact')}
          <div>
            <div style={{ marginBottom: 4 }}>关联商机（本次采购需求）</div>
            {/* 客户没选时这个框是灰的（判据没动）。灰着的时候外面包一层接点击：
                点它就弹一句「先选客户，才能关联该客户的商机」，而不是毫无反应。 */}
            <div className="blocked-hint-click" onClick={notifyOpportunityBlocked} style={{ display: 'block' }}>
              <RecordSearchSelect
                key={`inquiry-opportunity-${form.customer_id ?? 'none'}`}
                scope={`inquiry-opportunity-${form.customer_id ?? 'none'}`}
                enabled={editVisible && Boolean(form.customer_id)}
                disabled={!form.customer_id}
                placeholder="按商机名称搜索，避免重复创建"
                value={form.opportunity_id ?? undefined}
                fallbackLabel={selectedOpportunity.data?.title}
                // 只列「进行中」的商机（已失单/已成交的不该再作为本次采购需求的关联对象）。
                list={(input) => listOpportunities({
                  ...input, customer_id: form.customer_id ?? undefined, status: 'open',
                })}
                lookup={async (id) => {
                  const row = await getOpportunity(id)
                  return row.status === 'open' && row.customer_id === form.customer_id ? row : undefined
                }}
                labelOf={(item) => item.title}
                onChange={(item) => setForm({ ...form, opportunity_id: item?.id ?? null })}
                style={{ width: '100%', ...(form.customer_id ? {} : { pointerEvents: 'none' }) }}
              />
            </div>
            {/* 常驻灰字：为什么灰、下一步先做哪一步。上面的客户框就在正上方，
                所以只说"先选客户"，不重复怎么选。 */}
            {!form.customer_id && (
              <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.7, color: 'var(--crm-text-3)' }}>
                先选客户，才能关联该客户的商机
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            {!nativeFields && <div style={{ flex: 1 }}>
              <FormLabel required>预估订购数量</FormLabel>
              <Input value={form.quantity} onChange={(value) => setForm({ ...form, quantity: value })} />
            </div>}
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户目标价（元/件）</div>
              <Input
                value={form.target_price}
                onChange={(value) => setForm({ ...form, target_price: value })}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注</div>
            <Input value={form.remark} onChange={(value) => setForm({ ...form, remark: value })} />
          </div>
          {!nativeFields && renderSubmissionFields(editing)}
        </div>
      </Modal>

      <Modal
        width={520}
        title={`修订：${reviseTarget?.title ?? ''}`}
        visible={Boolean(reviseTarget)}
        onCancel={() => setReviseTarget(null)}
        onOk={() => reviseMutation.mutate()}
        confirmLoading={reviseMutation.isPending}
        okText="生成新版"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 12 }}>
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
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))', gap: 10 }}>
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
            { title: '目标价', dataIndex: 'target_price', width: 90, className: 'cell-money', render: (v: number | null) => v ?? '-' },
            { title: '本版说明', dataIndex: 'revision_note', render: (v: string | null) => v ?? '（原始要求）' },
            { title: '操作', render: (_: unknown, row: CustomInquiryRow) => (
              <div>
                {!row.is_deleted && (row.linked_quotes?.length || (can('quote:manage') && !row.is_superseded)) ? (
                  <button type="button" className="crm-text-action" style={{ display: 'block' }} onClick={() => { setHistoryTarget(null); openQuote(row) }}>
                    {row.linked_quotes?.length ? '查看原报价' : row.status === 'archived' ? '修订后报价' : '按此版本生成报价草稿'}
                  </button>
                ) : null}
                {can('sample:manage') && <button type="button" className="crm-text-action" onClick={() => { setHistoryTarget(null); setSampleSourceId(row.id) }}>按此版本申请打样</button>}
                {can('order:manage') && <button type="button" className="crm-text-action" style={{ display: 'block' }} onClick={() => { setHistoryTarget(null); setDraftSourceId(row.id) }}>按此版本建订单草稿</button>}
              </div>
            ) },
            {
              title: '提交时间',
              dataIndex: 'created_at',
              width: 150,
              render: (v: string) => formatBeijingTime(v),
            },
          ]}
          dataSource={historyQuery.data ?? []}
          loading={historyQuery.isLoading}
          rowKey="id"
          pagination={false}
        />
      </Modal>

      <Modal
        title={`审批记录：${approvalTarget?.title ?? ''}`}
        visible={Boolean(approvalTarget)}
        onCancel={() => setApprovalTarget(null)}
        footer={null}
        width={1000}
        bodyStyle={{ maxHeight: '70vh', overflowY: 'auto' }}
      >
        {approvalTarget && <AutoOaSync enabled={can('quote:manage')} row={approvalRows[0]} invalidateKeys={[
          ['inquiry-approvals', approvalTarget.id], ['custom-inquiries'], ['inquiry-history', approvalTarget.id],
        ]} />}
        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 10 }}>
          审批进度会自动更新，也可点击「立即同步」查询。
          展开记录可查看节点、处理意见和时间。结果未知时先核对审批系统，避免重复发起。
        </div>

        <Table<OaApprovalInstance>
          columns={[
            { title: '轮次', dataIndex: 'submit_round', width: 60, render: (v: number) => v ?? 1 },
            {
              title: '状态',
              dataIndex: 'status_label',
              width: 130,
              render: (v: string, row: OaApprovalInstance) => (
                <div>
                  <Tag color={row.status === 'needs_review' ? 'orange' : 'grey'}>{v}</Tag>
                  {row.resolve_state === 'processing' && (
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>核定处理中</div>
                  )}
                </div>
              ),
            },
            {
              title: '审批信息',
              dataIndex: 'instance_id',
              render: (v: string | null, row: OaApprovalInstance) => (
                <div style={{ fontSize: 12 }}>
                  <div>{v ? '已创建审批' : row.status === 'needs_review' ? '是否建单待核对' : row.status === 'submitting' ? '正在发起' : '尚未创建审批'}</div>
                  <div style={{ color: 'var(--crm-text-3)' }}>第 {row.submit_round ?? 1} 轮</div>
                  {row.error && <div style={{ color: 'var(--crm-text-3)' }}>{row.error}</div>}
                  {row.result?.withdraw_snapshot_pending === true && <div>撤回已确认，正在等待审批系统 最终过程记录；下方为此前同步的快照。</div>}
                </div>
              ),
            },
            {
              title: '操作',
              width: 240,
              render: (_: unknown, row: OaApprovalInstance) => {
                // **按钮只看服务端给的 allowed_actions**：前端不再自己判断状态，
                // 两边各写一套 if 必然分叉，分叉出去的那一侧就是重复建实例的入口
                const actions = row.allowed_actions ?? []
                const isLatest = (row.submit_round ?? 1) === latestApprovalRound
                const buttons: ReactNode[] = []
                if (row.provider === 'self_oa' && row.instance_id && can('quote:manage')) {
                  buttons.push(<SyncOaButton key="sync" oaId={row.id} invalidateKeys={[[ 'inquiry-approvals', approvalTarget?.id ]]} />)
                }
                if (actions.includes('resolve_adopt')) {
                  buttons.push(
                    <button type="button" className="crm-text-action"
                      key="adopt"
                      onClick={() => {
                        resolveKeyRef.current = newRequestKey()
                        setAdoptTarget(row)
                        setAdoptInstanceId('')
                      }}
                    >
                      认领
                    </button>,
                  )
                }
                if (actions.includes('resolve_resend')) {
                  buttons.push(
                    <Popconfirm
                      key="resend"
                      title="确认审批系统没有这张单？"
                      content="重发会再向审批系统 发起一次；若其实已经建过，就会多出一张审批单。"
                      onConfirm={() =>
                        resolveMutation.mutate({
                          oaId: row.id,
                          action: 'resend',
                          requestKey: newRequestKey(),
                        })
                      }
                    >
                      <button type="button" className="crm-text-action">重发</button>
                    </Popconfirm>,
                  )
                }
                if (actions.includes('resolve_abandon')) {
                  buttons.push(
                    <Popconfirm
                      key="abandon"
                      title="作废本轮记录？"
                      content="仅在本系统里作废，不会动审批系统。"
                      onConfirm={() =>
                        resolveMutation.mutate({
                          oaId: row.id,
                          action: 'abandon',
                          requestKey: newRequestKey(),
                        })
                      }
                    >
                      <button type="button" className="crm-text-action" style={{ color: 'var(--crm-error)' }}>作废</button>
                    </Popconfirm>,
                  )
                }
                if (isLatest && actions.includes('resubmit')) {
                  buttons.push(
                    <Popconfirm
                      key="resubmit"
                      title="驳回或撤回后重新提交？"
                      content="会向审批系统「新建一轮」审批（旧轮次保留在记录里）。"
                      onConfirm={() => {
                        if (!approvalTarget) return
                        const target = approvalTarget
                        setApprovalTarget(null)
                        startApproval(target, true)
                      }}
                    >
                      <button type="button" className="crm-text-action">修改后重新提交</button>
                    </Popconfirm>,
                  )
                }
                if (isLatest && actions.includes('retry')) {
                  buttons.push(
                    <Popconfirm
                      key="retry"
                      title="重新发起？"
                      content="重新提交本轮申请；请先确认审批系统没有已存在的申请。"
                      onConfirm={() => {
                        if (!approvalTarget) return
                        const target = approvalTarget
                        setApprovalTarget(null)
                        startApproval(target, false)
                      }}
                    >
                      <button type="button" className="crm-text-action">重新发起</button>
                    </Popconfirm>,
                  )
                }
                if (isLatest && row.provider === 'self_oa' && row.status === 'pending' && row.instance_id && row.oa_type === 'inquiry') {
                  buttons.push(
                    <Popconfirm
                      key="withdraw"
                      title="撤回这张审批？"
                      content="只对仍在审批中的审批系统 流程生效；已完成的审批不能撤回。"
                      onConfirm={() => withdrawMutation.mutate(row.id)}
                    >
                      <button type="button" className="crm-text-action" style={{ color: 'var(--crm-error)' }}>撤回审批</button>
                    </Popconfirm>,
                  )
                }
                if (buttons.length === 0) {
                  return <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>-</span>
                }
                return <span style={{ display: 'inline-flex', gap: 10 }}>{buttons}</span>
              },
            },
          ]}
          // 小三角换成文字触发器（同报价明细/审批表）
          expandIcon={(expanded?: boolean) => (
            <span className="expand-toggle">{expanded ? '收起' : '展开'}</span>
          )}
          expandedRowRender={(row?: OaApprovalInstance) => {
            if (!row) return null
            const progress = approvalProgress(row.result)
            return <div style={{ padding: 12, minWidth: 0 }}>
              <div style={{ marginBottom: 10 }}>审批在审批系统 处理，以下为最近同步的过程。
                最近同步：{row.synced_at ? formatApprovalTime(row.synced_at) : '尚未同步'}
              </div>
              {progress.length ? <Table dataSource={progress.map((entry, index) => ({ ...entry, key: index }))}
                rowKey="key" pagination={false} scroll={{ x: 650 }} columns={[
                  { title: '类别', dataIndex: 'kind', width: 80, render: (kind: string) => kind === 'task' ? '节点' : '操作记录' },
                  { title: '处理人', dataIndex: 'person', width: 160 },
                  { title: '节点 / 操作', dataIndex: 'node', width: 150 },
                  { title: '状态 / 结果', dataIndex: 'status', width: 130 },
                  { title: '发生时间', dataIndex: 'time', width: 180, render: (value: string | null) => {
                    return formatApprovalEventTime(value).replace('尚未返回', '未返回')
                  } },
                  { title: '处理意见', dataIndex: 'remark', render: (value: string) => value || '—' },
                ]} /> : <div>审批系统 尚未返回节点或操作记录，稍后再查看；最终结果以本轮审批总状态为准。</div>}
              <OaFormSnapshot result={row.result} />
            </div>
          }}
          dataSource={approvalRows}
          loading={approvalsQuery.isLoading}
          rowKey="id"
          pagination={false}
        />
      </Modal>

      <Modal
        title="认领审批单"
        visible={Boolean(adoptTarget)}
        onCancel={() => setAdoptTarget(null)}
        onOk={() => {
          if (!adoptTarget || !adoptInstanceId.trim()) {
            Toast.error('请填写审批系统已有的审批单号')
            return
          }
          resolveMutation.mutate({
            oaId: adoptTarget.id,
            action: 'adopt',
            instanceId: adoptInstanceId.trim(),
            // 同一个对话框里重试复用同一个键：服务端靠它回放结果，
            // 而不是把"再点一次认领"当成新的一次核定
            requestKey: resolveKeyRef.current ?? undefined,
          })
        }}
        confirmLoading={resolveMutation.isPending}
        okText="认领"
        width={460}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            到审批系统中找到这张审批单，把它的单号填进来——系统会先核实模板、发起人和来源需求，
            对不上就拒绝采纳，然后才接着它回收审批结果。
          </div>
          <Input
            placeholder="审批单号"
            value={adoptInstanceId}
            onChange={(value) => setAdoptInstanceId(value)}
          />
        </div>
      </Modal>

      {/* 转报价（§3.1/场景09）：只填两个数——核价成本与报价。
          成本必填不是啰嗦：按 0 记成本会算出 100% 毛利、低价审批永不触发 */}
      <Modal
        title={`生成报价草稿：${quoteTarget?.title ?? ''}`}
        cancelText="取消"
        visible={Boolean(quoteTarget)}
        onCancel={() => setQuoteTarget(null)}
        onOk={() => {
          if (!quoteForm.unit_cost.trim() || !quoteForm.quoted_price.trim()) {
            Toast.warning('请填写核价成本与报价')
            return
          }
          quoteMutation.mutate()
        }}
        confirmLoading={quoteMutation.isPending}
        okText="生成报价草稿"
        width={520}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 12 }}>
          {quoteMutation.error && (
            <Banner type="danger" fullMode={false} closeIcon={null} description={quoteMutation.error.message}>
                <Button theme="borderless" onClick={() => {
                  refresh()
                  void queryClient.invalidateQueries({ queryKey: ['inquiry-history'] })
                  setQuoteTarget(null)
                }}>刷新询价</Button>
            </Banner>
          )}
          <div style={{ padding: 12, borderRadius: 8, background: 'var(--crm-surface-low)', color: 'var(--crm-text-2)', lineHeight: 1.7 }}>可以先准备内部报价草稿；正式对外报价需先通过核价及报价复核，无需等待后续打样、采购或备货结束。</div>

          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            {quoteTarget?.customer_name
              ? `客户：${quoteTarget.customer_name}`
              : '这条需求还没关联客户——先去「编辑」补上客户再转报价。'}
            {!quoteTarget?.customer_name && (
              <button
                type="button"
                className="crm-text-action"
                style={{ marginLeft: 4 }}
                onClick={() => {
                  const target = quoteTarget
                  setQuoteTarget(null)
                  if (target) openEdit(target)
                }}
              >
                去编辑补客户
              </button>
            )}
            {quoteTarget?.inquiry_no ? ` · 需求编号 ${quoteTarget.inquiry_no}` : ''}
          </div>
          <div>
            <FormLabel
              required
              help="填核价人员确认的单件成本。系统用它计算利润和审批门槛，运费单独计算。"
              helpLabel="核价成本"
            >
              核价成本（元/件，不含运费）
            </FormLabel>
            <Input
              style={{ width: '100%' }}
              inputMode="decimal"
              value={quoteForm.unit_cost}
              onChange={(value) => setQuoteForm({ ...quoteForm, unit_cost: value })}
            />
          </div>
          <div>
            <FormLabel required>报价（元/件）</FormLabel>
            <Input
              style={{ width: '100%' }}
              inputMode="decimal"
              value={quoteForm.quoted_price}
              onChange={(value) =>
                setQuoteForm({ ...quoteForm, quoted_price: value })
              }
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>数量</div>
            <Input
              style={{ width: '100%' }}
              inputMode="decimal"
              value={quoteForm.quantity}
              onChange={(value) => setQuoteForm({ ...quoteForm, quantity: value })}
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
