import type { CSSProperties, ReactNode } from 'react'
import { Link } from 'react-router-dom'
import HelpTip from './HelpTip'

export interface KpiItem {
  label: string
  value: ReactNode
  hint?: ReactNode
  /** hint 是提示胶囊（默认）还是卡底的可点击文字入口。 */
  hintVariant?: 'chip' | 'link'
  /** 指标口径不清楚时，用一句话解释它统计的是什么。 */
  help?: string
  tone?: 'default' | 'primary' | 'success' | 'warning' | 'error'
  onClick?: () => void
  /** 小标签自己要跳的地址；点它不会连带触发整张卡的 onClick。 */
  href?: string
}

const TONE_STYLE: Record<string, { color?: string; chip: string }> = {
  default: { chip: 'chip' },
  primary: { color: 'var(--crm-primary)', chip: 'chip chip-primary' },
  success: { color: 'var(--crm-success)', chip: 'chip chip-success' },
  warning: { color: 'var(--crm-warning)', chip: 'chip chip-warning' },
  error: { color: 'var(--crm-error)', chip: 'chip chip-error' },
}

/** 一排指标卡：列表页、分析页、工作台共用这一套，不再各写一份。 */
export default function KpiStrip({
  items,
  columns,
  className,
  tintValues = true,
  style,
}: {
  items: KpiItem[]
  columns?: number
  /** 传自定义类名以接住响应式列数（工作台的 kpi-cols-6 / kpi-cols-5）。 */
  className?: string
  /** 数值是否跟随状态色。工作台要保住「读数是黑色、颜色只用来提醒」，传 false。 */
  tintValues?: boolean
  style?: CSSProperties
}) {
  if (!items.length) return null
  return (
    <div
      className={className ?? 'kpi-grid'}
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
            className={`kpi-card tone-${item.tone ?? 'default'}`}
            style={{ cursor: item.onClick ? 'pointer' : 'default' }}
            onClick={item.onClick}
          >
            <div className="kpi-label crm-help-label">
              {item.label}
              {item.help && <HelpTip forLabel={item.label} text={item.help} variant="metric" />}
            </div>
            <div className="kpi-value" style={{ color: tintValues ? tone.color : undefined }}>
              {item.value}
            </div>
            {item.hint && (
              <div
                className={item.hintVariant === 'link' ? 'kpi-hint-link' : undefined}
                style={{ marginTop: 'var(--crm-space-3)' }}
              >
                {item.href ? (
                  <Link
                    className={tone.chip}
                    to={item.href}
                    onClick={(event) => event.stopPropagation()}
                  >
                    {item.hint}
                  </Link>
                ) : item.hintVariant === 'link' ? (
                  item.hint
                ) : (
                  <span className={tone.chip}>{item.hint}</span>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
