import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const source = readFileSync(new URL('../frontend/src/modules/quote/WhatIfPanel.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText

// Run the actual component and its event handlers; replace only hooks, API and UI dependencies.
function setup(unitCost = 1) {
  const state = []
  let cursor = 0
  const exports = {}
  runInNewContext(compiled, {
    exports, module: { exports },
    require: (id) => {
      if (id === 'react') return {
        useMemo: (fn) => fn(),
        useState: (initial) => {
          const index = cursor++
          if (!(index in state)) state[index] = initial
          return [state[index], (value) => { state[index] = value }]
        },
      }
      if (id === '@tanstack/react-query') return { useQuery: () => ({ data: { versions: [] } }) }
      if (id === '@douyinfe/semi-ui') return Object.fromEntries(['Button', 'Input', 'Slider', 'Table', 'Tag', 'Toast'].map((name) => [name, name]))
      if (id.endsWith('/SectionCard')) return { default: 'SectionCard' }
      // 「标签全家」再添一员（2026-11-21）：产品给 WhatIfPanel 加了 HelpTip
      // （默认导出），替身没跟上就 MODULE_NOT_FOUND 连环红。
      if (id.endsWith('/HelpTip')) return { default: 'HelpTip' }
      // 「标签全家」：mock 必须覆盖 WhatIfPanel 实际用到的 money 全部导出。
      // 2026-11-17 教训：formatUnitPrice/toTwoDecimals 缺席即 TypeError，
      // 三条用例连环红——替身家族要跟着组件真实 import 走。
      if (id.endsWith('/money')) return {
        formatMoney: String,
        formatUnitPrice: (value) => `¥${value}`,
        toTwoDecimals: String,
      }
      if (id.endsWith('/api/quote')) return {}
      return require(id)
    },
  })
  return () => {
    cursor = 0
    const tree = exports.default({ quoteId: 1, versionId: 1, items: [{ quantity: 10000, quoted_price: 1.2345, cost_snapshot: unitCost }] })
    const nodes = []
    const walk = (node) => {
      if (Array.isArray(node)) return node.forEach(walk)
      if (node && typeof node === 'object' && node.props) { nodes.push(node); walk(node.props.children) }
    }
    walk(tree)
    return {
      slider: nodes.find((node) => node.type === 'Slider').props,
      input: nodes.find((node) => node.type === 'Input').props,
      button: (label) => nodes.find((node) => node.type === 'Button' && node.props.children === label)?.props,
      save: (index) => nodes.filter((node) => node.type === 'Button' && Array.isArray(node.props.children) && node.props.children[0] === '记为方案 ')[index].props.onClick(),
      // 产品 2026-11-20 给方案表加了空态（没记方案时只出引导文字、不出 Table）——
      // 记完方案才有表，所以惰性查找：各用例都是 save 之后才取 scenarios。
      get scenarios() {
        return nodes.find((node) => node.type === 'Table' && node.props.rowKey === 'label')?.props
      },
    }
  }
}

test('four-decimal baseline and saved scenario keep amount and profit', () => {
  const render = setup()
  let view = render()
  assert.equal(view.input.value, '1.2345')
  assert.equal(view.slider.step, 0.0001)
  assert.equal(view.slider.tipFormatter(1.2345), '¥1.2345')
  view.save(0)
  view = render()
  const row = view.scenarios.dataSource[0]
  assert.equal(row.price, 1.2345)
  assert.ok(Math.abs(row.amount - 12345) < 1e-8)
  assert.ok(Math.abs(row.profit - 2345) < 1e-8)
  assert.equal(view.scenarios.columns.find((col) => col.dataIndex === 'price').render(row.price), '¥1.2345')
})

test('slider, manual input, scenarios and reset use the same four-decimal price', () => {
  const render = setup()
  render().slider.onChange(1.2346)
  let view = render()
  assert.equal(view.input.value, '1.2346')
  view.save(1)
  assert.equal(render().scenarios.dataSource[1].price, 1.2346)
  render().input.onChange('1.23456')
  view = render()
  assert.equal(view.input.value, '1.2346')
  view.button('重置到当前价').onClick()
  assert.equal(render().input.value, '1.2345')
})

test('unknown cost remains unknown in saved four-decimal scenario', () => {
  const render = setup(null)
  render().save(0)
  const row = render().scenarios.dataSource[0]
  assert.equal(row.price, 1.2345)
  assert.equal(row.profit, null)
  assert.equal(row.margin, null)
})
