import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const source = readFileSync(new URL('../frontend/src/shared/payment-allocation.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const exports = {}; runInNewContext(compiled, { exports, module: { exports } })
const fill = (...args) => JSON.parse(JSON.stringify(exports.fillPaymentByDueDate(...args)))
const plan = (id, amount, received_amount=0, status='pending') => ({id,amount,received_amount,status,due_date:`2026-10-${10+id}`})
test('rebuilding the same receipt leaves it in the first installment', () => {
  assert.deepEqual(fill(100,[plan(1,100,100),plan(2,100)],[{plan_id:1,amount:100}]),{amounts:{1:'100.00'},unallocated:0})
})
test('other receipts stay occupied; rebuild is stable when repeated', () => {
  assert.deepEqual(fill(100,[plan(1,100,100),plan(2,100,50)],[{plan_id:1,amount:50},{plan_id:2,amount:50}]),{amounts:{1:'50.00',2:'50.00'},unallocated:0})
})
test('cent tail goes to next installment without loss', () => {
  assert.deepEqual(fill(.03,[plan(2,.02),plan(1,.01)],[]),{amounts:{1:'0.01',2:'0.02'},unallocated:0})
})
test('cancelled installments are not candidates; surplus stays unallocated', () => {
  assert.deepEqual(fill(100,[plan(1,100,0,'cancelled'),plan(2,50)],[]),{amounts:{2:'50.00'},unallocated:50})
})
test('no plans / zero payment does not invent allocations', () => {
  assert.deepEqual(fill(100,[],[]),{amounts:{},unallocated:100})
  assert.deepEqual(fill(0,[plan(1,100)],[]),{amounts:{},unallocated:0})
})
test('invalid amounts are rejected', () => {
  for (const amount of [-1,Infinity,NaN]) assert.throws(()=>fill(amount,[],[]))
})
test('maximum legal amount uses exact strings and keeps the final cent', () => {
  assert.deepEqual(fill('99999999999999.99', [{...plan(1,99999999999999.98),amount_exact:'99999999999999.99',received_amount_exact:'99999999999999.99'}], [{plan_id:1,amount:99999999999999.98,amount_exact:'99999999999999.99'}]), {amounts:{1:'99999999999999.99'},unallocated:0})
})
test('sub-cent amounts must be rejected rather than rounded down', () => {
  assert.throws(()=>fill('0.001',[],[]))
})

const moneySource = readFileSync(new URL('../frontend/src/shared/components/money.ts', import.meta.url), 'utf8')
const moneyCompiled = ts.transpileModule(moneySource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const moneyExports = {}; runInNewContext(moneyCompiled, { exports: moneyExports, module: { exports: moneyExports } })
test('exact monetary strings retain negative fractions and cent precision', () => {
  for (const [value, expected] of [
    ['-0.01', '¥-0.01'], ['-0.99', '¥-0.99'], ['-0.0100', '¥-0.0100'],
    ['-000.01', '¥-0.01'], ['-1.01', '¥-1.01'], ['0.01', '¥0.01'],
    ['0', '¥0'], ['99999999999999.99', '¥99,999,999,999,999.99'],
    ['-99999999999999.99', '¥-99,999,999,999,999.99'],
  ]) assert.equal(moneyExports.formatMoney(value, 'CNY'), expected)
  assert.equal(moneyExports.formatMoney(null, 'CNY'), '-')
  assert.equal(moneyExports.formatMoney(-0.01, 'CNY'), '¥-0.01')
})
