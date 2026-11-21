import { useEffect, useState } from 'react'
import { parseOptionalDecimal } from '../../shared/number-input'
import MaskedContact from '../../shared/components/MaskedContact'
import PageHeader from '../../shared/components/PageHeader'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyNode } from '../../shared/components/EmptyState'
import {
  Banner,
  Button,
  Checkbox,
  DatePicker,
  Input,
  Modal,
  Pagination,
  Popconfirm,
  Radio,
  RadioGroup,
  Select,
  Table,
  TextArea,
  Toast,
} from '@douyinfe/semi-ui'

import {
  assignLead,
  claimLead,
  convertLead,
  createLead,
  deduplicateLead,
  discardLead,
  getLead,
  listLeads,
  releaseLead,
  type Lead,
} from '../../shared/api/lead'
import { listCustomers } from '../../shared/api/customer'
import { listUsers } from '../../shared/api/system'
import { optionMatcher } from '../../shared/components/optionMatch'
import FollowUpModal from '../common/FollowUpModal'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import StatusDot from '../../shared/components/StatusDot'
import FormLabel from '../../shared/components/FormLabel'
import GuardedButton from '../../shared/components/GuardedButton'

const STATUS_OPTIONS = [
  { value: 'pending', label: '待分配' },
  { value: 'assigned', label: '已分配' },
  { value: 'following', label: '跟进中' },
  { value: 'converted', label: '已转客户' },
  { value: 'invalid', label: '无效' },
]

const SOURCE_OPTIONS = ['展会', '官网', '企业微信', '老客户介绍', 'Excel 导入', '手工录入', '其他渠道'].map(
  (value) => ({ value, label: value }),
)

const STATUS_COLOR: Record<string, TagTone> = {
  pending: 'orange',
  assigned: 'blue',
  following: 'green',
  converted: 'violet',
  invalid: 'grey',
}

const EMPTY_FORM = {
  name: '',
  company_name: '',
  contact_name: '',
  mobile: '',
  email: '',
  source: '手工录入',
  source_detail: '',
  region: '',
  remark: '',
}

export default function LeadListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canViewCustomers = can('customer:view')
  const canViewOpportunities = can('opportunity:view')
  const [searchParams, setSearchParams] = useSearchParams()

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [onlyUnassigned, setOnlyUnassigned] = useState(false)
  const [releasedLead, setReleasedLead] = useState<Lead | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  // 工作台右上「新建线索」跳到 /leads?new=1 时，直接把新建弹窗打开 ——
  // 否则用户点完只看到一张列表，还得自己再找一次按钮（2026-11-17 视觉审视 D2-04）。
  const openCreateFromUrl = can('lead:create') && searchParams.get('new') === '1'
  const [createVisible, setCreateVisible] = useState(false)
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const closeCreate = () => {
    setCreateVisible(false)
    if (openCreateFromUrl) {
      setSearchParams((params) => {
        const next = new URLSearchParams(params)
        next.delete('new')
        return next
      }, { replace: true })
    }
  }

  const [assignTarget, setAssignTarget] = useState<Lead | null>(null)
  const [assignTo, setAssignTo] = useState<number | null>(null)
  const [discardTarget, setDiscardTarget] = useState<Lead | null>(null)
  const [discardReason, setDiscardReason] = useState('')
  const [followupTarget, setFollowupTarget] = useState<Lead | null>(null)

  const [convertTarget, setConvertTarget] = useState<Lead | null>(null)
  const [conversionReceipt, setConversionReceipt] = useState<{
    customerId: number
    opportunityId: number
    customerMode: 'new' | 'existing'
  } | null>(null)
  const [customerMode, setCustomerMode] = useState<'new' | 'existing'>('new')
  const [existingCustomer, setExistingCustomer] = useState<{ label: string; value: number } | null>(null)
  const [customerSearch, setCustomerSearch] = useState('')
  const [customerKeyword, setCustomerKeyword] = useState('')
  const [createOpportunity, setCreateOpportunity] = useState(false)
  const [opportunityTitle, setOpportunityTitle] = useState('')
  const [expectedAmount, setExpectedAmount] = useState('')
  const [expectedCloseDate, setExpectedCloseDate] = useState<Date | null>(null)
  const opportunityAmount = createOpportunity ? parseOptionalDecimal(expectedAmount) : null
  const invalidOpportunityAmount = opportunityAmount !== null
    && (Number.isNaN(Number(opportunityAmount)) || Number(opportunityAmount) < 0)
  const convertDisabled = invalidOpportunityAmount || (customerMode === 'existing' && !existingCustomer)

  const listStatus = onlyUnassigned ? 'pending' : status
  const requestedLeadId = Number(searchParams.get('lead'))
  const focusedLead = Number.isSafeInteger(requestedLeadId) && requestedLeadId > 0
  const query = useQuery({
    queryKey: ['leads', focusedLead ? { leadId: requestedLeadId } : { keyword, status: listStatus, onlyUnassigned, page, pageSize }],
    queryFn: async () => {
      if (focusedLead) {
        const lead = await getLead(requestedLeadId)
        return { items: [lead], total: 1, page: 1, page_size: 1 }
      }
      return listLeads({
        keyword,
        status: listStatus,
        unassigned: onlyUnassigned || undefined,
        page,
        page_size: pageSize,
      })
    },
  })

  const usersQuery = useQuery({
    queryKey: ['assignable-users', 'active-for-leads'],
    queryFn: async () => {
      const filters = { status: 'active', page_size: 100 }
      const first = await listUsers({ ...filters, page: 1 })
      const users = [...first.items]
      for (let page = 2; users.length < first.total; page += 1) {
        const next = await listUsers({ ...filters, page })
        if (!next.items.length) break
        users.push(...next.items)
      }
      return users
    },
    enabled: Boolean(assignTarget) && can('lead:assign'),
  })
  const availableAssignees = (usersQuery.data ?? []).filter(item => item.id !== assignTarget?.owner_id)
  const selectedAssignee = availableAssignees.find(item => item.id === assignTo)
  const assignBlocked = usersQuery.isError
    ? '请先重新读取负责人名单。'
    : usersQuery.isPending
      ? '正在读取负责人名单。'
      : !availableAssignees.length
        ? '没有可接收线索的在职账号。'
        : !selectedAssignee
          ? '先选择新负责人。'
          : ''

  const candidatesQuery = useQuery({
    queryKey: ['lead-candidates', convertTarget?.id],
    queryFn: () => deduplicateLead(convertTarget!.id),
    enabled: Boolean(convertTarget),
  })
  const candidates = candidatesQuery.data?.candidates ?? []
  useEffect(() => {
    const timer = setTimeout(() => setCustomerKeyword(customerSearch.trim()), 250)
    return () => clearTimeout(timer)
  }, [customerSearch])
  const customerSearchQuery = useInfiniteQuery({
    queryKey: ['lead-conversion-customer-search', customerKeyword],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => listCustomers({ keyword: customerKeyword, page: pageParam, page_size: 20 }),
    getNextPageParam: (last, pages) =>
      last.items.length && pages.length * 20 < last.total ? pages.length + 1 : undefined,
    enabled: Boolean(convertTarget) && customerMode === 'existing' && canViewCustomers && Boolean(customerKeyword),
  })
  const customerOptions = (customerKeyword ? customerSearchQuery.data?.pages.flatMap((page) => page.items) ?? [] : candidates)
    .map((item) => ({
      value: item.id,
      label: [item.name, item.region, `编号 ${item.id}`].filter(Boolean).join(' · '),
    }))
  if (existingCustomer && !customerOptions.some((option) => option.value === existingCustomer.value)) {
    customerOptions.push(existingCustomer)
  }

  const openConvert = (lead: Lead) => {
    convertMutation.reset()
    setCustomerMode('new')
    setExistingCustomer(null)
    setCustomerSearch('')
    setCustomerKeyword('')
    setCreateOpportunity(false)
    setOpportunityTitle('')
    setExpectedAmount('')
    setExpectedCloseDate(null)
    setConvertTarget(lead)
  }

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['leads'] })
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
    void queryClient.invalidateQueries({ queryKey: ['opportunities'] })
  }

  const createMutation = useMutation({
    mutationFn: () => createLead(form),
    onSuccess: () => {
      Toast.success(can('followup:create') ? '线索已创建。联系到对方后，可在该线索的操作列记录跟进。' : '线索已创建')
      closeCreate()
      setForm({ ...EMPTY_FORM })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const claimMutation = useMutation({
    mutationFn: (id: number) => claimLead(id),
    onSuccess: (lead) => {
      setReleasedLead(current => current?.id === lead.id ? null : current)
      Toast.success(onlyUnassigned || status === 'pending'
        ? `已领取「${lead.name}」。取消当前筛选后，找到该线索并记录跟进。`
        : `已领取「${lead.name}」，可在本行点「跟进」。`)
      refresh()
    },
    onError: (error: Error) => {
      Toast.error(error.message)
      refresh()
    },
  })

  const assignMutation = useMutation({
    mutationFn: ({ leadId, ownerId }: { leadId: number; ownerId: number }) => assignLead(leadId, ownerId),
    onSuccess: (lead, { ownerId }) => {
      const owner = usersQuery.data?.find(item => item.id === ownerId)?.name
      Toast.success(owner ? `已把「${lead.name}」分配给${owner}。` : `已分配「${lead.name}」。`)
      setAssignTarget(null)
      setAssignTo(null)
      refresh()
    },
    onError: () => { void usersQuery.refetch() },
  })

  const releaseMutation = useMutation({
    mutationFn: (id: number) => releaseLead(id),
    onSuccess: (lead) => {
      setReleasedLead(lead)
      Toast.success(lead.status === 'pending' ? `「${lead.name}」已退回线索池` : `已解除「${lead.name}」的线索负责人`)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const discardMutation = useMutation({
    mutationFn: () => discardLead(discardTarget!.id, discardReason.trim()),
    onSuccess: () => {
      Toast.success('线索已废弃')
      setDiscardTarget(null)
      setDiscardReason('')
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['an-leads'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const convertMutation = useMutation({
    mutationFn: () =>
      convertLead(convertTarget!.id, {
        customer_mode: customerMode,
        customer_id: customerMode === 'existing' ? existingCustomer?.value : null,
        create_contact: true,
        create_opportunity: createOpportunity,
        opportunity_title: createOpportunity ? opportunityTitle || null : null,
        expected_amount: opportunityAmount,
        expected_close_date: createOpportunity && expectedCloseDate
          ? `${expectedCloseDate.getFullYear()}-${String(expectedCloseDate.getMonth() + 1).padStart(2, '0')}-${String(expectedCloseDate.getDate()).padStart(2, '0')}`
          : null,
      }),
    onSuccess: (data) => {
      Toast.success(data.opportunity_id
        ? '客户和商机已关联。'
        : can('customer:view') && can('opportunity:manage') && can('opportunity:view')
          ? '线索已转为客户。有明确采购需求时，可从客户详情的「商机」页开始。'
          : '线索已转为客户。')
      setConvertTarget(null)
      refresh()
      if (data.opportunity_id && canViewOpportunities) {
        setConversionReceipt({ customerId: data.customer_id, opportunityId: data.opportunity_id, customerMode })
      } else if (data.customer_id && canViewCustomers) {
        navigate(`/customers/${data.customer_id}`)
      }
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /* 1024 宽内容区约 700px，线索表八列合计 1066px，「状态 / 负责人 / 操作」
     整块被裁到卡片外。窄屏切紧凑档：联系人 + 负责人并到线索名称第二行，
     其余列宽收紧，操作列改两列网格，五个文字链才排得下。 */
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const compact = viewport < 1200
  const mobile = viewport <= 640
  useEffect(() => {
    const onResize = () => setViewport(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const renderLeadActions = (record: Lead) => (
    <div className={`lead-row-actions${compact && !mobile ? ' lead-row-actions-compact' : ''}`}>
      {/* 五个动作原来一样重（全是主色小字），扫一行看不出该点哪个。
          规则：一条线索当下只有一个"推进"动作 —— 还没人领就「领取」，
          已经在你名下就「跟进」；分配 / 转客户 / 释放是旁支，走文字按钮；
          「废弃」是终止动作，单独红色。 */}
      {!record.owner_id && record.status === 'pending' && (
        <Popconfirm
          title={`确认领取线索「${record.name}」？`}
          content="领取后你将成为这条线索的负责人。"
          onConfirm={() => claimMutation.mutate(record.id)}
          disabled={claimMutation.isPending}
        >
          <Button
            theme="borderless"
            type="primary"
            size="small"
            style={{ fontWeight: 600 }}
          >
            {claimMutation.isPending ? '领取中…' : '领取'}
          </Button>
        </Popconfirm>
      )}
      {/* 原来有归属时是 solid 主按钮：一屏三十行就是三十个白底描边框，
          远看像整列都是按钮（2026-11-21 巡检）。行内只留纯文字，
          当前这条线索的"推进"动作靠加粗区分，不靠框。 */}
      <Button
        theme="borderless"
        size="small"
        style={record.owner_id ? { fontWeight: 600, color: 'var(--crm-primary)' } : undefined}
        onClick={() => setFollowupTarget(record)}
      >
        跟进
      </Button>
      {can('lead:assign') && (
        <Button
          theme="borderless"
          size="small"
          onClick={() => {
            assignMutation.reset()
            setAssignTarget(record)
            setAssignTo(null)
          }}
        >
          分配
        </Button>
      )}
      {record.status !== 'converted' && can('lead:convert') && (
        <Button
          theme="borderless"
          size="small"
          onClick={() => openConvert(record)}
        >
          转客户
        </Button>
      )}
      {record.status === 'converted' && record.converted_customer_id && canViewCustomers && (
        <Button theme="borderless" size="small" onClick={() => navigate(`/customers/${record.converted_customer_id}`)}>
          查看客户
        </Button>
      )}
      {record.status === 'converted' && record.converted_opportunity_id && canViewOpportunities && (
        <Button theme="borderless" size="small" onClick={() => navigate(`/opportunities/${record.converted_opportunity_id}`)}>
          查看商机
        </Button>
      )}
      {record.owner_id && can('lead:assign') && (
        <Popconfirm
          title={record.status === 'converted' ? '解除线索负责人？' : '退回线索池？'}
          content={record.status === 'converted'
            ? `解除后，「${record.name}」仍是已转客户，客户和商机的归属保持原样。`
            : `当前负责人是${record.owner_name ?? '该线索经办人'}。退回后将进入待分配，其他有权限的员工可领取。`}
          okText={record.status === 'converted' ? '确认解除' : '确认退回'}
          cancelText="保留线索"
          disabled={releaseMutation.isPending}
          onConfirm={() => releaseMutation.mutate(record.id)}
        >
          <Button
            theme="borderless"
            size="small"
            aria-label={record.status === 'converted' ? '解除负责人' : '退回线索池'}
            title={record.status === 'converted' ? '解除这条线索的负责人关联' : '把这条线索退回线索池，其他人可以领取'}
          >
            {record.status === 'converted' ? '解除' : '退回'}
          </Button>
        </Popconfirm>
      )}
      {can('lead:assign') && (
        <Popconfirm title="确认废弃这条线索？" onConfirm={() => setDiscardTarget(record)}>
          <Button type="danger" theme="borderless" size="small">废弃</Button>
        </Popconfirm>
      )}
    </div>
  )

  const columns = [
    // 名称列收窄并允许省略：9 列加起来 1350，比内容区还宽，表格内部多出一条横向滚动条。
    // 170 → 200：12 字的线索名（「天津线索30【演示造数】」）实测要 178，
    // 170 时有 5px 溢出（2026-11-16 全站表格扫描）。
    {
      title: '线索名称',
      dataIndex: 'name',
      width: compact ? 140 : 200,
      ellipsis: !compact,
      render: (text: string, record: Lead) =>
        compact ? (
          <div style={{ minWidth: 0 }}>
            <div
              title={text}
              style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            >
              {text}
            </div>
            <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)' }}>
              {(record.contact_name ?? '-')} · {(record.owner_name ?? '未分配')}
            </div>
          </div>
        ) : (
          text
        ),
    },
    {
      title: '公司',
      dataIndex: 'company_name',
      /* 130 → 150：企业名叫「青岛电子有限公司」这种七到九字是常态，
         130 下整列全是「天津电子有限公…」，一列省略号等于没有这一列。
         从操作列里让出来的空间（见下）补给这里。 */
      width: compact ? 100 : 150,
      ellipsis: true,
      render: (v: string | null) => v ?? '-',
    },
    ...(compact
      ? []
      : [{ title: '联系人', dataIndex: 'contact_name', width: 80, render: (v: string | null) => v ?? '-' }]),
    // 手机号 11 位：130（2026-11-19 复测）—— 118 时公司列刚加到 150、操作列加到 240，
    // 整表被压，11 位数字又截成「138269363…」。130 减掉单元格左右 32 的内边距，
    // 剩 98 刚好装下 11 位等宽数字。
    { title: '手机', dataIndex: 'mobile', width: compact ? 126 : 130, render: (v: string | null) => <MaskedContact value={v} /> },
    /* 「来源」和「地区」各占一列、内容都只有两三个字。并成一列「来源 · 地区」：
       两列 190 变一列 150，信息一点没少，正好把表格从 1150 收回到内容区里。 */
    {
      title: compact ? '来源' : '来源 · 地区',
      dataIndex: 'source',
      /* 126 是实测的刚好值（2026-11-19）：内容是「客户介绍 · 华东」这种 4+3 字
         外加两个空格，约 103px，再减掉单元格左右 32 的内边距 —— 120 会截成
         「客户介绍 · 华…」，136 才完整。多出来的 16px 从名称列（弹性）让。 */
      width: compact ? 84 : 136,
      render: (_: string | null, record: Lead) =>
        [record.source, record.region].filter((v) => v && v !== '-').join(' · ') || '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: compact ? 96 : 96,
      render: (value: string, record: Lead) => (
        <StatusDot label={record.status_label} tone={(STATUS_COLOR[value] ?? 'grey') as TagTone} />
      ),
    },
    ...(compact
      ? []
      : [{
          title: '负责人',
          dataIndex: 'owner_name',
          width: 100,
          render: (v: string | null) => v ?? '未分配',
        }]),
    {
      title: '操作',
      // 操作最多同时出现五个文字链（领取/跟进/分配/转客户/释放/废弃里取五）。
      // 220 那会儿总宽 1140 > 内容区 1100：表格先挂横滚条，操作列又被压窄，
      // 于是「释放/废弃」折到第二行、行高一格一格不一样。
      // 现在把 公司 150→130、联系人 90→80、来源 150→110、状态 100→96 让出 74px，
      // 操作列仍是 220，五个链接一行放完、横滚条也不出现了（合计 1066 < 内容区 1070）。
      /* 220 → 240、链接间距 8 → 12：五个动作词（跟进 / 分配 / 转客户 / 释放 / 废弃）
         挤在 8px 里读成一串，"转客户"和"释放"尤其容易看混（2026-11-19 巡检）。 */
      width: compact ? 154 : 258,
      render: (_: unknown, record: Lead) => renderLeadActions(record),
    },
  ]

  const isFiltered = Boolean(keyword || status || onlyUnassigned)
  const leadEmpty = emptyNode(query, {
    title: isFiltered ? '没有符合条件的线索' : '还没有线索',
    hint: isFiltered ? undefined : can('lead:create') ? '点「新建线索」记下第一条。' : undefined,
    action: isFiltered ? (
      <Button size="small" theme="outline" onClick={() => {
        setKeywordInput('')
        setKeyword('')
        setStatus(undefined)
        setOnlyUnassigned(false)
        setPage(1)
      }}>清除筛选</Button>
    ) : undefined,
  })
  const pagination = {
    currentPage: page,
    pageSize,
    total: query.data?.total ?? 0,
    showSizeChanger: true,
    onPageChange: setPage,
    onPageSizeChange: (next: number) => { setPageSize(next); setPage(1) },
  }

  return (
    <div className="page-container">
      <PageHeader title="线索中心" subtitle="还在了解情况时，点「跟进」记录进展。确认对方是哪家客户后，再点「转客户」。" />

      <SectionCard className={mobile ? 'auto-height' : undefined}>
        {focusedLead ? (
          <div className="toolbar">
            <span>正在查看通知关联的线索</span>
            <Button size="small" theme="borderless" onClick={() => setSearchParams((previous) => {
              const next = new URLSearchParams(previous)
              next.delete('lead')
              return next
            })}>查看全部线索</Button>
          </div>
        ) : <div className="toolbar">
          <Input
            placeholder="搜索线索 / 公司 / 联系人 / 手机"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
            style={{ width: 260 }}
            showClear
          />
          <Select
            placeholder="状态"
            value={listStatus}
            disabled={onlyUnassigned}
            onChange={(value) => {
              setStatus(value as string | undefined)
              setPage(1)
            }}
            optionList={STATUS_OPTIONS}
            style={{ width: 130 }}
            showClear
          />
          <Checkbox
            checked={onlyUnassigned}
            onChange={(event) => {
              setOnlyUnassigned(Boolean(event.target.checked))
              setPage(1)
            }}
          >
            只看线索池
          </Checkbox>
          <Button
            type="tertiary"
            theme="borderless"
            onClick={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
          >
            查询
          </Button>
          <div style={{ flex: 1 }} />
          {can('lead:create') && (
            <Button type="primary" theme="outline" onClick={() => setCreateVisible(true)}>
              新建线索
            </Button>
          )}
        </div>}

        {releasedLead && (
          <Banner
            type="success"
            fullMode={false}
            description={releasedLead.status === 'pending'
              ? `「${releasedLead.name}」现在可以在线索池领取。`
              : `「${releasedLead.name}」仍是已转客户，${releasedLead.converted_opportunity_id ? '仍可查看关联客户和商机' : '仍保留关联客户'}。`}
            onClose={() => setReleasedLead(null)}
            style={{ marginBottom: 12 }}
          >
            {releasedLead.status === 'pending' && <Button size="small" theme="borderless" onClick={() => {
              setKeywordInput(releasedLead.name)
              setKeyword(releasedLead.name)
              setStatus('pending')
              setOnlyUnassigned(true)
              setPage(1)
              setReleasedLead(null)
            }}>在线索池查看</Button>}
            {releasedLead.status === 'converted' && releasedLead.converted_customer_id && canViewCustomers && (
              <Button size="small" theme="borderless" onClick={() => navigate(`/customers/${releasedLead.converted_customer_id}`)}>打开关联客户</Button>
            )}
          </Banner>
        )}
        {mobile ? (
          <div className="lead-mobile-list">
            {query.isLoading ? <p className="workflow-guide">正在读取线索…</p>
              : !query.data?.items.length ? leadEmpty
                : query.data.items.map(record => (
                    <article key={record.id} className="lead-mobile-record">
                      <div className="lead-mobile-record-heading">
                        <strong>{record.name}</strong>
                        <StatusDot label={record.status_label} tone={(STATUS_COLOR[record.status] ?? 'grey') as TagTone} />
                      </div>
                      <dl className="lead-mobile-details">
                        <div><dt>负责人</dt><dd>{record.owner_name ?? (record.owner_id ? '姓名暂不可用' : '未分配')}</dd></div>
                        {record.company_name && <div><dt>公司</dt><dd>{record.company_name}</dd></div>}
                        {record.contact_name && <div><dt>联系人</dt><dd>{record.contact_name}</dd></div>}
                        {record.mobile && <div><dt>手机</dt><dd><MaskedContact value={record.mobile} /></dd></div>}
                        {(record.source || record.region) && (
                          <div><dt>来源</dt><dd>{[record.source, record.region].filter(Boolean).join(' · ')}</dd></div>
                        )}
                      </dl>
                      {renderLeadActions(record)}
                    </article>
                  ))}
            {!focusedLead && pagination.total > 0 && (
              <div className="lead-mobile-pagination">
                <span>共 {pagination.total} 条</span>
                <Pagination {...pagination} showTotal={false} size="small" />
              </div>
            )}
          </div>
        ) : (
          <Table<Lead>
            className="leads-table"
            columns={columns}
            dataSource={query.data?.items ?? []}
            loading={query.isLoading}
            rowKey="id"
            size="middle"
            empty={leadEmpty}
            pagination={focusedLead ? false : pagination}
          />
        )}
      </SectionCard>

      <Modal
        title={
          <div className="modal-head">
            <span className="modal-head-icon" aria-hidden>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M5 21V4" /><path d="M5 4h10l-1.6 3L15 10H5" /><path d="M9 14h4v7h-4z" /></svg>
            </span>
            <div>
              新建线索
              <div className="modal-subtitle">名称必填；公司、联系人可以后补</div>
            </div>
          </div>
        }
        width={600}
        visible={can('lead:create') && (createVisible || openCreateFromUrl)}
        onCancel={closeCreate}
        onOk={() => {
          if (!form.name.trim()) {
            Toast.warning('线索名称必填')
            return
          }
          if (form.source === '其他渠道' && !form.source_detail.trim()) {
            Toast.warning('请填写其他渠道说明')
            return
          }
          createMutation.mutate()
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
      >
                <div className="form-cards">
          <section className="form-card">
            <div className="form-card-title">这条线索</div>
            <div className="form-card-grid">
              <div className="full">
                <div className="form-field-label">线索名称<span className="req">*</span></div>
                <Input
                  value={form.name}
                  onChange={(v) => setForm({ ...form, name: v })}
                  placeholder="客户要什么，例如：杭州电商仓周转箱需求"
                />
              </div>
              <div className="full">
                <div className="form-field-label">公司名称</div>
                <Input
                  value={form.company_name}
                  onChange={(v) => setForm({ ...form, company_name: v })}
                  placeholder="还没问到就先空着"
                />
              </div>
              <div className="full">
                <div className="form-field-label">备注</div>
                <TextArea
                  value={form.remark}
                  onChange={(v) => setForm({ ...form, remark: v })}
                  rows={2}
                  placeholder="需求细节、预算、时间点……"
                />
              </div>
            </div>
          </section>

          <section className="form-card">
            <div className="form-card-title">从哪来、找谁</div>
            <div className="form-card-grid">
              <div>
                <div className="form-field-label">来源</div>
                <Select
                  value={form.source}
                  onChange={(v) => setForm({
                    ...form,
                    source: v as string,
                    source_detail: v === '其他渠道' ? form.source_detail : '',
                  })}
                  optionList={SOURCE_OPTIONS}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <div className="form-field-label">省份 / 地区</div>
                <Input value={form.region} onChange={(v) => setForm({ ...form, region: v })} placeholder="浙江" />
              </div>
              {form.source === '其他渠道' && (
                <div className="full">
                  <div className="form-field-label">渠道说明<span className="req">*</span></div>
                  <Input
                    value={form.source_detail}
                    onChange={(v) => setForm({ ...form, source_detail: v })}
                    placeholder="请填写具体来源，例如：合作伙伴推荐、线下拜访"
                  />
                </div>
              )}
              <div>
                <div className="form-field-label">联系人</div>
                <Input value={form.contact_name} onChange={(v) => setForm({ ...form, contact_name: v })} />
              </div>
              <div>
                <div className="form-field-label">手机</div>
                <Input value={form.mobile} onChange={(v) => setForm({ ...form, mobile: v })} />
              </div>
              <div className="full form-tip">
                联系人和手机可以先记个大概，后面进到客户中心再补全。
              </div>
            </div>
          </section>
        </div>
      </Modal>

      <Modal
        width={520}
        title="分配线索"
        visible={Boolean(assignTarget)}
        onCancel={() => { if (!assignMutation.isPending) setAssignTarget(null) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={assignMutation.isPending} onClick={() => setAssignTarget(null)}>取消</Button>
            <GuardedButton
              theme="outline"
              disabled={Boolean(assignBlocked)}
              blockedReasons={assignBlocked ? [assignBlocked] : []}
              action="确认分配"
              hintBelow
              loading={assignMutation.isPending}
              onClick={() => {
                if (assignTarget && selectedAssignee && !assignBlocked) {
                  assignMutation.mutate({ leadId: assignTarget.id, ownerId: selectedAssignee.id })
                }
              }}
            >
              确认分配
            </GuardedButton>
          </div>
        }
      >
        <div className="receivable-picker">
          <div>
            <strong>{assignTarget?.name}</strong>
            <p className="receivable-picker-guide">
              当前负责人 {assignTarget?.owner_name ?? (assignTarget?.owner_id ? '姓名暂不可用' : '未分配')}
            </p>
          </div>
          <div>
            <FormLabel required>新负责人</FormLabel>
            <Select
              aria-label="新负责人"
              placeholder="搜索并选择在职员工"
              value={assignTo ?? undefined}
              onChange={(value) => { setAssignTo(Number(value)); assignMutation.reset() }}
              filter={optionMatcher}
              loading={usersQuery.isFetching}
              disabled={usersQuery.isError || assignMutation.isPending}
              optionList={availableAssignees.map((item) => ({
                value: item.id,
                label: `${item.name} · ${item.department ?? '未分配部门'} · ${item.username}`,
              }))}
              style={{ width: '100%' }}
            />
          </div>
          <p className="receivable-picker-guide">
            分配后由新负责人在线索中心继续跟进。
            {assignTarget?.owner_id && '要把线索退回线索池，可用列表里的退回操作。'}
          </p>
          {usersQuery.isError && (
            <Banner type="danger" fullMode={false} closeIcon={null} description="负责人名单暂时读不到。">
              <Button size="small" onClick={() => void usersQuery.refetch()}>重新读取</Button>
            </Banner>
          )}
          {assignMutation.error && (
            <Banner type="danger" fullMode={false} closeIcon={null} description={assignMutation.error.message} />
          )}
        </div>
      </Modal>

      <Modal
        width={520}
        title={`废弃线索：${discardTarget?.name ?? ''}`}
        visible={Boolean(discardTarget)}
        onCancel={() => setDiscardTarget(null)}
        onOk={() => {
          if (!discardReason.trim()) {
            Toast.warning('请填写废弃原因')
            return
          }
          discardMutation.mutate()
        }}
        confirmLoading={discardMutation.isPending}
        okText="确认废弃"
      >
        <Input
          value={discardReason}
          onChange={setDiscardReason}
          placeholder="例如：电话空号 / 无采购需求"
        />
      </Modal>

      <Modal
        title={`线索转客户：${convertTarget?.name ?? ''}`}
        visible={Boolean(convertTarget)}
        className="workflow-scroll-modal"
        onCancel={() => { if (!convertMutation.isPending) setConvertTarget(null) }}
        onOk={() => { if (!convertDisabled) convertMutation.mutate() }}
        okButtonProps={{ disabled: convertDisabled }}
        confirmLoading={convertMutation.isPending}
        okText={customerMode === 'existing' ? '关联客户' : '创建客户'}
        width="min(520px, calc(100vw - 24px))"
      >
        <div style={{ display: 'grid', gap: 16 }}>
          <div>
            <div style={{ marginBottom: 8 }}>这家公司在 CRM 里有档案吗？</div>
            <RadioGroup
              value={customerMode}
              onChange={(event) => setCustomerMode(event.target.value as 'new' | 'existing')}
            >
              <Radio value="new">创建新客户</Radio>
              <Radio value="existing">关联已有客户</Radio>
            </RadioGroup>
            <p className="workflow-guide">
              {customerMode === 'new' ? '确认是新客户时，创建一份客户档案。' : '已建档的客户直接关联，避免新增重复档案。'}
            </p>
            {candidatesQuery.isLoading && <p className="workflow-guide">正在查找疑似客户…</p>}
            {candidatesQuery.isSuccess && candidates.length > 0 && (
              <p className="workflow-guide">查到 {candidates.length} 个疑似客户，建议先打开「关联已有客户」核对。</p>
            )}
            {candidatesQuery.isError && (
              <p className="workflow-guide" role="alert">
                暂时查不到疑似客户。<Button theme="borderless" onClick={() => void candidatesQuery.refetch()}>重试查重</Button>
              </p>
            )}
          </div>

          {customerMode === 'existing' && (
            <div>
              <p className="workflow-guide">{canViewCustomers
                ? '可选查重建议里的客户，也可输入客户名称搜索。'
                : '可选择查重建议里的客户。手动搜索需要客户查看权限。'}</p>
              <Select
                placeholder={canViewCustomers ? '输入客户名称或选择查重建议' : '选择查重建议'}
                value={existingCustomer?.value}
                onChange={(value) => setExistingCustomer(customerOptions.find((option) => option.value === value) ?? null)}
                optionList={customerOptions}
                filter={canViewCustomers}
                remote={canViewCustomers}
                onSearch={canViewCustomers ? setCustomerSearch : undefined}
                loading={customerKeyword ? customerSearchQuery.isFetching : candidatesQuery.isFetching}
                outerBottomSlot={customerKeyword && customerSearchQuery.isError ? (
                  <Button theme="borderless" onClick={() => {
                    if (customerSearchQuery.isFetchNextPageError) void customerSearchQuery.fetchNextPage()
                    else void customerSearchQuery.refetch()
                  }}>搜索失败，点击重试</Button>
                ) : customerSearchQuery.hasNextPage ? (
                  <Button theme="borderless" loading={customerSearchQuery.isFetchingNextPage} onClick={() => {
                    if (!customerSearchQuery.isFetching && customerKeyword === customerSearch.trim()) {
                      void customerSearchQuery.fetchNextPage()
                    }
                  }}>加载更多客户</Button>
                ) : undefined}
                style={{ width: '100%' }}
              />
            </div>
          )}

          <div>
            <Checkbox
              checked={createOpportunity}
              onChange={(event) => setCreateOpportunity(Boolean(event.target.checked))}
            >
              同时创建商机
            </Checkbox>
            <p className="workflow-guide">已经谈到一笔具体采购时再勾选。暂时只想把客户建档，可以以后从客户详情新建商机。</p>
            {createOpportunity && (
              <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
                <Input
                  value={opportunityTitle}
                  onChange={setOpportunityTitle}
                  placeholder="商机名称，例如：周转箱年度采购"
                />
                <Input
                  value={expectedAmount}
                  onChange={setExpectedAmount}
                  placeholder="预计金额（元）"
                  aria-invalid={invalidOpportunityAmount}
                />
                {invalidOpportunityAmount && <div role="alert" style={{ color: 'var(--crm-error)', fontSize: 12 }}>
                  预计金额请填大于或等于 0 的数字，单位为元。
                </div>}
                <DatePicker
                  value={expectedCloseDate ?? undefined}
                  onChange={(date) => setExpectedCloseDate((date as Date) ?? null)}
                  placeholder="预计成交日期（选填）"
                  style={{ width: '100%' }}
                />
              </div>
            )}
          </div>

          <p className="workflow-guide">提交后，这条线索会标记为「已转客户」。已有联系人信息会带入客户档案。</p>
        </div>
      </Modal>

      <Modal
        title={conversionReceipt?.customerMode === 'existing' ? '客户已关联，商机已创建' : '客户和商机已创建'}
        visible={Boolean(conversionReceipt)}
        width="min(460px, calc(100vw - 24px))"
        onCancel={() => setConversionReceipt(null)}
        footer={conversionReceipt && (
          <div className="lead-conversion-actions">
            <Button size="small" theme="borderless" onClick={() => setConversionReceipt(null)}>留在线索</Button>
            {canViewCustomers && <Button size="small" theme="outline" onClick={() => navigate(`/customers/${conversionReceipt.customerId}`)}>查看客户</Button>}
            <Button size="small" type="primary" theme="solid" onClick={() => navigate(`/opportunities/${conversionReceipt.opportunityId}`)}>查看商机</Button>
          </div>
        )}
      >
        <p>线索已标记为「已转客户」。{canViewCustomers
          ? '可以打开商机继续跟进采购需求，或到客户档案核对联系人。'
          : '可以打开商机继续跟进采购需求。'}</p>
      </Modal>

      <FollowUpModal
        key={followupTarget?.id}
        visible={Boolean(followupTarget)}
        onClose={() => setFollowupTarget(null)}
        target={{ leadId: followupTarget?.id, customerId: followupTarget?.converted_customer_id ?? undefined }}
        onCreated={refresh}
      />
    </div>
  )
}
