import type { CSSProperties, ReactNode } from 'react'

/**
 * 详情页统一头部（客户 / 商机 / 报价 / 订单四个详情页共用）。
 *
 * 排布取自设计稿（abc_gmbh、_10、q20240924001_v3、_1）：
 *
 *   标题行   [标题] [状态标签…]                 [操作按钮…]
 *   信息行   [关键信息…]（在标题下方，左对齐）
 *
 * 关键信息行必须在标题**下方**并且靠左，不能跑到标题右边——
 * 标题右边留给状态标签，按钮贴右边缘，这样四页看起来才是同一套。
 */
export default function DetailHeader({
  title,
  tags,
  meta,
  extra,
  children,
  style,
}: {
  /** 主标题：客户名 / 商机名 / 报价单号 / 订单号 */
  title: ReactNode
  /** 标题右侧的状态标签，如「A 级客户」「进行中 · 商务谈判」 */
  tags?: ReactNode
  /** 标题下方的关键信息行（左对齐） */
  meta?: ReactNode
  /** 右上角操作区 */
  extra?: ReactNode
  /** 头部卡片里的其余内容（如报价版本切换、订单 KPI），跨整行显示 */
  children?: ReactNode
  style?: CSSProperties
}) {
  return (
    <div className="card-block" style={{ marginBottom: 16, ...style }}>
      {/* 移动端（场景23）：单列堆叠——标题在上、操作按钮换行到下方，
          手机上不再左右挤在两列里 */}
      <div className="detail-header-grid">
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span className="detail-title">{title}</span>
            {tags}
          </div>
          {meta && <div className="detail-meta">{meta}</div>}
        </div>
        {extra && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexWrap: 'wrap',
              justifyContent: 'flex-end',
            }}
          >
            {extra}
          </div>
        )}
        {children && <div style={{ gridColumn: '1 / -1' }}>{children}</div>}
      </div>
    </div>
  )
}
