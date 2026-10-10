import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AutoComplete, Button, DatePicker, Input, Modal, Popconfirm, Select, Switch, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import { listCustomers } from '../../shared/api/customer'
import { createItem, createOpportunity, listOpportunities } from '../../shared/api/opportunity'
import {
  createCost,
  expireCost,
  createCustomerPriceRule,
  createLogisticsRate,
  createPriceRule,
  deleteCustomerPriceRule,
  deleteLogisticsRate,
  disablePriceRule,
  restorePriceRule,
  listCosts,
  listCustomerPriceRules,
  listLogisticsRates,
  listPricePermissions,
  listPriceRules,
  listSkusForPricing,
  listPricingHistory,
  lookupPrice,
  savePricePermission,
  updateCustomerPriceRule,
  updateLogisticsRate,
  updatePriceRule,
  type CostRecord,
  type CustomerPriceRow,
  type LogisticsRateRow,
  type PriceLookupResult,
  type PricePermissionRow,
  type PriceRuleRow,
  type PricingHistoryRow,
} from '../../shared/api/pricing'
import PageHeader from '../../shared/components/PageHeader'
import CsvImportButtons from '../../shared/components/CsvImportButtons'
import { usePermissions } from '../../shared/hooks/permissions'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import { optionMatcher, withCode } from '../../shared/components/optionMatch'

const TABS = [
  { tab: '客户查价', itemKey: 'lookup' },
  { tab: '成本', itemKey: 'costs' },
  { tab: '价格规则', itemKey: 'rules' },
  { tab: '客户特殊价', itemKey: 'customer-prices' },
  { tab: '价格权限', itemKey: 'permissions' },
  { tab: '运费费率', itemKey: 'logistics' },
  { tab: '核价历史', itemKey: 'history' },
]

/** 空值/次要信息统一用三级文字色（与项目其它页面一致）。 */
const HINT = { color: 'var(--crm-text-3)' }

/** 核价历史里会出现审计日志的 business_type（与后端 /pricing/history 的取值一致）。 */
const HISTORY_TYPE_LABEL: Record<string, string> = {
  quote: '报价单',
  quote_item: '报价明细',
  product_cost: '成本',
  price_rule: '价格规则',
  customer_price_rule: '客户特殊价',
}

/**
 * 快照里的内部字段名 → 中文（2026-10-06 主人反馈）。
 *
 * 原来的实现直接把内部名拼上屏：`id: - → 5284；amount: - → 20；remark: - → null`。
 * 那是"机器话"——字段名是英文内部名、空值原样显示、还把新增说成"新增"两个字
 * 等于把"动作"列又抄了一遍。数据本身存得很好（改前改后快照都在），
 * 缺的是把它翻成人话这一步。
 */
const HISTORY_FIELD_LABEL: Record<string, string> = {
  quote_no: '报价单号',
  status: '状态',
  status_label: '状态',
  current_version_no: '当前版本',
  current_version_amount: '报价总额',
  valid_until: '有效期',
  owner_id: '负责人',
  owner_name: '负责人',
  approval_status: '审批状态',
  approval_required: '需审批',
  opportunity_id: '商机',
  customer_id: '客户',
  contact_id: '联系人',
  quantity: '数量',
  quoted_price: '单价',
  amount: '金额',
  total_amount: '总额',
  sku_id: '产品',
  sku_code: '产品编码',
  sku_name: '产品名称',
  purchase_cost: '采购成本',
  production_cost: '生产成本',
  package_cost: '包装成本',
  processing_cost: '加工成本',
  total_cost: '总成本',
  standard_price: '标准价',
  guide_price: '指导价',
  minimum_price: '最低保护价',
  target_margin: '目标利润率',
  min_qty: '起订量',
  max_qty: '最大数量',
  customer_level: '客户等级',
  agreed_price: '约定价',
  price_source: '价格来源',
  profit_rate_snapshot: '利润率',
  effective_from: '生效日',
  effective_to: '失效日',
  remark: '备注',
  currency: '币种',
  calculation: '计算口径',
  item_count: '明细条数',
  count: '条数',
}

/** 金额类字段：显示时带上 ¥，否则「20」看不出是钱还是件。 */
const MONEY_FIELDS = new Set([
  'current_version_amount',
  'amount',
  'total_amount',
  'quoted_price',
  'purchase_cost',
  'production_cost',
  'package_cost',
  'processing_cost',
  'total_cost',
  'standard_price',
  'guide_price',
  'minimum_price',
  'agreed_price',
])

/**
 * 这些是**技术字段**：改了也不该出现在「变更内容」里。
 *
 * 实测发现（2026-10-06 在真实数据上复看）：新增明细那条渲染成了
 * 「id：空 → 5,284；金额：空 → ¥20；产品：空 → 678」——
 * `id` 是内部编号、`678` 是产品的内部编号，对使用者毫无意义，
 * 而且会把真正有用的"数量/单价"挤出前四位。
 */
const SKIP_FIELDS = new Set([
  'id',
  'item_id',
  'sku_id',
  'customer_id',
  'contact_id',
  'opportunity_id',
  'owner_id',
  'reviewer_id',
  'created_at',
  'updated_at',
  'current_version_id',
  'quote_version_id',
  'version_id',
  'converted_inquiry_id',
])

/** 英文枚举值 → 中文。快照里存的是 `approved` 这种值，直接上屏是机器话。 */
const VALUE_LABEL: Record<string, string> = {
  approved: '已通过',
  rejected: '未通过',
  pending: '审批中',
  not_submitted: '未提交',
  approval_rejected: '审批未通过',
  pending_approval: '待审批',
  draft: '草稿',
  sent: '已发送',
  accepted: '已接受',
  declined: '客户拒绝',
  converted: '已转客户',
  open: '进行中',
  closed: '已关闭',
  active: '启用',
  disabled: '停用',
}

/**
 * 各类型「新增 / 删除」时值得说出来的关键字段（按顺序取）。
 * 不是把整个快照倒出来——快照里有二三十个字段，全列出来等于没重点。
 */
const SUMMARY_FIELDS: Record<string, string[]> = {
  quote: ['quote_no', 'status_label', 'current_version_amount', 'valid_until'],
  quote_item: ['sku_code', 'quantity', 'quoted_price', 'amount'],
  product_cost: [
    'sku_code',
    'purchase_cost',
    'production_cost',
    'package_cost',
    'processing_cost',
    'total_cost',
  ],
  price_rule: ['sku_code', 'standard_price', 'minimum_price', 'target_margin'],
  customer_price_rule: ['customer_id', 'sku_code', 'agreed_price', 'minimum_price'],
}

/** 单个值 → 人能看的字。空值说「空」，不显示 null。 */
function fmtField(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '空'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'string' && VALUE_LABEL[value]) return VALUE_LABEL[value]
  if (typeof value === 'number') {
    if (MONEY_FIELDS.has(key)) return `¥${value.toLocaleString('zh-CN')}`
    // 比率类存的是小数（0.15 = 15%），直接显示 0.15 会让人以为是 0.15%
    if (key === 'target_margin') return `${(value * 100).toFixed(1)}%`
    return value.toLocaleString('zh-CN')
  }
  return String(value)
}

/** 从一份快照里挑出关键字段，拼成「产品编码 ZX-6040-B、数量 100」这种短句。 */
function summaryOfSnapshot(
  businessType: string | null,
  snap: Record<string, unknown>,
): string {
  const fields = SUMMARY_FIELDS[businessType ?? ''] ?? []
  const parts: string[] = []
  for (const key of fields) {
    const value = snap[key]
    if (value === null || value === undefined || value === '') continue
    parts.push(`${HISTORY_FIELD_LABEL[key] ?? key} ${fmtField(key, value)}`)
  }
  return parts.join('、')
}

/**
 * 把一条审计记录翻成人话（2026-10-06 重写）。
 *
 * 三件事都要做对：
 *  ① 新增/删除不能只写「新增」「删除」——那是把"动作"列又抄一遍，
 *     要说出**加了什么**（从快照里挑关键字段）；
 *  ② 修改只列**真正变了**的字段，且用中文名、空值说「空」；
 *  ③ 同一 `business_type` 下不同 `action` 的快照形状**不一样**
 *     （cloned 只记条数、recalculate 只记金额），不能一刀切按"整对象"处理。
 */
function historySummary(row: PricingHistoryRow): string {
  const before = (row.before ?? {}) as Record<string, unknown>
  const after = (row.after ?? {}) as Record<string, unknown>

  // 这些动作只记了一两个字段，按动作单独说清楚，比逐字段比对准确得多
  if (row.action === 'import') {
    // 批量导入：快照里只有统计数字（没有单个 SKU 对象），所以「对象」那列
    // 也反查不出东西，只能在这儿把结果说清楚。
    return `批量导入：新增 ${after.created ?? 0} 条、失败 ${after.failed ?? 0} 条、跳过 ${after.skipped ?? 0} 条`
  }
  if (row.action === 'add_item' || row.action === 'delete_item') {
    // 明细增删：要直接说清"加了哪一行、数量单价多少"，
    // 别让 id / sku_id 这些内部编号占掉前四位（实测就会这样）
    const snap = row.action === 'add_item' ? after : before
    const parts = ['sku_code', 'sku_name', 'quantity', 'quoted_price', 'amount']
      .map((key) =>
        snap[key] === null || snap[key] === undefined || snap[key] === ''
          ? null
          : `${HISTORY_FIELD_LABEL[key] ?? key} ${fmtField(key, snap[key])}`,
      )
      .filter((item): item is string => Boolean(item))
    const verb = row.action === 'add_item' ? '新增一行明细' : '删除一行明细'
    return parts.length ? `${verb}：${parts.join('、')}` : verb
  }
  if (row.action === 'accept') return '客户接受本版报价'
  if (row.action === 'decline' || row.action === 'reject') return '客户拒绝本版报价'
  if (row.action === 'submit_approval') return '提交审批'
  if (row.action === 'withdraw_approval') return '撤回审批'
  if (row.action === 'mark_sent') return '标记为已发送'
  if (row.action === 'clone') {
    const n = after.copied_items ?? after.copied
    return n === null || n === undefined ? '复制了一份明细' : `复制了 ${n} 条明细`
  }
  if (row.action === 'set_items') {
    const n = after.count
    return n === null || n === undefined ? '明细整批替换' : `明细整批替换为 ${n} 条`
  }
  if (row.action === 'refresh_prices') {
    return `按最新价重算：刷新 ${after.refreshed ?? 0} 条、跳过 ${after.skipped ?? 0} 条`
  }
  if (row.action === 'update_item') {
    const oldPrice = before.quoted_price
    const newPrice = after.quoted_price
    if (oldPrice !== undefined && newPrice !== undefined) {
      return `明细单价 ${fmtField('quoted_price', oldPrice)} → ${fmtField('quoted_price', newPrice)}`
    }
  }

  if (row.action === 'create' || row.action === 'delete') {
    const snap = row.action === 'create' ? after : before
    const parts = summaryOfSnapshot(row.business_type, snap)
    const verb = row.action === 'create' ? '新增' : '删除'
    // 挑不出关键字段时也别只说「新增」——把能确定的类型说进去
    return parts ? `${verb}：${parts}` : `${verb}了一条${HISTORY_TYPE_LABEL[row.business_type ?? ''] ?? '记录'}`
  }

  // 修改：只列真正变了的字段
  const changed: string[] = []
  const keys = [...new Set([...Object.keys(after), ...Object.keys(before)])]
  for (const key of keys) {
    // 技术字段（内部编号、时间戳）一律不展示：用户看不懂，还会挤掉有用信息
    if (SKIP_FIELDS.has(key)) continue
    const oldValue = before[key]
    const newValue = after[key]
    if (String(oldValue ?? '') === String(newValue ?? '')) continue
    changed.push(
      `${HISTORY_FIELD_LABEL[key] ?? key}：${fmtField(key, oldValue)} → ${fmtField(key, newValue)}`,
    )
    if (changed.length >= 4) {
      changed.push('…')
      break
    }
  }
  if (changed.length) return changed.join('；')

  // 没字段差异（如 recalculate 只记了重算后的金额）：退回用快照说结果
  const fallback = summaryOfSnapshot(row.business_type, after)
  return fallback ? `重算：${fallback}` : '无字段变化'
}

const money = (value?: number | null) => (value === null || value === undefined ? '-' : `¥${value}`)

/**
 * 价格规则 / 客户特殊价弹窗的**空表单**（模块级常量）。
 *
 * 放在组件外是为了两处共用同一份"空"：`useState` 的初值与"关闭后复位"
 * 各写一份的话，加字段时必然漏一处 —— 漏的那处会在下次打开时残留上一次的值。
 */
const emptyRuleForm = {
  sku_id: null as number | null,
  customer_level: '',
  min_qty: '0',
  max_qty: '',
  standard_price: '',
  guide_price: '',
  minimum_price: '',
  target_margin: '',
  effective_from: '',
  effective_to: '',
  remark: '',
}
const emptyCustomerPriceForm = {
  customer_id: null as number | null,
  sku_id: null as number | null,
  min_qty: '0',
  max_qty: '',
  agreed_price: '',
  minimum_price: '',
  effective_from: '',
  effective_to: '',
  remark: '',
}

/**
 * 把表单里的数字文本解析成提交值（2026-10-09 审查 R02）。
 *
 * 从前各处直接写 `Number(x)`，于是"填了字母"会**静默**变成 `NaN`，
 * 而 `JSON.stringify` 又把 `NaN` 序列化成 `null` —— 后端把 `max_qty: null`
 * 理解成"不限数量"。实测（真实浏览器）：数量上限 97000 改成 `abc` 点保存，
 * 上限被清空成"不限"，**页面没有任何提示**，而且从此任何数量都命中这条价。
 *
 * 所以把三种情况分开：
 *   - 空串 / 只有空白 → `null`（= 明确清空，这是合法操作）；
 *   - 能解析成有限数字 → 数字；
 *   - 其它（字母、`1abc`、`Infinity`…）→ `NaN`，调用方**必须**拦下来并提示，
 *     绝不能当成 null 提交。
 */
function parseOptionalNumber(raw: string): number | null {
  const text = String(raw ?? '').trim()
  if (text === '') return null
  const n = Number(text)
  return Number.isFinite(n) ? n : NaN
}

/** 数量类字段：同上，另外限制最多 3 位小数（与库列 `Numeric(16,3)` 对齐）。
 *
 * 不在前端先拦的话，`1.2349` 会被数据库静默舍成 `1.235`，用户以为上限是 1.2349，
 * 之后看到"区间重叠"也复现不出原因（审查 R04 的同一个坑）。
 */
function parseQuantity(raw: string): number | null {
  const n = parseOptionalNumber(raw)
  if (n === null || Number.isNaN(n)) return n
  const decimals = (String(raw).split('.')[1] ?? '').length
  return decimals > 3 ? NaN : n
}

/** 数字字段的中文名：解析失败时提示要点名是哪个字段（R02）。 */
const NUMBER_FIELD_LABEL: Record<string, string> = {
  min_qty: '数量下限',
  max_qty: '数量上限',
  standard_price: '标准价',
  guide_price: '指导价',
  minimum_price: '最低保护价',
  target_margin: '目标利润率',
  agreed_price: '约定价',
}

export default function PriceCenterPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { can } = usePermissions()
  const canManage = can('price:manage')

  // 默认落在「客户查价」：这是价格中心最高频的入口，
  // 之前默认停在「成本」，用户进来得先自己找页签。
  const [activeKey, setActiveKey] = useState('lookup')
  const [costSkuId, setCostSkuId] = useState<number | undefined>()
  const [costVisible, setCostVisible] = useState(false)
  const [costForm, setCostForm] = useState({
    purchase_cost: '',
    package_cost: '',
    production_cost: '',
    processing_cost: '',
    effective_from: new Date().toISOString().slice(0, 10),
  })
  const [ruleVisible, setRuleVisible] = useState(false)
  // 价格规则搜索与分页（2026-10-10 主人："没有搜索功能，一旦很多就太乱了"）。
  // 从前写死 `{page:1, page_size:100}` —— 既搜不了，**第 101 条以后也根本看不到**。
  const [ruleKeyword, setRuleKeyword] = useState('')
  const [rulePage, setRulePage] = useState(1)
  const [rulePageSize, setRulePageSize] = useState(20)
  // 客户特殊价同一型问题：也写死 100 条、也没有搜索（同一次修掉）
  const [cpKeyword, setCpKeyword] = useState('')
  const [cpPage, setCpPage] = useState(1)
  const [cpPageSize, setCpPageSize] = useState(20)
  // 价格规则：新增与修改**共用同一个弹窗**（`ruleEditing` 为空 = 新增），
  // 与下面运费费率同一范式，避免两套弹窗各自漂移。
  const [ruleEditing, setRuleEditing] = useState<PriceRuleRow | null>(null)
  const [ruleForm, setRuleForm] = useState(emptyRuleForm)
  const [customerPriceVisible, setCustomerPriceVisible] = useState(false)
  // 客户特殊价：同样新增与修改共用一个弹窗（`customerPriceEditing` 为空 = 新增）
  const [customerPriceEditing, setCustomerPriceEditing] = useState<CustomerPriceRow | null>(null)
  const [customerPriceForm, setCustomerPriceForm] = useState(emptyCustomerPriceForm)

  // 客户查价（产品报价中心 · 第一批）：选客户+SKU+数量 → 适用价与来源
  const [lookupCustomerId, setLookupCustomerId] = useState<number | undefined>()
  const [lookupSkuId, setLookupSkuId] = useState<number | undefined>()
  const [lookupQty, setLookupQty] = useState('1')
  const [lookupResult, setLookupResult] = useState<PriceLookupResult | null>(null)
  const [lookupLoading, setLookupLoading] = useState(false)
  // 选品打通（§7 行 2）：查价结果一键加入商机需求
  const [lookupOppId, setLookupOppId] = useState<number | undefined>()
  const [addingToOpp, setAddingToOpp] = useState(false)
  const [creatingQuickOpp, setCreatingQuickOpp] = useState(false)
  const [permissionTarget, setPermissionTarget] = useState<PricePermissionRow | null>(null)
  const [permissionForm, setPermissionForm] = useState({ minimum_margin: '0.15', can_approve: false })
  // 运费费率：新增与修改**共用同一个弹窗**（`rateEditing` 为空 = 新增）。
  // 表单值一律用字符串存（受控输入），提交时再转数字 —— 空串代表"没填"。
  const [rateVisible, setRateVisible] = useState(false)
  const [rateEditing, setRateEditing] = useState<LogisticsRateRow | null>(null)
  const emptyRateForm = {
    provider: '',
    origin_region: '',
    destination_region: '',
    shipping_method: '陆运',
    unit_price_per_kg: '',
    unit_price_per_volume: '',
    min_charge: '',
    eta_days: '',
    eta_days_max: '',
    status: 'active' as 'active' | 'inactive',
    remark: '',
  }
  const [rateForm, setRateForm] = useState(emptyRateForm)
  const [historySkuId, setHistorySkuId] = useState<number | undefined>()
  const [historyPage, setHistoryPage] = useState(1)

  const skusQuery = useQuery({ queryKey: ['skus-for-pricing'], queryFn: listSkusForPricing })
  const lookupOppQuery = useQuery({
    queryKey: ['opportunities-for-lookup'],
    queryFn: () => listOpportunities({ status: 'open', page: 1, page_size: 100 }),
  })
  const customersQuery = useQuery({
    queryKey: ['customers-for-select'],
    queryFn: () => listCustomers({ page: 1, page_size: 100 }),
  })
  const costsQuery = useQuery({
    queryKey: ['costs', costSkuId],
    queryFn: () => listCosts(costSkuId!),
    enabled: Boolean(costSkuId),
  })
  const rulesQuery = useQuery({
    // 关键词/页码进 queryKey：改了才重新请求，翻页不会把上一页的数据当这一页用
    queryKey: ['price-rules', ruleKeyword, rulePage, rulePageSize],
    queryFn: () =>
      listPriceRules({
        keyword: ruleKeyword.trim() || undefined,
        page: rulePage,
        page_size: rulePageSize,
      }),
    enabled: activeKey === 'rules',
    // 翻页时保留上一页内容，避免表格闪成空白（后端分页是真实的，数据量可能很大）
    placeholderData: (prev) => prev,
  })
  const customerPricesQuery = useQuery({
    queryKey: ['customer-price-rules', cpKeyword, cpPage, cpPageSize],
    queryFn: () =>
      listCustomerPriceRules({
        keyword: cpKeyword.trim() || undefined,
        page: cpPage,
        page_size: cpPageSize,
      }),
    placeholderData: (prev) => prev,
    enabled: activeKey === 'customer-prices',
  })
  const permissionsQuery = useQuery({
    queryKey: ['price-permissions'],
    queryFn: listPricePermissions,
    enabled: activeKey === 'permissions',
  })
  const ratesQuery = useQuery({
    queryKey: ['logistics-rates'],
    queryFn: listLogisticsRates,
    enabled: activeKey === 'logistics',
  })
  const historyQuery = useQuery({
    queryKey: ['pricing-history', historyPage, historySkuId],
    queryFn: () =>
      listPricingHistory({ page: historyPage, page_size: 20, sku_id: historySkuId }),
    enabled: activeKey === 'history',
  })

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['costs'] })
    void queryClient.invalidateQueries({ queryKey: ['price-rules'] })
    void queryClient.invalidateQueries({ queryKey: ['customer-price-rules'] })
    void queryClient.invalidateQueries({ queryKey: ['price-permissions'] })
    void queryClient.invalidateQueries({ queryKey: ['logistics-rates'] })
  }

  const costMutation = useMutation({
    mutationFn: () =>
      createCost(costSkuId!, {
        purchase_cost: Number(costForm.purchase_cost || 0),
        package_cost: Number(costForm.package_cost || 0),
        production_cost: Number(costForm.production_cost || 0),
        processing_cost: Number(costForm.processing_cost || 0),
        effective_from: costForm.effective_from,
      }),
    onSuccess: () => {
      Toast.success('成本已保存')
      setCostVisible(false)
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 人工停用一条成本（第十一批 11.5）。这是个**会影响核价**的动作，所以带二次确认；
  // 且不可撤销 —— 撤销按钮故意不做：成本是按日期分版本的，"改回来"的正确做法是
  // 新增一条，而不是把历史抹掉。
  const expireMutation = useMutation({
    mutationFn: (costId: number) => expireCost(costId),
    onSuccess: () => {
      Toast.success('该成本已停用，当天起不再参与核价')
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /**
   * 价格规则：新增与修改共用（`ruleEditing` 非空即修改）。
   *
   * **改的时候只提交允许改的字段**（区间 / 有效期 / 客户等级 / 备注）：
   * 价钱类字段一律不提交 —— 后端会明确拒绝并点名是哪个字段（400），
   * 而口径上"改价钱"就该走「停用旧的 + 新增一条」，这样生效时序在单据上看得见。
   * 弹窗里那几个价钱字段在修改态是**只读展示**，让人看得见当前价、也知道去哪改。
   */
  const ruleMutation = useMutation({
    mutationFn: () => {
      // 解析 + 校验放一处（R02）：数字字段填了字母必须**当场拒绝**，
      // 不能让它变成 null 提交出去（那等于把上限改成"不限"）。
      const nums = {
        min_qty: parseQuantity(ruleForm.min_qty),
        max_qty: parseQuantity(ruleForm.max_qty),
        standard_price: parseOptionalNumber(ruleForm.standard_price),
        guide_price: parseOptionalNumber(ruleForm.guide_price),
        minimum_price: parseOptionalNumber(ruleForm.minimum_price),
        target_margin: parseOptionalNumber(ruleForm.target_margin),
      }
      const bad = Object.entries(nums).find(([, v]) => Number.isNaN(v))
      if (bad) {
        throw new Error(`${NUMBER_FIELD_LABEL[bad[0]] ?? bad[0]}只能填数字（最多 3 位小数）`)
      }
      const common = {
        min_qty: nums.min_qty ?? 0,
        max_qty: nums.max_qty,
        effective_from: ruleForm.effective_from || null,
        effective_to: ruleForm.effective_to || null,
        remark: ruleForm.remark || null,
      }
      if (ruleEditing) {
        return updatePriceRule(ruleEditing.id, {
          customer_level: ruleForm.customer_level || null,
          ...common,
        })
      }
      return createPriceRule({
        sku_id: ruleForm.sku_id,
        customer_level: ruleForm.customer_level || null,
        ...common,
        standard_price: nums.standard_price,
        guide_price: nums.guide_price,
        minimum_price: nums.minimum_price,
        target_margin: nums.target_margin,
      })
    },
    onSuccess: () => {
      Toast.success(ruleEditing ? '价格规则已保存' : '价格规则已创建')
      closeRuleModal()
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /**
   * 客户特殊价：新增与修改共用（`customerPriceEditing` 非空即修改）。
   *
   * 修改走 `PATCH /customer-price-rules/{id}` 原地改 —— 这正是它被设计出来的场景
   * （客户谈定的价涨了两块），此前界面只给「删除」，改价要删了重录。
   * `customer_id` / `sku_id` 不提交：后端明确不支持改归属（等于换一条规则）。
   */
  const customerPriceMutation = useMutation({
    mutationFn: () => {
      // 同一把尺子（R02）：约定价必填且必须是数字，其余数字字段填字母要当场拒绝
      const nums = {
        min_qty: parseQuantity(customerPriceForm.min_qty),
        max_qty: parseQuantity(customerPriceForm.max_qty),
        agreed_price: parseOptionalNumber(customerPriceForm.agreed_price),
        minimum_price: parseOptionalNumber(customerPriceForm.minimum_price),
      }
      const bad = Object.entries(nums).find(([, v]) => Number.isNaN(v))
      if (bad) {
        throw new Error(`${NUMBER_FIELD_LABEL[bad[0]] ?? bad[0]}只能填数字`)
      }
      const common = {
        min_qty: nums.min_qty ?? 0,
        max_qty: nums.max_qty,
        agreed_price: nums.agreed_price,
        minimum_price: nums.minimum_price,
        effective_from: customerPriceForm.effective_from || null,
        effective_to: customerPriceForm.effective_to || null,
        remark: customerPriceForm.remark || null,
      }
      if (customerPriceEditing) {
        return updateCustomerPriceRule(customerPriceEditing.id, common)
      }
      return createCustomerPriceRule({
        customer_id: customerPriceForm.customer_id,
        sku_id: customerPriceForm.sku_id,
        ...common,
      })
    },
    onSuccess: () => {
      Toast.success(customerPriceEditing ? '客户特殊价已保存' : '客户特殊价已创建')
      closeCustomerPriceModal()
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const permissionMutation = useMutation({
    mutationFn: () =>
      savePricePermission(permissionTarget!.role_id, {
        minimum_margin: Number(permissionForm.minimum_margin || 0),
        can_approve: permissionForm.can_approve,
      }),
    onSuccess: () => {
      Toast.success('价格权限已保存')
      setPermissionTarget(null)
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const closeRateModal = () => {
    setRateVisible(false)
    setRateEditing(null)
    setRateForm(emptyRateForm)
  }

  /** 价格规则弹窗：传行 = 改（预填可改字段），不传 = 新增。 */
  const closeRuleModal = () => {
    setRuleVisible(false)
    setRuleEditing(null)
    setRuleForm(emptyRuleForm)
  }
  const openRuleModal = (row?: PriceRuleRow) => {
    setRuleEditing(row ?? null)
    setRuleForm(
      row
        ? {
            sku_id: row.sku_id,
            customer_level: row.customer_level ?? '',
            min_qty: String(row.min_qty ?? '0'),
            max_qty: row.max_qty == null ? '' : String(row.max_qty),
            // 价钱类字段在修改态是只读展示，这里照实填上当前值
            standard_price: row.standard_price == null ? '' : String(row.standard_price),
            guide_price: row.guide_price == null ? '' : String(row.guide_price),
            minimum_price: row.minimum_price == null ? '' : String(row.minimum_price),
            target_margin: row.target_margin == null ? '' : String(row.target_margin),
            effective_from: row.effective_from ?? '',
            effective_to: row.effective_to ?? '',
            remark: row.remark ?? '',
          }
        : emptyRuleForm,
    )
    setRuleVisible(true)
  }

  /** 客户特殊价弹窗：传行 = 改（预填），不传 = 新增。 */
  const closeCustomerPriceModal = () => {
    setCustomerPriceVisible(false)
    setCustomerPriceEditing(null)
    setCustomerPriceForm(emptyCustomerPriceForm)
  }
  const openCustomerPriceModal = (row?: CustomerPriceRow) => {
    setCustomerPriceEditing(row ?? null)
    setCustomerPriceForm(
      row
        ? {
            customer_id: row.customer_id,
            sku_id: row.sku_id,
            min_qty: String(row.min_qty ?? '0'),
            max_qty: row.max_qty == null ? '' : String(row.max_qty),
            agreed_price: String(row.agreed_price ?? ''),
            minimum_price: row.minimum_price == null ? '' : String(row.minimum_price),
            effective_from: row.effective_from ?? '',
            effective_to: row.effective_to ?? '',
            remark: row.remark ?? '',
          }
        : emptyCustomerPriceForm,
    )
    setCustomerPriceVisible(true)
  }

  /** 打开费率弹窗：传行 = 改（预填），不传 = 新增。 */
  const openRateModal = (row?: LogisticsRateRow) => {
    setRateEditing(row ?? null)
    setRateForm(
      row
        ? {
            provider: row.provider,
            origin_region: row.origin_region ?? '',
            destination_region: row.destination_region ?? '',
            shipping_method: row.shipping_method,
            unit_price_per_kg: String(row.unit_price_per_kg ?? ''),
            unit_price_per_volume:
              row.unit_price_per_volume == null ? '' : String(row.unit_price_per_volume),
            min_charge: String(row.min_charge ?? ''),
            eta_days: row.eta_days == null ? '' : String(row.eta_days),
            eta_days_max: row.eta_days_max == null ? '' : String(row.eta_days_max),
            status: row.status === 'inactive' ? 'inactive' : 'active',
            remark: row.remark ?? '',
          }
        : emptyRateForm,
    )
    setRateVisible(true)
  }

  const rateMutation = useMutation({
    mutationFn: () => {
      // 两个地区字段**显式传 null**（不是不传）：不传 = "保持原值"，改不动。
      // 留空 = 不限（匹配时视作通配）；写「全国」是一个**具体取值**，两者行为不同。
      const payload = {
        provider: rateForm.provider.trim(),
        origin_region: rateForm.origin_region.trim() || null,
        destination_region: rateForm.destination_region.trim() || null,
        shipping_method: rateForm.shipping_method.trim() || '陆运',
        unit_price_per_kg: Number(rateForm.unit_price_per_kg || 0),
        unit_price_per_volume: rateForm.unit_price_per_volume.trim()
          ? Number(rateForm.unit_price_per_volume)
          : null,
        min_charge: Number(rateForm.min_charge || 0),
        eta_days: rateForm.eta_days.trim() ? Number(rateForm.eta_days) : null,
        eta_days_max: rateForm.eta_days_max.trim() ? Number(rateForm.eta_days_max) : null,
        status: rateForm.status,
        remark: rateForm.remark.trim() || null,
      }
      return rateEditing
        ? updateLogisticsRate(rateEditing.id, payload)
        : createLogisticsRate(payload)
    },
    onSuccess: () => {
      Toast.success(rateEditing ? '运费费率已保存' : '运费费率已创建')
      closeRateModal()
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const rateDeleteMutation = useMutation({
    mutationFn: (rateId: number) => deleteLogisticsRate(rateId),
    onSuccess: () => {
      Toast.success('运费费率已删除')
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /**
   * 起运地 / 目的地 / 运输方式的候选项：**从现有费率里去重取**。
   *
   * 为什么要给候选：核价匹配是**字符串完全相等**（空 = 不限）。手写与真实取值
   * 差一个字（"华东" vs "华东区"）就匹配不上，会掉到"列出全部费率"那一级 ——
   * 于是最便宜的那条兜底费率胜出，运费被算少。所以既能从已有取值里选，
   * 也允许现场输入新的（给候选而不是写死下拉，是因为第一条费率没有候选可选）。
   */
  const rateOptions = (field: 'origin_region' | 'destination_region' | 'shipping_method') =>
    Array.from(
      new Set(
        (ratesQuery.data ?? [])
          .map((row) => row[field])
          .filter((value): value is string => Boolean(value && value.trim())),
      ),
    ).sort()

  const skuOptions = (skusQuery.data ?? []).map((sku) => ({
    value: sku.id,
    label: `${sku.product_name ?? ''} ${sku.sku_code} ${sku.specification ?? ''}`,
  }))

  // 查价的两个必填项。按钮在没选齐时是禁用态，但**禁用≠可以不解释**：
  // 见下面 Button 外那层 span 的注释（主人 2026-10-06 反馈「灰着没有任何解释」）。
  const lookupReady = Boolean(lookupCustomerId && lookupSkuId)

  return (
    <div className="page-container">
      <PageHeader
        title="价格中心"
        subtitle="核价引擎的全部依据都在这一页：成本、标准价与最低保护价、客户特殊价、各角色的让价权限、运费费率"
        extra={
          <Button theme="solid" onClick={() => navigate('/pricing')}>
            打开核价
          </Button>
        }
      />

      <SectionCard>
        <Tabs type="line" activeKey={activeKey} onChange={setActiveKey} tabList={TABS} />

        <div style={{ marginTop: 16 }}>
          {activeKey === 'lookup' && (
            <div style={{ display: 'grid', gap: 16 }}>
              <div className="toolbar">
                <Select
                  placeholder="选择客户"
                  value={lookupCustomerId}
                  onChange={(value) => setLookupCustomerId(value as number)}
                  optionList={(customersQuery.data?.items ?? []).map((item) => ({
                    value: item.id,
                    label: withCode(`${item.name}${item.level ? `（${item.level} 级）` : ''}`, item.id),
                  }))}
                  filter={optionMatcher}
                  style={{ width: 280 }}
                />
                <Select
                  placeholder="选择 SKU"
                  value={lookupSkuId}
                  onChange={(value) => setLookupSkuId(value as number)}
                  optionList={skuOptions}
                  filter={optionMatcher}
                  style={{ width: 360 }}
                />
                <Input
                  value={lookupQty}
                  onChange={setLookupQty}
                  style={{ width: 120 }}
                  placeholder="数量"
                />
                {/* 禁用按钮必须能解释自己（主人 2026-10-06：「查价按钮灰着没有任何解释
                    ——你填了数量还以为是自己错」）。两个要点：
                    ① 禁用时按钮旁给一行灰字，说清缺什么；
                    ② 禁用时点一下要**有反应**，而不是石沉大海。
                    难点：这个组件库的禁用按钮用的是原生 `disabled` 属性，浏览器对
                    原生禁用控件**根本不派发点击事件**，所以外面这层 span 收不到。
                    破法是在禁用态下给按钮加 `pointerEvents: 'none'` —— 按钮对点击
                    透明，点击的落点就变成外层 span，span 上的 onClick 才接得住。
                    可用态则不加这个样式，按钮自己处理点击（冒泡上来的那次由
                    `lookupReady` 判断挡掉，不会重复触发）。 */}
                <span
                  style={{ cursor: lookupReady ? undefined : 'not-allowed' }}
                  onClick={() => {
                    if (!lookupReady) {
                      Toast.warning(
                        !lookupCustomerId && !lookupSkuId
                          ? '请先选择客户和产品，再点查价'
                          : !lookupCustomerId
                            ? '请先选择客户，再点查价'
                            : '请先选择产品，再点查价',
                      )
                    }
                  }}
                >
                  <Button
                    theme="solid"
                    loading={!lookupResult && lookupLoading}
                    disabled={!lookupReady}
                    style={lookupReady ? undefined : { pointerEvents: 'none' }}
                    onClick={async () => {
                      setLookupLoading(true)
                      try {
                        const result = await lookupPrice({
                          customer_id: lookupCustomerId!,
                          sku_id: lookupSkuId!,
                          quantity: Number(lookupQty || 1),
                        })
                        setLookupResult(result)
                      } catch (error) {
                        Toast.error(error instanceof Error ? error.message : '查价失败')
                      } finally {
                        setLookupLoading(false)
                      }
                    }}
                  >
                    查价
                  </Button>
                </span>
                {!lookupReady && (
                  <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                    选好客户和产品后才能查价
                  </span>
                )}
              </div>
              {lookupResult?.status === 'ok' && (
                <div className="toolbar">
                  <span style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>选品下单：</span>
                  <Select
                    placeholder="选择要加入的商机（可选）"
                    value={lookupOppId}
                    onChange={(value) => setLookupOppId(value as number)}
                    optionList={(lookupOppQuery.data?.items ?? []).map((item) => ({
                      value: item.id,
                      label: withCode(`${item.title ?? '商机'}（${item.customer_name ?? ''}）`, item.id),
                    }))}
                    filter={optionMatcher}
                    style={{ width: 320 }}
                  />
                  {/* 同一个页面里同款毛病，一起修：禁用按钮外面包一层，
                      禁用态下让按钮对点击透明，点击落到外层 span 上给提示。 */}
                  <span
                    style={{ cursor: lookupOppId ? undefined : 'not-allowed' }}
                    onClick={() => {
                      if (!lookupOppId) {
                        Toast.warning('请先选择一个商机，或点右边的「新建快捷商机」')
                      }
                    }}
                  >
                    <Button
                      disabled={!lookupOppId}
                      loading={addingToOpp}
                      style={lookupOppId ? undefined : { pointerEvents: 'none' }}
                      onClick={async () => {
                        setAddingToOpp(true)
                        try {
                          await createItem(lookupOppId!, {
                            sku_id: lookupResult.sku.id,
                            quantity: Number(lookupQty || 1),
                            target_price: lookupResult.unit_price,
                          })
                          Toast.success(
                            `已把 ${lookupResult.sku.sku_code}（¥${lookupResult.unit_price}）加入商机需求`,
                          )
                        } catch (error) {
                          Toast.error(error instanceof Error ? error.message : '加入失败')
                        } finally {
                          setAddingToOpp(false)
                        }
                      }}
                    >
                      加入商机需求
                    </Button>
                  </span>
                  {!lookupOppId && (
                    <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      选一个已有商机，右边也能一键新建
                    </span>
                  )}
                  <Button
                    loading={creatingQuickOpp}
                    onClick={async () => {
                      // D8：报价必须挂商机。没有现成商机时当场一键生成极简商机
                      //（客户名+日期+询价、首个阶段=初始阶段、SKU/数量写入需求明细），
                      // 让"合规"比"绕开"更省事，而不是让销售回去填一套表
                      const customer = (customersQuery.data?.items ?? []).find(
                        (item) => item.id === lookupCustomerId,
                      )
                      const d = new Date()
                      const dateLabel = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
                      setCreatingQuickOpp(true)
                      try {
                        const opp = await createOpportunity({
                          customer_id: lookupCustomerId,
                          title: `${customer?.name ?? '客户'}-${dateLabel}-询价`,
                        })
                        await createItem(opp.id, {
                          sku_id: lookupResult.sku.id,
                          quantity: Number(lookupQty || 1),
                          target_price: lookupResult.unit_price,
                        })
                        setLookupOppId(opp.id)
                        refreshAll()
                        Toast.success(
                          `快捷商机已创建并加入 ${lookupResult.sku.sku_code}，可直接去报价`,
                        )
                      } catch (error) {
                        Toast.error(error instanceof Error ? error.message : '快捷商机创建失败')
                      } finally {
                        setCreatingQuickOpp(false)
                      }
                    }}
                  >
                    新建快捷商机并加入
                  </Button>
                  <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                    以系统适用价写入需求明细，销售在商机页可再调整目标价
                  </span>
                </div>
              )}
              {lookupResult && (
                <div style={{ display: 'grid', gap: 10, maxWidth: 640 }}>
                  {lookupResult.status === 'ok' ? (
                    <>
                      <div>
                        <span style={{ fontSize: 28, fontWeight: 700 }}>
                          {lookupResult.unit_price}
                        </span>
                        <span style={{ marginLeft: 6, color: 'var(--crm-text-3)' }}>
                          {lookupResult.currency} / 件（数量 {lookupResult.quantity}）
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: 16 }}>
                        <Tag color="blue">{lookupResult.source_label}</Tag>
                        <span style={{ color: 'var(--crm-text-3)' }}>
                          {lookupResult.customer.name}
                          {lookupResult.customer.level ? `（${lookupResult.customer.level} 级）` : ''} ·{' '}
                          {lookupResult.sku.sku_code} · 有效期{' '}
                          {lookupResult.effective_from ?? '即日'} ~ {lookupResult.effective_to ?? '长期'}
                        </span>
                      </div>
                      {lookupResult.fallback_note && (
                        <div
                          style={{
                            background: 'var(--crm-warning-soft, #fff7e6)',
                            padding: '8px 12px',
                            borderRadius: 6,
                          }}
                        >
                          {lookupResult.fallback_note}
                        </div>
                      )}
                      {lookupResult.can_see_cost && (
                        <div style={{ color: 'var(--crm-text-3)' }}>
                          货成本：
                          {lookupResult.cost ?? '—'}
                          {lookupResult.cost_note ? `（${lookupResult.cost_note}）` : ''}
                          {lookupResult.minimum_price != null
                            ? ` · 保护价 ${lookupResult.minimum_price}`
                            : ''}
                        </div>
                      )}
                      <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                        以上为系统适用价；对外拟报价默认带入，销售调整时将按权限检查。
                      </div>
                    </>
                  ) : (
                    <div
                      style={{
                        background: 'var(--crm-warning-soft, #fff7e6)',
                        padding: '12px 16px',
                        borderRadius: 6,
                      }}
                    >
                      待定价：该条件下没有维护有效售价
                      {lookupResult.fallback_note ? `（${lookupResult.fallback_note}）` : ''}
                      ，请联系价格管理员维护；系统不会用成本推算价冒充有效售价。
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          {activeKey === 'costs' && (
            <>
              <div className="toolbar">
                <Select
                  placeholder="选择 SKU 查看成本"
                  value={costSkuId}
                  onChange={(value) => setCostSkuId(value as number)}
                  optionList={skuOptions}
                  filter={optionMatcher}
                  style={{ width: 360 }}
                />
                {canManage && (
                  <>
                    <CsvImportButtons
                      templateUrl="/api/v1/costs/import-template"
                      templateName="成本导入模板.csv"
                      importUrl="/api/v1/costs/import"
                      invalidateQueryKeys={['costs']}
                    />
                    <Button
                      theme="solid"
                      disabled={!costSkuId}
                      onClick={() => setCostVisible(true)}
                    >
                      新增成本
                    </Button>
                  </>
                )}
              </div>
              <Table<CostRecord>
                columns={[
                  { title: '生效日期', dataIndex: 'effective_from', width: 130 },
                  { title: '采购', dataIndex: 'purchase_cost', width: 100, render: money },
                  { title: '生产', dataIndex: 'production_cost', width: 100, render: money },
                  { title: '包装', dataIndex: 'package_cost', width: 100, render: money },
                  { title: '加工', dataIndex: 'processing_cost', width: 100, render: money },
                  {
                    title: '合计成本',
                    dataIndex: 'total_cost',
                    width: 120,
                    render: (value: number) => <span style={{ fontWeight: 600 }}>{money(value)}</span>,
                  },
                  {
                    // 「人工停用」和「自然到期」必须分得开（第十一批 11.5）：
                    // 一条被点过「失效」的成本 `stopped_at` 有值、`effective_to` 仍是空，
                    // 只按日期渲染会显示成「生效中」—— 正好把已经停用的说成还在生效。
                    title: '状态',
                    dataIndex: 'stopped_at',
                    width: 140,
                    render: (stopped: string | null, record: { effective_to?: string | null }) => {
                      if (stopped) return <Tag color="grey">已停用</Tag>
                      if (record.effective_to) return <Tag>到 {record.effective_to} 止</Tag>
                      return <Tag color="green">生效中</Tag>
                    },
                  },
                  { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
                  {
                    // 「失效」这个动作**只有接口、没有入口**了一段时间（第十一批 11.5
                    // 的遗留）：后端能停用，界面上点不到，等于没有。这里补上。
                    //
                    // 只给「生效中」的行显示按钮：已停用的重复点虽然幂等（不改原时刻），
                    // 但摆一个点了没反应的按钮，人会以为没生效、反复点。
                    // 已自然到期（`effective_to` 有值）的行也不给 —— 它已经不参与核价了。
                    title: '操作',
                    width: 96,
                    render: (_: unknown, record: CostRecord) => {
                      const alreadyStopped = Boolean(record.stopped_at)
                      const naturallyExpired = Boolean(record.effective_to)
                      if (alreadyStopped || naturallyExpired) {
                        return <span style={{ color: 'var(--semi-color-text-2)' }}>—</span>
                      }
                      if (!canManage) return <span style={{ color: 'var(--semi-color-text-2)' }}>—</span>
                      return (
                        <Popconfirm
                          title="停用这条成本？"
                          content="停用后当天起不再参与核价，且不能撤销；要改回来请新增一条成本。"
                          onConfirm={() => expireMutation.mutate(record.id)}
                        >
                          <a>停用</a>
                        </Popconfirm>
                      )
                    },
                  },
                ]}
                dataSource={costsQuery.data ?? []}
                loading={costsQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={costSkuId ? '该 SKU 还没有成本记录' : '请先选择 SKU'}
              />
            </>
          )}

          {activeKey === 'rules' && (
            <>
              <div className="toolbar">
                {/* 搜索：编码 / SKU 名称 / 规格 / 产品名 / 产品线 / 品牌都能搜到 */}
                <Input
                  placeholder="搜索 SKU 编码 / 名称 / 规格 / 产品名 / 产品线 / 品牌"
                  value={ruleKeyword}
                  onChange={(v) => {
                    setRuleKeyword(v)
                    setRulePage(1) // 换关键词必须回到第 1 页，否则会停在一个空页上
                  }}
                  style={{ width: 360 }}
                  showClear
                />
                <div style={{ flex: 1 }} />
                {canManage && (
                  <>
                    <CsvImportButtons
                      templateUrl="/api/v1/price-rules/import-template"
                      templateName="价格规则导入模板.csv"
                      importUrl="/api/v1/price-rules/import"
                      invalidateQueryKeys={['price-rules']}
                    />
                    <Button theme="solid" onClick={() => openRuleModal()}>
                      新增价格规则
                    </Button>
                  </>
                )}
              </div>
              <Table<PriceRuleRow>
                columns={[
                  {
                    title: 'SKU',
                    dataIndex: 'sku_code',
                    width: 200,
                    // 编码在上、名称在下（与报价/订单明细同一口径）。
                    // 后端 `serialize_price_rule` 现在会带 `sku_name` 回来。
                    render: (v: string | null, record: PriceRuleRow) => (
                      <div>
                        <div>{v ?? '-'}</div>
                        {record.sku_name && (
                          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                            {record.sku_name}
                          </div>
                        )}
                      </div>
                    ),
                  },
                  {
                    title: '客户等级',
                    dataIndex: 'customer_level',
                    width: 100,
                    render: (v: string | null) => v ?? '全部',
                  },
                  {
                    title: '数量区间',
                    width: 160,
                    render: (_: unknown, record: PriceRuleRow) =>
                      `${record.min_qty} ~ ${record.max_qty ?? '不限'}`,
                  },
                  { title: '标准价', dataIndex: 'standard_price', width: 100, render: money },
                  { title: '指导价', dataIndex: 'guide_price', width: 100, render: money },
                  {
                    title: '最低保护价',
                    dataIndex: 'minimum_price',
                    width: 120,
                    render: (value: number | null) => (
                      <span style={{ fontWeight: 600, color: 'var(--crm-error)' }}>{money(value)}</span>
                    ),
                  },
                  {
                    title: '目标利润率',
                    dataIndex: 'target_margin',
                    width: 110,
                    render: (v: number | null) => (v === null ? '-' : `${(v * 100).toFixed(0)}%`),
                  },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    width: 90,
                    render: (v: string) =>
                      v === 'active' ? (
                        <Tag color="green">生效</Tag>
                      ) : v === 'historical' ? (
                        <Tag color="grey">历史</Tag>
                      ) : (
                        <Tag>停用</Tag>
                      ),
                  },
                  {
                    title: '操作',
                    width: 120,
                    render: (_: unknown, record: PriceRuleRow) =>
                      canManage ? (
                        <span style={{ display: 'inline-flex', gap: 10 }}>
                          {/* 「编辑」只改"这条规则在什么条件下适用"（数量区间/有效期/
                              客户等级/备注）；**价钱不在其中** —— 改价钱走
                              「停用 + 新增」，这样生效时序在单据上看得见。
                              后端也会拒绝改价并点名字段，不是只靠界面挡。 */}
                          <a onClick={() => openRuleModal(record)}>编辑</a>
                          {record.status === 'active' ? (
                            <Popconfirm
                              title="停用这条价格规则？"
                              onConfirm={() => disablePriceRule(record.id).then(refreshAll)}
                            >
                              <a style={{ color: 'var(--crm-error)' }}>停用</a>
                            </Popconfirm>
                          ) : (
                            // 恢复启用（审查建议）：停用是软操作（只把 status 改成
                            // disabled，行还在），所以可以恢复。带二次确认 ——
                            // 恢复会让这条规则**重新参与取价**，且若与现有启用规则
                            // 撞区间会被后端拒绝（40901），提示里说清这一点。
                            <Popconfirm
                              title="恢复启用这条价格规则？"
                              content="恢复后它会重新参与取价；若与现有启用规则的数量区间重叠，后端会拒绝并保持停用。"
                              onConfirm={() =>
                                restorePriceRule(record.id).then(refreshAll)
                              }
                            >
                              <a>恢复启用</a>
                            </Popconfirm>
                          )}
                        </span>
                      ) : (
                        '-'
                      ),
                  },
                ]}
                dataSource={rulesQuery.data?.items ?? []}
                loading={rulesQuery.isLoading}
                rowKey="id"
                // 服务端分页：从前 `pagination={false}` + 写死 100 条，
                // 规则超过 100 条就再也翻不到（不是"看着乱"而已，是看不到）
                pagination={{
                  currentPage: rulePage,
                  pageSize: rulePageSize,
                  total: rulesQuery.data?.total ?? 0,
                  showSizeChanger: true,
                  pageSizeOpts: [20, 50, 100, 200],
                  onPageChange: setRulePage,
                  onPageSizeChange: (size: number) => {
                    setRulePageSize(size)
                    setRulePage(1)
                  },
                }}
                empty={
                  ruleKeyword.trim()
                    ? `没有匹配「${ruleKeyword.trim()}」的价格规则`
                    : '还没有价格规则'
                }
                scroll={{ x: 1100 }}
              />
            </>
          )}

          {activeKey === 'customer-prices' && (
            <>
              <div className="toolbar">
                {/* 搜索：编码 / SKU 名称 / 规格 / 产品名 / 产品线 / 品牌 / 客户名 都能搜到 */}
                <Input
                  placeholder="搜索 SKU 编码 / 名称 / 规格 / 产品名 / 客户名"
                  value={cpKeyword}
                  onChange={(v) => {
                    setCpKeyword(v)
                    setCpPage(1)
                  }}
                  style={{ width: 360 }}
                  showClear
                />
                <div style={{ flex: 1 }} />
                {canManage && (
                  <>
                    <CsvImportButtons
                      templateUrl="/api/v1/customer-price-rules/import-template"
                      templateName="客户特殊价导入模板.csv"
                      importUrl="/api/v1/customer-price-rules/import"
                      invalidateQueryKeys={['customer-price-rules']}
                    />
                    <Button theme="solid" onClick={() => openCustomerPriceModal()}>
                      新增客户特殊价
                    </Button>
                  </>
                )}
              </div>
              <Table<CustomerPriceRow>
                columns={[
                  { title: '客户', dataIndex: 'customer_name', width: 240, render: (v: string | null) => v ?? '-' },
                  {
                    title: 'SKU',
                    dataIndex: 'sku_code',
                    width: 200,
                    render: (v: string | null, record: CustomerPriceRow) => (
                      <div>
                        <div>{v ?? '-'}</div>
                        {record.sku_name && (
                          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                            {record.sku_name}
                          </div>
                        )}
                      </div>
                    ),
                  },
                  {
                    title: '起订量',
                    dataIndex: 'min_qty',
                    width: 110,
                  },
                  {
                    title: '约定价',
                    dataIndex: 'agreed_price',
                    width: 110,
                    render: (value: number) => <span style={{ fontWeight: 600 }}>{money(value)}</span>,
                  },
                  { title: '最低价', dataIndex: 'minimum_price', width: 110, render: money },
                  { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
                  {
                    title: '操作',
                    width: 120,
                    render: (_: unknown, record: CustomerPriceRow) =>
                      canManage ? (
                        <span style={{ display: 'inline-flex', gap: 10 }}>
                          {/* 客户特殊价**允许原地改约定价** —— 这正是它被设计出来的
                              场景（客户谈定的价涨了两块）。后端 PATCH 一直就有，
                              只是此前没有任何页面调过它，想改只能删了重录。
                              归属（客户/SKU）不给改：那等于换一条规则。 */}
                          <a onClick={() => openCustomerPriceModal(record)}>编辑</a>
                          <Popconfirm
                            title="删除这条客户特殊价？"
                            onConfirm={() => deleteCustomerPriceRule(record.id).then(refreshAll)}
                          >
                            <a style={{ color: 'var(--crm-error)' }}>删除</a>
                          </Popconfirm>
                        </span>
                      ) : (
                        '-'
                      ),
                  },
                ]}
                dataSource={customerPricesQuery.data?.items ?? []}
                loading={customerPricesQuery.isLoading}
                rowKey="id"
                pagination={{
                  currentPage: cpPage,
                  pageSize: cpPageSize,
                  total: customerPricesQuery.data?.total ?? 0,
                  showSizeChanger: true,
                  pageSizeOpts: [20, 50, 100, 200],
                  onPageChange: setCpPage,
                  onPageSizeChange: (size: number) => {
                    setCpPageSize(size)
                    setCpPage(1)
                  },
                }}
                empty={
                  cpKeyword.trim()
                    ? `没有匹配「${cpKeyword.trim()}」的客户特殊价`
                    : '还没有客户特殊价'
                }
              />
            </>
          )}

          {activeKey === 'permissions' && (
            <Table<PricePermissionRow>
              columns={[
                { title: '角色', dataIndex: 'role_name', width: 160 },
                { title: '角色码', dataIndex: 'role_code', width: 160, render: (v: string | null) => v ?? '-' },
                {
                  title: '最低利润率',
                  dataIndex: 'minimum_margin',
                  width: 140,
                  render: (value: number) => `${(value * 100).toFixed(0)}%`,
                },
                {
                  title: '最大折扣',
                  dataIndex: 'discount_limit',
                  width: 120,
                  render: (v: number | null) => (v === null ? '-' : `${(v * 100).toFixed(0)}%`),
                },
                {
                  title: '可审批报价',
                  dataIndex: 'can_approve',
                  width: 120,
                  render: (v: boolean) => (v ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
                },
                { title: '说明', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
                {
                  title: '操作',
                  width: 80,
                  render: (_: unknown, record: PricePermissionRow) =>
                    canManage ? (
                      <a
                        style={{ color: 'var(--crm-primary)' }}
                        onClick={() => {
                          setPermissionTarget(record)
                          setPermissionForm({
                            minimum_margin: String(record.minimum_margin),
                            can_approve: record.can_approve,
                          })
                        }}
                      >
                        调整
                      </a>
                    ) : (
                      '-'
                    ),
                },
              ]}
              dataSource={permissionsQuery.data ?? []}
              loading={permissionsQuery.isLoading}
              rowKey="id"
              pagination={false}
            />
          )}

          {activeKey === 'logistics' && (
            <>
              <div className="toolbar">
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
                  运费用于核价里的单件运费估算，也可以核价时手工覆盖
                </div>
                <div style={{ flex: 1 }} />
                {canManage && (
                  <Button theme="solid" onClick={() => openRateModal()}>
                    新增运费费率
                  </Button>
                )}
              </div>
              <Table<LogisticsRateRow>
                columns={[
                  { title: '承运方式', dataIndex: 'provider' },
                  // 两个地区字段：**空 = 不限**。这里把"不限"明确写出来，
                  // 免得与"填了个具体地名"混为一谈（两者在核价匹配里行为不同）。
                  {
                    title: '起运地',
                    dataIndex: 'origin_region',
                    width: 100,
                    render: (v: string | null) => (v ? v : <span style={HINT}>不限</span>),
                  },
                  {
                    title: '目的地',
                    dataIndex: 'destination_region',
                    width: 100,
                    render: (v: string | null) => (v ? v : <span style={HINT}>不限</span>),
                  },
                  { title: '运输方式', dataIndex: 'shipping_method', width: 100 },
                  {
                    title: '公斤单价',
                    dataIndex: 'unit_price_per_kg',
                    width: 100,
                    render: (value: number) => `¥${value}`,
                  },
                  {
                    title: '体积单价',
                    dataIndex: 'unit_price_per_volume',
                    width: 100,
                    render: (value: number | null) =>
                      value == null ? <span style={HINT}>—</span> : `¥${value}/m³`,
                  },
                  {
                    title: '最低收费',
                    dataIndex: 'min_charge',
                    width: 100,
                    render: (value: number) => `¥${value}`,
                  },
                  {
                    title: '时效（天）',
                    dataIndex: 'eta_days',
                    width: 100,
                    render: (v: number | null, record: LogisticsRateRow) =>
                      v == null
                        ? '-'
                        : record.eta_days_max != null && record.eta_days_max !== v
                          ? `${v}~${record.eta_days_max}`
                          : String(v),
                  },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    width: 90,
                    render: (v: string) =>
                      v === 'inactive' ? (
                        <Tag color="grey">已停用</Tag>
                      ) : (
                        <Tag color="green">启用中</Tag>
                      ),
                  },
                  ...(canManage
                    ? [
                        {
                          title: '操作',
                          width: 130,
                          render: (_: unknown, record: LogisticsRateRow) => (
                            <div style={{ display: 'flex', gap: 12 }}>
                              <a onClick={() => openRateModal(record)}>修改</a>
                              <Popconfirm
                                title={`删除费率「${record.provider}」？`}
                                content="删掉之后核价不再用它估算运费。已发出的报价不受影响：运费当时已作为快照存进报价明细。"
                                onConfirm={() => rateDeleteMutation.mutate(record.id)}
                              >
                                <a style={{ color: 'var(--crm-error)' }}>删除</a>
                              </Popconfirm>
                            </div>
                          ),
                        },
                      ]
                    : []),
                ]}
                dataSource={ratesQuery.data ?? []}
                loading={ratesQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty="还没有运费费率"
              />
            </>
          )}

          {activeKey === 'history' && (
            <>
              <div className="toolbar">
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
                  报价、成本与价格规则的全部变更（取自审计日志），按 SKU 过滤可追溯一价一改
                </div>
                <div style={{ flex: 1 }} />
                <Select
                  placeholder="按 SKU 过滤（可空）"
                  value={historySkuId}
                  onChange={(value) => {
                    setHistorySkuId(value as number | undefined)
                    setHistoryPage(1)
                  }}
                  optionList={skuOptions}
                  filter={optionMatcher}
                  showClear
                  style={{ width: 320 }}
                />
              </div>
              <Table<PricingHistoryRow>
                columns={[
                  {
                    title: '时间',
                    dataIndex: 'created_at',
                    width: 170,
                    render: (v: string) => new Date(v).toLocaleString('zh-CN'),
                  },
                  {
                    title: '类型',
                    dataIndex: 'business_type',
                    width: 110,
                    render: (v: string | null) => HISTORY_TYPE_LABEL[v ?? ''] ?? v ?? '-',
                  },
                  {
                    // 「对象」而不是内部编号（2026-10-06 主人反馈）：
                    // 原先这一列显示 295、4320 这种内部 id，业务上根本看不出是哪张单。
                    // 后端已把 business_id 反查成人话（报价单号 / 产品编码 / 客户名）随行下发。
                    title: '对象',
                    dataIndex: 'target_label',
                    width: 230,
                    ellipsis: true,
                    render: (label: string | null, record: PricingHistoryRow) =>
                      label ? (
                        record.target_link ? (
                          <Link to={record.target_link} style={{ color: 'var(--crm-primary)' }}>
                            {label}
                          </Link>
                        ) : (
                          <span title={label}>{label}</span>
                        )
                      ) : (
                        // 反查不到就退回显示"类型 #编号"，别让这一格变成空白
                        //（历史记录里可能有对象已被删除、或类型不在反查范围内的）
                        <span style={{ color: 'var(--crm-text-3)' }}>
                          {(HISTORY_TYPE_LABEL[record.business_type ?? ''] ??
                            record.business_type) ?? '-'}
                          {record.business_id != null ? ` #${record.business_id}` : ''}
                        </span>
                      ),
                  },
                  {
                    title: '动作',
                    dataIndex: 'action',
                    width: 90,
                    render: (v: string) =>
                      ({ create: '新增', update: '修改', delete: '删除' })[v] ?? v,
                  },
                  {
                    title: '变更内容',
                    dataIndex: 'after',
                    render: (_: unknown, record: PricingHistoryRow) => historySummary(record),
                  },
                  {
                    title: '操作人',
                    dataIndex: 'operator_name',
                    width: 110,
                    render: (v: string | null) => v ?? '-',
                  },
                ]}
                dataSource={historyQuery.data?.items ?? []}
                loading={historyQuery.isLoading}
                rowKey="id"
                pagination={{
                  currentPage: historyPage,
                  pageSize: 20,
                  total: historyQuery.data?.total ?? 0,
                  onPageChange: setHistoryPage,
                }}
                empty="还没有变更记录"
              />
            </>
          )}
        </div>
      </SectionCard>

      <Modal
        title="新增成本"
        visible={costVisible}
        onCancel={() => setCostVisible(false)}
        onOk={() => costMutation.mutate()}
        confirmLoading={costMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          {(
            [
              ['purchase_cost', '采购成本'],
              ['production_cost', '生产成本'],
              ['package_cost', '包装成本'],
              ['processing_cost', '加工成本'],
            ] as const
          ).map(([field, label]) => (
            <div key={field}>
              <div style={{ marginBottom: 4 }}>{label}（元）</div>
              <Input
                value={costForm[field]}
                onChange={(value) => setCostForm({ ...costForm, [field]: value })}
                placeholder="留空按 0 处理"
              />
            </div>
          ))}
          <div>
            <div style={{ marginBottom: 4 }}>生效日期</div>
            <DatePicker
              type="date"
              format="yyyy-MM-dd"
              showClear
              style={{ width: '100%' }}
              placeholder="选择生效日期"
              value={costForm.effective_from ? new Date(costForm.effective_from) : undefined}
              onChange={(_, dateStr) =>
                setCostForm({ ...costForm, effective_from: (dateStr as string) || '' })
              }
            />
          </div>
        </div>
      </Modal>

      <Modal
        title={ruleEditing ? `编辑价格规则 #${ruleEditing.id}` : '新增价格规则'}
        visible={ruleVisible}
        width={620}
        onCancel={closeRuleModal}
        onOk={() => {
          if (!ruleEditing && !ruleForm.sku_id) {
            Toast.warning('请选择 SKU')
            return
          }
          ruleMutation.mutate()
        }}
        confirmLoading={ruleMutation.isPending}
        okText={ruleEditing ? '保存' : '创建'}
      >
        {ruleEditing && (
          <div
            style={{
              marginBottom: 12,
              padding: '8px 10px',
              fontSize: 12,
              lineHeight: 1.7,
              color: 'var(--crm-text-3)',
              background: 'var(--crm-surface-high, #f6f7f9)',
              borderRadius: 6,
            }}
          >
            这里只改<strong>「这条规则在什么条件下适用」</strong>：数量区间、有效期、客户等级、备注。
            <br />
            <strong>价钱不能原地改</strong>（标准价 / 指导价 / 最低保护价 / 目标利润率）——
            改价钱请「停用这条规则 + 新增一条」，这样"哪条从哪天起生效"在单据上看得见。
            {ruleEditing.status === 'active' && '本条正在生效，保存后立即影响后续取价（草稿报价会提示价格已更新）。'}
          </div>
        )}
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>SKU</FormLabel>
            <Select
              value={ruleForm.sku_id ?? undefined}
              onChange={(value) => setRuleForm({ ...ruleForm, sku_id: value as number })}
              optionList={skuOptions}
              filter={optionMatcher}
              style={{ width: '100%' }}
              disabled={Boolean(ruleEditing)}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户等级（留空 = 全部等级）</div>
              <Select
                value={ruleForm.customer_level || undefined}
                onChange={(value) => setRuleForm({ ...ruleForm, customer_level: (value as string) ?? '' })}
                optionList={['A', 'B', 'C', 'D'].map((value) => ({ value, label: `${value} 级` }))}
                showClear
                style={{ width: '100%' }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>起订数量</div>
              <Input
                value={ruleForm.min_qty}
                onChange={(value) => setRuleForm({ ...ruleForm, min_qty: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>数量上限（留空 = 不限）</div>
              <Input
                value={ruleForm.max_qty}
                onChange={(value) => setRuleForm({ ...ruleForm, max_qty: value })}
                placeholder="例如 999"
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>标准价</div>
              <Input
                value={ruleForm.standard_price}
                onChange={(value) => setRuleForm({ ...ruleForm, standard_price: value })}
                disabled={Boolean(ruleEditing)}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>指导价</div>
              <Input
                value={ruleForm.guide_price}
                onChange={(value) => setRuleForm({ ...ruleForm, guide_price: value })}
                disabled={Boolean(ruleEditing)}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>最低保护价</div>
              <Input
                value={ruleForm.minimum_price}
                onChange={(value) => setRuleForm({ ...ruleForm, minimum_price: value })}
                disabled={Boolean(ruleEditing)}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>目标利润率（如 0.30）</div>
              <Input
                value={ruleForm.target_margin}
                onChange={(value) => setRuleForm({ ...ruleForm, target_margin: value })}
                disabled={Boolean(ruleEditing)}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>生效起始日（留空 = 立即生效）</div>
              <DatePicker
                type="date"
                format="yyyy-MM-dd"
                showClear
                style={{ width: '100%' }}
                placeholder="选择起始日"
                value={ruleForm.effective_from ? new Date(ruleForm.effective_from) : undefined}
                onChange={(_, dateStr) =>
                  setRuleForm({ ...ruleForm, effective_from: (dateStr as string) || '' })
                }
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>生效截止日（留空 = 长期有效）</div>
              <DatePicker
                type="date"
                format="yyyy-MM-dd"
                showClear
                style={{ width: '100%' }}
                placeholder="选择截止日"
                value={ruleForm.effective_to ? new Date(ruleForm.effective_to) : undefined}
                onChange={(_, dateStr) =>
                  setRuleForm({ ...ruleForm, effective_to: (dateStr as string) || '' })
                }
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注</div>
            <Input
              value={ruleForm.remark}
              onChange={(value) => setRuleForm({ ...ruleForm, remark: value })}
              placeholder="例如：2026 年度协议价"
            />
          </div>
        </div>
      </Modal>

      <Modal
        title={customerPriceEditing ? '编辑客户特殊价' : '新增客户特殊价'}
        visible={customerPriceVisible}
        onCancel={closeCustomerPriceModal}
        onOk={() => {
          // `!value` 挡不住**纯空白**：`" "` 在 JS 里是 truthy，会一路走到提交，
          // 被 `parseOptionalNumber` 转成 `null`，于是后端把"显式传 null"当成
          // "这个字段不改"、返回 200「已保存」，而价格一个字没动 ——
          // 用户看到的是"保存成功但价格没变"（审查 2026-10-09 实测）。
          // 所以这里先 trim 再判空，并且要求**正数**（约定价是"这个客户按多少钱买"，
          // 0 元不成立，后端也已按 gt=0 收紧）。
          const agreedRaw = String(customerPriceForm.agreed_price ?? '').trim()
          const agreedNum = Number(agreedRaw)
          const agreedOk = agreedRaw !== '' && Number.isFinite(agreedNum) && agreedNum > 0
          if (customerPriceEditing) {
            // 改的时候只要求约定价还在（客户/SKU 不给改，不必再校验）
            if (!agreedOk) {
              Toast.warning('约定价必填，且必须是大于 0 的正数（最多 4 位小数）')
              return
            }
          } else if (!customerPriceForm.customer_id || !customerPriceForm.sku_id || !agreedOk) {
            Toast.warning('客户、SKU、约定价都要填（约定价必须是正数）')
            return
          }
          customerPriceMutation.mutate()
        }}
        confirmLoading={customerPriceMutation.isPending}
        okText={customerPriceEditing ? '保存' : '创建'}
      >
        {customerPriceEditing && (
          <div
            style={{
              marginBottom: 12,
              padding: '8px 10px',
              fontSize: 12,
              lineHeight: 1.7,
              color: 'var(--crm-text-3)',
              background: 'var(--crm-surface-high, #f6f7f9)',
              borderRadius: 6,
            }}
          >
            就地修改这条专属价（约定价、最低价、起订量、有效期、备注）。
            <br />
            <strong>客户与 SKU 不给改</strong>：改归属等于换一条规则，请删掉重录。
          </div>
        )}
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>客户</FormLabel>
            <Select
              value={customerPriceForm.customer_id ?? undefined}
              onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, customer_id: value as number })}
              optionList={(customersQuery.data?.items ?? []).map((item) => ({ value: item.id, label: withCode(item.name, item.id) }))}
              filter={optionMatcher}
              style={{ width: '100%' }}
              disabled={Boolean(customerPriceEditing)}
            />
          </div>
          <div>
            <FormLabel required>SKU</FormLabel>
            <Select
              value={customerPriceForm.sku_id ?? undefined}
              onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, sku_id: value as number })}
              optionList={skuOptions}
              filter={optionMatcher}
              style={{ width: '100%' }}
              disabled={Boolean(customerPriceEditing)}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <FormLabel>起订量</FormLabel>
              <Input
                value={customerPriceForm.min_qty}
                onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, min_qty: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              {/* 四个格子并排、每个只有一百来像素宽：「留空 = 不限」这类说明一旦写进
                  标签就会折行，折行后这一格的输入框被顶下去，整行参差不齐。
                  所以标签只留字段名，说明挪进占位提示（placeholder）。 */}
              <FormLabel>数量上限</FormLabel>
              <Input
                value={customerPriceForm.max_qty}
                onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, max_qty: value })}
                placeholder="留空 = 不限（如 999）"
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel required>约定价</FormLabel>
              <Input
                value={customerPriceForm.agreed_price}
                onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, agreed_price: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel>最低价</FormLabel>
              <Input
                value={customerPriceForm.minimum_price}
                onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, minimum_price: value })}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <FormLabel>生效起始日</FormLabel>
              <DatePicker
                type="date"
                format="yyyy-MM-dd"
                showClear
                style={{ width: '100%' }}
                placeholder="留空 = 立即生效"
                value={
                  customerPriceForm.effective_from ? new Date(customerPriceForm.effective_from) : undefined
                }
                onChange={(_, dateStr) =>
                  setCustomerPriceForm({
                    ...customerPriceForm,
                    effective_from: (dateStr as string) || '',
                  })
                }
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel>生效截止日</FormLabel>
              <DatePicker
                type="date"
                format="yyyy-MM-dd"
                showClear
                style={{ width: '100%' }}
                placeholder="留空 = 长期有效"
                value={
                  customerPriceForm.effective_to ? new Date(customerPriceForm.effective_to) : undefined
                }
                onChange={(_, dateStr) =>
                  setCustomerPriceForm({
                    ...customerPriceForm,
                    effective_to: (dateStr as string) || '',
                  })
                }
              />
            </div>
          </div>
          <div>
            <FormLabel>备注</FormLabel>
            <Input
              value={customerPriceForm.remark}
              onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, remark: value })}
              placeholder="例如：2026 年度协议价"
            />
          </div>
        </div>
      </Modal>

      <Modal
        title={`调整价格权限：${permissionTarget?.role_name ?? ''}`}
        visible={Boolean(permissionTarget)}
        onCancel={() => setPermissionTarget(null)}
        onOk={() => permissionMutation.mutate()}
        confirmLoading={permissionMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>最低利润率（0.15 = 15%）</div>
            <Input
              value={permissionForm.minimum_margin}
              onChange={(value) => setPermissionForm({ ...permissionForm, minimum_margin: value })}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Switch
              checked={permissionForm.can_approve}
              onChange={(value) => setPermissionForm({ ...permissionForm, can_approve: value })}
            />
            <span>该角色可以审批低价报价</span>
          </div>
        </div>
      </Modal>

      <Modal
        title={rateEditing ? `修改运费费率：${rateEditing.provider}` : '新增运费费率'}
        visible={rateVisible}
        onCancel={closeRateModal}
        onOk={() => {
          if (!rateForm.provider.trim()) {
            Toast.warning('请填写承运方式')
            return
          }
          rateMutation.mutate()
        }}
        confirmLoading={rateMutation.isPending}
        okText={rateEditing ? '保存' : '创建'}
        cancelText="取消"
        style={{ maxWidth: 'calc(100vw - 48px)' }}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel
              required
              hint="就是费率行里一个名字，试算页的承运商下拉由这张表去重得出"
            >
              承运方式
            </FormLabel>
            <Input
              value={rateForm.provider}
              onChange={(value) => setRateForm({ ...rateForm, provider: value })}
              placeholder="例如：德邦零担"
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <FormLabel hint="留空 = 不限">起运地</FormLabel>
              <AutoComplete
                value={rateForm.origin_region}
                onChange={(value) =>
                  setRateForm({ ...rateForm, origin_region: String(value ?? '') })
                }
                data={rateOptions('origin_region')}
                placeholder="留空 = 不限，例如：华东"
                style={{ width: '100%' }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel hint="留空 = 不限；核价按完全相同的写法匹配">目的地</FormLabel>
              <AutoComplete
                value={rateForm.destination_region}
                onChange={(value) =>
                  setRateForm({ ...rateForm, destination_region: String(value ?? '') })
                }
                data={rateOptions('destination_region')}
                placeholder="留空 = 不限，例如：华东"
                style={{ width: '100%' }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel hint="试算时按这个筛">运输方式</FormLabel>
              <AutoComplete
                value={rateForm.shipping_method}
                onChange={(value) =>
                  setRateForm({ ...rateForm, shipping_method: String(value ?? '') })
                }
                data={rateOptions('shipping_method')}
                placeholder="例如：陆运 / 快递 / 专线"
                style={{ width: '100%' }}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <FormLabel required hint="按重量计费">公斤单价（元/kg）</FormLabel>
              <Input
                value={rateForm.unit_price_per_kg}
                onChange={(value) => setRateForm({ ...rateForm, unit_price_per_kg: value })}
                placeholder="例如：0.9"
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel hint="留空 = 这家不按体积计费">体积单价（元/m³）</FormLabel>
              <Input
                value={rateForm.unit_price_per_volume}
                onChange={(value) =>
                  setRateForm({ ...rateForm, unit_price_per_volume: value })
                }
                placeholder="抛货要填，留空 = 不计体积"
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel hint="算出来低于它就按它收">最低收费（元）</FormLabel>
              <Input
                value={rateForm.min_charge}
                onChange={(value) => setRateForm({ ...rateForm, min_charge: value })}
                placeholder="例如：50"
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <FormLabel>时效（起，天）</FormLabel>
              <Input
                value={rateForm.eta_days}
                onChange={(value) => setRateForm({ ...rateForm, eta_days: value })}
                placeholder="例如：4"
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel hint="只填一个数就留空这里">时效（止，天）</FormLabel>
              <Input
                value={rateForm.eta_days_max}
                onChange={(value) => setRateForm({ ...rateForm, eta_days_max: value })}
                placeholder="例如：6"
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel hint="停用后核价不再用它，数据留着">状态</FormLabel>
              <Select
                value={rateForm.status}
                onChange={(value) =>
                  setRateForm({ ...rateForm, status: value === 'inactive' ? 'inactive' : 'active' })
                }
                style={{ width: '100%' }}
                optionList={[
                  { label: '启用中', value: 'active' },
                  { label: '已停用', value: 'inactive' },
                ]}
              />
            </div>
          </div>
          <div>
            <FormLabel hint="给自己看的备注，不参与计算">备注</FormLabel>
            <Input
              value={rateForm.remark}
              onChange={(value) => setRateForm({ ...rateForm, remark: value })}
              placeholder="例如：只走江浙沪，月结"
            />
          </div>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)', lineHeight: 1.7 }}>
            核价估算会在「匹配到的」费率里取最便宜的一条。起运地与目的地留空表示不限；
            填了就得和试算时选的写法完全一致，否则会退化成「列出全部费率」，运费可能算少。
          </div>
        </div>
      </Modal>
    </div>
  )
}
