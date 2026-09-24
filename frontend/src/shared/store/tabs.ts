/** 多页面标签：关闭页面不丢列表筛选、返回列表保留页码的基础。 */

import { create } from 'zustand'

export interface TabItem {
  key: string
  label: string
  path: string
}

interface TabsState {
  tabs: TabItem[]
  activeKey: string
  openTab: (tab: TabItem) => void
  closeTab: (key: string) => void
  setActive: (key: string) => void
  closeAll: () => void
}

const HOME_TAB: TabItem = { key: '/workbench', label: '工作台', path: '/workbench' }

export const useTabsStore = create<TabsState>((set) => ({
  tabs: [HOME_TAB],
  activeKey: HOME_TAB.key,
  openTab: (tab) =>
    set((state) => {
      const exists = state.tabs.some((item) => item.key === tab.key)
      return {
        tabs: exists ? state.tabs : [...state.tabs, tab],
        activeKey: tab.key,
      }
    }),
  closeTab: (key) =>
    set((state) => {
      const tabs = state.tabs.filter((item) => item.key !== key)
      if (tabs.length === 0) {
        return { tabs: [HOME_TAB], activeKey: HOME_TAB.key }
      }
      const activeKey = state.activeKey === key ? tabs[tabs.length - 1].key : state.activeKey
      return { tabs, activeKey }
    }),
  setActive: (key) => set({ activeKey: key }),
  closeAll: () => set({ tabs: [HOME_TAB], activeKey: HOME_TAB.key }),
}))
