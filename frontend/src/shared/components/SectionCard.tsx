import type { CSSProperties, ReactNode } from 'react'

/** 统一的区块卡片：标题 +（可选）右侧操作 + 内容。 */
export default function SectionCard({
  title,
  extra,
  children,
  style,
  id,
  accent,
  className,
}: {
  title?: ReactNode
  extra?: ReactNode
  children: ReactNode
  style?: CSSProperties
  /** 额外的类名（例如 card-hug：内容很少时不要被拉满整屏） */
  className?: string
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
    <div className={className ? `card-block ${className}` : 'card-block'} id={id} style={style}>
      {(title || extra) && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            // 窄屏下标题与按钮抢同一行，标题会被压成一个字一行（461px 实测
            // 「报价明细」逐字竖排、按钮压进表格）。这里允许换行：
            // 标题仍占一行，按钮整组掉到下一行，谁也不挤谁。
            flexWrap: 'wrap',
            gap: 12,
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
            {typeof title === 'string' ? <span className="card-title" title={title}>{title}</span> : title}
          </span>
          {/* extra 必须整组包成**一个** flex item：直接摊开的话，
              每个按钮都会成为标题行里的独立项，space-between 把它们分别顶到
              中间和右端 —— 客户详情的「客户总结」就孤零零飘在标题右边一半的位置，
              「跟进建议」却贴在卡片右上角。包一层之后，整组动作始终靠右对齐。 */}
          {extra && (
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 8,
                marginLeft: 'auto',
              }}
            >
              {extra}
            </span>
          )}
        </div>
      )}
      {children}
    </div>
  )
}
