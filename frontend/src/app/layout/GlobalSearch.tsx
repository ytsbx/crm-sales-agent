import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { IconSearch } from '@douyinfe/semi-icons'
import { Button } from '@douyinfe/semi-ui'

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
  const [searchTerm, setSearchTerm] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  /** 结果层的落点（相对视口）：跟着输入框走，窄屏也不会超出右边界 */
  const [panel, setPanel] = useState({ left: 0, top: 56, width: 360, maxHeight: 420 })
  const boxRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const term = keyword.trim()

  useEffect(() => {
    const timer = window.setTimeout(() => setSearchTerm(term), 200)
    return () => window.clearTimeout(timer)
  }, [term])

  const query = useQuery({
    queryKey: ['global-search', searchTerm],
    queryFn: () => globalSearch(searchTerm, 4),
    enabled: open && Boolean(searchTerm) && searchTerm === term,
  })

  const waiting = term !== searchTerm || query.isPending
  const groups = !waiting && !query.isError ? (query.data?.groups ?? []).filter((group) => group.items.length > 0) : []
  const flat = groups.flatMap((group) => group.items.map((item) => ({ group, item })))
  const activeId = open && activeIndex >= 0 && activeIndex < flat.length ? `global-search-option-${activeIndex}` : undefined

  const syncPanel = () => {
    const rect = boxRef.current?.getBoundingClientRect()
    if (!rect) return
    const width = Math.min(420, window.innerWidth - 24)
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
    const top = Math.round(rect.bottom + 6)
    setPanel({ left, top, width, maxHeight: Math.min(420, Math.max(0, window.innerHeight - top - 12)) })
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
    if (!open && term && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault()
      setOpen(true)
      syncPanel()
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

  useEffect(() => {
    if (activeId) document.getElementById(activeId)?.scrollIntoView({ block: 'nearest' })
  }, [activeId])

  return (
    <div
      style={{ position: 'relative' }}
      ref={boxRef}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          setOpen(false)
          setActiveIndex(-1)
        }
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || !open) return
        event.preventDefault()
        inputRef.current?.focus()
        setOpen(false)
        setActiveIndex(-1)
      }}
    >
      <div className="global-search">
        <IconSearch />
        <input
          ref={inputRef}
          value={keyword}
          role="combobox"
          aria-label="全局搜索"
          aria-expanded={open && Boolean(term) && flat.length > 0}
          aria-controls={open && flat.length > 0 ? 'global-search-results' : undefined}
          aria-activedescendant={activeId}
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

      {open && term && (
        <div
          id="global-search-panel"
          style={{
            // fixed 而不是 absolute：页头有 overflow:hidden，absolute 会被裁掉
            position: 'fixed',
            top: panel.top,
            left: panel.left,
            width: panel.width,
            maxHeight: panel.maxHeight,
            overflow: 'auto',
            background: 'var(--crm-surface)',
            border: '1px solid var(--crm-surface-high)',
            borderRadius: 'var(--crm-radius)',
            boxShadow: 'var(--crm-shadow-lg)',
            padding: 8,
            zIndex: 60,
          }}
        >
          {waiting && (
            <div role="status" style={{ padding: 12, color: 'var(--crm-text-3)', fontSize: 13 }}>搜索中…</div>
          )}
          {!waiting && query.isError && (
            <div style={{ padding: 12 }}>
              <p role="status" className="workflow-guide">搜索暂时不可用，请重试。</p>
              <Button size="small" loading={query.isFetching} onClick={() => {
                inputRef.current?.focus()
                void query.refetch()
              }}>重试搜索</Button>
            </div>
          )}
          {!waiting && !query.isError && groups.length === 0 && (
            <div role="status" style={{ padding: 12, color: 'var(--crm-text-3)', fontSize: 13 }}>
              没有找到「{term}」相关记录。可以试试客户名称或业务单号。
            </div>
          )}
          {flat.length > 0 && <div id="global-search-results" role="listbox" aria-label="搜索结果">{groups.map((group) => {
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
                      id={`global-search-option-${flatIndex}`}
                      role="option"
                      aria-selected={active}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => go(group.route, item)}
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
          })}</div>}
        </div>
      )}
    </div>
  )
}
