import { useEffect, useRef } from 'react'

import echarts, { type EChartsInstance, type EChartsOption } from './echarts'

/**
 * ECharts 的 React 外壳：管好"建实例 / 换配置 / 跟着容器缩放 / 卸载时释放"。
 *
 * 三件容易漏的事，都在这里做掉了：
 * 1. **缩放**：用 `ResizeObserver` 盯着容器（不是盯 window）—— 侧边栏收起、
 *    卡片换行这类"窗口没变但容器变了"的情况，只听 window 是收不到的；
 * 2. **释放**：卸载时 `dispose()`。本项目开了 StrictMode，effect 会跑两遍，
 *    不释放就会留下一个占着 canvas 的僵尸实例；
 * 3. **`notMerge`**：数据变少时（比如从 5 个扇区变 2 个），默认的合并模式会
 *    把上一次的旧扇区留在图上 —— 那是最难发现的一类错，干脆整份替换。
 *
 * 容器必须有**确定的高度**，否则 ECharts 量不到尺寸、什么都不画。
 */
export default function EChart({
  option,
  height = 260,
  ariaLabel,
}: {
  option: EChartsOption
  height?: number
  ariaLabel: string
}) {
  const boxRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<EChartsInstance | null>(null)

  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const chart = echarts.init(box)
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(box)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true })
  }, [option])

  return (
    <div
      ref={boxRef}
      role="img"
      aria-label={ariaLabel}
      style={{ width: '100%', height }}
    />
  )
}
