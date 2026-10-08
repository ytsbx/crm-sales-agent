/**
 * 图表的颜色与字号 —— **唯一一处**。
 *
 * ECharts 画在 canvas 上，**不认 CSS 变量**：`color: 'var(--crm-primary)'` 传进去
 * 会被当成无效颜色（图会是黑的、或者干脆不画）。所以只能在渲染时从页面上把真实
 * 色值读出来 —— 这样以后改主题，图跟着变，不用回来改代码。
 *
 * 分类色板（`CATEGORY_COLORS`）是本项目新加的：原来那套只有语义色
 * （主蓝 / 紫 / 绿 / 橙 / 红），够表达"状态"，但**不够表达"类别"** ——
 * 环形图五六个扇区全一个颜色等于没上色。这里按主色系补到 6 个，
 * 顺序刻意错开色相，相邻扇区不会糊在一起。
 *
 * 类别超过 6 个时**不要继续堆颜色**（人眼分不清 8 种以上的色块），
 * 改用横向排名条 —— 见 `rankBarChartOption`。
 */

export interface ChartTokens {
  text: string
  text2: string
  textFaint: string
  /** 网格线 / 坐标轴用的浅色 */
  line: string
  /** 悬浮提示的底色与描边 */
  surface: string
  outline: string
}

const FALLBACK: ChartTokens = {
  text: '#181c23',
  text2: '#424655',
  textFaint: '#727687',
  line: '#e5e8f3',
  surface: '#ffffff',
  outline: '#c2c6d8',
}

/** 环形 / 折线多序列 / 柱状多序列共用的分类色（按需循环取用）。 */
export const CATEGORY_COLORS = [
  '#004ec6',
  '#7431d3',
  '#00b42a',
  '#ff7d00',
  '#0e9aa7',
  '#ba1a1a',
]

/** 漏斗用的蓝色梯度：从浅到深，一眼能看出"往下走"。 */
export const FUNNEL_COLORS = [
  '#b5d4f4',
  '#93c1f0',
  '#71adec',
  '#4f99e8',
  '#378add',
  '#2874c2',
  '#1d64a8',
  '#185fa5',
  '#0c447c',
]

let cached: ChartTokens | null = null

export function chartTokens(): ChartTokens {
  if (cached) return cached
  if (typeof window === 'undefined') return FALLBACK
  const root = document.documentElement
  const read = (name: string, fallback: string) => {
    const value = getComputedStyle(root).getPropertyValue(name).trim()
    return value || fallback
  }
  cached = {
    text: read('--crm-text', FALLBACK.text),
    text2: read('--crm-text-2', FALLBACK.text2),
    textFaint: read('--crm-text-3', FALLBACK.textFaint),
    line: read('--crm-surface-high', FALLBACK.line),
    surface: read('--crm-surface', FALLBACK.surface),
    outline: read('--crm-outline', FALLBACK.outline),
  }
  return cached
}

export function colorAt(index: number): string {
  return CATEGORY_COLORS[index % CATEGORY_COLORS.length]
}
