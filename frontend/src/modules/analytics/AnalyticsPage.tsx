import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Input, Modal, Select, Table, Toast } from '@douyinfe/semi-ui'

import { parseOptionalDecimal, parseOptionalNumber } from '../../shared/number-input'
import { formatMoney, formatWholeCny, toTwoDecimals } from '../../shared/components/money'
import PageHeader from '../../shared/components/PageHeader'
import KpiStrip from '../../shared/components/KpiStrip'
import SectionCard from '../../shared/components/SectionCard'
import GroupHead from '../../shared/components/SectionGroupHead'
import HelpTip from '../../shared/components/HelpTip'
import EChart from '../../shared/components/charts/EChart'
import DonutLegend from '../../shared/components/charts/DonutLegend'
import {
  asMoney,
  asPercent,
  asPercentValue,
  columnChartOption,
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

/**
 * 卡片：图表/表格都塞在这里，**强制撑满行高**（`height: '100%'`）——
 * 不写这一句，网格里矮的那张卡会缩成自己内容的高度，一行就高矮不齐了。
 */
function Card({
  title,
  note,
  children,
  extra,
}: {
  title: string
  note?: string
  children: ReactNode
  extra?: ReactNode
}) {
  return (
    <SectionCard
      /* note 原来是括号拼在标题后面的：四列布局卡宽只有 210，标题缝上括号注释
         直接被截成「进行中商机的阶段分布（顺序即…」，连标题本身都读不全。
         改成标题一行、note 一行 11px 小灰字，标题始终完整。 */
      title={(
        <span style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
          <span className="card-title" title={title} style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {title}
          </span>
          {note && <span className="an-card-note">{note}</span>}
        </span>
      )}
      extra={extra}
      style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
    >
      {children}
    </SectionCard>
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

/** 纵向柱：**分档 / 分阶段**的量。分类名横着排不开时可以斜 30 度。 */
function Columns({
  data,
  unit,
  height = 220,
  rotateLabels = false,
}: {
  data: NameValue[]
  unit: string
  height?: number
  rotateLabels?: boolean
}) {
  if (!data.length) return <NoData />
  return (
    <EChart
      height={height}
      ariaLabel={`柱状图：${data.map((item) => `${item.name} ${item.value}`).join('，')}`}
      option={columnChartOption({
        categories: data.map((item) => item.name),
        series: [{ name: unit, values: data.map((item) => item.value) }],
        format: asCount(unit),
        rotateLabels,
      })}
    />
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

export default function AnalyticsPage() {
  const { can, dataScopeLabel } = usePermissions()
  // 钩子里的「全部」是给权限配置用的词；报表上写「全公司」才是业务看得懂的话。
  const scopeLabel = dataScopeLabel === '全部' ? '全公司' : dataScopeLabel
  const canSetTarget = can('settings:manage')
  const queryClient = useQueryClient()
  const [activeReport, setActiveReport] = useState<(typeof ANALYTICS_REPORTS)[number]['id']>('basis')
  const [reportKeyword, setReportKeyword] = useState('')
  const [reportMonth, setReportMonth] = useState<string>()
  const selectReport = (id: typeof activeReport) => {
    if (activeReport !== id) {
      setReportKeyword('')
      setReportMonth(undefined)
      setActiveReport(id)
    }
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
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
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
        period: `${targetYear}-${String(new Date().getMonth() + 1).padStart(2, '0')}`,
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

  return (
    <div className="page-container analytics-page">
      <PageHeader
        title="数据分析"
        subtitle="查看成交、回款走势和发货风险。"
        extra={
          <div className="analytics-header-links">
            <a className="analytics-report-link" href="#analytics-reports" onClick={() => selectReport('target')}>销售目标</a>
            <a className="analytics-report-link" href="#analytics-reports">查看明细报表 ↓</a>
          </div>
        }
      />

      {currencyWarnings.map((note) => (
        <div key={note} style={{ marginBottom: 14 }}>
          <Banner type="warning" closeIcon={null} description={note} title="汇总里含外币金额" />
        </div>
      ))}

      <GroupHead
        index="01"
        title="核心指标"
        note="先看成交、未到账金额和发货风险。"
      />
      {/* 13 个指标按业务口径分三组。顺序按组重排，数值和口径一个没改。 */}
      <div className="kpi-groups">
        <div className="kpi-group">
          <div className="kpi-group-head">成交与转化</div>
          <KpiStrip
            tintValues={false}
            /* 7 张卡在 4 列网格里排成 4 + 3，第二行右边空一格。
               曾经补过 kpi-fill-last 让末张跨两列去补洞 —— 主人 2026-11-19 说
               「不要让那一个卡片适应窗口呀，三个一起更好看吧」：一张 770 宽的卡
               夹在三张 390 宽的卡中间，量级和字号都被拉变形了。
               现在不补洞：第二行三张等宽，右边就空一格背景色。 */
            className="kpi-grid kpi-grid-fixed"
            items={[
              {
                // 7 张卡排 4 + 3，右下永远空一格（主人 2026-11-19：
                // 「加一个…空一个丑啊」）。补一张"总量"当分母放在最前：
                // 后面五个都是率，率没有基数就说不清 —— 79 单里成交 11 单，
                // 比单看 52% 更像一句人话。凑满 8 张，两行正好铺齐。
                label: '累计商机',
                tone: 'default',
                value: `${createdTotalQuery.data?.total ?? 0} 单`,
                hint: '含成交与失单的全部商机',
              },
              {
                label: '商机成交率',
                tone: 'primary',
                value: opportunity && opportunity.won_count + opportunity.loss_count > 0
                  ? `${(opportunity.win_rate * 100).toFixed(0)}%` : '—',
                hint: opportunity ? `成交 ${opportunity.won_count} 个 · 失单 ${opportunity.loss_count} 个` : undefined,
                help: '已成交商机 ÷（已成交 + 已失单）。进行中的商机不参与。',
              },
              {
                label: '报价接受率',
                tone: 'success',
                value: quote && quote.sent_count !== 0 ? `${(quote.accept_rate * 100).toFixed(0)}%` : '—',
                hint: quote?.sent_count == null ? undefined : `已接受 ${quote.accepted_count} / 已发出 ${quote.sent_count} 张`,
                help: '客户已接受的报价 ÷ 已发出的报价。已发出包括等待答复、已接受和已拒绝。',
              },
              {
                // 让价为负 = 整体报得比建议价还高，写成「-0.20%」像算错了 —— 按符号切「让价 / 加价」。
                label: (quote?.average_discount ?? 0) >= 0 ? '平均让价幅度' : '平均加价幅度',
                tone: 'warning',
                value: `${Math.abs((quote?.average_discount ?? 0) * 100).toFixed(2)}%`,
                help: '让价 =（建议价 − 实际报价）÷ 建议价，按报价明细逐行平均。它反映实际让了多少，不是折扣权限。',
                hint: `平均版本数 ${quote?.average_versions ?? 0}`,
              },
              {
                label: '低价审批比例',
                tone: 'warning',
                // 同页下方「低价审批率」是按报价明细行算的（3/44），这张卡原来用报价版本口径
                // （12/36），两个数打架。统一按明细行 —— help 文案本来就写的"报价明细占比"。
                value: `${((pricing?.low_price_approval_rate ?? 0) * 100).toFixed(0)}%`,
                help: '需要走审批的报价明细占比。拟报价低于授权底价或公司保护价的行，会被标成需要审批。',
                hint: `需审批 ${pricing?.approval_required_count ?? 0} 行 / 明细 ${pricing?.item_count ?? 0} 行`,
              },

              {
                label: '线索转化率',
                tone: 'primary',
                value: lead?.total ? `${(lead.conversion_rate * 100).toFixed(0)}%` : '—',
                hint: lead ? `已转客户 ${lead.converted_count} / 线索 ${lead.total} 条` : undefined,
                help: '已转成客户的线索 ÷ 统计范围内的全部线索，含线索池中未分配的线索。',
              },
              {
                label: '近期活跃 / 长期未跟进',
                tone: 'success',
                value: `${customer?.active_count ?? 0} / ${customer?.dormant_count ?? 0}`,
                help: `以最近一次跟进或业务进展为准，两者都没有时看建档日。最近 ${customer?.active_days ?? 30} 天算活跃；其余客户超过 ${customer?.dormant_days ?? 30} 天算长期未跟进。`,
                hint: `复购客户 ${customer?.repeat_customer_count ?? 0} 家`,
              },
              {
                label: '平均成交周期',
                tone: 'default',
                value: cycle?.average_days != null ? `${cycle.average_days} 天` : '-',
                hint: cycle?.won_with_history
                  ? `基于 ${cycle.won_with_history} 个有阶段记录的成交商机`
                  : '暂无成交商机',
              },
            ]}
          />
        </div>
        <div className="kpi-group">
          <div className="kpi-group-head">回款与应收</div>
          <KpiStrip
            tintValues={false}
            className="kpi-grid kpi-cols-4"
            items={[
              {
                label: '应收合计',
                tone: 'primary',
                value: money(receivable?.plan_amount),
                hint: `实际到账 ${money(receivable?.received_amount)} · 已分配 ${money(receivable?.allocated_amount)}`,
                help: '有效应收计划上的金额合计，按订单当前负责人统计。',
              },
              {
                label: '尚未到账',
                tone: 'warning',
                value: money(receivable?.unreceived_amount),
                hint: `逾期 ${receivable?.overdue_count ?? 0} 期`,
                help: '应收合计减去财务已确认的到账金额，包含尚未到期的款。',
              },
              {
                label: '已到账待分配',
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
              {
                label: '逾期应收',
                tone: 'error',
                value: money(payment?.overdue_amount),
                help: '已经过了应收日期、仍然没有结清的金额，按每期应收统计。',
                hint: `逾期 ${payment?.overdue_node_count ?? 0} 期`,
              },
            ]}
          />
        </div>
        <div className="kpi-group">
          <div className="kpi-group-head">履约与风险</div>
          {/* 2026-11-21：原先两张半屏宽的大卡，每张只有 3 行字，一屏里
              数它两块最空。改成和上面「回款与应收」同宽的四格 ——
              准时率、已交付、延迟、交期风险各占一格，行高与上一组对齐。 */}
          <KpiStrip
            tintValues={false}
            className="kpi-grid kpi-cols-4"
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
        </div>
      </div>

      {/* ── ① 趋势：随时间变的东西，只有折线说得清 ─────────────────────── */}
      <GroupHead index="02" title="趋势" note="按月查看签单、发货、回款和发货准时情况。" />
      <Row columns={2}>
        <Card title="月度业绩走势" note="签单看建单月，发货看实际发货月，回款看财务确认月。">
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
            下方「每月签单、发货与回款」可查看各月具体金额。
          </div>
        </Card>

        <Card title="交期趋势" note="柱子是发货单数，折线是首批准时发货率。">
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
        </Card>
      </Row>

      {/* ── ② 占比：看"各占几成"，环形比横条直观 ───────────────────────── */}
      <GroupHead index="03" title="客源、客户等级与回款方式" />
      {/* 2×2 而不是一行四张（2026-11-19）：四列时卡内只有 232 宽，
          环图吃掉 107 后图例只剩 115，名字全被省略号截成「官...」「电...」；
          而"回款方式分布"只有两项，卡被行高拉到 380、下面空两百多像素。
          两列后卡宽 570，环图与图例左右并排各有余量，四张卡也紧凑成两行。 */}
      <Row columns={2}>
        <Card title="客户来源分布">
          <Donut data={customer?.by_source ?? []} format={asCount('家')} centerLabel="家客户" />
        </Card>
        <Card title="客户等级分布">
          <Donut data={customer?.by_level ?? []} format={asCount('家')} centerLabel="家客户" />
        </Card>
        <Card title="线索来源分布">
          <Donut data={lead?.by_source ?? []} format={asCount('条')} centerLabel="条线索" />
        </Card>
        <Card title="回款方式分布" note="按金额">
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
        </Card>
      </Row>

      {/* ── ③ 分档：每一档各多少，且顺序有意义 ───────────────────────────── */}
      <GroupHead index="04" title="线索与应收状态" />
      <Row columns={3}>
        <Card title="线索状态分布">
          <Columns data={lead?.by_status ?? []} unit="条" />
        </Card>
        <Card title="各期应收状态">
          <Columns data={receivable?.by_status ?? []} unit="期" />
        </Card>
        <Card title="应收逾期了多久" note="按未结清的应收期数统计">
          <Columns data={payment?.aging ?? []} unit="个" rotateLabels />
        </Card>
      </Row>

      <GroupHead index="05" title="商机进展" />
      <Card title="商机与关联订单" note="前两项数商机，最后一项数订单。一条商机可能关联多张订单。">
        <KpiStrip
          tintValues={false}
          className="kpi-grid kpi-cols-3"
          items={[
            { label: '累计商机', value: createdTotalQuery.data ? `${createdTotalQuery.data.total} 个` : '—' },
            { label: '已成交商机', value: opportunity ? `${opportunity.won_count} 个` : '—' },
            {
              label: '关联商机的订单',
              value: orderCountWithOpportunity === null ? '—' : `${orderCountWithOpportunity} 张`,
              hint: orders && orders.total > orders.items.length ? '订单列表未取全，无法统计全部关联订单' : undefined,
            },
          ]}
        />
        <div className="deal-steps-toggle">
          <Button className="deal-toggle-btn" theme="borderless" size="small" aria-expanded={showStageDetail}
            onClick={() => setShowStageDetail((open) => !open)}>
            {showStageDetail ? '收起各阶段明细' : '查看各阶段有多少商机到达过'}
          </Button>
        </div>
        {showStageDetail && (
          <>
            <p className="deal-steps-note">同一商机可以出现在多个阶段，也可以跳过阶段。每行单独计数。</p>
            <Table
              className="deal-detail-table"
              size="small"
              pagination={false}
              rowKey="stage_id"
              dataSource={opportunity?.stage_conversion ?? []}
              columns={[
                { title: '商机阶段', dataIndex: 'stage_name' },
                { title: '到达过的商机数', dataIndex: 'reached_count', className: 'cell-right' },
              ]}
            />
          </>
        )}
      </Card>

      <GroupHead index="06" title="商机、报价与交期明细" />
      <Row columns={4}>
        <Card title="进行中商机的阶段分布" note="顺序即阶段先后">
          {(opportunity?.funnel ?? []).length === 0 ? (
            <NoData />
          ) : (
            <EChart
              // 九个阶段、名字四五个字：竖着放底下一排标签会互相挤掉，
              // 横着写才能把每一级都读全（顺序也照阶段先后，不按数量排）
              height={rankHeight((opportunity?.funnel ?? []).length)}
              ariaLabel="进行中商机在各阶段的分布条形图"
              option={rankBarChartOption({
                data: (opportunity?.funnel ?? []).map((row) => ({
                  name: row.stage_name,
                  value: row.count,
                })),
                format: asCount('个'),
              })}
            />
          )}
        </Card>

        <Card title="失单原因分布">
          {(lossQuery.data ?? []).length === 0 ? (
            <NoData />
          ) : (
            <EChart
              height={rankHeight(lossQuery.data?.length ?? 0)}
              ariaLabel="失单原因排名条形图"
              option={rankBarChartOption({
                data: lossQuery.data ?? [],
                format: asCount('单'),
              })}
            />
          )}
        </Card>

        <Card title="平均报价" note="按客户等级">
          {(pricing?.average_quoted_price_by_level ?? []).length === 0 ? (
            <NoData />
          ) : (
            <EChart
              height={rankHeight((pricing?.average_quoted_price_by_level ?? []).length)}
              ariaLabel="按客户等级的平均报价条形图"
              option={rankBarChartOption({
                data: (pricing?.average_quoted_price_by_level ?? []).map((row) => ({
                  /* 「未分级」这个等级名本身已带「级」，模板再补一个就成「未分级 级」
                     （2026-11-20 实拍）。有「级」就不补。 */
                  name: `${row.level}${row.level.endsWith('级') ? '' : ' 级'}（${row.item_count} 条）`,
                  value: row.average_price,
                })),
                format: asMoney,
                axisFormat: compactMoney,
              })}
            />
          )}
          <div style={{ marginTop: 14, fontSize: 13, color: 'var(--crm-text-2)', lineHeight: 2 }}>
            <div>低价审批率：{asPercent(pricing?.low_price_approval_rate ?? 0)}</div>
            {/* 让价率是「推荐价 - 报单价 / 推荐价」，整体报高了就会是负数。
                写成「平均让价：-0.20%」看着像算错了 —— 负的时候改成「加价」，
                一看就懂是报得比推荐价还高（2026-11-20 实拍）。 */}
            <div>
              平均{(pricing?.average_discount_rate ?? 0) >= 0 ? '让价' : '加价'}：
              {Math.abs((pricing?.average_discount_rate ?? 0) * 100).toFixed(2)}%
            </div>
            <div>
              最大{(pricing?.max_discount_rate ?? 0) >= 0 ? '让价' : '加价'}：
              {Math.abs((pricing?.max_discount_rate ?? 0) * 100).toFixed(2)}%
            </div>
          </div>
        </Card>

        <Card title="哪些履约环节超期" note="只计已过计划日、仍未完成的环节">
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
        </Card>
      </Row>

      <section id="analytics-reports" aria-label="明细报表">
        <GroupHead index="07" title="明细报表" />
        {/* 报表不写范围，业务员会把自己看到的数当成公司合计（2026-11-21 问过）。
            范围来自账号角色的 data_scope，后端真的按它过滤，这里只是把它说出来。 */}
        <p className="analytics-scope-note">
          <span className="crm-help-label">
            统计范围：{scopeLabel}
            <HelpTip
              forLabel="统计范围"
              text="数据范围由你的角色决定：全公司＝所有人的记录；本部门＝同部门同事；本部门及下级＝本部门连同所有下级部门；仅本人＝只看自己。可在「设置 → 我的 → 我的信息」查看自己的数据范围。两个同事看到不同数字，正是因为范围不同。"
            />
          </span>
          <span>
            以下报表只汇总这个范围内的记录。月度业绩、目标完成情况的金额按签单归属统计，钱算签单人；
            首批发货、延迟未发货按当前负责人统计。
          </span>
        </p>
        <div className="analytics-report-picker" role="group" aria-label="选择报表">
          {ANALYTICS_REPORTS.map(({ id, label }) => (
            <button key={id} type="button" aria-pressed={activeReport === id} onClick={() => selectReport(id)}>
              {label}
            </button>
          ))}
        </div>
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
              value={reportMonth} onChange={(value) => setReportMonth(typeof value === 'string' ? value : undefined)}
              showClear
              optionList={activeReport === 'delivery'
                ? (delivery?.trend ?? []).map((row) => ({ value: row.month, label: row.month.replace('-', ' 年 ') + ' 月' }))
                : Array.from({ length: 12 }, (_, index) => ({
                    value: String(index + 1).padStart(2, '0'),
                    label: `${index + 1} 月`,
                  }))}
              style={{ width: activeReport === 'delivery' ? 156 : 128 }}
            />
          )}
          {reportSearch && (
            <Input aria-label={`搜索${reportSearch}`} placeholder={`搜索${reportSearch}`} showClear
              value={reportKeyword} onChange={setReportKeyword} style={{ width: 240 }}
            />
          )}
          {(reportKeyword || reportMonth) && (
            <Button theme="borderless" size="small" onClick={() => { setReportKeyword(''); setReportMonth(undefined) }}>清除筛选</Button>
          )}
          {report && 'limited' in report && <span className="an-card-note">搜索范围为当前报表列出的记录。</span>}
        </div>
        {activeReport === 'delivery' && <SectionCard id="detail-delivery" title="首批发货情况（按负责人）">
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
        </SectionCard>}

        {activeReport === 'risk-order' && <SectionCard id="detail-risk-order" title="已过发货日、首批未发的订单">
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
        </SectionCard>}

        {activeReport === 'product' && <SectionCard id="detail-product" title="产品表现（询盘 / 报价 / 成交 / 失单 / 利润）">
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
        </SectionCard>}

        {activeReport === 'sales' && <SectionCard id="detail-sales" title="业务员表现">
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
                render: (v: number, r: SalesUserStat) => formatWholeCny(r.order_amount_exact ?? v),
              },
              {
                title: '回款额（按订单负责人）',
                dataIndex: 'received_amount',
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
        </SectionCard>}

        {/* 场景17：三个销售额口径必须分开看，否则"回款没到但签了单"会被当成已完成 */}
        {activeReport === 'basis' && <SectionCard id="detail-basis" title="每月签单、发货与回款">
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
                      render: (_: unknown, r: { month: string }) =>
                        formatWholeCny(exact(b.signed, r.month)),
                    },
                    {
                      title: '发货',
                      width: 130,
                      render: (_: unknown, r: { month: string }) =>
                        formatWholeCny(exact(b.shipped, r.month)),
                    },
                    {
                      title: '回款',
                      width: 130,
                      render: (_: unknown, r: { month: string }) =>
                        formatWholeCny(exact(b.received, r.month)),
                    },
                    {
                      title: '老客净额',
                      width: 130,
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
        </SectionCard>}

        {activeReport === 'target' && <SectionCard id="detail-target" title="月度销售目标">
          <p className="workflow-guide">
            这里看公司、部门或个人每月的目标，以及实际完成了多少。比如本月确认回款要到 10 万元、要有 3 家新客户下首单。
          </p>
          <p className="workflow-guide">
            {canSetTarget ? '要录入或修改月目标，点「设置月度目标」；也可以从对应月份那一行进入。' : '月度目标由有「系统设置与规则」权限的人设置。'}
            {can('task:view') && <> 给同事安排一次跟进、催款等具体事项，请到<Link to="/tasks">销售任务</Link>。</>}
          </p>
          {canSetTarget && <p className="an-card-note">月度实绩快照用于留存已结束月份的完成数据，日常设置目标时不用保存快照。</p>}
          {canSetTarget && <div className="toolbar" style={{ marginBottom: 12 }}>
            <Button onClick={() => openFreezeModal('freeze')}>保存月度实绩快照</Button>
            <Button type="primary" theme="outline" onClick={() => openTargetModal()}>设置月度目标</Button>
          </div>}
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
                    {r.actual_frozen && (
                      // 存档 / 实时必须一眼分得清：存档值不会因为后来的退货变小，
                      // 两者对不上时先看这个标记再怀疑数据（§4.1.5）
                      <span
                        style={{ marginLeft: 6, fontSize: 11, color: 'var(--crm-text-3)' }}
                        title="这一期已结账：数字是结账当天抄下来的存档值，不会因为后来的退货、改单变小"
                      >
                        已结账
                      </span>
                    )}
                  </span>
                ),
              },
              { title: '对象', dataIndex: 'user_name', width: 120 },
              { title: '新客目标', dataIndex: 'new_customer_target', width: 100 },
              { title: '新客实际', dataIndex: 'new_customer_actual', width: 100 },
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
                render: (v: number, r: SalesTargetRow) => formatWholeCny(r.sales_target_exact ?? v),
              },
              {
                // **考核口径**：确认回款。差额与达成率都是按它算的（§4.3）。
                // 这一列以前显示的是签单额、而差额用的是回款——同一行里摆了两个口径，
                // 用户看到的"实际"和"差额"自然对不上（返工单第 3 条同一类问题）。
                title: '实际·确认回款',
                dataIndex: 'assess_actual',
                width: 160,
                render: (v: number | undefined, r: SalesTargetRow) => (
                  <span>
                    {formatWholeCny(r.received_actual_exact ?? v)}
                    <a style={{ marginLeft: 6, fontSize: 12 }} onClick={() => openDrilldown(r, 'received')}>
                      明细
                    </a>
                  </span>
                ),
              },
              {
                // 展示口径：签单额。照常显示，但**不进差额**（§4.3）
                title: '签单额',
                dataIndex: 'sales_actual',
                width: 120,
                render: (v: number | undefined, r: SalesTargetRow) => formatWholeCny(r.sales_actual_exact ?? v),
              },
              {
                // 展示口径：发货额（按实际发货批次分摊，§4.1.4）
                title: '发货额',
                dataIndex: 'shipped_actual',
                width: 120,
                render: (v: number | undefined, r: SalesTargetRow) => formatWholeCny(r.shipped_actual_exact ?? v),
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
                render: (v: number, r: SalesTargetRow) => (v ? formatWholeCny(r.repeat_customer_target_exact ?? v) : '-'),
              },
              {
                title: '复购实际',
                dataIndex: 'repeat_customer_actual',
                width: 130,
                render: (v: number | undefined, r: SalesTargetRow) => formatWholeCny(r.repeat_customer_actual_exact ?? v),
              },
              {
                title: '复购差额',
                dataIndex: 'repeat_customer_variance',
                width: 130,
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
                          {r.actual_frozen && (
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
            dataSource={(targetsQuery.data?.rows ?? []).filter((row) =>
              (!reportMonth || row.period.slice(5) === reportMonth) && matches(row.user_name))}
            loading={targetsQuery.isLoading}
            rowKey={(r?: SalesTargetRow) => `${r?.period}-${r?.user_id ?? 'all'}`}
            pagination={false}
            empty={keyword || reportMonth ? noMatches : '还没有目标数据'}
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
        </SectionCard>}

      </section>

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
          <span style={{ fontSize: 13 }}>
            合计 ¥{Math.round(drilldownQuery.data?.total ?? 0).toLocaleString('zh-CN')} ·{' '}
            {drilldownQuery.data?.count ?? 0} 条
          </span>
          <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            {drilldownQuery.data?.source === 'snapshot'
              ? '读的是结账存档（不会因为后来的退货变小）'
              : '实时统计'}
          </span>
        </div>
        <Table
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
        />
        {drilldownQuery.data?.truncated && (
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
            <Select
              value={targetForm.user_id ?? 0}
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
            <Select
              value={targetForm.department_id ?? 0}
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
