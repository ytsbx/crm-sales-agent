/**
 * ECharts 的**按需**引入：只挂本项目真正用到的图型与组件。
 *
 * 为什么要从 `echarts/core` 引、再 `use([...])` 一遍，而不是 `import * as echarts from 'echarts'`：
 * 后者会把全部图型（地图、雷达、桑基、K 线…）与全部组件打进包里。这里只用到
 * **折线 / 柱 / 环形 / 漏斗** 四种图 + **网格 / 图例 / 悬浮提示** 三个组件，
 * 按需引入能把体积压到全量的零头。
 *
 * 以后要用新图型，**在这里补一行 `echarts.use([...])` 就行**，别改成整包引入。
 */

import { BarChart, FunnelChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  FunnelChart,
  LineChart,
  PieChart,
  // 按需引入最容易踩的坑：**用了哪个组件就得引哪个**。环形图中间那句
  // 合计用的是 `title`，漏掉 `TitleComponent` 时 ECharts 只在控制台报一行
  // `Component title is used but not imported`，**画面上什么都不缺** ——
  // 中间的数字凭空消失，你不去翻控制台根本发现不了。
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
])

export default echarts

/** 图表实例类型：从 `init` 的返回值推，避免跟着 ECharts 版本改类型名。 */
export type EChartsInstance = ReturnType<typeof echarts.init>

export type { EChartsOption } from 'echarts'
