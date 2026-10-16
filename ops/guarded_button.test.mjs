import assert from 'node:assert'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

/**
 * `GuardedButton`：「灰掉时自己解释为什么」的按钮。
 *
 * ## 为什么要加这一层（2026-10-15）
 *
 * 「灰按钮要解释」这条规矩 `BlockedHint` 早就定了，但**没人用**：全项目 35 个
 * 文件里有禁用控件，只有 19 个引了它，新写的页面基本都漏 —— 2026-10-15 一轮就
 * 给三个文件补了例外登记（`PersonalAccountPanel` / `UnratedRulesPanel` /
 * `SampleDingTalkModal`）。原因在**摩擦**：照现有写法一个灰按钮要写八行、三层、
 * 还要自己记住 `pointerEvents: 'none'` 和 CSS 类名，而写新页面的人只会写 `<Button>`。
 *
 * `GuardedButton` 把那八行压成一个标签。但"压成一个标签"要成立，
 * **两条规矩必须由它自己保证**，页面不用操心：
 *
 *   ① **看得见**：禁用时按钮旁常驻一行灰字，说清缺什么；
 *   ② **点得动**：禁用时点一下要有反应（弹提示条），不是石沉大海；
 *   ③ 而且——**可用态绝不能吞掉点击**（"能点也不响应"比不解释更糟）。
 *
 * 这里就守这三条。手法：`shouldInterceptClick` 是纯函数直接测；
 * 渲染用 `react-dom/server` 真渲染出 HTML 再断言（比断源码字符串强）——
 * 用了 SSR 就不必装 jsdom/浏览器，CI 里跟 `blocked_hint.test.mjs` 一起跑。
 *
 * 跑法：
 *   cd frontend && node --test ../ops/guarded_button.test.mjs
 */

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')

/**
 * 把 Semi UI 打桩。
 *
 * `@douyinfe/semi-ui` 的 CJS 产物在 Node 里直接 `require` 会抛
 * `SyntaxError: Unexpected token ':'`（它的构建产物不是给 Node 用的）。
 * 而本文件要测的是 **`GuardedButton` 自己的行为**（样式、要点、渲染），
 * 不是 Semi 的实现 —— 所以把 Button 换成一个把 props 摊到原生 `<button>` 上的桩，
 * 断言反而更准（`pointer-events` 与 `disabled` 都能直接读出来）。
 *
 * 另外这样就不必为测试装 jsdom / React 测试栈：`react` 与 `react-dom/server`
 * 都能正常 `require`，真渲染出 HTML 再断言。
 */
const ButtonStub = ({ children, ...props }) =>
  require('react').createElement('button', props, children)
const ToastStub = { warning: () => {}, success: () => {}, error: () => {} }
const SEMI_STUB = { Button: ButtonStub, Toast: ToastStub }
const SOURCE = readFileSync(
  new URL('../frontend/src/shared/components/GuardedButton.tsx', import.meta.url),
  'utf8',
)

/** 把源码编成 CommonJS 并在 vm 里跑（React 等依赖从真实 node_modules 取）。 */
function loadModule() {
  const compiled = ts.transpileModule(SOURCE, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText
  const exports = {}
  const module = { exports }
  const req = (id) => {
    // 源码里的相对导入：只用到 BlockedHint 与 blocked-hint-reasons，
    // 这两个本身能跑（它们是纯 TS/TSX），所以照常解析。
    if (id === './BlockedHint') return requireBlockedHint()
    if (id === './blocked-hint-reasons') return requireReasons()
    if (id === '@douyinfe/semi-ui') return SEMI_STUB
    return require(id)
  }
  runInNewContext(compiled, { exports, module, require: req, console })
  return module.exports
}

/** 编译任意前端 TS/TSX 源码并取导出（同一套 vm 手法）。 */
function loadTsx(relPath, resolveMap = {}) {
  const source = readFileSync(new URL(`../frontend/src/${relPath}`, import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText
  const exports = {}
  const module = { exports }
  runInNewContext(compiled, {
    exports,
    module,
    console,
    require: (id) =>
      resolveMap[id] ? resolveMap[id]() : id === '@douyinfe/semi-ui' ? SEMI_STUB : require(id),
  })
  return module.exports
}

let reasonsCache = null
function requireReasons() {
  if (!reasonsCache) reasonsCache = loadTsx('shared/components/blocked-hint-reasons.ts')
  return reasonsCache
}

let blockedHintCache = null
function requireBlockedHint() {
  if (!blockedHintCache) {
    blockedHintCache = loadTsx('shared/components/BlockedHint.tsx', {
      './blocked-hint-reasons': requireReasons,
    })
  }
  return blockedHintCache
}

let mod = null
function load() {
  if (!mod) mod = loadModule()
  return mod
}

// ------------------------------------------------------------------ 纯逻辑

test('被挡住时拦下点击；可用态必须放过去', () => {
  const { shouldInterceptClick } = load()
  assert.equal(shouldInterceptClick(true), true, '挡住了却放过去 → 点了石沉大海')
  // 这条最要紧：可用态返回 true 会让"能点的按钮点了没反应"，比不解释更糟
  assert.equal(shouldInterceptClick(false), false, '可用态拦点击 → 能点也不响应')
})

// ------------------------------------------------------------------ 真渲染

const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

function render(props = {}, children = '保存') {
  const { default: GuardedButton } = load()
  return renderToStaticMarkup(React.createElement(GuardedButton, props, children))
}

test('禁用时：按钮带 pointer-events: none（点击才能冒泡去弹提示）', () => {
  const html = render({ disabled: true, blockedReasons: ['还没选客户'], action: '保存' })
  assert.match(html, /pointer-events:\s*none/, '缺了它，点击到不了外层，点了没反应')
  assert.match(html, /disabled/, '按钮本身必须是禁用的（本件不放松口径）')
})

test('禁用时：常驻一行灰字说清缺什么（规矩①：看得见）', () => {
  const html = render({ disabled: true, blockedReasons: ['还没选客户'], action: '保存' })
  assert.match(html, /还不能保存/, '缺少「还不能{动作}」这句头')
  assert.match(html, /还没选客户/, '原因没渲染出来 —— 等于没解释')
})

test('可用时：不留 pointer-events、不渲染任何解释', () => {
  const html = render({ disabled: false, blockedReasons: ['还没选客户'], action: '保存' })
  assert.doesNotMatch(html, /pointer-events/, '可用态还留着它 → 按钮点不动')
  assert.doesNotMatch(html, /还没选客户/, '可用态不该再显示"为什么不能点"')
  assert.doesNotMatch(html, /还不能保存/, '可用态不该出现被挡的说明')
})

test('禁用但没给原因：仍然禁用，但不写空话', () => {
  // 提交中的瞬时禁用就属于这一类（那种情况登记在 check_disabled_hint 的例外清单里）
  const html = render({ disabled: true })
  assert.match(html, /disabled/)
  assert.doesNotMatch(html, /还不能/)
})

test('外层容器的类名与光标语义不能改', () => {
  // `.blocked-hint-click` 负责 cursor: pointer / not-allowed（见 index.css）。
  // 改成别的类名 → 光标不再提示"这个能点/不能点"，而这正是本件要修的东西。
  const html = render({ disabled: true, blockedReasons: ['x'] })
  assert.match(html, /class="blocked-hint-click"/)
})

test('透传按钮自己的 props 与 className（不能被吃掉）', () => {
  const html = render({ disabled: false, className: 'my-cta', 'data-testid': 'cta' })
  assert.match(html, /class="my-cta"/, 'className 没透传过去')
  assert.match(html, /data-testid="cta"/, '其它 props 没透传过去')
})

test('禁用时点击落到外层容器上（按钮自己不派发 click，只能靠冒泡）', () => {
  // 这正是"点得动"（规矩②）的实现方式：禁用按钮不派发 click，
  // 所以必须在**外层**接住 —— 少了这个 onClick，点了就真的石沉大海。
  const html = render({ disabled: true, blockedReasons: ['还没选客户'] })
  const wrapper = html.slice(0, html.indexOf('>'))
  assert.match(wrapper, /blocked-hint-click/, '外层容器不见了，点击无处可接')
})

test('只能有一个根节点 —— 放进横向 flex 行时不许变成"两个格子"', () => {
  // ⚠️ 这条是 2026-10-16 主人截图「没对齐啊」抓出来的：
  // 原来它返回 `<>…<span>按钮</span>…<BlockedHint/></>` —— **两个兄弟节点**。
  // 放进 `display:flex` 的横向行里，这两个节点会各占一个 flex 格子，
  // 于是提示块被当成"第三列"摆到右边，整行就散了。
  //
  // 判据：无论禁用与否，渲染结果**只有一个顶层元素** ——
  // 数一数顶层开标签（缩进为 0 的那些）必须是 1 个。
  for (const props of [
    { disabled: true, blockedReasons: ['还没选客户'], action: '保存' },
    { disabled: true },
    { disabled: false, blockedReasons: ['还没选客户'], action: '保存' },
  ]) {
    const html = render(props)
    const topLevel = html.match(/^<[a-z]+/g) ?? []
    assert.equal(
      topLevel.length,
      1,
      `顶层元素应当只有 1 个，实际 ${topLevel.length} 个 → 放进 flex 行里会错位：${html.slice(0, 120)}`,
    )
  }
})

test('按钮与提示在**同一个容器**里（竖着排），不是兄弟节点', () => {
  // 同一个根因的另一面：提示块必须在根容器**内部**，
  // 这样它才会跟着按钮一起当"一个元素"参与布局。
  const html = render({ disabled: true, blockedReasons: ['还没选客户'], action: '保存' })
  const rootStart = html.indexOf('<span')
  assert.ok(rootStart === 0, '根节点应当是那个容器 span')
  const rootEnd = html.lastIndexOf('</span>')
  const hintAt = html.indexOf('还没选客户')
  assert.ok(hintAt > 0 && hintAt < rootEnd, '提示块必须落在根容器内部')
})

test('提示用**紧凑样式**（不再铺一大块底色）', () => {
  // 主人 2026-10-16 截图（打样「产品名称 + 数量 + 添加」那一行）：
  // 「红色这一块占着那么一大块空白，不美观」。
  // 默认的块状样式（警示底色 + 内边距 + 项目符号）是给弹窗底部那种宽松地方用的；
  // 这个件多数用在**表单行/工具栏**，必须走紧凑版。
  const html = render({ disabled: true, blockedReasons: ['还没选客户'], action: '保存' })
  assert.match(html, /blocked-hint-compact/, '没走紧凑样式 —— 会铺一大块底色')
  assert.doesNotMatch(html, /class="blocked-hint"/, '混进了块状样式')
  assert.doesNotMatch(html, /<ul/, '紧凑版不该有项目符号列表')
  // 「看得见」这条不打折：原因仍然常驻
  assert.match(html, /还没选客户/)
  assert.match(html, /还不能保存/)
})
