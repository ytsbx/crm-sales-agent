import { formatBeijingDate, formatBeijingTime } from '../../shared/beijing-time'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Banner,
  Button,
  Input,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tag,
  Toast,
  TextArea,
} from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import { useAuthStore } from '../../shared/store/auth'
import SectionCard from '../../shared/components/SectionCard'
import PageHeader from '../../shared/components/PageHeader'
import FormLabel from '../../shared/components/FormLabel'
import { emptyNode } from '../../shared/components/EmptyState'
import {
  createCase,
  deleteCaseDraft,
  getCase,
  listCases,
  reviseCase,
  reviewCase,
  submitCase,
  updateCase,
  withdrawPublishedCase,
  type CaseEvidenceItem,
  type CaseRow,
} from '../../shared/api/cases'
import { listQuotes } from '../../shared/api/quote'
import { listOrders } from '../../shared/api/order'
import { listSamples } from '../../shared/api/sample'
import { getCustomer, listCustomers } from '../../shared/api/customer'
import { getCustomerTimeline } from '../../shared/api/followup'
import { optionMatcher } from '../../shared/components/optionMatch'
import RecordSearchSelect from '../../shared/components/RecordSearchSelect'

const EMPTY_FILTERS = {
  keyword: '',
  status: undefined as string | undefined,
  // 第四批 §5.1.6：下面这几个筛选此前只是前端没接通，
  // 看起来像"只能按关键词和状态搜"。
  industry: undefined as string | undefined,
  product_line: undefined as string | undefined,
  stage: undefined as string | undefined,
  problem_tags: undefined as string | undefined,
  // **客户类型**（企业/个人）：取自关联客户，与「行业」不是一回事。
  // 方案 §3.7 的检索维度是"客户类型、产品线、阶段及问题"——
  // 之前拿「行业」顶替了客户类型，两个筛选合一，等于少一个维度。
  customer_type: undefined as string | undefined,
}

const STAGES = ['understanding', 'quote', 'sample', 'first_order', 'repeat', 'stable']
/** 证据类别 → 详情页的跳转路径（点开原单核对）。 */
const EVIDENCE_PATH: Record<string, string> = {
  quote: '/quotes/',
  order: '/orders/',
  sample: '/samples/',
  opportunity: '/opportunities/',
}

const STAGE_LABEL: Record<string, string> = {
  understanding: '了解', quote: '报价', sample: '打样',
  first_order: '首单', repeat: '返单', stable: '稳定复购',
}
const STATUS_TONE: Record<string, 'green' | 'grey' | 'orange' | 'red'> = {
  published: 'green', draft: 'grey', pending_review: 'orange', rejected: 'red',
  // 被修订版取代的旧版：**仍可读、只是不能再改**（§5.1.5），所以是中性灰不是"错"的红
  superseded: 'grey', withdrawn: 'grey',
}

const EMPTY_FORM = {
  title: '',
  customer_id: undefined as number | undefined,
  // 旧字段保留（后端仍下发、仍接受），但界面已改成证据列表：
  // 打开老案例时用 `evidences` 回填，提交时传 `evidences`。
  quote_id: undefined as number | undefined,
  order_id: undefined as number | undefined,
  sample_id: undefined as number | undefined,
  /** 证据单据列表（**可以多条**，§3.7：一个案例常由多张单据支撑） */
  evidences: [] as CaseEvidenceItem[],
  customer_label: '',
  industry: '',
  product_line: '',
  stage_reached: undefined as string | undefined,
  problem_tags: '',
  background: '',
  goal: '',
  key_actions: '',
  objection_handling: '',
  process: '',
  result: '',
  lessons: '',
}

export default function CasesPage() {
  /* 1024 宽内容区约 700px，案例表七列合计 1026px，「作者 / 行业/产品线 / 操作」
     整块被裁到卡片外、底部常驻横向滚动条。窄屏切紧凑档：作者和行业/产品线
     并到标题第二行小字。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const queryClient = useQueryClient()
  const { can, isReviewer: hasReviewerRole } = usePermissions()
  const isReviewer = hasReviewerRole || can('settings:manage')
  // 「开修订稿」后端只放行**作者或主管**（其余人 403）。前端据此显隐按钮，
  // 免得每个人点一下都被拒——真正的拦截仍在后端。
  const currentUserId = useAuthStore((state) => state.user?.id)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [includeHistory, setIncludeHistory] = useState(false)
  //: 「按单据挑」那个下拉当前选中的值（格式 `kind:id`），点「添加」才进证据列表
  const [evidencePick, setEvidencePick] = useState<string | undefined>(undefined)

  // 改筛选条件要回到第 1 页：否则筛完还停在第 5 页，翻出来是空的，
  // 看着像"没有符合条件的案例"，其实只是页码越界了。
  const applyFilter = (patch: Partial<typeof filters>) => {
    setFilters((current) => ({ ...current, ...patch }))
    setPage(1)
  }
  const resetFilters = (status?: string) => {
    setFilters({ ...EMPTY_FILTERS, status })
    setIncludeHistory(false)
    setPage(1)
  }
  const hasOtherFilters = includeHistory || Object.entries(filters).some(([key, value]) => key !== 'status' && Boolean(value))
  const hasFilters = hasOtherFilters || Boolean(filters.status)
  const onlyPendingReview = filters.status === 'pending_review' && !hasOtherFilters

  const casesQuery = useQuery({
    queryKey: ['cases', filters, page, pageSize, includeHistory],
    queryFn: () =>
      listCases({ ...filters, page, page_size: pageSize, include_history: includeHistory }),
  })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['cases'] })
  }

  const [editVisible, setEditVisible] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  // 正在编辑的那一行本身：弹窗左下要不要给「删除这份案例」，得看它的状态和作者，
  // 光有 id 判不了（主人 2026-11-21「这个难道没有删除的地方吗」—— 打开弹窗想删，找不到入口）
  const [editMeta, setEditMeta] = useState<CaseRow | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [detail, setDetail] = useState<CaseRow | null>(null)
  // 驳回意见改成**真弹窗填写**。原先写死一句"请补充关键动作与可复用做法"，
  // 于是不管案例哪里不合格，作者收到的都是同一句话，照着改根本不知道改什么（返工单第 7 条）。
  const [reviewModal, setReviewModal] = useState<{ visible: boolean; id?: number }>({
    visible: false,
  })
  const [reviewNote, setReviewNote] = useState('')

  // 打开详情时**重新拉一次详情接口**，而不是直接拿列表那条记录渲染。
  // 列表与详情是两套序列化，历史上就漂移过（列表标题带着客户全称、详情已换成代称），
  // 而详情直接用列表记录打开会把这种漂移放大成"分享版泄露客户身份"（§5.1.1）。
  // 先用列表记录占位避免弹窗空一下，拿到详情后覆盖。
  const openDetail = async (row: CaseRow) => {
    setDetail(row)
    try {
      const full = await getCase(row.id)
      // 期间用户可能已经关掉或换了另一条，只认仍然是同一条的那个响应
      setDetail((current) => (current && current.id === row.id ? full : current))
    } catch {
      // 拉详情失败就保持列表那份（它同样已按分享口径脱敏），不把弹窗关掉
    }
  }

  /** 只按 id 拉一条详情（用于旧版的"查看当前版本"跳转）。
   *
   * 不复用 `openDetail`：那个函数会先 `setDetail(row)`，传一个只有 id 的假对象进去，
   * 界面会先渲染一片空白、等接口回来才填上，看着像闪了一下。
   * 这里直接等结果再设，失败就明说——不静默，否则用户点了没反应更困惑。
   */
  const openDetailById = async (id: number) => {
    try {
      setDetail(await getCase(id))
    } catch {
      Toast.error('打不开这一版：可能已删除，或你没有查看权限')
    }
  }

  // 证据单据（§3.7）：选了客户才去拉这个客户的报价/订单/打样。
  // 同一客户下单据可能超过一页，循环取全，避免第 101 条以后选不到。
  const evidenceCustomerId = editVisible ? form.customer_id : undefined
  const fetchAll = async <T extends { id: number }>(
    load: (page: number) => Promise<{ items: T[]; total: number; page: number; page_size: number }>,
  ) => {
    const first = await load(1)
    const all = [...first.items]
    for (let page = 2; all.length < first.total; page += 1) {
      const next = await load(page)
      if (!next.items.length) break
      all.push(...next.items)
    }
    return { ...first, items: all }
  }
  const quotesQuery = useQuery({
    queryKey: ['case-quotes', evidenceCustomerId],
    queryFn: () => fetchAll((page) => listQuotes({ customer_id: evidenceCustomerId, page, page_size: 100 })),
    enabled: Boolean(evidenceCustomerId),
  })
  const ordersQuery = useQuery({
    queryKey: ['case-orders', evidenceCustomerId],
    queryFn: () => fetchAll((page) => listOrders({ customer_id: evidenceCustomerId, page, page_size: 100 })),
    enabled: Boolean(evidenceCustomerId),
  })
  const samplesQuery = useQuery({
    queryKey: ['case-samples', evidenceCustomerId],
    queryFn: () => fetchAll((page) => listSamples({ customer_id: evidenceCustomerId, page, page_size: 100 })),
    enabled: Boolean(evidenceCustomerId),
  })
  // 表单里那份时间线（「从时间线挑」用）：跟着表单选的客户走
  const formTimelineQuery = useQuery({
    queryKey: ['case-form-timeline', evidenceCustomerId],
    queryFn: () => getCustomerTimeline(evidenceCustomerId!),
    enabled: Boolean(evidenceCustomerId),
  })

  /** 把 `kind:id` 加进证据列表（已加过的忽略，后端也有唯一约束兜底）。 */
  const addEvidence = (token: string) => {
    const [kind, rawId] = token.split(':')
    const businessId = Number(rawId)
    if (!kind || !Number.isFinite(businessId)) return
    const labels: Record<string, string> = {
      quote: '报价单',
      order: '销售订单',
      sample: '打样单',
      opportunity: '商机',
    }
    setForm((current) => {
      if (
        current.evidences.some(
          (row) => row.kind === kind && row.business_id === businessId,
        )
      ) {
        Toast.info('这条已经挂上了')
        return current
      }
      return {
        ...current,
        evidences: [
          ...current.evidences,
          {
            kind,
            kind_label: labels[kind] ?? kind,
            business_id: businessId,
            label: null,
          },
        ],
      }
    })
  }

  const openCreate = () => {
    setEditId(null)
    // 关键：不清的话，新建弹窗左下会挂着上一条案例的「删除这份案例」——
    // 点下去删的是别人那条，比没有入口严重得多。
    setEditMeta(null)
    setForm(EMPTY_FORM)
    setEditVisible(true)
  }
  const openEdit = (row: CaseRow) => {
    setEditId(row.id)
    setEditMeta(row)
    setForm({
      ...EMPTY_FORM,
      title: row.title,
      customer_id: row.customer_id ?? undefined,
      quote_id: row.quote_id ?? undefined,
      order_id: row.order_id ?? undefined,
      sample_id: row.sample_id ?? undefined,
      customer_label: row.customer_label ?? '',
      industry: row.industry ?? '',
      product_line: row.product_line ?? '',
      stage_reached: row.stage_reached ?? undefined,
      problem_tags: (row.problem_tags ?? []).join('，'),
      background: row.background ?? '',
      goal: row.goal ?? '',
      key_actions: row.key_actions ?? '',
      objection_handling: row.objection_handling ?? '',
      process: row.process ?? '',
      result: row.result ?? '',
      lessons: row.lessons ?? '',
      // 回填证据列表：**以 `evidences` 为准**（多条），旧的四列只作为兜底 ——
      // 老案例还没跑迁移时后端仍会从旧列补出来，这里两种都能接住。
      evidences:
        row.evidences && row.evidences.length > 0
          ? row.evidences
          : ([
              ['quote', row.quote_id],
              ['order', row.order_id],
              ['sample', row.sample_id],
              ['opportunity', row.opportunity_id],
            ] as [string, number | null][])
              .filter(([, id]) => id != null)
              .map(([kind, id]) => ({
                kind,
                kind_label:
                  { quote: '报价单', order: '销售订单', sample: '打样单', opportunity: '商机' }[
                    kind
                  ] ?? kind,
                business_id: id as number,
                label: null,
              })),
    })
    setEditVisible(true)
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        ...form,
        problem_tags: form.problem_tags
          .split(/[,，]/)
          .map((tag) => tag.trim())
          .filter(Boolean),
        // 证据以列表形式提交（后端会整体替换 `case_evidences` 并同步旧列）
        evidences: form.evidences.map((item) => ({
          kind: item.kind,
          business_id: item.business_id,
          label: item.label ?? null,
          note: item.note || null,
        })),
      }
      return editId ? updateCase(editId, payload) : createCase(payload)
    },
    onSuccess: () => {
      Toast.success('案例已保存')
      setEditVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const submitMutation = useMutation({
    mutationFn: (id: number) => submitCase(id),
    onSuccess: () => {
      Toast.success(isReviewer
        ? '已提交审核。在「案例库」筛选「待审核」，可对这条案例点「通过」或「驳回」。'
        : '已提交审核。请有审核权限的同事在「案例库」筛选「待审核」处理。')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const reviewMutation = useMutation({
    // note 由调用方给：通过时留空即可，**驳回时必须是主管自己写的那句话**。
    // 原来这里写死一句「请补充关键动作与可复用做法」，不管哪里不合格都是同一句，
    // 作者照着改也不知道改什么。
    mutationFn: ({ id, approve, note }: { id: number; approve: boolean; note?: string }) =>
      reviewCase(id, { approve, note }),
    onSuccess: (result) => {
      Toast.success(result.status === 'published' ? '已发布' : '已驳回')
      setReviewModal({ visible: false })
      setReviewNote('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const deleteMutation = useMutation({
    mutationFn: deleteCaseDraft,
    onSuccess: (_, id) => {
      Toast.success('案例已删除')
      setDetail((current) => current?.id === id ? null : current)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const withdrawMutation = useMutation({
    mutationFn: withdrawPublishedCase,
    onSuccess: () => {
      Toast.success('案例已撤下，历史已保留')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  // 开修订稿（§5.1.5）：已发布的那一版**只读**，要改就复制一份新的重新走审核。
  // 后端做了幂等——同一原版最多一份在途修订稿，重复点不会建出一堆 V2。
  const reviseMutation = useMutation({
    mutationFn: (id: number) => reviseCase(id),
    onSuccess: (result) => {
      const version = `第 ${result.version ?? 2} 版`
      Toast.success(result.status === 'pending_review'
        ? `${version}正在等审核。通过前，原版仍可供培训。`
        : `新版草稿（${version}）已就绪。请在列表找到这版，点「编辑」，改完再点「提交审核」。`)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <div className="page-container">
      {/* 案例库原来没有页头（2026-11-17 视觉审视 D-03）：从侧栏点进来，
          屏幕最上面直接就是搜索框，不知道自己在哪个模块，也没有全局动作的位置。
          补一个标准页头：标题 + 一句"这些案子能干什么"的副标题 + 右上新建。
          新建按钮从工具条挪到页头右上，工具条只留筛选。 */}
      <PageHeader
        title="案例库"
        subtitle="审核通过的案例才能拿去培训；编写中与待审核的只有相关人能看见"
        extra={
          can('quote:view') && (
            <Button type="primary" theme="outline" onClick={openCreate}>
              新建案例
            </Button>
          )
        }
      />
      <SectionCard
        className={(casesQuery.data?.items?.length ?? 0) <= 8 ? 'auto-height' : undefined}
      >
        <div className="toolbar" style={{ marginBottom: 10 }}>
          {isReviewer && !onlyPendingReview && (
            <Button theme="outline" onClick={() => resetFilters('pending_review')}>查看待审核</Button>
          )}
          {/* 七个筛选控件原来宽度参差（190 / 105 / 100 / 105 / 100 / 100 / 100），
              一线排开像随手摆的；统一成 210 + 6×108 后左边缘对齐、节奏一致
              （2026-11-19 巡检）。 */}
          {/* 工具栏是 flex + wrap，子项默认可以被压：7 个控件设了 108 宽，
              实际渲染成 95 / 115 / 78 / 80 / 64 / 74 —— 一排在视觉上仍是参差
              （2026-11-21 截图核对）。加 flexShrink:0，宽度才真是设定的宽度。 */}
          <Input
            style={{ width: 210, flexShrink: 0 }}
            placeholder="搜标题 / 做法 / 关键动作"
            value={filters.keyword}
            onChange={(v) => applyFilter({ keyword: v })}
          />
          <Select
            style={{ width: 108, flexShrink: 0 }}
            placeholder="状态"
            value={filters.status}
            onChange={(v) => applyFilter({ status: v as string | undefined })}
            optionList={[
              { value: 'published', label: '已发布' },
              { value: 'pending_review', label: '待审核' },
              { value: 'draft', label: '编写中' },
              { value: 'rejected', label: '已驳回' },
              ...(includeHistory ? [{ value: 'withdrawn', label: '已撤下' }] : []),
            ]}
            showClear
          />
          {/* 行业/产品线是自由文本，用输入框（后端按包含匹配，不用打全称） */}
          <Input
            style={{ width: 108, flexShrink: 0 }}
            placeholder="行业"
            value={filters.industry ?? ''}
            onChange={(v) => applyFilter({ industry: v || undefined })}
          />
          {/* 客户类型：企业 / 个人。取自关联客户档案，
              与上面那个「行业」是两件事（一个企业客户可以属于任何行业）。 */}
          <Select
            style={{ width: 108, flexShrink: 0 }}
            placeholder="客户类型"
            value={filters.customer_type}
            showClear
            onChange={(v) => applyFilter({ customer_type: (v as string) || undefined })}
            optionList={[
              { value: '企业', label: '企业' },
              { value: '个人', label: '个人' },
            ]}
          />
          <Input
            style={{ width: 108, flexShrink: 0 }}
            placeholder="产品线"
            value={filters.product_line ?? ''}
            onChange={(v) => applyFilter({ product_line: v || undefined })}
          />
          <Select
            style={{ width: 108, flexShrink: 0 }}
            placeholder="阶段"
            value={filters.stage}
            onChange={(v) => applyFilter({ stage: v as string | undefined })}
            optionList={STAGES.map((s) => ({ value: s, label: STAGE_LABEL[s] }))}
            showClear
          />
          <Input
            style={{ width: 108, flexShrink: 0 }}
            placeholder="问题标签"
            value={filters.problem_tags ?? ''}
            onChange={(v) => applyFilter({ problem_tags: v || undefined })}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
            <input
              type="checkbox"
              checked={includeHistory}
              onChange={(e) => {
                setIncludeHistory(e.target.checked)
                if (!e.target.checked && filters.status === 'withdrawn') applyFilter({ status: undefined })
                setPage(1)
              }}
            />
            <span title="同时查看已被取代与已撤下的版本">
              含历史版本
            </span>
          </div>
          {/* 分页条在"一页装得下"时会收起，条数总得有个地方交代：
              推到工具栏最右，跟搜索框同一行的灰字，位置固定不跳。 */}
          {((casesQuery.data?.total ?? 0) > 0) && (
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)', marginLeft: 'auto', flexShrink: 0 }}>
              共 {casesQuery.data?.total ?? 0} 条
            </span>
          )}
        </div>
        <Table<CaseRow>
          className="cases-table"
          columns={[
            {
              title: '标题',
              dataIndex: 'title',
              /* 原来是"超过 22 个字就切掉"：这个 22 是按当年列宽估的，
                 字宽一变就切得多余或不够；标题短一点明明放得下也被切。
                 改成给这一列定宽 + CSS 省略号 —— 有多少空间就显示多少，
                 悬停看全文。 */
              width: compact ? 232 : 316,
              render: (v: string, record: CaseRow) => (
                <div style={{ minWidth: 0 }}>
                  {/* 标题本身就是进详情的入口：原来标题是死的，要读到它讲什么
                      得先跑到最右边点「详情」——同一个动作两个位置。
                      现在标题可点（蓝色 = 可跳转，与全站表格链接一致），
                      操作列里的「详情」随之去掉，那里只留真正的动作。 */}
                  <a
                    href="#"
                    title={v}
                    onClick={(e) => {
                      e.preventDefault()
                      void openDetail(record)
                    }}
                    /* 标题是这份案例的主体，原来卡在 300px 单行、一律省略成
                       「…毛利只…」，看不出讲的是什么。宽屏放开成最多两行：
                       316px 一列大概两行就能装下，悬停仍可看全文。 */
                    style={{
                      display: 'block',
                      overflow: 'hidden',
                      textOverflow: compact ? 'ellipsis' : 'clip',
                      whiteSpace: compact ? 'nowrap' : 'normal',
                      lineHeight: 1.45,
                    }}
                  >
                    {v}
                  </a>
                  {compact && (
                    <div
                      style={{
                        marginTop: 2,
                        fontSize: 11,
                        color: 'var(--crm-text-3)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {[record.author_name, record.industry, record.product_line]
                        .filter(Boolean)
                        .join(' · ') || '-'}
                    </div>
                  )}
                </div>
              ),
            },
            // 列顺序按「看这份案例时要先判断什么」排：给哪个客户做的、审到哪一步了，
            // 比"谁写的"重要得多。原来作者排第 2，客户和状态被挤到第 6、第 7 位，
            // 一屏扫过去先看到作者名，而不是能不能用。
            {
              title: '客户',
              width: compact ? 108 : 146,
              render: (_: unknown, record: CaseRow) => record.customer_name ?? record.customer_label ?? '-',
            },
            {
              title: '状态',
              dataIndex: 'status_label',
              width: compact ? 100 : 108,
              render: (v: string, record: CaseRow) => (
                // 状态列统一左对齐、版本位预留固定宽度：有没有 V2，状态胶囊都从
                // 同一 x 坐标起；V2 也不能把「编写中」往左挤（2026-11-25 实拍）。
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', minWidth: 0 }}>
                  <Tag color={STATUS_TONE[record.status] ?? 'grey'}>{v}</Tag>
                  <span style={{
                    flex: '0 0 22px', width: 22, fontSize: 11, lineHeight: '20px',
                    color: 'var(--crm-text-3)', whiteSpace: 'nowrap',
                  }}>
                    {(record.version ?? 1) > 1 ? `V${record.version}` : null}
                  </span>
                </span>
              ),
            },
            {
              title: '阶段',
              dataIndex: 'stage_reached',
              width: compact ? 92 : 86,
              render: (v: string | null) => (v ? STAGE_LABEL[v] ?? v : '-'),
            },
            ...(compact ? [] : [{ title: '作者', dataIndex: 'author_name', width: 78 }]),
            // 行业 / 产品线合成一列：两者都是"这条经验适合什么场景"的分类标签，
            // 分开占两列各 100 宽，把标题挤得只剩 300；合并成一列省下 100。
            ...(compact
              ? []
              : [{
                  title: '行业 / 产品线',
                  width: 132,
                  render: (_: unknown, record: CaseRow) => {
                    const parts = [record.industry, record.product_line].filter(Boolean)
                    return parts.length ? parts.join(' · ') : '-'
                  },
                }]),
            {
              title: '操作',
              width: compact ? 150 : 190,
              /* 原来是一排蓝色小字（详情 / 编辑 / 提交审核 / 开修订稿 / 通过 / 驳回 / 撤下），
                 七个动作同重量，只有提交和通过点了之后会改变状态，却和"编辑"长得一模一样。
                 现在：标题已经可点（详情去掉）；会推进状态的两个动作走主按钮（提交审核 / 通过），
                 驳回与撤下走红色文字按钮，编辑和开修订稿走普通文字按钮。 */
              render: (_: unknown, record: CaseRow) => (
                <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'center' }}>
                  {(record.status === 'draft' || record.status === 'rejected') && (
                    <>
                      <Button theme="borderless" size="small" onClick={() => openEdit(record)}>
                        编辑
                      </Button>
                      <Popconfirm title="确认提交这份案例审核？" onConfirm={() => submitMutation.mutate(record.id)}>
                        {/* 2026-11-20：列表行内的动作不该有"主按钮"——
                            一行里的「提交审核」白底描边，旁边三个是纯蓝字，
                            看着像那一行只有这一个能点。整列统一文字按钮。 */}
                        <Button type="tertiary" theme="borderless" size="small">提交审核</Button>
                      </Popconfirm>
                    </>
                  )}
                  {(record.status === 'draft' || record.status === 'rejected') &&
                    (isReviewer || record.author_id === currentUserId) && (
                      <Popconfirm
                        title="删除这份案例？"
                        content={`「${record.title}」将从案例库移除。`}
                        okText="删除"
                        onConfirm={() => deleteMutation.mutate(record.id)}
                      >
                        <Button type="danger" theme="borderless" size="small" disabled={deleteMutation.isPending}>
                          删除
                        </Button>
                      </Popconfirm>
                    )}
                  {/* 只有**当前发布版**能开修订稿。
                      ⚠️ 已被取代的旧版**不给**这个入口（实测确认过的坑）：
                      从 V1 开出来的草稿取的是 V1 的旧内容、版本号还会跟已发布的 V2 撞号，
                      审核通过后就是"用旧内容覆盖新版本"——把改进过的内容退回旧版。
                      旧版仍然读得到（见上面的详情弹窗），只是不能从它派生新版本。 */}
                  {record.status === 'published' &&
                    (isReviewer || record.author_id === currentUserId) && (
                      <Popconfirm
                        title="修改已发布的案例？"
                        content="系统会准备一份新版草稿。编辑并提交审核后，新版通过才会替换当前发布版。在此期间，大家仍可查看和使用原版。"
                        okText="准备新版草稿"
                        onConfirm={() => reviseMutation.mutate(record.id)}
                      >
                        <Button theme="borderless" size="small">修改案例</Button>
                      </Popconfirm>
                    )}
                  {isReviewer && record.status === 'pending_review' && (
                    <>
                      {/* 审核通过会把案例发布出去、成为别人能引用的基线，不可逆，须确认。
                          「驳回」本来就走原因弹窗，那条不用加。 */}
                      <Popconfirm
                        title="通过这份案例的审核？"
                        content="通过后案例立即发布，可以用于培训。以后可通过「修改案例」提交新版。"
                        onConfirm={() => reviewMutation.mutate({ id: record.id, approve: true })}
                      >
                        <Button type="tertiary" theme="borderless" size="small">通过</Button>
                      </Popconfirm>
                      <Button
                        type="danger"
                        theme="borderless"
                        size="small"
                        onClick={() => {
                          setReviewNote('')
                          setReviewModal({ visible: true, id: record.id })
                        }}
                      >
                        驳回
                      </Button>
                    </>
                  )}
                  {isReviewer && (record.status === 'published' || record.status === 'superseded') && (
                    <Popconfirm
                      title="撤下这个案例版本？"
                      content="撤下后退出学习列表，作者和审核人仍可查看历史版本与审核记录。"
                      onConfirm={() => withdrawMutation.mutate(record.id)}
                    >
                      <Button type="danger" theme="borderless" size="small">撤下</Button>
                    </Popconfirm>
                  )}
                  {record.status === 'pending_review' && !isReviewer && (
                    <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>等待有权限的同事审核</span>
                  )}
                </span>
              ),
            },
          ]}
          dataSource={casesQuery.data?.items ?? []}
          loading={casesQuery.isLoading}
          rowKey="id"
          /* 12 条挂「显示第 1-12 条 / 翻页 / 每页条数」只是噪音：
             总数不过一页就收起分页条，条数交给上方工具栏的灰字。 */
          pagination={(casesQuery.data?.total ?? 0) <= pageSize ? false : {
            currentPage: page,
            pageSize,
            total: casesQuery.data?.total ?? 0,
            onPageChange: setPage,
            showSizeChanger: true,
            pageSizeOpts: [10, 20, 50, 100],
            onPageSizeChange: (size: number) => { setPageSize(size); setPage(1) },
          }}
          empty={emptyNode(casesQuery, {
            title: onlyPendingReview ? '暂无待审核案例' : hasFilters ? '没有符合筛选条件的案例' : '还没有案例',
            hint: hasFilters ? undefined : '可以把一次有效的做法整理成案例，提交审核后供团队学习。',
            action: hasFilters ? <Button size="small" onClick={() => resetFilters()}>查看全部案例</Button>
              : can('quote:view') ? <Button size="small" onClick={openCreate}>新建案例</Button> : undefined,
          })}
        />
      </SectionCard>

      <Modal
        title="驳回案例"
        visible={reviewModal.visible}
        onCancel={() => setReviewModal({ visible: false })}
        onOk={() => {
          // 理由必填：作者要照着这句话改，空着等于让他猜
          if (!reviewNote.trim()) {
            Toast.error('请写清驳回原因：作者要照着它改')
            return
          }
          reviewMutation.mutate({
            id: reviewModal.id as number,
            approve: false,
            note: reviewNote.trim(),
          })
        }}
        confirmLoading={reviewMutation.isPending}
        okText="驳回"
        cancelText="取消"
        width={520}
      >
        <div style={{ display: 'grid', gap: 8 }}>
          <FormLabel required hint="作者会看到这段话，请说清哪里不合格、要补什么">
            驳回原因
          </FormLabel>
          <TextArea
            rows={4}
            value={reviewNote}
            onChange={setReviewNote}
            placeholder="例如：关键动作只写了结果，看不到具体怎么谈的；证据单据挂错了订单"
          />
        </div>
      </Modal>

      <Modal
        title={editId ? '编辑案例' : '新建案例'}
        visible={editVisible}
        onCancel={() => setEditVisible(false)}
        width={860}
        // 这个表单有十一块，整弹窗比屏幕还高，底部「保存 / 取消」得滚到最底才见得到 ——
        // 内容区自己滚，动作条钉在窗口底（主人 2026-11-21 截图里就是看不到按钮）。
        bodyStyle={{ maxHeight: 'calc(100vh - 250px)', overflowY: 'auto' }}
        footer={
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            {/* 删除放在左下角、离「保存」最远的位置：编辑到一半误点的代价太大。
                只对草稿/驳回单、且是作者或主管才出现 —— 跟列表行内那条同一套判据。 */}
            <div>
              {editMeta &&
                (editMeta.status === 'draft' || editMeta.status === 'rejected') &&
                (isReviewer || editMeta.author_id === currentUserId) && (
                  <Popconfirm
                    title="删除这份案例？"
                    content={`「${editMeta.title}」将从案例库移除。`}
                    okText="删除"
                    onConfirm={() =>
                      deleteMutation.mutate(editMeta.id, {
                        onSuccess: () => setEditVisible(false),
                      })
                    }
                  >
                    <Button type="danger" theme="borderless" disabled={deleteMutation.isPending}>
                      删除这份案例
                    </Button>
                  </Popconfirm>
                )}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button onClick={() => setEditVisible(false)}>取消</Button>
              <Button
                type="primary"
                theme="outline"
                loading={saveMutation.isPending}
                onClick={() => saveMutation.mutate()}
              >
                保存
              </Button>
            </div>
          </div>
        }
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div>
            <FormLabel required>标题</FormLabel>
            <Input placeholder="一句话说清这个案例做成了什么" value={form.title} onChange={(v) => setForm({ ...form, title: v })} />
          </div>
          <div>
            <FormLabel hint="看案例的人可按权限跳转过去">关联客户</FormLabel>
            <RecordSearchSelect
              key={`case-customer-${editVisible}`}
              scope="case-customer"
              enabled={editVisible}
              placeholder="按客户名称或编号搜索"
              value={form.customer_id}
              list={listCustomers}
              lookup={getCustomer}
              labelOf={(customer) => customer.name}
              onChange={(customer) =>
                setForm({
                  ...form,
                  customer_id: customer?.id,
                  // 换客户时清掉旧证据，避免挂到别的客户的单据上
                  quote_id: undefined,
                  order_id: undefined,
                  sample_id: undefined,
                })
              }
              style={{ width: '100%' }}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 10 }}>
            <div>
              <FormLabel hint="对外展示用，别写客户真名">客户代称</FormLabel>
              <Input placeholder="如：某包装厂" value={form.customer_label} onChange={(v) => setForm({ ...form, customer_label: v })} />
            </div>
            <div>
              <FormLabel>行业</FormLabel>
              <Input placeholder="如：食品" value={form.industry} onChange={(v) => setForm({ ...form, industry: v })} />
            </div>
            <div>
              <FormLabel>产品线</FormLabel>
              <Input placeholder="如：重型包装" value={form.product_line} onChange={(v) => setForm({ ...form, product_line: v })} />
            </div>
            <div>
              <FormLabel>推进到的阶段</FormLabel>
              <Select
                style={{ width: '100%' }}
                placeholder="选择阶段"
                value={form.stage_reached}
                onChange={(v) => setForm({ ...form, stage_reached: v as string })}
                optionList={STAGES.map((s) => ({ value: s, label: STAGE_LABEL[s] }))}
                showClear
              />
            </div>
          </div>
          <div>
            <FormLabel hint="逗号分隔">问题标签</FormLabel>
            <Input placeholder="如：价格异议，交期紧" value={form.problem_tags} onChange={(v) => setForm({ ...form, problem_tags: v })} />
          </div>
          {form.customer_id && (
            <div
              style={{
                display: 'grid',
                gap: 8,
                padding: 10,
                background: 'var(--crm-surface-low)',
                borderRadius: 8,
              }}
            >
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                证据单据（§3.7：从已有时间线和单据里挑证据，看案例的人可跳转查看）
              </div>

              {/* 已选证据：**可以挂多条**（一个案例常常是三张订单、两份打样支撑的）。
                  原来每种只能选一张，遇到"三个订单一起说明问题"就表达不了。 */}
              <div style={{ display: 'grid', gap: 4 }}>
                {form.evidences.length === 0 ? (
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                    还没有挂证据
                  </div>
                ) : (
                  form.evidences.map((item) => (
                    <div
                      key={`${item.kind}:${item.business_id}`}
                      style={{ display: 'grid', gap: 4, fontSize: 13 }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Tag size="small" color="blue">
                          {item.kind_label}
                        </Tag>
                        <span style={{ flex: 1 }}>{item.label}</span>
                        <button type="button" className="crm-text-action"
                          style={{ color: 'var(--crm-danger, #d45)' }}
                          onClick={() =>
                            setForm({
                              ...form,
                              evidences: form.evidences.filter(
                                (row) => row.kind !== item.kind || row.business_id !== item.business_id,
                              ),
                            })
                          }
                        >
                          移除
                        </button>
                      </div>
                      <Input
                        size="small"
                        aria-label="证据备注"
                        placeholder="备注（选填，随证据分享）"
                        maxLength={255}
                        value={item.note ?? ''}
                        onChange={(note) =>
                          setForm({
                            ...form,
                            evidences: form.evidences.map((row) => row === item ? { ...row, note } : row),
                          })
                        }
                      />
                    </div>
                  ))
                )}
              </div>

              {/* 添加入口一：按单据挑（下拉会列出该客户名下的报价/订单/打样） */}
              <div>
                <FormLabel hint="可多选，选完点右侧「添加」">按单据挑</FormLabel>
                <div style={{ display: 'flex', gap: 8 }}>
                  <Select
                    style={{ flex: 1 }}
                    placeholder="选择这个客户的报价单 / 订单 / 打样单"
                    value={evidencePick}
                    filter={optionMatcher}
                    onChange={(v) => setEvidencePick(v as string)}
                    optionList={[
                      ...(quotesQuery.data?.items ?? []).map((q) => ({
                        value: `quote:${q.id}`,
                        label: `报价 ${q.quote_no} · ${q.status_label ?? q.status}`,
                      })),
                      ...(ordersQuery.data?.items ?? []).map((o) => ({
                        value: `order:${o.id}`,
                        label: `订单 ${o.order_no} · ¥${o.total_amount ?? 0}`,
                      })),
                      ...(samplesQuery.data?.items ?? []).map((sp) => ({
                        value: `sample:${sp.id}`,
                        label: `打样单编号 ${sp.id} · ${sp.status_label ?? sp.status}`,
                      })),
                    ]}
                  />
                  <Button
                    disabled={!evidencePick}
                    onClick={() => {
                      if (!evidencePick) return
                      addEvidence(evidencePick)
                      setEvidencePick(undefined)
                    }}
                  >
                    添加
                  </Button>
                </div>
              </div>

              {/* 添加入口二：**从时间线挑**（§3.7 的原话就是"从已有时间线和单据选证据"）。
                  时间线里的事件带 source（哪张单产生的），点一下就把它加成证据 ——
                  比在几十张单里翻编号直观得多。没有 source 的事件（纯跟进记录）
                  不给按钮：它不是单据，挂不上。 */}
              <div>
                <FormLabel hint={`共 ${formTimelineQuery.data?.length ?? 0} 条事件`}>
                  从时间线挑
                </FormLabel>
                <div
                  style={{
                    maxHeight: 150,
                    overflow: 'auto',
                    display: 'grid',
                    gap: 4,
                    fontSize: 12.5,
                  }}
                >
                  {(formTimelineQuery.data ?? []).map((event, index) => {
                    /* 原来一行只有「日期 + 类型」：连着十条「2026-11-14 业务进展」，
                       根本分不清哪条能证明这次的做法（2026-11-17 视觉审视 V2-10）。
                       改成三段：日期降为小字，中间放事件摘要（没有摘要退回标题），
                       下面一行补事件类型、操作人，主次一眼分得开。 */
                    const summary = (event.detail ?? '').replace(/\s+/g, ' ').trim()
                    const main = summary || event.title
                    const sub = [event.title, event.operator_name].filter(Boolean).join(' · ')
                    return (
                      <div
                        key={`${event.at}-${index}`}
                        style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}
                      >
                        <span style={{ color: 'var(--crm-text-3)', flex: '0 0 auto' }}>
                          {event.at ? formatBeijingDate(event.at) : ''}
                        </span>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div
                            title={main}
                            style={{
                              color: 'var(--crm-text)',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {main}
                          </div>
                          {sub && (
                            <div
                              style={{
                                fontSize: 11,
                                color: 'var(--crm-text-3)',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {sub}
                            </div>
                          )}
                        </div>
                        {event.source ? (
                          <button type="button" className="crm-text-action"
                            onClick={() =>
                              addEvidence(`${event.source!.type}:${event.source!.id}`)
                            }
                          >
                            设为证据
                          </button>
                        ) : (
                          <span style={{ color: 'var(--crm-text-3)', flex: '0 0 auto' }}>非单据</span>
                        )}
                      </div>
                    )
                  })}
                  {(formTimelineQuery.data ?? []).length === 0 && (
                    <div style={{ color: 'var(--crm-text-3)' }}>这个客户还没有时间线事件</div>
                  )}
                </div>
              </div>
            </div>
          )}
          <div>
            <FormLabel>客户背景</FormLabel>
            <TextArea rows={2} value={form.background} onChange={(v) => setForm({ ...form, background: v })} />
          </div>
          <div>
            <FormLabel>客户目标</FormLabel>
            <TextArea rows={2} value={form.goal} onChange={(v) => setForm({ ...form, goal: v })} />
          </div>
          {/* 下面两项**不打实心红星**：存草稿时后端根本不要求它们，只有「提交审核」
              才要求两者至少填一项（cases/service.py 的 submit_case）—— 按
              `03-API` 设计文档的口径是**二选一，不是各自必填**，打实心星就是撒谎
              （2026-10-06 因此把星号去掉过）。
              但只留灰字又不显眼（主人 2026-10-13 截图：「这个必填的怎么没有标注 * 号」），
              所以用 `conditionallyRequired`：**空心灰星**代表"有条件下必填"，
              条件由 hint 讲清。 */}
          <div>
            <FormLabel
              conditionallyRequired
              hint="提交审核时，与「可复用做法」至少填一项（存草稿不要求）"
            >
              关键动作
            </FormLabel>
            <TextArea rows={2} value={form.key_actions} onChange={(v) => setForm({ ...form, key_actions: v })} />
          </div>
          <div>
            <FormLabel>异议处理</FormLabel>
            <TextArea rows={2} value={form.objection_handling} onChange={(v) => setForm({ ...form, objection_handling: v })} />
          </div>
          <div>
            <FormLabel>报价/打样经过</FormLabel>
            <TextArea rows={2} value={form.process} onChange={(v) => setForm({ ...form, process: v })} />
          </div>
          <div>
            <FormLabel>结果</FormLabel>
            <TextArea rows={2} value={form.result} onChange={(v) => setForm({ ...form, result: v })} />
          </div>
          <div>
            <FormLabel
              conditionallyRequired
              hint="提交审核时，与「关键动作」至少填一项（存草稿不要求）"
            >
              可复用做法
            </FormLabel>
            <TextArea rows={2} value={form.lessons} onChange={(v) => setForm({ ...form, lessons: v })} />
          </div>
        </div>
      </Modal>

      <Modal
        title={
          <span>
            {detail?.title ?? ''}
            {/* 版本号要摆在标题上：列表里同时有 V2 和原版时，点开详情得知道自己看的是哪一版 */}
            {detail && (detail.version ?? 1) > 1 && (
              <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--crm-text-3)' }}>
                V{detail.version}
              </span>
            )}
          </span>
        }
        visible={Boolean(detail)}
        onCancel={() => setDetail(null)}
        footer={null}
        width={680}
      >
        {detail && (
          <div style={{ display: 'grid', gap: 10, fontSize: 14 }}>
            {detail.status === 'withdrawn' && (
              <Banner type="info" closeIcon={null} description="此版本已撤下，内容与审核记录保留供查阅。" />
            )}
            {/* 已被取代的旧版：**内容照常可读**（培训资料不断档），但必须说清
                "这是历史版本、且不能再改"，否则读者会照着过时的做法用，
                作者也会纳闷为什么编辑按钮没了（§5.1.5 的口径）。 */}
            {detail.status === 'superseded' && (
              <Banner
                type="info"
                closeIcon={null}
                description={
                  <span>
                    这是<b>历史版本</b>（V{detail.version ?? 1}），已被更新的一版取代，
                    内容保留供查阅、不能再修改。
                    {detail.superseded_by ? (
                      <>
                        {' '}
                        <button type="button" className="crm-text-action" onClick={() => void openDetailById(detail.superseded_by as number)}>
                          查看当前版本
                        </button>
                      </>
                    ) : null}
                  </span>
                }
              />
            )}
            {/* 修订稿：说明它是从哪一版改出来的，免得作者忘了自己在改什么。
                已发布和在途要说**不一样**的话——对已经发布的版本说"审核通过后会替换它"，
                用户会困惑"不是已经发布了吗"。 */}
            {detail.revision_of_id && detail.status !== 'superseded' && detail.status !== 'withdrawn' && (
              <Banner
                type="info"
                closeIcon={null}
                description={
                  detail.status === 'published' ? (
                    <span>
                      本版是第 {detail.version ?? 2} 版（从第 {(detail.version ?? 2) - 1} 版改出），
                      已替换原版成为当前版本；原版转为只读的历史版本，仍可查阅。
                    </span>
                  ) : (
                    <span>
                      本版是第 {detail.version ?? 2} 版（从第 {(detail.version ?? 2) - 1} 版改出）的
                      {detail.status === 'pending_review' ? '待审稿' : '草稿'}。
                      原版仍可供培训。新版审核通过后才会替换原版。
                    </span>
                  )
                }
              />
            )}
            {/* 脱敏可解释（§3.7）：读者要知道"这里为什么少了个数字"，
                作者/主管要在发布前知道"分享出去会被抹掉哪些片段" */}
            {detail.share_view && (detail.redaction_summary ?? []).length > 0 && (
              <Banner
                type="info"
                closeIcon={null}
                description={`分享版已脱敏：${(detail.redaction_summary ?? []).join('、')}。做法可学，具体价格与联系方式不外泄。`}
              />
            )}
            {!detail.share_view && (detail.redaction_summary ?? []).length > 0 && (
              <Banner
                type="warning"
                closeIcon={null}
                description={`本案例正文含受限信息（${(detail.redaction_summary ?? []).join('、')}），分享版会自动替换成占位。建议先把正文里的数字改写后再发布，培训价值不受影响。`}
              />
            )}
            {!detail.share_view && (detail.hidden_evidence ?? []).length > 0 && (
              <Banner
                type="warning"
                closeIcon={null}
                description="注意：这条案例关联的原始单据，部分读者没有查看权限——分享版不会向他们下发单据入口（原单据仍按业务权限访问）。"
              />
            )}
            <div>
              {detail.customer_name ?? detail.customer_label ?? '客户未填'}
              {detail.industry ? ` · ${detail.industry}` : ''}
              {detail.product_line ? ` · ${detail.product_line}` : ''}
              {detail.stage_reached ? ` · ${STAGE_LABEL[detail.stage_reached] ?? detail.stage_reached}` : ''}
            </div>
            {(() => {
              const sections = ([
                ['客户背景', detail.background],
                ['客户目标', detail.goal],
                ['关键动作', detail.key_actions],
                ['异议处理', detail.objection_handling],
                ['报价/打样经过', detail.process],
                ['结果', detail.result],
                ['可复用做法', detail.lessons],
              ] as const).filter(([, text]) => text)
              // 正文一段都没填时，整块会只剩大片空白，看的人以为加载失败
              // （2026-11-17 视觉审视 V2-09）。这里明确说「还没写」，并指出在哪补。
              if (sections.length === 0) {
                return (
                  <div className="case-empty-body">
                    <div>这条案例的正文还没有填写：客户目标、关键动作、结果、可复用做法都是空的。</div>
                    <div className="case-empty-hint">
                      点右上角「编辑」把这几段补上，审核通过后它才会成为可培训的案例。
                    </div>
                  </div>
                )
              }
              return sections.map(([label, text]) => (
                <div key={label}>
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>{label}</div>
                  <div style={{ whiteSpace: 'pre-wrap', color: 'var(--crm-text-2)' }}>{text}</div>
                </div>
              ))
            })()}
            {(detail.evidences ?? []).length > 0 ? (
              <div style={{ display: 'grid', gap: 4, fontSize: 13 }}>
                <span style={{ color: 'var(--crm-text-3)' }}>
                  证据单据（{(detail.evidences ?? []).length} 条，可点开原单）：
                </span>
                {(detail.evidences ?? []).map((item) => (
                  <div key={`${item.kind}:${item.business_id}`}>
                    <Link to={EVIDENCE_PATH[item.kind] ? `${EVIDENCE_PATH[item.kind]}${item.business_id}` : '#'}>
                      {item.label || `${item.kind_label}（暂无编号）`}
                      <span style={{ color: 'var(--crm-text-3)' }}> · {item.kind_label}</span>
                    </Link>
                    {item.note && (
                      <div style={{ whiteSpace: 'pre-wrap', color: 'var(--crm-text-2)' }}>{item.note}</div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              (detail.quote_id || detail.order_id || detail.sample_id) && (
                <div style={{ display: 'flex', gap: 12, fontSize: 13, flexWrap: 'wrap' }}>
                  <span style={{ color: 'var(--crm-text-3)' }}>证据单据：</span>
                  {detail.quote_id && <Link to={`/quotes/${detail.quote_id}`}>报价单 #{detail.quote_id}</Link>}
                  {detail.order_id && <Link to={`/orders/${detail.order_id}`}>订单 #{detail.order_id}</Link>}
                  {detail.sample_id && (
                    <Link to={`/samples/${detail.sample_id}`}>打样单编号 {detail.sample_id}</Link>
                  )}
                </div>
              )
            )}
            {/* 逐轮审核历史（返工单 P2-8）：`review_note` 只有最后一次，
                前几轮是谁、什么时候、结论如何，只有这里能看到。
                字段名过去前后端不一致（前端写 `at`、后端存 `reviewed_at`），
                所以这一块一直显示不出审核人和时间。 */}
            {(detail.review_history ?? []).length > 0 && (
              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>
                  审核记录（共 {(detail.review_history ?? []).length} 轮）
                </div>
                <div style={{ display: 'grid', gap: 6, fontSize: 13 }}>
                  {(detail.review_history ?? []).map((round, index) => (
                    <div
                      key={`${round.reviewed_at ?? index}`}
                      style={{
                        padding: '6px 8px',
                        border: '1px solid var(--crm-outline)',
                        borderRadius: 4,
                      }}
                    >
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <Tag color={round.approve ? 'green' : 'red'} size="small">
                          {round.approve ? '通过' : '驳回'}
                        </Tag>
                        <span style={{ color: 'var(--crm-text-3)' }}>
                          第 {round.round ?? index + 1} 轮
                          {round.version ? ` · V${round.version}` : ''}
                        </span>
                        <span style={{ flex: 1 }} />
                        <span style={{ color: 'var(--crm-text-3)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                          {round.reviewer_name || '-'}
                          {(round.self_reviewed ?? Boolean(round.reviewer_id && round.reviewer_id === detail.author_id)) && (
                            <Tag color="orange" size="small">本人审核</Tag>
                          )}
                        </span>
                        <span style={{ color: 'var(--crm-text-3)' }}>
                          {round.reviewed_at ? formatBeijingTime(round.reviewed_at) : ''}
                        </span>
                      </div>
                      {round.note && (
                        <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{round.note}</div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {detail.share_view && (detail.hidden_evidence ?? []).length > 0 && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                该案例关联的原始单据对当前账号不可见——案例只引用单据，打开仍需原单据权限。
              </div>
            )}
            {(detail.problem_tags ?? []).length > 0 && (
              <div>
                {(detail.problem_tags ?? []).map((tag) => (
                  <Tag key={tag}>{tag}</Tag>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
