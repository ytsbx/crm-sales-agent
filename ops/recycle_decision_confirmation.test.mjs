import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
// 回收决策已从系统设置搬到「客户中心 › 回收复核」，确认流程也统一成
// `notePrompt.ask`（普通批准可选说明，保护/提前/驳回/暂缓必填）。
const source = ts.createSourceFile('PoolRecyclePage.tsx', readFileSync(new URL('../frontend/src/modules/recycle/PoolRecyclePage.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
let decision
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'decide') decision = node.initializer
  ts.forEachChild(node, visit)
}
visit(source)
assert.ok(decision)
const compiled = ts.transpileModule(`exports.decide=${decision.getText(source)}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText

function load(note = '隔离说明') {
  const calls = [], prompts = [], errors = [], exports = {}
  runInNewContext(compiled, {
    exports,
    decideMutation: { mutate: (payload) => calls.push(payload) },
    notePrompt: { ask: async (options) => { prompts.push(options); return note } },
    Toast: { error: (message) => errors.push(message) },
    fmtDay: (value) => value,
  })
  return { ...exports, calls, prompts, errors }
}

test('ordinary approval asks for an optional note; cancel writes nothing', async () => {
  const t = load('已核实客户结束合作')
  await t.decide('approve', { id: 7, customer_id: 7, customer_name: '隔离客户', protection: [] })
  assert.equal(t.prompts.length, 1)
  assert.equal(t.prompts[0].required, false)
  assert.equal(t.calls.length, 1)
  assert.equal(t.calls[0].candidate.id, 7)
  assert.equal(t.calls[0].decision, 'approve')
  assert.equal(t.calls[0].note, '已核实客户结束合作')
  assert.equal(t.calls[0].early, false)

  const cancelled = load(null)
  await cancelled.decide('approve', { id: 8, customer_id: 8, customer_name: '取消客户', protection: [] })
  assert.equal(cancelled.prompts.length, 1)
  assert.equal(cancelled.calls.length, 0)
})

for (const [mode, row, options] of [
  ['approve', { id: 7, customer_id: 7, customer_name: '隔离客户', protection: ['订单履约中'] }, undefined],
  ['approve', { id: 7, customer_id: 7, customer_name: '隔离客户', protection: [], earliest_action_at: '2026-12-01T00:00:00+08:00' }, { early: true }],
  ['defer', { id: 7, customer_id: 7, customer_name: '隔离客户', protection: [] }, undefined],
  ['reject', { id: 7, customer_id: 7, customer_name: '隔离客户', protection: [] }, undefined],
]) {
  test(`${mode} ${options?.early ? 'early' : 'reason'}: note is required and the decision is submitted once`, async () => {
    const t = load('隔离原因')
    await t.decide(mode, row, options)
    assert.equal(t.prompts.length, 1)
    assert.equal(t.prompts[0].required, true)
    assert.equal(t.calls.length, 1)
    assert.equal(t.calls[0].decision, mode)
    assert.equal(t.calls[0].note, '隔离原因')
    assert.equal(t.calls[0].early, Boolean(options?.early))
  })
}
