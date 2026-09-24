import type { CSSProperties, ReactNode } from 'react'

/** 统一的区块卡片：标题 +（可选）右侧操作 + 内容。 */
export default function SectionCard({
  title,
  extra,
  children,
  style,
}: {
  title?: ReactNode
  extra?: ReactNode
  children: ReactNode
  style?: CSSProperties
}) {
  return (
    <div className="card-block" style={style}>
      {(title || extra) && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 14,
          }}
        >
          {typeof title === 'string' ? <span className="card-title">{title}</span> : title}
          {extra}
        </div>
      )}
      {children}
    </div>
  )
}
