import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { IconSearch } from '@douyinfe/semi-icons'

import { globalSearch } from '../../shared/api/search'

/**
 * 顶栏全局搜索：输入即查，点结果直接跳转对应详情。
 *
 * ⚠️ 2026-11-17 视觉审视 P1（V2-02）：「结果已经返回，但浮层被页头裁掉」。
 * 原因：结果层原来是 `position: absolute`，挂在搜索框的 relative 容器里，
 * 而页头本身 `overflow: hidden`（高度 56）。结果层从 top:42 往下铺，
 * 超出的部分全被页头切掉 —— 用户以为"搜索没反应"，其实数据早就回来了。
 *
 * 修法：结果层改成 `position: fixed`（浮在最上层，坐标按输入框实时量），
 * 并补上报告要求的键盘路径（↑↓ 选择、Enter 进入、Esc 关闭）与读屏可读的结构。
 * 只改展示与交互反馈，搜索接口与跳转目标不变。
 */
export default function GlobalSearch() {
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  /** 结果层的落点（相对视口）：跟着输入框走，窄屏也不会超出右边界 */
  const [panel, setPanel] = useState({ left: 0, top: 56, width: 360 })
  const boxRef = useRef<HTMLDivElement | null>(null)

  const query = useQuery({
    queryKey: ['global-search', keyword],
    queryFn: () => globalSearch(keyword, 4),
    enabled: keyword.trim().length > 0,
  })

  const groups = (query.data?.groups ?? []).filter((group) => group.items.length > 0)

  /** 把分组摊平成一个可上下移动的列表，键盘选择才有一条顺序线 */
  const flat = useMemo(
    () =>
      groups.flatMap((group) =>
        group.items.map((item) => ({ group, item })),
      ),
    [groups],
  )

  const syncPanel = () => {
    const rect = boxRef.current?.getBoundingClientRect()
    if (!rect) return
    const width = Math.min(420, window.innerWidth - 24)
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
    setPanel({ left, top: Math.round(rect.bottom + 6), width })
  }

  const go = (route: string, item: { id: number; customer_id?: number }) => {
    setOpen(false)
    setKeyword('')
    setActiveIndex(-1)
    if (route === '/customers' && item.customer_id) {
      navigate(`/customers/${item.customer_id}`)
      return
    }
    navigate(`${route}/${item.id}`)
  }

  // 窗口变化时结果层跟着重算，避免停在旧坐标上被边缘切断
  useEffect(() => {
    if (!open) return
    const onResize = () => syncPanel()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  })

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      setOpen(false)
      setActiveIndex(-1)
      return
    }
    if (!flat.length) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((index) => (index + 1) % flat.length)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((index) => (index <= 0 ? flat.length - 1 : index - 1))
      return
    }
    if (event.key === 'Enter' && activeIndex >= 0) {
      const picked = flat[activeIndex]
      if (picked) {
        event.preventDefault()
        go(picked.group.route, picked.item)
      }
    }
  }

  return (
    <div style={{ position: 'relative' }} ref={boxRef}>
      <div className="global-search">
        <IconSearch />
        <input
          value={keyword}
          role="combobox"
          aria-expanded={open && Boolean(keyword.trim())}
          aria-controls="global-search-panel"
          aria-autocomplete="list"
          onChange={(event) => {
            setKeyword(event.target.value)
            setOpen(true)
            setActiveIndex(-1)
            syncPanel()
          }}
          onFocus={() => {
            setOpen(true)
            syncPanel()
          }}
          onKeyDown={onKeyDown}
          onBlur={() => window.setTimeout(() => setOpen(false), 180)}
          placeholder="搜索客户、商机、报价、订单、联系人…"
          style={{
            flex: 1,
            border: 'none',
            outline: 'none',
            background: 'transparent',
            fontSize: 13,
            color: 'var(--crm-text)',
          }}
        />
      </div>

      {open && keyword.trim() && (
        <div
          id="global-search-panel"
          role="listbox"
          style={{
            // fixed 而不是 absolute：页头有 overflow:hidden，absolute 会被裁掉
            position: 'fixed',
            top: panel.top,
            left: panel.left,
            width: panel.width,
            maxHeight: 420,
            overflow: 'auto',
            background: 'var(--crm-surface)',
            border: '1px solid var(--crm-surface-high)',
            borderRadius: 'var(--crm-radius)',
            boxShadow: 'var(--crm-shadow-lg)',
            padding: 8,
            zIndex: 60,
          }}
        >
          {query.isLoading && (
            <div style={{ padding: 12, color: 'var(--crm-text-3)', fontSize: 13 }}>搜索中…</div>
          )}
          {!query.isLoading && groups.length === 0 && (
            <div style={{ padding: 12, color: 'var(--crm-text-3)', fontSize: 13 }}>
              没有找到「{keyword}」相关记录
            </div>
          )}
          {groups.map((group) => {
            // 每个分组内序号 → 摊平后的序号，键盘高亮才对得上
            let offset = 0
            for (const g of groups) {
              if (g === group) break
              offset += g.items.length
            }
            return (
              <div key={group.type} style={{ marginBottom: 6 }}>
                <div
                  style={{
                    padding: '6px 10px',
                    fontSize: 11,
                    color: 'var(--crm-text-3)',
                  }}
                >
                  {group.label}（{group.items.length}）
                </div>
                {group.items.map((item, index) => {
                  const flatIndex = offset + index
                  const active = flatIndex === activeIndex
                  return (
                    <div
                      key={`${group.type}-${item.id}`}
                      role="option"
                      aria-selected={active}
                      onMouseDown={() => go(group.route, item)}
                      onMouseEnter={() => setActiveIndex(flatIndex)}
                      style={{
                        padding: '8px 10px',
                        borderRadius: 'var(--crm-radius-sm)',
                        cursor: 'pointer',
                        background: active ? 'var(--crm-surface-low)' : 'transparent',
                      }}
                    >
                      <div style={{ fontSize: 13 }}>{item.title}</div>
                      <div style={{ fontSize: 11, color: 'var(--crm-text-3)', marginTop: 2 }}>
                        {item.subtitle}
                      </div>
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
