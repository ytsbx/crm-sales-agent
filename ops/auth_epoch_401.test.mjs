import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

/**
 * 迟到的 401 不许把新登录的账号踢下线（第十批审查 ⑩，2026-10-13）。
 *
 * ## 原来的毛病
 *
 * `client.ts` 的响应拦截器对 401 是**无条件**清登录态 + 跳 /login。于是：
 *
 *     账号 A 发一个慢请求 → A 退出 → B 登录 → A 那个请求这时才回 401
 *     → B 被踢下线，B 的查询缓存也被清空
 *
 * 弱网或慢接口下很容易撞上，用户感受是"刚登录就被弹出去"。
 *
 * ## 判据
 *
 * 请求发出时在 config 上记下 `authEpochAtSend`（登录代次，`setAuth`/`clear`
 * 都会加一）；401 回来时比对"现在还是那一代吗"。不是 → 这次失败属于上一次
 * 登录，只把错误抛给调用方，**不动登录态、不跳转**。
 *
 * 拦截器本身要跑真 axios，这里测的是从它里面抽出来的纯判据
 * （`belongsToCurrentLogin`）——判据错了拦截器一定错，判据对了拦截器只剩接线。
 */

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const source = readFileSync(
  new URL('../frontend/src/shared/api/client.ts', import.meta.url),
  'utf8',
)

/**
 * 只抠出那个纯函数来测。
 *
 * 整文件 transpile 会在 import axios / zustand store 时炸（Node 里没这些运行时），
 * 而我们要测的就是这一小段没有依赖的判据。
 */
function loadJudgement() {
  const start = source.indexOf('export function belongsToCurrentLogin')
  assert.ok(start > 0, 'client.ts 里找不到 belongsToCurrentLogin —— 判据被改名或删了？')
  const body = source.slice(start, source.indexOf('\n}', start) + 2)
  const compiled = ts.transpileModule(body, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const exports = {}
  runInNewContext(compiled, { exports, module: { exports } })
  return exports
}

test('同一代次：属于当前登录态（该清就清）', () => {
  const { belongsToCurrentLogin } = loadJudgement()
  assert.equal(belongsToCurrentLogin(3, 3), true)
})

test('★代次变了：旧账号的 401 不属于当前登录态（不许清）', () => {
  const { belongsToCurrentLogin } = loadJudgement()
  // A 登录第 1 代发出请求 → 退出（第 2 代）→ B 登录（第 3 代）
  assert.equal(belongsToCurrentLogin(1, 3), false, '迟到的 401 不该动 B 的登录态')
  // 同一账号重新登录也算换了一代
  assert.equal(belongsToCurrentLogin(1, 2), false)
})

test('没记代次（老代码/非 axios 来源）按"属于当前"处理', () => {
  const { belongsToCurrentLogin } = loadJudgement()
  // 宁可多清一次，也不要漏清 —— 漏清会让用户卡在永远 401 的界面上
  assert.equal(belongsToCurrentLogin(undefined, 7), true)
})

test('拦截器真的把代次记在请求上、并在 401 时比对（接线检查）', () => {
  // 判据对、但拦截器忘了调它，等于没修。这里静态盯住两处接线。
  assert.ok(
    /authEpochAtSend\s*=\s*currentAuthEpoch\(\)/.test(source),
    '请求拦截器没有记下发送时的登录代次',
  )
  assert.ok(
    /belongsToCurrentLogin\(sentAt,\s*currentAuthEpoch\(\)\)/.test(source),
    '401 处理没有用 belongsToCurrentLogin 比对，仍是无条件清登录态',
  )
})
