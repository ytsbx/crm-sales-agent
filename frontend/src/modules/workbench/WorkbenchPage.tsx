import { useQuery } from '@tanstack/react-query'
import {
  IconArrowUp,
  IconCalendar,
  IconComment,
  IconRefresh,
} from '@douyinfe/semi-icons'
import { Banner, Button, Checkbox, Tag, Toast } from '@douyinfe/semi-ui'
import { Link, useNavigate } from 'react-router-dom'

import {
  getDashboardActivities,
  getDashboardRisks,
  getDashboardSummary,
  getDashboardTasks,
  getDashboardTrend,
  getTeamSummary,
  type DashboardTask,
  type TrendRow,
} from '../../shared/api/analytics'
import { getFunnel, listOpportunities } from '../../shared/api/opportunity'
import SectionCard from '../../shared/components/SectionCard'
import EChart from '../../shared/components/charts/EChart'
import {
  asMoney,
  comboBarLineOption,
  compactMoney,
} from '../../shared/components/charts/options'
import { useAuthStore } from '../../shared/store/auth'
import { useCopilotStore } from '../../shared/store/copilot'
import { usePermissions } from '../../shared/hooks/permissions'

const PRIORITY_LABEL: Record<string, string> = { high: '高优先级', normal: '普通', low: '低' }

const money = (value?: number) => `¥${Math.round(value ?? 0).toLocaleString('zh-CN')}`

function greeting() {
  const hour = new Date().getHours()
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
      height={240}
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
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const openCopilot = useCopilotStore((state) => state.openWith)
  const { can } = usePermissions()

  const summaryQuery = useQuery({ queryKey: ['dashboard-summary'], queryFn: getDashboardSummary })
  const tasksQuery = useQuery({ queryKey: ['dashboard-tasks'], queryFn: getDashboardTasks })
  const risksQuery = useQuery({ queryKey: ['dashboard-risks'], queryFn: getDashboardRisks })
  const funnelQuery = useQuery({ queryKey: ['funnel'], queryFn: getFunnel })
  const trendQuery = useQuery({ queryKey: ['dashboard-trend'], queryFn: () => getDashboardTrend(6) })
  const activitiesQuery = useQuery({
    queryKey: ['dashboard-activities'],
    queryFn: () => getDashboardActivities(6),
  })
  const opportunitiesQuery = useQuery({
    queryKey: ['workbench-opportunities'],
    queryFn: () => listOpportunities({ status: 'open', page_size: 5 }),
  })
  // PRD §4.2 主管视图：数据范围是 self 的人会拿到 is_team_view=false
  const teamQuery = useQuery({ queryKey: ['dashboard-team'], queryFn: getTeamSummary })

  const summary = summaryQuery.data
  const team = teamQuery.data
  const isTeamView = Boolean(team?.is_team_view)
  const tasks = tasksQuery.data ?? []
  const funnel = (funnelQuery.data ?? []).filter((row) => row.count > 0 || row.sequence <= 6)
  const maxFunnel = Math.max(...funnel.map((row) => row.count), 1)
  const trend = trendQuery.data ?? []
  const activities = activitiesQuery.data ?? []
  const opportunities = opportunitiesQuery.data?.items ?? []
  const risks = risksQuery.data ?? []

  const today = new Date()
  const todayTasks = tasks.filter((task) => {
    if (!task.due_at) return false
    const due = new Date(task.due_at)
    return due.toDateString() === today.toDateString()
  })

  const cards = [
    {
      label: '今日待办',
      value: String(summary?.todo_count ?? '-'),
      footer: `逾期 ${summary?.overdue_task_count ?? 0} 项`,
      tone: (summary?.overdue_task_count ?? 0) > 0 ? 'chip-error' : 'chip',
      onClick: () => navigate('/tasks'),
    },
    {
      label: '待跟进客户',
      value: String(summary?.stale_customer_count ?? '-'),
      footer: '30 天未联系',
      tone: (summary?.stale_customer_count ?? 0) > 0 ? 'chip-warning' : 'chip',
      onClick: () => navigate('/customers'),
    },
    {
      label: '进行中商机',
      value: String(summary?.open_opportunity_count ?? '-'),
      footer: `估算货值 ${money(summary?.open_opportunity_amount)}`,
      tone: 'chip',
      onClick: () => navigate('/opportunities'),
    },
    {
      label: '待审批报价',
      value: String(summary?.pending_approval_count ?? '-'),
      footer: '需要你处理',
      tone: (summary?.pending_approval_count ?? 0) > 0 ? 'chip-warning' : 'chip',
      onClick: () => navigate('/approvals'),
    },
    {
      label: '本月订单金额',
      value: money(summary?.month_won_amount),
      footer: '按订单金额统计',
      tone: 'chip',
      onClick: () => navigate('/orders'),
    },
    {
      label: '本月回款金额',
      value: money(summary?.month_received_amount),
      footer: `待回款 ${money(summary?.pending_receivable_amount)}`,
      tone: 'chip',
      onClick: () => navigate('/receivables'),
    },
  ]

  return (
    <div className="page-container">
      {/* 含外币、未折算的提醒（兜底）。
          业务口径本是"只做国内、币种固定人民币"，服务层也加了闸，所以正常情况下
          这块**根本不出现**。它是给"万一"准备的：外币金额与人民币直接相加得到的
          数字是错的，但页面上看不出来 —— 宁可明说"可能不准"，也不静默出错数。
          数据来自报表接口的 `currency_warnings`（见 shared/api/analytics.ts）。 */}
      {(summaryQuery.data?.currency_warnings ?? []).map((note) => (
        <div key={note} style={{ marginBottom: 14 }}>
          <Banner
            type="warning"
            closeIcon={null}
            description={note}
            title="汇总里含外币金额"
          />
        </div>
      ))}
      {/* 问候 */}
      <div
        className="wb-greeting"
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          marginBottom: 18,
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 24, fontWeight: 650, letterSpacing: '-0.3px' }}>
              {greeting()}，{user?.name ?? ''}
            </span>
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
          </div>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 13, marginTop: 6 }}>
            这里是您今天的工作概览 ·{' '}
            {today.toLocaleDateString('zh-CN', {
              year: 'numeric',
              month: 'long',
              day: 'numeric',
              weekday: 'long',
            })}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            数据同步时间：{new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
          </span>
          <Button
            icon={<IconRefresh />}
            onClick={() => {
              void summaryQuery.refetch()
              void tasksQuery.refetch()
              void trendQuery.refetch()
              void activitiesQuery.refetch()
              void teamQuery.refetch()
              Toast.success('已刷新')
            }}
          />
          <Button theme="solid" onClick={() => navigate('/leads')}>
            快速创建
          </Button>
        </div>
      </div>

      {/* 6 张 KPI 卡 */}
      <div className="kpi-grid kpi-cols-6">
        {cards.map((card) => (
          <div
            key={card.label}
            className="kpi-card"
            style={{ cursor: 'pointer' }}
            onClick={card.onClick}
          >
            <div className="kpi-label">{card.label}</div>
            <div className="kpi-value">{card.value}</div>
            <div style={{ marginTop: 10 }}>
              <span className={card.tone}>{card.footer}</span>
            </div>
          </div>
        ))}
      </div>

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

          <div className="kpi-grid kpi-cols-5">
            <div className="kpi-card">
              <div className="kpi-label">团队待办</div>
              <div className="kpi-value">{team.team_task_count ?? 0}</div>
              <div style={{ marginTop: 10 }}>
                <span
                  className={(team.team_overdue_count ?? 0) > 0 ? 'chip chip-error' : 'chip'}
                >
                  逾期 {team.team_overdue_count ?? 0} 项
                </span>
              </div>
            </div>
            <div
              className="kpi-card"
              style={{ cursor: 'pointer' }}
              onClick={() => navigate('/approvals')}
            >
              <div className="kpi-label">待审批报价</div>
              <div className="kpi-value">{team.pending_approval_count ?? 0}</div>
              <div style={{ marginTop: 10 }}>
                <span
                  className={(team.pending_approval_count ?? 0) > 0 ? 'chip chip-warning' : 'chip'}
                >
                  需要你处理
                </span>
              </div>
            </div>
            <div
              className="kpi-card"
              style={{ cursor: 'pointer' }}
              onClick={() => navigate('/customers')}
            >
              <div className="kpi-label">客户分配</div>
              <div className="kpi-value">{team.unassigned_customer_count ?? 0}</div>
              <div style={{ marginTop: 10 }}>
                <span className="chip">无负责人，待分配</span>
              </div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">本月团队成交</div>
              <div className="kpi-value">{team.team_won_count_this_month ?? 0}</div>
              <div style={{ marginTop: 10 }}>
                <span className="chip">
                  ¥{Math.round(team.team_won_amount_this_month ?? 0).toLocaleString('zh-CN')}
                </span>
              </div>
            </div>
            <div
              className="kpi-card"
              style={{ cursor: 'pointer' }}
              onClick={() => navigate('/opportunities')}
            >
              <div className="kpi-label">风险商机</div>
              <div className="kpi-value">{team.risky_opportunities?.length ?? 0}</div>
              <div style={{ marginTop: 10 }}>
                <span
                  className={(team.risky_opportunities?.length ?? 0) > 0 ? 'chip chip-error' : 'chip'}
                >
                  临近成交 / 长期未更新
                </span>
              </div>
            </div>
          </div>

          {/* 成员明细：谁忙、谁逾期、谁没跟客户 */}
          <div style={{ marginTop: 18 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10 }}>成员明细</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                  <th style={{ textAlign: 'left', padding: '6px 0', fontWeight: 500 }}>成员</th>
                  <th style={{ textAlign: 'right', padding: '6px 0', fontWeight: 500 }}>待办</th>
                  <th style={{ textAlign: 'right', padding: '6px 0', fontWeight: 500 }}>逾期</th>
                  <th style={{ textAlign: 'right', padding: '6px 0', fontWeight: 500 }}>
                    待跟进客户
                  </th>
                  <th style={{ textAlign: 'right', padding: '6px 0', fontWeight: 500 }}>
                    本月成交额
                  </th>
                </tr>
              </thead>
              <tbody>
                {(team.members ?? []).map((member) => (
                  <tr key={member.user_id} style={{ borderTop: '1px solid var(--crm-surface-high)' }}>
                    <td style={{ padding: '8px 0' }}>{member.name}</td>
                    <td style={{ textAlign: 'right', padding: '8px 0' }}>{member.todo_count}</td>
                    <td
                      style={{
                        textAlign: 'right',
                        padding: '8px 0',
                        color: member.overdue_count > 0 ? 'var(--crm-error)' : undefined,
                      }}
                    >
                      {member.overdue_count}
                    </td>
                    <td
                      style={{
                        textAlign: 'right',
                        padding: '8px 0',
                        color: member.stale_customer_count > 0 ? 'var(--crm-warning)' : undefined,
                      }}
                    >
                      {member.stale_customer_count}
                    </td>
                    <td style={{ textAlign: 'right', padding: '8px 0' }}>
                      ¥{Math.round(member.won_amount_this_month).toLocaleString('zh-CN')}
                    </td>
                  </tr>
                ))}
                {(team.members ?? []).length === 0 && (
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

      {/* 漏斗 / 趋势 / 动态 */}
      <div className="wb-tri-grid">
        <SectionCard>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14 }}>
            <span className="card-title">全链路销售转化漏斗</span>
            <Link to="/analytics" style={{ fontSize: 12, color: 'var(--crm-primary)' }}>
              查看分析
            </Link>
          </div>
          <div style={{ display: 'grid', gap: 14 }}>
            {funnel.map((row, index) => {
              const prev = index > 0 ? funnel[index - 1].count : null
              const rate = prev && prev > 0 ? Math.round((row.count / prev) * 100) : null
              return (
                <div key={row.stage_id}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                    <span style={{ color: 'var(--crm-text-2)' }}>
                      {index + 1}. {row.stage_name}
                    </span>
                    <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      {rate !== null && (
                        <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>转化 {rate}%</span>
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
        </SectionCard>

        <SectionCard>
          {/* 标题右边原来手写了一份图例（■ 订单金额 / ● 回款金额）——那是给
              老的手写 SVG 补的（它自己画不出图例）。换成 ECharts 之后图例由图表
              自己出、还能点选隐藏，留着这份就成了两行重复的图例。 */}
          <div style={{ marginBottom: 8 }}>
            <span className="card-title">订单与回款趋势</span>
          </div>
          <TrendChart data={trend} />
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
          <div style={{ display: 'grid', gap: 12 }}>
            {activities.map((row) => (
              <div key={row.id} style={{ display: 'flex', gap: 10 }}>
                <span
                  style={{
                    width: 26,
                    height: 26,
                    borderRadius: 8,
                    flexShrink: 0,
                    background: 'var(--crm-surface-low)',
                    color: 'var(--crm-primary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 12,
                  }}
                >
                  {row.operator.slice(0, 1)}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13 }}>
                    <span style={{ fontWeight: 600 }}>{row.operator}</span> {row.title}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--crm-text-3)', marginTop: 2 }}>
                    {new Date(row.at).toLocaleString('zh-CN', {
                      month: 'numeric',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </div>
                </div>
              </div>
            ))}
            {activities.length === 0 && <div style={{ color: 'var(--crm-text-3)' }}>暂无动态</div>}
          </div>
        </SectionCard>
      </div>

      {/* 待办 / 重点商机 / 日程与 AI */}
      <div className="wb-tri-grid" style={{ marginTop: 16 }}>
        <SectionCard>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <span className="card-title">我的待办任务</span>
            <Link to="/tasks" style={{ fontSize: 12, color: 'var(--crm-primary)' }}>
              全部 {tasks.length}
            </Link>
          </div>
          <div style={{ display: 'grid', gap: 10 }}>
            {tasks.slice(0, 5).map((task: DashboardTask) => (
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
                <Checkbox
                  onChange={async () => {
                    const { completeTask } = await import('../../shared/api/task')
                    await completeTask(task.id)
                    Toast.success('任务已完成')
                    void tasksQuery.refetch()
                    void summaryQuery.refetch()
                  }}
                />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13, lineHeight: 1.5 }}>{task.title}</div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
                    <span className={task.priority === 'high' ? 'chip chip-warning' : 'chip'}>
                      {PRIORITY_LABEL[task.priority] ?? task.priority}
                    </span>
                    {task.due_at && (
                      <span style={{ fontSize: 12, color: task.overdue ? 'var(--crm-error)' : 'var(--crm-text-3)' }}>
                        {new Date(task.due_at).toLocaleString('zh-CN', {
                          month: 'numeric',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                        {task.overdue ? '（已逾期）' : ''}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
            {tasks.length === 0 && (
              <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>今天没有待办，轻松一下</div>
            )}
          </div>
        </SectionCard>

        <SectionCard>
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
        </SectionCard>

        <div style={{ display: 'grid', gap: 16 }}>
          <SectionCard>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <span className="card-title">
                <IconCalendar /> 今日日程 ({todayTasks.length})
              </span>
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                {today.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}
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
                      ? new Date(task.due_at).toLocaleTimeString('zh-CN', {
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

          {can('agent:use') && <SectionCard>
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
              <button
                type="button"
                onClick={() => {
                  const now = new Date()
                  openCopilot(`分析本月成交冲刺机会（${now.getFullYear()}年${now.getMonth() + 1}月）。请先查询我有权限查看的进行中商机及关联报价、跟进记录，列出可以推进的机会、当前阻碍和建议的下一步动作。区分已知事实与判断；预计成交日期缺失时注明信息不足，不要编造金额或成交概率。本次仅提供分析，不新增或修改业务记录。`)
                }}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '8px 10px',
                  border: '1px solid var(--crm-surface-high)',
                  borderRadius: 'var(--crm-radius-sm)',
                  fontSize: 12.5,
                  background: 'transparent',
                  width: '100%',
                  fontFamily: 'inherit',
                  textAlign: 'left',
                  cursor: 'pointer',
                }}
              >
                <span style={{ color: 'var(--crm-text-2)' }}>
                  <IconArrowUp /> 分析本月成交冲刺机会
                </span>
                <span style={{ color: 'var(--crm-text-3)' }}>›</span>
              </button>
            </div>
            <div style={{ marginTop: 12, fontSize: 11, color: 'var(--crm-text-3)' }}>
              点击后查询当前账号有权限查看的业务数据
            </div>
          </SectionCard>}
        </div>
      </div>
    </div>
  )
}
