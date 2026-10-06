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
  hint,
  style,
}: {
  children: ReactNode
  /** 必填：在标签后跟一个红色星号 */
  required?: boolean
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
      {hint && <span className="label-hint">{hint}</span>}
    </div>
  )
}
