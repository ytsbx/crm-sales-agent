import { Link } from 'react-router-dom'
import type { TimelineEvent } from '../../shared/api/followup'

const KIND_COLOR: Record<string, string> = {
  audit: 'var(--crm-primary)',
  followup: 'var(--crm-success)',
  task: 'var(--crm-caution)',
  /* 阶段推进用色板的紫（secondary）；负责人变更是高信号事件，用品板里仅剩的 error 红 */
  stage: 'var(--crm-secondary)',
  owner_change: 'var(--crm-error)',
}

export default function Timeline({ events, loading }: { events: TimelineEvent[]; loading?: boolean }) {
  if (loading) return <div style={{ color: 'var(--crm-text-3)' }}>加载中…</div>
  if (!events.length) return <div className="placeholder-box">还没有动态</div>

  return (
    <div>
      {events.map((event, index) => (
        <div
          key={`${event.at}-${index}`}
          style={{ display: 'flex', gap: 12, paddingBottom: 16, position: 'relative' }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                marginTop: 6,
                background: KIND_COLOR[event.kind] ?? 'var(--crm-text-3)',
              }}
            />
            {index < events.length - 1 && (
              <span style={{ flex: 1, width: 1, background: 'var(--crm-surface-high)', marginTop: 4 }} />
            )}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14 }}>{event.title}</div>
            {event.detail && (
              <div style={{ color: 'var(--crm-text-2)', fontSize: 13, marginTop: 2 }}>{event.detail}</div>
            )}
            {event.source && (
              <Link style={{ color: 'var(--crm-primary)', fontSize: 13 }} to={`/${{ sample: 'samples', order: 'orders', quote: 'quotes', opportunity: 'opportunities' }[event.source.type]}/${event.source.id}`}>
                查看原单
              </Link>
            )}
            <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 4 }}>
              {event.operator_name} · {new Date(event.at).toLocaleString('zh-CN')}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
