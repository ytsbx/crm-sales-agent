/**
 * F15-B 全局样式 [5001-8487] 复核脚本。
 * 运行：node review/repro/f15_frontend/check_f15b.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const css = readFileSync(resolve(ROOT, 'frontend/src/index.css'), 'utf8')
const orderDetail = readFileSync(resolve(ROOT, 'frontend/src/modules/order/OrderDetailPage.tsx'), 'utf8')
const knowledge = readFileSync(resolve(ROOT, 'frontend/src/modules/knowledge/KnowledgePage.tsx'), 'utf8')
const results = []
const check = (id, name, verdict, ok, evidence) => results.push({ id, name, verdict, ok: Boolean(ok), evidence })
const has = (text, s) => text.includes(s)
const count = (text, re) => (text.match(re) ?? []).length

check('F15B-01', '窄屏 display:none 隐藏已收/未收/已分配/待分配', 'CONFIRMED',
  has(css, '@media (max-width: 1199px)')
    && has(css, '.col-hide-narrow')
    && has(css, 'display: none !important;')
    && has(orderDetail, "title: '已收'")
    && has(orderDetail, "title: '未收'")
    && has(orderDetail, "title: '已分配'")
    && has(orderDetail, "title: '待分配'")
    && has(orderDetail, 'className: \'cell-right col-hide-narrow\''),
  '1024 视口命中 max-width:1199；四列都是 display:none，不是横向滚动/折叠。订单只剩应收金额+状态，回款只剩整笔到账',
)

check('F15B-02', '弹窗自定义样式把错误态覆盖成白底蓝边', 'CONFIRMED',
  has(css, '.semi-modal .semi-input-wrapper,')
    && has(css, '.semi-modal .semi-input-wrapper-error,')
    && has(css, 'background: #fff !important;')
    && has(css, 'border-color: var(--crm-primary) !important;')
    && !has(css, '.semi-modal .semi-input-wrapper.semi-input-wrapper-error')
    && has(css, '.semi-modal .form-card .semi-input-wrapper'),
  '错误态选择器和 focus 同组，被强设成白底主色边；form-card 还有 3 类白底灰边规则。Semi 默认 danger 红底红边被压掉，校验失败看起来像正常字段',
)

check('F15B-03', '禁用实心主按钮仍白底蓝字', 'CONFIRMED',
  has(css, '.semi-button-primary.semi-button-solid {')
    && has(css, 'background: #fff !important;')
    && has(css, 'color: #2f6df6 !important;')
    && has(css, 'border: 1px solid rgba(47, 109, 246, .5) !important;')
    && !has(css, '.semi-button-primary.semi-button-solid.semi-button-disabled')
    && !has(css, '.semi-button-primary.semi-button-solid:disabled')
    && has(knowledge, '<Button theme="solid" disabled='),
  'solid 主按钮用 !important 固定白底/蓝字/蓝边，没有补 solid 的 disabled 规则；KnowledgePage 的“选择照片”在无权限/保存中仍像可点',
)

check('F15B-04', '分析页漏斗固定 404px，320 宽溢出', 'CONFIRMED',
  has(css, '.deal-flow {')
    && has(css, 'flex: 0 0 404px;')
    && has(css, 'grid-template-columns: minmax(0, 1fr) 72px 96px 96px;')
    && !/@media \(max-width: (560|600)px\)[\s\S]{0,400}\.deal-flow/.test(css)
    && !has(css, '.deal-flow { flex-basis: 100%'),
  '320 宽去掉卡片左右内边距约 280px，404px 流程条必溢出；四列固定部分 72+96+96+36 gap=300px，同样顶出容器',
)

check('F15B-05', '危险链接红色只认 3 个内联字符串白名单', 'CONFIRMED',
  has(css, '.semi-table-tbody .semi-table-row-cell a {')
    && has(css, 'color: #2c62d9 !important;')
    && has(css, 'a[style*="crm-error"]')
    && has(css, 'a[style*="#f53f3f"]')
    && has(css, 'a[style*="#ff4d4f"]')
    && count(css, /a\[style\*=/g) >= 3
    && !has(css, 'a.cell-danger-link'),
  '表格链接统一蓝 !important；只有内联 style 原文含 crm-error/#f53f3f/#ff4d4f 才恢复红。换 class、换红色变量或新增危险动作忘记写内联色，就回到普通蓝',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
console.log(`\n${results.filter((r) => r.ok).length} confirmed, ${results.filter((r) => !r.ok).length} missing`)
