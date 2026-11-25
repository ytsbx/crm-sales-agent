import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import ts from 'typescript'
const require = createRequire(import.meta.url)
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, filename)
const { formatBeijingTime, formatBeijingDate, formatDingTalkTime } = require('../src/shared/beijing-time.ts')
const { formatApprovalTime, formatApprovalEventTime } = require('../src/modules/knowledge/approval-progress.ts')
for (const value of ['2026-10-17T02:14:31Z', '2026-10-17T02:14:31', '2026-10-17 02:14:31', '2026-10-17T10:14:31+08:00', Date.parse('2026-10-17T02:14:31Z')]) {
  assert.match(formatBeijingTime(value), /10:14:31/, String(value))
}
assert.match(formatDingTalkTime('2026-10-17T10:14Z'), /10:14:00/)
assert.match(formatApprovalEventTime('2026-10-17T10:14Z'), /10:14:00/)
// CRM 的标准 UTC 不能套用钉钉特例；秒精度也保持标准语义。
assert.match(formatApprovalTime('2026-10-17T10:14Z'), /18:14:00/)
assert.match(formatDingTalkTime('2026-10-17T10:14:00Z'), /18:14:00/)
assert.match(formatBeijingTime('2026-10-17T18:00:00Z'), /2026\/10\/18 02:00:00/)
assert.equal(formatBeijingDate('2026-10-17'), '2026/10/17')
assert.equal(formatBeijingTime(null), '-')
assert.equal(formatBeijingTime('bad date'), '-')
assert.equal(formatApprovalEventTime(null), '尚未返回')
assert.match(formatBeijingTime('2026-10-17T02:14:31Z', { timeZone: 'America/Los_Angeles' }), /10:14:31/)
console.log(`Beijing time checks passed (browser TZ: ${process.env.TZ})`)
