import type { CSSProperties, ReactNode } from 'react'

/**
 * 表单字段的标签行。
 *
 * 存在的理由只有一个：**必填的星号要能一眼看见**。
 *
 * 以前全站都是把 `*` 直接拼在标签文字里（`<div>跟进内容 *</div>`），
 * 星号是个纯文本节点 —— 文本节点的颜色只能继承父级，CSS 也选不中它
 * （没有选择器能命中「文本里某个字符」）。所以星号和标签永远同一个颜色，
 * 扫一遍根本看不出哪些是必填。
 *
 * 要单独上色，只能把星号变成独立标签。样式统一在 index.css 的
 * `.required-mark`：**全站必填标记只有这一个来源，新页面别再手写 `*`。**
 */
export default function FormLabel({
  children,
  required,
  conditionallyRequired,
  hint,
  style,
}: {
  children: ReactNode
  /** 必填：在标签后跟一个红色星号 */
  required?: boolean
  /**
   * **条件必填**：在标签后跟一个灰色空心星 `*`，并且**必须同时给 `hint`**
   * 说明"什么条件下必填"。
   *
   * ## 为什么要有这一档（主人 2026-10-13 截图反馈）
   *
   * 案例库的「关键动作 / 可复用做法」是**二选一**（提交审核时至少填一项），
   * 存草稿时两项都不要求。所以：
   *   · 打**实心红星**是撒谎 —— 看起来两项都必填、而且存草稿就要填
   *     （2026-10-06 就是因此把星号去掉的）；
   *   · 但只留一行灰色小字，规则**不显眼** —— 主人扫一眼的结论就是
   *     "必填的怎么没标星号"。
   *
   * 所以给第三档：**空心灰星**代表"有条件下必填"，比纯文字显眼、
   * 又不会被误读成"现在就必填"。空心 vs 实心的区别写在 `index.css`
   * 的 `.required-mark-conditional`，并在标签后用 `hint` 把条件讲出来
   * （`ops/check_form_required_marks.py` 会强制"标了就必须有 hint"）。
   */
  conditionallyRequired?: boolean
  /**
   * 灰色小字说明，跟在标签（和星号）后面。
   *
   * 专门给「不是一句必填/选填能说清」的情况用 —— 典型例子是案例库的
   * 「关键动作 / 可复用做法」：**存草稿时不要求，提交审核时要求两者至少填一项**。
   * 这种规则用星号标不出来（标了就是假的），只能用一句话讲明白。
   */
  hint?: string
  /** 个别地方标签的字号/颜色本来就不同（如设置页用小号次要色），从这里覆盖 */
  style?: CSSProperties
}) {
  return (
    <div style={{ marginBottom: 4, ...style }}>
      {children}
      {required && (
        // aria-hidden：读屏器念一声「星号」对使用者没有帮助，必填最终由
        // 表单校验兜底。这里只是给人看的视觉提示，不假装是无障碍标记。
        <span className="required-mark" aria-hidden="true">
          *
        </span>
      )}
      {!required && conditionallyRequired && (
        // 空心灰星：有条件下必填。空心是为了与"现在就必填"的实心红星区分开，
        // 具体条件由旁边的 hint 说清（标了星却不给 hint 会被静态检查拦下）。
        <span className="required-mark-conditional" aria-hidden="true">
          *
        </span>
      )}
      {hint && <span className="label-hint">{hint}</span>}
    </div>
  )
}
