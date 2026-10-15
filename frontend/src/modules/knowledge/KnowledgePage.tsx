import { useRef, useState, type ComponentProps, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Dropdown,
  DatePicker,
  Input,
  InputNumber,
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
import { approvalProgress } from './approval-progress'
import AttachmentPanel from '../common/AttachmentPanel'
import SampleFromSourceModal from '../sample/SampleFromSourceModal'
import { parseOptionalNumber } from '../../shared/number-input'
import { usePermissions } from '../../shared/hooks/permissions'
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
import { getOpportunity, listOpportunities } from '../../shared/api/opportunity'
import {
  listInquiryApprovals,
  getInquirySubmissionOptions,
  resolveOaInstance,
  startInquiryApproval,
  type OaApprovalInstance,
} from '../../shared/api/dingtalk'
import { reportOperationTiming } from '../../shared/api/analytics'
import { newRequestKey } from '../../shared/api/requestKey'
import FormLabel from '../../shared/components/FormLabel'
import { useBlockedNotify } from '../../shared/components/BlockedHint'
import { optionMatcher, optionsWithCodeOnDuplicate } from '../../shared/components/optionMatch'

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
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const rawOpportunityId = Number(searchParams.get('opportunity_id'))
  const opportunityFilter = Number.isSafeInteger(rawOpportunityId) && rawOpportunityId > 0 ? rawOpportunityId : undefined
  const { can } = usePermissions()
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
  const [editVisible, setEditVisible] = useState(false)
  const [editing, setEditing] = useState<CustomInquiryRow | null>(null)
  const [form, setForm] = useState<InquiryForm>(EMPTY_FORM)
  const [pendingPhotos, setPendingPhotos] = useState<FileItem[]>([])
  const [pendingAttachments, setPendingAttachments] = useState<FileItem[]>([])
  const saveIntent = useRef(false)
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
  // 钉钉审批记录：查看历次提交；"结果不明"的单子在这里转人工处理
  const [approvalTarget, setApprovalTarget] = useState<CustomInquiryRow | null>(null)
  const [adoptTarget, setAdoptTarget] = useState<OaApprovalInstance | null>(null)
  const [adoptInstanceId, setAdoptInstanceId] = useState('')
  /** 当前这次人工核定的请求键：打开对话框时生成，成功后清掉（重试复用同一个） */
  const resolveKeyRef = useRef<string | null>(null)
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
        note: action === 'abandon' ? '人工核对确认钉钉未建单，作废本轮' : undefined,
        // 同一次核定带同一个请求键：重试只生效一次并回放同一份结果。
        // 换新键等于告诉后端"这是一次新的核定"（就会再打一次钉钉）
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
    queryKey: ['custom-inquiries', { statusFilter, keyword, page, opportunityFilter }],
    queryFn: () =>
      listCustomInquiries({ status: statusFilter, keyword, page, page_size: 20, opportunity_id: opportunityFilter }),
  })
  const linkedOpportunity = useQuery({
    queryKey: ['opportunity', opportunityFilter],
    queryFn: () => getOpportunity(opportunityFilter!), enabled: Boolean(opportunityFilter),
  })
  const opportunitiesQuery = useQuery({
    queryKey: ['opportunities-for-inquiry', form.customer_id],
    // 只列「进行中」的商机（理由同「新建样品申请」：已失单/已成交的不该再作为
    // 「本次采购需求」的关联对象，其它选商机的地方也都带这道过滤）。
    queryFn: () => listOpportunities({
      customer_id: form.customer_id ?? undefined, status: 'open', page_size: 100,
    }),
    enabled: editVisible && Boolean(form.customer_id),
  })
  const selectedOpportunity = useQuery({
    queryKey: ['opportunity', form.opportunity_id],
    queryFn: () => getOpportunity(form.opportunity_id!), enabled: editVisible && Boolean(form.opportunity_id),
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
    mutationFn: async (payload: CustomInquiryPayload) => {
      const existing = editing ?? savedInquiry.current
      const options = submissionOptions.data
      const draft = { fields: submitFields, quote_owner_user_id: submitQuoteOwner ?? options?.selected_quote_owner ?? null, attachment_file_ids: submitAttachments }
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
          throw new Error(`询价已保存；钉钉状态：${result.status_label}。请查看审批记录，结果不明时不要重复提交。`)
        }
        Toast.success('已提交到钉钉，生产部将在钉钉处理')
        setApprovalTarget(row)
      }
      return row
    },
    onSuccess: () => {
      if (!saveIntent.current) Toast.success('草稿已保存，尚未发送钉钉审批')
      setEditVisible(false)
      setEditing(null)
      savedInquiry.current = null
      setPendingPhotos([])
      setPendingAttachments([])
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

  const submissionOptions = useQuery({
    queryKey: ['inquiry-oa-submission', submitTarget?.id ?? editing?.id ?? 'new'],
    queryFn: () => getInquirySubmissionOptions(submitTarget?.id ?? editing?.id),
    enabled: Boolean(submitTarget) || editVisible,
  })
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
        Toast.success('询价已提交到钉钉OA，后续由生产部在钉钉处理')
      } else if (['needs_review', 'submitting'].includes(result.status)) {
        Toast.warning('提交结果尚未确定，请查看审批记录并核对钉钉，不要重复提交')
      } else if (['skipped', 'not_sent', 'failed'].includes(result.status)) {
        Toast.warning(`尚未提交成功：${result.error || result.status_label}`)
      } else Toast.info(`当前钉钉审批状态：${result.status_label}，请查看审批记录`)
      setApprovalTarget(submitTarget)
      setSubmitTarget(null)
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
    setQuoteForm({
      unit_cost: null,
      quoted_price: null,
      quantity: row.quantity ?? null,
    })
    quoteStartedAt.current = Date.now()
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
        if (can('quote:manage')) moreActions.push({ key: 'oa-start', label: '提交询价到钉钉', onClick: () => startApproval(record) })
        // 历次审批 + "结果不明"的人工处理入口（认领/重发/作废）
        moreActions.push({ key: 'oa-log', label: '审批记录', onClick: () => setApprovalTarget(record) })
        if ((record.version ?? 1) > 1) {
          moreActions.push({ key: 'history', label: '历史', onClick: () => setHistoryTarget(record) })
        }
        if (record.status !== 'developing' && record.status !== 'converted') {
          moreActions.push({ key: 'developing', label: '转开发中', onClick: () => changeStatus(record, 'developing') })
        }
        if (record.status !== 'archived') {
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
  return (
          <span style={{ display: 'inline-flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            {can('quote:manage') && <a onClick={() => setPhotoTarget(record)}>上传照片</a>}
            <a onClick={() => openEdit(record)}>编辑</a>
            <a onClick={() => openRevise(record)}>修订</a>
            {/* 定制件没有 SKU，报价中心选不到它——这里直接转报价（场景09） */}
            <a onClick={() => openQuote(record)}>转报价</a>
            <Popconfirm title="删除这条定制询价？" onConfirm={() => deleteMutation.mutate(record.id)}>
              <a style={{ color: 'var(--crm-error)' }}>删除</a>
            </Popconfirm>
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
              <a title="更多操作">更多</a>
            </Dropdown>
          </span>
        )
      },
    },
  ]

  const renderSubmissionFields = (target: CustomInquiryRow | null) => <>
    {submissionOptions.isPending && <div>正在读取钉钉询价表单……保存草稿不受影响。</div>}
    {submissionOptions.isError && <div style={{ color: 'var(--crm-error)' }}>钉钉表单读取失败，仍可保存草稿；请稍后重试提交。</div>}
          {submissionOptions.data?.quote_owner_required && <div>
            <FormLabel required>对接报价员</FormLabel>
            <Select style={{ width: '100%' }} placeholder="选择钉钉对接报价员"
              disabled={submitMutation.isPending || saveMutation.isPending}
              value={submitQuoteOwner ?? submissionOptions.data.selected_quote_owner ?? undefined}
              onChange={(value) => setSubmitQuoteOwner(value as string)}
              optionList={submissionOptions.data.quote_owners.map((item) => ({ value: item.user_id, label: item.name }))}
            />
          </div>}
          {submissionOptions.data?.fields.map((field) => <div key={field.id}>
            <FormLabel required={field.required}>{field.label}</FormLabel>
            {field.type === 'DDAttachment' ? (
              <>
                <div style={{ marginBottom: 8 }}>选填，可上传图纸、PDF、表格等文件；与上方产品参考照片分别提交。</div>
                {editVisible && <Upload action="" uploadTrigger="custom" multiple fileList={pendingAttachments}
                  disabled={saveMutation.isPending || !can('file:manage')} maxSize={20 * 1024}
                  onSizeError={() => Toast.warning('单个附件不能超过20 MB')}
                  onChange={({ fileList }) => setPendingAttachments(fileList)}
                  prompt="选中的附件会在保存草稿或提交时上传。">
                  <Button disabled={saveMutation.isPending || !can('file:manage')}>选择附件（可多选）</Button>
                </Upload>}
                {target && <AttachmentPanel businessType={submissionOptions.data!.file_business_type} businessId={target.id}
                  category="oa_attachment" onlyCategory="oa_attachment" writePermission="quote:manage" /> }
                <Select multiple style={{ width: '100%' }} placeholder="选择本次要提交到钉钉的附件（最多10个）"
                  value={submitAttachments} disabled={submitMutation.isPending || saveMutation.isPending} maxTagCount={3}
                  optionList={(submissionFiles.data ?? []).filter((file) => file.category === 'oa_attachment').map((file) => ({ value: file.id, label: file.file_name }))}
                  onChange={(value) => {
                    const ids = value as number[]
                    if (ids.length > 10) { Toast.warning('单次最多提交10个附件'); return }
                    setSubmitAttachments(ids)
                  }} />
                {submissionFiles.isError && <div style={{ color: 'var(--crm-error)' }}>附件列表读取失败，请重试。</div>}
              </>
            ) : field.type === 'DDSelectField' ? (
              <Select style={{ width: '100%' }} placeholder="请选择" disabled={submitMutation.isPending || saveMutation.isPending}
                value={submitFields[field.id] || undefined}
                optionList={field.options.map((value) => ({ value, label: value }))}
                onChange={(value) => setSubmitFields((current) => ({ ...current, [field.id]: value as string }))} />
            ) : field.type === 'DDDateField' ? (
              <DatePicker type="dateTime" format="yyyy-MM-dd HH:mm" style={{ width: '100%' }}
                disabled={submitMutation.isPending || saveMutation.isPending} placeholder="请选择期望回复时间" showClear
                value={submitFields[field.id] || undefined}
                onChange={(_, value) => setSubmitFields((current) => ({ ...current, [field.id]: (value as string) || '' }))} />
            ) : (
              <TextArea value={submitFields[field.id] || ''} rows={2} disabled={submitMutation.isPending || saveMutation.isPending}
                placeholder={`填写${field.label}`}
                onChange={(value) => setSubmitFields((current) => ({ ...current, [field.id]: value }))} />
            )}
          </div>)}
  </>

  const saveForm = (submit: boolean) => {
    if (saveMutation.isPending) return
    if (!form.title.trim()) { Toast.warning('产品名称必填'); return }
    if (submitAttachments.length + pendingAttachments.length > 10) { Toast.warning('单次最多选择10个附件，请移除多余文件'); return }
    if (submit) {
      const options = submissionOptions.data
      if (!options?.configured || !options.enabled) { Toast.warning('钉钉询价暂不可提交，请先保存草稿'); return }
      if (options.quote_owner_required && !(submitQuoteOwner ?? options.selected_quote_owner)) { Toast.warning('请选择对接报价员'); return }
      const missing = options.fields.find((field) => field.required && (field.type === 'DDAttachment'
        ? !submitAttachments.length && !pendingAttachments.length : !submitFields[field.id]?.trim()))
      if (missing) { Toast.warning(`请填写${missing.label}`); return }
      if (!form.description.trim() || !form.quantity.trim()) { Toast.warning('提交前请填写材质及工艺要求、预估订购数量'); return }
      if (options.image_required && !pendingPhotos.length && !(submissionFiles.data ?? []).some((file) => file.category !== 'oa_attachment' && file.mime_type?.startsWith('image/'))) {
        Toast.warning('提交前请选择产品参考照片'); return
      }
    }
    saveIntent.current = submit
    saveMutation.mutate({ title: form.title.trim(), description: form.description.trim() || null,
      customer_id: form.customer_id ?? null, opportunity_id: form.opportunity_id ?? null,
      quantity: form.quantity.trim() ? parseOptionalNumber(form.quantity) : null,
      target_price: form.target_price.trim() ? parseOptionalNumber(form.target_price) : null,
      remark: form.remark.trim() || null, status: editing ? (form.status ?? editing.status) : undefined })
  }

  return (
    <div className="page-container">
      <PageHeader
        title="定制需求"
        subtitle="客户问了但我们还没有标准产品的需求都沉淀在这里——这是找开发方向的原料"
      />

      <SectionCard>
        {opportunityFilter && <div style={{ marginBottom: 12 }}>
          当前商机：{linkedOpportunity.data?.title ?? linkedOpportunity.error?.message ?? `商机编号 ${opportunityFilter}`} ·
          <Button theme="borderless" onClick={() => { setSearchParams({}); setPage(1) }}>查看全部询价</Button>
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
            placeholder="搜索产品名称"
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

      {draftSourceId != null && <SampleFromSourceModal mode="order" source={{ inquiry_id: draftSourceId }} onClose={() => setDraftSourceId(null)} />}
      {sampleSourceId != null && <SampleFromSourceModal source={{ inquiry_id: sampleSourceId }} onClose={() => setSampleSourceId(null)} />}
      <Modal title="询价照片与附件" visible={Boolean(photoTarget)} width={720}
        bodyStyle={{ maxHeight: '65vh', overflowY: 'auto' }}
        onCancel={() => setPhotoTarget(null)} footer={<Button onClick={() => setPhotoTarget(null)}>完成</Button>}>
        <div style={{ marginBottom: 12 }}>{photoTarget?.title}：上传的照片会用于这条询价的钉钉审批。</div>
        {photoTarget && <AttachmentPanel businessType="inquiry" businessId={photoTarget.id} writePermission="quote:manage" excludeCategory="oa_attachment" uploadLabel="上传照片" accept="image/jpeg,image/png,image/gif,image/webp,image/bmp" />}
      </Modal>
      <Modal
        title="提交询价到钉钉OA"
        visible={Boolean(submitTarget)}
        width={720}
        bodyStyle={{ maxHeight: '65vh', overflowY: 'auto' }}
        onCancel={() => { if (!submitMutation.isPending) setSubmitTarget(null) }}
        okText="确认提交到钉钉"
        confirmLoading={submitMutation.isPending}
        okButtonProps={{ disabled: submissionOptions.isPending || submissionOptions.isError || !submissionOptions.data?.configured || !submissionOptions.data?.enabled }}
        onOk={() => {
          if (!submitTarget || !submissionOptions.data) return
          const quoteOwner = submitQuoteOwner ?? submissionOptions.data.selected_quote_owner ?? undefined
          if (submissionOptions.data.quote_owner_required && !quoteOwner) { Toast.warning('请选择对接报价员'); return }
          const missing = submissionOptions.data.fields.find((field) => field.required && (field.type === 'DDAttachment' ? !submitAttachments.length : !submitFields[field.id]?.trim()))
          if (missing) { Toast.warning(`请填写${missing.label}`); return }
          submitMutation.mutate({ inquiryId: submitTarget.id, quoteOwner, resubmit: submitResubmit, fields: submitFields })
        }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 12 }}>
          <div>业务员在CRM提交，生产部在钉钉接收并处理。确认提交后才会发起审批，无需在钉钉重复填单。</div>
          <div><strong>{submitTarget?.title}</strong>（第 {submitTarget?.version ?? 1} 版）</div>
          <div style={{ whiteSpace: 'pre-wrap' }}>{submitTarget?.description || '尚未填写需求描述'}</div>
          <div>数量：{submitTarget?.quantity ?? '未填写'}；客户：{submitTarget?.customer_name || '未关联'}</div>
          {submissionOptions.isError && <div style={{ color: 'var(--crm-error)' }}>{submissionOptions.error.message}</div>}
          {submissionOptions.data && !submissionOptions.data.configured && <div>尚未配置钉钉询价模板，请联系管理员。</div>}
          {submissionOptions.data && !submissionOptions.data.enabled && <div>钉钉询价提交暂未开启，请联系管理员。</div>}
          {submitTarget && submissionOptions.data && <div>
            <FormLabel required={submissionOptions.data.image_required}>产品参考图片</FormLabel>
            <div style={{ marginBottom: 8, color: 'var(--crm-text-3)', fontSize: 12 }}>上传真实产品参考图片后再提交；审批结果可在「审批记录」查看，系统会定期从钉钉同步。</div>
            <AttachmentPanel businessType={submissionOptions.data.file_business_type} businessId={submitTarget.id}
              writePermission="quote:manage" excludeCategory="oa_attachment" uploadLabel="上传照片" accept="image/jpeg,image/png,image/gif,image/webp,image/bmp" />
          </div>}
          {renderSubmissionFields(submitTarget)}

        </div>
      </Modal>

      <Modal
        title={editing ? '编辑定制询价' : '记录定制询价'}
        visible={editVisible}
        onCancel={() => { if (!saveMutation.isPending) setEditVisible(false) }}
        cancelButtonProps={{ disabled: saveMutation.isPending }}
        footer={<div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <Button disabled={saveMutation.isPending} onClick={() => setEditVisible(false)}>取消</Button>
          <Button loading={saveMutation.isPending && !saveIntent.current} disabled={saveMutation.isPending} onClick={() => saveForm(false)}>保存草稿</Button>
          <Button theme="solid" loading={saveMutation.isPending && saveIntent.current}
            disabled={saveMutation.isPending || submissionOptions.isPending || submissionOptions.isError} onClick={() => saveForm(true)}>提交到钉钉</Button>
        </div>}
        width={760}
        bodyStyle={{ maxHeight: '65vh', overflowY: 'auto' }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 12 }}>
          <div>
            <FormLabel>产品参考照片</FormLabel>
            {(!editing || pendingPhotos.length > 0) && <>
              <Upload action="" uploadTrigger="custom" multiple accept="image/jpeg,image/png,image/gif,image/webp,image/bmp"
                maxSize={20 * 1024} onSizeError={() => Toast.warning('单张照片不能超过 20 MB')}
                fileList={pendingPhotos} disabled={saveMutation.isPending || !can('file:manage') || !can('quote:manage')}
                onChange={({ fileList }) => setPendingPhotos(fileList)}
                onAcceptInvalid={() => Toast.warning('请选择 JPG、PNG、GIF、WebP 或 BMP 图片')}
                prompt="先选择照片；点「保存草稿」仅保存，点「提交到钉钉」才会发出审批。">
                <Button theme="solid" disabled={saveMutation.isPending || !can('file:manage') || !can('quote:manage')}>选择照片（可多选）</Button>
              </Upload>
              {(!can('file:manage') || !can('quote:manage')) && <div>上传照片需要文件管理和报价管理权限，请联系管理员。</div>}
            </>}
            {editing && <AttachmentPanel businessType="inquiry" businessId={editing.id} writePermission="quote:manage" excludeCategory="oa_attachment" uploadLabel="上传照片" accept="image/jpeg,image/png,image/gif,image/webp,image/bmp" />}
          </div>
          <div>
            <FormLabel required>产品名称</FormLabel>
            <Input
              value={form.title}
              onChange={(value) => setForm({ ...form, title: value })}
              placeholder="例如：客户想要带磁吸翻盖的礼品盒"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>材质及工艺要求</div>
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
              onChange={(value) => setForm({ ...form, customer_id: (value as number) ?? null, opportunity_id: null })}
              optionList={Array.from(new Map([
                ...(customersQuery.data?.items ?? []).map((item) => [item.id, { value: item.id, label: item.name }] as const),
                ...(selectedOpportunity.data ? [[selectedOpportunity.data.customer_id, {
                  value: selectedOpportunity.data.customer_id,
                  label: selectedOpportunity.data.customer_name ?? `客户编号 ${selectedOpportunity.data.customer_id}`,
                }] as const] : []),
              ]).values())}
              filter={optionMatcher}
              style={{ width: '100%' }}
              showClear
              placeholder="关联客户"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>关联商机（本次采购需求）</div>
            {/* 客户没选时这个框是灰的（判据没动）。灰着的时候外面包一层接点击：
                点它就弹一句「先选客户，才能关联该客户的商机」，而不是毫无反应。 */}
            <div className="blocked-hint-click" onClick={notifyOpportunityBlocked} style={{ display: 'block' }}>
              <Select value={form.opportunity_id ?? undefined} disabled={!form.customer_id}
                onChange={(value) => setForm({ ...form, opportunity_id: (value as number) ?? null })}
                optionList={optionsWithCodeOnDuplicate(
                  Array.from(new Map([
                    ...(opportunitiesQuery.data?.items ?? []),
                    ...(selectedOpportunity.data && selectedOpportunity.data.customer_id === form.customer_id ? [selectedOpportunity.data] : []),
                  ].map((item) => [item.id, item])).values()),
                  // 显示文案就是商机名：同一客户底下同名商机必然撞车，
                  // 按项目规矩撞车才补「编号 N」。
                  (item) => item.title,
                  (item) => item.id,
                )}
                filter showClear
                style={{ width: '100%', ...(form.customer_id ? null : { pointerEvents: 'none' }) }}
                placeholder="选择该客户的商机，避免重复创建" />
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
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>预估订购数量</div>
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
          <div style={{ borderTop: '1px solid var(--crm-border)', paddingTop: 12 }}>钉钉询价资料：草稿允许暂缺，提交时会检查必填项。</div>
          {renderSubmissionFields(editing)}
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
            { title: '目标价', dataIndex: 'target_price', width: 90, render: (v: number | null) => v ?? '-' },
            { title: '本版说明', dataIndex: 'revision_note', render: (v: string | null) => v ?? '（原始要求）' },
            { title: '操作', render: (_: unknown, row: CustomInquiryRow) => <div>{can('sample:manage') && <a onClick={() => { setHistoryTarget(null); setSampleSourceId(row.id) }}>按此版本申请打样</a>}{can('order:manage') && <a style={{ display: 'block' }} onClick={() => { setHistoryTarget(null); setDraftSourceId(row.id) }}>按此版本建订单草稿</a>}</div> },
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
        width={1000}
        bodyStyle={{ maxHeight: '70vh', overflowY: 'auto' }}
      >
        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 10 }}>
          点击每行左侧展开按钮可查看钉钉节点、处理意见和时间。后台定期同步，显示可能有延迟。
          发起过程中断时钉钉那边可能已经建了单，而钉钉接口没有"只许建一次"的开关，
          所以「不会自动重发」。请先到钉钉确认，再选「认领 / 重发 / 作废」。
          按钮由服务端返回的可用动作决定——待审批、已通过、结果未知都不会给出「重提」，
          因为那会在钉钉里另建一张单。
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
              title: '钉钉单号 / 请求号',
              dataIndex: 'instance_id',
              render: (v: string | null, row: OaApprovalInstance) => (
                <div style={{ fontSize: 12 }}>
                  <div>{v ?? '-'}</div>
                  {/* 请求号 + 尝试次数：出问题时拿这两个数与钉钉那一次请求对账 */}
                  <div style={{ color: 'var(--crm-text-3)' }}>
                    请求号 {row.request_no ?? '-'}｜已发起 {row.attempt_count ?? 0} 次
                  </div>
                  {row.error && <div style={{ color: 'var(--crm-text-3)' }}>{row.error}</div>}
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
                if (actions.includes('resolve_adopt')) {
                  buttons.push(
                    <a
                      key="adopt"
                      onClick={() => {
                        resolveKeyRef.current = newRequestKey()
                        setAdoptTarget(row)
                        setAdoptInstanceId('')
                      }}
                    >
                      认领
                    </a>,
                  )
                }
                if (actions.includes('resolve_resend')) {
                  buttons.push(
                    <Popconfirm
                      key="resend"
                      title="确认钉钉那边没有这张单？"
                      content="重发会再向钉钉发起一次；若其实已经建过，就会多出一张审批单。"
                      onConfirm={() =>
                        resolveMutation.mutate({
                          oaId: row.id,
                          action: 'resend',
                          requestKey: newRequestKey(),
                        })
                      }
                    >
                      <a>重发</a>
                    </Popconfirm>,
                  )
                }
                if (actions.includes('resolve_abandon')) {
                  buttons.push(
                    <Popconfirm
                      key="abandon"
                      title="作废本轮记录？"
                      content="仅在本系统里作废，不会动钉钉那边。"
                      onConfirm={() =>
                        resolveMutation.mutate({
                          oaId: row.id,
                          action: 'abandon',
                          requestKey: newRequestKey(),
                        })
                      }
                    >
                      <a style={{ color: 'var(--crm-error)' }}>作废</a>
                    </Popconfirm>,
                  )
                }
                if (isLatest && actions.includes('resubmit')) {
                  buttons.push(
                    <Popconfirm
                      key="resubmit"
                      title="驳回后重提？"
                      content="会向钉钉「新建一轮」审批（旧轮次保留在记录里）。"
                      onConfirm={() => {
                        if (!approvalTarget) return
                        const target = approvalTarget
                        setApprovalTarget(null)
                        startApproval(target, true)
                      }}
                    >
                      <a>驳回后重提</a>
                    </Popconfirm>,
                  )
                }
                if (isLatest && actions.includes('retry')) {
                  buttons.push(
                    <Popconfirm
                      key="retry"
                      title="重新发起？"
                      content="沿用同一轮、同一请求号再发一次（外部确认没建单时才适用）。"
                      onConfirm={() => {
                        if (!approvalTarget) return
                        const target = approvalTarget
                        setApprovalTarget(null)
                        startApproval(target, false)
                      }}
                    >
                      <a>重新发起</a>
                    </Popconfirm>,
                  )
                }
                if (buttons.length === 0) {
                  return <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>—</span>
                }
                return <span style={{ display: 'inline-flex', gap: 10 }}>{buttons}</span>
              },
            },
          ]}
          expandedRowRender={(row?: OaApprovalInstance) => {
            if (!row) return null
            const progress = approvalProgress(row.result)
            return <div style={{ padding: 12, minWidth: 0 }}>
              <div style={{ marginBottom: 10 }}>审批在钉钉处理，以下为最近同步的过程。
                最近同步：{row.synced_at ? new Date(row.synced_at).toLocaleString('zh-CN') : '尚未同步'}
              </div>
              {progress.length ? <Table dataSource={progress.map((entry, index) => ({ ...entry, key: index }))}
                rowKey="key" pagination={false} scroll={{ x: 650 }} columns={[
                  { title: '类别', dataIndex: 'kind', width: 80, render: (kind: string) => kind === 'task' ? '节点' : '操作记录' },
                  { title: '处理人', dataIndex: 'person', width: 160 },
                  { title: '节点 / 操作', dataIndex: 'node', width: 150 },
                  { title: '状态 / 结果', dataIndex: 'status', width: 130 },
                  { title: '时间', dataIndex: 'time', width: 180, render: (value: string | null) => {
                    const date = value ? new Date(value) : null
                    return date && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN') : '未返回'
                  } },
                  { title: '处理意见', dataIndex: 'remark', render: (value: string) => value || '—' },
                ]} /> : <div>钉钉尚未返回节点或操作记录，稍后再查看；最终结果以本轮审批总状态为准。</div>}
            </div>
          }}
          dataSource={approvalRows}
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
            到钉钉里找到这张审批单，把它的单号填进来——系统会先核实模板、发起人和来源需求，
            对不上就拒绝采纳，然后才接着它回收审批结果。
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
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            {quoteTarget?.customer_name
              ? `客户：${quoteTarget.customer_name}`
              : '这条需求还没关联客户——先去「编辑」补上客户再转报价。'}
            {quoteTarget?.inquiry_no ? ` · 需求编号 ${quoteTarget.inquiry_no}` : ''}
          </div>
          <div>
            <FormLabel required>核价成本（元/件，不含运费）</FormLabel>
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
            <FormLabel required>报价（元/件）</FormLabel>
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
