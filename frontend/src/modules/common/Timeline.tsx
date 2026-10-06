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

export default function Timeline({
  events,
  loading,
  currentType,
  currentId,
}: {
  events: TimelineEvent[]
  loading?: boolean
  /**
   * 当前页面这个对象的类型与 id（商机页传 opportunity，客户页传 customer）。
   *
   * 为什么要传：**一条跟进可以同时挂在客户和商机名下**，而后端按固定优先级
   * （样品 → 订单 → 报价 → 商机）挑一个当作来源。在商机详情页看时间线时，
   * 挑中的很可能就是这个商机本身 —— 于是「查看原单」的地址等于当前页，
   * 点下去页面一动不动，看起来像功能坏了（实测复现过）。
   *
   * 来源和当前位置重合时，这个链接没有任何意义：直接不给。
   * 用 string 而不是收窄成那 4 种来源类型 —— 因为客户页传的 "customer"
   * 本来就不在后端会返回的来源里，收窄了这行直接编译不过。
   */
  currentType?: string
  currentId?: number
}) {
  const isSelfSource = (event: TimelineEvent) =>
    currentType !== undefined &&
    currentId !== undefined &&
    event.source?.type === currentType &&
    event.source?.id === currentId

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
            {/* 来源就是本页这个对象时不显示 —— 点了等于原地不动（原因见组件入参注释） */}
            {event.source && !isSelfSource(event) && (
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
