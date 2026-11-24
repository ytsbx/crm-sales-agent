/**
 * F08-01 / F08-04 的行为复现（SSR，无浏览器、无网络）。
 *
 * 用法：node review/repro/f08_frontend/run.mjs
 * 断言写的都是「修复后应有的行为」，因此在当前代码上会失败。
 * 只为留证据，不改业务代码。
 */
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontend = path.resolve(here, '../../../frontend')
const mock = (name) => path.join(here, 'mocks', name)
const requireFromFrontend = createRequire(path.join(frontend, 'package.json'))
const load = async (name) => import(pathToFileURL(requireFromFrontend.resolve(name)).href)

const { createServer } = await load('vite')
const react = (await load('@vitejs/plugin-react')).default
const React = await load('react')
const { renderToStaticMarkup } = await load('react-dom/server')

const server = await createServer({
  configFile: false,
  root: frontend,
  logLevel: 'error',
  appType: 'custom',
  server: { middlewareMode: true },
  plugins: [react()],
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: [
      { find: '@tanstack/react-query', replacement: mock('useQuery.ts') },
      { find: '@douyinfe/semi-ui', replacement: mock('semi.tsx') },
      { find: '@douyinfe/semi-icons', replacement: mock('icons.tsx') },
      { find: 'react-router-dom', replacement: mock('router.tsx') },
      { find: '../store/auth', replacement: mock('auth.ts') },
      { find: '../../shared/store/auth', replacement: mock('auth.ts') },
      { find: '../../shared/hooks/permissions', replacement: mock('permissions.ts') },
      { find: './HelpTip', replacement: mock('HelpTip.tsx') },
      { find: '../../shared/components/HelpTip', replacement: mock('HelpTip.tsx') },
      { find: '../../shared/components/PageHeader', replacement: mock('Slot.tsx') },
      { find: '../../shared/components/SectionGroupHead', replacement: mock('Slot.tsx') },
      { find: '../../shared/components/SectionCard', replacement: mock('Slot.tsx') },
      { find: '../../shared/components/MaskedContact', replacement: mock('Slot.tsx') },
      { find: '../../shared/components/charts/EChart', replacement: mock('EChart.tsx') },
      { find: '../../shared/components/charts/options', replacement: mock('options.ts') },
    ],
  },
})

const Page = (await server.ssrLoadModule('/src/modules/workbench/WorkbenchPage.tsx')).default
const KpiStrip = (await server.ssrLoadModule('/src/shared/components/KpiStrip.tsx')).default

const summaryOk = {
  todo_count: 3,
  overdue_task_count: 1,
  stale_customer_count: 2,
  stale_customer_days: 30,
  open_opportunity_count: 4,
  open_opportunity_amount: 1000,
  pending_approval_count: 0,
  month_won_amount: 5000,
  month_order_goods_amount: 4500,
  month_order_logistics_amount: 300,
  month_order_other_charge_amount: 0,
  month_order_discount_amount: 0,
  month_order_unverified_count: 0,
  month_received_amount: 2000,
  month_received_by_arrival_amount: 2000,
  pending_receivable_amount: 3000,
  overdue_receivable_count: 0,
  open_lead_count: 1,
}

const error = new Error('500 Internal Server Error')

function renderPage(scenario) {
  globalThis.__F08_SCENARIO__ = scenario
  const html = renderToStaticMarkup(React.createElement(Page))
  return html
}

function textOf(html) {
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&yen;/g, '¥')
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim()
}

let failed = 0
function check(name, fn) {
  try {
    fn()
    console.log(`PASS  ${name}`)
  } catch (e) {
    failed += 1
    console.log(`FAIL  ${name}\n      ${e.message}`)
  }
}
function assert(cond, message) {
  if (!cond) throw new Error(message)
}

// ---------------------------------------------------------------- F08-01
const allError = {
  'dashboard-summary': { isError: true, error, isLoading: false },
  'dashboard-tasks': { isError: true, error, isLoading: false },
  'dashboard-risks': { isError: true, error, isLoading: false },
  funnel: { isError: true, error, isLoading: false },
  'dashboard-trend': { isError: true, error, isLoading: false },
  'dashboard-activities': { isError: true, error, isLoading: false },
  'workbench-opportunities': { isError: true, error, isLoading: false },
  'dashboard-team': { isError: true, error, isLoading: false },
}

const allErrorHtml = renderPage(allError)
const allErrorText = textOf(allErrorHtml)
console.log('\n--- 接口全部 500 时页面实际显示（节选）---')
console.log(allErrorText.slice(0, 900))

check('F08-01: 接口 500 时页面出现“加载失败”而不是空态/¥0', () => {
  assert(/加载失败|数据加载失败|查询失败/.test(allErrorText), `实际文案：${allErrorText.slice(0, 400)}`)
})
check('F08-01: 汇总失败时“估算货值”不显示为 ¥0', () => {
  assert(!/估算货值 ¥0/.test(allErrorText), '页面把失败折成了 ¥0')
})
check('F08-01: 汇总失败时订单金额组成不显示“加载中”', () => {
  assert(!/货款与运费明细加载中/.test(allErrorText), '页面把失败永久停在“加载中”')
})

// 单个查询失败（团队接口挂、其余成功）也会把“我的待办”打回 “-”
const teamOnlyError = {
  'dashboard-summary': { data: summaryOk, isSuccess: true, isLoading: false },
  'dashboard-tasks': { data: [], isSuccess: true, isLoading: false },
  'dashboard-risks': { data: [], isSuccess: true, isLoading: false },
  funnel: { data: [], isSuccess: true, isLoading: false },
  'dashboard-trend': { data: [], isSuccess: true, isLoading: false },
  'dashboard-activities': { data: [], isSuccess: true, isLoading: false },
  'workbench-opportunities': { data: { items: [], total: 0 }, isSuccess: true, isLoading: false },
  'dashboard-team': { isError: true, error, isLoading: false },
}
const teamOnlyText = textOf(renderPage(teamOnlyError))
check('F08-01: 只有团队接口失败时，“我的待办”仍应显示 3（summary 已成功）', () => {
  assert(/我的待办\s*3/.test(teamOnlyText), `实际：${teamOnlyText.slice(0, 300)}`)
})

// 主管视图（is_team_view=true）时同一份 summary 应当展示 3
const managerTeam = {
  is_team_view: true,
  data_scope: 'department',
  member_count: 1,
  team_task_count: 3,
  team_overdue_count: 1,
  team_won_count_this_month: 0,
  team_won_amount_this_month: 0,
  members: [{ user_id: 101, name: '张三', todo_count: 3, overdue_count: 1, won_amount_this_month: 0, stale_customer_count: 2 }],
  risky_opportunities: [],
  funnel: [],
}
const managerText = textOf(renderPage({ ...teamOnlyError, 'dashboard-team': { data: managerTeam, isSuccess: true, isLoading: false } }))
check('F08-01(对照): 主管视图下“我的待办”显示 3（证明数据链路本身可用）', () => {
  assert(/我的待办\s*3/.test(managerText), `实际：${managerText.slice(0, 300)}`)
})

// ---------------------------------------------------------------- 漏项：self 范围下“我的待办”永远 “-”
const selfScope = {
  'dashboard-summary': { data: summaryOk, isSuccess: true, isLoading: false },
  'dashboard-tasks': { data: [], isSuccess: true, isLoading: false },
  'dashboard-risks': { data: [], isSuccess: true, isLoading: false },
  funnel: { data: [], isSuccess: true, isLoading: false },
  'dashboard-trend': { data: [], isSuccess: true, isLoading: false },
  'dashboard-activities': { data: [], isSuccess: true, isLoading: false },
  'workbench-opportunities': { data: { items: [], total: 0 }, isSuccess: true, isLoading: false },
  'dashboard-team': { data: { is_team_view: false, data_scope: 'self' }, isSuccess: true, isLoading: false },
}
const selfText = textOf(renderPage(selfScope))
check('漏项: 业务员（data_scope=self）的“我的待办”应显示 summary.todo_count=3', () => {
  assert(/我的待办\s*3/.test(selfText), `实际：${selfText.slice(0, 300)}`)
})

// ---------------------------------------------------------------- F08-04
const kpiHtml = renderToStaticMarkup(
  React.createElement(KpiStrip, {
    items: [
      { label: '我的待办', value: '3', hint: '逾期 1 项', onClick: () => {} },
      { label: '进行中商机', value: '4', onClick: () => {} },
    ],
  }),
)
check('F08-04: 有 onClick 的 KPI 卡可被键盘聚焦/触发（role=button 或原生 button）', () => {
  assert(/role="button"|tabindex="0"|<button/.test(kpiHtml), `实际：${kpiHtml.slice(0, 400)}`)
})

await server.close()
console.log(`\n结果：${failed} 个断言失败（这些断言代表“修复后应有的行为”）`)
process.exitCode = 1
