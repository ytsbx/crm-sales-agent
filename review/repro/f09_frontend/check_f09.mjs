/**
 * F09 设置与审计复核脚本。
 * 运行：node review/repro/f09_frontend/check_f09.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  settings: 'frontend/src/modules/settings/SettingsPage.tsx',
  audit: 'frontend/src/modules/settings/AuditLogPanel.tsx',
  profile: 'frontend/src/modules/settings/PersonalProfilePanel.tsx',
  settingsApi: 'frontend/src/shared/api/settings.ts',
  roleSchema: 'backend/app/modules/user/schema.py',
  tags: 'backend/app/modules/customer/tags.py',
}
const raw = Object.fromEntries(Object.entries(F).map(([k, rel]) => [k, readFileSync(resolve(ROOT, rel), 'utf8')]))
const results = []
const check = (id, name, verdict, ok, evidence) => results.push({ id, name, verdict, ok: Boolean(ok), evidence })
const between = (text, start, end) => {
  const i = text.indexOf(start)
  if (i < 0) return ''
  const j = text.indexOf(end, i + start.length)
  return j < 0 ? text.slice(i) : text.slice(i, j)
}
const has = (text, s) => text.includes(s)
const noQueryError = (text, names) => names.every((n) => !new RegExp(`${n}\\\\.isError`).test(text))

check('F09-01', '设置页列表失败当空态', 'CONFIRMED',
  noQueryError(raw.settings, ['usersQuery', 'rolesQuery', 'deptsQuery', 'poolRulesQuery', 'dictionariesQuery', 'tagsQuery'])
    && has(raw.settings, 'dataSource={usersQuery.data?.items ?? []}')
    && has(raw.settings, "empty={\n                  userKeyword || userStatus || userDeptId\n                    ? '没有符合条件的用户，试试放宽筛选条件'\n                    : '还没有用户'")
    && has(raw.settings, "empty={roleKeyword ? '没有匹配的角色' : '还没有角色'}")
    && has(raw.settings, "empty={deptKeyword ? '没有匹配的部门' : '还没有部门'}")
    && has(raw.settings, 'dataSource={tagsQuery.data ?? []}')
    && has(raw.settings, 'dataSource={(dictionariesQuery.data ?? []).length}') === false
    && has(raw.settings, 'empty="还没有数据字典项"'),
  'users/roles/depts/poolRules/dictionaries/tags 六个查询都没有 isError；失败时 data ?? [] 直接落“还没有…”空态',
)

check('F09-02', '编辑标签不提交 status', 'CONFIRMED',
  raw.settings.includes('updateTag(tagEditTarget!.id, { name: tagEditTarget!.name, type: tagEditTarget!.type })')
    && raw.settings.includes('value={tagEditTarget.status}')
    && raw.settings.includes('status: value as string')
    && raw.tags.includes('for field in ("status", "sort_no")'),
  'mutation 只传 name/type；弹窗有 status Select 并写入 tagEditTarget；后端 update_tag 明确支持 status',
)

check('F09-03', '新建角色状态不提交', 'CONFIRMED',
  /: createRole\(\{[\s\S]{0,180}?permission_codes: roleForm\.permission_codes,[\s\S]{0,30}?\}\)/.test(raw.settings)
    && !/createRole\(\{[\s\S]{0,200}?status: roleForm\.status/.test(raw.settings)
    && /class RoleCreate\(CreateModel\):[\s\S]{0,260}?permission_codes/.test(raw.roleSchema)
    && !/class RoleCreate\(CreateModel\):[\s\S]{0,180}?status/.test(raw.roleSchema)
    && raw.settings.includes('value={roleForm.status}'),
  'createRole payload 无 status；RoleCreate 无 status 且 extra=ignore；弹窗对新建/编辑共用同一个状态 Select',
)

check('F09-04', '审批分档金额上限 Number 解析丢分', 'CONFIRMED',
  raw.settings.includes('const AMOUNT_TEXT_PATTERN = /^\\d+(\\.\\d{1,2})?$/')
    && raw.settings.includes('const parsed = Number(text)')
    && raw.settings.includes('max_amount: parsed')
    && raw.settings.includes('prevMax = parsed')
    && Number('99999999999999.99') === 99999999999999.98,
  `正则允许 16 位；Number('99999999999999.99') -> ${Number('99999999999999.99')}，JSON 发到后端已不是原文`,
)

check('F09-05', '公海规则天数/开关全字段并发覆盖', 'CONFIRMED',
  /updatePublicPoolRule\(id, \{ days, enabled \}\)/.test(raw.settings)
    && /poolRuleMutation\.mutate\(\{ id: record\.id, days: next, enabled: record\.enabled \}\)/.test(raw.settings)
    && /poolRuleMutation\.mutate\(\{ id: record\.id, days: record\.days, enabled: value \}\)/.test(raw.settings)
    && !/poolRuleMutation\.isPending[\s\S]{0,100}disabled/.test(raw.settings),
  '失焦发 {days:新, enabled:旧}，开关发 {days:旧, enabled:新}；两个 PATCH 都带全字段，后到覆盖先到',
)

check('F09-06', '审计失败显示“没有记录”且保留旧页', 'CONFIRMED',
  /const rows = query\.data\?\.items \?\? \[\]/.test(raw.audit)
    && /const total = query\.data\?\.total \?\? 0/.test(raw.audit)
    && /placeholderData: \(prev\) => prev/.test(raw.audit)
    && /empty="没有符合条件的记录"/.test(raw.audit)
    && !/query\.isError/.test(raw.audit),
  '无 isError；失败时 rows 为空/保留旧 placeholder，表格显示“没有符合条件的记录”或上一筛选的旧行',
)

check('F09-07', '审计明细直接摊原始 JSON', 'CONFIRMED',
  /JSON\.stringify\(detail\.before_data, null, 2\)/.test(raw.audit)
    && /JSON\.stringify\(detail\.after_data, null, 2\)/.test(raw.audit)
    && /<pre style=\{preStyle\}>/.test(raw.audit),
  'SideSheet 用 <pre> 直接展示 before/after 原始 JSON；与 2026-11-16“前端不展示原始 JSON”的固定约束冲突',
)

check('F09-08', '权限查询失败显示“没有任何权限”', 'CONFIRMED',
  /const owned = permQuery\.data\?\.permissions \?\? \[\]/.test(raw.profile)
    && /!permQuery\.isLoading && owned\.length === 0/.test(raw.profile)
    && /你还没有任何权限/.test(raw.profile)
    && !/permQuery\.isError/.test(raw.profile),
  '无 isError；失败时 owned=[]，直接进入“你还没有任何权限，请联系管理员分配角色”',
)

check('F09-09', '部门上级下拉只排除自己', 'CONFIRMED',
  /optionList=\{deptOptions\.filter\(\(d\) => d\.value !== deptEditTarget\?\.id\)\}/.test(raw.settings)
    && /不能选它自己，也不能选它的下级/.test(raw.settings),
  '只过滤当前部门自身，未过滤后代；后端 _assert_no_cycle 才拒绝，界面承诺与实现不一致',
)

check('F09-10', '公海天数非法值不回填', 'CONFIRMED',
  /defaultValue=\{String\(days\)\}/.test(raw.settings)
    && /if \(!raw \|\| !Number\.isInteger\(next\) \|\| next <= 0\) \{[\s\S]{0,80}?Toast\.warning\('公海回收天数须为正整数'\)[\s\S]{0,40}?return/.test(raw.settings)
    && !/event\.target\.value = String\(days\)/.test(raw.settings),
  'defaultValue 非受控；校验失败只 Toast + return，输入框仍显示 0/-1/1.5',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed, ${results.filter((r) => !r.ok).length} missing`)
