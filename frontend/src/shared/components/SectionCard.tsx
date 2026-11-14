import type { CSSProperties, ReactNode } from 'react'

/** 统一的区块卡片：标题 +（可选）右侧操作 + 内容。 */
export default function SectionCard({
  title,
  extra,
  children,
  style,
  id,
  accent,
}: {
  title?: ReactNode
  extra?: ReactNode
  children: ReactNode
  style?: CSSProperties
  /** 页内锚点：待办区之类的地方要能 `scrollIntoView` 定位到这一块 */
  id?: string
  /**
   * 标题前的色条（3px 宽）。用来**区分模块**，不是装饰：
   * 一个页面里最多三种色，同一类模块全局同色（AI=紫、文档=青、基础信息=蓝）。
   * 只上在这一小条上，卡片底色仍然白 —— 颜色有存在感，但不会把页面搅花。
   */
  accent?: string
}) {
  return (
    <div className="card-block" id={id} style={style}>
      {(title || extra) && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 'var(--crm-space-4)',
          }}
        >
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            {accent && (
              <span
                aria-hidden
                style={{
                  width: 3,
                  height: 15,
                  borderRadius: 2,
                  background: accent,
                  flex: 'none',
                }}
              />
            )}
            {typeof title === 'string' ? <span className="card-title">{title}</span> : title}
          </span>
          {extra}
        </div>
      )}
      {children}
    </div>
  )
}
