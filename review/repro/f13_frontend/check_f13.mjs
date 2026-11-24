/**
 * F13 审计标签 / 通用组件 / 布局复核脚本。
 * 运行：node review/repro/f13_frontend/check_f13.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  layout: 'frontend/src/app/layout/AppLayout.tsx',
  globalSearch: 'frontend/src/app/layout/GlobalSearch.tsx',
  decision: 'frontend/src/modules/common/DecisionMakerCard.tsx',
  copilot: 'frontend/src/modules/common/CopilotDrawer.tsx',
  followup: 'frontend/src/modules/common/FollowUpModal.tsx',
  bell: 'frontend/src/modules/common/NotificationBell.tsx',
  voucher: 'frontend/src/modules/common/PaymentVoucherControl.tsx',
  timeline: 'frontend/src/modules/common/Timeline.tsx',
  auditLabels: 'frontend/src/shared/audit-labels.ts',
  picker: 'frontend/src/shared/components/PermissionPicker.tsx',
  settings: 'frontend/src/modules/settings/SettingsPage.tsx',
  agentInsight: 'frontend/src/shared/components/AgentInsight.tsx',
  lazy: 'frontend/src/shared/components/LazyRegion.tsx',
  masked: 'frontend/src/shared/components/MaskedContact.tsx',
  followupSvc: 'backend/app/modules/followup/service.py',
  approvalNotify: 'backend/app/modules/approval/router.py',
  paymentNotify: 'backend/app/modules/payment/router.py',
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

check('F13-01', '账号生成弹窗明文默认密码', 'CONFIRMED',
  has(src.layout, '你的登录账号已生成')
    && has(src.layout, '<p>初始密码：<strong>123456</strong></p>')
    && has(src.layout, 'initial_password_pending'),
  'Modal 直接渲染固定初始密码 123456；截屏/同事路过即可带走，改密前可用它登录',
)

check('F13-02', '联系人查询失败当“没有联系人”', 'CONFIRMED',
  has(src.decision, 'const contactsQuery = useQuery')
    && has(src.decision, 'const contacts = useMemo')
    && !/contactsQuery\.isError/.test(src.decision)
    && has(src.decision, '该客户还没有登记联系人')
    && has(src.decision, '去客户详情加联系人'),
  '查询无 isError；失败 contacts=[]，落“没有登记联系人”空态并引导去加联系人',
)

check('F13-03', 'Copilot 会话详情失败静默空会话', 'CONFIRMED',
  has(src.copilot, 'const detailQuery = useQuery')
    && !/detailQuery\.isError/.test(src.copilot)
    && has(src.copilot, 'const detailMessages = detailQuery.data?.messages ?? []')
    && has(src.copilot, 'const pending = (detailQuery.data?.actions ?? []).filter')
    && has(src.copilot, 'messages.length === 0 && !sending'),
  '详情无 isError；失败 messages/pending 为空，只显示快捷提问，待确认动作也消失',
)

check('F13-04', '权限目录失败显示 0/0 且静默丢 code', 'CONFIRMED',
  has(src.settings, 'const permissionsQuery = useQuery')
    && has(src.settings, 'loading={permissionsQuery.isLoading}')
    && !/permissionsQuery\.isError/.test(src.settings)
    && has(src.settings, 'options={(permissionsQuery.data ?? []).map((perm) => ({')
    && has(src.picker, 'onChange(options.filter((option) => next.has(option.code)).map((option) => option.code))')
    && has(src.picker, '已选 {picked} / {total}'),
  '失败 options=[]；显示“已选 0 / 0”；toggle/toggleGroup 用 options.filter 回写，会丢掉未加载的 code',
)

check('F13-05', '审计“还有 N 项”少报对象/未知枚举', 'CONFIRMED',
  has(src.auditLabels, "if (typeof value === 'object') {")
    && has(src.auditLabels, 'return \'\'')
    && has(src.auditLabels, 'if (/^[a-z][a-z0-9_]{1,24}$/.test(text)')
    && has(src.auditLabels, 'if (!auditFieldLabel(type, key)) hidden += 1')
    && !/auditFieldValue[\s\S]{0,40}=== ''[\s\S]{0,80}hidden/.test(src.auditLabels),
  '对象/未知英文码返回空串，changeSummary 跳过；hidden 只数“字段没中文名”，不数“值被隐藏”。修正：数组返回“N 项”，不会丢',
)

check('F13-06', 'AgentInsight 只显示前 10 行', 'CONFIRMED',
  has(src.agentInsight, '.slice(0, 10)')
    && has(src.agentInsight, 'const records: Record<string, unknown>[]')
    && !/还有 \$\{|共 \$\{.*条/.test(src.agentInsight),
  'rows.slice(0,10) 后直接渲染，没有总数/剩余提示',
)

check('F13-07', 'LazyRegion 无 default 时静默空白', 'CONFIRMED',
  has(src.lazy, 'setLoaded(() => mod.default)')
    && has(src.lazy, "setPhase('ready')")
    && has(src.lazy, 'Loaded ? createElement(Loaded')
    && has(src.lazy, ': null'),
  'mod.default 非函数时 Loaded 为 undefined，phase 仍 ready，最终渲染 null，无错误态',
)

check('F13-08', '全局搜索浮层不跟滚动、resize 监听重建', 'CONFIRMED',
  has(src.globalSearch, "window.addEventListener('resize', onResize)")
    && !/window.addEventListener\('scroll'/.test(src.globalSearch)
    && /useEffect\(\(\) => \{[\s\S]{0,200}?window\.addEventListener\('resize'[\s\S]{0,80}?\}\)/.test(src.globalSearch),
  '只有 resize 重算 fixed 坐标，没有 scroll；resize effect 无依赖数组，每次渲染重建',
)

check('F13-09', 'MaskedContact 展开态不随 value 复位', 'CONFIRMED',
  has(src.masked, 'const [revealed, setRevealed] = useState(false)')
    && has(src.masked, 'const shown = revealed')
    && has(src.masked, '? value')
    && !/useEffect\([^)]*setRevealed\(false\)[^)]*\[value\]/.test(src.masked),
  'revealed 只在点击时切换；value 从 A 换 B 不复位，复用行会直接显示新号码',
)

check('F13-10', '跟进弹窗 reset 不重置方式', 'CONFIRMED',
  has(src.followup, "const [followupType, setFollowupType] = useState('电话')")
    && has(src.followup, 'const reset = () => {')
    && !/reset = \(\) => \{[\s\S]{0,200}?setFollowupType\('电话'\)/.test(src.followup),
  'reset 清内容/反馈/下一动作/免填原因/请求键/时间，但没有 setFollowupType',
)

check('F13-11', '未读计数失败静默当 0', 'CONFIRMED',
  has(src.bell, 'const countQuery = useQuery')
    && has(src.bell, 'const count = countQuery.data?.count ?? 0')
    && !/countQuery\.isError/.test(src.bell),
  'countQuery 无 isError；失败 data 空，count=0，铃铛不显示角标',
)

check('F13-12', '回款凭证下载链接键盘不可达', 'CONFIRMED',
  has(src.voucher, '<a')
    && has(src.voucher, 'onClick={() => void showDownloadResult(downloadPaymentVoucher')
    && !/href=/.test(between(src.voucher, '<a', '</a>')),
  '下载入口是无 href 的 <a>，不可聚焦，键盘 Tab 到不了',
)

check('F13-13', '时间线来源类型未知拼 /undefined', 'CONFIRMED',
  has(src.timeline, "{ sample: 'samples', order: 'orders', quote: 'quotes', opportunity: 'opportunities' }[event.source.type]")
    && has(src.timeline, '查看原单'),
  '映射不到时 base=undefined，Link 变 /undefined/{id}；当前后端称四类固定，属潜在缺口',
)

check('F13-14', '自定义角色代号原样显示英文', 'CONFIRMED',
  has(src.layout, 'ROLE_LABEL[role] ?? role')
    && has(src.layout, 'join(\' / \')')
    && has(src.settings, 'createRole({')
    && has(src.settings, 'roleForm.code'),
  '自定义角色不在 ROLE_LABEL 时直接显示 code；角色管理允许新建自定义 role',
)

check('F13-15', '漏项：DecisionMaker 未传 masked，遮罩号码仍可拨号/发邮件', 'CONFIRMED',
  has(src.decision, '<MaskedContact value={primary.email} kind="email" empty="无邮箱" />')
    && has(src.decision, '<MaskedContact value={primary.mobile ?? primary.phone} empty="无电话" />')
    && !/masked=\{/.test(src.decision)
    && has(src.decision, 'disabled={!(primary.mobile ?? primary.phone)}')
    && has(src.decision, 'window.location.href = `tel:${primary.mobile ?? primary.phone}`')
    && has(src.decision, 'window.location.href = `mailto:${primary.email}`'),
  '后端 contact_masked=true 时返回星号；组件没传 masked，按钮只判 truthy，遮罩号码仍会拼 tel:/mailto:',
)

check('F13-T01', '通知 payment/approval 跳转映射（待确认）', 'PENDING',
  has(src.bell, "payment: (id) => `/orders/${id}`")
    && has(src.bell, "approval: (id) => `/quotes/${id}`")
    && has(src.followupSvc, '("order", order_id) if order_id else')
    && has(src.approvalNotify, 'business_type="quote"')
    && has(src.paymentNotify, 'business_type="order"'),
  '前端把 payment/approval 当订单/报价 id；后端 record_and_notify 有 order_id 时优先 order，审批通知用 quote、回款通知用 order，未找到实际 payment/approval 通知来源，仍只能待确认',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed/pending, ${results.filter((r) => !r.ok).length} missing`)
