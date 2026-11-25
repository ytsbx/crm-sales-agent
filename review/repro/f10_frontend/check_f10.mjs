/**
 * F10 企微 / 任务 / 回收站复核脚本。
 * 运行：node review/repro/f10_frontend/check_f10.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  wecom: 'frontend/src/modules/wecom/WeComPage.tsx',
  wecomApi: 'frontend/src/shared/api/wecom.ts',
  wecomRouter: 'backend/app/modules/wecom/router.py',
  wecomService: 'backend/app/modules/wecom/service.py',
  task: 'frontend/src/modules/task/TaskListPage.tsx',
  taskApi: 'frontend/src/shared/api/task.ts',
  taskRouter: 'backend/app/modules/task/router.py',
  recycle: 'frontend/src/modules/recycle/RecycleBinPage.tsx',
  pool: 'frontend/src/modules/recycle/PoolRecyclePage.tsx',
  router: 'frontend/src/app/router.tsx',
  customerService: 'backend/app/modules/customer/service.py',
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

check('F10-01', '企微候选/建客/绑客绕过客户权限', 'CONFIRMED',
  has(src.wecom, "const canManage = can('wecom:manage')")
    && has(src.wecom, "const canSearchCustomers = canManage && can('customer:view')")
    && has(src.wecom, 'enabled: Boolean(activeId),')
    && has(src.wecom, '{(candidatesQuery.data?.candidates ?? []).map((candidate, index) => (')
    && has(src.wecom, 'candidate.name')
    && has(src.wecom, '{canManage && active && (')
    && has(src.wecom, '创建新客户')
    && has(src.wecomRouter, 'async def unbound_candidates')
    && has(src.wecomRouter, 'require_permission("wecom:view")')
    && has(src.wecomRouter, 'require_wecom_action("wecom:manage")')
    && has(src.wecomService, '这里**故意不做数据范围过滤**'),
  '前端候选查询只按 activeId；候选卡无条件渲染；建客/绑客只门 canManage。后端候选只要 wecom:view，建/绑只要 wecom:manage',
)

check('F10-02', '待确认列表失败显示“没有待确认客户”', 'CONFIRMED',
  has(src.wecom, 'const rows = listQuery.data?.items ?? []')
    && has(src.wecom, "listQuery.isLoading ? '加载中…' : '没有待确认客户'")
    && !/listQuery\.isError/.test(src.wecom),
  'rows 兜底空数组；Empty 只判 isLoading，无 isError/重试',
)

check('F10-03', '新建任务无请求键', 'CONFIRMED',
  has(src.taskApi, 'export function createTask(payload: Record<string, unknown>)')
    && has(src.task, 'const createMutation = useMutation')
    && has(src.task, 'createTask({')
    && !/request_key/.test(src.taskApi)
    && !/request_key/.test(between(src.task, 'const createMutation', 'const postponeMutation')),
  '前端 createTask 无键；后端 POST /tasks 直接 TaskCreate 落库，无幂等记录',
)

check('F10-04', '勾选跨筛选/跨页保留并一起提交', 'CONFIRMED',
  has(src.task, 'const [selectedVersions, setSelectedVersions] = useState<Record<number, string>>({})')
    && has(src.task, 'const selectedIds = Object.keys(selectedVersions).map(Number)')
    && has(src.task, 'mutationFn: () => batchCompleteTasks(selectedIds, undefined, selectedVersions)')
    && has(src.task, 'mutationFn: () => batchHandoverTasks(selectedIds, transferOwner!)')
    && !/function setFilter[\s\S]{0,120}?clearSelection/.test(src.task)
    && /onChange=\{\(value\) => \{\n              setFilter\('status'/.test(src.task)
    && /if \(handover\) clearSelection\(\)/.test(src.task),
  'setFilter/普通页翻页都不清 selectedVersions；批量完成/交接直接用全部 selectedIds',
)

check('F10-05', '待办交接目标不限部门/范围', 'CONFIRMED',
  has(src.taskRouter, 'stmt = select(User).where(User.status == "active")')
    && has(src.taskRouter, 'async def handover_candidates')
    && has(src.taskRouter, 'target = await _active_owner(session, payload.owner_id)')
    && has(src.taskRouter, 'async def batch_handover_tasks')
    && !/handover_candidates[\s\S]{0,400}?department/.test(src.taskRouter)
    && !/target = await _active_owner[\s\S]{0,100}?scoped_owner_ids/.test(src.taskRouter),
  '候选人 SQL 只筛 active；batch 对目标只 _active_owner（存在+在职），不校验部门/数据范围',
)

check('F10-06', '企微单条建客/绑客无请求键', 'CONFIRMED',
  has(src.wecomApi, 'export function bindUnboundContact(')
    && has(src.wecomApi, 'export function createCustomerFromContact(')
    && !/request_key/.test(between(src.wecomApi, 'export function bindUnboundContact', 'export function ignoreUnboundContact'))
    && !/request_key/.test(between(src.wecomApi, 'export function createCustomerFromContact', 'export function'))
    && has(src.wecomService, '这条联系人已经归一过了'),
  'API 无键；后端靠 crm_contact_id 行锁防二次，但第二次 422，前端没有“上次已成功”的补偿',
)

check('F10-07', '独立 /pool-recycle 无权限壳', 'CONFIRMED',
  has(src.router, "{ path: 'pool-recycle', element: <PoolRecyclePage /> }")
    && !/pool-recycle[\s\S]{0,80}?permission/.test(src.router)
    && has(src.pool, "const canRestore = can('customer:pool_review') && can('customer:pool_restore')")
    && has(src.pool, '批准回收')
    && has(src.pool, '暂缓')
    && has(src.pool, '驳回')
    && !/const canReview = can\('customer:pool_review'\)/.test(src.pool),
  '路由直接渲染页面；页面只有 canRestore，没有 canReview；批准/暂缓/驳回与批量条无条件渲染',
)

check('F10-08', '线索窄屏删除人待核实显示成 —', 'CONFIRMED',
  has(src.recycle, "record.deleted_by_name ?? '—'")
    && has(src.recycle, 'renderDeletedBy(v, record.deleted_by_pending)')
    && has(src.recycle, '历史操作人待核实'),
  '窄屏分支直接 deleted_by_name ?? "—"，没看 deleted_by_pending；宽屏/其他表有“待核实”',
)

check('F10-09', '待确认列表下一页只看 rows.length', 'CONFIRMED',
  has(src.wecom, 'disabled={rows.length < 20}')
    && has(src.wecom, 'setPage((current) => current + 1)')
    && !/total[\s\S]{0,80}page_size/.test(between(src.wecom, 'const rows = listQuery', 'const active')),
  '没有 total；最后一页正好 20 条时仍允许下一页，翻出空页显示“没有待确认客户”',
)

check('F10-10', '恢复客户新负责人可跨部门（待业务确认）', 'PENDING',
  has(src.recycle, "listUsers({ status: 'active', keyword: ownerKeyword, page: pageParam, page_size: 20 })")
    && !/department_id/.test(between(src.recycle, 'const usersQuery = useInfiniteQuery', 'const ownerOptions'))
    && /target = await session\.get\(User, new_owner_id\)/.test(src.customerService)
    && /if old_owner_id == new_owner_id/.test(src.customerService)
    && !/target\.department_id|target\.data_scope/.test(src.customerService),
  '前端只按 active 拉人；后端只校验存在/在职。是否允许跨部门由业务口径决定，待确认',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed/pending, ${results.filter((r) => !r.ok).length} missing`)
