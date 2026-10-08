import { colorAt } from './tokens'
import type { NameValue, ValueFormat } from './options'

/**
 * 环形图右侧的图例：**名字 + 数量 + 占比**。
 *
 * 为什么不用 ECharts 自带的图例：它只显示名字，而这张图唯一要说的事就是
 * "各占几成" —— 占比得自己画出来。放在图右侧而不是底部，是为了让卡片长宽比
 * 更接近正方形，一行四张时高矮更好看齐。
 */
export default function DonutLegend({
  data,
  format,
}: {
  data: NameValue[]
  format: ValueFormat
}) {
  const total = data.reduce((sum, item) => sum + item.value, 0)
  return (
    <div style={{ display: 'grid', gap: 7, fontSize: 12, flex: 1, minWidth: 0 }}>
      {data.map((item, index) => (
        <div key={item.name} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span
            style={{
              width: 10,
              height: 10,
              flex: 'none',
              borderRadius: 2,
              background: colorAt(index),
            }}
          />
          <span
            title={item.name}
            style={{
              flex: 1,
              minWidth: 0,
              color: 'var(--crm-text-2)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {item.name}
          </span>
          <span
            style={{
              flex: 'none',
              color: 'var(--crm-text)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {format(item.value)}
          </span>
          <span
            style={{
              flex: 'none',
              width: 38,
              textAlign: 'right',
              color: 'var(--crm-text-3)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {total ? `${Math.round((item.value / total) * 100)}%` : '0%'}
          </span>
        </div>
      ))}
    </div>
  )
}
