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
 *
 * 宽度不够时（按钮多 + 窗口窄，见 index.css 的 .detail-header-grid）：
 * 按钮整块换到标题下方并靠左，标题独占一行，不会再互相压字。
 */
export default function DetailHeader({
  title,
  tags,
  meta,
  extra,
  children,
  style,
  metaFull = false,
}: {
  /** 主标题：客户名 / 商机名 / 报价单号 / 订单号 */
  title: ReactNode
  /** 标题右侧的状态标签，如「A 级客户」「进行中 · 商务谈判」 */
  tags?: ReactNode
  /** 标题下方的关键信息行（左对齐） */
  meta?: ReactNode
  /**
   * 信息行是否独占整个头部宽度（默认与右上角按钮并排、让出按钮那块位置）。
   * 按钮一多（订单详情有 5 个），信息行只剩 600 宽，5 项就得折成「4 + 1」，
   * 最后一项孤零零一行。开了这个开关，信息行排到按钮下方、占满整宽。
   */
  metaFull?: boolean
  /** 右上角操作区 */
  extra?: ReactNode
  /** 头部卡片里的其余内容（如报价版本切换、订单 KPI），跨整行显示 */
  children?: ReactNode
  style?: CSSProperties
}) {
  return (
    <div
      /* 头卡与下面的白卡之间要有一眼分界：四个详情页共用这个头部，
         以前它和普通卡片长得一模一样，整页就是"白卡叠白卡"。
         detail-header-card 只加一层极淡渐变 + 顶部一条渐变细线（见 index.css）。 */
      className="card-block detail-header-card"
      style={{ marginBottom: 'var(--crm-space-4)', ...style }}
    >
      {/* 宽度不够时自动换行：标题在上、操作按钮落到下方靠左（见 index.css） */}
      <div className="detail-header-grid">
        <div className="dh-main">
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--crm-space-3)',
              flexWrap: 'wrap',
            }}
          >
            <span className="detail-title">{title}</span>
            {tags}
          </div>
          {meta && !metaFull && <div className="detail-meta">{meta}</div>}
        </div>
        {extra && <div className="dh-actions">{extra}</div>}
        {meta && metaFull && <div className="detail-meta dh-full">{meta}</div>}
        {children && <div className="dh-full">{children}</div>}
      </div>
    </div>
  )
}
