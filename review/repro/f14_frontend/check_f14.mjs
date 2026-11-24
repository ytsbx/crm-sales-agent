/**
 * F14 框架 / 登录 / 权限守卫复核脚本。
 * 运行：node review/repro/f14_frontend/check_f14.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  agentPage: 'frontend/src/modules/agent/AgentPage.tsx',
  agentApi: 'frontend/src/shared/api/agent.ts',
  login: 'frontend/src/modules/auth/LoginPage.tsx',
  client: 'frontend/src/shared/api/client.ts',
  guard: 'frontend/src/app/RoutePermissionGuard.tsx',
  router: 'frontend/src/app/router.tsx',
  menu: 'frontend/src/app/menu.ts',
  tabs: 'frontend/src/shared/store/tabs.ts',
  beijing: 'frontend/src/shared/beijing-time.ts',
  safeUrl: 'frontend/src/shared/safe-url.ts',
  humanize: 'frontend/src/shared/humanize-text.ts',
}
const src = Object.fromEntries(Object.entries(F).map(([k, rel]) => [k, readFileSync(resolve(ROOT, rel), 'utf8')]))
const results = []
const check = (id, name, verdict, ok, evidence) => results.push({ id, name, verdict, ok: Boolean(ok), evidence })
const has = (text, s) => text.includes(s)
const between = (text, start, end) => {
  const i = text.indexOf(start)
  if (i < 0) return ''
  const j = text.indexOf(end, i + start.length)
  return j < 0 ? text.slice(i) : text.slice(i, j)
}

check('F14-01', 'AI 会话详情失败显示“还没有消息”', 'CONFIRMED',
  has(src.agentPage, 'const detailQuery = useQuery')
    && !/detailQuery\.isError/.test(src.agentPage)
    && has(src.agentPage, "sessionId ? '这段对话还没有消息' : '开始新对话'")
    && has(src.agentPage, "messages.length === 0 && (!sessionId || !detailQuery.isPending)"),
  'detailQuery 无 isError；失败时 isPending=false、messages=[]，落“这段对话还没有消息”，待确认动作也看不到',
)

check('F14-02', '确认 AI 报价动作后不失效报价缓存', 'CONFIRMED',
  has(src.agentPage, "void queryClient.invalidateQueries({ queryKey: ['agent-session', sessionId] })")
    && has(src.agentPage, "queryKey: ['customers']")
    && has(src.agentPage, "queryKey: ['tasks']")
    && has(src.agentPage, "queryKey: ['followups']")
    && !between(src.agentPage, 'const confirmMutation', 'const rejectMutation').includes("queryKey: ['quotes']")
    && has(src.agentPage, 'confirmAgentAction(actionId)'),
  '确认动作只失效 agent-session/customers/tasks/followups；报价列表/详情 key 是 quotes/quote-versions，没有失效',
)

check('F14-03', 'AI 流式发送/建会话没有请求键', 'CONFIRMED',
  has(src.agentApi, 'body: JSON.stringify({ content })')
    && !between(src.agentApi, 'export async function streamAgentMessage', 'throw new ApiError').includes('request_key')
    && has(src.agentPage, 'createAgentSession({')
    && !between(src.agentPage, 'createAgentSession({', '})').includes('request_key'),
  'streamAgentMessage 只发 content；createAgentSession 也不带键。断流重试会重复消息/会话',
)

check('F14-04', 'API 客户端不统一 humanize 后端原文', 'CONFIRMED-P3',
  has(src.client, 'return text')
    && has(src.client, 'const baseMessage: string = readableMessage(body?.message, code)')
    && !has(src.client, 'humanizeBackendText')
    && has(src.humanize, 'export function humanizeBackendText'),
  'readableMessage 只做空值兜底；全站错误出口没有调用已有的 humanizeBackendText，外部开关名/上游原文可直达 Toast。影响是文案不是流程，建议 P3',
)

check('F14-05', '三个深链无菜单项，权限守卫直接放行', 'CONFIRMED',
  has(src.router, "{ path: 'order-drafts', element: <OrderDraftsPage /> }")
    && has(src.router, "{ path: 'pool-recycle', element: <PoolRecyclePage /> }")
    && has(src.router, "{ path: 'pricing', element: <PricingPage /> }")
    && !has(src.menu, "path: '/order-drafts'")
    && !has(src.menu, "path: '/pool-recycle'")
    && !/path: '\/pricing'/.test(src.menu)
    && has(src.guard, 'if (!menu || menu.key === \'settings\' || hasPermission(user, menu.permission))')
    && has(src.guard, 'return <Outlet />'),
  '三个路由都挂载了，但 MENUS 没有对应项；RoutePermissionGuard 对 !menu 一律放行，深链绕过“无权限”说明',
)

check('F14-06', '已有会话发送失败不恢复输入', 'CONFIRMED',
  has(src.agentPage, "const content = text.trim()")
    && has(src.agentPage, "setInput('')")
    && has(src.agentPage, 'void runStream(sessionId, content)')
    && has(src.agentPage, "else if (!controller.signal.aborted) Toast.error((error as Error).message)")
    && !between(src.agentPage, 'const runStream', 'const handleSend').includes('setInput(content)'),
  '已有会话直接 runStream，catch 只 Toast；输入框已在 handleSend 清空，失败后不恢复（新建会话分支才会恢复）',
)

check('F14-07', '钉钉授权地址未校验协议', 'CONFIRMED',
  has(src.login, 'const { authorize_url } = await dingtalkAuthorize()')
    && has(src.login, 'window.location.href = authorize_url')
    && !between(src.login, 'const { authorize_url }', 'window.location.href').includes('isSafeExternalUrl')
    && has(src.safeUrl, 'export function isSafeExternalUrl'),
  'authorize_url 直接赋给 location.href；已有 isSafeExternalUrl 却未使用，后端配置被污染时可跳 javascript:',
)

check('F14-08', '登录后 fetchMe 失败被吞，按无权限渲染', 'CONFIRMED',
  has(src.login, 'setAuth(result.access_token, null)')
    && has(src.login, 'const me = await fetchMe()')
    && has(src.login, '} catch {\n        // 用户信息拉取失败不阻塞登录\n      }')
    && has(src.login, 'navigate(returnTo, { replace: true })'),
  'token 已写入、user=null；fetchMe 失败静默，随后跳业务页。守卫对 user=null 判无权限，首屏是“没有权限”',
)

check('F14-09', '北京时间格式化把 dateStyle/timeStyle 分错组', 'CONFIRMED-LATENT',
  has(src.beijing, "const DATE_PART_KEYS = ['year', 'month', 'day', 'weekday', 'era'] as const")
    && has(src.beijing, 'date.toLocaleTimeString(\'zh-CN\', { hour12: false, ...parts.time')
    && !has(src.beijing, 'dateStyle')
    && !has(src.beijing, 'timeStyle'),
  'dateStyle/timeStyle 会被归到 time，toLocaleTimeString 收到复合选项会 TypeError；但当前全仓没有调用点传这两个选项，属潜伏',
)

check('F14-10', '多页签淘汰只提示最后一个', 'CONFIRMED',
  has(src.tabs, 'let closed: TabItem | null = null')
    && has(src.tabs, 'closed = victim')
    && has(src.tabs, 'autoClosed: trimmed.closed')
    && has(src.tabs, '? { label: trimmed.closed.label, seq: ++autoCloseSeq }')
    && has(src.tabs, 'result = result.filter((item) => item.key !== victim.key && item.parentKey !== victim.key)'),
  'closed 是单值，循环覆盖；关模块还会连带删详情，提示仍只报最后一个，实际关闭数可能更多',
)

check('F14-11', 'AI 动作确认/拒绝共用 mutation，按钮互相牵连', 'CONFIRMED',
  has(src.agentPage, 'loading={confirmMutation.isPending}')
    && has(src.agentPage, '<Button size="small" onClick={() => rejectMutation.mutate(action.id)}>')
    && !has(src.agentPage, 'disabled={confirmMutation.isPending || rejectMutation.isPending}'),
  '每张待确认卡都读同一个 confirmMutation.isPending，一起转圈；取消按钮无 disabled，可并发点另一条',
)

check('F14-12', 'AI 会话删除入口键盘不可达', 'CONFIRMED',
  has(src.agentPage, '<Popconfirm')
    && has(src.agentPage, '<span style={{ color: \'var(--crm-text-3)\', fontSize: 12 }} onClick={(e) => e.stopPropagation()}>')
    && !between(src.agentPage, '<Popconfirm\ntitle="删除这个会话？', '</Popconfirm>').includes('tabIndex'),
  '删除入口是纯 span，无 role/tabIndex/onKeyDown，键盘 Tab 无法聚焦',
)

check('F14-13', 'AI 动作只失效四个域，商机/审批/时间线仍旧', 'CONFIRMED',
  has(src.agentPage, "const confirmMutation = useMutation")
    && has(src.agentPage, "queryKey: ['agent-session', sessionId]")
    && has(src.agentPage, "queryKey: ['customers']")
    && has(src.agentPage, "queryKey: ['tasks']")
    && has(src.agentPage, "queryKey: ['followups']")
    && !between(src.agentPage, 'const confirmMutation', 'const rejectMutation').includes("['opportunities']")
    && !between(src.agentPage, 'const confirmMutation', 'const rejectMutation').includes("['approvals']")
    && !between(src.agentPage, 'const confirmMutation', 'const rejectMutation').includes("['timeline']")
    && has(src.agentPage, 'confirmAgentAction(actionId)'),
  '确认动作可按 tool_name 执行 update_opportunity_next_action / request_quote_approval / create_followup / 建报价；当前只失效 agent-session/customers/tasks/followups，商机详情与列表、审批列表、时间线、通知都可能保持旧值',
)

// 数值/运行时验证：复合日期选项在 Node 与浏览器同源，都会抛 TypeError
let dateStyleThrows = false
try {
  new Date('2026-11-23T10:00:00+08:00').toLocaleTimeString('zh-CN', { dateStyle: 'long' })
} catch (error) {
  dateStyleThrows = error instanceof TypeError
}
check('F14-09-RUNTIME', 'dateStyle 传给 toLocaleTimeString 必抛 TypeError', 'CONFIRMED',
  dateStyleThrows,
  'Node 实测 Invalid option : dateStyle；浏览器 Intl 同规范，确认会抛',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(16) : 'MISSING'.padEnd(16)} ${r.id} ${r.name}`)
  console.log(`                 ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed/pending, ${results.filter((r) => !r.ok).length} missing`)
