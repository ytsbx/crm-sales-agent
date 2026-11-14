/**
 * 页面内的大分组标题。
 *
 * 分析页、工作台都用它把「这页分几块、先看哪块」写到界面上 ——
 * 从前这些分组只存在于代码注释里，整页就是一片等权平铺的卡片。
 * primary 只给最该先看的那一组，用主色边条拉出主次。
 */
export default function SectionGroupHead({
  index,
  title,
  note,
  primary = false,
}: {
  index: string
  title: string
  note?: string
  primary?: boolean
}) {
  return (
    <div className={`section-group-head${primary ? ' primary' : ''}`}>
      <span className="section-group-index">{index}</span>
      <span className="section-group-title">{title}</span>
      {note && <span className="section-group-note">{note}</span>}
    </div>
  )
}
