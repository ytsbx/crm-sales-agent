import type { TagTone } from '../types'

/**
 * 「状态圆点 + 文字」。
 *
 * 原来的列表状态是一整块彩色底（Semi Tag 的色块）：
 * 产品中心「在售」十个绿块、报价中心「已接受」十八个绿块、商机阶段一片蓝块……
 * 一屏排下来像一面色块墙，眼睛先被颜色吃掉，反而看不清是哪一行。
 *
 * 这里只留一个 6px 圆点表示状态，文字统一用正文色：
 * 颜色退成辅助标记（想区分状态时扫圆点），正文恢复成黑字（想读信息时直接读）。
 * 与客户中心「阶段」列是同一套视觉语言。
 *
 * 取色比 Semi Tag 的背景色深一档 —— 白底上 6px 的圆点如果沿用浅底色会糊掉。
 */
const DOT_COLOR: Record<TagTone, string> = {
  amber: 'var(--crm-warning)',
  blue: '#1f4fa8',
  cyan: '#0891b2',
  green: '#00b42a',
  grey: '#9aa0ab',
  indigo: '#4f46e5',
  lime: '#65a30d',
  orange: '#ff7d00',
  pink: '#db2777',
  purple: '#8b5cf6',
  red: '#f5222d',
  teal: '#0d9488',
  violet: '#7431d3',
  yellow: '#e09616',
}

export default function StatusDot({
  label,
  tone = 'grey',
  bold = false,
}: {
  /** 圆点右边显示的文案，通常就是状态名 */
  label: string
  /** 状态色调，与 Semi Tag 的 color 取值同名，方便直接把原来的 Tag 换过来 */
  tone?: TagTone
  /** 需要强调时（如「逾期」）把文字加重，默认不加重 */
  bold?: boolean
}) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, lineHeight: 1.4 }}>
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          flexShrink: 0,
          background: DOT_COLOR[tone] ?? DOT_COLOR.grey,
        }}
      />
      <span style={{ color: bold ? 'var(--crm-text-1)' : 'var(--crm-text-2)' }}>{label}</span>
    </span>
  )
}
