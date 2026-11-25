/**
 * 日历日期 → `YYYY-MM-DD` 字符串。**全项目只此一处**（第九批问题 ②）。
 *
 * ## 为什么不能写 `toISOString().slice(0, 10)`
 *
 * `toISOString()` 转的是 **UTC**，而用户在日历上点的是**当地那一天**。
 * 北京（UTC+8）选出 10 月 1 日、若这个 Date 是**当地时间零点**，转成 UTC 就变成
 * 9 月 30 日 16:00 —— 截出来是 **09-30**，整整早一天（跨月时还会把月份也带错）。
 *
 * 实测（本机 Asia/Shanghai）：
 *
 *     new Date(2026, 9, 1)                    → 本地 2026-10-01，UTC 截日 2026-09-30 ❌
 *     new Date('2026-10-01T08:00:00+08:00')   → 本地 2026-10-01，UTC 截日 2026-10-01 ✅
 *
 * 也就是说：**只有当地零点会翻车**，而日历控件给的恰恰常常是零点
 * （Semi 的 DatePicker 内部用 `startOfDay` 归一）。所以这个 bug 是"有时候错、
 * 有时候不错"，最难查的那种。
 *
 * ## 口径
 *
 * **按当地年月日取**：用户点哪天就送哪天。后端也只当它是"日历上的那一天"。
 *
 * 项目里原本就有人这么写（`LeadListPage`、`PriceCenterPage` 手拼年月日），
 * 也有人用 UTC 截日 —— 两种口径并存正是这个 bug 的来源。现在统一到这里。
 */

const pad = (value: number) => String(value).padStart(2, '0')

/** 把一个 Date 取成**当地**的 `YYYY-MM-DD`。`null` / 非法日期返回 null。 */
export function calendarDay(value: Date | null | undefined): string | null {
  if (!value) return null
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 同上，但不接受空值：给"必须给一个日期"的地方用（例如分期首期）。 */
export function calendarDayOrToday(value: Date | null | undefined): string {
  return calendarDay(value) ?? calendarDay(new Date())!
}
