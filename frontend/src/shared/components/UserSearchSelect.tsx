import { useEffect, useState, type CSSProperties } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Button, Select } from '@douyinfe/semi-ui'
import { listUsers } from '../api/system'

/** 按需搜索用户；每页 20 人，选中项在切换关键词时继续保留。 */
export default function UserSearchSelect({ queryScope, enabled, value, onChange, placeholder, className, style, disabled }: {
  queryScope: string
  enabled: boolean
  value?: number
  onChange: (value: number | undefined) => void
  placeholder?: string
  className?: string
  style?: CSSProperties
  disabled?: boolean
}) {
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selected, setSelected] = useState<{ label: string; value: number } | null>(null)
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!enabled) setSearch('')
      setKeyword(enabled ? search.trim() : '')
    }, enabled ? 250 : 0)
    return () => clearTimeout(timer)
  }, [enabled, search])
  const query = useInfiniteQuery({
    queryKey: ['user-search-options', queryScope, keyword],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => listUsers({ keyword, page: pageParam, page_size: 20 }),
    getNextPageParam: (last, pages) =>
      pages.reduce((count, page) => count + page.items.length, 0) < last.total && last.items.length > 0
        ? pages.length + 1 : undefined,
    enabled: enabled && !disabled,
  })
  const options = (query.data?.pages.flatMap((page) => page.items) ?? []).map((user) => ({
    value: user.id,
    label: `${user.name}（${user.username} · ${user.department ?? '未分配部门'}）`,
  }))
  if (selected && selected.value === value && !options.some((option) => option.value === value)) options.push(selected)
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
      setSelected(options.find((option) => option.value === id) ?? null)
      onChange(id)
    }}
    outerBottomSlot={query.isError ? <Button theme="borderless" onClick={() => {
      if (query.isFetchNextPageError) void query.fetchNextPage()
      else void query.refetch()
    }}>加载失败，点击重试</Button> : query.hasNextPage ? <Button theme="borderless" loading={query.isFetchingNextPage} onClick={() => {
      if (!query.isFetching && search.trim() === keyword) void query.fetchNextPage()
    }}>加载更多</Button> : undefined}
  />
}
