import { useEffect, useRef, type MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Dropdown, Toast } from '@douyinfe/semi-ui'

import { TAB_LIMIT, useTabsStore, type TabItem } from '../../shared/store/tabs'

/** 页签栏：模块页签是“入口”，详情页签是“从入口点进去的记录”，两者样式分开。 */
export default function TabBar() {
  const navigate = useNavigate()
  const tabs = useTabsStore((state) => state.tabs)
  const activeKey = useTabsStore((state) => state.activeKey)
  const closeTab = useTabsStore((state) => state.closeTab)
  const autoClosed = useTabsStore((state) => state.autoClosed)
  const clearAutoClosed = useTabsStore((state) => state.clearAutoClosed)
  const tabRefs = useRef<Record<string, HTMLDivElement | null>>({})

  // 打开/切换页签后把它滚进可视区：页签多时，新开的那个不能藏在右边看不见。
  useEffect(() => {
    tabRefs.current[activeKey]?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activeKey, tabs])

  /* 到上限后自动关掉的页签要让人知道，不能无声消失。
     但连续切几个页面时会一次次触发（实测连开 4 个新页就弹 4 条、叠成一摞），
     所以这里改成「先攒着、半秒后再报一次」：这半秒内关掉的合成一条，
     只报总数和前几个名字；半秒后没有新的关闭了，就只报那一个。
     攒的时候立刻把 store 里那条清掉，避免同一条被重复计入。 */
  const pendingClosedRef = useRef<string[]>([])
  const flushTimerRef = useRef<number | null>(null)
  useEffect(() => {
    if (!autoClosed) return
    pendingClosedRef.current.push(autoClosed.label)
    clearAutoClosed()
    if (flushTimerRef.current) window.clearTimeout(flushTimerRef.current)
    flushTimerRef.current = window.setTimeout(() => {
      const labels = pendingClosedRef.current
      pendingClosedRef.current = []
      if (!labels.length) return
      if (labels.length === 1) {
        Toast.info(`页签最多保留 ${TAB_LIMIT} 个，已自动关闭最久未用的「${labels[0]}」`)
        return
      }
      const names = labels.slice(0, 3).join('、')
      Toast.info(
        `页签最多保留 ${TAB_LIMIT} 个，本次自动关闭了 ${labels.length} 个最久未用的：${names}${labels.length > 3 ? ' 等' : ''}`,
      )
    }, 500)
  }, [autoClosed, clearAutoClosed])

  const handleActivate = (tab: TabItem) => {
    useTabsStore.getState().setActive(tab.key)
    navigate(tab.path)
  }

  const handleClose = (event: MouseEvent, key: string) => {
    event.stopPropagation()
    const before = useTabsStore.getState().activeKey
    closeTab(key)
    const nextKey = useTabsStore.getState().activeKey
    // 关模块页签会连带收走它下面的详情页；如果当前正停在其中一条详情上，
    // activeKey 也会变，必须跟着导航过去，不能留在已关闭的页面上。
    if (nextKey !== before) {
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
          ref={(element) => { tabRefs.current[tab.key] = element }}
          className={`tab-chip ${tab.kind}${tab.key === activeKey ? ' active' : ''}`}
          title={tab.kind === 'detail' ? `详情页：${tab.label}` : tab.label}
          onClick={() => handleActivate(tab)}
        >
          {tab.kind === 'detail' && <span className="tab-kind">详情</span>}
          <span className="tab-label">{tab.label}</span>
          {tabs.length > 1 && (
            <span
              className="tab-close"
              role="button"
              aria-label={`关闭${tab.label}`}
              onClick={(event) => handleClose(event, tab.key)}
            >
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
            <Dropdown.Menu style={{ maxHeight: 360, overflowY: 'auto', minWidth: 220 }}>
              <div className="tab-menu-hint">
                最多 {TAB_LIMIT} 个页签，超出自动关闭最久未用的页签（先关详情）
              </div>
              {tabs.map((tab) => (
                <Dropdown.Item key={tab.key} onClick={() => handleActivate(tab)}>
                  <span className={`tab-menu-kind ${tab.kind}`}>
                    {tab.kind === 'module' ? '模块' : '详情'}
                  </span>
                  <span className="tab-menu-label">{tab.label}</span>
                  {tab.key === activeKey && <span className="tab-menu-active">当前</span>}
                </Dropdown.Item>
              ))}
              <Dropdown.Divider />
              <Dropdown.Item onClick={handleKeepOnlyActive}>关闭其他页签</Dropdown.Item>
              <Dropdown.Item onClick={handleCloseAll}>关闭全部页签</Dropdown.Item>
            </Dropdown.Menu>
          }
        >
          <span className="tab-more" title="全部页签">
            ⋯
          </span>
        </Dropdown>
      )}
    </div>
  )
}
