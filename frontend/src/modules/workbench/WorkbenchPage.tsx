import { useQuery } from '@tanstack/react-query'
import {
  IconArrowUp,
  IconCalendar,
  IconComment,
  IconRefresh,
} from '@douyinfe/semi-icons'
import { Button, Checkbox, Tag, Toast } from '@douyinfe/semi-ui'
import { Link, useNavigate } from 'react-router-dom'

import {
  getDashboardActivities,
  getDashboardRisks,
  getDashboardSummary,
  getDashboardTasks,
  getDashboardTrend,
  type DashboardTask,
  type TrendRow,
} from '../../shared/api/analytics'
import { getFunnel, listOpportunities } from '../../shared/api/opportunity'
import { useAuthStore } from '../../shared/store/auth'

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

/** 订单与回款趋势：柱状（订单）+ 折线（回款），手写 SVG，不引图表库。 */
function TrendChart({ data }: { data: TrendRow[] }) {
  const width = 520
  const height = 220
  const padding = { top: 16, right: 12, bottom: 28, left: 44 }
  const innerW = width - padding.left - padding.right
  const innerH = height - padding.top - padding.bottom
  const max = Math.max(...data.flatMap((d) => [d.order_amount, d.received_amount]), 1)
  const step = innerW / Math.max(data.length, 1)
  const barW = Math.min(28, step * 0.42)
  const y = (value: number) => padding.top + innerH - (value / max) * innerH

  const linePoints = data
    .map((d, i) => `${padding.left + step * i + step / 2},${y(d.received_amount)}`)
    .join(' ')

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: 'block' }}>
      {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
        <line
          key={ratio}
          x1={padding.left}
          x2={width - padding.right}
          y1={padding.top + innerH * ratio}
          y2={padding.top + innerH * ratio}
          stroke="var(--crm-surface-high)"
          strokeDasharray="3 4"
        />
      ))}
      {data.map((row, index) => {
        const x = padding.left + step * index + step / 2 - barW / 2
        return (
          <rect
            key={row.month}
            x={x}
            y={y(row.order_amount)}
            width={barW}
            height={Math.max(padding.top + innerH - y(row.order_amount), 2)}
            rx={5}
            fill="var(--crm-primary)"
          />
        )
      })}
      <polyline
        points={linePoints}
        fill="none"
        stroke="var(--crm-secondary)"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      {data.map((row, index) => (
        <circle
          key={`${row.month}-dot`}
          cx={padding.left + step * index + step / 2}
          cy={y(row.received_amount)}
          r={3.5}
          fill="#fff"
          stroke="var(--crm-secondary)"
          strokeWidth={2}
        />
      ))}
      {data.map((row, index) => (
        <text
          key={`${row.month}-label`}
          x={padding.left + step * index + step / 2}
          y={height - 8}
          textAnchor="middle"
          fontSize={12}
          fill="var(--crm-text-3)"
        >
          {row.label}
        </text>
      ))}
      <text x={4} y={padding.top + 4} fontSize={11} fill="var(--crm-text-3)">
        {money(max)}
      </text>
      <text x={4} y={padding.top + innerH} fontSize={11} fill="var(--crm-text-3)">
        0
      </text>
    </svg>
  )
}

export default function WorkbenchPage() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)

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

  const summary = summaryQuery.data
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
      {/* 问候 */}
      <div
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
              Toast.success('已刷新')
            }}
          />
          <Button theme="solid" onClick={() => navigate('/leads')}>
            快速创建
          </Button>
        </div>
      </div>

      {/* 6 张 KPI 卡 */}
      <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(6, minmax(0, 1fr))' }}>
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

      {/* 漏斗 / 趋势 / 动态 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.05fr 1.35fr 0.95fr', gap: 16 }}>
        <div className="card-block">
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
        </div>

        <div className="card-block">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span className="card-title">订单与回款趋势</span>
            <span style={{ display: 'flex', gap: 14, fontSize: 12, color: 'var(--crm-text-3)' }}>
              <span>
                <span style={{ color: 'var(--crm-primary)' }}>■</span> 订单金额
              </span>
              <span>
                <span style={{ color: 'var(--crm-secondary)' }}>●</span> 回款金额
              </span>
            </span>
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
        </div>

        <div className="card-block">
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
        </div>
      </div>

      {/* 待办 / 重点商机 / 日程与 AI */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.05fr 1.35fr 0.95fr', gap: 16, marginTop: 16 }}>
        <div className="card-block">
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
        </div>

        <div className="card-block">
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
        </div>

        <div style={{ display: 'grid', gap: 16 }}>
          <div className="card-block">
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
          </div>

          <div className="card-block">
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <span className="card-title">
                <IconComment /> AI Sales Agent
              </span>
              <span className="chip chip-ai">实时运行中</span>
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
              <Link
                to="/agent"
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
                  <IconArrowUp /> 分析本月成交冲刺机会
                </span>
                <span style={{ color: 'var(--crm-text-3)' }}>›</span>
              </Link>
            </div>
            <div style={{ marginTop: 12, fontSize: 11, color: 'var(--crm-text-3)' }}>
              已关联商机、报价与回款数据
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
