import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { webcrypto } from 'node:crypto'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const source = readFileSync(new URL('../frontend/src/shared/api/requestKey.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

for (const [name, crypto] of [
  ['HTTPS native UUID', webcrypto],
  ['LAN HTTP without randomUUID', { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) }],
  ['browser without crypto', undefined],
]) {
  test(name, () => {
    const exports = {}
    runInNewContext(compiled, { exports, crypto })
    const keys = new Set()
    for (let i = 0; i < 1000; i += 1) {
      const key = exports.newRequestKey()
      assert.match(key, uuid)
      keys.add(key)
    }
    assert.equal(keys.size, 1000)
  })
}
