/**
 * F08 工作台/图表复核脚本。
 * 运行：node review/repro/f08_frontend/check_f08.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  workbench: 'frontend/src/modules/workbench/WorkbenchPage.tsx',
  analyticsApi: 'frontend/src/shared/api/analytics.ts',
  kpi: 'frontend/src/shared/components/KpiStrip.tsx',
  options: 'frontend/src/shared/components/charts/options.ts',
  legend: 'frontend/src/shared/components/charts/DonutLegend.tsx',
  analyticsService: 'backend/app/modules/analytics/service.py',
  analyticsRouter: 'backend/app/modules/analytics/router.py',
}
const src = Object.fromEntries(Object.entries(F).map(([k, rel]) => [k, readFileSync(resolve(ROOT, rel), 'utf8')]))
const results = []
const check = (id, name, verdict, ok, evidence) => results.push({ id, name, verdict, ok: Boolean(ok), evidence })
const between = (text, start, end) => {
  const i = text.indexOf(start)
  if (i < 0) return ''
  const j = text.indexOf(end, i + start.length)
  return j < 0 ? text.slice(i) : text.slice(i, j)
}
const queries = (src.workbench.match(/useQuery\(/g) || []).length
const isErrors = (src.workbench.match(/isError/g) || []).length

check('F08-01', '工作台查询失败无错误态', 'CONFIRMED',
  ['summaryQuery','tasksQuery','risksQuery','funnelQuery','trendQuery','activitiesQuery','opportunitiesQuery','teamQuery']
    .every((name) => !new RegExp(`${name}\\.isError`).test(src.workbench))
    && /const summary = summaryQuery\.data/.test(src.workbench)
    && /const tasks = tasksQuery\.data \?\? \[\]/.test(src.workbench)
    && /const funnel = funnelQuery\.data \?\? \[\]/.test(src.workbench)
    && /const trend = trendQuery\.data \?\? \[\]/.test(src.workbench)
    && /const activities = activitiesQuery\.data \?\? \[\]/.test(src.workbench)
    && /const opportunities = opportunitiesQuery\.data\?\.items \?\? \[\]/.test(src.workbench)
    && /const risks = risksQuery\.data \?\? \[\]/.test(src.workbench)
    && /if \(!summary\) return '货款与运费明细加载中'/.test(src.workbench)
    && /funnel\.length === 0 && <div[^>]*>暂无商机数据/.test(src.workbench)
    && /tasksQuery\.isSuccess && tasks\.length === 0/.test(src.workbench),
  '8 个业务查询（summary/tasks/risks/funnel/trend/activities/opportunities/team）都没有 isError；失败落空/¥0/永久“加载中”',
)

check('F08-02', '工作台/团队/趋势金额契约无 exact，后端 float', 'CONFIRMED',
  /open_opportunity_amount: number/.test(src.analyticsApi)
    && /month_won_amount: number/.test(src.analyticsApi)
    && /month_received_amount: number/.test(src.analyticsApi)
    && /pending_receivable_amount: number/.test(src.analyticsApi)
    && /order_amount: number/.test(src.analyticsApi)
    && /won_amount_this_month: number/.test(src.analyticsApi)
    && /def _f\(value\) -> float:[\s\S]{0,80}?return float\(value or 0\)/.test(src.analyticsService)
    && /"month_won_amount": _f\(month_won_amount\)/.test(src.analyticsService)
    && /orders: dict\[str, float\] = \{\}/.test(src.analyticsService)
    && /orders\[key\] = orders\.get\(key, 0\.0\) \+ _f\(amount\)/.test(src.analyticsService)
    && /const exactMoney = \(value\?: number\) =>/.test(src.workbench)
    && !/month_won_amount_exact|team_won_amount_exact|pending_receivable_amount_exact/.test(src.analyticsApi),
  'DashboardSummary/TrendRow/TeamSummary 全是 number；后端 `_f` 转 float，趋势用 dict[str,float] 累加；前端 exactMoney 只格式化已丢精度的 number',
)

check('F08-03', '图表金额格式器整元/万字取整', 'CONFIRMED',
  /export const asMoney: ValueFormat = \(value\) =>[\s\S]*?Math\.round\(value\)/.test(src.options)
    && /abs >= 10_000\) return `¥\$\{\(value \/ 10_000\)\.toFixed\(abs >= 100_000 \? 0 : 1\)\}万`/.test(src.options)
    && /Math\.round\(\(item\.value \/ total\) \* 100\)/.test(src.legend)
    && 199999 / 10000 === 19.9999
    && (199999 / 10000).toFixed(0) === '20',
  `asMoney Math.round；compactMoney(199999)=${(199999 / 10000).toFixed(0)}万；图例百分比 Math.round`,
)

check('F08-04', 'KPI 卡键盘不可操作，key 用 label', 'CONFIRMED',
  src.kpi.includes('key={item.label}')
    && src.kpi.includes('onClick={item.onClick}')
    && !src.kpi.includes('role="button"')
    && !src.kpi.includes('tabIndex')
    && !src.kpi.includes('onKeyDown'),
  '整卡是 div onClick；无 role/tabIndex/onKeyDown；key 用展示 label',
)

check('F08-05', '漏项：self 范围用户“我的待办”永远显示 -', 'CONFIRMED',
  /if user\.data_scope not in TEAM_SCOPES:[\s\S]*?return \{"is_team_view": False, "data_scope": user\.data_scope\}/.test(src.analyticsService)
    && /const myRow = \(team\?\.members \?\? \[\]\)\.find\(\(m\) => m\.user_id === user\?\.id\)/.test(src.workbench)
    && /label: '我的待办',[\s\S]{0,80}value: String\(myRow\?\.todo_count \?\? '-'\)/.test(src.workbench)
    && /hint: `逾期 \$\{myRow\?\.overdue_count \?\? 0\} 项`/.test(src.workbench),
  'team_summary 对 data_scope=self 直接返回、不带 members；myRow 永远 undefined；“我的待办”显示 - / 逾期 0 项，即使 tasks 查询有数据',
)

check('F08-06', '待确认：商机卡片未按 opportunity:view 收门，后端 summary 只要求 customer:view', 'PENDING',
  /label: '进行中商机'/.test(src.workbench)
    && /value: String\(summary\?\.open_opportunity_count \?\? '-'\)/.test(src.workbench)
    && /hint: `估算货值 \$\{money\(summary\?\.open_opportunity_amount\)\}`/.test(src.workbench)
    && !/canViewOpportunities[\s\S]{0,120}进行中商机/.test(src.workbench)
    && /@router\.get\("\/dashboard\/summary"\)[\s\S]{0,160}require_permission\("customer:view"\)/.test(src.analyticsRouter)
    && /"open_opportunity_amount": _f\(open_amount\)/.test(src.analyticsService),
  '卡片无条件渲染；后端 summary 只要 customer:view 就返回 open_opportunity_count/amount。是否存在“有客户权、无商机权”的角色未核实，故待确认',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed/pending, ${results.filter((r) => !r.ok).length} missing`)
