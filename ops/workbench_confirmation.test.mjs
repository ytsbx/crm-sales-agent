import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const source = readFileSync(new URL('../frontend/src/modules/workbench/WorkbenchPage.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText

function render({ fail = false } = {}) {
  const calls = { completed: [], refreshed: 0, errors: [] }
  const task = { id: 7, title: '需确认的任务', due_at: new Date().toISOString(), priority: 'normal' }
  const jsx = (type, props) => ({ type, props: props ?? {} })
  const components = new Proxy({}, { get: (_, name) => name })
  const query = ({ queryKey }) => ({
    data: queryKey[0] === 'dashboard-tasks' ? [task]
      : queryKey[0] === 'dashboard-summary' ? {}
        : queryKey[0] === 'dashboard-team' ? { is_team_view: false }
          : queryKey[0] === 'workbench-opportunities' ? { items: [] } : [],
    refetch: async () => { calls.refreshed++ },
  })
  const mocks = {
    'react/jsx-runtime': { jsx, jsxs: jsx },
    react: { useState: (value) => [value, () => {}] },
    '@tanstack/react-query': { useQuery: query },
    '@douyinfe/semi-ui': { ...Object.fromEntries(['Banner', 'Button', 'Checkbox', 'Pagination', 'Popconfirm', 'SideSheet', 'Tag'].map(name => [name, name])),
      Toast: { success() {}, error(message) { calls.errors.push(message) } } },
    'react-router-dom': { Link: 'Link', useNavigate: () => () => {} },
    '../../shared/store/auth': { useAuthStore: selector => selector({ user: { name: '测试用户' } }) },
    '../../shared/store/copilot': { useCopilotStore: selector => selector({ openWith() {} }) },
    '../../shared/hooks/permissions': { usePermissions: () => ({ can: () => true }) },
    '../../shared/beijing-time': { formatBeijingDate: () => '2026-10-19', formatBeijingClock: () => '12', formatBeijingTime: () => '12:00' },
    '../../shared/api/task': { completeTask: async id => {
      calls.completed.push(id)
      if (fail) throw new Error('完成失败')
    } },
  }
  const exports = {}
  runInNewContext(compiled, { exports, module: { exports }, require: name => mocks[name] ?? components, Date })
  return { tree: exports.default(), calls }
}

function find(node, predicate) {
  if (!node || typeof node !== 'object') return undefined
  if (Array.isArray(node)) return node.map(child => find(child, predicate)).find(Boolean)
  if (predicate(node)) return node
  return find(node.props?.children, predicate)
}

for (const fail of [false, true]) {
  test(`workbench completion requires confirmation and refreshes after ${fail ? 'failure' : 'success'}`, async () => {
    const { tree, calls } = render({ fail })
    const gate = find(tree, node => node.type === 'Popconfirm' && find(node.props.children, child => child.type === 'Checkbox'))
    assert.ok(gate, 'today task checkbox must be protected by confirmation')
    const checkbox = find(gate.props.children, node => node.type === 'Checkbox')
    await checkbox.props.onChange?.({ target: { checked: true } })
    assert.equal(calls.completed.length, 0, 'opening confirmation must not complete')
    await gate.props.onCancel?.()
    assert.equal(calls.completed.length, 0, 'cancelling must not complete')
    await gate.props.onConfirm()
    assert.deepEqual(calls.completed, [7])
    assert.equal(calls.refreshed, 2)
    assert.equal(calls.errors.length, fail ? 1 : 0)
  })
}
