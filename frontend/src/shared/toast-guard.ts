/**
 * 提示条兜底闸：**不允许出现「只有红点、没有一个字」的提示条**。
 *
 * 主人 2026-10-06 反馈：「报错弹条没有文字（只有个红点）—— 查价坏掉那会儿你完全
 * 不知道发生了什么」，并要求「报错必须带文字；『系统内部错误』也比空白强」。
 *
 * 空文案可能有几个来源，且分布在 80 多个调用点上：
 *   · 接口返回的 message 是空串（`??` 兜不住空串，见 api/client.ts 的处理）
 *   · 抛出的不是 Error，`e.message` 于是是 undefined
 *   · 调用方漏传参数
 * 逐个去堵调用点既慢又一定会漏，而且以后新写的代码照样会漏，所以在**唯一出口**
 * 统一兜一道：正文字符串取不到可用内容时，按提示级别换成一句人话。
 *
 * 实现方式是给 Semi 的 Toast 四个级别各包一层。只改「正文」怎么取，其余参数
 * （duration、position、图标…）原样透传，所以不改变任何既有行为。
 * 安装失败也不能影响应用启动，因此整体包了 try/catch —— 兜底闸本身不能成为
 * 新的故障点。
 */

import { Toast } from '@douyinfe/semi-ui'

/** 各级别的兜底文案。宁可说「系统内部错误」，也不要给用户一个没有字的红点。 */
const FALLBACK: Record<string, string> = {
  error: '系统内部错误，请稍后重试或联系管理员',
  warning: '操作未完成，请检查填写内容后重试',
  success: '操作已完成',
  info: '提示',
}

function usableText(value: unknown): value is string {
  return typeof value === 'string' && value.trim() !== ''
}

/**
 * 归一化 Toast 的入参。
 * 这个组件库支持两种写法：`Toast.error('文案')` 和 `Toast.error({ content: '文案' })`，
 * 两种都要兜住。
 */
function withText(input: unknown, fallback: string): unknown {
  if (typeof input === 'string') return usableText(input) ? input : fallback
  if (input === undefined || input === null) return fallback
  if (typeof input === 'object') {
    const config = { ...(input as Record<string, unknown>) }
    if (!usableText(config.content)) config.content = fallback
    return config
  }
  return input
}

export function installToastGuard(): void {
  try {
    const toast = Toast as unknown as Record<string, unknown>
    for (const level of Object.keys(FALLBACK)) {
      const original = toast[level]
      if (typeof original !== 'function') continue
      const bound = (original as (...args: unknown[]) => unknown).bind(Toast)
      toast[level] = (...args: unknown[]) => {
        // 第一个参数是正文；没有参数时也补上兜底文案
        const [content, ...rest] = args
        return bound(withText(content, FALLBACK[level]), ...rest)
      }
    }
  } catch (error) {
    // 兜底闸装不上不影响应用：最多是回到「可能空文案」的老样子
    console.warn('提示条兜底闸安装失败', error)
  }
}
