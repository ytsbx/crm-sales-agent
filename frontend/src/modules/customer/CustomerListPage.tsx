import { UNRATED_OPTION, customerLevelValue, customerLevelPayload } from '../../shared/customer-level'
import { useRef, useState, type ComponentProps } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Button, Input, Modal, Select, Table, Tag, Toast, TextArea } from '@douyinfe/semi-ui'

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
import { listUsers } from '../../shared/api/system'
import { optionMatcher } from '../../shared/components/optionMatch'
import { newRequestKey } from '../../shared/api/requestKey'
import { useAuthStore } from '../../shared/store/auth'
import { usePermissions } from '../../shared/hooks/permissions'
import type { Customer } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import BlockedHint, { useBlockedNotify } from '../../shared/components/BlockedHint'

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
type TagColor = ComponentProps<typeof Tag>['color']
const STAGE_TONE: Record<string, TagColor> = {
  understanding: 'grey',
  quote: 'blue',
  sample: 'orange',
  first_order: 'cyan',
  repeat: 'purple',
  stable: 'green',
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

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [level, setLevel] = useState<string | undefined>()
  // 默认范围跟着数据权限走：管理员看全部，业务员看自己的——
  // 否则管理员打开客户中心会是一片空白（他名下本来就没有客户）
  const [scope, setScope] = useState<Scope>(
    currentUser?.data_scope === 'all' ? 'all' : 'mine',
  )
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const [modalVisible, setModalVisible] = useState(false)
  const [form, setForm] = useState<CustomerPayload>(EMPTY_FORM)
  const [otherSource, setOtherSource] = useState('')
  // 批量导入导出
  const fileInputRef = useRef<HTMLInputElement>(null)
  // 本次「新建客户」表单的幂等键：打开表单时生成，成功后清空（第八批 8.15）
  const createRequestKeyRef = useRef('')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<{
    created_count: number
    skipped_count: number
    failed_count: number
    created: Array<{ name: string }>
    skipped: Array<{ name: string; reason: string }>
    failed: Array<{ name: string; reason: string }>
  } | null>(null)
  const [exportModalOpen, setExportModalOpen] = useState(false)
  const [exportPurpose, setExportPurpose] = useState<CustomerExportPurpose | undefined>()
  const [exportPurposeNote, setExportPurposeNote] = useState('')
  const [exporting, setExporting] = useState(false)

  // 「导出」这一路的两处说明，共用同一个判据（判据本身没动，只是把原因写出来）：
  //   ① 工具栏上的导出按钮：没权限时灰着，旁边一行灰字；
  //   ② 导出弹窗的确认键：没选用途 / 选了「其他」没填说明时灰着，弹窗底部列出缺什么。
  // 两条规矩见 shared/components/BlockedHint.tsx：看得见（常驻灰字）+ 点得动（点了有反应）。
  const canExport = can('customer:export')
  const exportBlockedReasons = [
    ...(!exportPurpose ? ['请选择导出用途。'] : []),
    // 用途选「其他」时后端要求写清具体用途（schema 里有最小长度校验），
    // 只把「其他」两个字交上去会被拒 —— 先说清楚，别让人白点一次。
    ...(exportPurpose === 'other' && !exportPurposeNote.trim()
      ? ['选择「其他」时必须填写导出用途说明。']
      : []),
  ]
  const notifyExportBlocked = useBlockedNotify(canExport ? [] : [CUSTOMER_EXPORT_PERMISSION_REASON])
  const notifyExportPurposeBlocked = useBlockedNotify(exportBlockedReasons, '导出')

  const tokenHeader = () => {
    const token = JSON.parse(localStorage.getItem('crm-auth') ?? '{}')?.state?.token
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
  // 六阶段分布（后端按当前数据范围自动推导）
  const stageDistQuery = useQuery({
    queryKey: ['customer-stage-distribution'],
    queryFn: () => listStageDistribution(),
  })
  const usersQuery = useQuery({
    queryKey: ['assignable-users'],
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
    enabled: batchTransferOpen,
  })
  // ⚠️ 新建客户弹窗里的"负责人"选择器要**自己拉一次**用户列表。
  // 上面那个 `usersQuery` 被 `enabled: batchTransferOpen` 门控着（只有批量转移
  // 弹窗打开才请求），直接复用它的话，新建客户弹窗里这个下拉**永远是"暂无数据"**
  // —— 我第一版就是这么写的，截图一眼看出来（三个下拉全空）。
  const ownerOptionsQuery = useQuery({
    queryKey: ['assignable-users', 'for-create'],
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
    enabled: modalVisible && can('customer:assign'),
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
      const response = await axios.post('/api/v1/customers/import', formData, {
        headers: tokenHeader(),
      })
      const body = response.data
      if (body.code !== 0) throw new Error(body.message)
      Toast.success(body.message)
      setImportResult(body.data)
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '导入失败')
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
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

  const columns = [
    {
      title: '客户名称',
      dataIndex: 'name',
      render: (text: string, record: Customer) => (
        <a style={{ color: 'var(--crm-primary)' }} onClick={() => navigate(`/customers/${record.id}`)}>
          {text}
        </a>
      ),
    },
    {
      title: '等级',
      dataIndex: 'level',
      width: 80,
      render: (value: string | null) =>
        value ? (
          <Tag color={value === 'A' ? 'green' : value === 'B' ? 'blue' : 'grey'}>{value}</Tag>
        ) : (
          <Tag color="grey">未分级</Tag>
        ),
    },
    {
      // 领导六阶段：由订单/打样/报价事实自动推导（customer/stage.py），不占销售一分钟
      title: '阶段',
      dataIndex: 'stage',
      width: 96,
      render: (_: unknown, record: Customer) =>
        record.stage ? (
          <Tag color={STAGE_TONE[record.stage] ?? 'grey'}>{record.stage_label ?? record.stage}</Tag>
        ) : (
          '-'
        ),
    },
    {
      title: '当前商机',
      dataIndex: 'current_opportunity_count',
      width: 90,
      render: (value: number | undefined) => value ?? 0,
    },
    {
      title: '近12月项目',
      dataIndex: 'recent_order_count',
      width: 90,
      render: (value: number | undefined) => value ?? 0,
    },
    {
      title: '历史有效订单',
      dataIndex: 'historical_order_count',
      width: 105,
      render: (value: number | undefined) => value ?? 0,
    },
    {
      title: '活跃状态',
      dataIndex: 'activity_status',
      width: 90,
      render: (value: string | undefined) =>
        value === 'active' ? '活跃' : value === 'stale' ? '沉睡' : value === 'watch' ? '关注' : '待补核',
    },
    { title: '地区', dataIndex: 'region', width: 100, render: (v: string | null) => v ?? '-' },
    {
      // PRD §6.1：客户列表要能直接看到标签
      title: '标签',
      dataIndex: 'tags',
      width: 180,
      render: (tags: Customer['tags']) =>
        (tags ?? []).length === 0 ? (
          <span style={{ color: 'var(--crm-text-3)' }}>-</span>
        ) : (
          <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4 }}>
            {(tags ?? []).map((tag) => (
              <Tag key={tag.id} size="small">
                {tag.name}
              </Tag>
            ))}
          </span>
        ),
    },
    { title: '来源', dataIndex: 'source', width: 120, render: (v: string | null) => v ?? '-' },
    {
      title: '联系人',
      dataIndex: 'contact_count',
      width: 90,
    },
    {
      title: '负责人',
      dataIndex: 'owner_name',
      width: 110,
      render: (v: string | null) => v ?? '公海',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (v === 'active' ? '正常' : v),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader title="客户中心" subtitle="客户、联系人与归属都在这里统一管理" />

      <SectionCard>
        <div className="toolbar">
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
          <Button onClick={() => downloadCsv('/api/v1/customers/import-template', '客户导入模板.csv')}>
            下载模板
          </Button>
          {/* 按当前筛选导出（POST /customers/export）：列表页筛出什么就导出什么，
              数据范围后端强制；导出是独立权限（customer:export），与查看分开。
              按钮改成**常驻但灰着**：从前没这个权限的角色（四种内置角色都没有）看到的是
              "工具栏上根本没有导出"，只会以为功能没做。灰着的同时旁边有一行灰字说清
              缺哪个权限、找谁开，点按钮或点那行字都会弹同一句话（文案同源，见
              blocked-hint-reasons.ts 的 blockedToastText）。
              ⚠️ 判据没放松：disabled 里仍然是同一个 can('customer:export')，
              灰着时按钮加 pointerEvents:'none' 是为了让点击落到外层容器上（原生禁用
              控件不派发点击事件，光包一层收不到 —— 判例见 PriceCenterPage.tsx:880）。 */}
          <div className="blocked-hint-click" onClick={notifyExportBlocked}>
            <Button
              disabled={!canExport}
              style={canExport ? undefined : { pointerEvents: 'none' }}
              onClick={() => setExportModalOpen(true)}
            >
              导出
            </Button>
          </div>
          {!canExport && (
            // flexShrink: 0 + 够宽的 maxWidth —— 这行字不许被工具栏挤成两行（实测第一版
            // 挤成了「…开通「客户导出」权 / 限。」，一句话被劈成两截）；挤不下就整行换到下一行。
            <div
              data-blocked-click="1"
              className="blocked-hint-click"
              onClick={notifyExportBlocked}
              style={{ display: 'block', maxWidth: 340, flexShrink: 0, fontSize: 12, lineHeight: 1.7, color: 'var(--crm-text-3)' }}
            >
              {CUSTOMER_EXPORT_PERMISSION_REASON}
            </div>
          )}
          <Button loading={importing} onClick={() => fileInputRef.current?.click()}>
            批量导入
          </Button>
          <Button
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
            {stageDistQuery.data.map((item) => (
              <Tag key={item.stage} color={STAGE_TONE[item.stage] ?? 'grey'} type="light">
                {item.label} {item.count}
              </Tag>
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
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          size="middle"
          empty={emptyText(query, '没有符合条件的客户')}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds((keys ?? []) as number[]),
          }}
          pagination={{
            currentPage: page,
            pageSize,
            total: query.data?.total ?? 0,
            showSizeChanger: true,
            pageSizeOpts: [10, 20, 50],
            onPageChange: (nextPage: number) => setPage(nextPage),
            onPageSizeChange: (nextSize: number) => {
              setPageSize(nextSize)
              setPage(1)
            },
          }}
        />
      </SectionCard>

      <Modal
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
        title="导入结果"
        visible={Boolean(importResult)}
        onCancel={() => setImportResult(null)}
        onOk={() => setImportResult(null)}
        okText="知道了"
        cancelText="关闭"
        width={620}
      >
        {importResult && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', gap: 16 }}>
              <span className="chip chip-primary">成功 {importResult.created_count} 条</span>
              <span className="chip chip-warning">
                跳过疑似重复 {importResult.skipped_count} 条
              </span>
              {importResult.failed_count > 0 && (
                <span className="chip chip-error">失败 {importResult.failed_count} 条</span>
              )}
            </div>
            {importResult.skipped.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>被跳过的行</div>
                {importResult.skipped.map((item) => (
                  <div
                    key={`skip-${item.name}`}
                    style={{ fontSize: 12, color: 'var(--crm-text-2)', lineHeight: 1.8 }}
                  >
                    {item.name} —— {item.reason}
                  </div>
                ))}
              </div>
            )}
            {importResult.failed.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>失败的行</div>
                {importResult.failed.map((item) => (
                  <div
                    key={`fail-${item.name}`}
                    style={{ fontSize: 12, color: 'var(--crm-error)', lineHeight: 1.8 }}
                  >
                    {item.name} —— {item.reason}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* 批量打标签 */}
      <Modal
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
        <Select
          style={{ width: '100%' }}
          placeholder="选择新的负责人"
          showClear
          value={batchOwnerId ?? undefined}
          onChange={(value) => setBatchOwnerId((value as number) ?? null)}
          loading={usersQuery.isLoading}
          filter={optionMatcher}
          optionList={(usersQuery.data?.items ?? []).map((item) => ({
            value: item.id,
            label: `${item.name}（${item.department ?? '未分配部门'}）`,
          }))}
        />
        <div style={{ marginTop: 12, color: 'var(--crm-text-3)', fontSize: 12 }}>
          清空选择后确认 = 把这些客户放入公海。每次转移都会记录负责人变更历史。
        </div>
      </Modal>

      <Modal
        title="新建客户"
        visible={modalVisible}
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
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>客户名称</FormLabel>
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
              style={{
                background: 'var(--crm-warning-soft)',
                color: 'var(--crm-warning)',
                padding: 10,
                borderRadius: 4,
                fontSize: 13,
              }}
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

          {/* 归属：**可以指定**（主人 2026-10-13：「为啥只能谁创建谁是负责人，
              不能指定吗，没有指定再是自己」）。后端一直支持（`CustomerCreate.owner_id`
              + `create_customer` 里"为空才归创建人"），缺的只是这个入口 + 前端类型字段。

              位置：**必须独立成一格、排在客户名称之后**。原来它被塞在
              「客户名称」的标签与输入框之间，界面上就成了「客户名称*」→「负责人」
              →一个没有标签的输入框，看着像客户名称没地方填（主人 2026-10-15 截图）；
              错位是 `fd3eb24` 引入的。这一格原来还有 `marginTop: 12`，那是为了跟
              上面的标签拉开距离用的；现在它是独立一格，网格的 `gap: 12` 已经负责间距，
              再用就会变成 24、跟其它字段不一致。 */}
          <div>
            <FormLabel hint="不选就归你自己，之后也能转移">负责人</FormLabel>
            <Select
              className="owner-picker"
              style={{ width: '100%' }}
              placeholder="不选 = 归你自己"
              showClear
              disabled={!can('customer:assign')}
              value={form.owner_id ?? undefined}
              onChange={(value) => setForm({ ...form, owner_id: (value as number) ?? null })}
              loading={ownerOptionsQuery.isLoading}
              filter={optionMatcher}
              optionList={(ownerOptionsQuery.data?.items ?? []).map((item) => ({
                value: item.id,
                label: `${item.name}（${item.department ?? '未分配部门'}）`,
              }))}
            />
            <div style={{ marginTop: 6, fontSize: 12, color: 'var(--crm-text-3)' }}>
              {can('customer:assign')
                ? '不指定就归「你」；指定给别人时，这位客户从一开始就记在他名下。放进公海请创建后用列表里的「放入公海」。'
                : '你没有客户分配权限，新客户会归到「你」名下；如需指定其他负责人，请联系管理员。'}
            </div>
          </div>

          <div>
            <div style={{ marginBottom: 4 }}>客户简称</div>
            <Input
              value={form.short_name ?? ''}
              onChange={(value) => setForm({ ...form, short_name: value })}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>省份 / 地区</div>
              <Input
                value={form.region ?? ''}
                onChange={(value) => setForm({ ...form, region: value })}
                placeholder="浙江"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户等级</div>
              <Select
                value={customerLevelValue(form.level)}
                onChange={(value) => setForm({ ...form, level: customerLevelPayload(value) })}
                optionList={LEVEL_OPTIONS}
                style={{ width: '100%' }}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>客户来源</div>
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
          <div>
            <div style={{ marginBottom: 4 }}>详细地址</div>
            <Input
              value={form.address ?? ''}
              onChange={(value) => setForm({ ...form, address: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注</div>
            <Input
              value={form.remark ?? ''}
              onChange={(value) => setForm({ ...form, remark: value })}
            />
          </div>
        </div>
      </Modal>
    </div>
  )
}
