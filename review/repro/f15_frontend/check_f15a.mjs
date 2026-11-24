/**
 * F15-A 全局样式 [1-5000] 复核脚本。
 * 运行：node review/repro/f15_frontend/check_f15a.mjs
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { resolve, join } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const css = readFileSync(resolve(ROOT, 'frontend/src/index.css'), 'utf8')
const orderList = readFileSync(resolve(ROOT, 'frontend/src/modules/order/OrderListPage.tsx'), 'utf8')
const whatIf = readFileSync(resolve(ROOT, 'frontend/src/modules/quote/WhatIfPanel.tsx'), 'utf8')
const layout = readFileSync(resolve(ROOT, 'frontend/src/app/layout/AppLayout.tsx'), 'utf8')
const results = []
const check = (id, name, verdict, ok, evidence) => results.push({ id, name, verdict, ok: Boolean(ok), evidence })
const has = (text, s) => text.includes(s)
const count = (text, re) => (text.match(re) ?? []).length

function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) out.push(...walk(path))
    else if (/\.(tsx|ts)$/.test(name)) out.push(path)
  }
  return out
}
const tsxFiles = walk(resolve(ROOT, 'frontend/src')).filter((p) => p.endsWith('.tsx'))
const anchorTags = tsxFiles.reduce((n, p) => n + count(readFileSync(p, 'utf8'), /<a\b/g), 0)
const anchorHref = tsxFiles.reduce((n, p) => n + count(readFileSync(p, 'utf8'), /<a\b[^>]*href=/g), 0)

check('F15A-01', '无 href 的 <a> 键盘不可聚焦，CSS 只补鼠标手型', 'CONFIRMED',
  has(css, 'a:not([href])')
    && has(css, 'cursor: pointer')
    && anchorTags > 0
    && anchorHref < anchorTags
    && !has(css, 'a:not([href]):focus-visible'),
  `全仓 TSX 中 <a> ${anchorTags} 个，带 href 只有 ${anchorHref} 个；CSS 只给无 href 的 a 补 cursor/color，没有 role/tabIndex，键盘 Tab 仍然跳过`,
)

check('F15A-02', '全站表格单行省略，金额列截断且无完整值兜底', 'CONFIRMED',
  has(css, '.semi-table-tbody .semi-table-row-cell {')
    && has(css, 'white-space: nowrap;')
    && has(css, 'overflow: hidden;')
    && has(css, 'text-overflow: ellipsis;')
    && has(orderList, "title: '订单金额'")
    && has(orderList, 'width: compact ? 108 : 124')
    && !/title: '订单金额'[\s\S]{0,400}?title:/.test(orderList)
    && has(orderList, 'render: (v: number, record: Order) =>')
    && has(orderList, 'formatMoney(toTwoDecimals(record.total_amount_exact ?? v), record.currency)'),
  '所有表体单元格 nowrap+overflow hidden；订单金额列 108/124px，render 直接返回 formatMoney 字符串，没有 title/展开入口。位数多的金额只能看到省略号',
)

check('F15A-03', '全站表格强制居中，What-if 七个数值列漏挂 .cell-right', 'CONFIRMED',
  has(css, 'text-align: center !important;')
    && has(css, '.semi-table-tbody .semi-table-row-cell.cell-right')
    && count(whatIf, /align: 'right' as const/g) >= 7
    && !has(whatIf, 'cell-right'),
  `What-if 列声明了 ${count(whatIf, /align: 'right' as const/g)} 个 align:right，但 Table 没有 cell-right 类；全局 center !important 压掉内联/列对齐，金额、毛利率仍居中`,
)

check('F15A-04', '46 处 :has() 承载布局/隐藏逻辑', 'CONFIRMED',
  count(css, /:has\(/g) === 46
    && has(css, '.page-container:not(:has(.card-block ~ .card-block)) > .card-block')
    && has(css, '.semi-table-wrapper:has(.semi-table-placeholder)'),
  `当前 CSS 共 ${count(css, /:has\(/g)} 处 :has()；不支持的浏览器会整条丢弃，单卡拉伸、空表隐藏滚动条等降级为旧表现`,
)

// 顶栏 56 + 页签条最低高度（8 padding-top + 1 border + tab-open 6*2 padding + 13 字号最小行高）
const minTabStrip = 8 + 1 + 12 + 13
const minToastTop = 56 + minTabStrip
check('F15A-T01', 'toast 固定 top:84px 会压住页签栏', 'CONFIRMED',
  has(css, 'top: 84px !important;')
    && minToastTop > 84
    && has(layout, 'className="app-header"'),
  `静态最低高度：顶栏 56 + 页签条 ${minTabStrip} = ${minToastTop}px，toast 从 84px 开始，至少压住页签下沿 ${minToastTop - 84}px；原报告“待确认”可落为 P3`,
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\nCSS 当前 ${css.endsWith('\n') ? css.split('\n').length - 1 : css.split('\n').length} 行（审查报告写 8487 行，当前 +13 行）；${results.filter((r) => r.ok).length} confirmed, ${results.filter((r) => !r.ok).length} missing`)
