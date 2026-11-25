/**
 * F08-03 的数值复现：直接加载真实图表配置构造器与 DonutLegend（不 mock 格式化函数）。
 * 用法：node review/repro/f08_frontend/run_charts.mjs
 */
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

import react from '@vitejs/plugin-react'
import { createServer } from 'vite'
import * as React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontend = path.resolve(here, '../../../frontend')

const server = await createServer({
  configFile: false,
  root: frontend,
  logLevel: 'error',
  appType: 'custom',
  server: { middlewareMode: true },
  plugins: [react()],
  resolve: { dedupe: ['react', 'react-dom'] },
})

const options = await server.ssrLoadModule('/src/shared/components/charts/options.ts')
const DonutLegend = (await server.ssrLoadModule('/src/shared/components/charts/DonutLegend.tsx')).default

let failed = 0
function check(name, fn) {
  try {
    fn()
    console.log(`PASS  ${name}`)
  } catch (e) {
    failed += 1
    console.log(`FAIL  ${name}\n      ${e.message}`)
  }
}
function assert(cond, message) {
  if (!cond) throw new Error(message)
}

const opt = options.comboBarLineOption({
  categories: ['11月'],
  bars: [{ name: '订单金额', values: [123.45] }],
  line: { name: '回款金额', values: [123.45] },
  barFormat: options.asMoney,
  axisFormat: options.compactMoney,
})
const tooltip = opt.tooltip.formatter([
  { axisValueLabel: '11月', seriesName: '订单金额', value: 123.45, marker: '<i></i>' },
])
console.log('tooltip(123.45) =', JSON.stringify(tooltip))
console.log('asMoney(123.45) =', JSON.stringify(options.asMoney(123.45)))
console.log('compactMoney(199999) =', JSON.stringify(options.compactMoney(199999)))

check('F08-03: tooltip 保留分位（123.45 不显示成 123）', () => {
  assert(String(tooltip).includes('123.45'), `实际：${tooltip}`)
})
check('F08-03: compactMoney 不把 199,999 显示成 20 万', () => {
  assert(options.compactMoney(199999) !== '¥20万', `实际：${options.compactMoney(199999)}`)
})
check('F08-03: 轴刻度保留分位（asMoney 不取整）', () => {
  assert(options.asMoney(123.45) === '¥123.45', `实际：${options.asMoney(123.45)}`)
})

// 7 个等值扇区：每项 14%，合计 98%
const legend = renderToStaticMarkup(
  React.createElement(DonutLegend, {
    data: ['A', 'B', 'C', 'D', 'E', 'F', 'G'].map((name) => ({ name, value: 1 })),
    format: options.asMoney,
  }),
)
const percents = [...legend.matchAll(/· (\d+)%/g)].map((m) => Number(m[1]))
const totalPercent = percents.reduce((sum, n) => sum + n, 0)
console.log('DonutLegend 各项占比 =', percents, '合计 =', totalPercent)

check('F08-03: 环形图图例占比合计等于 100%', () => {
  assert(totalPercent === 100, `实际合计 ${totalPercent}%（${percents.join(' / ')}）`)
})

await server.close()
console.log(`\n结果：${failed} 个断言失败（这些断言代表“修复后应有的行为”）`)
process.exitCode = 1
