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
  className,
}: {
  /** 纯文字标题，或「标题 + 状态标签」这样的组合（工作台要用）。 */
  title: ReactNode
  subtitle?: string
  extra?: ReactNode
  /** 需要窄屏特殊排布的页面挂自己的响应式钩子（工作台的 wb-greeting）。 */
  className?: string
}) {
  return (
    <div
      className={className ? `page-header ${className}` : 'page-header'}
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: 'var(--crm-space-4)',
        marginBottom: 'var(--crm-space-4)',
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
      {extra && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--crm-space-3)' }}>
          {extra}
        </div>
      )}
    </div>
  )
}
