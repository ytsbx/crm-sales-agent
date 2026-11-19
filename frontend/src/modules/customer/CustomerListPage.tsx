import { UNRATED_OPTION, customerLevelValue, customerLevelPayload } from '../../shared/customer-level'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { PoolRecyclePanel } from '../recycle/PoolRecyclePage'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Button, Input, Modal, Select, Table, Tabs, Toast, TextArea } from '@douyinfe/semi-ui'

import {
  batchTagCustomers,
  batchTransferCustomers,
  createCustomer,
  deduplicateCustomers,
  exportCustomersFiltered,
  listCustomers,
  listStageDistribution,
  listTags,
  type CustomerExportPurpose,
  type DuplicateMatch,
  type CustomerPayload,
} from '../../shared/api/customer'
import UserSearchSelect from '../../shared/components/UserSearchSelect'
import { newRequestKey } from '../../shared/api/requestKey'
import { useAuthStore } from '../../shared/store/auth'
import { usePermissions } from '../../shared/hooks/permissions'
import type { Customer } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import BlockedHint, { useBlockedNotify } from '../../shared/components/BlockedHint'
import GuardedButton from '../../shared/components/GuardedButton'
import { getPersistedAuthToken } from '../../shared/auth-token'

type Scope = 'mine' | 'pool' | 'all'

const SCOPE_OPTIONS = [
  { value: 'mine', label: '我负责的' },
  { value: 'pool', label: '公海客户' },
  { value: 'all', label: '全部客户' },
]

const LEVEL_OPTIONS = [
  UNRATED_OPTION,
  { value: 'A', label: 'A 级' },
  { value: 'B', label: 'B 级' },
  { value: 'C', label: 'C 级' },
  { value: 'D', label: 'D 级' },
]

const SOURCE_OPTIONS = ['展会', '官网', '老客户介绍', '企业微信', 'Excel 导入', '手工录入', '其他'].map(
  (value) => ({ value, label: value }),
)

const CUSTOMER_EXPORT_PURPOSES: Array<{ value: CustomerExportPurpose; label: string }> = [
  { value: 'customer_follow_up', label: '客户跟进' },
  { value: 'business_analysis', label: '经营分析' },
  { value: 'management_report', label: '管理汇报' },
  { value: 'data_reconciliation', label: '数据核对' },
  { value: 'historical_migration', label: '历史数据迁移' },
  { value: 'other', label: '其他' },
]

/**
 * 没有「客户导出」权限时给用户看的那句话。
 *
 * 为什么要有这句：`customer:export` 是**独立权限**，与其他查看权限分开，
 * 而四种内置角色一个都没有它。从前的写法是「没权限就整个按钮不渲染」，
 * 用户看到的是**工具栏上根本没有导出**，只会得出"这个功能没做"的结论 ——
 * 主人 2026-10-12 的原话就是「点又点不了，又不给提示为什么点不了，类似的情况都要修」。
 * 权限本身没有放松（判据仍是同一个 `can('customer:export')`），只是把"为什么没有"说出来。
 */
const CUSTOMER_EXPORT_PERMISSION_REASON =
  '导出需要单独授权，请联系管理员开通「客户导出」权限。'

// 领导六阶段（自动推导，非人工填写）：了解 → 报价 → 打样 → 首单 → 返单 → 稳定复购
// 阶段原本每个占一个彩色块，一列扫过去像彩灯；改成小圆点 + 文字：
// 颜色还在（阶段越靠后越"熟"），但不再是一排色块抢眼。
const STAGE_DOT: Record<string, string> = {
  understanding: '#9aa0ab',
  quote: '#1f4fa8',
  sample: '#b45309',
  first_order: '#0891b2',
  repeat: '#7431d3',
  stable: '#00b42a',
}

const EMPTY_FORM: CustomerPayload = {
  name: '',
  short_name: '',
  region: '',
  address: '',
  source: '手工录入',
  level: null,
  remark: '',
  // 不选负责人 = 归创建人自己（后端 `create_customer` 的口径）
  owner_id: null,
}

export default function CustomerListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const currentUser = useAuthStore((state) => state.user)
  // 批量转移需要 customer:assign，前端先隐藏按钮，避免点了才吃 403
  const { can } = usePermissions()

  /* 1024 宽内容区只有约 700px，九列合计 975px，「标签 / 负责人 / 状态」
     全被推到卡片外。窄屏切紧凑档：负责人并进客户名称第二行，
     「近12月项目 + 历史有效订单」并成一列两行，其余列宽收紧。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [level, setLevel] = useState<string | undefined>()
  // 默认范围跟着数据权限走：管理员看全部，业务员看自己的——
  // 否则管理员打开客户中心会是一片空白（他名下本来就没有客户）
  const [scope, setScope] = useState<Scope>(
    currentUser?.data_scope === 'all' ? 'all' : 'mine',
  )
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  // 第二个页签「回收复核」（2026-11-19 主人：公海回收不用单独做一个板块）。
  // 复核本来就是公海池的下一步：同一张卡里先看池子，再看谁该被回收、直接批 / 驳。
  const [view, setView] = useState<'list' | 'review'>('list')

  const [modalVisible, setModalVisible] = useState(false)
  const [form, setForm] = useState<CustomerPayload>(EMPTY_FORM)
  const [otherSource, setOtherSource] = useState('')
  // 批量导入导出
  const fileInputRef = useRef<HTMLInputElement>(null)
  // 本次「新建客户」表单的幂等键：打开表单时生成，成功后清空（第八批 8.15）
  const createRequestKeyRef = useRef('')
  const [importing, setImporting] = useState(false)
  // 导入预览 / 导入结果共用的行排版（2026-11-17 导入页审视）。
  // 原来是「名字 —— 原因」一行行往下堆，几十条失败原因长得一模一样，
  // 既不知道是 Excel 第几行，名称空着时前面只剩一个破折号。
  // 现在：行号单独一列对齐，名称占主位，原因换到第二行。
  const importSectionTitle: CSSProperties = {
    fontWeight: 600,
    fontSize: 13,
    color: 'var(--crm-text)',
    marginBottom: 6,
  }
  const importRow: CSSProperties = { display: 'flex', gap: 8, padding: '3px 0', fontSize: 13, lineHeight: 1.7 }
  const importRowNo: CSSProperties = {
    flex: '0 0 auto',
    minWidth: 58,
    fontSize: 12,
    color: 'var(--crm-text-3)',
    fontVariantNumeric: 'tabular-nums',
  }
  const importRowText: CSSProperties = { minWidth: 0, overflowWrap: 'anywhere', color: 'var(--crm-text-2)' }
  const importScroll: CSSProperties = {
    // 关键：正文自己滚。不设上限时四十多行失败原因会把弹窗顶出屏幕，
    // 「确认导入 / 取消」跟着一起被顶出去，等于没法操作。
    maxHeight: 'min(56vh, 520px)',
    overflowY: 'auto',
    paddingRight: 6,
  }
  type ImportSummary = {
    created_count: number
    total: number
    skipped_count: number
    failed_count: number
    created: Array<{ row?: number; id?: number; name: string }>
    skipped: Array<{ row?: number; name: string; reason: string }>
    failed: Array<{ row?: number; name: string; reason: string; problems?: string[] }>
    disputed_count?: number
    disputed?: Array<{
      row?: number
      name: string
      candidate?: string | null
      note?: string | null
      score?: number
      reasons?: string[]
    }>
    // 后端一直有这三个字段（preview 与结果共用同一结构），前端类型没声明：
    // 更新了几条、失败总共有多少、是否被截断都拿不到，只能干说一个数。
    updated_count?: number
    failed_total?: number
    failed_truncated?: boolean
    preview_token?: string
  }
  const [importPreview, setImportPreview] = useState<ImportSummary | null>(null)
  const [importPreviewFile, setImportPreviewFile] = useState<File | null>(null)
  const [importResult, setImportResult] = useState<ImportSummary | null>(null)
  const [exportModalOpen, setExportModalOpen] = useState(false)
  const [exportPurpose, setExportPurpose] = useState<CustomerExportPurpose | undefined>()
  const [exportPurposeNote, setExportPurposeNote] = useState('')
  const [exporting, setExporting] = useState(false)

  // 「导出」这一路的两处说明，共用同一个判据（判据本身没动，只是把原因写出来）：
  //   ① 工具栏上的导出按钮：没权限时灰着，旁边一行灰字；
  //   ② 导出弹窗的确认键：没选用途 / 选了「其他」没填说明时灰着，弹窗底部列出缺什么。
  // 两条规矩见 shared/components/BlockedHint.tsx：看得见（常驻灰字）+ 点得动（点了有反应）。
  const canExport = can('customer:export')
  // 回收复核只对能批回收的人开放（主管的 pool_review / 管理员的 settings:manage）
  const canPoolReview = can('customer:pool_review') || can('settings:manage')
  const exportBlockedReasons = [
    ...(!exportPurpose ? ['请选择导出用途。'] : []),
    // 用途选「其他」时后端要求写清具体用途（schema 里有最小长度校验），
    // 只把「其他」两个字交上去会被拒 —— 先说清楚，别让人白点一次。
    ...(exportPurpose === 'other' && !exportPurposeNote.trim()
      ? ['选择「其他」时必须填写导出用途说明。']
      : []),
  ]
  const notifyExportPurposeBlocked = useBlockedNotify(exportBlockedReasons, '导出')

  const tokenHeader = () => {
    const token = getPersistedAuthToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  }

  // ---- 批量操作（03-API §7 的 batch-tag / batch-transfer） ----
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [batchTagOpen, setBatchTagOpen] = useState(false)
  const [batchTagIds, setBatchTagIds] = useState<number[]>([])
  const [batchTagMode, setBatchTagMode] = useState<'add' | 'replace' | 'remove'>('add')
  const [batchTransferOpen, setBatchTransferOpen] = useState(false)
  const [batchOwnerId, setBatchOwnerId] = useState<number | null>(null)

  // ---- 新建时的查重提示（PRD §6.3） ----
  const [dupMatches, setDupMatches] = useState<DuplicateMatch[]>([])

  const tagsQuery = useQuery({
    queryKey: ['tags'],
    queryFn: () => listTags(false),
    enabled: batchTagOpen,
  })
  // 六个阶段各一种色相 —— 颜色只落在数字上，标签仍是灰字：
  // 一排数字能一眼区分「哪个阶段人多」，又不会像色块墙那样抢走整行注意力。
  // 色值取自设计稿状态色板（与 StatusDot 同源），保证和列表里的小圆点一致。
  const STAGE_COLOR: Record<string, string> = {
    understanding: '#727687', // 了解：中性灰，还没开始动作
    quote: '#1f6fe0',         // 报价：蓝
    sample: '#c2710a',        // 打样：琥珀
    first_order: '#0d9488',   // 首单：青
    repeat: '#65a30d',        // 返单：绿
    stable: '#7a4fd3',        // 稳定复购：紫（周期最长的阶段）
  }

  // 六阶段分布（后端按当前数据范围自动推导）
  const stageDistQuery = useQuery({
    queryKey: ['customer-stage-distribution'],
    queryFn: () => listStageDistribution(),
  })

  const refreshList = () => {
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
    setSelectedIds([])
  }

  const batchTagMutation = useMutation({
    mutationFn: () =>
      batchTagCustomers({ customer_ids: selectedIds, tag_ids: batchTagIds, mode: batchTagMode }),
    onSuccess: (result) => {
      Toast.success(`已处理 ${result.affected} 个客户`)
      setBatchTagOpen(false)
      setBatchTagIds([])
      refreshList()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const batchTransferMutation = useMutation({
    mutationFn: () =>
      batchTransferCustomers({ customer_ids: selectedIds, owner_id: batchOwnerId }),
    onSuccess: (result) => {
      Toast.success(`已转移 ${result.affected} 个客户`)
      setBatchTransferOpen(false)
      setBatchOwnerId(null)
      refreshList()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const dedupMutation = useMutation({
    // 新建表单里只有名称可以用来查重（税号等字段表单没收集）
    mutationFn: () => deduplicateCustomers({ name: form.name }),
    onSuccess: (result) => setDupMatches(result.matches ?? []),
    onError: () => setDupMatches([]),
  })

  const downloadCsv = async (url: string, filename: string) => {
    const { default: axios } = await import('axios')
    const response = await axios.get(url, { responseType: 'blob', headers: tokenHeader() })
    const blobUrl = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(blobUrl)
  }

  const handleImport = async (file: File) => {
    setImporting(true)
    try {
      const { default: axios } = await import('axios')
      const formData = new FormData()
      formData.append('file', file)
      formData.append('preview', 'true')
      const response = await axios.post('/api/v1/customers/import', formData, {
        headers: tokenHeader(),
      })
      const body = response.data
      if (body.code !== 0) throw new Error(body.message ?? '预览失败')
      setImportPreview(body.data)
      setImportPreviewFile(file)
    } catch (error) {
      const data = (error as { response?: { data?: { message?: string } } })?.response?.data
      Toast.error(data?.message || (error instanceof Error ? error.message : '预览失败'))
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const confirmImport = async () => {
    if (!importPreviewFile || !importPreview?.preview_token) return
    setImporting(true)
    try {
      const { default: axios } = await import('axios')
      const formData = new FormData()
      formData.append('file', importPreviewFile)
      formData.append('preview_token', importPreview.preview_token)
      const response = await axios.post('/api/v1/customers/import', formData, {
        headers: tokenHeader(),
      })
      const body = response.data
      if (body.code !== 0) throw new Error(body.message ?? '导入失败')
      setImportPreview(null)
      setImportPreviewFile(null)
      setImportResult(body.data)
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
    } catch (error) {
      const data = (error as { response?: { data?: { message?: string; code?: number } } })?.response?.data
      const message = data?.message || (error instanceof Error ? error.message : '导入失败')
      setImportPreview(null)
      setImportPreviewFile(null)
      Toast.error(Number(data?.code) === 40902 ? `${message}，请重新选择文件预览` : message)
    } finally {
      setImporting(false)
    }
  }

  const query = useQuery({
    queryKey: ['customers', { keyword, level, scope, page, pageSize, userId: currentUser?.id }],
    queryFn: () =>
      listCustomers({
        keyword,
        level,
        page,
        page_size: pageSize,
        owner_id: scope === 'mine' ? currentUser?.id : undefined,
        pool_status: scope === 'pool' ? 'public' : undefined,
      }),
  })

  const createMutation = useMutation({
    // 请求幂等键跟着"这一次填表"走：弱网/超时后用户再点一次提交，
    // 带的是同一把键，服务端只会建一条（第八批 8.15）。
    mutationFn: (payload: CustomerPayload) =>
      createCustomer({ ...payload, request_key: createRequestKeyRef.current || undefined }),
    onSuccess: (customer) => {
      Toast.success(`客户「${customer.name}」已创建`)
      setModalVisible(false)
      setForm(EMPTY_FORM)
      setOtherSource('')
      // 这次表单已经用掉了这把键，下一次新建要换新的
      createRequestKeyRef.current = ''
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
      void queryClient.invalidateQueries({ queryKey: ['workbench'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const handleSearch = () => {
    setKeyword(keywordInput.trim())
    setPage(1)
  }

  const handleExport = async () => {
    if (!exportPurpose || (exportPurpose === 'other' && !exportPurposeNote.trim())) return
    setExporting(true)
    try {
      await exportCustomersFiltered({
        keyword: keyword || undefined,
        level,
        owner_id: scope === 'mine' ? currentUser?.id : undefined,
        pool_status: scope === 'pool' ? 'public' : undefined,
        purpose: exportPurpose,
        purpose_note: exportPurpose === 'other' ? exportPurposeNote.trim() : undefined,
      })
      Toast.success('客户数据已导出，导出用途已记入审计')
      setExportModalOpen(false)
      setExportPurpose(undefined)
      setExportPurposeNote('')
    } catch {
      Toast.error('导出失败，请检查权限或筛选范围后重试')
    } finally {
      setExporting(false)
    }
  }

  // 计数格：有值主色加粗，0 用浅灰
  const countCell = (value?: number) => {
    const count = value ?? 0
    return (
      <span
        style={{
          fontWeight: count > 0 ? 600 : 400,
          color: count > 0 ? 'var(--crm-primary)' : 'var(--crm-text-3)',
        }}
      >
        {count}
      </span>
    )
  }

  // 「活跃状态」原本是独立一列，现在并进客户名称的下方小字
  const activityLabel = (value?: string) =>
    value === 'active' ? '活跃' : value === 'stale' ? '沉睡' : value === 'watch' ? '关注' : '待补核'

  const columns = [
    {
      title: '客户名称',
      dataIndex: 'name',
      // 两行：上面公司名，下面「地区 · 来源 · 活跃状态」。
      // 那三列各自只有几个字却各占 90-120px，合到名字下面能省掉三列，
      // 顺带把横向滚动条也去掉（14 列 → 10 列）。
      // 260 → 210：实测这一列 268 时整表 1180 > 内容区 1130，横向滚动条又回来了
      // （2026-11-16 全站表格扫描）。210 仍够放「宁波家电01厂【演示造数】」。
      width: compact ? 215 : 210,
      render: (text: string, record: Customer) => (
        <div>
          <a
            style={{ color: 'var(--crm-primary)', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            onClick={() => navigate(`/customers/${record.id}`)}
            title={text}
          >
            {text}
          </a>
          <div
            title={[record.region, record.source, activityLabel(record.activity_status), compact ? `负责人 ${record.owner_name ?? '公海'}` : ''].filter(Boolean).join(' · ')}
            style={{ marginTop: 2, color: 'var(--crm-text-3)', fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {compact ? `${record.level ?? '未分级'} · ` : ''}
            {[record.region, record.source, activityLabel(record.activity_status)]
              .filter(Boolean)
              .join(' · ')}
            {compact ? ` · 负责人 ${record.owner_name ?? '公海'}` : ''}
          </div>
        </div>
      ),
    },
    ...(compact
      ? []
      : [{
      title: '等级',
      dataIndex: 'level',
      // 96 而不是 80：描边标签（未分级）比实心标签宽，80 时末尾会多出一个省略号
      width: 70,
      render: (value: string | null) =>
        value ? (
          // A 绿 / B 蓝 / C 橙：字母本身已经够辨识，不必再套一个色块。
          // 一列十几行色块叠在一起，整张表就是"彩灯"。
          <span
            style={{
              fontWeight: 600,
              color:
                value === 'A'
                  ? 'var(--crm-success)'
                  : value === 'B'
                    ? 'var(--crm-primary)'
                    : 'var(--crm-warning)',
            }}
          >
            {value}
          </span>
        ) : (
          // 「未分级」是**还没评**，不是一种等级：灰字即可
          <span style={{ color: 'var(--crm-text-3)' }}>未分级</span>
        ),
    }]),
    {
      // 领导六阶段：由订单/打样/报价事实自动推导（customer/stage.py），不占销售一分钟
      title: '阶段',
      dataIndex: 'stage',
      width: compact ? 96 : 70,
      render: (_: unknown, record: Customer) =>
        record.stage ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                flexShrink: 0,
                background: STAGE_DOT[record.stage] ?? '#9aa0ab',
              }}
            />
            {record.stage_label ?? record.stage}
          </span>
        ) : (
          '-'
        ),
    },
    // 三个计数列共用：**有值才上色加粗**，0 保持浅灰。
    // 全是同一种灰时，一列扫过去分不出「这家有 3 个商机」和「这家啥也没有」。
    {
      title: compact ? '商机' : '当前商机',
      dataIndex: 'current_opportunity_count',
      width: compact ? 76 : 90,
      render: (value: number | undefined) => countCell(value),
    },
    ...(compact
      ? []
      : [{
          title: '近12月项目',
          dataIndex: 'recent_order_count',
          width: 90,
          render: (value: number | undefined) => countCell(value),
        }]),
    {
      title: compact ? '项目 / 订单' : '历史有效订单',
      dataIndex: 'historical_order_count',
      width: compact ? 96 : 105,
      render: (value: number | undefined, record: Customer) =>
        compact ? (
          /* 窄屏把「近12月项目」并到这一列：上一行近12月、下一行历史 */
          <div style={{ display: 'grid', gap: 2, justifyItems: 'center' }}>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              近12月 {countCell(record.recent_order_count)}
            </span>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              历史 {countCell(value)}
            </span>
          </div>
        ) : (
          countCell(value)
        ),
    },
    {
      // PRD §6.1：客户列表要能直接看到标签
      title: '标签',
      dataIndex: 'tags',
      // 180 → 140：标签本来就会折行，收窄后空间让给客户名称
      width: compact ? 110 : 140,
      render: (tags: Customer['tags']) => {
        const list = tags ?? []
        if (list.length === 0) {
          return <span style={{ color: 'var(--crm-text-3)' }}>-</span>
        }
        // 标签不再一个个套灰胶囊：两个胶囊就折成两行，行高被撑、整表节奏被拉乱。
        // 改成一行灰字、「·」分隔，最多两个，多出来的收成「+N」。
        return (
          <span
            title={list.map((tag) => tag.name).join('·')}
            style={{ fontSize: 12, color: 'var(--crm-text-2)', whiteSpace: 'nowrap' }}
          >
            {list
              .slice(0, 2)
              .map((tag) => tag.name)
              .join('·')}
            {list.length > 2 && ` +${list.length - 2}`}
          </span>
        )
      }
    },
    ...(compact
      ? []
      : [{
          title: '负责人',
          dataIndex: 'owner_name',
          width: 110,
          render: (v: string | null) => v ?? '公海',
        }]),
    {
      title: '状态',
      dataIndex: 'status',
      width: compact ? 66 : 90,
      render: (v: string) => (v === 'active' ? '正常' : v),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader title="客户中心" subtitle="客户、联系人与归属都在这里统一管理" />

      <SectionCard>
        {canPoolReview && (
          <Tabs
            type="line"
            activeKey={view}
            onChange={(key) => setView(key as 'list' | 'review')}
            tabList={[
              { tab: '客户列表', itemKey: 'list' },
              { tab: '回收复核', itemKey: 'review' },
            ]}
          />
        )}
        {view === 'review' ? (
          <div style={{ marginTop: 16 }}>
            <PoolRecyclePanel />
          </div>
        ) : (
        <div className="customer-list-body" style={canPoolReview ? { marginTop: 16 } : undefined}>
        <div className="toolbar customer-list-toolbar">
          <Input
            placeholder="搜索客户名称 / 简称 / 地址"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={handleSearch}
            style={{ width: 260 }}
            showClear
          />
          <Select
            placeholder="客户等级"
            value={level}
            onChange={(value) => {
              setLevel(value as string | undefined)
              setPage(1)
            }}
            optionList={LEVEL_OPTIONS}
            style={{ width: 140 }}
            showClear
          />
          <Select
            value={scope}
            onChange={(value) => {
              setScope(value as Scope)
              setPage(1)
            }}
            optionList={SCOPE_OPTIONS}
            style={{ width: 140 }}
          />
          <Button onClick={handleSearch}>查询</Button>
          <div style={{ flex: 1 }} />
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            style={{ display: 'none' }}
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void handleImport(file)
            }}
          />
          <Button type="tertiary" onClick={() => downloadCsv('/api/v1/customers/import-template', '客户导入模板.csv')}>
            下载模板
          </Button>
          {/* 导出独立授权；提示统一由 GuardedButton 渲染，整行显示在按钮下方。 */}
          <GuardedButton
            type="tertiary"
            hintBelow
            disabled={!canExport}
            blockedReasons={[CUSTOMER_EXPORT_PERMISSION_REASON]}
            action="导出"
            onClick={() => setExportModalOpen(true)}
          >
            导出
          </GuardedButton>
          <Button type="tertiary" loading={importing} onClick={() => fileInputRef.current?.click()}>
            批量导入
          </Button>
          <Button type="primary"
            theme="solid"
            onClick={() => {
              // 每次打开"新建客户"都是一次新表单 → 换一把新的幂等键
              createRequestKeyRef.current = newRequestKey()
              if (!can('customer:assign')) {
                setForm((current) => ({ ...current, owner_id: null }))
              }
              setModalVisible(true)
            }}
          >
            新建客户
          </Button>
        </div>

        {stageDistQuery.data && (
          <div
            className="toolbar"
            style={{
              background: 'var(--crm-surface-high)',
              padding: '8px 12px',
              borderRadius: 4,
              alignItems: 'center',
              marginBottom: 12,
            }}
          >
            <span style={{ fontSize: 13, color: 'var(--crm-text-3)' }}>客户阶段分布</span>
            {stageDistQuery.data.map((item, idx) => (
              // 颜色只落在数字上（标签保持灰字），六个阶段六种色相：
              // 2026-11-16 那版把六个数字统一成主色蓝，主人 2026-11-17 反馈
              // 「怎么变一个色了，以前好几种颜色吧」—— 一排数字全同色等于没区分。
              // 现在按阶段给色相，仍不做色块。
              <span key={item.stage} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {idx > 0 && (
                  <span style={{ width: 1, height: 12, background: 'var(--crm-border, #e3e5ea)' }} />
                )}
                <span style={{ fontSize: 13, color: 'var(--crm-text-3)' }}>
                  {item.label}
                  <strong style={{ marginLeft: 4, color: STAGE_COLOR[item.stage] ?? 'var(--crm-text-2)' }}>
                    {item.count}
                  </strong>
                </span>
              </span>
            ))}
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              由订单 / 打样 / 正式报价事实自动推导，无需人工维护
            </span>
          </div>
        )}

        {selectedIds.length > 0 && (
          <div
            className="toolbar"
            style={{
              background: 'var(--crm-primary-soft)',
              padding: '8px 12px',
              borderRadius: 4,
              alignItems: 'center',
            }}
          >
            <span style={{ fontWeight: 600 }}>已选 {selectedIds.length} 个客户</span>
            {/* ⚠️ 必须与后端同码（2026-10-14 审查修）。
                后端 `POST /customers/batch-tag` 要 `customer:update`
                （`customer/tags_router.py`），而这里原来**没有任何判据** ——
                紧邻的「批量转移负责人」判了 `customer:assign`，只有这个漏了。
                内置的**财务**角色权限是 [customer:view, quote:view, order:view,
                payment:view, payment:manage, order:manage]，**没有 `customer:update`**，
                于是财务勾完客户、选好标签、点确认才吃 403 —— 正是本文件
                :98 注释自己反对的"点了才吃 403"。 */}
            {can('customer:update') && (
              <Button size="small" onClick={() => setBatchTagOpen(true)}>
                批量打标签
              </Button>
            )}
            {can('customer:assign') && (
              <Button size="small" onClick={() => setBatchTransferOpen(true)}>
                批量转移负责人
              </Button>
            )}
            <div style={{ flex: 1 }} />
            <Button size="small" theme="borderless" onClick={() => setSelectedIds([])}>
              取消选择
            </Button>
          </div>
        )}

        <Table<Customer>
          className="table-center customers-table"
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          size="middle"
          // 原来写死 1180，比内容区（1130）宽 50 —— 横向滚动条常驻，右边
          // 「操作」被切一半（2026-11-16 全站表格扫描抓到）。
          // 改成跟容器走：列多时按比例压缩，宁可略微挤一点也不横滚。
          scroll={{ x: '100%' }}
          empty={emptyText(query, '没有符合条件的客户', '换个筛选条件试试，或点右上角「新建客户」。')}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds((keys ?? []) as number[]),
          }}
          pagination={{
            currentPage: page,
            pageSize,
            total: query.data?.total ?? 0,
            showSizeChanger: true,
            pageSizeOpts: [10, 20, 50, 100],
            onPageChange: (nextPage: number) => setPage(nextPage),
            onPageSizeChange: (nextSize: number) => {
              setPageSize(nextSize)
              setPage(1)
            },
          }}
        />
        </div>
        )}
      </SectionCard>

      <Modal
        width={520}
        title="导出客户"
        visible={exportModalOpen}
        onCancel={() => setExportModalOpen(false)}
        onOk={() => void handleExport()}
        confirmLoading={exporting}
        okText="确认导出"
        cancelText="取消"
        okButtonProps={{
          disabled: !exportPurpose || (exportPurpose === 'other' && !exportPurposeNote.trim()),
        }}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>
            导出的数据受你的客户数据权限范围限制，用途会记录在审计日志中。
          </div>
          <Select
            value={exportPurpose}
            placeholder="请选择导出用途（必填）"
            optionList={CUSTOMER_EXPORT_PURPOSES}
            onChange={(value) => setExportPurpose(value as CustomerExportPurpose)}
            style={{ width: '100%' }}
          />
          {exportPurpose === 'other' && (
            <TextArea
              value={exportPurposeNote}
              placeholder="请说明具体用途（必填，最多 200 字）"
              maxLength={200}
              autosize={{ minRows: 2, maxRows: 4 }}
              onChange={setExportPurposeNote}
            />
          )}
          {/* 确认键灰着的时候，原因必须在同一屏里看得见（从前打开就没有理由地灰着）。
              这段说明自己也是「点得动」的落点：点它会弹一句同样的原因 —— 弹窗 footer 在
              portal 里，包住确认键那层收不到点击（同 SampleFromSourceModal 的处理）。 */}
          <div
            data-blocked-click="1"
            className="blocked-hint-click"
            onClick={notifyExportPurposeBlocked}
            style={{ display: 'block' }}
          >
            <BlockedHint reasons={exportBlockedReasons} action="导出" style={{ marginTop: 0 }} />
          </div>
        </div>
      </Modal>

      <Modal
        title="导入预览（尚未写入）"
        visible={Boolean(importPreview)}
        onCancel={() => {
          setImportPreview(null)
          setImportPreviewFile(null)
        }}
        onOk={() => void confirmImport()}
        confirmLoading={importing}
        okText={`确认导入 ${importPreview?.created_count ?? 0} 条`}
        cancelText="取消"
        width={680}
      >
        {importPreview && (
          <div style={importScroll}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
              <span className="chip chip-primary">将新增 {importPreview.created_count} 条</span>
              {(importPreview.updated_count ?? 0) > 0 && (
                <span className="chip chip-success">更新 {importPreview.updated_count} 条</span>
              )}
              {/* 跳过是中性结果（同名/空行，不算错），原来和失败一样橙，分不出轻重 */}
              <span className="chip">跳过 {importPreview.skipped_count} 条</span>
              <span className="chip chip-error">失败 {importPreview.failed_count} 条</span>
              {(importPreview.disputed_count ?? 0) > 0 && (
                <span className="chip chip-warning">待裁定 {importPreview.disputed_count} 条</span>
              )}
            </div>

            {importPreview.created.length > 0 && (
              <section style={{ marginBottom: 16 }}>
                <div style={importSectionTitle}>这次会新增的客户</div>
                {importPreview.created.map((item) => {
                  // 同一行既在「新增」又在「待裁定」时，光看两个名单会以为重复导入 ——
                  // 实际是"先建、再挂起等你裁定"，这里说清楚。
                  const suspended = (importPreview.disputed ?? []).some((d) => d.row === item.row)
                  return (
                    <div key={`preview-new-${item.row ?? item.name}`} style={importRow}>
                      <span style={importRowNo}>{item.row ? `第 ${item.row} 行` : '—'}</span>
                      <span style={importRowText}>
                        {item.name}
                        {suspended && (
                          <span style={{ color: 'var(--crm-warning)' }}> — 会先挂起，等你在疑似重复里裁定</span>
                        )}
                      </span>
                    </div>
                  )
                })}
              </section>
            )}

            {(importPreview.disputed ?? []).length > 0 && (
              <section style={{ marginBottom: 16 }}>
                <div style={importSectionTitle}>疑似重复：导入后先挂起，等你裁定</div>
                {(importPreview.disputed ?? []).map((item) => (
                  <div key={`preview-disputed-${item.row}-${item.name}`} style={importRow}>
                    <span style={importRowNo}>{item.row ? `第 ${item.row} 行` : '—'}</span>
                    <span style={importRowText}>
                      {item.name}
                      <span style={{ color: 'var(--crm-text-3)' }}>
                        {item.candidate && item.candidate !== item.name
                          ? ` — 与已有客户「${item.candidate}」相似${item.score ? `（${item.score} 分）` : ''}`
                          : ' — 与系统里已有客户同名'}
                      </span>
                      {(item.reasons ?? []).length > 0 && (
                        <span style={{ color: 'var(--crm-text-3)' }}>：{(item.reasons ?? []).join('、')}</span>
                      )}
                    </span>
                  </div>
                ))}
              </section>
            )}

            {importPreview.failed.length > 0 && (
              <section>
                <div style={importSectionTitle}>
                  失败的行
                  {(importPreview.failed_truncated ?? false) && (
                    <span style={{ fontWeight: 400, color: 'var(--crm-text-3)' }}>
                      （只列出前 {importPreview.failed.length} 行，共 {importPreview.failed_total ?? importPreview.failed_count} 行）
                    </span>
                  )}
                </div>
                {importPreview.failed.map((item) => (
                  <div key={`preview-fail-${item.row}-${item.name}`} style={importRow}>
                    <span style={importRowNo}>{item.row ? `第 ${item.row} 行` : '—'}</span>
                    <span style={importRowText}>
                      {item.name || (
                        <span style={{ color: 'var(--crm-text-3)' }}>（这一行没填客户名称）</span>
                      )}
                      <div style={{ color: 'var(--crm-error)' }}>{item.reason}</div>
                    </span>
                  </div>
                ))}
              </section>
            )}
          </div>
        )}
      </Modal>

      <Modal
        title="导入结果"
        visible={Boolean(importResult)}
        onCancel={() => setImportResult(null)}
        onOk={() => setImportResult(null)}
        okText="知道了"
        cancelText="关闭"
        width={680}
      >
        {importResult && (
          <div style={importScroll}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
              <span className="chip chip-success">成功 {importResult.created_count} 条</span>
              {(importResult.updated_count ?? 0) > 0 && (
                <span className="chip chip-success">更新 {importResult.updated_count} 条</span>
              )}
              <span className="chip">跳过 {importResult.skipped_count} 条</span>
              {importResult.failed_count > 0 && (
                <span className="chip chip-error">失败 {importResult.failed_count} 条</span>
              )}
              {(importResult.disputed_count ?? 0) > 0 && (
                <span className="chip chip-warning">待裁定 {importResult.disputed_count} 条</span>
              )}
            </div>

            {/* 结果页原来只说「成功 4 条」，不说是哪 4 个 —— 导完还得自己去客户列表里翻。 */}
            {importResult.created.length > 0 && (
              <section style={{ marginBottom: 16 }}>
                <div style={importSectionTitle}>已新增的客户</div>
                {importResult.created.map((item) => {
                  const suspended = (importResult.disputed ?? []).some((d) => d.row === item.row)
                  return (
                    <div key={`result-new-${item.row ?? item.name}`} style={importRow}>
                      <span style={importRowNo}>{item.row ? `第 ${item.row} 行` : '—'}</span>
                      <span style={importRowText}>
                        {item.name}
                        {suspended && (
                          <span style={{ color: 'var(--crm-warning)' }}> — 已建，但挂起等你在疑似重复里裁定</span>
                        )}
                      </span>
                    </div>
                  )
                })}
              </section>
            )}

            {(importResult.disputed ?? []).length > 0 && (
              <section style={{ marginBottom: 16 }}>
                <div style={importSectionTitle}>已进入待裁定的疑似重复</div>
                {(importResult.disputed ?? []).map((item) => (
                  <div key={`result-disputed-${item.row}-${item.name}`} style={importRow}>
                    <span style={importRowNo}>{item.row ? `第 ${item.row} 行` : '—'}</span>
                    <span style={importRowText}>
                      {item.name}
                      <span style={{ color: 'var(--crm-text-3)' }}>
                        {item.candidate && item.candidate !== item.name
                          ? ` — 与已有客户「${item.candidate}」相似${item.score ? `（${item.score} 分）` : ''}`
                          : ' — 与系统里已有客户同名'}
                      </span>
                      {(item.reasons ?? []).length > 0 && (
                        <span style={{ color: 'var(--crm-text-3)' }}>：{(item.reasons ?? []).join('、')}</span>
                      )}
                    </span>
                  </div>
                ))}
              </section>
            )}

            {importResult.skipped.length > 0 && (
              <section style={{ marginBottom: 16 }}>
                <div style={importSectionTitle}>被跳过的行</div>
                {importResult.skipped.map((item) => (
                  <div key={`skip-${item.row ?? ''}-${item.name}`} style={importRow}>
                    <span style={importRowNo}>{item.row ? `第 ${item.row} 行` : '—'}</span>
                    <span style={importRowText}>
                      {item.name}
                      <div style={{ color: 'var(--crm-text-3)' }}>{item.reason}</div>
                    </span>
                  </div>
                ))}
              </section>
            )}

            {importResult.failed.length > 0 && (
              <section>
                <div style={importSectionTitle}>
                  失败的行
                  {(importResult.failed_truncated ?? false) && (
                    <span style={{ fontWeight: 400, color: 'var(--crm-text-3)' }}>
                      （只列出前 {importResult.failed.length} 行，共 {importResult.failed_total ?? importResult.failed_count} 行）
                    </span>
                  )}
                </div>
                {importResult.failed.map((item) => (
                  <div key={`fail-${item.row ?? ''}-${item.name}`} style={importRow}>
                    <span style={importRowNo}>{item.row ? `第 ${item.row} 行` : '—'}</span>
                    <span style={importRowText}>
                      {item.name || (
                        <span style={{ color: 'var(--crm-text-3)' }}>（这一行没填客户名称）</span>
                      )}
                      <div style={{ color: 'var(--crm-error)' }}>{item.reason}</div>
                    </span>
                  </div>
                ))}
              </section>
            )}
          </div>
        )}
      </Modal>

      {/* 批量打标签 */}
      <Modal
        width={520}
        title={`批量打标签（已选 ${selectedIds.length} 个客户）`}
        visible={batchTagOpen}
        onCancel={() => setBatchTagOpen(false)}
        onOk={() => batchTagMutation.mutate()}
        confirmLoading={batchTagMutation.isPending}
        okText="确认"
        okButtonProps={{ disabled: batchTagIds.length === 0 }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              操作方式
            </div>
            <Select
              style={{ width: '100%' }}
              value={batchTagMode}
              onChange={(value) => setBatchTagMode(value as 'add' | 'replace' | 'remove')}
              optionList={[
                { value: 'add', label: '追加（保留原有标签）' },
                { value: 'replace', label: '覆盖（先清空原有标签）' },
                { value: 'remove', label: '摘除（移除选中的标签）' },
              ]}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>标签</div>
            <Select
              multiple
              style={{ width: '100%' }}
              placeholder="选择一个或多个标签"
              value={batchTagIds}
              onChange={(value) => setBatchTagIds((value as number[]) ?? [])}
              loading={tagsQuery.isLoading}
              optionList={(tagsQuery.data ?? []).map((tag) => ({
                value: tag.id,
                label: `${tag.name}（${tag.type}）`,
              }))}
            />
            {(tagsQuery.data ?? []).length === 0 && !tagsQuery.isLoading && (
              <div style={{ marginTop: 6, fontSize: 12, color: 'var(--crm-text-3)' }}>
                还没有标签，先去「系统设置 › 客户标签」建一个。
              </div>
            )}
          </div>
        </div>
      </Modal>

      {/* 批量转移负责人 */}
      <Modal
        title={`批量转移负责人（已选 ${selectedIds.length} 个客户）`}
        visible={batchTransferOpen}
        onCancel={() => setBatchTransferOpen(false)}
        onOk={() => batchTransferMutation.mutate()}
        confirmLoading={batchTransferMutation.isPending}
        okText="确认转移"
      >
        <UserSearchSelect
          queryScope="customer-batch-transfer"
          enabled={batchTransferOpen}
          style={{ width: '100%' }}
          placeholder="按姓名或登录账号搜索新的负责人"
          value={batchOwnerId ?? undefined}
          onChange={(value) => setBatchOwnerId(value ?? null)}
        />
        <div style={{ marginTop: 12, color: 'var(--crm-text-3)', fontSize: 12 }}>
          清空选择后确认 = 把这些客户放入公海。每次转移都会记录负责人变更历史。
        </div>
      </Modal>

      <Modal
        title={
          <div className="modal-head">
            <span className="modal-head-icon" aria-hidden>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
                <path d="M3 21h18" />
                <path d="M5 21V8l7-5 7 5v13" />
                <path d="M9.5 21v-5.5h5V21" />
              </svg>
            </span>
            <div>
              新建客户
              <div className="modal-subtitle">只填名称就能建，其余可以后补</div>
            </div>
          </div>
        }
        // 不写 width 时 Semi 用 448，表单挤成一条竖线、八个字段拉出一屏半高。
        visible={modalVisible}
        width={600}
        onCancel={() => setModalVisible(false)}
        onOk={() => {
          if (!form.name.trim()) {
            Toast.warning('客户名称必填')
            return
          }
          createMutation.mutate({
            ...form,
            source: form.source === '其他' ? (otherSource.trim() || '其他') : form.source,
          })
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
        cancelText="取消"
      >
        <div className="form-cards">
          <section className="form-card">
            <div className="form-card-title">客户档案</div>
            <div className="form-card-grid">
              <div className="full">
                <div className="form-field-label">客户名称<span className="req">*</span></div>
                <Input
                  value={form.name}
                  onChange={(value) => {
                    setForm({ ...form, name: value })
                    // 名称改了，旧的查重结果就失效了
                    if (dupMatches.length) setDupMatches([])
                  }}
                  onBlur={() => {
                    if (form.name.trim().length >= 2) dedupMutation.mutate()
                  }}
                  placeholder="公司全称，例如：宁波宏远包装制品有限公司"
                />
              </div>

              {/* PRD §6.3：新建时就提示疑似重复，而不是等导入时才拦 */}
              {dupMatches.length > 0 && (
                <div
                  className="full form-tip"
                  style={{ display: 'block', background: 'var(--crm-warning-soft)', color: 'var(--crm-warning)' }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>
                    发现 {dupMatches.length} 个疑似重复客户
                  </div>
                  {dupMatches.map((match) => (
                    <div key={match.id} style={{ fontSize: 12, lineHeight: 1.7 }}>
                      · {match.name}（相似度 {match.score}%：{match.reasons.join('、')}）
                    </div>
                  ))}
                  <div style={{ fontSize: 12, marginTop: 4 }}>
                    如确认是同一家，建议先取消，去已有客户里补资料；确实不同再继续创建。
                  </div>
                </div>
              )}

              <div>
                <div className="form-field-label">客户简称</div>
                <Input
                  value={form.short_name ?? ''}
                  onChange={(value) => setForm({ ...form, short_name: value })}
                  placeholder="日常叫法，例如：宏远"
                />
              </div>
              <div>
                <div className="form-field-label">客户等级</div>
                <Select
                  value={customerLevelValue(form.level)}
                  onChange={(value) => setForm({ ...form, level: customerLevelPayload(value) })}
                  optionList={LEVEL_OPTIONS}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <div className="form-field-label">省份 / 地区</div>
                <Input
                  value={form.region ?? ''}
                  onChange={(value) => setForm({ ...form, region: value })}
                  placeholder="浙江"
                />
              </div>
              <div>
                <div className="form-field-label">客户来源</div>
                <Select
                  value={form.source ?? undefined}
                  onChange={(value) => setForm({ ...form, source: value as string })}
                  optionList={SOURCE_OPTIONS}
                  style={{ width: '100%' }}
                />
                {form.source === '其他' && (
                  <Input
                    aria-label="其他客户来源"
                    value={otherSource}
                    onChange={setOtherSource}
                    maxLength={32}
                    placeholder="请输入具体来源，例如：抖音、小红书（最多32字）"
                    style={{ marginTop: 8 }}
                  />
                )}
              </div>
              <div className="full">
                <div className="form-field-label">详细地址</div>
                <Input
                  value={form.address ?? ''}
                  onChange={(value) => setForm({ ...form, address: value })}
                  placeholder="省市区 + 街道门牌，收货地址以订单为准"
                />
              </div>
              <div className="full">
                <div className="form-field-label">备注</div>
                <TextArea
                  rows={2}
                  value={form.remark ?? ''}
                  onChange={(value) => setForm({ ...form, remark: value })}
                  placeholder="偏好、忌讳、结算习惯……"
                />
              </div>
            </div>
          </section>

          <section className="form-card">
            <div className="form-card-title">谁来跟这个客户</div>
            <div className="form-card-grid">
              <div>
                <div className="form-field-label">负责人</div>
                <UserSearchSelect
                  queryScope="customer-create"
                  enabled={modalVisible && can('customer:assign')}
                  className="owner-picker"
                  style={{ width: '100%' }}
                  placeholder="按姓名或登录账号搜索"
                  disabled={!can('customer:assign')}
                  value={form.owner_id ?? undefined}
                  onChange={(value) => setForm({ ...form, owner_id: value ?? null })}
                />
              </div>
              <div className="form-tip">
                {can('customer:assign')
                  ? '不选就归你自己，之后也能在列表里转移。'
                  : '没有分配权限时新客户归你；要指定别人请联系管理员。'}
              </div>
            </div>
          </section>
        </div>
      </Modal>
    </div>
  )
}
