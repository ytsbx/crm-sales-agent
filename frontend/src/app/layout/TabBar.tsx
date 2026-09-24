import type { MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { useTabsStore } from '../../shared/store/tabs'

export default function TabBar() {
  const navigate = useNavigate()
  const tabs = useTabsStore((state) => state.tabs)
  const activeKey = useTabsStore((state) => state.activeKey)
  const closeTab = useTabsStore((state) => state.closeTab)

  const handleActivate = (key: string, path: string) => {
    useTabsStore.getState().setActive(key)
    navigate(path)
  }

  const handleClose = (event: MouseEvent, key: string) => {
    event.stopPropagation()
    const isActive = key === activeKey
    closeTab(key)
    if (isActive) {
      const nextKey = useTabsStore.getState().activeKey
      const next = useTabsStore.getState().tabs.find((item) => item.key === nextKey)
      navigate(next?.path ?? '/workbench')
    }
  }

  return (
    <div className="tab-strip">
      {tabs.map((tab) => (
        <div
          key={tab.key}
          className={tab.key === activeKey ? 'tab-chip active' : 'tab-chip'}
          onClick={() => handleActivate(tab.key, tab.path)}
        >
          {tab.label}
          {tabs.length > 1 && (
            <span className="tab-close" onClick={(event) => handleClose(event, tab.key)}>
              ✕
            </span>
          )}
        </div>
      ))}
    </div>
  )
}
