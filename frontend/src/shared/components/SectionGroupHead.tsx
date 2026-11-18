/**
 * 页面内的大分组标题。
 *
 * 分析页、工作台都用它把「这页分几块、先看哪块」写到界面上 ——
 * 从前这些分组只存在于代码注释里，整页就是一片等权平铺的卡片。
 * primary 只给最该先看的那一组，用主色边条拉出主次。
 */
export default function SectionGroupHead({
  // index 不再显示：一排「01/02/03」灰底小方块挤在标题前，
  // 既不是目录也不是版本号，读起来像调试标记（主人 2026-11-18 指出）。
  // 保留 prop 是为了不牵动十几个调用点；要恢复可以从这里接回来。
  index: _index,
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
      <span className="section-group-title">{title}</span>
      {note && <span className="section-group-note">{note}</span>}
    </div>
  )
}
