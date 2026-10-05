import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { IconSearch } from '@douyinfe/semi-icons'

import { globalSearch } from '../../shared/api/search'

/** 顶栏全局搜索：输入即查，点结果直接跳转对应详情。 */
export default function GlobalSearch() {
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [open, setOpen] = useState(false)

  const query = useQuery({
    queryKey: ['global-search', keyword],
    queryFn: () => globalSearch(keyword, 4),
    enabled: keyword.trim().length > 0,
  })

  const groups = (query.data?.groups ?? []).filter((group) => group.items.length > 0)

  const go = (route: string, item: { id: number; customer_id?: number }) => {
    setOpen(false)
    setKeyword('')
    if (route === '/customers' && item.customer_id) {
      navigate(`/customers/${item.customer_id}`)
      return
    }
    navigate(`${route}/${item.id}`)
  }

  return (
    <div style={{ position: 'relative' }}>
      <div className="global-search">
        <IconSearch />
        <input
          value={keyword}
          onChange={(event) => {
            setKeyword(event.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
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
          style={{
            position: 'absolute',
            top: 42,
            left: 0,
            width: '100%',
            maxHeight: 420,
            overflow: 'auto',
            background: 'var(--crm-surface)',
            border: '1px solid var(--crm-surface-high)',
            borderRadius: 'var(--crm-radius)',
            boxShadow: 'var(--crm-shadow-lg)',
            padding: 8,
            zIndex: 50,
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
          {groups.map((group) => (
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
              {group.items.map((item) => (
                <div
                  key={`${group.type}-${item.id}`}
                  onMouseDown={() => go(group.route, item)}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 'var(--crm-radius-sm)',
                    cursor: 'pointer',
                  }}
                  onMouseEnter={(event) => {
                    event.currentTarget.style.background = 'var(--crm-surface-low)'
                  }}
                  onMouseLeave={(event) => {
                    event.currentTarget.style.background = 'transparent'
                  }}
                >
                  <div style={{ fontSize: 13 }}>{item.title}</div>
                  <div style={{ fontSize: 11, color: 'var(--crm-text-3)', marginTop: 2 }}>
                    {item.subtitle}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
