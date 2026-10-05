import type { MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Dropdown } from '@douyinfe/semi-ui'

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

  // 页签会随浏览不断累积，一个个叉太麻烦：提供两个批量清理入口
  const handleKeepOnlyActive = () => {
    useTabsStore.getState().closeOthers(activeKey)
  }

  const handleCloseAll = () => {
    useTabsStore.getState().closeAll()
    navigate('/workbench')
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
      {tabs.length > 1 && (
        <Dropdown
          trigger="click"
          position="bottomRight"
          render={
            <Dropdown.Menu>
              <Dropdown.Item onClick={handleKeepOnlyActive}>关闭其他页签</Dropdown.Item>
              <Dropdown.Item onClick={handleCloseAll}>关闭全部页签</Dropdown.Item>
            </Dropdown.Menu>
          }
        >
          <span className="tab-more" title="批量关闭页签">
            ⋯
          </span>
        </Dropdown>
      )}
    </div>
  )
}
