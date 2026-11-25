import { useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Input, Modal, Select, Table, Toast } from '@douyinfe/semi-ui'

import { parseOptionalDecimal, parseOptionalNumber } from '../../shared/number-input'
import { formatBeijingDate } from '../../shared/beijing-time'
import { formatMoney, formatWholeCny, toTwoDecimals } from '../../shared/components/money'
import PageHeader from '../../shared/components/PageHeader'
import KpiStrip, { type KpiItem } from '../../shared/components/KpiStrip'
import { ApiError } from '../../shared/api/client'
import HelpTip from '../../shared/components/HelpTip'
import EChart from '../../shared/components/charts/EChart'
import DonutLegend from '../../shared/components/charts/DonutLegend'
import {
  asMoney,
  asPercent,
  asPercentValue,
  comboBarLineOption,
  compactMoney,
  donutChartOption,
  lineChartOption,
  plainNumber,
  rankBarChartOption,
  type ValueFormat,
} from '../../shared/components/charts/options'
import {
  freezeSalesActuals,
  getCustomerStats,
  getDeliveryStats,
  getLeadStats,
  getLossStats,
  getOpportunityStats,
  getPaymentStats,
  getPricingStats,
  getProductStats,
  getQuoteStats,
  getReceivableStats,
  getSalesTargetBases,
  getSalesTargetDrilldown,
  getSalesUserStats,
  listSalesTargets,
  refreezeSalesActuals,
  upsertSalesTarget,
  type NameValue,
  type StageConversionRow,
  type DeliveryOwnerRow,
  type DeliveryRiskOrder,
  type DeliveryTrendRow,
  type ProductStat,
  type SalesTargetRow,
  type SalesUserStat,
} from '../../shared/api/analytics'
import { listDepartments, listUsers } from '../../shared/api/system'
import { listOpportunities } from '../../shared/api/opportunity'
import { listOrders } from '../../shared/api/order'
import { usePermissions } from '../../shared/hooks/permissions'
import { optionMatcher } from '../../shared/components/optionMatch'

/** 下钻明细里 record_type 的中文名：直接亮 shipment_batch 这种内部标识没人看得懂。 */
const DRILLDOWN_TYPE_LABEL: Record<string, string> = {
  order: '订单',
  shipment_batch: '发货批次',
  payment: '回款',
  customer: '客户',
}

/**
 * 一列一排的格子：同一行**等高**（grid 默认就是拉伸的），所以用几列由内容定，
 * 窄屏自动掉成一列。整页的排版都靠它，别各写各的 grid。
 */
function Row({ columns = 4, children }: { columns?: 1 | 2 | 3 | 4; children: ReactNode }) {
  // 列数由 CSS 类按断点控制（见 index.css 的 .row-*）：用 auto-fit 会在某个宽度
  // 把最后一张卡挤到下一行、自成一行，那一张就不跟别人等高了。
  return <div className={`row-${columns}`}>{children}</div>
}

interface AnalyticsRead {
  isError: boolean
  isPending: boolean
  isFetching: boolean
  error: unknown
  refetch: () => Promise<unknown>
}

function readIssue(query?: AnalyticsRead): ReactNode {
  if (query?.isError) {
    if (query.error instanceof ApiError && query.error.status === 403) {
      return <span role="status">没有权限查看这项数据</span>
    }
    return <span role="status">数据加载失败。 <Button size="small" theme="borderless"
      loading={query.isFetching} onClick={() => void query.refetch()}>重试</Button></span>
  }
  return query?.isPending ? <span role="status">正在读取数据…</span> : null
}

function AnalyticsKpis({ items, ...props }: Omit<Parameters<typeof KpiStrip>[0], 'items'> & {
  items: (KpiItem & { query: AnalyticsRead })[]
}) {
  return <KpiStrip {...props} items={items.map(({ query, ...item }) => {
    const issue = readIssue(query)
    return issue ? { ...item, value: '—', hint: issue } : item
  })} />
}

function ReportSection({ title, note, children, extra, id, query }: {
  title: string
  note?: string
  children: ReactNode
  extra?: ReactNode
  id?: string
  query?: AnalyticsRead
}) {
  return (
    <section className="analytics-section" id={id}>
      <div className="analytics-section-heading">
        <div>
          <h3>{title}</h3>
          {note && <p className="an-card-note">{note}</p>}
        </div>
        {extra}
      </div>
      {readIssue(query) ?? children}
    </section>
  )
}

/** 数量 + 单位：`12 家`。图和提示里都用它，免得一处写"家"一处写"个"。 */
const asCount = (unit: string) => (value: number) =>
  `${Math.round(value).toLocaleString('zh-CN')} ${unit}`

/** 环形图：图 + 右侧图例（名字 / 数量 / 占比）。 */
function Donut({
  data,
  format,
  centerLabel,
  centerValueFormat,
}: {
  data: NameValue[]
  format: ValueFormat
  centerLabel: string
  centerValueFormat?: ValueFormat
}) {
  if (!data.length) return <NoData />
  const total = data.reduce((sum, item) => sum + item.value, 0)
  return (
    /* 环图与图例左右并排：这个区改成 2 列后卡宽 570，图例有 400 可用，
       名字 / 数量 / 占比三样都放得下，不需要再堆成上下两块（那会把卡撑高）。 */
    <div style={{ display: 'flex', alignItems: 'center', gap: 18, minWidth: 0 }}>
      <div style={{ width: 150, flex: 'none' }}>
        <EChart
          height={150}
          ariaLabel={`占比环形图，共 ${format(total)}`}
          option={donutChartOption({
            data,
            format,
            // 中间那两行**一个给数、一个给名**：`4 家` 配 `家客户`。
            // 两个都带上单位会出现「4 家 / 家」这种重复。
            centerLabel,
            centerValue: (centerValueFormat ?? plainNumber)(total),
          })}
        />
      </div>
      <DonutLegend data={data} format={format} />
    </div>
  )
}

/**
 * 环图中心用的金额：万为单位**保留一位小数**（¥29.7万）。
 * 轴上那版 compactMoney 到十万就抹掉小数变成「¥30万」，凑整后跟右侧图例
 * 的 ¥296,830 差出一截，看着像两个数（2026-11-20 实拍）。
 */
const centerMoney: ValueFormat = (value) =>
  Math.abs(value) >= 10_000 ? `¥${(value / 10_000).toFixed(1)}万` : asMoney(value)

function StatusCounts({ title, note, data, unit, query }: {
  title: string
  note?: string
  data: NameValue[]
  unit: string
  query?: AnalyticsRead
}) {
  return (
    <section className="analytics-status-group" aria-label={title}>
      <div>
        <h3>{title}</h3>
        {note && <p>{note}</p>}
      </div>
      {readIssue(query) ?? (data.length ? (
        <dl>
          {data.map(({ name, value }) => (
            <div key={name} className={value === 0 ? 'is-empty' : undefined}>
              <dt>{name}</dt>
              <dd>{value.toLocaleString('zh-CN')} <span>{unit}</span></dd>
            </div>
          ))}
        </dl>
      ) : <NoData />)}
    </section>
  )
}

function ReportRows({ rows }: { rows: { label: string; detail?: string; value: string }[] }) {
  if (!rows.length) return <NoData />
  return (
    <dl className="analytics-report-rows">
      {rows.map(({ label, detail, value }) => (
        <div key={label}>
          <dt>{label}{detail && <span>{detail}</span>}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function OpportunityStageChart({ stages }: { stages: StageConversionRow[] }) {
  if (stages.length === 0) return <NoData />
  const maxCount = Math.max(...stages.map((stage) => stage.reached_count))
  const tickUnit = maxCount >= 20 ? 5 : 1
  const tickStep = Math.max(1, Math.ceil(maxCount / (4 * tickUnit))) * tickUnit
  const ticks = Array.from({ length: 5 }, (_, index) => index * tickStep)
  return (
    <div id="analytics-stage-chart" className="analytics-stage-chart">
      <div className="analytics-stage-axis" aria-hidden="true">
        <div>{ticks.map((tick) => <span key={tick}>{tick.toLocaleString('zh-CN')}</span>)}</div>
      </div>
      <dl aria-label="到达过各阶段的商机数">
        {stages.map((stage) => (
          <div key={stage.stage_id}>
            <dt>{stage.stage_name}</dt>
            <dd>
              <span className="analytics-stage-plot" aria-hidden="true">
                <span
                  className={stage.reached_count === 0 ? 'is-zero' : undefined}
                  style={{ left: `${stage.reached_count / (4 * tickStep) * 100}%` }}
                />
              </span>
              <strong>{asCount('个')(stage.reached_count)}</strong>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/** 横向排名条：名字长、类别多的（业务员、失单原因）用它。 */
const rankHeight = (rows: number) => Math.max(130, rows * 32 + 26)

/** 没有数据时给一句话，**不要**画一张空图（空坐标系比空白更像"出错了"）。 */
function NoData() {
  return <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>暂无数据</div>
}

/** 目标值：空串当 0（目标可以不填），坏输入**抛错点名**（不给静默清空的机会）。
 *
 * 原来写 `Number(x || 0)`：字母是 truthy，`|| 0` 不起作用 → NaN →
 * `JSON.stringify` 变 null → 后端当"没提供这个字段"，目标被悄悄清空。
 */
function targetNumber(raw: unknown, label: string): number {
  const n = parseOptionalNumber(raw)
  if (typeof n === 'number' && Number.isNaN(n)) {
    throw new Error(`${label}只能填数字`)
  }
  return n ?? 0
}

function targetMoney(raw: unknown, label: string): number | string {
  const value = parseOptionalDecimal(raw)
  if (typeof value === 'number' && Number.isNaN(value)) {
    throw new Error(`${label}只能填数字`)
  }
  return value ?? 0
}

const ANALYTICS_REPORTS = [
  { id: 'basis', label: '月度业绩' },
  { id: 'delivery', label: '首批发货', search: '负责人' },
  { id: 'risk-order', label: '延迟未发货', search: '订单号、客户或负责人', limited: true },
  { id: 'product', label: '产品表现', search: '产品、SKU 或规格', limited: true },
  { id: 'sales', label: '业务员表现', search: '业务员姓名', limited: true },
  { id: 'target', label: '目标完成情况', search: '公司、部门或姓名' },
] as const

const ANALYTICS_VIEWS = [
  { id: 'overview', label: '经营概览' },
  { id: 'sales', label: '商机与报价' },
  { id: 'customers', label: '客户与线索' },
  { id: 'payments', label: '回款与应收' },
  { id: 'delivery', label: '发货与交期' },
  { id: 'reports', label: '明细报表' },
  { id: 'target', label: '销售目标' },
] as const
type AnalyticsView = (typeof ANALYTICS_VIEWS)[number]['id']
type AnalyticsReport = (typeof ANALYTICS_REPORTS)[number]['id']

const TARGET_SCOPES = [
  { value: 'company', label: '全公司' },
  { value: 'department', label: '部门' },
  { value: 'person', label: '个人' },
  { value: 'all', label: '全部对象' },
] as const

function LiveActualNote({ show }: { show?: boolean }) {
  return show ? <small style={{ display: 'block', color: 'var(--crm-text-3)', fontSize: 11 }}>按当前数据计算</small> : null
}

function TargetProgress({ label, actual, target, achievement, live }: {
  label: string
  actual: string
  target: string
  achievement: number | null
  live?: boolean
}) {
  return (
    <div className="analytics-target-progress">
      <span>{label}</span>
      <strong>{actual}</strong>
      <LiveActualNote show={live} />
      <small>{achievement == null ? '未设目标' : `目标 ${target} · 已完成 ${Math.round(achievement * 100)}%`}</small>
    </div>
  )
}

export default function AnalyticsPage() {
  const { can, dataScopeLabel } = usePermissions()
  // 钩子里的「全部」是给权限配置用的词；报表上写「全公司」才是业务看得懂的话。
  const scopeLabel = dataScopeLabel === '全部' ? '全公司' : dataScopeLabel
  const canSetTarget = can('settings:manage')
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const view = ANALYTICS_VIEWS.find(({ id }) => id === params.get('view'))?.id ?? 'overview'
  const reportId = ANALYTICS_REPORTS.find(({ id }) => id !== 'target' && id === params.get('report'))?.id
  const activeReport: AnalyticsReport = view === 'target' ? 'target' : reportId ?? 'basis'
  const [showTargetDetails, setShowTargetDetails] = useState(false)
  const [reportKeyword, setReportKeyword] = useState('')
  const [reportMonth, setReportMonth] = useState<string>()
  const [targetScope, setTargetScope] = useState<(typeof TARGET_SCOPES)[number]['value']>()
  const currentMonth = formatBeijingDate(new Date()).slice(5, 7)
  const selectView = (nextView: AnalyticsView, nextReport?: AnalyticsReport) => {
    setParams((previous) => {
      const next = new URLSearchParams(previous)
      if (nextView === 'overview') next.delete('view')
      else next.set('view', nextView)
      if (nextView === 'reports' && nextReport) next.set('report', nextReport)
      else next.delete('report')
      return next
    })
    document.getElementById('analytics-view-nav')?.scrollIntoView({ block: 'nearest' })
  }
  const filterContext = `${view}:${activeReport}`
  const [previousFilterContext, setPreviousFilterContext] = useState<typeof filterContext>()
  if (previousFilterContext !== filterContext) {
    setPreviousFilterContext(filterContext)
    setReportKeyword('')
    setReportMonth(view === 'target' ? currentMonth : undefined)
    setTargetScope(undefined)
    setShowTargetDetails(false)
  }
  const report = ANALYTICS_REPORTS.find((item) => item.id === activeReport)
  const reportSearch = report && 'search' in report ? report.search : undefined
  const keyword = reportKeyword.trim().toLocaleLowerCase()
  const matches = (...fields: (string | null | undefined)[]) =>
    !keyword || fields.some((field) => field?.toLocaleLowerCase().includes(keyword))
  const noMatches = '没有符合筛选条件的记录'
  const [targetYear, setTargetYear] = useState(new Date().getFullYear())
  const [targetModal, setTargetModal] = useState<{
    visible: boolean
    period?: string
    user_id?: number | null
    department_id?: number | null
    expected_updated_at?: string | null
    remark?: string | null
  }>({ visible: false })
  const [showStageDetail, setShowStageDetail] = useState(false)
  const [targetForm, setTargetForm] = useState<{ user_id?: number | null; department_id?: number | null; period: string; new_customer_target: string; sales_target: string; repeat_customer_target: string }>({
    user_id: undefined,
    department_id: undefined,
    period: `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}`,
    new_customer_target: '',
    sales_target: '',
    repeat_customer_target: '',
  })

  const opportunityQuery = useQuery({ queryKey: ['an-opportunities'], queryFn: getOpportunityStats })
  const createdTotalQuery = useQuery({
    queryKey: ['an-created-total'],
    queryFn: () => listOpportunities({ page: 1, page_size: 1 }),
  })
  const ordersAllQuery = useQuery({
    queryKey: ['an-orders-all'],
    // 只在单页取得全部订单时显示关联单数。
    queryFn: () => listOrders({ page: 1, page_size: 200 }),
  })
  const quoteQuery = useQuery({ queryKey: ['an-quotes'], queryFn: getQuoteStats })
  const customerQuery = useQuery({ queryKey: ['an-customers'], queryFn: getCustomerStats })
  const productQuery = useQuery({ queryKey: ['an-products'], queryFn: getProductStats })
  const salesQuery = useQuery({ queryKey: ['an-sales-users'], queryFn: getSalesUserStats })
  const receivableQuery = useQuery({ queryKey: ['an-receivables'], queryFn: getReceivableStats })
  const lossQuery = useQuery({ queryKey: ['an-losses'], queryFn: getLossStats })
  // PRD §23 补齐的三个维度
  const leadQuery = useQuery({ queryKey: ['an-leads'], queryFn: getLeadStats })
  const pricingQuery = useQuery({ queryKey: ['an-pricing'], queryFn: getPricingStats })
  const paymentQuery = useQuery({ queryKey: ['an-payments'], queryFn: getPaymentStats })
  // 交期整维此前是空的（跟单里程碑与发货批次只写不读）
  const deliveryQuery = useQuery({ queryKey: ['an-delivery'], queryFn: () => getDeliveryStats() })
  const targetsQuery = useQuery({
    queryKey: ['sales-targets', targetYear],
    queryFn: () => listSalesTargets(targetYear),
  })
  // 目标口径（场景17）：签单/发货/回款三个口径刻意分开显示，不互相顶替
  const basesQuery = useQuery({
    queryKey: ['sales-target-bases', targetYear],
    queryFn: () => getSalesTargetBases(targetYear),
  })
  const usersQuery = useQuery({
    queryKey: ['assignable-users'],
    // 「全公司或个人」是同一个下拉：人员超过一页时循环取全，否则第 101 位
    // 以后的人无法被选为目标对象。
    queryFn: async () => {
      const first = await listUsers({ page: 1, page_size: 100 })
      const all = [...first.items]
      for (let page = 2; all.length < first.total; page += 1) {
        const next = await listUsers({ page, page_size: 100 })
        if (!next.items.length) break
        all.push(...next.items)
      }
      return { ...first, items: all }
    },
    enabled: targetModal.visible,
  })
  // 团队目标要选部门（文档 §六 :121「目标按团队、业务员、周期设置」）
  const departmentsQuery = useQuery({
    queryKey: ['departments'],
    queryFn: () => listDepartments(),
    enabled: targetModal.visible,
  })
  const targetSaveMutation = useMutation({
    mutationFn: () =>
      upsertSalesTarget({
        period: targetForm.period,
        // 部门与业务员互斥：选了部门就是团队目标，不再带 user_id，
        // 否则后端会按"人 + 部门"两个条件去找同一行，谁都匹配不上
        user_id: targetForm.department_id ? null : (targetForm.user_id ?? null),
        department_id: targetForm.department_id ?? null,
        // 版本属于原期间/对象；切换到另一目标时不能携带原行版本。
        expected_updated_at: targetModal.period === targetForm.period
          && (targetModal.user_id ?? null) === (targetForm.department_id ? null : (targetForm.user_id ?? null))
          && (targetModal.department_id ?? null) === (targetForm.department_id ?? null)
          ? targetModal.expected_updated_at ?? undefined : undefined,
        // 备注没有编辑控件，只保留原目标的值，不复制到另一期间/对象。
        remark: targetModal.period === targetForm.period
          && (targetModal.user_id ?? null) === (targetForm.department_id ? null : (targetForm.user_id ?? null))
          && (targetModal.department_id ?? null) === (targetForm.department_id ?? null)
          ? targetModal.remark : undefined,
        // 不能写 `Number(x || 0)`（2026-10-14 审查修）：字母是 truthy，
        // `|| 0` 不起作用 → NaN → JSON null → 后端当"没提供"，
        // **目标被悄悄清空**，达成率从此显示成"未设目标"。
        new_customer_target: targetNumber(targetForm.new_customer_target, '新客目标'),
        sales_target: targetMoney(targetForm.sales_target, '销售额目标'),
        repeat_customer_target: targetMoney(targetForm.repeat_customer_target, '复购目标'),
      }),
    onSuccess: () => {
      Toast.success('目标已保存')
      setTargetModal({ visible: false })
      void queryClient.invalidateQueries({ queryKey: ['sales-targets'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ---- 结账 / 重算（返工单第 4 条：这三个接口后端早就有，前端一个都没接）----
  // 结账是"把这一期已经过完的账抄一份存档"的**显式动作**：什么时候算结完账只有人知道，
  // 程序猜不出来（见后端 target_actuals.py 的模块说明）。
  const [freezeModal, setFreezeModal] = useState<{
    visible: boolean
    mode: 'freeze' | 'refreeze'
  }>({ visible: false, mode: 'freeze' })
  const [freezePeriod, setFreezePeriod] = useState('')
  const [freezeNote, setFreezeNote] = useState('')

  // 可结账的期间 = 已经过完的月份（当月不结：数据还在产生，冻了等于冻在半路上）
  const pastPeriods = (() => {
    const list: string[] = []
    const cursor = new Date()
    let year = cursor.getFullYear()
    let month = cursor.getMonth() // 0-based；当月没过完，所以从上一个月开始倒推
    for (let i = 0; i < 12; i += 1) {
      month -= 1
      if (month < 0) {
        month = 11
        year -= 1
      }
      list.push(`${year}-${String(month + 1).padStart(2, '0')}`)
    }
    return list
  })()

  const openFreezeModal = (mode: 'freeze' | 'refreeze', period?: string) => {
    setFreezePeriod(period ?? pastPeriods[0])
    setFreezeNote('')
    setFreezeModal({ visible: true, mode })
  }

  const freezeMutation = useMutation({
    mutationFn: async () => {
      if (freezeModal.mode === 'refreeze') {
        // 重算必须带原因，后端也会再校验一次（这里只是把话说在前面）
        const result = await refreezeSalesActuals(freezePeriod, freezeNote)
        return { removed: result.removed }
      }
      await freezeSalesActuals(freezePeriod, freezeNote || undefined)
      return { removed: 0 }
    },
    onSuccess: ({ removed }) => {
      Toast.success(
        freezeModal.mode === 'freeze'
          ? `${freezePeriod} 已结账：实绩与构成明细一并存档`
          : `${freezePeriod} 已重算${removed ? `，清除陈旧汇总 ${removed} 条` : ''}`,
      )
      setFreezeModal({ visible: false, mode: 'freeze' })
      void queryClient.invalidateQueries({ queryKey: ['sales-targets'] })
      void queryClient.invalidateQueries({ queryKey: ['target-drilldown'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ---- 可追溯明细（§4.3）----
  // 三个销售口径都值得点开看，默认看**考核口径**（确认回款）——
  // 差额就是按它算的，用户最想核的就是这一列。
  const [drilldown, setDrilldown] = useState<{
    visible: boolean
    period?: string
    user_id?: number | null
    department_id?: number | null
    user_name?: string
    metric: string
  }>({ visible: false, metric: 'received' })

  const drilldownQuery = useQuery({
    queryKey: [
      'target-drilldown',
      drilldown.period,
      drilldown.metric,
      drilldown.user_id,
      drilldown.department_id,
    ],
    queryFn: () =>
      getSalesTargetDrilldown({
        period: drilldown.period as string,
        metric: drilldown.metric,
        user_id: drilldown.user_id ?? null,
        department_id: drilldown.department_id ?? null,
      }),
    enabled: drilldown.visible && !!drilldown.period,
  })

  const openDrilldown = (row: SalesTargetRow, metric: string) => {
    setDrilldown({
      visible: true,
      period: row.period,
      user_id: row.user_id,
      department_id: row.department_id ?? null,
      user_name: row.user_name,
      metric,
    })
  }

  const openTargetModal = (row?: SalesTargetRow) => {
    if (row) {
      setTargetForm({
        user_id: row.user_id,
        department_id: row.department_id,
        period: row.period,
        new_customer_target: String(row.new_customer_target),
        sales_target: String(row.sales_target_exact ?? row.sales_target),
        repeat_customer_target: row.repeat_customer_target
          ? String(row.repeat_customer_target_exact ?? row.repeat_customer_target)
          : '',
      })
      setTargetModal({ visible: true, period: row.period, user_id: row.user_id, department_id: row.department_id, expected_updated_at: row.target_id ? row.updated_at : undefined, remark: row.remark })
    } else {
      setTargetForm({
        user_id: undefined,
        period: `${targetYear}-${reportMonth ?? currentMonth}`,
        new_customer_target: '',
        sales_target: '',
        repeat_customer_target: '',
      })
      setTargetModal({ visible: true })
    }
  }

  const rate = (actual: number, target: number) => {
    if (!target) return <span style={{ color: 'var(--crm-text-3)' }}>-</span>
    const pct = Math.round((actual / target) * 100)
    const color = pct >= 100 ? 'var(--crm-primary)' : pct >= 70 ? 'var(--crm-caution)' : 'var(--crm-error)'
    return <span style={{ fontWeight: 600, color }}>{pct}%</span>
  }

  const opportunity = opportunityQuery.data

  const orders = ordersAllQuery.data
  const orderCountWithOpportunity = orders && orders.total <= orders.items.length
    ? orders.items.filter((order) => order.opportunity_id).length
    : null
  const quote = quoteQuery.data
  const customer = customerQuery.data
  const receivable = receivableQuery.data
  const lead = leadQuery.data
  const pricing = pricingQuery.data
  const payment = paymentQuery.data
  const delivery = deliveryQuery.data
  const deliverySummary = delivery?.summary
  const cycle = opportunity?.cycle
  const money = (value?: number) => `¥${Math.round(value ?? 0).toLocaleString('zh-CN')}`

  // 含外币、未折算的提醒（兜底）。正常情况下永远为空 —— 业务口径是"只做国内、
  // 币种固定人民币"，服务层也加了闸。它是给"万一"准备的：外币与人民币直接相加
  // 得到的数字是错的，但页面上看不出来，宁可明说"可能不准"。
  //
  // 提醒是**页面级**的：只从"回对象"的那几个接口里取（应收/商机/回款），
  // 本页另有几个接口回的是数组、装不下这句话（后端 `_with_fx_note` 的注释里
  // 列了是哪几个），不影响整页提示。
  const currencyWarnings = [
    receivableQuery.data?.currency_warnings,
    opportunityQuery.data?.currency_warnings,
    paymentQuery.data?.currency_warnings,
  ].find((notes) => (notes?.length ?? 0) > 0) ?? []

  // 业绩趋势：四个口径拼到一条横轴上。**全为零的月份不画** —— 十二个月里只有两三个月
  // 有数时，剩下十条贴地的线只会让图看着像坏了。
  const bases = basesQuery.data
  const basisValue = (rows: { month: string; value: number }[] | undefined, month: string) =>
    (rows ?? []).find((row) => row.month === month)?.value ?? 0
  const trendMonths = (bases?.signed ?? [])
    .map((row) => row.month)
    .filter(
      (month) =>
        basisValue(bases?.signed, month) ||
        basisValue(bases?.shipped, month) ||
        basisValue(bases?.received, month) ||
        basisValue(bases?.repeat_net, month),
    )

  // X 轴月份补「月」字：后端给的是 "10"，画出来轴上只有「10」「11」，
  // 旁边交期趋势的表格里写的却是「10月」—— 同屏两处口径不一致（2026-11-20 实拍）。
  const monthLabel = (month: string) => (/^\d{1,2}$/.test(month) ? `${month}月` : month)

  // 交期趋势：单量柱 + 准时率线，只用有发货记录的那几个月
  const deliveryTrend = (delivery?.trend ?? []).filter((row) => row.on_time + row.late > 0)

  const targetRowsInYear = targetsQuery.data?.rows ?? []
  const scopeOfTarget = (row: SalesTargetRow) => row.department_id ? 'department' : row.user_id ? 'person' : 'company'
  const defaultTargetScope = TARGET_SCOPES.find(({ value }) =>
    targetRowsInYear.some((row) => value === scopeOfTarget(row)))?.value ?? 'company'
  const selectedTargetScope = targetScope ?? defaultTargetScope
  const targetRows = targetRowsInYear.filter((row) =>
    (!reportMonth || row.period.slice(5) === reportMonth)
    && (selectedTargetScope === 'all' || scopeOfTarget(row) === selectedTargetScope)
    && matches(row.user_name))
  const targetEmpty = keyword ? noMatches : '所选月份和对象暂无目标数据'
  const targetRowKey = (row: SalesTargetRow) => `${row.period}-${row.user_id ?? 'all'}-${row.department_id ?? 'all'}`

  return (
    <div className="page-container analytics-page">
      <PageHeader title="数据分析" />
      <nav id="analytics-view-nav" className="analytics-view-nav" aria-label="数据分析主题">
        {ANALYTICS_VIEWS.map(({ id, label }) => (
          <button key={id} type="button" aria-current={view === id ? 'page' : undefined}
            onClick={() => selectView(id)}>
            {label}
          </button>
        ))}
      </nav>
      {currencyWarnings.map((note) => (
        <div key={note} style={{ marginBottom: 14 }}>
          <Banner type="warning" closeIcon={null} description={note} title="汇总里含外币金额" />
        </div>
      ))}

      <p className="analytics-scope-note">
        <span className="crm-help-label">
          统计范围：{scopeLabel}
          <HelpTip
            forLabel="统计范围"
            text="数据范围由你的角色决定：全公司＝所有人的记录；本部门＝同部门同事；本部门及下级＝本部门连同所有下级部门；仅本人＝只看自己。可在「设置 → 我的 → 我的信息」查看自己的数据范围。两个同事看到不同数字，正是因为范围不同。"
          />
        </span>
        {(view === 'reports' || view === 'target') && <span>月度业绩和目标金额按签单归属统计，发货风险按当前负责人统计。</span>}
      </p>

      {view === 'overview' && <div className="analytics-view" aria-label="经营概览">
        <dl className="analytics-summary">
          <div><dt>已成交商机</dt><dd>{readIssue(opportunityQuery) ?? (opportunity ? `${opportunity.won_count} 个` : '—')}</dd></div>
          <div><dt>尚未到账</dt><dd>{readIssue(receivableQuery) ?? (receivable ? money(receivable.unreceived_amount) : '—')}</dd></div>
          <div><dt>逾期应收</dt><dd>{readIssue(paymentQuery) ?? (payment ? money(payment.overdue_amount) : '—')}</dd></div>
          <div><dt>超期未发首批</dt><dd>{readIssue(deliveryQuery) ?? (deliverySummary ? `${deliverySummary.risk_order_count} 单` : '—')}</dd></div>
        </dl>
        <ReportSection title={`${targetYear} 年业绩走势`} query={basesQuery} note="签单看建单月，发货看实际发货月，回款看财务确认月。">
          {trendMonths.length === 0 ? (
            <NoData />
          ) : (
            <EChart
              height={264}
              ariaLabel="月度签单、发货、回款与老客净额的折线图"
              option={lineChartOption({
                categories: trendMonths.map(monthLabel),
                series: [
                  { name: '签单', values: trendMonths.map((m) => basisValue(bases?.signed, m)) },
                  { name: '发货', values: trendMonths.map((m) => basisValue(bases?.shipped, m)) },
                  { name: '回款', values: trendMonths.map((m) => basisValue(bases?.received, m)) },
                  {
                    name: '老客净额',
                    values: trendMonths.map((m) => basisValue(bases?.repeat_net, m)),
                  },
                ],
                format: asMoney,
                axisFormat: compactMoney,
              })}
            />
          )}
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--crm-text-3)', lineHeight: 1.8 }}>
            <Button size="small" theme="borderless" onClick={() => selectView('reports', 'basis')}>查看各月金额</Button>
          </div>
        </ReportSection>
      </div>}

      {view === 'sales' && <div className="analytics-view" aria-label="商机与报价">
        <ReportSection title="成交与转化">
          <AnalyticsKpis
            tintValues={false}
            className="kpi-grid analytics-metrics"
            items={[
              {
                // 7 张卡排 4 + 3，右下永远空一格（主人 2026-11-19：
                // 「加一个…空一个丑啊」）。补一张"总量"当分母放在最前：
                // 后面五个都是率，率没有基数就说不清 —— 79 单里成交 11 单，
                // 比单看 52% 更像一句人话。凑满 8 张，两行正好铺齐。
                query: createdTotalQuery,
                label: '累计商机',
                tone: 'default',
                value: `${createdTotalQuery.data?.total ?? 0} 单`,
                hint: '含成交与失单的全部商机',
              },
              { query: opportunityQuery, label: '商机成交率',
                tone: 'primary',
                value: opportunity && opportunity.won_count + opportunity.loss_count > 0
                  ? `${(opportunity.win_rate * 100).toFixed(0)}%` : '—',
                hint: opportunity ? `成交 ${opportunity.won_count} 个 · 失单 ${opportunity.loss_count} 个` : undefined,
                help: '已成交商机 ÷（已成交 + 已失单）。进行中的商机不参与。',
              },
              { query: quoteQuery, label: '报价接受率',
                tone: 'success',
                value: quote && quote.sent_count !== 0 ? `${(quote.accept_rate * 100).toFixed(0)}%` : '—',
                hint: quote?.sent_count == null ? undefined : `已接受 ${quote.accepted_count} / 已发出 ${quote.sent_count} 张`,
                help: '客户已接受的报价 ÷ 已发出的报价。已发出包括等待答复、已接受和已拒绝。',
              },
              {
                // 让价为负 = 整体报得比建议价还高，写成「-0.20%」像算错了 —— 按符号切「让价 / 加价」。
                query: quoteQuery,
                label: (quote?.average_discount ?? 0) >= 0 ? '平均让价幅度' : '平均加价幅度',
                tone: 'warning',
                value: `${Math.abs((quote?.average_discount ?? 0) * 100).toFixed(2)}%`,
                help: '让价 =（建议价 − 实际报价）÷ 建议价，按报价明细逐行平均。它反映实际让了多少，不是折扣权限。',
                hint: `平均版本数 ${quote?.average_versions ?? 0}`,
              },
              { query: pricingQuery, label: '低价审批比例',
                tone: 'warning',
                // 同页下方「低价审批率」是按报价明细行算的（3/44），这张卡原来用报价版本口径
                // （12/36），两个数打架。统一按明细行 —— help 文案本来就写的"报价明细占比"。
                value: `${((pricing?.low_price_approval_rate ?? 0) * 100).toFixed(0)}%`,
                help: '需要走审批的报价明细占比。拟报价低于授权底价或公司保护价的行，会被标成需要审批。',
                hint: `需审批 ${pricing?.approval_required_count ?? 0} 行 / 明细 ${pricing?.item_count ?? 0} 行`,
              },

              { query: leadQuery, label: '线索转化率',
                tone: 'primary',
                value: lead?.total ? `${(lead.conversion_rate * 100).toFixed(0)}%` : '—',
                hint: lead ? `已转客户 ${lead.converted_count} / 线索 ${lead.total} 条` : undefined,
                help: '已转成客户的线索 ÷ 统计范围内的全部线索，含线索池中未分配的线索。',
              },
              { query: customerQuery, label: '近期活跃 / 长期未跟进',
                tone: 'success',
                value: `${customer?.active_count ?? 0} / ${customer?.dormant_count ?? 0}`,
                help: `以最近一次跟进或业务进展为准，两者都没有时看建档日。最近 ${customer?.active_days ?? 30} 天算活跃；其余客户超过 ${customer?.dormant_days ?? 30} 天算长期未跟进。复购按当前范围内的在册客户统计，一家客户有两张及以上未取消订单即算一次。`,
                hint: `复购客户 ${customer?.repeat_customer_count ?? 0} 家`,
              },
              { query: opportunityQuery, label: '平均成交周期',
                tone: 'default',
                value: cycle?.average_days != null ? `${cycle.average_days} 天` : '-',
                hint: cycle?.won_with_history
                  ? `基于 ${cycle.won_with_history} 个有阶段记录的成交商机`
                  : '暂无成交商机',
              },
            ]}
          />
        </ReportSection>
      <ReportSection title="商机与关联订单" note="前两项数商机，最后一项数订单。一条商机可能关联多张订单。">
        <AnalyticsKpis
          tintValues={false}
          className="kpi-grid analytics-metrics"
          items={[
            { query: createdTotalQuery, label: '累计商机', value: createdTotalQuery.data ? `${createdTotalQuery.data.total} 个` : '—' },
            { query: opportunityQuery, label: '已成交商机', value: opportunity ? `${opportunity.won_count} 个` : '—' },
            { query: ordersAllQuery, label: '关联商机的订单',
              value: orderCountWithOpportunity === null ? '—' : `${orderCountWithOpportunity} 张`,
              hint: orders && orders.total > orders.items.length ? '订单列表未取全，无法统计全部关联订单' : undefined,
            },
          ]}
        />
        <div className="deal-steps-toggle">
          <Button className="deal-toggle-btn" theme="borderless" size="small" aria-expanded={showStageDetail}
            aria-controls={showStageDetail && opportunity?.stage_conversion?.length ? 'analytics-stage-chart' : undefined}
            onClick={() => setShowStageDetail((open) => !open)}>
            {showStageDetail ? '收起阶段图' : '查看商机阶段图'}
          </Button>
        </div>
        {showStageDetail && (
          <>
            <p className="deal-steps-note">各阶段显示曾经到达过的商机数。同一商机可能经过多个阶段，也可能跳过阶段。</p>
            {readIssue(opportunityQuery) ?? <OpportunityStageChart stages={opportunity?.stage_conversion ?? []} />}
          </>
        )}
      </ReportSection>
        <div className="analytics-sales-breakdown">
          <ReportSection title="进行中商机" query={opportunityQuery} note="按业务阶段顺序">
            <ReportRows rows={(opportunity?.funnel ?? []).map((row) => ({
              label: row.stage_name, value: asCount('个')(row.count),
            }))} />
          </ReportSection>
          <ReportSection title="失单原因" query={lossQuery}>
            <ReportRows rows={(lossQuery.data ?? []).map((row) => ({
              label: row.name, value: asCount('单')(row.value),
            }))} />
          </ReportSection>
          <ReportSection title="平均报价" query={pricingQuery} note="按客户等级">
            <ReportRows rows={(pricing?.average_quoted_price_by_level ?? []).map((row) => ({
              label: row.level.endsWith('级') ? row.level : `${row.level} 级`,
              detail: `${row.item_count} 条明细`,
              value: asMoney(row.average_price),
            }))} />
            {pricing && (
              <dl className="analytics-price-notes">
                <div><dt>低价审批率</dt><dd>{asPercent(pricing.low_price_approval_rate)}</dd></div>
                <div>
                  <dt>平均{pricing.average_discount_rate >= 0 ? '让价' : '加价'}</dt>
                  <dd>{Math.abs(pricing.average_discount_rate * 100).toFixed(2)}%</dd>
                </div>
                <div>
                  <dt>最大{pricing.max_discount_rate >= 0 ? '让价' : '加价'}</dt>
                  <dd>{Math.abs(pricing.max_discount_rate * 100).toFixed(2)}%</dd>
                </div>
              </dl>
            )}
          </ReportSection>
        </div>
      </div>}

      {view === 'customers' && <div className="analytics-view" aria-label="客户与线索">
        <div className="analytics-status-summary">
          <StatusCounts title="线索状态" query={leadQuery} data={lead?.by_status ?? []} unit="条" />
          {(lead?.invalid_reasons.length ?? 0) > 0 && (
            <StatusCounts
              title="线索废弃原因"
              note="统计仍为无效的线索。已移入回收站的线索只在这里统计原因。"
              data={lead?.invalid_reasons ?? []}
              unit="条"
            />
          )}
        </div>
        <Row columns={2}>
          <ReportSection title="客户来源分布" query={customerQuery}>
            <Donut data={customer?.by_source ?? []} format={asCount('家')} centerLabel="家客户" />
          </ReportSection>
          <ReportSection title="线索来源分布" query={leadQuery}>
            <Donut data={lead?.by_source ?? []} format={asCount('条')} centerLabel="条线索" />
          </ReportSection>
          <ReportSection title="客户等级分布" query={customerQuery}>
            <Donut data={customer?.by_level ?? []} format={asCount('家')} centerLabel="家客户" />
          </ReportSection>
        </Row>
      </div>}

      {view === 'payments' && <div className="analytics-view" aria-label="回款与应收">
        <ReportSection title="应收和到账金额" extra={can('payment:view') && <Link to="/receivables">打开回款中心</Link>}>
          <AnalyticsKpis
            tintValues={false}
            className="kpi-grid analytics-metrics"
            items={[
              { query: receivableQuery, label: '应收合计',
                tone: 'primary',
                value: money(receivable?.plan_amount),
                hint: `实际到账 ${money(receivable?.received_amount)} · 已分配 ${money(receivable?.allocated_amount)}`,
                help: '有效应收计划上的金额合计，按订单当前负责人统计。',
              },
              { query: receivableQuery, label: '尚未到账',
                tone: 'warning',
                value: money(receivable?.unreceived_amount),
                hint: `逾期 ${receivable?.overdue_count ?? 0} 期`,
                help: '应收合计减去财务已确认的到账金额，包含尚未到期的款。',
              },
              { query: receivableQuery, label: '已到账待分配',
                tone: 'success',
                value: money(receivable?.unallocated_amount),
                help: '客户已经付款、但还没分配到具体应收期的钱。分配之后，才会冲减对应账期的应收。',
                // 只报口径，不带指令：一句"请先核对分配再催款"把卡片撑成两行，
                // 也会让人以为这卡是个待办（2026-11-21 巡检）
                // 值为 0 时还挂「应收期未结 131 万」读着像自相矛盾：0 就是没有
                // 已到账没分掉的钱，这一句得先说话（2026-11-21 巡检）
                hint: (receivable?.unallocated_amount ?? 0) > 0
                  ? `应收期未结 ${money(receivable?.unsettled_plan_amount)}`
                  : '没有已到账未分配的钱',
              },
              { query: paymentQuery, label: '逾期应收',
                tone: 'error',
                value: money(payment?.overdue_amount),
                help: '已经过了应收日期、仍然没有结清的金额，按每期应收统计。',
                hint: `逾期 ${payment?.overdue_node_count ?? 0} 期`,
              },
            ]}
          />
        </ReportSection>
        <div className="analytics-status-summary">
          <StatusCounts title="各期应收状态" query={receivableQuery} data={receivable?.by_status ?? []} unit="期" />
          <StatusCounts title="应收逾期了多久" query={paymentQuery} note="只统计未结清的应收" data={payment?.aging ?? []} unit="期" />
        </div>
        <ReportSection title="回款方式分布" query={paymentQuery} note="按金额">
          <Donut
            data={(payment?.by_payment_method ?? []).map((row) => ({
              name: row.name,
              value: Math.round(row.value),
            }))}
            format={asMoney}
            /* 中心用紧凑金额：环内直径只有 ~99px，「¥296,830」9 个字会压到环壁
               （2026-11-20 实拍，数字左半边已经出了圆）。「¥29.7万」6 个字正合适，
               精确到元的值在右侧图例里给。 */
            centerValueFormat={centerMoney}
            centerLabel="合计"
          />
        </ReportSection>
      </div>}

      {view === 'delivery' && <div className="analytics-view" aria-label="发货与交期">
        <ReportSection title="首批发货与交期风险" query={deliveryQuery} extra={
          <Button size="small" theme="borderless" onClick={() => selectView('reports', 'risk-order')}>查看超期订单</Button>
        }>
          <KpiStrip
            tintValues={false}
            className="kpi-grid analytics-metrics"
            items={[
              {
                label: '首批准时发货率',
                tone: 'success',
                value: deliverySummary?.delivered_order_count
                  ? `${(deliverySummary.on_time_rate * 100).toFixed(0)}%`
                  : '-',
                // 没有已交付样本时给 "-" 而不是 0%——0% 会被读成"全都延迟"
                hint: `准时 ${deliverySummary?.on_time_count ?? 0} / 延迟 ${deliverySummary?.late_count ?? 0}`,
              },
              {
                label: '首批发货单数',
                tone: 'default',
                value: `${deliverySummary?.delivered_order_count ?? 0} 单`,
                hint: '只计有计划发货日的订单',
              },
              {
                label: '首批发货晚了',
                tone: 'error',
                value: `${deliverySummary?.late_count ?? 0} 单`,
                hint: '首批实际发货日比计划发货日晚',
              },
              {
                label: '已过发货日、首批未发',
                tone: 'warning',
                value: `${deliverySummary?.risk_order_count ?? 0} 单`,
                hint: `另有 ${deliverySummary?.due_soon_order_count ?? 0} 单将在 ${deliverySummary?.due_soon_days ?? 7} 天内到期`,
              },
            ]}
          />
        </ReportSection>
        <Row columns={2}>
        <ReportSection title="交期趋势" query={deliveryQuery} note="柱子是发货单数，折线是首批准时发货率。">
          {deliveryTrend.length === 0 ? (
            <NoData />
          ) : (
            <EChart
              height={188}
              ariaLabel="每月准时与延迟发货单量的堆叠柱，叠加准时率折线"
              option={comboBarLineOption({
                categories: deliveryTrend.map((row) => row.label),
                bars: [
                  { name: '准时', values: deliveryTrend.map((row) => row.on_time) },
                  { name: '延迟', values: deliveryTrend.map((row) => row.late) },
                ],
                line: {
                  name: '准时率',
                  values: deliveryTrend.map((row) =>
                    row.on_time + row.late > 0
                      ? Math.round((row.on_time / (row.on_time + row.late)) * 100)
                      : null,
                  ),
                },
                barFormat: asCount('单'),
                lineFormat: asPercentValue,
                lineAxis: 'percent',
              })}
            />
          )}
        </ReportSection>
        <ReportSection title="哪些履约环节超期" query={deliveryQuery} note="只计已过计划日、仍未完成的环节">
          {(delivery?.overdue_nodes ?? []).length === 0 ? (
            <NoData />
          ) : (
            <EChart
              height={rankHeight((delivery?.overdue_nodes ?? []).length)}
              ariaLabel="逾期节点分布条形图"
              option={rankBarChartOption({
                data: delivery?.overdue_nodes ?? [],
                format: asCount('个节点'),
              })}
            />
          )}
          <div style={{ marginTop: 14, fontSize: 13, color: 'var(--crm-text-2)', lineHeight: 2 }}>
            <div>在跟订单：{deliverySummary?.open_order_count ?? 0} 单</div>
            <div>已过计划发货日、首批未发：{deliverySummary?.risk_order_count ?? 0} 单</div>
            <div>
              {deliverySummary?.due_soon_days ?? 7} 天内到期且未发货：
              {deliverySummary?.due_soon_order_count ?? 0} 单
            </div>
            <div>在跟但交期或类型待补充：{deliverySummary?.no_due_date_open_count ?? 0} 单</div>
            <div>
              平均延迟：
              {deliverySummary?.average_delay_days != null
                ? `${deliverySummary.average_delay_days} 天`
                : '-'}
              （最长 {deliverySummary?.max_delay_days ?? '-'} 天）
            </div>
          </div>
        </ReportSection>
        </Row>
        <Button size="small" theme="borderless" onClick={() => selectView('reports', 'delivery')}>查看首批发货明细</Button>
      </div>}

      {(view === 'reports' || view === 'target') && <section id="analytics-reports" aria-label={view === 'target' ? '销售目标' : '明细报表'}>
        {view === 'reports' && <div className="analytics-report-picker" role="group" aria-label="选择报表">
          {ANALYTICS_REPORTS.filter(({ id }) => id !== 'target').map(({ id, label }) => (
            <button key={id} type="button" aria-pressed={activeReport === id} onClick={() => selectView('reports', id)}>
              {label}
            </button>
          ))}
        </div>}
        <div className="analytics-report-filters" role="group" aria-label="筛选当前报表">
          {(activeReport === 'basis' || activeReport === 'target') && (
            <Select aria-label="报表年份" value={targetYear} onChange={(value) => setTargetYear(value as number)}
              optionList={[targetYear - 1, targetYear, targetYear + 1].map((year) => ({ value: year, label: `${year} 年` }))}
              style={{ width: 116 }}
            />
          )}
          {(activeReport === 'basis' || activeReport === 'target' || activeReport === 'delivery') && (
            <Select aria-label={activeReport === 'delivery' ? '月度发货表月份' : '报表月份'}
              placeholder={activeReport === 'delivery' ? '月度发货表月份' : '全部月份'}
              value={activeReport === 'target' ? reportMonth ?? 'all' : reportMonth}
              onChange={(value) => setReportMonth(typeof value === 'string' && value !== 'all' ? value : undefined)}
              showClear={activeReport !== 'target'}
              optionList={activeReport === 'delivery'
                ? (delivery?.trend ?? []).map((row) => ({ value: row.month, label: row.month.replace('-', ' 年 ') + ' 月' }))
                : [...(activeReport === 'target' ? [{ value: 'all', label: '全部月份' }] : []), ...Array.from({ length: 12 }, (_, index) => ({
                    value: String(index + 1).padStart(2, '0'),
                    label: `${index + 1} 月`,
                  }))]}
              style={{ width: activeReport === 'delivery' ? 156 : 128 }}
            />
          )}
          {activeReport === 'target' && (
            <Select aria-label="目标对象" value={selectedTargetScope} optionList={[...TARGET_SCOPES]}
              onChange={(value) => { setTargetScope(value as typeof targetScope); setReportKeyword('') }}
              style={{ width: 130 }}
            />
          )}
          {reportSearch && (
            <Input aria-label={`搜索${reportSearch}`} placeholder={`搜索${reportSearch}`} showClear
              value={reportKeyword} onChange={setReportKeyword} style={{ width: 240 }}
            />
          )}
          {activeReport === 'target' ? (
            (reportKeyword || reportMonth !== currentMonth || selectedTargetScope !== defaultTargetScope) &&
            <Button theme="borderless" size="small" onClick={() => {
              setReportKeyword('')
              setReportMonth(currentMonth)
              setTargetScope(undefined)
            }}>重置筛选</Button>
          ) : (reportKeyword || reportMonth) && (
            <Button theme="borderless" size="small" onClick={() => { setReportKeyword(''); setReportMonth(undefined) }}>清除筛选</Button>
          )}
          {report && 'limited' in report && <span className="an-card-note">搜索范围为当前报表列出的记录。</span>}
        </div>
        {activeReport === 'delivery' && <ReportSection id="detail-delivery" title="首批发货情况（按负责人）" query={deliveryQuery}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 10, lineHeight: 1.7 }}>
            统计近 {deliverySummary?.window_months ?? 12} 个月的首批发货。首批在计划发货日当天或之前发出，就算准时。
            客户约的是到货日期时，计划发货日按到货日期减运输天数算。另有 {deliverySummary?.undated_delivered_count ?? 0} 单缺少可核对的计划发货日。
          </div>
          {(delivery?.by_owner ?? []).length === 0 ? (
            <NoData />
          ) : (
            <Table<DeliveryOwnerRow>
              size="small"
              pagination={false}
              rowKey={(r?: DeliveryOwnerRow) => String(r?.owner_id ?? r?.owner_name ?? '')}
              dataSource={(delivery?.by_owner ?? []).filter((row) => matches(row.owner_name))}
              empty={keyword ? noMatches : undefined}
              columns={[
                { title: '负责人', dataIndex: 'owner_name', width: 110 },
                { title: '首批发货单数', dataIndex: 'order_count', width: 96 },
                { title: '准时', dataIndex: 'on_time_count', width: 70 },
                { title: '延迟', dataIndex: 'late_count', width: 70 },
                {
                  title: '准时率',
                  dataIndex: 'on_time_rate',
                  width: 90,
                  render: (v: number) => `${(v * 100).toFixed(0)}%`,
                },
                {
                  title: '平均延迟',
                  dataIndex: 'average_delay_days',
                  render: (v: number | null) => (v == null ? '-' : `${v} 天`),
                },
              ]}
            />
          )}
          <h4 className="analytics-report-subtitle">按月查看准时和延迟单数</h4>
          <Table<DeliveryTrendRow>
            size="small"
            pagination={false}
            rowKey="month"
            dataSource={deliveryTrend.filter((row) => !reportMonth || row.month === reportMonth)}
            columns={[
              { title: '月份', dataIndex: 'label', width: 80 },
              { title: '准时', dataIndex: 'on_time', width: 70 },
              { title: '延迟', dataIndex: 'late', width: 70 },
              {
                title: '准时率',
                render: (_: unknown, r: DeliveryTrendRow) =>
                  r.on_time + r.late ? `${((r.on_time / (r.on_time + r.late)) * 100).toFixed(0)}%` : '-',
              },
            ]}
            empty={reportMonth ? '所选月份没有发货记录' : '近 12 个月还没有发货记录'}
          />
        </ReportSection>}

        {activeReport === 'risk-order' && <ReportSection id="detail-risk-order" title="已过发货日、首批未发的订单" query={deliveryQuery}>
          <Table<DeliveryRiskOrder>
            columns={[
              { title: '订单号', dataIndex: 'order_no', width: 170 },
              {
                title: '客户',
                dataIndex: 'customer_name',
                width: 160,
                render: (v: string | null) => v ?? '-',
              },
              {
                title: '负责人',
                dataIndex: 'owner_name',
                width: 110,
                render: (v: string | null) => v ?? '未分配',
              },
              { title: '计划发货日', dataIndex: 'delivery_date', width: 120 },
              {
                title: '超期',
                dataIndex: 'days_overdue',
                width: 90,
                render: (v: number) => (
                  <span style={{ color: 'var(--crm-error)', fontWeight: 600 }}>{v} 天</span>
                ),
              },
              { title: '订单状态', dataIndex: 'status_label', width: 110 },
            ]}
            dataSource={(delivery?.risk_orders ?? []).filter((row) =>
              matches(row.order_no, row.customer_name, row.owner_name ?? '未分配'))}
            loading={deliveryQuery.isLoading}
            rowKey="order_id"
            pagination={false}
            empty={keyword ? noMatches : '没有超期未发货的订单'}
          />
        </ReportSection>}

        {activeReport === 'product' && <ReportSection id="detail-product" title="产品表现（询盘 / 报价 / 成交 / 失单 / 利润）" query={productQuery}>
          <Table<ProductStat>
            // 10 列在 1180px 内排得下（2026-11-21 巡检：原来 12 列，右侧「成交数量/失单/毛利」
            // 被容器直接切掉，页面看不到）。同族指标并成一列用「/」隔开：
            // 报价 = 正式/草稿，成交 = 成交商机/失单。
            columns={[
              {
                title: 'SKU',
                dataIndex: 'sku_code',
                width: 110,
                // 编码认不出是哪个产品，第二行带产品名一起读。
                render: (v: string | null, record: ProductStat) => (
                  <div>
                    <div>{record.sku_name || v || '未命名产品'}</div>
                    {v && record.sku_name && (
                      <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{v}</div>
                    )}
                  </div>
                ),
              },
              { title: '产品', dataIndex: 'product_name', width: 132, ellipsis: true, render: (v: string | null) => v ?? '-' },
              { title: '规格', dataIndex: 'specification', width: 152, ellipsis: true, render: (v: string | null) => v ?? '-' },
              { title: '询盘', dataIndex: 'inquiry_times', width: 60 },
              {
                title: '报价（正式/草稿）',
                width: 104,
                render: (_: unknown, r: ProductStat) => `${r.quote_formal_times} / ${r.quote_draft_times}`,
              },
              {
                title: '报价数量',
                dataIndex: 'quote_quantity',
                width: 88,
                render: (v: number) => v.toLocaleString('zh-CN'),
              },
              {
                title: '成交/失单',
                width: 92,
                render: (_: unknown, r: ProductStat) => `${r.won_times} / ${r.lost_times}`,
              },
              {
                title: '实际成交数量',
                dataIndex: 'order_won_quantity',
                width: 104,
                render: (v: number) => (v ?? 0).toLocaleString('zh-CN'),
              },
              // 成本口径：无 price:manage 的人不显示这一列（后端也已返回 null）
              ...(can('price:manage')
                ? [
                    {
                      title: '报价预估毛利',
                      dataIndex: 'quote_estimated_profit',
                      width: 118,
                      className: 'cell-money',
                      render: (v: number | null) =>
                        v === null || v === undefined
                          ? '-'
                          : `¥${v.toLocaleString('zh-CN')}`,
                    } as const,
                  ]
                : []),
            ]}
            // 全 0 行不渲染：库里有一批 SKU 一次都没被询过/报过/卖过（ZKB-0041 这类），
            // 摆十几行 0 只让人以为"系统没数"。有数才上来。
            dataSource={(productQuery.data ?? []).filter(
              (r) =>
                r.inquiry_times +
                  r.quote_draft_times +
                  r.quote_formal_times +
                  r.version_revision_times +
                  r.quote_quantity +
                  r.won_times +
                  r.lost_times +
                  (r.order_won_quantity ?? 0) >
                0,
            ).filter((row) => matches(row.sku_code, row.sku_name, row.product_name, row.specification))}
            loading={productQuery.isLoading}
            rowKey="sku_code"
            pagination={false}
            empty={keyword ? noMatches : '还没有报价数据'}
          />
        </ReportSection>}

        {activeReport === 'sales' && <ReportSection id="detail-sales" title="业务员表现" query={salesQuery}>
          <Table<SalesUserStat>
            columns={[
              { title: '姓名', dataIndex: 'name', width: 120 },
              { title: '客户数', dataIndex: 'customer_count', width: 90 },
              { title: '跟进数', dataIndex: 'followup_count', width: 90 },
              { title: '商机数', dataIndex: 'opportunity_count', width: 90 },
              { title: '报价数', dataIndex: 'quote_count', width: 90 },
              {
                title: '订单金额',
                dataIndex: 'order_amount',
                width: 150,
                className: 'cell-money',
                render: (v: number, r: SalesUserStat) => formatWholeCny(r.order_amount_exact ?? v),
              },
              {
                title: '回款额（按订单负责人）',
                dataIndex: 'received_amount',
                className: 'cell-money',
                render: (v: number, r: SalesUserStat) => formatWholeCny(r.received_amount_exact ?? v),
              },
            ]}
            // 没有任何客户/跟进/商机/报价/金额的人（周文斌等 4 个空账号）不上表：
            // 一行行 0 会把"这列是不是坏了"读出来。
            dataSource={(salesQuery.data ?? []).filter(
              (r) =>
                (r.customer_count ?? 0) +
                  (r.followup_count ?? 0) +
                  (r.opportunity_count ?? 0) +
                  (r.quote_count ?? 0) +
                  (r.order_amount ?? 0) +
                  (r.received_amount ?? 0) >
                0,
            ).filter((row) => matches(row.name))}
            loading={salesQuery.isLoading}
            rowKey="user_id"
            pagination={false}
            empty={keyword ? noMatches : '暂无数据'}
          />
        </ReportSection>}

        {/* 场景17：三个销售额口径必须分开看，否则"回款没到但签了单"会被当成已完成 */}
        {activeReport === 'basis' && <ReportSection id="detail-basis" title="每月签单、发货与回款" query={basesQuery}>
          {(() => {
            const b = basesQuery.data
            if (!b) return <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>暂无数据</div>
            const pick = (rows: { month: string; value: number }[], m: string) =>
              rows.find((r) => r.month === m)?.value ?? 0
            const exact = (rows: { month: string; value: number; value_exact?: string }[], m: string) => {
              const row = rows.find((item) => item.month === m)
              return row?.value_exact ?? row?.value ?? 0
            }
            const months = (b.signed ?? [])
              .map((r) => r.month)
              .filter(
                (m) =>
                  pick(b.signed, m) ||
                  pick(b.shipped, m) ||
                  pick(b.received, m) ||
                  pick(b.repeat_net, m) ||
                  pick(b.new_by_created, m) ||
                  pick(b.new_by_first_deal, m),
              )
            return (
              <>
                <Table
                  size="small"
                  pagination={false}
                  rowKey="month"
                  dataSource={months.filter((month) => !reportMonth || month === reportMonth).map((m) => ({ month: m }))}
                  empty={reportMonth ? '所选月份没有数据' : '该年份还没有数据'}
                  columns={[
                    { title: '月份', dataIndex: 'month', width: 80 },
                    {
                      title: '签单',
                      width: 130,
                      className: 'cell-money',
                      render: (_: unknown, r: { month: string }) =>
                        formatWholeCny(exact(b.signed, r.month)),
                    },
                    {
                      title: '发货',
                      width: 130,
                      className: 'cell-money',
                      render: (_: unknown, r: { month: string }) =>
                        formatWholeCny(exact(b.shipped, r.month)),
                    },
                    {
                      title: '回款',
                      width: 130,
                      className: 'cell-money',
                      render: (_: unknown, r: { month: string }) =>
                        formatWholeCny(exact(b.received, r.month)),
                    },
                    {
                      title: '老客净额',
                      width: 130,
                      className: 'cell-money',
                      render: (_: unknown, r: { month: string }) =>
                        formatWholeCny(exact(b.repeat_net, r.month)),
                    },
                    {
                      title: '新客(建档)',
                      width: 100,
                      render: (_: unknown, r: { month: string }) => pick(b.new_by_created, r.month),
                    },
                    {
                      title: '新客(首单)',
                      width: 100,
                      render: (_: unknown, r: { month: string }) =>
                        pick(b.new_by_first_deal, r.month),
                    },
                  ]}
                />
                <dl className="analytics-basis-notes" aria-label="报表统计说明">
                  <div><dt>签单</dt><dd>按订单创建月计，取消的订单不计入。</dd></div>
                  <div><dt>发货</dt><dd>按每批实际发货的月份和金额计。</dd></div>
                  <div><dt>回款</dt><dd>按财务确认的月份计。</dd></div>
                  <div><dt>老客净额</dt><dd>年初已有有效订单的客户，在本期产生的订单净额。</dd></div>
                  <div><dt>新客建档</dt><dd>按客户档案创建月计。</dd></div>
                  <div><dt>新客首单</dt><dd>按第一笔有效订单的月份计。</dd></div>
                </dl>
              </>
            )
          })()}
        </ReportSection>}

        {activeReport === 'target' && <ReportSection id="detail-target" title="月度销售目标">
          <p className="workflow-guide">
            看每月实际完成多少、目标是多少。回款按财务确认计，新客只数下了首单的客户。
          </p>
          <div className="analytics-target-toolbar">
            {canSetTarget && <Button type="primary" theme="outline" onClick={() => openTargetModal()}>设置月度目标</Button>}
            <Button aria-pressed={showTargetDetails} onClick={() => setShowTargetDetails(!showTargetDetails)}>
              {showTargetDetails ? '返回目标概览' : '查看完整报表'}
            </Button>
            {showTargetDetails && canSetTarget && <Button onClick={() => openFreezeModal('freeze')}>保存月度实绩快照</Button>}
            {can('task:view') && <Link to="/tasks">销售任务</Link>}
          </div>
          {readIssue(targetsQuery)}
          {!showTargetDetails && targetsQuery.isSuccess && (
            targetRows.length === 0 ? <p className="workflow-guide">{targetEmpty}</p>
              : <div className="analytics-target-list">
                {targetRows.map((row) => (
                  <article key={targetRowKey(row)} className="analytics-target-row">
                    <div className="analytics-target-person">
                      <strong>{row.user_name}</strong>
                      <span>{Number(row.period.slice(5))} 月{row.actual_frozen ? ' · 已存档' : row.period_frozen ? ' · 部分存档' : ''}</span>
                    </div>
                    <TargetProgress label="确认回款" actual={formatWholeCny(row.received_actual_exact ?? row.assess_actual ?? row.received_actual)}
                      target={formatWholeCny(row.sales_target_exact ?? row.sales_target)} achievement={row.sales_achievement} live={row.live_actual_metrics?.includes('received')} />
                    <TargetProgress label="新客户首单" actual={`${row.new_customer_actual} 家`}
                      target={`${row.new_customer_target} 家`} achievement={row.new_customer_achievement} live={row.live_actual_metrics?.includes('new_customer')} />
                    <TargetProgress label="老客户订单净额" actual={formatWholeCny(row.repeat_customer_actual_exact ?? row.repeat_customer_actual)}
                      target={formatWholeCny(row.repeat_customer_target_exact ?? row.repeat_customer_target)} achievement={row.repeat_customer_achievement} live={row.live_actual_metrics?.includes('repeat_net')} />
                    <div className="analytics-target-row-actions">
                      <Button size="small" theme="borderless" onClick={() => openDrilldown(row, 'received')}>查看明细</Button>
                      {canSetTarget && <Button size="small" theme="borderless" onClick={() => openTargetModal(row)}>
                        {row.target_id ? '修改目标' : '设置本月目标'}
                      </Button>}
                    </div>
                  </article>
                ))}
              </div>
          )}
          {showTargetDetails && targetsQuery.isSuccess && <>
          {/* 包一层横向滚动容器：Semi 自带的 scroll 只让表体可滚，
              表头按 2060px 撑出去的部分仍会把整页顶出 5px——1440 实拍见过
              页面级横向滚动条。让溢出发生在容器里，页面本身不横滚。 */}
          <div className="table-x-scroll">
          <Table<SalesTargetRow>
            scroll={{ x: 'max(100%, 2060px)' }}
            columns={[
              {
                title: '月份',
                dataIndex: 'period',
                width: 130,
                render: (v: string, r: SalesTargetRow) => (
                  <span>
                    {v.slice(5)} 月
                    {(r.period_frozen ?? r.actual_frozen) && (
                      <small style={{ display: 'block', color: 'var(--crm-text-3)', fontSize: 11 }}>
                        {r.actual_frozen ? '已存档' : '部分存档'}
                      </small>
                    )}
                  </span>
                ),
              },
              { title: '对象', dataIndex: 'user_name', width: 120 },
              { title: '新客目标', dataIndex: 'new_customer_target', width: 100 },
              { title: '新客实际', dataIndex: 'new_customer_actual', width: 100,
                render: (v: number, r: SalesTargetRow) => <>
                  {v}<LiveActualNote show={r.live_actual_metrics?.includes('new_customer')} />
                </> },
              {
                // R09：新建客户档案数（过程指标）。
                // 放在「新客实际」旁边而不是替换它——两个是不同口径：
                // 新客实际按**首次有效成交**算，这一列按**建档时间**算。
                // 并排才看得出「档案开了一堆、成交没跟上」这种过程问题。
                // 明确标注"不进差额与达成率"，否则会被误当成考核口径。
                title: '新建档数',
                dataIndex: 'new_customer_created_actual',
                width: 110,
                render: (v: number | undefined) => (
                  <span title="本月新建客户档案数（过程指标，只展示，不进差额与达成率）">
                    {v ?? 0}
                  </span>
                ),
              },
              {
                // 差额与达成率成对看：只看达成率的话，"差了 3 家"和"差了 30 家"
                // 可能都是同一个百分比（基数不同），差额才是能直接派活的数字
                title: '新客差额',
                dataIndex: 'new_customer_variance',
                width: 100,
                render: (v: number) => (
                  <span style={{ color: v >= 0 ? 'var(--crm-primary)' : 'var(--crm-error)' }}>
                    {v >= 0 ? '+' : ''}
                    {v}
                  </span>
                ),
              },
              {
                title: '达成率',
                width: 90,
                // 零基期不给百分比（文档场景17）：没设目标时后端返回 null
                render: (_: unknown, r: SalesTargetRow) =>
                  r.new_customer_achievement == null
                    ? <span style={{ color: 'var(--crm-text-3)' }}>-</span>
                    : rate(r.new_customer_actual, r.new_customer_target),
              },
              {
                title: '销售目标',
                dataIndex: 'sales_target',
                width: 130,
                className: 'cell-money',
                render: (v: number, r: SalesTargetRow) => formatWholeCny(r.sales_target_exact ?? v),
              },
              {
                // **考核口径**：确认回款。差额与达成率都是按它算的（§4.3）。
                // 这一列以前显示的是签单额、而差额用的是回款——同一行里摆了两个口径，
                // 用户看到的"实际"和"差额"自然对不上（返工单第 3 条同一类问题）。
                title: '实际·确认回款',
                dataIndex: 'assess_actual',
                width: 160,
                className: 'cell-money',
                render: (v: number | undefined, r: SalesTargetRow) => (
                  <span>
                    {formatWholeCny(r.received_actual_exact ?? v)}
                    <button type="button" className="crm-text-action" style={{ marginLeft: 6, fontSize: 12 }} onClick={() => openDrilldown(r, 'received')}>
                      明细
                    </button>
                    <LiveActualNote show={r.live_actual_metrics?.includes('received')} />
                  </span>
                ),
              },
              {
                // 展示口径：签单额。照常显示，但**不进差额**（§4.3）
                title: '签单额',
                dataIndex: 'sales_actual',
                width: 120,
                className: 'cell-money',
                render: (v: number | undefined, r: SalesTargetRow) => <>
                  {formatWholeCny(r.sales_actual_exact ?? v)}
                  <LiveActualNote show={r.live_actual_metrics?.includes('sales')} />
                </>,
              },
              {
                // 展示口径：发货额（按实际发货批次分摊，§4.1.4）
                title: '发货额',
                dataIndex: 'shipped_actual',
                width: 120,
                className: 'cell-money',
                render: (v: number | undefined, r: SalesTargetRow) => <>
                  {formatWholeCny(r.shipped_actual_exact ?? v)}
                  <LiveActualNote show={r.live_actual_metrics?.includes('shipped')} />
                </>,
              },
              {
                title: '达成率',
                width: 90,
                // **零基期不给百分比**（文档场景17 要求）：没设目标时后端返回 null，
                // 这里显示"—"并给出说明，而不是拿 0 当分母算出一个假增长率。
                // 分子必须用**考核口径**（确认回款），否则这个百分比跟同一行的差额
                // 不是同一个算法，两个数都"有道理"却互相打脸。
                render: (_: unknown, r: SalesTargetRow) =>
                  r.sales_achievement == null
                    ? <span style={{ color: 'var(--crm-text-3)' }} title={r.achievement_note ?? ''}>-</span>
                    : rate(r.assess_actual ?? r.sales_actual, r.sales_target),
              },
              {
                // 复购（老客净额）：口径是"期初固定的老客池在本期的订单净额"，
                // 定义在后端 target_bases.py。以前目标能存、页面看不到，等于设了没人管。
                title: '复购目标',
                dataIndex: 'repeat_customer_target',
                width: 130,
                className: 'cell-money',
                render: (v: number, r: SalesTargetRow) => (v ? formatWholeCny(r.repeat_customer_target_exact ?? v) : '-'),
              },
              {
                title: '复购实际',
                dataIndex: 'repeat_customer_actual',
                width: 130,
                className: 'cell-money',
                render: (v: number | undefined, r: SalesTargetRow) => <>
                  {formatWholeCny(r.repeat_customer_actual_exact ?? v)}
                  <LiveActualNote show={r.live_actual_metrics?.includes('repeat_net')} />
                </>,
              },
              {
                title: '复购差额',
                dataIndex: 'repeat_customer_variance',
                width: 130,
                className: 'cell-money',
                render: (v: number, r: SalesTargetRow) =>
                  r.repeat_customer_target ? (
                    <span style={{ color: v >= 0 ? 'var(--crm-primary)' : 'var(--crm-error)' }}>
                      {v >= 0 ? '+' : ''}
                      {formatWholeCny(r.repeat_customer_variance_exact ?? v)}
                    </span>
                  ) : (
                    <span style={{ color: 'var(--crm-text-3)' }}>-</span>
                  ),
              },
              {
                title: '差额',
                dataIndex: 'sales_variance',
                width: 130,
                className: 'cell-money',
                render: (v: number, r: SalesTargetRow) => (
                  <span style={{ color: v >= 0 ? 'var(--crm-primary)' : 'var(--crm-error)' }}>
                    {v >= 0 ? '+' : ''}{formatWholeCny(r.sales_variance_exact ?? v).replace('¥-', '-¥')}
                  </span>
                ),
              },
              ...(canSetTarget
                ? [
                    {
                      title: '操作',
                      width: 195,
                      render: (_: unknown, r: SalesTargetRow) => (
                        <>
                          <Button size="small" theme="borderless" onClick={() => openTargetModal(r)}>
                            {r.target_id ? '修改目标' : '设置本月目标'}
                          </Button>
                          {(r.period_frozen ?? r.actual_frozen) && (
                            // 只有已结账的期间才谈得上"重算"：没结过账本来就在实时算
                            <Button size="small" theme="borderless"
                              onClick={() => openFreezeModal('refreeze', r.period)}
                            >
                              重算月度快照
                            </Button>
                          )}
                        </>
                      ),
                    },
                  ]
                : []),
            ]}
            dataSource={targetRows}
            loading={targetsQuery.isLoading}
            rowKey={(row?: SalesTargetRow) => row ? targetRowKey(row) : ''}
            pagination={false}
            empty={targetEmpty}
          />
          </div>
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--crm-text-3)' }}>
            {targetsQuery.data?.attribution_note ??
              '归属口径（业绩口径）：按订单签单归属——交接后钱仍算签单人。汇总、差额、明细同一政策。'}
          </div>
          <div style={{ marginTop: 6, fontSize: 12, color: 'var(--crm-text-3)' }}>
            考核口径 = <b>确认回款</b>（按财务确认时间归月，不是客户打款那天）；
            签单额与发货额只展示、不进差额。
            {targetsQuery.data?.missing_confirmed_at_note && (
              <span style={{ color: 'var(--crm-caution)', marginLeft: 6 }}>
                {targetsQuery.data.missing_confirmed_at_note}
              </span>
            )}
          </div>
          </>}
        </ReportSection>}

      </section>}

      <Modal
        width={520}
        title={freezeModal.mode === 'freeze' ? '保存月度实绩快照' : '重算月度实绩快照'}
        visible={freezeModal.visible}
        onCancel={() => setFreezeModal({ visible: false, mode: 'freeze' })}
        onOk={() => {
          // 前端先把话说清楚，别把后端的字段名（`参数校验失败：reason`）甩给用户。
          // 后端仍会再校验一次——纵深防御，前端拦不住也有兜底。
          if (freezeModal.mode === 'refreeze' && !freezeNote.trim()) {
            Toast.error('请填写重算原因：改了历史数字，事后得能查出是谁、为什么改的')
            return
          }
          freezeMutation.mutate()
        }}
        confirmLoading={freezeMutation.isPending}
        okText={freezeModal.mode === 'freeze' ? '保存快照' : '确认重算'}
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>期间</div>
            <Select
              value={freezePeriod}
              onChange={(value) => setFreezePeriod(value as string)}
              optionList={pastPeriods.map((p) => ({ value: p, label: p }))}
              // 重算只能针对已经结过账的那一期，期间不再让改
              disabled={freezeModal.mode === 'refreeze'}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>
              {freezeModal.mode === 'freeze' ? '备注（可不填）' : '重算原因（必填）'}
            </div>
            <Input
              value={freezeNote}
              onChange={setFreezeNote}
              placeholder={
                freezeModal.mode === 'freeze'
                  ? '例如：9 月账已关'
                  : '例如：发现有批 9 月订单的状态当初录错了'
              }
            />
          </div>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            {freezeModal.mode === 'freeze'
              ? '把所选月份的实际完成数和明细保存下来。以后改单或退货，历史快照仍保持保存时的数字。只能保存已经结束的月份。'
              : '按现在的数据重新计算，并替换所选月份的原快照。系统会记录操作人、时间和原因。'}
          </div>
        </div>
      </Modal>

      <Modal
        title={`${drilldown.period ?? ''} · ${drilldown.user_name ?? ''} 的实际构成`}
        visible={drilldown.visible}
        onCancel={() => setDrilldown({ visible: false, metric: 'received' })}
        footer={null}
        width={900}
      >
        <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 12 }}>
          <Select
            value={drilldown.metric}
            onChange={(value) => setDrilldown({ ...drilldown, metric: value as string })}
            optionList={[
              { value: 'received', label: '确认回款（考核口径）' },
              { value: 'signed', label: '签单额' },
              { value: 'shipped', label: '发货额' },
              { value: 'new_customer', label: '新客户' },
              { value: 'repeat_net', label: '老客净额（复购）' },
            ]}
            style={{ width: 220 }}
          />
          {!readIssue(drilldownQuery) && <span style={{ fontSize: 13 }}>
            合计 ¥{Math.round(drilldownQuery.data?.total ?? 0).toLocaleString('zh-CN')} ·{' '}
            {drilldownQuery.data?.count ?? 0} 条
          </span>}
          {!readIssue(drilldownQuery) && <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            {drilldownQuery.data?.source === 'snapshot'
              ? '读的是结账存档（不会因为后来的退货变小）'
              : drilldownQuery.data?.archive_missing_metric ? '本项未存档，按当前数据计算' : '实时统计'}
          </span>}
        </div>
        {readIssue(drilldownQuery) ?? <Table
          columns={[
            {
              title: '单据',
              dataIndex: 'label',
              render: (v: string | null | undefined, r) => v ?? `编号 ${r?.id ?? ''}`,
            },
            {
              title: '类型',
              dataIndex: 'record_type',
              width: 110,
              render: (v: string) => DRILLDOWN_TYPE_LABEL[v] ?? v,
            },
            {
              title: '金额',
              dataIndex: 'amount',
              width: 130,
              className: 'cell-right',
              // 下钻出来的是**具体单据**的金额，不是概览统计，按单据口径两位小数。
              render: (v?: number) => formatMoney(toTwoDecimals(v ?? 0), 'CNY'),
            },
            {
              title: '单据日期',
              dataIndex: 'date',
              width: 150,
              // 新客行的日期取自**冻结快照**（R07）。旧版快照只冻了归属月份、
              // 没冻日期，这里就是空的 —— 如实说「未记录」，不拿现在的订单
              // 重算一个顶上（那正是自相矛盾的来源：归属一月、日期跳三月）。
              render: (v?: string | null, r?: { record_type?: string }) =>
                v ?? (r?.record_type === 'customer' ? '未记录' : '-'),
            },
          ]}
          dataSource={drilldownQuery.data?.items ?? []}
          loading={drilldownQuery.isLoading}
          rowKey={(r?: { record_type: string; id: number }) => `${r?.record_type}-${r?.id}`}
          pagination={false}
          scroll={{ y: 360 }}
          empty="这一期该指标没有明细"
        />}
        {!drilldownQuery.isError && drilldownQuery.data?.truncated && (
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--crm-caution)' }}>
            明细超过 200 条，只显示前 200 条；合计数是全部。
          </div>
        )}
      </Modal>

      <Modal
        width={520}
        title={targetModal.expected_updated_at ? '修改月度目标' : '设置月度目标'}
        visible={targetModal.visible}
        onCancel={() => setTargetModal({ visible: false })}
        onOk={() => targetSaveMutation.mutate()}
        confirmLoading={targetSaveMutation.isPending}
        okText="保存目标"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <p className="workflow-guide">选月份和统计对象，再填要考核的数字。保存的是目标值，表里的实际完成数由订单和确认回款统计。</p>
          <div>
            <div style={{ marginBottom: 4 }}>全公司或个人</div>
            {targetModal.visible && readIssue(usersQuery)}
            <Select
              value={targetForm.user_id ?? 0}
              loading={usersQuery.isFetching}
              onChange={(value) =>
                // 选人就清掉部门：两者互斥（后端按"人 + 部门"两个条件找同一行，
                // 两个都带谁都匹配不上）
                setTargetForm({
                  ...targetForm,
                  user_id: value === 0 ? null : (value as number),
                  department_id: undefined,
                })
              }
              optionList={[
                { value: 0, label: '全公司' },
                ...(usersQuery.data?.items ?? []).map((u) => ({ value: u.id, label: u.name })),
              ]}
              filter={optionMatcher}
              style={{ width: '100%' }}
              placeholder="选择全公司或某位业务员"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>部门（设部门目标时选）</div>
            {targetModal.visible && readIssue(departmentsQuery)}
            <Select
              value={targetForm.department_id ?? 0}
              loading={departmentsQuery.isFetching}
              onChange={(value) =>
                setTargetForm({
                  ...targetForm,
                  department_id: value === 0 ? undefined : (value as number),
                  user_id: undefined,
                })
              }
              optionList={[
                { value: 0, label: '未指定部门' },
                ...(departmentsQuery.data ?? []).map((d) => ({ value: d.id, label: d.name })),
              ]}
              filter={optionMatcher}
              style={{ width: '100%' }}
              placeholder="选部门即设为团队目标"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>月份</div>
            <Select
              value={targetForm.period}
              onChange={(value) => setTargetForm({ ...targetForm, period: value as string })}
              optionList={Array.from({ length: 12 }, (_, i) => {
                const period = `${targetYear}-${String(i + 1).padStart(2, '0')}`
                return { value: period, label: `${targetYear} 年 ${i + 1} 月` }
              })}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>新客户首单目标（家）</div>
            <Input
              aria-label="新客户首单目标"
              value={targetForm.new_customer_target}
              onChange={(value) => setTargetForm({ ...targetForm, new_customer_target: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>财务确认回款目标（元）</div>
            <Input
              aria-label="财务确认回款目标"
              value={targetForm.sales_target}
              onChange={(value) => setTargetForm({ ...targetForm, sales_target: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>老客户订单净额目标（元）</div>
            <Input
              aria-label="老客户订单净额目标"
              value={targetForm.repeat_customer_target}
              onChange={(value) => setTargetForm({ ...targetForm, repeat_customer_target: value })}
            />
          </div>
        </div>
      </Modal>
    </div>
  )
}
