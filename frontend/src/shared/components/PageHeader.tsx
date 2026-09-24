import type { ReactNode } from 'react'

/**
 * 统一的页面头：标题 + 副标题 + 右侧操作区。
 *
 * 各页面都用它，改一处全局统一——这就是"设计规范落进代码"的落点：
 * 页面不自己写标题样式，只描述内容。
 */
export default function PageHeader({
  title,
  subtitle,
  extra,
}: {
  title: string
  subtitle?: string
  extra?: ReactNode
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: 16,
        marginBottom: 16,
      }}
    >
      <div>
        <h2 className="page-title">{title}</h2>
        {subtitle && (
          <p className="page-subtitle" style={{ marginBottom: 0 }}>
            {subtitle}
          </p>
        )}
      </div>
      {extra && <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>{extra}</div>}
    </div>
  )
}
