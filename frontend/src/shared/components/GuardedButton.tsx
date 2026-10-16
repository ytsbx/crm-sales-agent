import { Button } from '@douyinfe/semi-ui'
import type { ButtonProps } from '@douyinfe/semi-ui/lib/es/button'

import BlockedHint, { useBlockedNotify } from './BlockedHint'
import { distinctReasons } from './blocked-hint-reasons'

/**
 * 「灰掉时自己解释为什么」的按钮 —— 页面里**不用再手写** BlockedHint + 包装 div。
 *
 * ## 为什么要再加一层（`BlockedHint` 不是已经有了吗）
 *
 * 有，而且规矩（看得见 + 点得动）就是它定的。问题是**没人用**：
 * 全项目 35 个文件里有禁用控件，只有 19 个引了 `BlockedHint`，
 * 新写的页面基本都漏（2026-10-15 一轮就补了三个文件的例外登记）。
 *
 * 原因不在"不知道"，在**摩擦**：照现有写法，一个灰按钮要写
 *
 *     const notifyBlocked = useBlockedNotify(reasons, '建立订单草稿')
 *     <div className="blocked-hint-click" onClick={notifyBlocked}>
 *       <Button disabled={blocked} style={blocked ? { pointerEvents: 'none' } : undefined}
 *               onClick={run}>建立订单草稿</Button>
 *     </div>
 *     <BlockedHint reasons={reasons} action="建立订单草稿" />
 *
 * —— 八行、三层、还得自己记住 `pointerEvents: 'none'` 和那个 CSS 类名。
 * 写新页面的人只会写 `<Button>`。**所以把正确写法压成一个标签**：
 *
 *     <GuardedButton disabled={blocked} blockedReasons={reasons}
 *                    action="建立订单草稿" onClick={run}>建立订单草稿</GuardedButton>
 *
 * ## 两条规矩都由本件保证（页面不用操心）
 *
 * - **看得见**：`disabled` 为真时，按钮下面常驻一行灰字说明缺什么；
 * - **点得动**：禁用时点一下弹提示条 —— 靠"禁用态给按钮 `pointer-events: none`，
 *   让点击冒泡到外层捕获"实现（禁用按钮自己不派发 click，这是唯一可靠的做法）。
 *
 * ⚠️ **不禁用按钮本身**：`disabled` 由调用方给。少了这层，改动就从"补提示"
 * 变成了"放松口径"（详见 `BlockedHint.tsx` 的说明）。
 *
 * ## 什么时候**不该**用它
 *
 * 禁用原因**眼睛一看就明白**的，别用它加噪 —— 判断标准见
 * `ops/check_disabled_hint.py` 的例外清单（提交中的 loading、分页到头、
 * 明细只剩一行不能删、加载失败旁边已经写着错误）。这类直接写 `<Button>` 并登记例外。
 * 本件是给**"条件不满足但用户看不出为什么"**那种按钮用的。
 */
/**
 * 这次冒泡上来的点击，要不要拦下来弹提示？
 *
 * 抽成独立函数是为了**能单测**：内联在 `onClick` 里的话，要验它就得装
 * jsdom + React 测试栈，而这个判断本身是纯逻辑。
 *
 * 判据只有一条：**按钮确实被挡住**。
 * 可用态必须返回 `false`（放过去）—— 否则就成了"能点也不响应"，
 * 那比不解释更糟（`BlockedHint.tsx` 里专门警告过这一条）。
 */
export function shouldInterceptClick(blocked: boolean): boolean {
  return blocked
}

export interface GuardedButtonProps extends Omit<ButtonProps, 'disabled'> {
  /** 是否被挡住（不能点）。**本件不禁用按钮本身**，只负责解释。 */
  disabled?: boolean
  /**
   * 为什么被挡住 —— 每一句都要是**用户能懂、能行动**的话
   * （「还没选客户」而不是「customerId is null」）。空/不传时只禁用、不解释。
   */
  blockedReasons?: readonly string[]
  /**
   * 被挡住的是哪个动作，用来起头：「还不能{action}：…」。
   * 与 `BlockedHint` 同一个口径，不传就不写这句头。
   */
  action?: string
  /**
   * 把提示块移到**整行下面**（而不是紧跟在按钮那一列里）。
   *
   * 用在横排表单行里：`GuardedButton` 只占一列，提示挤在那一列宽度内、
   * 右边控件下面全空着，看着就是"左边一大段空白"（主人 2026-10-16）。
   * 传上它就变成"按钮留在行里、灰字落到行下面"，宽度够也不挤。
   */
  hintBelow?: boolean
}

export default function GuardedButton({
  disabled = false,
  blockedReasons,
  action,
  hintBelow = false,
  children,
  ...buttonProps
}: GuardedButtonProps) {
  const reasons = blockedReasons ?? []
  const notifyBlocked = useBlockedNotify(reasons, action)
  // 与 BlockedHint / 提示条同一套判据：去重后还有没有话说
  const hasReason = distinctReasons(reasons.filter((t) => t.trim() !== '')).length > 0

  /** 提示块本体（紧凑样式，见 `BlockedHint` 的 `compact` 说明）。 */
  const hint = disabled && hasReason
    ? <BlockedHint reasons={reasons} action={action} compact />
    : null

  const button = (
    <span
      className="blocked-hint-click"
      style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-start' }}
      onClick={() => {
        // 可用态下**必须放过去**（不吞事件），否则就成了"能点也不响应"。
        // `useBlockedNotify` 自己也判了一次（没有原因时什么都不做），
        // 这里再挡一道是为了不依赖它的内部行为。
        if (shouldInterceptClick(disabled)) notifyBlocked()
      }}
    >
      <Button
        {...buttonProps}
        disabled={disabled}
        // 禁用时让按钮不接事件，点击才能冒泡到上面那层去弹提示。
        // 可用态**绝不能**这么干 —— 那会把按钮自己的点击也吃掉。
        style={disabled ? { ...buttonProps.style, pointerEvents: 'none' } : buttonProps.style}
      >
        {children}
      </Button>
      {/* 在"跟随按钮"模式下，灰字排在按钮下面 */}
      {!hintBelow ? hint : null}
    </span>
  )

  // `hintBelow`：按钮留在这一行，**提示落到整行下面**（占满整行宽）。
  //
  // 为什么需要它（2026-10-16 主人：「这段话可以移到添加旁边去，不然左边一大段空白
  // 不好看」）：在横排表单行里，`GuardedButton` 只占**一列** —— 提示被挤在那一列的
  // 宽度里，而右边那几个控件下面全空着，看着就是"左边一大段空白"。
  // 传 `hintBelow` 后提示被移出那一列，成为整行之后的独立一块，宽度够、也不再挤。
  if (hintBelow) {
    return (
      <>
        {button}
        {hint}
      </>
    )
  }
  return button
}
