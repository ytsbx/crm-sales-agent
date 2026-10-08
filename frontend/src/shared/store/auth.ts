import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import type { CurrentUser } from '../types'

/**
 * 「登录代」计数器（第十批 10.12）。
 *
 * 退出是**瞬间**的一下（清本地状态），但此刻可能还有请求在路上。一个
 * 「退出前发出、退出后才回来」的响应，如果直接把自己拿到的 token / 用户写回
 * store，界面就会"自己又登回去了" —— 人明明点了退出，却发现自己还在系统里。
 *
 * 所以：**建立或清除登录态时都把代加一**；任何异步流程在写回登录态之前，
 * 先比一下"现在还是我出发时那一代吗"，对不上就丢弃这次结果。
 *
 * 放在模块级、**不进 store**、**不被持久化**：刷新页面后旧请求本来就已经没了，
 * 代从 0 重新开始才是对的；持久化反而会让"上一次遗留的号码"干扰这一次判断。
 */
let authEpoch = 0

/** 取当前的「登录代」：异步流程出发前记下它，写回登录态前再比一次。 */
export function currentAuthEpoch(): number {
  return authEpoch
}

interface AuthState {
  token: string | null
  user: CurrentUser | null
  setAuth: (token: string, user: CurrentUser | null) => void
  setUser: (user: CurrentUser) => void
  clear: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      // 建立登录态也算"换代"：这样"上一次登录期间发出、这一次登录之后才回来"
      // 的响应同样会被丢掉，而不是把新会话的 token 覆盖成旧的。
      setAuth: (token, user) => {
        authEpoch += 1
        set({ token, user })
      },
      setUser: (user) => set({ user }),
      clear: () => {
        authEpoch += 1
        set({ token: null, user: null })
      },
    }),
    { name: 'crm-auth' },
  ),
)
