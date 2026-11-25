import { useEffect, useState, type CSSProperties } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Button, Select } from '@douyinfe/semi-ui'
import { listUsers } from '../api/system'

/** 按需搜索用户；每页 20 人，选中项在切换关键词时继续保留。 */
export default function UserSearchSelect({ queryScope, activeOnly, enabled, value, fallbackLabel, onChange, placeholder, className, style, disabled }: {
  queryScope: string
  activeOnly?: boolean
  enabled: boolean
  value?: number
  fallbackLabel?: string
  onChange: (value: number | undefined, name?: string) => void
  placeholder?: string
  className?: string
  style?: CSSProperties
  disabled?: boolean
}) {
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selected, setSelected] = useState<{ label: string; value: number; name: string } | null>(null)
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!enabled) setSearch('')
      setKeyword(enabled ? search.trim() : '')
    }, enabled ? 250 : 0)
    return () => clearTimeout(timer)
  }, [enabled, search])
  const query = useInfiniteQuery({
    queryKey: ['user-search-options', queryScope, activeOnly, keyword],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => listUsers({ keyword, status: activeOnly ? 'active' : undefined, page: pageParam, page_size: 20 }),
    getNextPageParam: (last, pages) =>
      pages.reduce((count, page) => count + page.items.length, 0) < last.total && last.items.length > 0
        ? pages.length + 1 : undefined,
    enabled: enabled && !disabled,
  })
  const options = (query.data?.pages.flatMap((page) => page.items) ?? []).map((user) => ({
    value: user.id,
    name: user.name,
    label: `${user.name}（${user.username} · ${user.department ?? '未分配部门'}）`,
  }))
  if (selected && selected.value === value && !options.some((option) => option.value === value)) options.push(selected)
  if (value != null && fallbackLabel && !options.some((option) => option.value === value)) {
    options.push({ value, label: fallbackLabel, name: fallbackLabel })
  }
  return <Select
    className={className}
    style={style}
    disabled={disabled}
    placeholder={placeholder ?? '按姓名或登录账号搜索'}
    value={value}
    showClear
    filter
    remote
    onSearch={setSearch}
    loading={query.isFetching}
    optionList={options}
    onChange={(next) => {
      const id = next as number | undefined
      const option = options.find((item) => item.value === id)
      setSelected(option ?? null)
      onChange(id, option?.name)
    }}
    outerBottomSlot={query.isError ? <Button theme="borderless" onClick={() => {
      if (query.isFetchNextPageError) void query.fetchNextPage()
      else void query.refetch()
    }}>加载失败，点击重试</Button> : query.hasNextPage ? <Button theme="borderless" loading={query.isFetchingNextPage} onClick={() => {
      if (!query.isFetching && search.trim() === keyword) void query.fetchNextPage()
    }}>加载更多</Button> : undefined}
  />
}
