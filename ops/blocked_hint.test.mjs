import assert from 'node:assert'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

/**
 * 「为什么这个按钮点不了」的判据单测。
 *
 * 主人 2026-10-12 反馈：「这种我点又点不了，又不给提示为什么点不了，这种体验很不好，
 * 类似的情况都要修」。修法是给灰按钮补两类说明（见 shared/components/BlockedHint.tsx）：
 *   ① 看得见：旁边一行常驻灰字，说清缺什么；
 *   ② 点得动：灰着的时候点一下弹一句同样的话。
 * 这里测的就是这两条文案背后的**纯逻辑** —— 它一旦算错，用户看到的就又是
 * 「没有原因」或者「一句空白」，而那正是要被修掉的毛病，所以它必须有断言。
 *
 * 手法照 `ops/request_key.test.mjs`：直接编译前端 TS 源码，在 vm 里跑，
 * 不用装浏览器、不用装测试框架（Node 自带 node:test）。CI 里跟它一起跑。
 *
 * 跑法：
 *   cd frontend && node --test ../ops/blocked_hint.test.mjs
 */

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const source = readFileSync(
  new URL('../frontend/src/shared/components/blocked-hint-reasons.ts', import.meta.url),
  'utf8',
)
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

/** 每次取一份新的模块实例，避免用例之间串状态。 */
function load() {
  const exports = {}
  runInNewContext(compiled, { exports, module: { exports } })
  return exports
}

test('blockedReasons：条件满足的不算原因，只留没满足的，且保持传入顺序', () => {
  const { blockedReasons } = load()
  assert.deepEqual(
    blockedReasons([
      { ok: true, reason: '已经选好了' },
      { ok: false, reason: '至少要勾选一行明细' },
      { ok: false, reason: '有 2 行没有填数量' },
    ]),
    ['至少要勾选一行明细', '有 2 行没有填数量'],
  )
})

test('blockedReasons：全满足 = 空数组（调用方据此不渲染任何东西）', () => {
  const { blockedReasons } = load()
  assert.deepEqual(blockedReasons([{ ok: true, reason: '不该出现' }]), [])
  assert.deepEqual(blockedReasons([]), [])
})

test('blockedReasons：数据还没到时（undefined / null）不冒原因', () => {
  const { blockedReasons } = load()
  assert.deepEqual(blockedReasons(undefined), [])
  assert.deepEqual(blockedReasons(null), [])
})

test('blockedReasons：空串与纯空格的文案不算原因（空白提示等于没有提示）', () => {
  const { blockedReasons } = load()
  assert.deepEqual(
    blockedReasons([
      { ok: false, reason: '' },
      { ok: false, reason: '   ' },
      { ok: false, reason: '\n' },
      { ok: false, reason: '这句是真的' },
    ]),
    ['这句是真的'],
  )
})

test('blockedReasons：坏数据不炸（null 项 / 文案不是字符串）', () => {
  const { blockedReasons } = load()
  assert.deepEqual(
    blockedReasons([null, undefined, { ok: false, reason: undefined }, { ok: false, reason: 42 }]),
    [],
  )
})

test('distinctReasons：同一句话只留一条，并保持第一次出现的顺序', () => {
  const { distinctReasons } = load()
  assert.deepEqual(distinctReasons(['缺数量', '缺数量', '没勾选', '缺数量']), ['缺数量', '没勾选'])
  assert.deepEqual(distinctReasons([]), [])
})

test('blockedToastText：第一条原因 + 带动作起头', () => {
  const { blockedToastText } = load()
  assert.equal(
    blockedToastText(['至少要勾选一行明细', '有 2 行没有填本次下单数量'], '建立订单草稿'),
    '还不能建立订单草稿：至少要勾选一行明细（另有 1 条原因，见弹窗底部的说明）',
  )
})

test('blockedToastText：只有一条原因时不写「另有 N 条」', () => {
  const { blockedToastText } = load()
  assert.equal(
    blockedToastText(['至少要勾选一行明细'], '建立订单草稿'),
    '还不能建立订单草稿：至少要勾选一行明细',
  )
})

test('blockedToastText：没有原因 = 空串（可用态冒泡上来时绝不能弹提示条）', () => {
  const { blockedToastText } = load()
  assert.equal(blockedToastText([], '建立订单草稿'), '')
  assert.equal(blockedToastText(['', '  '], '建立订单草稿'), '')
})

test('blockedToastText：「另有 N 条」数的是去重后的条数（8 行同款缺失不该说成 7 条）', () => {
  const { blockedToastText } = load()
  const same = '有 1 行没有填本次下单数量（数量必须大于 0）；来源没记原采购数量的行需要你手动填。'
  assert.equal(
    blockedToastText([same, same, same, same, same, same, same, same], '建立订单草稿'),
    `还不能建立订单草稿：${same}`,
  )
})

test('blockedToastText：不带动作时也能说清楚（不是「：原因」这种残句）', () => {
  const { blockedToastText } = load()
  assert.equal(blockedToastText(['至少要勾选一行明细']), '至少要勾选一行明细')
})

test('文案纪律：这几个文件里下发给用户的原因不许出现 markdown 星号', () => {
  // 项目踩过一次：文案带了 `**加粗**`，原样露在页面上，是**截图**才发现的
  // （见 .workbuddy/memory/2026-10-06.md 的记载）。强调一律用「」。
  //
  // 这里扫的是**源文件里的字符串字面量**，不是运行结果 —— 星号本来就该在
  // 写代码时就不写进去，而不是靠事后替换（事后替换会留下半截字符）。
  const files = [
    '../frontend/src/shared/components/blocked-hint-reasons.ts',
    '../frontend/src/shared/components/BlockedHint.tsx',
    '../frontend/src/modules/sample/SampleFromSourceModal.tsx',
  ]
  for (const file of files) {
    const text = readFileSync(new URL(file, import.meta.url), 'utf8')
    // 只看单引号/双引号字符串里的成对星号（注释与正则不算）
    const offenders = [...text.matchAll(/(['"])((?:[^'"\\\n]|\\.)*)\1/g)]
      .map((match) => match[2])
      .filter((literal) => /\*\*[^*]+\*\*/.test(literal))
    assert.deepEqual(offenders, [], `${file} 的用户文案里不该有 markdown 星号`)
  }
})
