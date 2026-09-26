import { create } from 'zustand'

/**
 * 全局 Copilot 抽屉的开关状态。
 *
 * 放在 zustand 而不是 AppLayout 的 useState 里：设计稿的快捷入口除了顶栏，
 * 详情页里的「AI 洞察」卡片也要能把它拉起来（例如报价详情「生成商务函」）。
 */
interface CopilotState {
  open: boolean
  /** 打开抽屉时希望自动带入的问题（快捷按钮用）。 */
  seed: string | null
  openWith: (seed?: string) => void
  close: () => void
  clearSeed: () => void
}

export const useCopilotStore = create<CopilotState>((set) => ({
  open: false,
  seed: null,
  openWith: (seed) => set({ open: true, seed: seed ?? null }),
  close: () => set({ open: false }),
  clearSeed: () => set({ seed: null }),
}))
