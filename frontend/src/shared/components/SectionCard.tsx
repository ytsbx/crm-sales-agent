import type { CSSProperties, ReactNode } from 'react'

/** 统一的区块卡片：标题 +（可选）右侧操作 + 内容。 */
export default function SectionCard({
  title,
  extra,
  children,
  style,
  id,
}: {
  title?: ReactNode
  extra?: ReactNode
  children: ReactNode
  style?: CSSProperties
  /** 页内锚点：待办区之类的地方要能 `scrollIntoView` 定位到这一块 */
  id?: string
}) {
  return (
    <div className="card-block" id={id} style={style}>
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
