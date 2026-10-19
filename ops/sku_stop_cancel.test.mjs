import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const file = process.env.SKU_TEST_SOURCE ?? new URL('../frontend/src/modules/product/ProductDetailPage.tsx', import.meta.url)
const source = ts.createSourceFile('ProductDetailPage.tsx', readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
let options
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'identityActionMutation') options = node.initializer.arguments[0]
  ts.forEachChild(node, visit)
}
visit(source)
assert.ok(options)
const compiled = ts.transpileModule(`exports.handlers = ${options.getText(source)}`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

for (const note of [null, '', '停止从旧系统导入']) {
  test(`stop source: ${note === null ? 'cancel leaves data unchanged' : 'confirm submits optional note'}`, async () => {
    const calls = [], messages = [], refreshes = []
    const exports = {}
    runInNewContext(compiled, { exports,
      window: { prompt: () => note },
      stopSkuIdentity: async (...args) => { calls.push(args); return { id: 7 } },
      Toast: { success: message => messages.push(message), error: message => messages.push(message) },
      queryClient: { invalidateQueries: args => refreshes.push(args) }, masterSku: { id: 3 },
    })
    const result = await exports.handlers.mutationFn({ action: 'stop', source: { id: 7 } })
    exports.handlers.onSuccess(result)
    if (note === null) {
      assert.equal(calls.length, 0)
      assert.equal(messages.length, 0)
      assert.equal(refreshes.length, 0)
    } else {
      assert.deepEqual(calls, [[7, note]])
      assert.equal(messages.length, 1)
      assert.equal(refreshes.length, 2)
    }
  })
}
