import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const file = 'frontend/src/modules/approval/RulesPanel.tsx'

test('approval rule enable/disable waits for confirmation before mutating', () => {
  const source = ts.createSourceFile(file, readFileSync(new URL(`../${file}`, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let mutation = null
  let modal = null
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'toggleMutation.mutate') mutation = node
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'Modal.confirm') modal = node
    ts.forEachChild(node, visit)
  }
  visit(source)
  assert.ok(mutation, 'toggle mutation must exist')
  assert.ok(modal, 'toggle must open a confirmation modal')
  let parent = mutation.parent
  while (parent && !ts.isPropertyAssignment(parent)) parent = parent.parent
  assert.equal(parent?.name.getText(source), 'onOk', 'toggle mutation must be the confirmation callback')
  assert.match(modal.getText(source), /审批规则/) 
})
