/**
 * 图表配置的构造器 —— 全项目只在这一处定义"我们的图长什么样"。
 *
 * 为什么不各页各写一份 `option`：坐标轴字号、网格线虚实、柱宽、圆角、
 * 悬浮提示的措辞……一旦各写一份，两页的图就会慢慢长得不一样
 * （工作台那张手写折线图与分析页的差异就是这么来的）。
 *
 * 每个构造器都**返回完整配置**（含 tooltip / grid / axis / 颜色），
 * 调用方只管喂数据与格式化函数。
 */

import type { EChartsOption } from './echarts'
import { FUNNEL_COLORS, chartTokens, colorAt } from './tokens'

export interface NameValue {
  name: string
  value: number
}

export interface SeriesDef {
  name: string
  values: (number | null)[]
}

/** 数字 → 展示文本（轴、悬浮提示、柱端标签都用它，保证同一张图里口径一致）。 */
export type ValueFormat = (value: number) => string

/** 按**序列名**分别格式化 —— 一张图里同时有"单量"和"百分比"时必须分开。 */
export type FormatMap = Record<string, ValueFormat>

export const plainNumber: ValueFormat = (value) => Math.round(value).toLocaleString('zh-CN')

export const asMoney: ValueFormat = (value) =>
  `¥${Math.round(value).toLocaleString('zh-CN')}`

export const asPercent: ValueFormat = (value) => `${(value * 100).toFixed(0)}%`

/** 坐标轴上的金额要短：`¥180万` 比 `¥1,800,000` 好读，也不挤掉别人的位置。 */
export const compactMoney: ValueFormat = (value) => {
  const abs = Math.abs(value)
  if (abs >= 100_000_000) return `¥${(value / 100_000_000).toFixed(1)}亿`
  if (abs >= 10_000) return `¥${(value / 10_000).toFixed(abs >= 100_000 ? 0 : 1)}万`
  return asMoney(value)
}

/** 已经算成百分比的数字（0~100），显示时补一个 `%`。 */
export const asPercentValue: ValueFormat = (value) => `${Math.round(value)}%`

/** ECharts 回调参数的最小形状（不用 any：只取我们真正读的几个字段）。 */
interface TooltipParam {
  axisValueLabel?: string
  seriesName?: string
  name?: string
  value?: number | string
  marker?: string
  percent?: number
}

function asParams(raw: unknown): TooltipParam[] {
  return (Array.isArray(raw) ? raw : [raw]) as TooltipParam[]
}

const TOOLTIP_BASE = {
  borderWidth: 0.5,
  padding: [8, 12] as [number, number],
  extraCssText: 'box-shadow: 0 4px 12px rgba(16, 24, 40, 0.08); border-radius: 8px;',
}

/**
 * 悬浮提示：**逐条按序列名挑格式化函数**。
 *
 * 一张图里混着"12 单"和"86%"是常态，共用一个格式化函数必然有一个是错的。
 */
function tooltipFor(
  tokens: ReturnType<typeof chartTokens>,
  formats: FormatMap,
  fallback: ValueFormat,
) {
  return {
    ...TOOLTIP_BASE,
    backgroundColor: tokens.surface,
    borderColor: tokens.outline,
    textStyle: { color: tokens.text, fontSize: 12 },
    formatter: (raw: unknown) => {
      const items = asParams(raw)
      const head = items[0]?.axisValueLabel ?? items[0]?.name ?? ''
      const rows = items.map((item) => {
        const picked = item.seriesName ? formats[item.seriesName] : undefined
        const shown = (picked ?? fallback)(Number(item.value ?? 0))
        const label = item.seriesName ? `${item.seriesName}　` : ''
        const share = item.percent != null ? `（${item.percent}%）` : ''
        return `${item.marker ?? ''}${label}${shown}${share}`
      })
      return [head, ...rows].filter(Boolean).join('<br/>')
    },
  }
}

function namedFormats(series: SeriesDef[], format: ValueFormat): FormatMap {
  return Object.fromEntries(series.map((item) => [item.name, format]))
}

function categoryAxis(tokens: ReturnType<typeof chartTokens>, categories: string[]) {
  return {
    type: 'category' as const,
    data: categories,
    axisLabel: { color: tokens.textFaint, fontSize: 12, hideOverlap: true },
    axisLine: { lineStyle: { color: tokens.line } },
    axisTick: { show: false },
  }
}

/**
 * 纵轴。**刻度上的数字不带单位** —— 这一条踩过坑：轴和提示共用同一个格式化函数时，
 * 数量轴会被写成 `0 个 / 1 个 / 1 个 / 2 个…`（刻度取整后重复，看着像坏了）。
 * 单位属于**提示与柱端标签**，轴只要数字。
 */
function valueAxis(
  tokens: ReturnType<typeof chartTokens>,
  format: ValueFormat,
  showSplitLine = true,
) {
  return {
    type: 'value' as const,
    // 刻度最小间隔 1：数量轴的最大值常常只有 1、2，不卡的话 ECharts 会切出
    // 0.2/0.4/0.6 这种刻度，取整后显示成 `0 0 0 1 1 1`（看着像坏了）。
    // 金额与百分比轴的刻度本来就远大于 1，这条对它们没有任何影响。
    minInterval: 1,
    axisLabel: { color: tokens.textFaint, fontSize: 12, formatter: (v: number) => format(v) },
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: showSplitLine
      ? { lineStyle: { color: tokens.line, type: 'dashed' as const } }
      : { show: false },
  }
}

function legend(tokens: ReturnType<typeof chartTokens>) {
  return {
    top: 0,
    left: 0,
    icon: 'roundRect',
    itemWidth: 10,
    itemHeight: 10,
    itemGap: 16,
    textStyle: { color: tokens.text2, fontSize: 12 },
  }
}

const GRID = { left: 6, right: 16, top: 34, bottom: 2, containLabel: true }

/**
 * 折线图：**随时间变化**的东西（月度业绩、准时率趋势）。
 *
 * 时间序列用柱状图会把"趋势"变成"一堆柱子高低"；折线才能看出走势与拐点。
 *
 * ⚠️ 默认**不平滑**（`smooth: false`）：平滑曲线在点稀疏、数值又忽上忽下时会
 * "甩出去" —— 相邻两点一个 0 一个 8 万，平滑后中间那段会拱到 8 万之上、再砸到 0 以下，
 * 看着像"发货量曾经冲到 12 万又掉到零"，而实际数据里根本没有那些值。
 * **折线的义务是如实连接两个点**，不是画得顺滑。
 */
export function lineChartOption({
  categories,
  series,
  format = plainNumber,
  axisFormat,
  formats,
  smooth = false,
}: {
  categories: string[]
  series: SeriesDef[]
  format?: ValueFormat
  axisFormat?: ValueFormat
  formats?: FormatMap
  smooth?: boolean
}): EChartsOption {
  const tokens = chartTokens()
  return {
    grid: GRID,
    // 只有一条序列时不显示图例：标题已经说了它是什么，再来一行图例是噪音
    legend: series.length > 1 ? legend(tokens) : { show: false },
    tooltip: { trigger: 'axis', ...tooltipFor(tokens, formats ?? namedFormats(series, format), format) },
    xAxis: categoryAxis(tokens, categories),
    yAxis: valueAxis(tokens, axisFormat ?? plainNumber),
    series: series.map((item, index) => ({
      name: item.name,
      type: 'line',
      smooth,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { width: 2 },
      itemStyle: { color: colorAt(index) },
      data: item.values,
    })),
  }
}

/**
 * 纵向柱状图：**分档 / 分阶段**的量（账龄分桶、阶段分布）。
 *
 * 与折线的分工：柱说的是"每一档各多少"，档与档之间没有连续关系；
 * 折线说的是"从一个时点到下一个时点怎么变"。用错就会误读。
 */
export function columnChartOption({
  categories,
  series,
  format = plainNumber,
  axisFormat,
  formats,
  stacked = false,
  rotateLabels = false,
}: {
  categories: string[]
  series: SeriesDef[]
  format?: ValueFormat
  axisFormat?: ValueFormat
  formats?: FormatMap
  stacked?: boolean
  rotateLabels?: boolean
}): EChartsOption {
  const tokens = chartTokens()
  return {
    grid: GRID,
    legend: series.length > 1 ? legend(tokens) : { show: false },
    tooltip: { trigger: 'axis', ...tooltipFor(tokens, formats ?? namedFormats(series, format), format) },
    xAxis: {
      ...categoryAxis(tokens, categories),
      axisLabel: {
        color: tokens.textFaint,
        fontSize: 12,
        hideOverlap: true,
        rotate: rotateLabels ? 30 : 0,
      },
    },
    yAxis: valueAxis(tokens, axisFormat ?? plainNumber),
    series: series.map((item, index) => ({
      name: item.name,
      type: 'bar',
      stack: stacked ? 'total' : undefined,
      barMaxWidth: 26,
      itemStyle: {
        color: colorAt(index),
        // 堆叠时只有最上面那段带圆角，否则每段都圆角会像一节节香肠
        borderRadius:
          stacked && index !== series.length - 1 ? 0 : ([5, 5, 0, 0] as [number, number, number, number]),
      },
      data: item.values,
    })),
  }
}

/**
 * 柱 + 线 的组合图：**数量与另一个指标要一起看**的时候。
 *
 * 两种用法：
 * - `lineAxis: 'value'`（工作台）：柱=订单金额、线=回款金额，**同一根纵轴**，
 *   因为两者是同一个量纲，同轴才能直接比高低；
 * - `lineAxis: 'percent'`（交期趋势）：柱=发货单量、线=准时率，量纲不同，
 *   线走**右侧 0~100% 的副轴**。
 *
 * ⚠️ 比率**不能**脱开分母单独看：只发 1 单准时就是 100%，同一根横轴上摆着
 * 单量柱，读者才能自己判断这个百分比可不可信。
 */
export function comboBarLineOption({
  categories,
  bars,
  line,
  barFormat = plainNumber,
  lineFormat,
  lineAxis = 'value',
  axisFormat,
}: {
  categories: string[]
  bars: SeriesDef[]
  line: SeriesDef
  barFormat?: ValueFormat
  lineFormat?: ValueFormat
  lineAxis?: 'value' | 'percent'
  axisFormat?: ValueFormat
}): EChartsOption {
  const tokens = chartTokens()
  const formats: FormatMap = {
    ...namedFormats(bars, barFormat),
    [line.name]: lineFormat ?? barFormat,
  }
  return {
    grid: { ...GRID, right: lineAxis === 'percent' ? 36 : 16 },
    legend: legend(tokens),
    tooltip: { trigger: 'axis', ...tooltipFor(tokens, formats, barFormat) },
    xAxis: categoryAxis(tokens, categories),
    yAxis: [
      valueAxis(tokens, axisFormat ?? plainNumber),
      lineAxis === 'percent'
        ? { ...valueAxis(tokens, asPercentValue, false), max: 100, min: 0 }
        : { ...valueAxis(tokens, axisFormat ?? plainNumber, false), show: false },
    ],
    series: [
      ...bars.map((item, index) => ({
        name: item.name,
        type: 'bar' as const,
        stack: 'volume',
        barMaxWidth: 22,
        itemStyle: {
          color: colorAt(index),
          borderRadius:
            index === bars.length - 1 ? ([5, 5, 0, 0] as [number, number, number, number]) : 0,
        },
        data: item.values,
      })),
      {
        name: line.name,
        type: 'line' as const,
        yAxisIndex: lineAxis === 'percent' ? 1 : 0,
        // 同 lineChartOption：不平滑，别让曲线替数据编形状
        smooth: false,
        symbol: 'circle',
        symbolSize: 6,
        lineStyle: { width: 2 },
        itemStyle: { color: colorAt(bars.length) },
        // 断点（没有分母的月份）如实断开，别把两点直连成"看起来有数据"
        connectNulls: false,
        data: line.values,
      },
    ],
  }
}

/**
 * 环形图：**看占比**（各来源占几成）。
 *
 * 中间的合计与右侧图例由调用方用 HTML 画（ECharts 自带图例没法显示百分比，
 * 而"各占几成"恰恰是这张图唯一要说的事）。
 */
export function donutChartOption({
  data,
  format = plainNumber,
  centerLabel,
  centerValue,
}: {
  data: NameValue[]
  format?: ValueFormat
  centerLabel: string
  centerValue: string
}): EChartsOption {
  const tokens = chartTokens()
  // 中间那个数字是画在圆环**里面**的：字太长（比如 `¥45,000`）会压到环上。
  // 按字数收字号，比"把环做大"更稳 —— 环大了整张卡都得跟着长。
  const centerSize = centerValue.length <= 4 ? 20 : centerValue.length <= 6 ? 17 : 15
  return {
    tooltip: { trigger: 'item', ...tooltipFor(tokens, {}, format) },
    legend: { show: false },
    title: {
      text: centerValue,
      subtext: centerLabel,
      left: 'center',
      top: '38%',
      textAlign: 'center',
      textStyle: { color: tokens.text, fontSize: centerSize, fontWeight: 500 },
      subtextStyle: { color: tokens.textFaint, fontSize: 12 },
    },
    series: [
      {
        type: 'pie',
        radius: ['62%', '88%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        label: { show: false },
        labelLine: { show: false },
        itemStyle: { borderColor: tokens.surface, borderWidth: 2 },
        data: data.map((item, index) => ({
          name: item.name,
          value: item.value,
          itemStyle: { color: colorAt(index) },
        })),
      },
    ],
  }
}

/**
 * 漏斗图：**逐级减少**的才配叫漏斗。
 *
 * ⚠️ 口径提醒：后端 `analytics/opportunities` 里有两组数 ——
 * `funnel` 是"当前停在每个阶段的进行中商机数"（**存量分布**，可能中间大两头小，
 * 用漏斗画会误导），`stage_conversion.reached_count` 才是"到达过该阶段"
 * （累计，单调递减）。**漏斗图只能喂后者。**
 */
export function funnelChartOption({
  data,
  format = plainNumber,
}: {
  data: NameValue[]
  format?: ValueFormat
}): EChartsOption {
  const tokens = chartTokens()
  return {
    tooltip: { trigger: 'item', ...tooltipFor(tokens, {}, format) },
    series: [
      {
        type: 'funnel',
        left: '2%',
        // 右边留出标签的位置：标签放**里面**时，尾端数值为 0 的窄片塞不下字，
        // 会被截成"推荐""价 0"这种半截话（实测踩过）。放外面就都能读全。
        right: '30%',
        top: 6,
        bottom: 6,
        minSize: '14%',
        maxSize: '100%',
        sort: 'none',
        gap: 3,
        label: {
          show: true,
          position: 'right',
          color: tokens.text,
          fontSize: 12,
          formatter: (raw: unknown) => {
            const item = asParams(raw)[0]
            return `${item.name ?? ''}　${format(Number(item.value ?? 0))}`
          },
        },
        labelLine: { show: true, length: 10, lineStyle: { color: tokens.line } },
        itemStyle: { borderColor: tokens.surface, borderWidth: 1 },
        data: data.map((item, index) => ({
          name: item.name,
          value: item.value,
          itemStyle: { color: FUNNEL_COLORS[index % FUNNEL_COLORS.length] },
        })),
      },
    ],
  }
}

/**
 * 横向排名条：**比大小**、类别名还长（业务员、失单原因、按等级均价）。
 *
 * 名字横着写才读得顺；纵过来写要么斜排要么被截断。类别超过 6 个时也只能用它 ——
 * 环形图塞不下那么多扇区，靠颜色也没人分得清。
 */
export function rankBarChartOption({
  data,
  format = plainNumber,
  axisFormat,
}: {
  data: NameValue[]
  format?: ValueFormat
  axisFormat?: ValueFormat
}): EChartsOption {
  const tokens = chartTokens()
  // ECharts 的类目轴是从下往上排的，倒一次让最大的显示在最上面
  const ordered = [...data].reverse()
  return {
    grid: { left: 6, right: 56, top: 6, bottom: 2, containLabel: true },
    tooltip: { trigger: 'axis', ...tooltipFor(tokens, {}, format) },
    xAxis: valueAxis(tokens, axisFormat ?? plainNumber, false),
    yAxis: {
      type: 'category',
      data: ordered.map((item) => item.name),
      axisLabel: { color: tokens.text2, fontSize: 12 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 18,
        itemStyle: { color: colorAt(0), borderRadius: [0, 4, 4, 0] },
        label: {
          show: true,
          position: 'right',
          color: tokens.text2,
          fontSize: 12,
          formatter: (raw: unknown) => format(Number(asParams(raw)[0]?.value ?? 0)),
        },
        data: ordered.map((item) => item.value),
      },
    ],
  }
}
