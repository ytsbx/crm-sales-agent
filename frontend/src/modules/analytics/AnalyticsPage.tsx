import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Input, Modal, Select, Table, Toast } from '@douyinfe/semi-ui'

import PageHeader from '../../shared/components/PageHeader'
import KpiStrip from '../../shared/components/KpiStrip'
import SectionCard from '../../shared/components/SectionCard'
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
  funnelChartOption,
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
  getOperationTimingSummary,
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
      title={note ? `${title}（${note}）` : title}
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
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
      <div style={{ width: 146, flex: 'none' }}>
        <EChart
          height={146}
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

export default function AnalyticsPage() {
  const { can } = usePermissions()
  const canSetTarget = can('settings:manage')
  const queryClient = useQueryClient()
  const [targetYear, setTargetYear] = useState(new Date().getFullYear())
  const [targetModal, setTargetModal] = useState<{
    visible: boolean
    period?: string
    user_id?: number | null
  }>({ visible: false })
  const [targetForm, setTargetForm] = useState<{ user_id?: number | null; department_id?: number | null; period: string; new_customer_target: string; sales_target: string; repeat_customer_target: string }>({
    user_id: undefined,
    department_id: undefined,
    period: `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}`,
    new_customer_target: '',
    sales_target: '',
    repeat_customer_target: '',
  })

  const opportunityQuery = useQuery({ queryKey: ['an-opportunities'], queryFn: getOpportunityStats })
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
  // 操作耗时（场景18）：回答"系统比表格快多少"，数字来自前端埋点
  const timingQuery = useQuery({
    queryKey: ['operation-timings'],
    queryFn: () => getOperationTimingSummary(30),
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
        new_customer_target: Number(targetForm.new_customer_target || 0),
        sales_target: Number(targetForm.sales_target || 0),
        repeat_customer_target: Number(targetForm.repeat_customer_target || 0),
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
        period: row.period,
        new_customer_target: String(row.new_customer_target),
        sales_target: String(row.sales_target),
        repeat_customer_target: row.repeat_customer_target
          ? String(row.repeat_customer_target)
          : '',
      })
      setTargetModal({ visible: true, period: row.period, user_id: row.user_id })
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

  // 交期趋势：单量柱 + 准时率线，只用有发货记录的那几个月
  const deliveryTrend = (delivery?.trend ?? []).filter((row) => row.on_time + row.late > 0)

  return (
    <div className="page-container">
      <PageHeader title="数据分析" subtitle="数据从业务流程实时聚合，不做二次录入" />

      {currencyWarnings.map((note) => (
        <div key={note} style={{ marginBottom: 14 }}>
          <Banner type="warning" closeIcon={null} description={note} title="汇总里含外币金额" />
        </div>
      ))}

      <KpiStrip
        items={[
          {
            label: '商机成交率',
            value: `${((opportunity?.win_rate ?? 0) * 100).toFixed(0)}%`,
            hint: `成交 ${opportunity?.won_count ?? 0} / 失单 ${opportunity?.loss_count ?? 0}`,
          },
          {
            label: '报价接受率',
            value: `${((quote?.accept_rate ?? 0) * 100).toFixed(0)}%`,
            hint: `报价单 ${quote?.quote_count ?? 0} 张`,
          },
          {
            label: '平均让价幅度',
            value: `${((quote?.average_discount ?? 0) * 100).toFixed(2)}%`,
            hint: `平均版本数 ${quote?.average_versions ?? 0}`,
          },
          {
            label: '低价审批比例',
            value: `${((quote?.approval_rate ?? 0) * 100).toFixed(0)}%`,
            hint: `需审批 ${quote?.approval_required_count ?? 0} 次`,
          },
          {
            label: '应收合计',
            value: money(receivable?.plan_amount),
            hint: `已收 ${money(receivable?.received_amount)}`,
          },
          {
            label: '未回款',
            value: money(receivable?.unreceived_amount),
            hint: `逾期节点 ${receivable?.overdue_count ?? 0}`,
          },
          {
            label: '线索转化率',
            value: `${((lead?.conversion_rate ?? 0) * 100).toFixed(0)}%`,
            hint:
              lead?.average_conversion_days != null
                ? `平均 ${lead.average_conversion_days} 天转化`
                : `线索 ${lead?.total ?? 0} 条`,
          },
          {
            label: '客户活跃 / 沉睡',
            value: `${customer?.active_count ?? 0} / ${customer?.dormant_count ?? 0}`,
            hint: `复购客户 ${customer?.repeat_customer_count ?? 0} 家`,
          },
          {
            label: '平均成交周期',
            value: cycle?.average_days != null ? `${cycle.average_days} 天` : '-',
            hint: cycle?.won_with_history
              ? `基于 ${cycle.won_with_history} 个有阶段记录的成交商机`
              : '暂无成交商机',
          },
          {
            label: '逾期应收',
            value: money(payment?.overdue_amount),
            hint: `逾期节点 ${payment?.overdue_node_count ?? 0} 个`,
          },
          {
            label: '准时交付率',
            // 没有已交付样本时给 "-" 而不是 0%——0% 会被读成"全都延迟"
            value: deliverySummary?.delivered_order_count
              ? `${(deliverySummary.on_time_rate * 100).toFixed(0)}%`
              : '-',
            hint: `准时 ${deliverySummary?.on_time_count ?? 0} / 延迟 ${deliverySummary?.late_count ?? 0}`,
          },
          {
            label: '交期风险',
            value: `${deliverySummary?.risk_order_count ?? 0} 单`,
            hint: `${deliverySummary?.due_soon_days ?? 7} 天内到期 ${deliverySummary?.due_soon_order_count ?? 0} 单`,
          },
        ]}
      />

      {/* ── ① 趋势：随时间变的东西，只有折线说得清 ─────────────────────── */}
      <Row columns={2}>
        <Card title="业绩趋势" note="月度 · 签单 / 发货 / 回款，另附老客净额">
          {trendMonths.length === 0 ? (
            <NoData />
          ) : (
            <EChart
              height={264}
              ariaLabel="月度签单、发货、回款与老客净额的折线图"
              option={lineChartOption({
                categories: trendMonths,
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
            与下方「目标口径明细」同一份数：签单看成交日、发货看首批发货、回款看到账日。
          </div>
        </Card>

        <Card title="交期趋势" note="近 12 个月首批发货 · 柱=单量，线=准时率">
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
          <Table<DeliveryTrendRow>
            size="small"
            pagination={false}
            rowKey="month"
            dataSource={deliveryTrend}
            columns={[
              { title: '月份', dataIndex: 'label', width: 80 },
              { title: '准时', dataIndex: 'on_time', width: 70 },
              { title: '延迟', dataIndex: 'late', width: 70 },
              {
                title: '准时率',
                render: (_: unknown, r: DeliveryTrendRow) =>
                  r.on_time + r.late ? `${((r.on_time / (r.on_time + r.late)) * 100).toFixed(0)}%` : '—',
              },
            ]}
            empty="近 12 个月还没有发货记录"
          />
        </Card>
      </Row>

      {/* ── ② 占比：看"各占几成"，环形比横条直观 ───────────────────────── */}
      <Row columns={4}>
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
            centerValueFormat={asMoney}
            centerLabel="合计"
          />
        </Card>
      </Row>

      {/* ── ③ 分档：每一档各多少，且顺序有意义 ───────────────────────────── */}
      <Row columns={3}>
        <Card title="线索状态分布">
          <Columns data={lead?.by_status ?? []} unit="条" />
        </Card>
        <Card title="应收节点状态">
          <Columns data={receivable?.by_status ?? []} unit="个节点" />
        </Card>
        <Card title="逾期账龄分布" note="未结清节点 · 顺序即账龄">
          <Columns data={payment?.aging ?? []} unit="个" rotateLabels />
        </Card>
      </Row>

      {/* ── ④ 漏斗：只有"到达过"才是逐级减少，才配画成漏斗 ───────────────── */}
      <Row columns={1}>
        <Card title="商机阶段转化" note="到达过该阶段的商机数（累计，逐级递减）">
          {(opportunity?.stage_conversion ?? []).length === 0 ? (
            <NoData />
          ) : (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))',
                gap: 16,
                alignItems: 'start',
              }}
            >
              <EChart
                height={Math.max(240, (opportunity?.stage_conversion ?? []).length * 38)}
                ariaLabel="商机阶段转化漏斗图"
                option={funnelChartOption({
                  data: (opportunity?.stage_conversion ?? []).map((row) => ({
                    name: row.stage_name,
                    value: row.reached_count,
                  })),
                  format: asCount('个'),
                })}
              />
              <Table
                size="small"
                pagination={false}
                rowKey="stage_id"
                dataSource={opportunity?.stage_conversion ?? []}
                columns={[
                  { title: '阶段', dataIndex: 'stage_name', width: 130 },
                  { title: '到达', dataIndex: 'reached_count', width: 80 },
                  {
                    title: '较上一阶段转化',
                    dataIndex: 'conversion_from_previous',
                    render: (v: number | null) =>
                      v === null ? (
                        <span style={{ color: 'var(--crm-text-3)' }}>—</span>
                      ) : (
                        `${(v * 100).toFixed(0)}%`
                      ),
                  },
                ]}
              />
            </div>
          )}
        </Card>
      </Row>

      {/* ── ⑤ 排名：比大小、名字长，横着写才读得顺 ───────────────────────── */}
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
                  name: `${row.level} 级（${row.item_count} 条）`,
                  value: row.average_price,
                })),
                format: asMoney,
                axisFormat: compactMoney,
              })}
            />
          )}
          <div style={{ marginTop: 14, fontSize: 13, color: 'var(--crm-text-2)', lineHeight: 2 }}>
            <div>低价审批率：{asPercent(pricing?.low_price_approval_rate ?? 0)}</div>
            <div>平均让价：{((pricing?.average_discount_rate ?? 0) * 100).toFixed(2)}%</div>
            <div>最大让价：{((pricing?.max_discount_rate ?? 0) * 100).toFixed(2)}%</div>
          </div>
        </Card>

        <Card title="逾期节点分布" note="与每日逾期提醒同口径">
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
            <div>已过交期仍未发货：{deliverySummary?.risk_order_count ?? 0} 单</div>
            <div>
              {deliverySummary?.due_soon_days ?? 7} 天内到期且未发货：
              {deliverySummary?.due_soon_order_count ?? 0} 单
            </div>
            <div>在跟但交期或类型待补充：{deliverySummary?.no_due_date_open_count ?? 0} 单</div>
            <div>
              平均延迟：
              {deliverySummary?.average_delay_days != null
                ? `${deliverySummary.average_delay_days} 天`
                : '—'}
              （最长 {deliverySummary?.max_delay_days ?? '—'} 天）
            </div>
          </div>
        </Card>
      </Row>

      {/* ── ⑥ 明细表：算账要抄的具体数字，图替代不了 ───────────────────── */}
      <SectionCard title="交期履约（按负责人）" style={{ marginTop: 16 }}>
        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 10, lineHeight: 1.7 }}>
          准时 = 首批发货日期 ≤ 计划发货日（到货日减运输天数）；统计近 {deliverySummary?.window_months ?? 12} 个月内已发首批货的订单。
          缺少交期或交期类型未确认的已发货单不进准时率分母（当前 {deliverySummary?.undated_delivered_count ?? 0} 单）。
        </div>
        {(delivery?.by_owner ?? []).length === 0 ? (
          <NoData />
        ) : (
          <Table<DeliveryOwnerRow>
            size="small"
            pagination={false}
            rowKey={(r?: DeliveryOwnerRow) => String(r?.owner_id ?? r?.owner_name ?? '')}
            dataSource={delivery?.by_owner ?? []}
            columns={[
              { title: '负责人', dataIndex: 'owner_name', width: 110 },
              { title: '已交付', dataIndex: 'order_count', width: 80 },
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
                render: (v: number | null) => (v == null ? '—' : `${v} 天`),
              },
            ]}
          />
        )}
      </SectionCard>

      <SectionCard title="交期风险单（已过交期仍未发货）" style={{ marginTop: 16 }}>
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
          dataSource={delivery?.risk_orders ?? []}
          loading={deliveryQuery.isLoading}
          rowKey="order_id"
          pagination={false}
          empty="没有超期未发货的订单"
        />
      </SectionCard>

      <SectionCard title="产品表现（询盘 / 报价 / 成交 / 失单 / 利润）" style={{ marginTop: 16 }}>
        <Table<ProductStat>
          columns={[
            {
              title: 'SKU',
              dataIndex: 'sku_code',
              width: 180,
              // 原本只有编码。分析页每行是一个 SKU 的汇总，编码认不出是哪个产品；
              // `product_name` 是**产品**名（如"田字塑料托盘 1200×1000"），
              // 与 SKU 名（如"田字塑料托盘 1200×1000 黑色"）不是一回事，两个都要有。
              render: (v: string | null, record: ProductStat) => (
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
            { title: '产品', dataIndex: 'product_name', width: 160, render: (v: string | null) => v ?? '-' },
            { title: '规格', dataIndex: 'specification', render: (v: string | null) => v ?? '-' },
            { title: '询盘', dataIndex: 'inquiry_times', width: 80 },
            { title: '报价', dataIndex: 'quote_times', width: 80 },
            {
              title: '累计报价数量',
              dataIndex: 'quote_quantity',
              width: 120,
              render: (v: number) => v.toLocaleString('zh-CN'),
            },
            { title: '成交', dataIndex: 'won_times', width: 80 },
            { title: '失单', dataIndex: 'lost_times', width: 80 },
            {
              title: '利润（快照累计）',
              dataIndex: 'profit_amount',
              width: 150,
              render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
            },
          ]}
          dataSource={productQuery.data ?? []}
          loading={productQuery.isLoading}
          rowKey="sku_code"
          pagination={false}
          empty="还没有报价数据"
        />
      </SectionCard>

      <SectionCard title="业务员表现" style={{ marginTop: 16 }}>
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
              render: (v: number) => `¥${Math.round(v).toLocaleString('zh-CN')}`,
            },
            {
              title: '回款额（按订单负责人）',
              dataIndex: 'received_amount',
              render: (v: number) => `¥${Math.round(v).toLocaleString('zh-CN')}`,
            },
          ]}
          dataSource={salesQuery.data ?? []}
          loading={salesQuery.isLoading}
          rowKey="user_id"
          pagination={false}
          empty="暂无数据"
        />
      </SectionCard>

      {/* 场景17：三个销售额口径必须分开看，否则"回款没到但签了单"会被当成已完成 */}
      <SectionCard title="目标口径明细（签单 / 发货 / 回款 + 老客净额）" style={{ marginTop: 16 }}>
        {(() => {
          const b = basesQuery.data
          if (!b) return <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>暂无数据</div>
          const pick = (rows: { month: string; value: number }[], m: string) =>
            rows.find((r) => r.month === m)?.value ?? 0
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
                dataSource={months.map((m) => ({ month: m }))}
                empty="今年还没有数据"
                columns={[
                  { title: '月份', dataIndex: 'month', width: 80 },
                  {
                    title: '签单',
                    width: 130,
                    render: (_: unknown, r: { month: string }) =>
                      `¥${Math.round(pick(b.signed, r.month)).toLocaleString('zh-CN')}`,
                  },
                  {
                    title: '发货',
                    width: 130,
                    render: (_: unknown, r: { month: string }) =>
                      `¥${Math.round(pick(b.shipped, r.month)).toLocaleString('zh-CN')}`,
                  },
                  {
                    title: '回款',
                    width: 130,
                    render: (_: unknown, r: { month: string }) =>
                      `¥${Math.round(pick(b.received, r.month)).toLocaleString('zh-CN')}`,
                  },
                  {
                    title: '老客净额',
                    width: 130,
                    render: (_: unknown, r: { month: string }) =>
                      `¥${Math.round(pick(b.repeat_net, r.month)).toLocaleString('zh-CN')}`,
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
              <div style={{ marginTop: 12, fontSize: 12, color: 'var(--crm-text-3)', lineHeight: 1.9 }}>
                <div>{b.source_note}</div>
                <div>老客：{b.basis_note?.repeat}</div>
                <div>发货：{b.basis_note?.shipped}</div>
                <div>新客两种口径：{b.basis_note?.new_by_created}；{b.basis_note?.new_by_first_deal}</div>
              </div>
            </>
          )
        })()}
      </SectionCard>

      <SectionCard title="操作耗时（场景18：拿它跟表格流程比）" style={{ marginTop: 16 }}>
        <Table
          size="small"
          pagination={false}
          dataSource={timingQuery.data?.summary ?? []}
          rowKey="operation"
          columns={[
            { title: '流程', dataIndex: 'operation_label', width: 200 },
            { title: '样本数', dataIndex: 'samples', width: 80 },
            {
              title: '平均耗时',
              dataIndex: 'avg_ms',
              width: 110,
              render: (v: number | null) => (v == null ? '—' : `${Math.round(v / 1000)} 秒`),
            },
            {
              title: '中位数',
              dataIndex: 'median_ms',
              width: 100,
              render: (v: number | null) => (v == null ? '—' : `${Math.round(v / 1000)} 秒`),
            },
            {
              title: 'P90',
              dataIndex: 'p90_ms',
              width: 100,
              render: (v: number | null) => (v == null ? '—' : `${Math.round(v / 1000)} 秒`),
            },
            {
              title: '平均手输字段',
              dataIndex: 'avg_typed_fields',
              width: 120,
              render: (v: number | null) => v ?? '—',
            },
          ]}
        />
        {timingQuery.data?.note && (
          <div style={{ marginTop: 8, color: 'var(--crm-text-3)', fontSize: 12 }}>
            {timingQuery.data.note}
          </div>
        )}
      </SectionCard>

      <SectionCard title="目标 vs 实际（模块⑧）" style={{ marginTop: 16 }}>
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <Select
            value={targetYear}
            onChange={(value) => setTargetYear(value as number)}
            optionList={[targetYear - 1, targetYear, targetYear + 1].map((y) => ({
              value: y,
              label: `${y} 年`,
            }))}
            style={{ width: 120 }}
          />
          <div style={{ flex: 1 }} />
          {canSetTarget && (
            <Button onClick={() => openFreezeModal('freeze')} style={{ marginRight: 8 }}>
              结账存档
            </Button>
          )}
          {canSetTarget && <Button theme="solid" onClick={() => openTargetModal()}>设定目标</Button>}
        </div>
        <Table<SalesTargetRow>
          scroll={{ x: 2060 }}
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
                  ? <span style={{ color: 'var(--crm-text-3)' }}>—</span>
                  : rate(r.new_customer_actual, r.new_customer_target),
            },
            {
              title: '销售目标',
              dataIndex: 'sales_target',
              width: 130,
              render: (v: number) => `¥${Math.round(v).toLocaleString('zh-CN')}`,
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
                  ¥{Math.round(v ?? 0).toLocaleString('zh-CN')}
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
              render: (v?: number) => `¥${Math.round(v ?? 0).toLocaleString('zh-CN')}`,
            },
            {
              // 展示口径：发货额（按实际发货批次分摊，§4.1.4）
              title: '发货额',
              dataIndex: 'shipped_actual',
              width: 120,
              render: (v?: number) => `¥${Math.round(v ?? 0).toLocaleString('zh-CN')}`,
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
                  ? <span style={{ color: 'var(--crm-text-3)' }} title={r.achievement_note ?? ''}>—</span>
                  : rate(r.assess_actual ?? r.sales_actual, r.sales_target),
            },
            {
              // 复购（老客净额）：口径是"期初固定的老客池在本期的订单净额"，
              // 定义在后端 target_bases.py。以前目标能存、页面看不到，等于设了没人管。
              title: '复购目标',
              dataIndex: 'repeat_customer_target',
              width: 130,
              render: (v: number) => (v ? `¥${Math.round(v).toLocaleString('zh-CN')}` : '—'),
            },
            {
              title: '复购实际',
              dataIndex: 'repeat_customer_actual',
              width: 130,
              render: (v: number) => `¥${Math.round(v).toLocaleString('zh-CN')}`,
            },
            {
              title: '复购差额',
              dataIndex: 'repeat_customer_variance',
              width: 130,
              render: (v: number, r: SalesTargetRow) =>
                r.repeat_customer_target ? (
                  <span style={{ color: v >= 0 ? 'var(--crm-primary)' : 'var(--crm-error)' }}>
                    {v >= 0 ? '+' : ''}
                    ¥{Math.round(v).toLocaleString('zh-CN')}
                  </span>
                ) : (
                  <span style={{ color: 'var(--crm-text-3)' }}>—</span>
                ),
            },
            {
              title: '差额',
              dataIndex: 'sales_variance',
              width: 130,
              render: (v: number) => (
                <span style={{ color: v >= 0 ? 'var(--crm-primary)' : 'var(--crm-error)' }}>
                  {v >= 0 ? '+' : '-'}¥{Math.abs(Math.round(v)).toLocaleString('zh-CN')}
                </span>
              ),
            },
            ...(canSetTarget
              ? [
                  {
                    title: '操作',
                    width: 120,
                    render: (_: unknown, r: SalesTargetRow) => (
                      <>
                        <a onClick={() => openTargetModal(r)}>编辑</a>
                        {r.actual_frozen && (
                          // 只有已结账的期间才谈得上"重算"：没结过账本来就在实时算
                          <a
                            style={{ marginLeft: 8 }}
                            onClick={() => openFreezeModal('refreeze', r.period)}
                          >
                            重算
                          </a>
                        )}
                      </>
                    ),
                  },
                ]
              : []),
          ]}
          dataSource={targetsQuery.data?.rows ?? []}
          loading={targetsQuery.isLoading}
          rowKey={(r?: SalesTargetRow) => `${r?.period}-${r?.user_id ?? 'all'}`}
          pagination={false}
          empty="还没有目标数据"
        />
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
      </SectionCard>

      <Modal
        title={freezeModal.mode === 'freeze' ? '结账存档' : '重算已结账的实绩'}
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
        okText={freezeModal.mode === 'freeze' ? '结账' : '重算'}
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
              ? '结账后这一期的实绩和「构成它的明细」都抄一份存档：之后订单再被退货、改单，这一期也不会变。当月不能结——数据还在产生。'
              : '重算会按现在的数据重出一份，并清掉「上一版有、这一版没有」的陈旧汇总。改了历史数字会写进审计（谁、什么时候、为什么）。'}
          </div>
        </div>
      </Modal>

      <Modal
        title={`${drilldown.period ?? ''} · ${drilldown.user_name ?? ''} 的实际构成`}
        visible={drilldown.visible}
        onCancel={() => setDrilldown({ visible: false, metric: 'received' })}
        footer={null}
        width={780}
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
              render: (v: string | null | undefined, r) => v ?? `#${r?.id ?? ''}`,
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
              render: (v?: number) => `¥${Math.round(v ?? 0).toLocaleString('zh-CN')}`,
            },
            {
              title: '时间',
              dataIndex: 'date',
              width: 150,
              // 新客行的日期取自**冻结快照**（R07）。旧版快照只冻了归属月份、
              // 没冻日期，这里就是空的 —— 如实说「未记录」，不拿现在的订单
              // 重算一个顶上（那正是自相矛盾的来源：归属一月、日期跳三月）。
              render: (v?: string | null, r?: { record_type?: string }) =>
                v ?? (r?.record_type === 'customer' ? '未记录' : '—'),
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
        title={targetModal.period ? '设定目标' : '设定目标'}
        visible={targetModal.visible}
        onCancel={() => setTargetModal({ visible: false })}
        onOk={() => targetSaveMutation.mutate()}
        confirmLoading={targetSaveMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>对象</div>
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
            <div style={{ marginBottom: 4 }}>团队（可选）</div>
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
                { value: 0, label: '不限（全公司）' },
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
            <div style={{ marginBottom: 4 }}>新客户目标（个）</div>
            <Input
              value={targetForm.new_customer_target}
              onChange={(value) => setTargetForm({ ...targetForm, new_customer_target: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>销售额目标（元）</div>
            <Input
              value={targetForm.sales_target}
              onChange={(value) => setTargetForm({ ...targetForm, sales_target: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>复购目标（元，老客净额）</div>
            <Input
              value={targetForm.repeat_customer_target}
              onChange={(value) => setTargetForm({ ...targetForm, repeat_customer_target: value })}
            />
          </div>
        </div>
      </Modal>
    </div>
  )
}
