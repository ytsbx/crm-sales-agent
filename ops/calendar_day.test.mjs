import assert from 'node:assert'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

/**
 * 日历日期转换的单测（第九批问题 ②）。
 *
 * 判据只有一条：**用户点哪一天，就送哪一天**。
 * 原来全仓用 `toISOString().slice(0, 10)`（UTC 截日），当地零点的日期会早一天
 * —— 北京选出 10 月 1 日，送出去是 9 月 30 日（跨月还会带错月份）。
 */

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const source = readFileSync(new URL('../frontend/src/shared/calendar-day.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function load() {
  const exports = {}
  runInNewContext(compiled, { exports, module: { exports } })
  return exports
}

test('当地零点不会退一天（这正是原来的 bug）', () => {
  const { calendarDay } = load()
  // 本机时区 Asia/Shanghai（UTC+8）：当地 10/1 零点 → toISOString 会变 09-30
  assert.equal(calendarDay(new Date(2026, 9, 1, 0, 0, 0)), '2026-10-01')
  assert.equal(calendarDay(new Date(2026, 9, 1, 23, 59, 59)), '2026-10-01')
  assert.equal(calendarDay(new Date(2026, 0, 1, 0, 0, 0)), '2026-01-01', '跨年也要对')
})

test('月份与日期补零', () => {
  const { calendarDay } = load()
  assert.equal(calendarDay(new Date(2026, 0, 5)), '2026-01-05')
  assert.equal(calendarDay(new Date(2026, 11, 31)), '2026-12-31')
})

test('空值与非法日期返回 null（不抛、也不编一个今天）', () => {
  const { calendarDay } = load()
  assert.equal(calendarDay(null), null)
  assert.equal(calendarDay(undefined), null)
  assert.equal(calendarDay(new Date('不是日期')), null)
})

test('calendarDayOrToday：必须给值时回落到今天，且同样是当地日期', () => {
  const { calendarDayOrToday, calendarDay } = load()
  assert.equal(calendarDayOrToday(null), calendarDay(new Date()))
  assert.equal(calendarDayOrToday(new Date(2026, 9, 1, 0, 0, 0)), '2026-10-01')
})
