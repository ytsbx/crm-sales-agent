import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const file = 'frontend/src/modules/settings/SettingsPage.tsx'

test('dictionary enable/disable waits for confirmation before mutating', () => {
  const source = ts.createSourceFile(file, readFileSync(new URL(`../${file}`, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let mutation = null
  let modal = null
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'dictionaryUpdateMutation.mutate') {
      const text = node.getText(source)
      if (text.includes('changes: { enabled')) mutation = node
    }
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'Modal.confirm') modal = node
    ts.forEachChild(node, visit)
  }
  visit(source)
  assert.ok(mutation, 'dictionary enabled mutation must exist')
  assert.ok(modal, 'dictionary toggle must open a confirmation modal')
  let parent = mutation.parent
  while (parent && !ts.isPropertyAssignment(parent)) parent = parent.parent
  assert.equal(parent?.name.getText(source), 'onOk', 'dictionary toggle must be the confirmation callback')
  assert.match(modal.getText(source), /字典项/)
})
