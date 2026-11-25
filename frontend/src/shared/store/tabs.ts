/** 多页面标签：关闭页面不丢列表筛选、返回列表保留页码的基础。 */

import { create } from 'zustand'

/** 页签类型：模块入口（左侧菜单）与详情页（从列表点进去的记录）。 */
export type TabKind = 'module' | 'detail'

export interface TabItem {
  key: string
  label: string
  path: string
  kind: TabKind
  /** 详情页所属模块页签的 key（如 `/customers`）；模块页签为空。 */
  parentKey?: string
  /** 最近一次激活的顺序号，只用于“超出上限时先关谁”。 */
  lastUsed?: number
}

/** 页签上限。工作台固定保留；超限时先关最久未用的详情页签，不够再关模块页签。 */
export const TAB_LIMIT = 10

interface TabsState {
  tabs: TabItem[]
  activeKey: string
  /** 最近一次因超限被自动关掉的页签；TabBar 用它提示用户，提示完清空。 */
  autoClosed: { label: string; seq: number } | null
  openTab: (tab: TabItem) => void
  closeTab: (key: string) => void
  setActive: (key: string) => void
  closeAll: () => void
  closeOthers: (key: string) => void
  /** 详情页数据回来后，把“客户详情”这类通用标题换成客户名/报价号。 */
  updateTabLabel: (key: string, label: string) => void
  clearAutoClosed: () => void
}

const HOME_TAB: TabItem = {
  key: '/workbench',
  label: '工作台',
  path: '/workbench',
  kind: 'module',
  lastUsed: 0,
}

let usageSeq = 0
let autoCloseSeq = 0

function touch(tab: TabItem): TabItem {
  return { ...tab, lastUsed: ++usageSeq }
}

/** 详情路由 → 所属模块 key。只用于兜底（正常由 AppLayout 传入 parentKey）。 */
function parentKeyOf(path: string): string | undefined {
  const first = path.split('/').filter(Boolean)[0]
  return first ? `/${first}` : undefined
}

/**
 * 超出上限时决定关谁。
 *
 * 顺序：先关最久未用的**详情**页签；详情都关完了还不够，再关最久未用的**模块**
 * 页签（连同它下面的详情）。当前页签、工作台、以及当前详情所属的模块不参与淘汰 ——
 * 否则用户刚点开的那一页可能立刻被自己挤掉。
 */
function trimTabs(
  tabs: TabItem[],
  activeKey: string,
  protectedKey?: string,
): { tabs: TabItem[]; closed: TabItem | null } {
  if (tabs.length <= TAB_LIMIT) return { tabs, closed: null }

  let result = [...tabs]
  let closed: TabItem | null = null
  const active = result.find((item) => item.key === activeKey)
  const protectedParent =
    protectedKey ?? (active?.kind === 'detail' ? active.parentKey : undefined)
  const removable = (item: TabItem) =>
    item.key !== activeKey && item.key !== HOME_TAB.key && item.key !== protectedParent

  const detailVictims = result
    .filter((item) => item.kind === 'detail' && removable(item))
    .sort((a, b) => (a.lastUsed ?? 0) - (b.lastUsed ?? 0))
  for (const victim of detailVictims) {
    if (result.length <= TAB_LIMIT) break
    result = result.filter((item) => item.key !== victim.key)
    closed = victim
  }

  const moduleVictims = result
    .filter((item) => item.kind === 'module' && removable(item))
    .sort((a, b) => (a.lastUsed ?? 0) - (b.lastUsed ?? 0))
  for (const victim of moduleVictims) {
    if (result.length <= TAB_LIMIT) break
    // 关模块时连同它下面的详情一起收走，避免无主详情继续占位。
    result = result.filter((item) => item.key !== victim.key && item.parentKey !== victim.key)
    closed = victim
  }

  return { tabs: result, closed }
}

export const useTabsStore = create<TabsState>((set) => ({
  tabs: [HOME_TAB],
  activeKey: HOME_TAB.key,
  autoClosed: null,
  openTab: (tab) =>
    set((state) => {
      const existing = state.tabs.find((item) => item.key === tab.key)
      const tabs = existing
        ? state.tabs.map((item) => (item.key === tab.key ? touch(item) : item))
        : [...state.tabs, touch(tab)]
      const trimmed = trimTabs(tabs, tab.key)
      return {
        tabs: trimmed.tabs,
        activeKey: tab.key,
        autoClosed: trimmed.closed
          ? { label: trimmed.closed.label, seq: ++autoCloseSeq }
          : state.autoClosed,
      }
    }),
  closeTab: (key) =>
    set((state) => {
      const closing = state.tabs.find((item) => item.key === key)
      // 关模块页签时，把挂在它下面的详情页签一起收走，避免留下无主详情。
      const removed = new Set<string>([key])
      if (closing?.kind === 'module') {
        state.tabs.filter((item) => item.parentKey === key).forEach((item) => removed.add(item.key))
      }
      const tabs = state.tabs.filter((item) => !removed.has(item.key))
      if (tabs.length === 0) {
        return { tabs: [HOME_TAB], activeKey: HOME_TAB.key }
      }
      if (!removed.has(state.activeKey)) {
        return { tabs, activeKey: state.activeKey }
      }
      // 关掉当前页签后落到它左边那个；没有左边就落到新位置上的第一个。
      const oldIndex = state.tabs.findIndex((item) => item.key === key)
      const next = tabs[Math.min(Math.max(oldIndex - 1, 0), tabs.length - 1)] ?? HOME_TAB
      return { tabs, activeKey: next.key }
    }),
  setActive: (key) =>
    set((state) => ({
      activeKey: key,
      tabs: state.tabs.map((item) => (item.key === key ? touch(item) : item)),
    })),
  closeAll: () => set({ tabs: [HOME_TAB], activeKey: HOME_TAB.key, autoClosed: null }),
  closeOthers: (key) =>
    set((state) => {
      const active = state.tabs.find((item) => item.key === key)
      const keep = new Set<string>([key])
      // 详情页的模块父页签一起留着，否则“关闭其他”会把用户刚离开的模块也收走。
      if (active?.kind === 'detail' && active.parentKey) keep.add(active.parentKey)
      const tabs = state.tabs.filter((item) => keep.has(item.key))
      return tabs.length > 0
        ? { tabs, activeKey: key }
        : { tabs: [HOME_TAB], activeKey: HOME_TAB.key }
    }),
  updateTabLabel: (key, label) =>
    set((state) => {
      if (!label.trim()) return state
      if (state.tabs.some((item) => item.key === key)) {
        return {
          tabs: state.tabs.map((item) =>
            item.key === key && item.label !== label ? { ...item, label } : item,
          ),
        }
      }
      // 子页面 effect 可能先于 AppLayout 跑：此时页签还没建，补一条详情页签，
      // 等 AppLayout 的 openTab 认领（它不会覆盖已存在的标题）。
      const created = touch({
        key,
        label,
        path: key,
        kind: 'detail',
        parentKey: parentKeyOf(key),
      })
      const trimmed = trimTabs([...state.tabs, created], key, created.parentKey)
      return {
        tabs: trimmed.tabs,
        activeKey: state.activeKey,
        autoClosed: trimmed.closed
          ? { label: trimmed.closed.label, seq: ++autoCloseSeq }
          : state.autoClosed,
      }
    }),
  clearAutoClosed: () => set({ autoClosed: null }),
}))
