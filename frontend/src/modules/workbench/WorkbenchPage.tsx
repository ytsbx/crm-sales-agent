import { formatBeijingDate, formatBeijingDateTime, formatBeijingClock, formatBeijingWeekdayCN } from '../../shared/beijing-time'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  IconCalendar,
  IconComment,
  IconRefresh,
} from '@douyinfe/semi-icons'
import { Banner, Button, Checkbox, Pagination, Popconfirm, SideSheet, Tag, Toast } from '@douyinfe/semi-ui'
import { Link, useNavigate } from 'react-router-dom'

import {
  getStaleCustomers,
  getDashboardActivities,
  getDashboardRisks,
  getDashboardSummary,
  getDashboardTasks,
  getDashboardTrend,
  getTeamSummary,
  type DashboardSummary,
  type DashboardTask,
  type TrendRow,
} from '../../shared/api/analytics'
import { getFunnel, listOpportunities } from '../../shared/api/opportunity'
import MaskedContact from '../../shared/components/MaskedContact'
import PageHeader from '../../shared/components/PageHeader'
import GroupHead from '../../shared/components/SectionGroupHead'
import KpiStrip, { type KpiItem } from '../../shared/components/KpiStrip'
import SectionCard from '../../shared/components/SectionCard'
import { AUDIT_ACTION_LABEL } from '../../shared/audit-labels'
import EChart from '../../shared/components/charts/EChart'
import {
  asMoney,
  comboBarLineOption,
  compactMoney,
} from '../../shared/components/charts/options'
import { useAuthStore } from '../../shared/store/auth'
import { usePermissions } from '../../shared/hooks/permissions'

const PRIORITY_LABEL: Record<string, string> = { high: '高优先级', normal: '普通', low: '低' }

const money = (value?: number) => `¥${Math.round(value ?? 0).toLocaleString('zh-CN')}`
const exactMoney = (value?: number) => `¥${(value ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`

function orderAmountBreakdown(summary?: DashboardSummary) {
  if (!summary) return '货款与运费明细加载中'
  if (summary.month_order_unverified_count > 0) {
    return `${summary.month_order_unverified_count} 笔订单金额组成待核实`
  }
  const parts = [
    `货款 ${exactMoney(summary.month_order_goods_amount)}`,
    `运费 ${exactMoney(summary.month_order_logistics_amount)}`,
  ]
  if (summary.month_order_other_charge_amount) parts.push(`其他 ${exactMoney(summary.month_order_other_charge_amount)}`)
  if (summary.month_order_discount_amount) parts.push(`优惠 ${exactMoney(summary.month_order_discount_amount)}`)
  return parts.join(' + ')
}

function greeting() {
  const hour = Number(formatBeijingClock(new Date(), { hour: '2-digit' }))
  if (hour < 6) return '凌晨好'
  if (hour < 12) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
}

/**
 * 订单与回款趋势：柱=订单金额、线=回款金额，**同一根纵轴**（都是金额，同轴才能直接比）。
 *
 * 走全项目共用的图表组件（`shared/components/charts`），不再自己手写 SVG ——
 * 手写版没有悬浮提示、不跟主题色、缩放也要自己算，分析页那批图同样用它。
 */
function TrendChart({ data }: { data: TrendRow[] }) {
  if (!data.length) {
    return <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>暂无数据</div>
  }
  return (
    <EChart
      // 卡片会被同一行的「阶段分布」拉着等高，固定 240 就会在下面留一大块白。
      // 这里交给父级的 flex 决定：图表吃掉剩余高度，图会跟着变高，
      // 而不是留一块空白（2026-11-18）。
      height="100%"
      ariaLabel="近 6 个月订单金额与回款金额趋势图"
      option={comboBarLineOption({
        categories: data.map((row) => row.label),
        bars: [{ name: '订单金额', values: data.map((row) => row.order_amount) }],
        line: { name: '回款金额', values: data.map((row) => row.received_amount) },
        barFormat: asMoney,
        axisFormat: compactMoney,
      })}
    />
  )
}

export default function WorkbenchPage() {
  /** 正在提交"完成"的任务 id：用于禁用勾选框，防连点重复请求 */
  const [completingId, setCompletingId] = useState<number | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [lastRefreshAt, setLastRefreshAt] = useState<Date>()

  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const { can } = usePermissions()
  const canViewCustomers = can('customer:view')
  const canViewOpportunities = can('opportunity:view')
  const canViewTasks = can('task:view')

  const summaryQuery = useQuery({ queryKey: ['dashboard-summary'], queryFn: getDashboardSummary, enabled: canViewCustomers })
  const tasksQuery = useQuery({ queryKey: ['dashboard-tasks'], queryFn: getDashboardTasks, enabled: canViewCustomers })
  const risksQuery = useQuery({
    queryKey: ['dashboard-risks'], queryFn: getDashboardRisks,
    enabled: canViewCustomers && canViewOpportunities && can('agent:use'),
  })
  const funnelQuery = useQuery({ queryKey: ['funnel'], queryFn: getFunnel, enabled: canViewOpportunities })
  const trendQuery = useQuery({ queryKey: ['dashboard-trend'], queryFn: () => getDashboardTrend(6), enabled: canViewCustomers })
  const activitiesQuery = useQuery({
    queryKey: ['dashboard-activities'],
    queryFn: () => getDashboardActivities(6),
    enabled: canViewCustomers,
  })
  const opportunitiesQuery = useQuery({
    queryKey: ['workbench-opportunities'],
    queryFn: () => listOpportunities({ status: 'open', page_size: 5 }),
    enabled: canViewOpportunities,
  })
  // PRD §4.2 主管视图：数据范围是 self 的人会拿到 is_team_view=false
  const teamQuery = useQuery({ queryKey: ['dashboard-team'], queryFn: getTeamSummary, enabled: canViewCustomers })

  const summary = summaryQuery.data
  const team = teamQuery.data
  const disabledMembers = team?.disabled_members
  const isTeamView = canViewCustomers && Boolean(team?.is_team_view)
  const tasks = tasksQuery.data ?? []
  // ⚠️ 不再按 `count > 0` 过滤（2026-10-14 审查修）：`GET /opportunities/funnel`
  // 返回的是**全部阶段**（含 count=0 的档），把后面的空档滤掉会让下面
  // `{index + 1}.` 的**序号断档**（1./2./4.），而且「占在谈」的分母
  // （所有在谈商机）也会算错。空档显示 0 就是事实。
  const funnel = funnelQuery.data ?? []
  const maxFunnel = Math.max(...funnel.map((row) => row.count), 1)
  //: 所有在谈商机数 = 各阶段存量之和（funnel 本身就是 `status == "open"` 的存量分布）
  const funnelTotal = funnel.reduce((sum, row) => sum + row.count, 0)
  const trend = trendQuery.data ?? []
  const activities = activitiesQuery.data ?? []
  const opportunities = opportunitiesQuery.data?.items ?? []
  const risks = risksQuery.data ?? []

  const today = new Date()
  const todayTasks = tasks.filter((task) => {
    if (!task.due_at) return false
    return formatBeijingDate(task.due_at) === formatBeijingDate(today)
  })

  // 待跟进客户清单抽屉（审查指出：卡片只报数、看不到是哪几个）
  const [staleOpen, setStaleOpen] = useState(false)
  const [stalePage, setStalePage] = useState(1)
  const [stalePageSize, setStalePageSize] = useState(100)
  const staleQuery = useQuery({
    queryKey: ['stale-customers', stalePage, stalePageSize],
    queryFn: () => getStaleCustomers({ page: stalePage, page_size: stalePageSize }),
    enabled: staleOpen && canViewCustomers,
  })

  const refreshWorkbench = async () => {
    setRefreshing(true)
    const requests = [
      ...(canViewCustomers ? [
        summaryQuery.refetch(), tasksQuery.refetch(), trendQuery.refetch(),
        activitiesQuery.refetch(), teamQuery.refetch(),
      ] : []),
      ...(canViewOpportunities ? [funnelQuery.refetch(), opportunitiesQuery.refetch()] : []),
      ...(risksQuery.isEnabled ? [risksQuery.refetch()] : []),
      ...(staleQuery.isEnabled ? [staleQuery.refetch()] : []),
    ]
    const results = await Promise.allSettled(requests)
    setRefreshing(false)
    const failed = results.filter((result) => result.status === 'rejected' || result.value.isError).length
    if (failed === 0) {
      setLastRefreshAt(new Date())
      Toast.success('已刷新')
    } else if (failed === results.length) {
      Toast.error('工作台刷新失败，请重试。')
    } else {
      Toast.warning('部分工作台数据未刷新，请重试。')
    }
  }

  /* 「今日待办」原来取的是 summary.todo_count —— 那是**全团队** 11 个人的未完成待办
     之和（实测 83），卡片却叫"今日待办"，看着像我一个人今天要干 83 件。
     我自己其实只有 15 件（成员明细里那一行）。这里改成从成员明细里取"我"的那一行，
     并改名叫「我的待办」；团队口径留在下面「团队概览 › 团队待办 83」，不再重复。
     （2026-11-19 巡检，主人确认按这个改） */
  const myRow = (team?.members ?? []).find((m) => m.user_id === user?.id)

  const cards: KpiItem[] = [
    {
      label: '我的待办',
      value: String(myRow?.todo_count ?? '-'),
      hint: `逾期 ${myRow?.overdue_count ?? 0} 项`,
      tone: (myRow?.overdue_count ?? 0) > 0 ? 'error' : 'default',
      onClick: () => navigate('/tasks?mine=true&status=active'),
      href: '/tasks?mine=true&status=active&overdue=true',
    },
    {
      label: '待跟进客户',
      value: String(summary?.stale_customer_count ?? '-'),
      help: '超过设定天数没有有效跟进或业务进展的客户。点这张卡可以直接看是哪几家。',
      hint: `有在途业务但 ${summary?.stale_customer_days ?? '-'} 天无进展`,
      tone: (summary?.stale_customer_count ?? 0) > 0 ? 'warning' : 'default',
      // ⚠️ 从前这里跳 `/customers`（不带任何筛选），而客户列表既没有「待跟进」
      // 筛选项也没有「最近跟进」列 —— 报了警却看不到是哪几个（审查指出）。
      // 现在直接打开清单抽屉，一步到位。
      onClick: () => { setStalePage(1); setStaleOpen(true) },
    },
    {
      label: '进行中商机',
      value: String(summary?.open_opportunity_count ?? '-'),
      hint: `估算货值 ${money(summary?.open_opportunity_amount)}`,
      tone: 'default',
      onClick: () => navigate('/opportunities'),
    },
    {
      // 名字从「待审批报价」改成「待我审批」：下面团队概览原来也有一张同名的 1，
      // 两张卡摆在同一屏、数字一样，看着像重复计数。这里明确是"轮到我处理的"。
      label: '待我审批',
      value: String(summary?.pending_approval_count ?? '-'),
      hint: '需要你处理',
      tone: (summary?.pending_approval_count ?? 0) > 0 ? 'warning' : 'default',
      onClick: () => navigate('/approvals'),
    },
    {
      // 名字里带"订单"，是为了和下面团队概览的「本月团队成交」（商机口径）分开：
      // 两张卡原来一个叫"本月订单金额"一个叫"团队成交"，都是钱、差 10 倍，像算错了。
      label: '本月订单金额',
      // 统计卡金额走整数（主人 2026-11-17 拍板的口径）：概览大数字只用来看量级，
      // 加 .00 只会让六张卡显得啰嗦；带小数的账在单据页看。`money` 就是整数写法。
      value: money(summary?.month_won_amount),
      help: '本月创建的订单金额，按签约口径统计；不是商机的预估金额。',
      hint: `订单签约额 · ${orderAmountBreakdown(summary)}`,
      tone: 'default',
      onClick: () => navigate('/orders'),
    },
    {
      label: '本月回款金额',
      value: money(summary?.month_received_amount),
      // 两个日子会算出两个数（2026-11-21 主人要求显式并列）：
      // 主数字按**财务确认时间**归月（会计确认口径，与目标页一致）；
      // 下面小字按**实际到账日**归月（现金流口径）。差额 = 上月已到账、本月才点确认的那几笔。
      // 不并列的话，财务和出纳各报一个数会像"系统算错"。
      help: '本月财务已确认到账的金额，按财务确认时间归属。「按到账日」是同一笔钱按实际到账日统计，差额来自上月到账、本月才确认的笔数。「应收未结」含已到账待分配。',
      hint: (
        <div style={{ display: 'grid', gap: 2 }}>
          <span className="chip">
            按到账日 {money(summary?.month_received_by_arrival_amount)}
          </span>
          <span className="chip">应收未结 {money(summary?.pending_receivable_amount)}</span>
        </div>
      ),
      tone: 'default',
      onClick: () => navigate('/receivables?tab=payments'),
    },
  ]

  return (
    <div className="page-container">
      {/* 含外币、未折算的提醒（兜底）。
          业务口径本是"只做国内、币种固定人民币"，服务层也加了闸，所以正常情况下
          这块**根本不出现**。它是给"万一"准备的：外币金额与人民币直接相加得到的
          数字是错的，但页面上看不出来 —— 宁可明说"可能不准"，也不静默出错数。
          数据来自报表接口的 `currency_warnings`（见 shared/api/analytics.ts）。 */}
      {canViewCustomers && (summary?.currency_warnings ?? []).map((note) => (
        <div key={note} style={{ marginBottom: 14 }}>
          <Banner
            type="warning"
            closeIcon={null}
            description={note}
            title="汇总里含外币金额"
          />
        </div>
      ))}
      {/* 问候：复用全站页面头，标题字号与右侧操作区的间距跟其他页面同一套刻度。
          wb-greeting 只用来接住窄屏纵向堆叠那条老规则，不自带视觉。 */}
      <PageHeader
        className="wb-greeting"
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--crm-space-2)' }}>
            {greeting()}，{user?.name ?? ''}
            <span className="chip chip-primary">
              {user?.department ?? '未分配部门'} ·{' '}
              {(
                {
                  admin: '管理员',
                  sales_manager: '销售主管',
                  salesperson: '业务员',
                  finance: '财务',
                } as Record<string, string>
              )[user?.roles?.[0] ?? ''] ?? '无角色'}
            </span>
          </span>
        }
        subtitle={`这里是您今天的工作概览 · ${formatBeijingDate(today)} ${formatBeijingWeekdayCN(today)}`}
        extra={
          <>
            {lastRefreshAt && <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              上次完整刷新：{formatBeijingClock(lastRefreshAt, { hour: '2-digit', minute: '2-digit' })}
            </span>}
            {(canViewCustomers || canViewOpportunities) && <Button
              aria-label="刷新工作台"
              loading={refreshing}
              icon={<IconRefresh />}
              onClick={() => void refreshWorkbench()}
            />}
            {can('lead:view') && can('lead:create') && <Button type="primary" theme="outline" onClick={() => navigate('/leads?new=1')}>
              新建线索
            </Button>}
          </>
        }
      />

      {!canViewCustomers && <Banner closeIcon={null} type="info"
        description="当前账号没有客户查看权限，工作台的业务概览不显示。你仍可从左侧菜单打开已授权的功能。"
      />}
      {canViewCustomers && <><GroupHead
        index="01"
        title="今天要做什么"
        note="先看要处理的数，再看下面的走势和安排"
        primary
      />
      {/* 6 张 KPI 卡：与列表页/分析页共用一套；数值保持黑色，颜色只给状态标签（主人选甲） */}
      <KpiStrip
        items={cards}
        className="kpi-grid kpi-cols-6"
        tintValues={false}
        style={{ marginTop: 'var(--crm-space-2)' }}
      /></>}

      {/* PRD §4.2 主管视图：只有数据范围 ≥ 部门的人才会拿到 is_team_view */}
      {isTeamView && team && (
        <SectionCard style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14 }}>
            <div style={{ fontWeight: 600 }}>
              团队概览
              <span style={{ marginLeft: 8, color: 'var(--crm-text-3)', fontSize: 12, fontWeight: 400 }}>
                {team.member_count} 名成员 ·{' '}
                {(
                  {
                    department: '本部门',
                    department_and_sub: '本部门及下级',
                    all: '全部',
                  } as Record<string, string>
                )[team.data_scope] ?? team.data_scope}
              </span>
            </div>
          </div>

          {/* kpi-inline：这四格是"一张卡里的统计"，不是四张卡（2026-11-21 巡检） */}
          <KpiStrip
            className="kpi-grid kpi-cols-4 kpi-inline"
            tintValues={false}
            items={[
              {
                label: '团队待办',
                value: team.team_task_count ?? 0,
                hint: `逾期 ${team.team_overdue_count ?? 0} 项`,
                tone: (team.team_overdue_count ?? 0) > 0 ? 'error' : 'default',
              },
              {
                label: '公海客户',
                value: team.unassigned_customer_count ?? 0,
                hint: '没有跟进人，可领取',
                onClick: () => navigate('/customers?scope=pool'),
              },
              {
                label: '本月团队成交',
                // 大数字给金额、小字给笔数：这张卡的名字是「成交」，一眼要能看出成交额；
                // 原来大数字是笔数（10），旁边小字才是金额，读起来像「成交 10 元」。
                value: exactMoney(team.team_won_amount_this_month),
                hint: `商机成交额 · ${team.team_won_count_this_month ?? 0} 笔`,
              },
              {
                label: '风险商机',
                value: team.risky_opportunities?.length ?? 0,
                hint: '临近成交 / 长期未更新',
                tone: (team.risky_opportunities?.length ?? 0) > 0 ? 'error' : 'default',
                onClick: () => navigate('/opportunities'),
              },
            ]}
          />

          {/* 成员明细：谁忙、谁逾期、谁没跟客户 */}
          <div style={{ marginTop: 18 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10 }}>成员明细</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }} className="team-table">
              <thead>
                <tr style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                  {/* 数字列给固定宽：整表只有 5 列，不给宽度就会五等分，
                      「待跟进客户」占 170px 只放一个数，中间空得厉害 */}
                  {/* 列宽按比例给：原来是「成员」列不设宽、把剩下的 670px 全吃了，
                      右边四个数字列挤在 480px 里，一左一右失衡（2026-11-21 巡检）。 */}
                  <th style={{ textAlign: 'left', padding: '6px 12px', fontWeight: 500, width: '32%' }}>成员</th>
                  <th style={{ textAlign: 'right', padding: '6px 12px', fontWeight: 500, width: '17%' }}>待办</th>
                  <th style={{ textAlign: 'right', padding: '6px 12px', fontWeight: 500, width: '17%' }}>逾期</th>
                  <th style={{ textAlign: 'right', padding: '6px 12px', fontWeight: 500, width: '17%' }}>
                    待跟进客户
                  </th>
                  {/* 金额列挂 .cell-right：2246 行那条「原生表格一律居中」的
                      !important 规则会把这里的内联右对齐压掉（2026-11-19 实测
                      computed 仍是 center），必须靠 index.css 里同名的豁免拿回来。 */}
                  <th className="cell-right" style={{ textAlign: 'right', padding: '6px 12px', fontWeight: 500, width: '17%' }}>
                    本月成交额
                  </th>
                </tr>
              </thead>
              <tbody>
                {(team.members ?? []).map((member) => (
                  <tr key={member.user_id} style={{ borderTop: '1px solid var(--crm-surface-high)' }}>
                    <td style={{ padding: '8px 12px' }}>{member.name}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px' }}>{member.todo_count}</td>
                    <td
                      style={{
                        textAlign: 'right',
                        padding: '8px 12px',
                        color: member.overdue_count > 0 ? 'var(--crm-error)' : undefined,
                      }}
                    >
                      {member.overdue_count}
                    </td>
                    <td
                      style={{
                        textAlign: 'right',
                        padding: '8px 12px',
                        color: member.stale_customer_count > 0 ? 'var(--crm-warning)' : undefined,
                      }}
                    >
                      {member.stale_customer_count}
                    </td>
                    <td className="cell-right" style={{ textAlign: 'right', padding: '8px 12px' }}>
                      {exactMoney(member.won_amount_this_month)}
                    </td>
                  </tr>
                ))}
                {disabledMembers && (
                  <tr style={{ borderTop: '1px solid var(--crm-surface-high)', fontWeight: 600 }}>
                    <th scope="row" style={{ padding: '8px 12px' }}>停用员工合计</th>
                    <td style={{ padding: '8px 12px' }}>{disabledMembers.todo_count}</td>
                    <td
                      style={{
                        padding: '8px 12px',
                        color: disabledMembers.overdue_count > 0 ? 'var(--crm-error)' : undefined,
                      }}
                    >
                      {disabledMembers.overdue_count}
                    </td>
                    <td style={{ padding: '8px 12px' }}>—</td>
                    <td className="cell-right" style={{ textAlign: 'right', padding: '8px 12px' }}>
                      {exactMoney(disabledMembers.won_amount_this_month)}
                    </td>
                  </tr>
                )}
                {(team.members ?? []).length === 0 && !disabledMembers && (
                  <tr>
                    <td colSpan={5} style={{ padding: '12px 0', color: 'var(--crm-text-3)' }}>
                      数据范围内没有成员
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {(canViewCustomers || canViewOpportunities) && <><GroupHead index="02" title="业务走势" note="查看有权限的业务数据" />
      {/* 漏斗 / 趋势 / 动态 */}
      <div className="wb-tri-grid" style={{ marginTop: 'var(--crm-space-2)' }}>
        {canViewOpportunities && <SectionCard>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14 }}>
            {/* 标题原本是「全链路销售转化漏斗」，但画的是**存量分布** ——
                叫"转化漏斗"会让人以为每档是转化来的（2026-10-14 审查修）。
                要看曾到达各阶段的商机数，可以去分析页。 */}
            <span className="card-title">各阶段在谈商机分布</span>
            <Link to="/analytics" style={{ fontSize: 12, color: 'var(--crm-primary)' }}>
              查看分析
            </Link>
          </div>
          <div style={{ display: 'grid', gap: 14 }}>
            {funnel.map((row, index) => {
              // 阶段现存数量占当前全部在谈商机的比例。
              const rate = funnelTotal > 0 ? Math.round((row.count / funnelTotal) * 100) : null
              return (
                <div key={row.stage_id}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                    <span style={{ color: 'var(--crm-text-2)' }}>
                      {index + 1}. {row.stage_name}
                    </span>
                    <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      {rate !== null && (
                        <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>占在谈 {rate}%</span>
                      )}
                      <span style={{ fontWeight: 600 }}>{row.count}</span>
                    </span>
                  </div>
                  <div className="progress-track" style={{ marginTop: 6 }}>
                    <div
                      className="progress-fill"
                      style={{ width: `${Math.max((row.count / maxFunnel) * 100, 2)}%` }}
                    />
                  </div>
                </div>
              )
            })}
            {funnel.length === 0 && <div style={{ color: 'var(--crm-text-3)' }}>暂无商机数据</div>}
          </div>
        </SectionCard>}

        {canViewCustomers && <><SectionCard className="card-fill">
          {/* 标题右边原来手写了一份图例（■ 订单金额 / ● 回款金额）——那是给
              老的手写 SVG 补的（它自己画不出图例）。换成 ECharts 之后图例由图表
              自己出、还能点选隐藏，留着这份就成了两行重复的图例。 */}
          <div style={{ marginBottom: 8 }}>
            <span className="card-title">订单与回款趋势</span>
          </div>
          <div className="card-fill-body">
            <TrendChart data={trend} />
          </div>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              marginTop: 10,
              paddingTop: 12,
              borderTop: '1px solid var(--crm-surface-high)',
              fontSize: 12,
              color: 'var(--crm-text-2)',
            }}
          >
            <span>
              近 6 个月订单合计：{money(trend.reduce((sum, row) => sum + row.order_amount, 0))}
            </span>
            <span>
              已确认回款：{money(trend.reduce((sum, row) => sum + row.received_amount, 0))}
            </span>
          </div>
        </SectionCard>

        <SectionCard>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <span className="card-title">团队与业务动态</span>
          </div>
          {/* 动态卡在两个断点下会横跨整行，一行一条会剩半张白纸。
              网格按 280px 最小列宽自动分列：宽时三列、三栏布局时一列。 */}
          <div className="activity-list">
            {activities.map((row) => (
              <div key={row.id} style={{ display: 'flex', gap: 10 }}>
                {/* 头像方块去掉了（2026-11-17）：26px 灰色小方块里放操作人姓名的第一个字，
                    一个方框包一个字，既不像头像也不提供任何信息。
                    操作人本身就在下一行加粗显示，直接去掉更干净。 */}
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13 }}>
                    <span style={{ fontWeight: 600 }}>{row.operator}</span> {humanizeActivityTitle(row.title)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--crm-text-3)', marginTop: 2 }}>
                    {/* 原来是 month/day 数字，zh-CN 输出 `11/16 16:38`；
                        站内其它页都是 `2026-11-16`，统一成 YYYY-MM-DD HH:mm */}
                    {formatBeijingDateTime(row.at)}
                  </div>
                </div>
              </div>
            ))}
            {activities.length === 0 && <div style={{ color: 'var(--crm-text-3)' }}>暂无动态</div>}
          </div>
        </SectionCard></>}
      </div></>}

      {(canViewCustomers || canViewTasks || canViewOpportunities) && <><GroupHead index="03" title="待办与安排" note="查看有权限的任务与业务安排" />
      {/* 待办 / 重点商机 / 日程与 AI */}
      <div className="wb-tri-grid" style={{ marginTop: 'var(--crm-space-2)' }}>
        {(canViewCustomers || canViewTasks) && <SectionCard>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <span className="card-title">我的待办任务</span>
            {canViewTasks && <Link to="/tasks" style={{ fontSize: 12, color: 'var(--crm-primary)' }}>
              查看任务
            </Link>}
          </div>
          <div style={{ display: 'grid', gap: 10 }}>
            {canViewCustomers && tasks.slice(0, 5).map((task: DashboardTask) => (
              <div
                key={task.id}
                style={{
                  display: 'flex',
                  gap: 10,
                  alignItems: 'flex-start',
                  padding: 12,
                  border: '1px solid var(--crm-surface-high)',
                  borderRadius: 'var(--crm-radius-sm)',
                }}
              >
                {can('task:manage') && <Popconfirm
                  title={`确认完成「${task.title}」？`}
                  content="完成后任务会从未完成列表中移除。"
                  disabled={completingId === task.id}
                  onConfirm={async () => {
                    setCompletingId(task.id)
                    try {
                      const { completeTask } = await import('../../shared/api/task')
                      await completeTask(task.id)
                      Toast.success('任务已完成')
                    } catch (error) {
                      Toast.error((error as Error).message || '完成失败，请重试')
                    } finally {
                      setCompletingId(null)
                      // 无论成败都重取：成功要更新列表，失败要让勾回到真实状态
                      void tasksQuery.refetch()
                      void summaryQuery.refetch()
                    }
                  }}
                >
                  {/* 勾选只打开确认框；状态以服务端为准，请求失败不显示完成。 */}
                  <span style={{ display: 'inline-flex' }}>
                    <Checkbox checked={false} disabled={completingId === task.id} aria-label={`完成任务：${task.title}`} />
                  </span>
                </Popconfirm>}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13, lineHeight: 1.5 }}>{task.title}</div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
                    <span className={task.priority === 'high' ? 'chip chip-warning' : 'chip'}>
                      {PRIORITY_LABEL[task.priority] ?? task.priority}
                    </span>
                    {task.due_at && (
                      <span style={{ fontSize: 12, color: task.overdue ? 'var(--crm-error)' : 'var(--crm-text-3)' }}>
                        {/* 到期时间同上：一律 2026-11-16 16:38，不再 `11/16 16:38` */}
                        {formatBeijingDateTime(task.due_at)}
                        {task.overdue ? '（已逾期）' : ''}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
            {canViewCustomers && tasksQuery.isSuccess && tasks.length === 0 && (
              <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>今天没有待办，轻松一下</div>
            )}
            {!canViewCustomers && (
              <div className="workflow-guide">
                还没有客户查看权限，<Link to="/tasks">去「销售任务」</Link>查看自己的待办。
              </div>
            )}
          </div>
        </SectionCard>}

        {canViewOpportunities && <SectionCard>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <span className="card-title">重点跟进商机</span>
            <Link to="/opportunities" style={{ fontSize: 12, color: 'var(--crm-primary)' }}>
              查看全部
            </Link>
          </div>
          <div style={{ display: 'grid', gap: 10 }}>
            {opportunities.map((opp) => (
              <div
                key={opp.id}
                style={{
                  paddingBottom: 10,
                  borderBottom: '1px solid var(--crm-surface-high)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                  <Link
                    to={`/opportunities/${opp.id}`}
                    style={{ color: 'var(--crm-primary)', fontSize: 13, flex: 1, minWidth: 0 }}
                  >
                    {opp.title}
                  </Link>
                  <span style={{ fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap' }}>
                    {money(opp.expected_amount ?? 0)}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--crm-text-3)', marginTop: 2 }}>
                  {opp.customer_name}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
                  <Tag color="blue">{opp.stage_name}</Tag>
                  <div className="progress-track" style={{ flex: 1 }}>
                    <div
                      className="progress-fill"
                      style={{ width: `${Math.min((opp.item_count || 0) * 25 + 25, 100)}%` }}
                    />
                  </div>
                  <span style={{ fontSize: 11, color: 'var(--crm-text-3)', whiteSpace: 'nowrap' }}>
                    需求 {opp.item_count} 条
                  </span>
                </div>
              </div>
            ))}
            {opportunities.length === 0 && (
              <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>暂无进行中的商机</div>
            )}
          </div>
        </SectionCard>}

        {canViewCustomers && <div style={{ display: 'grid', gap: 16 }}>
          <SectionCard>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <span className="card-title">
                <IconCalendar /> 今日日程 ({todayTasks.length})
              </span>
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                {/* 卡片右上角原来是 `11/17`，缺年份；统一成 2026-11-17 */}
                {formatBeijingDate(today)}
              </span>
            </div>
            <div style={{ display: 'grid', gap: 8 }}>
              {todayTasks.slice(0, 4).map((task) => (
                <div
                  key={task.id}
                  style={{
                    padding: '8px 10px',
                    background: 'var(--crm-surface-low)',
                    borderRadius: 'var(--crm-radius-sm)',
                    fontSize: 12.5,
                  }}
                >
                  <span style={{ color: 'var(--crm-primary)', marginRight: 8 }}>
                    {task.due_at
                      ? formatBeijingClock(task.due_at, {
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                      : '--:--'}
                  </span>
                  {task.title}
                </div>
              ))}
              {todayTasks.length === 0 && (
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>今天没有安排</div>
              )}
            </div>
          </SectionCard>

          {canViewOpportunities && can('agent:use') && <SectionCard>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <span className="card-title">
                <IconComment /> AI Sales Agent
              </span>
              <span className="chip chip-ai">按需分析</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 10 }}>
              基于当前销售线为您推荐高频动作：
            </div>
            <div style={{ display: 'grid', gap: 8 }}>
              {risks.slice(0, 3).map((risk) => (
                <Link
                  key={risk.id}
                  to={`/opportunities/${risk.id}`}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '8px 10px',
                    border: '1px solid var(--crm-surface-high)',
                    borderRadius: 'var(--crm-radius-sm)',
                    fontSize: 12.5,
                  }}
                >
                  <span style={{ color: 'var(--crm-text-2)' }}>
                    查看「{risk.title}」进展
                  </span>
                  <span style={{ color: 'var(--crm-text-3)' }}>›</span>
                </Link>
              ))}
            </div>
            <div style={{ marginTop: 12, fontSize: 11, color: 'var(--crm-text-3)' }}>
              点击后查询当前账号有权限查看的业务数据
            </div>
          </SectionCard>}
        </div>}
      </div></>}

      {/* 待跟进客户清单（审查指出：卡片只报数、点进去看不到是哪几个）。
          口径与卡片数字**同源**（同一个 `/dashboard/stale-customers` 背后的条件），
          所以不会出现"卡片说 2 个、这里列出 3 个"。 */}
      <SideSheet
        title={`待跟进客户${staleQuery.data ? `（${staleQuery.data.total} 个）` : ''}`}
        visible={staleOpen && canViewCustomers}
        onCancel={() => setStaleOpen(false)}
        width={680}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-2)' }}>
            口径：你可见的私海客户里，
            有<b>在途业务</b>（未结束商机 / 已发送或已接受报价 / 在途打样 /
            未完成订单 / 未收清应收 / 待处理需求），且超过
            <b> {staleQuery.data?.days ?? summary?.stale_customer_days ?? '-'} 天</b>
            没有有效跟进或业务进展。没有在途业务的客户不在这里催。
          </div>
          {staleQuery.isLoading && <div style={{ color: 'var(--crm-text-3)' }}>正在加载…</div>}
          {staleQuery.isError && (
            <div style={{ color: 'var(--crm-danger, #d92c20)' }}>
              加载失败：{(staleQuery.error as Error).message}
            </div>
          )}
          {staleQuery.data && staleQuery.data.total === 0 && (
            <div style={{ color: 'var(--crm-text-3)' }}>没有在途业务停滞的客户，保持得很好。</div>
          )}
          {(staleQuery.data?.items ?? []).map((row) => (
            <div
              key={row.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '10px 12px',
                border: '1px solid var(--crm-border, #f0f0f0)',
                borderRadius: 6,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Link to={`/customers/${row.id}`} style={{ fontWeight: 500 }}>
                    {row.name}
                  </Link>
                  {row.level && <Tag size="small">{row.level} 级</Tag>}
                  <Tag size="small" color={row.days_since === null ? 'red' : 'orange'}>
                    {row.days_since === null ? '暂无活动时间记录' : `${row.days_since} 天无有效活动`}
                  </Tag>
                </div>
                <div style={{ marginTop: 4, fontSize: 12, color: 'var(--crm-text-2)' }}>
                  跟进人：{(row.followers ?? []).map((item) => item.name).join('、') || '（无）'}
                  {row.contact_name ? ' · 主联系人：' : ''}
                  {row.contact_name}
                  {row.contact_name && row.contact_phone ? (
                    <>
                      （<MaskedContact value={row.contact_phone} />）
                    </>
                  ) : null}
                  {row.next_followup_at
                    ? ` · 约定下次跟进：${formatBeijingDate(row.next_followup_at)}`
                    : ''}
                </div>
              </div>
              {/* 直接跳到该客户的跟进页签，落地就能记录 */}
              <Link to={`/customers/${row.id}?tab=followups`}>
                <Button size="small" theme="outline" type="primary">
                  去记录跟进
                </Button>
              </Link>
            </div>
          ))}
          {staleQuery.data && staleQuery.data.total > 0 && (
            <Pagination
              currentPage={stalePage}
              pageSize={stalePageSize}
              total={staleQuery.data.total}
              showTotal
              showSizeChanger
              pageSizeOpts={[10, 20, 50, 100, 200]}
              onPageChange={setStalePage}
              onPageSizeChange={(size: number) => { setStalePageSize(size); setStalePage(1) }}
            />
          )}
        </div>
      </SideSheet>
    </div>
  )
}


/**
 * 业务动态的标题说人话（2026-11-17 视觉审视 V-01）。
 * 后端把「动作码 + 对象」直接拼在一起下发，例如
 * `open_customer_duplicate_cases客户`、`create_quote记录`：
 * 非技术同事看到的是一串英文加中文硬拼。
 * 这里拿审计日志那张已经维护好的动作中文表（156 条）做前缀匹配，
 * 认得的就翻成中文、认不得的原样保留 —— 不改后端、不改数据。
 */
const ACTIVITY_ACTION_CODES = Object.keys(AUDIT_ACTION_LABEL).sort((a, b) => b.length - a.length)

function humanizeActivityTitle(title: string): string {
  const code = ACTIVITY_ACTION_CODES.find((item) => title.startsWith(item))
  if (!code) return title
  const rest = title.slice(code.length).replace(/^记录/, '').trim()
  const label = AUDIT_ACTION_LABEL[code]
  return rest ? `${label} · ${rest}` : label
}
