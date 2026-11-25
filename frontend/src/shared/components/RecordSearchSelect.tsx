import { useEffect, useState, type CSSProperties } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Button, Select } from '@douyinfe/semi-ui'
import { ApiError } from '../api/client'
import type { PageResult } from '../types'
import { optionsWithCodeOnDuplicate } from './optionMatch'

export default function RecordSearchSelect<T extends { id: number }>({
  scope, enabled, disabled, value, fallbackLabel, placeholder, list, lookup, labelOf, accept, style, onChange,
}: {
  scope: string
  enabled: boolean
  disabled?: boolean
  value?: number
  fallbackLabel?: string
  placeholder: string
  list: (input: { keyword: string; page: number; page_size: number }) => Promise<PageResult<T>>
  lookup?: (id: number) => Promise<T | undefined>
  labelOf: (row: T) => string
  /** 只让符合业务条件的记录可选（例如只列本报价可关联的需求）。不影响翻页。 */
  accept?: (row: T) => boolean
  style?: CSSProperties
  onChange: (row: T | undefined) => void
}) {
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selected, setSelected] = useState<T>()
  useEffect(() => {
    const timer = setTimeout(() => setKeyword(search.trim()), 250)
    return () => clearTimeout(timer)
  }, [search])

  const query = useInfiniteQuery({
    queryKey: ['record-search', scope, keyword],
    enabled: enabled && !disabled,
    initialPageParam: 1,
    queryFn: async ({ pageParam }) => {
      const result = await list({ keyword, page: pageParam, page_size: 20 })
      const filtered = accept ? { ...result, items: result.items.filter(accept) } : result
      const id = Number(keyword)
      if (lookup && pageParam === 1 && /^\d+$/.test(keyword) && id > 0 && Number.isSafeInteger(id)) {
        try {
          const record = await lookup(id)
          if (record && (!accept || accept(record)) && !filtered.items.some((row) => row.id === record.id)) {
            return { ...filtered, items: [record, ...filtered.items] }
          }
        } catch (error) {
          if (!(error instanceof ApiError && (error.status === 403 || error.status === 404))) throw error
        }
      }
      return filtered
    },
    // 不能要求「本页有可见行」才翻页：筛选把整页都滤掉时，目标可能还在下一页。
    getNextPageParam: (last) => last.page * last.page_size < last.total
      ? last.page + 1 : undefined,
  })

  const rows = new Map(query.data?.pages.flatMap((page) => page.items).map((row) => [row.id, row]))
  if (selected && selected.id === value && !rows.has(selected.id)) rows.set(selected.id, selected)
  const options = optionsWithCodeOnDuplicate([...rows.values()], labelOf, (row) => row.id)
  if (value != null && fallbackLabel && !rows.has(value)) options.push({ value, label: fallbackLabel })

  return (
    <Select aria-label={placeholder} placeholder={placeholder} style={{ width: '100%', ...style }}
      value={value} disabled={disabled} optionList={options} filter remote showClear
      loading={query.isFetching || search.trim() !== keyword}
      onSearch={setSearch}
      onChange={(next) => {
        const row = rows.get(next as number)
        if (next != null && !row) return
        setSelected(row)
        onChange(row)
      }}
      outerBottomSlot={query.isError ? <Button theme="borderless" onClick={() => {
        if (query.isFetchNextPageError) void query.fetchNextPage()
        else void query.refetch()
      }}>加载失败，点击重试</Button> : query.hasNextPage ? (
        <Button theme="borderless" loading={query.isFetchingNextPage} onClick={() => {
          if (!query.isFetching && search.trim() === keyword) void query.fetchNextPage()
        }}>加载更多</Button>
      ) : undefined}
    />
  )
}
