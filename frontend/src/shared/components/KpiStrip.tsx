import type { CSSProperties, ReactNode } from 'react'

export interface KpiItem {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'primary' | 'success' | 'warning' | 'error'
  onClick?: () => void
}

const TONE_STYLE: Record<string, { color?: string; chip: string }> = {
  default: { chip: 'chip' },
  primary: { color: 'var(--crm-primary)', chip: 'chip chip-primary' },
  success: { color: 'var(--crm-success)', chip: 'chip chip-success' },
  warning: { color: 'var(--crm-warning)', chip: 'chip chip-warning' },
  error: { color: 'var(--crm-error)', chip: 'chip chip-error' },
}

/** 列表页顶部的一排指标卡，与工作台的 KPI 卡同一套样式。 */
export default function KpiStrip({
  items,
  columns,
  style,
}: {
  items: KpiItem[]
  columns?: number
  style?: CSSProperties
}) {
  if (!items.length) return null
  return (
    <div
      className="kpi-grid"
      style={{
        ...(columns ? { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` } : null),
        ...style,
      }}
    >
      {items.map((item) => {
        const tone = TONE_STYLE[item.tone ?? 'default']
        return (
          <div
            key={item.label}
            className="kpi-card"
            style={{ cursor: item.onClick ? 'pointer' : 'default' }}
            onClick={item.onClick}
          >
            <div className="kpi-label">{item.label}</div>
            <div className="kpi-value" style={{ color: tone.color }}>
              {item.value}
            </div>
            {item.hint && (
              <div style={{ marginTop: 10 }}>
                <span className={tone.chip}>{item.hint}</span>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
